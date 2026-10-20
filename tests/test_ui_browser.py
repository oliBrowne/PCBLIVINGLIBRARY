"""The web UI as a first-time user meets it: plain-HTTP checks of what the page and the new endpoints promise, and
(when node, Playwright and Chromium are installed) a real Chromium driving the main flows at desktop and phone width:
load a BOM, triage, fill the library, held parts, Altium export / plan / dry run, status and the setup check. The browser part skips cleanly without them.
Run:  python3 -m tests.test_ui_browser      (ECADLIB_UI_SHOTS=DIR also saves screenshots)"""
import contextlib, io, json, os, shutil, subprocess, tempfile, threading
from pathlib import Path

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
    for sec in ("bomcard", "results", "libcard", "export", "fpcard", "statuscard", "setup"):
        assert f'id="{sec}"' in PAGE and f'href="#{sec}"' in PAGE or sec == "results", sec
    order = [PAGE.index(f'<section class="card" id="{s}"') for s in ("bomcard", "results", "libcard", "export")]
    assert order == sorted(order), "the steps are not in the order a user does them"
    assert '<link rel="icon" href="data:image/svg+xml,' in PAGE    # the mark inline: no /favicon.ico 404 in the console
    assert "--on-accent" in PAGE                                      # white on light blue in dark mode was ~2:1
    for el in ("tnext", "libbusy", "stout", "doctorout"):
        assert f'id="{el}"' in PAGE, el


def test_status_and_doctor_endpoints():
    with tempfile.TemporaryDirectory() as d:
        ws, bom = FX.build(d)
        srv, call = serve(ws)
        try:
            st, r = call("GET", "/api/status")
            assert st == 200 and r["totals"]["parts"] == 14 and "agent" not in r, r
            st, r = call("GET", "/api/doctor")
            assert st == 200 and any(c["check"] == "workspace" for c in r["checks"]), r
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


def test_parts_endpoint_says_why_with_both_values_and_cost():
    """/api/parts: every part as a card; held reasons in plain words with a next step; the cross-check and the
    KiCad / Altium comparison with both values; datasheet page; cost and model; a footprint picture."""
    with tempfile.TemporaryDirectory() as d:
        ws, bom = FX.build(d)
        srv, call = serve(ws)
        try:
            st, r = call("GET", "/api/parts?bom=" + bom)
            assert st == 200, r
            assert r["counts"] == {"parts": 14, "ready": 5, "held": 9, "waiting": 0, "cross_check_disagreed": 1,
                                   "parity_mismatch": 1, "changes_held": 0}, r["counts"]
            assert r["cost"]["usd"] == 0.111 and r["cost"]["models"] == ["claude-sonnet-4-5"] and r["cost"]["answers"] == 2
            by = {p["part"]: p for p in r["parts"]}
            tps = by["TPS62133RGTR"]
            assert tps["status_label"] == "held" and tps["reasons"][0]["code"] == "crosscheck.disagree", tps["reasons"]
            assert "pin pitch (0.65 mm vs 0.5 mm)" in tps["reasons"][0]["text"] and "page 34" in tps["reasons"][0]["fix"]
            rows = tps["cross_check"]["rows"]
            assert [(x["label"], x["first"], x["second"], x["page"]) for x in rows] == [
                ("pin pitch", "0.65 mm", "0.5 mm", 34), ("body width", "3 mm", "3.1 mm", 34)], rows
            assert tps["datasheet"] == {"url": "https://www.lcsc.com/datasheet/C73973.pdf", "page": 34}
            assert tps["cost"] == {"usd": 0.111, "models": ["claude-sonnet-4-5"], "answers": 2}
            stm = by["STM32F103C8T6"]
            assert stm["parity"]["result"] == "mismatch" and stm["parity"]["rows"] == [
                {"what": "pad 1 size", "kicad": "0.3 x 1.5 mm", "altium": "0.3 x 1.45 mm"}], stm["parity"]
            assert any("KiCad 0.3 x 1.5 mm, Altium 0.3 x 1.45 mm" in x["text"] for x in stm["reasons"]), stm["reasons"]
            assert stm["picture"].startswith("<svg") and stm["footprint"]["pads"] == 48
            for p in r["parts"]:                      # every held part says why and what to do, without machine words
                if p["status"] == "held":
                    assert p["reasons"] and all(x["text"] and x["fix"] for x in p["reasons"]), p
                    assert not any(w in x["text"] + x["fix"] for x in p["reasons"] for w in (".SchLib", ".PcbLib", "plan.json", "parity:", "files:")), p["reasons"]
            assert str(ws.root) not in json.dumps(r)
            from ecadlib import mcp_server as M
            got = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "library_parts", "arguments": {"bom": bom}}})
            mc = json.loads(got["result"]["content"][0]["text"])
            assert mc["counts"]["held"] == 9 and all(p["picture"] is None for p in mc["parts"]), mc["counts"]
            st, r = call("GET", "/api/parts?bom=uploads/nope.csv")
            assert st == 400 and str(ws.root) not in r["error"], r
        finally:
            srv.shutdown()


def test_jobs_stream_lines_and_stop():
    """Long jobs keep one line per part ("3/14 NAME  ready") and stop when asked; a stopped librarian run leaves the
    plan as it was."""
    from tests.test_ui import wait_job
    with tempfile.TemporaryDirectory() as d:
        ws, bom = FX.build(d)
        srv, call = serve(ws)
        try:
            st, j = call("POST", "/api/triage", {"bom": bom})
            res = wait_job(call, j["job"])
            assert res["state"] == "done" and len(res["lines"]) == 14 and all(x["state"] == "done" for x in res["lines"]), res["lines"]
            assert res["lines"][0]["text"].startswith("1/14 ") and res["elapsed"] >= 0, res["lines"][0]
            st, j = call("POST", "/api/librarian/run", {"bom": bom})
            res = wait_job(call, j["job"])
            assert res["state"] == "done", res
            texts = [x["text"] for x in res["lines"]]
            assert len(texts) == 14 and texts[0] == "1/14 CAP-0402-100nF  ready" and any(t.endswith("  held") for t in texts), texts
            plan = (ws.root / "librarian" / "sample_bom" / "plan.json").read_bytes()
            jobs = S.Jobs()
            gate = threading.Event()

            def slow(progress=None):
                for i in range(200):
                    progress(i, 200, f"line {i}")
                    progress.line(f"p{i}", text=f"{i}/200")
                    gate.wait(0.02)
                return "finished"
            jid = jobs.start(slow)["job"]
            assert jobs.cancel(jid)["cancel"] is True
            for _ in range(200):
                if jobs.get(jid)["state"] != "running":
                    break
                gate.wait(0.02)
            got = jobs.get(jid)
            assert got["state"] == "cancelled" and got["result"] is None and 0 < len(got["lines"]) < 200, got["state"]
            st, j = call("POST", "/api/librarian/run", {"bom": bom, "refresh": True})
            st, c = call("POST", "/api/job/cancel", {"id": j["job"]})
            assert st == 200, c
            res = wait_job(call, j["job"])
            assert res["state"] in ("cancelled", "done"), res
            if res["state"] == "cancelled":
                assert (ws.root / "librarian" / "sample_bom" / "plan.json").read_bytes() == plan
            assert call("POST", "/api/job/cancel", {"id": "nope"})[0] == 404
        finally:
            srv.shutdown()


def test_demo_builds_the_sample_offline():
    from tests.test_ui import wait_job
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [FX.LIB], providers=["digikey"], offline=True)
        srv, call = serve(ws)
        try:
            st, j = call("POST", "/api/demo", {})
            assert st == 200 and j["bom"] == "uploads/demo/demo_bom.csv", j
            res = wait_job(call, j["job"])
            assert res["state"] == "done", res
            r = res["result"]
            assert r["parts"] == 7 and r["ready"] + r["held"] == 7 and len(r["triage"]["rows"]) == 7, r
            assert any(x["text"] == "BOM checked: 7 lines" for x in res["lines"])
            assert not (ws.root / "ecadlib.toml").exists()           # the person's workspace keeps its own rules
            assert call("GET", "/api/parts?bom=" + j["bom"])[1]["counts"]["parts"] == 7
        finally:
            srv.shutdown()
    from unittest import mock
    from ecadlib import firstrun
    ws = Workspace.__new__(Workspace)                     # never touches the disk: the guard comes first
    ws.root = Path(Path.cwd().anchor)
    for fn in (ws.demo_bom, lambda: firstrun.build_demo(ws, log=lambda *a: None)):
        with mock.patch.object(Path, "mkdir", side_effect=AssertionError("wrote at /")), \
                mock.patch("shutil.copyfile", side_effect=AssertionError("wrote at /")):
            try:
                fn()
                raise AssertionError("demo ran at the filesystem root")
            except ValueError as e:
                assert "filesystem root" in str(e), e


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


def planned_workspace(root):
    """What `ecadlib demo` leaves: a librarian plan for uploads/demo/demo_bom.csv and no BOM check (triage history)."""
    from ecadlib import firstrun as FR
    ws = Workspace(root, [FX.LIB], providers=["digikey"], offline=True)
    (ws.root / "uploads" / "demo").mkdir(parents=True, exist_ok=True)
    (ws.root / "uploads" / "demo" / "demo_bom.csv").write_bytes(FR.DEMO_BOM.read_bytes())
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ws.librarian_run("uploads/demo/demo_bom.csv", sources=["kicad", "standard"])
    return ws


@contextlib.contextmanager
def _servers(d):
    srvs, urls = [], {"local": {}}

    def up(ws):
        srv = S.make_server(ws, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        srvs.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        for w in (1280, 390):
            urls["local"][str(w)] = up(FX.build(Path(d) / f"local{w}")[0])
        urls["empty"] = up(Workspace(Path(d) / "empty", [FX.LIB], providers=["digikey"], offline=True))
        urls["demo"] = {str(w): up(Workspace(Path(d) / f"demo{w}", [FX.LIB], providers=["digikey"], offline=True)) for w in (1280, 390)}
        urls["planned"] = up(planned_workspace(Path(d) / "planned"))
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
        cfg = {**urls, "bom": str(ROOT / "examples" / "sample_bom.csv"), "executable": exe,
               "shots": os.environ.get("ECADLIB_UI_SHOTS")}
        p = subprocess.run([node, str(ROOT / "tests" / "ui_browser.js"), json.dumps(cfg)], capture_output=True, text=True, cwd=d,
                           env={**os.environ, "NODE_PATH": mods}, timeout=900)
        assert p.returncode == 0, p.stderr[-3000:]
        checks = json.loads(p.stdout.strip().splitlines()[-1])["checks"]
    bad = [c for c in checks if not c[1]]
    assert len(checks) >= 100 and not bad, (len(checks), bad)
    print(f"      {len(checks)} browser checks passed")


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok ", k)
