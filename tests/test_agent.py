"""Offline tests for ecadlib.agent (the orchestrator).  python3 -m tests.test_agent

agent_watch, agent_queue and visual_check are replaced by small fakes that follow the shared contract; the librarian
is the real one (on test_librarian's fake KiCad library), git is real (a temp repo with a bare `origin`) and GitHub
REST is a fake session."""
import json, os, subprocess, tempfile, threading, time
from pathlib import Path
from unittest import mock

from ecadlib import agent as A, rules as RU
from tests import test_librarian as TL

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


# ------------------------------------------------------------------ fakes of the three sibling modules
class FakeQueue:
    """agent_queue.Queue per the contract: upsert by key, state machine, history, batches."""
    MOVES = {"detected": {"building"}, "building": {"proving"}, "proving": {"review"}, "review": {"approved", "building"},
             "approved": {"pushed"}, "failed": {"detected"}, "rejected": {"detected"}}
    OPEN = {"detected", "building", "proving", "review", "approved"}

    def __init__(self):
        self.items, self.batches, self.n = {}, {}, 0

    def upsert(self, f):
        cur = next((i for i in self.items.values() if i["key"] == f["key"] and i["status"] in self.OPEN | {"failed"}), None)
        if cur:
            cur.update(change=f["change"], updated="now")
            return cur
        self.n += 1
        it = {"id": f"q{self.n}", "status": "detected", "history": [], "build": {}, "proof": None,
              "pictures": {"before": [], "after": []}, "batch": None, "pr_url": None, **f}
        self.items[it["id"]] = it
        return it

    def get(self, id):
        return json.loads(json.dumps(self.items[id]))

    def list(self, status=None, batch=None):
        return [self.get(i) for i, it in self.items.items() if (status is None or it["status"] == status)
                and (batch is None or it["batch"] == batch)]

    def update(self, id, **f):
        self.items[id].update(f)
        return self.get(id)

    def transition(self, id, to, user=None, note=""):
        it = self.items[id]
        if to not in self.MOVES.get(it["status"], set()) and not (to in ("failed", "rejected") and it["status"] in self.OPEN):
            raise ValueError(f"{it['status']} -> {to}")
        it["history"].append({"from": it["status"], "to": to, "user": user, "note": note})
        it["status"] = to
        return self.get(id)

    def batch(self, ids, **f):
        b = {"id": f"b{len(self.batches) + 1}", "items": list(ids), "created": "now", "pr_url": None, "status": "open", **f}
        self.batches[b["id"]] = b
        for i in ids:
            self.items[i]["batch"] = b["id"]
        return b


class FakeWatcher:
    """scan(): the given changes once, then [] (idempotent), with its state in agent/watch_state.json."""
    def __init__(self, changes):
        self.changes, self.calls = changes, 0

    def scan(self, workspace, rules=None, sources=None, client=None, now=None):
        self.calls += 1
        f = Path(workspace) / "agent" / "watch_state.json"
        seen = json.loads(f.read_text()) if f.exists() else []
        new = [c for c in self.changes if c["key"] not in seen]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(seen + [c["key"] for c in new]))
        return new


class FakeVision:
    def __init__(self, verdicts=None):
        self.verdicts, self.proved = verdicts or {}, []

    def render_footprint(self, src, out_png, scale=None):
        assert Path(src).is_file(), src
        Path(out_png).write_bytes(PNG)
        return out_png

    def render_symbol(self, src, out_png, part=None):
        assert Path(src).is_file(), src
        Path(out_png).write_bytes(PNG + b"s")
        return out_png

    def prove(self, item, workspace=None, rules=None, client=None):
        self.proved.append(item["id"])
        assert item["build"]["part"] and item["pictures"]["after"], item
        v = self.verdicts.get(item["mpn"], "doubt")
        return {"verdict": v, "score": 0.5, "reason": f"fake {v}", "model": "fake",
                "findings": [{"severity": "warning", "what": "pin 1 mark", "expected": "dot", "found": "none", "page": 3}],
                "images": {"ours": [], "datasheet": []}, "cost_usd": 0.0}


class FakeGitHub:
    """requests-like session for upkeep.GitHub (REST)."""
    def __init__(self):
        self.calls = []

    def request(self, method, url, json=None, timeout=None, headers=None):
        self.calls.append((method, url, json))
        assert headers["Authorization"] == "Bearer tok"
        body = [] if method == "GET" else {"number": 7, "html_url": "https://github.com/acme/lib/pull/7"}
        return mock.Mock(status_code=201 if method == "POST" else 200, json=lambda: body, content=b"x", text="")


class FakeNexar:
    def __init__(self):
        self.released = []

    def release_defs(self, ws):
        return {}

    def upload(self, p):
        return "file-" + Path(p).name

    def find(self, ws, name):
        return None

    def find_model(self, ws, kind, name, folder):
        return None

    def folder_id(self, ws, path):
        return "fld-" + path

    def release(self, inp):
        self.released.append(inp)


def change(mpn, mfr="ACME", kind="new", **line):
    ln = {"mpn": mpn, "manufacturer": mfr, "qty": "1", **line}
    return {"key": A.part_key(mfr, mpn), "mpn": mpn, "manufacturer": mfr, "kind": kind,
            "source": {"type": "bom_file", "ref": "boards/main.csv", "name": "main.csv"}, "line": ln, "was": {},
            "detected": "2026-10-15T06:00:00Z"}


CHANGES = [change("RC0603-10K", designator="R1", value="10k", footprint="R_0603_1608Metric", lcsc="C1001",
                  description="Resistor 10k 0603", datasheet="https://example.com/r.pdf"),
           change("TPS1234DSGR", designator="U1", value="TPS1234DSGR", footprint="SOIC-8_3.9x4.9mm_P1.27mm",
                  description="Test regulator"),
           change("NOWHERE1", mfr="", designator="U4")]


def gitc(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_ws(d, git=True, remote=True, **agent):
    ws = Path(d) / "ws"
    ws.mkdir()
    rules = {"agent": {"github_repo": "acme/lib", **agent}}
    (ws / "ecadlib_rules.json").write_text(json.dumps(rules))
    if git:
        gitc(ws, "init", "-q", "-b", "main")
        gitc(ws, "config", "gc.auto", "0")
        gitc(ws, "config", "maintenance.auto", "false")
        gitc(ws, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "init")
        if remote:
            gitc(d, "init", "-q", "--bare", "remote.git")
            for k, v in (("receive.autogc", "false"), ("gc.auto", "0")):   # no detached gc still writing into
                gitc(Path(d) / "remote.git", "config", k, v)                # remote.git while the temp dir is removed
            gitc(ws, "remote", "add", "origin", str(Path(d) / "remote.git"))
            gitc(ws, "push", "-q", "origin", "main")
    return ws, RU.load(rules)


def builder():
    http = TL.FakeHTTP({"C3003": TL.ee_as("TPS1234DSGR", "C3003")})
    return lambda ws, bom, out, refresh, rules, log: A.librarian_builder(ws, bom, out, refresh, rules, log, http=http)


def run(ws, rules, q, w, v, **kw):
    env = kw.pop("env", {"GITHUB_TOKEN": "tok"})
    with mock.patch.dict(os.environ, {k: x for k, x in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
        return A.run_once(ws, rules, watcher=w, queue=q, prover=v, builder=kw.pop("builder", None) or builder(),
                          upkeep=kw.pop("upkeep", lambda *a: []), env=env, now=kw.pop("now", "2026-10-15T07:00:00Z"), **kw)


# ------------------------------------------------------------------ tests
def test_full_loop_pr_idempotent_then_push_after_approval():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, auto_push=True)
        q, w, v, gh, nx = FakeQueue(), FakeWatcher(CHANGES), FakeVision(), FakeGitHub(), FakeNexar()
        rules["altium365"]["workspace"] = "https://acme.365.altium.com"
        rep = run(ws, rules, q, w, v, github_session=gh, a365=nx)
        by = {i["mpn"]: i for i in q.list()}
        assert by["RC0603-10K"]["status"] == "review" and by["TPS1234DSGR"]["status"] == "review", {k: i["status"] for k, i in by.items()}
        nowhere = by["NOWHERE1"]
        assert nowhere["status"] == "failed" and "build incomplete" in nowhere["history"][-1]["note"], nowhere["history"]
        assert len(rep["failed"]) == 1 and len(rep["review"]) == 2 and set(v.proved) == {by["RC0603-10K"]["id"], by["TPS1234DSGR"]["id"]}
        r = by["RC0603-10K"]
        assert r["build"]["bom"] == "agent/library_bom.csv" and r["build"]["user"] == A.AGENT and r["build"]["part"]
        assert r["pictures"]["after"] and all((ws / p).is_file() for p in r["pictures"]["after"]) and r["pictures"]["before"] == []
        # one batch, one PR, committed to the bare remote without touching the working tree
        assert len(rep["batches"]) == 1 and rep["batches"][0]["fallback"] is None, rep["batches"]
        b = rep["batches"][0]
        assert [c[0] for c in gh.calls] == ["GET", "POST"], gh.calls
        body = gh.calls[1][2]["body"]
        assert "| Before | After |" in body and "?raw=true" in body and "pin 1 mark" in body and "The independent check is not sure about this part" in body
        assert f"/blob/{b['commit']}/agent-pictures/" in body and gh.calls[1][2]["head"] == "ecadlib/agent-b1"
        tree = gitc(Path(d) / "remote.git", "ls-tree", "-r", "--name-only", "ecadlib/agent-b1").splitlines()
        assert any(p.startswith("agent-pictures/b1/") for p in tree) and any(p.endswith(".PcbLib") for p in tree), tree
        assert any(p.endswith("plan.json") for p in tree) and not gitc(ws, "status", "--porcelain", "--untracked-files=no")
        assert gitc(ws, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert all(i["pr_url"] == "https://github.com/acme/lib/pull/7" for i in q.list(status="review"))
        assert rep["push"]["approved"] == [] and not nx.released          # nothing approved: nothing pushed
        assert (ws / "agent" / "batches" / "b1" / "PR.md").is_file()
        st = json.loads((ws / "agent" / "status.json").read_text())
        assert st["state"] == "idle" and st["last_run"]["review"] == 2

        # second run: nothing new -> nothing queued, no commit, GitHub untouched
        head = gitc(ws, "rev-parse", "ecadlib/agent-b1")
        rep2 = run(ws, rules, q, w, v, github_session=gh, a365=nx)
        assert len(q.items) == 3 and rep2["detected"] == [] and rep2["batches"] == [] and len(gh.calls) == 2
        assert gitc(ws, "rev-parse", "ecadlib/agent-b1") == head

        # a rebuild of one item updates the same PR (PATCH), never a second one; no force push
        rid = by["RC0603-10K"]["id"]
        q.transition(rid, "building", user="ann", note="rebuild")
        q.update(rid, build=dict(q.get(rid)["build"], requested_at="2026-10-15T08:00:00Z", requested_by="ann"))
        rep3 = run(ws, rules, q, w, v, github_session=gh, a365=nx, now="2026-10-15T09:00:00Z")
        assert q.get(rid)["status"] == "review" and q.get(rid)["pictures"]["before"], q.get(rid)
        assert [c[0] for c in gh.calls[2:]] == ["GET", "PATCH"] and gh.calls[3][1].endswith("/pulls/7"), gh.calls
        assert len({i["batch"] for i in q.list(status="review")}) == 1

        # push only after approval
        q.transition(rid, "approved", user="ann", note="ok")
        rep4 = run(ws, rules, q, w, v, github_session=gh, a365=nx, now="2026-10-15T10:00:00Z")
        res = {x["id"]: x for x in rep4["push"]["results"]}
        assert res[rid]["action"] == "release" and q.get(rid)["status"] == "pushed" and len(nx.released) == 1, res
        assert q.get(by["TPS1234DSGR"]["id"])["status"] == "review"


def test_dry_run_touches_nothing():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        snap = lambda: {p.relative_to(ws).as_posix(): p.read_bytes() for p in ws.rglob("*") if p.is_file() and "run.lock" not in p.name}
        before, refs = snap(), gitc(ws, "for-each-ref")
        q, w = FakeQueue(), FakeWatcher(CHANGES)
        rep = run(ws, rules, q, w, FakeVision(), dry_run=True, do_upkeep=True)
        assert rep["dry_run"] and len(rep["detected"]) == 3 and len(rep["would_build"]) == 3
        assert q.items == {} and snap() == before and gitc(ws, "for-each-ref") == refs
        rep = run(ws, rules, q, w, FakeVision(), github_session=FakeGitHub())    # the real run still sees the changes
        assert len(rep["detected"]) == 3 and len(q.items) == 3


def test_failures_go_to_failed_with_reason():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        q = FakeQueue()

        def broken(*a):
            raise RuntimeError("index exploded")
        rep = run(ws, rules, q, FakeWatcher(CHANGES[:1]), FakeVision(), builder=broken)
        it = q.list()[0]
        assert it["status"] == "failed" and "librarian failed: RuntimeError: index exploded" in it["history"][-1]["note"]
        assert rep["failed"][0]["reason"].startswith("librarian failed")
        q2 = FakeQueue()
        ws2 = Path(d) / "ws2"
        ws2.mkdir()
        rep = run(ws2, rules, q2, FakeWatcher(CHANGES[:2]), FakeVision({"TPS1234DSGR": "fail"}))
        by = {i["mpn"]: i for i in q2.list()}
        assert by["TPS1234DSGR"]["status"] == "failed" and "proof failed: fake fail" in by["TPS1234DSGR"]["history"][-1]["note"]
        assert by["RC0603-10K"]["status"] == "review"


def test_no_token_or_no_git_writes_batch_folder():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        rep = run(ws, rules, FakeQueue(), FakeWatcher(CHANGES[:1]), FakeVision(), env={})
        b = rep["batches"][0]
        assert "no git repository" in b["fallback"] and b["pr_url"] is None
        f = ws / b["folder"]
        md = (f / "PR.md").read_text()
        assert "](pictures/q1/after_footprint.png)" in md and (f / "pictures" / "q1" / "after_footprint.png").read_bytes() == PNG
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        gh = FakeGitHub()
        rep = run(ws, rules, FakeQueue(), FakeWatcher(CHANGES[:1]), FakeVision(), env={}, github_session=gh)
        b = rep["batches"][0]
        assert "no GitHub token in $GITHUB_TOKEN" in b["fallback"] and gh.calls == []
        assert gitc(ws, "rev-parse", "ecadlib/agent-b1") == b["commit"]          # committed locally, not pushed
        assert subprocess.run(["git", "rev-parse", "--verify", "-q", "ecadlib/agent-b1"], cwd=Path(d) / "remote.git",
                              capture_output=True).returncode != 0


def test_upkeep_findings_become_items_on_schedule():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        q, calls = FakeQueue(), []
        plan = [{"name": "TPS1234DSGR", "parameters": [{"name": "Manufacturer Part Number", "value": "TPS1234DSGR"},
                                                       {"name": "Manufacturer", "value": "ACME"}], "footprint": {}, "symbol": {}}]

        def upkeep(ws_, rules_, now):
            calls.append(now)
            return [{"check": "lifecycle", "kind": "obsolete", "item": "TPS1234DSGR", "was": "active", "now": "obsolete",
                     "detail": "", "plan": "librarian/x/plan.json", "_items": plan},
                    {"check": "sourcing", "kind": "price jump", "item": "RC0603-10K", "was": "$0.01", "now": "$0.02", "detail": ""},
                    {"check": "lifecycle", "kind": "recovered", "item": "X1", "was": "nrnd", "now": "active", "detail": ""}]
        rep = run(ws, rules, q, FakeWatcher([]), FakeVision(), upkeep=upkeep)
        items = {i["key"]: i for i in q.list()}
        assert set(items) == {"acme|TPS1234DSGR", "|RC0603-10K"} and rep["upkeep"]["findings"] == 2, items
        ob = items["acme|TPS1234DSGR"]
        assert ob["origin"] == "upkeep" and ob["change"]["kind"] == "obsolete" and ob["status"] == "review"
        assert ob["build"]["action"] == "none" and "obsolete" in ob["build"]["reason"]
        run(ws, rules, q, FakeWatcher([]), FakeVision(), upkeep=upkeep, now="2026-10-16T07:00:00Z")
        assert len(calls) == 1                                   # weekly: not due again the next day
        run(ws, rules, q, FakeWatcher([]), FakeVision(), upkeep=upkeep, now="2026-10-22T07:00:00Z")
        assert len(calls) == 2 and len(q.items) == 2             # due a week later; the same findings do not duplicate


def test_lock_one_run_at_a_time_and_status_cli():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        with A.run_lock(ws) as got:
            assert got
            with A.run_lock(ws) as got2:
                assert got2 is False
            rep = run(ws, rules, FakeQueue(), FakeWatcher(CHANGES), FakeVision())
            assert rep["skipped"] == "another agent run is in progress"
        A.write_status(ws, state="idle")
        with mock.patch.object(A, "_queue", lambda ws_: FakeQueue()):
            assert A.main(["--workspace", str(ws), "status", "--healthy", "60"]) == 0
            st = json.loads((ws / "agent" / "status.json").read_text())
            st["heartbeat"] = "2020-01-01T00:00:00Z"
            (ws / "agent" / "status.json").write_text(json.dumps(st))
            assert A.main(["--workspace", str(ws), "status", "--healthy", "60"]) == 1


def test_serve_stops_cleanly_and_idles_when_disabled():
    with tempfile.TemporaryDirectory() as d:
        ws, _ = make_ws(d, git=False)
        stop, runs = threading.Event(), []

        def fake_run(w, r, **kw):
            runs.append(kw)
            if len(runs) == 2:
                stop.set()
            return {"started": "t", "review": [], "failed": [], "batches": []}
        n = A.serve(ws, 0.01, rules={"agent": {"enabled": True}}, stop=stop, run=fake_run, install_signals=False, log=lambda *a: None)
        assert n == 2 and json.loads((ws / "agent" / "status.json").read_text())["state"] == "stopped"
        t = threading.Thread(target=A.serve, args=(ws, 0.01), kwargs=dict(rules={"agent": {"enabled": False}}, stop=stop,
                                                                          run=fake_run, install_signals=False, log=lambda *a: None))
        stop.clear()
        t.start()
        for _ in range(100):                     # a slow runner may take a while to write the first status
            time.sleep(0.05)
            if json.loads((ws / "agent" / "status.json").read_text())["state"] == "disabled":
                break
        assert json.loads((ws / "agent" / "status.json").read_text())["state"] == "disabled"
        stop.set()
        t.join(5)
        assert not t.is_alive() and len(runs) == 2


def test_workspace_and_mcp_agent_run_default_to_dry_run():
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as d:
        w, q = Workspace(Path(d) / "ws"), FakeQueue()
        fakes = (FakeWatcher(CHANGES[:1]), q, FakeVision())
        with mock.patch.object(A, "_modules", lambda *a: fakes), mock.patch.object(A, "_queue", lambda ws_: q):
            r = M.call_tool(w, "agent_run", {})
            assert r["dry_run"] is True and len(r["detected"]) == 1 and q.items == {}
            assert "queue" in M.call_tool(w, "agent_status", {})


def test_agent_rules_defaults_and_validation():
    r = RU.load({})
    assert r["agent"]["enabled"] is False and r["agent"]["auto_push"] is False and r["agent"]["upkeep_schedule"] == "weekly"
    for bad, msg in (({"enabled": "yes"}, "true or false"), ({"upkeep_schedule": "hourly"}, "off, daily"),
                     ({"github_token_env": "ghp_abc def"}, "NAME"), ({"github_repo": "nope"}, "owner/name"),
                     ({"batch_size": 0}, "1 to 500"), ({"github_api": "http://x"}, "https")):
        try:
            RU.load({"agent": bad})
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert msg in str(e), e
    assert A.github_slug("git@github.com:acme/lib.git") == "acme/lib" and A.github_slug("https://github.com/a/b") == "a/b"
    assert A.github_slug("/tmp/remote.git") is None


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_agent"]))
