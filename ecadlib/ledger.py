"""The part ledger: append-only release history of every part in a library folder, and the files of each revision.

    <library>/library.ledger.jsonl        one JSON line per event, git-friendly (sorted keys, never rewritten)
    <library>/.ecadlib/blobs/ab/<sha256>  the exact bytes of every released file and fingerprint (content-addressed)
    <library>/.ecadlib/proposed/<part>.json   a held change: the proposed version waiting for approval
    <library>/.ecadlib/marks/<part>@r<N>@<digest>   one empty file per recorded release / revert: a ledger cut back
                                          or merged from two branches is told by them (fail closed, Ledger.trouble)
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
import contextlib, copy, datetime, hashlib, json, os, re, tempfile, threading, time
from pathlib import Path

from .fsutil import portable_stem, write_text_atomic

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


def is_text(b):
    """Bytes git would check out with converted line ends (core.autocrlf): no NUL byte. .kicad_mod, .ecadbuild and STEP
    files are text; a .SchLib / .PcbLib (OLE) is not."""
    return b"\0" not in b[:65536]


def eol_forms(b):
    """The forms of file bytes that differ only in line ends: (raw, LF, CRLF) for text, (raw,) otherwise. A library
    checked out by Git for Windows with core.autocrlf has CRLF where ecadlib wrote LF; that alone is no edit (R-G2-4)."""
    if not is_text(b) or b"\r" not in b and b"\n" not in b:
        return (b,)
    lf = b.replace(b"\r\n", b"\n")
    return (b, lf, lf.replace(b"\n", b"\r\n"))


def eol_shas(b):
    """sha256 of every eol_forms of b."""
    return {sha_bytes(x) for x in eol_forms(b)}


def safe_name(part):
    s = portable_stem(re.sub(r"[^A-Za-z0-9._+-]", "_", str(part))[:120] or "_")   # AUX.json is a Windows device
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
    """{kind: relative path} of the model files a plan item releases (symbol, footprint, kicad_mod). kicad_mod is the
    footprint's kicad_mod, else the .kicad_mod beside its .PcbLib (models/KiCad/<ref>.kicad_mod), which the KiCad
    publishers ship when it is there (fingerprint.kicad_mod_path)."""
    from .fingerprint import kicad_mod_beside
    out = {}
    for kind, key in (("symbol", "file"), ("footprint", "file"), ("kicad_mod", "kicad_mod")):
        half = item.get("footprint" if kind == "kicad_mod" else kind) or {}
        if half.get(key):
            out[kind] = half[key]
        elif kind == "kicad_mod" and kicad_mod_beside(half):
            out[kind] = kicad_mod_beside(half)
    return out


def step_files(lib_path):
    """The STEP files the .ecadbuild recipe beside a PcbLib names (ecadlib_3d/<footprint>_<n>/<model>, relative to the
    library's folder); [] without a recipe."""
    from .altium_native import recipe_steps
    try:
        return [s for s in recipe_steps(lib_path) if ".." not in s.replace("\\", "/").split("/") and not Path(s).is_absolute()]
    except (OSError, UnicodeDecodeError):
        return []


def side_drift(rec, path):
    """The Altium build recipe (<file>.ecadbuild) and the 3D models it names beside `path` that differ from those a
    snapshot record (a ledger head's files entry) stored -> {path on disk: stored sha256}. Altium's own rebuild
    (altium_native, altium-pkg's RebuildWithAltium) makes the shipped library from these files, but the fingerprint
    reads the .SchLib / .PcbLib only, so an edit of them alone is a change no fingerprint shows. A missing file
    counts as different; a record that stored no recipe (or no STEP files) has nothing to compare."""
    p = Path(path)
    out = {}
    if (rec or {}).get("ecadbuild"):
        side = p.with_name(p.name + ".ecadbuild")
        if not side.is_file() or rec["ecadbuild"] not in eol_shas(side.read_bytes()):   # CRLF alone: no edit
            out[str(side)] = rec["ecadbuild"]
    for s, h in ((rec or {}).get("steps") or {}).items():
        if ".." in s.replace("\\", "/").split("/") or Path(s).is_absolute():
            continue
        f = p.parent / s
        if not f.is_file() or h not in eol_shas(f.read_bytes()):
            out[str(f)] = h
    return out


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
                text = self.path.read_bytes()          # bytes: a write cut inside a UTF-8 character still reads
                rows = text.splitlines()
                for n, raw in enumerate(rows, 1):
                    if not raw.strip():
                        continue
                    try:
                        e = json.loads(raw.decode("utf-8"))
                        if not isinstance(e, dict):
                            raise ValueError(f"a JSON {type(e).__name__}, not a ledger entry")
                    except ValueError as ex:
                        if n == len(rows) and not text.endswith((b"\n", b"\r")):
                            continue           # a write cut short (killed, disk full): it never happened; append()
                        raise LedgerError(     # sets its bytes aside before the next line
f"{self.path}:{n}: a broken ledger line ({ex}); nothing was changed",
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
        self.__dict__.pop("_stored_fps", None)
        self.__dict__.pop("_marks", None)

    def exists(self):
        return self.path.is_file()

    def append(self, entry):
        e = {"schema_version": SCHEMA_VERSION, "at": now(), **entry}
        self.folder.mkdir(parents=True, exist_ok=True)
        first = not self.path.is_file()
        if not first:
            self._mend_tail()
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n")
        if first:                              # the folder's stamp lists the ledger from the start (ecadlib.migrate)
            from . import migrate as MG
            MG.write_stamp(self.folder, ["ledger"])
        if self._lines is not None:
            self._lines.append(e)
        if e.get("event") in RELEASES and e.get("part") and isinstance(e.get("revision"), int):
            self._mark(e)
        return e

    # ------------------------------------------------------------ marks: a cut or merged ledger fails closed (R-G2-3)
    def _mark(self, e):
        """After a release / revert line: an empty file .ecadlib/marks/<part>@r<N>@<digest>. A ledger restored from an
        older copy, or merged from two branches, loses or doubles lines; these files (merged by git like any others)
        still say which revisions were recorded."""
        d = self.store / "marks"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{safe_name(e['part'])}@r{e['revision']}@{str(e.get('digest') or '-')[:16]}").touch()
        except OSError:
            pass
        self.__dict__.pop("_marks", None)

    def marks(self, part):
        """The releases .ecadlib/marks says were recorded for `part` -> {(revision, digest prefix)}."""
        if "_marks" not in self.__dict__:
            found = {}
            d = self.store / "marks"
            try:
                names = os.listdir(d) if d.is_dir() else []
            except OSError:
                names = []
            for n in names:
                bits = n.rsplit("@", 2)
                if len(bits) == 3 and bits[1][:1] == "r" and bits[1][1:].isdigit():
                    found.setdefault(bits[0], set()).add((int(bits[1][1:]), bits[2]))
            self._marks = found
        return self._marks.get(safe_name(part), set())

    def trouble(self, part):
        """Why `part`'s release history cannot be trusted, or None: a revision recorded twice or after a later one (a
        ledger merged from two branches), a line whose base is above the head, or a release .ecadlib/marks records that
        is above the head or another version of it (the ledger was cut back to an older copy, or a merge kept only
        one branch's lines)."""
        rel, seen = self.releases(part), []
        for e in rel:
            r = e.get("revision")
            if isinstance(r, int) and seen and r <= seen[-1]:
                two = sorted(f"{STORE}/marks/{safe_name(part)}@r{r}@{d}" for rr, d in self.marks(part) if rr == r)
                return (f"{LEDGER} records r{r} of {part} " + ("twice" if r in seen else f"after r{seen[-1]}") +
                        " (a ledger merged from two branches, or lines copied back); to mend it keep the one release "
                        f"line of r{r} you want in {LEDGER}, delete the other, and delete the other line's mark "
                        f"(one of {', '.join(two) or 'none'}, named by its digest)")
            if isinstance(r, int):
                seen.append(r)
        top = seen[-1] if seen else 0
        for e in self.history(part):
            b = e.get("base")
            if isinstance(b, int) and b > top:
                return f"a {e.get('event')} line of {part} in {LEDGER} is based on r{b}, above its head r{top}"
        head = rel[-1] if rel else None
        mine = {(e.get("revision"), str(e.get("digest") or "-")[:16]) for e in rel}
        for r, d in sorted(self.marks(part)):
            if (r, d) in mine:
                continue
            if r > top:
                return (f"{STORE}/marks says r{r} of {part} was released, but {LEDGER} ends at r{top} (the ledger is an "
                        f"older copy, or a merge dropped those lines)")
            if head is not None and r == top:
                return (f"{STORE}/marks says another r{r} of {part} was released than the one {LEDGER} holds (a ledger "
                        f"merged from two branches kept only one side); if the r{r} line {LEDGER} holds is the one you "
                        f"want, delete {STORE}/marks/{safe_name(part)}@r{r}@{d} (the other branch's mark), else restore "
                        f"that branch's r{r} line")
        return None

    def _mend_tail(self):
        """Before a line is appended: a ledger that does not end in a newline would glue the new line onto its last
        one (a broken line: every command on the library stops). A complete last line (an editor dropped the final
        newline) gets its newline; a cut one (a write killed half way, which lines() never counted) is moved to
        .ecadlib/torn-<time>.jsonl, so its bytes are kept, and cut off."""
        with open(self.path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if not size:
                return
            f.seek(size - 1)
            if f.read(1) in (b"\n", b"\r"):
                return
            back = min(size, 1 << 20)
            while True:                        # the start of the last line
                f.seek(size - back)
                chunk = f.read(back)
                i = max(chunk.rfind(b"\n"), chunk.rfind(b"\r"))
                if i >= 0 or back == size:
                    break
                back = min(size, back * 2)
            start = size - back + i + 1
            tail = chunk[i + 1:]
            try:
                ok = isinstance(json.loads(tail.decode("utf-8")), dict)
            except ValueError:
                ok = False
            if ok:
                f.seek(size)
                f.write(b"\n")
                return
            self.store.mkdir(parents=True, exist_ok=True)
            (self.store / f"torn-{now().replace(':', '')}-{os.getpid()}.jsonl").write_bytes(tail)
            f.truncate(start)

    def history(self, part):
        return [e for e in self.lines() if e.get("part") == part]

    def releases(self, part):
        return [e for e in self.history(part) if e.get("event") in RELEASES]

    def head(self, part):
        r = self.releases(part)
        if r:
            why = self.trouble(part)
            if why:                            # which revision is released is not known: nothing is taken as it
                raise LedgerError(f"{self.folder}: {why}; nothing was changed",
                                  f"restore {LEDGER} and {STORE}/ from git as one (`git log -- {LEDGER}` in the library "
                                  f"folder shows where lines were lost or doubled), or mend it as the message says (one "
                                  f"release line per revision, the other branch's {STORE}/marks file deleted), then run "
                                  f"again; `ecadlib part history` lists the part's lines; see docs/change-control.md, "
                                  f"\"When the ledger is damaged\"")
        return r[-1] if r else None

    def revision(self, part, rev):
        """A release / revert line by number ('3', 3, 'r3'), or None."""
        rev = int(str(rev).lstrip("rR"))
        return next((e for e in self.releases(part) if e.get("revision") == rev), None)

    def parts(self):
        return sorted({e["part"] for e in self.lines() if e.get("event") in RELEASES})

    def rejected(self, part, digest, fp=None):
        """The reject line for this proposed digest after the part's head, or None. fp: the proposed fingerprint,
        to match a reject line recorded by an older ecadlib (another fingerprint schema, so another digest): its
        snapshot is fingerprinted again, or (a line with no snapshot) its stored fingerprint is compared on what that
        schema recorded."""
        head = self.head(part)
        after = False if head else True
        hit = None
        for e in self.history(part):
            if e is head:
                after = True
                continue
            if after and e.get("event") == "reject" and (e.get("digest") == digest or fp is not None
                                                         and self._same_proposal(e, fp)):
                hit = e
        return hit

    def _same_proposal(self, e, fp):
        from . import change_diff as CD, fingerprint as FP
        if e.get("fingerprint") and e.get("files") and e.get("item") is not None:
            return fp["hashes"]["digest"] in self.digests(e)
        old = next((f for _, _, f in self.stored_fingerprints(e.get("part")) if f["hashes"].get("digest") == e.get("digest")),
                   None)
        return bool(old) and old.get("schema") != FP.SCHEMA and not CD.diff_stored(old, fp)

    def digests(self, entry):
        """The digests a ledger entry (a release, a proposal) stands for: the one it stored and, when an older ecadlib
        stored it (another fingerprint schema), the one this ecadlib computes from its snapshot. A digest computed now
        is compared with these, never with the stored one alone."""
        out = {entry.get("digest")} if entry and entry.get("digest") else set()
        try:
            fp = self.fingerprint(entry) if entry and entry.get("fingerprint") else None
        except (OSError, ValueError, KeyError):
            fp = None
        if fp:
            out.add((fp.get("hashes") or {}).get("digest"))
        return out - {None}

    def stored_fingerprints(self, part):
        """Every fingerprint of `part` in the blob store (they carry the part's name) -> [(mtime, sha, fingerprint)]."""
        blobs = self.store / "blobs"
        found = []
        if not blobs.is_dir():
            return found
        cache = self.__dict__.setdefault("_stored_fps", {})
        if part in cache:
            return cache[part]
        for p in blobs.glob("*/*"):
            try:
                if not p.is_file() or p.stat().st_size > 4_000_000:
                    continue
                with open(p, "rb") as f:
                    if f.read(1) != b"{":
                        continue
                fp = json.loads(p.read_bytes().decode("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(fp, dict) and fp.get("part") == part and isinstance(fp.get("hashes"), dict) and "schema" in fp:
                found.append((p.stat().st_mtime, p.name, fp))
        cache[part] = found
        return found

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
        """The entry's fingerprint. One stored by an older ecadlib (another fingerprint schema) is read again from the
        entry's file snapshot with this ecadlib, so it compares like for like with the files on disk: an upgrade that
        fingerprints more detail neither shows every released part as changed nor hides a real change."""
        if not entry or not entry.get("fingerprint"):
            return None
        fp = self.get_json(entry["fingerprint"])
        from . import fingerprint as FP
        if fp.get("schema") == FP.SCHEMA or not entry.get("files") or entry.get("item") is None:
            return fp
        key = (entry["fingerprint"], FP.SCHEMA, json.dumps(entry.get("files"), sort_keys=True))
        cache = self.__dict__.setdefault("_refp", {})
        if key not in cache:
            cache[key] = self._refingerprint(entry)
        return copy.deepcopy(cache[key])

    def _refingerprint(self, entry):
        """Fingerprint an entry's snapshot (its blobs, written to a temporary folder) with the current code."""
        from . import fingerprint as FP
        item = dict(json.loads(json.dumps(entry.get("item") or {})), name=entry.get("part") or "")
        halves = {"symbol": ("symbol", "file"), "footprint": ("footprint", "file"), "kicad_mod": ("footprint", "kicad_mod")}
        with tempfile.TemporaryDirectory(prefix="ecadlib-refp-") as tmp:
            for kind, rec in (entry.get("files") or {}).items():
                if kind not in halves:
                    continue
                p = Path(tmp) / kind / Path(str(rec["path"]).replace("\\", "/")).name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(self.get_bytes(rec["sha256"]))
                if rec.get("ecadbuild"):
                    p.with_name(p.name + ".ecadbuild").write_bytes(self.get_bytes(rec["ecadbuild"]))
                half, key = halves[kind]
                item[half] = dict(item.get(half) or {}, **{key: str(p)})
            for half, key in (("symbol", "file"), ("footprint", "file"), ("footprint", "kicad_mod")):
                kind = "kicad_mod" if key == "kicad_mod" else half
                if kind not in (entry.get("files") or {}) and (item.get(half) or {}).get(key):
                    item[half] = dict(item[half], **{key: str(Path(tmp) / kind / "missing")})   # not snapshotted: absent
            return FP.of_item(item, tmp)

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
    """project: a path / name; parts: {part: {"revision": n or None, "designators": [...]}}. Replaces that project's row.
    The read-change-write holds the library lock (re-entrant: the librarian holds it already), so two scans at once
    keep both rows. A library folder this user cannot write (no lock file): the unchanged rescan still reads."""
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(locked(folder))
        except OSError:                                  # a changed row's write below says what is wrong
            pass
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
