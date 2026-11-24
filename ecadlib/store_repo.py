"""Shared store of confirmed parts, the store repository's side (docs/shared-store-plan.md): what its maintainer and
its GitHub Actions run inside a checkout of the public store repository, with `pip install ecadlib` and no secrets.

    ecadlib store-repo init ROOT [--name N] [--maintainer NAME ...] [--force]
    ecadlib store-repo add ROOT RECORD.json|SHARE_DIR ... [--author NAME] [--maintainer [--dispute]]
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
store_template/ (README, LICENSE, NOTICE.md, CONTRIBUTING.md, .github/ workflows, templates and CODEOWNERS) and
ecadlib-version.txt (the ecadlib the verify workflow installs, read from the base branch). store.json also holds
"maintainers" (their two builds with two AI models confirm: store_record.level) and "trusted" (null: everyone's evidence
counts; a list: only those names and the maintainers').

Index. An entry {"key", "also", "rev", "sha256", "level", "status"} is listed in the shard of the record's own MPN and
in the shard of every `also` code (shards go by MPN; the client looks a code up only in that code's shard). `also`
holds keys (MAKER|MPN, always the record's own maker), as the client compares them. A record that does not validate,
sits at the wrong path or is withdrawn at its rev is never indexed (and is a problem). A pointer record (EasyEDA: no
drawing, nothing to rebuild or compare) is indexed as "checked" at most, never "confirmed". Files are written as sorted,
indented JSON, so an unchanged store re-indexes to the same bytes; store.json "updated" changes only with the index.

Revisions. A new part is rev 1, or one past its withdrawal. Agreeing evidence (the same pads and pins) is merged into
the record at the same rev: it adds to the record, it never changes the land. An entry whose text_hash is not the
record's (the same pads and pins, another drawing) is merged too, with a note, but confirms nothing
(store_record.level); add refuses an incoming entry whose text_hash is not the drawing it is shared with. A land that changes is a new rev, and only a maintainer makes
one (withdraw the old, add the corrected: there is deliberately no `restore`).

Contributors (add without --maintainer, verify without --maintainer) may only add parts and their own builds: per
record exactly one new `build` entry, all new evidence of one change under one name (--author: that name). Maintainer
only: `signoff` / `kicad-exact` evidence, `also` codes (on a new record as on an existing one), any status change, a
rev past 1 on a new file, any record of a part listed in withdrawn.json, pointer records, removed records, and every
file outside parts/ and index/ (store.json but its date, withdrawn.json, withdrawn/, disputes.json, every top-level
file, .github/).

Verify (the pull-request and nightly check; no AI, no network, never runs anything from the tree): per record, its
path; store_record.validate; a rebuild from the record alone (store_record.build) through release_check.check_item with
ecadlib's own rules (never an ecadlib.toml from the tree): any failure means the part would be held, which fails; the
REBUILT footprint (its digest at the current fp_share.HASH_V) and the REBUILT symbol's pins against the record's pins,
pin_map and pads (a pointer record is not rebuilt). Against the base (git or --old): pads or pins changed at the same
rev fail; evidence may only be appended; the contributor rules above (maintainer-only changes pass with --maintainer).
With --base the changed paths come from `git diff -z` (no quoting to dodge) plus untracked files; a changed path that
is not a record, an index shard or a maintainer-only file fails, and so does any symbolic link, submodule or executable
file in git's index. Then the index against parts/ and every file of the tree (only .json / .md / .yml / .txt / LICENSE
in their folders and .github/CODEOWNERS; no symbolic links; nothing over 400 KB). A change to .github/ is never proven
by its own pull request's check (a `pull_request` run uses the pull request's copy of the workflow): verify says so in
a WARNING line, and a maintainer reviews it by hand (CODEOWNERS + branch protection). A `kicad-exact` entry is not
checked against the KiCad library in v1."""
import argparse, collections, copy, datetime, json, os, re, shlex, shutil, stat, subprocess, sys, tempfile, zipfile
from pathlib import Path

from . import __version__, fp_share as FS, store_keys as SK, store_record as SR

TEMPLATE = Path(__file__).parent / "store_template"
MAINTAINER_ONLY = "maintainer-only change"
MAX_FILE = 400_000
TEXT = (".json", ".md", ".yml", ".yaml", ".txt")
LEGAL = ("LICENSE", "NOTICE.md")
SNAP = ("store.json", "withdrawn.json")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
VERSION_FILE = "ecadlib-version.txt"
CODEOWNERS = ".github/CODEOWNERS"
OWNERS_TOKEN = "@MAINTAINERS"
POINTER_ONLY = ("pointer records are not accepted from contributors yet (nothing checks an EasyEDA pointer's claim in "
                "v1; a maintainer adds them)")
_HEX2, _HEX32 = re.compile(r"[0-9a-f]{2}"), re.compile(r"[0-9a-f]{32}")
_REC_PATH, _IDX_PATH = re.compile(r"parts/[0-9a-f]{2}/[0-9a-f]{32}\.json"), re.compile(r"index/[0-9a-f]{2}\.json")
_VERSION = re.compile(r"\d+(\.\d+){1,3}")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_GIT_MODES = {"120000": "a symbolic link", "160000": "a submodule", "100755": "an executable file"}


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


def _safe(s):
    """Printable text of a line that may hold a path git gave as raw bytes (surrogate escapes) or control characters."""
    s = str(s).encode("utf-8", "backslashreplace").decode("utf-8")
    return "".join(c if c.isprintable() or c == " " else f"\\x{ord(c):02x}" for c in s)


def is_pointer(record):
    """A pointer record (EasyEDA): a half with no drawing, only an LCSC number. Nothing in v1 checks its claim."""
    for half, text in (("symbol", "kicad_sym"), ("footprint", "kicad_mod")):
        d = record.get(half) if isinstance(record, dict) else None
        if not isinstance(d, dict):
            continue
        if SR.kind_of(d.get("source")) == "easyeda" or d.get(text) is None or d.get("pointer") is not None:
            return True
    return False


def level_of(record, trusted=None, maintainers=()):
    """store_record.level, but never above "checked" for a pointer record (informational only)."""
    lv = SR.level(record, trusted, maintainers)
    return "checked" if is_pointer(record) and lv == "confirmed" else lv


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
    """An empty store at root plus the template files. Existing files are kept unless force. -> paths written
    maintainers: GitHub names; they go into store.json and, as @handles, into .github/CODEOWNERS (which owns every
    file outside parts/ and index/). ecadlib-version.txt pins this ecadlib for the verify workflow."""
    bad = [m for m in maintainers if not _LOGIN.fullmatch(str(m))]
    if bad:
        raise Unusable(f"--maintainer {bad[0]!r}: a maintainer is named by their GitHub name (letters, digits, '-')")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for d in ("index", "parts"):
        (root / d).mkdir(exist_ok=True)
    meta = {"schema": SK.SCHEMA, "name": name or root.resolve().name, "shards": SK.SHARDS, "min_ecadlib": __version__,
            "maintainers": list(dict.fromkeys(maintainers)), "trusted": None, "updated": at or _now()}
    files = {"store.json": _text(meta), "withdrawn.json": _text([]), VERSION_FILE: __version__ + "\n"}
    files.update({rel: src.read_text(encoding="utf-8") for rel, src in template_files().items()})
    owners = " ".join(f"@{m}" for m in meta["maintainers"])
    if owners:
        files[CODEOWNERS] = files[CODEOWNERS].replace(OWNERS_TOKEN, owners)
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
    if not owners and CODEOWNERS in wrote:
        log(f"note: no --maintainer given: put the maintainers' GitHub names in place of {OWNERS_TOKEN} in {CODEOWNERS}, "
            "then switch on branch protection as the README's maintainer checklist says")
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
                 "level": level_of(rec, trusted, maint), "status": rec["status"]}
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
def _pin_canon(name):
    """A pin name as the rebuilt SchLib holds it: KiCad's ~{X} overbar in Altium's X\\ form, cp1252, no spaces, upper
    case (so the record's name and the rebuilt one compare however a later pins_hash normalises names)."""
    from . import altium_lib as AL
    s = AL.overline(str(name or ""))
    return re.sub(r"\s", "", AL.enc(s).decode("cp1252", "replace")).upper()


def check_rebuild(record):
    """Problems of the part rebuilt from the record alone: release-check failures (it would be held), and the REBUILT
    part against the record's claims: the rebuilt .kicad_mod's digest at the current fp_share.HASH_V against the
    record's drawing and hash, the rebuilt symbol's pins against the record's pins, and pin_map against how the rebuilt
    part really wires pins (pin n to pad n, which must exist on the rebuilt footprint). ecadlib's own rules, never a
    settings file from the store tree. -> (problems, notes)"""
    from . import altium_lib as AL, release_check as RC, rules as RU, sexpr
    probs = []
    fp = record.get("footprint") if isinstance(record.get("footprint"), dict) else {}
    hv = fp.get("hash_v")
    if hv != FS.HASH_V:
        probs.append(f"footprint hashed with digest version hash_v {hv!r}, not the current {FS.HASH_V}: the hash leaves "
                     "out what a newer digest compares (a maintainer re-hashes the record)")
    try:
        with tempfile.TemporaryDirectory(prefix="ecadlib-store-verify-") as d:
            try:
                item = SR.build(record, d, RU.DEFAULTS)
            except SR.PointerOnly:
                return probs, ["pointer record (EasyEDA): not rebuilt here; informational only (never confirmed, "
                               "never used by a client)"]
            fails, _, _ = RC.check_item(item, d, RU.DEFAULTS)
            probs += [f"rebuilt part would be held by the release check: {r}: {m}" for r, m in fails]
            text = (Path(d) / item["footprint"]["kicad_mod"]).read_text(encoding="utf-8")
            now = FS.digest(text, FS.HASH_V)
            if now != FS.digest(str(fp.get("kicad_mod") or ""), FS.HASH_V):
                probs.append(f"rebuilt footprint's pad hash (digest v{FS.HASH_V}) is not the hash of the record's "
                             "kicad_mod")
            elif hv == FS.HASH_V and now != fp.get("hash"):
                probs.append("rebuilt footprint's pad hash is not the record's footprint.hash")
            pads = {str(p[1]) for p in sexpr.find(sexpr.parse(text), "pad") if len(p) > 1 and str(p[1])}
            sym = AL.read_schlib(Path(d) / item["symbol"]["file"])[1][0]
            got = {(str(r["num"]), _pin_canon(r.get("name", ""))) for r in sym["records"] if r.get("RECORD") == "2"}
            pins = (record.get("symbol") or {}).get("pins") or []
            want = {(str(p.get("num")), _pin_canon(p.get("name"))) for p in pins if isinstance(p, dict)}
            if got != want:
                diff = sorted(got ^ want)[:4]
                probs.append("rebuilt symbol's pins are not the record's pins (" + ", ".join(
                    f"{'rebuilt' if x in got else 'record'} has pin {x[0]} {x[1] or '(no name)'}" for x in diff) + ")")
            pm = record.get("pin_map") if isinstance(record.get("pin_map"), dict) else {}
            wired = sorted((str(n), str(v)) for n, v in pm.items() if str(n) != str(v))
            if wired:
                probs.append("pin_map wires " + ", ".join(f"pin {n} to pad {v}" for n, v in wired[:4]) + ", but the "
                             "rebuilt part connects each pin to the pad of the same number")
            nopad = sorted({n for n, _ in got} - pads, key=lambda s: (len(s), s))
            if nopad:
                probs.append(f"rebuilt symbol's pins {', '.join(nopad[:6])} have no pad on the rebuilt footprint")
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


def add(root, sources, dispute=False, at=None, log=print, maintainer=False, author=None):
    """Add incoming records (files, or `ecadlib store share` folders) to the store, then re-index. -> problems
    Without maintainer, the contributor rules verify applies (module doc) are applied here first, so what add writes is
    what verify passes: a refused record is not written, and when the call's new evidence is in more than one name (or
    not in author's) nothing is written. dispute (maintainer only): on a conflict, mark the store's record disputed."""
    root = Path(root)
    if dispute and not maintainer:
        raise Unusable("--dispute changes a record's status and disputes.json: a maintainer's (add --maintainer)")
    trusted, maint = _people(_meta(root))
    wd = _withdrawn(root)
    probs, files = [], list(_incoming(sources))
    if not files:
        raise Unusable("no record files given (RECORD.json, or a folder with parts/<shard>/<key_id>.json)")
    plan, names = {}, {}                                  # dest -> (old, new); contributor name -> first file
    for f in files:
        rec, why = _load(f)
        bad = [why] if why else SR.validate(rec)
        if bad:
            probs.append(f"{f}: refused: {_brief(bad)}")
            continue
        k, rel = rec["key"]["key"], rel_path(rec)
        kid, dest = SK.key_id(k), root / rel
        if dest in plan:
            old = plan[dest][1]
        else:
            old, why = _load(dest) if dest.exists() else (None, None)
            if why:
                probs.append(f"{f}: refused: the store's {rel} is {why}")
                continue
        had = {SK.digest(e) for e in (old or {}).get("evidence") or []}
        other = [e for e in rec["evidence"] if SK.digest(e) not in had and e.get("text_hash") != rec["text_hash"]]
        if other:
            probs.append(f"{f}: refused: evidence by {other[0].get('by')!r} gives a text_hash that is not this file's "
                         "drawing (an entry attests the drawing it is shared with)")
            continue
        if not maintainer:
            mine = []
            if kid in wd:
                mine.append(f"{k} was withdrawn (withdrawn.json): bringing it back or adding to it is a maintainer's")
            if is_pointer(rec):
                mine.append(POINTER_ONLY)
            also = sorted(set(rec.get("also") or []) - set((old or {}).get("also") or []))
            if also or (old is None and rec.get("also")):
                mine.append(f"also codes {', '.join(also or rec['also'])}: that another code shares this land is the "
                            "contributor's word alone; a maintainer adds also codes")
            if mine:
                probs.append(f"{f}: refused: {mine[0]} (add --maintainer)")
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
        else:
            try:
                new = SR.merge(old, rec)
            except SR.Conflict as e:
                probs.append(f"{f}: conflict: {e}; not added" + ("; the store's record is now disputed" if dispute else
                                                                  " (a maintainer's add --maintainer --dispute marks the "
                                                                  "store's record disputed)"))
                if dispute and old.get("status") != "disputed":
                    _write(dest, dict(old, status="disputed"))
                    _dispute_log(root, old, f"conflicting build: {e}", "", at)
                continue
            if maintainer:                                # a maintainer's also codes are taken, whatever merge does
                new["also"] = sorted(set(new.get("also") or []) | set(rec.get("also") or []))
            bad = SR.validate(new)
            if bad:
                probs.append(f"{f}: refused: the merged record does not validate: {_brief(bad)}")
                continue
        if not maintainer:
            b, m, bys = _against_base(rel, new, _text(old) if old is not None else None, author, wd)
            if b or m:
                probs.append(f"{f}: refused: {_brief(b + m)}" + (" (add --maintainer)" if m and not b else ""))
                continue
            for n in bys:
                names.setdefault(n, f)
        plan[dest] = (plan[dest][0] if dest in plan else old, new)
    if len(names) > 1:
        probs.append("refused, nothing written: new evidence in several names in one change ("
                     + ", ".join(f"{n} in {names[n]}" for n in sorted(names)) + "): each contributor adds their own "
                     "builds in their own pull request")
        return probs
    for dest, (old, new) in plan.items():
        k = new["key"]["key"]
        if not _write(dest, new):
            log(f"{k}: nothing new")
            continue
        if old is None:
            log(f"added {k} r{new['rev']} ({dest.relative_to(root).as_posix()})")
        else:
            log(f"merged into {k} r{new['rev']}: {len(new['evidence']) - len(old['evidence'])} evidence entries, "
                f"{len(new.get('also') or []) - len(old.get('also') or [])} codes added")
        for line in other_drawing_notes(old, new):
            log(line)
        if level_of(new, trusted, maint) == "confirmed" and (
                old is None or level_of(old, trusted, maint) != "confirmed"):
            log(f"note: this makes {k} confirmed (used without a person looking): check who built it")
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
    """Output of a read-only git command in root, or None when it fails (a file missing at that ref). Raw path bytes
    that are not UTF-8 come back as surrogate escapes (never a crash, never a dodged path)."""
    try:
        from .progpath import resolved               # never a git in the current folder (review 2026-11-23, 2.6)
        r = subprocess.run(resolved(["git", "-C", str(root), *args]), capture_output=True, text=True, encoding="utf-8",
                           errors="surrogateescape", timeout=120)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_paths(out):
    return {n for n in str(out or "").split("\0") if n}


def _base_reader(root, base, old):
    """(read(rel) -> text or None, changed rel paths or None)."""
    if base:
        out = _git(root, "diff", "-z", "--name-only", "--no-renames", "--relative", base, "--")
        more = _git(root, "ls-files", "-z", "--others") if out is not None else None
        if out is None or more is None:
            raise Unusable(f"git diff against {base} failed: is {root} a git checkout with {base} fetched "
                           "(actions/checkout fetch-depth: 0)?")
        return (lambda rel: _git(root, "show", f"{base}:./{rel}")), sorted(_git_paths(out) | _git_paths(more))
    if old:
        o = Path(old)
        if o.is_dir():
            now, then = _files(root), _files(o)
            changed = sorted(r for r in set(now) | set(then) if r not in now or r not in then
                             or now[r].read_bytes() != then[r].read_bytes())

            def read(rel):
                f = o / rel
                return f.read_text(encoding="utf-8") if f.is_file() and not f.is_symlink() else None
            return read, changed
        rec, why = _load(o)
        if why or not isinstance(rec, dict):
            raise Unusable(f"--old {o}: {why or 'not a record'}")
        text = o.read_text(encoding="utf-8")
        return (lambda rel: text if rel == rel_path(rec) else None), None
    return None, None


def _git_mode_problems(root):
    """Entries of git's index that no store holds: symbolic links, submodules, executable files (anywhere)."""
    out = _git(root, "ls-files", "-s", "-z")
    if out is None:
        return ["git ls-files failed: the file modes could not be checked"]
    probs = []
    for line in out.split("\0"):
        if "\t" not in line:
            continue
        info, rel = line.split("\t", 1)
        mode = info.split(" ", 1)[0]
        if mode != "100644":
            probs.append(f"{rel}: {_GIT_MODES.get(mode, f'git mode {mode}')} (git mode {mode}): a store holds plain "
                         "files only")
    return probs


def _walk(root):
    """(regular files {rel: path}, other entries [(rel, what)]) under root, never following a link; .git/ left out."""
    root = Path(root)
    files, other = {}, []
    for d, dirs, names in os.walk(root, followlinks=False):
        here = Path(d)
        for n in list(dirs):
            p = here / n
            rel = p.relative_to(root).as_posix()
            if rel == ".git":
                dirs.remove(n)
            elif p.is_symlink():
                other.append((rel, "a symbolic link"))
                dirs.remove(n)
        for n in names:
            p = here / n
            rel = p.relative_to(root).as_posix()
            if rel == ".git":
                continue
            try:
                m = p.lstat().st_mode
            except OSError:
                other.append((rel, "unreadable"))
                continue
            if stat.S_ISLNK(m):
                other.append((rel, "a symbolic link"))
            elif not stat.S_ISREG(m):
                other.append((rel, "not a regular file"))
            else:
                files[rel] = p
    return files, sorted(other)


def _files(root):
    return _walk(root)[0]


def _tree_problems(root, meta=None):
    """Files that do not belong in a store: wrong kind, wrong folder, too big, a link; ecadlib-version.txt unusable."""
    files, other = _walk(root)
    probs = [f"{rel}: {what}: not allowed in a store (plain files only)" for rel, what in other]
    for rel, p in sorted(files.items()):
        parts = rel.split("/")
        name = parts[-1]
        ok_kind = name.endswith(TEXT) or name.startswith("LICENSE") or rel == CODEOWNERS
        if not ok_kind:
            where = "anywhere" if re.search(r"\.(step|stp|wrl|pdf|zip|igs|iges|x_t)$", name, re.I) else "in a store"
            probs.append(f"{rel}: not allowed {where} (only .json / .md / .yml / .txt and LICENSE; no 3D models, "
                         "PDFs, archives or code)")
        elif len(parts) == 1:
            pass
        elif parts[0] == "index":
            if not _IDX_PATH.fullmatch(rel):
                probs.append(f"{rel}: not an index shard (index/<ab>.json)")
        elif parts[0] == "parts":
            if not _REC_PATH.fullmatch(rel):
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
    f = files.get(VERSION_FILE)
    v = None
    try:
        v = f.read_text(encoding="utf-8").strip() if f else None
    except (OSError, UnicodeDecodeError):
        pass
    if v is None:
        probs.append(f"{VERSION_FILE}: missing or unreadable: the verify workflow installs the ecadlib version it names "
                     "(a maintainer writes it; init does)")
    elif not _VERSION.fullmatch(v):
        probs.append(f"{VERSION_FILE}: {v[:40]!r} is not a version number (like {__version__})")
    elif meta and meta.get("min_ecadlib") and _vt(v) < _vt(meta["min_ecadlib"]):
        probs.append(f"{VERSION_FILE}: {v} is older than store.json min_ecadlib {meta['min_ecadlib']}")
    return probs


def _vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def _against_base(rel, new, old_text, author, wd):
    """(problems, maintainer-only problems, names of the new evidence) of one record file against its base version
    (old_text None: a new file). The contributor rules of the module doc."""
    probs, mo = [], []
    old = None
    if old_text is not None:
        try:
            old = json.loads(old_text)
        except ValueError:
            old = None
        if not isinstance(old, dict):
            old = None
    if new is None:
        if old is not None:
            mo.append("record removed (a withdrawal moves it to withdrawn/)")
        return probs, mo, set()
    try:
        k = new["key"]["key"]
        kid = SK.key_id(k)
    except (KeyError, TypeError):
        return probs + ["no key: not a record"], mo, set()
    if kid in wd:
        mo.append(f"{k} was withdrawn (withdrawn.json, r{wd[kid]}): any record of a withdrawn part is a maintainer's "
                  "to add or change")
    if is_pointer(new) or (old is not None and is_pointer(old)):
        mo.append(POINTER_ONLY)
    if old is None:                              # a new part
        added = list(new.get("evidence") or [])
        want = int(wd[kid]) + 1 if kid in wd and wd[kid] != float("inf") else 1
        if new.get("rev") != want:
            mo.append(f"a new part is r{want}, not r{new.get('rev')}")
        elif want != 1:
            mo.append(f"r{want}: a corrected part after a withdrawal is a maintainer's to add")
        if new.get("also"):
            mo.append(f"also codes on a new part: {', '.join(map(str, new['also']))} (that they share this land is "
                      "the contributor's word alone; a maintainer adds them)")
        if new.get("status") != "active":
            mo.append(f"a new part with status {new.get('status')!r} (a maintainer sets a status)")
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
        na, oa = set(map(str, new.get("also") or [])), set(map(str, old.get("also") or []))
        if na - oa:
            mo.append(f"codes added to also: {', '.join(sorted(na - oa))} "
                      "(that they share this land is the contributor's word alone)")
        if oa - na:
            mo.append("codes removed from also")
        if old.get("status") != new.get("status"):
            mo.append(f"status {old.get('status')} -> {new.get('status')} (a maintainer disputes a part or settles a "
                      "dispute)")
        same = {k for k in SR.TOP if k not in ("evidence", "also", "status") and old.get(k) != new.get(k)}
        if rev == orev and same and SR.agree(old, new):
            mo.append(f"{', '.join(sorted(same))} changed at the same rev")
    if len(added) > 1:
        mo.append(f"{len(added)} new evidence entries for one part: a contributor adds one build per part")
    for e in added:
        if isinstance(e, dict) and e.get("kind") != "build":
            mo.append(f"evidence kind {e.get('kind')} (by {e.get('by')!r}) is a maintainer's to add")
    bys = sorted({str(e.get("by") or "") for e in added if isinstance(e, dict)})
    if "" in bys:
        probs.append("new evidence without a contributor name (by)")
    if len(bys) > 1:
        mo.append(f"new evidence from several contributors in one change ({', '.join(bys)}): one GitHub account "
                     "counts once, so each adds their own")
    if author and any(b != author for b in bys):
        probs.append(f"new evidence in the name of {', '.join(b for b in bys if b != author)}, but the change is "
                     f"{author}'s")
    return probs, mo, {b for b in bys if b}


def other_drawing_notes(old, new):
    """Notes for evidence new adds over old (None: a new record) that attests the record's pads and pins but another
    drawing (its text_hash is not the record's): merged, but it does not confirm the record (store_record.level)."""
    had = {SK.digest(e) for e in (old or {}).get("evidence") or [] if isinstance(e, dict)}
    out = []
    for e in new.get("evidence") or []:
        if isinstance(e, dict) and SK.digest(e) not in had and e.get("text_hash") != new.get("text_hash"):
            out.append(f"note: {new['key']['key']}: the {e.get('kind')} by {e.get('by')!r} has the same pads and pins "
                       "but another drawing (silk, fab, courtyard, text or symbol graphics): merged as evidence; it "
                       "does not confirm the record")
    return out


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


def _outside(rel, read_old, meta):
    """(problem, maintainer-only problem) of a changed path that is not a record or an index shard."""
    if rel.startswith(("parts/", "index/")) or rel in ("parts", "index"):
        return (f"{rel}: not a store path (parts/<ab>/<key_id>.json or index/<ab>.json): verify cannot classify "
                "this change, so it fails"), None
    if rel == "store.json":
        o = read_old(rel) if read_old else None
        try:
            o = json.loads(o) if o else {}
        except ValueError:
            o = {}
        o = o if isinstance(o, dict) else {}
        keys = sorted(k for k in set(o) | set(meta) if k != "updated" and o.get(k) != meta.get(k))
        return None, (f"store.json: {MAINTAINER_ONLY}: {', '.join(keys)} changed" if keys else None)
    why = ("the verify workflow and the repository's settings" if rel.startswith(".github/") else
           "the withdrawn parts" if rel == "withdrawn.json" or rel.startswith("withdrawn/") else
           "the dispute log" if rel == "disputes.json" else
           "the ecadlib the verify workflow installs" if rel == VERSION_FILE else
           "a licence file" if rel in LEGAL or rel.startswith("LICENSE") else
           "the store's own documentation" if "/" not in rel else
           "outside parts/ and index/")
    return None, f"{rel}: {MAINTAINER_ONLY} ({why}: every file outside parts/ and index/ is a maintainer's)"


def _root_rel(root, path):
    """A --changed path (absolute, or relative to the current folder, as a shell completes it) as a path relative to
    the store root; Unusable when it is not inside the root. Links are not followed (a path names a file in the tree)."""
    p = Path(path)
    p = p if p.is_absolute() else Path.cwd() / p
    for a, b in ((os.path.abspath(p), os.path.abspath(root)), (os.path.realpath(p), os.path.realpath(root))):
        try:
            rel = Path(a).relative_to(b).as_posix()
        except ValueError:
            continue
        if rel not in ("", ".") and ".." not in rel.split("/"):
            return rel
    raise Unusable(f"--changed {path}: not a file inside the store {root} (give paths as your shell sees them: "
                   "relative to the current folder, or absolute)")


def verify(root, changed=None, base=None, old=None, all_records=False, maintainer=False, author=None, log=print):
    """The pull-request / nightly check (module doc). -> problems ([] = pass). Prints one line per problem."""
    root = Path(root)
    meta = _meta(root)
    wd = _withdrawn(root)
    trusted, maint = _people(meta)
    read_old, diff = _base_reader(root, base, old)
    if changed:
        diff = sorted({_root_rel(root, c) for c in changed})
    probs, mo, notes, failing, names = [], [], [], {}, set()
    every = sorted(r for r in _files(root) if re.fullmatch(r"parts/[^/]+/[^/]+\.json", r))
    todo = every if all_records or diff is None else [r for r in diff if r.startswith("parts/")]
    for rel in todo:
        f = root / rel
        new, why = _load(f) if f.is_file() and not f.is_symlink() else (None, None)
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
        if diff is not None and rel in diff and why is None and (new is None or isinstance(new, dict)):
            # no base (an explicit list alone): the record counts as new, every evidence entry as contributed
            b, m, bys = _against_base(rel, new, read_old(rel) if read_old else None, author, wd)
            names |= bys
            mine += b
            mo += [f"{rel}: {MAINTAINER_ONLY}: {x}" for x in m]
            if new is not None and read_old is not None and not b and not mine:
                try:
                    old_rec = json.loads(read_old(rel) or "null")
                except ValueError:
                    old_rec = None
                if level_of(new, trusted, maint) == "confirmed" and (
                        not isinstance(old_rec, dict) or level_of(old_rec, trusted, maint) != "confirmed"):
                    notes.append(f"{rel}: note: this change makes {new['key']['key']} confirmed (used without a "
                                 "person looking): check who built it")
                notes += [f"{rel}: {x}" for x in other_drawing_notes(old_rec if isinstance(old_rec, dict) else None,
                                                                     new)]
        probs += [f"{rel}: {x}" for x in mine]
        if mine and isinstance(new, dict) and isinstance(new.get("key"), dict):
            failing[new["key"].get("key")] = mine[0]
    if len(names) > 1:
        mo.append(f"new evidence from several contributors in one change ({', '.join(sorted(names))}): each "
                  "contributor opens their own pull request")
    github = False
    for rel in diff or []:
        if _REC_PATH.fullmatch(rel) or _IDX_PATH.fullmatch(rel):
            continue
        github = github or rel == ".github" or rel.startswith(".github/")
        p, m = _outside(rel, read_old, meta)
        probs += [p] if p else []
        mo += [m] if m else []
    probs += index(root, check=True, log=log)
    probs += _tree_problems(root, meta)
    if base:
        probs += _git_mode_problems(root)
    if maintainer:
        notes += [f"{m} (allowed: --maintainer)" for m in mo]
    else:
        probs += mo
    if github:
        notes.insert(0, "WARNING: this change touches .github/. A pull_request check runs the pull request's OWN copy "
                        "of the workflows, so its result proves nothing about this change: a maintainer must review "
                        "every .github/ change by hand (CODEOWNERS and branch protection make that required).")
    for line in notes:
        log(_safe(line))
    for line in probs:
        log(_safe(line))
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
    p.add_argument("--author", help="your GitHub name: all new evidence must be in it (contributors)")
    p.add_argument("--maintainer", action="store_true",
                   help="a maintainer's add: also codes, sign-offs, several builds, withdrawn parts, pointer records")
    p.add_argument("--dispute", action="store_true", help="with --maintainer: on a conflict, mark the store's record "
                   "disputed")
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
    p.add_argument("--changed", nargs="+", metavar="FILE",
                   help="the changed files, as paths relative to the current folder or absolute (each must be inside "
                        "ROOT; e.g. from the store's folder: parts/ab/<key_id>.json); default: from --base / --old, "
                        "else all")
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
            probs = add(a.root, a.records, a.dispute, a.at, maintainer=a.maintainer, author=a.author)
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
