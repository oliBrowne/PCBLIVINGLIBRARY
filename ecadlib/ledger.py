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
(an approval not applied because the proposed version fails the release check; it is not counted as an approval). The head of
a part is its last release or revert. Nothing is ever deleted: `ecadlib part history` shows every line."""
import copy, datetime, hashlib, json, os, re, tempfile
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
                        raise ValueError(f"{self.path}:{n}: not a JSON line ({ex}); the ledger is append-only, "
                                         f"fix that line by hand (git shows what it was)") from None
                    if int(e.get("schema_version") or 1) > SCHEMA_VERSION:
                        from . import migrate as MG, __version__
                        raise MG.SchemaTooNew(f"{self.path} was made by a newer ecadlib (ledger schema "
                                              f"{e.get('schema_version')}; this ecadlib {__version__} reads up to "
                                              f"{SCHEMA_VERSION}): upgrade ecadlib")
                    self._lines.append(e)
        return self._lines

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
