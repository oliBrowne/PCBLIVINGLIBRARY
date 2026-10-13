"""Lifecycle watcher (offline; every vendor response here is a MOCK shaped after the documented fields). Run:
    python3 -m tests.test_lifecycle"""
import csv, json, os, tempfile
from pathlib import Path

from ecadlib import altium365 as A, altium_export as X, bom_triage as T, lifecycle as L
from ecadlib.httpcache import CachedHTTP, Response


def test_normalisation_table():
    table = {"Production": "active", "Active": "active", "New Product": "active", "New": "active",
             "Preliminary": "active", "NRND": "nrnd", "Not Recommended for New Designs": "nrnd",
             "Not For New Designs": "nrnd", "Discontinued at Digi-Key": "nrnd", "Last Time Buy": "ltb", "LTB": "ltb",
             "EOL": "obsolete", "End of Life": "obsolete", "Obsolete": "obsolete", "Discontinued": "obsolete",
             "Inactive": "obsolete", "": "unknown", None: "unknown", "Unknown": "unknown", "Not Listed": "unknown",
             "  production \n": "active"}
    got = {k: L.normalise(k) for k in table}
    assert got == table, {k: (v, table[k]) for k, v in got.items() if v != table[k]}
    assert L.SEVERITY["active"] < L.SEVERITY["nrnd"] < L.SEVERITY["ltb"] < L.SEVERITY["obsolete"]


def cur(status, stock=None):
    return {"status": status, "stock": stock}


def test_state_diffing():
    C = L.classify
    assert C(None, cur("active", 10)) == "new"
    assert C({"status": "active", "stock": 10}, cur("active", 5)) == "unchanged"
    assert C({"status": "active", "stock": 10}, cur("nrnd", 10)) == "worse"
    assert C({"status": "nrnd"}, cur("obsolete")) == "worse"
    assert C({"status": "active", "stock": 10}, cur("active", 0)) == "worse"            # stock fell to zero
    assert C({"status": "active", "stock": None}, cur("active", 0)) == "unchanged"      # no earlier stock to compare
    assert C({"status": "ltb", "stock": 3}, cur("nrnd", 3)) == "better"
    assert C({"status": "active", "stock": 0}, cur("active", 50)) == "better"
    assert C({"status": "active", "stock": 0}, cur("obsolete", 50)) == "worse"           # worse wins
    assert C({"status": "active", "stock": 10}, cur("unknown", None)) == "unknown"      # became unknown
    assert C({"status": "unknown", "stock": None}, cur("unknown", None)) == "unchanged"
    assert C({"status": "unknown"}, cur("nrnd")) == "worse"                              # first known value is risky
    assert C({"status": "unknown"}, cur("active")) == "unchanged"
    assert C({"status": "nrnd", "stock": 5}, cur("unknown", 0)) == "worse"               # stock alone still counts


def test_snapshot_keeps_last_known():
    s0 = L.next_state({"parts": {}}, {"K": {"mpn": "K", "manufacturer": "", "name": "", "status": "active", "stock": 4,
                                            "raw": "nexar: Production"}}, "t0")
    s1 = L.next_state(s0, {"K": {"mpn": "K", "manufacturer": "", "name": "", "status": "unknown", "stock": None, "raw": ""}}, "t1")
    k = s1["parts"]["K"]
    assert (k["status"], k["stock"], k["raw"], k["since"], k["seen"], k["checked"]) == ("active", 4, "nexar: Production", "t0", "t0", "t1"), k
    s2 = L.next_state(s1, {"K": {"mpn": "K", "manufacturer": "", "name": "", "status": "nrnd", "stock": 4, "raw": "x"}}, "t2")
    assert s2["parts"]["K"]["since"] == "t2" and L.classify(s1["parts"]["K"], cur("nrnd", 4)) == "worse"


def test_exit_codes():
    row = lambda change, status="active", stock=5: {"change": change, "status": status, "stock": stock}
    assert L.exit_code([row("unchanged"), row("new"), row("better")]) == 0
    assert L.exit_code([row("unchanged"), row("worse", "nrnd")]) == 1
    assert L.exit_code([row("unknown", "unknown", None)]) == 0
    assert L.exit_code([row("unknown", "unknown", None)], strict=True) == 1
    assert L.exit_code([row("new", "obsolete")]) == 0 and L.exit_code([row("new", "obsolete")], strict=True) == 1
    assert L.exit_code([row("new", "active", 0)], strict=True) == 1


# ------------------------------------------------------------------ provider mocks
class FakeHTTP:
    """Records requests; answers by URL substring with a canned body."""
    def __init__(self, routes):
        self.routes, self.calls, self.log = routes, [], []

    def request(self, method, url, body=None, headers=None, cache=True):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers or {}, "cache": cache})
        for frag, (status, payload) in self.routes.items():
            if frag in url:
                return Response(status, payload if isinstance(payload, str) else json.dumps(payload))
        return Response(0, "", error="no route")

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


NEXAR_MOCK = {"data": {"supSearchMpn": {"hits": 2, "results": [
    {"part": {"mpn": "LM339N", "manufacturer": {"name": "Texas Instruments"}, "octopartUrl": "https://octopart.com/x",
              "totalAvail": 999, "avgAvail": 99.9, "specs": []}},
    {"part": {"mpn": "LM339", "manufacturer": {"name": "Texas Instruments"}, "octopartUrl": "https://octopart.com/lm339-ti",
              "totalAvail": 0, "avgAvail": 0.0,
              "specs": [{"attribute": {"shortname": "case_package"}, "displayValue": "DIP", "value": "DIP"},
                        {"attribute": {"shortname": "lifecyclestatus"}, "displayValue": "NRND", "value": "NRND"}]}}]}}}


def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
    return old


def test_nexar_supply_mock():
    old = _env(NEXAR_SUPPLY_TOKEN=None, NEXAR_CLIENT_ID="cid", NEXAR_CLIENT_SECRET="sec")
    try:
        http = FakeHTTP({"identity.nexar.com": (200, {"access_token": "tok", "expires_in": 86400}),
                         "api.nexar.com/graphql": (200, NEXAR_MOCK)})
        prov = L.NexarSupply(http)
        r1, r2 = prov.check({"mpn": "LM339", "manufacturer": "TI"}), prov.check({"mpn": "lm-339", "manufacturer": ""})
        assert (r1.found, r1.status, r1.raw, r1.stock, r1.url) == (True, "nrnd", "NRND", 0, "https://octopart.com/lm339-ti"), r1
        assert r2.status == "nrnd"
        tok = [c for c in http.calls if "identity" in c["url"]]
        assert len(tok) == 1 and tok[0]["cache"] is False and "scope=supply.domain" in tok[0]["body"] \
            and "grant_type=client_credentials" in tok[0]["body"], tok
        q = [c for c in http.calls if "graphql" in c["url"]][0]
        assert q["headers"]["Authorization"] == "Bearer tok" and json.loads(q["body"])["variables"]["q"] == "LM339"
        miss = L.parse_nexar({"data": {"supSearchMpn": {"results": [NEXAR_MOCK["data"]["supSearchMpn"]["results"][0]]}}},
                             {"mpn": "LM339"}, L.LifecycleResult("nexar"))
        assert miss.found is False and miss.status == "unknown"
        nospec = L.parse_nexar({"data": {"supSearchMpn": {"results": [NEXAR_MOCK["data"]["supSearchMpn"]["results"][0]]}}},
                               {"mpn": "LM339N"}, L.LifecycleResult("nexar"))
        assert nospec.status == "unknown" and nospec.stock == 999 and "lifecyclestatus" in nospec.note
        err = L.parse_nexar({"errors": [{"message": "quota exceeded"}]}, {"mpn": "X"}, L.LifecycleResult("nexar"))
        assert err.error == "quota exceeded"
        bad = L.NexarSupply(FakeHTTP({"identity.nexar.com": (401, "nope")}))
        assert "token request failed" in bad.check({"mpn": "X"}).error
    finally:
        _env(**old)


def test_other_providers_mock():
    old = _env(DIGIKEY_CLIENT_ID="id", DIGIKEY_CLIENT_SECRET="s", MOUSER_API_KEY="SECRETKEY")
    try:
        dk = L.DigiKeyLifecycle(FakeHTTP({"oauth2/token": (200, {"access_token": "t"}),
                                          "productdetails": (200, {"Product": {"ManufacturerProductNumber": "LM339N",
                                              "ProductStatus": {"Id": 7, "Status": "Last Time Buy"}, "QuantityAvailable": 1200,
                                              "Discontinued": False, "EndOfLife": False}})}))
        r = dk.check({"mpn": "LM339N"})
        assert (r.status, r.stock, r.raw) == ("ltb", 1200, "Last Time Buy"), r
        call = dk.http.calls[-1]
        assert call["url"].endswith("/products/v4/search/LM339N/productdetails") and call["headers"]["X-DIGIKEY-Client-Id"] == "id"
        eol = L.parse_digikey({"Product": {"ManufacturerProductNumber": "A", "ProductStatus": {"Status": "Active"},
                                           "EndOfLife": True}}, {"mpn": "A"}, L.LifecycleResult("digikey"))
        assert eol.status == "obsolete", eol
        assert L.parse_digikey({"Product": {"ManufacturerProductNumber": "B"}}, {"mpn": "A"}, L.LifecycleResult("d")).found is False

        mo = L.MouserLifecycle(FakeHTTP({"search/partnumber": (200, {"Errors": [], "SearchResults": {"NumberOfResult": 1,
            "Parts": [{"ManufacturerPartNumber": "LM339N", "Manufacturer": "Texas Instruments", "LifecycleStatus": "End of Life",
                       "Availability": "0 In Stock", "AvailabilityInStock": "0"}]}})}))
        r = mo.check({"mpn": "LM339N"})
        call = mo.http.calls[-1]
        assert (r.status, r.stock) == ("obsolete", 0) and call["cache"] is False, r
        assert json.loads(call["body"]) == {"SearchByPartRequest": {"mouserPartNumber": "LM339N", "partSearchOptions": "Exact"}}
        plain = L.parse_mouser({"SearchResults": {"Parts": [{"ManufacturerPartNumber": "X", "LifecycleStatus": None,
                                                             "Availability": "1,234 In Stock"}]}}, {"mpn": "X"}, L.LifecycleResult("m"))
        assert (plain.status, plain.stock) == ("active", 1234), plain
        down = L.MouserLifecycle(FakeHTTP({"nothing": (200, {})}))
        down.http.routes = {}
        down.http.request = lambda *a, **k: Response(0, "", error="ConnectionError: url=/api?apiKey=SECRETKEY")
        assert "SECRETKEY" not in down.check({"mpn": "X"}).error
        down.http.request = lambda *a, **k: Response(0, "", error="ConnectionError: url=/api?apiKey=SECR")   # truncated
        assert "SECR" not in down.check({"mpn": "X"}).error

        jl = L.JLCLifecycle(FakeHTTP({"jlcpcb.com": (200, {"data": {"componentPageInfo": {"list": [
            {"componentCode": "C6186", "componentModelEn": "AMS1117-3.3", "componentBrandEn": "AMS", "stockCount": 0}]}}})}))
        r = jl.check({"mpn": "AMS1117-3.3", "lcsc": "C6186"})
        assert (r.found, r.status, r.stock) == (True, "unknown", 0) and json.loads(jl.http.calls[0]["body"])["keyword"] == "C6186"
    finally:
        _env(**old)


def test_combine_worst_status_highest_stock():
    R = L.LifecycleResult
    c = L.combine(L._part("X"), [R("nexar", True, "active", "Production", 0), R("mouser", True, "nrnd", "NRND", 40),
                                 R("jlcpcb_search", None, error="blocked")])
    assert (c["status"], c["stock"], c["answered"]) == ("nrnd", 40, True), c
    assert "jlcpcb_search: blocked" in c["notes"]


# ------------------------------------------------------------------ Altium 365 write-back, validated against the schema
def _schema():
    try:
        from graphql import build_schema
    except ImportError:
        return None
    return build_schema(Path("tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)


def test_queries_match_nexar_schema():
    schema = _schema()
    if not schema:
        print("skip test_queries_match_nexar_schema (pip install graphql-core)"); return
    from graphql import parse, validate
    for q in (L.NexarSupply.QUERY, L.Q_LIBRARY, L.Q_FIND_PARAMS):
        errs = validate(schema, parse(q))
        assert not errs, (q, errs)


ROWS = [{"name": "CH340C", "mpn": "CH340C", "status": "obsolete", "component_id": "", "a365_value": None},
        {"name": "RES-0603-10k", "mpn": "0603WAF1002T5E", "status": "active", "component_id": "", "a365_value": None},
        {"name": "X", "mpn": "X", "status": "unknown", "component_id": "", "a365_value": None},
        {"name": "", "mpn": "Y", "status": "nrnd", "component_id": "", "a365_value": None}]


def test_a365_update_dry_run_matches_schema():
    out = L.a365_updates(ROWS, None, "https://ws")
    assert [(u["name"], u["action"], u["value"]) for u in out] == [("CH340C", "dry_run", "Obsolete"),
                                                                    ("RES-0603-10k", "dry_run", "Active")], out
    req = out[0]["detail"]
    assert req["query"] == A.M_UPDATE_PARAMS and req["variables"]["input"]["replaceExisting"] is False
    schema = _schema()
    if not schema:
        print("skip schema half of test_a365_update_dry_run_matches_schema (pip install graphql-core)"); return
    from graphql import parse, validate
    from graphql.execution.values import get_variable_values
    doc = parse(req["query"])
    assert not validate(schema, doc)
    got = get_variable_values(schema, doc.definitions[0].variable_definitions, req["variables"])
    assert not isinstance(got, list), got              # a list is coercion errors
    bad = get_variable_values(schema, doc.definitions[0].variable_definitions, {"input": {"componentId": "c"}})
    assert isinstance(bad, list)                       # sanity: missing `parameters` is caught


class FakeDesign:
    """Nexar Design API mock: two library pages, a name lookup, and recorded updates."""
    def __init__(self):
        self.updates = []
        self.lib = {"CH340C": {"id": "c1", "name": "CH340C", "isManaged": True, "details": {"parameters": [
                        {"name": "Manufacturer Part Number", "value": "CH340C"}, {"name": "Lifecycle", "value": "Active"}]},
                        "manufacturerParts": []},
                    "RES-0603-10k": {"id": "c2", "name": "RES-0603-10k", "isManaged": True, "details": {"parameters": [
                        {"name": "Lifecycle", "value": "Active"}]},
                        "manufacturerParts": [{"companyName": "UNI-ROYAL", "partNumber": "0603WAF1002T5E"}]},
                    "Draft": {"id": "c3", "name": "Draft", "isManaged": False, "details": {"parameters": []},
                              "manufacturerParts": [{"companyName": "A", "partNumber": "DRAFT1"}]}}

    def gql(self, query, variables=None):
        if "after:" in query:
            names = list(self.lib)
            page = names[:2] if not variables.get("after") else names[2:]
            return {"desLibrary": {"components": {"pageInfo": {"hasNextPage": not variables.get("after"), "endCursor": "p2"},
                                                  "nodes": [self.lib[n] for n in page]}}}
        node = self.lib.get(variables["name"])
        return {"desLibrary": {"components": {"nodes": [node] if node else []}}}

    def update_params(self, cid, params, note):
        self.updates.append((cid, params))
        return cid + "'"


def test_a365_update_with_client():
    c = FakeDesign()
    rows = ROWS[:2] + [{"name": "Missing", "mpn": "M", "status": "nrnd", "component_id": "", "a365_value": None},
                       {"name": "Draft", "mpn": "DRAFT1", "status": "nrnd", "component_id": "", "a365_value": None}]
    out = {u["name"]: u for u in L.a365_updates(rows, c, "https://ws", apply=True)}
    assert out["CH340C"]["action"] == "updated" and c.updates == [("c1", [{"name": "Lifecycle", "value": "Obsolete"}])]
    assert out["RES-0603-10k"]["action"] == "unchanged"
    assert out["Missing"]["action"] == "skip" and "not a released" in out["Draft"]["detail"]


# ------------------------------------------------------------------ inputs
def test_parts_from_each_input():
    bom = L.parts_from_bom("examples/sample_bom.csv")
    by = {p["mpn"]: p for p in bom}
    assert by["STM32F103C8T6"]["lcsc"] == "C8734" and by["STM32F103C8T6"]["manufacturer"] == "STMicroelectronics"
    assert by["0603WAF1002T5E"]["name"] == "RES-0603-10k" and by["FTSH-105-01-L-DV-K"]["lcsc"] == ""
    with tempfile.TemporaryDirectory() as d:
        X.main(["examples/sample_bom.csv", "-o", d])
        db = L.parts_from_dblib(f"{d}/parts.sqlite")
        assert {p["mpn"] for p in db} == set(by), {p["mpn"] for p in db} ^ set(by)
        assert any(p["sources"] == ["dblib:ICs"] for p in db)
        Path(f"{d}/plan.json").write_text(json.dumps(A.plan(T.read_bom("examples/sample_bom.csv")[0])))
        pl = L.parts_from_plan(f"{d}/plan.json")
        assert {p["mpn"] for p in pl} == set(by) and {p["name"] for p in pl} >= {"CAP-0402-100nF", "CH340C"}
        merged, _ = L.merge(bom + db + pl)
        assert len(merged) == len(by) and merged["STM32F103C8T6"]["sources"] == ["bom:sample_bom.csv", "dblib:ICs", "plan"]
        assert L.read_inputs([f"{d}/parts.sqlite"])[0]["sources"][0].startswith("dblib:")
    ws = L.parts_from_a365(FakeDesign(), "https://ws")
    assert [(p["mpn"], p["component_id"], p["a365_value"]) for p in ws] == [("CH340C", "c1", "Active"),
                                                                            ("0603WAF1002T5E", "c2", "Active")], ws
    merged, skipped = L.merge([L._part("", name="NoMpn"), L._part("", lcsc="c1525", name="Cap")])
    assert list(merged) == ["LCSC:C1525"] and skipped == ["NoMpn"]


def test_altbom_comment_part_numbers():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ab.csv"
        f.write_text("Comment;Designator;Footprint;LibRef;Quantity\nSTM32F103C8T6;U1;LQFP-48;STM32F103C8Tx;1\n10k;R1;RESC1608X55N;RES;1\n")
        merged, skipped = L.merge(L.parts_from_bom(f))
        assert list(merged) == ["STM32F103C8T6"] and skipped == ["RES-0603-10k"], (merged, skipped)


# ------------------------------------------------------------------ end to end through the CLI (seeded cache, offline)
def _seed(cache, mpn, display, stock):
    http = CachedHTTP(cache, offline=True)
    body = json.dumps({"query": L.NexarSupply.QUERY, "variables": {"q": mpn, "limit": 5}})
    payload = {"data": {"supSearchMpn": {"hits": 1, "results": [{"part": {"mpn": mpn, "manufacturer": {"name": "M"},
               "octopartUrl": "", "totalAvail": stock, "avgAvail": 1.0,
               "specs": [{"attribute": {"shortname": "lifecyclestatus"}, "displayValue": display, "value": display}]}}]}}}
    (Path(cache) / f"{http._key('POST', L.NexarSupply.API_URL, body)}.json").write_text(
        json.dumps({"url": "", "status": 200, "text": json.dumps(payload)}))


def test_cli_runs_and_exit_codes():
    old = _env(NEXAR_SUPPLY_TOKEN="t", NEXAR_TOKEN=None)
    try:
        with tempfile.TemporaryDirectory() as d:
            bom = Path(d) / "bom.csv"
            bom.write_text("Designator,MPN,Manufacturer\nU1,LM339,TI\nU2,NE555,TI\n")
            args = [str(bom), "--providers", "nexar", "--offline", "--cache", f"{d}/c", "--state", f"{d}/s.json", "-o", f"{d}/r"]
            run = lambda *extra: L.main(args + list(extra))
            _seed(f"{d}/c", "LM339", "Production", 500); _seed(f"{d}/c", "NE555", "Production", 10)
            assert run() == 0
            st = json.loads(Path(f"{d}/s.json").read_text())
            assert st["parts"]["LM339"]["status"] == "active" and st["parts"]["NE555"]["stock"] == 10
            assert run() == 0                                                   # unchanged
            _seed(f"{d}/c", "LM339", "Last Time Buy", 500); _seed(f"{d}/c", "NE555", "Production", 0)
            assert run("--no-save") == 1                                        # LTB + out of stock
            rows = {r["mpn"]: r for r in csv.DictReader(open(f"{d}/r.csv"))}
            assert rows["LM339"]["change"] == "worse" and rows["LM339"]["prev_status"] == "active" and rows["LM339"]["status"] == "ltb"
            assert rows["NE555"]["change"] == "worse" and rows["NE555"]["stock"] == "0"
            md = Path(f"{d}/r.md").read_text()
            assert "## Changes since last snapshot" in md and "Last Time Buy" in md
            assert run() == 1 and run() == 0                                    # saved: the next run is unchanged
            _seed(f"{d}/c", "LM339", "Production", 500)
            assert run() == 0 and json.loads(Path(f"{d}/s.json").read_text())["parts"]["LM339"]["status"] == "active"
            assert run("--update-a365", "--workspace", "https://ws") == 0
            plan = json.loads(Path(f"{d}/r_a365.json").read_text())
            assert {u["name"]: u["value"] for u in plan} == {"LM339": "Active", "NE555": "Active"}
            os.environ.pop("NEXAR_SUPPLY_TOKEN")
            assert run() == 3                                                   # nothing answered
            try:
                L.main(["--providers", "nexar"]); raise AssertionError("no input accepted")
            except SystemExit as e:
                assert e.code == 2
            assert L.main([str(Path(d) / "missing.csv")]) == 2
    finally:
        _env(**old)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
