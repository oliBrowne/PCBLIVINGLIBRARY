"""Local web UI: drag in a BOM, review the triage, generate and approve footprints, export to Altium.

    python -m ecadlib.ui --lib path/to/kicad/libs [--workspace ecadlib_workspace] [--port 8765] [--offline]

Standard library only (http.server). It binds to 127.0.0.1 by default: this is a single-user
desktop tool, not a hosted service, and provider API keys stay in the user's environment."""
import argparse, json, mimetypes, sys, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..app import Workspace

STATIC = Path(__file__).with_name("static")
MAX_UPLOAD = 20 * 1024 * 1024


def make_handler(ws: Workspace):
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

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                raise ValueError("upload too large")
            return self.rfile.read(n)

        def _json(self):
            return json.loads(self._body() or b"{}")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if u.path == "/api/info":
                    return self._send(200, ws.info())
                if u.path == "/api/specs":
                    return self._send(200, ws.example_specs())
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
            if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self._send(403, {"error": "cross-origin request refused"})   # stop other sites driving the API
            try:
                if u.path == "/api/upload":
                    name = self.headers.get("X-Filename", "bom.csv")
                    rel = ws.save_upload(name, self._body())
                    return self._send(200, ws.triage(rel))
                if u.path == "/api/triage":
                    return self._send(200, ws.triage(self._json()["bom"]))
                if u.path == "/api/footprint/preview":
                    b = self._json()
                    return self._send(200, ws.preview_footprint(b["spec"], b.get("density")))
                if u.path == "/api/footprint/approve":
                    b = self._json()
                    return self._send(200, ws.approve_footprint(b["spec"], b.get("density")))
                if u.path == "/api/export/dblib":
                    return self._send(200, ws.export_dblib(self._json()["bom"]))
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._error(e)

        def _error(self, e):
            traceback.print_exc()
            msg = getattr(e, "message", None) or str(e) or type(e).__name__
            return self._send(400, {"error": f"{type(e).__name__}: {msg}"[:500]})

    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description="ecadlib local web UI")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library dir to index (repeatable)")
    ap.add_argument("--workspace", default="ecadlib_workspace")
    ap.add_argument("--providers", help="comma list; default all")
    ap.add_argument("--offline", action="store_true", help="use cached provider responses only")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    ws = Workspace(a.workspace, a.lib, a.providers.split(",") if a.providers else None, a.offline)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(ws))
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
