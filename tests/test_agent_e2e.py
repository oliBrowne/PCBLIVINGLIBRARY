"""End-to-end test of the library agent with the REAL modules together: agent_watch -> agent_queue -> librarian ->
visual_check -> batch PR -> web UI review (team mode) -> approvals/release check -> push -> upkeep, plus the MCP tools.
Run:  python3 -m tests.test_agent_e2e

Only external services are faked: the Anthropic Messages API (requests.post, in the documented response shape; the
model answers are SYNTHETIC), GitHub REST (a requests-like session), Altium 365 (a Nexar-like client) and the KiCad /
EasyEDA web sources (test_librarian's FakeHTTP). git is real (a temp repo with a bare origin), the datasheets are PDFs
generated here, and the web server is the real one on an ephemeral port."""
import contextlib, io, json, os, re, subprocess, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import agent as A, agent_queue as AQ, cli, datasheet as D, rules as RU, upkeep as UK
from ecadlib.app import Workspace
from tests import test_librarian as TL, test_team as TT, test_visual_check as TV

RULES = '''
[agent]
enabled = true
github_repo = "acme/lib"
upkeep_schedule = "off"
datasheet_batch = false
batch_size = 20

[agent.watch]
sources = [ { type = "bom_file", ref = "boms/main.csv" }, { type = "folder", ref = "uploads" } ]

[agent.proof]
max_pages = 4
api_key_env = "ANTHROPIC_API_KEY"

[release.approvals]
required = 1
roles = ["approver"]

[altium365]
workspace = "https://acme.365.altium.com"
'''
HEAD = "Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
MAIN = HEAD + """R1,1,10k,R_0603_1608Metric,ACME,RC0603-10K,C1001,Resistor 10k 0603,datasheets/res.pdf
D1,1,RED,LED_0603_1608Metric,ACME,LED-RED-0603,,LED red 0603,datasheets/led.pdf
U1,1,TPS1234DSGR,SOIC-8_3.9x4.9mm_P1.27mm,ACME,TPS1234DSGR,C3003,Test regulator,datasheets/tps.pdf
"""
OLD_UPLOAD = HEAD + "Q9,1,OLDPART1,,ACME,OLDPART1,,superseded upload,\n"
NEW_UPLOAD = HEAD + "Q1,1,EEPART1,,ACME,EEPART1,C2002,EasyEDA-only part,datasheets/ee.pdf\n"
ALL_OK = [{"aspect": a, "status": "match", "severity": "info", "expected": "as drawn", "found": "as drawn", "page": 3, "note": ""}
          for a in ("pitch", "pad_count", "pad_numbering", "pin1_location", "view_orientation", "body_size", "pad_size",
                    "pin_count", "pin_numbers", "pin_names")]
PITCH = [{"aspect": "pitch", "status": "mismatch", "severity": "error", "expected": "e = 1.27 mm", "found": "1.55 mm",
          "page": 3, "note": "outline D0008A"}]


def gitc(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Anthropic:
    """requests.post for the Messages API: the answer depends on the part named in the task (SYNTHETIC answers)."""
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        task = json["messages"][0]["content"][-1]["text"]
        part = re.search(r"Part: (\S+)", task).group(1)
        self.calls.append(part)
        assert headers["x-api-key"] == "sk-test-e2e" and url.endswith("/v1/messages"), (url, headers)
        n_img = sum(c["type"] == "image" for c in json["messages"][0]["content"])
        assert n_img >= 3, n_img                         # datasheet page(s) + our footprint + our symbol
        if part in ("TPS1234DSGR", "EEPART1"):
            msg = TV.message(TV.answer("pass", ALL_OK, 0.97, "pitch, pads, pin 1 and pins match outline D0008A"))
        elif part == "LED-RED-0603":
            msg = TV.message(TV.answer("fail", PITCH, 0.1, "the pitch differs"))
        else:
            msg = TV.message(None, stop="refusal", text="")
        return TV.Resp(200, msg)


class GitHub:
    """requests-like session for GitHub REST; numbers PRs, remembers their bodies."""
    def __init__(self):
        self.calls, self.prs = [], {}

    def request(self, method, url, json=None, timeout=None, headers=None):
        assert headers["Authorization"] == "Bearer tok"
        self.calls.append((method, url, json))
        if method == "GET":
            body = []
        elif method == "POST":
            n = 7 + len(self.prs)
            self.prs[n] = dict(json)
            body = {"number": n, "html_url": f"https://github.com/acme/lib/pull/{n}"}
        else:
            n = int(url.rsplit("/", 1)[1])
            self.prs[n].update(json)
            body = {"number": n, "html_url": f"https://github.com/acme/lib/pull/{n}"}
        return mock.Mock(status_code=201 if method == "POST" else 200, json=lambda: body, content=b"x", text="")

    def verbs(self):
        return [c[0] for c in self.calls]


class Nexar:
    def __init__(self):
        self.released = []

    def release_defs(self, ws):
        return {}

    def upload(self, p):
        return "file-" + Path(p).name

    def find(self, ws, name):
        return None

    def release(self, inp):
        self.released.append(inp)


def make_ws(d):
    d = Path(d)
    ws = d / "ws"
    ws.mkdir()
    gitc(ws, "init", "-q", "-b", "main")
    gitc(ws, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "init")
    gitc(d, "init", "-q", "--bare", "remote.git")
    gitc(ws, "remote", "add", "origin", str(d / "remote.git"))
    gitc(ws, "push", "-q", "origin", "main")
    (ws / "ecadlib.toml").write_text(RULES)
    for sub in ("boms", "datasheets"):
        (ws / sub).mkdir()
    for n in ("res", "led", "tps", "ee"):
        (ws / "datasheets" / f"{n}.pdf").write_bytes(TV.make_pdf(TV.DS_PAGES))
    (ws / "boms" / "main.csv").write_text(MAIN)
    for stamp, text in (("20261014-060000-aaaaaa", OLD_UPLOAD), ("20261015-060000-bbbbbb", NEW_UPLOAD)):
        (ws / "uploads" / stamp).mkdir(parents=True)
        (ws / "uploads" / stamp / "extra.csv").write_text(text)      # the newest upload of extra.csv counts
    return ws


@contextlib.contextmanager
def services(key=True):
    """Every external service faked for the real modules (also for code paths that build their own clients)."""
    api, gh, http = Anthropic(), GitHub(), TL.FakeHTTP({"C2002": TL.EE, "C3003": TL.ee_as("TPS1234DSGR", "C3003")})
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "NEXAR_TOKEN", "ECADLIB_USER")}
    env["GITHUB_TOKEN"] = "tok"
    if key:
        env["ANTHROPIC_API_KEY"] = "sk-test-e2e"
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(D.requests, "post", api), \
            mock.patch("ecadlib.httpcache.CachedHTTP", lambda *a, **k: http), mock.patch("requests.Session", lambda: gh):
        yield api, gh


def cli_run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def by_mpn(ws):
    return {i["mpn"]: i for i in AQ.Queue(ws).list()}


def plan_status(ws):
    plan = next(Path(ws).glob("librarian/*/plan.json"))
    return {i["name"]: i["status"] for i in json.loads(plan.read_text())}


# ------------------------------------------------------------------ the whole loop
def test_rules_validate_with_agent_watch_and_proof_together():
    with tempfile.TemporaryDirectory() as d:
        ws = make_ws(d)
        r = RU.load(ws / "ecadlib.toml")
        assert r["agent"]["enabled"] is True and r["agent"]["proof"]["max_pages"] == 4
        assert [s["type"] for s in r["agent"]["watch"]["sources"]] == ["bom_file", "folder"]
        assert r["agent"]["watch"]["interval"] == RU.DEFAULTS["agent"]["watch"]["interval"]
        rc, out = cli_run("rules", "check", str(ws / "ecadlib.toml"))
        assert rc == 0, out


def test_full_loop_real_modules():
    with tempfile.TemporaryDirectory() as d, services() as (api, gh):
        ws = make_ws(d)

        # 0. dry run through the CLI: reports, writes nothing
        rc, out = cli_run("agent", "--workspace", str(ws), "run", "--dry-run")
        rep = json.loads(out[out.index("{"):])
        assert rc == 0 and rep["dry_run"] and sorted(c["mpn"] for c in rep["detected"]) == \
            ["EEPART1", "LED-RED-0603", "RC0603-10K", "TPS1234DSGR"], rep["detected"]         # not the superseded upload
        assert not (ws / "agent" / "watch_state.json").exists() and AQ.Queue(ws).list() == [] and not api.calls

        # 1. the real run through the CLI entry point
        rc, out = cli_run("agent", "--workspace", str(ws), "run")
        assert rc == 0, out
        rep = json.loads(out[out.index("\n{") + 1:])
        items = by_mpn(ws)
        assert {m: i["status"] for m, i in items.items()} == {"TPS1234DSGR": "review", "EEPART1": "review",
                                                               "RC0603-10K": "review", "LED-RED-0603": "failed"}, items
        verdict = {m: (i["proof"] or {}).get("verdict") for m, i in items.items()}
        assert verdict == {"TPS1234DSGR": "pass", "EEPART1": "pass", "RC0603-10K": "doubt", "LED-RED-0603": "fail"}, verdict
        assert sorted(api.calls) == ["EEPART1", "LED-RED-0603", "RC0603-10K", "TPS1234DSGR"]
        led, rc_, tps = items["LED-RED-0603"], items["RC0603-10K"], items["TPS1234DSGR"]
        assert "pitch" in led["history"][-1]["note"] and "e = 1.27 mm" in led["proof"]["reason"], led["history"]
        assert "declined" in rc_["proof"]["reason"] and items["EEPART1"]["change"]["source"]["type"] == "folder"
        for it in (tps, rc_, items["EEPART1"]):
            assert it["build"]["bom"] == "agent/library_bom.csv" and it["build"]["user"] == A.AGENT
            assert [Path(p).name for p in it["pictures"]["after"]] == ["after_footprint.png", "after_symbol.png"]
            assert all((ws / p).read_bytes()[:4] == b"\x89PNG" for p in it["pictures"]["after"]) and it["pictures"]["before"] == []
            assert it["proof"]["images"]["datasheet"] and all(Path(p).is_file() for p in it["proof"]["images"]["datasheet"])
        assert tps["proof"]["model"] == "claude-opus-5-5" and tps["proof"]["cost_usd"] > 0

        # one batch, one PR on the real repo (working tree and HEAD untouched), body linking the pictures
        assert len(rep["batches"]) == 1 and rep["batches"][0]["fallback"] is None, rep["batches"]
        b = rep["batches"][0]
        assert gh.verbs() == ["GET", "POST"] and len({i["batch"] for i in items.values() if i["batch"]}) == 1
        body = gh.prs[7]["body"]
        for it in (tps, rc_, items["EEPART1"]):
            for name in ("after_footprint.png", "after_symbol.png"):
                path = f"agent-pictures/{b['id']}/{it['id']}/{name}"
                assert f"https://github.com/acme/lib/blob/{b['commit']}/{path}?raw=true" in body, (path, body)
                assert gitc(Path(d) / "remote.git", "cat-file", "-t", f"{b['commit']}:{path}") == "blob"
        assert "LED-RED-0603" not in body and "**Independent check:** pass (score 0.97)" in body and "not sure about this part: the model declined" in body
        assert gitc(ws, "rev-parse", "--abbrev-ref", "HEAD") == "main" and not gitc(ws, "status", "--porcelain", "--untracked-files=no")
        assert all(i["pr_url"] == "https://github.com/acme/lib/pull/7" for i in AQ.Queue(ws).list(status="review"))
        assert plan_status(ws)["TPS1234DSGR"] == "awaiting_approval"            # [release.approvals] holds it

        # 2. the web UI in team mode
        w = Workspace(ws, offline=True)
        with TT.server(w) as (_, team, port):
            vic, lib, apr, ap2 = (TT.Client(port) for _ in range(4))
            for c, u in ((vic, "vic"), (lib, "lib"), (apr, "apr"), (ap2, "ap2")):
                assert c.login(u)[0] == 200
            st, q = lib.call("GET", "/api/agent/queue?status=review")
            assert st == 200 and {x["mpn"] for x in q["items"]} == {"TPS1234DSGR", "EEPART1", "RC0603-10K"}, q
            assert q["counts"]["failed"] == 1 and q["batches"][0]["pr_url"].endswith("/pull/7")
            st, one = lib.call("GET", f"/api/agent/queue/item?id={tps['id']}")
            assert st == 200 and one["item"]["proof"]["verdict"] == "pass" and "approved" in one["allowed"]
            pics = one["pictures"]
            assert len(pics["after"]) == 2 and pics["ours"] and pics["datasheet"] and None not in sum(pics.values(), [])
            for p in pics["after"] + pics["ours"] + pics["datasheet"][:1]:
                st, raw = lib.call("GET", f"/api/agent/queue/picture?path={p}")
                assert st == 200 and raw[:4] == b"\x89PNG", (p, st)
            assert lib.call("GET", "/api/agent/queue/picture?path=ecadlib.toml")[0] == 400
            assert vic.call("POST", "/api/agent/queue/approve", {"id": tps["id"]})[0] == 403          # viewer: no
            assert lib.call("POST", "/api/agent/queue/approve", {"id": tps["id"]})[0] == 403          # librarian: no

            # apr asks for a rebuild, the agent rebuilds it (same PR, PATCH): apr may not approve their own build
            st, r = apr.call("POST", "/api/agent/queue/rebuild", {"id": tps["id"], "note": "check pin 1 again"})
            assert st == 200 and r["item"]["status"] == "building" and r["item"]["build"]["requested_by"] == "Apr Prover <apr@example.com>"
            rep2 = A.run_once(ws, now="2099-01-01T00:00:00Z")
            t2 = AQ.Queue(ws).get(tps["id"])
            assert rep2["built"] == [tps["id"]] and t2["status"] == "review" and t2["build"]["requested_by"] == "Apr Prover <apr@example.com>"
            assert gh.verbs()[2:] == ["GET", "PATCH"] and gh.calls[3][1].endswith("/pulls/7") and t2["proof"].get("cached") is True
            assert len(api.calls) == 4                                                            # same pictures: cached answer
            st, r = apr.call("POST", "/api/agent/queue/approve", {"id": tps["id"], "note": "ok"})
            assert st == 403 and "self-approval" in r["error"], r
            st, r = ap2.call("POST", "/api/agent/queue/approve", {"id": tps["id"], "note": "pinout checked"})
            assert st == 200 and r["item"]["status"] == "approved" and r["approval"]["recorded"], r
            assert r["item"]["approval"]["by"] == "Two Prover <ap2@example.com>"
        assert plan_status(ws)["TPS1234DSGR"] == "ready" and plan_status(ws)["EEPART1"] == "awaiting_approval"
        assert any(e.get("part") == "TPS1234DSGR" and e.get("result") == "recorded" and "team" in e.get("via", "")
                   for e in map(json.loads, next(ws.glob("librarian/*/approvals_audit.jsonl")).read_text().splitlines()))

        # 3. push: the dry run lists the approved part only; the real push moves it to pushed
        rc, out = cli_run("agent", "--workspace", str(ws), "push", "--dry-run")
        pr = json.loads(out)
        assert rc == 0 and pr["approved"] == [tps["id"]] and [x["action"] for x in pr["results"]] == ["release"], pr
        assert AQ.Queue(ws).get(tps["id"])["status"] == "approved"
        nx = Nexar()
        pr = A.push_approved(ws, a365=nx)
        assert pr["pushed"] == [tps["id"]] and AQ.Queue(ws).get(tps["id"])["status"] == "pushed" and len(nx.released) == 1, pr

        # the CLI approve (identity typed) goes through approvals.py too
        rc, out = cli_run("agent", "--workspace", str(ws), "approve", items["EEPART1"]["id"], "--user", "Ann Lee <ann@x.com>",
                          "--role", "approver")
        assert rc == 0 and "approved; sign-off: recorded" in out, out
        assert plan_status(ws)["EEPART1"] == "ready"

        # 4. nothing changed: nothing new, no API call, no PR call
        n_gh = len(gh.calls)
        rep3 = A.run_once(ws, now="2099-01-02T00:00:00Z")
        assert rep3["detected"] == [] and rep3["batches"] == [] and len(gh.calls) == n_gh and len(api.calls) == 4
        assert len(AQ.Queue(ws).list()) == 4

        # the BOM's footprint changes for one part -> "changed" -> rebuilt -> the same PR is updated, no new item
        (ws / "boms" / "main.csv").write_text(MAIN.replace(",R_0603_1608Metric,", ",Resistor_SMD:R_0603_1608Metric,"))
        rep4 = A.run_once(ws, now="2099-01-03T00:00:00Z")
        assert [(c["mpn"], c["kind"]) for c in rep4["detected"]] == [("RC0603-10K", "changed")], rep4["detected"]
        r4 = AQ.Queue(ws).get(rc_["id"])
        assert r4["status"] == "review" and r4["change"]["was"]["footprint"] == "R_0603_1608Metric" and rep4["built"] == [rc_["id"]]
        assert any("rebuild: changed" in h["note"] for h in r4["history"]) and r4["build"]["built"] == "2099-01-03T00:00:00Z"
        assert [Path(p).name for p in r4["pictures"]["before"]] == ["before_footprint.png", "before_symbol.png"]
        assert gh.verbs()[n_gh:] == ["GET", "PATCH"] and gh.calls[-1][1].endswith("/pulls/7") and len(gh.prs) == 1
        assert len(AQ.Queue(ws).list()) == 4 and api.calls[4:] == ["RC0603-10K"]               # refusals are asked again
        assert "before_footprint" in gh.prs[7]["body"]

        # 5. an upkeep finding becomes an upkeep item (real upkeep_findings / finding_item; upkeep.run itself faked)
        def fake_upkeep(plan, checks, state, out, **kw):
            return {"checks": [{"check": "lifecycle", "changes": [
                {"kind": "obsolete", "item": "TPS1234DSGR", "was": "active", "now": "obsolete", "detail": "EOL notice 2099"}]}]}
        with mock.patch.object(UK, "run", fake_upkeep):
            rep5 = A.run_once(ws, do_upkeep=True, now="2099-01-04T00:00:00Z")
        assert rep5["upkeep"]["findings"] == 1, rep5["upkeep"]
        up = [i for i in AQ.Queue(ws).list() if i["origin"] == "upkeep"]
        assert len(up) == 1 and up[0]["key"] == "acme|TPS1234DSGR" and up[0]["status"] == "review", up
        assert up[0]["change"]["kind"] == "obsolete" and up[0]["build"]["action"] == "none"
        assert up[0]["batch"] != tps["batch"] and gh.verbs()[-2:] == ["GET", "POST"] and "EOL notice 2099" in gh.prs[8]["body"]

        # 6. the MCP tools on the same workspace
        from ecadlib import mcp_server as M
        st = M.call_tool(w, "agent_status", {})
        assert st["queue"] == {"review": 2, "failed": 1, "pushed": 1, "approved": 1} and st["state"] == "idle", st["queue"]
        dry = M.call_tool(w, "agent_run", {})
        assert dry["dry_run"] is True and dry["detected"] == []
        scan = M.call_tool(w, "agent_watch_scan", {"dry_run": True})
        assert scan["changes"] == [] and {s["status"] for s in scan["sources"]} == {"ok"} and len(scan["sources"]) == 2
        lst = M.call_tool(w, "agent_queue_list", {"status": "failed"})
        assert [x["mpn"] for x in lst["items"]] == ["LED-RED-0603"] and lst["items"][0]["verdict"] == "fail"
        got = M.call_tool(w, "agent_queue_get", {"id": rc_["id"]})
        assert got["item"]["mpn"] == "RC0603-10K" and got["pictures"]["before"] and "approved" in got["allowed"]
        dec = M.call_tool(w, "agent_queue_decide", {"id": rc_["id"], "decision": "reject", "user": "Vic V <vic@x.com>",
                                                    "note": "pin 1 mark missing"})
        assert dec["item"]["status"] == "rejected" and dec["approval"]["recorded"] is True
        vc = M.call_tool(w, "visual_check", {"target": "TPS1234DSGR", "datasheet": "datasheets/tps.pdf", "render_only": True})
        assert vc["verdict"] == "skipped" and vc["images"]["ours"] == ["agent/proofs/TPS1234DSGR/footprint.png",
                                                                       "agent/proofs/TPS1234DSGR/symbol.png"], vc
        vc = M.call_tool(w, "visual_check", {"target": "TPS1234DSGR", "datasheet": "datasheets/tps.pdf"})
        assert vc["verdict"] == "pass" and api.calls[-1] == "TPS1234DSGR", vc   # the plan's footprint + symbol were found


def test_no_key_goes_to_review_with_pictures():
    with tempfile.TemporaryDirectory() as d, services(key=False) as (api, gh):
        ws = make_ws(d)
        (ws / "boms" / "main.csv").write_text(HEAD + MAIN.splitlines()[3] + "\n")             # TPS1234DSGR only
        rep = A.run_once(ws, now="2099-01-01T00:00:00Z")
        assert not api.calls and rep["review"] and not rep["failed"], rep
        it = next(i for i in AQ.Queue(ws).list() if i["mpn"] == "TPS1234DSGR")
        assert it["status"] == "review" and it["proof"]["verdict"] == "skipped" and "ANTHROPIC_API_KEY is not set" in it["proof"]["reason"]
        assert all((ws / p).is_file() for p in it["pictures"]["after"]) and len(it["pictures"]["after"]) == 2
        assert it["proof"]["images"]["ours"] and it["proof"]["images"]["datasheet"]
        body = gh.prs[7]["body"]
        assert "did not run: ANTHROPIC\\_API\\_KEY is not set" in body and "after_footprint.png?raw=true" in body   # part data is markdown-escaped
        assert "(claude-opus-5-5)" not in body                                                  # no model was asked


def test_bom_line_without_mpn_is_queued_and_built():
    """A bare passive (no MPN) in a watched BOM is keyed |VALUE FOOTPRINT; it used to crash the whole run."""
    with tempfile.TemporaryDirectory() as d, services(key=False):
        ws = make_ws(d)
        (ws / "boms" / "main.csv").write_text(HEAD + "R2,1,4k7,R_0603_1608Metric,,,,,\n")
        rep = A.run_once(ws, now="2099-01-01T00:00:00Z")
        it = AQ.Queue(ws).list()[0] if len(AQ.Queue(ws).list()) == 1 else next(i for i in AQ.Queue(ws).list() if i["mpn"].startswith("4k7"))
        assert it["key"] == "|4K7 R_0603_1608METRIC" and it["mpn"] == "4k7 R_0603_1608Metric", it
        assert it["status"] == "review" and it["build"]["part"] == "RES-0603-4k7" and it["id"] in rep["review"], it["history"]


def test_pr_body_links_the_datasheet_for_the_reviewer():
    it = {"id": "q1", "key": "acme|X1", "mpn": "X1", "manufacturer": "ACME", "origin": "watch",
          "change": {"kind": "new", "source": {"type": "bom_file", "ref": "b.csv", "name": "b.csv"},
                     "line": {"datasheet": "https://example.com/x1.pdf"}},
          "build": {}, "proof": {"verdict": "skipped", "reason": "no key", "model": "claude-opus-5-5"}, "pictures": {}}
    md = A.pr_body("b1", [it], lambda i, p: p, "t")
    assert "Datasheet: <https://example.com/x1.pdf>" in md and "(claude-opus-5-5)" not in md, md


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_agent_e2e"]))
