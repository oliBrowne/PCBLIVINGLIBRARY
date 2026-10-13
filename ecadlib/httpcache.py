"""Tiny disk-cached, per-host rate-limited HTTP client on top of requests.

Responses are stored as JSON files keyed by sha256(method, url, body). Only 2xx/404
responses are cached; network errors are returned (not raised) so callers can
report "source unreachable" instead of crashing. Downloaded bodies are treated as
untrusted data: they are only ever parsed as JSON/text, never executed."""
import hashlib, json, os, re, time
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


def redact(url):
    u = urlparse(url)
    q = [(k, "REDACTED" if SECRET_Q.search(k) else v) for k, v in parse_qsl(u.query, keep_blank_values=True)]
    return urlunparse(u._replace(query=urlencode(q))) if q else url


class CachedHTTP:
    def __init__(self, cache_dir="out/http_cache", min_interval=1.0, ttl_s=7 * 86400,
                 offline=False, timeout=20):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.min_interval, self.ttl, self.offline, self.timeout = min_interval, ttl_s, offline, timeout
        self._last = {}  # host -> last request time
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.log = []  # (method, url, status/error, cached)

    def _key(self, method, url, body):
        return hashlib.sha256(f"{method}\n{url}\n{body or ''}".encode()).hexdigest()[:32]

    def request(self, method, url, body=None, headers=None, cache=True):
        """cache=False: never read or store (credentials in the URL or body, tokens in the response).
        Query-string secrets (apiKey=, token=...) are redacted from cache keys and stored URLs."""
        f = self.dir / f"{self._key(method, redact(url), body)}.json"
        if cache and f.exists() and (self.offline or time.time() - f.stat().st_mtime < self.ttl):   # offline: stale beats nothing
            d = json.loads(f.read_text())
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
            r = self.s.request(method, url, data=body, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            m = re.search(r"Tunnel connection failed: ([^'\")]+)", str(e))
            msg = f"{type(e).__name__}: " + (f"proxy CONNECT refused ({m.group(1).strip()})" if m else str(e)[:120])
            self.log.append((method, url, msg, False))
            return Response(0, "", error=msg)
        self.log.append((method, url, r.status_code, False))
        if cache and (200 <= r.status_code < 300 or r.status_code == 404):
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"url": redact(url), "status": r.status_code, "text": r.text}))
            os.replace(tmp, f)
        return Response(r.status_code, r.text, error=None if r.ok or r.status_code == 404
                        else f"HTTP {r.status_code}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, body, **kw):
        return self.request("POST", url, body=body, **kw)
