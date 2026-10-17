"""Regression tests from the 2026-10-18 review of the local web UI server and the MCP server: framing of the local UI
(clickjacking), deeply nested JSON bodies and API keys in network error messages. Run:
    python3 -m tests.run_all test_review_1018_ui_mcp"""
import contextlib, http.client, io, json, tempfile, threading
from pathlib import Path

import requests

from ecadlib.app import Workspace
from ecadlib.httpcache import CachedHTTP
from ecadlib.ui import server as S


@contextlib.contextmanager
def _served(ws):
    srv = S.make_server(ws, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _req(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    return r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read()


def test_local_ui_refuses_to_be_framed():
    """Local mode had no X-Frame-Options / frame-ancestors (team mode did): any web page could frame
    http://127.0.0.1:8765/ and trick the user into clicking Fill, Approve or a live Altium 365 push; the clicks are
    same-origin requests inside the frame, so the Host / Origin checks pass."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "w", providers=["digikey"])
        with _served(ws) as port, contextlib.redirect_stderr(io.StringIO()):
            for path in ("/", "/setup", "/api/info"):
                st, h, _ = _req(port, "GET", path)
                assert st == 200, (path, st)
                assert h.get("x-frame-options") == "DENY", (path, h)
                assert "frame-ancestors 'none'" in h.get("content-security-policy", ""), (path, h)


def test_deeply_nested_json_body_is_a_plain_400():
    """A 2 MB body of '[' raised RecursionError inside json.loads: the terminal got a traceback and the page "Something
    went wrong (RecursionError ...)" instead of a bad-request answer (the MCP server already treats it as a parse error)."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "w", providers=["digikey"])
        err = io.StringIO()
        with _served(ws) as port, contextlib.redirect_stderr(err):
            st, _, body = _req(port, "POST", "/api/footprint/preview", b"[" * 100000)
        msg = json.loads(body)["error"]
        assert st == 400 and "Something went wrong" not in msg and "nested" in msg, (st, msg)
        assert "Traceback" not in err.getvalue(), err.getvalue()[-500:]


def test_network_error_message_does_not_carry_the_api_key():
    """requests' ConnectionError names the URL, so Mouser's ?apiKey= reached Response.error and http.log (the triage
    report's "HTTP (this run)" list, downloadable from the UI) with the first characters of the key in the 120-char cut."""
    with tempfile.TemporaryDirectory() as d:
        h = CachedHTTP(d, min_interval=0)
        key = "S3CRETKEYabcdefghijklmnop"
        url = f"https://api.mouser.com/api/v1/search/keyword?apiKey={key}"

        def boom(method, u, **kw):
            raise requests.ConnectionError(f"HTTPSConnectionPool(host='api.mouser.com', port=443): Max retries exceeded "
                                           f"with url: /api/v1/search/keyword?apiKey={key} (Caused by NewConnectionError)")
        h.s.request = boom
        r = h.post(url, "{}", cache=False)
        assert r.status == 0 and r.error.startswith("ConnectionError"), r.error
        assert "S3C" not in r.error, r.error
        assert not any("S3C" in str(x) for e in h.log for x in e[2:]), h.log


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_1018_ui_mcp"]))
