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
