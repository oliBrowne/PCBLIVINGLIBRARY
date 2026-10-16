"""The web UI as a first-time user meets it: plain-HTTP checks of what the page and the new endpoints promise, and
(when node, Playwright and Chromium are installed) a real Chromium driving the main flows at desktop and phone width:
load a BOM, triage, fill the library, held parts, Altium export / plan / dry run, the review queue, status, setup check,
sign-in and roles. The browser part skips cleanly without them.
Run:  python3 -m tests.test_ui_browser      (ECADLIB_UI_SHOTS=DIR also saves screenshots)"""
import contextlib, json, os, shutil, subprocess, tempfile, threading
from pathlib import Path

from ecadlib import team as TM
from ecadlib.app import Workspace
from ecadlib.ui import server as S
from tests import ui_fixture as FX
from tests.test_ui import serve, wait_job

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "ecadlib" / "ui" / "static" / "index.html").read_text(encoding="utf-8")


def _skip(what):
    print(f"      ({what} not installed: skipped)")


# ------------------------------------------------------------------ plain HTTP (no browser)
def test_page_has_steps_next_hints_and_no_favicon_404():
    for sec in ("bomcard", "results", "libcard", "export", "queuecard", "fpcard", "statuscard", "setup"):
        assert f'id="{sec}"' in PAGE and f'href="#{sec}"' in PAGE or sec == "results", sec
    order = [PAGE.index(f'<section class="card" id="{s}"') for s in ("bomcard", "results", "libcard", "export", "queuecard")]
    assert order == sorted(order), "the steps are not in the order a user does them"
    assert '<link rel="icon" href="data:,">' in PAGE                  # no /favicon.ico 404 in the console
    assert "--on-accent" in PAGE                                      # white on light blue in dark mode was ~2:1
    for el in ("tnext", "libbusy", "rolenote", "stout", "doctorout"):
        assert f'id="{el}"' in PAGE, el


def test_status_and_doctor_endpoints_local_and_team_roles():
    with tempfile.TemporaryDirectory() as d:
        ws, bom = FX.build(d, team=True)
        srv, call = serve(ws)
        try:
            st, r = call("GET", "/api/status")
            assert st == 200 and r["totals"]["parts"] == 14 and r["agent"]["waiting_review"] == 2, r
            st, r = call("GET", "/api/doctor")
            assert st == 200 and any(c["check"] == "workspace" for c in r["checks"]), r
        finally:
            srv.shutdown()
        assert S.ROUTES[("GET", "/api/status")] == "viewer" and S.ROUTES[("GET", "/api/doctor")] == "admin"
        team = TM.Team(ws.root)
        srv = S.make_server(ws, "127.0.0.1", 0, team)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            import http.client
            port = srv.server_address[1]

            def req(method, path, body=None, cookie=None, csrf=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                h = {"Content-Type": "application/json"}
                if cookie:
                    h["Cookie"] = cookie
                if csrf:
                    h["X-CSRF-Token"] = csrf
                c.request(method, path, json.dumps(body) if body is not None else None, h)
                r = c.getresponse()
                return r.status, json.loads(r.read() or b"{}"), r.getheader("Set-Cookie")
            assert req("GET", "/api/status")[0] == 401
            st, s, ck = req("POST", "/api/login", {"username": "vic", "password": FX.PW})
            ck = ck.split(";")[0]
            assert st == 200 and req("GET", "/api/status", cookie=ck)[0] == 200
            assert req("GET", "/api/doctor", cookie=ck)[0] == 403          # tool paths and key names: admins only
            st, s, ck = req("POST", "/api/login", {"username": "ada", "password": FX.PW})
            assert req("GET", "/api/doctor", cookie=ck.split(";")[0])[0] == 200
        finally:
            srv.shutdown()


def test_errors_are_plain_lines_without_server_paths():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [FX.LIB], providers=["digikey"], offline=True)
        srv, call = serve(ws)
        try:
            st, j = call("POST", "/api/upload", b"hello world\n", {"X-Filename": "notes.csv"})
            res = wait_job(call, j["job"])
            assert res["state"] == "error", res
            e = res["error"]
            assert e.startswith("couldn't read notes.csv as a BOM: ") and "ValueError" not in e and str(ws.root) not in e, e
            assert "Designator" in e, e                                   # says which columns it needs
            # any message naming a workspace file comes back relative to the workspace
            st, r = call("POST", "/api/release-check", {"bom": "uploads/none.csv"})
            assert st == 400 and str(ws.root) not in r["error"], r
        finally:
            srv.shutdown()
    assert S._msg(ValueError(f"{d}/uploads/x.csv: bad"), Path(d)) == "uploads/x.csv: bad"


def test_queue_approval_refusal_reads_plainly():
    with tempfile.TemporaryDirectory() as d:
        ws, bom = FX.build(d)
        from ecadlib import agent_queue as AQ
        it = next(i for i in AQ.Queue(ws.root).list() if i["mpn"] == "CH340C")
        AQ.Queue(ws.root).update(it["id"], build={**it["build"], "part": "NOT-IN-PLAN"})
        try:
            ws.agent_queue_decide(it["id"], "approve", "Ann Lee")
            raise AssertionError("approved a part missing from the plan")
        except ValueError as e:
            assert str(e).startswith("the library plan refused this approval") and "approvals.py" not in str(e), e


# ------------------------------------------------------------------ real Chromium
def _browser():
    """(node, NODE_PATH, chromium executable or None) or None when any piece is missing."""
    node = shutil.which("node")
    if not node:
        return None
    paths = [p for p in (os.environ.get("NODE_PATH"), "/opt/node22/lib/node_modules", "/usr/lib/node_modules",
                         "/usr/local/lib/node_modules") if p]
    mods = next((p for p in paths if (Path(p) / "playwright").is_dir()), None)
    if not mods:
        return None
    exe = os.environ.get("ECADLIB_CHROMIUM") or next((p for p in ("/opt/pw-browsers/chromium",) if Path(p).exists()), None)
    probe = subprocess.run([node, "-e", "const {chromium}=require('playwright');chromium.launch(%s).then(b=>b.close())"
                            % json.dumps({"executablePath": exe} if exe else {})],
                           env={**os.environ, "NODE_PATH": mods}, capture_output=True, timeout=120)
    return (node, mods, exe) if probe.returncode == 0 else None


@contextlib.contextmanager
def _servers(d):
    srvs, urls = [], {"local": {}}

    def up(ws, team=None):
        srv = S.make_server(ws, "127.0.0.1", 0, team)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        srvs.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        for w in (1280, 390):
            urls["local"][str(w)] = up(FX.build(Path(d) / f"local{w}")[0])
        urls["empty"] = up(Workspace(Path(d) / "empty", [FX.LIB], providers=["digikey"], offline=True))
        ws, _ = FX.build(Path(d) / "team", team=True)
        urls["team"] = up(ws, TM.Team(ws.root))
        yield urls
    finally:
        for s in srvs:
            s.shutdown()


def test_browser_main_flows():
    b = _browser()
    if not b:
        return _skip("node + Playwright + Chromium")
    node, mods, exe = b
    with tempfile.TemporaryDirectory() as d, _servers(d) as urls:
        cfg = {**urls, "bom": str(ROOT / "examples" / "sample_bom.csv"), "password": FX.PW, "executable": exe,
               "shots": os.environ.get("ECADLIB_UI_SHOTS")}
        p = subprocess.run([node, str(ROOT / "tests" / "ui_browser.js"), json.dumps(cfg)], capture_output=True, text=True,
                           env={**os.environ, "NODE_PATH": mods}, timeout=900)
        assert p.returncode == 0, p.stderr[-3000:]
        checks = json.loads(p.stdout.strip().splitlines()[-1])["checks"]
    bad = [c for c in checks if not c[1]]
    assert len(checks) >= 60 and not bad, (len(checks), bad)
    print(f"      {len(checks)} browser checks passed")


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok ", k)
