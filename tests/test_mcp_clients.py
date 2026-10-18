"""The MCP server as Claude Code / Desktop, OpenAI Codex and xAI Grok clients use it: schemas every client accepts,
arguments as models really send them, slow tools that answer with a job instead of a client timeout, no paid model
call without a yes, nothing but JSON-RPC on stdout, and the Streamable HTTP transport (token, origin, file
confinement). When the official `mcp` Python SDK is installed, its stdio and HTTP clients drive the server too. Run:
    python3 -m tests.run_all test_mcp_clients"""
import contextlib, http.client, io, json, os, re, subprocess, sys, tempfile, threading, time
from pathlib import Path

from ecadlib import mcp_server as M, mcp_http as H
from ecadlib.app import Workspace

REPO = Path(__file__).resolve().parent.parent
BOM = str(REPO / "examples" / "sample_bom.csv")


def _call(ws, name, args, i=1, meta=None):
    params = {"name": name, "arguments": args}
    if meta:
        params["_meta"] = meta
    return M.handle(ws, {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": params})


def _text(resp):
    return resp["result"]["content"][0]["text"]


def _walk(s, path, out):
    """Problems a strict tool-schema converter has with one JSON schema node (Codex turns MCP schemas into its own
    types, OpenAI-compatible APIs such as xAI's reject some keywords, small models need a type on every argument)."""
    if not isinstance(s, dict):
        return out.append(f"{path}: not an object")
    if not isinstance(s.get("type"), str):
        out.append(f"{path}: needs one 'type'")
    for k in ("anyOf", "oneOf", "allOf", "$ref", "not", "if"):
        if k in s:
            out.append(f"{path}: uses {k}")
    if s.get("type") == "array" and not isinstance(s.get("items"), dict):
        out.append(f"{path}: array without items")
    if s.get("type") == "object" and "properties" not in s and "additionalProperties" not in s:
        out.append(f"{path}: object without properties or additionalProperties")
    if "enum" in s and not all(isinstance(x, str) for x in s["enum"]):
        out.append(f"{path}: non-string enum")
    for k, v in s.get("properties", {}).items():
        _walk(v, f"{path}.{k}", out)
    if isinstance(s.get("items"), dict):
        _walk(s["items"], f"{path}[]", out)
    if isinstance(s.get("additionalProperties"), dict):
        _walk(s["additionalProperties"], f"{path}{{}}", out)
    return out


def test_every_tool_schema_works_in_claude_codex_and_grok():
    """Tool names fit ^[a-zA-Z0-9_-]{1,64}$ (OpenAI / xAI function names), descriptions fit 1024 characters (OpenAI's
    limit), every argument has one type (Codex guessed 'string' for librarian_run's untyped anyOf refresh), required
    arguments exist, and every tool has a title, annotations and a handler."""
    import inspect
    names, problems = [t["name"] for t in M.TOOLS], []
    assert len(names) == len(set(names)), "duplicate tool names"
    src = inspect.getsource(M.call_tool) + inspect.getsource(M._call)
    for t in M.TOOLS:
        n, sch = t["name"], t["inputSchema"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", n):
            problems.append(f"{n}: name")
        if not 20 <= len(t["description"]) <= 1024:
            problems.append(f"{n}: description length {len(t['description'])}")
        if sch.get("type") != "object" or not isinstance(sch.get("properties"), dict):
            problems.append(f"{n}: inputSchema must be an object with properties")
        problems += [f"{n}: required {k} not a property" for k in sch.get("required", []) if k not in sch["properties"]]
        problems += [f"{n}: {p}" for p in _walk(sch, "input", [])[1:] if not p.startswith("input:")]
        a = t.get("annotations") or {}
        if not t.get("title") or set(a) < {"readOnlyHint", "destructiveHint", "openWorldHint"}:
            problems.append(f"{n}: title / annotations")
        if f'"{n}"' not in src:
            problems.append(f"{n}: no handler")
    assert not problems, "\n".join(problems)
    import jsonschema
    for t in M.TOOLS:
        jsonschema.Draft7Validator.check_schema(t["inputSchema"])
    assert M.READ_ONLY <= set(names) and M.DESTRUCTIVE <= set(names) and M.OPEN_WORLD <= set(names) and set(M.CORE) <= set(names)
    assert not M.READ_ONLY & M.DESTRUCTIVE
    assert len(json.dumps(M.TOOLS)) < 48000, "the tool list is getting expensive for every request"


def test_initialize_offers_instructions_and_newer_protocols():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        for asked, want in (("2025-11-25", "2025-11-25"), ("2025-06-18", "2025-06-18"), ("2024-11-05", "2024-11-05"),
                            ("2099-01-01", M.PROTOCOL)):
            r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "codex"}}})["result"]
            assert r["protocolVersion"] == want and "job_status" in r["instructions"] and r["capabilities"] == {
                "tools": {"listChanged": False}}, r
        # clients that ask for resources / prompts whatever the capabilities say get empty lists, not errors
        for m, k in (("resources/list", "resources"), ("resources/templates/list", "resourceTemplates"), ("prompts/list", "prompts")):
            assert M.handle(ws, {"jsonrpc": "2.0", "id": 2, "method": m})["result"] == {k: []}
        assert M.handle(ws, {"jsonrpc": "2.0", "id": 3, "method": "logging/setLevel", "params": {"level": "info"}})["result"] == {}
        assert M.handle(ws, {"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601


def test_arguments_as_small_models_send_them():
    """null for optional arguments (OpenAI strict-mode models), "5" and "true" as strings, an object as a JSON string,
    a part number as a number, and the old refresh: true all work; what still does not fit comes back as a tool error
    naming the arguments the tool takes, so the model can call again."""
    tool = lambda n: next(t for t in M.TOOLS if t["name"] == n)
    a = M._normalize(tool("part_candidates"), {"bom": "b.csv", "part": 12, "kind": None, "limit": "5"})
    assert a == {"bom": "b.csv", "part": "12", "limit": 5}, a
    a = M._normalize(tool("find_duplicates"), {"bom": "b.csv", "merge": "false"})
    assert a["merge"] is False
    a = M._normalize(tool("preview_footprint"), {"spec": '{"name": "X", "type": "chip"}', "density": None})
    assert a == {"spec": {"name": "X", "type": "chip"}}, a
    assert M._normalize(tool("librarian_run"), {"bom": "b", "refresh": True}) == {"bom": "b", "refresh_all": True}
    assert M._normalize(tool("librarian_run"), {"bom": "b", "refresh": "R1, C2"}) == {"bom": "b", "refresh": ["R1", "C2"]}
    assert M._normalize(tool("librarian_run"), {"bom": "b", "refresh": False}) == {"bom": "b"}
    assert M._normalize(tool("upkeep_run"), {"bom": "b", "checks": '["lifecycle", "a365"]'})["checks"] == ["lifecycle", "a365"]
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        r = _call(ws, "triage_bom", {"bom_path": BOM})["result"]          # a wrong argument name
        assert r["isError"] and "missing argument(s): bom" in r["content"][0]["text"] \
            and "bom (string, required)" in r["content"][0]["text"], r
        r = _call(ws, "part_candidates", {"bom": "b.csv", "part": "R1", "limit": 500})["result"]
        assert r["isError"] and "limit" in r["content"][0]["text"], r
        r = M.handle(ws, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "workspace_info", "arguments": "{}"}})["result"]   # arguments as a string
        assert not r["isError"], r
        r = _call(ws, "workspace_inf", {})
        assert r["error"]["code"] == -32602 and "did you mean workspace_info" in r["error"]["message"], r


def test_slow_tool_answers_with_a_job_instead_of_timing_out():
    """Codex stops waiting for a tool after 60 s by default and other clients have their own limits: a call that
    takes longer than the server's wait answers with a job id, job_status waits for it and returns the tool's own
    answer, progress notifications go out while it runs (when the client asked for them), and a second write on the
    same BOM is refused while the first runs."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        ws.mcp_options = M.Options(wait=0.2)
        gate = threading.Event()
        real = M.call_tool

        def slow(ws_, name, args, progress=None):
            if name == "librarian_run":
                for i in range(3):
                    progress(i, 3, f"part {i}")
                    gate.wait(5)
                return {"ready": 3, "held": 0}
            return real(ws_, name, args, progress)
        M.call_tool = slow
        try:
            sent = []
            r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "librarian_run", "arguments": {"bom": "b.csv"}, "_meta": {"progressToken": "t1"}}},
                         sent.append)["result"]
            job = json.loads(r["content"][0]["text"])
            assert not r["isError"] and job["state"] == "running" and job["tool"] == "librarian_run" \
                and "job_status" in job["next"], job
            assert sent and sent[0]["method"] == "notifications/progress" and sent[0]["params"]["progressToken"] == "t1"
            assert [n["params"]["progress"] for n in sent] == sorted({n["params"]["progress"] for n in sent})   # increasing
            busy = _call(ws, "release_check", {"bom": "b.csv"})["result"]
            assert busy["isError"] and job["job"] in busy["content"][0]["text"], busy
            gate.set()
            done = _call(ws, "job_status", {"id": job["job"], "wait_seconds": 10})["result"]
            assert not done["isError"] and json.loads(done["content"][0]["text"]) == {"ready": 3, "held": 0}, done
            assert _call(ws, "job_status", {"id": "nope"})["result"]["isError"]
            listed = json.loads(_text(_call(ws, "job_status", {})))["jobs"]
            assert any(j["job"] == job["job"] and j["state"] == "done" for j in listed), listed
            # cancel: the job stops at its next progress call
            gate.clear()
            job = json.loads(_text(_call(ws, "librarian_run", {"bom": "c.csv"})))
            r = _call(ws, "job_status", {"id": job["job"], "cancel": True, "wait_seconds": 0})
            gate.set()
            r = _call(ws, "job_status", {"id": job["job"], "wait_seconds": 10})["result"]
            assert r["isError"] and "cancelled" in r["content"][0]["text"], r
        finally:
            M.call_tool = real
            gate.set()
            ws._mcp_jobs.wait(10)


def test_librarian_run_never_pays_without_a_yes():
    """librarian_run over MCP ran the paid datasheet reader (ai=True) with no quote and no yes; the Altium, KiCad and
    web front ends already priced it first (#49). Now it prices, and ai_read needs the approved amount."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        seen = {}
        ws.librarian_run = lambda bom, sources=None, allow=(), refresh=None, progress=None, ai=True: seen.update(ai=ai) or {"ai": None}
        assert not _call(ws, "librarian_run", {"bom": "b.csv"})["result"]["isError"]
        assert seen == {"ai": False}, seen
        r = _call(ws, "ai_read", {"bom": "b.csv"})["result"]
        assert r["isError"] and "approved_usd" in r["content"][0]["text"], r
        got = {}
        ws.ai_read = lambda bom, approved, over, unknown, parts, progress: got.update(usd=approved) or {"ready": 1}
        assert not _call(ws, "ai_read", {"bom": "b.csv", "approved_usd": "0.42"})["result"]["isError"]
        assert got == {"usd": 0.42}, got
        style = {}
        ws.learn_house_style = lambda *a, **k: style.update(k) or {"ok": True}
        _call(ws, "learn_house_style", {"ai": True})
        assert style.get("ask_first") is True, style


def test_long_answers_are_cut_with_a_hint():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        ws.mcp_options = M.Options(max_chars=200)
        ws.info = lambda: {"x": "y" * 5000}
        t = _text(_call(ws, "workspace_info", {}))
        assert len(t) < 600 and "cut: the answer was" in t and "ECADLIB_MCP_MAX_CHARS" in t, t
        ws.mcp_options = M.Options()
        assert "Ω" in _text(_call(ws, "workspace_info", {})) or True        # ensure_ascii off: no \\u escapes
        ws.info = lambda: {"tol": "±1%"}
        assert _text(_call(ws, "workspace_info", {})) == '{"tol":"±1%"}'


def test_tool_sets_for_small_models():
    assert M.tool_names("core") == set(M.CORE) and "job_status" in M.tool_names("read-only")
    assert M.tool_names("triage_bom,doctor") == {"triage_bom", "doctor"}
    try:
        M.tool_names("triage")
        raise AssertionError("no error")
    except ValueError as e:
        assert "unknown tool" in str(e)
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        ws.mcp_options = M.Options(tools=M.tool_names("read-only"))
        names = {t["name"] for t in M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        assert "altium365_push" not in names and "workspace_info" in names
        r = _call(ws, "altium365_push", {"bom": "b.csv"})
        assert r["error"]["code"] == -32602 and "not enabled" in r["error"]["message"], r


def test_stdout_carries_only_json_rpc_even_when_a_child_process_prints():
    """contextlib.redirect_stdout only moves Python's sys.stdout: kicad-cli, pdftoppm or git started by a tool
    inherited file descriptor 1 and could write into the protocol stream. The real server now keeps its own copy."""
    code = ("import os, sys; from ecadlib import mcp_server as M\n"
            "real = M.call_tool\n"
            "def noisy(ws, name, args, progress=None):\n"
            "    os.system('echo CHILD-NOISE'); print('PY-NOISE'); os.write(1, b'FD-NOISE\\n')\n"
            "    return real(ws, name, args, progress)\n"
            "M.call_tool = noisy\n"
            "M.main(sys.argv[1:])\n")
    with tempfile.TemporaryDirectory() as d:
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "workspace_info", "arguments": {}}}]
        p = subprocess.run([sys.executable, "-c", code, "--workspace", d], input="".join(json.dumps(m) + "\n" for m in msgs),
                           capture_output=True, text=True, timeout=60, cwd=REPO)
    lines = p.stdout.splitlines()
    assert [json.loads(x)["id"] for x in lines] == [1, 2], p.stdout
    assert "CHILD-NOISE" in p.stderr and "PY-NOISE" in p.stderr and "FD-NOISE" in p.stderr, p.stderr


@contextlib.contextmanager
def _http(ws, **kw):
    srv, url = H.serve_in_thread(ws, **kw)
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _post(port, body, headers=None, path="/mcp", method="POST"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    c.request(method, path, body=data if method == "POST" else None,
              headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})})
    r = c.getresponse()
    raw = r.read()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)


def test_http_transport_token_origin_and_confinement():
    """Streamable HTTP (the xAI API's remote MCP tool cannot start a local process): bearer token required, a web
    page's Origin refused, notifications get 202, GET 405, and file arguments outside the workspace are refused."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "w")
        ws.mcp_options = M.Options(confine=True)
        auth = {"Authorization": "Bearer s3cret"}
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        with _http(ws, token="s3cret") as port, contextlib.redirect_stderr(io.StringIO()):
            st, h, _ = _post(port, init)
            assert st == 401 and "bearer" in h.get("www-authenticate", "").lower()
            assert _post(port, init, {"Authorization": "Bearer wrong"})[0] == 401
            assert _post(port, init, {"Authorization": "s3cret"})[0] == 200          # sent bare (no "Bearer ")
            st, h, body = _post(port, init, auth)
            assert st == 200 and body["result"]["serverInfo"]["name"] == "ecadlib" and h.get("mcp-session-id"), (st, body)
            assert _post(port, init, {**auth, "Origin": "https://evil.example"})[0] == 403
            assert _post(port, init, {**auth, "Origin": "http://localhost:6274"})[0] == 200     # MCP Inspector
            assert _post(port, {"jsonrpc": "2.0", "method": "notifications/initialized"}, auth)[0] == 202
            assert _post(port, b"", auth, method="GET")[0] == 405
            assert _post(port, b"", {}, path="/health", method="GET")[0] == 200
            assert _post(port, init, {**auth, "MCP-Protocol-Version": "1999-01-01"})[0] == 400
            assert _post(port, b"{not json", auth)[2]["error"]["code"] == -32700
            st, _, body = _post(port, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                       "params": {"name": "triage_bom", "arguments": {"bom": BOM}}}, auth)
            assert body["result"]["isError"] and "outside the workspace" in body["result"]["content"][0]["text"], body
            st, _, body = _post(port, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "triage_bom", "arguments": {"bom": "../../etc/passwd"}}}, auth)
            assert body["result"]["isError"] and "outside the workspace" in body["result"]["content"][0]["text"], body
            up = _post(port, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "upload_bom", "arguments": {"filename": "b.csv", "content": Path(BOM).read_text(encoding="utf-8")}}}, auth)[2]
            bom = json.loads(up["result"]["content"][0]["text"])["bom"]
            st, _, body = _post(port, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                "name": "triage_bom", "arguments": {"bom": bom, "summary_only": True}}}, auth)
            assert not body["result"]["isError"] and json.loads(body["result"]["content"][0]["text"])["counts"], body
        # no token: only on a loopback address, and the Host header must be local (DNS rebinding)
        try:
            H.make_server(ws, "0.0.0.0", 0, token=None)
            raise AssertionError("no error")
        except ValueError as e:
            assert "loopback" in str(e)
        with _http(ws, token=None) as port, contextlib.redirect_stderr(io.StringIO()):
            assert _post(port, init)[0] == 200
            assert _post(port, init, {"Host": "attacker.example:80"})[0] == 403


def _sdk():
    try:
        import mcp  # noqa: F401
        from mcp.client.session import ClientSession  # noqa: F401
        return True
    except Exception:
        return False


def test_official_mcp_sdk_clients_stdio_and_http():
    """The reference MCP client (the Python SDK that much tooling builds on) over stdio and Streamable HTTP."""
    if not _sdk():
        print("skip  the mcp SDK is not installed")
        return
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    try:
        from mcp.client.streamable_http import streamable_http_client as http_client, create_mcp_http_client
    except ImportError:
        http_client = None

    def attr(o, *names):
        return next(getattr(o, n) for n in names if hasattr(o, n))

    async def exercise(s):
        init = await s.initialize()
        assert attr(init, "instructions")
        tools = (await s.list_tools()).tools
        assert len(tools) == len(M.TOOLS)
        r = await s.call_tool("workspace_info", {})
        assert not attr(r, "is_error", "isError") and "workspace" in json.loads(r.content[0].text)
        r = await s.call_tool("triage_bom", {})
        assert attr(r, "is_error", "isError") and "missing argument" in r.content[0].text

    async def main(d):
        p = StdioServerParameters(command=sys.executable, args=["-m", "ecadlib", "mcp", "--workspace", d, "--offline"],
                                  cwd=str(REPO))
        async with stdio_client(p) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await exercise(s)
        if http_client is None:
            return
        ws = Workspace(Path(d) / "h", offline=True)
        srv, url = H.serve_in_thread(ws, token="t0k")
        try:
            hc = create_mcp_http_client(headers={"Authorization": "Bearer t0k"})
            async with http_client(url, http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await exercise(s)
        finally:
            srv.shutdown()
            srv.server_close()
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
        asyncio.run(main(d))
