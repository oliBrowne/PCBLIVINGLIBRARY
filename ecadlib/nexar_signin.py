"""Sign in to Altium 365 once and stay signed in: your own free Nexar app, then ecadlib refreshes the token itself.

    ecadlib nexar signin        asks for your Nexar app's client id and secret (once), opens the browser to sign in
    ecadlib nexar status        signed in or not, when the current access token runs out (never the token itself)
    ecadlib nexar signout       forgets the sign-in (the app's id and secret stay stored)

Why your own app: a Nexar portal token lasts 24 hours, so pasting one means doing it again every day. Nexar only
accepts confidential apps (identity.nexar.com lists client_secret_basic / client_secret_post as its only client
authentication), so ecadlib cannot ship one shared "Sign in with Altium" app: its secret would be public. Each user
makes a free app on portal.nexar.com (Design API: free, no quota) and gives ecadlib its id and secret once.

How (checked against identity.nexar.com/.well-known/openid-configuration, 2026-10-20): authorization code with PKCE
(S256) on a redirect to this computer (default http://localhost:3000/login, NEXAR_REDIRECT_URI changes it; the
same address must be listed as a redirect URI of the app), scopes openid user.access design.domain offline_access.
offline_access gives a refresh token; ecadlib swaps it for a new access token when the current one is within 5
minutes of running out, and keeps the new refresh token when Nexar rotates it.

Where: everything goes into ecadlib's own key store (userconfig.py, DPAPI-encrypted on Windows): the app as
NEXAR_CLIENT_ID / NEXAR_CLIENT_SECRET (the same names the supply lookups use, so one app can do both), the sign-in
as NEXAR_SIGNIN (refresh token, current access token, its expiry). Nothing goes into the environment, a config file
or a log. userconfig.secret("NEXAR_TOKEN") answers from the sign-in first, so every Altium 365 command and the
Altium menu use it without change; a pasted NEXAR_TOKEN still works when there is no sign-in."""
import argparse, base64, getpass, hashlib, json, secrets, socket, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import userconfig as UC

AUTH_URL = "https://identity.nexar.com/connect/authorize"
TOKEN_URL = "https://identity.nexar.com/connect/token"
SCOPES = "openid user.access design.domain offline_access"
REDIRECT = "http://localhost:3000/login"
STORE = "NEXAR_SIGNIN"
MARGIN = 300                      # refresh this many seconds before the access token runs out
RETRY = 60                        # after a failed refresh, wait this long before asking Nexar again (offline UI polls)
last_error = None                 # why the last access_token() came back empty (no secrets in it)
_failed = {}                      # refresh token -> (time of the failed refresh, why)


class SigninError(Exception):
    pass


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce():
    """(verifier, S256 challenge)."""
    verifier = _b64(secrets.token_bytes(48))
    return verifier, _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def redirect_uri(environ=None):
    import os
    env = os.environ if environ is None else environ
    return (env.get("NEXAR_REDIRECT_URI") or REDIRECT).strip()


def parse_redirect(uri):
    """(host, port, path) of a redirect ecadlib can receive itself: http, this computer, an explicit port."""
    u = urllib.parse.urlsplit(uri)
    if u.scheme != "http" or u.hostname not in ("localhost", "127.0.0.1", "::1") or not u.port:
        raise SigninError(f"{uri}: the redirect URI must be http://localhost:<port>/<path> (this computer)")
    return u.hostname, u.port, u.path or "/"


def authorize_url(client_id, redirect, state, challenge):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": SCOPES,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def _post(url, data, timeout=15):
    import requests
    return requests.post(url, data=data, timeout=timeout)


def _token_request(form, post=None):
    """POST to the token endpoint; the token response, or SigninError with Nexar's reason (never the secret)."""
    try:
        r = (post or _post)(TOKEN_URL, data=form, timeout=15)
    except Exception as e:                                      # no network, proxy, DNS
        raise SigninError(f"could not reach identity.nexar.com ({type(e).__name__})")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not r.ok or not body.get("access_token"):
        why = body.get("error") or f"HTTP {r.status_code}"
        if body.get("error_description"):
            why += f": {body['error_description']}"
        raise SigninError(f"Nexar refused the token request ({why})")
    return body


def _load():
    try:
        v = json.loads(UC.get_key(STORE) or "null")
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _save(tok, now=None, refresh=None):
    """Store a token response: access token, its expiry, and the refresh token (the old one when none came back)."""
    now = time.time() if now is None else now
    blob = {"access_token": tok["access_token"], "expires_at": int(now + int(tok.get("expires_in") or 3600)),
            "refresh_token": tok.get("refresh_token") or refresh, "scope": tok.get("scope") or SCOPES}
    UC.set_key(STORE, json.dumps(blob, separators=(",", ":")))
    return blob


def wait_for_code(redirect, state, timeout=300, ready=None):
    """Listen on the redirect address (this computer only) until the browser comes back; returns the code."""
    host, port, path = parse_redirect(redirect)
    got, done = {}, threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path != path:
                self.send_error(404)
                return
            if not done.is_set():
                got.update(urllib.parse.parse_qsl(u.query))
                done.set()
            ok = "code" in got and got.get("state") == state
            body = ("<h2>ecadlib is signed in to Altium 365.</h2><p>You can close this tab.</p>" if ok else
                    "<h2>ecadlib could not sign in.</h2><p>Go back to ecadlib for the reason.</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):                              # the query holds the code: never log it
            pass

    class Server6(HTTPServer):
        address_family = socket.AF_INET6
    servers = []
    for addr in (["127.0.0.1", "::1"] if host == "localhost" else [host]):   # "localhost" may resolve to either
        try:
            servers.append((Server6 if ":" in addr else HTTPServer)((addr, port), Handler))
        except OSError:
            continue
    if not servers:
        raise SigninError(f"port {port} on this computer is in use: close what uses it, or set NEXAR_REDIRECT_URI "
                          "to another http://localhost:<port>/login (and add it to your Nexar app)")
    threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        if ready:
            ready()
        if not done.wait(timeout):
            raise SigninError(f"no answer from the browser in {timeout} s: run `ecadlib nexar signin` again")
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
    if got.get("error"):
        raise SigninError(f"the sign-in was not completed ({got['error']}"
                          + (f": {got['error_description']}" if got.get("error_description") else "") + ")")
    if got.get("state") != state:
        raise SigninError("the browser came back with the wrong state (not this sign-in): nothing was stored")
    if not got.get("code"):
        raise SigninError("the browser came back without a code")
    return got["code"]


def signin(client_id, client_secret, open_browser=None, redirect=None, post=None, now=None, timeout=300, out=print):
    """The one-time browser sign-in. Stores the app and the tokens; returns {"refresh": bool, "expires_at": ...}."""
    import webbrowser
    redirect = redirect or redirect_uri()
    parse_redirect(redirect)
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    url = authorize_url(client_id, redirect, state, challenge)

    def ready():
        out("Sign in to Altium 365 in your browser and allow access. If no browser opens, open this address:")
        out("  " + url)
        (open_browser or webbrowser.open)(url)
    code = wait_for_code(redirect, state, timeout=timeout, ready=ready)
    tok = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                          "code_verifier": verifier, "client_id": client_id, "client_secret": client_secret}, post)
    UC.set_key("NEXAR_CLIENT_ID", client_id)
    UC.set_key("NEXAR_CLIENT_SECRET", client_secret)
    blob = _save(tok, now)
    return {"refresh": bool(blob["refresh_token"]), "expires_at": blob["expires_at"]}


def access_token(post=None, now=None):
    """A current access token from the sign-in (refreshed when it is about to run out), or None: not signed in, or
    the refresh failed (last_error says why)."""
    global last_error
    last_error = None
    blob = _load()
    if not blob:
        return None
    now = time.time() if now is None else now
    if blob.get("access_token") and blob.get("expires_at", 0) - MARGIN > now:
        return blob["access_token"]
    if not blob.get("refresh_token"):
        last_error = ("the Altium 365 sign-in ran out and Nexar gave no refresh token (allow offline access for your "
                      "Nexar app): run `ecadlib nexar signin` again")
        return None
    seen = _failed.get(blob["refresh_token"])
    if seen and now - seen[0] < RETRY:
        last_error = seen[1]
        return None
    cid, sec = UC.secret("NEXAR_CLIENT_ID"), UC.secret("NEXAR_CLIENT_SECRET")
    if not (cid and sec):
        last_error = "NEXAR_CLIENT_ID / NEXAR_CLIENT_SECRET are not stored: run `ecadlib nexar signin`"
        return None
    try:
        tok = _token_request({"grant_type": "refresh_token", "refresh_token": blob["refresh_token"],
                              "client_id": cid, "client_secret": sec}, post)
    except SigninError as e:
        again = _load() or {}                     # another ecadlib may have refreshed (and rotated) it meanwhile
        if again.get("access_token") and again.get("expires_at", 0) - MARGIN > now:
            return again["access_token"]
        last_error = f"could not refresh the Altium 365 sign-in: {e}; run `ecadlib nexar signin` again"
        _failed[blob["refresh_token"]] = (now, last_error)
        return None
    return _save(tok, now, refresh=blob["refresh_token"])["access_token"]


def hint():
    """What to do when there is no Altium 365 token: why the sign-in failed, else how to sign in."""
    return last_error or ("sign in once with `ecadlib nexar signin` (your own free Nexar app; ecadlib keeps it signed "
                          "in), or store a 24-hour Nexar portal token on the setup page or with `ecadlib keys set NEXAR_TOKEN`")


def signed_in():
    return bool(_load())


def signout():
    return UC.remove_key(STORE)


# ------------------------------------------------------------------ ecadlib nexar
def main(argv=None, inp=None, out=print):
    ap = argparse.ArgumentParser(prog="ecadlib nexar", description="Altium 365 sign-in with your own Nexar app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("signin", help="sign in once in the browser; ecadlib keeps the sign-in fresh by itself")
    s.add_argument("--new-app", action="store_true", help="ask for the app's client id and secret again")
    s.add_argument("--no-browser", action="store_true", help="only print the address to open")
    s.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser (default 300)")
    sub.add_parser("status", help="signed in or not (never shows a token)")
    sub.add_parser("signout", help="forget the sign-in; the app's id and secret stay stored")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        blob = _load()
        if not blob:
            out("not signed in: run `ecadlib nexar signin`" + ("  (a pasted NEXAR_TOKEN is stored; it lasts a day)"
                                                                if UC.get_key("NEXAR_TOKEN") else ""))
            return 1
        cid = UC.secret("NEXAR_CLIENT_ID") or ""
        exp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(blob.get("expires_at", 0)))
        out(f"signed in to Altium 365 with Nexar app ...{cid[-4:]}; access token valid until {exp}")
        out("ecadlib refreshes it by itself" if blob.get("refresh_token") else
            "no refresh token: sign in again when it runs out (allow offline access for your Nexar app)")
        return 0
    if a.cmd == "signout":
        out("signed out of Altium 365" if signout() else "was not signed in")
        return 0
    ask = inp or input
    cid = None if a.new_app else UC.secret("NEXAR_CLIENT_ID")
    sec = None if a.new_app else UC.secret("NEXAR_CLIENT_SECRET")
    try:
        if not (cid and sec):
            out("Your Nexar app (portal.nexar.com > your app > Authorization): its client id and secret, asked once.")
            cid = (ask("Client ID: ") or "").strip()
            sec = ((inp or getpass.getpass)("Client secret (input hidden): ") or "").strip()
            if not (cid and sec):
                raise SigninError("both the client id and the secret are needed")
        got = signin(cid, sec, open_browser=(lambda url: True) if a.no_browser else None, timeout=a.timeout, out=out)
    except (SigninError, EOFError, ValueError) as e:
        print(f"ecadlib nexar signin: {e}", file=sys.stderr)
        return 2
    UC.set_key("NEXAR_CLIENT_ID", cid)
    UC.set_key("NEXAR_CLIENT_SECRET", sec)
    out("signed in to Altium 365; " + ("ecadlib keeps the sign-in fresh by itself" if got["refresh"] else
        "Nexar gave no refresh token, so this lasts until the access token runs out (allow offline access for your app)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
