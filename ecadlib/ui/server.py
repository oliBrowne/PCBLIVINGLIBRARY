"""Web UI: drag in a BOM, review the triage, generate and approve footprints, export to Altium.

    ecadlib ui --lib path/to/kicad/libs [--workspace ecadlib_workspace] [--port 8765] [--offline]
    (or: python -m ecadlib.ui ...)

Standard library only (http.server), bound to 127.0.0.1; a single-user desktop tool, provider API keys stay in
ecadlib's own key store (or the user's environment); the server never returns one."""
import argparse, contextlib, ipaddress, itertools, json, mimetypes, re, secrets, socket, sys, threading, os, time, \
    traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from ..app import WAITING, LibraryBusy, Workspace
from .. import easy_setup as ES

STATIC = Path(__file__).with_name("static")


def _save_bug(where, e):
    """An error ecadlib did not expect: saved on this computer for `ecadlib report-bug` / Report a bug."""
    from .. import bugreport
    from ..cliutil import friendly
    if friendly(e) is None:                    # a wrong path or a missing package is not a bug
        bugreport.record(where, e)
MAX_UPLOAD = 20 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024                 # JSON bodies: no 20 MB parse
EXPECTED = (ValueError, KeyError, FileExistsError, json.JSONDecodeError, PermissionError)   # user errors: no traceback
LOCAL_HEADERS = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}
# Actions that rewrite library files under change control: while another run holds the library lock they are queued
# as a job that waits for it (Handler._or_queue). Decisions (sign-off, release anyway) are recorded at once instead.
QUEUED = {"/api/resolve/choose", "/api/change/decide", "/api/change/revert", "/api/dedupe"}


class Cancelled(Exception):
    """Raised inside a job's progress callback once someone pressed Stop (checked between lines / parts)."""


class Jobs:
    """Long actions (triage looks parts up online, about a second per request) run in a thread; the page polls.
    A job has an optional key: a second job with the same key, e.g. the librarian on one BOM, is refused while the
    first runs. A job keeps a short list of
    per-part lines ({key, n, total, name, state, text}) for the live progress list, and can be stopped (cancel)."""
    MAX_LINES = 400

    def __init__(self, root=None):
        self.jobs, self.ids, self.lock = {}, itertools.count(1), threading.Lock()
        self.root = root
        self.threads = []

    def start(self, fn, *args, label="", key=None, tool=None):
        with self.lock:
            if key and self.running(key):
                raise FileExistsError(f"already running for {key.split(':', 1)[-1]}; wait for it to finish")
            jid = f"{next(self.ids)}-{secrets.token_hex(6)}"
            job = {"id": jid, "state": "running", "done": 0, "total": 0, "label": label, "result": None, "error": None,
                   "key": key, "lines": [], "cancel": False, "started": time.time(), "tool": tool}
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
                    _save_bug(f"web UI job: {tool or (key or 'job').split(':', 1)[0]}", e)
                job["error"], job["state"] = _msg(e, self.root), "error"
            job["seconds"] = round(time.time() - job["started"], 1)
        th = threading.Thread(target=run, daemon=True)
        self.threads = [t for t in self.threads if t.is_alive()] + [th]
        th.start()
        return {"job": jid}

    def wait(self, timeout=30):
        """Wait for the running jobs' threads (tests, before their workspace folder is removed)."""
        end = time.time() + timeout
        for th in list(self.threads):
            th.join(max(0, end - time.time()))

    def running(self, key):
        return next((j for j in list(self.jobs.values()) if j["key"] == key and j["state"] == "running"), None)

    def get(self, jid):
        job = self.jobs.get(jid)
        if not job:
            return None
        out = {k: v for k, v in job.items() if k not in ("key", "lines")}
        out["lines"] = [dict(x) for x in list(job["lines"])]
        out["elapsed"] = job.get("seconds") or round(time.time() - job["started"], 1)
        return out

    def cancel(self, jid):
        """Ask a running job to stop at its next line or part. -> the job, or None when unknown."""
        if self.get(jid) is None:
            return None
        job = self.jobs[jid]
        if job["state"] == "running":
            job["cancel"] = True
        return self.get(jid)


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


def _usd(v):
    """An approved amount from the page: a number of dollars, or None (not approved / no price known)."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 1e6:
        raise ValueError("approved_usd must be a number of dollars")
    return float(v)


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


_LOCAL_WHO = []


def local_who():
    """This computer's user, as approvals records it (ECADLIB_USER, git config, login name); looked up once."""
    if not _LOCAL_WHO:
        from ..approvals import identity
        _LOCAL_WHO.append(identity())
    return _LOCAL_WHO[0]


def _no_constant(c):
    """json.loads takes NaN / Infinity, which JSON itself does not: refused (a 400), never handed to int() / float()."""
    raise ValueError(f"{c} is not a number JSON allows")


def _list_of_str(v, what):
    """None, or a list of names (librarian sources, parts to look up again) from a JSON body."""
    if v is None:
        return None
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ValueError(f"{what} must be a list of names")
    return v


def _strs(v, what):
    """A list of path strings from a JSON body (scan sources: any folder the person types, read for names only)."""
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) and len(x) < 1000 and "\0" not in x for x in v):
        raise ValueError(f"{what} must be a list of paths")
    return [x for x in v if x.strip()]


def make_handler(ws: Workspace):
    jobs = Jobs(ws.root)
    write_lock = threading.Lock()                          # one writer at a time in the workspace

    class H(BaseHTTPRequestHandler):
        server_version = "ecadlib-ui/0.1"
        job_runner = jobs
        _body_read = False

        def log_message(self, fmt, *args):
            sys.stderr.write("ui: " + fmt % args + "\n")

        def _send(self, code, body, ctype="application/json", headers=None):
            data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in LOCAL_HEADERS.items():             # no other page may frame the UI (clickjacking)
                self.send_header(k, v)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            """Refuse DNS-rebinding requests: the Host header must name this server's own loopback address."""
            port = self.server.server_address[1]
            names = {"127.0.0.1", "localhost", "[::1]", self.server.server_address[0]}
            ok = {f"{h}:{port}" for h in names} | (names if port == 80 else set())   # browsers leave out :80
            return self.headers.get("Host") in ok

        def _origin_ok(self):
            """Stop other sites driving the API: an Origin, when sent, must be this server."""
            return self.headers.get("Origin") in (None, f"http://{self.headers.get('Host')}")

        def _gate(self, method):
            """Checks before any route runs. Returns the parsed URL, or None after sending a refusal."""
            u = urlparse(self.path)
            if not self._host_ok():
                return self._send(403, {"error": "unexpected Host header"})
            if method == "POST" and not self._origin_ok():
                return self._send(403, {"error": "cross-origin request refused"})
            if u.path.startswith("/api/") and not (ws.root / "uploads").is_dir():   # the workspace folder was deleted or
                with contextlib.suppress(OSError):                                   # moved while this runs: make it again
                    ws.ensure_folders()
            return u

        # ---------------------------------------------------------- request bodies
        def _bom(self, body=None, write=False):
            """Web clients may only name BOMs inside the workspace (absolute paths would read any file on disk)."""
            bom = (body if body is not None else self._json()).get("bom")
            if not isinstance(bom, str) or not bom:
                raise ValueError("no BOM given")
            if len(bom) > 1000 or any(len(x) > 200 for x in bom.replace("\\", "/").split("/")):
                raise ValueError("that BOM name is too long; pick a BOM from the list")
            ws.inside(bom)                                 # refuses a path outside the workspace
            if write and jobs.running(self._plan_key(bom)):
                raise FileExistsError(f"the librarian is still running on {bom}; try again when it has finished")
            return bom

        @staticmethod
        def _plan_key(bom):
            """One librarian job per plan: two uploads of BOM.csv share one plan folder (Workspace._key)."""
            try:
                return "librarian:" + ws._key(ws._bom_path(bom))
            except (ValueError, OSError):
                return "librarian:" + bom

        def _body(self, limit=MAX_UPLOAD):
            self._body_read = True
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
            try:
                b = json.loads(self._body(MAX_JSON) or b"{}", parse_constant=_no_constant)
            except RecursionError:
                raise ValueError("the JSON body is nested too deeply") from None
            if not isinstance(b, dict):
                raise ValueError("expected a JSON object")
            return b

        # ---------------------------------------------------------- routes
        def do_GET(self):
            u = self._gate("GET")
            if u is None:
                return
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if u.path == "/setup":                         # setup page (ecadlib.easy_setup)
                    return self._send(200, (STATIC / "setup.html").read_bytes(), "text/html; charset=utf-8")
                if u.path in ES.GET_ROUTES:
                    return self._send(200, ES.GET_ROUTES[u.path](ws.root, q))
                if u.path == "/hairline.js":                    # the line figures (scripts/build_ui_figures.sh)
                    return self._send(200, (STATIC / "hairline.js").read_bytes(), "text/javascript; charset=utf-8")
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
                if u.path == "/api/bugreport":                  # the last saved error as a GitHub issue link:
                    from .. import bugreport                    # the page shows it; nothing is sent from here
                    return self._send(200, bugreport.report_for())
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
                    bom = self._bom({"bom": q.get("bom")})
                    if q.get("ifplan") == "1" and not ws.has_plan(bom):   # no plan yet is no error (a new BOM)
                        return self._send(200, {"plan": None})
                    return self._send(200, ws.library_parts(bom, q.get("pictures") != "0"))
                if u.path == "/api/job":                        # a running job (its live log)
                    job = jobs.get(q.get("id", ""))
                    return self._send(200, job) if job else self._send(404, {"error": "no such job"})
                if u.path == "/api/file":                       # download anything inside the workspace
                    p = ws.inside(q.get("path", ""))
                    if not p.is_file():
                        return self._send(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    plain = re.sub(r'[^\x20-\x7e]|["\\]', "_", p.name)   # headers are latin-1: UTF-8 name in filename*
                    return self._send(200, p.read_bytes(), ctype,
                                      {"Content-Disposition": f'attachment; filename="{plain}"'
                                                              + (f"; filename*=UTF-8''{quote(p.name, errors='replace')}" if plain != p.name else "")})
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._error(e)

        def do_POST(self):
            self._body_read = False
            try:
                u = self._gate("POST")
                if u is None:
                    return
                try:
                    return self._post(u)
                except Exception as e:
                    return self._error(e)
            finally:
                self._drain()

        def _drain(self):
            """Read and drop a small POST body nobody read (a refused request): Windows resets a connection closed
            with unread data, and the client loses the answer it was already sent."""
            if self._body_read:
                return
            self._body_read = True
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= 65536:
                    self.rfile.read(n)
                elif n:
                    self.close_connection = True
            except (OSError, ValueError):
                self.close_connection = True

        def _post(self, u):
            if u.path in ES.POST_ROUTES:                # setup page (ecadlib.easy_setup)
                b = self._json()
                with write_lock if u.path in ES.WRITES else contextlib.nullcontext():
                    page_libs = lambda: [str(Path(x).resolve()) for x in ES.ws_libs(ws.root)]
                    before = page_libs() if u.path == "/api/setup" else None
                    try:
                        out = ES.POST_ROUTES[u.path](ws.root, b)
                    except (ValueError, PermissionError) as e:   # about folders the person typed on this page:
                        code = 403 if isinstance(e, PermissionError) else 400   # their full paths, not file names
                        return self._send(code, {"error": (str(e) or type(e).__name__)[:600]})
                    if before is not None and out.get("saved") and not out.get("restart"):
                        after = page_libs()             # saved here: this server uses the page's folders at once
                        ws.libs = [x for x in ws.libs if x not in before or x in after] + [
                            x for x in after if x not in ws.libs]
                    return self._send(200, out)
            if u.path == "/api/footprint/preview":                # read-only, no lock
                b = self._json()
                return self._send(200, ws.preview_footprint(b["spec"], b.get("density")))
            if u.path == "/api/job/cancel":                       # Stop: the job ends at its next line or part
                job = jobs.cancel(_str(self._json().get("id"), "id"))
                return self._send(200, job) if job else self._send(404, {"error": "no such job"})
            if u.path == "/api/demo":                             # first visit: the bundled sample BOM, offline
                self._json()
                with write_lock:
                    rel = ws.demo_bom()
                return self._send(200, dict(jobs.start(self._demo, rel, label=rel, key=self._plan_key(rel)), bom=rel))
            if u.path == "/api/upload":
                name = unquote(self.headers.get("X-Filename", "bom.csv"))
                with write_lock:
                    rel = ws.save_upload(name, self._body())
                return self._send(200, jobs.start(self._triage, rel, label=rel))
            if u.path == "/api/triage":
                bom = self._bom()
                return self._send(200, jobs.start(self._triage, bom, label=bom))
            if u.path == "/api/librarian/run":                    # long, network-bound: a background job
                b = self._json()
                bom = self._bom(b)
                refresh = [] if b.get("refresh") is True else _list_of_str(b.get("refresh") or None, "refresh")
                return self._send(200, jobs.start(self._librarian, bom, _list_of_str(b.get("sources"), "sources"), refresh, label=bom,
                                                  key=self._plan_key(bom)))
            if u.path == "/api/ai/quote":                         # downloads datasheets (cached) to price them: a job
                bom = self._bom(self._json())
                return self._send(200, jobs.start(self._ai_quote, bom, label=bom))
            if u.path == "/api/ai/read":                          # paid model calls the person approved: a job
                b = self._json()
                bom = self._bom(b)
                return self._send(200, jobs.start(self._ai_read, bom, _usd(b.get("approved_usd")), b.get("over_limit_ok") is True,
                                                  b.get("price_unknown_ok") is True, label=bom, key=self._plan_key(bom)))
            if u.path == "/api/style/guide":                      # the model writes the guide: a job
                b = self._json()
                return self._send(200, jobs.start(self._style_guide, _usd(b.get("approved_usd")), b.get("over_limit_ok") is True,
                                                  b.get("price_unknown_ok") is True, _str(b.get("model"), "model", True) or None,
                                                  label="house style"))
            if u.path == "/api/resolve/candidates":               # may fetch KiCad library files: a background job
                b = self._json()
                bom, part = self._bom(b), _str(b.get("part"), "part")
                ws.held_parts(bom)                                # no plan yet: answer now, not from the job
                return self._send(200, jobs.start(self._candidates, bom, part, b.get("kind") or None,
                                                  b.get("limit") or 10, label=part))
            if u.path == "/api/rules/propose":                    # reads library folders the person names: a job
                b = self._json()
                paths, a365 = _strs(b.get("paths"), "paths"), _strs(b.get("a365"), "a365")
                choices = b.get("choices") or {}
                if not isinstance(choices, dict):
                    raise ValueError("choices must map setting keys to values")
                return self._send(200, jobs.start(self._propose, paths, a365, choices, label="library rules"))
            if u.path == "/api/style/learn":                      # scans library folders, maybe asks the model: a job
                b = self._json()
                return self._send(200, jobs.start(self._learn_style, _strs(b.get("paths"), "paths"),
                                                  _strs(b.get("direction"), "direction"), b.get("ai") is True,
                                                  _str(b.get("model"), "model", True) or None, label="house style"))
            if u.path == "/api/altium365/workspaces":             # read-only network call, no lock
                self._json()
                return self._send(200, ws.altium365_workspaces())
            actions = {
                "/api/footprint/approve": lambda b: ws.approve_footprint(b["spec"], b.get("density"), bool(b.get("overwrite"))),
                "/api/export/dblib": lambda b: ws.export_dblib(self._bom(b, True)),
                "/api/altium/models": lambda b: ws.build_altium_models(self._bom(b, True)),
                "/api/altium365/plan": lambda b: ws.altium365_plan(self._bom(b, True)),
                "/api/release-check": lambda b: ws.release_check(self._bom(b), _names(b.get("allow")), _names(b.get("unallow")),
                                                                 _str(b.get("user"), "user", True)),
                "/api/resolve/held": lambda b: ws.held_parts(self._bom(b)),
                "/api/resolve/choose": lambda b: ws.choose_part(self._bom(b), _str(b.get("part"), "part"), b.get("kind"),
                                                                _str(b.get("id"), "id"), b.get("pin_map") or None,
                                                                self._who(b)),
                "/api/approvals/status": lambda b: ws.approval_status(self._bom(b)),
                "/api/approvals/decide": self._decide,
                "/api/change/history": lambda b: ws.part_history(self._bom(b), _str(b.get("part"), "part")),
                "/api/change/decide": self._change_decide,
                "/api/change/revert": lambda b: ws.revert_part(self._bom(b), _str(b.get("part"), "part"), b.get("revision"),
                                                               _str(b.get("user"), "user"), _str(b.get("comment"), "comment", True)),
                "/api/dedupe": lambda b: ws.find_duplicates(self._bom(b), b.get("merge") is True),
                "/api/rules/save": lambda b: ws.save_rules(_str(b.get("text"), "text"), b.get("overwrite") is True),
                "/api/style/direction": lambda b: ws.style_direction(_str(b.get("text"), "text", True) or None,
                                                                     b.get("clear") is True),
                "/api/altium365/push": lambda b: ws.altium365_push(self._bom(b, True),
                                                                   _str(b.get("workspace"), "workspace", True),
                                                                   dry_run=b.get("dry_run", True) is not False),
            }
            if u.path in actions:
                b = self._json()
                if u.path in QUEUED:
                    return self._send(200, self._or_queue(lambda: actions[u.path](b), self._bom(b)))
                with write_lock:
                    return self._send(200, actions[u.path](b))
            return self._send(404, {"error": "not found"})

        def _or_queue(self, fn, bom):
            """Run fn now; while another run holds the library lock (a librarian run here or from the command line,
            a publisher), queue it instead: a job that waits for the lock and then applies it, reported like any job.
            The person's action is never refused or lost for that, and no request waits for the run. While this
            server's own librarian job runs on the plan, it is queued at once (that run may not hold the lock yet)."""
            key = self._plan_key(bom)
            queue = lambda folder: dict(jobs.start(self._when_free, fn, folder, key, label=WAITING), queued=True,
                                        waiting=WAITING)
            if jobs.running(key):
                return queue(ws._librarian_dir(ws._bom_path(bom)))
            try:
                with write_lock:
                    return fn()
            except LibraryBusy as e:
                return queue(e.folder)

        @staticmethod
        def _when_free(fn, folder, key, progress=None):
            from .. import ledger as LG
            while True:
                progress(0, 0, WAITING)                       # (raises Cancelled once Stop was pressed)
                if jobs.running(key):                         # this server's librarian job on the plan
                    time.sleep(0.25)
                    continue
                try:
                    LG.unlock(LG.lock(folder, wait=1))       # the run let go of the library
                except LG.LedgerError:
                    continue
                try:
                    with write_lock:
                        progress(0, 0, "applying it now")
                        return fn()
                except LibraryBusy:                           # taken again in between: wait on
                    continue

        def _who(self, b):
            """Who did it: the name typed in the page, else this computer's user (git config / login name)."""
            typed = b.get("user") if isinstance(b.get("user"), str) else ""
            return typed.strip()[:120] or local_who()

        def _change_decide(self, b):
            return ws.decide_change(self._bom(b), _str(b.get("part"), "part"), b.get("decision"),
                                    _str(b.get("user"), "user"), _str(b.get("comment"), "comment", True),
                                    _str(b.get("role"), "role", True), _str(b.get("digest"), "digest", True))

        def _decide(self, b):
            return ws.approve_part(self._bom(b), _str(b.get("part"), "part"), b.get("decision"),
                                   _str(b.get("user"), "user"), _str(b.get("role"), "role", True),
                                   _str(b.get("comment"), "comment", True))

        # ---------------------------------------------------------- jobs
        @staticmethod
        def _librarian(bom, sources, refresh, progress=None):
            # never a paid model call here: parts that need a datasheet read come back priced ("ai"), see /api/ai/read
            return _final_lines(progress, ws.librarian_run(bom, sources, (), refresh, _part_lines(progress), ai=False))

        @staticmethod
        def _ai_quote(bom, progress=None):
            if progress:
                progress(0, 0, "pricing the datasheet reads")
            return {"ai": ws.ai_quote(bom)}

        @staticmethod
        def _ai_read(bom, approved, over_ok, unknown_ok, progress=None):
            return _final_lines(progress, ws.ai_read(bom, approved, over_ok, unknown_ok, progress=progress))

        @staticmethod
        def _style_guide(approved, over_ok, unknown_ok, model, progress=None):
            return ws.style_guide(approved, over_ok, unknown_ok, model, progress)

        @staticmethod
        def _demo(bom, progress=None):
            """Triage, then fill the library from the built-in standard parts only (no network, no API key)."""
            t = ws.triage(bom, _line_lines(progress, "Checking the BOM"), lock=write_lock, offline=True)   # as `ecadlib demo`
            progress.line("__triage", state="done", text=f"BOM checked: {len(t['rows'])} lines")
            r = _final_lines(progress, ws.librarian_run(bom, ["standard"], (), None, _part_lines(progress), ai=False,
                                                        offline=True))
            return dict(r, triage=t)

        @staticmethod
        def _candidates(bom, part, kind, limit, progress=None):
            return ws.part_candidates(bom, part, kind, limit, progress)

        @staticmethod
        def _propose(paths, a365, choices, progress=None):
            return ws.propose_rules(paths, a365, choices, progress)

        @staticmethod
        def _learn_style(paths, direction, ai, model, progress=None):
            return ws.learn_house_style(paths, direction, ai, model, progress, ask_first=True)   # the guide: /api/style/guide

        @staticmethod
        def _triage(bom, progress=None):
            t = ws.triage(bom, _line_lines(progress), lock=write_lock)   # lock only while reports are written
            for r in t["rows"]:
                progress.line(f"line {r['line']}", state="done", text=f"{r['line']}/{len(t['rows'])} "
                              f"{r.get('mpn') or r.get('value') or r.get('designator') or ''}  {_triage_word(r.get('status'))}")
            return t

        def _error(self, e):
            if not isinstance(e, EXPECTED):
                traceback.print_exc()
                _save_bug(f"web UI: {urlparse(self.path).path}", e)
            code = 409 if isinstance(e, FileExistsError) else 403 if isinstance(e, PermissionError) else 400
            return self._send(code, {"error": _msg(e, ws.root)})

    return H


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128                               # a burst of requests must queue, not get connection resets

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, TimeoutError, socket.timeout)):
            return                                          # dropped clients: not worth a traceback
        super().handle_error(request, client_address)


def make_server(ws, host="127.0.0.1", port=0):
    """The UI server (not started)."""
    host = host.strip("[]")
    cls = type("Server6", (Server,), {"address_family": socket.AF_INET6}) if ":" in host else Server   # --host ::1
    srv = cls((host, port), make_handler(ws))
    srv.jobs = srv.RequestHandlerClass.job_runner
    return srv


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib ui", description="ecadlib web UI (local, single user)")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library dir to index (repeatable)")
    ap.add_argument("--workspace", default="ecadlib_workspace")
    ap.add_argument("--providers", help="comma list; default all")
    ap.add_argument("--offline", action="store_true", help="use cached provider responses only")
    ap.add_argument("--host", default="127.0.0.1", help="loopback only (127.0.0.1, ::1, localhost)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--setup", action="store_true", help="open the setup page (AI model and key, Altium / KiCad, folders)")
    a = ap.parse_args(argv)
    if not 0 <= a.port <= 65535:                            # bind() raises OverflowError: a typo, not a bug
        ap.error("--port must be from 0 to 65535")
    from .. import userconfig
    userconfig.apply_env()                                  # kicad-cli on PATH; stored keys are read when needed
    if not _loopback(a.host):
        sys.exit(f"refusing to serve on {a.host}: the UI has no login, so anyone who can reach it could edit the "
                 "workspace and push to Altium 365 with your token. Keep the default 127.0.0.1; to use it from another "
                 "machine, forward the port over SSH (ssh -L 8765:127.0.0.1:8765 host).")
    ws = Workspace(a.workspace, a.lib, a.providers.split(",") if a.providers else None, a.offline)
    try:
        srv = make_server(ws, a.host, a.port)
    except OSError as e:
        sys.exit(f"can't listen on {a.host}:{a.port} ({e.strerror}); is ecadlib already running? Try --port {a.port + 1}")
    shown = f"[{a.host}]" if ":" in a.host and not a.host.startswith("[") else a.host   # IPv6 literal in a URL
    url = f"http://{shown}:{srv.server_address[1]}/"
    print(f"ecadlib UI on {url}{'setup' if a.setup else ''}  (workspace {ws.root}; Ctrl+C to stop)")
    if not a.no_browser:                    # a thread: a console browser (w3m, lynx) waits for the page we serve below
        threading.Thread(target=webbrowser.open, args=(url + ("setup" if a.setup else ""),), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
