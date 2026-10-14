"""Weekly upkeep (offline: every distributor, GitLab, Altium 365 and GitHub answer here is a FAKE; nothing real is
called). Run:  python3 -m tests.test_upkeep"""
import copy, json, os, tempfile
from pathlib import Path

from ecadlib import cli, mcp_server as M, upkeep as UK
from ecadlib.app import Workspace
from tests import test_sourcing as TS, test_upstream as TU

NOW1, NOW2, NOW3 = "2026-10-05T06:23:00+00:00", "2026-10-12T06:23:00+00:00", "2026-10-19T06:23:00+00:00"
CREDS = {"NEXAR_SUPPLY_TOKEN": "t"}


class World:
    """Distributor fakes (Nexar Supply + JLCPCB shapes from test_sourcing) whose answers a test can change between runs."""
    def __init__(self):
        self.nexar, self.jlc = copy.deepcopy(TS.NEXAR), copy.deepcopy(TS.JLC)

    def route(self, method, url, body):
        if "identity.nexar.com" in url:
            return 200, {"access_token": "tok"}
        b = json.loads(body) if body else {}
        if "api.nexar.com/graphql" in url and "supSearchMpn" in b["query"]:
            p = self.nexar.get(b["variables"]["q"])
            return 200, {"data": {"supSearchMpn": {"hits": int(bool(p)), "results": [{"part": p}] if p else []}}}
        if "jlcpcb.com" in url:
            it = self.jlc.get(b["keyword"])
            return 200, {"data": {"componentPageInfo": {"list": [it] if it else []}}}
        return 0, "no route"

    def ctx(self, env=CREDS, **kw):
        return UK.Ctx(http=TS.Router(self.route), env=dict(env), **kw)

    def lifecycle(self, mpn, word):
        for s in self.nexar[mpn]["specs"]:
            if s["attribute"]["shortname"] == "lifecyclestatus":
                s["displayValue"] = s["value"] = word


def kinds(rep, check):
    s = next(s for s in rep["checks"] if s["check"] == check)
    return sorted((c["kind"], c["item"]) for c in s["changes"])


def section(rep, check):
    return next(s for s in rep["checks"] if s["check"] == check)


def test_lifecycle_and_sourcing_report_only_new_changes():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TS.BOM)
        w, st, out = World(), Path(d) / "state", Path(d) / "upkeep"
        go = lambda now: UK.run(bom, ["lifecycle", "sourcing"], st, out, ctx=w.ctx(), now=now)
        r1 = go(NOW1)
        assert section(r1, "lifecycle")["status"] == "ok" and section(r1, "sourcing")["status"] == "ok", r1
        assert ("new risky part", "CH340C") in kinds(r1, "lifecycle"), kinds(r1, "lifecycle")
        assert ("stock-out", "CH340C") in kinds(r1, "sourcing"), kinds(r1, "sourcing")
        assert "first run" in "; ".join(section(r1, "sourcing")["notes"])
        assert r1["exit_code"] == 1 and (st / "lifecycle_state.json").exists() and (st / "sourcing_state.json").exists()

        r2 = go(NOW2)                                                     # same answers: nothing new
        assert r2["new_changes"] == 0 and r2["exit_code"] == 0, [s["changes"] for s in r2["checks"]]
        assert "Nothing new since the last run." in out.with_suffix(".md").read_text()
        assert r2["previous_run"] == NOW1

        w.lifecycle("0603WAF1002T5E", "Obsolete")                        # EOL transition
        for _, offers in [(s["company"]["name"], s["offers"]) for s in w.nexar["0603WAF1002T5E"]["sellers"]]:
            for o in offers:
                for p in o["prices"]:
                    p["price"] = p["convertedPrice"] = p["price"] * 3     # price jump on every offer
        for p in w.jlc["C25804"]["componentPrices"]:
            p["productPrice"] *= 3
        w.nexar["CL05B104KO5NNNC"]["sellers"][0]["offers"][0]["inventoryLevel"] = 0   # the only stock is gone
        r3 = go(NOW3)
        assert kinds(r3, "lifecycle") == [("EOL", "0603WAF1002T5E")], kinds(r3, "lifecycle")
        lc = section(r3, "lifecycle")["changes"][0]
        assert lc["was"].startswith("Active") and lc["now"].startswith("Obsolete")
        assert kinds(r3, "sourcing") == [("price jump", "0603WAF1002T5E"), ("stock-out", "CL05B104KO5NNNC")], kinds(r3, "sourcing")
        pj = next(c for c in section(r3, "sourcing")["changes"] if c["kind"] == "price jump")
        assert "+200%" in pj["detail"] and "threshold 20%" in pj["detail"], pj
        md = out.with_suffix(".md").read_text()
        assert "## Lifecycle" in md and "| EOL | 0603WAF1002T5E |" in md and "| price jump | 0603WAF1002T5E |" in md
        js = json.loads(out.with_suffix(".json").read_text())
        assert js["new_changes"] == 3 and js["exit_code"] == 1 and all("_state" not in s for s in js["checks"])

        r4 = go(NOW3)
        assert r4["new_changes"] == 0 and r4["exit_code"] == 0
        # a higher threshold hides the same jump on a fresh state
        st2 = Path(d) / "state2"
        UK.run(bom, ["sourcing"], st2, out, ctx=World().ctx(), now=NOW1)
        r5 = UK.run(bom, ["sourcing"], st2, out, rules={"upkeep": {"price_jump_pct": 500}}, ctx=w.ctx(), now=NOW2)
        assert kinds(r5, "sourcing") == [("stock-out", "CL05B104KO5NNNC")]


def test_missing_credentials_skip_checks_not_fail():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TS.BOM)
        dead = UK.Ctx(http=TS.Router(lambda m, u, b: (0, "blocked")), env={})
        rep = UK.run(bom, None, Path(d) / "s", Path(d) / "r", ctx=dead, now=NOW1)
        got = {s["check"]: (s["status"], s["reason"]) for s in rep["checks"]}
        assert set(got) == set(UK.CHECKS)
        assert all(v[0] == "skipped" for v in got.values()), got
        assert "MOUSER_API_KEY" in got["lifecycle"][1] and "credentials" in got["sourcing"][1]
        assert "manifest" in got["upstream"][1] and "plan.json" in got["a365"][1]
        assert rep["exit_code"] == 0 and rep["new_changes"] == 0
        md = (Path(d) / "r.md").read_text()
        assert md.count("| skipped: ") == 4, md
        assert not (Path(d) / "s" / "lifecycle_state.json").exists()
        # with a plan but no workspace / token, the a365 check says which is missing
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps(TU.plan_items()))
        rep = UK.run(plan, ["a365"], Path(d) / "s", Path(d) / "r", ctx=dead, now=NOW1)
        assert "workspace" in section(rep, "a365")["reason"]
        rep = UK.run(plan, ["a365"], Path(d) / "s", Path(d) / "r", workspace="https://ws", ctx=dead, now=NOW1)
        assert section(rep, "a365")["status"] == "skipped" and "NEXAR_TOKEN" in section(rep, "a365")["reason"]
        rep = UK.run(plan, ["a365"], Path(d) / "s", Path(d) / "r", rules={"altium365": {"workspace": "https://ws"}},
                     ctx=dead, now=NOW1)
        assert "NEXAR_TOKEN" in section(rep, "a365")["reason"]                 # workspace taken from the rules
        # a credentialed source that never answers is an error (exit 2), not a skip
        rep = UK.run(bom, ["lifecycle"], Path(d) / "s", Path(d) / "r", ctx=UK.Ctx(http=dead.http, env=CREDS), now=NOW1)
        assert section(rep, "lifecycle")["status"] == "error" and rep["exit_code"] == 2


def _plan_in(d):
    TU.convert(d)                                                     # d/out/manifest.json from the KiCad fixtures
    plan = Path(d) / "out" / "plan.json"
    plan.write_text(json.dumps(TU.plan_items()))
    return plan


def test_upstream_new_only_then_apply_reconverts():
    with tempfile.TemporaryDirectory() as d:
        plan, st, out = _plan_in(d), Path(d) / "state", Path(d) / "r"
        http = TU.seeded_http(d)
        ctx = lambda files: UK.Ctx(http=http, gitlab=TU.U.GitLab(session=TU.FakeGitLab(files)), env={})
        go = lambda files, now, apply=False: UK.run(plan, ["upstream"], st, out, apply=apply, ctx=ctx(files), now=now)
        assert go(TU.upstream_now(), NOW1)["exit_code"] == 0              # manifest found next to the plan
        r = go(TU.upstream_now(TU.USB2, TU.REG), NOW2)
        assert kinds(r, "upstream") == [("KiCad changed", "footprint:USB_C_16P")], kinds(r, "upstream")
        assert "B12" in section(r, "upstream")["changes"][0]["detail"] and not r["applied_paths"]
        assert go(TU.upstream_now(TU.USB2, TU.REG), NOW3)["new_changes"] == 0   # reported once, not every week
        r = go(TU.upstream_now(TU.USB2, TU.REG, step=None), NOW3)          # the STEP model is deleted upstream too
        assert kinds(r, "upstream") == [("KiCad deleted", "footprint:USB_C_16P")], kinds(r, "upstream")

        old_sha = json.loads((plan.parent / "manifest.json").read_text())["entries"]["footprint:USB_C_16P"]["output_sha256"]
        r = go(TU.upstream_now(TU.USB2, TU.REG), NOW3, apply=True)
        assert ("reconverted", "footprint:USB_C_16P") in kinds(r, "upstream"), kinds(r, "upstream")
        assert sorted(Path(p).name for p in r["applied_paths"]) == ["TYPE-C-31-M-12.SchLib", "USB_C_16P.PcbLib", "manifest.json"], \
            r["applied_paths"]                                            # the spec's symbol is rewritten with it
        new = json.loads((plan.parent / "manifest.json").read_text())["entries"]["footprint:USB_C_16P"]
        assert new["output_sha256"] != old_sha and new["sources"][0]["blob_id"] == TU.U.digests(TU.USB2)[1]
        assert "## Applied" in out.with_suffix(".md").read_text()
        r = go(TU.upstream_now(TU.USB2, TU.REG), NOW3)                    # library now matches upstream
        assert r["new_changes"] == 0 and r["exit_code"] == 0, kinds(r, "upstream")

        r = go(TU.upstream_now(TU.USB2, TU.REG, step=None), NOW3, apply=True)   # deleted: never applied
        s = section(r, "upstream")
        assert kinds(r, "upstream") == [("KiCad deleted", "footprint:USB_C_16P")] and not r["applied_paths"]
        assert any("needs a person" in n for n in s["notes"]), s["notes"]
        bad = UK.Ctx(http=http, gitlab=TU.U.GitLab(session=TU.FakeGitLab({}, fail=True)), env={})
        r = UK.run(plan, ["upstream"], st, out, ctx=bad, now=NOW3)
        assert section(r, "upstream")["status"] == "error" and r["exit_code"] == 2


def test_a365_drift_new_only():
    with tempfile.TemporaryDirectory() as d:
        items = TU.plan_items()
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps(items))
        ch, drift = TU.ws_nodes(items)
        st, out = Path(d) / "s", Path(d) / "r"
        go = lambda nodes, now: UK.run(plan, ["a365"], st, out, workspace="https://ws",
                                       ctx=UK.Ctx(design=TU.FakeWorkspace(nodes), env={}), now=now)
        r1 = go([ch, drift], NOW1)
        k = kinds(r1, "a365")
        assert ("A365 missing", "TPS62133RGTR") in k and ("A365 parameter", "AMS1117-3.3") in k and ("A365 symbol", "AMS1117-3.3") in k
        assert not [x for x in k if x[1] == "CH340C"] and r1["exit_code"] == 1
        assert go([ch, drift], NOW2)["new_changes"] == 0
        ch2 = TU.node("CH340C", "Components/ICs", {p["name"]: p["value"] for p in ch["details"]["parameters"]}, rev="r2",
                      comment=ch["comment"], description=ch["description"])
        r3 = go([ch2, drift], NOW3)
        assert kinds(r3, "a365") == [("A365 revision", "CH340C")], kinds(r3, "a365")
        assert section(r3, "a365")["changes"][0]["was"] == "r1"


# ------------------------------------------------------------------ GitHub (fake REST session, fake gh / git runner)
class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, issues=()):
        self.issues, self.calls = list(issues), []

    def request(self, method, url, json=None, headers=None, timeout=None):
        assert url.startswith("https://api.github.com/repos/me/lib/"), url
        assert headers["Authorization"] == "Bearer ghs_x"
        path = url.split("/repos/me/lib/", 1)[1]
        self.calls.append((method, path, json))
        if method == "GET" and path.startswith("issues?labels=ecadlib-upkeep"):
            return FakeResp(200, self.issues)
        if method == "POST" and path == "labels":
            return FakeResp(422, {"message": "already_exists"})
        if method == "POST" and path == "issues":
            return FakeResp(201, {"number": 41, "html_url": "https://github.com/me/lib/issues/41"})
        return FakeResp(200, {})


def _rep(new=2, applied=()):
    return {"date": NOW2, "previous_run": NOW1, "input": "plan.json", "input_kind": "plan", "schedule": "weekly",
            "new_changes": new, "applied_paths": list(applied), "state_dir": "s", "exit_code": 1 if new else 0,
            "checks": [{"check": "lifecycle", "status": "ok", "reason": "", "notes": [], "resolved": [], "applied": [],
                        "changes": [UK._change("EOL", f"P{i}", "Active", "Obsolete") for i in range(new)]}]}


def test_github_rolling_issue_rest():
    s = FakeSession()
    gh = UK.GitHub("me/lib", "ghs_x", session=s)
    res = UK.publish(_rep(), "issue", gh)
    assert res == {"kind": "issue", "action": "opened", "number": 41, "url": "https://github.com/me/lib/issues/41"}
    posts = [(m, p) for m, p, _ in s.calls if m != "GET"]
    assert posts == [("POST", "labels"), ("POST", "issues")], s.calls
    body = s.calls[-1][2]
    assert body["labels"] == ["ecadlib-upkeep"] and "| EOL | P0 |" in body["body"] and "2 new change(s)" in body["title"]

    s = FakeSession([{"number": 9, "pull_request": {}}, {"number": 12, "html_url": "u12"}, {"number": 7, "html_url": "u7"}])
    res = UK.publish(_rep(), "issue", UK.GitHub("me/lib", "ghs_x", session=s))
    assert (res["action"], res["number"]) == ("updated", 7)                # oldest open issue, never the PR
    assert [(m, p) for m, p, _ in s.calls] == [("GET", "issues?labels=ecadlib-upkeep&state=open&per_page=100"),
                                               ("PATCH", "issues/7"), ("POST", "issues/7/comments")], s.calls
    s = FakeSession()
    assert UK.publish(_rep(0), "issue", UK.GitHub("me/lib", "ghs_x", session=s))["action"] == "none" and not s.calls
    big = _rep(3000)
    assert len(UK._body(big)) <= UK.BODY_MAX and "more in the JSON report" in UK._body(big)
    try:
        UK.GitHub(None, "ghs_x")
        raise AssertionError("token without repo accepted")
    except UK.GitHubError:
        pass


class FakeRunner:
    """git and `gh api` stand-in: records argv, answers from a small script."""
    def __init__(self, dirty=True, prs=(), email=False):
        self.calls, self.dirty, self.prs, self.email = [], dirty, list(prs), email

    def __call__(self, cmd, input=None, cwd=None):
        self.calls.append((cmd, input))
        if cmd[0] == "git":
            a = cmd[1:]
            if a[0] == "status":
                return 0, " M lib/PcbLib/USB_C_16P.PcbLib\n" if self.dirty else "", ""
            if a[0] == "rev-parse":
                return 0, "main\n", ""
            if a[:2] == ["config", "user.email"]:
                return (0, "me@x\n", "") if self.email else (1, "", "")
            return 0, "", ""
        assert cmd[:3] == ["gh", "api", "-X"], cmd
        method, path = cmd[3], cmd[4]
        assert path.startswith("repos/{owner}/{repo}/"), path          # gh resolves the repo from the checkout
        path = path.split("/", 3)[3]
        if method == "GET" and path.startswith("pulls"):
            return 0, json.dumps(self.prs), ""
        if method == "GET" and path.startswith("issues"):
            return 0, "[]", ""
        if method == "POST" and path == "pulls":
            return 0, json.dumps({"number": 5, "html_url": "https://github.com/me/lib/pull/5"}), ""
        if method == "POST" and path == "labels":
            return 1, "", "HTTP 422: already_exists"
        if method == "POST" and path == "issues":
            return 0, json.dumps({"number": 6}), ""
        return 0, "{}", ""


def test_github_pr_branch_with_gh_cli():
    run = FakeRunner()
    gh = UK.GitHub(runner=run)
    assert gh.mode == "gh"
    res = UK.publish(_rep(1, ["lib/PcbLib/USB_C_16P.PcbLib", "lib/manifest.json"]), "pr", gh, run)
    assert res == {"kind": "pr", "action": "opened", "number": 5, "url": "https://github.com/me/lib/pull/5",
                   "branch": "ecadlib/upkeep-2026-10-12"}, res
    git = [c[1:] for c, _ in run.calls if c[0] == "git"]
    assert ["switch", "-C", "ecadlib/upkeep-2026-10-12"] in git and ["switch", "main"] == git[-1], git
    assert ["add", "--", "lib/PcbLib/USB_C_16P.PcbLib", "lib/manifest.json"] in git
    commit = next(g for g in git if "commit" in g)
    assert "user.name=ecadlib-upkeep" in commit                          # no git identity configured: a bot one
    assert ["push", "--force-with-lease=refs/heads/ecadlib/upkeep-2026-10-12:", "-u", "origin", "ecadlib/upkeep-2026-10-12"] in git
    assert not any(g[:1] == ["push"] and "--force" in g for g in git)    # never a blind force push
    gha = [(c[3], c[4].split("/", 3)[3], json.loads(i) if i else None) for c, i in run.calls if c[0] == "gh"]
    pr = next(x for x in gha if x[:2] == ("POST", "pulls"))[2]
    assert (pr["head"], pr["base"]) == ("ecadlib/upkeep-2026-10-12", "main") and "## Applied" in pr["body"]
    assert ("POST", "issues/5/labels", {"labels": ["ecadlib-upkeep"]}) in gha

    run = FakeRunner(prs=[{"number": 3, "head": {"ref": "ecadlib/upkeep-2026-10-12"}, "html_url": "u3"}], email=True)
    res = UK.publish(_rep(1, ["lib/x.PcbLib"]), "pr", UK.GitHub(runner=run), run)
    assert (res["action"], res["number"]) == ("updated", 3)
    assert not any("user.name=ecadlib-upkeep" in c for c, _ in run.calls)
    assert ("PATCH", "pulls/3") in [(c[3], c[4].split("/", 3)[3]) for c, _ in run.calls if c[0] == "gh"]

    run = FakeRunner(dirty=False)                                         # nothing to commit: the issue instead
    res = UK.publish(_rep(1, ["lib/x.PcbLib"]), "pr", UK.GitHub(runner=run), run)
    assert (res["kind"], res["action"], res["number"]) == ("issue", "opened", 6)
    assert not any(c[1] == "switch" for c, _ in run.calls if c[0] == "git")
    run = FakeRunner()
    assert UK.publish(_rep(1), "pr", UK.GitHub(runner=run), run)["kind"] == "issue"   # report-only run


def test_pr_rerun_never_overwrites_a_persons_commits():
    """Real git against a local bare remote: a same-day re-run replaces upkeep's own branch, but refuses when a person
    pushed a fix to it (the old blind --force push deleted that commit)."""
    import subprocess
    if not __import__("shutil").which("git"):
        return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        sh = lambda *a, cwd: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
        remote, work, human = d / "remote.git", d / "work", d / "human"
        sh("git", "init", "-q", "--bare", str(remote), cwd=d)
        sh("git", "clone", "-q", str(remote), str(work), cwd=d)
        for c in (work,):
            sh("git", "config", "user.email", "a@b", cwd=c), sh("git", "config", "user.name", "a", cwd=c)
        (work / "lib.txt").write_text("v1\n")
        sh("git", "add", ".", cwd=work), sh("git", "commit", "-qm", "init", cwd=work)
        sh("git", "push", "-q", "origin", "HEAD", cwd=work)

        class GH:
            def api(self, m, p, body=None):
                return [] if m == "GET" else {"number": 1}

            def ensure_label(self):
                pass
        rep, br = _rep(1, [str(work / "lib.txt")]), "ecadlib/upkeep-2026-10-12"
        (work / "lib.txt").write_text("v2\n")
        assert UK.publish_pr(rep, GH(), cwd=work)["branch"] == br
        (work / "lib.txt").write_text("v3\n")
        assert UK.publish_pr(rep, GH(), cwd=work)["branch"] == br             # upkeep's own branch: replaced
        assert sh("git", "log", "--format=%s", br, cwd=remote).splitlines()[0].startswith("ecadlib upkeep ")
        assert len(sh("git", "log", "--format=%s", br, cwd=remote).splitlines()) == 2
        sh("git", "clone", "-q", "-b", br, str(remote), str(human), cwd=d)
        sh("git", "config", "user.email", "h@b", cwd=human), sh("git", "config", "user.name", "h", cwd=human)
        (human / "fix.txt").write_text("hand fix\n")
        sh("git", "add", ".", cwd=human), sh("git", "commit", "-qm", "hand fix", cwd=human), sh("git", "push", "-q", cwd=human)
        (work / "lib.txt").write_text("v4\n")
        try:
            UK.publish_pr(rep, GH(), cwd=work)
            raise AssertionError("overwrote a person's commit")
        except UK.GitHubError as e:
            assert "hand fix" in str(e)
        assert sh("git", "log", "-1", "--format=%s", br, cwd=remote) == "hand fix"


def test_pr_lookup_ignores_fork_prs_with_the_same_branch_name():
    fork = {"number": 8, "head": {"ref": "ecadlib/upkeep-2026-10-12", "repo": {"full_name": "someone/fork"}},
            "base": {"repo": {"full_name": "me/lib"}}}
    run = FakeRunner(prs=[fork])
    res = UK.publish(_rep(1, ["lib/x.PcbLib"]), "pr", UK.GitHub(runner=run), run)
    assert (res["action"], res["number"]) == ("opened", 5), res
    assert ("PATCH", "pulls/8") not in [(c[3], c[4].split("/", 3)[3]) for c, _ in run.calls if c[0] == "gh"]


def test_sourcing_unpriced_line_does_not_carry_a_price_across_currencies():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TS.BOM)
        w, st = World(), Path(d) / "state"
        st.mkdir()
        from ecadlib.kicad_index import norm_mpn
        (st / "sourcing_state.json").write_text(json.dumps({"version": 1, "updated": NOW1, "lines": {
            norm_mpn("0603WAF1002T5E"): {"mpn": "0603WAF1002T5E", "unit": 99.0, "currency": "EUR", "distributor": "X",
                                         "in_stock": 2, "checked": NOW1}}}))
        for s in w.nexar["0603WAF1002T5E"]["sellers"]:
            for o in s["offers"]:
                o["prices"] = []
        w.jlc["C25804"]["componentPrices"] = []
        UK.run(bom, ["sourcing"], st, Path(d) / "r", ctx=w.ctx(), now=NOW2)
        line = json.loads((st / "sourcing_state.json").read_text())["lines"][norm_mpn("0603WAF1002T5E")]
        assert line["currency"] == "USD" and line["unit"] is None, line          # not 99 EUR relabelled as USD


def test_cli_exit_codes_and_failed_github_keeps_state():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TS.BOM)
        w, st = World(), Path(d) / "s"
        args = [str(bom), "--checks", "lifecycle,sourcing", "--state", str(st), "-o", str(Path(d) / "r")]

        class Denied(FakeSession):
            def request(self, method, url, **kw):
                self.calls.append((method, url, None))
                return FakeResp(401, {"message": "Bad credentials"})
        assert UK.main(args + ["--github", "issue"], ctx=w.ctx(), gh=UK.GitHub("me/lib", "ghs_x", session=Denied())) == 2
        assert not st.exists() or not (st / "lifecycle_state.json").exists()      # changes will be reported again
        assert "HTTP 401" in json.loads((Path(d) / "r.json").read_text())["github"]["error"]
        s = FakeSession()
        assert UK.main(args + ["--github", "issue"], ctx=w.ctx(), gh=UK.GitHub("me/lib", "ghs_x", session=s)) == 1
        assert (st / "lifecycle_state.json").exists() and ("POST", "issues") in [(m, p) for m, p, _ in s.calls]
        s = FakeSession()
        assert UK.main(args + ["--github", "issue"], ctx=w.ctx(), gh=UK.GitHub("me/lib", "ghs_x", session=s)) == 0
        assert not s.calls                                                       # nothing new: GitHub untouched
        assert UK.main(args + ["--no-save"], ctx=w.ctx()) == 0
        assert UK.main([str(bom), "--checks", "bogus", "--state", str(st), "-o", str(Path(d) / "r")]) == 2
        assert UK.main([str(Path(d) / "missing.csv"), "-o", str(Path(d) / "r")]) == 2
        assert cli.COMMANDS["upkeep"][0] == "ecadlib.upkeep"


def test_rules_defaults_and_checks():
    assert UK.upkeep_rules(None) == UK.DEFAULTS and UK.upkeep_rules({})["checks"] == list(UK.CHECKS)
    assert UK.upkeep_rules({"upkeep": {"checks": ["a365"], "schedule": "daily"}})["schedule"] == "daily"
    assert isinstance(UK.load_rules(None, start=tempfile.gettempdir()), dict)
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text(TS.BOM)
        rep = UK.run(bom, None, Path(d) / "s", Path(d) / "r", rules={"upkeep": {"checks": ["a365"], "schedule": "daily"}},
                     ctx=World().ctx(), now=NOW1)
        assert [s["check"] for s in rep["checks"]] == ["a365"] and rep["schedule"] == "daily"
        assert "(daily)" in (Path(d) / "r.md").read_text()


def test_workspace_and_mcp_tool():
    assert "upkeep_run" in {t["name"] for t in M.TOOLS}
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "ws", offline=True)
        bom = ws.save_upload("bom.csv", TS.BOM.encode())
        bom = bom["path"] if isinstance(bom, dict) else bom
        r = M.call_tool(ws, "upkeep_run", {"bom": bom, "checks": ["upstream", "a365"]})
        assert [c["status"] for c in r["checks"]] == ["skipped", "skipped"] and r["exit_code"] == 0, r
        assert r["reports"] == ["upkeep/bom/upkeep.md", "upkeep/bom/upkeep.json"] and r["input"] == "uploads/bom.csv"
        assert (ws.root / "upkeep" / "bom" / "upkeep.md").exists()


def test_example_workflow_is_an_example_only():
    root = Path(__file__).resolve().parents[1]
    y = (root / "examples" / "upkeep.yml").read_text()
    for s in ("cron:", "workflow_dispatch", "NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET", "DIGIKEY_CLIENT_ID",
              "DIGIKEY_CLIENT_SECRET", "MOUSER_API_KEY", "NEXAR_TOKEN", "issues: write", "--github issue", "--github pr",
              "python -m ecadlib upkeep", "out/upkeep_state"):
        assert s in y, s
    assert not any("ecadlib upkeep" in f.read_text() for f in (root / ".github" / "workflows").glob("*.yml"))
    try:
        import yaml
    except ImportError:
        return
    doc = yaml.safe_load(y)
    assert doc[True]["schedule"][0]["cron"] and "workflow_dispatch" in doc[True]            # `on:` parses as True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
