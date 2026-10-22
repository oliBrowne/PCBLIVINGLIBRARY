"""Signing in to Altium 365 with the user's own Nexar app (ecadlib.nexar_signin): the browser sign-in once (authorization
code + PKCE on a localhost redirect), then ecadlib refreshes the access token by itself, so nobody pastes a 24-hour
portal token every day. Offline: a fake token endpoint and a fake browser that follows the redirect. Run:
    python3 -m tests.test_nexar_signin"""
import base64, hashlib, io, json, os, threading, urllib.parse, urllib.request
from contextlib import redirect_stdout

from ecadlib import nexar_signin as NS, userconfig as UC
from tests.fixtures_lite import lite


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    NS._failed.clear()
    for k in ("NEXAR_TOKEN", "NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET", UC.NO_STORED, "NEXAR_REDIRECT_URI"):
        if k in os.environ:
            monkeypatch.setenv(k, "")
            del os.environ[k]
    UC._CACHE.clear()


class FakeIdentity:
    """identity.nexar.com's token endpoint: remembers every form it got, answers from a script of replies."""
    def __init__(self, *replies):
        self.replies, self.forms = list(replies), []

    def __call__(self, url, data=None, timeout=None, **kw):
        assert url == NS.TOKEN_URL, url
        self.forms.append(dict(data))
        status, body = self.replies.pop(0)
        return _Resp(status, body)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _browser(code="CODE1", state=None, error=None):
    """Opens the authorize URL like a browser whose user clicks Allow: goes straight to the redirect with a code."""
    seen = {}

    def open_url(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        seen.update(q)
        back = {"error": error} if error else {"code": code, "state": state or q["state"]}

        def go():
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # localhost: never a proxy
            direct.open(q["redirect_uri"] + "?" + urllib.parse.urlencode(back), timeout=10).read()
        threading.Thread(target=go, daemon=True).start()
        return True
    return open_url, seen


def _free_redirect():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://localhost:{port}/login"


@lite
def test_signin_uses_pkce_and_stores_refresh_token(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    idp = FakeIdentity((200, {"access_token": "AT1", "expires_in": 86400, "refresh_token": "RT1", "token_type": "Bearer"}))
    open_url, seen = _browser()
    redirect = _free_redirect()
    with redirect_stdout(io.StringIO()):
        got = NS.signin("my-app", "s3cret", open_browser=open_url, redirect=redirect, post=idp, now=1000, timeout=20)
    assert got["refresh"] is True
    # the authorize request: code flow, S256 challenge, the scopes the Design API needs plus offline_access
    assert seen["response_type"] == "code" and seen["client_id"] == "my-app" and seen["redirect_uri"] == redirect
    assert seen["code_challenge_method"] == "S256"
    assert set(seen["scope"].split()) == {"openid", "user.access", "design.domain", "offline_access"}
    # the code exchange proves the verifier behind that challenge, with the app's secret (Nexar wants confidential apps)
    form = idp.forms[0]
    assert form["grant_type"] == "authorization_code" and form["code"] == "CODE1" and form["redirect_uri"] == redirect
    assert form["client_id"] == "my-app" and form["client_secret"] == "s3cret"
    digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert digest == seen["code_challenge"]
    # stored in the key store, never as plain text in the environment
    assert "NEXAR_TOKEN" not in os.environ
    assert NS.access_token(now=2000) == "AT1"
    assert UC.secret("NEXAR_CLIENT_ID") == "my-app" and UC.secret("NEXAR_CLIENT_SECRET") == "s3cret"


@lite
def test_signin_rejects_wrong_state(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    idp = FakeIdentity()
    open_url, _ = _browser(state="forged")
    try:
        with redirect_stdout(io.StringIO()):
            NS.signin("my-app", "s3cret", open_browser=open_url, redirect=_free_redirect(), post=idp, timeout=20)
    except NS.SigninError as e:
        assert "state" in str(e)
    else:
        raise AssertionError("a forged state was accepted")
    assert idp.forms == [] and UC.get_key(NS.STORE) is None


@lite
def test_signin_reports_denied_consent(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    open_url, _ = _browser(error="access_denied")
    try:
        with redirect_stdout(io.StringIO()):
            NS.signin("my-app", "s3cret", open_browser=open_url, redirect=_free_redirect(), post=FakeIdentity(), timeout=20)
    except NS.SigninError as e:
        assert "access_denied" in str(e)
    else:
        raise AssertionError("a denied sign-in passed")


@lite
def test_expired_access_token_is_refreshed_and_rotated(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_CLIENT_ID", "my-app")
    UC.set_key("NEXAR_CLIENT_SECRET", "s3cret")
    NS._save({"access_token": "OLD", "expires_in": 3600, "refresh_token": "RT1"}, now=0)
    idp = FakeIdentity((200, {"access_token": "NEW", "expires_in": 86400, "refresh_token": "RT2"}))
    assert NS.access_token(post=idp, now=10) == "OLD"                 # still valid: no network
    assert idp.forms == []
    assert NS.access_token(post=idp, now=3600 - 60) == "NEW"          # inside the safety margin: refreshed
    assert idp.forms[0] == {"grant_type": "refresh_token", "refresh_token": "RT1",
                            "client_id": "my-app", "client_secret": "s3cret"}
    assert json.loads(UC.get_key(NS.STORE))["refresh_token"] == "RT2"  # a rotated refresh token replaces the old one


@lite
def test_refresh_keeps_old_refresh_token_when_none_returned(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_CLIENT_ID", "my-app")
    UC.set_key("NEXAR_CLIENT_SECRET", "s3cret")
    NS._save({"access_token": "OLD", "expires_in": 10, "refresh_token": "RT1"}, now=0)
    idp = FakeIdentity((200, {"access_token": "NEW", "expires_in": 86400}))
    assert NS.access_token(post=idp, now=1000) == "NEW"
    assert json.loads(UC.get_key(NS.STORE))["refresh_token"] == "RT1"


@lite
def test_failed_refresh_says_why_and_falls_back_to_a_pasted_token(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_CLIENT_ID", "my-app")
    UC.set_key("NEXAR_CLIENT_SECRET", "s3cret")
    NS._save({"access_token": "OLD", "expires_in": 10, "refresh_token": "RT1"}, now=0)
    idp = FakeIdentity((400, {"error": "invalid_grant"}))
    assert NS.access_token(post=idp, now=1000) is None
    assert "invalid_grant" in NS.last_error and "ecadlib nexar signin" in NS.last_error
    assert "s3cret" not in NS.last_error
    assert NS.access_token(post=idp, now=1010) is None and "invalid_grant" in NS.last_error   # no second call for a while
    UC.set_key("NEXAR_TOKEN", "PASTED")                               # the old pasted-token route still works
    monkeypatch.setattr(NS, "access_token", lambda **kw: None)
    assert UC.secret("NEXAR_TOKEN") == "PASTED"


@lite
def test_secret_nexar_token_comes_from_the_signin(tmp_path, monkeypatch):
    """Every A365 command reads UC.secret("NEXAR_TOKEN"); a sign-in answers it, ahead of a pasted (expiring) token."""
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_TOKEN", "PASTED")
    import time
    NS._save({"access_token": "FRESH", "expires_in": 86400, "refresh_token": "RT1"}, now=time.time())
    assert UC.secret("NEXAR_TOKEN") == "FRESH"
    assert UC.key_source("NEXAR_TOKEN") == "stored"
    monkeypatch.setenv(UC.NO_STORED, "1")                             # the eval gate never reads stored keys
    assert UC.secret("NEXAR_TOKEN") is None


@lite
def test_no_signin_means_no_change(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert NS.access_token() is None and NS.last_error is None
    monkeypatch.setenv("NEXAR_TOKEN", "FROM_ENV")
    assert UC.secret("NEXAR_TOKEN") == "FROM_ENV"


@lite
def test_redirect_must_be_this_computer(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    for bad in ("https://example.com/login", "http://localhost/login", "http://10.0.0.5:3000/login"):
        try:
            NS.parse_redirect(bad)
        except NS.SigninError:
            continue
        raise AssertionError(f"{bad} was accepted")
    assert NS.parse_redirect("http://localhost:3000/login") == ("localhost", 3000, "/login")


@lite
def test_cli_status_and_signout_never_print_tokens(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_CLIENT_ID", "my-app-1234")
    UC.set_key("NEXAR_CLIENT_SECRET", "s3cret")
    import time
    NS._save({"access_token": "SECRET_AT", "expires_in": 86400, "refresh_token": "SECRET_RT"}, now=time.time())
    out = []
    assert NS.main(["status"], out=out.append) == 0
    text = "\n".join(out)
    assert "signed in" in text and "SECRET" not in text and "s3cret" not in text
    out.clear()
    assert NS.main(["signout"], out=out.append) == 0
    assert UC.get_key(NS.STORE) is None and UC.secret("NEXAR_CLIENT_ID") == "my-app-1234"  # the app stays, the sign-in goes
    out.clear()
    assert NS.main(["status"], out=out.append) == 1
    assert "not signed in" in "\n".join(out)


@lite
def test_cli_signin_asks_for_the_app_once(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    calls = {}

    def fake_signin(cid, secret, **kw):
        calls.update(cid=cid, secret=secret)
        return {"refresh": True, "expires_at": 0}
    monkeypatch.setattr(NS, "signin", fake_signin)
    answers = iter(["my-app", "s3cret"])
    out = []
    assert NS.main(["signin", "--no-browser"], inp=lambda prompt: next(answers), out=out.append) == 0
    assert calls == {"cid": "my-app", "secret": "s3cret"}
    assert UC.secret("NEXAR_CLIENT_ID") == "my-app"
    assert NS.main(["signin", "--no-browser"], inp=lambda p: (_ for _ in ()).throw(AssertionError("asked again")),
                   out=out.append) == 0


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_nexar_signin"]))


@lite
def test_a_same_size_rewrite_in_one_clock_tick_is_read_back(tmp_path, monkeypatch):
    # Windows: keys.json rewritten within one mtime tick at the same size kept the old stamp, so the old value came back
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_CLIENT_ID", "aaaa")
    f = UC.config_dir() / UC.KEYS
    st = f.stat()
    assert UC.get_key("NEXAR_CLIENT_ID") == "aaaa"
    UC.set_key("NEXAR_CLIENT_ID", "bbbb")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert f.stat().st_size == st.st_size
    assert UC.get_key("NEXAR_CLIENT_ID") == "bbbb"
    UC.remove_key("NEXAR_CLIENT_ID")
    assert UC.get_key("NEXAR_CLIENT_ID") is None
