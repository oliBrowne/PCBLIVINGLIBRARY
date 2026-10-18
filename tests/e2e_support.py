"""Helpers for tests/test_e2e.py: run the real `ecadlib` CLI in a subprocess, from a fresh folder, fully offline.

* recorded KiCad library: tests/fixtures/e2e/kicad/<repo>/<path> are real files from gitlab.com/kicad/libraries
  (master, fetched 2026-10-16 with `python3 -m tests.e2e_support record`). seed_cache() writes them into an ecadlib
  HTTP cache (the same JSON entries CachedHTTP stores) plus a name index, so `librarian --offline --cache DIR` uses
  them exactly as it would after an online run. Nothing else is in the cache: every other request is an offline miss.
* EasyEDA: tests/fixtures/easyeda/C2002.json (SYNTHETIC; easyeda.com is blocked here), recorded the same way.
* Claude: FakeAnthropic is a local HTTP server that answers POST /v1/messages with the recorded (SYNTHETIC) answers in
  tests/fixtures/e2e/anthropic/; the CLI reaches it through ANTHROPIC_BASE_URL, as the Anthropic SDKs do.
* Nexar / Altium 365: FakeNexar answers the GraphQL and file-upload calls `altium365 push` makes (NEXAR_API_URL /
  NEXAR_FILES_URL), recording every upload and release, so a push is checked without Altium 365. FakeGitHub answers the
  agent's pull-request calls ([agent] github_api); both run over HTTPS with a throwaway certificate (REQUESTS_CA_BUNDLE)."""
import json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
E2E = FIX / "e2e"
KICAD = E2E / "kicad"
GITLAB = "https://gitlab.com/kicad/libraries/{repo}/-/raw/master/{path}"
EASYEDA = {"C2002": FIX / "easyeda" / "C2002.json"}
# what `record` fetches (lib:name); a derived symbol's parent (`extends`) is fetched with it
RECORD = {"kicad-symbols": ["Device:R", "Device:C", "Amplifier_Operational:LM358", "Amplifier_Operational:LM2904",
                            "MCU_ST_STM32F1:STM32F103C8Tx", "MCU_ST_STM32F1:STM32F103C_8-B_Tx"],
          "kicad-footprints": ["Resistor_SMD:R_0603_1608Metric", "Resistor_SMD:R_0402_1005Metric",
                               "Capacitor_SMD:C_0402_1005Metric", "Capacitor_SMD:C_0603_1608Metric",
                               "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Package_QFP:LQFP-48_7x7mm_P0.5mm",
                               "Package_TO_SOT_SMD:SOT-23-5"]}
SUFFIX = {"kicad-symbols": (".kicad_symdir", ".kicad_sym"), "kicad-footprints": (".pretty", ".kicad_mod")}


def record():
    """Fetch RECORD from gitlab.com into tests/fixtures/e2e/kicad (run by hand; the files are untrusted data)."""
    import requests
    for repo, refs in RECORD.items():
        dsuf, fsuf = SUFFIX[repo]
        for ref in refs:
            lib, name = ref.split(":")
            path = f"{lib}{dsuf}/{name}{fsuf}"
            r = requests.get(GITLAB.format(repo=repo, path=path), timeout=60)
            r.raise_for_status()
            f = KICAD / repo / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(r.text, encoding="utf-8")
            print(f"{r.status_code} {repo}/{path} {len(r.text)}")


def seed_cache(cache):
    """Write the recorded KiCad files and EasyEDA payloads into an ecadlib HTTP cache folder + the KiCad name index."""
    from ecadlib.httpcache import CachedHTTP, redact
    from ecadlib.providers import EasyEDA
    cache = Path(cache)
    http = CachedHTTP(cache, offline=True)
    index = {"symbols": {}, "footprints": {}}

    def put(url, text, status=200):
        f = cache / f"{http._key('GET', redact(url), None)}.json"
        f.write_text(json.dumps({"url": redact(url), "status": status, "text": text}), encoding="utf-8")

    for repo, (dsuf, fsuf) in SUFFIX.items():
        for f in sorted((KICAD / repo).glob(f"*{dsuf}/*{fsuf}")):
            lib, name = f.parent.name[:-len(dsuf)], f.name[:-len(fsuf)]
            put(GITLAB.format(repo=repo, path=f"{f.parent.name}/{f.name}"), f.read_text(encoding="utf-8"))
            index["symbols" if repo == "kicad-symbols" else "footprints"].setdefault(name, []).append(lib)
    for lcsc, f in EASYEDA.items():
        put(EasyEDA.URL.format(lcsc), f.read_text(encoding="utf-8"))
    (cache / "kicad_library_index.json").write_text(json.dumps({"built": "2026-10-16", **index}), encoding="utf-8")
    return index


# ---------------------------------------------------------------- the user's inputs
DS = "https://datasheets.example.com/{}.pdf"
PARTS = [  # the .xlsx BOM: Designator, Qty, Value, Footprint, Manufacturer, Mfr Part #, LCSC, Description, Datasheet
    ("R1, R2, R3", 3, "10k", "R_0603_1608Metric", "Yageo", "RC0603FR-0710KL", "", "Resistor 10k 1% 0603", DS.format("yageo-rc")),
    ("C1, C2", 2, "100nF", "C_0603_1608Metric", "Murata", "GRM188R71C104KA01D", "", "Capacitor 100nF X7R 0603", DS.format("grm188")),
    ("U1", 1, "STM32F103C8T6", "", "STMicroelectronics", "STM32F103C8T6", "", "MCU ARM Cortex-M3 64k LQFP-48", DS.format("stm32f103")),
    ("U2", 1, "LM358DR", "SOIC-8_3.9x4.9mm_P1.27mm", "Texas Instruments", "LM358DR", "", "Dual op amp SOIC-8", DS.format("lm358")),
    ("Q1", 1, "EEPART1", "", "ACME", "EEPART1", "C2002", "EasyEDA-only part", DS.format("eepart1")),
    ("U3", 1, "DSPART16", "", "ACME", "DSPART16", "", "Buck converter VQFN-16", DS.format("dspart16")),
    ("U4", 1, "TPS7A0233PDBVR", "SOT-23-5", "Texas Instruments", "TPS7A0233PDBVR", "", "LDO 3.3 V 200 mA SOT-23-5", DS.format("tps7a02")),
    ("U9", 1, "NOWHERE1", "", "", "NOWHERE1", "", "", ""),
]
HEADER = ["Designator", "Qty", "Value", "Footprint", "Manufacturer", "Mfr Part #", "LCSC", "Description", "Datasheet"]


def write_parts_xlsx(path):
    """The .xlsx BOM a user would export from a spreadsheet: a title row, a blank row, then the table."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(["Board E2E rev A - bill of materials"])
    ws.append([])
    ws.append(HEADER)
    for row in PARTS:
        ws.append(list(row))
    wb.save(path)
    return Path(path)


def seed_datasheet(cache, mpn, pages=None):
    """A datasheet downloaded on an earlier run: <cache>/datasheets/<sha256(url)>.pdf + .json, as PdfStore keeps it."""
    import hashlib
    from tests.test_visual_check import make_pdf
    url = DS.format(mpn.lower())
    data = make_pdf(pages or [f"{mpn} datasheet\nPin functions", "Package outline\nVQFN-16 3x3 mm e 0.5 mm",
                              "Recommended land pattern"])
    stem = hashlib.sha256(url.encode()).hexdigest()[:24]
    d = Path(cache) / "datasheets"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.pdf").write_bytes(data)
    (d / f"{stem}.json").write_text(json.dumps({"url": url, "final_url": url, "where": "BOM", "status": 200,
                                                "content_type": "application/pdf", "sha256": hashlib.sha256(data).hexdigest(),
                                                "bytes": len(data), "pages": 3, "fetched": "2026-10-16T00:00:00"}))
    return url


# ---------------------------------------------------------------- local fake services
def self_signed(d):
    """A throwaway certificate for 127.0.0.1 (openssl) -> (cert, key); the cert is also the CA bundle to trust."""
    import shutil
    if not shutil.which("openssl"):
        return None
    cert, key = Path(d) / "cert.pem", Path(d) / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    return cert, key


class _Server:
    """A local HTTP(S) server on an ephemeral port. tls=(cert, key): HTTPS (for clients that only accept https URLs;
    the subprocess trusts cert through REQUESTS_CA_BUNDLE)."""
    def __init__(self, handler_cls, tls=None):
        self.calls = []
        outer = self

        class H(handler_cls):
            server_obj = outer

            def log_message(self, *a):
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        if tls:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*map(str, tls))
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.url = f"{'https' if tls else 'http'}://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _JSONHandler(BaseHTTPRequestHandler):
    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return raw.decode("utf-8", "replace")

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("request-id", "req_e2e")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):       # the client was killed while it waited
            pass


class FakeAnthropic(_Server):
    """POST /v1/messages. A request with our drawings as images is a visual proof (answer: the part's
    anthropic/proof_<PART>.json, else proof_default.json); any other is a datasheet read (answer:
    anthropic/datasheet_<PART>.json, else HTTP 400). The part is the one named in the request text."""
    def __init__(self, key="sk-e2e-test", tls=None, hold=()):
        """hold: parts whose datasheet request is never answered (the client is killed while it waits)."""
        class H(_JSONHandler):
            def do_POST(self):
                body, srv = self._body(), self.server_obj
                srv.calls.append({"path": self.path, "key": self.headers.get("x-api-key"), "body": body})
                if self.headers.get("x-api-key") != key:
                    return self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})
                if not self.path.endswith("/v1/messages"):
                    return self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}})
                content = body["messages"][0]["content"]
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                part = srv.part_of(text)
                kind = "proof" if sum(c.get("type") == "image" for c in content) >= 2 else "datasheet"
                srv.kinds.append((kind, part))
                if kind == "datasheet" and part in srv.hold:
                    srv.held.set()
                    time.sleep(60)                     # the caller kills the client meanwhile
                    return self._send(503, {"type": "error", "error": {"type": "overloaded_error", "message": "held"}})
                f = E2E / "anthropic" / f"{kind}_{part}.json"
                if not f.exists() and kind == "proof":
                    f = E2E / "anthropic" / "proof_default.json"
                if not f.exists():
                    return self._send(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                                       "message": f"no recorded answer for {kind} {part}"}})
                return self._send(200, json.loads(f.read_text()))
        self.kinds, self.hold, self.held = [], set(hold), threading.Event()
        super().__init__(H, tls)

    @staticmethod
    def part_of(text):
        import re
        m = re.search(r"Part(?: number)?:\s*(\S+)", text)
        return m.group(1).strip("\"'.,") if m else "?"


class FakeGitHub(_Server):
    """GitHub REST for the agent's batch pull requests: no open PR at first, then PR #7 (opened / updated)."""
    def __init__(self, tls=None):
        class H(_JSONHandler):
            def _any(self):
                srv = self.server_obj
                body = self._body() if self.command != "GET" else None
                srv.calls.append((self.command, self.path.split("/repos/", 1)[-1], body))
                if self.command == "GET" and "/pulls?" in self.path:
                    return self._send(200, [pr for pr in srv.prs.values() if pr["state"] == "open"])
                if self.command == "POST" and self.path.endswith("/pulls"):
                    srv.prs[7] = {"number": 7, "html_url": "https://github.com/acme/lib/pull/7", "state": "open", **body}
                    return self._send(201, srv.prs[7])
                if "/pulls/" in self.path:
                    pr = srv.prs.setdefault(7, {"number": 7, "state": "open"})
                    pr.update(body or {})
                    return self._send(200, pr)
                return self._send(201 if self.command == "POST" else 200, {})
            do_GET = do_POST = do_PATCH = _any
        self.prs = {}
        super().__init__(H, tls)


class FakeNexar(_Server):
    """Nexar GraphQL + file upload as `altium365 push` uses them (NEXAR_API_URL / NEXAR_FILES_URL): no component exists
    yet, every release succeeds. Records the uploads (file name -> bytes) and the release inputs."""
    def __init__(self, tls=None):
        class H(_JSONHandler):
            def do_POST(self):
                srv = self.server_obj
                if self.path.rstrip("/").endswith("/File/Upload"):
                    import email.parser, email.policy
                    n = int(self.headers.get("content-length") or 0)
                    raw = self.rfile.read(n)
                    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                        b"content-type: " + self.headers["content-type"].encode() + b"\r\n\r\n" + raw)
                    for part in msg.iter_parts():
                        srv.uploads[part.get_filename()] = part.get_payload(decode=True)
                    fid = f"file-{len(srv.uploads)}"
                    data = fid.encode()
                    self.send_response(200)
                    self.send_header("content-type", "text/plain")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
                body = self._body()
                q = body.get("query", "")
                srv.calls.append(body)
                if "desWorkspaceInfos" in q:
                    return self._send(200, {"data": {"desWorkspaceInfos": [{"name": "Acme", "url": srv.workspace}]}})
                if "componentScheme" in q:
                    ids = {k: {"id": f"{k}-id", "name": k} for k in ("componentScheme", "symbolScheme", "footprintScheme",
                                                                     "componentLifeCycle", "symbolLifeCycle", "footprintLifeCycle")}
                    return self._send(200, {"data": ids})
                if "folders" in q:                 # an empty library: every folder is created on first use
                    return self._send(200, {"data": {"desLibrary": {"folders": srv.folders}}})
                if "desCreateFolder" in q:
                    inp = body["variables"]["input"]
                    fid = f"fld-{len(srv.folders) + 1}"
                    srv.folders.append({"id": fid, "name": inp["name"], "parent": {"id": inp["parentId"]} if inp.get("parentId") else None})
                    return self._send(200, {"data": {"desCreateFolder": {"id": fid, "errors": []}}})
                if "desLibrary" in q:
                    return self._send(200, {"data": {"desLibrary": {"components": {"nodes": []}}}})
                if "desReleaseComponent" in q:
                    srv.releases.append(body["variables"]["input"])
                    return self._send(200, {"data": {"desReleaseComponent": {"errors": []}}})
                return self._send(200, {"errors": [{"message": f"FakeNexar: query not known: {q[:80]}"}]})
        self.uploads, self.releases, self.folders, self.workspace = {}, [], [], "https://acme.365.altium.com"
        super().__init__(H, tls)


# ---------------------------------------------------------------- the CLI as a user runs it
def clean_env(**extra):
    """The environment of a user with no keys set: no API keys, no ECADLIB_* defaults; the package importable."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("ECADLIB_") or k.endswith("_API_KEY") or k in ("NEXAR_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN"))}
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # no real network: every request that is not to the local fake servers fails at once at a dead proxy, so a run
    # can only use the recorded cache (as on a machine with the vendor hosts blocked)
    env.update({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    env.update({"GIT_AUTHOR_NAME": "E2E Tester", "GIT_AUTHOR_EMAIL": "e2e@example.com", "GIT_COMMITTER_NAME": "E2E Tester",
                "GIT_COMMITTER_EMAIL": "e2e@example.com", "ECADLIB_USER": "E2E Tester", "PYTHONUNBUFFERED": "1"})
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def ecadlib(cwd, *args, env=None, timeout=600, check=True, stdin=None):
    """`ecadlib ARGS` in cwd -> CompletedProcess (text). check: a non-zero exit fails with the output in the message."""
    p = subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env or clean_env(), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise AssertionError(f"ecadlib {' '.join(map(str, args))} exited {p.returncode}\n--- stdout\n{p.stdout[-4000:]}"
                             f"\n--- stderr\n{p.stderr[-4000:]}")
    return p


def ecadlib_killed(cwd, *args, env=None, when=None, timeout=300):
    """Start `ecadlib ARGS` and SIGKILL it as soon as when() is true (polled; e.g. a fake server got a request).
    -> (output so far, killed)."""
    import tempfile
    with tempfile.TemporaryFile("w+") as log:
        p = subprocess.Popen([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env or clean_env(),
                             stdout=log, stderr=subprocess.STDOUT, text=True)
        killed, t0 = False, time.time()
        while p.poll() is None and time.time() - t0 < timeout:
            if when():
                p.kill()                                    # SIGKILL; TerminateProcess on Windows
                killed = True
                break
            time.sleep(0.02)
        if p.poll() is None:
            p.kill()
        p.wait()
        log.seek(0)
        return log.read(), killed


def snapshot(root, skip=("http_cache", "cache", "__pycache__", ".git")):
    """{relative path: sha256} of every file under root (for 'nothing changed' checks)."""
    import hashlib
    root = Path(root)
    return {f.relative_to(root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(root.rglob("*")) if f.is_file() and not any(s in f.relative_to(root).parts for s in skip)}


class MCPClient:
    """`ecadlib mcp` over stdio as an MCP client (Claude Code, Claude Desktop) drives it: JSON-RPC, one message a line."""
    def __init__(self, cwd, *args, env=None):
        self.p = subprocess.Popen([sys.executable, "-m", "ecadlib", "mcp", *map(str, args)], cwd=cwd, env=env or clean_env(),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        line = self.p.stdout.readline()
        if not line:
            raise AssertionError(f"ecadlib mcp closed stdout; stderr:\n{self.p.stderr.read()[-3000:]}")
        r = json.loads(line)
        assert r.get("id") == msg["id"], r
        return r

    def call(self, tool, **arguments):
        r = self.send("tools/call", {"name": tool, "arguments": arguments})
        assert "result" in r, r
        return r["result"]

    def close(self):
        try:
            self.p.stdin.close()
            return self.p.wait(timeout=30)
        finally:
            if self.p.poll() is None:
                self.p.kill()


# ---------------------------------------------------------------- KiCad 7 copies of the KiCad 8 export
def kicad7_footprint(text):
    """kicad_export writes KiCad 8 footprints (version 20240108). KiCad 7 (installed here) refuses that format, so the
    KiCad checks load a KiCad 7 copy: Reference/Value properties -> fp_text, other properties dropped, (hide yes) ->
    hide, (fill no|yes) -> (fill none|solid), uuid/embedded_fonts dropped. Pads, drills, layers and outlines are untouched."""
    from ecadlib import sexpr
    Q = sexpr.Q
    t = sexpr.parse(text, keep_quotes=True)

    def walk(n):
        out = [n[0]]
        for c in n[1:]:
            if isinstance(c, list) and c:
                if c[0] in ("uuid", "embedded_fonts", "generator_version", "unlocked"):
                    continue
                if c[0] == "hide" and n[0] in ("fp_text", "property"):
                    if len(c) < 2 or c[1] != "no":
                        out.append("hide")
                    continue
                if c[0] == "fill" and len(c) == 2 and c[1] in ("no", "yes"):
                    out.append(["fill", "none" if c[1] == "no" else "solid"])
                    continue
                if c[0] == "property" and n[0] == "footprint":
                    if c[1] not in ("Reference", "Value"):
                        continue
                    c = ["fp_text", c[1].lower(), Q(c[2])] + c[3:]
                out.append(walk(c))
            else:
                out.append(c)
        return out
    t = walk(t)
    v = sexpr.first(t, "version")
    if v:
        v[1] = "20221018"
    return sexpr.dumps(t) + "\n"


def kicad7_copy(src, dst):
    """A KiCad 7 copy of an exported KiCad folder (symbols, footprints, tables) when the installed KiCad is 7."""
    import shutil
    from tests.test_kicad_real import kicad7_symbols
    shutil.copytree(src, dst)
    for f in Path(dst).glob("*.kicad_sym"):
        f.write_text(kicad7_symbols(f.read_text(encoding="utf-8")), encoding="utf-8")
    for f in Path(dst).glob("*.pretty/*.kicad_mod"):
        f.write_text(kicad7_footprint(f.read_text(encoding="utf-8")), encoding="utf-8")
    return Path(dst)


if __name__ == "__main__" and sys.argv[1:] == ["record"]:
    record()
