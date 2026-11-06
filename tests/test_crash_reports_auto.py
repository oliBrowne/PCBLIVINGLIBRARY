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
def test_payload_keeps_only_frames_in_ecadlib_and_fixed_values(tmp_path, monkeypatch):
    rec = {"version": "0.4.1.dev3+g12ab", "installed": "pip", "os": "Windows-11", "python": "3.13.1",
           "where": "web UI: /api/parts/ACME-SECRET", "error": "KeyError",
           "frames": "ecadlib/app.py:12:run\nnot a frame\necadlib/ui/server.py:40:do_GET\nC:/acme/x.py:1:f",
           "traceback": '  File "ecadlib/Acme_Radar_JaneDoe.py", line 1, in leak'}
    p = B.payload(rec)
    assert [f["file"] for f in p["frames"]] == ["ecadlib/app.py", "ecadlib/ui/server.py"]
    assert p["where"] == "web UI" and p["version"] == "0.4.1"
    text = json.dumps(p)
    for leak in ("ACME", "acme", "Jane", "Radar", "dev3"):
        assert leak not in text, leak
    odd = B.payload({"os": "Call Jane 555", "where": "my project", "error": "not an error: x", "python": "3.13.0+"})
    assert odd["os"] == "Other-unknown" and odd["where"] == "command line" and odd["error"] == "Exception"
    assert odd["python"] == "3.13.0" and odd["frames"] == []
    assert B.payload({"where": "librarian"})["where"] == "librarian"           # a real command name stays


@lite
def test_frames_come_from_the_traceback_never_from_the_message(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s)
        fake = 'x\n  File "ecadlib/Acme_Radar_rev3_JaneDoe.py", line 1, in leak\nKeyError: y'
        B.record("triage", _in_ecadlib(fake), wait=True)
        raw = s.got[0]["body"].decode()
        assert "Acme" not in raw and "JaneDoe" not in raw
        assert [f["file"] for f in json.loads(raw)["frames"]] == ["ecadlib/userconfig.py"]
    finally:
        s.close()


@lite
def test_a_cleared_report_is_not_brought_back_by_a_send(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s)
        B.record("triage", _boom("x"), wait=True)
        rec, f = B.latest(), B._files()[0]
        B.clear()
        assert B.send(rec, f) and not f.exists()
    finally:
        s.close()


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
def test_ask_once_stays_quiet_unless_a_person_can_answer(tmp_path, monkeypatch):
    s = _Sentry()
    try:
        _setup(monkeypatch, tmp_path, s, choice=None)
        B.record("triage", _boom("first"))

        class Tty(io.StringIO):
            def isatty(self):
                return True
        monkeypatch.setattr(B.sys, "stdin", Tty("y\n"))
        out = io.StringIO()                                # stderr is not a terminal (redirected): no question
        B.ask_once(out)
        assert out.getvalue() == "" and B.auto_choice() is None
        monkeypatch.setenv("CI", "true")                   # a CI job never waits on a question
        B.ask_once(Tty())
        assert B.auto_choice() is None
        monkeypatch.setenv("CI", "")
        out = Tty()
        B.ask_once(out)                                    # both terminals: asked on `out`, answer read from stdin
        assert "[y/N]" in out.getvalue() and B.auto_choice() is True

        UC.save_settings({B.SETTING: None})

        def ctrl_c(_):
            raise KeyboardInterrupt
        B.ask_once(io.StringIO(), inp=ctrl_c)              # Ctrl-C: no answer, nothing saved, no traceback
        assert B.auto_choice() is None
    finally:
        s.close()


@lite
def test_a_dead_server_never_holds_up_the_exit(tmp_path):
    """A server that takes the connection and never answers: the command still ends within SEND_DEADLINE."""
    import os, socket, subprocess, sys, time
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    try:
        env = dict(os.environ, ECADLIB_HOME=str(tmp_path), ECADLIB_NO_CRASH_REPORTS="", DO_NOT_TRACK="",
                   ECADLIB_REPORT_URL=f"http://127.0.0.1:{srv.getsockname()[1]}/x")
        code = ("from ecadlib import bugreport as B, userconfig as UC\nB.set_auto(True)\n"
                "try:\n    UC.check_name('bad name ä')\nexcept ValueError as e:\n    B.record('triage', e)\n")
        t = time.monotonic()
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
        took = time.monotonic() - t
        assert r.returncode == 0, r.stderr
        assert took < B.SEND_DEADLINE + 2.5, took
    finally:
        srv.close()


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
