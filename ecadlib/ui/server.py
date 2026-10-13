"""Local web UI: drag in a BOM, review the triage, generate and approve footprints, export to Altium.

    ecadlib ui --lib path/to/kicad/libs [--workspace ecadlib_workspace] [--port 8765] [--offline]
    (or: python -m ecadlib.ui ...)

Standard library only (http.server). It binds to 127.0.0.1 by default: this is a single-user
desktop tool, not a hosted service, and provider API keys stay in the user's environment."""
import argparse, itertools, json, mimetypes, sys, threading, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..app import Workspace

STATIC = Path(__file__).with_name("static")
MAX_UPLOAD = 20 * 1024 * 1024
EXPECTED = (ValueError, KeyError, FileExistsError, json.JSONDecodeError)   # user errors: no traceback in the log


class Jobs:
    """Long actions (triage looks parts up online, about a second per request) run in a thread; the page polls."""
    def __init__(self):
        self.jobs, self.ids, self.lock = {}, itertools.count(1), threading.Lock()

    def start(self, fn, *args, label=""):
        jid = str(next(self.ids))
        job = {"id": jid, "state": "running", "done": 0, "total": 0, "label": label, "result": None, "error": None}
        with self.lock:
            self.jobs[jid] = job
            for old in list(self.jobs)[:-20]:              # keep the last 20
                del self.jobs[old]

        def progress(done, total, lab):
            job.update(done=done, total=total, label=lab)

        def run():
            try:
                job["result"] = fn(*args, progress=progress)
                job["state"] = "done"
            except Exception as e:
                if not isinstance(e, EXPECTED):
                    traceback.print_exc()
                job["error"], job["state"] = _msg(e), "error"
        threading.Thread(target=run, daemon=True).start()
        return {"job": jid}

    def get(self, jid):
        return self.jobs.get(jid)


def _msg(e):
    m = str(e) or type(e).__name__
    return (m if isinstance(e, (ValueError, FileExistsError)) else f"{type(e).__name__}: {m}")[:600]


def make_handler(ws: Workspace):
    jobs = Jobs()
    write_lock = threading.Lock()                          # one writer at a time in the workspace

    class H(BaseHTTPRequestHandler):
        server_version = "ecadlib-ui/0.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("ui: " + fmt % args + "\n")

        def _send(self, code, body, ctype="application/json", headers=None):
            data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            """Refuse DNS-rebinding requests: the Host header must name this server's own loopback address."""
            port = self.server.server_address[1]
            names = {"127.0.0.1", "localhost", "[::1]", self.server.server_address[0]}
            return self.headers.get("Host") in {f"{h}:{port}" for h in names}

        def _bom(self, body=None):
            """Web clients may only name BOMs inside the workspace (absolute paths would read any file on disk)."""
            bom = (body if body is not None else self._json()).get("bom")
            if not isinstance(bom, str) or not bom:
                raise ValueError("no BOM given")
            ws.inside(bom)
            return bom

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > MAX_UPLOAD:
                raise ValueError("bad or too large Content-Length")
            return self.rfile.read(n)

        def _json(self):
            b = json.loads(self._body() or b"{}")
            if not isinstance(b, dict):
                raise ValueError("expected a JSON object")
            return b

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not self._host_ok():
                return self._send(403, {"error": "unexpected Host header"})
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if u.path == "/api/info":
                    return self._send(200, ws.info())
                if u.path == "/api/specs":
                    return self._send(200, ws.example_specs())
                if u.path == "/api/job":
                    job = jobs.get(q.get("id", ""))
                    return self._send(200, job) if job else self._send(404, {"error": "no such job"})
                if u.path == "/api/file":                       # download anything inside the workspace
                    p = ws.inside(q.get("path", ""))
                    if not p.is_file():
                        return self._send(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    return self._send(200, p.read_bytes(), ctype,
                                      {"Content-Disposition": f'attachment; filename="{p.name}"'})
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._error(e)

        def do_POST(self):
            u = urlparse(self.path)
            if not self._host_ok():
                return self._send(403, {"error": "unexpected Host header"})
            if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self._send(403, {"error": "cross-origin request refused"})   # stop other sites driving the API
            try:
                if u.path == "/api/footprint/preview":                # read-only, no lock
                    b = self._json()
                    return self._send(200, ws.preview_footprint(b["spec"], b.get("density")))
                if u.path == "/api/upload":
                    name = unquote(self.headers.get("X-Filename", "bom.csv"))
                    with write_lock:
                        rel = ws.save_upload(name, self._body())
                    return self._send(200, jobs.start(self._locked(ws.triage), rel, label=rel))
                if u.path == "/api/triage":
                    bom = self._bom()
                    return self._send(200, jobs.start(self._locked(ws.triage), bom, label=bom))
                actions = {
                    "/api/footprint/approve": lambda b: ws.approve_footprint(b["spec"], b.get("density"), bool(b.get("overwrite"))),
                    "/api/export/dblib": lambda b: ws.export_dblib(self._bom(b)),
                    "/api/altium/models": lambda b: ws.build_altium_models(self._bom(b)),
                    "/api/altium365/plan": lambda b: ws.altium365_plan(self._bom(b)),
                    "/api/altium365/workspaces": lambda b: ws.altium365_workspaces(),
                    "/api/altium365/push": lambda b: ws.altium365_push(self._bom(b), b.get("workspace"),
                                                                       dry_run=b.get("dry_run", True) is not False),
                }
                if u.path in actions:
                    b = self._json()
                    with write_lock:
                        return self._send(200, actions[u.path](b))
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._error(e)

        @staticmethod
        def _locked(fn):
            def run(*a, **k):
                with write_lock:
                    return fn(*a, **k)
            return run

        def _error(self, e):
            if not isinstance(e, EXPECTED):
                traceback.print_exc()
            return self._send(409 if isinstance(e, FileExistsError) else 400, {"error": _msg(e)})

    return H


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib ui", description="ecadlib local web UI")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library dir to index (repeatable)")
    ap.add_argument("--workspace", default="ecadlib_workspace")
    ap.add_argument("--providers", help="comma list; default all")
    ap.add_argument("--offline", action="store_true", help="use cached provider responses only")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    ws = Workspace(a.workspace, a.lib, a.providers.split(",") if a.providers else None, a.offline)
    try:
        srv = ThreadingHTTPServer((a.host, a.port), make_handler(ws))
    except OSError as e:
        sys.exit(f"can't listen on {a.host}:{a.port} ({e.strerror}); is ecadlib already running? Try --port {a.port + 1}")
    url = f"http://{a.host}:{srv.server_address[1]}/"
    print(f"ecadlib UI on {url}  (workspace {ws.root}; Ctrl+C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
