"""Tiny disk-cached, per-host rate-limited HTTP client on top of requests.

Responses are stored as JSON files keyed by sha256(method, url, body). Only 2xx/404
responses are cached (not a 2xx whose JSON body is an `errors` list); network errors are returned (not raised) so callers can
report "source unreachable" instead of crashing. Downloaded bodies are treated as
untrusted data: they are only ever parsed as JSON/text, never executed."""
import hashlib, json, os, re, threading, time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

UA = "ecad-librarian-prototype/0.1 (+https://github.com/; contact: maintainers)"


class Response:
    def __init__(self, status, text, from_cache=False, error=None):
        self.status, self.text, self.from_cache, self.error = status, text, from_cache, error

    @property
    def ok(self):
        return self.error is None and 200 <= self.status < 300

    def json(self):
        return json.loads(self.text)


SECRET_Q = re.compile(r"(api[_-]?key|key|token|secret|password|signature|sig|client[_-]?secret|access[_-]?token)$", re.I)


VARY_H = re.compile(r"locale|currency|language", re.I)


_ERRORS = re.compile(r'"[eE]rrors"\s*:\s*[\[{"]')


def error_body(text):
    """True for a 2xx JSON answer that is really an error: GraphQL `errors` (Nexar: bad token, quota, a field the plan
    does not include) or Mouser's `Errors` list (bad API key, daily limit). It is not cached: the key or the quota
    may be fixed by the next run, and a cached error would come back for the whole TTL."""
    if not _ERRORS.search(text[:65536]):
        return False
    try:
        d = json.loads(text)
    except ValueError:
        return False
    return isinstance(d, dict) and bool(d.get("errors") or d.get("Errors"))


_HTML = re.compile(r"\s*(?:<!doctype\s+html|<html[\s>])", re.I)
DATA_EXT = {"json", "kicad_mod", "kicad_sym", "lib", "dcm", "csv", "txt", "xml", "step", "stp", "wrl", "gz"}
DATA_HOSTS = {"modules.easyeda.com"}          # data only, at paths with no extension (easyeda_3d.STEP_URL: /<key>/<uuid>)


def page_not_data(url, text):
    """True for an HTML page answering a URL that serves data (an API, a raw library file): a captive portal or proxy
    block page sent with HTTP 200. It is not cached, so the next run, on a working network, asks again."""
    if not _HTML.match(text[:512]):
        return False
    u = urlparse(url)
    last = u.path.rsplit("/", 1)[-1]
    ext = last.rpartition(".")[2].lower() if "." in last else ""
    return (u.netloc.lower().startswith("api.") or u.netloc.lower() in DATA_HOSTS or "/api/" in u.path + "/" or u.path.endswith("/graphql")
            or "/-/raw/" in u.path or ext in DATA_EXT)


# A download that may work on a later try: a timeout, a dropped or reset connection (not a TLS or proxy refusal,
# which fails the same way every time), 429 or 5xx. Used by datasheet.PdfStore (PDF downloads only): RETRIES more
# tries, the wait doubling from BACKOFF s, or the server's Retry-After up to RETRY_AFTER_MAX s.
RETRIES, BACKOFF, RETRY_AFTER_MAX = 2, 2.0, 30.0
TRANSIENT = re.compile(r"^(ReadTimeout|ConnectTimeout|Timeout|ConnectionError|ChunkedEncodingError|ReadTimeoutError|"
                       r"ProtocolError|IncompleteRead)\b|ConnectionResetError|Connection reset|RemoteDisconnected")


def retry_after(value):
    """A Retry-After header (seconds, or an HTTP date) -> seconds to wait, capped at RETRY_AFTER_MAX; None if unreadable."""
    v = str(value or "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return min(float(v), RETRY_AFTER_MAX)
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    try:
        when = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return min(max(0.0, (when - datetime.now(timezone.utc)).total_seconds()), RETRY_AFTER_MAX)


def redact(url):
    u = urlparse(url)
    q = [(k, "REDACTED" if SECRET_Q.search(k) else v) for k, v in parse_qsl(u.query, keep_blank_values=True)]
    return urlunparse(u._replace(query=urlencode(q))) if q else url


# Credential parameters by their whole name: left out of the cache key (a rotated key keeps its cached answers). A
# name that only ends in token/key (pageToken, nextToken, continuationToken, sortkey) is part of the request and of
# the key: page 2 is not page 1. The stored URL is still redacted with SECRET_Q.
CRED_Q = re.compile(r"(api[_-]?key|apikey|x-api-key|key|token|secret|password|passwd|signature|sig|client[_-]?secret|"
                    r"access[_-]?token|auth[_-]?token|subscription[_-]?key)", re.I)


def key_url(url):
    u = urlparse(url)
    q = [(k, "REDACTED" if CRED_Q.fullmatch(k) else v) for k, v in parse_qsl(u.query, keep_blank_values=True)]
    return urlunparse(u._replace(query=urlencode(q))) if q else url


class CachedHTTP:
    def __init__(self, cache_dir="out/http_cache", min_interval=1.0, ttl_s=7 * 86400,
                 offline=False, timeout=20):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        from . import migrate as MG                   # a cache stamped by a newer ecadlib is refused (never misread)
        MG.open_folder(self.dir, ["http_cache"], write=False)
        self.min_interval, self.ttl, self.offline, self.timeout = min_interval, ttl_s, offline, timeout
        self._last = {}  # host -> last request time
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.log = []  # (method, url, status/error, cached)

    def _key(self, method, url, body, headers=None):
        # headers that change the answer (DigiKey's X-DIGIKEY-Locale-Currency: the same URL priced in EUR or USD) are
        # part of the key; auth headers never are (tokens rotate, and must not decide what is cached)
        vary = sorted((k.lower(), str(v)) for k, v in (headers or {}).items() if VARY_H.search(k))
        extra = "".join(f"\n{k}: {v}" for k, v in vary)
        return hashlib.sha256(f"{method}\n{url}\n{body or ''}{extra}".encode()).hexdigest()[:32]

    def request(self, method, url, body=None, headers=None, cache=True, timeout=None):
        """cache=False: never read or store (credentials in the URL or body, tokens in the response). timeout: seconds
        for this request (default: the client's).
        Query-string secrets (apiKey=, token=...) are redacted from cache keys and stored URLs.
        A 2xx answer whose body is an error (error_body) is neither stored nor served from an older cache entry."""
        f = self.dir / f"{self._key(method, key_url(url), body, headers)}.json"
        d = None
        bad = lambda st, text: 200 <= st < 300 and (error_body(text) or page_not_data(url, text))
        if cache and f.exists() and (self.offline or time.time() - f.stat().st_mtime < self.ttl):   # offline: stale beats nothing
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                d = d if isinstance(d, dict) and isinstance(d.get("status"), int) and isinstance(d.get("text"), str) else None
            except (OSError, ValueError):            # a damaged entry (crash, full disk) is a miss, not a crash
                d = None
            if d is not None and bad(d["status"], d["text"]):
                d = None
        if d is not None:
            self.log.append((method, url, d["status"], True))
            return Response(d["status"], d["text"], from_cache=True)
        if self.offline:
            self.log.append((method, url, "offline-miss", False))
            return Response(0, "", error="offline: not in cache")
        host = urlparse(url).netloc
        wait = self.min_interval - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()
        try:
            from .term import waiting, source_name               # a slow source says so on the terminal
            with waiting(source_name(host)):
                r = self.s.request(method, url, data=body, headers=headers, timeout=timeout or self.timeout)
        except requests.RequestException as e:
            m = re.search(r"Tunnel connection failed: ([^'\")]+)", str(e))
            text = re.sub(r"([?&])([^=&\s'\"]+)=([^&\s'\")]*)",          # the URL in the message: no ?apiKey= value
                          lambda q: f"{q[1]}{q[2]}=REDACTED" if SECRET_Q.search(q[2]) else q[0], str(e))
            msg = f"{type(e).__name__}: " + (f"proxy CONNECT refused ({m.group(1).strip()})" if m else text[:120])
            self.log.append((method, url, msg, False))
            return Response(0, "", error=msg)
        self.log.append((method, url, r.status_code, False))
        if cache and (200 <= r.status_code < 300 or r.status_code == 404) and not bad(r.status_code, r.text):
            tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp")   # own temp file per writer: two
            tmp.write_text(json.dumps({"url": redact(url), "status": r.status_code, "text": r.text}), encoding="utf-8")
            os.replace(tmp, f)                                                       # threads fetching one URL never share one
        return Response(r.status_code, r.text, error=None if r.ok or r.status_code == 404
                        else f"HTTP {r.status_code}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, body, **kw):
        return self.request("POST", url, body=body, **kw)
