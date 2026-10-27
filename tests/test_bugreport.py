"""Bug reports: errors are saved on this computer with secrets and personal paths blanked out, and
`ecadlib report-bug` / the web UI turn the last one into a GitHub issue the user reads before sending.
Nothing is sent by ecadlib itself."""
import io, json, os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tests.fixtures_lite import lite
from ecadlib import bugreport as B


def _boom(msg="boom"):
    try:
        raise RuntimeError(msg)
    except RuntimeError as e:
        return e


@lite
def test_scrub_blanks_api_keys_tokens_and_emails(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    text = ("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz012345 and sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZ "
            "gemini AIzaSyA1234567890abcdefghijklmnopqrstu github ghp_abcdefghijklmnopqrstuvwxyz0123456789 "
            "Authorization: Bearer eyJhbGciOi.payload.sig url https://api.x.com/p?apikey=SECRETVALUE&q=1 "
            "mail me@example.com")
    out = B.scrub(text)
    for bad in ("sk-ant-api03", "sk-proj-ZZZ", "AIzaSy", "ghp_abc", "eyJhbGciOi", "SECRETVALUE", "me@example.com"):
        assert bad not in out, (bad, out)
    assert "q=1" in out and "[secret]" in out and "[email]" in out


@lite
def test_scrub_blanks_the_values_of_keys_ecadlib_knows(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "plainwords123")      # no recognisable shape: blanked by value
    from ecadlib import userconfig
    userconfig.set_key("MOUSER_API_KEY", "another-plain-value-77")
    out = B.scrub("a plainwords123 b another-plain-value-77 c")
    assert "plainwords123" not in out and "another-plain-value-77" not in out


@lite
def test_scrub_hides_home_folder_and_user_name(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    home = str(Path.home())
    out = B.scrub(f'File "{home}/projects/board/bom.csv" and C:\\Users\\Jane Doe\\Desktop\\x.csv '
                  "and /home/jdoe/a and /Users/jdoe/b")
    assert home not in out or home in ("/", "")
    assert "Jane Doe" not in out and "jdoe" not in out
    assert "~" in out and "[user]" in out


@lite
def test_scrub_keeps_code_words_that_are_also_account_names(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    monkeypatch.setattr(B.getpass, "getuser", lambda: "root")
    assert "self.root" in B.scrub("self.root.joinpath(x)")
    monkeypatch.setattr(B.getpass, "getuser", lambda: "ofbro")
    assert "ofbro" not in B.scrub("owner ofbro ran it")


@lite
def test_record_saves_a_scrubbed_report_and_keeps_only_the_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    monkeypatch.setattr(B, "MAX_KEPT", 3)
    for i in range(5):
        p = B.record("triage", _boom(f"bad thing {i} sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123"))
        assert p and p.is_file()
    files = sorted((tmp_path / "crashes").glob("*.json"))
    assert len(files) == 3
    rec = B.latest()
    assert rec["where"] == "triage" and rec["error"] == "RuntimeError" and "bad thing 4" in rec["message"]
    assert "sk-ant" not in json.dumps(rec)
    assert "RuntimeError" in rec["traceback"] and rec["version"] and rec["os"] and rec["python"]


@lite
def test_record_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("ECADLIB_HOME", blocker)                      # crashes/ cannot be made under a file
    assert B.record("triage", _boom()) is None


@lite
def test_issue_url_is_prefilled_and_short_enough(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    B.record("librarian", _boom("x" * 20000))
    title, body = B.build(B.latest())
    url = B.issue_url(title, body)
    assert url.startswith(B.ISSUES_NEW) and len(url) <= B.MAX_URL
    q = parse_qs(urlparse(url).query)
    assert "librarian" in q["title"][0] and "RuntimeError" in q["title"][0]
    assert "ecadlib version" in q["body"][0] and q["labels"] == ["bug"]


@lite
def test_build_without_a_saved_error_still_gives_a_blank_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    assert B.latest() is None
    title, body = B.build(None)
    assert title and "What happened" in body and "ecadlib version" in body


@lite
def test_cli_prints_the_report_and_opens_nothing_with_print(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    B.record("triage", _boom("printed"))
    opened = []
    monkeypatch.setattr(B, "_open", opened.append)
    out = io.StringIO()
    assert B.main(["--print"], out=out) == 0
    assert "printed" in out.getvalue() and "github.com" in out.getvalue() and opened == []


@lite
def test_cli_asks_before_opening_github(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    B.record("triage", _boom())
    opened = []
    monkeypatch.setattr(B, "_open", opened.append)
    assert B.main([], out=io.StringIO(), inp=lambda _: "n") == 0 and opened == []
    assert B.main([], out=io.StringIO(), inp=lambda _: "") == 0 and len(opened) == 1
    assert B.main(["--yes"], out=io.StringIO(), inp=lambda _: 1 / 0) == 0 and len(opened) == 2


@lite
def test_cli_list_and_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    B.record("triage", _boom("one"))
    out = io.StringIO()
    assert B.main(["--list"], out=out) == 0 and "triage" in out.getvalue()
    assert B.main(["--clear"], out=io.StringIO()) == 0
    assert B.latest() is None


@lite
def test_cli_crash_is_saved_and_the_hint_names_report_bug(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    monkeypatch.setenv("ECADLIB_DEBUG", "")
    from ecadlib import cli, cliutil
    err = io.StringIO()
    code = cliutil.report("triage", _boom("cli crash"), err)
    assert code == 1 and "ecadlib report-bug" in err.getvalue()
    assert B.latest()["message"].endswith("cli crash")
    assert "report-bug" in cli.COMMANDS


@lite
def test_friendly_errors_are_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    from ecadlib import cliutil
    cliutil.report("triage", FileNotFoundError(2, "No such file", "bom.csv"), io.StringIO())
    assert B.latest() is None


@lite
def test_web_ui_saves_a_bug_and_offers_it_as_a_github_link(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    import tempfile
    from ecadlib.ui import server as S
    from tests import ui_fixture as FX
    from tests.test_ui import serve
    with tempfile.TemporaryDirectory() as d:
        ws, bom = FX.build(d)
        def broken(*a, **k):
            raise RuntimeError("status exploded")
        monkeypatch.setattr(ws, "status", broken)
        srv, call = serve(ws)
        try:
            st, r = call("GET", "/api/status")
            assert st == 400
            st, r = call("GET", "/api/bugreport")
            assert st == 200 and r["saved"] and "status exploded" in r["body"], r
            assert r["url"].startswith(B.ISSUES_NEW) and "/api/status" in r["title"]
        finally:
            srv.shutdown()
    page = (Path(__file__).resolve().parent.parent / "ecadlib" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="bugbtn"' in page and "/api/bugreport" in page and "Nothing has been sent" in page


@lite
def test_mcp_server_saves_unexpected_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    from ecadlib import mcp_server
    with __import__("contextlib").redirect_stderr(io.StringIO()):
        mcp_server._error_text(_boom("mcp bug"))
    assert B.latest()["where"] == "MCP server" and "mcp bug" in B.latest()["message"]


@lite
def test_altium_and_kicad_menu_bugs_are_saved_and_say_how_to_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    from ecadlib import altium_menu as AM
    r = AM._error(_boom("menu bug"))
    assert "report-bug" in r["fix"] and B.latest()["where"] == "Altium / KiCad menu"
    B.clear()
    AM._error(ValueError("a wrong MPN"))                              # the user's mistake, not a bug
    assert B.latest() is None
