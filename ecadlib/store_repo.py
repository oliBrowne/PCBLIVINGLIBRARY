"""Shared store of confirmed parts, the store repository's side (docs/shared-store-plan.md): what its maintainer and
its GitHub Actions run inside a checkout of the public store repository, with `pip install ecadlib` and no secrets.

    ecadlib store-repo init ROOT [--name N] [--maintainer NAME ...] [--force]
    ecadlib store-repo add ROOT RECORD.json|SHARE_DIR ... [--dispute]
    ecadlib store-repo index ROOT [--check]
    ecadlib store-repo verify ROOT [--base GIT_REF | --old ROOT_OR_FILE] [--changed FILE ...] [--all]
                                   [--maintainer] [--author NAME]
    ecadlib store-repo withdraw ROOT KEY --reason TEXT [--issue URL]
    ecadlib store-repo dispute ROOT KEY --reason TEXT [--issue URL]
    ecadlib store-repo snapshot ROOT -o snapshot.zip
    ecadlib store-repo stats ROOT
Exit 0 ok, 1 problems found (one line each), 2 cannot run (usage, no such part, not a store).

The layout is exactly what store_client reads (store.json, index/<ab>.json, parts/<ab>/<key_id>.json, withdrawn.json),
plus what only this side keeps: withdrawn/<ab>/<key_id>.r<rev>.json (a withdrawn record, moved out of parts/ so the
index can never serve it, kept for the audit trail), disputes.json (why each dispute was raised) and the files of
store_template/ (README, LICENSE, NOTICE.md, CONTRIBUTING.md, .github/ workflows and templates). store.json also holds
"maintainers" (their two builds with two AI models confirm: store_record.level) and "trusted" (null: everyone's evidence
counts; a list: only those names and the maintainers').

Index. An entry {"key", "also", "rev", "sha256", "level", "status"} is listed in the shard of the record's own MPN and
in the shard of every `also` code (shards go by MPN; the client looks a code up only in that code's shard). `also`
holds keys (MAKER|MPN, always the record's own maker), as the client compares them. A record that does not validate,
sits at the wrong path or is withdrawn at its rev is never indexed (and is a problem). Files are written as sorted,
indented JSON, so an unchanged store re-indexes to the same bytes; store.json "updated" changes only with the index.

Revisions. A new part is rev 1, or one past its withdrawal. Agreeing evidence and new `also` codes are merged into the
record at the same rev: they add to the record, they never change the land. A land that changes is a new rev, and
only a maintainer makes one (withdraw the old, add the corrected: there is deliberately no `restore`).

Verify (the pull-request and nightly check; no AI, no network, never runs anything from the tree): per record, its
path; store_record.validate; a rebuild from the record alone (store_record.build) through release_check.check_item with
ecadlib's own rules (never an ecadlib.toml from the tree): any failure means the part would be held, which fails; the
rebuilt footprint's and symbol's hashes against the record's (a pointer record is not rebuilt). Against the base (git
or --old): pads or pins changed at the same rev fail; evidence may only be appended, `also` codes only added, status
only go to disputed; new evidence must come from one contributor (the pull request's author with --author), and
`signoff` / `kicad-exact` entries, a new rev, a removed record and changes to withdrawn.json, withdrawn/,
disputes.json, store.json (but its date), the licence files or .github/ are maintainer-only (they pass with
--maintainer). Then the index against parts/ and every file of the tree (only .json / .md / .yml / .txt / LICENSE in
their folders; nothing over 400 KB). A `kicad-exact` entry is not checked against the KiCad library in v1."""
import argparse, collections, copy, datetime, json, re, shlex, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

from . import __version__, fp_share as FS, store_keys as SK, store_record as SR

TEMPLATE = Path(__file__).parent / "store_template"
MAINTAINER_ONLY = "maintainer-only change"
MAX_FILE = 400_000
TEXT = (".json", ".md", ".yml", ".yaml", ".txt")
LEGAL = ("LICENSE", "NOTICE.md")
SNAP = ("store.json", "withdrawn.json")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_HEX2, _HEX32 = re.compile(r"[0-9a-f]{2}"), re.compile(r"[0-9a-f]{32}")


class Unusable(ValueError):
    """The store or the request cannot be handled at all (exit 2)."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _text(obj)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8", newline="\n")
        return True
    return False


def _load(path):
    """(parsed JSON, None) or (None, why)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, f"not readable JSON ({type(e).__name__})"


def _meta(root):
    meta, why = _load(Path(root) / "store.json")
    if why or not isinstance(meta, dict):
        raise Unusable(f"{root}: no readable store.json: not a store (ecadlib store-repo init makes one)")
    return meta


def _people(meta):
    """(trusted set or None, maintainers) as store_record.level takes them."""
    maint = tuple(str(m) for m in meta.get("maintainers") or [] if isinstance(m, str))
    t = meta.get("trusted")
    return (set(map(str, t)) | set(maint) if isinstance(t, list) else None), maint


def _withdrawn_list(root):
    lst, why = _load(Path(root) / "withdrawn.json")
    if why or not isinstance(lst, list):
        raise Unusable(f"{root}: withdrawn.json is missing or not a list: the store cannot say what is withdrawn")
    return lst


def _wrev(w):
    r = w.get("rev")
    return r if isinstance(r, int) and not isinstance(r, bool) else float("inf")    # no rev: every rev


def _withdrawn(root):
    """key_id -> highest withdrawn rev."""
    out = {}
    for w in _withdrawn_list(root):
        if isinstance(w, dict) and isinstance(w.get("key_id"), str):
            out[w["key_id"]] = max(out.get(w["key_id"], 0), _wrev(w))
    return out


def rel_path(record):
    """parts/<shard>/<key_id>.json of a record."""
    k = record["key"]["key"]
    return f"parts/{SK.shard(k)}/{SK.key_id(k)}.json"


def _path_problem(record, rel):
    try:
        want = rel_path(record)
    except (KeyError, TypeError):
        return "no key: not a record"
    if rel == want:
        return None
    got = rel.split("/")
    if len(got) != 3 or got[0] != "parts":
        return f"a record belongs at {want}"
    if got[1] != want.split("/")[1]:
        return f"in shard {got[1]}, but its MPN's shard is {want.split('/')[1]} (belongs at {want})"
    return f"file name is not its key_id {want.split('/')[2][:-5]} (belongs at {want})"


def also_keys(record):
    """The record's `also` codes as keys under its own maker, without its own key, sorted."""
    own = record["key"]["key"]
    maker = record["key"].get("maker") or SK.maker_of(own)
    out = {SK.key(maker, SK.mpn_of(a) if "|" in str(a) else a) for a in record.get("also") or [] if isinstance(a, str)}
    return sorted(k for k in out if k and SK.maker_of(k) and k != own)


def _brief(probs):
    return probs[0] + (f" (and {len(probs) - 1} more)" if len(probs) > 1 else "")


# ---------------------------------------------------------------- init
def template_files():
    """Published path in the store repo -> the file in ecadlib/store_template (its github/ is the repo's .github/)."""
    out = {}
    for f in sorted(TEMPLATE.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            rel = f.relative_to(TEMPLATE).as_posix()
            out["." + rel if rel.startswith("github/") else rel] = f
    return out


def init(root, name=None, maintainers=(), force=False, at=None, log=print):
    """An empty store at root plus the template files. Existing files are kept unless force. -> paths written"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for d in ("index", "parts"):
        (root / d).mkdir(exist_ok=True)
    meta = {"schema": SK.SCHEMA, "name": name or root.resolve().name, "shards": SK.SHARDS, "min_ecadlib": __version__,
            "maintainers": list(dict.fromkeys(maintainers)), "trusted": None, "updated": at or _now()}
    files = {"store.json": _text(meta), "withdrawn.json": _text([])}
    files.update({rel: src.read_text(encoding="utf-8") for rel, src in template_files().items()})
    wrote, kept = [], []
    for rel, text in files.items():
        f = root / rel
        if f.exists() and not force:
            kept.append(rel)
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
        wrote.append(rel)
    log(f"store at {root}: {len(wrote)} files written" + (f", {len(kept)} kept as they were (--force replaces them)"
                                                          if kept else ""))
    return wrote


# ---------------------------------------------------------------- index
def _index_of(root):
    """-> ({shard: {key_id: entry}}, problems)."""
    root = Path(root)
    trusted, maint = _people(_meta(root))
    wd = _withdrawn(root)
    shards, probs = {}, []
    for f in sorted((root / "parts").glob("*/*.json")):
        rel = f.relative_to(root).as_posix()
        rec, why = _load(f)
        if why:
            probs.append(f"{rel}: {why}; not indexed")
            continue
        bad = SR.validate(rec)
        if bad:
            probs.append(f"{rel}: not a valid record: {_brief(bad)}; not indexed")
            continue
        bad = _path_problem(rec, rel)
        if bad:
            probs.append(f"{rel}: {bad}; not indexed")
            continue
        k = rec["key"]["key"]
        kid = SK.key_id(k)
        if rec["rev"] <= wd.get(kid, 0):
            probs.append(f"{rel}: withdrawn at r{wd[kid]} (withdrawn.json) but still under parts/ at r{rec['rev']}; "
                         "not indexed")
            continue
        entry = {"key": k, "also": also_keys(rec), "rev": rec["rev"], "sha256": SK.digest(rec),
                 "level": SR.level(rec, trusted, maint), "status": rec["status"]}
        for ab in {SK.shard(k)} | {SK.shard(a) for a in entry["also"]}:
            shards.setdefault(ab, {})[kid] = entry
    return shards, probs


def index(root, check=False, at=None, log=print):
    """Rebuild index/ from parts/ (check: only compare). -> problems ([] = up to date / written cleanly)."""
    root = Path(root)
    shards, probs = _index_of(root)
    want = {f"index/{ab}.json": _text(e) for ab, e in shards.items()}
    have = {f.relative_to(root).as_posix() for f in (root / "index").glob("*")} if (root / "index").is_dir() else set()
    changed = []
    for rel, text in sorted(want.items()):
        f = root / rel
        try:
            same = f.read_text(encoding="utf-8") == text
        except (OSError, UnicodeDecodeError):
            same = False
        if not same:
            changed.append(rel)
            if not check:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8", newline="\n")
    for rel in sorted(have - set(want)):
        changed.append(rel)
        if not check:
            p = root / rel
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    if check:
        probs += [f"{rel}: index not up to date with parts/ (run: ecadlib store-repo index .)" for rel in changed]
    elif changed:
        meta = _meta(root)
        meta["updated"] = at or _now()
        _write(root / "store.json", meta)
    if not check:
        log(f"index: {len({k for e in shards.values() for k in e})} parts in {len(shards)} shard files, "
            f"{len(changed)} files changed")
    return probs


# ---------------------------------------------------------------- the rebuild check
def check_rebuild(record):
    """Problems of the part rebuilt from the record alone: release-check failures (it would be held) and hashes that
    differ from the record's. ecadlib's own rules, never a settings file from the store tree. -> (problems, notes)"""
    from . import altium_lib as AL, release_check as RC, rules as RU
    probs = []
    try:
        with tempfile.TemporaryDirectory(prefix="ecadlib-store-verify-") as d:
            try:
                item = SR.build(record, d, RU.DEFAULTS)
            except SR.PointerOnly:
                return [], ["pointer record (EasyEDA): not rebuilt here; a client fetches it and compares the hash"]
            fails, _, _ = RC.check_item(item, d, RU.DEFAULTS)
            probs += [f"rebuilt part would be held by the release check: {r}: {m}" for r, m in fails]
            fp = record["footprint"]
            text = (Path(d) / item["footprint"]["kicad_mod"]).read_text(encoding="utf-8")
            if FS.digest(text, fp["hash_v"]) != fp["hash"]:
                probs.append("rebuilt footprint's pad hash is not the record's footprint.hash")
            sym = AL.read_schlib(Path(d) / item["symbol"]["file"])[1][0]
            pins = [{"num": r["num"], "name": r.get("name", ""), "type": ""} for r in sym["records"]
                    if r.get("RECORD") == "2"]
            if SR.pins_hash(pins, record.get("pin_map") or {}) != SR.pins_hash(record):
                probs.append("rebuilt symbol's pins are not the record's pins (pins_hash differs)")
    except Exception as e:                      # not checked is not passed: fail
        probs.append(f"the part could not be rebuilt from the record ({type(e).__name__}: {str(e)[:160]}): not "
                     "released unchecked")
    return probs, []


# ---------------------------------------------------------------- add
def _incoming(sources):
    for s in sources:
        p = Path(s)
        if p.is_dir():
            yield from sorted((p / "parts").glob("*/*.json")) if (p / "parts").is_dir() else sorted(p.glob("*.json"))
        else:
            yield p


def _withdrawn_copies(root, record):
    k = record["key"]["key"]
    out = []
    for f in sorted((Path(root) / "withdrawn" / SK.shard(k)).glob(f"{SK.key_id(k)}.r*.json")):
        rec, why = _load(f)
        if not why and isinstance(rec, dict):
            out.append(rec)
    return out


def _dispute_log(root, record, reason, issue, at):
    k = record["key"]["key"]
    f = Path(root) / "disputes.json"
    lst, why = _load(f) if f.exists() else ([], None)
    lst = lst if not why and isinstance(lst, list) else []
    lst.append({"key_id": SK.key_id(k), "key": k, "rev": record["rev"], "reason": reason, "issue": issue or "",
                "at": at or _now()})
    _write(f, lst)


def add(root, sources, dispute=False, at=None, log=print):
    """Add incoming records (files, or `ecadlib store share` folders) to the store, then re-index. -> problems"""
    root = Path(root)
    _meta(root)
    wd = _withdrawn(root)
    probs, files = [], list(_incoming(sources))
    if not files:
        raise Unusable("no record files given (RECORD.json, or a folder with parts/<shard>/<key_id>.json)")
    for f in files:
        rec, why = _load(f)
        bad = [why] if why else SR.validate(rec)
        if bad:
            probs.append(f"{f}: refused: {_brief(bad)}")
            continue
        k, rel = rec["key"]["key"], rel_path(rec)
        kid, dest = SK.key_id(k), root / rel
        old, why = _load(dest) if dest.exists() else (None, None)
        if why:
            probs.append(f"{f}: refused: the store's {rel} is {why}")
            continue
        if old is None:
            new = copy.deepcopy(rec)
            new["status"] = "active"
            if kid in wd:
                if wd[kid] == float("inf"):
                    probs.append(f"{f}: refused: {k} is withdrawn at every rev")
                    continue
                same = [w for w in _withdrawn_copies(root, rec) if SR.agree(w, rec)]
                if same:
                    probs.append(f"{f}: refused: {k} has the same pads and pins as its withdrawn r{same[0]['rev']}")
                    continue
                new["rev"] = int(wd[kid]) + 1
            else:
                new["rev"] = 1
            bad = check_rebuild(new)[0]
            if bad:
                probs.append(f"{f}: refused: {_brief(bad)}")
                continue
            _write(dest, new)
            log(f"added {k} r{new['rev']} ({rel})")
            continue
        try:
            new = SR.merge(old, rec)
        except SR.Conflict as e:
            probs.append(f"{f}: conflict: {e}; not added" + ("; the store's record is now disputed" if dispute else
                                                              " (--dispute marks the store's record disputed)"))
            if dispute and old.get("status") != "disputed":
                _write(dest, dict(old, status="disputed"))
                _dispute_log(root, old, f"conflicting build: {e}", "", at)
            continue
        bad = SR.validate(new)
        if bad:
            probs.append(f"{f}: refused: the merged record does not validate: {_brief(bad)}")
            continue
        if _write(dest, new):
            log(f"merged into {k} r{new['rev']}: {len(new['evidence']) - len(old['evidence'])} evidence entries, "
                f"{len(new['also']) - len(old.get('also') or [])} codes added")
        else:
            log(f"{k}: nothing new")
    probs += index(root, at=at, log=log)
    return probs


# ---------------------------------------------------------------- withdraw, dispute
def find(root, which):
    """(rel path, record) of the part named by a key ('MAKER|MPN', as written or normalised) or a key_id."""
    s = str(which or "").strip()
    if _HEX32.fullmatch(s):
        hits = sorted(Path(root).glob(f"parts/*/{s}.json"))
    elif "|" in s:
        k = SK.key(*s.split("|", 1))
        hits = [Path(root) / f"parts/{SK.shard(k)}/{SK.key_id(k)}.json"] if k and SK.maker_of(k) else []
    else:
        raise Unusable(f"{s!r}: name the part as MAKER|MPN or by its key_id")
    hits = [h for h in hits if h.is_file()]
    if not hits:
        raise Unusable(f"{s}: no such part in the store (parts/)")
    rec, why = _load(hits[0])
    if why:
        raise Unusable(f"{hits[0]}: {why}")
    return hits[0].relative_to(root).as_posix(), rec


def withdraw(root, which, reason, issue="", at=None, log=print):
    """Withdraw a part: withdrawn.json gains an entry, the record moves to withdrawn/ (out of the index). -> problems"""
    root = Path(root)
    if not str(reason or "").strip():
        raise Unusable("a withdrawal needs --reason")
    rel, rec = find(root, which)
    lst = _withdrawn_list(root)
    k = rec["key"]["key"]
    kid = SK.key_id(k)
    lst.append({"key_id": kid, "key": k, "rev": rec["rev"], "reason": str(reason), "issue": str(issue or ""),
                "at": at or _now()})
    _write(root / "withdrawn.json", lst)
    keep = root / "withdrawn" / SK.shard(k) / f"{kid}.r{rec['rev']}.json"
    keep.parent.mkdir(parents=True, exist_ok=True)
    (root / rel).replace(keep)
    log(f"withdrew {k} r{rec['rev']}: {reason}")
    return index(root, at=at, log=log)


def dispute(root, which, reason, issue="", at=None, log=print):
    """Mark a part disputed (rev unchanged; a second opinion only until a maintainer settles it). -> problems"""
    root = Path(root)
    if not str(reason or "").strip():
        raise Unusable("a dispute needs --reason")
    rel, rec = find(root, which)
    _write(root / rel, dict(rec, status="disputed"))
    _dispute_log(root, rec, str(reason), issue, at)
    log(f"disputed {rec['key']['key']} r{rec['rev']}: {reason}")
    return index(root, at=at, log=log)


# ---------------------------------------------------------------- verify
def _git(root, *args):
    """Output of a read-only git command in root, or None when it fails (a file missing at that ref)."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _base_reader(root, base, old):
    """(read(rel) -> text or None, changed rel paths or None)."""
    if base:
        out = _git(root, "diff", "--name-only", "--no-renames", "--relative", base, "--")
        if out is None:
            raise Unusable(f"git diff against {base} failed: is {root} a git checkout with {base} fetched "
                           "(actions/checkout fetch-depth: 0)?")
        return (lambda rel: _git(root, "show", f"{base}:./{rel}")), sorted({l.strip() for l in out.splitlines()
                                                                           if l.strip()})
    if old:
        o = Path(old)
        if o.is_dir():
            now, then = _files(root), _files(o)
            changed = sorted(r for r in set(now) | set(then) if r not in now or r not in then
                             or now[r].read_bytes() != then[r].read_bytes())

            def read(rel):
                f = o / rel
                return f.read_text(encoding="utf-8") if f.is_file() else None
            return read, changed
        rec, why = _load(o)
        if why or not isinstance(rec, dict):
            raise Unusable(f"--old {o}: {why or 'not a record'}")
        text = o.read_text(encoding="utf-8")
        return (lambda rel: text if rel == rel_path(rec) else None), None
    return None, None


def _files(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts[:1]}


def _tree_problems(root):
    """Files that do not belong in a store: wrong kind, wrong folder, too big."""
    probs = []
    for rel, p in sorted(_files(root).items()):
        parts = rel.split("/")
        name = parts[-1]
        ok_kind = name.endswith(TEXT) or name.startswith("LICENSE")
        if not ok_kind:
            where = "anywhere" if re.search(r"\.(step|stp|wrl|pdf|zip|igs|iges|x_t)$", name, re.I) else "in a store"
            probs.append(f"{rel}: not allowed {where} (only .json / .md / .yml / .txt and LICENSE; no 3D models, "
                         "PDFs, archives or code)")
        elif len(parts) == 1:
            pass
        elif parts[0] == "index":
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{2}\.json", name):
                probs.append(f"{rel}: not an index shard (index/<ab>.json)")
        elif parts[0] == "parts":
            if len(parts) != 3 or not _HEX2.fullmatch(parts[1]) or not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                probs.append(f"{rel}: not a record file (parts/<ab>/<key_id>.json)")
        elif parts[0] == "withdrawn":
            if len(parts) != 3 or not _HEX2.fullmatch(parts[1]) or not re.fullmatch(r"[0-9a-f]{32}\.r\d+\.json", name):
                probs.append(f"{rel}: not a withdrawn record (withdrawn/<ab>/<key_id>.r<rev>.json)")
        elif parts[0] != ".github":
            probs.append(f"{rel}: not in a store folder (index/, parts/, withdrawn/, .github/ or the top)")
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if size > MAX_FILE:
            probs.append(f"{rel}: {size} bytes, more than {MAX_FILE}")
    return probs


def _against_base(rel, new, old_text, author, wd):
    """(problems, maintainer-only problems) of one record file against its base version (old_text None: new)."""
    probs, mo = [], []
    old = None
    if old_text is not None:
        try:
            old = json.loads(old_text)
        except ValueError:
            old = None
    if new is None:
        if old is not None:
            mo.append("record removed (a withdrawal moves it to withdrawn/)")
        return probs, mo
    if old is None:                              # a new part
        added = list(new.get("evidence") or [])
        kid = SK.key_id(new["key"]["key"])
        want = int(wd[kid]) + 1 if kid in wd and wd[kid] != float("inf") else 1
        if new.get("rev") != want:
            mo.append(f"a new part is r{want}, not r{new.get('rev')}")
    else:
        rev, orev = new.get("rev"), old.get("rev")
        if isinstance(rev, int) and isinstance(orev, int) and rev < orev:
            probs.append(f"rev went down (r{orev} -> r{rev})")
        if rev == orev and not SR.agree(old, new):
            probs.append(f"{' and '.join(SR._differs(old, new))} changed without a new rev: a conflict with the "
                         "store's record (a corrected part is withdrawn and comes back as a new rev)")
        elif rev != orev:
            mo.append(f"r{orev} -> r{rev}: a new rev replaces the part")
        oe, ne = old.get("evidence") or [], new.get("evidence") or []
        if [SK.digest(e) for e in ne[:len(oe)]] != [SK.digest(e) for e in oe]:
            mo.append("evidence removed or edited (evidence may only be appended)")
            seen = {SK.digest(e) for e in oe}
            added = [e for e in ne if SK.digest(e) not in seen]
        else:
            added = ne[len(oe):]
        if set(new.get("also") or []) - set(old.get("also") or []):
            mo.append(f"codes added to also: {', '.join(sorted(set(new['also']) - set(old.get('also') or [])))} "
                      "(that they share this land is the contributor's word alone)")
        if set(old.get("also") or []) - set(new.get("also") or []):
            mo.append("codes removed from also")
        if old.get("status") == "disputed" and new.get("status") == "active":
            mo.append("status disputed -> active (a maintainer settles a dispute)")
        same = {k for k in SR.TOP if k not in ("evidence", "also", "status") and old.get(k) != new.get(k)}
        if rev == orev and same and SR.agree(old, new):
            mo.append(f"{', '.join(sorted(same))} changed at the same rev")
    for e in added:
        if isinstance(e, dict) and e.get("kind") in ("signoff", "kicad-exact"):
            mo.append(f"evidence kind {e['kind']} (by {e.get('by')!r}) is a maintainer's to add")
    bys = sorted({str(e.get("by") or "") for e in added if isinstance(e, dict)})
    if "" in bys:
        probs.append("new evidence without a contributor name (by)")
    if len(bys) > 1:
        probs.append(f"new evidence from several contributors in one change ({', '.join(bys)}): one GitHub account "
                     "counts once, so each adds their own")
    if author and any(b != author for b in bys):
        probs.append(f"new evidence in the name of {', '.join(b for b in bys if b != author)}, but the change is "
                     f"{author}'s")
    return probs, mo


def _dupes(record):
    seen, out = set(), []
    for e in record.get("evidence") or []:
        if isinstance(e, dict) and e.get("kind") == "build":
            r = e.get("reader") if isinstance(e.get("reader"), dict) else {}
            k = (e.get("by"), r.get("provider"), r.get("model"))
            if k in seen:
                out.append(f"note: two build entries by {k[0]!r} with reader {k[2] or 'none'}: they count once")
            seen.add(k)
    return out


def verify(root, changed=None, base=None, old=None, all_records=False, maintainer=False, author=None, log=print):
    """The pull-request / nightly check (module doc). -> problems ([] = pass). Prints one line per problem."""
    root = Path(root)
    meta = _meta(root)
    wd = _withdrawn(root)
    trusted, maint = _people(meta)
    read_old, diff = _base_reader(root, base, old)
    if changed:
        diff = sorted({(Path(c).resolve().relative_to(root.resolve()).as_posix() if Path(c).is_absolute()
                        else Path(c).as_posix()) for c in changed})
    probs, mo, notes, failing = [], [], [], {}
    every = sorted(f.relative_to(root).as_posix() for f in (root / "parts").glob("*/*.json")) \
        if (root / "parts").is_dir() else []
    todo = every if all_records or diff is None else [r for r in diff if r.startswith("parts/")]
    for rel in todo:
        f = root / rel
        new, why = _load(f) if f.is_file() else (None, None)
        mine = []
        if why:
            mine.append(why)
        elif new is not None:
            bad = SR.validate(new)
            mine += bad
            if not bad:
                p = _path_problem(new, rel)
                mine += [p] if p else []
                kid = SK.key_id(new["key"]["key"])
                if new["rev"] <= wd.get(kid, 0):
                    mine.append(f"withdrawn at r{wd[kid]}: not served at r{new['rev']} (a corrected part is a "
                                "higher rev)")
                elif any(SR.agree(w, new) for w in _withdrawn_copies(root, new)):
                    mine.append("same pads and pins as a withdrawn rev of this part")
                b, n = check_rebuild(new)
                mine += b
                notes += [f"{rel}: {x}" for x in n + _dupes(new)]
        if diff is not None and rel in diff and why is None:
            # no base (an explicit list alone): the record counts as new, every evidence entry as contributed
            b, m = _against_base(rel, new, read_old(rel) if read_old else None, author, wd)
            mine += b
            mo += [f"{rel}: {MAINTAINER_ONLY}: {x}" for x in m]
            if new is not None and read_old is not None and not b and not mine:
                try:
                    old_rec = json.loads(read_old(rel) or "null")
                except ValueError:
                    old_rec = None
                if SR.level(new, trusted, maint) == "confirmed" and (
                        not isinstance(old_rec, dict) or SR.level(old_rec, trusted, maint) != "confirmed"):
                    notes.append(f"{rel}: note: this change makes {new['key']['key']} confirmed (used without a "
                                 "person looking): check who built it")
        probs += [f"{rel}: {x}" for x in mine]
        if mine and new is not None and isinstance(new.get("key"), dict):
            failing[new["key"].get("key")] = mine[0]
    for rel in diff or []:
        if rel.startswith("parts/") or rel.startswith("index/"):
            continue
        if rel == "store.json":
            o = read_old(rel) if read_old else None
            try:
                o = json.loads(o) if o else {}
            except ValueError:
                o = {}
            keys = sorted(k for k in set(o) | set(meta) if k != "updated" and o.get(k) != meta.get(k))
            if keys:
                mo.append(f"store.json: {MAINTAINER_ONLY}: {', '.join(keys)} changed")
        elif rel in ("withdrawn.json", "disputes.json") or rel.split("/")[0] in ("withdrawn", ".github") \
                or rel in LEGAL:
            mo.append(f"{rel}: {MAINTAINER_ONLY}")
    probs += index(root, check=True, log=log)
    probs += _tree_problems(root)
    if maintainer:
        notes += [f"{m} (allowed: --maintainer)" for m in mo]
    else:
        probs += mo
    for line in notes:
        log(line)
    for line in probs:
        log(line)
    n = len(todo)
    log(f"verify: {n} record{'s' if n != 1 else ''} checked with ecadlib {__version__}: "
        + (f"{len(probs)} problem{'s' if len(probs) != 1 else ''}" if probs else "all pass"))
    if failing and all_records:
        log("To withdraw them (a maintainer runs this and commits; nothing is pushed for you):")
        for k, why in sorted(failing.items()):
            log(f"  ecadlib store-repo withdraw . {shlex.quote(str(k))} --reason "
                f"{shlex.quote(f'fails the release check of ecadlib {__version__}: {why[:160]}')}")
    return probs


# ---------------------------------------------------------------- snapshot, stats
def snapshot(root, out, log=print):
    """A deterministic zip of store.json, withdrawn.json, index/ and parts/ (sorted names, fixed times)."""
    root, out = Path(root), Path(out)
    _meta(root)
    names = [n for n in SNAP if (root / n).is_file()]
    names += sorted(f.relative_to(root).as_posix() for f in (root / "index").glob("*.json"))
    names += sorted(f.relative_to(root).as_posix() for f in (root / "parts").glob("*/*.json"))
    names.sort()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            zi = zipfile.ZipInfo(n, ZIP_TIME)
            zi.compress_type, zi.create_system, zi.external_attr = zipfile.ZIP_DEFLATED, 3, 0o644 << 16
            z.writestr(zi, (root / n).read_bytes())
    tmp.replace(out)
    log(f"snapshot: {len(names)} files -> {out}")
    return out


def stats(root):
    """Counts of the indexed parts by level, status, footprint source kind and licence; withdrawals."""
    root = Path(root)
    shards, _ = _index_of(root)
    entries = {kid: e for s in shards.values() for kid, e in s.items()}
    c = {k: collections.Counter() for k in ("level", "status", "source", "license")}
    for kid, e in entries.items():
        rec, _ = _load(root / f"parts/{SK.shard(e['key'])}/{kid}.json")
        c["level"][e["level"]] += 1
        c["status"][e["status"]] += 1
        c["source"][SR.kind_of(rec["footprint"]["source"]) or "?"] += 1
        c["license"][rec["footprint"]["license"]] += 1
    return {"parts": len(entries), **{k: dict(sorted(v.items())) for k, v in c.items()},
            "withdrawn": len({w.get("key_id") for w in _withdrawn_list(root) if isinstance(w, dict)})}


# ---------------------------------------------------------------- command line
def _parser():
    ap = argparse.ArgumentParser(prog="ecadlib store-repo", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="make an empty store with its workflows and licence files")
    p.add_argument("root")
    p.add_argument("--name")
    p.add_argument("--maintainer", action="append", default=[])
    p.add_argument("--force", action="store_true", help="replace files that already exist")
    p.add_argument("--at", help=argparse.SUPPRESS)
    p = sub.add_parser("add", help="add records (files or `ecadlib store share` folders), then re-index")
    p.add_argument("root")
    p.add_argument("records", nargs="+")
    p.add_argument("--dispute", action="store_true", help="on a conflict, mark the store's record disputed")
    p.add_argument("--at", help=argparse.SUPPRESS)
    p = sub.add_parser("index", help="rebuild index/ from parts/")
    p.add_argument("root")
    p.add_argument("--check", action="store_true", help="only report whether the index is up to date")
    p.add_argument("--at", help=argparse.SUPPRESS)
    p = sub.add_parser("verify", help="the pull-request / nightly check")
    p.add_argument("root")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--base", help="git ref of the target branch (origin/main): check what changed against it")
    g.add_argument("--old", help="the base as a store folder or one record file (instead of git)")
    p.add_argument("--changed", nargs="+", help="the changed files (default: from --base / --old, else all)")
    p.add_argument("--all", action="store_true", help="check every record (the nightly)")
    p.add_argument("--maintainer", action="store_true", help="the change is a maintainer's: allow maintainer-only")
    p.add_argument("--author", help="the pull request's GitHub name: new evidence must be theirs")
    for name, hlp in (("withdraw", "pull a part: withdrawn.json + the record moved to withdrawn/"),
                      ("dispute", "mark a part disputed (second opinion only)")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("root")
        p.add_argument("key", help="MAKER|MPN or the key_id")
        p.add_argument("--reason", required=True)
        p.add_argument("--issue", default="")
        p.add_argument("--at")
    p = sub.add_parser("snapshot", help="zip the store for offline use (deterministic)")
    p.add_argument("root")
    p.add_argument("-o", "--out", default="snapshot.zip")
    p = sub.add_parser("stats", help="counts by level, status, source and licence")
    p.add_argument("root")
    return ap


def main(argv=None):
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        if a.cmd == "init":
            init(a.root, a.name, a.maintainer, a.force, a.at)
            return 0
        if a.cmd == "stats":
            s = stats(a.root)
            print(f"{s['parts']} parts, {s['withdrawn']} withdrawn")
            for k in ("level", "status", "source", "license"):
                print(f"  {k}: " + (", ".join(f"{n} {v}" for n, v in s[k].items()) or "-"))
            return 0
        if a.cmd == "snapshot":
            snapshot(a.root, a.out)
            return 0
        if a.cmd == "add":
            probs = add(a.root, a.records, a.dispute, a.at)
        elif a.cmd == "index":
            probs = index(a.root, a.check, a.at)
        elif a.cmd == "verify":
            return 1 if verify(a.root, a.changed, a.base, a.old, a.all, a.maintainer, a.author) else 0
        elif a.cmd == "withdraw":
            probs = withdraw(a.root, a.key, a.reason, a.issue, a.at)
        else:
            probs = dispute(a.root, a.key, a.reason, a.issue, a.at)
    except Unusable as e:
        print(f"ecadlib store-repo {a.cmd}: {e}", file=sys.stderr)
        return 2
    for p in probs:
        print(p)
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
