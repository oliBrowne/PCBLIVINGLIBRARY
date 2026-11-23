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

Privacy: an online lookup asks only for those four kinds of file, so the host learns a shard (1/256 of all parts) and a
key id, never a maker or a part number. mode="snapshot" downloads the whole store as one zip (at most once per
SNAPSHOT_TTL_S), unpacks it safely and answers every lookup from that folder. offline=True reads the cache only.

Caching (online mode, under cache_dir): store.json, the index shards and withdrawn.json are asked again after TTL_S;
an older copy is used when the network fails. A record is immutable for its sha256, so it is kept for good by that
hash. The first network failure of a Store stops it asking again for the rest of the run (cache only), so a BOM of
500 lines waits for one timeout, not 500, and the log says "could not be reached" once.

A record is an opaque JSON dict here: only schema, rev, status, key, also and evidence are ever read
(store_record.py owns its meaning)."""
import hashlib, io, json, os, re, shutil, tempfile, time, zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from . import store_keys as SK

TTL_S = 6 * 3600                    # store.json, index shards, withdrawn.json: asked again after this long
SNAPSHOT_TTL_S = 86400              # snapshot mode: the zip is downloaded again after this long
SNAPSHOT_MAX_BYTES = 1024 * 2 ** 20  # unpacked size of a snapshot, and the download's size
SNAPSHOT_MAX_MEMBERS = 500_000
ACCEPT = ("confirmed", "checked", "off")
SOURCE = re.compile(r"store:([0-9a-f]{32})@r(\d+)")


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

    @property
    def source(self):
        """The source line a part built from this record carries: store:<key_id>@r<rev>."""
        return f"store:{self.key_id}@r{self.rev}"


def parse_source(s):
    """'store:<key_id>@r<rev>' -> (key_id, rev); None for any other source."""
    m = SOURCE.fullmatch(str(s or "").strip())
    return (m[1], int(m[2])) if m else None


def decide(hit, accept="confirmed"):
    """What a run does with a hit: 'use' (build from it), 'second-opinion' (keep it to compare with the local build)
    or 'skip'. A disputed record is only ever a second opinion; a checked one is used only with accept='checked'."""
    if hit is None or accept not in ACCEPT or accept == "off" or hit.status not in ("active", "disputed") \
            or hit.level not in ("confirmed", "checked"):
        return "skip"
    if hit.status == "disputed":
        return "second-opinion"
    return "use" if hit.level == "confirmed" or accept == "checked" else "second-opinion"


def _version(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _rev(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class Store:
    def __init__(self, url, http=None, cache_dir=None, offline=False, mode="online", now=time.time, ttl_s=TTL_S,
                 snapshot_url=None, snapshot_ttl_s=SNAPSHOT_TTL_S, accept="confirmed"):
        self.url, self.http, self.offline, self.mode, self.now = str(url or "").strip().rstrip("/"), http, offline, \
            mode, now
        self.ttl, self.snapshot_ttl, self.accept = ttl_s, snapshot_ttl_s, accept
        self.snapshot_url = snapshot_url or (self.url + "/snapshot.zip" if self.url else "")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.trouble = []
        self._mem, self._usable, self._down, self._root, self._tmp = {}, None, False, None, None
        low = self.url.lower()
        self.remote = low.startswith(("https://", "http://"))
        if not self.url:
            self.kind = "off"
        elif self.remote:
            self.kind = "snapshot" if mode == "snapshot" else "http"
        else:
            self.kind = "folder"
            self._root = Path(url2pathname(urlparse(self.url).path)) if low.startswith("file:") else Path(self.url)

    @property
    def enabled(self):
        return self.kind != "off"

    def _note(self, msg):
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

    def _fetch(self, rel):
        """GET base/rel -> ('ok', text) | ('missing', None) | ('error', message). Never raises."""
        if self.offline:
            return "error", "offline"
        if self._down:
            return "error", "down"
        try:
            r = self._http().get(f"{self.url}/{rel}", cache=False)
            status, text, err = getattr(r, "status", 0), getattr(r, "text", ""), getattr(r, "error", None)
        except Exception as e:                       # a fake or a future client that raises: still a miss
            status, text, err = 0, "", f"{type(e).__name__}: {e}"
        if status == 404:
            return "missing", None
        if err is None and 200 <= status < 300 and isinstance(text, str):
            return "ok", text
        self._down = True
        self._note(f"shared store could not be reached ({str(err or f'HTTP {status}')[:120]}); parts are built "
                   "without it this run")
        return "error", "down"

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
        """Text of a store file -> ('ok', text) | ('missing', None) | ('error', None). sha: a record, cached for good
        by its hash; otherwise store.json / an index shard / withdrawn.json, cached for self.ttl."""
        if self.kind in ("folder", "snapshot"):
            root = self._folder()
            if root is None:
                return "error", None
            f = root / rel
            try:
                return ("ok", f.read_text(encoding="utf-8")) if f.is_file() else ("missing", None)
            except (OSError, UnicodeDecodeError) as e:
                self._note(f"shared store: cannot read {rel} ({type(e).__name__})")
                return "error", None
        cache = self._cache()
        if sha:
            f = cache / "records" / f"{sha}.json"
            try:
                text = f.read_text(encoding="utf-8")
                if self._digest_ok(text, sha):
                    return "ok", text
            except OSError:
                pass
            got, text = self._fetch(rel)
            if got == "ok" and self._digest_ok(text, sha):
                self._write(f, text)
            elif got == "error" and text != "down":
                self._note("shared store: offline and the record is not cached; parts are built without it")
            return (got, text) if got != "error" else ("error", None)
        f = cache / "live" / rel
        old = self._read_cached(f)
        if old is not None and (self.offline or self._down or 0 <= self.now() - old["at"] < self.ttl):
            return ("ok", old["text"]) if isinstance(old.get("text"), str) else ("missing", None)
        got, text = self._fetch(rel)
        if got == "ok" or got == "missing":
            self._write(f, json.dumps({"at": self.now(), "text": text}))
            return got, text
        if old is not None:                          # the network failed: an old copy beats none
            return ("ok", old["text"]) if isinstance(old.get("text"), str) else ("missing", None)
        if text == "offline":
            self._note("shared store: offline and nothing of it is cached yet; parts are built without it")
        return "error", None

    @staticmethod
    def _digest_ok(text, sha):
        try:
            return SK.digest(json.loads(text)) == sha
        except ValueError:
            return False

    def _json(self, rel):
        """A live store file (store.json, an index shard, withdrawn.json) -> ('ok', parsed) | ('missing', None) |
        ('error', None); not JSON is an error, with a trouble line. Within one Store each file is read once."""
        if rel not in self._mem:
            got, text = self._raw(rel)
            d = None
            if got == "ok":
                try:
                    d = json.loads(text)
                except ValueError:
                    self._note(f"shared store: {rel} is not valid JSON; ignored")
                    got = "error"
            self._mem[rel] = (got, d)
        return self._mem[rel]

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
            self._root = tree
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
                r = http.s.get(url, timeout=max(120, getattr(http, "timeout", 0) or 0), stream=True)
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
        if not self.enabled:
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
        return True

    def withdrawn(self):
        """key_id -> its withdrawal entry (the highest rev withdrawn); {} when there is none or the store is off."""
        if not self.usable():
            return {}
        return self._withdrawn() or {}

    def _withdrawn(self):
        """As withdrawn(), but None when the list could not be read (a lookup then refuses: it cannot tell)."""
        if "_withdrawn" in self._mem:
            return self._mem["_withdrawn"]
        got, lst = self._json("withdrawn.json")
        if got == "error":
            out = None
        elif got == "missing":
            out = {}
        elif not isinstance(lst, list):
            self._note("shared store: withdrawn.json is not a list; store parts are not used")
            out = None
        else:
            out = {}
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

    def _index(self, ab):
        idx = self._json(f"index/{ab}.json")[1] if re.fullmatch(r"[0-9a-f]{2}", ab or "") else None
        return {k: e for k, e in idx.items() if isinstance(e, dict) and isinstance(e.get("key"), str)} \
            if isinstance(idx, dict) else {}

    def entry(self, key_id, mpn_shard):
        """A part's current index entry (level, status, rev, sha256) or None. mpn_shard: the shard ('3f'), an MPN or a
        key."""
        if not self.usable():
            return None
        s = str(mpn_shard or "")
        return self._index(s if re.fullmatch(r"[0-9a-f]{2}", s) else SK.shard(s)).get(str(key_id))

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
        """(key_id, entry, note) for key k in one index shard, or None."""
        exact = [(i, e) for i, e in idx.items() if e["key"] == k]
        if exact:
            return exact[0] + ("",)
        also = [(i, e) for i, e in idx.items() if k in (e.get("also") or [])]
        if len(also) == 1:
            return also[0] + (f"{SK.mpn_of(k)} is one of the codes the store record {also[0][1]['key']} covers",)
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
                      + ("" if exact else f" (as a code the record {e['key']} covers)"))

    def _lookup(self, maker, mpn):
        if not self.enabled or self.accept == "off":
            return None
        k = SK.key(maker, mpn)
        if not k or not self.usable():
            return None
        idx = self._index(SK.shard(k))
        found = self._find_no_maker(k, idx) if k.startswith("|") else self._find(k, idx)
        if found is None:
            return None
        kid, e, note = found
        rev, sha = _rev(e.get("rev")), e.get("sha256")
        if rev is None or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            self._note(f"shared store: index entry {kid} is incomplete; ignored")
            return None
        wd = self._withdrawn()
        if wd is None:
            return None
        if self.is_withdrawn(kid, rev):
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
        except ValueError:
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
        return Hit(rec, e["key"], kid, rev, str(e.get("level") or ""), str(e.get("status") or "active"), sha, note)


def from_rules(rules, http=None, cache_dir=None, offline=False):
    """The Store a run's rules ask for: [store] url, accept ("confirmed" | "checked" | "off"), mode ("online" |
    "snapshot"), snapshot (the zip's URL; default <url>/snapshot.zip). No [store] section, no url or accept "off":
    a Store that is off and contacts nothing."""
    s = (rules or {}).get("store") if isinstance(rules, dict) else None
    s = s if isinstance(s, dict) else {}
    accept = str(s.get("accept") or "confirmed").strip().lower()
    url = "" if accept == "off" else str(s.get("url") or "")
    mode = str(s.get("mode") or "online").strip().lower()
    return Store(url, http=http, cache_dir=cache_dir, offline=offline, mode=mode,
                 snapshot_url=s.get("snapshot") or None, accept=accept)
