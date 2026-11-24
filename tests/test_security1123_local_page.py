"""Security review 2026-11-23, group S-A: the local web page.
2.1  the page obeyed any program on the computer (no key): a program without this run's key is now refused;
2.7  a crafted triage report (or plan) ran script in the page: every value from a file is escaped, and the page's
     Content-Security-Policy runs only the page's own script.
Run:  python3 -m tests.test_security1123_local_page"""
import contextlib, http.client, io, json, re, shutil, socket, subprocess, tempfile, threading, time
from pathlib import Path

from ecadlib import altium_menu as AM, easy_setup as ES, launcher as LA, userconfig as UC
from ecadlib.app import Workspace
from ecadlib.ui import access as AC, server as S
from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parent.parent
FAKE = "sk-ant-api03-VICTIMfakeFAKEfake0123456789abcdef"            # not a real key
THEIRS = "sk-ant-api03-ATTACKERfakeFAKEfake00000000000000"


def _call(port, method, path, body=None, cookie=None, headers=None):
    """One request, no redirects followed. -> (status, JSON or bytes, response headers)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Content-Type": "application/json"}
    if cookie is not None:                                   # the key the page's script (or this user's program) adds
        h[AC.HEADER] = cookie
    h.update(headers or {})
    c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw), r.headers
    except ValueError:
        return r.status, raw, r.headers


@contextlib.contextmanager
def _app(ws):
    """The desktop app as `ecadlib start` runs it (the review's repro w1_local_page_no_origin.py)."""
    app = LA.App(ws, 0)
    app.start()
    ES.RESTART = app.restart
    try:
        yield app
    finally:
        ES.RESTART = None
        app.stop()


def _enter(port, link):
    """Open a launcher's link as a browser does. -> the session key the page now sends, or None when refused. The
    answer sets no cookie: the key goes into the tab's own storage (second review, I1)."""
    st, page, h = _call(port, "GET", "/" + link.split("/", 3)[3].split("#")[0])
    if st != 200:
        return None
    assert "Set-Cookie" not in h and "no-store" in h["Cache-Control"], dict(h)
    m = re.search(rb'sessionStorage\.setItem\("ecadlib_key", "([A-Za-z0-9_-]{30,})"\);[^\n]*\nlocation\.replace\("(/[^"]*)" \+ location\.hash\)', page)
    assert m and b"key=" not in m.group(2), page
    return m.group(1).decode()


# ------------------------------------------------------------------ 2.1
@lite
def test_a_program_without_the_key_is_refused(tmp_path):
    """The review's repro: no Origin header, no key. Nothing is read, changed, started or moved."""
    UC.set_key("ANTHROPIC_API_KEY", FAKE)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "private_notes.txt").write_text("private\n")
    with _app(tmp_path / "ws") as app:
        port = app.port
        (Path(app.workspace) / "uploads").mkdir(exist_ok=True)
        (Path(app.workspace) / "uploads" / "x.csv").write_text("Designator,MPN\nR1,X\n")
        for cookie in (None, "guess", "", "A" * 43):
            for method, path, body in (
                    ("POST", "/api/setup/crash-reports", {"on": True}),
                    ("POST", "/api/setup/key", {"name": "ANTHROPIC_API_KEY", "value": THEIRS}),
                    ("POST", "/api/setup/key/forget", {"name": "ANTHROPIC_API_KEY"}),
                    ("POST", "/api/ai/read", {"bom": "uploads/x.csv", "approved_usd": 50, "over_limit_ok": True, "price_unknown_ok": True}),
                    ("POST", "/api/setup/restart", {"workspace": str(tmp_path / "docs")}),
                    ("POST", "/api/setup", {"provider": "none", "tool": "kicad", "workspace": str(tmp_path / "docs")}),
                    ("POST", "/api/upload", None), ("POST", "/api/access/ticket", {}),
                    ("GET", "/api/setup", None), ("GET", "/api/info", None), ("GET", "/api/status", None),
                    ("GET", "/api/file?path=uploads/x.csv", None), ("GET", "/api/triage/history", None),
                    ("GET", "/api/job?id=1", None)):
                st, j, _ = _call(port, method, path, body, cookie)
                assert st == 401 and "error" in j, (method, path, st, j)
                assert "workspace" not in j and "config_dir" not in j, j           # not even where things are
        assert UC.get_key("ANTHROPIC_API_KEY") == FAKE                               # not swapped, not forgotten
        assert not ES.crash_reports_state()["on"] and not app.srv.jobs.jobs         # not switched on, nothing started
        time.sleep(0.6)
        assert app.workspace == str(tmp_path / "ws")                                 # not moved
        for path in ("/", "/index.html", "/setup"):        # the pages are ecadlib's own files: they hold nothing of
            st, page, h = _call(port, "GET", path)         # this run, and say how to open them when /api refuses
            assert st == 200 and b"ecadlib start" in page and "text/html" in h["Content-Type"], (path, st)
            assert app.access.key.encode() not in page and str(tmp_path).encode() not in page and "Set-Cookie" not in h
        st, page, _ = _call(port, "GET", "/?key=wrong")                              # a link that is no link
        assert st == 401 and b"ecadlib start" in page and app.access.key.encode() not in page


@lite
def test_the_launchers_link_lets_the_page_in_once(tmp_path):
    with _app(tmp_path / "ws") as app:
        port, link = app.port, app.link("setup")
        assert link.startswith(f"http://127.0.0.1:{port}/setup?key=") and app.access.key not in link
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cookie = _enter(port, link)
            assert _enter(port, f"http://127.0.0.1:{port}/?x=1&key={app.access.key}")   # the terminal's link
        assert link.split("key=")[1] not in err.getvalue() and app.access.key not in err.getvalue(), err.getvalue()
        assert "key=..." in err.getvalue()                                           # the request log names no key
        assert cookie and app.access.key != cookie
        for path in ("/", "/setup", "/api/info", "/api/setup", "/hairline.js"):      # the page, a reload, its calls
            assert _call(port, "GET", path, cookie=cookie)[0] == 200, path
        st, j, _ = _call(port, "POST", "/api/setup/crash-reports", {"on": False}, cookie)
        assert st == 200 and j["on"] is False
        st, j, _ = _call(port, "POST", "/api/access/ticket", {}, cookie)           # a page cannot hand itself on
        assert st == 403 and "ticket" not in j
        # the key as a cookie is no key (another local server gets every 127.0.0.1 cookie): second review, I1
        for name in ("ecadlib_key", f"ecadlib_key_{port}"):
            assert _call(port, "GET", "/api/info", headers={"Cookie": f"{name}={cookie}"})[0] == 401
            assert _call(port, "GET", "/api/info", headers={"Cookie": f"{name}={app.access.key}"})[0] == 401
        # the same link from a second program (it read the browser's command line): refused, and the session of
        # whoever came first is closed, so a thief that was quicker than the browser keeps nothing
        assert _enter(port, link) is None
        assert _call(port, "GET", "/api/info", cookie=cookie)[0] == 401
        cookie2 = _enter(port, app.link())
        assert cookie2 and _call(port, "GET", "/api/info", cookie=cookie2)[0] == 200
        for bad in ("", "x", app.link().split("key=")[1][:-1]):
            assert _enter(port, f"http://127.0.0.1:{port}/?key={bad}") is None
        old = app.access.ticket()                                                    # a link nobody opened in time
        app.access.tickets[old][0] = time.monotonic() - 1
        assert _enter(port, f"http://127.0.0.1:{port}/?key={old}") is None


@lite
def test_website_checks_are_kept_for_a_page_that_has_the_key(tmp_path):
    with _app(tmp_path / "ws") as app:
        port = app.port
        cookie = _enter(port, app.link())
        st, j, _ = _call(port, "POST", "/api/setup/crash-reports", {"on": True}, cookie, {"Origin": "https://evil.example"})
        assert st == 403 and "cross-origin" in j["error"] and not ES.crash_reports_state()["on"]
        assert _call(port, "GET", "/api/info", None, cookie, {"Host": "evil.example"})[0] == 403
        assert _call(port, "POST", "/api/setup/crash-reports", {"on": False}, cookie,
                     {"Origin": f"http://127.0.0.1:{port}"})[0] == 200


@lite
def test_the_same_persons_programs_find_the_key_and_nobody_else(tmp_path, monkeypatch):
    """A second `ecadlib start`, the Altium / KiCad menu: they read the run file in the settings folder."""
    with _app(tmp_path / "ws") as app:
        port, home = app.port, UC.config_dir()
        f = AC.run_file(port)
        assert f.is_file() and json.loads(f.read_text())["key"] == app.access.key
        if hasattr(f.stat(), "st_mode") and UC.os.name != "nt":
            assert f.stat().st_mode & 0o077 == 0, oct(f.stat().st_mode)             # this user only
        assert AM.probe_ui(port) == ("ecadlib", str((tmp_path / "ws").resolve()))
        assert LA.free_port(port) == (port, str((tmp_path / "ws").resolve()))        # second start: reuse it
        link = AC.link(port, "#libcard")
        assert "key=" in link and link.endswith("#libcard") and app.access.key not in link
        assert _call(port, "GET", "/api/info", cookie=_enter(port, link))[0] == 200
        monkeypatch.setenv("ECADLIB_HOME", tmp_path / "another-account")             # no run file: another account
        assert AM.probe_ui(port) == ("ecadlib-team", None)
        assert AC.link(port, "setup") == f"http://127.0.0.1:{port}/setup"
        assert LA.free_port(port)[0] != port                                         # it takes its own port
        cfg = {"port": port, "workspace": tmp_path / "ws"}
        r = AM.open_queue(cfg, opener=lambda u: (_ for _ in ()).throw(AssertionError(u)))
        assert r["status"] == "error" and str(port) in r["message"] and r["fix"], r
        monkeypatch.setenv("ECADLIB_HOME", home)
    assert not f.exists()                                                            # stopped: the run file is gone


@lite
def test_moving_the_workspace_keeps_the_open_page_working(tmp_path):
    with _app(tmp_path / "one") as app:
        port = app.port
        cookie = _enter(port, app.link())
        st, j, _ = _call(port, "POST", "/api/setup", {"provider": "none", "tool": "kicad", "workspace": str(tmp_path / "two")}, cookie)
        assert st == 200 and j["restart"], j
        assert _call(port, "POST", "/api/setup/restart", {"workspace": j["workspace"]}, cookie)[0] == 200
        for _ in range(80):
            time.sleep(0.1)
            with contextlib.suppress(OSError):
                st, j, _ = _call(port, "GET", "/api/setup", cookie=cookie)
                if st == 200 and j["workspace"] == str((tmp_path / "two").resolve()):
                    break
        else:
            raise AssertionError("the page lost the server after the move")
        assert AM.probe_ui(port) == ("ecadlib", str((tmp_path / "two").resolve()))


@lite
def test_the_workspace_is_never_the_settings_folder(tmp_path, monkeypatch):
    """Everything under the workspace can be downloaded through the page: the folder with the key file, or one
    that holds it, is no workspace, and its files are not served."""
    cfg = tmp_path / "home" / "settings"
    monkeypatch.setenv("ECADLIB_HOME", cfg)
    UC.set_key("ANTHROPIC_API_KEY", FAKE)
    (tmp_path / "ws").mkdir()
    moved = []
    monkeypatch.setattr(ES, "RESTART", moved.append)
    for bad in (cfg, cfg / "run", tmp_path / "home", tmp_path):
        Path(bad).mkdir(parents=True, exist_ok=True)
        for fn, args in ((ES.restart, (str(bad),)),
                         (ES.save, (tmp_path / "ws", {"provider": "none", "tool": "kicad", "workspace": str(bad)}))):
            try:
                fn(*args)
                raise AssertionError(f"{fn.__name__} took {bad}")
            except ValueError as e:
                assert "settings folder" in str(e), e
        assert not (Path(bad) / "ecadlib.toml").exists()
    assert moved == [] and UC.load_settings().get("workspace") is None
    assert ES.restart(str(tmp_path / "ws"))["restarting"] and moved == [str(tmp_path / "ws")]
    # a server already running above the settings folder (`ecadlib ui --workspace ~`): the key file is not served
    srv = S.make_server(Workspace(tmp_path / "home"), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        cookie = srv.access.key
        (tmp_path / "home" / "notes.txt").write_bytes(b"a workspace file\n")   # (bytes: no \r\n on Windows)
        st, body, h = _call(port, "GET", "/api/file?path=notes.txt", cookie=cookie)
        assert body == b"a workspace file\n" and h["X-Content-Type-Options"] == "nosniff", dict(h)
        assert h["Content-Security-Policy"].startswith("sandbox;") and "attachment" in h["Content-Disposition"]   # M1
        for path in ("/api/file?path=settings/keys.json", "/api/file?path=settings/../settings/keys.json"):
            st, j, _ = _call(port, "GET", path, cookie=cookie)
            assert st in (403, 404) and b"plain:" not in json.dumps(j).encode(), (path, st, j)
        st, j, _ = _call(port, "POST", "/api/triage", {"bom": "settings/keys.json"}, cookie)
        assert st in (400, 403) and "job" not in j, j
    finally:
        srv.shutdown()
        srv.server_close()


@lite
def test_ecadlib_ui_prints_the_key_only_for_a_person_at_a_terminal(tmp_path):
    key = "K" * 43
    plain = S.start_text("http://127.0.0.1:8765/", "setup", tmp_path, key, browser=True, tty=True)
    assert key not in plain and "http://127.0.0.1:8765/setup" in plain
    log = S.start_text("http://127.0.0.1:8765/", "", tmp_path, key, browser=False, tty=False)   # the menu's log file
    assert key not in log and "ecadlib start" in log
    term = S.start_text("http://127.0.0.1:8765/", "", tmp_path, key, browser=False, tty=True)
    assert f"http://127.0.0.1:8765/?key={key}" in term


def test_the_menus_start_the_real_web_ui_and_open_it_with_a_key():
    """Open review queue (Altium and KiCad): `ecadlib ui --no-browser` is started, its log holds no key, and the
    browser is handed a link that lets it in."""
    d = Path(tempfile.mkdtemp(prefix="ecadlib-sec-"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg, procs, opened = {"port": port, "workspace": d / "ws"}, [], []
    try:
        r = AM.open_queue(cfg, opener=opened.append, launcher=lambda ws, p: procs.append(AM.start_ui(ws, p)))
        assert r["status"] == "ok" and r["started"] == "1", r
        key = AC.key_for(port)
        assert key and "key=" in opened[0] and key not in opened[0] and opened[0].endswith("#libcard")
        assert "key=" not in r["url"] and "key=" not in r["message"]                 # the dialog and job files: no key
        cookie = _enter(port, opened[0])
        st, page, _ = _call(port, "GET", "/", cookie=cookie)
        assert st == 200 and b'id="libcard"' in page
        assert _call(port, "GET", "/api/info")[0] == 401
        r = AM.open_queue(cfg, opener=opened.append, launcher=None)                  # again: found, not restarted
        assert r["status"] == "ok" and r["started"] == "0" and _enter(port, opened[1])
        assert key not in (d / "ws" / "altium_menu_ui.log").read_text(errors="replace")
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)
        shutil.rmtree(d, ignore_errors=True)


def test_access_compares_in_constant_time_and_forgets_old_sessions():
    a = AC.Access()
    assert a.allowed(a.key) and not a.allowed(a.key[:-1]) and not a.allowed("") and not a.allowed(None)
    assert not a.allowed("é" * 10) and a.enter("é") is None                          # non-ASCII: refused, no crash
    s = a.enter(a.key)                                                               # the terminal's link: many times
    assert s and s != a.key and a.allowed(s) and a.enter(a.key) != s
    t = a.ticket()
    s1 = a.enter(t)
    assert s1 and a.allowed(s1) and a.enter(t) is None and not a.allowed(s1)
    for _ in range(AC.MAX_SESSIONS + 5):
        a.enter(a.key)
    assert len(a.sessions) == AC.MAX_SESSIONS and not a.allowed(s)
    src = (ROOT / "ecadlib" / "ui" / "access.py").read_text()
    assert "compare_digest" in src and " == self.key" not in src and "in self.sessions" not in src.replace("for s in self.sessions", "")


# ------------------------------------------------------------------ 2.7
PAYLOAD = "\"'><img src=x onerror=PWNED>"
PAGES = ("index.html", "setup.html")


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def _page_cases(d):
    """What the server really answers for a small workspace (tests/ui_fixture: some parts ready, some held), plus
    hand-made answers in the page's shapes for the routes that need a network or a second library."""
    from tests import ui_fixture as FX
    ws, bom = FX.build(d)
    hist = ws.triage_history()
    parts = ws.library_parts(bom)
    held = next(p["part"] for p in parts["parts"] if p["status"] == "held")
    spec = json.loads((ROOT / "ecadlib" / "package_specs" / "R_0603_1608Metric.json").read_text())
    P = PAYLOAD
    cases = {
        "info": ws.info(), "triage": ws.triage_result(hist["triages"][0]["id"]), "history": hist, "parts": parts,
        "part_history": _quiet(ws.part_history, bom, held), "status": ws.status(), "doctor": _quiet(ws.doctor),
        "specs": {P: {}}, "approvals": _quiet(ws.approval_status, bom), "plan": _quiet(ws.altium365_plan, bom),
        "dupes": _quiet(ws.find_duplicates, bom), "dblib": _quiet(ws.export_dblib, bom),
        "models": _quiet(ws.build_altium_models, bom), "preview": ws.preview_footprint(spec), "style": ws.house_style(),
        "librarian_summary": {"ready": 3, "parts": 9, "held": 2, "awaiting_approval": 1, "reports": ["reports/a.md"]},
        "kicad": {"written": 3, "folder": "kicad", "library": "ecadlib", "held": ["A"], "errors": ["e"],
                  "install": {"versions": {"8.0": "installed", "9.0": "no settings"}, "nick": "ecadlib", "var": "ECADLIB",
                              "warnings": ["w"]}},
        "workspaces": {"configured": "https://a.365.altium.com/", "rules": "ecadlib.toml",
                       "workspaces": [{"url": "https://b.365.altium.com/", "name": "B", "configured": False}]},
        "push": {"report": "reports/push.json", "results": [{"action": "release", "name": "A", "detail": "d"},
                                                             {"action": "error", "name": "B", "detail": "d"}]},
        "candidates": {"item": {"status": "held", "description": "d", "folder": "f", "symbol": {"source": "s", "pins": {"length": P}},
                                "footprint": {"source": "s", "pads": ["1", "2"]}, "hold_reasons": ["h"],
                                "pins_vs_pads": {"match": False, "pins_without_pad": ["1"], "pads_without_pin": ["2"]}},
                       "candidates": {"symbol": [{"id": "Lib:Sym", "current": True, "score": 0.9, "why": ["a", "b"], "error": "e",
                                                  "count": 8, "match": False, "pins_without_pad": ["1"], "pads_without_pin": ["2"],
                                                  "pin_map": {"1": "A"}}],
                                      "footprint": [{"id": "Lib:Fp", "score": 1, "why": [], "count": None, "match": True,
                                                     "pins_without_pad": [], "pads_without_pin": [], "pin_map": None}],
                                      P: []}},
        "rules": {"questions": [{"key": "naming.case", "text": "t", "choices": ["upper", "lower"]}], "scanned": {"SchLib": 2, P: 1},
                  "settings": [{"key": "naming.case", "value": "upper", "level": "high", "confidence": 0.9, "source": "s", "evidence": ["e"]},
                               {"key": "folders.x", "value": ["a"], "level": "low", "confidence": 0.2, "source": "s", "evidence": ["e"]}],
                  "components": 4, "footprints": 5, "boms": 1, "warnings": ["w"], "errors": ["e"], "toml": "x = 1", "diff": "+x",
                  "file": "ecadlib.toml"},
        "rules_save": {"needs_confirm": True, "path": "ecadlib.toml", "diff": "+x"},
        "ai": {"text": "t", "key_set": True, "price_known": True, "usd": 0.02, "usd_max": 0.2, "approve_usd": 0.2, "over_limit": False,
               "model": "m", "limit_usd": 1, "limit_name": "max_cost_usd"},
        "ai_cost": {"text": "t"},
        "lines": {"lines": [{"key": "k", "n": 1, "total": 2, "name": "n", "state": "working", "text": "t"}]},
        "errors": {"error": "e"},
    }
    return cases


def _node():
    return shutil.which("node")


def test_no_value_from_a_file_reaches_the_page_as_markup():
    """The review's repro (w2_triage_report_xss.py) and the rest of the page: the page's own script runs in node
    against a stand-in DOM; every renderer gets the server's answer with one value at a time replaced by markup
    (a line number, a count, a part name, ...), and none of it may reach innerHTML unescaped."""
    node = _node()
    if not node:
        print("      (node not installed: skipped)")
        return
    with tempfile.TemporaryDirectory() as d:
        cases = _page_cases(Path(d) / "ws")
        # the review's own report file, as the server returns it
        evil = {"id": "shared", "name": "BOM.csv", "bom": None, "finished": "2026-11-20T10:00:00+00:00", "user": None,
                "mapping": {}, "reports": ["reports/a.md", "reports/a.csv"], "counts": {"in_library": PAYLOAD},
                "rows": [{"line": PAYLOAD, "designator": "R1", "mpn": "X", "status": "in_library"}]}
        ws = Workspace(Path(d) / "ws2")
        (ws.root / "reports" / "triage_shared.json").write_text(json.dumps(evil))
        got = ws.triage_result("shared")
        assert got["rows"][0]["line"] == PAYLOAD                 # (the server hands the file on as it is)
        base = {k: cases[k] for k in ("info", "plan", "part_history")}      # (what every renderer starts from)
        f, runs = Path(d) / "cases.json", []
        for c in (cases, {**base, "triage": got}):
            f.write_text(json.dumps({"page": str(S.STATIC / "index.html"), "payload": PAYLOAD, "raw_keys": ["picture", "svg"],
                                     "cases": c}), encoding="utf-8")
            p = subprocess.run([node, str(ROOT / "tests" / "sec1123_page_render.js"), str(f)], capture_output=True, text=True, timeout=600)
            assert p.returncode == 0, p.stderr[-3000:]
            runs.append(json.loads(p.stdout.strip().splitlines()[-1]))
    for r in runs:
        assert not r["leaks"], (len(r["leaks"]), r["leaks"][:12])
    r = runs[0]
    assert not r["missing"] and len(r["renderers"]) >= 25, r["missing"]
    assert r["leaves"] > 1500 and r["thrown"] < r["leaves"] // 4, (r["leaves"], r["thrown"])
    print(f"      {r['leaves']} values tried in {len(r['renderers'])} renderers ({r['thrown']} stopped the renderer)")


def test_the_pages_run_only_their_own_script():
    """Defence in depth for 2.7: the pages are sent with a Content-Security-Policy that allows only their own
    <script> blocks (a nonce, new for each response), so an inline handler in markup that slipped through does not
    run. For that the pages themselves must have no inline handler."""
    for name in PAGES:
        page = (S.STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", page), (name, re.search(r"<[^>]*\son[a-z]+\s*=", page).group(0))
        assert "javascript:" not in page and "eval(" not in page and "new Function" not in page, name
    with tempfile.TemporaryDirectory() as d:
        srv = S.make_server(Workspace(Path(d) / "ws"), "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_address[1]
            cookie = srv.access.key
            seen = set()
            with contextlib.redirect_stderr(io.StringIO()):
                for path in ("/", "/setup", "/"):
                    st, page, h = _call(port, "GET", path, cookie=cookie)
                    csp = h.get_all("Content-Security-Policy")
                    assert st == 200 and len(csp) == 1, (path, csp)
                    m = re.search(r"script-src 'nonce-([A-Za-z0-9_-]{16,})';", csp[0])     # not 'self': /api/file is this address too
                    assert m and "unsafe-inline" not in csp[0] and "frame-ancestors 'none'" in csp[0], csp
                    tags = re.findall(rb"<script[^>]*>", page)
                    assert tags and all(f'nonce="{m.group(1)}"'.encode() in t for t in tags), tags
                    seen.add(m.group(1))
                st, _, h = _call(port, "GET", "/api/info", cookie=cookie)
                assert st == 200 and h["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
            assert len(seen) == 3                                    # a new nonce for every response
        finally:
            srv.shutdown()
            srv.server_close()


def test_the_pages_work_under_that_policy_in_chromium():
    """A real Chromium (skipped without node + Playwright): the page opened by a launcher's link runs its script
    and draws the workspace; markup put in a triage report is shown as text and its handler does not run; the
    setup page's Open ecadlib button (it had an inline handler) still opens the main page."""
    from tests.test_ui_browser import _browser
    b = _browser()
    if not b:
        print("      (node + Playwright + Chromium not installed: skipped)")
        return
    node, mods, exe = b
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "ws")
        (ws.root / "reports" / "triage_shared.json").write_text(json.dumps({
            "id": "shared", "name": "BOM.csv", "bom": None, "finished": "2026-11-20T10:00:00+00:00", "user": None, "mapping": {},
            "reports": ["reports/a.md", "reports/a.csv"], "counts": {"in_library": PAYLOAD.replace("PWNED", "window.PWNED=1")},
            "rows": [{"line": PAYLOAD.replace("PWNED", "window.PWNED=1"), "designator": "R1", "mpn": "X", "status": "in_library"}]}))
        srv = S.make_server(ws, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/"
            cfg = {"url": AC.with_key(url, srv.access.ticket()), "setup": AC.with_key(url + "setup", srv.access.ticket()),
                   "plain": url, "executable": exe}
            with contextlib.redirect_stderr(io.StringIO()):
                p = subprocess.run([node, str(ROOT / "tests" / "sec1123_page_browser.js"), json.dumps(cfg)], capture_output=True,
                                   text=True, env={**UC.os.environ, "NODE_PATH": mods}, timeout=300)
        finally:
            srv.shutdown()
            srv.server_close()
    assert p.returncode == 0, p.stderr[-3000:]
    checks = json.loads(p.stdout.strip().splitlines()[-1])["checks"]
    assert len(checks) >= 8 and all(c[1] for c in checks), [c for c in checks if not c[1]]


# ------------------------------------------------------------------ second review (I2): another account's program
@lite
def test_another_accounts_connection_is_told_apart_on_linux(tmp_path):
    """The socket list the kernel keeps (/proc/net/tcp) names the owner of each end. A line for the client's end
    owned by another account means: refuse. Not found, or root, or ours: the key decides."""
    import os, sys
    if not sys.platform.startswith("linux"):
        assert AC.other_account(("127.0.0.1", 5), ("127.0.0.1", 6)) is False
        return
    me = os.getuid()
    head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    row = "   {n}: {a} {b} 01 00000000:00000000 00:00000000 00000000 {uid:5d}        0 123 1 0000000000000000 20 4 30 10 -1\n"
    cl, sv = "0100007F:C001", "0100007F:223D"                        # 127.0.0.1:49153 -> 127.0.0.1:8765
    for uid, want in ((me, False), (0, False), (me + 4242, True)):
        (tmp_path / "tcp").write_text(head + row.format(n=0, a=sv, b=cl, uid=me) + row.format(n=1, a=cl, b=sv, uid=uid))
        assert AC.other_account(("127.0.0.1", 49153), ("127.0.0.1", 8765), proc=str(tmp_path)) is want, uid
    assert AC.other_account(("127.0.0.1", 49154), ("127.0.0.1", 8765), proc=str(tmp_path)) is False     # not listed
    assert AC.other_account(("127.0.0.1", 49153), ("127.0.0.1", 8765), proc=str(tmp_path / "none")) is False
    (tmp_path / "tcp6").write_text(head + row.format(n=0, a="00000000000000000000000001000000:C001",
                                                    b="00000000000000000000000001000000:223D", uid=me + 4242))
    assert AC.other_account(("::1", 49153, 0, 0), ("::1", 8765, 0, 0), proc=str(tmp_path)) is True
    assert AC._proc_addr("127.0.0.1", 8765) == ("0100007F:223D" if sys.byteorder == "little" else "7F000001:223D")


def test_another_account_is_refused_even_with_the_key(tmp_path=None):
    """For real, where the test may switch account (root on Linux): a client run as "nobody" that holds the run's
    key and a fresh link gets nothing, and the link stays good for the person's browser."""
    import os, sys
    if not sys.platform.startswith("linux") or os.getuid() != 0:
        print("      (needs root on Linux to run a client as another account: skipped)")
        return
    with tempfile.TemporaryDirectory() as d:
        srv = S.make_server(Workspace(Path(d) / "ws"), "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port, link = srv.server_address[1], None
            link = AC.with_key(f"http://127.0.0.1:{port}/", srv.access.ticket())
            code = ("import os, sys, urllib.request, urllib.error\nos.setgid(65534); os.setuid(65534)\n"
                    "o = urllib.request.build_opener(urllib.request.ProxyHandler({}))\nout = []\n"
                    "for url, head in ((sys.argv[1], {}), (sys.argv[2], {'X-Ecadlib-Key': sys.argv[3]})):\n"
                    "    try:\n        out.append(o.open(urllib.request.Request(url, headers=head), timeout=20).status)\n"
                    "    except urllib.error.HTTPError as e:\n        out.append(e.code)\nprint(out)\n")
            with contextlib.redirect_stderr(io.StringIO()):
                p = subprocess.run([sys.executable, "-I", "-c", code, link, f"http://127.0.0.1:{port}/api/info", srv.access.key],
                                   capture_output=True, text=True, timeout=120)
                assert p.stdout.strip() == "[403, 403]", (p.stdout, p.stderr[-800:])
                assert _enter(port, link)                            # not used up: the person's browser still gets in
                assert _call(port, "GET", "/api/info", cookie=srv.access.key)[0] == 200
        finally:
            srv.shutdown()
            srv.server_close()
