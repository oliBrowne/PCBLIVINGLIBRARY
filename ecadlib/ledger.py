"""The part ledger: append-only release history of every part in a library folder, and the files of each revision.

    <library>/library.ledger.jsonl        one JSON line per event, git-friendly (sorted keys, never rewritten)
    <library>/.ecadlib/blobs/ab/<sha256>  the exact bytes of every released file and fingerprint (content-addressed)
    <library>/.ecadlib/proposed/<part>.json   a held change: the proposed version waiting for approval
    <library>/library.usage.json          which projects / BOMs use which part revision (ecadlib project scan)

The library folder is the folder of the plan.json (the librarian's -o, an altium365 plan's folder), or of a
kicad_import manifest.json when there is no plan. A line of the ledger:

    {"schema_version": 1, "event": "release", "part": "AO3401A", "revision": 2, "base": 1, "at": "2026-10-18T22:40:00+00:00",
     "writer": "librarian", "by": "Ann <ann@x.com>", "decision": "approved", "approved_by": ["Ann <ann@x.com>"],
     "reason": "pad 3 moved to the datasheet's position", "eid": ..., "gid": ..., "mid": ..., "cid": ..., "digest": ...,
     "fingerprint": <blob sha>, "files": {"symbol": {"path": "models/SchLib/AO3401A.SchLib", "sha256": ...}, ...},
     "item": {<the plan item's content fields>}, "changes": [{"class": "geometry-major", "text": "pad 3 moved ..."}],
     "classes": {"geometry-major": 1}, "source": {"symbol": "kicad:...", "footprint": "...", "ecadlib": "0.4.0"}}

Events: `release` (a new revision: the first one is the baseline, later ones passed the gate or were approved),
`revert` (a new revision that copies an older one), `hold` (a change was held; the proposal is in .ecadlib/proposed),
`reject` (a person rejected a held change; the same proposal is held again without asking), `publish` (a revision went
to a target: altium365, dblib, kicad), `approve` (one approval of a held change, for the two-person rule), `refused`
(an approval not applied because the proposed version fails the release check; it is not counted as an approval),
`overwritten` (an approval replaced a newer edit on disk; those bytes are kept in the blob store), `retired` (a released
part left the plan) and `adopted` decisions (`ecadlib part adopt`: a person took the files on disk as a part's release
after its history was lost). The head of a part is its last release or revert. Nothing is ever deleted: `ecadlib part
history` shows every line.

Writers hold the library lock (.ecadlib/lock: lock() / locked()) from the gate's begin() through settle(), and every
step that records (settle, baseline, publish lines, approve / reject / revert) takes it and reads the ledger again, so
two runs on one library never record the same revision twice or settle one another's half-written files. It is an OS lock (fcntl on
POSIX, msvcrt on Windows), so the OS frees it when the holder dies: a crashed run never leaves a stale lock."""
import contextlib, datetime, hashlib, json, os, re, threading, time
from pathlib import Path

from .fsutil import write_text_atomic

LEDGER = "library.ledger.jsonl"
USAGE = "library.usage.json"
STORE = ".ecadlib"
SCHEMA_VERSION = 1
RELEASES = ("release", "revert")
# plan item fields the fingerprint covers; a held change restores them, an approval applies them
CONTENT_FIELDS = ("parameters", "cleared", "description", "comment", "symbol", "footprint")
HALF_KEYS = ("ref", "file", "kicad_mod")


class LedgerError(ValueError):
    """A library's change-control records cannot be used as they are: a broken ledger line, a ledger that lost a part's
    history, another run holding the library. The CLI reports it as a plain message with its hint, not a crash."""
    plain_error = True
    hint = "see docs/change-control.md, section \"When the ledger is damaged\""

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        if hint:
            self.hint = hint


# ---------------------------------------------------------------- the library lock
LOCK_WAIT = 600                                # seconds a writer waits for another run (env ECADLIB_LOCK_WAIT)
_LOCKS, _LOCKS_GUARD = {}, threading.Lock()    # lock path -> {rlock, n, f}: re-entrant within a process


def _os_trylock(f):
    try:
        import fcntl
    except ImportError:                        # Windows: lock byte 0 (the file position decides which byte)
        import msvcrt
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(f):
    try:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)
    except ImportError:
        import msvcrt
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    except OSError:
        pass


def lock(folder, wait=None):
    """Take the library lock of `folder` (re-entrant in one thread; other threads and processes wait). -> a token for
    unlock(). Raises LedgerError after `wait` seconds (default $ECADLIB_LOCK_WAIT or LOCK_WAIT)."""
    if wait is None:
        try:
            wait = float(os.environ.get("ECADLIB_LOCK_WAIT") or LOCK_WAIT)
        except ValueError:
            wait = LOCK_WAIT
    store = Path(folder) / STORE
    store.mkdir(parents=True, exist_ok=True)
    key = str((store / "lock").resolve())
    with _LOCKS_GUARD:
        ent = _LOCKS.setdefault(key, {"rlock": threading.RLock(), "n": 0, "f": None})
    busy = LedgerError(f"{folder}: another ecadlib run is changing this library (waited {wait:g} s for {STORE}/lock)",
                       "wait for it to finish and run again (ECADLIB_LOCK_WAIT sets how long to wait)")
    deadline = time.monotonic() + wait
    if not ent["rlock"].acquire(timeout=max(wait, 0)):
        raise busy
    try:
        if ent["n"] == 0:
            f = open(key, "a+b")
            while not _os_trylock(f):
                if time.monotonic() >= deadline:
                    f.close()
                    raise busy
                time.sleep(0.05)
            ent["f"] = f
        ent["n"] += 1
    except BaseException:
        ent["rlock"].release()
        raise
    return key


def unlock(key):
    ent = _LOCKS.get(key)
    if not ent or ent["n"] <= 0:
        return
    ent["n"] -= 1
    if ent["n"] == 0 and ent["f"] is not None:
        _os_unlock(ent["f"])
        ent["f"].close()
        ent["f"] = None
    ent["rlock"].release()


@contextlib.contextmanager
def locked(folder, wait=None):
    key = lock(folder, wait)
    try:
        yield key
    finally:
        unlock(key)


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def safe_name(part):
    s = re.sub(r"[^A-Za-z0-9._+-]", "_", str(part))[:120] or "_"
    return s if s == str(part) else f"{s}-{sha_bytes(str(part).encode())[:8]}"


def content_of(item):
    """The plan item's fields the fingerprint covers (a deep copy)."""
    out = {}
    for k in CONTENT_FIELDS:
        v = item.get(k)
        if k in ("symbol", "footprint"):
            v = {h: (v or {}).get(h) for h in HALF_KEYS if (v or {}).get(h) is not None}
        out[k] = json.loads(json.dumps(v)) if v is not None else None
    return out


def apply_content(item, content):
    """Put content fields back into a plan item (restoring a head, applying an approved proposal)."""
    for k in CONTENT_FIELDS:
        v = content.get(k)
        if k in ("symbol", "footprint"):
            half = dict(item.get(k) or {})
            for h in HALF_KEYS:
                if (v or {}).get(h) is not None:
                    half[h] = v[h]
                elif h == "kicad_mod":
                    half.pop(h, None)
                else:                          # plan items always carry ref and file (None when missing)
                    half[h] = None
            item[k] = half
        elif v is None:
            item.pop(k, None)
        else:
            item[k] = json.loads(json.dumps(v))


def item_files(item):
    """{kind: relative path} of the model files a plan item releases (symbol, footprint, kicad_mod)."""
    out = {}
    for kind, key in (("symbol", "file"), ("footprint", "file"), ("kicad_mod", "kicad_mod")):
        half = item.get("footprint" if kind == "kicad_mod" else kind) or {}
        if half.get(key):
            out[kind] = half[key]
    return out


def step_files(lib_path):
    """The STEP files the .ecadbuild recipe beside a PcbLib names (ecadlib_3d/<footprint>_<n>/<model>, relative to the
    library's folder); [] without a recipe."""
    from .altium_native import recipe_steps
    try:
        return [s for s in recipe_steps(lib_path) if ".." not in s.replace("\\", "/").split("/") and not Path(s).is_absolute()]
    except (OSError, UnicodeDecodeError):
        return []


class Ledger:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / LEDGER
        self.store = self.folder / STORE
        self._lines = None

    # ------------------------------------------------------------ lines
    def lines(self):
        if self._lines is None:
            self._lines = []
            if self.path.is_file():
                for n, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                    if not raw.strip():
                        continue
                    try:
                        e = json.loads(raw)
                    except ValueError as ex:
                        raise LedgerError(f"{self.path}:{n}: a broken ledger line ({ex}); nothing was changed",
                                          f"the ledger is append-only: put that line back as it was (`git diff {LEDGER}` "
                                          f"in the library folder shows it), then run again") from None
                    if int(e.get("schema_version") or 1) > SCHEMA_VERSION:
                        from . import migrate as MG, __version__
                        raise MG.SchemaTooNew(f"{self.path} was made by a newer ecadlib (ledger schema "
                                              f"{e.get('schema_version')}; this ecadlib {__version__} reads up to "
                                              f"{SCHEMA_VERSION}): upgrade ecadlib")
                    self._lines.append(e)
        return self._lines

    def reload(self):
        """Read the file again on the next lines() (another run may have appended since: done under the lock)."""
        self._lines = None

    def exists(self):
        return self.path.is_file()

    def append(self, entry):
        e = {"schema_version": SCHEMA_VERSION, "at": now(), **entry}
        self.folder.mkdir(parents=True, exist_ok=True)
        first = not self.path.is_file()
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n")
        if first:                              # the folder's stamp lists the ledger from the start (ecadlib.migrate)
            from . import migrate as MG
            MG.write_stamp(self.folder, ["ledger"])
        if self._lines is not None:
            self._lines.append(e)
        return e

    def history(self, part):
        return [e for e in self.lines() if e.get("part") == part]

    def releases(self, part):
        return [e for e in self.history(part) if e.get("event") in RELEASES]

    def head(self, part):
        r = self.releases(part)
        return r[-1] if r else None

    def revision(self, part, rev):
        """A release / revert line by number ('3', 3, 'r3'), or None."""
        rev = int(str(rev).lstrip("rR"))
        return next((e for e in self.releases(part) if e.get("revision") == rev), None)

    def parts(self):
        return sorted({e["part"] for e in self.lines() if e.get("event") in RELEASES})

    def rejected(self, part, digest):
        """The reject line for this proposed digest after the part's head, or None."""
        head = self.head(part)
        after = False if head else True
        hit = None
        for e in self.history(part):
            if e is head:
                after = True
                continue
            if after and e.get("event") == "reject" and e.get("digest") == digest:
                hit = e
        return hit

    def published(self, part, target):
        """The last publish line of a part to a target ('altium365', 'dblib', 'kicad'), or None."""
        return next((e for e in reversed(self.history(part)) if e.get("event") == "publish" and e.get("target") == target), None)

    # ------------------------------------------------------------ blobs
    def _blob(self, sha):
        return self.store / "blobs" / sha[:2] / sha

    def put_bytes(self, data):
        sha = sha_bytes(data)
        p = self._blob(sha)
        if not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return sha

    def get_bytes(self, sha):
        p = self._blob(sha)
        if not p.is_file():
            raise FileNotFoundError(f"ledger blob {sha[:12]} is missing from {self.store / 'blobs'} (was .ecadlib/ deleted?)")
        return p.read_bytes()

    def put_json(self, obj):
        return self.put_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode())

    def get_json(self, sha):
        return json.loads(self.get_bytes(sha).decode())

    def snapshot_files(self, item, base=None):
        """Copy a plan item's model files (and their .ecadbuild recipes) into the blob store -> files dict."""
        base = Path(base or self.folder)
        out = {}
        for kind, rel in item_files(item).items():
            p = Path(rel) if Path(rel).is_absolute() else base / rel
            if not p.is_file():
                continue
            rec = {"path": rel, "sha256": self.put_bytes(p.read_bytes())}
            side = p.with_name(p.name + ".ecadbuild")
            if side.is_file():
                rec["ecadbuild"] = self.put_bytes(side.read_bytes())
                steps = {s: self.put_bytes((p.parent / s).read_bytes()) for s in step_files(p) if (p.parent / s).is_file()}
                if steps:                      # the 3D models the recipe names: a hold puts them back too
                    rec["steps"] = steps
            out[kind] = rec
        return out

    def restore_files(self, files, base=None):
        """Write the files of a snapshot back where they were. -> [paths written]"""
        base = Path(base or self.folder)
        done = []
        for rec in (files or {}).values():
            p = Path(rec["path"]) if Path(rec["path"]).is_absolute() else base / rec["path"]
            data = self.get_bytes(rec["sha256"])
            if not p.is_file() or p.read_bytes() != data:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
                tmp.write_bytes(data)
                os.replace(tmp, p)
                done.append(str(p))
            if rec.get("ecadbuild"):
                side = p.with_name(p.name + ".ecadbuild")
                sb = self.get_bytes(rec["ecadbuild"])
                if not side.is_file() or side.read_bytes() != sb:
                    side.write_bytes(sb)
            for s, sha in (rec.get("steps") or {}).items():
                f = p.parent / s
                data = self.get_bytes(sha)
                if not f.is_file() or f.read_bytes() != data:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    tmp = f.with_name(f.name + f".{os.getpid()}.tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, f)
                    done.append(str(f))
        return done

    def fingerprint(self, entry):
        return self.get_json(entry["fingerprint"]) if entry and entry.get("fingerprint") else None

    # ------------------------------------------------------------ proposals (held changes)
    def proposal_path(self, part):
        return self.store / "proposed" / f"{safe_name(part)}.json"

    def proposal(self, part):
        p = self.proposal_path(part)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def proposals(self):
        d = self.store / "proposed"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []

    def save_proposal(self, prop):
        p = self.proposal_path(prop["part"])
        p.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(p, json.dumps(prop, indent=1, sort_keys=True, ensure_ascii=False))

    def drop_proposal(self, part):
        p = self.proposal_path(part)
        if p.is_file():
            p.unlink()


# ---------------------------------------------------------------- usage index
def load_usage(folder):
    p = Path(folder) / USAGE
    if not p.is_file():
        return {"schema_version": SCHEMA_VERSION, "projects": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    if int(data.get("schema_version") or 1) > SCHEMA_VERSION:
        from . import migrate as MG
        raise MG.SchemaTooNew(f"{p} was made by a newer ecadlib (usage schema {data.get('schema_version')}): upgrade ecadlib")
    data.setdefault("projects", {})
    return data


def save_usage(folder, data):
    p = Path(folder) / USAGE
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if not p.is_file() or p.read_text(encoding="utf-8") != text:
        write_text_atomic(p, text)


def record_usage(folder, project, parts, tool="bom", note=""):
    """project: a path / name; parts: {part: {"revision": n or None, "designators": [...]}}. Replaces that project's row."""
    data = load_usage(folder)
    old = data["projects"].get(str(project)) or {}
    row = {"tool": tool, "parts": parts, **({"note": note} if note else {})}
    if {k: old.get(k) for k in row} != row or "scanned_at" not in old:    # unchanged: the file stays byte-identical
        data["projects"][str(project)] = dict(row, scanned_at=now())
        save_usage(folder, data)
    return data


def used_in(folder, part):
    """[(project, revision or None, designators)] for a part."""
    out = []
    for proj, row in sorted(load_usage(folder)["projects"].items()):
        rec = (row.get("parts") or {}).get(part)
        if rec is not None:
            out.append((proj, rec.get("revision"), rec.get("designators") or []))
    return out
