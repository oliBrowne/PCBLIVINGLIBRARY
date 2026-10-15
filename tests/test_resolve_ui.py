"""Held parts (resolve), sign-off (approvals) and duplicates (dedupe) through the front ends: Workspace, web UI, MCP.
Offline: the plan is tests.test_resolve.make_plan (three held parts) inside a workspace, and the KiCad library is
its fake HTTP (patched in for resolve's CachedHTTP).
Run:  python3 -m tests.test_resolve_ui"""
import contextlib, io, json, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import mcp_server as M, resolve as R
from ecadlib.app import Workspace
from tests.test_resolve import make_plan
from tests.test_ui import SPEC, serve, wait_job

BOM = Path("examples/sample_bom.csv")
NOSHIELD = "kicad:Connector:USB_C_Receptacle_Fake_4P_NoShield"
SHIELD = "kicad:Connector:USB_C_Receptacle_Fake_4P"
HDR = "kicad:Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical"
ANN, BOB = "Ann Lee <ann@example.com>", "Bob Roe <bob@example.com>"


@contextlib.contextmanager
def workspace():
    """Workspace with sample_bom uploaded and a resolve test plan as its librarian plan; resolve's HTTP is the fake."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [], providers=["digikey"], offline=True)
        (ws.root / "uploads" / "sample_bom.csv").write_bytes(BOM.read_bytes())   # flat upload: librarian/sample_bom
        bom = "uploads/sample_bom.csv"
        plan, src = make_plan(ws.root / "librarian" / "sample_bom")
        with mock.patch.object(R, "CachedHTTP", lambda *a, **k: src.http):
            yield ws, bom, plan


def raises(fn, *a, contains=""):
    try:
        fn(*a)
    except ValueError as e:
        assert contains in str(e), (contains, str(e))
        return
    raise AssertionError(f"{fn.__name__}{a} did not raise")


def test_workspace_held_candidates_choose():
    with workspace() as (ws, bom, plan):
        h = ws.held_parts(bom)
        assert {x["name"] for x in h["held"]} == {"USBC-FAKE", "FTSH-105-TEST", "FAKEFET1"} and h["plan"] == "librarian/sample_bom/plan.json", h
        seen = []
        c = ws.part_candidates(bom, "USBC-FAKE", progress=lambda *a: seen.append(a))
        assert list(c["candidates"]) == ["symbol"] and c["item"]["missing"] == ["symbol"], c["item"]
        top = c["candidates"]["symbol"][0]
        assert top["id"] == NOSHIELD and top["match"] is True and top["count"] == 4, top
        near = next(x for x in c["candidates"]["symbol"] if x["id"] == SHIELD)
        assert near["pin_map"] == {"S1": "SH"} and seen[0][:2] == (0, 1) and seen[-1][:2] == (1, 1), (near, seen)
        assert len(ws.part_candidates(bom, "USBC-FAKE", "footprint", 2)["candidates"]["footprint"]) <= 2
        raises(ws.part_candidates, bom, "NOPE", contains="not a part")
        raises(ws.part_candidates, bom, "USBC-FAKE", "both", contains="kind must be")
        for bad in ("spec:../../etc/passwd", "local:..:x", "kicad:a/b:c", "file:/etc/passwd", "spec:", "kicad:Lib"):
            raises(ws.choose_part, bom, "USBC-FAKE", "symbol", bad, contains="candidate id")
        raises(ws.choose_part, bom, "USBC-FAKE", "symbol", NOSHIELD, {"S1": 3}, contains="pin_map")
        other = ws.save_upload("other.csv", BOM.read_bytes())
        raises(ws.held_parts, other, contains="run the librarian")

        r = ws.choose_part(bom, "USBC-FAKE", "symbol", NOSHIELD)
        assert r["chosen"]["status"] == "ready" and r["chosen"]["source"] == NOSHIELD, r["chosen"]
        assert next(x for x in r["review"] if x["part"] == "USBC-FAKE")["status"] == "ready" and r["ready"] == 1, r["review"]
        assert {"librarian/sample_bom/review.md", "librarian/sample_bom/release_check.md"} <= set(r["reports"]), r["reports"]
        assert "USBC-FAKE" in (plan.parent / "review.md").read_text()
        r = ws.choose_part(bom, "FTSH-105-TEST", "footprint", HDR)
        assert r["chosen"]["status"] == "ready" and r["ready"] == 2 and r["held"] == 1, r
        assert {x["name"] for x in ws.held_parts(bom)["held"]} == {"FAKEFET1"}


def test_workspace_approvals():
    with workspace() as (ws, bom, plan):
        st = ws.approval_status(bom)
        assert st["policy"] is None and st["policy_file"] is None and len(st["rows"]) == 3, st
        (plan.parent / "approvals.json").write_text('{"required": 1}')
        ws.choose_part(bom, "FTSH-105-TEST", "footprint", HDR)
        r = ws.choose_part(bom, "USBC-FAKE", "symbol", SHIELD)                    # S1 has no pad: held on pins
        assert r["chosen"]["status"] == "held"
        r = ws.release_check(bom, allow=["USBC-FAKE"], user=BOB)                    # Bob releases it anyway
        assert {x["part"]: x["status"] for x in r["review"]}["USBC-FAKE"] == "awaiting_approval", r["review"]
        assert r["awaiting_approval"] == 2 and r["ready"] == 0, r
        assert json.loads(plan.read_text())[0]["allow"]["user"] == BOB
        raises(ws.approve_part, bom, "USBC-FAKE", "approve", "  ", contains="user is required")
        raises(ws.approve_part, bom, "USBC-FAKE", "approve", None, contains="user is required")
        raises(ws.approve_part, bom, "USBC-FAKE", "maybe", ANN, contains="approve or reject")
        raises(ws.approve_part, bom, "USBC-FAKE", "approve", BOB, contains="cannot approve")     # no_self_approval
        raises(ws.approve_part, bom, "FTSH-105-TEST", "reject", ANN, contains="needs a comment")
        a = ws.approve_part(bom, "USBC-FAKE", "approve", ANN, None, "pinout checked")
        assert a["decision"] == {"part": "USBC-FAKE", "decision": "approve", "who": ANN, "role": "", "status": "ready", "enforced": True}, a["decision"]
        row = next(x for x in a["review"] if x["part"] == "USBC-FAKE")
        assert row["status"] == "ready" and row["approval"]["approved_by"] == [ANN] and a["awaiting_approval"] == 1, row
        st = ws.approval_status(bom)
        assert st["policy_file"] == "librarian/sample_bom/approvals.json" and st["awaiting_approval"] == 1, st
        assert st["audit"] == "librarian/sample_bom/approvals_audit.jsonl"
        log = [json.loads(l) for l in (plan.parent / "approvals_audit.jsonl").read_text().splitlines()]
        assert [x["result"].split(":")[0] for x in log] == ["refused", "refused", "recorded"], log
        assert all(x["via"] == "ecadlib ui/mcp" for x in log) and log[-1]["who"] == ANN
        push = {x["name"]: x["action"] for x in ws.altium365_push(bom, None, dry_run=True)["results"]}
        assert push["USBC-FAKE"] == "release" and push["FTSH-105-TEST"] == "skip", push


def test_workspace_dedupe():
    with workspace() as (ws, bom, plan):
        items = json.loads(plan.read_text())
        dup = json.loads(json.dumps(items[0]))
        dup["name"] = "USBC-FAKE-TR"
        dup["parameters"] = [p if p["name"] != "Manufacturer Part Number" else dict(p, value="USBC-FAKE/TR") for p in dup["parameters"]]
        plan.write_text(json.dumps(items + [dup], indent=2))
        spec = dict(SPEC("SOT-23"), name="MY_SOT23")                               # same land pattern, other name
        ws.approve_footprint(spec)
        ws.choose_part(bom, "FAKEFET1", "footprint", "spec:SOT-23")
        lib = sorted(p.read_bytes() for p in (ws.root / "altium_models").rglob("*") if p.is_file())
        r = ws.find_duplicates(bom)
        kinds = {g["kind"]: g for g in r["groups"]}
        assert kinds["mpn"]["keeper"] in ("USBC-FAKE", "USBC-FAKE-TR") and len(kinds["mpn"]["members"]) == 2, kinds
        fp = kinds["footprint"]
        assert fp["keeper"] == "MY_SOT23" and {m["name"] for m in fp["members"]} == {"MY_SOT23", "SOT-23"}, fp
        assert all(not f.startswith("/") for m in fp["members"] for f in m.get("files", [])), fp   # workspace-relative
        assert r["reports"] == ["librarian/sample_bom/dedupe.md", "librarian/sample_bom/dedupe_map.csv"] and not r["merged"]
        assert "duplicate_of" not in plan.read_text()                              # no merge: plan untouched
        m = ws.find_duplicates(bom, merge=True)
        assert m["merged"] and m["summary"]["duplicates"] == 1, m["summary"]
        after = {i["name"]: i for i in json.loads(plan.read_text())}
        dupname = next(n for n in ("USBC-FAKE", "USBC-FAKE-TR") if after[n].get("duplicate_of"))
        assert after[dupname]["status"] == "duplicate" and (plan.parent / "plan.json.pre-dedupe").exists()
        assert after["FAKEFET1"]["footprint"]["ref"] == "MY_SOT23", after["FAKEFET1"]["footprint"]
        assert sorted(p.read_bytes() for p in (ws.root / "altium_models").rglob("*") if p.is_file()) == lib   # library untouched
        row = next(x for x in m["summary"]["review"] if x["part"] == dupname)
        assert row["status"] == "duplicate" and row["duplicate_of"], row


def test_ui_endpoints():
    with workspace() as (ws, bom, plan):
        srv, call = serve(ws)
        try:
            st, h = call("POST", "/api/resolve/held", {"bom": bom})
            assert st == 200 and len(h["held"]) == 3, h
            st, e = call("POST", "/api/resolve/held", {"bom": str(BOM.resolve())})
            assert st == 400 and "outside workspace" in e["error"], e
            st, e = call("POST", "/api/resolve/candidates", {"bom": "uploads/none.csv", "part": "USBC-FAKE"})
            assert st == 400 and "run the librarian" in e["error"], e
            assert call("POST", "/api/resolve/candidates", {"bom": bom})[0] == 400                      # no part
            st, j = call("POST", "/api/resolve/candidates", {"bom": bom, "part": "USBC-FAKE"})
            assert st == 200 and "job" in j, j
            res = wait_job(call, j["job"])
            assert res["state"] == "done" and res["result"]["candidates"]["symbol"][0]["id"] == NOSHIELD, res
            st, j = call("POST", "/api/resolve/candidates", {"bom": bom, "part": "NOPE"})
            res = wait_job(call, j["job"])
            assert res["state"] == "error" and "not a part" in res["error"], res
            st, e = call("POST", "/api/resolve/choose", {"bom": bom, "part": "USBC-FAKE", "kind": "symbol", "id": "spec:../../x"})
            assert st == 400 and "candidate id" in e["error"], e
            st, r = call("POST", "/api/resolve/choose", {"bom": bom, "part": "USBC-FAKE", "kind": "symbol", "id": SHIELD,
                                                         "pin_map": {"S1": "SH"}})
            assert st == 200 and r["chosen"]["status"] == "ready" and r["ready"] == 1, r

            st, s = call("POST", "/api/approvals/status", {"bom": bom})
            assert st == 200 and s["policy"] is None, s
            st, e = call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve"})
            assert st == 400 and "user" in e["error"], e                               # never filled in from git
            st, e = call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve", "user": " "})
            assert st == 400, e
            st, r = call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve", "user": ANN, "role": None})
            assert st == 200 and r["decision"]["who"] == ANN and not r["decision"]["enforced"], r
            st, d = call("POST", "/api/dedupe", {"bom": bom})
            assert st == 200 and d["merged"] is False and "groups" in d, d
            st, page = call("GET", "/")
            for needle in (b'id="pickdlg"', b"/api/resolve/candidates", b"/api/resolve/choose", b'id="signer"',
                           b"/api/approvals/decide", b"/api/dedupe", b"There is no login"):
                assert needle in page, needle
        finally:
            srv.shutdown()


def test_mcp_tools():
    names = {t["name"] for t in M.TOOLS}
    assert {"held_parts", "part_candidates", "choose_part", "approval_status", "approve_part", "find_duplicates"} <= names
    assert "user" in next(t for t in M.TOOLS if t["name"] == "approve_part")["inputSchema"]["required"]
    with workspace() as (ws, bom, plan):
        call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
        lines = [call(1, "held_parts", {"bom": bom}),
                 call(2, "part_candidates", {"bom": bom, "part": "FTSH-105-TEST", "limit": 3}),
                 call(3, "choose_part", {"bom": bom, "part": "FTSH-105-TEST", "kind": "footprint", "candidate_id": HDR}),
                 call(4, "approve_part", {"bom": bom, "part": "FTSH-105-TEST", "decision": "approve"}),
                 call(5, "approve_part", {"bom": bom, "part": "FTSH-105-TEST", "decision": "approve", "user": ANN}),
                 call(6, "approval_status", {"bom": bom}),
                 call(7, "find_duplicates", {"bom": bom}),
                 call(8, "choose_part", {"bom": bom, "part": "FTSH-105-TEST", "kind": "footprint", "candidate_id": "spec:../x"})]
        out = io.BytesIO()
        M.serve(ws, io.BytesIO("\n".join(map(json.dumps, lines)).encode() + b"\n"), out)
        resp = {r["id"]: r for r in map(json.loads, out.getvalue().decode().splitlines())}
        body = lambda i: json.loads(resp[i]["result"]["content"][0]["text"])
        assert len(body(1)["held"]) == 3
        fps = body(2)["candidates"]["footprint"]
        assert fps[0]["id"] == HDR and len(fps) <= 3, fps
        assert body(3)["chosen"]["status"] == "ready" and "log" not in body(3)
        assert resp[4]["error"]["code"] == -32602 and "user" in resp[4]["error"]["message"], resp[4]
        assert body(5)["decision"]["who"] == ANN and "log" not in body(5)
        assert next(r for r in body(6)["rows"] if r["name"] == "FTSH-105-TEST")["approved_by"] == ANN
        assert body(7)["merged"] is False
        assert resp[8]["result"]["isError"] and "candidate id" in resp[8]["result"]["content"][0]["text"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
