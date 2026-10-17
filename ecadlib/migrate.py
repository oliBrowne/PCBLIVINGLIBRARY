"""Versioned workspace files: one schema_version scheme, a registry of migrations, and `ecadlib migrate`.

    ecadlib migrate [PATH] [--check] [--dry-run] [--json]

Everything ecadlib writes and later reads back has a schema version (an integer per *kind* of file, listed in KINDS):

* JSON state that other code iterates (plan.json is a list, librarian_state.json is keyed by part name, the queue is a
  folder of item files, the agent folder holds several files) carries its version in the folder's stamp file,
  `ecadlib_schema.json`: {"schema_version": 1, "ecadlib": "0.3.0", "kinds": {"plan": 1, "librarian_state": 2}}.
  "ecadlib" is the version that last raised a kind in that folder (for the "made by ecadlib X.Y" message), so the stamp
  does not change on every run.
* Files a person edits (ecadlib.toml, approvals.json) and new single-object files (library.lock.json) carry it in the
  file itself: a top-level `schema_version` key. Absent = 1.
* A folder (or file) without a version is a workspace from before versioning: version 1 of every kind. Migrations
  are written so that they are also correct on data that already has the newer shape (a stamp lost by copying one
  file elsewhere must never damage it).

Reading an older version migrates it: in memory (`read`, `--dry-run`), or on disk by a command that writes the folder
anyway (`open_folder(write=True)`: the librarian, the agent, the queue, `ecadlib migrate`), which first copies every file
it changes to <folder>/.ecadlib_backup/<kind>-v<old>-<time>/. Reading a newer version raises SchemaTooNew with one
line: "<file> was made by ecadlib X.Y (<kind> schema N; this ecadlib 0.3.0 reads up to M): upgrade ecadlib".

To change a format: bump KINDS[kind].current and add MIGRATIONS[(kind, old)] = function(folder, files) -> {path: new
text}; a test that opens a fixture from before the change (tests/fixtures/upgrade/) proves it."""
import argparse, datetime, json, os, shutil, sys, time
from pathlib import Path

from . import __version__
from .fsutil import write_text_atomic

STAMP = "ecadlib_schema.json"
STAMP_FORMAT = 1
BACKUP = ".ecadlib_backup"


class SchemaTooNew(RuntimeError):
    """A file written by a newer ecadlib, in a format this one does not know."""


class Kind:
    def __init__(self, name, current, files, where="stamp", note=""):
        self.name, self.current, self.files, self.where, self.note = name, current, tuple(files), where, note


# where="stamp": the version is in the folder's ecadlib_schema.json; "key": a top-level schema_version in the file
KINDS = {k.name: k for k in (
    Kind("plan", 1, ["plan.json"], note="librarian / altium365 plan (a JSON list of parts)"),
    Kind("librarian_state", 2, ["librarian_state.json"],
         note="what the librarian found per part; v2: footprint name + geometry hash recorded, `inputs` for the lock"),
    Kind("approvals_audit", 1, ["approvals_audit.jsonl"], note="append-only sign-off log"),
    Kind("agent", 1, ["status.json", "agent_state.json", "watch_state.json", "watch_pending.json", "library_bom.json"],
         note="library agent state (<workspace>/agent/)"),
    Kind("queue", 1, ["items", "batches"], note="review queue (<workspace>/agent/queue/: one JSON file per item)"),
    Kind("http_cache", 1, ["kicad_library_index.json"], note="HTTP cache folder (--cache)"),
    Kind("rules", 1, ["ecadlib.toml", "ecadlib_rules.json"], where="key", note="library rules"),
    Kind("approvals_policy", 1, ["approvals.json"], where="key", note="sign-off policy next to a plan"),
    Kind("lock", 1, ["library.lock.json"], where="key", note="exact inputs per part (ecadlib rebuild)"),
)}


# ------------------------------------------------------------------ migrations
def _state_1_to_2(folder, data):
    """librarian_state v1 -> v2: footprint records get the name and geometry hash fp_share records since round 7.
    The hash is the one fp_share would compute from the record's .kicad_mod anyway, so nothing is decided
    differently; a footprint that got another part's file by name only (unchecked share) is left without a hash so the
    librarian still re-checks it."""
    from . import fp_share as FS
    for rec in (data or {}).values():
        fp = (rec or {}).get("footprint") if isinstance(rec, dict) else None
        if not isinstance(fp, dict) or fp.get("hash") or FS.unchecked_share(fp):
            continue
        k = Path(folder) / (fp.get("kicad_mod") or "")
        if fp.get("kicad_mod") and k.is_file():
            fp["hash"] = FS.digest(k.read_text(encoding="utf-8"))
        if not fp.get("name") and fp.get("file"):
            fp["name"] = Path(fp["file"]).stem
    return data


# (kind, from_version) -> function(folder, parsed data of the kind's main file) -> migrated data
MIGRATIONS = {("librarian_state", 1): _state_1_to_2}


# ------------------------------------------------------------------ stamps
def _ver(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 1


def _read(f):
    """Windows refuses a read while another process is replacing the file (os.replace): wait a moment and retry."""
    from .fsutil import DELAYS
    for delay in DELAYS + (None,):
        try:
            return f.read_text(encoding="utf-8")
        except PermissionError:
            if os.name != "nt" or delay is None:
                raise
            time.sleep(delay)


def read_stamp(folder):
    f = Path(folder) / STAMP
    try:
        d = json.loads(_read(f))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise SchemaTooNew(f"{f}: unreadable ({e}); restore it from {BACKUP}/ or delete it to treat the folder as "
                           f"ecadlib 0.3.0 data") from None
    if not isinstance(d, dict) or _ver(d.get("schema_version")) > STAMP_FORMAT:
        raise SchemaTooNew(f"{f} was made by ecadlib {d.get('ecadlib', '?') if isinstance(d, dict) else '?'} "
                           f"(stamp format {d.get('schema_version') if isinstance(d, dict) else '?'}; this ecadlib "
                           f"{__version__} reads up to {STAMP_FORMAT}): upgrade ecadlib (pip install -U ecadlib)")
    return d


def write_stamp(folder, kinds):
    """Record the current version of `kinds` in folder's stamp (other kinds already there are kept). Writes only when
    something changes, so a re-run leaves the file alone."""
    folder = Path(folder)
    old = read_stamp(folder) or {}
    have = dict(old.get("kinds") or {})
    new = dict(have, **{k: KINDS[k].current for k in kinds})
    if new == have and old.get("schema_version") == STAMP_FORMAT:
        return False
    d = {"schema_version": STAMP_FORMAT, "ecadlib": __version__ if new != have else old.get("ecadlib", __version__),
         "kinds": dict(sorted(new.items()))}
    folder.mkdir(parents=True, exist_ok=True)
    write_text_atomic(folder / STAMP, json.dumps(d, indent=1) + "\n")
    return True


def too_new(kind, version, made_by, where):
    k = KINDS[kind]
    return SchemaTooNew(f"{where} was made by ecadlib {made_by or '?'} ({kind} schema {version}; this ecadlib "
                        f"{__version__} reads up to {k.current}): upgrade ecadlib (pip install -U ecadlib)")


def folder_version(folder, kind):
    """(version, made_by) of `kind` in folder: from the stamp, 1 when there is none (data from before versioning)."""
    st = read_stamp(folder) or {}
    return _ver((st.get("kinds") or {}).get(kind, 1)), st.get("ecadlib")


def check_version(kind, version, made_by="", where=""):
    """Raise SchemaTooNew for a version this ecadlib does not know; returns the version as an int."""
    v = _ver(version)
    if v > KINDS[kind].current:
        raise too_new(kind, v, made_by, where or kind)
    return v


def check_key(kind, data, where=""):
    """For where="key" kinds: check data's top-level schema_version (absent = 1) and return data without it."""
    if not isinstance(data, dict):
        return data
    check_version(kind, data.get("schema_version", 1), data.get("ecadlib", ""), where)
    return {k: v for k, v in data.items() if k not in ("schema_version",)}


# ------------------------------------------------------------------ reading and upgrading a folder
def _main_file(folder, kind):
    for n in KINDS[kind].files:
        if (Path(folder) / n).exists():
            return Path(folder) / n
    return None


def kinds_in(folder):
    """The stamp kinds whose files are in this folder."""
    folder = Path(folder)
    out = [k for k, d in KINDS.items() if d.where == "stamp" and any((folder / n).exists() for n in d.files)]
    if "queue" in out and folder.name != "queue":
        out.remove("queue")                    # items/ and batches/ only mean a queue in agent/queue
    if "agent" in out and folder.name != "agent":
        out.remove("agent")
    return out


def pending(folder, kinds=None):
    """[(kind, from_version, to_version)] for kinds in folder that are older than this ecadlib; raises for newer."""
    folder = Path(folder)
    st = read_stamp(folder) or {}
    out = []
    for k in kinds if kinds is not None else kinds_in(folder):
        v = check_version(k, (st.get("kinds") or {}).get(k, 1), st.get("ecadlib"), str(_main_file(folder, k) or folder))
        if v < KINDS[k].current:
            out.append((k, v, KINDS[k].current))
    return out


def migrate_data(kind, version, data, folder="."):
    """Data of `kind` at `version` -> data at the current version (in memory)."""
    v = check_version(kind, version)
    while v < KINDS[kind].current:
        fn = MIGRATIONS.get((kind, v))
        if fn:
            data = fn(Path(folder), data)
        v += 1
    return data


def read(path, kind=None):
    """A versioned JSON file, migrated in memory to the current version (nothing is written)."""
    path = Path(path)
    kind = kind or next((k for k, d in KINDS.items() if path.name in d.files), None)
    data = json.loads(path.read_text(encoding="utf-8"))
    if kind is None:
        return data
    if KINDS[kind].where == "key":
        return check_key(kind, data, str(path))
    v, made = folder_version(path.parent, kind)
    check_version(kind, v, made, str(path))
    return migrate_data(kind, v, data, path.parent)


def _backup(folder, kind, version, files, t):
    d = Path(folder) / BACKUP / f"{kind}-v{version}-{t}"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, d / Path(f).name)
    return d


def open_folder(folder, kinds=None, write=True, log=None):
    """Check a folder ecadlib is about to use. Newer than this ecadlib -> SchemaTooNew. Older -> migrated on disk
    (write=True, after a backup) or left alone (write=False: callers then read through read()). write=True also stamps
    the current versions of `kinds` (default: the kinds found in the folder). Returns [(kind, from, to)] migrated."""
    folder = Path(folder)
    kinds = list(kinds) if kinds is not None else kinds_in(folder)
    todo = pending(folder, kinds) if folder.is_dir() else []
    if not write:
        return todo
    t = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    for kind, old, new in todo:
        f = _main_file(folder, kind)
        steps = [MIGRATIONS[(kind, v)] for v in range(old, new) if (kind, v) in MIGRATIONS]
        if f and f.is_file() and steps:
            b = _backup(folder, kind, old, [f], t)
            data = migrate_data(kind, old, json.loads(f.read_text(encoding="utf-8")), folder)
            write_text_atomic(f, json.dumps(data, indent=2))
            if log:
                log(f"  migrated {f} from {kind} v{old} to v{new} (backup in {b})")
    if folder.is_dir() and kinds:
        write_stamp(folder, kinds)
    return todo


# ------------------------------------------------------------------ whole workspace
SKIP_DIRS = {BACKUP, "datasheets", "__pycache__", ".git", "node_modules"}


def scan(root, max_depth=6):
    """Every versioned thing under root -> [{"path", "kind", "version", "current", "made_by", "state"}].
    state: ok | old (migration due) | new (made by a newer ecadlib) | error."""
    root = Path(root)
    rows = []

    def add(path, kind, v, made):
        cur = KINDS[kind].current
        rows.append({"path": str(path), "kind": kind, "version": v, "current": cur, "made_by": made or "",
                     "state": "new" if v > cur else "old" if v < cur else "ok"})

    for d, subdirs, files in os.walk(root):
        rel_depth = len(Path(d).relative_to(root).parts)
        subdirs[:] = sorted(s for s in subdirs if s not in SKIP_DIRS and rel_depth < max_depth
                            and not (Path(d).name == "queue" and s in ("items", "batches")))
        d = Path(d)
        if (d / "kicad_library_index.json").exists() or d.name == "http_cache":
            subdirs[:] = []                    # a cache: thousands of entries, nothing else to find inside
        try:
            st = read_stamp(d) or {}
        except SchemaTooNew as e:
            rows.append({"path": str(d / STAMP), "kind": "stamp", "version": None, "current": STAMP_FORMAT,
                         "made_by": "", "state": "new", "error": str(e)})
            continue
        for k in kinds_in(d) + [k for k in (st.get("kinds") or {}) if k in KINDS and k not in kinds_in(d)]:
            add(_main_file(d, k) or d, k, _ver((st.get("kinds") or {}).get(k, 1)), st.get("ecadlib"))
        for k in (st.get("kinds") or {}):
            if k not in KINDS:                 # a kind only a newer ecadlib has: not ours to read, not an error
                rows.append({"path": str(d), "kind": k, "version": st["kinds"][k], "current": None,
                             "made_by": st.get("ecadlib", ""), "state": "unknown"})
        for k, kd in KINDS.items():
            if kd.where != "key":
                continue
            for n in kd.files:
                f = d / n
                if not f.is_file():
                    continue
                try:
                    v, made = _key_version(f)
                except ValueError as e:
                    rows.append({"path": str(f), "kind": k, "version": None, "current": kd.current, "made_by": "",
                                 "state": "error", "error": str(e)})
                    continue
                add(f, k, v, made)
    return rows


def _key_version(f):
    f = Path(f)
    text = f.read_text(encoding="utf-8")
    if f.suffix == ".toml":
        from .rules import toml_module
        tomllib = toml_module()
        if tomllib is None:                    # Python 3.10 without tomli: a plain look at the top-level key
            import re
            m = re.search(r"(?m)^\s*schema_version\s*=\s*(\d+)", text.split("[", 1)[0])
            return (int(m.group(1)) if m else 1), ""
        try:
            d = tomllib.loads(text)
        except Exception as e:
            raise ValueError(f"not valid TOML ({e})") from None
    else:
        try:
            d = json.loads(text or "{}")
        except ValueError as e:
            raise ValueError(f"not valid JSON ({e})") from None
    d = d if isinstance(d, dict) else {}
    return _ver(d.get("schema_version", 1)), str(d.get("ecadlib") or "")


_CHECKED = {}


def guard_tree(root):
    """Read-only check of a whole workspace (once per process per folder): raise SchemaTooNew when anything in it was
    written by a newer ecadlib. Used where a workspace is opened (the web UI / MCP Workspace)."""
    root = Path(root).resolve()
    if root in _CHECKED:
        return
    for r in scan(root, max_depth=4):
        if r["state"] == "new":
            if r["kind"] == "stamp":
                raise SchemaTooNew(r["error"])
            raise too_new(r["kind"], r["version"], r["made_by"], r["path"])
    _CHECKED[root] = True


def migrate_tree(root, dry_run=False, log=print):
    """Bring every versioned folder under root to the current versions (backups first). -> scan rows with 'action'."""
    rows = scan(root)
    bad = [r for r in rows if r["state"] in ("new", "error")]
    if bad:
        return rows
    for r in rows:
        r["action"] = ""
        if r["state"] != "old":
            continue
        folder = Path(r["path"]).parent if Path(r["path"]).is_file() else Path(r["path"])
        steps = [v for v in range(r["version"], r["current"]) if (r["kind"], v) in MIGRATIONS]
        r["action"] = (f"migrate v{r['version']} -> v{r['current']}" if steps else f"stamp v{r['current']} (no change to the data)")
        if not dry_run:
            open_folder(folder, [r["kind"]], write=True, log=log)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="check or upgrade the files of an ecadlib workspace or library folder to "
                                             "this ecadlib's formats (backups in .ecadlib_backup/)")
    ap.add_argument("path", nargs="?", default=".", help="workspace or library folder (default: current folder)")
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 if anything needs migrating or is newer")
    ap.add_argument("--dry-run", action="store_true", help="show what would be migrated, change nothing")
    ap.add_argument("--json", action="store_true", help="print the rows as JSON")
    a = ap.parse_args(argv)
    root = Path(a.path)
    if not root.exists():
        print(f"ecadlib migrate: {root}: no such folder", file=sys.stderr)
        return 2
    rows = migrate_tree(root, dry_run=a.check or a.dry_run, log=(lambda s: None) if a.json else print)
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        from . import messages as M, term as TM
        t = TM.Term()
        dry = a.check or a.dry_run
        table = []
        for r in rows:
            what = {"ok": "up to date", "old": r.get("action") or f"v{r['version']} -> v{r['current']}",
                    "new": r.get("error") or f"made by ecadlib {r['made_by'] or '?'}: upgrade ecadlib",
                    "unknown": f"kind from a newer ecadlib {r['made_by']} (left alone)", "error": r.get("error", "")}[r["state"]]
            st, word = {"ok": ("ready", "ok"), "old": ("signoff", "to migrate" if dry else "migrated"),
                        "new": ("failed", "newer"), "unknown": ("skipped", "left alone"), "error": ("failed", "unreadable")}[r["state"]]
            if r["state"] == "ok" and not t.verbose and len(rows) > 12:
                continue                        # long trees: only what needs attention (-v lists every file)
            table.append([t.status(st, word), os.path.relpath(r["path"], root), M.clean(what)])
        t.table(table)
        n_old = sum(r["state"] == "old" for r in rows)
        n_bad = sum(r["state"] in ("new", "error") for r in rows)
        verb = "would be migrated" if dry else "migrated"
        nxt = []
        if dry and n_old:
            nxt.append((f"ecadlib migrate {a.path}", "upgrade them (backups go to .ecadlib_backup/ first)"))
        if n_bad:
            nxt.append(("pip install -U ecadlib", "a file was written by a newer ecadlib"))
        t.summary(f"{M.plural(len(rows), 'versioned file')}: {n_old} {verb}, {n_bad} newer or unreadable"
                  + ("; nothing written" if dry else ""), next=nxt)
    if any(r["state"] in ("new", "error") for r in rows):
        return 1
    return 1 if a.check and any(r["state"] == "old" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
