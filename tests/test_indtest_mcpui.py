"""Regression tests from the 2026-10-20 independent testing pass: the MCP server and the local web UI. Run:
    python3 -m tests.run_all test_indtest_mcpui"""
import json
from pathlib import Path

from ecadlib.app import Workspace
from tests.fixtures_lite import lite

REPO = Path(__file__).resolve().parent.parent
DEMO_LIB = str(REPO / "examples" / "kicad_lib")


# T046
@lite
def test_offline_house_style_guide_makes_no_model_call(tmp_path, monkeypatch):
    """`ecadlib mcp --offline` priced and then sent house_style_guide to the model: --offline must make no model call."""
    from ecadlib import house_style as HS
    sent = []
    monkeypatch.setattr(HS, "write_guide", lambda *a, **k: sent.append(a) or {"usd": 0.0, "model": "x"})
    ws = Workspace(tmp_path / "ws", libs=[DEMO_LIB], offline=True)
    r = ws.learn_house_style(ai=True, ask_first=True)
    assert r["ai"]["offline"] is True and "--offline" in r["ai"]["text"]
    for call in (lambda: ws.style_guide(5, True, True), lambda: ws.learn_house_style(ai=True)):
        try:
            call()
            raise AssertionError("an offline workspace asked the model")
        except ValueError as e:
            assert "--offline" in str(e)
    assert sent == []


def _res_bom(path, n):
    pk = ["0402", "0603", "0805", "1206"]
    path.write_text("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #\n" + "".join(
        f"R{i},1,{i}k,R_{pk[i % 4]},ACME,RES{pk[i % 4]}-{i}K-T\n" for i in range(1, n + 1)))
    return str(path)


# T047
@lite
def test_cancelled_librarian_run_keeps_finished_parts(tmp_path):
    """A librarian_run job cancelled at part 6 of 12 (or stopped while logging its summary) saved no
    librarian_state.json: the re-run built all 12 again although the cancel answer said finished parts are kept."""
    from ecadlib import librarian as L
    bom, out = _res_bom(tmp_path / "bom.csv", 12), tmp_path / "out"

    class Stop(Exception):
        pass

    def stop_at(prefix):
        def log(*a, **k):
            if str(a[0] if a else "").lstrip().startswith(prefix):
                raise Stop
        return log
    for prefix, kept in (("[6/12]", 5), ("done ", 12)):
        o = out / prefix[:2]
        try:
            L.run(bom, [DEMO_LIB], str(o), ["standard"], offline=True, cache=str(tmp_path / "cache"), log=stop_at(prefix))
            raise AssertionError("not stopped")
        except Stop:
            pass
        state = json.loads((o / "librarian_state.json").read_text())
        assert len(state) == kept, (prefix, sorted(state))
        lines = []
        L.run(bom, [DEMO_LIB], str(o), ["standard"], offline=True, cache=str(tmp_path / "cache"), log=lines.append)
        assert f"finding symbols and footprints for {12 - kept} part(s)" in "\n".join(lines) or kept == 12, lines[:20]
        assert sum(x.lstrip().startswith("skip ") for x in lines) == kept, lines


def _mcp(ws, name, args, i=1):
    """tools/call over the in-process MCP handler -> (isError, text)."""
    from ecadlib import mcp_server as M
    r = M.handle(ws, {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
    if "error" in r:
        return True, json.dumps(r["error"])
    return bool(r["result"].get("isError")), "".join(c.get("text", "") for c in r["result"]["content"])


# T048
@lite
def test_cut_answer_advises_only_arguments_the_tool_takes_and_library_parts_pages(tmp_path):
    """A cut library_parts answer said "Ask for less: summary_only, one part, a status filter or a smaller page" and
    library_parts refused every one of them as unknown arguments: a 200-part plan could not be read whole."""
    from ecadlib import mcp_server as M
    ws = Workspace(tmp_path / "ws", offline=True)
    ws.mcp_options = M.Options(wait=120, max_chars=3000)
    bom = _res_bom(tmp_path / "bom.csv", 12)
    assert not _mcp(ws, "librarian_run", {"bom": bom, "sources": ["standard"]})[0]
    for tool in ("library_parts", "altium365_plan"):
        err, txt = _mcp(ws, tool, {"bom": bom})
        note = txt[txt.index("[cut:"):]
        assert "offset and limit" in note and "summary_only" not in note and "smaller page" not in note, note
    seen, off = [], 0
    while off is not None:
        err, txt = _mcp(ws, "library_parts", {"bom": bom, "offset": off, "limit": 2})
        assert not err and "[cut:" not in txt, txt[-300:]
        r = json.loads(txt)
        seen += [p["part"] for p in r["parts"]]
        off = r["page"]["next_offset"]
    assert len(seen) == len(set(seen)) == 12 == r["page"]["total"]
    err, txt = _mcp(ws, "library_parts", {"bom": bom, "part": seen[3]})
    assert [p["part"] for p in json.loads(txt)["parts"]] == [seen[3]]
    err, txt = _mcp(ws, "library_parts", {"bom": bom, "status": "nonesuch"})
    assert json.loads(txt)["parts"] == []
    ws.mcp_options = M.Options(wait=120, max_chars=200)
    err, txt = _mcp(ws, "workspace_info", {})
    assert "cannot return less" in txt and "--max-chars" in txt, txt


def _http(url, method, headers, body=None):
    import http.client
    from urllib.parse import urlsplit
    u = urlsplit(url)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=20)
    c.request(method, u.path, body=body, headers=headers)
    r = c.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    c.close()
    return out


# T049
@lite
def test_http_server_answers_cors_only_for_allowed_origins(tmp_path):
    """A page at an --allow-origin origin could not call `ecadlib mcp --http` from a browser: OPTIONS was 501 and no
    answer carried Access-Control-Allow-Origin. Only origins given with --allow-origin get CORS; the token still counts."""
    from ecadlib import mcp_http as H
    ws = Workspace(tmp_path / "ws", offline=True)
    good, bad, local = "https://app.example", "https://evil.example", "http://localhost:5173"
    srv, url = H.serve_in_thread(ws, token="tok", origins=[good + "/"])
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
    post = {"Content-Type": "application/json", "Authorization": "Bearer tok"}
    try:
        st, h, _ = _http(url, "OPTIONS", dict(pre, Origin=good))
        assert st == 204 and h["access-control-allow-origin"] == good, (st, h)
        assert "POST" in h["access-control-allow-methods"] and "authorization" in h["access-control-allow-headers"].lower()
        for o in (bad, local):                     # neither a foreign nor a local page gets CORS it was not given
            st, h, _ = _http(url, "OPTIONS", dict(pre, Origin=o))
            assert st == 403 and "access-control-allow-origin" not in h, (o, st, h)
        st, h, b = _http(url, "POST", dict(post, Origin=good), init)
        assert st == 200 and h["access-control-allow-origin"] == good and json.loads(b)["result"], (st, h, b)
        st, h, _ = _http(url, "POST", {"Content-Type": "application/json", "Origin": good}, init)
        assert st == 401, st                       # the token still counts
        st, h, _ = _http(url, "POST", dict(post, Origin=bad), init)
        assert st == 403 and "access-control-allow-origin" not in h, (st, h)
        st, h, _ = _http(url, "POST", dict(post, Origin=local), init)
        assert st == 200 and "access-control-allow-origin" not in h, (st, h)
        st, h, _ = _http(url, "POST", post, init)
        assert st == 200 and "access-control-allow-origin" not in h, (st, h)
    finally:
        srv.shutdown()
        srv.server_close()


PAGE = (REPO / "ecadlib" / "ui" / "static" / "index.html").read_text(encoding="utf-8")


# T051 (the browser check: tests/test_ui_browser.py, "planned")
@lite
def test_ui_opens_a_plan_made_outside_it(tmp_path):
    """`ecadlib demo` then `ecadlib ui --workspace ecadlib-demo` opened with step 1 only: a plan with no BOM check in
    the UI's history was never opened. /api/info names the BOMs that have a plan and the page opens the newest."""
    from tests.test_ui import serve
    from tests.test_ui_browser import planned_workspace
    ws = planned_workspace(tmp_path / "ws")
    srv, call = serve(ws)
    try:
        st, info = call("GET", "/api/info")
        assert st == 200 and info["planned"] == ["uploads/demo/demo_bom.csv"], info
        assert call("GET", "/api/triage/history")[1]["triages"] == []
    finally:
        srv.shutdown()
    fn = PAGE[PAGE.index("async function openReview"):PAGE.index("function startApp")]
    assert "info.planned" in fn, fn


# T112
@lite
def test_new_bom_asks_for_its_plan_without_an_error(tmp_path):
    """Every first upload of a new BOM made the page GET /api/parts -> 400 (and a console error): "no plan yet" is no
    error. With ifplan=1 the server answers 200 {"plan": null}; without it a missing plan is still a 400."""
    from tests.test_ui import serve
    ws = Workspace(tmp_path / "ws", [DEMO_LIB], offline=True)
    (ws.root / "uploads").mkdir(parents=True, exist_ok=True)
    _res_bom(ws.root / "uploads" / "new.csv", 2)
    srv, call = serve(ws)
    try:
        assert call("GET", "/api/parts?bom=uploads/new.csv&ifplan=1") == (200, {"plan": None})
        assert call("GET", "/api/parts?bom=uploads/new.csv")[0] == 400
        assert call("GET", "/api/info")[1]["planned"] == []
    finally:
        srv.shutdown()
    fn = PAGE[PAGE.index("async function loadParts"):PAGE.index("function libNext")]
    assert '"&ifplan=1"' in fn and "!parts.plan" in fn, fn


# T052 (the browser check: tests/ui_browser.js, "release anyway on a missing file says it stays held")
def test_release_anyway_toast_reads_the_new_status():
    """Release anyway on a part held for a missing symbol toasted "Released ... it goes to Altium with the next push"
    while the release check kept it held (a missing file cannot be overridden)."""
    fn = PAGE[PAGE.index("async function release("):PAGE.index("async function partClick")]
    assert 'effective(p) === "held"' in fn and "stays held" in fn, fn
    assert fn.index("stays held") < fn.index("goes to Altium with the next push"), fn


# T053
def test_setup_check_key_checks_the_pasted_key():
    """Setup page: pasting a key and pressing Check key said "no key yet: paste your key above" (only the stored key was
    checked); with a key stored, the stored one was checked instead of the pasted one."""
    from ecadlib import easy_setup as ES
    from tests.test_easy_setup import _R, _home
    with _home():
        seen = []

        def get(url, headers, timeout):
            seen.append(headers["x-api-key"])
            return _R(200, {"data": []})
        r = ES.check_key({"provider": "anthropic", "key": " sk-ant-pasted "}, get=get)
        assert r["ok"] and seen == ["sk-ant-pasted"] and "Save" in r["message"] and "sk-ant" not in r["message"], r
        assert not ES.check_key({"provider": "anthropic", "key": ""}, get=get)["ok"]          # nothing pasted, none stored
        assert not ES.check_key({"provider": "anthropic", "key": "two words"}, get=get)["ok"] and len(seen) == 1
    page = (REPO / "ecadlib" / "ui" / "static" / "setup.html").read_text(encoding="utf-8")
    fn = page[page.index('$("checkkey").onclick'):]
    assert 'key: $("key").value' in fn[:400], fn[:400]


# T103
@lite
def test_mcp_http_on_a_taken_port_names_the_address(tmp_path):
    """A second `ecadlib mcp --http` on the same port said "Address already in use / hint: check the path and the
    disk": no address, and a hint about files."""
    import argparse, contextlib, io, socket
    from ecadlib import mcp_http as H
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    a = argparse.Namespace(no_auth=False, token="t", host="127.0.0.1", port=port, path="/mcp", allow_origin=[])
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = H.run(Workspace(tmp_path / "ws"), a)
    finally:
        busy.close()
    assert rc == 2 and f"127.0.0.1:{port}" in err.getvalue() and f"--port {port + 1}" in err.getvalue(), err.getvalue()
    assert "disk" not in err.getvalue()


# T104
def test_expected_mcp_failures_are_not_reported_as_bugs():
    """An --offline cache miss and a refused API key came back as "Something went wrong (...cut at 160 characters).
    Run it again with --debug and report the output": the agent was told to report a bug instead of what to fix."""
    import contextlib, io
    from ecadlib import datasheet as D, mcp_server as M
    key = D.APIError("HTTP 401: " + D.HINTS[401], status=401)
    assert M._error_text(key) == str(key) and str(key).endswith("the variable it is read from")
    miss = M._error_text(IOError("kicad-symbols/Connector.kicad_symdir/USB_C_Receptacle.kicad_sym: offline: not in cache"))
    assert miss.endswith("not in cache; run the server without --offline to fetch it") and "report" not in miss, miss
    with contextlib.redirect_stderr(io.StringIO()):          # a real bug still says so (its traceback goes to stderr)
        assert "report" in M._error_text(RuntimeError("boom")) and "report" in M._error_text(OSError(5, "I/O error"))


def _stdio_mcp(args, home, calls):
    """`python -m ecadlib mcp <args>` over stdio: initialize, then each (tool, arguments) -> [(isError, text)]."""
    import os, subprocess, sys
    msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
             "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}]
    msgs += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": a}}
             for i, (n, a) in enumerate(calls, 1)]
    env = dict(os.environ, PYTHONPATH=str(REPO), ECADLIB_HOME=str(home), HTTPS_PROXY="http://127.0.0.1:9",
               HTTP_PROXY="http://127.0.0.1:9")
    p = subprocess.run([sys.executable, "-m", "ecadlib", "mcp"] + args, input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, env=env, timeout=120, cwd=str(home))
    got = {r["id"]: r for r in map(json.loads, p.stdout.splitlines()) if "id" in r}
    return [(bool(got[i]["result"].get("isError")), got[i]["result"]["content"][0]["text"]) for i in range(1, len(calls) + 1)]


# T105
@lite
def test_confine_refusal_hint_lifts_the_confinement(tmp_path):
    """stdio `--confine` refused a file outside the workspace with "start it with --any-path to allow more", but
    --any-path does not lift --confine: following the advice changed nothing."""
    import re
    (tmp_path / "home").mkdir()
    bom = tmp_path / "outside.csv"
    bom.write_text("Designator,Value,Footprint\nR1,10k,R_0603_1608Metric\n")
    call = [("triage_bom", {"bom": str(bom), "summary_only": True})]
    base = ["--offline", "--workspace", str(tmp_path / "ws")]
    (err, txt), = _stdio_mcp(base + ["--confine"], tmp_path / "home", call)
    hint = re.search(r"\((start it [^)]*?) to allow more\)", txt)
    assert err and hint and hint.group(1) == "start it without --confine", txt
    (err, txt), = _stdio_mcp(base, tmp_path / "home", call)
    assert not err, txt


# T106
@lite
def test_preview_symbol_refusal_says_symbol(tmp_path):
    """preview_symbol {"name": "MCU/ABC", save} answered "footprint name 'MCU/ABC' contains a path separator"."""
    ws = Workspace(tmp_path / "ws", offline=True)
    try:
        ws.preview_symbol([{"num": "1", "name": "VCC"}, {"num": "2", "name": "GND"}], "MCU/ABC", save=True)
        raise AssertionError("a symbol name with a slash was saved")
    except ValueError as e:
        assert str(e).startswith("symbol name 'MCU/ABC' contains a path separator") and "MCU_ABC" in str(e), e
    try:
        ws.safe_name("SOIC-8/TI_D", strict=True)
    except ValueError as e:
        assert str(e).startswith("footprint name"), e


# T107
def test_mcp_docs_name_only_real_tools():
    """docs/mcp.md told the user to price a sync datasheet read with `datasheet_quote`, which is no MCP tool."""
    import re
    from ecadlib import mcp_server as M
    names = {t["name"] for t in M.TOOLS}
    args = {k for t in M.TOOLS for k in t["inputSchema"].get("properties", {})}
    doc = (REPO / "docs" / "mcp.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z][a-z0-9]*_[a-z0-9_]+)`", doc)) - args
    assert sorted(n for n in named if n not in names) == []


# T108
@lite
def test_ui_survives_a_deleted_workspace_folder(tmp_path):
    """The workspace folder deleted while `ecadlib ui` ran: /api/info and every upload answered 400 "Something went
    wrong (FileNotFoundError ... 'uploads')" with a traceback, until a restart. The folders are made again."""
    import contextlib, io, shutil
    from tests.test_ui import serve, wait_job
    ws = Workspace(tmp_path / "ws", [DEMO_LIB], offline=True)
    srv, call = serve(ws)
    try:
        shutil.rmtree(tmp_path / "ws")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            st, info = call("GET", "/api/info")
            assert st == 200 and info["boms"] == [], info
            st, j = call("POST", "/api/upload", b"Designator,Value,Footprint\nR1,10k,R_0603_1608Metric\n",
                         {"X-Filename": "board.csv"})
            assert st == 200, j
            assert wait_job(call, j["job"])["state"] == "done"
        assert "Traceback" not in err.getvalue(), err.getvalue()
    finally:
        srv.shutdown()


# T109
@lite
def test_a365_refused_or_unreachable_nexar_is_a_plain_message(tmp_path, monkeypatch):
    """Step 3's Altium 365 workspace list and Push showed "Something went wrong (HTTPError: 401 Client Error ...)" (or
    ProxyError) with a traceback when Nexar refused the token or could not be reached."""
    import contextlib, io, requests
    from ecadlib import altium365 as A, userconfig as UC
    from tests.test_ui import serve

    class Session:
        def __init__(self, answer):
            self.answer = answer

        def post(self, url, **k):
            if isinstance(self.answer, Exception):
                raise self.answer
            r = requests.Response()
            r.status_code, r.url, r._content = self.answer, url, b'{"message": "Unauthorized"}'
            return r
    for answer, says in ((401, "refused the token (401)"), (502, "answered 502"),
                         (requests.ConnectionError("proxy down"), "could not reach Altium 365")):
        try:
            A.Nexar("tok", Session(answer)).workspaces()
            raise AssertionError("no error")
        except A.NexarUnreachable as e:
            assert says in str(e) and "Error:" not in str(e), e
    monkeypatch.setattr(UC, "secret", lambda name, *a, **k: "nexar-FAKE" if name == "NEXAR_TOKEN" else None)
    real = A.Nexar.__init__
    monkeypatch.setattr(A.Nexar, "__init__", lambda self, token, session=None: real(self, token, Session(401)))
    ws = Workspace(tmp_path / "ws", [DEMO_LIB], offline=True)
    srv, call = serve(ws)
    try:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            st, r = call("POST", "/api/altium365/workspaces", {})
        assert st == 400 and r["error"].startswith("Altium 365 (Nexar) refused the token (401)"), r
        assert "Traceback" not in err.getvalue(), err.getvalue()
    finally:
        srv.shutdown()


# T110
def test_docs_and_part_panel_name_no_review_queue_the_ui_lacks():
    """docs/using-ecadlib.md promised a Review queue nav item, a `q` shortcut and approving "in the review queue", and
    a part waiting for sign-off said "approve it in the review queue": the UI has none of them (step 2 has the parts)."""
    import re
    from ecadlib import messages as M
    doc = (REPO / "docs" / "using-ecadlib.md").read_text(encoding="utf-8")
    keyboard = re.search(r"\*\*Keyboard\.\*\*(.*)", doc).group(1).split("Light and dark")[0]
    keys = {k for d in re.findall(r"`([^`]+)`", keyboard) for k in d.split(" / ")}
    listed = set(re.findall(r"<kbd>([^<]+)</kbd>", PAGE[PAGE.index('id="keysdlg"'):]))
    assert keys <= listed, sorted(keys - listed)
    assert "Review queue" not in PAGE and not re.search(r"Review queue \(with how many|in the review queue\)", doc)
    assert "review queue" not in M.explain("awaiting approval: a reviewer")["fix"]


# T111
@lite
def test_dblib_card_names_only_files_export_writes(tmp_path):
    """Step 3's DbLib card promised parts.sqlite, parts.xlsx and two .DbLib files; after step 2 Export DbLib writes the
    plan's <name>.DbLib, .xlsx, .csv (and the models in SchLib/ PcbLib/)."""
    import re
    from tests.test_ui_browser import planned_workspace
    ws = planned_workspace(tmp_path / "ws")
    written = {f.rsplit("/", 1)[-1] for f in ws.export_dblib("uploads/demo/demo_bom.csv")["files"]}
    box = re.search(r"<h3>Database library \(DbLib\)</h3>\s*<p[^>]*>(.*?)</p>", PAGE, re.S).group(1)
    promised = set(re.findall(r"\b[\w-]+\.(?:sqlite|xlsx|DbLib)\b", box))
    assert promised <= written, (sorted(promised), sorted(written))
    assert "After step 2" in box and any(f.endswith(".DbLib") for f in written)
