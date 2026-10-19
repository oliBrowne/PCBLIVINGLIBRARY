"""Regression tests for the full review of the front ends (web UI server, MCP server, first run).
Run:  python3 -m tests.test_review_full_frontends"""
import contextlib, http.client, io, json, os, socket, sys, tempfile, threading
from pathlib import Path
from urllib.parse import quote

from ecadlib import firstrun as FR, mcp_server as M
from ecadlib.app import Workspace
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


def test_download_of_a_file_whose_name_is_not_latin1():
    """/api/file used to put the raw name in Content-Disposition: a name such as R_Ω.PcbLib (or a Chinese BOM's
    reports) can't be written as a latin-1 header, so the answer was a 200 with a mangled header block and body."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        (ws.root / "altium_models" / "PcbLib" / "R_Ω.PcbLib").write_bytes(b"pcblib")
        files = [("altium_models/PcbLib/R_Ω.PcbLib", b"pcblib")]
        if os.name != "nt":                                  # Windows file names can't hold a double quote
            (ws.root / "reports" / 'a"b.md').write_bytes(b"report")
            files.append(('reports/a"b.md', b"report"))
        with contextlib.redirect_stderr(io.StringIO()), _served(ws) as port:
            for rel, body in files:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request("GET", "/api/file?path=" + quote(rel))
                r = c.getresponse()
                assert r.status == 200 and r.read() == body, rel
                cd = r.getheader("Content-Disposition")
                assert cd.startswith('attachment; filename="') and cd.count('"') == 2, cd
                assert f"filename*=UTF-8''{quote(rel.split('/')[-1])}" in cd, cd


def test_ipv6_host_binds_an_ipv6_socket():
    """`ecadlib ui --host ::1` (the help names it) used to fail with 'Address family for hostname not supported':
    the server always made an IPv4 socket."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        try:
            srv = S.make_server(ws, "::1", 0)
        except socket.gaierror as e:
            raise AssertionError(f"::1 was resolved for an IPv4 socket: {e}")
        except OSError:                                    # this machine has no IPv6 at all
            return
        try:
            assert srv.address_family == socket.AF_INET6
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            port = srv.server_address[1]
            c = http.client.HTTPConnection("::1", port, timeout=10)
            with contextlib.redirect_stderr(io.StringIO()):
                c.request("GET", "/api/info", headers={"Host": f"[::1]:{port}"})
                r = c.getresponse()
            assert r.status == 200 and isinstance(json.loads(r.read()), dict)
        finally:
            srv.shutdown()
            srv.server_close()


def test_mcp_answer_with_an_undecodable_file_name_keeps_the_server_running():
    """A path the server itself listed (a file name that is not UTF-8 comes back as a \\udcXX escape) sent back in a
    call: the error text holds a lone surrogate, and encoding the answer as UTF-8 raised and stopped the server."""
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "triage_bom", "arguments": {"bom": "uploads/St\udcfcckliste.csv"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
        out = io.BytesIO()
        with contextlib.redirect_stderr(io.StringIO()):
            M.serve(ws, io.BytesIO("".join(json.dumps(m) + "\n" for m in msgs).encode()), out)
        lines = [json.loads(l) for l in out.getvalue().decode("utf-8").splitlines()]
        assert [l["id"] for l in lines] == [1, 2], lines
        assert lines[0]["result"]["isError"] and "St\udcfcckliste.csv" in lines[0]["result"]["content"][0]["text"]


def test_demo_and_rules_work_without_tomllib():
    """Python 3.10 (requires-python >= 3.10) has no tomllib: the demo wrote ecadlib.toml and then could not read it
    ("reading TOML needs Python 3.11+"), and `ecadlib init` failed the same way. tomli is now a 3.10 dependency."""
    try:
        import tomli  # noqa: F401
    except ImportError:
        print("skip  tomli not installed")
        return
    saved = sys.modules.get("tomllib", "absent")
    sys.modules["tomllib"] = None                          # `import tomllib` now raises ImportError, as on 3.10
    try:
        from ecadlib import rules as RU
        assert RU.parse_text('[sources]\norder = ["standard"]\n')["sources"]["order"] == ["standard"]
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(Path(d) / "demo", offline=True)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                res = FR.build_demo(ws, "altium", log=lambda *a: None)
                again = FR.build_demo(ws, "altium", log=lambda *a: None)       # re-running reuses the folder
            assert (ws.root / "ecadlib.toml").is_file()
            assert ws.rules()["sources"]["order"] == ["standard"]
            assert res["parts"] == again["parts"] == 7 and res["ready"] == 6, res
    finally:
        if saved == "absent":
            sys.modules.pop("tomllib", None)
        else:
            sys.modules["tomllib"] = saved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
