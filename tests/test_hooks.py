"""Connections to other tools: [notify] webhooks, GitLab merge requests, provider / exporter plugins.
Offline: every webhook, GitLab and git remote here is a fake or a local bare repo.  python3 -m tests.test_hooks"""
import io, json, os, subprocess, sys, tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ecadlib import agent as A, bom_triage as T, forge as FG, mcp_server as M, notify as N, plugins as PL, \
    providers as P, rules as RU, upkeep as UK
from ecadlib.app import Workspace
from tests import test_agent as TA, test_upkeep as TUK

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parent / "examples" / "ecadlib_example_plugin"
SLACK = "https://hooks.slack.com/services/T000/B000/SECRETxyz"


def rules_with(**sections):
    return RU.load(sections)


# ------------------------------------------------------------------ notify: payloads, retries, never raises
class Sender:
    """Fake webhook endpoint: answers from a list of statuses (or exceptions), records every post."""
    def __init__(self, *answers):
        self.answers, self.posts = list(answers), []

    def __call__(self, url, body, headers, timeout):
        self.posts.append((url, json.loads(body), headers, timeout))
        a = self.answers.pop(0) if self.answers else 200
        if isinstance(a, Exception):
            raise a
        return a


def _targets(*ts, **kw):
    return rules_with(notify=dict({"targets": list(ts)}, **kw))


def test_notify_payload_shapes_and_event_routing():
    r = _targets({"name": "chan", "type": "slack", "url_env": "SLACK_HOOK", "events": ["batch_ready"]},
                 {"name": "teams", "type": "teams", "url_env": "TEAMS_HOOK"},
                 {"name": "erp", "type": "json", "url_env": "ERP_HOOK", "token_env": "ERP_TOKEN", "events": ["lifecycle"]})
    env = {"SLACK_HOOK": SLACK, "TEAMS_HOOK": "https://prod.westeurope.logic.azure.com/workflows/x", "ERP_HOOK": "https://erp.example.com/h",
           "ERP_TOKEN": "erp-secret"}
    s = Sender()
    evs = [N.event("batch_ready", "Batch b1 <has> 2 parts & more", "pull request opened", ["RC0603 <x>"], "https://gh/pr/7"),
           N.event("lifecycle", "Lifecycle: 1 part", "", ["EOL: TPS1"], "", {"parts": [{"item": "TPS1"}]})]
    res = N.send(r, evs, env=env, sender=s)
    assert [(x["target"], x["event"], x["result"]) for x in res] == [
        ("chan", "batch_ready", "sent"), ("teams", "batch_ready", "sent"), ("teams", "lifecycle", "sent"),
        ("erp", "lifecycle", "sent")], res
    slack = s.posts[0][1]
    assert slack["text"].startswith("*Batch b1 &lt;has&gt; 2 parts &amp; more*") and "• RC0603 &lt;x&gt;" in slack["text"]
    assert slack["text"].endswith("<https://gh/pr/7|Open>")
    card = s.posts[1][1]["attachments"][0]
    assert s.posts[1][1]["type"] == "message" and card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["content"]["type"] == "AdaptiveCard" and card["content"]["actions"][0]["url"] == "https://gh/pr/7"
    erp = s.posts[3]
    assert erp[1]["event"] == "lifecycle" and erp[1]["data"]["parts"] == [{"item": "TPS1"}] and erp[1]["source"] == "ecadlib"
    assert erp[2]["Authorization"] == "Bearer erp-secret" and "Authorization" not in s.posts[0][2]


def test_notify_retries_backoff_and_never_raises():
    r = _targets({"name": "chan", "type": "slack", "url_env": "SLACK_HOOK"}, retries=3, backoff_s=2.0)
    ev = [N.event("part_held", "1 part held")]
    sleeps = []
    s = Sender(503, OSError("connection reset"), 429, 200)
    res = N.send(r, ev, env={"SLACK_HOOK": SLACK}, sender=s, sleep=sleeps.append)
    assert res[0]["result"] == "sent" and res[0]["attempts"] == 4 and sleeps == [2.0, 4.0, 8.0], (res, sleeps)
    # a permanent error (404: the webhook was deleted) is not retried; the URL (a secret) is never in the result
    logs, sleeps = [], []
    res = N.send(r, ev, env={"SLACK_HOOK": SLACK}, sender=Sender(404), sleep=sleeps.append, log=logs.append)
    assert res[0]["result"] == "failed: HTTP 404 from hooks.slack.com" and res[0]["attempts"] == 1 and not sleeps
    assert not any("SECRET" in str(x) for x in res + logs), (res, logs)
    # retries give up inside the time budget: never blocks the agent for long
    t = [0.0]
    res = N.send(r, ev, env={"SLACK_HOOK": SLACK}, sender=Sender(500, 500, 500, 500), sleep=lambda w: t.__setitem__(0, t[0] + w),
                 clock=lambda: t[0], max_seconds=5)
    assert res[0]["result"].startswith("failed: HTTP 500") and res[0]["attempts"] == 2 and t[0] == 2.0, (res, t)
    # a sender that raises every time: still no exception
    res = N.send(r, ev, env={"SLACK_HOOK": SLACK}, sender=Sender(*[RuntimeError("boom")] * 9), sleep=lambda w: None)
    assert res[0]["result"] == "failed: RuntimeError from hooks.slack.com" and res[0]["attempts"] == 4
    # missing variable, plain http URL, missing token: skipped with the reason
    assert N.send(r, ev, env={}, sender=Sender())[0]["result"] == "skipped: $SLACK_HOOK is not set"
    assert "not an https:// URL" in N.send(r, ev, env={"SLACK_HOOK": "http://evil.example.com/x"}, sender=Sender())[0]["result"]
    rj = _targets({"type": "json", "url_env": "H", "token_env": "TOK"})
    assert N.send(rj, ev, env={"H": "https://x.example.com"}, sender=Sender())[0]["result"] == "skipped: $TOK is not set"
    assert N.send(None, ev, sender=Sender()) == []                     # no [notify]: nothing, no error


def test_notify_rules_validation():
    ok = _targets({"type": "slack", "url_env": "A"}, {"type": "json", "url_env": "B", "events": "*", "token_env": "C"})
    assert [t["name"] for t in N.settings(ok)["targets"]] == ["slack1", "json2"]
    bad = [({"type": "slack", "url": SLACK}, "notify.targets[0].url is not a known key"),
           ({"type": "slack", "url_env": SLACK}, "must be the NAME of an environment variable"),
           ({"type": "slack"}, "url_env is required"),
           ({"type": "discord", "url_env": "A"}, "type = 'discord'"),
           ({"type": "slack", "url_env": "A", "events": ["held"]}, "events are batch_ready"),
           ({"type": "slack", "url_env": "A", "token_env": "T"}, "only used by type = \"json\"")]
    for t, msg in bad:
        try:
            _targets(t)
            raise AssertionError(f"accepted {t}")
        except RU.RulesError as e:
            assert msg in str(e), (msg, str(e))
    try:
        _targets({"name": "x", "type": "slack", "url_env": "A"}, {"name": "x", "type": "teams", "url_env": "B"})
        raise AssertionError("duplicate names accepted")
    except RU.RulesError as e:
        assert "different for each target" in str(e)
    assert RU.DEFAULTS["notify"]["targets"] == [] and RU.load({})["providers"]["order"] == []
    toml = RU.dumps(ok, only={"notify.targets"})
    assert RU.load(RU.parse_text(toml))["notify"]["targets"] == ok["notify"]["targets"]   # round-trips through TOML


def test_notify_events_from_agent_and_upkeep_reports():
    rep = {"batches": [{"id": "b1", "items": ["q1", "q2"], "pr_url": "https://gh/pr/7", "pr_action": "opened"}],
           "failed": [{"id": "q3", "reason": "proof failed: pin 1"}],
           "upkeep": {"findings": 2, "items": [{"check": "lifecycle", "kind": "NRND", "item": "TPS1", "was": "active", "now": "nrnd"},
                                               {"check": "sourcing", "kind": "price jump", "item": "R1", "was": "1", "now": "2"}]}}
    evs = N.agent_events(rep, lambda i: {"mpn": "NOWHERE1"} if i == "q3" else None)
    assert [e["event"] for e in evs] == ["batch_ready", "part_held", "upkeep_changes", "lifecycle"]
    assert evs[0]["url"] == "https://gh/pr/7" and "2 part(s)" in evs[0]["title"]
    assert evs[1]["lines"] == ["NOWHERE1: proof failed: pin 1"] and evs[3]["lines"] == ["NRND: TPS1 (active -> nrnd)"]
    uev = N.upkeep_events(TUK._rep(3))
    assert [e["event"] for e in uev] == ["upkeep_changes", "lifecycle"] and len(uev[1]["lines"]) == 3
    assert N.upkeep_events(TUK._rep(0)) == []
    big = N.event("x", "t", lines=[str(i) for i in range(50)])
    assert len(big["lines"]) == N.MAX_LINES and big["more"] == 30 and "... and 30 more" in N.payload("slack", big)["text"]


def test_agent_run_notifies_and_survives_a_dead_webhook():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = TA.make_ws(d, git=False)
        rules = RU.load({"agent": {"github_repo": "acme/lib"}, "notify": {
            "targets": [{"name": "chan", "type": "slack", "url_env": "SLACK_HOOK"}], "retries": 1, "backoff_s": 0.0}})
        s = Sender()
        q = TA.FakeQueue()
        rep = TA.run(ws, rules, q, TA.FakeWatcher(TA.CHANGES), TA.FakeVision(), env={"SLACK_HOOK": SLACK}, notifier=s)
        events = [p[1]["text"].split("\n")[0] for p in s.posts]
        assert [x["event"] for x in rep["notify"]] == ["batch_ready", "part_held"], rep["notify"]
        assert "batch b1 has 2 part(s)" in events[0] and "1 part(s) held" in events[1], events
        assert "NOWHERE1: " in s.posts[1][1]["text"]                    # the held part by its MPN, with why
        # a webhook that is down: the run finishes, the failure is in the report
        q2, ws2 = TA.FakeQueue(), Path(d) / "ws2"
        ws2.mkdir()
        dead = Sender(*[OSError("down")] * 10)
        rep = TA.run(ws2, rules, q2, TA.FakeWatcher(TA.CHANGES), TA.FakeVision(), env={"SLACK_HOOK": SLACK}, notifier=dead)
        assert rep.get("error") is None and len(rep["review"]) == 2
        assert all(x["result"] == "failed: OSError from hooks.slack.com" and x["attempts"] == 2 for x in rep["notify"]), rep["notify"]
        # without [notify]: no notification step at all
        q3, ws3 = TA.FakeQueue(), Path(d) / "ws3"
        ws3.mkdir()
        rep = TA.run(ws3, RU.load({}), q3, TA.FakeWatcher(TA.CHANGES[:1]), TA.FakeVision(), env={}, notifier=Sender())
        assert "notify" not in rep


def test_upkeep_cli_notifies_once_per_change():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TUK.TS.BOM)
        rf = Path(d) / "ecadlib_rules.json"
        rf.write_text(json.dumps({"notify": {"targets": [{"name": "life", "type": "json", "url_env": "HOOK",
                                                          "events": ["upkeep_changes", "lifecycle"]}]}}))
        w, st = TUK.World(), Path(d) / "s"
        args = [str(bom), "--checks", "lifecycle,sourcing", "--state", str(st), "-o", str(Path(d) / "r"), "--rules", str(rf)]
        s = Sender()
        with mock.patch.dict(os.environ, {"HOOK": "https://hooks.example.com/x"}), redirect_stdout(io.StringIO()):
            assert UK.main(args + ["--no-save"], ctx=w.ctx(), notifier=s) == 1
            assert s.posts == []                                        # dry run: the same changes come again
            assert UK.main(args, ctx=w.ctx(), notifier=s) == 1
            assert [p[1]["event"] for p in s.posts] == ["upkeep_changes"] and s.posts[0][1]["data"]["count"] >= 1
            assert UK.main(args, ctx=w.ctx(), notifier=s) == 0 and len(s.posts) == 1     # nothing new: nothing sent
            w.lifecycle("0603WAF1002T5E", "Obsolete")
            assert UK.main(args, ctx=w.ctx(), notifier=s) == 1
        assert [p[1]["event"] for p in s.posts[1:]] == ["upkeep_changes", "lifecycle"], [p[1] for p in s.posts]
        assert "EOL: 0603WAF1002T5E" in json.dumps(s.posts[-1][1]["lines"])


# ------------------------------------------------------------------ GitLab
def test_gitlab_remote_parsing_and_host_choice():
    api = "https://gitlab.com/api/v4"
    for remote, want in (("git@gitlab.com:acme/hw/library.git", "acme/hw/library"),
                         ("https://gitlab.com/acme/library.git", "acme/library"),
                         ("ssh://git@gitlab.com:2222/acme/library.git", "acme/library"),
                         ("https://oauth2:tok@gitlab.com/acme/library", "acme/library"),
                         ("", None), ("/srv/git/library.git", None)):
        assert FG.project_from_remote(remote, api) == want, (remote, FG.project_from_remote(remote, api))
    own = "https://git.example.com/gitlab/api/v4"                   # self-hosted under a path
    assert FG.project_from_remote("https://git.example.com/gitlab/grp/sub/lib.git", own) == "grp/sub/lib"
    assert FG.project_from_remote("git@git.example.com:grp/lib.git", own) == "grp/lib"
    cfg = dict(RU.DEFAULTS["agent"])
    assert FG.host_kind(cfg, "git@github.com:acme/lib.git") == "github"
    assert FG.host_kind(cfg, "git@gitlab.com:acme/lib.git") == "gitlab"
    assert FG.host_kind(cfg, "https://gitlab.acme.io/hw/lib.git") == "gitlab"
    assert FG.host_kind(cfg, "https://git.example.com/gitlab/grp/lib.git") == "github"      # unknown host: as before
    assert FG.host_kind(dict(cfg, gitlab_api=own), "https://git.example.com/gitlab/grp/lib.git") == "gitlab"
    assert FG.host_kind(dict(cfg, git_host="gitlab"), "git@github.com:acme/lib.git") == "gitlab"
    assert FG.host_kind(cfg, "/tmp/remote.git") == "github"
    for bad in ({"git_host": "svn"}, {"gitlab_api": "http://gitlab.local/api/v4"}, {"gitlab_project": "no spaces/x y"},
                {"gitlab_token_env": "glpat-123"}):
        try:
            RU.load({"agent": bad})
            raise AssertionError(f"accepted {bad}")
        except RU.RulesError:
            pass
    assert RU.load({"agent": {"git_host": "gitlab", "gitlab_project": "123", "gitlab_api": own}})["agent"]["gitlab_project"] == "123"


class FakeGitLab:
    """requests-like session for forge.GitLab: merge requests, notes, labels and issues of project acme/lib."""
    def __init__(self, api="https://gitlab.com/api/v4", project="acme%2Flib", issues=(), mrs=()):
        self.base = f"{api}/projects/{project}"
        self.calls, self.issues, self.mrs, self.state = [], list(issues), list(mrs), "opened"

    def request(self, method, url, json=None, timeout=None, headers=None):
        assert url.startswith(self.base), url
        assert headers["PRIVATE-TOKEN"] == "glt" and "Authorization" not in headers
        path = url[len(self.base):].lstrip("/")
        self.calls.append((method, path, json))
        r = lambda code, body: mock.Mock(status_code=code, json=lambda: body, content=b"x", text=str(body))
        if method == "GET" and path.startswith("merge_requests?"):
            return r(200, [m for m in self.mrs if m["state"] == "opened"])
        if method == "GET" and path.startswith("merge_requests/"):
            return r(200, {"iid": int(path.split("/")[1]), "state": self.state})
        if method == "POST" and path == "merge_requests":
            mr = {"iid": 3, "web_url": "https://gitlab.com/acme/lib/-/merge_requests/3", "state": "opened",
                  "source_project_id": 1, "target_project_id": 1}
            self.mrs.append(mr)
            return r(201, mr)
        if method == "GET" and path.startswith("issues?"):
            return r(200, self.issues)
        if method == "POST" and path == "labels":
            return r(409, {"message": "Label already exists"})
        if method == "POST" and path == "issues":
            return r(201, {"iid": 12, "web_url": "https://gitlab.com/acme/lib/-/issues/12"})
        return r(200, {})


def test_agent_batch_opens_gitlab_merge_request_then_updates_and_closes_it():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = TA.make_ws(d, git_host="gitlab", gitlab_project="acme/lib")
        gl, q, v = FakeGitLab(), TA.FakeQueue(), TA.FakeVision()
        env = {"GITLAB_TOKEN": "glt", "GITHUB_TOKEN": "ghs-never-used"}
        rep = TA.run(ws, rules, q, TA.FakeWatcher(TA.CHANGES), v, github_session=gl, env=env)
        b = rep["batches"][0]
        assert b["fallback"] is None and b["host"] == "gitlab" and b["pr_number"] == 3, b
        assert [(m, p.split("?")[0]) for m, p, _ in gl.calls] == [("GET", "merge_requests"), ("POST", "merge_requests")], gl.calls
        assert "source_branch=ecadlib%2Fagent-b1" in gl.calls[0][1]
        mr = gl.calls[1][2]
        assert (mr["source_branch"], mr["target_branch"]) == ("ecadlib/agent-b1", "main") and "| Before | After |" in mr["description"]
        assert f"https://gitlab.com/acme/lib/-/raw/{b['commit']}/agent-pictures/b1/" in mr["description"]
        assert TA.gitc(Path(d) / "remote.git", "rev-parse", "ecadlib/agent-b1") == b["commit"]          # pushed
        assert all(i["pr_url"] == "https://gitlab.com/acme/lib/-/merge_requests/3" for i in q.list(status="review"))
        # a rebuild updates the same MR (PUT), never a second one
        rid = next(i["id"] for i in q.list(status="review"))
        q.transition(rid, "building", user="ann", note="rebuild")
        q.update(rid, build=dict(q.get(rid)["build"], requested_at="2026-10-15T08:00:00Z", requested_by="ann"))
        n = len(gl.calls)
        TA.run(ws, rules, q, TA.FakeWatcher(TA.CHANGES), v, github_session=gl, env=env, now="2026-10-15T09:00:00Z")
        assert [(m, p) for m, p, _ in gl.calls[n:]] == [("GET", "merge_requests/3"), ("PUT", "merge_requests/3")], gl.calls[n:]
        # merged meanwhile: a new MR is opened, the old one is remembered
        gl.state, gl.mrs = "merged", []
        q.transition(rid, "building", user="ann", note="rebuild")
        q.update(rid, build=dict(q.get(rid)["build"], requested_at="2026-10-15T10:00:00Z", requested_by="ann"))
        n = len(gl.calls)
        rep = TA.run(ws, rules, q, TA.FakeWatcher(TA.CHANGES), v, github_session=gl, env=env, now="2026-10-15T11:00:00Z")
        assert [m for m, _, _ in gl.calls[n:]] == ["GET", "GET", "POST"] and rep["batches"][0]["closed_prs"], gl.calls[n:]
        # every part rejected: an outcome note and the MR is closed (state_event), not left open
        members = [dict(i, status="rejected") for i in q.list(batch="b1")]
        n = len(gl.calls)
        rec = A.close_batch(ws, rules, "b1", members, env=env, session=gl)
        assert rec.get("closed") and rec["pr_state"] == "closed", rec
        assert [(m, p) for m, p, _ in gl.calls[n:]] == [("POST", "merge_requests/3/notes"), ("PUT", "merge_requests/3")]
        assert gl.calls[-1][2] == {"state_event": "close"} and "closes this PR" in gl.calls[-2][2]["body"]


def test_agent_gitlab_without_token_or_project_explains():
    with tempfile.TemporaryDirectory() as d:
        ws, rules = TA.make_ws(d, git_host="gitlab", gitlab_project="acme/lib")
        gl = FakeGitLab()
        rep = TA.run(ws, rules, TA.FakeQueue(), TA.FakeWatcher(TA.CHANGES[:1]), TA.FakeVision(), github_session=gl,
                     env={"GITHUB_TOKEN": "tok"})
        assert "no GitLab token in $GITLAB_TOKEN" in rep["batches"][0]["fallback"] and gl.calls == []
    with tempfile.TemporaryDirectory() as d:                    # origin is a local path: no project to derive
        ws, rules = TA.make_ws(d, git_host="gitlab")
        rep = TA.run(ws, rules, TA.FakeQueue(), TA.FakeWatcher(TA.CHANGES[:1]), TA.FakeVision(), env={"GITLAB_TOKEN": "glt"})
        assert "not a GitLab remote and [agent] gitlab_project is empty" in rep["batches"][0]["fallback"]


def test_upkeep_gitlab_issue_and_merge_request_self_hosted():
    api = "https://git.example.com/gitlab/api/v4"
    s = FakeGitLab(api)
    gl = FG.GitLab("acme/lib", "glt", session=s, api=api)
    res = UK.publish(TUK._rep(), "issue", gl)
    assert res == {"kind": "issue", "action": "opened", "number": 12, "url": "https://gitlab.com/acme/lib/-/issues/12"}
    assert [(m, p) for m, p, _ in s.calls if m != "GET"] == [("POST", "labels"), ("POST", "issues")]
    assert s.calls[-1][2]["labels"] == "ecadlib-upkeep" and "| EOL | P0 |" in s.calls[-1][2]["description"]
    s = FakeGitLab(api, issues=[{"iid": 9, "web_url": "u9"}, {"iid": 4, "web_url": "u4"}])
    res = UK.publish(TUK._rep(), "issue", FG.GitLab("acme/lib", "glt", session=s, api=api))
    assert (res["action"], res["number"]) == ("updated", 4)                         # the oldest open issue
    assert [(m, p) for m, p, _ in s.calls][1:] == [("PUT", "issues/4"), ("POST", "issues/4/notes")]
    assert UK.publish(TUK._rep(0), "issue", FG.GitLab("acme/lib", "glt", session=FakeGitLab(api), api=api))["detail"] == \
        "nothing new: GitLab not touched"
    # MR: the same git steps as the GitHub PR, then a merge request with the label
    run, s = TUK.FakeRunner(), FakeGitLab(api)
    res = UK.publish(TUK._rep(1, ["lib/PcbLib/USB_C_16P.PcbLib"]), "pr", FG.GitLab("acme/lib", "glt", session=s, api=api), run)
    assert res == {"kind": "pr", "action": "opened", "number": 3, "url": "https://gitlab.com/acme/lib/-/merge_requests/3",
                   "branch": "ecadlib/upkeep-2026-10-12"}, res
    git = [c[1:] for c, _ in run.calls if c[0] == "git"]
    assert ["push", "--force-with-lease=refs/heads/ecadlib/upkeep-2026-10-12:", "-u", "origin", "ecadlib/upkeep-2026-10-12"] in git
    mr = next(b for m, p, b in s.calls if (m, p) == ("POST", "merge_requests"))
    assert mr["labels"] == "ecadlib-upkeep" and mr["target_branch"] == "main" and "## Applied" in mr["description"]
    s.state = "opened"
    res = UK.publish(TUK._rep(1, ["lib/PcbLib/USB_C_16P.PcbLib"]), "pr", FG.GitLab("acme/lib", "glt", session=s, api=api), TUK.FakeRunner())
    assert res["action"] == "updated" and s.calls[-1][:2] == ("PUT", "merge_requests/3")


def test_upkeep_cli_gitlab_flag():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TUK.TS.BOM)
        args = [str(bom), "--checks", "lifecycle,sourcing", "--state", str(Path(d) / "s"), "-o", str(Path(d) / "r")]
        s = FakeGitLab()
        with redirect_stdout(io.StringIO()) as out:
            assert UK.main(args + ["--gitlab", "issue"], ctx=TUK.World().ctx(), gh=FG.GitLab("acme/lib", "glt", session=s)) == 1
        assert "gitlab: issue opened #12" in out.getvalue() and ("POST", "issues") in [c[:2] for c in s.calls]
        assert json.loads((Path(d) / "r.json").read_text())["gitlab"]["number"] == 12
        with mock.patch.dict(os.environ, {}, clear=True):          # no token: exit 2, state not saved
            assert UK.main(args + ["--gitlab", "issue", "--gitlab-project", "acme/lib", "--state", str(Path(d) / "s2")],
                           ctx=TUK.World().ctx()) == 2
        assert "no GitLab token" in json.loads((Path(d) / "r.json").read_text())["gitlab"]["error"]
        assert not (Path(d) / "s2" / "lifecycle_state.json").exists()


# ------------------------------------------------------------------ plugins
def _install_example(d):
    """Make the example plugin discoverable the way pip would: a *.dist-info with entry_points.txt on sys.path."""
    di = Path(d) / "ecadlib_example_plugin-0.1.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: ecadlib-example-plugin\nVersion: 0.1.0\n")
    (di / "entry_points.txt").write_text("[ecadlib.providers]\nstockroom = ecadlib_example_plugin:StockroomCSV\n"
                                         "broken = ecadlib_example_plugin_missing:Nope\n\n"
                                         "[ecadlib.exporters]\nmarkdown-catalog = ecadlib_example_plugin:markdown_catalog\n")
    return [str(d), str(EXAMPLE)]


class _Paths:
    def __init__(self, paths):
        self.paths = paths

    def __enter__(self):
        sys.path[:0] = self.paths
        PL.reset()

    def __exit__(self, *a):
        for p in self.paths:
            sys.path.remove(p)
        sys.modules.pop("ecadlib_example_plugin", None)
        PL.reset()


def test_example_plugin_found_by_entry_points_and_used_in_order():
    with tempfile.TemporaryDirectory() as d, _Paths(_install_example(d)):
        d = Path(d)
        info = PL.describe()
        prov = {p["name"]: p for p in info["providers"]}
        assert prov["stockroom"]["plugin"] and not prov["easyeda"]["plugin"]
        assert [e["name"] for e in info["exporters"]] == ["markdown-catalog"]
        assert info["errors"][0]["plugin"] == "broken" and "ModuleNotFoundError" in info["errors"][0]["error"]
        stock = d / "stock.csv"
        stock.write_text("mpn,manufacturer,package,footprint,symbol,url\nTPS1234DSGR,ACME,SOIC-8,yes,yes,https://stock/TPS\n")
        rules = RU.load({"providers": {"order": ["stockroom", "easyeda", "jlcpcb_search"]}})
        classes = PL.select(None, rules)
        assert [c.name for c in classes] == ["stockroom", "jlcpcb_search", "easyeda"]   # jlcpcb finds EasyEDA's key first
        assert [c.name for c in PL.select("easyeda", rules)] == ["easyeda"]            # an explicit flag wins
        assert [c.name for c in PL.select(None, RU.load({}), default="jlcpcb_search,easyeda")] == ["jlcpcb_search", "easyeda"]
        try:
            PL.select(None, RU.load({"providers": {"order": ["broken"]}}))
            raise AssertionError("unknown provider accepted")
        except ValueError as e:
            assert "unknown provider 'broken'" in str(e) and "failed to load: ModuleNotFoundError" in str(e)
        from tests.test_librarian import FakeHTTP
        http = FakeHTTP({})
        lines = [{"designator": "U1", "mpn": "TPS1234DSGR", "manufacturer": "ACME", "value": "", "footprint": "", "lcsc": ""}]
        with mock.patch.dict(os.environ, {"ECADLIB_STOCKROOM_CSV": str(stock)}):
            rows = T.triage(lines, T.KiCadIndex([]), [c(http) for c in PL.select(["stockroom"], None)], http)
        assert rows[0]["src"] == "stockroom" and rows[0]["src_footprint"] == "yes", rows[0]
        with mock.patch.dict(os.environ, {}, clear=True):                              # not configured: a note, no crash
            rows = T.triage(lines, T.KiCadIndex([]), [c(http) for c in PL.select(["stockroom"], None)], http)
        assert "not configured: stockroom" in rows[0]["notes"]
        # exporter on a plan
        plan = d / "plan.json"
        plan.write_text(json.dumps([{"name": "TPS1234DSGR", "status": "ready", "footprint": {"ref": "SOIC-8"},
                                     "parameters": [{"name": "Manufacturer Part Number", "value": "TPS1234DSGR"},
                                                    {"name": "Manufacturer", "value": "A|CME"}]}]))
        files = PL.run_exporter("markdown-catalog", plan, d / "cat")
        md = Path(files[0]).read_text()
        assert "| TPS1234DSGR | TPS1234DSGR | A\\|CME | SOIC-8 | ready |" in md, md
        with redirect_stdout(io.StringIO()) as out:
            assert PL.main(["list"]) == 0 and PL.main(["export", "markdown-catalog", str(plan), "-o", str(d / "cat2")]) == 0
        assert "stockroom" in out.getvalue() and (d / "cat2" / "catalog.md").is_file()


class Exploding(P.Provider):
    name, _plugin = "exploding", True

    def lookup(self, line):
        raise KeyError("their bug")


class Liar(P.Provider):
    name, _plugin = "liar", True

    def lookup(self, line):
        return {"found": True}


def test_broken_plugin_provider_never_stops_triage():
    from tests.test_librarian import FakeHTTP
    http = FakeHTTP({})
    lines = [{"designator": "U1", "mpn": "X1", "manufacturer": "", "value": "", "footprint": "", "lcsc": ""}]
    rows = T.triage(lines, T.KiCadIndex([]), [Exploding(http), Liar(http)], http)
    assert "exploding: plugin error: KeyError" in rows[0]["notes"] and "liar: plugin returned no SourceResult" in rows[0]["notes"]
    assert rows[0]["status"] == "unverified (source unreachable)"
    class BuiltinBug(P.Provider):                              # a built-in's bug still surfaces (not swallowed)
        name = "builtin_bug"

        def lookup(self, line):
            raise KeyError("ours")
    try:
        T.triage(lines, T.KiCadIndex([]), [BuiltinBug(http)], http)
        raise AssertionError("built-in error swallowed")
    except KeyError:
        pass


def test_workspace_and_mcp_plugins_and_rules_order():
    with tempfile.TemporaryDirectory() as d, _Paths(_install_example(d)):
        ws = Workspace(Path(d) / "ws", offline=True)
        (ws.root / "ecadlib.toml").write_text('[providers]\norder = ["stockroom", "jlcpcb_search"]\n\n[[notify.targets]]\n'
                                              'name = "chan"\ntype = "slack"\nurl_env = "ECADLIB_TEST_HOOK"\n')
        with mock.patch.dict(os.environ, {"ECADLIB_TEST_HOOK": SLACK}):
            info = M.call_tool(ws, "list_plugins", {})
        assert info["order"] == ["stockroom", "jlcpcb_search"] and "markdown-catalog" in [e["name"] for e in info["exporters"]]
        assert info["notify"] == [{"name": "chan", "type": "slack", "events": sorted(N.EVENTS), "url_env": "ECADLIB_TEST_HOOK",
                                   "url_set": True}] and SLACK not in json.dumps(info)
        assert ws._librarian_providers() is None                          # the librarian takes the rules' order
        assert Workspace(Path(d) / "ws", providers=["easyeda"], offline=True)._librarian_providers() == "easyeda"
        assert {"list_plugins", "plugin_export"} <= {t["name"] for t in M.TOOLS}
        try:
            M.call_tool(ws, "plugin_export", {"name": "markdown-catalog", "bom": "nope.csv"})
            raise AssertionError("export without a plan")
        except (ValueError, FileNotFoundError):
            pass


def test_cli_registers_plugins_and_notify():
    from ecadlib import cli
    assert cli.COMMANDS["plugins"][0] == "ecadlib.plugins" and cli.COMMANDS["notify"][0] == "ecadlib.notify"
    with tempfile.TemporaryDirectory() as d:
        rf = Path(d) / "ecadlib_rules.json"
        rf.write_text(json.dumps({"notify": {"targets": [{"name": "chan", "type": "slack", "url_env": "ECADLIB_TEST_HOOK"}]}}))
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as out:
            assert N.main(["show", "--rules", str(rf)]) == 0
            assert N.main(["test", "--rules", str(rf)]) == 1                # variable not set: reported, exit 1
            assert N.main(["test", "--rules", str(rf), "--target", "nope"]) == 1
        assert "$ECADLIB_TEST_HOOK (NOT SET)" in out.getvalue() and "skipped: $ECADLIB_TEST_HOOK is not set" in out.getvalue()


if __name__ == "__main__":
    import tests.run_all
    sys.exit(tests.run_all.main(["test_hooks"]))
