"""Review 2026-10-17 (part sources / data pipeline): regressions for the bugs fixed in that review. Offline; every
vendor answer is a MOCK. Run:  python3 -m pytest -q tests/test_review_1017_sources.py"""
import json

from ecadlib import easyeda_convert as EC, lifecycle as L, plugins as PL, providers as P, release_check as RC
from ecadlib.httpcache import Response
from tests.fixtures_lite import lite


class FakeHTTP:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get(self, url, **kw):
        self.calls.append(url)
        return Response(200, json.dumps(self.payload))

    def post(self, url, body, **kw):
        self.calls.append(url)
        return Response(200, json.dumps(self.payload))


def test_builtin_default_order_is_official_first():
    # the web UI / MCP workspace triage asks "every built-in" (P.ALL): the official APIs must come before EasyEDA/JLCPCB
    names = [c.name for c in PL.select(None, {}, default=[c.name for c in P.ALL])]
    assert names.index("nexar") < names.index("jlcpcb_search") < names.index("easyeda"), names
    assert names.index("mouser") < names.index("jlcpcb_search"), names


@lite
def test_lifecycle_honours_unofficial_false(tmp_path, monkeypatch):
    (tmp_path / "ecadlib.toml").write_text("[providers]\nunofficial = false\n", encoding="utf-8")
    bom = tmp_path / "bom.csv"
    bom.write_text("Designator,MPN,Manufacturer\nU1,NE555DR,TI\n", encoding="utf-8")
    seen = {}

    class Stop(Exception):
        pass

    def fake_check(parts, provs):
        seen["names"] = [p.name for p in provs]
        raise Stop

    monkeypatch.setattr(L, "check_parts", fake_check)
    try:
        L.main([str(bom), "--offline", "--cache", str(tmp_path / "cache"), "-o", str(tmp_path / "out"),
                "--state", str(tmp_path / "state.json")])
        raise AssertionError("check_parts was not reached")
    except Stop:
        pass
    assert "jlcpcb_search" not in seen["names"] and "nexar" in seen["names"], seen


def test_jlcpcb_component_code_must_be_an_lcsc_number():
    item = {"componentModelEn": "NE555DR", "componentBrandEn": "TI"}
    for bad in (7593, "../../x?y=1", ""):
        http = FakeHTTP({"data": {"componentPageInfo": {"list": [dict(item, componentCode=bad)]}}})
        r = P.JLCPCBSearch(http).lookup({"mpn": "NE555DR"})
        assert r.error and not r.lcsc, (bad, r)
    http = FakeHTTP({"data": {"componentPageInfo": {"list": [dict(item, componentCode="C7593")]}}})
    r = P.JLCPCBSearch(http).lookup({"mpn": "NE555DR"})
    assert r.found and r.lcsc == "C7593" and not r.error


@lite
def test_official_source_with_a_non_object_answer_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setenv("NEXAR_SUPPLY_TOKEN", "tok")
    r = P.Nexar(FakeHTTP([1, 2, 3])).lookup({"mpn": "NE555DR"})      # a JSON list where an object belongs
    assert r.error and r.found is None, r


def _fp(*extra):
    pads = [f"PAD~RECT~{4000 + 5 * i}~3000~2~4~1~~{i + 1}~0~~0~gge{i}~0~~Y~0" for i in range(3)]
    return {"head": {"x": 4000, "y": 3000}, "shape": pads + list(extra)}


def test_easyeda_short_or_non_finite_shapes_do_not_break_the_footprint():
    notes = []
    text = EC.footprint_to_kicad(_fp("ARC~1~3", "SOLIDREGION~99", "TRACK~1~3", "CIRCLE~4000"), "X", notes)
    assert text.count("(pad ") == 3
    text = EC.footprint_to_kicad(_fp("TRACK~nan~3~~M 3990 inf L 4020 3010"), "X", [])
    assert "nan" not in text and "inf" not in text


def test_release_report_keeps_eagle_and_easyeda_reasons_apart():
    s = "height: no 3D height (no 3D body and no HEIGHT); eagle: EAGLE / Fusion: pad 3 differs; easyeda: EasyEDA Pro: pad 1"
    assert RC._split(s) == ["height: no 3D height (no 3D body and no HEIGHT)", "eagle: EAGLE / Fusion: pad 3 differs",
                            "easyeda: EasyEDA Pro: pad 1"]
    assert RC._split("footprint: silk: a; b; pins: x") == ["footprint: silk: a; b", "pins: x"]
