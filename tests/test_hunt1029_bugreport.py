"""Bug hunt 2026-10-29, bug reports: offline errors in the MCP server are not saved as bugs, a damaged saved error
does not break `ecadlib report-bug` / Report a bug, and a ``` inside an error does not end the report's code block."""
import contextlib, io, json
from urllib.parse import parse_qs, urlparse

from tests.fixtures_lite import lite
from ecadlib import bugreport as B


@lite
def test_mcp_offline_network_errors_are_not_saved_as_bugs(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    import urllib.error
    from ecadlib import mcp_server
    errs = [urllib.error.URLError("[Errno -3] Temporary failure in name resolution"), TimeoutError("timed out"),
            ConnectionResetError(104, "Connection reset by peer")]
    try:
        import requests
        errs.append(requests.ConnectionError("HTTPSConnectionPool(host='jlcpcb.com', port=443): Max retries exceeded"))
    except ImportError:
        pass
    with contextlib.redirect_stderr(io.StringIO()):
        for e in errs:
            assert mcp_server._error_text(e)                  # still a message for the assistant
    assert B.saved() == [], [r["error"] for r in B.saved()]   # the web UI and the CLI do not save these either


@lite
def test_damaged_saved_error_does_not_break_report_bug(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    d = B.crash_dir()
    d.mkdir(parents=True)
    (d / "1.json").write_text(json.dumps({"time": "t", "where": "x", "error": "E", "message": None,
                                          "traceback": None}), encoding="utf-8")
    (d / "2.json").write_text(json.dumps({"time": 5, "where": ["w"], "message": ["a"], "traceback": 7}),
                              encoding="utf-8")
    r = B.report_for()                                        # the web UI's /api/bugreport
    assert r["saved"] and r["url"].startswith(B.ISSUES_NEW)
    out = io.StringIO()
    assert B.main(["--print"], out=out) == 0
    assert B.main(["--list"], out=out) == 0
    assert B.saved()[0].get("where") != "report-bug"           # report-bug did not save a crash of its own


@lite
def test_backticks_in_an_error_do_not_end_the_code_block(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    try:
        raise RuntimeError("model reply was ```json\n{}\n``` then @someone and **bold**")
    except RuntimeError as e:
        B.record("librarian", e)
    for body in (B.report_for()["body"], parse_qs(urlparse(B.report_for()["url"]).query)["body"][0]):
        fence = body.split("**Details**")[1].split("\n- When:")[1].split("\n")[2]   # the line that opens the block
        assert set(fence) == {"`"} and len(fence) > 3, fence
        assert body.rstrip().endswith(fence) and body.count(fence) == 2, body
    try:                                                      # a report cut to fit the link keeps its fence closed
        raise RuntimeError("```" + "Ω" * 6000)
    except RuntimeError as e:
        B.record("librarian2", e)
    body = parse_qs(urlparse(B.report_for()["url"]).query)["body"][0]
    assert body.rstrip().endswith("````") and body.count("````") == 2, body[-200:]
