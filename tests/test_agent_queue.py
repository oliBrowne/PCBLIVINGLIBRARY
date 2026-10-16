"""Library agent review queue (ecadlib/agent_queue.py): state machine, dedupe, locking across threads and processes,
batches, approvals.py integration, the web routes per role (real server, team and local mode), picture containment,
CSRF, MCP tools and the CLI. Offline.
Run:  python3 -m tests.test_agent_queue"""
import contextlib, io, json, os, subprocess, sys, tempfile, threading
from pathlib import Path

from ecadlib import agent_queue as AQ, mcp_server as M
from ecadlib.app import Workspace

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00"
       b"\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82")


def change(mpn="LM358DR", mfr="Texas Instruments", kind="new", value="LM358", detected="2026-10-15T06:48:46Z"):
    return {"key": AQ.part_key(mfr, mpn), "mpn": mpn, "manufacturer": mfr, "kind": kind,
            "source": {"type": "bom_file", "ref": "boms/main.csv", "name": "main.csv"},
            "line": {"mpn": mpn, "manufacturer": mfr, "value": value, "designator": "U1", "qty": 1},
            "was": {}, "detected": detected}


def raises(exc, fn, *a, contains="", **k):
    try:
        fn(*a, **k)
    except exc as e:
        assert contains in str(e), (contains, str(e))
        return e
    raise AssertionError(f"{fn} did not raise {exc.__name__}")


def walk(q, i, *states, user="ecadlib-agent"):
    for s in states:
        q.transition(i, s, user=user, note=f"to {s}")


# ------------------------------------------------------------------ state machine
def test_state_machine_and_illegal_moves():
    with tempfile.TemporaryDirectory() as d:
        q = AQ.Queue(d)
        it = q.upsert(change())
        assert it["status"] == "detected" and it["key"] == "texas instruments|LM358DR" and it["origin"] == "watch"
        assert it["created"].endswith("Z") and len(it["created"]) == 20 and (Path(d) / "agent/queue/items" / f"{it['id']}.json").is_file()
        for bad in ("approved", "pushed", "review", "proving", "detected", "nonsense"):
            raises(ValueError, q.transition, it["id"], bad, user="x")
        walk(q, it["id"], "building", "proving", "review")
        raises(ValueError, q.transition, it["id"], "detected", user="x", contains="cannot go from review to detected")
        raises(ValueError, q.transition, it["id"], "pushed", user="x")
        raises(ValueError, q.transition, it["id"], "approved", contains="who decides")       # approve names a person
        walk(q, it["id"], "building", "proving", "review")                                    # rebuild loop
        q.transition(it["id"], "approved", user="Ann <ann@x.com>", note="looks right")
        it = q.transition(it["id"], "pushed", user="ecadlib-agent")
        for s in AQ.STATES:
            raises(ValueError, q.transition, it["id"], s, user="x")                           # pushed is final
        h = it["history"]
        assert [x["to"] for x in h] == ["detected", "building", "proving", "review", "building", "proving", "review", "approved", "pushed"]
        assert h[0]["from"] is None and all(a["to"] == b["from"] for a, b in zip(h, h[1:])) and h[7]["user"] == "Ann <ann@x.com>"
        assert set(h[1]) == {"at", "from", "to", "user", "note"} and h[7]["note"] == "looks right"
        # failed / rejected from any open state, then retry
        for stop in ("detected", "building", "proving", "review", "approved"):
            o = q.upsert({"mpn": f"P-{stop}", "manufacturer": "", "origin": "manual"})
            assert o["key"] == f"|P-{stop.upper()}"
            walk(q, o["id"], *AQ.OPEN[1:AQ.OPEN.index(stop) + 1])
            end = "failed" if len(stop) % 2 else "rejected"
            q.transition(o["id"], end, user="Ann", note="x")
            raises(ValueError, q.transition, o["id"], "building", user="x")
            assert q.transition(o["id"], "detected", user="x", note="retry")["status"] == "detected"
        raises(ValueError, q.update, it["id"], status="detected")                            # state only via transition
        raises(ValueError, q.update, it["id"], history=[])
        u = q.update(it["id"], build={"sources": ["kicad"], "notes": "ok"}, push={"ok": True})
        assert u["build"]["notes"] == "ok" and u["push"] == {"ok": True} and q.get(it["id"])["status"] == "pushed"
        raises(ValueError, q.get, "../../etc/passwd")
        raises(KeyError, q.get, "nope")
        assert raises(ValueError, q.list, "bogus") and {x["status"] for x in q.list("detected")} == {"detected"}


def test_self_approval_refused_in_transition():
    with tempfile.TemporaryDirectory() as d:
        q = AQ.Queue(d)
        i = q.upsert(change())["id"]
        walk(q, i, "building", "proving", "review")
        q.update(i, build={"user": "Bob Roe <bob@example.com>"})
        raises(PermissionError, q.transition, i, "approved", user="bob roe <BOB@example.com>", contains="no self-approval")
        assert q.transition(i, "approved", user="Ann <ann@example.com>")["status"] == "approved"


# ------------------------------------------------------------------ dedupe / upsert
def test_upsert_dedupes_by_part_key():
    with tempfile.TemporaryDirectory() as d:
        q = AQ.Queue(d)
        a = q.upsert(change())
        b = q.upsert({**change(detected="2026-10-16T00:00:00Z"), "mpn": " lm358dr ", "manufacturer": "  TEXAS INSTRUMENTS"})
        assert a["id"] == b["id"] and len(q.list()) == 1 and b["history"][-1]["note"] == "re-detected", b["history"]
        c = q.upsert(change(kind="changed", value="LM358 new"))
        assert c["id"] == a["id"] and c["change"]["kind"] == "changed" and "different change" in c["history"][-1]["note"]
        # fields form (agent.py / upkeep): no top-level kind
        f = q.upsert({"key": "|NE555", "mpn": "NE555", "manufacturer": "", "origin": "upkeep", "change": {"check": "lifecycle", "what": "NRND"}})
        assert f["origin"] == "upkeep" and f["key"] == "|NE555" and len(q.list()) == 2
        raises(ValueError, q.upsert, {"key": "ti|X", "mpn": "Y", "manufacturer": "ti"}, contains="does not match")
        raises(ValueError, q.upsert, {"mpn": " ", "manufacturer": "ti"}, contains="MPN")
        raises(ValueError, q.upsert, {"mpn": "Z", "origin": "robot"}, contains="origin")
        # rejected: the same change again leaves it rejected; a different change re-opens it
        walk(q, f["id"], "building")
        q.transition(f["id"], "rejected", user="Ann", note="wrong part")
        g = q.upsert({"mpn": "NE555", "manufacturer": "", "origin": "upkeep", "change": {"check": "lifecycle", "what": "NRND"}})
        assert g["id"] == f["id"] and g["status"] == "rejected"
        g = q.upsert({"mpn": "NE555", "manufacturer": "", "origin": "upkeep", "change": {"check": "lifecycle", "what": "EOL"}})
        assert g["id"] == f["id"] and g["status"] == "detected" and g["history"][-1]["from"] == "rejected"
        # pushed: a later change is a new item
        walk(q, a["id"], "building", "proving", "review")
        q.transition(a["id"], "approved", user="Ann")
        q.transition(a["id"], "pushed", user="agent")
        n = q.upsert(change(kind="changed", value="again"))
        assert n["id"] != a["id"] and n["status"] == "detected" and len(q.list()) == 3
        assert q.find("texas instruments|LM358DR")["id"] == n["id"]


# ------------------------------------------------------------------ concurrency
def test_concurrent_writers_threads():
    with tempfile.TemporaryDirectory() as d:
        errs = []

        def worker(n):
            try:
                q = AQ.Queue(d)                                   # a Queue per thread: the lock is per folder
                for k in range(15):
                    q.upsert(change(detected=f"t{n}-{k}"))        # the same part from every thread
                    q.upsert({"mpn": f"T{n}-{k}", "manufacturer": "acme"})
            except Exception as e:                                # pragma: no cover
                errs.append(e)
        ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errs, errs
        q = AQ.Queue(d)
        items = q.list()
        assert len(items) == 1 + 8 * 15, len(items)
        lm = q.find("texas instruments|LM358DR")
        assert len(lm["history"]) == 8 * 15, len(lm["history"])     # no lost update: every upsert left its entry
        assert not list((Path(d) / "agent/queue/items").glob("*.tmp")) and not list((Path(d) / "agent/queue/items").glob(".*tmp"))


CHILD = """
import sys
from ecadlib import agent_queue as AQ
q = AQ.Queue(sys.argv[1])
for k in range(40):
    q.upsert({"mpn": "SHARED1", "manufacturer": "acme", "change": {"n": sys.argv[2] + str(k)}})
    with q.lock:                                     # read-modify-write across processes under the lock
        it = q.find("acme|SHARED1")
        q.update(it["id"], counter=(it.get("counter") or 0) + 1)
"""


def test_concurrent_writers_two_processes():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        ps = [subprocess.Popen([sys.executable, "-c", CHILD, d, tag], env=env, stderr=subprocess.PIPE) for tag in ("a", "b")]
        outs = [p.communicate(timeout=120)[1] for p in ps]
        assert all(p.returncode == 0 for p in ps), outs
        q = AQ.Queue(d)
        items = q.list()
        assert len(items) == 1, len(items)
        assert len(items[0]["history"]) == 80 and items[0]["counter"] == 80, (len(items[0]["history"]), items[0].get("counter"))


# ------------------------------------------------------------------ batches
def test_queue_accepts_path_or_workspace():
    with tempfile.TemporaryDirectory() as d:                     # a Path's .root is "/": must not be taken for a Workspace
        for w in (d, Path(d), Workspace(d)):
            assert AQ.Queue(w).dir == Path(d).resolve() / "agent" / "queue"


def test_batches():
    with tempfile.TemporaryDirectory() as d:
        q = AQ.Queue(d)
        ids = [q.upsert({"mpn": f"B{n}"})["id"] for n in range(3)]
        raises(KeyError, q.batch, ids + ["missing1"])
        assert all(q.get(i)["batch"] is None for i in ids) and not q.batches()        # nothing half-written
        raises(ValueError, q.batch, [])
        b = q.batch(ids[:2], note="weekly")
        assert set(b) >= {"id", "items", "created", "pr_url", "status"} and b["status"] == "open" and b["note"] == "weekly"
        assert [q.get(i)["batch"] for i in ids] == [b["id"], b["id"], None]
        assert {x["id"] for x in q.list(batch=b["id"])} == set(ids[:2])
        bt = q.update_batch(b["id"], pr_url="https://github.com/o/r/pull/7", status="pr_open")
        assert bt["status"] == "pr_open" and q.get(ids[0])["pr_url"] == "https://github.com/o/r/pull/7"
        assert q.get_batch(b["id"])["pr_url"].endswith("/7") and [x["id"] for x in q.batches()] == [b["id"]]
        r = AQ.ws_list(d, batch=b["id"])
        assert r["batch"]["id"] == b["id"] and len(r["items"]) == 2 and r["batches"][0]["count"] == 2


# ------------------------------------------------------------------ approvals.py integration
def _review_item(ws, bom, part, builder="ecadlib-agent", pictures=None):
    q = AQ.Queue(ws.root)
    it = q.upsert(change(mpn=part, mfr=""))
    walk(q, it["id"], "building", "proving", "review")
    q.update(it["id"], build={"bom": bom, "part": part, "user": builder, "sources": ["kicad"], "notes": "from KiCad"},
             proof={"verdict": "doubt", "score": 0.6, "reason": "pin 1 marker unclear", "model": "claude-x", "cost_usd": 0.01,
                    "findings": [{"severity": "warning", "what": "pin 1", "expected": "dot", "found": "none", "page": 3}],
                    "images": {"ours": [], "datasheet": []}},
             pictures=pictures or {"before": [], "after": []})
    return it["id"]


def test_approve_records_through_approvals_py():
    from tests.test_resolve_ui import workspace, ANN, BOB
    with workspace() as (ws, bom, plan):
        (plan.parent / "approvals.json").write_text('{"required": 1}')
        i = _review_item(ws, bom, "FAKEFET1", builder=BOB)
        raises(PermissionError, ws.agent_queue_decide, i, "approve", "bob roe <bob@example.com>", contains="self-approval")
        raises(ValueError, ws.agent_queue_decide, i, "approve", "", contains="user is required")
        raises(ValueError, ws.agent_queue_decide, i, "reject", ANN, contains="needs a note")
        raises(ValueError, ws.agent_queue_decide, i, "retry", ANN)                            # review: use rebuild
        r = ws.agent_queue_decide(i, "approve", ANN, None, "pinout checked")
        assert r["item"]["status"] == "approved" and r["approval"]["recorded"] is True, r
        item = next(x for x in json.loads(plan.read_text()) if x["name"] == "FAKEFET1")
        assert item["approvals"][-1]["who"] == ANN and item["approvals"][-1]["comment"] == "pinout checked"
        st = next(x for x in ws.approval_status(bom)["rows"] if x["name"] == "FAKEFET1")
        assert ANN in st["approved_by"], st                                                    # release check sees it
        log = (plan.parent / "approvals_audit.jsonl").read_text().splitlines()
        assert json.loads(log[-1])["via"] == "ecadlib ui/mcp (agent queue)"
        raises(ValueError, ws.agent_queue_decide, i, "approve", ANN, contains="only items in review")
        # a policy refusal blocks the approve and leaves the queue item in review
        (plan.parent / "approvals.json").write_text('{"required": 1, "users": {"bob@example.com": ["librarian"]}}')
        j = _review_item(ws, bom, "USBC-FAKE")
        raises(ValueError, ws.agent_queue_decide, j, "approve", ANN, contains="the library plan refused this approval")
        assert AQ.Queue(ws.root).get(j)["status"] == "review"
        # reject is kept even when the policy refuses to record it; rebuild and retry
        r = ws.agent_queue_decide(j, "reject", ANN, None, "wrong pinout")
        assert r["item"]["status"] == "rejected" and r["approval"]["recorded"] is False and r["item"]["rejection"]["note"] == "wrong pinout"
        assert ws.agent_queue_decide(j, "retry", ANN)["item"]["status"] == "detected"
        k = _review_item(ws, bom, "FTSH-105-TEST")
        r = ws.agent_queue_decide(k, "rebuild", BOB, None, "use the 2x5 header")
        assert r["item"]["status"] == "building" and r["item"]["build"]["requested_by"] == BOB and r["item"]["build"]["bom"] == bom
        # no plan reference: decided in the queue only, and the result says so
        q = AQ.Queue(ws.root)
        n = q.upsert({"mpn": "NOPLAN1"})["id"]
        walk(q, n, "building", "proving", "review")
        r = ws.agent_queue_decide(n, "approve", ANN)
        assert r["item"]["status"] == "approved" and not r["approval"]["recorded"] and "queue only" in r["approval"]["reason"]


# ------------------------------------------------------------------ web server: roles, CSRF, pictures
def _seed_pictures(ws):
    pics = ws.root / "agent" / "pictures"
    pics.mkdir(parents=True, exist_ok=True)
    (pics / "before.png").write_bytes(PNG)
    (pics / "after.png").write_bytes(PNG)
    (pics / "evil.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    (ws.root / "reports" / "outside.png").write_bytes(PNG)
    with contextlib.suppress(OSError):
        os.symlink("/etc/passwd", pics / "link.png")
    return {"before": [str(pics / "before.png")], "after": ["agent/pictures/after.png", "reports/outside.png"]}


def test_server_routes_per_role_and_pictures():
    from tests.test_resolve_ui import workspace
    from tests.test_team import Client, server
    with workspace() as (ws, bom, plan), server(ws) as (_, team, port):
        pics = _seed_pictures(ws)
        i = _review_item(ws, bom, "FAKEFET1", pictures=pics)
        j = _review_item(ws, bom, "USBC-FAKE", builder="Apr Prover <apr@example.com>")
        k = _review_item(ws, bom, "FTSH-105-TEST")
        anon, vic, lib, apr = Client(port), Client(port), Client(port), Client(port)
        vic.login("vic"), lib.login("lib"), apr.login("apr")
        assert anon.call("GET", "/api/agent/queue")[0] == 401
        st, r = vic.call("GET", "/api/agent/queue")
        assert st == 200 and r["counts"]["review"] == 3 and len(r["items"]) == 3, r
        assert vic.call("GET", "/api/agent/queue?status=review")[1]["items"][0]["verdict"] == "doubt"
        st, r = vic.call("GET", f"/api/agent/queue/item?id={i}")
        assert st == 200 and r["item"]["proof"]["findings"][0]["what"] == "pin 1" and "approved" in r["allowed"], r
        assert r["pictures"]["before"] == ["agent/pictures/before.png"] and r["pictures"]["after"] == ["agent/pictures/after.png", None], r["pictures"]
        assert vic.call("GET", "/api/agent/queue/item?id=nope1")[0] == 404
        assert vic.call("GET", "/api/agent/queue/item?id=../x")[0] == 400
        # pictures: images inside agent/ only
        st, img = vic.call("GET", "/api/agent/queue/picture?path=agent/pictures/before.png")
        assert st == 200 and img == PNG and vic.last_headers["content-type"] == "image/png", st
        assert vic.last_headers["x-content-type-options"] == "nosniff" and "attachment" not in vic.last_headers.get("content-disposition", "")
        for bad in ("reports/outside.png", "agent/pictures/evil.svg", "agent/pictures/link.png", "agent/../reports/outside.png",
                    "../../../etc/passwd", "/etc/passwd", "agent/pictures", "team_users.json", "agent/pictures/missing.png", ""):
            st, e = vic.call("GET", "/api/agent/queue/picture?path=" + bad.replace("/", "%2F"))
            assert st == 400 and e["error"] != "not found", (bad, st, e)
        assert anon.call("GET", "/api/agent/queue/picture?path=agent/pictures/before.png")[0] == 401
        # viewer reads only
        for act in ("approve", "reject", "rebuild"):
            st, e = vic.call("POST", f"/api/agent/queue/{act}", {"id": i, "note": "x"})
            assert st == 403 and "needs the" in e["error"], (act, st, e)
        # librarian: reject and rebuild, not approve
        st, e = lib.call("POST", "/api/agent/queue/approve", {"id": i})
        assert st == 403 and "approver" in e["error"], e
        st, e = lib.call("POST", "/api/agent/queue/reject", {"id": k})
        assert st == 400 and "note" in e["error"], e
        st, r = lib.call("POST", "/api/agent/queue/reject", {"id": k, "note": "<b>wrong</b> pinout", "user": "Mallory <m@evil>"})
        assert st == 200 and r["item"]["status"] == "rejected" and r["item"]["rejection"]["by"] == "Lib Rarian <lib@example.com>", r
        st, r = lib.call("POST", "/api/agent/queue/rebuild", {"id": k})                       # rejected -> retry
        assert st == 200 and r["item"]["status"] == "detected", r
        # approver: approves as the signed-in user; no self-approval; CSRF and origin checks apply
        st, e = apr.call("POST", "/api/agent/queue/approve", {"id": i}, csrf=False)
        assert st == 403 and "CSRF" in e["error"], e
        st, e = apr.call("POST", "/api/agent/queue/approve", {"id": i}, {"Origin": "https://evil.example"})
        assert st == 403, e
        st, e = apr.call("POST", "/api/agent/queue/approve", {"id": j})
        assert st == 403 and "self-approval" in e["error"], e
        st, r = apr.call("POST", "/api/agent/queue/approve", {"id": i, "note": "ok", "user": "Mallory <m@evil>"})
        assert st == 200 and r["item"]["status"] == "approved" and r["approval"]["recorded"], r
        assert r["item"]["history"][-1]["user"] == "Apr Prover <apr@example.com>"
        item = next(x for x in json.loads(plan.read_text()) if x["name"] == "FAKEFET1")
        assert item["approvals"][-1]["who"] == "Apr Prover <apr@example.com>" and item["approvals"][-1]["role"] == "approver"
        assert apr.call("POST", "/api/agent/queue/approve", {"id": i})[0] == 400                # already approved
        rows = [(a["user"], a["route"], a["status"]) for a in __import__("ecadlib.team", fromlist=["x"]).read_audit(ws.root, 500)]
        assert ("apr", "/api/agent/queue/approve", 200) in rows and ("vic", "/api/agent/queue/approve", 403) in rows
        st, page = vic.call("GET", "/")
        for needle in (b'id="queuecard"', b"/api/agent/queue/item?id=", b"/api/agent/queue/picture?path=", b'data-q="approve"'):
            assert needle in page, needle


def test_local_mode_routes():
    from tests.test_resolve_ui import workspace, ANN
    from tests.test_ui import serve
    with workspace() as (ws, bom, plan):
        i = _review_item(ws, bom, "FAKEFET1")
        srv, call = serve(ws)
        try:
            assert call("GET", "/api/agent/queue")[1]["counts"]["review"] == 1
            st, e = call("POST", "/api/agent/queue/approve", {"id": i})
            assert st == 400 and "user" in e["error"], e                                      # no login: say who you are
            st, e = call("POST", "/api/agent/queue/approve", {"id": i, "user": ANN}, {"Origin": "https://evil.example"})
            assert st == 403, e
            st, r = call("POST", "/api/agent/queue/approve", {"id": i, "user": ANN})
            assert st == 200 and r["item"]["status"] == "approved", r
            assert call("GET", "/api/agent/queue/picture?path=agent/queue/.lock")[0] == 400
        finally:
            srv.shutdown()
            srv.server_close()


# ------------------------------------------------------------------ MCP and CLI
def test_mcp_tools_and_cli():
    from tests.test_resolve_ui import workspace, ANN
    names = {t["name"] for t in M.TOOLS}
    assert {"agent_queue_list", "agent_queue_get", "agent_queue_decide"} <= names
    assert "user" in next(t for t in M.TOOLS if t["name"] == "agent_queue_decide")["inputSchema"]["required"]
    with workspace() as (ws, bom, plan):
        i = _review_item(ws, bom, "FAKEFET1")
        assert M.call_tool(ws, "agent_queue_list", {"status": "review"})["items"][0]["id"] == i
        assert M.call_tool(ws, "agent_queue_get", {"id": i})["item"]["build"]["part"] == "FAKEFET1"
        r = M.call_tool(ws, "agent_queue_decide", {"id": i, "decision": "approve", "user": ANN})
        assert r["item"]["status"] == "approved"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert AQ.main(["--workspace", str(ws.root), "list"]) == 0
        assert i in out.getvalue() and "approved" in out.getvalue()
        try:
            AQ.main(["--workspace", str(ws.root), "approve", i, "--user", ANN])
            raise AssertionError("approved twice")
        except SystemExit as e:
            assert "only items in review" in str(e.code), e.code


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_agent_queue"]))
