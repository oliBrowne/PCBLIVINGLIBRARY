"""Official part APIs first, unofficial endpoints fenced off, and the live-source check (offline; every vendor answer
here is a MOCK shaped after the documented fields). Run:  python3 -m tests.test_official_sources"""
import json, os
from contextlib import contextmanager

from ecadlib import bom_triage as T, datasheet as D, plugins as PL, providers as P, rules as RU, sources_check as SC
from ecadlib.httpcache import Response
from ecadlib.kicad_index import KiCadIndex


class Router:
    """FakeHTTP that answers with fn(method, url, body) -> (status, payload)."""
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def request(self, method, url, body=None, headers=None, cache=True):
        self.calls.append((method, url))
        status, payload = self.fn(method, url, body)
        if status == 0:
            return Response(0, "", error=payload)
        return Response(status, payload if isinstance(payload, str) else json.dumps(payload))

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


@contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: v for k, v in kv.items() if v is not None})
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


NEXAR_PART = {"mpn": "NE555DR", "manufacturer": {"name": "Texas Instruments"}, "octopartUrl": "https://octopart.com/ne555dr",
              "shortDescription": "Timer, single, SOIC-8", "bestDatasheet": {"url": "https://www.ti.com/lit/ds/symlink/ne555.pdf"},
              "specs": [{"attribute": {"name": "Case/Package", "shortname": "case_package"}, "displayValue": "SOIC-8", "value": "SOIC-8"}],
              "sellers": [{"company": {"name": "Digi-Key"}, "isAuthorized": True, "offers": [{"sku": "296-1411-1-ND", "inventoryLevel": 9, "prices": []}]},
                          {"company": {"name": "LCSC"}, "isAuthorized": False, "offers": [{"sku": "C7593", "inventoryLevel": 500, "prices": []}]}]}
EASYEDA_NE555 = {"success": True, "result": {"title": "NE555DR", "lcsc": {"url": "https://www.lcsc.com/product-detail/C7593.html"},
                 "dataStr": {"head": {"x": 400, "y": 300, "c_para": {"Manufacturer Part": "NE555DR", "Manufacturer": "TI", "package": "SOIC-8"}},
                             "shape": ["R~380~280~2~2~40~40~#880000~1~0~none~gge1~0~"]},
                 "packageDetail": {"title": "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL", "dataStr": {"head": {"x": 4000, "y": 3000},
                                   "shape": [f"PAD~RECT~{3992.5 + 5 * (i % 4)}~{3013.4 if i < 4 else 2986.6}~2.4~6~1~~{i + 1}~0~~0~gge{i}~0~~Y~0"
                                             for i in range(8)]}}}}


def nexar_router(extra=None):
    def fn(m, u, b):
        if "identity.nexar.com" in u:
            return 200, {"access_token": "tok"}
        if "api.nexar.com/graphql" in u:
            return 200, {"data": {"supSearchMpn": {"hits": 1, "results": [{"part": NEXAR_PART}]}}}
        if extra:
            return extra(m, u, b)
        return 404, "{}"
    return Router(fn)


def test_nexar_is_official_and_finds_lcsc_and_datasheet():
    assert P.Nexar.official and P.DigiKey.official and P.Mouser.official
    assert not P.EasyEDA.official and not P.JLCPCBSearch.official
    with env(NEXAR_CLIENT_ID="id", NEXAR_CLIENT_SECRET="s", NEXAR_SUPPLY_TOKEN=None):
        http = nexar_router()
        prov = P.Nexar(http)
        r = prov.lookup({"mpn": "NE555DR", "manufacturer": "TI"})
        assert r.found and r.lcsc == "C7593" and r.manufacturer == "Texas Instruments", r
        assert r.extra["datasheet"].endswith("ne555.pdf") and r.extra["description"].startswith("Timer"), r
        assert r.has_footprint is None and r.has_symbol is None, r       # identity only: no CAD from the official APIs
        prov.lookup({"mpn": "NE555DR"})
        assert sum(1 for c in http.calls if "identity.nexar.com" in c[1]) == 1, http.calls   # one token for the run


def test_unconfigured_official_source_is_skipped_with_a_note():
    with env(NEXAR_CLIENT_ID=None, NEXAR_CLIENT_SECRET=None, NEXAR_SUPPLY_TOKEN=None):
        try:
            P.Nexar(Router(lambda m, u, b: (500, "{}"))).lookup({"mpn": "X"})
            raise AssertionError("expected NotConfigured")
        except P.NotConfigured as e:
            assert "NEXAR_CLIENT_ID" in str(e)


def test_triage_uses_official_lcsc_and_skips_the_jlcpcb_search():
    with env(NEXAR_CLIENT_ID="id", NEXAR_CLIENT_SECRET="s", NEXAR_SUPPLY_TOKEN=None, DIGIKEY_CLIENT_ID=None, MOUSER_API_KEY=None):
        def more(m, u, b):
            if "easyeda.com/api/products/C7593" in u:
                return 200, EASYEDA_NE555
            raise AssertionError(f"unexpected request {m} {u}")      # the JLCPCB search must not be asked
        http = nexar_router(more)
        provs = [c(http) for c in PL.select(None, RU.DEFAULTS, default=P.DEFAULT_ORDER)]
        assert [p.name for p in provs] == ["nexar", "digikey", "mouser", "jlcpcb_search", "easyeda"]
        row = T.triage([{"mpn": "NE555DR", "designator": "U1"}], KiCadIndex([]), provs, http)[0]
        assert row["lcsc"] == "C7593 (via MPN)" and "nexar: MPN -> C7593" in row["notes"], row
        assert row["src"] == "easyeda" and row["status"] in ("available_easyeda", "easyeda_footprint_only"), row
        assert row["src_datasheet"].endswith("ne555.pdf") and row["src_description"].startswith("Timer"), row
        assert "not configured: digikey,mouser" in row["notes"], row
        assert not any("jlcpcb.com" in u for _, u in http.calls), http.calls


def test_official_answer_kept_when_easyeda_misses():
    with env(NEXAR_CLIENT_ID="id", NEXAR_CLIENT_SECRET="s", NEXAR_SUPPLY_TOKEN=None):
        http = nexar_router(lambda m, u, b: (200, {"success": False, "result": None}))
        row = T.triage([{"mpn": "NE555DR"}], KiCadIndex([]), [P.Nexar(http), P.EasyEDA(http)], http)[0]
        assert row["src"] == "nexar" and row["src_mpn"] == "NE555DR", row
        assert row["status"] == "needs_footprint_generation", row


def test_unofficial_switch():
    rules = {"providers": {"order": [], "unofficial": False}}
    names = [c.name for c in PL.select(None, rules, default=P.DEFAULT_ORDER)]
    assert names == ["nexar", "digikey", "mouser"], names
    assert [c.name for c in PL.select("easyeda,nexar", rules)] == ["nexar"]       # off means off, even when named
    assert P.unofficial_allowed(RU.DEFAULTS) and P.unofficial_allowed({})
    try:
        RU.validate({"providers": {"unofficial": "no"}}, "test")
        raise AssertionError("expected RulesError")
    except RU.RulesError as e:
        assert "providers.unofficial" in str(e)


def test_datasheet_url_from_official_source_comes_after_the_bom():
    urls = D.datasheet_urls({"parameters": []}, {"datasheet": ""}, {"src": "nexar", "src_datasheet": "https://ti.com/ne555.pdf",
                                                                   "lcsc": "C7593"}, {})
    assert urls[0] == ("https://ti.com/ne555.pdf", "nexar datasheet link"), urls
    assert urls[-1][0] == "https://www.lcsc.com/datasheet/C7593.pdf", urls
    urls = D.datasheet_urls({"parameters": []}, {"datasheet": "https://bom.example/x.pdf"}, {"src_datasheet": "https://ti.com/a.pdf"}, {})
    assert [u for u, _ in urls[:2]] == ["https://bom.example/x.pdf", "https://ti.com/a.pdf"], urls


def test_lcsc_sku():
    assert P.lcsc_sku("C7593") == "C7593" and P.lcsc_sku("https://www.lcsc.com/product-detail/C2040.html") == "C2040"
    assert P.lcsc_sku("296-1411-1-ND") == "" and P.lcsc_sku("ABC123") == "" and P.lcsc_sku(None) == ""


def test_sources_check_classifies_answers():
    st, detail = SC.check_easyeda_answer(EASYEDA_NE555, "NE555DR", 8, 0)
    assert st == "ok" and "8 pads" in detail, (st, detail)
    st, detail = SC.check_easyeda_answer(EASYEDA_NE555, "NE555DR", 8, 4)
    assert st == "changed" and "pins 0 (should be 4)" in detail, detail
    st, detail = SC.check_easyeda_answer({"success": True, "result": {"title": "x"}}, "NE555DR", 8, 8)
    assert st == "changed", detail
    st, _ = SC.check_easyeda_answer(EASYEDA_NE555, "LM358DR", 8, 0)
    assert st == "changed"

    def site(m, u, b):
        if "jlcpcb.com" in u:
            return 403, "blocked"
        if "C7593" in u:
            return 200, EASYEDA_NE555
        return 0, "connection refused"
    rows = SC.run(Router(site))
    by = {r[1]: r[2] for r in rows}
    assert by["NE555DR"] == "unreachable" and by["C5423"] == "unreachable", rows
    assert by["C7593"] == "changed", rows              # hand-made answer has no symbol pins: flagged, not passed
    assert SC.exit_code(rows) == 1
    assert SC.exit_code([("a", "b", "unreachable", ""), ("c", "d", "unreachable", "")]) == 3
    assert SC.exit_code([("a", "b", "ok", ""), ("c", "d", "unreachable", "")]) == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
