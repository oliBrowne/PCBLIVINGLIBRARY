"""Offline tests for ecadlib.agent_watch (library agent, WATCH step). Run:  python3 -m tests.test_agent_watch

Altium 365 is served by FakeA365Projects: every GraphQL operation is validated against tests/nexar/schema.graphql
(as test_a365_sync does) and answered from in-memory projects. api.nexar.com is blocked here: nothing is live."""
import contextlib, io, json, os, tempfile, time
from pathlib import Path

from ecadlib import agent_watch as AW, rules as RU
from tests.test_a365_sync import check_op

WS = "https://acme.365.altium.com"
HDR = "Designator,Qty,Value,Footprint,Manufacturer,MPN,LCSC,Datasheet,Description\n"
BOM1 = HDR + ("U1,1,CH340C,SOIC-16,WCH,CH340C,C84681,https://x/ch340.pdf,USB UART\n"
              "U2,1,TPS62133,VQFN-16,Texas Instruments,TPS62133RGTR,,https://x/tps.pdf,Buck\n"
              "\"R1,R2\",2,10k,R_0603_1608Metric,UNI-ROYAL,0603WAF1002T5E,C25804,,Resistor\n")

SYM = """(kicad_symbol_lib (version 20211014) (generator test)
  (symbol "CH340C" (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 0 0))
    (property "Value" "CH340C" (at 0 0 0))
    (property "Footprint" "test:SOIC-16" (at 0 0 0))
    (property "MPN" "CH340C" (at 0 0 0))
  )
)
"""
FP = """(footprint "SOIC-16" (layer "F.Cu")
  (pad "1" smd rect (at 0 0) (size 1 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""


def _ws(d, lib=True):
    root = Path(d) / "ws"
    (root / "footprints").mkdir(parents=True)
    libdir = Path(d) / "lib"
    if lib:                                              # CH340C (symbol + footprint) is in the library
        (libdir / "test.pretty").mkdir(parents=True)
        (libdir / "test.kicad_sym").write_text(SYM)
        (libdir / "test.pretty" / "SOIC-16.kicad_mod").write_text(FP)
    return root, [str(libdir)]


def _scan(root, libs, sources, **kw):
    return AW.scan_report(root, RU.load({}), sources, libs=libs, **kw)


def _kinds(changes):
    return sorted((c["kind"], c["mpn"]) for c in changes)


def test_part_key_and_merge():
    assert AW.part_key(" Texas Instruments ", "tps62133rgtr ") == "texas instruments|TPS62133RGTR"
    assert AW.part_key("", "X1") == "|X1"
    parts = AW.parts_from_lines([{"designator": "R1", "qty": "1", "mpn": "A1", "value": "10k"},
                                 {"designator": "R7", "qty": "2", "mpn": "A1", "footprint": "R_0603"},
                                 {"designator": "C1", "value": "100n", "footprint": "C_0402"},
                                 {"designator": "U9", "value": "STM32F103C8T6"}, {"designator": "X"}])
    assert parts["|A1"]["designator"] == "R1, R7" and parts["|A1"]["qty"] == "3" and parts["|A1"]["footprint"] == "R_0603"
    assert "|100N C_0402" in parts and parts["|100N C_0402"]["mpn"] == ""
    assert parts["|STM32F103C8T6"]["mpn"] == "STM32F103C8T6"         # IC part number from the Comment, as triage does
    assert len(parts) == 3


def test_bom_file_new_changed_removed_and_idempotent():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        bom = root / "boms" / "main.csv"
        bom.parent.mkdir()
        bom.write_text(BOM1)
        src = [{"type": "bom_file", "ref": "boms/main.csv"}]
        r = _scan(root, libs, src, now="2026-10-15T06:00:00Z")
        # CH340C is already in the library (symbol + footprint): not "new"
        assert _kinds(r["changes"]) == [("new", "0603WAF1002T5E"), ("new", "TPS62133RGTR")], r["changes"]
        c = next(c for c in r["changes"] if c["mpn"] == "TPS62133RGTR")
        assert c["key"] == "texas instruments|TPS62133RGTR" and c["detected"] == "2026-10-15T06:00:00Z"
        assert c["source"] == {"type": "bom_file", "ref": "boms/main.csv", "name": "main.csv"}
        assert c["line"]["footprint"] == "VQFN-16" and c["line"]["datasheet"] == "https://x/tps.pdf" and c["was"] == {}
        assert (root / "agent" / "watch_state.json").exists()
        assert _scan(root, libs, src)["changes"] == []                    # idempotent
        time.sleep(0.01)
        bom.write_text(BOM1)                                              # touched, same content: nothing
        assert _scan(root, libs, src)["changes"] == []
        new = BOM1.replace("VQFN-16", "VQFN-16-1EP").replace("https://x/ch340.pdf", "https://x/ch340-v2.pdf")
        new = new.replace("\"R1,R2\",2,10k", "\"R1,R2,R3\",3,10k")       # quantity only: not a library change
        new = "\n".join(l for l in new.splitlines() if "UNI-ROYAL" not in l) + "\nJ1,1,,USB-C,GCT,USB4105-GF-A,,,USB C\n"
        bom.write_text(new)
        r = _scan(root, libs, src)
        assert _kinds(r["changes"]) == [("changed", "CH340C"), ("changed", "TPS62133RGTR"), ("new", "USB4105-GF-A"),
                                        ("removed", "0603WAF1002T5E")], r["changes"]
        ch = next(c for c in r["changes"] if c["mpn"] == "TPS62133RGTR")
        assert ch["was"]["footprint"] == "VQFN-16" and ch["line"]["footprint"] == "VQFN-16-1EP"
        rm = next(c for c in r["changes"] if c["kind"] == "removed")
        assert rm["line"] == {} and rm["was"]["mpn"] == "0603WAF1002T5E" and rm["manufacturer"] == "UNI-ROYAL"
        assert _scan(root, libs, src)["changes"] == []


def test_dry_run_does_not_save_and_rules_sources():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        (root / "a.csv").write_text(BOM1)
        rules = RU.load({"agent": {"watch": {"sources": [{"type": "bom_file", "ref": "a.csv", "name": "Board A"}]}}})
        r = AW.scan_report(root, rules, libs=libs, dry_run=True)
        assert len(r["changes"]) == 2 and r["changes"][0]["source"]["name"] == "Board A"
        assert not (root / "agent" / "watch_state.json").exists()
        assert len(AW.scan(root, rules)) == 3                              # no --lib: CH340C is new too
        assert AW.scan(root, rules) == []
        assert AW.scan(root, RU.load({})) == []                            # defaults: no sources, nothing watched


def test_library_from_plans_and_a365_state():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d, lib=False)
        (root / "librarian" / "main").mkdir(parents=True)
        (root / "librarian" / "main" / "plan.json").write_text(json.dumps([
            {"name": "TPS62133RGTR", "status": "ready", "parameters": [{"name": "Manufacturer Part Number", "value": "TPS62133RGTR"}]},
            {"name": "CH340C", "status": "needs_altium_models", "parameters": []}]))
        (root / "altium365").mkdir()
        (root / "altium365" / "a365_state.json").write_text(json.dumps(
            {"RES-0603-10K": {"parameters": {"Manufacturer Part Number": "0603WAF1002T5E"}}}))
        (root / "a.csv").write_text(BOM1)
        r = _scan(root, libs, ["a.csv"])
        assert _kinds(r["changes"]) == [("new", "CH340C")], r["changes"]     # a held plan item is not a library part


def test_error_source_keeps_state():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        bom = root / "a.csv"
        bom.write_text(BOM1)
        src = [{"type": "bom_file", "ref": "a.csv"}]
        _scan(root, libs, src)
        before = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]["bom_file:a.csv"]["files"]
        bom.write_bytes(b"\x00\x01binary junk")
        r = _scan(root, libs, src)
        assert r["changes"] == [] and r["errors"] and "binary" in r["errors"][0]["error"], r
        st = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]["bom_file:a.csv"]
        assert st["files"] == before and st["error"] and st["errors"] == 1
        bom.unlink()
        r = _scan(root, libs, src)
        assert r["changes"] == [] and r["sources"][0]["status"] == "error" and "not found" in r["sources"][0]["error"]
        st = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]["bom_file:a.csv"]
        assert st["files"] == before and st["errors"] == 2
        bom.write_text(BOM1)                                               # back: nothing new, error cleared
        r = _scan(root, libs, src)
        assert r["changes"] == [] and r["errors"] == []
        st = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]["bom_file:a.csv"]
        assert st["error"] is None and "error_since" not in st


def test_renamed_source_is_adopted():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        (root / "a.csv").write_text(BOM1)
        _scan(root, libs, [{"type": "bom_file", "ref": "a.csv"}])
        (root / "a.csv").rename(root / "b.csv")
        r = _scan(root, libs, [{"type": "bom_file", "ref": "b.csv"}])
        assert r["changes"] == [] and r["sources"][0]["renamed_from"] == "bom_file:a.csv", r
        st = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]
        assert list(st) == ["bom_file:b.csv"] and st["bom_file:b.csv"]["renamed_from"] == "a.csv"
        (root / "c.csv").write_text(BOM1.replace("10k", "4k7"))           # different content: a new source
        r = _scan(root, libs, [{"type": "bom_file", "ref": "c.csv"}])
        assert len(r["changes"]) == 2 and "renamed_from" not in r["sources"][0]


def test_folder_and_uploads_layout():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        up = root / "uploads"
        (up / "20261014-101010-aaaaaa").mkdir(parents=True)
        (up / "20261014-101010-aaaaaa" / "BOM.csv").write_text(BOM1)
        (up / "20261014-111111-bbbbbb").mkdir()
        (up / "20261014-111111-bbbbbb" / "other.csv").write_text(HDR + "D1,1,,SOD-123,Nexperia,BAT54,,,Schottky\n")
        (up / "notes.md").write_text("# not a BOM")
        (up / "skip_me.csv").write_text(HDR + "Q1,1,,SOT-23,onsemi,2N7002,,,FET\n")
        src = [{"type": "folder", "ref": "uploads", "exclude": ["skip_*"]}]
        r = _scan(root, libs, src)
        assert _kinds(r["changes"]) == [("new", "0603WAF1002T5E"), ("new", "BAT54"), ("new", "TPS62133RGTR")], r
        assert {c["line"]["file"] for c in r["changes"]} == {"BOM.csv", "other.csv"}
        assert r["sources"][0]["files"] == 2 and r["errors"] == []
        assert _scan(root, libs, src)["changes"] == []
        # the same BOM uploaded again with an edit: a new version of BOM.csv, not a second BOM
        (up / "20261015-090000-cccccc").mkdir()
        (up / "20261015-090000-cccccc" / "BOM.csv").write_text(BOM1.replace("R_0603_1608Metric", "R_0402_1005Metric"))
        r = _scan(root, libs, src)
        assert _kinds(r["changes"]) == [("changed", "0603WAF1002T5E")], r["changes"]
        # a part moving from one file of the folder to another is not removed + new
        (up / "20261015-090000-cccccc" / "BOM.csv").write_text(BOM1.replace("R_0603_1608Metric", "R_0402_1005Metric")
                                                               + "D1,1,,SOD-123,Nexperia,BAT54,,,Schottky\n")
        (up / "20261015-090001-dddddd").mkdir()
        (up / "20261015-090001-dddddd" / "other.csv").write_text(HDR + "D9,1,,SOD-123,Nexperia,BAT54,,,Schottky\n")
        assert _scan(root, libs, src)["changes"] == []
        # one unreadable file: reported, its last parts kept, the rest still scanned
        (up / "20261015-090001-dddddd" / "other.csv").write_bytes(b"\x00\x00junk")
        (up / "20261015-090000-cccccc" / "BOM.csv").write_text(BOM1)
        r = _scan(root, libs, src)
        assert _kinds(r["changes"]) == [("changed", "0603WAF1002T5E")] and len(r["errors"]) == 1, r
        assert r["sources"][0]["status"] == "partial"


def test_huge_bom_is_fast_and_not_reread():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        rows = [f"U{i},1,,QFN-{i % 50},Maker{i % 7},PN-{i:05d},,,Part {i}" for i in range(20000)]
        (root / "big.csv").write_text(HDR + "\n".join(rows) + "\n")
        src = [{"type": "bom_file", "ref": "big.csv"}]
        t = time.time()
        r = _scan(root, libs, src)
        first = time.time() - t
        assert len(r["changes"]) == 20000 and first < 60, first
        calls = []
        orig = AW.read_bom_parts
        AW.read_bom_parts = lambda *a: calls.append(a) or orig(*a)
        try:
            t = time.time()
            assert _scan(root, libs, src)["changes"] == []
            again = time.time() - t
        finally:
            AW.read_bom_parts = orig
        assert calls == [] and again < first, (again, first)
        print(f"  20000-line BOM: first scan {first:.1f}s, unchanged rescan {again:.2f}s")


def test_altium_project_source():
    from tests.test_altium_project import board
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        prj = board(root / "boards")
        src = [{"type": "altium_project", "ref": os.path.relpath(prj, root)}]
        r = _scan(root, libs, src)
        assert r["errors"] == [] and r["changes"] and all(c["kind"] == "new" for c in r["changes"]), r
        assert all(c["source"]["type"] == "altium_project" for c in r["changes"])
        assert _scan(root, libs, src)["changes"] == []
        # a folder source picks the project up too
        r2 = _scan(root, libs, [{"type": "folder", "ref": "boards"}], dry_run=True)
        assert {c["key"] for c in r2["changes"]} == {c["key"] for c in r["changes"]}


# ------------------------------------------------------------------ Altium 365 (fake Nexar)
def comp(name, mpn, mfr, fp, value="", ds="", priority=0, lcsc=""):
    par = [{"name": "Value", "value": value}] if value else []
    if ds:
        par += [{"name": "ComponentLink1Description", "value": "Datasheet"}, {"name": "ComponentLink1URL", "value": ds}]
    if lcsc:
        par.append({"name": "LCSC", "value": lcsc})
    return {"id": f"cmp-{name}", "name": name, "comment": "=Value", "description": f"{name} part",
            "manufacturerParts": [{"companyName": mfr, "partNumber": mpn, "priority": priority}] if mpn else [],
            "details": {"parameters": par, "footprints": [{"name": fp}]}}


class FakeA365Projects:
    """Nexar desProjects / desProjectById / BOM items, paged; each query checked against the schema."""

    def __init__(self, projects, page=2):
        self.projects, self.page, self.calls = projects, page, []

    def gql(self, query, variables=None):
        variables = variables or {}
        check_op(query, variables)
        self.calls.append(query.split("(")[1].split(":")[0].strip() if "desProjects(" not in query else "desProjects")
        if "desProjects(" in query:
            assert variables["ws"] == WS
            return {"desProjects": self._page([{k: p[k] for k in ("id", "projectId", "name", "url", "updatedAt")}
                                              for p in self.projects], variables)}
        p = next((x for x in self.projects if x["id"] == variables["id"]), None)
        if "bom {" not in query:
            return {"desProjectById": p and {**{k: p[k] for k in ("id", "projectId", "name", "url", "updatedAt")},
                                             "latestRevision": {"revisionId": p["rev"]},
                                             "design": {"variants": [{"name": v} for v in p["variants"]]}}}
        self.calls[-1] = "bom"
        v = variables["variant"]
        items = p["boms"][v]
        return {"desProjectById": {"design": {"variants": [{"name": v, "bom": {"items": self._page(items, variables)}}]}}}

    def _page(self, nodes, variables):
        start = int(variables.get("after") or 0)
        n = min(self.page, variables.get("first") or self.page)
        end = start + n
        return {"pageInfo": {"hasNextPage": end < len(nodes), "endCursor": str(end)}, "nodes": nodes[start:end]}


def _project(name="Main board", pid="prj-1"):
    items = [
        {"quantity": 1, "bomItemInstances": [{"designator": "U1", "isFitted": True}],
         "component": comp("CH340C", "CH340C", "WCH", "SOIC-16", ds="https://x/ch340.pdf")},
        {"quantity": 1, "bomItemInstances": [{"designator": "U2", "isFitted": True}],
         "component": comp("TPS62133", "TPS62133RGTR", "Texas Instruments", "VQFN-16", ds="https://x/tps.pdf")},
        {"quantity": 2, "bomItemInstances": [{"designator": "R1", "isFitted": True}, {"designator": "R2", "isFitted": True}],
         "component": comp("RES-0603-10K", "0603WAF1002T5E", "UNI-ROYAL", "R_0603_1608Metric", value="10k")},
        {"quantity": 0, "bomItemInstances": [{"designator": "R9", "isFitted": False}],
         "component": comp("RES-0603-0R", "", "", "R_0603_1608Metric", value="0R")},
    ]
    return {"id": pid, "projectId": "P-0001", "name": name, "url": f"{WS}/projects/{pid}", "updatedAt": "2026-10-14T10:00:00Z",
            "rev": "r1", "variants": ["[No Variations]", "Lite"], "boms": {"[No Variations]": items, "Lite": items[:1]}}


def test_a365_queries_are_valid_against_schema():
    for q, v in ((AW.Q_PROJECTS, {"ws": WS, "first": 100, "after": None}), (AW.Q_PROJECT, {"id": "x"}),
                 (AW.Q_BOM, {"id": "x", "variant": "", "first": 100, "after": "c"})):
        check_op(q, v)


def test_a365_project_source():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        proj = _project()
        fake = FakeA365Projects([_project("Other", "prj-0"), proj])
        src = [{"type": "a365_project", "ref": "main board", "workspace": WS}]
        r = _scan(root, libs, src, client=fake)
        assert r["errors"] == [], r
        assert _kinds(r["changes"]) == [("new", "0603WAF1002T5E"), ("new", "RES-0603-0R"), ("new", "TPS62133RGTR")], r
        c = next(c for c in r["changes"] if c["mpn"] == "TPS62133RGTR")
        assert c["key"] == "texas instruments|TPS62133RGTR" and c["line"]["datasheet"] == "https://x/tps.pdf"
        assert c["source"] == {"type": "a365_project", "ref": "main board", "name": "Main board"}
        res = next(c for c in r["changes"] if c["mpn"] == "0603WAF1002T5E")
        assert res["line"]["value"] == "10k" and res["line"]["designator"] == "R1, R2" and res["line"]["qty"] == "2"
        assert next(c for c in r["changes"] if c["mpn"] == "RES-0603-0R")["line"].get("dnp") is True
        assert fake.calls.count("bom") == 2                               # 4 items in pages of 2
        fake.calls.clear()
        assert _scan(root, libs, src, client=fake)["changes"] == []
        assert "bom" not in fake.calls                                    # updatedAt unchanged: BOM not fetched again
        # edited in Altium 365 and the project renamed: same state (by project id), one change
        proj["name"], proj["updatedAt"] = "Main board v2", "2026-10-15T08:00:00Z"
        proj["boms"]["[No Variations]"][1]["component"]["details"]["footprints"] = [{"name": "VQFN-16-1EP"}]
        r = _scan(root, libs, src, client=fake)
        assert _kinds(r["changes"]) == [("changed", "TPS62133RGTR")] and r["changes"][0]["source"]["name"] == "Main board v2", r
        # a variant
        r = _scan(root, libs, [dict(src[0], ref="P-0001", variant="Lite")], client=fake, dry_run=True)
        assert r["changes"] == []                                         # Lite only has CH340C, which the library has


def test_a365_errors_keep_state():
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        fake = FakeA365Projects([_project()])
        src = [{"type": "a365_project", "ref": "Main board", "workspace": WS}]
        _scan(root, libs, src, client=fake)
        st = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]
        sid = f"a365_project:{WS}#Main board"
        before = st[sid]["files"]

        class Down:
            def gql(self, *a):
                raise ConnectionError("api.nexar.com unreachable")
        r = _scan(root, libs, src, client=Down())
        assert r["changes"] == [] and "unreachable" in r["errors"][0]["error"]
        st = json.loads((root / "agent" / "watch_state.json").read_text())["sources"]
        assert st[sid]["files"] == before and st[sid]["error"]
        r = _scan(root, libs, [{"type": "a365_project", "ref": "Nope", "workspace": WS}], client=fake)
        assert "not found" in r["errors"][0]["error"]
        r = _scan(root, libs, [{"type": "a365_project", "ref": "Main board"}], client=fake)
        assert "workspace" in r["errors"][0]["error"]                     # none on the source nor in the rules
        old = os.environ.pop("NEXAR_TOKEN", None)
        try:
            r = _scan(root, libs, src)
            assert "NEXAR_TOKEN" in r["errors"][0]["error"]
        finally:
            if old is not None:
                os.environ["NEXAR_TOKEN"] = old
        r = _scan(root, libs, src, client=fake)                           # back: nothing lost, nothing new
        assert r["changes"] == [] and r["errors"] == []


def test_rules_validation_and_roundtrip():
    ok = {"agent": {"watch": {"sources": [{"type": "folder", "ref": "uploads", "exclude": ["old/*"]},
                                          {"type": "a365_project", "ref": "Main", "workspace": WS, "variant": "Lite"}],
                              "interval": 900, "include": ["*.csv"]}}}
    r = RU.load(ok)
    assert r["agent"]["watch"]["interval"] == 900 and r["agent"]["watch"]["exclude"] == []
    assert RU.load({})["agent"]["watch"]["sources"] == []
    for bad, msg in (({"sources": [{"type": "ftp", "ref": "x"}]}, "type"), ({"sources": [{"type": "folder"}]}, "ref"),
                     ({"sources": [{"type": "folder", "ref": "x", "colour": 1}]}, "colour"), ({"interval": 5}, "interval"),
                     ({"sources": "x"}, "sources"), ({"bogus": 1}, "bogus"), ({"include": "*.csv"}, "include")):
        try:
            RU.load({"agent": {"watch": bad}})
        except RU.RulesError as e:
            assert msg in str(e), (bad, e)
        else:
            raise AssertionError(f"accepted {bad}")
    text = RU.dumps(r)
    assert "[agent]" in text and RU.merge(RU.DEFAULTS, RU.parse_text(text))["agent"] == r["agent"], text


def test_workspace_mcp_and_cli():
    from ecadlib import cli, mcp_server as M
    from ecadlib.app import Workspace
    assert cli.COMMANDS["agent-watch"][0] == "ecadlib.agent_watch"
    assert "agent_watch_scan" in {t["name"] for t in M.TOOLS}
    with tempfile.TemporaryDirectory() as d:
        root, libs = _ws(d)
        ws = Workspace(root, libs=libs, offline=True)
        (root / "uploads" / "20261014-101010-aaaaaa").mkdir(parents=True)
        (root / "uploads" / "20261014-101010-aaaaaa" / "BOM.csv").write_text(BOM1)
        r = M.call_tool(ws, "agent_watch_scan", {"sources": [{"type": "folder", "ref": "uploads"}], "dry_run": True})
        assert len(r["changes"]) == 2 and r["state"] == "agent/watch_state.json" and r["dry_run"]
        assert not (root / "agent" / "watch_state.json").exists()
        assert len(ws.agent_watch_scan([{"type": "folder", "ref": "uploads"}])["changes"]) == 2
        assert ws.agent_watch_scan([{"type": "folder", "ref": "uploads"}])["changes"] == []
        (root / "ecadlib.toml").write_text('[agent.watch]\nsources = [{ type = "bom_file", ref = "x.csv" }]\n')
        (root / "x.csv").write_text(BOM1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["agent-watch", "--workspace", str(root), "--lib", libs[0], "--dry-run"])
        assert rc == 0 and "2 change(s) (dry run" in out.getvalue(), out.getvalue()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = cli.main(["agent-watch", "--workspace", str(root), "--lib", libs[0], "--json",
                           "--source", "bom_file:missing.csv"])
        assert rc == 1 and json.loads(out.getvalue())["sources"][0]["status"] == "error"


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_agent_watch"]))
