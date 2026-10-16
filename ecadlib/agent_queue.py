"""Library agent work queue: one durable JSON file per work item, a state machine, history and batches.

    ecadlib queue list [--status review] [--batch ID] [--workspace DIR]
    ecadlib queue show ID
    ecadlib queue approve|reject|rebuild|retry ID --user "Ann <ann@x.com>" [--role R] [-m NOTE]
    (or: python -m ecadlib.agent_queue ...)

Files under <workspace>/agent/queue/ (all JSON, written to a temp file and moved over the old one with os.replace):
    items/<id>.json     one work item (Item below)
    batches/<id>.json   one batch: {"id", "items", "created", "updated", "pr_url", "status", ...}
    .lock               the lock file (see _Lock)

Item = {"id", "key", "mpn", "manufacturer", "origin": "watch"|"upkeep"|"manual", "change": Change or upkeep finding,
        "status", "history": [{"at","from","to","user","note"}], "build": {...}, "proof": Proof or None,
        "pictures": {"before": [paths], "after": [paths]}, "batch": id or None, "pr_url": str or None,
        "created", "updated"}
Part key: "manufacturer|MPN" (manufacturer stripped and lower-cased, MPN stripped and upper-cased; "|MPN" without one).

States: detected -> building -> proving -> review -> approved -> pushed
        any open state -> failed | rejected;  failed/rejected -> detected (retry);  review -> building (rebuild).
`upsert` dedupes by part key: a re-detected part whose item is still open updates that item (a history entry with
from == to says so); a failed/rejected item is re-opened (-> detected) only when the change itself is different;
a part whose last item was pushed gets a new item.

`build` fields the review page and approvals use (the builder fills them; all optional):
    bom, part      workspace-relative BOM and the librarian plan item name: approve/reject are then also recorded
                   through approvals.py in that plan (so release_check / altium365 push see them)
    user           who started the build: that person may not approve the item (no self-approval)
    sources, notes, files   what was built from where, shown on the review page
A person's Rebuild sets build.requested_by/requested_at/request_note and moves review -> building (failed/rejected
-> detected): the agent picks it up from there.

Locking: a threading.RLock per queue folder (one process) plus an exclusive lock on .lock across processes (the web
server, `ecadlib agent serve` and this CLI may run at once): fcntl.flock on POSIX, msvcrt.locking on Windows. On
Windows msvcrt gives up after about 10 s of contention (raises OSError: try again) and network drives may not honour
it; keep the workspace on a local disk there. Reads take the lock too, so nobody sees a half-applied batch. Within one
hold the items are read once (many upserts under one `with q.lock:` cost one folder read, not one per part).
approve / reject also take <workspace>/agent/plan.lock (without waiting: "decide again in a minute") while they record
into the plan, which the agent holds while the librarian rewrites it. decide(expect=build_token(item)) refuses a
decision on a build other than the one the person looked at."""
import argparse, contextlib, copy, datetime, json, os, re, secrets, sys, threading, time
from pathlib import Path

STATES = ("detected", "building", "proving", "review", "approved", "pushed", "failed", "rejected")
OPEN = ("detected", "building", "proving", "review", "approved")
NEXT = {"detected": ("building",), "building": ("proving",), "proving": ("review",), "review": ("approved", "building"),
        "approved": ("pushed",), "pushed": (), "failed": ("detected",), "rejected": ("detected",)}
for _s in OPEN:
    NEXT[_s] = NEXT[_s] + ("failed", "rejected")
ORIGINS = ("watch", "upkeep", "manual")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FIXED = ("id", "key", "status", "history", "created")          # changed only by upsert / transition
PICTURE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Manufacturer names as part keys use them: "ST", "STMicroelectronics" and "STMicroelectronics N.V." are one maker.
from .rules import MANUFACTURER_ALIASES as MFR_ALIASES      # defaults; [agent] manufacturer_aliases extends
MFR_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "lp", "gmbh",
                "ag", "sa", "nv", "bv", "plc", "kg", "kk", "oy", "ab", "spa", "srl", "pte", "pty", "sas", "se"}


def _clean_maker(name):
    """lower case, no accents or punctuation, no legal suffix (Inc, Ltd, GmbH, N.V., "& Co. KG" ...)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", s)              # N.V. -> NV., S.p.A. -> SpA
    s = s.replace("&", " and ")
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    while len(words) > 1 and (words[-1] in MFR_SUFFIXES or words[-1] == "and"):
        words.pop()
    return " ".join(words)


def alias_table(rules=None):
    """{cleaned alias: cleaned canonical} from [agent] manufacturer_aliases (defaults: MFR_ALIASES)."""
    raw = ((rules or {}).get("agent") or {}).get("manufacturer_aliases")
    raw = MFR_ALIASES if raw is None else raw
    out = {_clean_maker(k): _clean_maker(v) for k, v in raw.items() if _clean_maker(k) and _clean_maker(v)}
    return {k: out.get(v, v) for k, v in out.items()}                 # one level of chaining: A -> B -> C


_ALIASES = {"table": alias_table(), "sig": None, "rules_file": None}


def aliases_sig():
    if _ALIASES["sig"] is None:
        import hashlib
        _ALIASES["sig"] = hashlib.sha256(json.dumps(sorted(_ALIASES["table"].items())).encode()).hexdigest()[:16]
    return _ALIASES["sig"]


def use_rules(rules):
    """Make part keys use these rules' [agent] manufacturer_aliases (the agent, the watch and the queue call it with
    the workspace rules; keys made under another table are migrated once, see Queue.migrate_keys)."""
    table = alias_table(rules)
    if table != _ALIASES["table"]:
        _ALIASES.update(table=table, sig=None)


def use_workspace_rules(root):
    """use_rules() with <root>'s rules file, re-read only when that file changes; bad rules keep the current table."""
    from . import rules as RU
    try:
        f = RU.find(root)
        sig = (str(f), f.stat().st_mtime_ns) if f else None
        if sig != _ALIASES["rules_file"]:
            use_rules(RU.load(None, start=root))
            _ALIASES["rules_file"] = sig
    except (OSError, ValueError):
        pass


def norm_manufacturer(name):
    """The manufacturer as a part key uses it: _clean_maker, then the alias table ("ST" -> "stmicroelectronics").
    "" stays ""."""
    s = _clean_maker(name)
    return _ALIASES["table"].get(s, s)


def part_key(manufacturer, mpn):
    mpn = str(mpn or "").strip().upper()
    if not mpn:
        raise ValueError("a queue item needs an MPN")
    return f"{norm_manufacturer(manufacturer)}|{mpn}"


def renorm_key(key):
    """An old or foreign "manufacturer|MPN" key in today's form."""
    m, _, n = str(key or "").partition("|")
    return f"{norm_manufacturer(m)}|{n.strip().upper()}"


def check_id(i):
    if not isinstance(i, str) or not ID.match(i):
        raise ValueError(f"bad queue id {str(i)[:80]!r}")
    return i


# ---------------------------------------------------------------- locking
_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


class _Lock:
    """Re-entrant across threads (RLock) and exclusive across processes (an OS lock on <queue>/.lock, taken by the
    outermost holder only)."""
    def __init__(self, path):
        self.path, self.rlock, self.depth, self.fh = path, threading.RLock(), 0, None
        self.cache = None              # {file name: item} read while the lock is held; nobody else writes meanwhile

    @classmethod
    def of(cls, folder):
        key = str(Path(folder).resolve())
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(key, cls(Path(key) / ".lock"))

    def __enter__(self):
        self.rlock.acquire()
        try:
            if self.depth == 0:
                self.fh = _os_lock(self.path)
                self.cache = None
        except BaseException:
            self.rlock.release()
            raise
        self.depth += 1
        return self

    def __exit__(self, *exc):
        self.depth -= 1
        try:
            if self.depth == 0:
                fh, self.fh, self.cache = self.fh, None, None
                _os_unlock(fh)
        finally:
            self.rlock.release()


def _os_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:                                     # Windows
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)      # retries for ~10 s, then OSError
    except BaseException:
        fh.close()
        raise
    return fh


def _os_unlock(fh):
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_UN)
        except ImportError:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        fh.close()


def _write(path, data):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextlib.contextmanager
def plan_lock(workspace_root, wait=True):
    """<workspace>/agent/plan.lock: the agent holds it while the librarian rewrites its plan.json (minutes), and every
    approval path (the queue, the Approvals page / approve_part, `ecadlib approve`) takes it while it records into a
    plan, so neither overwrites the other. wait=False yields False at once when it is held. Not re-entrant: the
    queue's decide() holds it and calls Workspace._approve_part, the unlocked body. Released by the OS if the holder
    dies."""
    d = Path(workspace_root).resolve() / "agent"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / "plan.lock", "a+b")
    msvcrt = None
    try:
        try:
            try:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except ImportError:                                 # Windows
                import msvcrt
                _win_lock(msvcrt, f, wait)
        except OSError:
            if wait:
                raise
            got = False
        else:
            got = True
        try:
            yield got
        finally:
            if msvcrt is not None and got:                      # Windows frees a lock left on a closed file late
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        f.close()


def _win_lock(msvcrt, f, wait, poll=0.5):
    """msvcrt's blocking LK_LOCK gives up with OSError after 10 one-second tries, but the librarian holds the plan lock
    for minutes: wait by polling the non-blocking lock instead."""
    while True:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not wait:
                raise
        time.sleep(poll)


def build_token(item):
    """Which build of an item a person looked at: its files' hash and build time. A decision sent with the token it
    was made on (decide(expect=...)) is refused once the item was rebuilt."""
    b = (item or {}).get("build") or {}
    return f"{b.get('hash')}@{b.get('built')}"


# ---------------------------------------------------------------- the store
class Queue:
    def __init__(self, workspace):
        root = workspace if isinstance(workspace, (str, os.PathLike)) else workspace.root   # a path or a Workspace
        self.workspace = Path(root).resolve()
        self.dir = self.workspace / "agent" / "queue"
        self.items_dir, self.batches_dir = self.dir / "items", self.dir / "batches"
        for d in (self.items_dir, self.batches_dir):
            d.mkdir(parents=True, exist_ok=True)
        from . import migrate as MG                    # a queue from a newer ecadlib is refused; ours is stamped
        MG.open_folder(self.dir, ["queue"])
        self.lock = _Lock.of(self.dir)
        use_workspace_rules(self.workspace)            # [agent] manufacturer_aliases for part keys

    # -- files
    def _item_path(self, i):
        return self.items_dir / f"{check_id(i)}.json"

    def _load(self, path, what):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(f"no {what} {path.stem}") from None
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e})") from None

    def _new_id(self, d):
        while True:
            i = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            if not (d / f"{i}.json").exists():
                return i

    # -- items
    def get(self, i):
        with self.lock:
            return self._load(self._item_path(i), "queue item")

    def list(self, status=None, batch=None):
        """Items, newest first. status: one state or a list of them."""
        want = ({status} if status else set()) if isinstance(status, str) else set(status or ())
        bad = want - set(STATES)
        if bad:
            raise ValueError(f"status must be one of {', '.join(STATES)}")
        with self.lock:
            out = [copy.deepcopy(it) for it in self._all().values()
                   if (not want or it.get("status") in want) and (batch is None or it.get("batch") == batch)]
        return sorted(out, key=lambda it: (it.get("created") or "", it["id"]), reverse=True)

    def _all(self):
        """{file name: item} of every readable item (call with the lock held). Read once per lock hold: a caller that
        does many upserts under one `with q.lock:` (a scan of a huge BOM) reads the folder once, not once per part."""
        if self.lock.cache is None:
            cache = {}
            for f in self.items_dir.glob("*.json"):
                try:
                    cache[f.name] = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue                                    # a damaged file must not hide the rest
            self.lock.cache = cache
        return self.lock.cache

    def _put(self, it):
        _write(self._item_path(it["id"]), it)
        if self.lock.cache is not None:
            self.lock.cache[f"{it['id']}.json"] = copy.deepcopy(it)

    def find(self, key, open_only=False):
        """The newest item for a part key (open ones first when open_only is False), or None."""
        with self.lock:
            items = sorted((it for it in self._all().values() if it.get("key") == key),
                           key=lambda it: (it.get("created") or "", it["id"]), reverse=True)
            live = [it for it in items if it.get("status") in OPEN]
            hit = (live or ([] if open_only else items) or [None])[0]
            return copy.deepcopy(hit)

    def upsert(self, fields, user=None):
        """Create or update the item for a part. fields: a watch Change ({"key","mpn","manufacturer","kind","source",
        ...}) or item fields ({"mpn","manufacturer","origin","change", ...}). Returns the item."""
        if not isinstance(fields, dict):
            raise ValueError("upsert needs a dict")
        fields = dict(fields)
        is_change = "kind" in fields and "source" in fields and "change" not in fields
        change = fields if is_change else fields.get("change")
        mpn = fields.get("mpn") or (change or {}).get("mpn")
        mfr = fields.get("manufacturer") if "manufacturer" in fields else (change or {}).get("manufacturer")
        key = part_key(mfr, mpn)
        if fields.get("key") and renorm_key(fields["key"]) != key:
            raise ValueError(f"key {fields['key']!r} does not match manufacturer/MPN ({key})")
        origin = fields.get("origin") or ("watch" if is_change else "manual")
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {', '.join(ORIGINS)}")
        extra = {} if is_change else {k: v for k, v in fields.items()
                                       if k not in FIXED + ("mpn", "manufacturer", "origin", "change")}
        t = now()
        with self.lock:
            self.migrate_keys()
            key = self._resolve_blank(key, mfr, t, user)
            it = self.find(key)
            if it and it["status"] != "pushed":
                same = _strip_time(it.get("change")) == _strip_time(change)
                if change is not None:
                    it["change"] = change
                it.update(extra)
                if it["status"] in OPEN:
                    it["history"].append({"at": t, "from": it["status"], "to": it["status"], "user": user,
                                          "note": "re-detected" + ("" if same else " with a different change")})
                elif not same:
                    it["history"].append({"at": t, "from": it["status"], "to": "detected", "user": user,
                                          "note": "re-detected with a different change: retry"})
                    it["status"] = "detected"
                it["updated"] = t
                self._put(it)
                return it
            i = self._new_id(self.items_dir)
            it = {"id": i, "key": key, "mpn": str(mpn).strip(), "manufacturer": str(mfr or "").strip(), "origin": origin,
                  "change": change, "status": "detected", "build": {}, "proof": None,
                  "pictures": {"before": [], "after": []}, "batch": None, "pr_url": None, "created": t, "updated": t,
                  "history": [{"at": t, "from": None, "to": "detected", "user": user,
                               "note": f"{(change or {}).get('kind') or 'added'} ({origin})"}]}
            it.update(extra)
            self._put(it)
            return it

    def _makers(self, mpn_part):
        """{manufacturer key part: [items]} of the items for one MPN."""
        out = {}
        for it in self._all().values():
            m, _, n = str(it.get("key") or "").partition("|")
            if n == mpn_part:
                out.setdefault(m, []).append(it)
        return out

    def _resolve_blank(self, key, mfr, t, user):
        """A blank manufacturer matches the one known maker of that MPN (and an item first seen without one is
        re-keyed once its maker is known), so "|LM358" and "ti|LM358" are one part. Two known makers: kept apart."""
        m, _, n = key.partition("|")
        makers = self._makers(n)
        known = [x for x in makers if x]
        if not m:
            return f"{known[0]}|{n}" if len(known) == 1 else key
        if "" in makers and set(known) <= {m}:
            for it in makers[""]:
                it = copy.deepcopy(it)
                it["history"].append({"at": t, "from": it["status"], "to": it["status"], "user": user,
                                      "note": f"key {it['key']} -> {key}: manufacturer now known"})
                it["key"], it["updated"] = key, t
                it["manufacturer"] = it.get("manufacturer") or str(mfr or "").strip()
                self._put(it)
        return key

    def migrate_keys(self):
        """Once per queue: re-key items made before manufacturer names were normalised (and blank makers that
        match one known maker). Items that become the same part are merged: the one furthest along stays open,
        the other open ones are rejected by the agent as duplicates (their history says of what)."""
        marker = self.dir / "keys.json"
        if getattr(self.lock, "migrated", None) == (aliases_sig(), marker.exists()):
            return 0                                            # checked in this process already
        with self.lock:
            with contextlib.suppress(OSError, ValueError):
                if json.loads(marker.read_text()).get("aliases") == aliases_sig():
                    self.lock.migrated = (aliases_sig(), True)
                    return 0                                    # done for this alias table
            t, moved = now(), 0
            items = list(self._all().values())
            for it in items:
                m, _, n = str(it.get("key") or "").partition("|")
                new = f"{norm_manufacturer(it.get('manufacturer') or m)}|{n}"     # the name as written, if known
                if new != it.get("key"):
                    it = copy.deepcopy(it)
                    it["history"].append({"at": t, "from": it["status"], "to": it["status"], "user": "ecadlib-agent",
                                          "note": f"key {it['key']} -> {new}: manufacturer names normalised"})
                    it["key"], it["updated"] = new, t
                    self._put(it)
                    moved += 1
            for it in list(self._all().values()):                  # blank makers matching one known maker
                m, _, n = it["key"].partition("|")
                if not m:
                    known = [x for x in self._makers(n) if x]
                    if len(known) == 1:
                        it = copy.deepcopy(it)
                        it["history"].append({"at": t, "from": it["status"], "to": it["status"], "user": "ecadlib-agent",
                                              "note": f"key {it['key']} -> {known[0]}|{n}: one known manufacturer"})
                        it["key"], it["updated"] = f"{known[0]}|{n}", t
                        self._put(it)
                        moved += 1
            rank = {"approved": 5, "review": 4, "proving": 3, "building": 2, "detected": 1}
            groups = {}
            for it in self._all().values():
                if it["status"] in OPEN:
                    groups.setdefault(it["key"], []).append(it)
            for key, its in groups.items():
                if len(its) < 2:
                    continue
                its.sort(key=lambda x: (rank[x["status"]], x.get("created") or "", x["id"]), reverse=True)
                for dup in its[1:]:
                    self.transition(dup["id"], "rejected", user="ecadlib-agent",
                                    note=f"duplicate of {its[0]['id']} (same part once manufacturer names are normalised)")
            _write(marker, {"version": 2, "migrated": t, "rekeyed": moved, "aliases": aliases_sig()})
            self.lock.migrated = (aliases_sig(), True)
            return moved

    def update(self, i, **fields):
        """Change data fields (build, proof, pictures, pr_url, ...). The state only changes through transition()."""
        bad = set(fields) & set(FIXED)
        if bad:
            raise ValueError(f"{', '.join(sorted(bad))} cannot be changed with update(); use transition()")
        with self.lock:
            it = self.get(i)
            it.update(fields)
            it["updated"] = now()
            self._put(it)
            return it

    def allowed(self, i_or_item):
        it = i_or_item if isinstance(i_or_item, dict) else self.get(i_or_item)
        return list(NEXT.get(it["status"], ()))

    def transition(self, i, to, user=None, note="", **fields):
        """Move an item to state `to` (ValueError on an illegal move); appends to its history. fields are stored too."""
        if to not in STATES:
            raise ValueError(f"state must be one of {', '.join(STATES)}")
        bad = set(fields) & set(FIXED)
        if bad:
            raise ValueError(f"{', '.join(sorted(bad))} cannot be set here")
        with self.lock:
            it = self.get(i)
            if to not in NEXT[it["status"]]:
                raise ValueError(f"{it['id']}: cannot go from {it['status']} to {to}"
                                 + (f" (allowed: {', '.join(NEXT[it['status']])})" if NEXT[it["status"]] else ""))
            if to in ("approved", "rejected") and not (isinstance(user, str) and user.strip()):
                raise ValueError(f"{to}: say who decides (user)")
            if to == "approved" and _who_key(user) in {_who_key(b) for b in builders(it)}:
                raise PermissionError(f"{user} started this build and cannot approve it (no self-approval)")
            t = now()
            it["history"].append({"at": t, "from": it["status"], "to": to, "user": user, "note": str(note or "")})
            it.update(fields)
            it["status"], it["updated"] = to, t
            self._put(it)
            return it

    # -- batches
    def batch(self, ids, **fields):
        """Group items (one PR per batch). Each item's `batch` is set; an item may be in one batch at a time."""
        ids = [check_id(i) for i in ids]
        if not ids:
            raise ValueError("a batch needs at least one item")
        if set(fields) & {"id", "items", "created"}:
            raise ValueError("id, items and created are set by batch()")
        with self.lock:
            items = [self.get(i) for i in ids]                 # all exist before anything is written
            t = now()
            b = {"id": self._new_id(self.batches_dir), "items": ids, "created": t, "updated": t, "pr_url": None,
                 "status": "open", **fields}
            _write(self.batches_dir / f"{b['id']}.json", b)
            for it in items:
                it["batch"], it["updated"] = b["id"], t
                if b.get("pr_url"):
                    it["pr_url"] = b["pr_url"]
                self._put(it)
            return b

    def get_batch(self, b):
        with self.lock:
            return self._load(self.batches_dir / f"{check_id(b)}.json", "batch")

    def update_batch(self, b, **fields):
        """Change a batch (status, pr_url, ...); a new pr_url is copied to its items."""
        if set(fields) & {"id", "items", "created"}:
            raise ValueError("id, items and created cannot be changed")
        with self.lock:
            bt = self.get_batch(b)
            bt.update(fields)
            bt["updated"] = now()
            _write(self.batches_dir / f"{bt['id']}.json", bt)
            if "pr_url" in fields:
                for i in bt["items"]:
                    with contextlib.suppress(KeyError):
                        self.update(i, pr_url=fields["pr_url"])
            return bt

    def batches(self):
        out = []
        with self.lock:
            for f in self.batches_dir.glob("*.json"):
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    out.append(json.loads(f.read_text(encoding="utf-8")))
        return sorted(out, key=lambda b: (b.get("created") or "", b["id"]), reverse=True)


def _strip_time(change):
    """A change without its detection time and the part identity (equal anyway once the key matches)."""
    return {k: v for k, v in change.items() if k not in ("detected", "key", "mpn", "manufacturer")} \
        if isinstance(change, dict) else change


# ---------------------------------------------------------------- front-end helpers (Workspace methods call these)
def _who_key(who):
    from .approvals import user_key
    return user_key(who) if who else ""


def builders(item):
    b = item.get("build") or {}
    return [x for x in (b.get("user"), b.get("requested_by")) if isinstance(x, str) and x.strip()]


def summary(it):
    p = it.get("proof") or {}
    ch = it.get("change") or {}
    return {"id": it["id"], "key": it["key"], "mpn": it.get("mpn"), "manufacturer": it.get("manufacturer"),
            "origin": it.get("origin"), "status": it["status"], "kind": ch.get("kind") or ch.get("check") or "",
            "source": (ch.get("source") or {}).get("name") or (ch.get("source") or {}).get("ref") or "",
            "verdict": p.get("verdict"), "score": p.get("score"), "findings": len(p.get("findings") or []),
            "batch": it.get("batch"), "pr_url": it.get("pr_url"), "created": it.get("created"), "updated": it.get("updated")}


def picture_path(workspace_root, path):
    """A picture the review page may show: an image file inside <workspace>/agent/ (after resolving links), else
    ValueError. path: workspace-relative, or absolute."""
    root = Path(workspace_root).resolve()
    agent = (root / "agent").resolve()
    if not isinstance(path, str) or not path.strip() or "\0" in path or len(path) > 1000:
        raise ValueError("no picture path given")
    p = (root / path).resolve()
    if agent not in p.parents:
        raise ValueError("pictures are served from the workspace's agent/ folder only")
    if p.suffix.lower() not in PICTURE_TYPES:
        raise ValueError("not a picture (png, jpg, gif or webp)")
    if not p.is_file():
        raise ValueError("no such picture")
    return p


def _pictures(root, it):
    """Workspace-relative paths of the item's pictures that the page can show (inside agent/), per group."""
    groups = {"before": (it.get("pictures") or {}).get("before"), "after": (it.get("pictures") or {}).get("after"),
              "ours": ((it.get("proof") or {}).get("images") or {}).get("ours"),
              "datasheet": ((it.get("proof") or {}).get("images") or {}).get("datasheet")}
    out = {}
    for g, paths in groups.items():
        out[g] = []
        for p in paths or []:
            try:
                out[g].append(picture_path(root, str(p)).relative_to(Path(root).resolve()).as_posix())
            except ValueError:
                out[g].append(None)                             # listed but not showable: the page says so
    return out


def ws_list(root, status=None, batch=None):
    q = Queue(root)
    items = q.list(status or None, batch or None)
    counts = {s: 0 for s in STATES}
    for it in q.list():
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    items.sort(key=lambda it: it["status"] != "review")      # stable: waiting for a person first, then newest first
    out = {"items": [summary(it) for it in items], "counts": counts, "states": list(STATES),
           "batches": [{k: b.get(k) for k in ("id", "created", "status", "pr_url")} | {"count": len(b.get("items") or [])}
                       for b in q.batches()]}
    if batch:
        out["batch"] = q.get_batch(batch)
    return out


def ws_get(root, i):
    q = Queue(root)
    it = q.get(i)
    b = None
    if it.get("batch"):
        with contextlib.suppress(KeyError, ValueError):
            b = q.get_batch(it["batch"])
    return {"item": it, "pictures": _pictures(root, it), "allowed": q.allowed(it), "batch": b, "build_token": build_token(it)}


ACTIONS = ("approve", "reject", "rebuild", "retry")


def decide(ws, i, action, user, role=None, note="", via="ecadlib ui/mcp", aliases=(), expect=None):
    """A person's decision on a queue item: approve (review -> approved), reject (open -> rejected), rebuild
    (review -> building) or retry (failed/rejected -> detected). approve/reject are also recorded through approvals.py
    in the item's librarian plan (build.bom + build.part) so release_check and push see them; a refusal there stops an
    approve. user: who decides (required); aliases: other names of the same person (team user name), used for the
    no-self-approval check against build.user / build.requested_by. expect: the build_token() of the build the person
    looked at; an approve / reject is refused when the item was rebuilt since (the agent rebuilds review items whose
    source changed). Returns {"item", "approval"}."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    user = user.strip() if isinstance(user, str) else ""
    if not user:
        raise ValueError("say who you are: user is required (e.g. \"Ann Lee <ann@example.com>\")")
    if len(user) > 200 or any(c in user for c in "\r\n\0"):
        raise ValueError("user must be one line of at most 200 characters")
    note = note if isinstance(note, str) else ""
    if len(note) > 2000:
        raise ValueError("note: at most 2000 characters")
    if action == "reject" and not note.strip():
        raise ValueError("a reject needs a note saying what is wrong")
    q = Queue(ws.root)
    with q.lock:
        it = q.get(i)
        to = {"approve": "approved", "reject": "rejected", "rebuild": "building", "retry": "detected"}[action]
        if action == "approve" and it["status"] != "review":
            raise ValueError(f"only items in review can be approved; this one is {it['status']}")
        if action == "rebuild" and it["status"] not in ("review",):
            raise ValueError(f"rebuild works on items in review (use retry for failed or rejected); this one is {it['status']}")
        if to not in NEXT[it["status"]]:
            raise ValueError(f"cannot {action} an item that is {it['status']}")
        if expect is not None and action in ("approve", "reject") and str(expect) != build_token(it):
            raise ValueError("this item was rebuilt since you opened it: look at the new build, then decide again")
        if action == "approve":
            me = {_who_key(user)} | {_who_key(a) for a in aliases if a}
            if any(_who_key(b) in me for b in builders(it)):
                raise PermissionError(f"{user} started this build and cannot approve it (no self-approval)")
        approval = None
        b = it.get("build") or {}
        if action in ("approve", "reject"):
            if b.get("bom") and b.get("part"):
                with plan_lock(ws.root, wait=False) as free:
                    if not free:          # the librarian is rewriting the plan: a decision written now would be lost
                        raise ValueError("the library agent is rebuilding the library right now; decide again in a minute")
                    try:
                        rec_ = getattr(ws, "_approve_part", None) or ws.approve_part     # this lock is held here
                        r = rec_(b["bom"], b["part"], action, user, role, note, via=via + " (agent queue)")
                        approval = {"recorded": True, **r["decision"]}
                    except ValueError as e:
                        if action == "approve":
                            raise ValueError(f"the library plan refused this approval: {e}") from e
                        approval = {"recorded": False, "reason": str(e)}
            else:
                approval = {"recorded": False, "reason": "no librarian plan for this item (build.bom / build.part): "
                                                         "decision kept in the queue only"}
        extra = {}
        if action == "approve":
            extra["approval"] = {"by": user, "role": role or "", "at": now(), "build": build_token(it), **(approval or {})}
        if action == "reject":
            extra["rejection"] = {"by": user, "at": now(), "note": note, **(approval or {})}
        if action in ("rebuild", "retry"):
            extra["build"] = {**b, "requested_by": user, "requested_at": now(), "request_note": note}
        it = q.transition(i, to, user=user, note=note, **extra)
    return {"item": it, "approval": approval}


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib queue", description="the library agent's work queue")
    ap.add_argument("--workspace", default="ecadlib_workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="items, newest first")
    p.add_argument("--status", choices=STATES)
    p.add_argument("--batch")
    p = sub.add_parser("show", help="one item as JSON")
    p.add_argument("id")
    for a in ACTIONS:
        p = sub.add_parser(a, help=f"{a} an item")
        p.add_argument("id")
        p.add_argument("--user", help="who you are (default: $ECADLIB_USER, then git config)")
        p.add_argument("--role")
        p.add_argument("-m", "--note", default="")
    a = ap.parse_args(argv)
    root = Path(a.workspace).resolve()
    try:
        if a.cmd == "list":
            items = Queue(root).list(a.status, a.batch)
            for it in items:
                s = summary(it)
                print(f"  {s['id']:<24} {s['status']:<9} {s['key']:<36} {s['kind']:<8} {s['verdict'] or '':<8} {s['batch'] or ''}")
            print(f"{len(items)} item(s) in {Queue(root).dir}")
        elif a.cmd == "show":
            print(json.dumps(Queue(root).get(a.id), indent=2))
        else:
            from .app import Workspace
            from .approvals import identity
            r = decide(Workspace(root), a.id, a.cmd, identity(a.user, cwd=root), a.role, a.note, via="ecadlib queue")
            ap_ = r["approval"]
            print(f"{a.id}: {r['item']['status']}" + (f"; approvals.py: {'recorded' if ap_['recorded'] else ap_['reason']}" if ap_ else ""))
    except (ValueError, KeyError, PermissionError) as e:
        sys.exit(f"ecadlib queue: {e.args[0] if e.args else e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
