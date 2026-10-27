"""Altium 365 on the setup page (new-user install audit, 2026-10-27): the workspace address is saved with the rest of
the setup ([altium365] workspace of the workspace's ecadlib.toml), and "Sign in to Altium 365" runs the same Nexar
sign-in as `ecadlib nexar signin`, without a terminal. Offline: the sign-in itself is a fake.
Run:  python3 -m tests.test_setup_a365"""
import json, os, threading, time
from unittest import mock

from ecadlib import easy_setup as ES, nexar_signin as NS, rules as RU, userconfig as UC
from tests.test_easy_setup import _call, _home, _server

URL = "https://acme.365.altium.com/"


def test_save_writes_the_altium365_workspace_and_state_shows_it():
    with _home() as d:
        ws = d / "ws"
        ES.save(ws, {"provider": "none", "tool": "altium", "workspace": str(ws), "a365_workspace": " " + URL + " "})
        assert RU.load(ws / "ecadlib.toml")["altium365"]["workspace"] == URL
        assert ES.state(ws)["altium365"]["workspace"] == URL
        ES.save(ws, {"provider": "none", "tool": "altium", "workspace": str(ws)})          # not sent: kept
        assert RU.load(ws / "ecadlib.toml")["altium365"]["workspace"] == URL
        ES.save(ws, {"provider": "none", "tool": "altium", "workspace": str(ws), "a365_workspace": ""})   # cleared
        assert not RU.load(ws / "ecadlib.toml")["altium365"]["workspace"]
        for bad in ("acme.365.altium.com", "http://acme.365.altium.com/", "https://", "javascript:alert(1)",
                    "https://a b.com/", 42, "https://" + "x" * 400 + ".com/"):
            try:
                ES.save(ws, {"provider": "none", "workspace": str(ws), "a365_workspace": bad})
                raise AssertionError(bad)
            except ValueError as e:
                assert "Altium 365" in str(e), e


def test_state_says_whether_signed_in_and_never_shows_the_app_secret():
    with _home() as d:
        ES._SIGNIN.clear()                                       # no sign-in started in this process (yet)
        st = ES.state(d)["altium365"]
        assert st["signed_in"] is False and st["app_stored"] is False and st["signin"] is None
        assert st["redirect"] == NS.redirect_uri()
        UC.set_key("NEXAR_CLIENT_ID", "cid-123")
        UC.set_key("NEXAR_CLIENT_SECRET", "very-secret-value")
        UC.set_key(NS.STORE, json.dumps({"access_token": "at-zzz", "expires_at": 9999999999, "refresh_token": "rt-zzz"}))
        st = ES.state(d)
        assert st["altium365"]["signed_in"] and st["altium365"]["app_stored"]
        text = json.dumps(st)
        for secret in ("very-secret-value", "at-zzz", "rt-zzz"):
            assert secret not in text


def _fake_signin(release, fail=None):
    """A stand-in for nexar_signin.signin: hands the browser address over, then waits like the real one."""
    seen = {}

    def signin(client_id, client_secret, open_browser=None, out=print, timeout=300, **kw):
        seen.update(client_id=client_id, client_secret=client_secret, timeout=timeout)
        open_browser("https://identity.nexar.com/connect/authorize?client_id=" + client_id)
        release.wait(10)
        if fail:
            raise NS.SigninError(fail)
        UC.set_key("NEXAR_CLIENT_ID", client_id)
        UC.set_key("NEXAR_CLIENT_SECRET", client_secret)
        UC.set_key(NS.STORE, json.dumps({"access_token": "at", "expires_at": 9999999999, "refresh_token": "rt"}))
        return {"refresh": True, "expires_at": 9999999999}
    return signin, seen


def _wait(pred, s=5):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_signin_starts_in_the_background_and_reports_back():
    with _home() as d:
        ES._SIGNIN.clear()
        release = threading.Event()
        fake, seen = _fake_signin(release)
        with mock.patch.object(NS, "signin", fake):
            r = ES.start_nexar_signin({"client_id": " cid-1 ", "client_secret": " sec-1 "})
            assert r["url"].startswith("https://identity.nexar.com/") and r["waiting"]
            assert seen["client_id"] == "cid-1" and seen["client_secret"] == "sec-1"
            assert ES.state(d)["altium365"]["signin"]["state"] == "waiting"
            again = ES.start_nexar_signin({})                    # pressed twice: the same sign-in, not a second one
            assert again["url"] == r["url"] and again["waiting"]
            release.set()
            assert _wait(lambda: ES.state(d)["altium365"]["signin"]["state"] == "done")
            st = ES.state(d)["altium365"]
            assert st["signed_in"] and st["app_stored"] and "signed in" in st["signin"]["message"]


def test_signin_uses_the_stored_app_and_says_why_it_failed():
    with _home() as d:
        ES._SIGNIN.clear()
        try:
            ES.start_nexar_signin({})
            raise AssertionError("no app: nothing to sign in with")
        except ValueError as e:
            assert "client ID" in str(e)
        UC.set_key("NEXAR_CLIENT_ID", "stored-cid")
        UC.set_key("NEXAR_CLIENT_SECRET", "stored-sec")
        release = threading.Event()
        fake, seen = _fake_signin(release, fail="the sign-in was not completed (access_denied)")
        with mock.patch.object(NS, "signin", fake):
            ES.start_nexar_signin({})
            assert seen["client_id"] == "stored-cid" and seen["client_secret"] == "stored-sec"
            release.set()
            assert _wait(lambda: ES.state(d)["altium365"]["signin"]["state"] == "error")
            st = ES.state(d)["altium365"]
            assert "access_denied" in st["signin"]["message"] and not st["signed_in"]
        assert ES.nexar_signout({}) == {"signed_out": False}


def test_signin_routes_need_this_page():
    with _home() as d, _server(d / "ws") as port:
        ES._SIGNIN.clear()
        release = threading.Event()
        fake, _ = _fake_signin(release)
        with mock.patch.object(NS, "signin", fake):
            st, j = _call(port, "POST", "/api/setup/nexar/signin", {"client_id": "c", "client_secret": "s"},
                          origin="http://evil.example")
            assert st == 403                                     # another site cannot start a sign-in
            st, j = _call(port, "POST", "/api/setup/nexar/signin", {"client_id": "c", "client_secret": "s"},
                          origin=f"http://127.0.0.1:{port}")
            assert st == 200 and j["url"].startswith("https://identity.nexar.com/")
            release.set()
            assert _wait(lambda: ES.state(d / "ws")["altium365"]["signed_in"])
            st, j = _call(port, "POST", "/api/setup/nexar/signout", {}, origin=f"http://127.0.0.1:{port}")
            assert st == 200 and j == {"signed_out": True} and not ES.state(d / "ws")["altium365"]["signed_in"]


def test_the_setup_page_has_the_altium365_card():
    page = (ES.Path(ES.__file__).parent / "ui" / "static" / "setup.html").read_text(encoding="utf-8")
    for needle in ('id="c-a365"', 'id="a365ws"', 'id="a365-signin"', "/api/setup/nexar/signin", "a365_workspace"):
        assert needle in page, needle


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_setup_a365"]))
