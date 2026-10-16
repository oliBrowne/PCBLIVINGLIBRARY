"""Regression tests for the full review of the agent area (agent, forge, notify, upkeep).  python3 -m tests.test_review_full_agent"""
import io, json, os, sys, tempfile, types
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ecadlib import agent as A, forge, notify as N, rules as RU, upkeep as UK
from tests import test_upkeep as TU


def _fake_msvcrt():
    """msvcrt.locking as Windows does it: nbytes locked from the CURRENT file position; a byte another handle holds
    refuses LK_NBLCK."""
    held = {}

    def locking(fd, mode, n):
        st = os.fstat(fd)
        key = (st.st_dev, st.st_ino, os.lseek(fd, 0, os.SEEK_CUR))
        if mode == m.LK_UNLCK:
            held.pop(key, None)
            return
        if key in held and held[key] != fd:
            raise OSError("locked")
        held[key] = fd
    m = types.SimpleNamespace(LK_UNLCK=0, LK_LOCK=1, LK_NBLCK=2, locking=locking)
    return m


def test_run_lock_on_windows_is_exclusive():
    # "a+" opens at the end of run.lock, and the first run writes its pid there: a second run used to lock another
    # byte (the new end of file) and got the lock too, so two agent runs worked on one workspace at once
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": _fake_msvcrt()}):
        with A.run_lock(d) as got:
            assert got and (Path(d) / "agent" / "run.lock").read_text().strip()
            with A.run_lock(d) as got2:
                assert got2 is False
            with A.run_lock(d, create=False) as got3:          # a dry run sees the real run too
                assert got3 is False


def test_upkeep_findings_survive_a_later_plan_that_fails():
    # upkeep saved plan A's state (its changes remembered as reported) before plan B raised: A's findings were then
    # neither queued (the exception) nor ever reported again (the state says seen)
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        for n in ("a", "b"):
            (ws / "librarian" / n).mkdir(parents=True)
        (ws / "librarian" / "a" / "plan.json").write_text(json.dumps([{"name": "X1", "parameters": []}]))
        (ws / "librarian" / "b" / "plan.json").write_text("{damaged")

        def lifecycle(src, state_dir, ctx, cfg, now):            # "new since the last run" = no state saved yet
            sec = UK._section("lifecycle")
            if src.items and not (Path(state_dir) / "lifecycle_state.json").exists():
                sec["changes"].append(UK._change("EOL", "X1", "active", "obsolete"))
            sec["_state"] = {"lifecycle_state.json": {"updated": now}}
            return sec
        rules = RU.load({"upkeep": {"checks": ["lifecycle"]}})
        with mock.patch.object(UK, "check_lifecycle", lifecycle):
            try:
                A.upkeep_findings(ws, rules, "2026-10-16T07:00:00Z")
                raise AssertionError("a damaged plan was not reported")
            except ValueError:
                pass
            (ws / "librarian" / "b" / "plan.json").write_text("[]")      # fixed: the next run still has A's finding
            found = A.upkeep_findings(ws, rules, "2026-10-23T07:00:00Z")
            assert [(f["item"], f["plan"]) for f in found] == [("X1", "librarian/a/plan.json")], found
            assert A.upkeep_findings(ws, rules, "2026-10-30T07:00:00Z") == []   # and only once


class _GH:
    kind = "github"

    def __init__(self, fail_close=1):
        self.calls, self.fail_close = [], fail_close

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "PATCH" and self.fail_close:
            self.fail_close -= 1
            raise UK.GitHubError("GitHub PATCH: HTTP 502")
        return {}


def test_close_batch_retry_does_not_repeat_the_comment():
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        bdir = ws / "agent" / "batches" / "b1"
        bdir.mkdir(parents=True)
        (bdir / "batch.json").write_text(json.dumps({"id": "b1", "pr_number": 4, "pr_url": "u4"}))
        gh = _GH()
        members = [{"id": "q1", "key": "|X", "mpn": "X", "status": "rejected"}]
        with mock.patch.object(A, "_github", lambda *a: (gh, None)):
            rec = A.close_batch(ws, RU.load({}), "b1", members, env={})
            assert "close_note" in rec and not rec.get("closed")
            rec = A.close_batch(ws, RU.load({}), "b1", members, env={})
        assert rec.get("closed") and rec["pr_state"] == "closed"
        assert gh.calls.count(("POST", "issues/4/comments")) == 1, gh.calls
        assert gh.calls.count(("PATCH", "pulls/4")) == 2, gh.calls


class _Resp:
    def __init__(self, status, body):
        self.status_code, self.content = status, json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.text)


class _GitLabSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, json=None, timeout=None, headers=None):
        path = url.split("/projects/grp%2Flib", 1)[1]
        self.calls.append((method, path))
        if method == "GET" and path == "/merge_requests/5":
            return _Resp(404, {"message": "404 Not found"})
        if method == "GET" and path.startswith("/merge_requests?"):
            return _Resp(200, [])
        if method == "POST" and path == "/merge_requests":
            return _Resp(201, {"iid": 6, "web_url": "https://gitlab.com/grp/lib/-/merge_requests/6"})
        return _Resp(500, {})


def test_gitlab_deleted_mr_gets_a_new_one():
    # the batch remembered MR !5, which a project owner deleted: every run failed on GET 404 and the batch was never
    # published again
    s = _GitLabSession()
    rec = {"branch": "ecadlib/agent-b1", "pr_number": 5, "pr_url": "https://gitlab.com/grp/lib/-/merge_requests/5"}
    forge.upsert_mr(forge.GitLab("grp/lib", "tok", s), rec, "t", "body", "main")
    assert (rec["pr_number"], rec["pr_action"]) == (6, "opened"), rec
    assert rec["closed_prs"] == ["https://gitlab.com/grp/lib/-/merge_requests/5"]
    try:                                                         # any other refusal still stops the publish
        forge.GitLab("grp/lib", "tok", s).api("GET", "nothing")
        raise AssertionError("HTTP 500 accepted")
    except forge.GitLabError as e:
        assert e.status == 500


def test_notify_test_target_keeps_its_name():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ecadlib.toml"
        f.write_text('[[notify.targets]]\ntype = "slack"\nurl_env = "HOOK_A"\n\n'
                     '[[notify.targets]]\ntype = "slack"\nurl_env = "HOOK_B"\n')
        sent = []
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOOK_A": "https://hooks.example/a", "HOOK_B": "https://hooks.example/b"}), \
                mock.patch.object(N, "http_sender", lambda url, *a: sent.append(url) or 200), redirect_stdout(out):
            assert N.main(["test", "--rules", str(f), "--target", "slack2"]) == 0
        assert sent == ["https://hooks.example/b"] and out.getvalue().split() == ["slack2", "sent"], out.getvalue()


class _ManyPRs(TU.FakeRunner):
    """More than 100 open PRs: the upkeep PR is only in the answer when the request asks for its head branch."""
    def __call__(self, cmd, input=None, cwd=None):
        if cmd[:2] == ["gh", "api"] and cmd[3] == "GET" and "/pulls?" in cmd[4]:
            self.calls.append((cmd, input))
            mine = {"number": 3, "head": {"ref": "ecadlib/upkeep-2026-10-12"}, "html_url": "u3"}
            other = [{"number": 100 + i, "head": {"ref": f"feature-{i}"}} for i in range(100)]
            return 0, json.dumps([mine] if "head={owner}:ecadlib/upkeep-2026-10-12" in cmd[4] else other), ""
        return super().__call__(cmd, input, cwd)


def test_upkeep_pr_found_among_many_open_prs():
    run = _ManyPRs(email=True)
    res = UK.publish(TU._rep(1, ["lib/x.PcbLib"]), "pr", UK.GitHub(runner=run), run)
    assert (res["action"], res["number"]) == ("updated", 3), res


if __name__ == "__main__":
    import inspect
    for name, fn in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            fn()
            print("ok", name)
