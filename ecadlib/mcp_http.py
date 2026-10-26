"""MCP over Streamable HTTP, for clients that cannot start a local process: the xAI (Grok) API's remote MCP tool,
Codex or Claude Code pointed at a URL, and anything behind a tunnel.

    ecadlib mcp --http --workspace /path/to/workspace [--host 127.0.0.1] [--port 8766] [--token ...]

One endpoint (default /mcp): POST a JSON-RPC message, get the answer as application/json (no server-sent events: the
tools answer within the --wait time or hand back a job id). GET answers 405, as the spec allows for a server that
never pushes. Same tools and handling as stdio (ecadlib.mcp_server.handle).

Safety, because a tunnel puts this on the internet even when it listens on 127.0.0.1:
- every request needs "Authorization: Bearer <token>" (or the bare token) (--token / ECADLIB_MCP_TOKEN, else a new one is printed);
  --no-auth is refused unless the server listens on a loopback address, and then the Host header must be local too;
- a request from a web page (an Origin header) is refused unless the origin is local or given with --allow-origin;
  only an origin given with --allow-origin gets the CORS answers (OPTIONS preflight, Access-Control-Allow-Origin) a
  browser needs to read the reply, and it needs the token like any request (with --no-auth, none);
- file arguments must be inside the workspace or a --lib folder (--any-path lifts that);
- bodies over 10 MB are refused. Standard library only."""
import hmac, http.server, ipaddress, json, secrets, sys, threading
from urllib.parse import urlsplit

from . import mcp_server as M

MAX_BODY = 10 * 1024 * 1024
LOCAL = {"localhost", "127.0.0.1", "::1"}


def _loopback(host):
    host = (host or "").strip("[]")
    if host in LOCAL:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname(value):
    """'example.com:8766' / '[::1]:8766' / 'https://x.y' -> the bare host name, lower case."""
    v = value if "//" in value else "//" + value
    try:
        return (urlsplit(v).hostname or "").lower()
    except ValueError:
        return ""


def make_server(ws, host="127.0.0.1", port=8766, path="/mcp", token=None, origins=(), quiet=False):
    """A ThreadingHTTPServer serving MCP at path. token None = no auth (only allowed on a loopback host)."""
    if token is None and not _loopback(host):
        raise ValueError(f"--no-auth is only allowed on a loopback address (127.0.0.1, localhost), not {host}")
    allowed_origins = {o.rstrip("/").lower() for o in origins}

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "ecadlib-mcp"
        protocol_version = "HTTP/1.1"
        timeout = 300                                       # a client that sends less than its Content-Length (or
                                                            # goes quiet on an open connection) frees its thread

        def log_message(self, fmt, *args):                  # request lines only: never headers, so never the token
            if not quiet:
                sys.stderr.write("mcp-http %s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status, body=None, headers=None):
            data = b"" if body is None else (body if isinstance(body, bytes) else
                                             json.dumps(body, ensure_ascii=False).encode("utf-8", "backslashreplace"))
            self.send_response(status)
            if status >= 400:                               # the body may be unread: never parse it as a request
                self.close_connection = True
                self.send_header("Connection", "close")
            for k, v in dict(self._cors(), **(headers or {})).items():
                self.send_header(k, v)
            if body is not None:
                self.send_header("Content-Type", "application/json" if not isinstance(body, bytes) else "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if data and self.command != "HEAD":
                self.wfile.write(data)

        def _cors(self):
            """CORS headers for a page at an origin given with --allow-origin (no other origin, not even a local one)."""
            origin = self.headers.get("Origin") or ""
            if not origin or origin.rstrip("/").lower() not in allowed_origins:
                return {"Vary": "Origin"} if origin else {}
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Expose-Headers": "Mcp-Session-Id"}

        def _refuse(self):
            """None when the request may go on, else the refusal already sent."""
            origin = self.headers.get("Origin")
            if origin and origin != "null" and _hostname(origin) not in LOCAL \
                    and origin.rstrip("/").lower() not in allowed_origins:
                return self._send(403, {"error": "origin not allowed (start the server with --allow-origin to allow it)"}) or True
            if origin == "null":
                return self._send(403, {"error": "origin not allowed"}) or True
            if token is None:                               # no token: only a local client, by name (DNS rebinding)
                if not _loopback(_hostname(self.headers.get("Host", ""))):
                    return self._send(403, {"error": "host not allowed"}) or True
                return None
            auth = self.headers.get("Authorization", "")
            given = auth[7:].strip() if auth[:7].lower() == "bearer " else auth.strip()   # some clients send it bare
            if not hmac.compare_digest(given.encode(), token.encode()):
                return self._send(401, {"error": "missing or wrong bearer token"},
                                  {"WWW-Authenticate": 'Bearer realm="ecadlib-mcp"'}) or True
            return None

        def _chunked(self):
            """A Transfer-Encoding: chunked body -> bytes; None when it is over MAX_BODY, False when it is malformed."""
            out = bytearray()
            while True:
                line = self.rfile.readline(1024)
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError:
                    return False
                if size < 0:
                    return False
                if size == 0:
                    while self.rfile.readline(8192).strip():    # trailer fields, up to the blank line
                        pass
                    return bytes(out)
                if len(out) + size > MAX_BODY:
                    return None
                data = self.rfile.read(size)
                if len(data) < size or self.rfile.readline(1024).strip():   # cut short, or no CRLF after the data
                    return False
                out += data

        def _ours(self):
            return urlsplit(self.path).path.rstrip("/") == path.rstrip("/")

        def do_GET(self):
            if urlsplit(self.path).path in ("/", "/health"):  # liveness for tunnels and uptime checks: says nothing
                return self._send(200, b"ecadlib MCP server: POST JSON-RPC to " + path.encode() + b"\n")
            if not self._ours():
                return self._send(404, {"error": "not found"})
            if self._refuse():
                return
            self._send(405, {"error": "this server does not push messages: POST requests only"}, {"Allow": "POST"})

        def do_OPTIONS(self):                               # CORS preflight: a browser asks before it POSTs
            if not self._ours():
                return self._send(404, {"error": "not found"})
            if "Access-Control-Allow-Origin" not in self._cors():
                return self._send(403, {"error": "origin not allowed (start the server with --allow-origin to allow it)"})
            self._send(204, None, {"Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS", "Access-Control-Max-Age": "600",
                                   "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, MCP-Protocol-Version, "
                                                                   "Mcp-Session-Id, Last-Event-ID"})

        def do_DELETE(self):                                # end of session: there is no session state to drop
            if not self._ours():
                return self._send(404, {"error": "not found"})
            if self._refuse():
                return
            self._send(405, {"error": "no sessions to end"}, {"Allow": "POST"})

        def do_POST(self):
            if not self._ours():
                return self._send(404, {"error": "not found"})
            if self._refuse():
                return
            pv = self.headers.get("MCP-Protocol-Version")
            if pv and pv not in M.SUPPORTED:
                return self._send(400, {"error": f"unsupported MCP-Protocol-Version {pv}"})
            te = (self.headers.get("Transfer-Encoding") or "").strip().lower()
            if te:                                          # no Content-Length: read as sent, else the body would
                if te != "chunked":                         # be taken for the next request on this connection
                    return self._send(501, {"error": f"Transfer-Encoding {te} is not supported"})
                raw = self._chunked()
                if raw is None:
                    return self._send(413, {"error": "body too large"})
                if raw is False:
                    return self._send(400, {"error": "bad chunked body"})
            else:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_BODY:
                    return self._send(413 if n > MAX_BODY else 400, {"error": "body too large" if n > MAX_BODY else "bad length"})
                raw = self.rfile.read(n)
            try:                                            # utf-8-sig: a byte-order mark, as the stdio server reads it
                msg = json.loads(raw.decode("utf-8-sig", errors="replace")) if raw.strip() else None
            except (ValueError, RecursionError):
                return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            try:
                resp = M.handle(ws, msg)
            except Exception as e:                          # a bug in one request: report it and keep serving
                import traceback
                traceback.print_exc(file=sys.stderr)
                mid = msg.get("id") if isinstance(msg, dict) and isinstance(msg.get("id"), (str, int)) else None
                resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
            if resp is None:                                # a notification or a response from the client
                return self._send(202)
            headers = {}
            if isinstance(msg, dict) and msg.get("method") == "initialize":
                headers["Mcp-Session-Id"] = secrets.token_hex(16)   # informational: every request stands alone
            self._send(200, resp, headers)

    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def run(ws, a):
    """ecadlib mcp --http: serve until Ctrl+C."""
    token = None if a.no_auth else (a.token or secrets.token_urlsafe(24))
    try:
        srv = make_server(ws, a.host, a.port, a.path, token, a.allow_origin)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:                                    # port taken, or an address this computer does not have
        print(f"ecadlib mcp: can't listen on {a.host}:{a.port} ({e.strerror or e})\n  hint: is another ecadlib mcp "
              f"--http already running there? Stop it or try --port {a.port + 1}", file=sys.stderr)
        return 2
    host = f"[{a.host}]" if ":" in a.host else a.host
    url = f"http://{host}:{srv.server_address[1]}{a.path}"
    print(f"ecadlib MCP server (Streamable HTTP) on {url}", file=sys.stderr)
    print(f"workspace: {ws.root}", file=sys.stderr)
    if token and not a.token:
        print(f"bearer token (new each start; set ECADLIB_MCP_TOKEN to keep one): {token}", file=sys.stderr)
    elif token:
        print("bearer token: from --token / ECADLIB_MCP_TOKEN", file=sys.stderr)
    else:
        print("no token: only clients on this computer can connect", file=sys.stderr)
    if not _loopback(a.host):
        print("listening beyond this computer: anyone who can reach it and has the token can use these tools",
              file=sys.stderr)
    print("Ctrl+C stops it.", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def serve_in_thread(ws, **kw):
    """Start a server on a free port in a daemon thread (tests) -> (server, url)."""
    srv = make_server(ws, port=0, quiet=True, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}{kw.get('path', '/mcp')}"
