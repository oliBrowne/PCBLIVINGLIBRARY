"""Ways to download a datasheet PDF that a maker's site refuses to plain scripts ([datasheet] download).

Some makers' sites (analog.com, Nexperia in the benchmark of 2026-10-18) answer a script with 403, a bot-check page
or a dropped connection, and give the same link to a browser. PdfStore tries ecadlib's own request first; only a
refusal (refused()) moves on to the next way, in this order:

- headers: plain requests, with a desktop browser's request headers (User-Agent, Accept, Accept-Language, Referer).
- impersonate: curl_cffi (optional: pip install "ecadlib[browser]"), a real Chrome TLS and HTTP/2 fingerprint and
  headers, no browser. Bot managers that check the TLS fingerprint refuse the two above and pass this one.
- browser: Playwright (optional, same extra) driving the Edge or Chrome installed on this machine, headless, in a fresh
  profile (none of your cookies or sign-ins). It opens the site's home page first so the site's own scripts run, then
  the link, and keeps the PDF the browser receives.
- handoff: the link opened in your own browser; the PDF is taken from your Downloads folder when it appears there.
  Only in a run someone is watching (a terminal), never in a benchmark.

None of them solves a CAPTCHA, signs in, reads your browser's cookies or goes through a third-party proxy. Each way
returns what PdfStore._get does: (status, bytes, content-type, final url, error[, response headers])."""
import atexit, concurrent.futures, os, re, shutil, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse

import requests

TIERS = ("headers", "impersonate", "browser")          # [datasheet] download = "auto": these, in this order
WAYS = {"plain": "ecadlib's own request", "headers": "a browser's request headers",
        "impersonate": "a Chrome TLS fingerprint (curl_cffi)", "browser": "a headless browser",
        "handoff": "your own browser"}
INSTALL = 'pip install "ecadlib[browser]"'
# What a desktop Chrome sends for a link it opens. Only datasheet PDF downloads that ecadlib's own request was refused
# for ever send it; API calls never do.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
BROWSER_HEADERS = {"User-Agent": BROWSER_UA,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
                   "Accept-Language": "en-US,en;q=0.9", "Upgrade-Insecure-Requests": "1"}
REFUSED_STATUS = {401, 403, 405, 406, 418, 429, 451, 503}
# A dropped or reset connection or a timeout: bot managers (Akamai) often answer a script by never answering, or by
# cutting its TLS handshake (Python's TLS looks like no browser's). A TLS or certificate error moves on too: Edge and
# Chrome check a certificate against the system's own store (a company's inspecting proxy) and fetch a missing
# intermediate, and talk to older servers Python's TLS refuses; every way still checks the certificate. A proxy
# refusal (this machine's network), a name that does not resolve, a closed port and a host that never answers the
# connection are the same for every way, so they never move on.
_DROPPED = re.compile(r"ReadTimeout|Timeout|ConnectionError|ChunkedEncodingError|ProtocolError|IncompleteRead|"
                      r"Connection reset|RemoteDisconnected|Connection aborted|too slow|SSLError|SSL:|CERTIFICATE_VERIFY", re.I)
_NOT_THE_SITE = re.compile(r"proxy CONNECT refused|ProxyError|NameResolution|getaddrinfo|Name or service not known|"
                           r"nodename nor servname|No address associated|ConnectTimeout|connect timeout|NewConnectionError|"
                           r"Connection refused|ConnectionRefused|ERR_NAME_NOT_RESOLVED|ERR_PROXY|ERR_TUNNEL_CONNECTION|"
                           r"ERR_CERT|ERR_SSL|ERR_CONNECTION_REFUSED|ERR_ADDRESS_UNREACHABLE|ERR_INTERNET_DISCONNECTED", re.I)
_CHALLENGE = re.compile(rb"Just a moment|cf-chl|challenge-platform|_Incapsula_Resource|Access Denied|"
                        rb"Request unsuccessful|captcha|bot.?(detection|protection)|enable JavaScript|"
                        rb"Reference #\d|akamai|perimeterx|px-captcha|distil", re.I)


def refused(status, data, err, pdf_link=None):
    """True when a download looks refused by the site rather than broken: worth another way. 404 / 410 (no such file)
    and anything this machine's network did (a proxy refusal, a name that does not resolve) never are.
    pdf_link: a function (html bytes) -> a PDF link or None; an HTML answer with no PDF link in it counts as refused
    (a bot-check page, or a product page whose link is written by its scripts)."""
    if err:
        return bool(_DROPPED.search(err)) and not _NOT_THE_SITE.search(err)
    if status in REFUSED_STATUS or 520 <= (status or 0) <= 530:
        return True
    if 200 <= (status or 0) < 300 and data and b"%PDF-" not in data[:1024]:
        head = data[:4096]
        if re.search(rb"<(!doctype|html)", head, re.I):
            return not (pdf_link and pdf_link(data))
        return bool(_CHALLENGE.search(head))
    return False


def origin(url):
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}/"


def _err(e):
    """A requests error, short. The cause urllib3 names at the end ("Caused by NameResolutionError") goes first: a
    long link pushes it past the cut, and refused() needs it to tell a dead name from a site that drops scripts."""
    s = str(e)
    m = re.search(r"Tunnel connection failed: ([^'\")]+)", s)
    if m:
        return f"{type(e).__name__}: proxy CONNECT refused ({m.group(1).strip()})"
    c = re.search(r"Caused by (\w+)", s)
    return f"{type(e).__name__}: " + (f"({c.group(1)}) " if c else "") + s[:160]


# ---------------------------------------------------------------- headers: plain requests, a browser's headers
def headers_get(url, session=None, timeout=60):
    from .datasheet import read_capped, DatasheetError
    s = session or requests.Session()
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True, stream=True, headers=dict(BROWSER_HEADERS, Referer=origin(url)))
        try:
            data = read_capped(r)
        finally:
            r.close()
    except DatasheetError as e:
        return 0, None, "", url, str(e)
    except (requests.RequestException, requests.packages.urllib3.exceptions.HTTPError) as e:
        return 0, None, "", url, _err(e)
    return r.status_code, data, r.headers.get("content-type", ""), getattr(r, "url", url), None, r.headers


# ---------------------------------------------------------------- impersonate: curl_cffi, Chrome's TLS fingerprint
def impersonate_available():
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:
        return False


class Impersonate:
    """One curl_cffi session (cookies kept per site for the run), impersonating the newest Chrome curl_cffi knows."""

    def __init__(self, timeout=60):
        self.timeout, self._s, self._lock = timeout, None, threading.Lock()

    def get(self, url):
        with self._lock:                              # one session for the process: one request at a time
            return self._get(url)

    def _get(self, url):
        from .datasheet import MAX_DOWNLOAD, DOWNLOAD_S
        try:
            from curl_cffi import requests as cr
        except Exception as e:
            return 0, None, "", url, f"not installed ({type(e).__name__}): {INSTALL}"
        if self._s is None:
            self._s = cr.Session(impersonate="chrome")
        try:
            r = self._s.get(url, timeout=self.timeout, allow_redirects=True, stream=True, headers={"Referer": origin(url)})
            buf, t0 = bytearray(), time.monotonic()
            try:
                for chunk in r.iter_content():
                    buf += chunk
                    if len(buf) > MAX_DOWNLOAD:
                        break
                    if time.monotonic() - t0 > DOWNLOAD_S:
                        return 0, None, "", url, f"download too slow ({len(buf) >> 10} KB in {DOWNLOAD_S} s); stopped"
            finally:
                r.close()
        except Exception as e:                        # curl_cffi raises its own RequestException / CurlError
            return 0, None, "", url, f"{type(e).__name__}: {str(e)[:160]}"
        return r.status_code, bytes(buf), r.headers.get("content-type", ""), str(getattr(r, "url", url)), None, r.headers

    def close(self):
        if self._s is not None:
            try:
                self._s.close()
            except Exception:
                pass
            self._s = None


# ---------------------------------------------------------------- browser: Playwright, the installed Edge / Chrome
def playwright_available():
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


CHANNELS = ("msedge", "chrome", None)                  # None: the Chromium Playwright downloaded (playwright install)


def _runs(cmd):
    """A program that can be started: a path, or a bare name found on PATH (PLAYWRIGHT_NODEJS_PATH=node)."""
    cmd = str(cmd)
    path = cmd if os.path.dirname(cmd) else shutil.which(cmd)
    return bool(path) and os.access(path, os.X_OK)


def pw_proxy(proxy, bypass=None):
    """HTTPS_PROXY as Playwright takes it: Chromium ignores user:password in the server URL, so they go apart."""
    from urllib.parse import urlsplit, unquote
    u = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    p = {"server": f"{u.scheme}://{u.netloc.rpartition('@')[2]}"}
    if u.username:
        p.update(username=unquote(u.username), password=unquote(u.password or ""))
    if bypass:
        p["bypass"] = bypass
    return p


# fetch() in the page, given up after ms and read up to a cap, kept in the page; _CHUNK_JS reads it out a piece at a
# time as base64 (page.evaluate only carries text, and one 80 MB string through the driver takes minutes).
_FETCH_JS = """async ([u, cap, ms]) => {
  try {
    const r = await fetch(u, {credentials: "include", referrerPolicy: "origin", signal: AbortSignal.timeout(ms)});
    if (+(r.headers.get("content-length") || 0) > cap) return [r.status, "", 0, "larger than " + (cap >> 20) + " MB"];
    const reader = r.body.getReader(), parts = []; let n = 0;
    for (;;) { const {done, value} = await reader.read(); if (done) break;
               n += value.length; if (n > cap) { reader.cancel(); return [r.status, "", 0, "larger than " + (cap >> 20) + " MB"]; }
               parts.push(value); }
    const b = new Uint8Array(n); let o = 0; for (const p of parts) { b.set(p, o); o += p.length; }
    window.__ecadlibPdf = b;
    return [r.status, r.headers.get("content-type") || "", n, null];
  } catch (e) { return [0, "", 0, "the page's fetch failed: " + String(e).slice(0, 160)]; }
}"""
_CHUNK_JS = """([a, z]) => { const b = window.__ecadlibPdf.subarray(a, z); let s = "";
  for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768));
  return btoa(s); }"""
CHUNK = 2 * 2 ** 20


class _Worker:
    """One daemon thread that runs calls in order: Playwright's sync API must stay on the thread that started it (and
    off an asyncio loop: the web UI). A call that overruns is abandoned with its thread, which never holds up exit."""

    def __init__(self):
        self.q = __import__("queue").Queue()
        threading.Thread(target=self._run, name="ecadlib-browser", daemon=True).start()

    def _run(self):
        while True:
            fn, fut = self.q.get()
            if fn is None:
                return
            try:
                fut.set_result(fn())
            except BaseException as e:                # handed to the caller, which turns it into a note
                fut.set_exception(e)

    def call(self, fn, timeout):
        fut = concurrent.futures.Future()
        self.q.put((fn, fut))
        return fut.result(timeout=timeout)

    def stop(self):
        self.q.put((None, None))


class _State:
    """One launched browser: its own, so a stuck call on an abandoned worker never touches the next one."""

    def __init__(self):
        self.pw = self.browser = self.ctx = self.why = self.engine = self.pid = None
        self.warm = set()


class Browser:
    """A headless browser for the whole run (one per process: shared()), started on first use and closed at exit.
    Every call runs on one worker thread of its own; a call that overruns (a site that stops sending) is given up,
    and the next call starts a new browser. Requests to this machine, link-local or other non-routable addresses
    (the datasheet.unsafe_download rule) are blocked inside the browser too; allow_local is for tests."""

    def __init__(self, timeout=45, settle=2.5, challenge_s=12, executable=None, allow_local=False):
        self.timeout, self.settle, self.challenge_s, self.allow_local = timeout, settle, challenge_s, allow_local
        self.executable = executable or os.environ.get("ECADLIB_BROWSER") or None
        self.limit = timeout * 3 + 30                # the most one call may take, warm-up and challenge included
        self._lock, self._worker, self._s = threading.Lock(), _Worker(), _State()
        atexit.register(self.close)

    @property
    def engine(self):
        return self._s.engine

    # -- on the worker thread
    def _start(self, s):
        if s.browser is not None and not s.browser.is_connected():
            self._close(s)                            # it died (a crash, an update): a new one
            s.__init__()
        if s.ctx is not None or s.why:
            return
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            s.why = f"not installed ({type(e).__name__}): {INSTALL}"
            return
        try:                                          # a driver that is not there: say so, without starting it
            from playwright._impl._driver import compute_driver_executable
            exe = compute_driver_executable()
            if not _runs(exe[0] if isinstance(exe, (tuple, list)) else exe):
                s.why = f"could not start a browser: Playwright's driver is missing ({INSTALL})"
                return
        except Exception:                             # (a private helper: when it moves, just start)
            pass
        try:
            s.pw = sync_playwright().start()
            try:                                      # the driver's process, to stop it if a call never returns
                s.pid = s.pw._impl_obj._connection._transport._proc.pid
            except Exception:
                s.pid = None
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
            tried = []
            for ch in ((None,) if self.executable else CHANNELS):
                kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}   # no navigator.webdriver
                if self.executable:
                    kw["executable_path"] = self.executable
                elif ch:
                    kw["channel"] = ch
                if proxy:                             # as requests does: HTTPS_PROXY, minus NO_PROXY
                    kw["proxy"] = pw_proxy(proxy, bypass)
                try:
                    s.browser = s.pw.chromium.launch(**kw)
                    s.engine = self.executable or ch or "chromium"
                    break
                except Exception as e:
                    tried.append(f"{ch or 'chromium'}: {str(e).splitlines()[0][:80]}")
            if s.browser is None:
                s.why = "no browser to drive (" + "; ".join(tried) + "): install Edge or Chrome, or run `playwright install chromium`"
                self._close(s)
                return
            try:                                      # a headless browser says "HeadlessChrome": say Chrome, as it is
                probe = s.browser.new_page()
                ua = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
                probe.close()
            except Exception:
                ua = BROWSER_UA
            s.ctx = s.browser.new_context(accept_downloads=True, user_agent=ua, locale="en-US",
                                          viewport={"width": 1366, "height": 768})
            if not self.allow_local:
                from . import datasheet as D
                s.ctx.route("**/*", lambda route: route.abort() if D.unsafe_download(route.request.url) else route.continue_())
        except Exception as e:                        # the driver cannot run (a broken install, a frozen build)
            self._close(s)
            s.why = f"could not start a browser ({type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''})"

    def _get(self, url, s):
        from playwright.sync_api import Error as PWError
        self._start(s)
        if s.why:
            return 0, None, "", url, s.why
        from .datasheet import is_pdf
        try:
            page = s.ctx.new_page()
        except Exception:                             # the driver or the browser died between calls: start again
            self._close(s)
            s.__init__()
            self._start(s)
            if s.why:
                return 0, None, "", url, s.why
            page = s.ctx.new_page()
        pdfs, downloads, nav = [], [], {}

        def on_response(r):
            try:
                if "pdf" in (r.headers or {}).get("content-type", "").lower():
                    pdfs.append(r)
                if r.request.is_navigation_request() and r.frame == page.main_frame:
                    nav["last"] = r                   # the page's own document, after any redirect or reload
            except Exception:
                pass

        def on_download(d):
            downloads.append(d)
            try:                                      # the page fetches it again: two copies would halve the speed
                d.cancel()
            except Exception:
                pass
        page.on("response", on_response)
        page.on("download", on_download)
        try:
            host = urlparse(url).netloc
            if host not in s.warm:                    # the site's own scripts (bot checks) run on its home page first
                s.warm.add(host)
                try:
                    page.goto(origin(url), wait_until="domcontentloaded", timeout=self.timeout * 1000)
                    page.wait_for_timeout(self.settle * 1000)
                except PWError:
                    pass
                pdfs.clear()
                nav.clear()
            nav_err = None
            try:
                page.goto(url, wait_until="load", timeout=self.timeout * 1000)
            except PWError as e:                      # "Download is starting": the PDF went to a download
                nav_err = str(e).splitlines()[0][:160]
            t0 = time.monotonic()
            while True:
                waited = time.monotonic() - t0
                target = downloads[-1].url if downloads else None
                for r in reversed(pdfs if target is None else []):
                    if nav_err:                       # it never finished loading: its body may never come
                        target = r.url
                        break
                    try:
                        body = r.body()
                    except PWError:                   # its body went to a download
                        target = r.url
                        break
                    if is_pdf(body):
                        return r.status, body, r.headers.get("content-type", ""), r.url, None
                    target = r.url                    # Chrome's PDF viewer hands back its own page instead
                    break
                if target is None and nav_err and "Download is starting" in nav_err:
                    if waited < self.timeout:         # the download event comes a little after the response
                        page.wait_for_timeout(250)
                        continue
                    return 0, None, "", url, f"the browser's download did not start in {self.timeout} s"
                if target is not None:                # fetched again by the page: a time limit and a size cap, no file
                    return self._fetch_in_page(page, target)
                resp = nav.get("last")
                if resp is None:
                    if waited < self.timeout and nav_err is None:
                        page.wait_for_timeout(250)
                        continue
                    return 0, None, "", url, f"the browser could not open it ({nav_err or 'no response'})"
                try:
                    html = page.content().encode("utf-8")
                except PWError:
                    html = b""
                if waited < self.challenge_s and (resp.status in REFUSED_STATUS or _CHALLENGE.search(html[:4096])):
                    page.wait_for_timeout(500)        # a bot-check page runs its own script, then reloads itself
                    continue
                return resp.status, html, (resp.headers or {}).get("content-type", "text/html"), page.url, None
        except PWError as e:
            return 0, None, "", url, f"browser: {str(e).splitlines()[0][:160]}"
        finally:
            for d in downloads:                       # the bytes came from the page's fetch: no file left behind
                try:
                    d.cancel()
                    d.delete()
                except Exception:
                    pass
            try:                                      # Edge 151+ never closes a page left on its error page
                page.goto("about:blank", timeout=1500)
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass

    def _fetch_in_page(self, page, url):
        """The PDF fetched again by the page itself: the same browser, cookies and TLS, with a time limit and
        MAX_DOWNLOAD as ecadlib's own request has. A fetch must come from the PDF's own site, so the page first moves
        to a blank page of that site, made here (the site's home page may redirect elsewhere, or to another site);
        leaving the page also stops its own copy of the PDF (a PDF viewer), which would halve the fetch's speed."""
        import base64
        from playwright.sync_api import Error as PWError
        from .datasheet import MAX_DOWNLOAD, DOWNLOAD_S
        limit = min(self.timeout * 2, DOWNLOAD_S)
        deadline = time.monotonic() + limit
        blank = origin(url) + "__ecadlib_blank__"
        cdp = None
        try:                                          # a site's service worker would answer the blank page itself
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.setBypassServiceWorker", {"bypass": True})
        except Exception:                             # (then the check below says so)
            pass
        page.route(blank, lambda r: r.fulfill(status=200, content_type="text/html", body="<!doctype html><title></title>"))
        try:
            page.goto(blank, wait_until="load", timeout=self.timeout * 1000)
        except PWError as e:
            return 0, None, "", url, f"browser: {str(e).splitlines()[0][:160]}"
        finally:
            try:
                page.unroute(blank)
            except PWError:
                pass
            try:                                      # (the page stays outside the worker; the PDF is not kept twice)
                cdp and cdp.detach()
            except Exception:
                pass
        if not urlparse(page.url).path.endswith("__ecadlib_blank__"):
            return 0, None, "", url, f"browser: the page went to {page.url[:120]} instead of a blank one"
        try:
            status, ctype, n, err = page.evaluate(_FETCH_JS, [url, MAX_DOWNLOAD, int(limit * 1000)])
            if err:
                return 0, None, "", url, err
            buf = bytearray()
            for i in range(0, n, CHUNK):
                if time.monotonic() > deadline:
                    return 0, None, "", url, f"download too slow ({len(buf) >> 10} KB in {limit:g} s); stopped"
                buf += base64.b64decode(page.evaluate(_CHUNK_JS, [i, min(i + CHUNK, n)]))
        except PWError as e:
            return 0, None, "", url, f"browser: {str(e).splitlines()[0][:160]}"
        finally:
            try:
                page.evaluate("() => { delete window.__ecadlibPdf; }")
            except PWError:
                pass
        return status, bytes(buf), ctype, url, None

    def _close(self, s):
        for x in (s.ctx, s.browser):
            try:
                x and x.close()
            except Exception:
                pass
        try:
            s.pw and s.pw.stop()
        except Exception:
            pass
        s.pw = s.browser = s.ctx = None

    # -- from any thread
    def get(self, url):
        limit = self.limit
        with self._lock:                              # one page at a time
            if self._worker is None:                  # closed: open again
                self._worker, self._s = _Worker(), _State()
            s = self._s
            try:
                return self._worker.call(lambda: self._get(url, s), limit)
            except concurrent.futures.TimeoutError:   # stuck (a site that stopped sending): a new worker and browser
                self._abandon()
                return 0, None, "", url, f"the browser did not finish in {limit} s"
            except Exception as e:                    # a browser that died under the call: started again next time
                self._abandon()
                return 0, None, "", url, f"browser: {type(e).__name__}: {(str(e).splitlines() or [''])[0][:160]}"

    def _abandon(self):
        """Leave the current worker and browser: closed once its call returns; its driver (and with it the
        browser) stopped now, which also ends a call that would never return. The next call starts afresh."""
        w, s = self._worker, self._s
        self._worker, self._s = _Worker(), _State()
        w.q.put((lambda: self._close(s), concurrent.futures.Future()))
        w.stop()
        if s.pid:
            try:
                import signal
                os.kill(s.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except Exception:
                pass

    def close(self):
        w, s, self._worker = self._worker, self._s, None
        if w is None:                                 # closed already (atexit after a close)
            return
        try:
            w.call(lambda: self._close(s), 15)
        except Exception:
            pass
        w.stop()


_SHARED, _SHARED_LOCK = {}, threading.Lock()


def shared(name):
    """The run's one Impersonate or Browser (a headless browser is heavy: every reader in the process uses this one)."""
    with _SHARED_LOCK:
        if name not in _SHARED:
            _SHARED[name] = Impersonate() if name == "impersonate" else Browser()
        return _SHARED[name]


# ---------------------------------------------------------------- handoff: your own browser, your Downloads folder
PARTIAL = (".crdownload", ".part", ".partial", ".download", ".tmp")


def downloads_dir(configured=""):
    """[datasheet] downloads_folder, else the system Downloads folder (on Windows the one Explorer uses, which may
    be moved, e.g. into OneDrive), else ~/Downloads."""
    if str(configured or "").strip():
        return Path(os.path.expandvars(str(configured))).expanduser()
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
                return Path(os.path.expandvars(v))
        except OSError:
            pass
    elif sys.platform != "darwin":                    # Linux: the XDG folder the browsers save to (~/Téléchargements)
        xdg = os.environ.get("XDG_DOWNLOAD_DIR")
        if not xdg:
            try:
                dirs = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
                m = re.search(r'^XDG_DOWNLOAD_DIR="?([^"\n]+)"?', dirs.read_text(encoding="utf-8", errors="replace"), re.M)
                xdg = m.group(1).replace("$HOME", str(Path.home())) if m else None
            except OSError:
                xdg = None
        if xdg and Path(xdg).is_dir():
            return Path(xdg)
    return Path.home() / "Downloads"


def gui(environ=None, platform=None):
    """A desktop to open a browser on: Windows or macOS (not over SSH: it would open on a screen nobody here sees),
    or an X11 / Wayland display (ssh -X included)."""
    env = os.environ if environ is None else environ
    if (platform or sys.platform) in ("win32", "darwin", "cygwin"):
        return not (env.get("SSH_CONNECTION") or env.get("SSH_TTY"))
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


CONSOLE = ("www-browser", "links", "links2", "elinks", "lynx", "w3m", "browsh")


def window_browsers():
    """webbrowser's choices here, in its order, less console browsers (they would take over this terminal while the
    hand-off waits for a file nobody can save). One it does not know by name (BROWSER=google-chrome-stable) runs in
    the background, as a browser with a window does: webbrowser would wait for it to exit."""
    import webbrowser
    try:
        webbrowser.get()                              # (reads BROWSER and finds the installed browsers)
    except webbrowser.Error:
        return []
    found = []
    for name in list(getattr(webbrowser, "_tryorder", None) or []):
        try:
            b = webbrowser.get(name)
        except webbrowser.Error:
            continue
        cmd = Path(str(getattr(b, "name", "") or "")).name.lower().removesuffix(".exe")
        if cmd in CONSOLE or isinstance(b, getattr(webbrowser, "Elinks", ())):
            continue
        if type(b) is webbrowser.GenericBrowser:
            b = webbrowser.BackgroundBrowser([b.name] + list(b.args))
        found.append(b)
    return found


def desktop_browser():
    """What opens a link in a browser window here (each of webbrowser's choices in turn), or None: no desktop, or
    only console browsers."""
    found = window_browsers() if gui() else []
    if not found:
        return None

    def open_url(url):
        for b in found:
            try:
                if b.open(url):
                    return True
            except Exception:
                continue
        return False
    return open_url


def interactive(stdin=None, stdout=None, environ=None, stderr=None):
    """A person is watching this run: stdin, stdout and stderr (where the hand-off says what to save) are a terminal,
    it is not CI, and this is the program's main thread (a web UI or server started from a terminal handles its
    requests on other threads)."""
    env = os.environ if environ is None else environ
    if env.get("CI") or env.get("ECADLIB_NO_HANDOFF") or threading.current_thread() is not threading.main_thread():
        return False
    try:
        return bool((stdin or sys.stdin).isatty() and (stdout or sys.stdout).isatty() and (stderr or sys.stderr).isatty())
    except (AttributeError, ValueError):
        return False


def _key(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def names_part(data, part, pages=3):
    """True when the first pages' text names the part (its MPN, or its first 4+ characters as a family name)."""
    key = _key(part)
    if len(key) < 3:
        return False
    try:
        import io, pypdf
        rd = pypdf.PdfReader(io.BytesIO(data))
        text = _key(" ".join((p.extract_text() or "") for p in rd.pages[:pages]))
    except Exception:
        return False
    if key in text:
        return True
    base = re.match(r"[A-Z]+\d+", key)                # LTM4671IY#PBF -> LTM4671
    return bool(base and len(base.group(0)) >= 4 and base.group(0) in text)


class Handoff:
    """Open a refused link in the person's own browser and take the PDF from their Downloads folder.

    A new .pdf there (not one that was there before, not a partial download) is taken when its name matches the
    link's file name or the part number, or its text names the part; anything else is left alone, so an unrelated
    download is never read as this part's datasheet. The file stays where it is (it is the person's); its bytes are
    kept in ecadlib's datasheet cache under the link, so the next run needs no browser."""

    def __init__(self, folder=None, wait_s=180, say=None, open_url=None, poll=1.0, sleep=None, clock=None):
        self.folder = Path(folder) if folder else downloads_dir()
        self.wait_s, self.poll = wait_s, poll
        self.say = say or (lambda m: print(m, file=sys.stderr, flush=True))
        self.open_url = open_url
        self.sleep, self.clock = sleep or time.sleep, clock or time.monotonic
        self.lock, self.tried, self.gone = threading.Lock(), set(), False

    def _pdfs(self):
        try:
            return {p: p.stat() for p in self.folder.iterdir()
                    if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.lower().endswith(PARTIAL)}
        except OSError:
            return {}

    def get(self, url, part=None):
        from .datasheet import is_pdf
        if not self.folder.is_dir():                  # (no path in what is returned: it is kept in the part's record)
            return 0, None, "", url, "your Downloads folder was not found (set [datasheet] downloads_folder)"
        try:
            with os.scandir(self.folder) as it:
                next(it, None)
        except OSError as e:                          # macOS: this terminal was not allowed to read Downloads
            return 0, None, "", url, (f"cannot read your Downloads folder ({type(e).__name__}): allow this terminal to "
                                      "read it (macOS: System Settings > Privacy & Security > Files and Folders) or set "
                                      "[datasheet] downloads_folder")
        if self.gone:
            return 0, None, "", url, "not opened in your browser: an earlier one got no PDF in time (nobody watching?)"
        same = url.split("://", 1)[-1].split("#")[0]
        if same in self.tried:                        # http and https of one link: asked once
            return 0, None, "", url, "already opened in your browser this run"
        self.tried.add(same)
        with self.lock:                               # one link at a time: each new file belongs to one request
            before = {p: (s.st_size, s.st_mtime) for p, s in self._pdfs().items()}
            stem = _key(Path(urlparse(url).path).stem)
            opener = self.open_url or desktop_browser() or (lambda u: False)
            try:
                ok = opener(url)
            except Exception as e:
                return 0, None, "", url, f"could not open your browser ({type(e).__name__}: {e})"
            if ok is False:                           # no browser here (SSH, a container): nothing can arrive
                self.say(f"  The site refused ecadlib's download of {part or 'this datasheet'}, and no browser could be "
                         f"opened here. Download it yourself:\n    {url}\n  and give it to ecadlib with "
                         f"`ecadlib datasheet FILE.pdf --part {part or 'MPN'}`.")
                return 0, None, "", url, "no browser could be opened here"
            self.say(f"  The site refused ecadlib's download of {part or 'this datasheet'}; it is open in your browser:\n"
                     f"    {url}\n  Save the PDF to {self.folder} (if it opens in the browser, use its Download button).\n"
                     f"  Waiting up to {self.wait_s:g} s for it ...")
            end, sizes, others = self.clock() + self.wait_s, {}, set()
            while self.clock() < end:
                for p, s in self._pdfs().items():
                    seen = (p, s.st_size, s.st_mtime)  # a file looked at once is looked at again when it changes
                    if before.get(p) == (s.st_size, s.st_mtime) or seen in others or not s.st_size:
                        continue                      # (Firefox writes an empty file first, then renames over it)
                    if sizes.get(p) != s.st_size:     # still being written: look again next time
                        sizes[p] = s.st_size
                        continue
                    try:
                        data = p.read_bytes()
                    except OSError:
                        continue
                    if not is_pdf(data):
                        others.add(seen)
                        continue
                    k, pk = _key(p.stem), _key(part)
                    if (stem and (k == stem or (len(stem) >= 4 and k.startswith(stem)))) or (len(pk) >= 4 and pk in k) \
                            or (part and names_part(data, part)):
                        self.say(f"  Got {p.name} ({len(data) >> 10} KB).")
                        return 200, data, "application/pdf", url, None, {"x-ecadlib-file": p.name}
                    others.add(seen)
                    self.say(f"  {p.name} is not named for {part or 'this link'} and does not mention it; left alone.")
                self.sleep(self.poll)
            self.gone = True
            return 0, None, "", url, f"no PDF for it appeared in your Downloads folder within {self.wait_s:g} s"
