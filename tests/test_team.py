"""Team mode of the web UI (ecadlib ui --team): sign-in, sessions, CSRF, roles per route, approvals identity,
per-user jobs, TLS, the `ecadlib team` CLI. Offline; a real HTTP(S) server on an ephemeral port.
Run:  python3 -m tests.test_team"""
import contextlib, http.client, io, itertools, json, os, re, shutil, ssl, stat, subprocess, sys, tempfile, threading, time
from pathlib import Path
from unittest import mock

from ecadlib import team as TM
from ecadlib.app import Workspace
from ecadlib.ui import server as S

PW = {"ada": "admin-password-1", "lib": "librarian-pass-2", "apr": "approver-pass-3", "ap2": "approver2-pass-4",
      "vic": "viewer-password-5"}
USERS = [("ada", "admin", "Ada Admin", "ada@example.com", None), ("lib", "librarian", "Lib Rarian", "lib@example.com", None),
         ("apr", "approver", "Apr Prover", "apr@example.com", None), ("ap2", "approver", "Two Prover", "ap2@example.com", "approver,hardware"),
         ("vic", "viewer", "Vic Viewer", "", None)]


def add_users(root):
    st = TM.UserStore(root)
    for u, role, name, email, sign in USERS:
        st.add(u, role, PW[u], name, email, sign)
    return st


class Client:
    """One browser: keeps the session cookie and the CSRF token like index.html does."""
    def __init__(self, port, tls_ctx=None, host=None):
        self.port, self.ctx, self.host = port, tls_ctx, host or f"127.0.0.1:{port}"
        self.cookie, self.csrf, self.last_headers = None, None, {}

    def call(self, method, path, body=None, headers=None, csrf=True):
        if self.ctx:
            c = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=30, context=self.ctx)
        else:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": self.host, **({"Cookie": f"{S.COOKIE}={self.cookie}"} if self.cookie else {})}
        if method == "POST" and csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        self.last_headers = {k.lower(): v for k, v in r.getheaders()}
        sc = r.getheader("Set-Cookie")
        if sc:
            m = re.match(rf"{S.COOKIE}=([^;]*)", sc)
            self.cookie = m.group(1) or None if m else self.cookie
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def login(self, user, pw=None):
        st, j = self.call("POST", "/api/login", {"username": user, "password": pw or PW[user]})
        if st == 200:
            self.csrf = j["csrf"]
        return st, j


@contextlib.contextmanager
def server(ws=None, overrides=None, cert=None, key=None):
    with contextlib.ExitStack() as stack:
        if ws is None:
            d = stack.enter_context(tempfile.TemporaryDirectory())
            ws = Workspace(d, ["examples/kicad_lib"], providers=["digikey"], offline=True)
        add_users(ws.root)
        team = TM.Team(ws.root, overrides)
        srv = S.make_server(ws, "127.0.0.1", 0, team, cert, key)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield ws, team, srv.server_address[1]
        finally:
            srv.shutdown()
            srv.server_close()


def wait_job(c, jid):
    for _ in range(400):
        st, j = c.call("GET", f"/api/job?id={jid}")
        assert st == 200, j
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------ users file and passwords
def test_users_file_hashes_and_roles():
    with tempfile.TemporaryDirectory() as d:
        st = add_users(d)
        raw = Path(d, TM.USERS_FILE).read_text()
        assert not any(pw in raw for pw in PW.values())                        # never stored in clear
        assert stat.S_IMODE(os.stat(Path(d, TM.USERS_FILE)).st_mode) == 0o600
        recs = st.users()
        assert recs["ada"]["password"]["alg"] == "scrypt" and recs["ada"]["password"]["salt"] != recs["lib"]["password"]["salt"]
        assert TM.verify_password(PW["lib"], recs["lib"]["password"]) and not TM.verify_password("wrong-pass-xx", recs["lib"]["password"])
        assert not TM.verify_password(PW["lib"], None)                         # unknown user: false, after a dummy hash
        for bad in (lambda: st.add("x", "admin", "short"), lambda: st.add("../x", "viewer", "long-enough-1"),
                    lambda: st.add("y", "root", "long-enough-1"), lambda: st.add("z", "viewer", "long-enough-1", "a\nb")):
            try:
                bad()
                raise AssertionError("accepted")
            except ValueError:
                pass
        try:
            st.add("lib", "viewer", "long-enough-1")
            raise AssertionError("duplicate accepted")
        except FileExistsError:
            pass
        st.set_role("ap2", "librarian")
        assert st.get("ap2")["role"] == "librarian" and TM.sign_roles(st.get("ap2")) == ["approver", "hardware"]
        try:
            st.remove("ada")                                                    # the last admin stays
            raise AssertionError("removed last admin")
        except ValueError as e:
            assert "last admin" in str(e)
        gen = st.get("vic")["gen"]
        st.reset_password("vic", "a-new-password")
        assert st.get("vic")["gen"] == gen + 1
        assert TM.identity(st.get("lib")) == "Lib Rarian <lib@example.com>" and TM.identity(st.get("vic")) == "Vic Viewer"
        assert st.find("APR@example.com")["username"] == "apr"


def test_team_cli_bootstrap_and_ui_guards():
    with tempfile.TemporaryDirectory() as d:
        def team(*args, stdin=""):
            with mock.patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(io.StringIO()) as out:
                try:
                    return TM.main(["--workspace", d, *args]), out.getvalue()
                except SystemExit as e:
                    return e.code, out.getvalue()
        code, _ = team("add-user", "bob", "--role", "viewer", "--password-stdin", stdin="bob-password-1\n")
        assert "first user must be an admin" in str(code), code
        try:                                                                    # no admin yet: the server will not start
            S.main(["--team", "--workspace", d, "--no-browser"])
            raise AssertionError("started without an admin")
        except SystemExit as e:
            assert "no admin yet" in str(e.code) and "add-user" in str(e.code), e.code
        assert team("add-user", "ann", "--role", "admin", "--name", "Ann", "--password-stdin", stdin="ann-password-1\n")[0] == 0
        assert team("add-user", "bob", "--role", "viewer", "--password-stdin", stdin="short\n")[0] != 0
        assert team("add-user", "bob", "--role", "viewer", "--password-stdin", stdin="bob-password-1\n")[0] == 0
        assert team("set-role", "bob", "approver", "--sign-as", "hardware")[0] == 0
        code, out = team("list")
        assert code == 0 and "bob" in out and "approver" in out, out
        assert team("reset-password", "bob", "--password-stdin", stdin="bob-password-2\n")[0] == 0
        assert TM.verify_password("bob-password-2", TM.UserStore(d).get("bob")["password"])
        assert team("remove", "bob")[0] == 0 and TM.UserStore(d).get("bob") is None
        assert "last admin" in str(team("remove", "ann")[0])
        for argv, needle in ((["--team", "--host", "0.0.0.0"], "plain HTTP"),                  # TLS or an explicit opt-out
                             (["--host", "0.0.0.0"], "no login without --team"),                # local mode: unchanged refusal
                             (["--team", "--cert", "c.pem"], "both --cert and --key")):
            try:
                S.main(argv + ["--workspace", d, "--no-browser"])
                raise AssertionError(argv)
            except SystemExit as e:
                assert needle in str(e.code), (argv, e.code)
    p = subprocess.run([sys.executable, "-m", "ecadlib", "team", "-h"], capture_output=True, text=True)
    assert p.returncode == 0 and "add-user" in p.stdout, p


# ------------------------------------------------------------------ sign-in, sessions, lockout
def test_login_logout_and_session_cookie():
    with server() as (ws, team, port):
        c = Client(port)
        assert c.call("GET", "/api/info")[0] == 401
        st, s = c.call("GET", "/api/session")
        assert st == 200 and s == {"team": True, "user": None, "proxy": False}, s
        st, page = c.call("GET", "/")
        assert st == 200 and b'id="signin"' in page and c.last_headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in c.last_headers["content-security-policy"]
        st, e = c.login("lib", "not-the-password")
        assert st == 401 and e["error"] == "wrong user name or password", e
        st, e2 = c.login("nobody", "not-the-password")
        assert st == 401 and e2 == e                                            # same answer for unknown names
        st, s = c.login("LIB", PW["lib"])                                                # names are case-insensitive
        assert st == 200 and s["user"]["username"] == "lib" and s["user"]["identity"] == "Lib Rarian <lib@example.com>", s
        sc = c.last_headers["set-cookie"]
        assert "HttpOnly" in sc and "SameSite=Strict" in sc and "Path=/" in sc and "Max-Age=" in sc and "Secure" not in sc, sc
        assert c.call("GET", "/api/info")[0] == 200
        old = c.cookie
        assert c.call("POST", "/api/logout", {})[0] == 200
        assert c.cookie is None and "Max-Age=0" in c.last_headers["set-cookie"]
        c.cookie = old                                                          # the old token is dead server-side
        assert c.call("GET", "/api/info")[0] == 401
        # expiry: idle timeout
        c2 = Client(port)
        c2.login("vic")
        team.sessions.idle = 0
        time.sleep(0.01)
        assert c2.call("GET", "/api/info")[0] == 401
        team.sessions.idle = 3600
        # password reset and removal end sessions; a role change applies at once
        c3 = Client(port)
        c3.login("vic")
        team.store.set_role("vic", "librarian")
        assert c3.call("GET", "/api/session")[1]["user"]["role"] == "librarian"
        team.store.reset_password("vic", "another-password")
        assert c3.call("GET", "/api/info")[0] == 401
        c4 = Client(port)
        c4.login("apr")
        team.store.remove("apr")
        assert c4.call("GET", "/api/info")[0] == 401


def test_wrong_password_lockout_and_unlock():
    with server() as (ws, team, port):
        c = Client(port)
        for _ in range(5):
            assert c.login("lib", "wrong-password-x")[0] == 401
        st, e = c.login("lib")                                                  # right password, still locked
        assert st == 429 and "too many failed sign-ins" in e["error"] and int(c.last_headers["retry-after"]) > 0, e
        for _ in range(5):                                                      # unknown names lock the same way
            c.login("ghost", "wrong-password-x")
        assert c.login("ghost", "wrong-password-x")[0] == 429
        assert c.login("apr")[0] == 200                                         # other users are not affected
        admin = Client(port)
        admin.login("ada")
        st, u = admin.call("GET", "/api/team/users")
        assert st == 200 and u["locked"] == ["lib"], u
        assert admin.call("POST", "/api/team/users", {"action": "unlock", "username": "lib"})[0] == 200
        assert c.login("lib")[0] == 200
    with server(overrides={"max_failures_per_ip": 3}) as (ws, team, port):  # per-address limit across names
        c = Client(port)
        for n in ("a1", "a2", "a3"):
            c.login(n, "wrong-password-x")
        assert c.login("ada")[0] == 429


# ------------------------------------------------------------------ CSRF, Origin, Host
def test_csrf_origin_and_host():
    with server() as (ws, team, port):
        c = Client(port)
        c.login("lib")
        body = {"spec": json.loads(Path("ecadlib/package_specs/R_0603_1608Metric.json").read_text())}
        st, e = c.call("POST", "/api/footprint/preview", body, csrf=False)
        assert st == 403 and "CSRF" in e["error"], e
        st, e = c.call("POST", "/api/footprint/preview", body, {"X-CSRF-Token": "x" * 43})
        assert st == 403 and "CSRF" in e["error"], e
        other = Client(port)
        other.login("apr")
        assert c.call("POST", "/api/footprint/preview", body, {"X-CSRF-Token": other.csrf})[0] == 403   # not mine
        assert c.call("POST", "/api/footprint/preview", body)[0] == 200
        assert c.call("POST", "/api/footprint/preview", body, {"Origin": "https://evil.example"})[0] == 403
        assert c.call("POST", "/api/footprint/preview", body, {"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert c.call("POST", "/api/footprint/preview", body, {"Origin": f"http://127.0.0.1:{port}"})[0] == 200
        assert c.call("POST", "/api/logout", {}, csrf=False)[0] == 403        # logout needs the token too
        assert c.call("POST", "/api/login", {"username": "lib", "password": PW["lib"]}, {"Origin": "https://evil.example"})[0] == 403
        assert c.call("GET", "/api/info", headers={"Host": "evil.example"})[0] == 403        # DNS rebinding
        assert c.call("GET", "/api/info", headers={"Host": f"10.1.2.3:{port}"})[0] == 200     # IP literals are safe
    with server(overrides={"allowed_hosts": ["ecadlib.corp.example"]}) as (ws, team, port):
        c = Client(port, host="ecadlib.corp.example")
        assert c.login("vic")[0] == 200 and c.call("GET", "/api/info")[0] == 200
        assert Client(port, host="other.corp.example").call("GET", "/api/session")[0] == 403


# ------------------------------------------------------------------ authorization, table-driven over every route
QUERY = {"/api/job": "?id=nope", "/api/file": "?path=notes.txt"}
DENIED = ("needs the", "sign in first", "CSRF")


def test_every_route_has_a_role():
    """New endpoints can't skip auth: every /api path the server mentions is in ROUTES (else team mode 404s it)."""
    src = Path(S.__file__).read_text()
    mentioned = set(re.findall(r'"(/api/[a-z0-9/_-]+)"', src))
    routed = {p for _, p in S.ROUTES}
    assert mentioned <= routed, sorted(mentioned - routed)
    assert all(r == S.PUBLIC or r in TM.ROLES for r in S.ROUTES.values())
    assert {p for (m, p), r in S.ROUTES.items() if r == S.PUBLIC} == {"/", "/index.html", "/hairline.js", "/api/session", "/api/login"}


def test_role_enforcement_per_route():
    with server() as (ws, team, port):
        (ws.root / "notes.txt").write_text("hello")
        anon = Client(port)
        for (method, path), need in sorted(S.ROUTES.items()):
            url = path + QUERY.get(path, "")
            st, r = anon.call(method, url, {} if method == "POST" else None)
            if need == S.PUBLIC:
                assert st != 403 and not any(x in (r.get("error", "") if isinstance(r, dict) else "") for x in DENIED), (path, st, r)
            else:
                assert st == 401, (method, path, st, r)
            for user, role in [(u, r) for u, r, *_ in USERS if u != "ap2"]:
                c = Client(port)
                tok, s = team.sessions.create(user, team.store.get(user)["gen"])   # fresh session per call (no scrypt)
                c.cookie, c.csrf = tok, s["csrf"]
                st, r = c.call(method, url, {} if method == "POST" else None)
                err = r.get("error", "") if isinstance(r, dict) else ""
                if need != S.PUBLIC and TM.RANK[role] < TM.RANK[need]:
                    assert st == 403 and f"needs the {need} role" in err, (method, path, user, st, r)
                else:
                    assert not any(x in err for x in DENIED) and (st != 401 or path == "/api/login"), (method, path, user, st, r)
                    assert err != "not found", (method, path, user, st, r)     # every listed route is really served
        c = Client(port)
        c.login("ada")
        assert c.call("GET", "/api/nope")[0] == 404 and c.call("POST", "/api/nope", {})[0] == 404
        assert anon.call("POST", "/api/nope", {})[0] == 404


def test_viewer_is_read_only_and_live_push_needs_approver():
    with server() as (ws, team, port):
        b = ws.save_upload("b.csv", Path("examples/sample_bom.csv").read_bytes())
        v, lib, apr = Client(port), Client(port), Client(port)
        v.login("vic"), lib.login("lib"), apr.login("apr")
        writes = [("/api/upload", b"a,b\n1,2\n"), ("/api/triage", {"bom": b}),
                  ("/api/export/dblib", {"bom": b}), ("/api/rules/save", {"text": "x"}),
                  ("/api/approvals/decide", {"bom": b, "part": "X", "decision": "approve"}),
                  ("/api/team/users", {"action": "add", "username": "eve", "role": "admin", "password": "eve-password-1"})]
        for path, body in writes:
            assert v.call("POST", path, body)[0] == 403, path
        assert TM.UserStore(ws.root).get("eve") is None and ws.info()["boms"] == [b]
        st, e = lib.call("POST", "/api/altium365/push", {"bom": b, "workspace": "https://w", "dry_run": False})
        assert st == 403 and "approver" in e["error"], e
        st, e = apr.call("POST", "/api/altium365/push", {"bom": b, "workspace": "https://w", "dry_run": False})
        assert st != 403, e                                                    # allowed; fails later on NEXAR_TOKEN / plan


# ------------------------------------------------------------------ approvals identity
def test_approvals_identity_from_session():
    from tests.test_resolve_ui import workspace, HDR, SHIELD
    with workspace() as (ws, bom, plan), server(ws) as (_, team, port):
        (plan.parent / "approvals.json").write_text('{"required": 1}')
        ws.choose_part(bom, "FTSH-105-TEST", "footprint", HDR)
        ws.choose_part(bom, "USBC-FAKE", "symbol", SHIELD)                      # held on pins
        apr, ap2, lib = Client(port), Client(port), Client(port)
        apr.login("apr"), ap2.login("ap2"), lib.login("lib")
        # the approver releases it anyway; a typed name is ignored, the session's identity is recorded
        st, r = apr.call("POST", "/api/release-check", {"bom": bom, "allow": ["USBC-FAKE"], "user": "Mallory <m@evil>"})
        assert st == 200, r
        item = next(i for i in json.loads(plan.read_text()) if i["name"] == "USBC-FAKE")
        assert item["allow"]["user"] == "Apr Prover <apr@example.com>", item["allow"]
        st, e = lib.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve"})
        assert st == 403 and "approver" in e["error"], e                      # librarians don't sign off
        st, e = apr.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve",
                                                          "user": "Someone Else <x@y>"})
        assert st == 400 and "cannot approve" in e["error"], e                 # no self-approval, typed name ignored
        st, e = ap2.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve", "role": "admin"})
        assert st == 403 and "sign as approver, hardware" in e["error"], e
        st, r = ap2.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve",
                                                          "role": "hardware", "user": "Mallory <m@evil>", "comment": "ok"})
        assert st == 200 and r["decision"]["who"] == "Two Prover <ap2@example.com>" and r["decision"]["role"] == "hardware", r
        assert r["decision"]["status"] == "ready", r["decision"]
        log = [json.loads(l) for l in (plan.parent / "approvals_audit.jsonl").read_text().splitlines()]
        assert log[-1]["via"] == "ecadlib ui (team, signed in)" and log[-1]["who"] == "Two Prover <ap2@example.com>", log[-1]
        assert not any("Mallory" in json.dumps(x) or "Someone Else" in json.dumps(x) for x in log), log
        # team audit: every write with user and time; no passwords
        audit = TM.read_audit(ws.root, 1000)
        rows = [(a["user"], a["route"], a["status"]) for a in audit]
        assert ("ap2", "/api/approvals/decide", 200) in rows and ("lib", "/api/approvals/decide", 403) in rows, rows
        assert ("apr", "/api/release-check", 200) in rows and all(a["ts"] for a in audit)
        text = (ws.root / TM.AUDIT_FILE).read_text()
        assert not any(pw in text for pw in PW.values())
        st, page = apr.call("GET", "/")
        for needle in (b'id="signerwrap"', b'id="signedas"', b"/api/team/users", b'id="signin"', b"X-CSRF-Token"):
            assert needle in page, needle


# ------------------------------------------------------------------ containment and jobs
def test_path_containment_and_team_files_hidden():
    with server() as (ws, team, port):
        c = Client(port)
        c.login("ada")
        assert c.call("GET", "/api/file?path=../../etc/passwd")[0] == 400
        for f in TM.TEAM_FILES:
            (ws.root / f).touch()
            assert c.call("GET", f"/api/file?path={f}")[0] == 404, f
        assert c.call("GET", "/api/file?path=uploads/../team_users.json")[0] == 404
        st, e = c.call("POST", "/api/triage", {"bom": str(Path("examples/sample_bom.csv").resolve())})
        assert st == 400 and "outside workspace" in e["error"], e


def test_jobs_are_per_user_and_librarian_is_exclusive():
    with server() as (ws, team, port):
        b = ws.save_upload("b.csv", Path("examples/sample_bom.csv").read_bytes())
        lib, apr, ada = Client(port), Client(port), Client(port)
        lib.login("lib"), apr.login("apr"), ada.login("ada")
        st, j = lib.call("POST", "/api/triage", {"bom": b})
        assert st == 200, j
        assert apr.call("GET", f"/api/job?id={j['job']}")[0] == 404             # someone else's job
        assert ada.call("GET", f"/api/job?id={j['job']}")[0] == 200             # admins see all
        res = wait_job(lib, j["job"])
        assert res["state"] == "done" and res["owner"] == "lib", res
        gate = threading.Event()
        with mock.patch.object(ws, "librarian_run", lambda *a, **k: (gate.wait(10), {"done": True})[1]):
            st, j = lib.call("POST", "/api/librarian/run", {"bom": b})
            assert st == 200, j
            st, e = apr.call("POST", "/api/librarian/run", {"bom": b})
            assert st == 409 and "started by lib" in e["error"], e
            st, e = apr.call("POST", "/api/release-check", {"bom": b})
            assert st == 409 and "still running" in e["error"], e
            gate.set()
            assert wait_job(lib, j["job"])["state"] == "done"
        st, e = apr.call("POST", "/api/release-check", {"bom": b})
        assert st == 400 and "no librarian plan" in e["error"], e               # no longer blocked


def test_parallel_users():
    """Several signed-in users at once: reads and writes interleave, every request answered, sessions kept apart."""
    with server() as (ws, team, port):
        clients = {u: Client(port) for u in ("lib", "apr", "vic", "ada")}
        for u, c in clients.items():
            c.login(u)
        spec = json.loads(Path("ecadlib/package_specs/R_0603_1608Metric.json").read_text())
        errors = []

        def work(u, c, i):
            try:
                for n in range(5):
                    st, s = c.call("GET", "/api/session")
                    assert st == 200 and s["user"]["username"] == u, s
                    st, _ = c.call("POST", "/api/footprint/preview", {"spec": spec})
                    assert st == 200
                    if u == "lib":
                        st, r = c.call("POST", "/api/footprint/approve", {"spec": dict(spec, name=f"R_T{i}_{n}")})
                        assert st == 200, r
            except Exception as e:                              # noqa: BLE001 - collected for the assert below
                errors.append(f"{u}: {e!r}")
        ts = [threading.Thread(target=work, args=(u, c, i)) for i, (u, c) in enumerate(clients.items())]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
        assert not errors, errors
        assert len(list((ws.root / "footprints" / "ecadlib.pretty").glob("R_T*.kicad_mod"))) == 5


# ------------------------------------------------------------------ SSO proxy header
def test_trusted_proxy_header():
    with server(overrides={"trusted_proxies": ["127.0.0.1"]}) as (ws, team, port):
        c = Client(port)
        st, s = c.call("GET", "/api/session", headers={"X-Forwarded-User": "apr@example.com"})
        assert st == 200 and s["user"]["username"] == "apr" and s["proxy"] is True, s
        h = {"X-Forwarded-User": "apr@example.com", "X-CSRF-Token": s["csrf"], "X-Forwarded-For": "10.0.0.7",
             "X-Forwarded-Proto": "https"}
        assert c.call("POST", "/api/approvals/status", {"bom": "x"}, h)[1]["error"] != "sign in first"
        assert c.call("POST", "/api/approvals/status", {"bom": "x"}, {"X-Forwarded-User": "apr@example.com"})[0] == 403   # CSRF still needed
        st, e = c.call("GET", "/api/info", headers={"X-Forwarded-User": "mallory"})
        assert st == 403 and "not a member" in e["error"], e
        assert any(a.get("ip") == "10.0.0.7" for a in TM.read_audit(ws.root))
    with server(overrides={"trusted_proxies": ["10.9.9.9"]}) as (ws, team, port):   # not from the proxy: ignored
        st, s = Client(port).call("GET", "/api/session", headers={"X-Forwarded-User": "ada"})
        assert s["user"] is None, s
        assert Client(port).call("GET", "/api/info", headers={"X-Forwarded-User": "ada"})[0] == 401
    with server() as (ws, team, port):                                                # proxy mode off by default
        assert Client(port).call("GET", "/api/info", headers={"X-Forwarded-User": "ada"})[0] == 401


# ------------------------------------------------------------------ admin page API and own password
def test_admin_user_management_and_own_password():
    with server() as (ws, team, port):
        a = Client(port)
        a.login("ada")
        st, r = a.call("POST", "/api/team/users", {"action": "add", "username": "Neo", "role": "librarian", "name": "Neo N",
                                                   "email": "neo@example.com", "password": "neo-password-1"})
        assert st == 200 and any(u["username"] == "neo" and u["role"] == "librarian" for u in r["users"]), r
        assert all("salt" not in json.dumps(u) and "hash" not in u for u in r["users"])   # no hashes to the browser
        assert a.call("POST", "/api/team/users", {"action": "add", "username": "neo", "role": "viewer", "password": "x" * 12})[0] == 409
        assert a.call("POST", "/api/team/users", {"action": "set-role", "username": "neo", "role": "approver", "sign_as": "hardware"})[0] == 200
        assert team.store.get("neo")["sign_as"] == ["hardware"]
        st, e = a.call("POST", "/api/team/users", {"action": "remove", "username": "ada"})
        assert st == 400 and "yourself" in e["error"], e
        n = Client(port)
        assert n.login("neo", "neo-password-1")[0] == 200
        st, e = n.call("POST", "/api/account/password", {"current": "wrong", "new": "neo-password-2"})
        assert st == 403, e
        st, s = n.call("POST", "/api/account/password", {"current": "neo-password-1", "new": "neo-password-2"})
        assert st == 200 and n.call("GET", "/api/info")[0] == 200, s             # new session, still signed in
        n.csrf = s["csrf"]
        assert a.call("POST", "/api/team/users", {"action": "reset-password", "username": "neo", "password": "neo-password-3"})[0] == 200
        assert n.call("GET", "/api/info")[0] == 401
        assert a.call("POST", "/api/team/users", {"action": "remove", "username": "neo"})[0] == 200
        st, au = a.call("GET", "/api/team/audit")
        assert st == 200 and au["entries"][0]["route"] == "/api/team/users" and au["entries"][0]["user"] == "ada", au["entries"][:2]


# ------------------------------------------------------------------ TLS
def test_tls_handshake_with_self_signed_cert():
    if not shutil.which("openssl"):
        print("skip: no openssl")
        return
    with tempfile.TemporaryDirectory() as d:
        cert, key = Path(d, "c.pem"), Path(d, "k.pem")
        p = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                            "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost", "-keyout", str(key), "-out", str(cert)],
                           capture_output=True, text=True)
        if p.returncode:
            print("skip: openssl could not make a certificate:", p.stderr[:200])
            return
        ctx = ssl.create_default_context(cafile=str(cert))
        with server(cert=str(cert), key=str(key)) as (ws, team, port):
            c = Client(port, ctx)
            st, s = c.call("GET", "/api/session")
            assert st == 200 and s["team"] is True, s
            assert "max-age" in c.last_headers["strict-transport-security"]
            assert c.login("vic")[0] == 200 and "; Secure" in c.last_headers["set-cookie"], c.last_headers
            assert c.call("GET", "/api/info")[0] == 200
            plain = http.client.HTTPConnection("127.0.0.1", port, timeout=5)      # plain HTTP to the TLS port fails
            try:
                plain.request("GET", "/api/session")
                plain.getresponse()
                raise AssertionError("plain HTTP answered")
            except (ConnectionError, http.client.HTTPException, OSError):
                pass
            bad = ssl.create_default_context()                                     # an untrusting client refuses
            try:
                Client(port, bad).call("GET", "/api/session")
                raise AssertionError("untrusted cert accepted")
            except ssl.SSLError:
                pass
            assert Client(port, ctx).call("GET", "/api/session")[0] == 200      # server still serving after bad clients


# ------------------------------------------------------------------ local mode unchanged
def test_local_mode_unchanged():
    from tests.test_ui import serve
    with tempfile.TemporaryDirectory() as d:
        srv, call = serve(Workspace(d, [], providers=["digikey"]))
        try:
            assert call("GET", "/api/session") == (200, {"team": False})
            assert call("POST", "/api/login", {"username": "a", "password": "b"})[0] == 404
            assert call("GET", "/api/team/users")[0] == 404
            assert call("GET", "/api/info")[0] == 200                              # no sign-in
            Path(d, TM.USERS_FILE).write_text("{}")
            assert call("GET", f"/api/file?path={TM.USERS_FILE}")[0] == 404
        finally:
            srv.shutdown()


# ------------------------------------------------------------------ security review regressions
def test_parallel_guesses_cannot_beat_the_lockout():
    """The lockout used to check, then hash (~50 ms), then count: N parallel guesses all passed the check, so 24
    concurrent requests got 12 password checks against a limit of 5. Now each try is counted before the hash."""
    with server() as (ws, team, port):
        codes, bar = [], threading.Barrier(16)

        def guess(i):
            c = Client(port)
            bar.wait()
            codes.append(c.call("POST", "/api/login", {"username": "lib", "password": f"wrong-guess-{i:04d}"})[0])
        ts = [threading.Thread(target=guess, args=(i,)) for i in range(16)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert codes.count(401) <= team.cfg["max_failures"] and codes.count(401) + codes.count(429) == 16, codes
        assert Client(port).login("lib")[0] == 429                             # locked, even with the right password
    th = TM.Throttle(max_failures=5, lockout_minutes=15, max_per_ip=3)
    for _ in range(5):                                                          # right passwords don't use up the address
        wait, ticket = th.attempt("ann", "10.0.0.1")
        assert wait == 0
        th.ok("ann", "10.0.0.1", ticket)
    assert th.blocked("bob", "10.0.0.1") == 0


def test_own_password_check_is_throttled():
    """/api/account/password checked the current password with no lockout: a stolen session could guess it forever."""
    with server() as (ws, team, port):
        c = Client(port)
        c.login("lib")
        codes = [c.call("POST", "/api/account/password", {"current": f"guess-{i:04d}-x", "new": "new-password-77"})[0]
                 for i in range(team.cfg["max_failures"] + 1)]
        assert codes[:-1] == [403] * team.cfg["max_failures"] and codes[-1] == 429, codes
        st, r = c.call("POST", "/api/account/password", {"current": PW["lib"], "new": "new-password-77"})
        assert st == 429, (st, r)                                               # the right one too, until unlocked
        assert TM.verify_password(PW["lib"], team.store.get("lib")["password"])


def test_removed_user_session_does_not_come_back():
    """Every user started at gen 1: remove + add again (say, to give a compromised account a fresh password) revived
    the old, possibly stolen, session cookie."""
    with server() as (ws, team, port):
        thief, ada = Client(port), Client(port)
        thief.login("apr"), ada.login("ada")
        assert ada.call("POST", "/api/team/users", {"action": "remove", "username": "apr"})[0] == 200
        st, r = ada.call("POST", "/api/team/users", {"action": "add", "username": "apr", "role": "approver",
                                                     "password": "a-fresh-password-1"})
        assert st == 200, r
        assert thief.call("GET", "/api/info")[0] == 401
        assert thief.call("GET", "/api/session")[1]["user"] is None
        gens = {TM.UserStore(ws.root).add(f"u{i}", "viewer", None)["gen"] for i in range(5)}
        assert len(gens) == 5                                                   # random start, not a counter from 1


def test_audit_line_is_written_before_the_answer():
    """The audit line was written after the response (the cause of the flaky
    test_approvals_identity_from_session: the client read team_audit.jsonl before the server thread wrote it)."""
    real = TM.audit

    def slow_audit(root, entry, *a, **kw):
        time.sleep(0.3)
        real(root, entry, *a, **kw)
    with server() as (ws, team, port), mock.patch.object(TM, "audit", slow_audit):
        c = Client(port)
        c.login("vic")
        assert ("vic", "/api/login", 200) in [(a["user"], a["route"], a["status"]) for a in TM.read_audit(ws.root)]
        st, _ = c.call("POST", "/api/triage", {"bom": "x.csv"})
        assert st == 403 and ("vic", "/api/triage", 403) in [(a["user"], a["route"], a["status"]) for a in TM.read_audit(ws.root)]
        assert Client(port).call("POST", "/api/nope", {})[0] == 404             # unknown routes are still not logged
        assert not any(a["route"] == "/api/nope" for a in TM.read_audit(ws.root))


def test_team_files_hidden_under_other_names():
    """A case-insensitive disk (macOS, Windows) served TEAM_USERS.JSON, i.e. every password hash, to any viewer;
    a hard link does the same on Linux. Team files are not readable as a BOM either."""
    with server() as (ws, team, port):
        c = Client(port)
        c.login("lib")
        users = ws.root / TM.USERS_FILE
        os.link(users, ws.root / "TEAM_USERS.JSON")                             # what a case-insensitive disk shows
        os.link(users, ws.root / "reports" / "copy.json")
        (ws.root / "team_users.json.1.tmp").write_text(users.read_text())     # a writer's temp file (pid 1 in Docker)
        (ws.root / "notes.txt").write_text("hello")
        for path in ("TEAM_USERS.JSON", "Team_Users.Json", "reports/copy.json", "team_users.json.1.tmp", "TEAM_AUDIT.JSONL"):
            st, r = c.call("GET", f"/api/file?path={path}")
            assert st == 404 and b"scrypt" not in (r if isinstance(r, bytes) else b""), (path, st)
        assert c.call("GET", "/api/file?path=notes.txt")[0] == 200
        for path in ("team_users.json", "TEAM_USERS.JSON", "uploads/../team_audit.jsonl"):
            st, e = c.call("POST", "/api/triage", {"bom": path})
            assert st == 400 and "not a BOM" in e["error"], (path, st, e)


def test_json_bodies_are_capped_before_sign_in():
    """Anyone could make the server parse a 20 MB JSON body on /api/login (~65 MB of memory each)."""
    with server() as (ws, team, port):
        big = b'{"username": "x", "password": "y", "z": "' + b"a" * (S.MAX_JSON + 10) + b'"}'
        st, e = Client(port).call("POST", "/api/login", big)
        assert st == 400 and "too large" in e["error"], (st, e)
        assert S.MAX_JSON < S.MAX_UPLOAD
        c = Client(port)
        c.login("lib")
        st, j = c.call("POST", "/api/upload", Path("examples/sample_bom.csv").read_bytes(), {"X-Filename": "b.csv"})
        assert st == 200, j                                                     # uploads keep their own limit
        wait_job(c, j["job"])


def _closed_after(port, first, trickle, limit=8.0):
    """Send `first`, then one byte of `trickle` every 0.3 s; seconds until the server closed the connection."""
    import socket
    s = socket.create_connection(("127.0.0.1", port))
    s.settimeout(0.3)
    t0 = time.monotonic()
    try:
        s.sendall(first)
        for b in itertools.cycle(trickle):
            if time.monotonic() - t0 > limit:
                return None
            try:
                s.send(bytes([b]))
                if s.recv(4096) is not None:
                    return time.monotonic() - t0                     # an answer or EOF: the server let go
            except socket.timeout:
                continue
            except OSError:
                return time.monotonic() - t0
    finally:
        s.close()


def test_slow_clients_get_one_deadline_per_request():
    """The 120 s timeout applied to each read: a client sending a byte now and then held its thread forever. Now the
    headers and body must arrive within request_seconds in total."""
    with server(overrides={"request_seconds": 1.5}) as (ws, team, port):
        host = f"Host: 127.0.0.1:{port}\r\n".encode()
        t = _closed_after(port, b"GET /api/session HTTP/1.1\r\n", host + b"X-Slow: " + b"a" * 100)
        assert t is not None and t < 4, t                                       # trickled headers
        body = b"POST /api/login HTTP/1.1\r\n" + host + b"Content-Length: 500\r\n\r\n"
        t = _closed_after(port, body, b'{"username": "x"' + b" " * 400)
        assert t is not None and t < 4, t                                       # trickled body
        assert Client(port).login("vic")[0] == 200                              # normal requests unaffected


def test_connections_per_address_are_capped():
    import socket
    with server(overrides={"max_connections_per_ip": 3}) as (ws, team, port):
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", port))
        extra.settimeout(3)
        assert extra.recv(10) == b""                                            # closed at once, no thread
        extra.close()
        idle.pop().close()
        time.sleep(0.5)
        assert Client(port).call("GET", "/api/session")[0] == 200
        [x.close() for x in idle]


def test_login_burst_is_queued_not_reset():
    """listen() backlog was 5: 60 simultaneous sign-ins (scrypt holds the GIL) got connection resets."""
    assert S.Server.request_queue_size >= 128
    with server(overrides={"max_failures_per_ip": 1000}) as (ws, team, port):
        out, bar = [], threading.Barrier(60)

        def go(i):
            c = Client(port)
            bar.wait()
            try:
                out.append(c.call("POST", "/api/login", {"username": f"nobody{i}", "password": "wrong-password-x"})[0])
            except OSError as e:
                out.append(type(e).__name__)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(60)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert out.count(401) == 60, sorted(map(str, out))


def test_audit_rotates_and_reads_only_the_tail():
    with tempfile.TemporaryDirectory() as d:
        for i in range(60):
            TM.audit(d, {"route": "/x", "n": i}, max_bytes=600, keep=2)
        names = sorted(p.name for p in Path(d).iterdir())
        assert names == [TM.AUDIT_FILE, TM.AUDIT_FILE + ".1", TM.AUDIT_FILE + ".2"], names
        assert all(p.stat().st_size <= 600 for p in Path(d).iterdir())
        got = [e["n"] for e in TM.read_audit(d, 1000, keep=2)]
        assert got == sorted(got, reverse=True) and got[0] == 59 and len(got) == sum(
            len(p.read_text().splitlines()) for p in Path(d).iterdir())                 # across the rotated files
        assert [e["n"] for e in TM.read_audit(d, 4, keep=2)] == [59, 58, 57, 56]
        f = Path(d, "big.jsonl")
        f.write_text("".join(f'{{"n": {i}}}\n' for i in range(5000)))
        assert TM._tail(f, 3, block=16) == [b'{"n": 4997}', b'{"n": 4998}', b'{"n": 4999}']
        with mock.patch.object(TM, "AUDIT_FILE", "big.jsonl"), \
                mock.patch.object(Path, "read_text", side_effect=AssertionError("read the whole file")):
            assert [e["n"] for e in TM.read_audit(d, 2, keep=0)] == [4999, 4998]


def test_unauthenticated_refusals_are_rate_limited_in_the_audit():
    """Anyone could append audit lines without limit (one per refused POST); now per_minute per address, then a
    count of the dropped ones on the next line."""
    with server(overrides={"audit_refusals_per_minute": 5}) as (ws, team, port):
        anon = Client(port)
        for _ in range(12):
            assert anon.call("POST", "/api/triage", {"bom": "x.csv"})[0] == 401
        rows = [a for a in TM.read_audit(ws.root, 1000) if a["route"] == "/api/triage"]
        assert len(rows) == 5, rows
        vic = Client(port)
        vic.login("vic")
        for _ in range(8):                                                      # signed-in refusals: all logged
            assert vic.call("POST", "/api/triage", {"bom": "x.csv"})[0] == 403
        assert sum(a["user"] == "vic" and a["route"] == "/api/triage" for a in TM.read_audit(ws.root, 1000)) == 8
    rl, clock = TM.RefusalLog(2), [1000.0]
    with mock.patch.object(TM.time, "time", lambda: clock[0]):
        assert [rl.admit("a") for _ in range(5)] == [{}, {}, None, None, None]
        assert rl.admit("b") == {}
        clock[0] += 61
        assert rl.admit("a") == {"suppressed": 3} and rl.admit("a") == {}


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_team"]))
