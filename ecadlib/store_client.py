"""Shared store of confirmed parts, read side (docs/shared-store-plan.md): find a part's record, check it, and say
whether a run may use it.

    st = Store("https://raw.githubusercontent.com/OWNER/REPO/main", http=CachedHTTP(...), cache_dir=...)
    hit = st.lookup("TI", "NE555DR")       # Hit or None; never raises
    decide(hit, accept="confirmed")        # "use" | "second-opinion" | "skip"
    st.trouble                             # one line per distinct problem, for the run log

A store is a GitHub repository read as raw files, a file:// URL or a plain folder ("" turns the store off: every
lookup is a miss and nothing is contacted):

    store.json                 {"schema", "name", "shards", "min_ecadlib", "updated"}
    index/<ab>.json            one shard, by store_keys.shard(MPN): {key_id: {"key", "also", "rev", "sha256", "level",
                               "status"}}
    parts/<ab>/<key_id>.json   the record; refused unless store_keys.digest(record) is the index's sha256
    withdrawn.json             [{"key_id", "key", "rev", "reason", "issue", "at"}]: never used at that rev or older

Privacy (online mode): every lookup asks for store.json, withdrawn.json and the index shard of the MPN (one of 256
slices, so that request names 1/256 of all parts, never a maker or a part number). Only on a hit is the record itself
fetched, and that request does identify the exact part: its file name is the key id, which the public index lists
beside the maker and part number. mode="snapshot" downloads the whole store as one zip (at most once per
SNAPSHOT_TTL_S), unpacks it safely and answers every lookup from that folder: the host then learns nothing about
which parts you look up. offline=True reads the cache only. Only https://, file:// URLs and folders are read: a
plain http:// store or snapshot URL is refused (one trouble line), and every online answer is capped in size
(FETCH_MAX_BYTES, RECORD_MAX_BYTES). No store request follows an HTTP redirect (an answer comes from the store's own
URL or not at all), except the snapshot download's from github.com to GitHub's release file host, over https only
(redirect_ok).

Caching (online mode, under cache_dir): store.json, the index shards and withdrawn.json are asked again after TTL_S;
an older copy is used when the network fails. A record is immutable for its sha256, so it is kept for good by that
hash. The first network failure of a Store stops it asking again for the rest of the run (cache only), so a BOM of
500 lines waits for one timeout, not 500, and the log says "could not be reached" once. A copy older than MAX_AGE_S
(an index shard, withdrawn.json, a snapshot) is never good enough to build a part from: such a lookup is a miss, with
one trouble line saying how old the copy is.

What a lookup checks before it returns a Hit: the record's sha256 is the index's, the index key id is the key's
(store_keys.key_id), the record's own key, rev and status are the index entry's, and its level is the LOWER of the
index's and the one store_record.level gives the record itself (store.json "trusted": the contributors whose
evidence counts, null = everyone, a list always with the "maintainers" in it, as the store's own index counts them). A match through another code (`also`) or without a maker is never
used to build a part (decide), only compared.

Parts that depend on the store (built from a record, or released by a confirmed record's agreement: depends) are
checked again by every librarian run, upkeep and publisher (problems): the store must vouch for them. One that is
switched off ([store] url empty, accept "off") cannot, nor can one whose withdrawn list cannot be read when no copy
younger than HOLD_MAX_AGE_S is saved on this computer: the part is held until a person releases it (--allow) or the
store can be read. A list that could not be read is never taken for "nothing withdrawn". Parts that owe the store
nothing are never touched, whatever the store's settings.

A record's meaning is store_record.py's; this module reads its schema, key, rev, status and (for level) evidence.
Everything from the store that is printed goes through safe() (no terminal control characters)."""
import hashlib, io, json, os, re, shutil, tempfile, time, zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import url2pathname

from . import store_keys as SK, store_record as SR

TTL_S = 6 * 3600                    # store.json, index shards, withdrawn.json: asked again after this long
SNAPSHOT_TTL_S = 86400              # snapshot mode: the zip is downloaded again after this long
SNAPSHOT_MAX_BYTES = 1024 * 2 ** 20  # unpacked size of a snapshot, and the download's size
SNAPSHOT_MAX_MEMBERS = 500_000
MAX_AGE_S = 7 * 86400               # an index / withdrawn list / snapshot older than this builds no part
HOLD_MAX_AGE_S = 30 * 86400         # a part that depends on the store is held once its withdrawn list is older than this
FETCH_MAX_BYTES = 16 * 2 ** 20       # store.json, an index shard, withdrawn.json: the most an answer may hold
RECORD_MAX_BYTES = 2 * SR.MAX_BYTES  # a record (store_record refuses one over MAX_BYTES anyway)
ACCEPT = ("confirmed", "checked", "off")
LEVELS = ("checked", "confirmed")    # low to high
SOURCE = re.compile(r"store:([0-9a-f]{32})@r(\d+)")
KID = re.compile(r"[0-9a-f]{32}")
_CTRL = SR._CTRL                    # C0 / C1 controls, bidi controls, zero-width characters
REDIRECT_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
OFF = ("this part came from the shared store, which is switched off, so withdrawals cannot be checked: release it "
       "yourself or switch the store on")
OFF_LIFTED = ("this part was released because a shared store record agreed with it, and the shared store is switched "
              "off, so withdrawals cannot be checked: the holds it lifted are back; release it yourself or switch the "
              "store on")


def safe(text):
    """Text from the store as it may be printed: C0 / C1 control characters (terminal escapes), bidirectional-text
    controls and zero-width characters taken out, newlines and tabs kept."""
    return _CTRL.sub("", str(text))


def configured(rules):
    """A shared store is set in these rules: [store] url not empty and accept not "off"."""
    s = (rules or {}).get("store") if isinstance(rules, dict) else None
    s = s if isinstance(s, dict) else {}
    return bool(str(s.get("url") or "").strip()) and str(s.get("accept") or "confirmed").strip().lower() != "off"


def lower(a, b):
    """The lower of two levels ('checked' < 'confirmed'); anything else is no level ('')."""
    if a not in LEVELS or b not in LEVELS:
        return ""
    return LEVELS[min(LEVELS.index(a), LEVELS.index(b))]


@dataclass
class Hit:
    record: dict
    key: str
    key_id: str
    rev: int
    level: str
    status: str
    sha256: str
    note: str = ""
    via: str = "exact"              # exact | also (another code the record covers) | no-maker (the BOM named none)

    @property
    def source(self):
        """The source line a part built from this record carries: store:<key_id>@r<rev>."""
        return f"store:{self.key_id}@r{self.rev}"


def parse_source(s):
    """'store:<key_id>@r<rev>' -> (key_id, rev); None for any other source."""
    m = SOURCE.fullmatch(str(s or "").strip())
    return (m[1], int(m[2])) if m else None


def origin(source, store=None, half="footprint"):
    """Where a half really came from: for a half built from a store record (store:<key_id>@r<rev>) the record's own
    source (kicad:Lib:Name, standard:..., datasheet, easyeda:C123), as the part's `store` block names it ({"symbol",
    "footprint", ...}, written by the librarian); for any other half its own source. A store half with no such block
    stays store:... (never taken for a KiCad part: no relaxed check, and no credit to keep)."""
    s = str(source or "")
    if parse_source(s) and isinstance(store, dict) and isinstance(store.get(half), str) and store[half]:
        return store[half]
    return s


def item_origin(item, half):
    """origin() of a plan item's (or librarian state record's) half."""
    src = (item or {}).get("source")
    s = src.get(half) if isinstance(src, dict) else ((item or {}).get(half) or {}).get("source")
    return origin(s, (item or {}).get("store"), half)


def decide(hit, accept="confirmed"):
    """What a run does with a hit: 'use' (build from it), 'second-opinion' (keep it to compare with the local build)
    or 'skip'. A disputed record is only ever a second opinion; a checked one is used only with accept='checked'; a
    match through an `also` code or without a maker is never used, only compared."""
    if hit is None or accept not in ACCEPT or accept == "off" or hit.status not in ("active", "disputed") \
            or hit.level not in ("confirmed", "checked"):
        return "skip"
    if hit.status == "disputed" or getattr(hit, "via", "exact") != "exact":
        return "second-opinion"
    return "use" if hit.level == "confirmed" or accept == "checked" else "second-opinion"


def _version(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _rev(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def redirect_ok(src, dest):
    """A snapshot download may follow this redirect: both https, from github.com to GitHub's own hosts (a release
    asset is served from objects.githubusercontent.com). Nothing else a store reads ever follows a redirect."""
    a, b = urlparse(str(src)), urlparse(str(dest))
    return a.scheme == b.scheme == "https" and (a.hostname or "") in REDIRECT_HOSTS and \
        (b.hostname or "") in REDIRECT_HOSTS and not b.username and not b.password and b.port in (None, 443)


def default_snapshot_url(url):
    """Where a store's weekly zip is: for a GitHub raw URL the rolling `snapshot` release of that repository (the
    store's snapshot workflow attaches it there; a zip is never committed), else <url>/snapshot.zip."""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", str(url or "") + "/")
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}/releases/download/snapshot/snapshot.zip"
    return str(url) + "/snapshot.zip" if url else ""


class Store:
    def __init__(self, url, http=None, cache_dir=None, offline=False, mode="online", now=time.time, ttl_s=TTL_S,
                 snapshot_url=None, snapshot_ttl_s=SNAPSHOT_TTL_S, accept="confirmed"):
        self.url, self.http, self.offline, self.mode, self.now = str(url or "").strip().rstrip("/"), http, offline, \
            mode, now
        self.ttl, self.snapshot_ttl, self.accept = ttl_s, snapshot_ttl_s, accept
        self.snapshot_url = snapshot_url or default_snapshot_url(self.url)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.trouble = []
        self.meta = {}
        self._mem, self._usable, self._down, self._root, self._tmp = {}, None, False, None, None
        self._down_msg, self._ages, self._snap_age, self._wd_state, self._stale_said = "", {}, 0.0, None, False
        self._old, self._snap_old, self._wd_old_said, self.withdrawn_age = set(), False, False, None
        low = self.url.lower()
        self.remote = low.startswith(("https://", "http://"))
        if not self.url:
            self.kind = "off"
        elif low.startswith("http://") or (mode == "snapshot" and str(self.snapshot_url).lower().startswith("http://")):
            self.kind = "refused"                    # a plain http:// answer can be changed on the way: never read
            self._note("shared store: " + ("its snapshot URL" if not low.startswith("http://") else "its URL")
                       + " is a plain http:// link; only https://, file:// and folders are read, so it is not used")
        elif self.remote:
            self.kind = "snapshot" if mode == "snapshot" else "http"
        else:
            self.kind = "folder"
            self._root = Path(url2pathname(urlparse(self.url).path)) if low.startswith("file:") else Path(self.url)

    @property
    def enabled(self):
        return self.kind != "off"

    def _note(self, msg):
        msg = safe(msg)
        if msg not in self.trouble:
            self.trouble.append(msg)

    # ------------------------------------------------------------ reading one file
    def _cache(self):
        if self.cache_dir is None:
            if self._tmp is None:
                self._tmp = tempfile.TemporaryDirectory(prefix="ecadlib-store-")
            base = Path(self._tmp.name)
        else:
            base = self.cache_dir
        return base / "store" / hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]

    def _http(self):
        if self.http is None:
            from .httpcache import CachedHTTP
            self.http = CachedHTTP(cache_dir=self._cache() / "http", min_interval=0, offline=self.offline)
        return self.http

    def _get(self, url, cap):
        """(status, text, error) of one GET, reading at most cap bytes of the answer (a longer one: status -1)."""
        http = self._http()
        from .httpcache import CachedHTTP
        if isinstance(http, CachedHTTP):             # the real client: streamed, so a huge answer is never read whole
            r = http.s.get(url, timeout=http.timeout or 20, stream=True, allow_redirects=False)
            try:
                if 300 <= r.status_code < 400:       # never followed: the store's answer comes from the store's URL
                    return r.status_code, "", f"HTTP {r.status_code} redirect not followed"
                if r.status_code == 404:
                    return 404, "", None
                if not 200 <= r.status_code < 300:
                    return r.status_code, "", f"HTTP {r.status_code}"
                buf = io.BytesIO()
                for chunk in r.iter_content(1 << 16):
                    buf.write(chunk)
                    if buf.tell() > cap:
                        return -1, "", None
                return r.status_code, buf.getvalue().decode("utf-8"), None
            finally:
                r.close()
        r = http.get(url, cache=False)               # a test's fake or another client: its whole answer, then the cap
        status, text, err = getattr(r, "status", 0), getattr(r, "text", ""), getattr(r, "error", None)
        if err is None and 300 <= status < 400:
            return status, "", f"HTTP {status} redirect not followed"
        if err is None and 200 <= status < 300 and isinstance(text, str) and len(text.encode("utf-8")) > cap:
            return -1, "", None
        return status, text, err

    def _fetch(self, rel, cap=None):
        """GET base/rel -> ('ok', text) | ('missing', None) | ('error', 'offline' | 'down' | 'large'). Never raises.
        cap: the most bytes the answer may hold (FETCH_MAX_BYTES)."""
        cap = FETCH_MAX_BYTES if cap is None else cap
        if self.offline:
            return "error", "offline"
        if self._down:
            return "error", "down"
        try:
            status, text, err = self._get(f"{self.url}/{rel}", cap)
        except Exception as e:                       # a network error, a fake that raises, bad UTF-8: still a miss
            status, text, err = 0, "", f"{type(e).__name__}: {e}"
        if status == -1:
            self._note(f"shared store: {rel} is larger than {cap} bytes; not used")
            return "error", "large"
        if status == 404:
            return "missing", None
        if err is None and 200 <= status < 300 and isinstance(text, str):
            return "ok", text
        self._down, self._down_msg = True, str(err or f"HTTP {status}")[:120]
        return "error", "down"

    def _unreached(self, then):
        self._note(f"shared store could not be reached ({self._down_msg or 'no answer'}); {then}")

    def _read_cached(self, f):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) and isinstance(d.get("at"), (int, float)) else None
        except (OSError, ValueError):
            return None

    def _write(self, f, text):
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass                                     # a cache that cannot be written only costs a later request

    def _raw(self, rel, sha=None):
        """Text of a store file -> ('ok', text) | ('missing', None) | ('error', why). sha: a record, cached for good
        by its hash; otherwise store.json / an index shard / withdrawn.json, cached for self.ttl (self._ages: how old
        the copy used is). why: 'down' / 'offline' (nothing could be asked) or 'bad' (the store answered, wrongly)."""
        if self.kind == "refused":
            return "error", "down"
        if self.kind in ("folder", "snapshot"):
            root = self._folder()
            if root is None:
                return "error", "down"
            f = root / rel
            self._ages[rel] = self._snap_age
            try:
                return ("ok", f.read_text(encoding="utf-8")) if f.is_file() else ("missing", None)
            except (OSError, UnicodeDecodeError) as e:
                self._note(f"shared store: cannot read {rel} ({type(e).__name__})")
                return "error", "bad"
        cache = self._cache()
        if sha:
            f = cache / "records" / f"{sha}.json"
            try:
                text = f.read_text(encoding="utf-8")
                if self._digest_ok(text, sha):
                    return "ok", text
            except OSError:
                pass
            got, text = self._fetch(rel, RECORD_MAX_BYTES)   # (read at call time: tests lower the caps)
            if got == "ok" and self._digest_ok(text, sha):
                self._write(f, text)
            elif got == "error" and text == "offline":
                self._note("shared store: offline and the record is not saved on this computer; parts are built "
                           "without it")
            elif got == "error" and text == "down":
                self._unreached("parts whose record is not saved on this computer are built without it")
            return (got, text) if got != "error" else ("error", "bad" if text == "large" else text)
        f = cache / "live" / rel
        old = self._read_cached(f)
        if old is not None and (self.offline or self._down or 0 <= self.now() - old["at"] < self.ttl):
            self._ages[rel] = max(0.0, self.now() - old["at"])
            if not 0 <= self.now() - old["at"] < self.ttl:
                self._old.add(rel)                   # served from this computer because the store was not asked
            return ("ok", old["text"]) if isinstance(old.get("text"), str) else ("missing", None)
        got, text = self._fetch(rel)
        if got == "ok" or got == "missing":
            self._write(f, json.dumps({"at": self.now(), "text": text}))
            self._ages[rel] = 0.0
            return got, text
        if text == "large":
            return "error", "bad"
        if old is not None:                          # the network failed: an old copy beats none (MAX_AGE_S decides
            self._ages[rel] = max(0.0, self.now() - old["at"])   # whether it may build a part)
            self._old.add(rel)
            if text == "down":
                self._unreached("using the copy saved on this computer earlier")
            return ("ok", old["text"]) if isinstance(old.get("text"), str) else ("missing", None)
        if text == "offline":
            self._note("shared store: offline and nothing of it is saved on this computer yet; parts are built without "
                       "it")
        else:
            self._unreached("parts are built without it this run")
        return "error", text

    @staticmethod
    def _digest_ok(text, sha):
        try:
            return SK.digest(json.loads(text)) == sha
        except Exception:                            # not JSON, nested too deep, too big: not the record
            return False

    def _json(self, rel):
        """A live store file (store.json, an index shard, withdrawn.json) -> ('ok', parsed) | ('missing', None) |
        ('error', 'down' | 'offline' | 'bad'); not JSON is 'bad', with a trouble line. Within one Store each file is
        read once."""
        if rel not in self._mem:
            got, text = self._raw(rel)
            d = None
            if got == "ok":
                try:
                    d = json.loads(text)
                except Exception as e:               # not JSON, or JSON nested too deep / too big to read
                    self._note(f"shared store: {rel} is not readable JSON ({type(e).__name__}); ignored")
                    got, d = "error", "bad"
            elif got == "error":
                d = text
            self._mem[rel] = (got, d)
        return self._mem[rel]

    def age(self, *rels):
        """Seconds since the oldest of these store files (or the snapshot) was read from the store itself."""
        return max([self._snap_age] + [self._ages.get(r, 0.0) for r in rels])

    # ------------------------------------------------------------ snapshot mode
    def _folder(self):
        """The folder lookups read: the store folder, or (snapshot mode) the unpacked snapshot; None if none."""
        if self.kind == "folder" or self._root is not None or self._down:
            return self._root
        base = self._cache() / "snapshot"
        stamp = self._read_cached(base / "stamp.json")
        rel = str((stamp or {}).get("tree") or "")
        tree = base / rel if re.fullmatch(r"tree-[\w-]+(/[^/\\:]+)?", rel) and ".." not in rel else base / "none"
        have = stamp is not None and (tree / "store.json").is_file()
        if have and (self.offline or 0 <= self.now() - stamp["at"] < self.snapshot_ttl):
            self._root, self._snap_age = tree, max(0.0, self.now() - stamp["at"])
            self._snap_old = not 0 <= self.now() - stamp["at"] < self.snapshot_ttl
            return tree
        data, err = self._download()
        new = self._unpack(data, base) if data is not None else None
        if new is not None:
            self._write(base / "stamp.json", json.dumps({"at": self.now(), "tree": new.relative_to(base).as_posix()}))
            if have and tree != new:                 # the old unpacked copy (its tree-* folder) goes
                shutil.rmtree(base / tree.relative_to(base).parts[0], ignore_errors=True)
            self._root = new
        elif data is None:
            self._note(f"shared store snapshot could not be downloaded ({err}); "
                       + ("using the copy from an earlier run" if have else "parts are built without it this run"))
            self._root = tree if have else None
        else:
            self._root = tree if have else None
        if have and self._root == tree:              # an old copy serves: how old (MAX_AGE_S)
            self._snap_age = max(0.0, self.now() - stamp["at"])
            self._snap_old = True
        self._down = self._root is None
        return self._root

    def _download(self):
        if self.offline:
            return None, "offline and no snapshot is cached"
        url, http = self.snapshot_url, self._http()
        try:
            getb = getattr(http, "get_bytes", None)
            if callable(getb):
                data = getb(url)
            else:
                for _ in range(5):                   # redirects: only GitHub's release download (github.com to its
                    r = http.s.get(url, timeout=max(120, getattr(http, "timeout", 0) or 0), stream=True,  # file host)
                                   allow_redirects=False)
                    if not 300 <= r.status_code < 400:
                        break
                    nxt = urljoin(url, r.headers.get("Location") or "")
                    r.close()
                    if not redirect_ok(url, nxt):
                        return None, f"redirected to {safe(nxt)[:80]}, which is not GitHub's release download over https"
                    url = nxt
                else:
                    return None, "too many redirects"
                if r.status_code != 200:
                    return None, f"HTTP {r.status_code}"
                buf = io.BytesIO()
                for chunk in r.iter_content(1 << 16):
                    buf.write(chunk)
                    if buf.tell() > SNAPSHOT_MAX_BYTES:
                        return None, "larger than the size cap"
                data = buf.getvalue()
        except Exception as e:
            return None, f"{type(e).__name__}: {str(e)[:120]}"
        return (data, None) if isinstance(data, (bytes, bytearray)) else (None, "no data")

    def _unpack(self, data, base):
        """Unpack a snapshot zip into a new folder under base -> the store's root in it, or None (refused, with one
        trouble line). Refuses absolute paths, drive letters, '..', links, too many members and too many bytes."""
        def refuse(why):
            self._note(f"shared store snapshot refused: {why}; parts are built without it this run")
            return None
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            infos = z.infolist()
        except (zipfile.BadZipFile, ValueError, OSError):
            return refuse("not a zip file")
        if len(infos) > SNAPSHOT_MAX_MEMBERS:
            return refuse(f"more than {SNAPSHOT_MAX_MEMBERS} files")
        if sum(i.file_size for i in infos) > SNAPSHOT_MAX_BYTES:
            return refuse("larger than the size cap")
        for i in infos:
            parts = re.split(r"[\\/]", i.filename)
            if i.filename.startswith(("/", "\\")) or ":" in i.filename or ".." in parts or "\0" in i.filename \
                    or (i.external_attr >> 16) & 0o170000 == 0o120000:
                return refuse(f"unsafe member {i.filename[:80]!r}")
        out = None
        try:
            base.mkdir(parents=True, exist_ok=True)
            out = Path(tempfile.mkdtemp(prefix="tree-", dir=base))
            total = 0
            for i in infos:
                rel = [p for p in re.split(r"[\\/]", i.filename) if p and p != "."]
                if not rel or i.is_dir():
                    continue
                dest = out.joinpath(*rel)
                if out.resolve() not in dest.resolve().parents:
                    raise ValueError(f"unsafe member {i.filename[:80]!r}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, open(dest, "wb") as dst:
                    while True:                      # the sizes a zip states can lie: count what really comes out
                        chunk = src.read(1 << 16)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > SNAPSHOT_MAX_BYTES:
                            raise ValueError("larger than the size cap")
                        dst.write(chunk)
        except Exception as e:                       # bad data (zlib, CRC, an unsupported method) or a full disk
            if out is not None:
                shutil.rmtree(out, ignore_errors=True)
            return refuse(str(e)[:120] if isinstance(e, ValueError) else type(e).__name__)
        if (out / "store.json").is_file():
            return out
        tops = [p for p in out.iterdir()]
        if len(tops) == 1 and (tops[0] / "store.json").is_file():     # GitHub's zips hold one top folder
            return tops[0]
        shutil.rmtree(out, ignore_errors=True)
        return refuse("no store.json in it")

    # ------------------------------------------------------------ store-wide checks
    def usable(self):
        """store.json read and this ecadlib new enough for it. Decided once per Store."""
        if self._usable is None:
            self._usable = self._check_meta()
        return self._usable

    def _check_meta(self):
        if not self.enabled or self.kind == "refused":
            return False
        got, meta = self._json("store.json")
        if got == "missing":
            self._note("shared store: no store.json at the store URL; it is not a store, so it is not used")
        if got != "ok":
            return False
        if not isinstance(meta, dict):
            self._note("shared store: store.json is not a store description; the store is not used")
            return False
        from . import __version__
        need = meta.get("min_ecadlib")
        if need and _version(need) > _version(__version__):
            self._note(f"shared store needs ecadlib {need} or newer (this is {__version__}); update ecadlib to use it")
            return False
        if isinstance(meta.get("schema"), int) and meta["schema"] > SK.SCHEMA:
            self._note(f"shared store is in a newer format (schema {meta['schema']}); update ecadlib to use it")
            return False
        self.meta = meta
        return True

    def trusted(self):
        """store.json "trusted": the contributor names whose evidence counts (None: everyone's); a list always counts
        the maintainers' too, as the store's own index does (store_repo._people), so both compute the same level."""
        t = self.meta.get("trusted")
        if not isinstance(t, list):
            return None
        return list(dict.fromkeys([x for x in t if isinstance(x, str)] + self.maintainers()))

    def maintainers(self):
        m = self.meta.get("maintainers")
        return [x for x in m if isinstance(x, str)] if isinstance(m, list) else []

    def withdrawn(self):
        """key_id -> its withdrawal entry (the highest rev withdrawn); {} when there is none, the store is off or its
        list cannot be read (withdrawals() tells those apart)."""
        if not self.usable():
            return {}
        return self._withdrawn() or {}

    def withdrawals(self):
        """(key_id -> withdrawal entry, None) when the list could be read; (None, why) when it cannot be told: 'off'
        (no store, or accept "off"), 'down' (the store could not be read and no copy of its list is saved on this
        computer), 'stale' (only a copy older than HOLD_MAX_AGE_S is: self.withdrawn_age says how old) or 'unknown' (the
        store answered, but its withdrawn.json is missing or unreadable). A copy saved on this computer earlier and
        younger than HOLD_MAX_AGE_S is used when the store cannot be read (one trouble line says so). A list that could
        not be read is never taken for "nothing withdrawn"."""
        self.withdrawn_age = None
        if not self.enabled or self.accept == "off":
            return None, "off"
        if not self.usable():
            return None, "down"
        wd = self._withdrawn()
        if wd is None:
            return None, "down" if self._wd_state == "down" else "unknown"
        age = self.age("withdrawn.json")
        self.withdrawn_age = age
        if age > HOLD_MAX_AGE_S:
            return None, "stale"
        if ("withdrawn.json" in self._old or self._snap_old) and not self._wd_old_said:
            self._wd_old_said = True
            self._note(f"shared store could not be read; withdrawals were checked against the list saved on this "
                       f"computer {age / 86400:.0f} day(s) ago")
        return wd, None

    def _withdrawn(self):
        """As withdrawn(), but None when the list could not be read (a lookup then refuses: it cannot tell)."""
        if "_withdrawn" in self._mem:
            return self._mem["_withdrawn"]
        got, lst = self._json("withdrawn.json")
        out = None
        if got == "error":
            self._wd_state = "down" if lst in ("down", "offline") else "bad"
        elif got == "missing":               # store_repo always writes one: a store without it cannot say what it took
            self._wd_state = "missing"       # back, so none of its records is used and parts built from them are held
            self._note("shared store: it has no withdrawn.json, so it cannot be told which records were withdrawn; "
                       "its parts are not used")
        elif not isinstance(lst, list):
            self._wd_state = "bad"
            self._note("shared store: withdrawn.json is not a list; store parts are not used")
        else:
            self._wd_state, out = "ok", {}
            for w in lst:
                if isinstance(w, dict) and isinstance(w.get("key_id"), str):
                    old = out.get(w["key_id"])
                    if old is None or self._wrev(w) > self._wrev(old):
                        out[w["key_id"]] = w
        self._mem["_withdrawn"] = out
        return out

    @staticmethod
    def _wrev(w):
        r = _rev(w.get("rev"))
        return float("inf") if r is None else r          # a withdrawal without a rev covers every rev

    def is_withdrawn(self, key_id, rev):
        """The withdrawal entry when key_id is withdrawn at rev or a later rev, else None."""
        w = self.withdrawn().get(str(key_id))
        return w if w is not None and (_rev(rev) is None or self._wrev(w) >= rev) else None

    def _shard(self, ab):
        """(state, {key_id: entry}) of one index shard: state 'ok' / 'missing' (no such shard: no part in it) / 'down' /
        'bad'. An entry whose key id is not its key's (store_keys.key_id) or whose `also` is not a list of codes is
        left out, with one trouble line."""
        if not re.fullmatch(r"[0-9a-f]{2}", ab or ""):
            return "bad", {}
        got, idx = self._json(f"index/{ab}.json")
        if got == "missing":
            return "missing", {}
        if got == "error":
            return ("down" if idx in ("down", "offline") else "bad"), {}
        if not isinstance(idx, dict):
            self._note(f"shared store: index/{ab}.json is not an index; ignored")
            return "bad", {}
        out, bad = {}, 0
        for k, e in idx.items():
            if not (isinstance(e, dict) and isinstance(e.get("key"), str)):
                continue
            also = e.get("also", [])
            if not (isinstance(k, str) and KID.fullmatch(k) and k == SK.key_id(e["key"])) or not (
                    isinstance(also, list) and all(isinstance(a, str) for a in also)):
                bad += 1
                continue
            out[k] = e
        if bad:
            self._note(f"shared store: index/{ab}.json has {bad} entr{'y' if bad == 1 else 'ies'} that do not name "
                       "their part; ignored")
        return "ok", out

    def _index(self, ab):
        return self._shard(ab)[1]

    def entry(self, key_id, mpn_shard):
        """A part's current index entry (level, status, rev, sha256) or None. mpn_shard: the shard ('3f'), an MPN or a
        key."""
        return self.entry_state(key_id, mpn_shard)[1]

    def entry_state(self, key_id, mpn_shard):
        """(state, entry): state 'ok' (entry: the part's index entry, or None when the store does not list it) or, with
        entry None, 'down' (nothing could be read) / 'bad' (the store's index shard is unreadable)."""
        if not self.usable():
            return "down", None
        s = str(mpn_shard or "")
        state, idx = self._shard(s if re.fullmatch(r"[0-9a-f]{2}", s) else SK.shard(s))
        if state in ("down", "bad"):
            return state, None
        return "ok", idx.get(str(key_id))

    def status(self, key_id, mpn_shard):
        e = self.entry(key_id, mpn_shard)
        return e.get("status") if e else None

    # ------------------------------------------------------------ lookup
    def lookup(self, maker, mpn):
        """Hit for this part or None. Never raises: trouble goes to self.trouble, once per distinct problem."""
        try:
            return self._lookup(maker, mpn)
        except Exception as e:                                       # a bug here must not stop a BOM run
            self._note(f"shared store lookup failed ({type(e).__name__}: {str(e)[:120]}); parts are built without it")
            return None

    def _find(self, k, idx):
        """(key_id, entry, note, via) for key k in one index shard, or None."""
        exact = [(i, e) for i, e in idx.items() if e["key"] == k]
        if exact:
            return exact[0] + ("", "exact")
        also = [(i, e) for i, e in idx.items() if k in (e.get("also") or [])]
        if len(also) == 1:
            return also[0] + (f"{SK.mpn_of(k)} is one of the codes the store record {also[0][1]['key']} covers", "also")
        return None

    def _find_no_maker(self, k, idx):
        mpn = SK.mpn_of(k)
        exact, via = [], []
        for i, e in idx.items():
            if SK.mpn_of(e["key"]) == mpn:
                exact.append((i, e))
            elif any(SK.mpn_of(a) == mpn for a in e.get("also") or [] if isinstance(a, str)):
                via.append((i, e))
        makers = {SK.maker_of(e["key"]) for _, e in exact + via}
        if len(makers) != 1:
            return None                                              # nobody, or two makers: no guess
        pick = exact or via
        if len(pick) != 1:
            return None
        i, e = pick[0]
        return i, e, (f"matched {mpn} without a maker: only {SK.maker_of(e['key'])} has it in the shared store"
                      + ("" if exact else f" (as a code the record {e['key']} covers)")), "no-maker"

    def _lookup(self, maker, mpn):
        if not self.enabled or self.accept == "off":
            return None
        k = SK.key(maker, mpn)
        if not k or not self.usable():
            return None
        ab = SK.shard(k)
        idx = self._index(ab)
        found = self._find_no_maker(k, idx) if k.startswith("|") else self._find(k, idx)
        if found is None:
            return None
        kid, e, note, via = found
        rev, sha = _rev(e.get("rev")), e.get("sha256")
        if rev is None or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            self._note(f"shared store: index entry {kid} is incomplete; ignored")
            return None
        wd = self._withdrawn()
        if wd is None:
            return None
        if self.is_withdrawn(kid, rev):
            return None
        old = self.age(f"index/{ab}.json", "withdrawn.json")
        if old > MAX_AGE_S:                          # a withdrawal since then would not be seen: never built from
            if not self._stale_said:
                self._stale_said = True
                self._note(f"shared store: the copy saved on this computer is {old / 86400:.0f} days old (more than "
                           f"{MAX_AGE_S // 86400}) and no newer one could be read; its parts are not used until it can")
            return None
        rel = f"parts/{SK.shard(e['key'])}/{kid}.json"
        got, text = self._raw(rel, sha)
        if got == "missing":
            self._note(f"shared store: record {kid} is listed in the index but missing; not used")
            return None
        if got != "ok":
            return None
        try:
            rec = json.loads(text)
        except Exception:
            rec = None
        if not isinstance(rec, dict) or SK.digest(rec) != sha:
            self._note(f"shared store: record {kid} does not match its hash in the index; refused")
            return None
        schema = rec.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool):
            self._note(f"shared store: record {kid} has no schema number; refused")
            return None
        if schema > SK.SCHEMA:
            self._note(f"shared store has parts in a newer format (schema {schema}); update ecadlib to use them")
            return None
        rk = rec.get("key") if isinstance(rec.get("key"), dict) else {}
        status = str(e.get("status") or "active")
        if rk.get("key") != e["key"] or _rev(rec.get("rev")) != rev or rec.get("status") != status:
            self._note(f"shared store: record {kid} is not what its index entry says (key, rev or status); refused")
            return None
        try:
            own = SR.level(rec, trusted=self.trusted(), maintainers=self.maintainers())
        except Exception:                            # a record level() cannot read: no level, so never used
            own = ""
        level = lower(str(e.get("level") or ""), own)
        return Hit(rec, e["key"], kid, rev, level, status, sha, note, via)

    def level_now(self, kid, e):
        """The level a lookup would give this index entry now: the lower of the index's and the record's own under
        today's store.json (trusted, maintainers), as lookup works it out; "" when the record cannot be read."""
        try:
            sha = e.get("sha256")
            got, text = self._raw(f"parts/{SK.shard(e['key'])}/{kid}.json", sha)
            rec = json.loads(text) if got == "ok" else None
            if not isinstance(rec, dict) or SK.digest(rec) != sha:
                return ""
            return lower(str(e.get("level") or ""), SR.level(rec, trusted=self.trusted(), maintainers=self.maintainers()))
        except Exception:
            return ""

    # ------------------------------------------------------------ parts that depend on the store
    def problems(self, item, accept=None):
        """problems(item, self, accept): see the module function."""
        return problems(item, self, accept)


def _level_ok(level, accept):
    """A record at this index level may still stand behind a part built from it under [store] accept."""
    return level in LEVELS and (accept == "checked" or level == "confirmed")


def depends(item):
    """What a plan item (or a librarian state record) owes the shared store: [{"kind": "built", "key_id", "rev", "key",
    "halves"}] for halves built from a record (source store:<key_id>@r<rev>), and {"kind": "lifted", ..., "lifted":
    [holds]} for a part whose own build a record's agreement released (store_check, Run.second_opinion), unless those
    holds are back already (store_check "revoked")."""
    out = {}
    item = item if isinstance(item, dict) else {}
    src = item.get("source") if isinstance(item.get("source"), dict) else None
    keys = (item.get("store") or {}).get("keys") or {} if isinstance(item.get("store"), dict) else {}
    for h in ("symbol", "footprint"):
        half = item.get(h) if isinstance(item.get(h), dict) else {}
        p = parse_source(src.get(h) if src is not None else half.get("source"))
        if p:
            d = out.setdefault(("built",) + p, {"kind": "built", "key_id": p[0], "rev": p[1], "key": "", "halves": []})
            d["halves"].append(h)
            d["key"] = d["key"] or str(half.get("store_key") or keys.get(p[0]) or "")
    sc = item.get("store_check")
    if isinstance(sc, dict) and sc.get("result") == "agreed" and sc.get("lifted") and not sc.get("revoked") \
            and isinstance(sc.get("key_id"), str) and _rev(sc.get("rev")) is not None:
        out[("lifted", sc["key_id"], sc["rev"])] = {"kind": "lifted", "key_id": sc["key_id"], "rev": sc["rev"],
                                                    "key": str(sc.get("key") or ""), "lifted": list(sc["lifted"])}
    return list(out.values())


def note_for(dep, why, redo=""):
    """The `verify` note for one of problems()'s answers: a part built from the record, or a part whose lifted holds
    are back (redo: what to do, for a part built from it). A note that the store could not vouch for the part at all
    (dep "unchecked": switched off, unreadable) is the same text wherever it is made, so a person's --allow keeps
    covering it run after run."""
    if dep.get("unchecked"):
        return why if why.startswith("this part ") else (
            f"{why}: this part's own build was released because that record agreed with it; the holds it lifted are "
            "back" if dep.get("kind") == "lifted" else f"{why}: this part was built from it")
    if dep.get("kind") == "lifted":
        return f"{why}: this part's own build was released because that record agreed with it; the holds it lifted " \
               "are back"
    return f"{why}: this part was built from it" + (f"; {redo}" if redo else "")


def _unchecked(deps, why):
    return [(dict(d, unchecked=True), why(d)) for d in deps]


def problems(item, store, accept=None):
    """[(dependency, why)] for what a part owes the shared store (depends) that does not hold now. [] when the part
    owes the store nothing (whatever the store's settings: a store that is off changes nothing for other parts).
    For a part that does owe it, the store must vouch for it, or the part is held:
      the record was withdrawn at that rev, is disputed, is no longer in the store's index (or, for an agreement, no
      longer at that rev), or its level is now below [store] accept (a part built from it);
      the store cannot vouch ("unchecked" dependencies; note_for words them the same every time): it is switched off
      (store None or not enabled, [store] url empty or accept "off": OFF / OFF_LIFTED), its withdrawn list cannot be
      read and no copy younger than HOLD_MAX_AGE_S is saved on this computer, or the store answers but its list or
      index is missing or unreadable. A list that could not be read is never taken for "nothing withdrawn", and
      anything that goes wrong while checking holds the part (never raises).
    why is text for a `verify` note: 'shared store record KEY rN ...'."""
    deps = depends(item)
    if not deps:
        return []
    try:
        return _problems(deps, store, accept)
    except Exception as e:                            # a broken store file or a bug here: held, never a crash
        name = type(e).__name__                       # `e` is cleared when the except block ends
        return _unchecked(deps, lambda d: safe(f"shared store record {d['key'] or d['key_id']} r{d['rev']}: "
                                               f"withdrawals could not be checked ({name})"))


def _problems(deps, store, accept):
    if store is None or not getattr(store, "enabled", False) or getattr(store, "accept", "confirmed") == "off" \
            or accept == "off":
        return _unchecked(deps, lambda d: OFF_LIFTED if d["kind"] == "lifted" else OFF)
    accept = accept or getattr(store, "accept", "confirmed")
    wd, why = store.withdrawals()
    lab = lambda d: safe(f"{d['key'] or d['key_id']} r{d['rev']}")
    if wd is None:
        days = HOLD_MAX_AGE_S // 86400
        text = {"off": None,
                "stale": f"withdrawals could not be checked for more than {days} days (the store could not be read, "
                         f"and the copy of its withdrawn list saved on this computer is older than that)",
                "unknown": "cannot tell whether the store withdrew it (its withdrawn list is missing or unreadable)"
                }.get(why, "withdrawals could not be checked (the store could not be read, and no copy of its "
                           f"withdrawn list younger than {days} days is saved on this computer)")
        if text is None:
            return _unchecked(deps, lambda d: OFF_LIFTED if d["kind"] == "lifted" else OFF)
        return _unchecked(deps, lambda d: f"shared store record {lab(d)}: {text}")
    out = []
    for d in deps:
        label = lab(d)
        w = wd.get(d["key_id"])
        if w is not None and store._wrev(w) >= d["rev"]:
            reason = "; ".join(str(x) for x in (w.get("reason"), w.get("issue")) if x) or "no reason given"
            out.append((d, safe(f"shared store record {label} was withdrawn from the store ({reason[:200]})")))
            continue
        if not d["key"]:
            continue                                  # (no key: its index shard is unknown; withdrawals were checked)
        state, e = store.entry_state(d["key_id"], d["key"])
        if state == "down":
            continue                                  # (withdrawals were checked; a dispute is seen once it is read)
        if state == "bad":
            out.append((dict(d, unchecked=True), f"shared store record {label}: cannot tell whether the store still "
                                                    "lists it (its index cannot be read)"))
        elif e is None:
            out.append((d, f"shared store record {label} is no longer in the store's index"))
        elif str(e.get("status") or "active") == "disputed":
            out.append((d, f"shared store record {label} is disputed"))
        elif d["kind"] == "lifted" and _rev(e.get("rev")) != d["rev"]:
            out.append((d, f"shared store record {label} is no longer the store's (it is at r{e.get('rev')} now)"))
        elif d["kind"] == "lifted" and (lv := (store.level_now(d["key_id"], e) if hasattr(store, "level_now")
                                               else str(e.get("level") or ""))) != "confirmed":
            # review 2026-11-25 (r5 M1): a hold lifted by a confirmed record is lifted no longer once the record is
            # only checked (the maintainers narrowed who may confirm), read as a lookup reads it (index and record)
            out.append((d, f"shared store record {label} is now only {safe(lv or 'unrated')}, and only a confirmed "
                           "record lifts a hold"))
        elif d["kind"] == "built" and accept in ACCEPT and accept != "off" and \
                LEVELS.index(str(e.get("level"))) < LEVELS.index("confirmed" if accept == "confirmed" else "checked") \
                if str(e.get("level")) in LEVELS else d["kind"] == "built":
            out.append((d, f"shared store record {label} is now only {safe(e.get('level') or 'unrated')} (below "
                           f"[store] accept = \"{accept}\")"))
    return out


def from_rules(rules, http=None, cache_dir=None, offline=False):
    """The Store a run's rules ask for: [store] url, accept ("confirmed" | "checked" | "off"), mode ("online" |
    "snapshot"), snapshot (the zip's URL; default: default_snapshot_url). No [store] section, no url or accept "off":
    a Store that is off and contacts nothing."""
    s = (rules or {}).get("store") if isinstance(rules, dict) else None
    s = s if isinstance(s, dict) else {}
    accept = str(s.get("accept") or "confirmed").strip().lower()
    url = "" if accept == "off" else str(s.get("url") or "")
    mode = str(s.get("mode") or "online").strip().lower()
    return Store(url, http=http, cache_dir=cache_dir, offline=offline, mode=mode,
                 snapshot_url=s.get("snapshot") or None, accept=accept)
