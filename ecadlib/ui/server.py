"""Web UI: drag in a BOM, review the triage, generate and approve footprints, export to Altium.

    ecadlib ui --lib path/to/kicad/libs [--workspace ecadlib_workspace] [--port 8765] [--offline]
    (or: python -m ecadlib.ui ...)

Local mode (default): standard library only (http.server), bound to 127.0.0.1; a single-user desktop tool, provider
API keys stay in the user's environment.

Team mode (`--team`): one server for the whole team with sign-in (ecadlib/team.py): users and roles in
team_users.json, session cookies, a CSRF token on every POST, a minimum role per route (ROUTES), approvals signed as
the signed-in user, every write in team_audit.jsonl. A non-loopback address needs --cert/--key (built-in TLS) or an
explicit --allow-http (behind an HTTPS reverse proxy on another host)."""
import argparse, contextlib, hmac, io, http.cookies, ipaddress, itertools, json, mimetypes, re, secrets, socket, ssl, sys, \
    threading, os, time, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import agent_queue as AQ, hub as HUB, team as TM
from ..app import Workspace
from .. import easy_setup as ES

STATIC = Path(__file__).with_name("static")
MAX_UPLOAD = 20 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024                 # JSON bodies (sign-in is unauthenticated: no 20 MB parse for anyone)
EXPECTED = (ValueError, KeyError, FileExistsError, json.JSONDecodeError, PermissionError)   # user errors: no traceback
COOKIE = "ecadlib_session"

# Minimum team role per route. Team mode answers only the routes listed here (anything else is 404), so a new endpoint
# stays unreachable until it gets a role; tests/test_team.py checks every "/api/..." in this file is listed.
PUBLIC = "public"
ROUTES = {
    ("GET", "/"): PUBLIC, ("GET", "/index.html"): PUBLIC, ("GET", "/hairline.js"): PUBLIC,
    ("GET", "/api/session"): PUBLIC, ("POST", "/api/login"): PUBLIC,
    ("POST", "/api/logout"): "viewer", ("POST", "/api/account/password"): "viewer",
    ("GET", "/api/info"): "viewer", ("GET", "/api/specs"): "viewer", ("GET", "/api/rules"): "viewer",
    ("GET", "/api/job"): "viewer", ("GET", "/api/file"): "viewer",
    ("POST", "/api/job/cancel"): "viewer",                 # only the job's owner (or an admin) can see it to stop it
    ("GET", "/api/parts"): "viewer",                       # a BOM's library plan as part cards (ecadlib.partview)
    ("POST", "/api/demo"): "librarian",                    # first visit: the bundled sample BOM, checked and filled
    ("GET", "/api/status"): "viewer",                      # workspace summary (Status card)
    ("GET", "/api/doctor"): "admin",                       # setup check: tool paths, which API key names are set
    ("GET", "/api/triage/history"): "viewer", ("GET", "/api/triage/result"): "viewer",   # finished results: library data
    ("POST", "/api/footprint/preview"): "viewer", ("POST", "/api/approvals/status"): "viewer",
    ("POST", "/api/resolve/held"): "viewer",
    ("POST", "/api/upload"): "librarian", ("POST", "/api/triage"): "librarian",
    ("POST", "/api/librarian/run"): "librarian", ("POST", "/api/resolve/candidates"): "librarian",
    ("POST", "/api/resolve/choose"): "librarian", ("POST", "/api/footprint/approve"): "librarian",
    ("POST", "/api/export/dblib"): "librarian", ("POST", "/api/altium/models"): "librarian",
    ("POST", "/api/export/tool"): "librarian",             # Fusion Electronics / EAGLE .lbr, EasyEDA Pro .elibz
    ("POST", "/api/altium365/plan"): "librarian", ("POST", "/api/altium365/workspaces"): "librarian",
    ("POST", "/api/altium365/push"): "librarian",          # dry run; a live push needs LIVE_PUSH
    ("POST", "/api/release-check"): "librarian", ("POST", "/api/dedupe"): "librarian",
    ("POST", "/api/approvals/decide"): "approver",
    ("GET", "/api/agent/queue"): "viewer", ("GET", "/api/agent/queue/item"): "viewer",
    ("GET", "/api/agent/queue/picture"): "viewer",             # images under <workspace>/agent/ only
    ("POST", "/api/agent/queue/rebuild"): "librarian", ("POST", "/api/agent/queue/reject"): "librarian",
    ("POST", "/api/agent/queue/approve"): "approver",
    ("POST", "/api/rules/propose"): "admin",               # reads any folder on the server
    ("POST", "/api/rules/save"): "admin",
    ("GET", "/api/hub"): "viewer", ("GET", "/api/hub/part"): "viewer",   # library hub: every part across tools
    ("POST", "/api/hub/fix"): "librarian", ("POST", "/api/hub/copy"): "librarian",
    ("GET", "/api/style"): "viewer",                       # the learned house style (ecadlib.house_style)
    ("POST", "/api/style/learn"): "admin",                 # reads library folders; may call the configured model
    ("POST", "/api/style/direction"): "admin",
    ("GET", "/api/team/users"): "admin", ("POST", "/api/team/users"): "admin", ("GET", "/api/team/audit"): "admin",
}
LIVE_PUSH = "approver"
TEAM_ONLY = {"/api/login", "/api/logout", "/api/account/password", "/api/team/users", "/api/team/audit"}
AUDIT_KEYS = ("bom", "part", "decision", "dry_run", "workspace", "kind", "id", "merge", "overwrite", "allow", "unallow",
              "action", "username", "role", "note")
TEAM_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
                "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'none'"}


class Refused(Exception):
    """403 with a message (team mode: role too low for this variant of a route)."""


class Cancelled(Exception):
    """Raised inside a job's progress callback once someone pressed Stop (checked between lines / parts)."""


class Jobs:
    """Long actions (triage looks parts up online, about a second per request) run in a thread; the page polls.
    Each job remembers who started it (team mode: only they and admins see it) and an optional key: a second job
    with the same key, e.g. the librarian on one BOM, is refused while the first runs. A job keeps a short list of
    per-part lines ({key, n, total, name, state, text}) for the live progress list, and can be stopped (cancel)."""
    MAX_LINES = 400

    def __init__(self, root=None):
        self.jobs, self.ids, self.lock = {}, itertools.count(1), threading.Lock()
        self.root = root

    def start(self, fn, *args, label="", owner=None, key=None):
        with self.lock:
            busy = key and self.running(key)
            if busy:
                raise FileExistsError(f"already running for {key.split(':', 1)[-1]}"
                                      + (f" (started by {busy['owner']})" if busy["owner"] else "") + "; wait for it to finish")
            jid = f"{next(self.ids)}-{secrets.token_hex(6)}"
            job = {"id": jid, "state": "running", "done": 0, "total": 0, "label": label, "result": None, "error": None,
                   "owner": owner, "key": key, "lines": [], "cancel": False, "started": time.time()}
            self.jobs[jid] = job
            for old in [k for k in self.jobs if self.jobs[k]["state"] != "running"][:-20]:   # keep the last 20 finished
                del self.jobs[old]

        def progress(done, total, lab):
            if job["cancel"]:
                raise Cancelled()
            job.update(done=done, total=total, label=lab)

        def line(key, **kw):
            """Add or update one line of the live list (by key, e.g. the part name)."""
            with self.lock:
                cur = next((x for x in job["lines"] if x["key"] == key), None)
                if cur is None:
                    if len(job["lines"]) >= self.MAX_LINES:
                        return
                    cur = {"key": key, "n": None, "total": None, "name": key, "state": "working", "text": ""}
                    job["lines"].append(cur)
                cur.update({k: v for k, v in kw.items() if v is not None})
        progress.line = line
        progress.sort = lambda: job["lines"].sort(key=lambda x: (x["n"] is not None, x["n"] or 0))
        progress.cancelled = lambda: job["cancel"]

        def run():
            try:
                job["result"] = fn(*args, progress=progress)
                job["state"] = "done"
            except Cancelled:
                job["state"] = "cancelled"
            except Exception as e:
                if not isinstance(e, EXPECTED):
                    traceback.print_exc()
                job["error"], job["state"] = _msg(e, self.root), "error"
            job["seconds"] = round(time.time() - job["started"], 1)
        threading.Thread(target=run, daemon=True).start()
        return {"job": jid}

    def running(self, key):
        return next((j for j in list(self.jobs.values()) if j["key"] == key and j["state"] == "running"), None)

    def get(self, jid, user=None):
        job = self.jobs.get(jid)
        if job and user is not None and job["owner"] != user["username"] and user["role"] != "admin":
            return None                                     # team mode: other people's jobs are invisible
        if not job:
            return None
        out = {k: v for k, v in job.items() if k not in ("key", "lines")}
        out["lines"] = [dict(x) for x in list(job["lines"])]
        out["elapsed"] = job.get("seconds") or round(time.time() - job["started"], 1)
        return out

    def cancel(self, jid, user=None):
        """Ask a running job to stop at its next line or part. -> the job, or None when unknown / not yours."""
        if self.get(jid, user) is None:
            return None
        job = self.jobs[jid]
        if job["state"] == "running":
            job["cancel"] = True
        return self.get(jid, user)


def _triage_word(status):
    s = str(status or "")
    return ("in your library" if s.startswith("in_library") else "not fitted" if s.startswith("dnp")
            else "unverified" if s.startswith("unverified") else "needs work")


def _line_lines(progress, what=None):
    """Triage progress -> one live line per BOM line ("3/14 CL05B104KO5NNNC  checking")."""
    def cb(done, total, label):
        progress(done, total, label)
        if what:
            progress.line("__triage", state="working", text=f"{what}: line {min(done + 1, total)} of {total}")
        elif done < total:
            progress.line(f"line {done + 1}", n=done + 1, total=total, name=label, state="working",
                          text=f"{done + 1}/{total} {label}  checking")
    cb.line = progress.line
    return cb


_PART = re.compile(r"^\[(\d+)/(\d+)\] (.+)$")
_DONE = re.compile(r"^(done|open|skip) +(\S.*?) +sym=(\S+) +fp=(\S+)")


def _part_lines(progress):
    """The librarian's log lines -> real part counts and one live line per part ("3/14 SOT-23  ready")."""
    state = {"total": 0, "n": 0, "prev": None}

    def cb(done, total, label):
        m, d = _PART.match(label or ""), _DONE.match(label or "")
        if m:
            n, tot, name = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            if state["prev"]:
                pn, pname = state["prev"]
                progress.line(pname, state="found", text=f"{pn}/{tot} {pname}  looked up")
            state.update(total=tot, n=n, prev=(n, name))
            progress(n - 1, tot, name)
            progress.line(name, n=n, total=tot, name=name, state="working", text=f"{n}/{tot} {name}  looking up")
        elif d:
            word = {"done": "found", "open": "still missing a symbol or footprint", "skip": "already done"}[d.group(1)]
            progress.line(d.group(2), name=d.group(2), state="found" if d.group(1) != "open" else "missing",
                          text=f"{d.group(2)}  {word}")
            if state["total"]:
                progress(state["total"], state["total"], "release check")
        elif not state["total"]:                            # the log's own wording stays in the log
            progress(0, 0, "finding symbols and footprints" if (label or "").startswith("finding") else "checking the BOM lines")
        else:
            progress(state["n"], state["total"], "checking each part before release")
    cb.line = progress.line
    return cb


def _final_lines(progress, r):
    """After the release check: every part's line says ready / held / waiting for sign-off."""
    from .. import messages as M
    rows = (r.get("review") if isinstance(r, dict) else None) or []
    n = len(rows)
    for i, x in enumerate(rows, 1):
        word = M.status_word(x["status"])
        progress.line(x["part"], n=i, total=n, name=x["part"], state=x["status"], text=f"{i}/{n} {x['part']}  {word}")
    progress.sort()
    return r


def _msg(e, root=None):
    m = (e.args[0] if isinstance(e, KeyError) and e.args else str(e)) or type(e).__name__
    if root:                                                # workspace paths as the person sees them: relative
        m = str(m).replace(str(root) + os.sep, "").replace(str(root) + "/", "").replace(str(root), "the workspace")
    from .. import messages as M                            # absolute paths and module names never reach the page
    m = M.clean(m)
    return (m if isinstance(e, (ValueError, FileExistsError, PermissionError, KeyError))
            else f"Something went wrong ({type(e).__name__}: {m}). The terminal running ecadlib ui shows the details.")[:600]


def _names(v):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError("allow / unallow must be a list of part names")
    return v


def _str(v, what, optional=False):
    """A string field from a JSON body. No identity is ever filled in here: an approval names its user explicitly."""
    if v is None and optional:
        return None
    if not isinstance(v, str) or (not optional and not v.strip()):
        raise ValueError(f"{what} must be a non-empty string")
    return v


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_LOCAL_WHO = []


def local_who():
    """This computer's user, as approvals records it (ECADLIB_USER, git config, login name); looked up once."""
    if not _LOCAL_WHO:
        from ..approvals import identity
        _LOCAL_WHO.append(identity())
    return _LOCAL_WHO[0]


def _strs(v, what):
    """A list of path strings from a JSON body (scan sources: any folder the person types, read for names only)."""
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) and len(x) < 1000 and "\0" not in x for x in v):
        raise ValueError(f"{what} must be a list of paths")
    return [x for x in v if x.strip()]


def _team_file(ws, p):
    """team_users.json & co. (password hashes, audit) are never downloadable nor readable as a BOM: also not under
    another letter case (case-insensitive disks: macOS, Windows serve TEAM_USERS.JSON), as their temp files, or
    through another hard link."""
    if p.parent == ws.root and any(p.name.casefold().startswith(f.casefold()) for f in TM.TEAM_FILES):
        return True
    for f in TM.TEAM_FILES:
        with contextlib.suppress(OSError):
            if os.path.samefile(p, ws.root / f):
                return True
    return False


class _DeadlineReader(io.RawIOBase):
    """Socket reads that share one deadline for the whole request (team mode): a client trickling a byte now and
    then would otherwise reset the per-read timeout forever and hold its thread."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def readable(self):
        return True

    def readinto(self, b):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("request took too long")
        self.sock.settimeout(left)
        return self.sock.recv_into(b)


def make_handler(ws: Workspace, team=None):
    """team: a team.Team for team mode; None = local mode (unchanged single-user behaviour)."""
    jobs = Jobs(ws.root)
    write_lock = threading.Lock()                          # one writer at a time in the workspace

    class H(BaseHTTPRequestHandler):
        server_version = "ecadlib-ui/0.1"
        user = sess = _audit_route = None
        _gated = False
        if team:
            timeout = 120                                  # a stalled client can't hold a thread forever

        def setup(self):
            super().setup()
            deadline = time.monotonic() + float(team.cfg["request_seconds"]) if team else None
            if team:
                self.connection.settimeout(min(self.timeout, team.cfg["request_seconds"]))
            if hasattr(self.request, "do_handshake"):      # TLS: handshake here, in the request's own thread
                self.request.do_handshake()
            if team:                                       # headers + body within request_seconds, all told
                self.rfile.close()                         # nothing read yet; drops the makefile's socket ref
                self.rfile = io.BufferedReader(_DeadlineReader(self.connection, deadline))

        def finish(self):
            if team:
                with contextlib.suppress(OSError):         # the answer gets its own (per-write) timeout
                    self.connection.settimeout(self.timeout)
            super().finish()

        def log_message(self, fmt, *args):
            sys.stderr.write("ui: " + fmt % args + "\n")

        def _send(self, code, body, ctype="application/json", headers=None):
            data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
            self._status = code
            if self._audit_route is not None and (self._gated or code in (401, 403)):
                route, self._audit_route = self._audit_route, None
                self._audit(route)                         # before the answer: logged by the time the client sees it
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if team:
                for k, v in TEAM_HEADERS.items():
                    self.send_header(k, v)
                if team.tls:
                    self.send_header("Strict-Transport-Security", "max-age=31536000")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            """Refuse DNS-rebinding requests: the Host header must name this server's own loopback address
            (team mode: an IP literal, localhost, this machine's name or a configured allowed host)."""
            if team:
                return team.host_ok(self.headers.get("Host"))
            port = self.server.server_address[1]
            names = {"127.0.0.1", "localhost", "[::1]", self.server.server_address[0]}
            return self.headers.get("Host") in {f"{h}:{port}" for h in names}

        def _origin_ok(self):
            """Stop other sites driving the API: an Origin, when sent, must be this server."""
            origin = self.headers.get("Origin")
            if not team:
                scheme = "https" if getattr(self.server, "tls", False) else "http"
                return origin in (None, f"{scheme}://{self.headers.get('Host')}")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return False
            if origin is None:
                return True
            o = urlparse(origin)                           # scheme may differ behind an HTTPS proxy; host:port may not
            return o.scheme in ("http", "https") and o.netloc.lower() == str(self.headers.get("Host") or "").lower()

        # ---------------------------------------------------------- team mode: who is asking
        def _from_proxy(self):
            return self.client_address[0] in team.cfg["trusted_proxies"]

        def _ip(self):
            """Client address for logs and throttling (the X-Forwarded-For hop when a trusted proxy forwards)."""
            if team and self._from_proxy() and self.headers.get("X-Forwarded-For"):
                return self.headers["X-Forwarded-For"].split(",")[-1].strip()[:64]
            return self.client_address[0]

        def _secure(self):
            return team.tls or team.cfg["secure_cookies"] or (
                self._from_proxy() and self.headers.get("X-Forwarded-Proto", "").lower() == "https")

        def _cookie(self):
            c = http.cookies.SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
            except http.cookies.CookieError:
                return None
            return c[COOKIE].value if COOKIE in c else None

        def _set_cookie(self, tok, max_age):
            return (f"{COOKIE}={tok}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
                    + ("; Secure" if self._secure() else ""))

        def _identify(self):
            """Sets self.user (users-file record) and self.sess (session dict, or 'proxy')."""
            hdr = team.cfg["proxy_user_header"]
            if team.cfg["trusted_proxies"] and self._from_proxy() and self.headers.get(hdr):
                self.user, self.sess = team.store.find(self.headers[hdr]), "proxy"
                return
            s = team.sessions.get(self._cookie())
            u = s and team.store.get(s["user"])
            if s and (not u or int(u.get("gen") or 1) != s["gen"]):     # removed or password reset: signed out
                team.sessions.drop(self._cookie())
                s = u = None
            self.user, self.sess = u, s

        def _csrf(self):
            if self.sess == "proxy":
                return team.proxy_csrf(self.user["username"])
            return self.sess["csrf"] if self.sess else None

        def _need(self, role):
            if TM.RANK[self.user["role"]] < TM.RANK[role]:
                raise Refused(f"this needs the {role} role; you are {self.user['role']}")

        def _gate(self, method):
            """Checks before any route runs. Returns the parsed URL, or None after sending a refusal."""
            u = urlparse(self.path)
            self._status, self._fields = None, {}
            if not self._host_ok():
                return self._send(403, {"error": "unexpected Host header"})
            if method == "POST" and not self._origin_ok():
                return self._send(403, {"error": "cross-origin request refused"})
            if not team:
                return self._send(404, {"error": "not found"}) if u.path in TEAM_ONLY else u
            role = ROUTES.get((method, u.path))
            if role is None:
                return self._send(404, {"error": "not found"})
            self._identify()
            if role == PUBLIC:
                self._gated = True
                return u
            if not self.user:
                if self.sess == "proxy":
                    return self._send(403, {"error": "your sign-in name is not a member of this ecadlib team"})
                return self._send(401, {"error": "sign in first"})
            if method == "POST" and not hmac.compare_digest(str(self.headers.get("X-CSRF-Token") or ""), self._csrf() or "-"):
                return self._send(403, {"error": "missing or wrong CSRF token: reload the page"})
            if TM.RANK[self.user["role"]] < TM.RANK[role]:
                return self._send(403, {"error": f"this needs the {role} role; you are {self.user['role']}"})
            self._gated = True
            return u

        def _audit(self, path):
            if team and self._status is not None:
                team.audit({"user": self.user["username"] if self.user else None, "ip": self._ip(), "route": path,
                            "status": self._status, **self._fields})

        # ---------------------------------------------------------- request bodies
        def _bom(self, body=None, write=False):
            """Web clients may only name BOMs inside the workspace (absolute paths would read any file on disk)."""
            bom = (body if body is not None else self._json()).get("bom")
            if not isinstance(bom, str) or not bom:
                raise ValueError("no BOM given")
            if len(bom) > 1000 or any(len(x) > 200 for x in bom.replace("\\", "/").split("/")):
                raise ValueError("that BOM name is too long; pick a BOM from the list")
            if _team_file(ws, ws.inside(bom)):
                raise ValueError(f"{bom} is not a BOM")
            if write and jobs.running("librarian:" + bom):
                raise FileExistsError(f"the librarian is still running on {bom}; try again when it has finished")
            return bom

        def _body(self, limit=MAX_UPLOAD):
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > limit:
                self.close_connection = True
                if 0 < n <= MAX_UPLOAD:                     # read and drop it (in chunks, never kept), so the client
                    try:                                    # gets the 400 instead of a reset while still sending
                        while n > 0:
                            got = self.rfile.read(min(n, 65536))
                            if not got:
                                break
                            n -= len(got)
                    except (OSError, ValueError):
                        pass
                raise ValueError("bad or too large Content-Length")
            try:
                data = self.rfile.read(n)
            except TimeoutError:
                self.close_connection = True
                raise ValueError("the request took too long to arrive") from None
            if len(data) < n:
                raise ValueError("the request body was cut short")
            return data

        def _json(self):
            b = json.loads(self._body(MAX_JSON) or b"{}")
            if not isinstance(b, dict):
                raise ValueError("expected a JSON object")
            self._fields = {k: b[k] for k in AUDIT_KEYS if k in b and isinstance(b[k], (str, bool, int, list))
                            and len(json.dumps(b[k])) < 500}
            return b

        def _owner(self):
            return self.user["username"] if team and self.user else None

        # ---------------------------------------------------------- routes
        def do_GET(self):
            u = self._gate("GET")
            if u is None:
                return
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if not team and u.path == "/setup":            # setup page: local mode only (ecadlib.easy_setup)
                    return self._send(200, (STATIC / "setup.html").read_bytes(), "text/html; charset=utf-8")
                if not team and u.path in ES.GET_ROUTES:
                    return self._send(200, ES.GET_ROUTES[u.path](ws.root, q))
                if u.path == "/hairline.js":                    # the line figures (scripts/build_ui_figures.sh)
                    return self._send(200, (STATIC / "hairline.js").read_bytes(), "text/javascript; charset=utf-8")
                if u.path == "/api/session":
                    return self._send(200, self._session_info())
                if u.path == "/api/info":
                    return self._send(200, ws.info())
                if u.path == "/api/specs":
                    return self._send(200, ws.example_specs())
                if u.path == "/api/rules":
                    return self._send(200, ws.rules_info())
                if u.path == "/api/style":
                    return self._send(200, ws.house_style())
                if u.path == "/api/status":
                    return self._send(200, ws.status())
                if u.path == "/api/doctor":
                    return self._send(200, ws.doctor())
                if u.path == "/api/triage/history":            # every finished triage, whoever ran it
                    return self._send(200, ws.triage_history())
                if u.path == "/api/triage/result":
                    try:
                        return self._send(200, ws.triage_result(q.get("id", "")))
                    except KeyError as e:
                        return self._send(404, {"error": _msg(e)})
                if u.path == "/api/parts":                      # part cards of a BOM's library plan
                    return self._send(200, ws.library_parts(self._bom({"bom": q.get("bom")}), q.get("pictures") != "0"))
                if u.path == "/api/hub":                        # the library hub: one page of parts, filtered
                    lst = lambda k: [x for x in (q.get(k) or "").split(",") if x]
                    return self._send(200, ws.hub(q.get("q", ""), lst("have"), lst("lack"), lst("state"), q.get("origin", ""),
                                                  lst("flags"), q.get("sort", "name"), _int(q.get("page"), 1),
                                                  _int(q.get("size"), 100), q.get("rescan") == "1",
                                                  q.get("tool", ""), q.get("library", ""), q.get("project", "")))
                if u.path == "/api/hub/part":
                    try:
                        return self._send(200, ws.hub_part(q.get("key", "")))
                    except KeyError as e:
                        return self._send(404, {"error": _msg(e)})
                if u.path == "/api/job":                        # a running job (its live log): owner or admin only
                    job = jobs.get(q.get("id", ""), self.user if team else None)
                    return self._send(200, job) if job else self._send(404, {"error": "no such job"})
                if u.path == "/api/file":                       # download anything inside the workspace
                    p = ws.inside(q.get("path", ""))
                    if not p.is_file() or _team_file(ws, p):
                        return self._send(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    plain = re.sub(r'[^\x20-\x7e]|["\\]', "_", p.name)   # headers are latin-1: UTF-8 name in filename*
                    return self._send(200, p.read_bytes(), ctype,
                                      {"Content-Disposition": f'attachment; filename="{plain}"'
                                                              + (f"; filename*=UTF-8''{quote(p.name, errors='replace')}" if plain != p.name else "")})
                if u.path == "/api/agent/queue":
                    return self._send(200, ws.agent_queue_list(q.get("status") or None, q.get("batch") or None))
                if u.path == "/api/agent/queue/item":
                    if not q.get("id"):
                        raise ValueError("no queue item id given")
                    try:
                        return self._send(200, ws.agent_queue_get(q["id"]))
                    except KeyError as e:
                        return self._send(404, {"error": _msg(e)})
                if u.path == "/api/agent/queue/picture":       # shown inline: images only, inside agent/ only
                    p = ws.agent_picture(q.get("path", ""))
                    return self._send(200, p.read_bytes(), AQ.PICTURE_TYPES[p.suffix.lower()],
                                      {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"}
                                      if not team else None)
                if team and u.path == "/api/team/users":
                    return self._send(200, self._users())
                if team and u.path == "/api/team/audit":
                    return self._send(200, {"entries": TM.read_audit(ws.root, 300, int(team.cfg["audit_keep"]))})
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._error(e)

        def do_POST(self):
            # team mode: _send() writes the audit line (every POST that passes the gate, and 401/403 refusals)
            self._audit_route = urlparse(self.path).path if team else None
            u = self._gate("POST")
            if u is None:
                return
            try:
                return self._post(u)
            except Exception as e:
                return self._error(e)

        def _post(self, u):
            if team and u.path in TEAM_ONLY:
                return self._team_post(u.path)
            if not team and u.path in ES.POST_ROUTES:                # setup page (ecadlib.easy_setup)
                b = self._json()
                with write_lock if u.path in ES.WRITES else contextlib.nullcontext():
                    return self._send(200, ES.POST_ROUTES[u.path](ws.root, b))
            if u.path == "/api/footprint/preview":                # read-only, no lock
                b = self._json()
                return self._send(200, ws.preview_footprint(b["spec"], b.get("density")))
            if u.path == "/api/job/cancel":                       # Stop: the job ends at its next line or part
                job = jobs.cancel(_str(self._json().get("id"), "id"), self.user if team else None)
                return self._send(200, job) if job else self._send(404, {"error": "no such job"})
            if u.path == "/api/demo":                             # first visit: the bundled sample BOM, offline
                self._json()
                with write_lock:
                    rel = ws.demo_bom()
                return self._send(200, dict(jobs.start(self._demo, rel, self._owner(), label=rel, owner=self._owner(),
                                                       key="librarian:" + rel), bom=rel))
            if u.path == "/api/upload":
                name = unquote(self.headers.get("X-Filename", "bom.csv"))
                self._fields = {"file": name[:200]}
                with write_lock:
                    rel = ws.save_upload(name, self._body())
                return self._send(200, jobs.start(self._triage, rel, self._owner(), label=rel, owner=self._owner()))
            if u.path == "/api/triage":
                bom = self._bom()
                return self._send(200, jobs.start(self._triage, bom, self._owner(), label=bom, owner=self._owner()))
            if u.path == "/api/librarian/run":                    # long, network-bound: a background job
                b = self._json()
                bom = self._bom(b)
                refresh = [] if b.get("refresh") is True else (b.get("refresh") or None)
                with contextlib.suppress(Exception):
                    HUB.note_action(ws.root, u.path, dict(b, bom=bom), self._who(b))
                return self._send(200, jobs.start(self._librarian, bom, b.get("sources"), refresh, label=bom,
                                                  owner=self._owner(), key="librarian:" + bom))
            if u.path == "/api/resolve/candidates":               # may fetch KiCad library files: a background job
                b = self._json()
                bom, part = self._bom(b), _str(b.get("part"), "part")
                ws.held_parts(bom)                                # no plan yet: answer now, not from the job
                return self._send(200, jobs.start(self._candidates, bom, part, b.get("kind") or None,
                                                  b.get("limit") or 10, label=part, owner=self._owner()))
            if u.path == "/api/rules/propose":                    # reads library folders the person names: a job
                b = self._json()
                paths, a365 = _strs(b.get("paths"), "paths"), _strs(b.get("a365"), "a365")
                choices = b.get("choices") or {}
                if not isinstance(choices, dict):
                    raise ValueError("choices must map setting keys to values")
                return self._send(200, jobs.start(self._propose, paths, a365, choices, label="library rules",
                                                  owner=self._owner()))
            if u.path == "/api/hub/fix":                          # a fix button on the library hub
                b = self._json()
                key, action = _str(b.get("key"), "key"), _str(b.get("action"), "action")
                if action == "build":                             # a one-line BOM through the librarian: a job
                    with write_lock:
                        bom = ws.hub_build_bom(key)
                    HUB.record(ws.root, [{"when": HUB.now(), "who": self._who(b), "what": "asked ecadlib to build it",
                                          "kind": "action", "key": key, "bom": bom}])
                    return self._send(200, dict(jobs.start(self._librarian, bom, None, None, label=bom, owner=self._owner(),
                                                           key="librarian:" + bom), bom=bom))
                with write_lock:
                    return self._send(200, ws.hub_fix(key, action, self._who(b)))
            if u.path == "/api/hub/copy":                         # copy parts to another tool (and into a project)
                b = self._json()
                keys = b.get("keys")
                if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys) or len(keys) > 2000:
                    raise ValueError("keys must be a list of part keys from /api/hub")
                with write_lock:
                    return self._send(200, ws.hub_copy(keys, _str(b.get("tool"), "tool"), _str(b.get("project"), "project", True) or "",
                                                       self._who(b)))
            if u.path == "/api/style/learn":                      # scans library folders, maybe asks the model: a job
                b = self._json()
                return self._send(200, jobs.start(self._learn_style, _strs(b.get("paths"), "paths"),
                                                  _strs(b.get("direction"), "direction"), b.get("ai") is True,
                                                  _str(b.get("model"), "model", True) or None, label="house style",
                                                  owner=self._owner()))
            if u.path == "/api/altium365/workspaces":             # read-only network call, no lock
                self._json()
                return self._send(200, ws.altium365_workspaces())
            actions = {
                "/api/footprint/approve": lambda b: ws.approve_footprint(b["spec"], b.get("density"), bool(b.get("overwrite"))),
                "/api/export/dblib": lambda b: ws.export_dblib(self._bom(b, True)),
                "/api/export/tool": lambda b: ws.tool_export(self._bom(b, True), b.get("tool")),
                "/api/altium/models": lambda b: ws.build_altium_models(self._bom(b, True)),
                "/api/altium365/plan": lambda b: ws.altium365_plan(self._bom(b, True)),
                "/api/release-check": lambda b: ws.release_check(self._bom(b, True), _names(b.get("allow")), _names(b.get("unallow")),
                                                                 TM.identity(self.user) if team else _str(b.get("user"), "user", True)),
                "/api/resolve/held": lambda b: ws.held_parts(self._bom(b)),
                "/api/resolve/choose": lambda b: ws.choose_part(self._bom(b, True), _str(b.get("part"), "part"), b.get("kind"),
                                                                _str(b.get("id"), "id"), b.get("pin_map") or None),
                "/api/approvals/status": lambda b: ws.approval_status(self._bom(b)),
                "/api/approvals/decide": self._decide,
                "/api/dedupe": lambda b: ws.find_duplicates(self._bom(b, True), b.get("merge") is True),
                "/api/rules/save": lambda b: ws.save_rules(_str(b.get("text"), "text"), b.get("overwrite") is True),
                "/api/style/direction": lambda b: ws.style_direction(_str(b.get("text"), "text", True) or None,
                                                                     b.get("clear") is True),
                "/api/altium365/push": self._push,
                "/api/agent/queue/approve": lambda b: self._queue(b, "approve"),
                "/api/agent/queue/reject": lambda b: self._queue(b, "reject"),
                "/api/agent/queue/rebuild": lambda b: self._queue(b, "rebuild"),
            }
            if u.path in actions:
                b = self._json()
                with write_lock:
                    res = actions[u.path](b)
                with contextlib.suppress(Exception):             # the hub's "who changed it": never fails the action
                    HUB.note_action(ws.root, u.path, b, self._who(b), res)
                return self._send(200, res)
            return self._send(404, {"error": "not found"})

        def _who(self, b):
            """Who did it, for the hub's history: the signed-in user (team), else the name typed in the page or this
            computer's user (git config / login name)."""
            if team and self.user:
                return TM.identity(self.user)
            typed = b.get("user") if isinstance(b.get("user"), str) else ""
            return typed.strip()[:120] or local_who()

        def _decide(self, b):
            role, comment = _str(b.get("role"), "role", True), _str(b.get("comment"), "comment", True)
            if not team:
                return ws.approve_part(self._bom(b, True), _str(b.get("part"), "part"), b.get("decision"),
                                       _str(b.get("user"), "user"), role, comment)
            roles = TM.sign_roles(self.user)                   # identity and approval role come from the session
            role = (role or "").strip() or roles[0]
            if role not in roles:
                raise Refused(f"you sign as {', '.join(roles)}, not {role}")
            return ws.approve_part(self._bom(b, True), _str(b.get("part"), "part"), b.get("decision"), TM.identity(self.user),
                                   role, comment, via="ecadlib ui (team, signed in)")

        def _queue(self, b, action):
            """Review queue decision. Team mode: identity and approval role from the session (typed names ignored);
            rebuild on a failed / rejected item is a retry."""
            i = _str(b.get("id"), "id")
            note, role = _str(b.get("note"), "note", True) or "", _str(b.get("role"), "role", True)
            expect = _str(b.get("expect"), "expect", True)      # the build the person looked at
            if action == "rebuild" and ws.agent_queue_get(i)["item"]["status"] in ("failed", "rejected"):
                action = "retry"
            if not team:
                return ws.agent_queue_decide(i, action, _str(b.get("user"), "user"), role, note, expect=expect)
            roles = TM.sign_roles(self.user)
            role = (role or "").strip() or roles[0]
            if role not in roles:
                raise Refused(f"you sign as {', '.join(roles)}, not {role}")
            return ws.agent_queue_decide(i, action, TM.identity(self.user), role, note, via="ecadlib ui (team, signed in)",
                                         aliases=(self.user["username"], self.user.get("email") or ""), expect=expect)

        def _push(self, b):
            dry = b.get("dry_run", True) is not False
            if team and not dry:
                self._need(LIVE_PUSH)
            return ws.altium365_push(self._bom(b, True), b.get("workspace"), dry_run=dry)

        # ---------------------------------------------------------- team mode routes
        def _session_info(self):
            if not team:
                return {"team": False}
            if not self.user:
                return {"team": True, "user": None, "proxy": bool(team.cfg["trusted_proxies"])}
            u = self.user
            return {"team": True, "csrf": self._csrf(), "proxy": self.sess == "proxy", "roles": list(TM.ROLES),
                    "user": {"username": u["username"], "name": u.get("name") or "", "email": u.get("email") or "",
                             "role": u["role"], "identity": TM.identity(u), "sign_as": TM.sign_roles(u)}}

        def _users(self):
            locked = [n for n in team.store.users() if team.throttle.blocked(n, "")]
            return {"users": team.store.public(), "roles": list(TM.ROLES), "locked": locked, "me": self.user["username"]}

        def _team_post(self, path):
            b = self._json()
            if path == "/api/login":
                return self._login(b)
            if path == "/api/logout":
                if self.sess != "proxy":
                    team.sessions.drop(self._cookie())
                return self._send(200, {"ok": True}, headers={"Set-Cookie": self._set_cookie("", 0)})
            if path == "/api/account/password":
                if self.sess == "proxy":
                    raise PermissionError("signed in through the SSO proxy: change the password there")
                name, ip = self.user["username"], self._ip()     # same lockout as sign-in: no unlimited guessing
                wait, ticket = team.throttle.attempt(name, ip)
                if wait:
                    return self._too_many(wait)
                if not TM.verify_password(b.get("current"), self.user.get("password")):
                    raise PermissionError("current password is wrong")
                team.throttle.ok(name, ip, ticket)
                team.store.reset_password(self.user["username"], b.get("new"))   # ends every other session
                self.user = team.store.get(self.user["username"])
                return self._new_session()
            act, name, st = b.get("action"), b.get("username"), team.store         # /api/team/users (admin)
            if act == "add":
                st.add(name, b.get("role"), b.get("password") or None, b.get("name") or "", b.get("email") or "",
                       b.get("sign_as"))
            elif act == "set-role":
                st.set_role(name, b.get("role"), b.get("sign_as"))
            elif act == "reset-password":
                st.reset_password(name, b.get("password"))
                team.throttle.unlock(TM.check_username(name))
            elif act == "remove":
                if TM.check_username(name) == self.user["username"]:
                    raise ValueError("you cannot remove yourself")
                st.remove(name)
            elif act == "unlock":
                team.throttle.unlock(TM.check_username(name))
            else:
                raise ValueError("action must be add, set-role, reset-password, remove or unlock")
            return self._send(200, self._users())

        def _too_many(self, wait):
            return self._send(429, {"error": f"too many failed sign-ins: try again in {max(1, wait // 60)} minute(s), "
                                             "or ask an admin to unlock you"}, headers={"Retry-After": str(wait)})

        def _new_session(self):
            tok, s = team.sessions.create(self.user["username"], int(self.user.get("gen") or 1))
            self.sess = s
            return self._send(200, self._session_info(), headers={"Set-Cookie": self._set_cookie(tok, team.sessions.remaining(s))})

        def _login(self, b):
            name, pw, ip = str(b.get("username") or "").strip().lower()[:64], b.get("password"), self._ip()
            self._fields = {"username": name}
            wait, ticket = team.throttle.attempt(name, ip)       # counted before the check: parallel tries too
            if wait:
                return self._too_many(wait)
            u = team.store.get(name)
            if not TM.verify_password(pw, u and u.get("password")):
                return self._send(401, {"error": "wrong user name or password"})
            team.throttle.ok(name, ip, ticket)
            self.user = u
            return self._new_session()

        # ---------------------------------------------------------- jobs
        @staticmethod
        def _librarian(bom, sources, refresh, progress=None):
            return _final_lines(progress, ws.librarian_run(bom, sources, (), refresh, _part_lines(progress)))

        @staticmethod
        def _demo(bom, user, progress=None):
            """Triage, then fill the library from the built-in standard parts only (no network, no API key)."""
            t = ws.triage(bom, _line_lines(progress, "Checking the BOM"), lock=write_lock, user=user)
            progress.line("__triage", state="done", text=f"BOM checked: {len(t['rows'])} lines")
            r = _final_lines(progress, ws.librarian_run(bom, ["standard"], (), None, _part_lines(progress)))
            return dict(r, triage=t)

        @staticmethod
        def _candidates(bom, part, kind, limit, progress=None):
            return ws.part_candidates(bom, part, kind, limit, progress)

        @staticmethod
        def _propose(paths, a365, choices, progress=None):
            return ws.propose_rules(paths, a365, choices, progress)

        @staticmethod
        def _learn_style(paths, direction, ai, model, progress=None):
            return ws.learn_house_style(paths, direction, ai, model, progress)

        @staticmethod
        def _triage(bom, user, progress=None):
            t = ws.triage(bom, _line_lines(progress), lock=write_lock, user=user)   # lock only while reports are written
            for r in t["rows"]:
                progress.line(f"line {r['line']}", state="done", text=f"{r['line']}/{len(t['rows'])} "
                              f"{r.get('mpn') or r.get('value') or r.get('designator') or ''}  {_triage_word(r.get('status'))}")
            return t

        def _error(self, e):
            if isinstance(e, Refused):
                return self._send(403, {"error": str(e)})
            if not isinstance(e, EXPECTED):
                traceback.print_exc()
            code = 409 if isinstance(e, FileExistsError) else 403 if isinstance(e, PermissionError) else 400
            return self._send(code, {"error": _msg(e, ws.root)})

    return H


class Server(ThreadingHTTPServer):
    daemon_threads = True
    tls = False
    team = None
    request_queue_size = 128                               # a burst of sign-ins must queue, not get connection resets

    def __init__(self, *a, **kw):
        self.per_ip, self.per_ip_lock = {}, threading.Lock()
        super().__init__(*a, **kw)

    def _limited(self, ip):
        return self.team is not None and int(self.team.cfg["max_connections_per_ip"]) > 0 and \
            ip not in self.team.cfg["trusted_proxies"]

    def process_request(self, request, client_address):
        """Team mode: at most max_connections_per_ip open connections per address (slow clients can't take every
        thread); more are closed at once. A trusted proxy (Caddy) is exempt: it carries everyone."""
        ip = client_address[0]
        if self._limited(ip):
            with self.per_ip_lock:
                if self.per_ip.get(ip, 0) >= int(self.team.cfg["max_connections_per_ip"]):
                    self.shutdown_request(request)
                    return
                self.per_ip[ip] = self.per_ip.get(ip, 0) + 1
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            ip = client_address[0]
            if self._limited(ip):
                with self.per_ip_lock:
                    self.per_ip[ip] = self.per_ip.get(ip, 1) - 1
                    if self.per_ip[ip] <= 0:
                        del self.per_ip[ip]

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ssl.SSLError, ConnectionError, TimeoutError, socket.timeout)):
            return                                          # bad handshakes / dropped clients: not worth a traceback
        super().handle_error(request, client_address)


def make_server(ws, host="127.0.0.1", port=0, team=None, cert=None, key=None):
    """The UI server (not started). cert/key: serve HTTPS with the standard library's ssl (TLS 1.2+)."""
    host = host.strip("[]")
    cls = type("Server6", (Server,), {"address_family": socket.AF_INET6}) if ":" in host else Server   # --host ::1 / ::
    srv = cls((host, port), make_handler(ws, team))
    srv.team = team
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        srv.tls = True
        if team:
            team.tls = True
    return srv


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib ui", description="ecadlib web UI (local, or --team for a shared server)")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library dir to index (repeatable)")
    ap.add_argument("--workspace", default="ecadlib_workspace")
    ap.add_argument("--providers", help="comma list; default all")
    ap.add_argument("--offline", action="store_true", help="use cached provider responses only")
    ap.add_argument("--host", default="127.0.0.1", help="loopback only without --team (127.0.0.1, ::1, localhost)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--setup", action="store_true", help="open the setup page (AI model and key, Altium / KiCad, folders)")
    t = ap.add_argument_group("team mode (shared server with sign-in; users: ecadlib team add-user ...)")
    t.add_argument("--team", action="store_true", help="require sign-in; roles from team_users.json in the workspace")
    t.add_argument("--cert", help="TLS certificate (PEM) for HTTPS")
    t.add_argument("--key", help="TLS private key (PEM)")
    t.add_argument("--allow-http", action="store_true",
                   help="plain HTTP on a non-loopback address (only behind an HTTPS reverse proxy on a trusted network)")
    t.add_argument("--allowed-host", action="append", default=[], help="extra Host name the server answers to (repeatable)")
    t.add_argument("--trusted-proxy", action="append", default=[],
                   help="IP of an SSO reverse proxy whose user header is believed (repeatable)")
    t.add_argument("--proxy-user-header", help="header carrying the signed-in user (default X-Forwarded-User)")
    a = ap.parse_args(argv)
    from .. import userconfig
    userconfig.apply_env()                                  # keys stored by the setup page (env variables win)
    if bool(a.cert) != bool(a.key):
        sys.exit("give both --cert and --key")
    for f in filter(None, (a.cert, a.key)):
        if not Path(f).is_file():
            sys.exit(f"no such file: {f}")
    team = None
    if not a.team:
        if not _loopback(a.host):
            sys.exit(f"refusing to serve on {a.host}: the UI has no login without --team, so anyone who can reach it "
                     "could edit the workspace and push to Altium 365 with your token. Keep the default 127.0.0.1; to "
                     "share it, run `ecadlib ui --team` (README: Team server), or forward the port over SSH "
                     "(ssh -L 8765:127.0.0.1:8765 host).")
    else:
        if not _loopback(a.host) and not a.cert and not a.allow_http:
            sys.exit(f"refusing to serve team mode on {a.host} over plain HTTP: passwords and session cookies would "
                     "cross the network unencrypted. Give --cert and --key, or put an HTTPS reverse proxy (Caddy, nginx) "
                     "in front and bind to 127.0.0.1, or (proxy on another host) add --allow-http.")
        try:
            team = TM.Team(Path(a.workspace).resolve(), {"allowed_hosts": a.allowed_host, "trusted_proxies": a.trusted_proxy,
                                                         "proxy_user_header": a.proxy_user_header})
        except (ValueError, json.JSONDecodeError) as e:
            sys.exit(f"team settings: {e}")
        if not team.has_admin():
            sys.exit("no admin yet: create one first (only the command line can):\n"
                     f"  ecadlib team --workspace {a.workspace} add-user YOURNAME --role admin --name \"Your Name\" --email you@example.com")
        if not _loopback(a.host) and not a.cert:
            print("\n" + "!" * 78 + f"\n!! WARNING: team mode on {a.host} over PLAIN HTTP (--allow-http). Passwords and\n"
                  "!! session cookies can be read by anyone on the network path. Only do this behind an HTTPS proxy.\n"
                  + "!" * 78 + "\n", file=sys.stderr)
    ws = Workspace(a.workspace, a.lib, a.providers.split(",") if a.providers else None, a.offline)
    try:
        srv = make_server(ws, a.host, a.port, team, a.cert, a.key)
    except ssl.SSLError as e:
        sys.exit(f"TLS certificate/key: {e}")
    except OSError as e:
        sys.exit(f"can't listen on {a.host}:{a.port} ({e.strerror}); is ecadlib already running? Try --port {a.port + 1}")
    shown = socket.getfqdn() if a.host.strip("[]") in ("0.0.0.0", "::") else a.host
    shown = f"[{shown}]" if ":" in shown and not shown.startswith("[") else shown       # IPv6 literal in a URL
    url = f"{'https' if a.cert else 'http'}://{shown}:{srv.server_address[1]}/"
    print(f"ecadlib UI on {url}  (workspace {ws.root}{'; team mode' if team else ''}; Ctrl+C to stop)")
    if a.setup and team:
        print("the setup page is for a local ecadlib (keys are per person); in team mode use the command line")
    if not a.no_browser and not team:
        webbrowser.open(url + ("setup" if a.setup else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
