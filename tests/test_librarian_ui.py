"""The librarian and release check through the front ends (Workspace, web UI, MCP), plus the footprint_check
requests from the pipeline thread (KiCad 6+ circles, A1 as pin 1). Offline: only the local example library.
Run:  python3 -m tests.test_librarian_ui"""
import io, json, tempfile
from pathlib import Path

from ecadlib import footprint_check as C, footprint_gen as F, mcp_server as M
from ecadlib.app import Workspace
from tests.test_ui import SPEC, serve, wait_job

BOM = Path("examples/sample_bom.csv")


def ws_with_bom(d):
    ws = Workspace(d, ["examples/kicad_lib"], providers=["digikey"], offline=True)
    (ws.root / "uploads" / "sample_bom.csv").write_bytes(BOM.read_bytes())    # flat (pre-folder) upload: reports keep the plain name
    return ws, "uploads/sample_bom.csv"


def test_checker_reads_kicad6_circles_and_a1():
    txt, _ = F.build(SPEC("SOIC-8_3.9x4.9mm_P1.27mm"))
    circle = txt.rstrip()[:-1] + '  (fp_circle (center 0 0) (end 0.5 0) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab")))\n'
    assert C.check(circle) == []                                       # used to KeyError on (center ...)
    old = txt.rstrip()[:-1] + '  (fp_circle (start 0 0) (end 0.5 0) (stroke (width 0.1) (type solid)) (layer "F.Fab")))\n'
    assert C.check(old) == []                                          # KiCad 5 form still read
    a1 = txt.replace('(pad "1" ', '(pad "A1" ')
    assert not [m for _, r, m in C.check(a1) if r == "pin1"], C.check(a1)
    none = txt.replace('(pad "1" ', '(pad "Z" ')
    assert any("no pad numbered 1" in m for _, _, m in C.check(none))


def test_workspace_librarian_and_release_check():
    with tempfile.TemporaryDirectory() as d:
        ws, bom = ws_with_bom(d)
        try:
            ws.release_check(bom)
            raise AssertionError("release check ran without a plan")
        except ValueError as e:
            assert "run the librarian" in str(e)
        seen = []
        r = ws.librarian_run(bom, sources=["kicad"], progress=lambda done, total, label: seen.append((done, total)))
        assert r["parts"] == 14 and r["ready"] + r["held"] == 14 and len(r["review"]) == 14, r
        assert seen[0] == (0, 14) and seen[-1][0] == 14, seen[-3:]
        assert {"librarian/sample_bom/review.md", "librarian/sample_bom/plan.json"} <= set(r["reports"]), r["reports"]
        held = next(x["part"] for x in r["review"] if x["status"] == "held")
        r2 = ws.release_check(bom, allow=[held], user="Ann Lee")
        assert next(x for x in r2["review"] if x["part"] == held)["allowed"] is True
        r3 = ws.release_check(bom, unallow=[held])
        assert next(x for x in r3["review"] if x["part"] == held)["allowed"] is False
        try:
            ws.release_check(bom, allow=["NOT-A-PART"], user="Ann Lee")
            raise AssertionError("unknown part accepted")
        except ValueError as e:
            assert "NOT-A-PART" in str(e)
        # the Altium 365 plan and push now come from the librarian, and held parts are skipped
        p = ws.altium365_plan(bom)
        assert p["source"] == "librarian" and p["total"] == 14 and p["plan"].startswith("librarian/"), p["plan"]
        push = ws.altium365_push(bom, None, dry_run=True)
        heldn = sum(i["status"] == "held" for i in p["items"])
        assert sum(x["action"] == "skip" for x in push["results"]) >= heldn, push["results"][:2]
        assert push["report"] == "altium365/push_dry_run_sample_bom.json"
        again = ws.librarian_run(bom, sources=["kicad"])                 # resumable: same answer on a re-run
        assert again["ready"] == r["ready"]


def test_ui_librarian_endpoints():
    with tempfile.TemporaryDirectory() as d:
        ws, bom = ws_with_bom(d)
        srv, call = serve(ws)
        try:
            st, e = call("POST", "/api/release-check", {"bom": bom})
            assert st == 400 and "run the librarian" in e["error"], e
            st, j = call("POST", "/api/librarian/run", {"bom": bom, "sources": ["kicad"]})
            assert st == 200 and "job" in j, j
            res = wait_job(call, j["job"])
            assert res["state"] == "done" and res["result"]["parts"] == 14, res
            held = next(x["part"] for x in res["result"]["review"] if x["status"] == "held")
            st, r = call("POST", "/api/release-check", {"bom": bom, "allow": [held], "user": "Ann Lee"})
            assert st == 200 and any(x["allowed"] for x in r["review"]), r
            assert call("POST", "/api/release-check", {"bom": bom, "allow": held, "user": "Ann Lee"})[0] == 400     # must be a list
            st, e = call("POST", "/api/librarian/run", {"bom": bom, "sources": ["nope"]})
            res = wait_job(call, e["job"])
            assert res["state"] == "error" and "unknown sources" in res["error"], res
            st, e = call("POST", "/api/librarian/run", {"bom": str(BOM.resolve())})
            assert st == 400 and "outside workspace" in e["error"], e
            st, page = call("GET", "/")
            assert b'id="librun"' in page and b"/api/librarian/run" in page
        finally:
            srv.shutdown()


def test_mcp_librarian_tools():
    names = {t["name"] for t in M.TOOLS}
    assert {"librarian_run", "release_check"} <= names
    with tempfile.TemporaryDirectory() as d:
        ws, bom = ws_with_bom(d)
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "release_check", "arguments": {"bom": bom}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "librarian_run",
                                                                                 "arguments": {"bom": bom, "sources": ["kicad"]}}},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "librarian_run",
                                                                                 "arguments": {"bom": bom, "sources": ["kicad"], "refresh": True}}}]
        out = io.BytesIO()
        M.serve(ws, io.BytesIO("\n".join(map(json.dumps, lines)).encode() + b"\n"), out)
        resp = [json.loads(l) for l in out.getvalue().decode().splitlines()]
        assert resp[0]["result"]["isError"] and "run the librarian" in resp[0]["result"]["content"][0]["text"]
        for r in resp[1:]:
            body = json.loads(r["result"]["content"][0]["text"])
            assert not r["result"]["isError"] and body["parts"] == 14 and "log" not in body, body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
