"""Long-run tests of the library agent: crashes between steps, people deciding while it works, clock steps, PR body
injection, git safety, and a soak over weeks of fake time.  python3 -m tests.test_agent_soak

The real agent, agent_queue and agent_watch run on a real BOM file and a real git repo with a bare origin; the
librarian is a small fake that behaves like librarian.run where it matters (reads plan.json at the start, writes the
whole plan at the end, keeps approvals), the proof, GitHub and the Altium 365 push are fakes."""
import contextlib, csv, hashlib, json, os, random, subprocess, tempfile, threading
from pathlib import Path
from unittest import mock

from ecadlib import agent as A, agent_queue as AQ, agent_watch as W, altium365, rules as RU
from ecadlib.app import Workspace

HEAD = "Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
ANN, BOB = "Ann Lee <ann@example.com>", "Bob Roe <bob@example.com>"


class Killed(BaseException):
    """A kill -9 between two steps: not caught by the agent's `except Exception` handlers."""


# ------------------------------------------------------------------ fakes
class Vision:
    def __init__(self, rng=None):
        self.rng = rng

    def render_footprint(self, src, out, scale=None):
        Path(out).write_bytes(PNG)
        return out
    render_symbol = render_footprint

    def prove(self, item, workspace=None, rules=None, client=None):
        v = self.rng.choice(["doubt", "doubt", "pass", "fail"]) if self.rng else "doubt"
        return {"verdict": v, "score": 0.5, "reason": f"fake {v}", "findings": [], "images": {"ours": [], "datasheet": []},
                "model": "fake", "cost_usd": 0.0}


def fake_librarian(ws, bom, out, refresh, rules, log, hook=None):
    """Like librarian.run for the agent: the plan is read when it starts and written whole when it ends."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    old = {i["name"]: i for i in json.loads((out / "plan.json").read_text())} if (out / "plan.json").exists() else {}
    if hook:
        hook()
    items = []
    with open(bom, newline="") as f:
        for r in csv.DictReader(f):
            name = r["Mfr. Part #"] or r["Value"]
            sym, fp = out / "models" / f"{A._safe(name)}.SchLib", out / "models" / f"{A._safe(name)}.PcbLib"
            sym.parent.mkdir(parents=True, exist_ok=True)
            body = f"fp {name} {r['Footprint']}"
            if not fp.exists() or fp.read_text() != body or (refresh and name in refresh):
                sym.write_text(f"sym {name} {os.urandom(4).hex()}")
                fp.write_text(body)
            it = {"name": name, "status": "ready", "description": "", "comment": name,
                  "parameters": [{"name": "Manufacturer Part Number", "value": r["Mfr. Part #"]},
                                 {"name": "Manufacturer", "value": r["Manufacturer"]}, {"name": "Value", "value": r["Value"]}],
                  "symbol": {"file": sym.relative_to(out).as_posix(), "ref": name},
                  "footprint": {"file": fp.relative_to(out).as_posix(), "ref": r["Footprint"]},
                  "source": {"symbol": "fake", "footprint": "fake"}, "librarian": {"tried": [], "review": []}}
            if old.get(name, {}).get("approvals"):
                it["approvals"] = old[name]["approvals"]
            items.append(it)
    (out / "plan.json").write_text(json.dumps(items, indent=2))
    return items


class GitHubPRs:
    """Stateful GitHub REST fake: PRs by number with head, state and body."""
    def __init__(self):
        self.prs, self.calls = {}, []

    def request(self, method, url, json=None, timeout=None, headers=None):
        self.calls.append((method, url))
        path = url.split("/repos/acme/lib/", 1)[1]
        if method == "GET" and path.startswith("pulls?"):
            head = path.split("head=acme:", 1)[1].split("&")[0]
            body = [p for p in self.prs.values() if p["head"] == head and p["state"] == "open"]
        elif method == "GET":
            body = self.prs[int(path.split("/")[1])]
        elif method == "POST" and path.startswith("issues/"):
            body = {"id": 1}
            self.prs[int(path.split("/")[1])].setdefault("comments", []).append(json["body"])
        elif method == "POST":
            n = len(self.prs) + 1
            body = self.prs[n] = {"number": n, "html_url": f"https://github.com/acme/lib/pull/{n}", "head": json["head"],
                                  "state": "open", "body": json["body"]}
        else:
            body = self.prs[int(path.split("/")[1])]
            body.update({k: v for k, v in json.items() if k in ("body", "state")})
        return mock.Mock(status_code=201 if method == "POST" else 200, json=lambda: body, content=b"x", text="")


def gitc(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_ws(d, git=True, **agent):
    ws = Path(d) / "ws"
    (ws / "boms").mkdir(parents=True)
    rules = {"agent": {"github_repo": "acme/lib", "upkeep_schedule": "off", **agent,
                       "watch": {"sources": [{"type": "bom_file", "ref": "boms/main.csv"}]}},
             "altium365": {"workspace": "https://acme.365.altium.com"}}
    (ws / "ecadlib_rules.json").write_text(json.dumps(rules))
    if git:
        gitc(ws, "init", "-q", "-b", "main")
        (ws / ".gitignore").write_text("agent/\nlibrarian/\nboms/\necadlib_rules.json\n")
        gitc(ws, "add", ".gitignore")
        gitc(ws, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "init")
        gitc(d, "init", "-q", "--bare", "remote.git")
        gitc(ws, "remote", "add", "origin", str(Path(d) / "remote.git"))
        gitc(ws, "push", "-q", "origin", "main")
    return ws, RU.load(rules)


def write_bom(ws, parts):
    """parts: {mpn: footprint or (footprint, manufacturer)}."""
    rows = [(m, *(v if isinstance(v, tuple) else (v, "ACME"))) for m, v in sorted(parts.items())]
    (ws / "boms" / "main.csv").write_text(HEAD + "".join(f'U{i},1,{m},{fp},"{mf}",{m}\n' for i, (m, fp, mf) in enumerate(rows)))


def run(ws, rules, **kw):
    return A.run_once(ws, rules, prover=kw.pop("prover", None) or Vision(), builder=kw.pop("builder", fake_librarian),
                      upkeep=kw.pop("upkeep", lambda *a: []), env=kw.pop("env", {}), **kw)


def killed(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except Killed:
        return True
    return False


def kill_on(*a, **kw):
    raise Killed()


def by_mpn(ws):
    return {i["mpn"]: i for i in AQ.Queue(ws).list()}


# ------------------------------------------------------------------ regressions (each failed before its fix)
def test_kill_between_scan_and_queue_loses_nothing():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        with mock.patch.object(AQ.Queue, "upsert", kill_on):
            assert killed(run, ws, rules)
        assert (ws / "agent" / "watch_pending.json").is_file()
        rep = run(ws, rules)
        assert set(by_mpn(ws)) == {"AAA1", "BBB2"} and not (ws / "agent" / "watch_pending.json").exists(), by_mpn(ws)
        assert any("scanning again" in n for n in rep["notes"])
        run(ws, rules)
        assert len(AQ.Queue(ws).list()) == 2                         # and no duplicate on the next run


def test_one_bad_change_does_not_lose_the_others():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        good = {"key": "acme|AAA1", "mpn": "AAA1", "manufacturer": "ACME", "kind": "new", "line": {"mpn": "AAA1"},
                "source": {"type": "bom_file", "ref": "x", "name": "x"}, "was": {}, "detected": "t"}
        bad = dict(good, key="acme|ZZZ", mpn="")                       # agent_queue refuses an item without an MPN
        watcher = mock.Mock(scan=lambda *a, **k: [bad, good])
        rep = run(ws, rules, watcher=watcher)
        assert list(by_mpn(ws)) == ["AAA1"] and any("not queued" in n for n in rep["notes"]), rep["notes"]


def test_killed_in_proof_or_after_retry_is_resumed_not_stuck():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        assert killed(run, ws, rules, prover=mock.Mock(prove=kill_on, render_footprint=Vision().render_footprint,
                                                      render_symbol=Vision().render_symbol))
        assert by_mpn(ws)["AAA1"]["status"] == "proving"
        run(ws, rules)
        it = by_mpn(ws)["AAA1"]
        assert it["status"] == "review", it["status"]
        # failed, retried by a person, and the run dies right after detected -> building: the old build.built stays
        q = AQ.Queue(ws)
        q.transition(it["id"], "failed", user=A.AGENT, note="x")
        q.transition(it["id"], "detected", user=ANN, note="retry")
        assert killed(run, ws, rules, builder=kill_on)
        assert q.get(it["id"])["status"] == "building"
        run(ws, rules)
        assert q.get(it["id"])["status"] == "review", q.get(it["id"])["status"]


def test_an_item_that_kills_every_run_is_failed_after_three():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        for _ in range(A.MAX_ATTEMPTS):
            assert killed(run, ws, rules, builder=kill_on)
        rep = run(ws, rules, builder=kill_on)                          # the 4th run fails it instead of dying again
        it = by_mpn(ws)["AAA1"]
        assert it["status"] == "failed" and "stopped the agent" in it["history"][-1]["note"], it["history"][-1]


def test_rebuild_request_survives_a_clock_step_back():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules, now="2031-01-01T00:00:00Z")                     # built while the clock was years ahead
        i = by_mpn(ws)["AAA1"]["id"]
        AQ.decide(Workspace(ws), i, "rebuild", BOB, note="pin 1")      # stamped with the corrected clock
        run(ws, rules, now="2026-10-15T09:00:00Z")
        assert AQ.Queue(ws).get(i)["status"] == "review"


def test_upkeep_runs_when_its_last_stamp_is_in_the_future():
    with tempfile.TemporaryDirectory() as d:
        A.write_json(Path(d) / "agent" / "agent_state.json", {"last_upkeep": "2036-02-07T06:28:16Z"})
        assert A.upkeep_due(d, {"upkeep_schedule": "weekly"}, "2026-11-30T00:00:00Z")
        A.write_json(Path(d) / "agent" / "agent_state.json", {"last_upkeep": "2026-11-29T00:00:00Z"})
        assert not A.upkeep_due(d, {"upkeep_schedule": "weekly"}, "2026-11-30T00:00:00Z")


def test_approve_is_refused_when_the_item_was_rebuilt_since_it_was_shown():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules, now="2026-10-15T07:00:00Z")
        i = by_mpn(ws)["AAA1"]["id"]
        token = AQ.ws_get(ws, i)["build_token"]                        # what the review page showed
        write_bom(ws, {"AAA1": "QFN-16"})
        run(ws, rules, now="2026-10-15T08:00:00Z")                     # rebuilt: back in review with another build
        try:
            AQ.decide(Workspace(ws), i, "approve", ANN, expect=token)
            raise AssertionError("approved a build nobody looked at")
        except ValueError as e:
            assert "rebuilt since you opened it" in str(e)
        r = AQ.decide(Workspace(ws), i, "approve", ANN, expect=AQ.ws_get(ws, i)["build_token"])
        assert r["item"]["status"] == "approved" and r["item"]["approval"]["build"] == AQ.build_token(r["item"])


def test_push_skips_an_approved_item_whose_files_changed():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules)
        i = by_mpn(ws)["AAA1"]["id"]
        AQ.decide(Workspace(ws), i, "approve", ANN)
        out = A._librarian_out(ws, ws / "agent" / A.BOM_NAME)
        (out / "models" / "AAA1.PcbLib").write_text("rebuilt by someone after the approval")
        with mock.patch.object(altium365, "push", side_effect=AssertionError("pushed")):
            rep = A.push_approved(ws, rules, a365=object(), env={})
        assert rep["pushed"] == [] and "changed since the approved build" in rep["results"][0]["detail"], rep


def test_approval_during_a_build_is_not_overwritten_by_the_librarian():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules)
        i = by_mpn(ws)["AAA1"]["id"]
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        seen = {}

        def approve_now():                                             # Ann clicks Approve while the librarian runs
            try:
                AQ.decide(Workspace(ws), i, "approve", ANN)
            except ValueError as e:
                seen["refused"] = str(e)
        run(ws, rules, builder=lambda *a: fake_librarian(*a, hook=approve_now))
        assert "rebuilding the library right now" in seen.get("refused", ""), seen
        assert AQ.Queue(ws).get(i)["status"] == "review"               # nothing half-recorded: she can approve again
        r = AQ.decide(Workspace(ws), i, "approve", ANN)
        plan = json.loads((A._librarian_out(ws, ws / "agent" / A.BOM_NAME) / "plan.json").read_text())
        assert r["approval"]["recorded"] and next(p for p in plan if p["name"] == "AAA1").get("approvals")


def test_person_rejecting_during_a_build_does_not_stop_the_run():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        q = AQ.Queue(ws)

        def reject():
            q.transition(next(x for x in q.list() if x["mpn"] == "AAA1")["id"], "rejected", user=ANN, note="no")
        rep = run(ws, rules, builder=lambda *a: fake_librarian(*a, hook=reject))
        st = {m: i["status"] for m, i in by_mpn(ws).items()}
        assert st == {"AAA1": "rejected", "BBB2": "review"} and any("changed meanwhile" in n for n in rep["notes"]), (st, rep["notes"])


def test_change_after_approval_is_rebuilt_after_the_push():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False, auto_push=True)
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules, now="2026-10-15T07:00:00Z")
        i = by_mpn(ws)["AAA1"]["id"]
        AQ.decide(Workspace(ws), i, "approve", ANN)
        write_bom(ws, {"AAA1": "QFN-16"})
        push = lambda pis, *a, **k: [{"action": "release", "name": p["name"]} for p in pis]
        with mock.patch.object(altium365, "push", push):
            run(ws, rules, a365=object(), now="2026-10-15T08:00:00Z")   # the approved SOIC-8 build is pushed
            run(ws, rules, a365=object(), now="2026-10-15T09:00:00Z")
        items = AQ.Queue(ws).list()
        assert sorted(x["status"] for x in items) == ["pushed", "review"], [(x["status"], x["build"]) for x in items]
        new = next(x for x in items if x["status"] == "review")
        assert new["change"]["line"]["footprint"] == "QFN-16"


def test_retried_part_gets_a_new_batch_and_a_closed_pr_is_not_reused():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        gh = GitHubPRs()
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        i = by_mpn(ws)["AAA1"]["id"]
        b1 = AQ.Queue(ws).get(i)["batch"]
        AQ.decide(Workspace(ws), i, "reject", ANN, note="wrong pinout")
        AQ.decide(Workspace(ws), i, "retry", BOB, note="again")
        run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        it = AQ.Queue(ws).get(i)
        assert it["status"] == "review" and it["batch"] and it["batch"] != b1, (b1, it["batch"])
        # a person merges the PR while the part is still in review; the source then changes: a NEW PR, not a PATCH
        gh.prs[2]["state"] = "closed"
        write_bom(ws, {"AAA1": "QFN-16"})
        run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        # PR 1 lost its only part to the retry: closed by the agent; PR 2 merged by a person; PR 3 replaces it
        assert [p["state"] for p in gh.prs.values()] == ["closed", "closed", "open"], gh.prs
        assert "no parts left" in gh.prs[1]["comments"][0] and "comments" not in gh.prs[2]
        assert AQ.Queue(ws).get(i)["pr_url"].endswith("/pull/3")


def test_pr_body_is_inert_markdown():
    it = {"id": "x1", "key": "acme|X", "manufacturer": "ACME](https://evil.example)", "origin": "watch",
          "mpn": "@acme/everyone fixes #1 <img src=x onerror=alert(1)> [click](https://evil.example) a|b\n## Approved",
          "change": {"kind": "new", "source": {"type": "bom_file", "name": "a`b <script>"},
                     "line": {"datasheet": "https://x.example/a.pdf> <img src=https://track.example/>"}},
          "build": {"notes": ["<details open>", "closes #2"]},
          "proof": {"verdict": "doubt", "reason": "@bob", "findings": [{"severity": "error", "what": "p|q", "page": 3}]}}
    body = A.pr_body("b1", [it], lambda it, p: p, "t")
    for s in ("@acme/everyone", "fixes #1", "<img", "](https://evil", "<script>", "@bob", "closes #2", "<details",
              "\n## Approved", "track.example"):
        assert s not in body, s
    assert "| error | p\\|q |" in body                                  # a pipe cannot add a table column
    assert "Datasheet: <https://x.example/a.pdf>" in A.pr_body(
        "b1", [dict(it, change={"kind": "new", "line": {"datasheet": "https://x.example/a.pdf"}})], lambda *a: "", "t")


def test_agent_branch_starts_from_the_remote_base_not_local_commits():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        (ws / "private.txt").write_text("work in progress")
        gitc(ws, "add", "private.txt")
        gitc(ws, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "local only, never pushed")
        head = gitc(ws, "rev-parse", "HEAD")
        write_bom(ws, {"AAA1": "SOIC-8"})
        rep = run(ws, rules, github_session=GitHubPRs(), env={"GITHUB_TOKEN": "tok"})
        br = rep["batches"][0]["branch"]
        files = gitc(Path(d) / "remote.git", "ls-tree", "-r", "--name-only", br).split()
        assert "private.txt" not in files and any(f.endswith(".PcbLib") for f in files), files
        assert gitc(ws, "rev-parse", "HEAD") == head and gitc(ws, "status", "--porcelain") == ""


def test_watch_keeps_parts_of_a_half_written_bom():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        W.scan(ws, rules)
        (ws / "boms" / "main.csv").write_text(HEAD)                   # caught mid-save: header only
        r = W.scan_report(ws, rules)
        assert r["changes"] == [] and "kept the last good parts" in r["errors"][0]["error"], r
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        assert W.scan(ws, rules) == []
        write_bom(ws, {"AAA1": "SOIC-8"})                              # a real removal is still reported
        assert [(c["kind"], c["mpn"]) for c in W.scan(ws, rules)] == [("removed", "BBB2")]


def test_serve_survives_a_rules_typo():
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d) / "ws"
        ws.mkdir()
        good = "[agent]\nenabled = true\n"
        (ws / "ecadlib.toml").write_text(good)
        if not RU.load(None, start=ws)["agent"]["enabled"]:            # Python 3.10 reads no TOML: nothing to test
            return
        stop, runs, logs = threading.Event(), [], []

        def fake_run(w, r, **k):
            runs.append(1)
            (ws / "ecadlib.toml").write_text("[agent\nenabled = true\n") if len(runs) == 1 else stop.set()
            return {"started": "x", "batches": []}

        def sleep_then_fix(*a):
            (ws / "ecadlib.toml").write_text(good)
        with mock.patch.object(threading.Event, "wait", lambda self, t=None: sleep_then_fix() if len(logs) > 1 else None):
            A.serve(ws, interval_s=0, stop=stop, run=fake_run, install_signals=False, log=logs.append, sleep_s=0)
        assert len(runs) == 2 and any("rules not loaded" in m for m in logs), logs


def test_huge_bom_upserts_read_the_queue_once():
    with tempfile.TemporaryDirectory() as d:
        q = AQ.Queue(d)
        reads = []
        real = Path.read_text
        with mock.patch.object(Path, "read_text", lambda self, *a, **k: (str(self).startswith(d) and reads.append(1)) or real(self, *a, **k)):
            with q.lock:
                for n in range(300):
                    q.upsert({"mpn": f"P{n}", "manufacturer": "acme", "origin": "watch", "change": {"kind": "new"}})
                q.upsert({"mpn": "P7", "manufacturer": "acme", "origin": "watch", "change": {"kind": "new"}})
        assert len(q.list()) == 300 and len(reads) < 20, len(reads)   # was one read of every item file per upsert


def test_status_reads_only_the_last_batches():
    with tempfile.TemporaryDirectory() as d:
        for n in range(30):
            A.write_json(Path(d) / "agent" / "batches" / f"b{n:03}" / "batch.json", {"id": f"b{n:03}"})
        st = A.status(d, queue=mock.Mock(list=lambda: []))
        assert [b["id"] for b in st["batches"]] == [f"b{n:03}" for n in range(10, 30)]


def test_rejected_part_files_leave_the_batch_branch():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        gh = GitHubPRs()
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        rep = run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        br = rep["batches"][0]["branch"]
        tree = lambda: gitc(Path(d) / "remote.git", "ls-tree", "-r", "--name-only", br).split()
        assert any("AAA1.PcbLib" in f for f in tree()) and any("AAA1" in f for f in tree())
        a = by_mpn(ws)["AAA1"]
        AQ.decide(Workspace(ws), a["id"], "reject", ANN, note="pin 1 wrong")
        run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        files = tree()
        assert not [f for f in files if "AAA1" in f or f"/{a['id']}/" in f], files
        assert any("BBB2.PcbLib" in f for f in files) and any(f"/{by_mpn(ws)['BBB2']['id']}/" in f for f in files)
        body = gh.prs[1]["body"]
        assert "Taken out of this PR" in body and "AAA1: rejected" in body and gh.prs[1]["state"] == "open"


def test_pictures_of_an_earlier_build_leave_the_branch_and_are_not_forgotten():
    # (soak seed 4 with a slower clock) a part asked to be rebuilt is removed from its BOM meanwhile: nothing is built,
    # it is back in review without AFTER pictures. Its old pictures stayed on the branch and batch.json forgot them,
    # so they were still in the open PR after the part was rejected.
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        gh, env = GitHubPRs(), {"GITHUB_TOKEN": "tok"}
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        br = run(ws, rules, github_session=gh, env=env)["batches"][0]["branch"]
        tree = lambda: gitc(Path(d) / "remote.git", "ls-tree", "-r", "--name-only", br).split()
        a = by_mpn(ws)["AAA1"]
        assert [f for f in tree() if f"/{a['id']}/after_" in f]
        AQ.decide(Workspace(ws), a["id"], "rebuild", BOB, note="again")
        write_bom(ws, {"BBB2": "SOIC-8"})                                # removed from the BOM before the rebuild ran
        run(ws, rules, github_session=gh, env=env)
        a = by_mpn(ws)["AAA1"]
        assert a["status"] == "review" and a["build"]["action"] == "none" and not a["pictures"]["after"], a
        assert not [f for f in tree() if f"/{a['id']}/after_" in f], tree()  # the old build's pictures left the PR
        rec = json.loads((ws / "agent" / "batches" / a["batch"] / "batch.json").read_text())
        assert any("AAA1.PcbLib" in x for x in rec["paths"][a["id"]]), rec["paths"]   # its library files: still its own
        AQ.decide(Workspace(ws), a["id"], "reject", ANN, note="not needed")
        run(ws, rules, github_session=gh, env=env)
        assert not [f for f in tree() if f"/{a['id']}/" in f or "AAA1" in f], tree()
        assert any(f"/{by_mpn(ws)['BBB2']['id']}/after_" in f for f in tree()) and gh.prs[1]["state"] == "open"


def test_every_approval_path_waits_for_the_plan_lock():
    from ecadlib import approvals as AP
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules)
        w, bom = Workspace(ws), "agent/library_bom.csv"
        plan = A._librarian_out(ws, ws / "agent" / A.BOM_NAME) / "plan.json"
        with AQ.plan_lock(ws):                                       # the agent's librarian is rewriting the plan
            for call in (lambda: w.approve_part(bom, "AAA1", "approve", ANN),
                         lambda: AQ.decide(w, by_mpn(ws)["AAA1"]["id"], "approve", ANN)):
                try:
                    call()
                    raise AssertionError("recorded while the librarian rewrites the plan")
                except ValueError as e:
                    assert "rebuilding the library right now" in str(e)
            try:
                AP.main([str(plan), "approve", "--part", "AAA1", "--user", ANN])
                raise AssertionError("approvals CLI ignored the lock")
            except SystemExit as e:
                assert "rebuilding the library" in str(e.code)
        assert not json.loads(plan.read_text())[0].get("approvals")
        assert w.approve_part(bom, "AAA1", "approve", ANN)["decision"]["decision"] == "approve"
        assert AP.main([str(plan), "status"]) in (0, None)


def test_decided_batch_with_a_pushed_part_stays_open_ready_to_merge():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, auto_push=True)
        gh = GitHubPRs()
        kw = dict(github_session=gh, env={"GITHUB_TOKEN": "tok"}, a365=object())
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        run(ws, rules, **kw)
        a, b = by_mpn(ws)["AAA1"], by_mpn(ws)["BBB2"]
        AQ.decide(Workspace(ws), a["id"], "approve", ANN)
        push = lambda pis, *x, **k: [{"action": "release", "name": p["name"]} for p in pis]
        with mock.patch.object(altium365, "push", push):
            run(ws, rules, **kw)
            assert gh.prs[1]["state"] == "open" and "comments" not in gh.prs[1]     # BBB2 still waits for review
            AQ.decide(Workspace(ws), b["id"], "reject", ANN, note="no")
            run(ws, rules, **kw)
        pr = gh.prs[1]                       # the library repo gets AAA1 only through this PR: never closed
        assert pr["state"] == "open" and len(pr["comments"]) == 1 and pr["comments"][0].startswith("All parts decided; ready to merge")
        files = gitc(Path(d) / "remote.git", "ls-tree", "-r", "--name-only", pr["head"]).split()
        assert any("AAA1.PcbLib" in f for f in files) and not [f for f in files if "BBB2" in f or f"/{b['id']}/" in f], files
        assert AQ.Queue(ws).get_batch(a["batch"])["status"] == "ready"
        n = len(gh.calls)
        run(ws, rules, **kw)
        assert len(gh.calls) == n                                      # said once, never touched again


def test_decided_batch_with_nothing_pushed_is_closed():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)
        gh = GitHubPRs()
        kw = dict(github_session=gh, env={"GITHUB_TOKEN": "tok"})
        write_bom(ws, {"AAA1": "SOIC-8", "BBB2": "SOIC-8"})
        run(ws, rules, **kw)
        for m in ("AAA1", "BBB2"):
            AQ.decide(Workspace(ws), by_mpn(ws)[m]["id"], "reject", ANN, note="no")
        run(ws, rules, **kw)
        assert gh.prs[1]["state"] == "closed" and "none was pushed" in gh.prs[1]["comments"][0]
        assert AQ.Queue(ws).get_batch(by_mpn(ws)["AAA1"]["batch"])["status"] == "closed"


def test_approved_but_unpushed_keeps_the_pr_open():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d)                                         # auto_push off
        gh = GitHubPRs()
        write_bom(ws, {"AAA1": "SOIC-8"})
        run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        AQ.decide(Workspace(ws), by_mpn(ws)["AAA1"]["id"], "approve", ANN)
        run(ws, rules, github_session=gh, env={"GITHUB_TOKEN": "tok"})
        assert gh.prs[1]["state"] == "open"


def test_manufacturer_spellings_and_blank_are_one_part():
    assert AQ.part_key("ST", "x") == AQ.part_key("STMicroelectronics N.V.", "X") == W.part_key("st micro", "X") \
        == A.part_key("STMICROELECTRONICS", "x") == "stmicroelectronics|X"
    assert AQ.norm_manufacturer("Würth Elektronik GmbH & Co. KG") == AQ.norm_manufacturer("WE") == "wurth elektronik"
    assert AQ.norm_manufacturer("Texas Instruments Inc.") == AQ.norm_manufacturer("TI")
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        (ws / "boms" / "main.csv").write_text(HEAD + "U1,1,LM1,SOIC-8,ST,LM1\nU2,1,LM1,SOIC-8,STMicroelectronics,LM1\n"
                                              "U3,1,LM1,SOIC-8,,LM1\nU4,1,AB2,SOIC-8,,AB2\n")
        builds = []
        run(ws, rules, builder=lambda *a: builds.append(1) or fake_librarian(*a))
        items = AQ.Queue(ws).list()
        assert sorted(i["key"] for i in items) == ["stmicroelectronics|LM1", "|AB2"], [i["key"] for i in items]
        lines = json.loads((ws / "agent" / "library_bom.json").read_text())
        assert sorted(lines) == ["stmicroelectronics|LM1", "|AB2"] and len(builds) == 1
        q = AQ.Queue(ws)                                              # blank first, maker learned later: re-keyed
        q.upsert({"mpn": "AB2", "manufacturer": "Acme Inc.", "origin": "manual"})
        assert [i["key"] for i in q.list() if i["mpn"] == "AB2"] == ["acme|AB2"]
        q.upsert({"mpn": "AB2", "manufacturer": "", "origin": "manual"})
        assert len([i for i in q.list() if i["mpn"] == "AB2"]) == 1
        q.upsert({"mpn": "CC3", "manufacturer": "Acme", "origin": "manual"})   # two known makers: blank stays apart
        q.upsert({"mpn": "CC3", "manufacturer": "Other Co", "origin": "manual"})
        q.upsert({"mpn": "CC3", "manufacturer": "", "origin": "manual"})
        assert sorted(i["key"] for i in q.list() if i["mpn"] == "CC3") == ["acme|CC3", "other|CC3", "|CC3"]


def test_old_keys_in_queue_and_watch_state_are_migrated():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        q = AQ.Queue(ws)
        a = q.upsert({"mpn": "LM1", "manufacturer": "ST", "origin": "manual"})
        b = q.upsert({"mpn": "LM1", "manufacturer": "Other", "origin": "manual"})
        for it, key, st in ((a, "st|LM1", "review"), (b, "stmicroelectronics n.v.|LM1", "detected")):
            raw = json.loads((q.items_dir / f"{it['id']}.json").read_text())   # as an older version wrote them
            raw.update(key=key, status=st, manufacturer=key.split("|")[0])
            (q.items_dir / f"{it['id']}.json").write_text(json.dumps(raw))
        (q.dir / "keys.json").unlink()
        assert q.migrate_keys() == 2 and q.migrate_keys() == 0
        got = {i["id"]: (i["key"], i["status"]) for i in q.list()}
        assert got == {a["id"]: ("stmicroelectronics|LM1", "review"), b["id"]: ("stmicroelectronics|LM1", "rejected")}, got
        assert "duplicate of" in q.get(b["id"])["history"][-1]["note"]
        # watch state keyed the old way: the same BOM under another spelling is no change
        (ws / "boms" / "main.csv").write_text(HEAD + "U1,1,LM1,SOIC-8,STMicroelectronics,LM1\n")
        W.scan(ws, rules)
        st = json.loads(W.state_path(ws).read_text())
        for ent in st["sources"].values():
            for fe in ent["files"].values():
                fe["parts"] = {"st|LM1": dict(fe["parts"]["stmicroelectronics|LM1"], manufacturer="ST")}
        st["version"] = 1
        W.state_path(ws).write_text(json.dumps(st))
        assert W.scan(ws, rules) == [] and json.loads(W.state_path(ws).read_text())["version"] == 2


def test_manufacturer_aliases_from_rules():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False)
        try:
            (ws / "boms" / "main.csv").write_text(HEAD + "U1,1,LM1,SOIC-8,Acme East Corp.,LM1\nU2,1,LM1,SOIC-8,ACME,LM1\n")
            run(ws, rules)
            assert sorted(i["key"] for i in AQ.Queue(ws).list()) == ["acme east|LM1", "acme|LM1"]
            assert AQ.norm_manufacturer("ST") == "stmicroelectronics"             # the built-in table is the default
            raw = json.loads((ws / "ecadlib_rules.json").read_text())
            raw["agent"]["manufacturer_aliases"] = {"Acme East": "ACME"}
            (ws / "ecadlib_rules.json").write_text(json.dumps(raw))
            rules2 = RU.load(raw)
            assert rules2["agent"]["manufacturer_aliases"]["st"] == "stmicroelectronics"   # extends, not replaces
            run(ws, rules2)                                   # keys under the new table: migrated, duplicates merged
            items = AQ.Queue(ws).list()
            assert {i["key"] for i in items} == {"acme|LM1"} and sorted(i["status"] for i in items) == ["rejected", "review"]
            assert W.scan(ws, rules2) == []                    # the watch state was re-keyed too: no removed / new
            try:
                RU.load({"agent": {"manufacturer_aliases": {"x": ["not text"]}}})
                raise AssertionError("bad alias table accepted")
            except RU.RulesError:
                pass
        finally:
            AQ.use_rules(None)                                 # back to the defaults for the other tests


def test_dry_run_writes_nothing_at_all():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, git=False, auto_push=True)
        write_bom(ws, {"AAA1": "SOIC-8"})
        snap = lambda: {str(p.relative_to(ws)): (p.read_bytes() if p.is_file() else None) for p in ws.rglob("*")}
        before = snap()
        rep = A.run_once(ws, rules, dry_run=True, do_upkeep=True, env={})
        assert rep["would_build"] == ["acme|AAA1"] and snap() == before, sorted(set(snap()) - set(before))
        run(ws, rules)                                                 # after a real run: still nothing written
        write_bom(ws, {"AAA1": "QFN-16", "BBB2": "SOIC-8"})
        before = snap()
        A.run_once(ws, rules, dry_run=True, do_upkeep=True, env={})
        assert snap() == before, sorted(k for k in snap() if snap()[k] != before.get(k))
        fresh = Path(d) / "nowhere"
        assert A.main(["--workspace", str(fresh), "run", "--dry-run"]) == 0 and not fresh.exists()


# ------------------------------------------------------------------ soak
KILL_POINTS = ("upsert", "transition", "builder", "prove", "publish", "commit", "watch_save", "push", "write_json")


def _invariants(ws, d, gh, pushes, approvals, start, msg, after_clean_run, must_have):
    q = AQ.Queue(ws)
    for f in list((ws / "agent").rglob("*.json")):                   # every state file is valid JSON
        json.loads(f.read_text())
    items = q.list()
    open_by_key = {}
    for it in items:
        if it["status"] in AQ.OPEN:
            open_by_key.setdefault(it["key"], []).append(it["id"])
        for h in it["history"][1:]:                                   # only legal moves were ever made
            assert h["from"] == h["to"] or h["to"] in AQ.NEXT[h["from"]], (msg, it["id"], h)
    assert all(len(v) == 1 for v in open_by_key.values()), (msg, open_by_key)
    mpns = {it["mpn"] for it in items}
    assert must_have <= mpns, (msg, "lost", must_have - mpns)
    open_by_mpn = {}
    for it in items:                                                  # spellings of one maker: one part, one item
        if it["status"] in AQ.OPEN:
            open_by_mpn.setdefault(it["mpn"].upper(), []).append(it["id"])
    assert all(len(v) == 1 for v in open_by_mpn.values()), (msg, "duplicate open items for one MPN", open_by_mpn)
    if after_clean_run:                                               # nothing stranded mid-pipeline
        assert not [it["id"] for it in items if it["status"] in ("detected", "building", "proving")], \
            (msg, [(it["mpn"], it["status"], it["build"]) for it in items if it["status"] in ("detected", "building", "proving")])
    for name, h in pushes:                                            # nothing pushed that a person did not approve
        ok = [i for i, tok in approvals.items() if tok.split("@")[0] == h and q.get(i)["build"].get("part") == name]
        assert ok, (msg, "pushed without approval", name, h)
    heads = {}
    for p in gh.prs.values():
        if p["state"] == "open":
            heads[p["head"]] = heads.get(p["head"], 0) + 1
    assert all(n == 1 for n in heads.values()), (msg, "duplicate PRs", heads)
    remote = Path(d) / "remote.git"
    qb = {b["id"]: b for b in q.batches()}
    for b in (ws / "agent" / "batches").glob("*/batch.json"):         # one PR per open batch, the one it records
        rec = json.loads(b.read_text())
        if not rec.get("pr_number"):
            continue
        pr = gh.prs[rec["pr_number"]]
        assert pr["head"] == rec["branch"], (msg, rec)
        members = q.list(batch=rec["id"])
        if after_clean_run and pr["state"] == "open":
            # a PR whose parts are all decided is closed; an open PR holds no file of a rejected / failed / moved part
            if all(m["status"] in ("pushed", "rejected", "failed") for m in members):   # decided: open only to merge
                assert any(m["status"] == "pushed" for m in members) and rec.get("ready"), (msg, "PR left open", rec["id"])
            tree = set(gitc(remote, "ls-tree", "-r", "--name-only", rec["branch"]).split())
            for i in [i for i in rec.get("paths", {}) if A._departed(A._get_or_none(q, i), rec["id"])]:
                raise AssertionError((msg, "departed part still listed", rec["id"], i))
            for m in q.list():
                if m.get("batch") != rec["id"] or m["status"] in ("rejected", "failed"):
                    pics = {x for x in tree if f"/{rec['id']}/{m['id']}/" in x}
                    assert not pics, (msg, "files of a part that left the batch", m["id"], m["status"], pics)
        if pr["state"] == "closed" and rec.get("closed"):
            assert qb[rec["id"]]["status"] == "closed" or not after_clean_run, (msg, rec["id"])
            # a PR with pushed parts is never closed by the agent: the library repo would never get them
            assert not any(m["status"] == "pushed" for m in members), (msg, "agent closed a PR with pushed parts", rec["id"])
    assert gitc(ws, "rev-parse", "HEAD") == start["head"] and gitc(ws, "symbolic-ref", "HEAD") == "refs/heads/main", msg
    assert gitc(ws, "status", "--porcelain", "--untracked-files=no") == "", msg
    for line in gitc(remote, "for-each-ref", "--format=%(refname) %(objectname)").splitlines():
        ref, sha = line.split()
        was = start["remote"].get(ref)
        if was and was != sha:                                        # every push fast-forwards: no force push
            subprocess.run(["git", "merge-base", "--is-ancestor", was, sha], cwd=remote, check=True)
        start["remote"][ref] = sha


def _soak(seed, runs):
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as d:
        ws, rules = make_ws(d, auto_push=True, batch_size=3)
        gh, pushes, approvals = GitHubPRs(), [], {}
        start = {"head": gitc(ws, "rev-parse", "HEAD"), "remote": {}}
        spell = ["ACME", "Acme Inc.", "ACME, Ltd.", "acme gmbh & co. kg", ""]      # one maker, many spellings
        parts = {f"P{n:02}": ("SOIC-8", rng.choice(spell)) for n in range(4)}
        write_bom(ws, parts)
        must_have = set()
        t = 1_791_000_000                                             # 2026-10-03, then weeks of fake time

        def fake_push(pis, *a, **k):
            for p in pis:
                h = A._hash_files({"footprint": Path(p["footprint"]["file"]), "symbol": Path(p["symbol"]["file"])})
                pushes.append((p["name"], h))
            return [{"action": "release", "name": p["name"]} for p in pis]

        for n in range(runs):
            msg = f"seed {seed} run {n}"
            # people and projects between runs
            r = rng.random()
            if r < 0.25 and len(parts) < 9:
                parts[f"P{rng.randrange(100):02}"] = ("SOIC-8", rng.choice(spell))
            elif r < 0.35 and parts:
                parts.pop(rng.choice(sorted(parts)))
            elif r < 0.5 and parts:
                m = rng.choice(sorted(parts))
                parts[m] = (rng.choice(["SOIC-8", "QFN-16", "TSSOP-14"]), parts[m][1])
            elif r < 0.6 and parts:                                  # the same maker spelled another way
                m = rng.choice(sorted(parts))
                parts[m] = (parts[m][0], rng.choice(spell))
            transient = rng.random() < 0.08
            (ws / "boms" / "main.csv").write_text(HEAD) if transient else write_bom(ws, parts)
            q = AQ.Queue(ws)
            for it in q.list():
                x = rng.random()
                try:
                    if it["status"] == "review" and x < 0.35:
                        tok = AQ.ws_get(ws, it["id"])["build_token"]
                        AQ.decide(Workspace(ws), it["id"], "approve", ANN, expect=tok)
                        approvals[it["id"]] = tok
                    elif it["status"] == "review" and x < 0.45:
                        AQ.decide(Workspace(ws), it["id"], "reject", ANN, note="pad size")
                    elif it["status"] == "review" and x < 0.55:
                        AQ.decide(Workspace(ws), it["id"], "rebuild", BOB, note="again")
                    elif it["status"] in ("failed", "rejected") and x < 0.3:
                        AQ.decide(Workspace(ws), it["id"], "retry", BOB, note="again")
                except PermissionError:
                    pass
            for p in gh.prs.values():                                 # someone merges an open PR now and then
                if p["state"] == "open" and rng.random() < 0.05:
                    p["state"] = "closed"
            t += rng.choice([3600, 3600, 7200, 86400, 2 * 86400]) - (7200 if rng.random() < 0.1 else 0)
            kill = rng.choice(KILL_POINTS) if rng.random() < 0.35 else None
            at = rng.randrange(1, 4)
            calls = {"n": 0}

            def maybe(point, fn):
                def wrapped(*a, **k):
                    if point == kill:
                        calls["n"] += 1
                        if calls["n"] == at and point in ("builder", "prove"):
                            raise Killed()
                    out = fn(*a, **k)
                    if point == kill and calls["n"] == at and point not in ("builder", "prove"):
                        raise Killed()
                    return out
                return wrapped
            vision = Vision(rng)
            vision.prove = maybe("prove", vision.prove)
            patches = [mock.patch.object(AQ.Queue, "upsert", maybe("upsert", AQ.Queue.upsert)),
                       mock.patch.object(AQ.Queue, "transition", maybe("transition", AQ.Queue.transition)),
                       mock.patch.object(A, "publish_batch", maybe("publish", A.publish_batch)),
                       mock.patch.object(A, "commit_files", maybe("commit", A.commit_files)),
                       mock.patch.object(W, "save_state", maybe("watch_save", W.save_state)),
                       mock.patch.object(A, "write_json", maybe("write_json", A.write_json)),
                       mock.patch.object(altium365, "push", maybe("push", fake_push))]
            present = set() if transient else set(parts)
            with contextlib.ExitStack() as st:
                for p in patches:
                    st.enter_context(p)
                died = killed(run, ws, rules, prover=vision, builder=maybe("builder", fake_librarian), now=t,
                              a365=object(), github_session=gh, env={"GITHUB_TOKEN": "tok"})
            if not died:
                must_have |= present
            _invariants(ws, d, gh, pushes, approvals, start, f"{msg} (kill {kill}@{at}: {died})", False, must_have)
            if died:                                                  # the service restarts and runs clean
                with mock.patch.object(altium365, "push", fake_push):
                    run(ws, rules, prover=Vision(rng), now=t + 60, a365=object(), github_session=gh, env={"GITHUB_TOKEN": "tok"})
                must_have |= present
            _invariants(ws, d, gh, pushes, approvals, start, msg, not transient, must_have)
        recs = [json.loads(b.read_text()) for b in (ws / "agent" / "batches").glob("*/batch.json")]
        return {"items": len(AQ.Queue(ws).list()), "pushes": len(pushes), "prs": len(gh.prs),
                "closed_by_agent": sum(p["state"] == "closed" and bool(p.get("comments")) for p in gh.prs.values()),
                "ready_to_merge": sum(p["state"] == "open" and bool(p.get("comments")) for p in gh.prs.values()),
                "taken_out": sum(len(r.get("removed") or []) for r in recs)}


def test_soak_weeks_of_runs_with_kills_and_people():
    total = {}
    for seed in (1, 2, 3):
        res = _soak(seed, 45)
        assert res["items"] and res["prs"], res
        for k, v in res.items():
            total[k] = total.get(k, 0) + v
    assert total["closed_by_agent"] and total["ready_to_merge"] and total["taken_out"], total      # the close and take-out paths ran


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_agent_soak"]))
