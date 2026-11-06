"""Automatic crash reports (opt-in): with the person's yes, each saved bug is also sent, cut down to what is safe to
publish (error type, the lines in ecadlib's own code, versions; never the message), to the project's endpoint, which
files it as a public GitHub issue. Off until they say yes; never in the tests' own runs. A local HTTP server stands in
for the endpoint. Run:  python3 -m tests.test_crash_reports_auto"""
import io, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.fixtures_lite import lite
from ecadlib import bugreport as B, userconfig as UC


def _in_ecadlib(text):
    """An error raised inside ecadlib's own code (its frames are what gets sent), carrying text."""
    try:
        UC.check_name(text)
    except ValueError as e:
        return e
    raise AssertionError("check_name took " + text)


def _boom(msg="boom"):
    try:
        raise RuntimeError(msg)
    except RuntimeError as e:
        return e


class _Sentry:
    """Records what the crash-report endpoint receives."""
    def __init__(self, status=200):
        self.got, outer = [], self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.got.append({"path": self.path, "ctype": self.headers.get("Content-Type", ""), "body": body})
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.dsn = f"http://127.0.0.1:{self.srv.server_port}/api/crash-report"

    def close(self):
        self.srv.shutdown()


def _setup(monkeypatch, tmp_path, sentry, choice="on"):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    monkeypatch.setenv(B.OFF_ENV, "")                     # tests/__init__ turns sending off for every other test
    monkeypatch.setenv("DO_NOT_TRACK", "")
    monkeypatch.setenv(B.URL_ENV, sentry.dsn if sentry else "")
    if choice:
        B.set_auto(choice == "on")


@lite
def test_off_until_the_person_says_yes(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s, choice=None)
        assert B.auto_choice() is None and not B.auto_on()
        B.record("triage", _boom("not asked yet"), wait=True)
        B.set_auto(False)
        B.record("triage", _boom("said no"), wait=True)
        assert s.got == []
    finally:
        s.close()


@lite
def test_sends_only_the_publishable_part(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECRETSECRETSECRET123456")
        B.record("librarian", _in_ecadlib("failed on ACME-SECRET-PROJECT for me@example.com"), wait=True)
        assert len(s.got) == 1
        g = s.got[0]
        assert g["path"] == "/api/crash-report" and g["ctype"].startswith("application/json")
        raw = g["body"].decode()
        for leak in ("SECRETSECRET", "me@example.com", "ACME", "failed on"):
            assert leak not in raw, leak                       # the message never leaves the computer
        p = json.loads(raw)
        assert set(p) == {"schema", "version", "installed", "os", "python", "where", "error", "frames"}
        assert p["error"] == "ValueError" and p["where"] == "librarian" and p["version"]
        assert p["frames"] and all(set(f) == {"file", "line", "function"} and f["file"] == "ecadlib/userconfig.py"
                                   for f in p["frames"])
        assert B.latest()["sent"] == "yes"
    finally:
        s.close()


@lite
def test_payload_keeps_only_frames_in_ecadlib_and_safe_characters(tmp_path, monkeypatch):
    rec = {"version": "0.4.1", "installed": "pip", "os": "Windows-11", "python": "3.13.1", "where": "web UI: /api/x",
           "error": "KeyError", "traceback": "\n".join([
               "Traceback (most recent call last):",
               '  File "ecadlib/app.py", line 12, in run',
               "    x = y['pins']",
               '  File "<python>/json/decoder.py", line 3, in decode',
               '  File "~/projects/acme/script.py", line 9, in <module>',
               '  File "ecadlib\\ui\\server.py", line 40, in do_GET',
               "KeyError: 'pins'"])}
    p = B.payload(rec)
    assert [f["file"] for f in p["frames"]] == ["ecadlib/app.py", "ecadlib/ui/server.py"]
    assert "pins" not in json.dumps(p) and "acme" not in json.dumps(p)


@lite
def test_switches_that_stop_sending(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        for env in (B.OFF_ENV, "DO_NOT_TRACK"):
            _setup(monkeypatch, tmp_path, s)
            monkeypatch.setenv(env, "1")
            assert not B.auto_on()
            B.record("triage", _boom(f"blocked by {env}"), wait=True)
        _setup(monkeypatch, tmp_path, None)                  # a build with no Sentry address: nothing to send to
        assert not B.auto_on() and not B.available()
        B.record("triage", _boom("no dsn"), wait=True)
        assert s.got == []
    finally:
        s.close()


@lite
def test_daily_limit_and_a_dead_server_never_break_anything(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s)
        monkeypatch.setattr(B, "MAX_SENT_PER_DAY", 2)
        for i in range(4):
            B.record("triage", _boom(f"crash {i}"), wait=True)
        assert len(s.got) == 2
    finally:
        s.close()
    _setup(monkeypatch, tmp_path / "dead", s)                # the server is gone now: refused, no exception
    assert B.record("triage", _boom("nobody listens"), wait=True) is not None
    assert not B.latest().get("sent")


@lite
def test_cli_asks_once_after_a_crash_and_remembers(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s, choice=None)
        B.record("triage", _boom("first"))
        out = io.StringIO()
        B.ask_once(out, inp=lambda _: "y")
        assert B.auto_choice() is True and "automatically" in out.getvalue()
        assert len(s.got) == 1 and json.loads(s.got[0]["body"])["where"] == "triage"   # the crash just asked about goes too
        out = io.StringIO()
        B.ask_once(out, inp=lambda _: 1 / 0)                  # asked once only
        assert out.getvalue() == ""
        UC.save_settings({B.SETTING: None})
        B.ask_once(io.StringIO(), inp=lambda _: "")           # Enter = no
        assert B.auto_choice() is False and len(s.got) == 1
    finally:
        s.close()


@lite
def test_report_bug_auto_switch(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s, choice=None)
        out = io.StringIO()
        assert B.main(["--auto", "on"], out=out) == 0 and B.auto_choice() is True
        assert B.main(["--auto", "off"], out=out) == 0 and B.auto_choice() is False
        out = io.StringIO()
        B.main(["--auto", "status"], out=out)
        assert "off" in out.getvalue()
    finally:
        s.close()


@lite
def test_setup_page_shows_and_saves_the_choice(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s, choice=None)
        from ecadlib import easy_setup as ES
        st = ES.state(tmp_path / "ws")["crash_reports"]
        assert st == {"available": True, "on": False, "asked": False}
        ES.POST_ROUTES["/api/setup/crash-reports"](tmp_path / "ws", {"on": True})
        assert ES.state(tmp_path / "ws")["crash_reports"]["on"] is True
        page = (ES.Path(ES.__file__).parent / "ui" / "static" / "setup.html").read_text(encoding="utf-8")
        assert 'id="c-crash"' in page and "/api/setup/crash-reports" in page
    finally:
        s.close()


def test_the_test_suite_itself_never_sends():
    import os
    assert os.environ.get(B.OFF_ENV) == "1"
