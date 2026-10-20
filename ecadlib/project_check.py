"""Project drift and freeze: has the library changed under a project since its parts were placed? (#105 phase 3)

    ecadlib project check BOARD.kicad_pro [--lib DIR ...] [-o REPORT] [--json]
    ecadlib project check BOARD.PrjPcb --workspace URL [--library LIBFOLDER] [-o REPORT] [--json]
    ecadlib project freeze BOARD.kicad_pro [--name NICK] [--dry-run]
    ecadlib project scan BOARD.kicad_pro|BOARD.PrjPcb|BOM.csv --library LIBFOLDER

KiCad keeps a copy of every symbol a schematic uses (lib_symbols in the .kicad_sch) and of every footprint on the
board (the .kicad_pcb). `check` fingerprints each copy (ecadlib.fingerprint; a board footprint is turned back into
its library frame first, fingerprint.unplace) and compares it with the part in the library today, through the
project's sym-lib-table / fp-lib-table, KiCad's global tables and any --lib folder. Each difference is classified as
change control classifies a library update (ecadlib.change_diff), so "Update symbols from library" is never a blind
step: the report says what it would change and which references use the part. Exit code 1 when a change is one the
[change] policy holds (identity, electrical, geometry-major by default).

`freeze` copies the exact parts the project uses (the schematic's cached symbols, the board's footprints in their
library frame) into project libraries <project>-frozen.kicad_sym / .pretty, adds them to the project's library
tables and points the schematic and board at them, so no library update can reach the project again. The files it
rewrites are backed up under .ecadlib/freeze-<time>/ first. freeze_kicad() is shared with the project pack (#109).
3D models are not copied (they keep their paths).

Altium: components placed from an Altium 365 workspace carry the revision they were placed at (RevisionGUID).
`check` asks the workspace (NEXAR_TOKEN) for each component's current revision and lists the out-of-date ones;
with --library (an ecadlib library that pushed them) it adds the classified changes between the two revisions from
the ledger when both are recorded there.

`scan` records in an ecadlib library's usage index (library.usage.json) which of its parts a project or BOM uses, at
which revision, so a held change says "used in 3 projects"."""
import argparse, json, os, re, shutil, sys
from pathlib import Path

from . import change_diff as CD, change_gate as CG, fingerprint as FP, sexpr
from .bom_inputs import _load_sch

KICAD_EXT = (".kicad_pro", ".kicad_sch", ".kicad_pcb")
ALTIUM_EXT = (".prjpcb", ".schdoc")
_SKIP_PARAMS = ("Reference", "Footprint", "ki_keywords", "ki_fp_filters", "ki_description", "ki_locked")


class ProjectError(ValueError):
    pass


# ---------------------------------------------------------------- the KiCad project
def kicad_paths(path):
    """A .kicad_pro / .kicad_sch / .kicad_pcb or the project folder -> {dir, name, sch, pcb} (sch / pcb may be None)."""
    p = Path(path)
    if p.is_dir():
        pros = sorted(p.glob("*.kicad_pro"))
        if not pros:
            raise ProjectError(f"{p}: no .kicad_pro in this folder")
        p = pros[0]
    if p.suffix.lower() not in KICAD_EXT:
        raise ProjectError(f"{p.name}: give a .kicad_pro, .kicad_sch, .kicad_pcb or the project folder")
    sch, pcb = p.with_suffix(".kicad_sch"), p.with_suffix(".kicad_pcb")
    out = {"dir": p.parent, "name": p.stem, "sch": sch if sch.is_file() else None, "pcb": pcb if pcb.is_file() else None}
    if not out["sch"] and not out["pcb"]:
        raise ProjectError(f"{p.name}: no {sch.name} or {pcb.name} next to it")
    return out


def sheet_files(root):
    """The root schematic and every sub-sheet file it uses (each once)."""
    out, todo = [], [Path(root)]
    while todo:
        f = todo.pop(0)
        if f.resolve() in {x.resolve() for x in out}:
            continue
        out.append(f)
        for sh in sexpr.find(_load_sch(f), "sheet"):
            props = {str(x[1]): str(x[2]) for x in sexpr.find(sh, "property") if len(x) > 2}
            sub = props.get("Sheetfile") or props.get("Sheet file")
            if not sub:
                continue
            target = f.parent / sub
            if not target.is_file():           # drawn on Windows / macOS: the case may differ (as bom_inputs reads it)
                from .altium_project import _find_file
                target = _find_file(f.parent, sub) or target
            if target.is_file():
                todo.append(target)
        if len(out) > 2000:
            raise ProjectError(f"{Path(root).name}: more than 2000 sheet files")
    return out


def _ref(sym):
    for proj in sexpr.find(sexpr.first(sym, "instances") or [], "project"):
        for p in sexpr.find(proj, "path"):
            r = sexpr.first(p, "reference")
            if r:
                return str(r[1])
    return next((str(x[2]) for x in sexpr.find(sym, "property") if len(x) > 2 and str(x[1]) == "Reference"), "")


def schematic_symbols(files):
    """{cache key: {"node": cached symbol, "lib_id": .., "refs": [..]}} over the sheet files. The cache key is the
    instance's lib_name when it has one (a symbol edited in the schematic), else its lib_id."""
    cache, uses = {}, {}
    for f in files:
        tree = _load_sch(f)
        for s in sexpr.find(sexpr.first(tree, "lib_symbols") or [], "symbol"):
            cache.setdefault(str(s[1]), s)
        for s in sexpr.find(tree, "symbol"):
            lid = sexpr.first(s, "lib_id")
            if not lid:
                continue
            ln = sexpr.first(s, "lib_name")
            key = str(ln[1]) if ln else str(lid[1])
            u = uses.setdefault(key, {"lib_id": str(lid[1]), "refs": set(), "edited": bool(ln), "inst_fp": {}})
            r = _ref(s)
            if r and not r.startswith("#"):
                u["refs"].add(r)
                fp = next((str(x[2]) for x in sexpr.find(s, "property") if len(x) > 2 and str(x[1]) == "Footprint"), None)
                if fp is not None:             # the footprint this placed symbol puts on the board
                    u["inst_fp"][r] = "" if fp == "~" else fp
    return {k: {"node": cache.get(k), "lib_id": u["lib_id"], "refs": sorted(u["refs"], key=_natkey),
                "edited": u["edited"], "inst_fp": u["inst_fp"]} for k, u in uses.items()}


def _natkey(s):
    return [int(t) if t.isdecimal() else t for t in re.split(r"(\d+)", s)]


def board_footprints(pcb, keep_quotes=False):
    """[{"fpid", "ref", "side", "node"}] for every footprint on the board."""
    tree = sexpr.parse(Path(pcb).read_text(encoding="utf-8"), keep_quotes=keep_quotes)
    out = []
    for f in sexpr.find(tree, "footprint"):
        ref = next((str(x[2]) for x in sexpr.find(f, "property") if len(x) > 2 and str(x[1]) == "Reference"), "") or \
            next((str(x[2]) for x in sexpr.find(f, "fp_text") if len(x) > 2 and str(x[1]) == "reference"), "")
        side = "bottom" if str((sexpr.first(f, "layer") or [None, "F.Cu"])[1]).startswith("B.") else "top"
        out.append({"fpid": str(f[1]), "ref": ref, "side": side, "node": f})
    return out


# ---------------------------------------------------------------- library tables
def _kicad_versions():
    return ("10.0", "9.0", "8.0", "7.0", "6.0")


def _default_dirs():
    """KiCad's own library folders when KICAD*_SYMBOL_DIR / _FOOTPRINT_DIR are not set (the installers' paths)."""
    if sys.platform == "win32":
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad" / v / "share" / "kicad"
                 for v in _kicad_versions()]
    elif sys.platform == "darwin":
        roots = [Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")]
    else:
        roots = [Path("/usr/share/kicad"), Path("/usr/local/share/kicad")]
    root = next((r for r in roots if r.is_dir()), None)
    return {"symbols": root / "symbols" if root else None, "footprints": root / "footprints" if root else None}


def expand(uri, projdir, env=None):
    """${KIPRJMOD} and ${VAR} in a library table uri -> a path, or None when a variable is unknown."""
    env = dict(os.environ if env is None else env)
    env.setdefault("KIPRJMOD", str(projdir))
    d = _default_dirs()
    for v in ("", "6", "7", "8", "9", "10"):
        for kind, key in (("SYMBOL", "symbols"), ("FOOTPRINT", "footprints")):
            if d[key]:
                env.setdefault(f"KICAD{v}_{kind}_DIR", str(d[key]))
    bad = []

    def sub(m):
        if m.group(1) in env:
            return env[m.group(1)]
        bad.append(m.group(1))
        return ""
    out = re.sub(r"\$\{([A-Za-z0-9_]+)\}", sub, str(uri))
    return None if bad else Path(out)


def _table(path):
    try:
        t = sexpr.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [(str((sexpr.first(l, "name") or ["", ""])[1]), str((sexpr.first(l, "uri") or ["", ""])[1]),
             str((sexpr.first(l, "type") or ["", "KiCad"])[1])) for l in sexpr.find(t, "lib")]


def _global_table(kind):
    base = Path(os.environ["APPDATA"]) / "kicad" if sys.platform == "win32" and os.environ.get("APPDATA") else \
        Path.home() / "Library" / "Preferences" / "kicad" if sys.platform == "darwin" else \
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kicad"
    for v in _kicad_versions():
        f = base / v / kind
        if f.is_file():
            return f
    return None


def lib_tables(projdir, extra=(), use_global=True):
    """{"sym": {nickname: path}, "fp": {nickname: path}} from the project's tables, KiCad's global tables and --lib
    folders (every .kicad_sym, .kicad_symdir and .pretty in them, named by file stem). The project's table wins."""
    out = {"sym": {}, "fp": {}}
    for kind, table in (("sym", "sym-lib-table"), ("fp", "fp-lib-table")):
        g = _global_table(table) if use_global else None
        for f in [g, Path(projdir) / table]:
            for name, uri, typ in _table(f) if f else []:
                if typ.lower() in ("kicad", "") and name:
                    p = expand(uri, projdir)
                    if p is not None:
                        out[kind][name] = p
    for d in extra:
        d = Path(d)
        for f in sorted(d.glob("*.kicad_sym")) + sorted(d.glob("*.kicad_symdir")):
            out["sym"].setdefault(f.name.rsplit(".", 1)[0], f)
        for f in sorted(d.glob("*.pretty")):
            out["fp"].setdefault(f.stem, f)
    return out


# ---------------------------------------------------------------- library parts and fingerprints
class Libraries:
    """Reads library symbols and footprints once each."""

    def __init__(self, tables):
        self.tables, self._files = tables, {}

    def _syms(self, f):
        if f not in self._files:
            self._files[f] = {str(s[1]): s for s in sexpr.find(sexpr.parse(Path(f).read_text(encoding="utf-8")), "symbol")}
        return self._files[f]

    def symbol(self, lib_id):
        """lib_id -> (extends chain of nodes, None) or (None, why)."""
        nick, _, name = lib_id.partition(":")
        path = self.tables["sym"].get(nick)
        if path is None:
            return None, f"library {nick} is not in the project's or KiCad's symbol library table"
        if path.is_dir() or path.suffix == ".kicad_symdir":
            f = path / f"{name}.kicad_sym"
            if not f.is_file():
                return None, f"{name} is no longer in library {nick}"
            syms = dict(self._syms(f))
        elif path.is_file():
            syms = self._syms(path)
        else:
            return None, f"library {nick} ({path}) is not on this computer"
        if name not in syms:
            return None, f"{name} is no longer in library {nick}"
        chain = [syms[name]]
        while sexpr.first(chain[-1], "extends") and len(chain) < 8:
            parent = str(sexpr.first(chain[-1], "extends")[1])
            if parent not in syms and path.is_dir():
                syms.update(self._syms(path / f"{parent}.kicad_sym"))
            if parent not in syms:
                return None, f"{name} extends {parent}, which is not in library {nick}"
            chain.append(syms[parent])
        return chain, None

    def fp_key(self, fpid, near=None):
        """A footprint id with its nickname resolved to the library folder it names: two nicknames for one .pretty
        (demo:SOT-23 and mylib:SOT-23 through the project's fp-lib-table) are the same footprint. near: the symbol
        library the id was read from; a nickname this project does not define is then the <nick>.pretty beside it
        (kicad-export writes <lib>.kicad_sym and <lib>.pretty together, and its symbols name <lib>:...)."""
        nick, sep, name = str(fpid or "").partition(":")
        if not sep:
            return ("", nick)
        path = self.tables["fp"].get(nick)
        if path is None and near is not None:
            beside = Path(near).parent / f"{nick}.pretty"
            path = beside if beside.is_dir() else None
        try:
            return (str(Path(path).resolve()) if path is not None else f"nick:{nick}", name)
        except OSError:
            return (f"nick:{nick}", name)

    def footprint(self, fpid):
        """fpid -> (tree, None) or (None, why)."""
        nick, _, name = fpid.partition(":")
        path = self.tables["fp"].get(nick)
        if path is None:
            return None, f"library {nick} is not in the project's or KiCad's footprint library table"
        f = path / f"{name}.kicad_mod"
        if not f.is_file():
            return None, f"{name} is no longer in library {nick}" if path.is_dir() else f"library {nick} ({path}) is not on this computer"
        return sexpr.parse(f.read_text(encoding="utf-8")), None


def symbol_fp(node_or_chain, name):
    """A symbol (cached in a schematic, or a library extends chain) -> its fingerprint, named `name`."""
    chain = node_or_chain if isinstance(node_or_chain, list) and node_or_chain and isinstance(node_or_chain[0], list) \
        else [node_or_chain]
    top = list(chain[0])
    top[1] = name                              # the cache calls it Lib:Name, the library Name
    sym, props = FP.kicad_symbol([top] + list(chain[1:]))
    return FP.build(name, sym, params={k: v for k, v in props.items() if k not in _SKIP_PARAMS}, tool="kicad")


def _no_instance_texts(tree):
    """The footprint without its reference and value texts: on a board they belong to the instance (R7, 10k, moved
    by hand), so they are not compared with the library."""
    keep = lambda c: not (isinstance(c, list) and len(c) > 1 and ((c[0] == "property" and str(c[1]) in ("Reference", "Value"))
                                                                   or (c[0] == "fp_text" and str(c[1]) in ("reference", "value"))))
    return [c for c in tree if keep(c)]


def _fp_field(node_or_chain):
    """A symbol's Footprint field (the footprint it places, Lib:Name), from a cached copy or a library extends chain."""
    chain = node_or_chain if isinstance(node_or_chain, list) and node_or_chain and isinstance(node_or_chain[0], list) \
        else [node_or_chain]
    for node in chain:                         # the derived symbol's own field first, then its parents'
        for p in sexpr.find(node, "property"):
            if len(p) > 2 and str(p[1]) == "Footprint":
                return "" if str(p[2]) == "~" else str(p[2])
    return ""


def _footprint_field(cached, chain, libs, inst=None, board=None, near=None):
    """The symbol's Footprint field, schematic copy -> library: another footprint is what "Update symbols from library"
    places (geometry-major), and one the library no longer has is held whatever the policy says (marked "missing").
    Nicknames are resolved to their library folder first (another nickname for the same .pretty is no change).
    inst: {ref: the placed symbol's own Footprint property}; board: {ref: the board footprint's lib_id}: each one that
    is not the library part's footprint is a geometry-major change too (the part on the board is not the one the
    library releases)."""
    a, b = _fp_field(cached), _fp_field(chain)
    out = []
    if b:
        kb = libs.fp_key(b, near)
        for what, got in (("its Footprint property", inst or {}), ("the board", board or {})):
            odd = {}
            for ref, fp in sorted(got.items(), key=lambda x: _natkey(x[0])):
                if not fp.strip() or libs.fp_key(fp, near) != kb:     # an empty one places no footprint at all
                    odd.setdefault(fp.strip(), []).append(ref)
            for fp, refs in odd.items():
                places = f"{CD._f(fp)} ({what})" if fp else f"no footprint ({what} is empty)"
                out.append({"class": "geometry-major", "area": "symbol", "what": "footprint", "old": fp, "new": b,
                            "text": f"{', '.join(refs)} {'place' if len(refs) > 1 else 'places'} {places}, "
                                    f"but the library part's footprint is {CD._f(b)}"})
    if a != b and libs.fp_key(a, near) != libs.fp_key(b, near):
        out.append({"class": "geometry-major", "area": "symbol", "what": "footprint", "old": a, "new": b,
                    "text": f"footprint reference (the symbol's Footprint field) changed from {CD._f(a)} to {CD._f(b)}"})
    if b and ":" in b:
        folder, name = libs.fp_key(b, near)
        if folder and not folder.startswith("nick:") and Path(folder).is_dir() and \
                not (Path(folder) / f"{name}.kicad_mod").is_file():
            why = f"{name} is no longer in library {b.partition(':')[0]}"
            out.append({"class": "geometry-major", "area": "symbol", "what": "footprint", "old": a, "new": b,
                        "missing": True, "text": f"the symbol's footprint {b} is not in the library ({why})"})
    return out


def footprint_fp(tree, name, instance=False):
    """instance=True: compare as a placed part (reference and value texts left out, on both sides)."""
    tree = _no_instance_texts(tree) if instance else tree
    return FP.build(name, None, *FP.kicad_footprint(tree), tool="kicad", footprint_ref=name)


# ---------------------------------------------------------------- check (KiCad)
def _row(kind, ident, refs, changes=None, state="same", why="", pol=None, action=None, **extra):
    changes = changes or []
    act = action or (CG.decide(changes, pol or CG.POLICY)["action"] if changes else "pass")
    return {"kind": kind, "id": ident, "refs": refs, "state": state, "why": why, "worst": CD.worst(changes) if changes else None,
            "action": act, "summary": CD.summary(changes) if changes else "", "changes": [{"class": c["class"], "text": c["text"]}
                                                                                       for c in changes], **extra}


def check_kicad(path, libs=(), rules=None, use_global=True):
    """The project's symbols and footprints against the library today -> {project, rows, counts, held}."""
    kp = kicad_paths(path)
    pol = CG.policy(rules, kp["dir"])
    L = Libraries(lib_tables(kp["dir"], libs, use_global))
    rows = []
    board_ids = {f["ref"]: f["fpid"] for f in board_footprints(kp["pcb"])} if kp["pcb"] else {}
    if kp["sch"]:
        for key, u in sorted(schematic_symbols(sheet_files(kp["sch"])).items()):
            if not u["refs"]:                  # power symbols, flags: nothing to place
                continue
            name = u["lib_id"].partition(":")[2] or u["lib_id"]
            if u["node"] is None:
                rows.append(_row("symbol", u["lib_id"], u["refs"], state="unreadable", why="no cached copy in the schematic"))
                continue
            chain, why = L.symbol(u["lib_id"])
            if chain is None:
                rows.append(_row("symbol", u["lib_id"], u["refs"], state="not in library", why=why))
                continue
            try:
                ch = CD.diff(symbol_fp(u["node"], name), symbol_fp(chain, name))
            except (ValueError, KeyError, IndexError, TypeError) as e:
                rows.append(_row("symbol", u["lib_id"], u["refs"], state="unreadable", why=f"{type(e).__name__}: {e}"))
                continue
            ch += _footprint_field(u["node"], chain, L, u.get("inst_fp"), {r: board_ids[r] for r in u["refs"] if r in board_ids},
                                   near=L.tables["sym"].get(u["lib_id"].partition(":")[0]))
            note = "edited in the schematic (lib_name " + key + ")" if u["edited"] else ""
            act = "hold" if any(c.get("missing") for c in ch) else None
            rows.append(_row("symbol", u["lib_id"], u["refs"], ch, "changed" if ch else "same", note, pol, act))
    if kp["pcb"]:
        groups = {}
        for f in board_footprints(kp["pcb"]):
            name = f["fpid"].partition(":")[2] or f["fpid"]
            try:
                fp = footprint_fp(FP.unplace(f["node"]), name, instance=True)
            except (ValueError, KeyError, IndexError, TypeError) as e:
                rows.append(_row("footprint", f["fpid"], [f["ref"]], state="unreadable", why=f"{type(e).__name__}: {e}"))
                continue
            g = groups.setdefault((f["fpid"], fp["hashes"]["digest"]), {"fp": fp, "refs": []})
            g["refs"].append(f["ref"])
        for (fpid, _), g in sorted(groups.items()):
            refs = sorted(g["refs"], key=_natkey)
            tree, why = L.footprint(fpid)
            if tree is None:                   # a footprint gone from a library that is there: an update cannot find
                rows.append(_row("footprint", fpid, refs, state="not in library", why=why,   # it: held
                                 action="hold" if why.endswith(f"is no longer in library {fpid.partition(':')[0]}") else None))
                continue
            ch = CD.diff(g["fp"], footprint_fp(tree, g["fp"]["part"], instance=True))
            rows.append(_row("footprint", fpid, refs, ch, "changed" if ch else "same", "", pol))
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    held = [r for r in rows if r["action"] == "hold"]
    return {"project": str(kp["dir"] / kp["name"]), "tool": "kicad", "rows": rows, "counts": counts, "held": len(held),
            "policy": {k: pol[k] for k in CG.CLASS_KEY.values()}}


# ---------------------------------------------------------------- check (Altium 365)
def _guid(s):
    return re.sub(r"[{}\s]", "", str(s or "")).lower()


VAULT_REVISION = re.compile(r"(?i)^([A-Z]{2,5}-\d{3,}-\d+)-\d+$")     # CMP-003-000000-1: item CMP-003-000000, revision 1


def _base_id(did):
    """A placed component's workspace item: an Altium vault item-revision id (CMP-003-000000-1) loses its revision
    number; any other name is the item's own (TPS7A02-33, AMS1117-3.3 keep their -33 / -3.3)."""
    did = str(did or "")
    m = VAULT_REVISION.match(did)
    return m.group(1) if m else did


def check_altium(path, client, ws, library=None, rules=None):
    """Components placed from the workspace -> rows: out of date (the workspace has a newer revision), current, not
    found. library: an ecadlib library folder whose ledger recorded the workspace revisions it pushed (publish lines)
    or pulled (`a365 pull` release lines): their changes are classified and the [change] policy decides. An out-of-date
    component whose changes the ledger cannot list gets the [change] unlisted action (default hold: the check fails)."""
    from . import altium_project as AP, ledger as LG
    proj = AP.load_project(path)
    lg = LG.Ledger(library) if library and LG.Ledger(library).exists() else None
    pol = CG.policy(rules, CG.lib_folder(library) if library else Path(path).parent)
    by_rev = {}
    if lg:                                     # A365 revision id -> (part, ledger revision): what ecadlib pushed or pulled
        for e in lg.lines():
            rid = e.get("event") == "publish" and isinstance(e.get("detail"), dict) and e["detail"].get("a365_revision")
            if e.get("event") in LG.RELEASES and e.get("a365_revision"):
                rid = e["a365_revision"]
            if rid and e.get("part") and e.get("revision"):
                by_rev[_guid(rid)] = (e["part"], e["revision"])
    groups, dblib = {}, {}
    for c in proj["components"]:
        if not (c.get("vault_guid") or c.get("item_guid")) or not c.get("revision_guid"):
            src = c.get("source_library") or ""
            if src.lower().endswith((".dblib", ".svndblib")) or c.get("database_table"):   # a DbLib's part: listed
                key = (c.get("design_item_id") or c.get("libref") or "", src, c.get("database_table") or "")
                dblib.setdefault(key, []).append(c.get("designator") or "")
            continue
        k = (_base_id(c.get("design_item_id")) or c.get("libref") or "", _guid(c["revision_guid"]))
        groups.setdefault(k, []).append(c.get("designator") or "")
    rows, cache = [], {}

    def find(n):
        if n not in cache:
            try:
                cache[n] = client.find(ws, n)
            except Exception as e:             # one part's lookup never stops the list
                cache[n] = {"error": f"{type(e).__name__}: {e}"}
        return cache[n]

    for (placed, rev), refs in sorted(groups.items()):
        refs = sorted(r for r in refs if r)
        name, node, note = placed, find(placed), ""
        m = re.match(r"^(.+)-\d+$", placed)
        if (not node or node.get("error")) and m:   # 'CAP-1': item CAP, revision 1 (only when there is no CAP-1)
            alt = find(m.group(1))
            if alt and not alt.get("error"):
                name, node = m.group(1), alt
                note = f"placed as {placed}: the workspace has no {placed}, so its -{placed.rsplit('-', 1)[1]} is read as a revision number; "
        if not node or node.get("error"):
            rows.append(_row("component", name, refs, state="not found", why=(node or {}).get("error") or
                             f"no component named {name} in the workspace", placed=rev))
            continue
        latest = _guid(((node.get("revision") or {}).get("revisionId")) or (node.get("revision") or {}).get("id"))
        if latest == rev:
            rows.append(_row("component", name, refs, why=note.rstrip("; "), placed=rev, latest=latest))
            continue
        changes, why, listed = [], note + "the workspace has a newer revision", False
        a, b = by_rev.get(rev), by_rev.get(latest)
        if a and b and a[0] == b[0]:
            old, new = lg.revision(*a), lg.revision(*b)
            if old and new:
                changes = CD.diff(lg.fingerprint(old), lg.fingerprint(new))
                why, listed = f"ledger r{a[1]} -> r{b[1]}", True
        if not listed:
            why += (" (the library's ledger does not record both revisions, so the changes are not listed)" if lg else
                    " (no --library: the changes are not listed)")
        rows.append(_row("component", name, refs, changes, "out of date", why, pol,
                         action=None if listed else pol["unlisted"], placed=rev, latest=latest))
    for (did, src, table), refs in sorted(dblib.items()):   # no workspace revision to compare: said, never skipped
        rows.append(_row("component", did or "(no Design Item ID)", sorted(r for r in refs if r), state="not checked",
                         why=f"placed from DbLib {src or '?'}" + (f" (table {table})" if table else "") +
                         ": it carries no workspace revision, so this check cannot compare it (the DbLib sync "
                         "publishes only released revisions)"))
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"project": proj["path"], "tool": "altium", "rows": rows, "counts": counts,
            "held": sum(r["action"] == "hold" for r in rows), "out_of_date": counts.get("out of date", 0)}


# ---------------------------------------------------------------- freeze (KiCad; shared with the project pack, #109)
def _unique(names):
    """{key: (nickname, name)} -> {key: new name}: Name, or Nick_Name where two libraries share a name."""
    seen = {}
    for k, (_, n) in names.items():
        seen[n] = seen.get(n, 0) + 1
    return {k: (n if seen[n] == 1 else f"{nick}_{n}") for k, (nick, n) in names.items()}


def _clean_fp(node, name):
    """A board footprint (already unplaced) -> a library footprint: the instance's nets, sheet path and reference go."""
    drop_top, drop_pad = {"path", "sheetname", "sheetfile", "uuid", "tstamp", "locked"}, {"net", "pinfunction", "pintype"}
    t = [node[0], sexpr.Q(name), ["version", "20241229"], ["generator", sexpr.Q("ecadlib")]]
    for c in node[2:]:
        if isinstance(c, list) and c and c[0] in drop_top:
            continue
        if isinstance(c, list) and c and c[0] == "pad":
            c = [x for x in c if not (isinstance(x, list) and x and x[0] in drop_pad)]
        if isinstance(c, list) and c and c[0] == "property" and len(c) > 2 and str(c[1]) in ("Reference", "Value"):
            c = list(c)
            c[2] = sexpr.Q("REF**" if str(c[1]) == "Reference" else name)
        if isinstance(c, list) and c and c[0] == "fp_text" and len(c) > 2 and str(c[1]) in ("reference", "value"):
            c = list(c)
            c[2] = sexpr.Q("REF**" if str(c[1]) == "reference" else name)
        t.append(c)
    return t


def _rename_units(node, old_base, new_base):
    for u in sexpr.find(node, "symbol"):
        m = re.fullmatch(re.escape(old_base) + r"(_\d+_\d+)", str(u[1]))
        if m:
            u[1] = sexpr.Q(new_base + m.group(1))


def _add_to_table(path, head, nick, uri, dry_run):
    entry = f'  (lib (name "{nick}")(type "KiCad")(uri "{uri}")(options "")(descr "frozen by ecadlib project freeze"))'
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if any(n == nick for n, _, _ in _table(path)):
            return "kept"
        text = text.rstrip()
        if not text.endswith(")"):
            raise ProjectError(f"{path.name}: not a KiCad library table")
        text = text[:-1].rstrip() + "\n" + entry + "\n)\n"
    else:
        text = f"({head}\n  (version 7)\n{entry}\n)\n"
    if not dry_run:
        path.write_text(text, encoding="utf-8")
    return "added"


def freeze_kicad(path, nick=None, dry_run=False, out_dir=None, rewrite=True):
    """Copy the parts a KiCad project uses into project libraries and point the project at them.
    -> {nick, symbols: {old: new}, footprints: {old: new}, files: [written], backup}.
    out_dir: where the libraries go (default the project folder; the pack uses another). rewrite=False writes the
    libraries only and leaves the schematic, board and tables alone."""
    from . import ledger as LG
    kp = kicad_paths(path)
    pdir = Path(kp["dir"])
    out_dir = Path(out_dir) if out_dir else pdir
    nick = nick or f"{kp['name']}-frozen"
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", nick):
        raise ProjectError(f"{nick!r}: a library nickname takes letters, digits and _ . + - only")
    sheets = sheet_files(kp["sch"]) if kp["sch"] else []
    trees = {f: sexpr.parse(f.read_text(encoding="utf-8"), keep_quotes=True) for f in sheets}
    cache, users = {}, {}
    for f, t in trees.items():
        for s in sexpr.find(sexpr.first(t, "lib_symbols") or [], "symbol"):
            cache.setdefault(str(s[1]), s)
        for s in sexpr.find(t, "symbol"):
            if sexpr.first(s, "lib_id"):
                ln = sexpr.first(s, "lib_name")
                users.setdefault(str(ln[1]) if ln else str(sexpr.first(s, "lib_id")[1]), True)
    keys = [k for k in cache if k in users]
    sym_new = _unique({k: (k.partition(":")[0] if ":" in k else "", k.rpartition(":")[2]) for k in keys})
    board = sexpr.parse(kp["pcb"].read_text(encoding="utf-8"), keep_quotes=True) if kp["pcb"] else None
    variants, fp_of = {}, {}                   # (fpid, digest) -> (unplaced tree, name); id(node) -> new fpid
    if board is not None:
        for f in sexpr.find(board, "footprint"):
            fpid = str(f[1])
            base = fpid.rpartition(":")[2]
            u = FP.unplace(f)
            d = footprint_fp(u, base, instance=True)["hashes"]["digest"]
            variants.setdefault((fpid, d), u)
            fp_of[id(f)] = (fpid, d)
    names = {}
    for fpid, d in variants:
        names.setdefault(fpid, []).append(d)
    labels = _unique({fpid: (fpid.partition(":")[0], fpid.rpartition(":")[2]) for fpid in names})
    fp_new = {}
    for fpid, ds in names.items():
        for n, d in enumerate(ds):              # the same footprint id placed in two different versions: two parts
            fp_new[(fpid, d)] = labels[fpid] if n == 0 else f"{labels[fpid]}_v{n + 1}"
    written = []
    if keys:
        lib = ["kicad_symbol_lib", ["version", "20241209"], ["generator", sexpr.Q("ecadlib")]]
        for k in keys:
            node = sexpr.parse(sexpr.dumps(cache[k]), keep_quotes=True)
            node[1] = sexpr.Q(sym_new[k])
            _rename_units(node, k.rpartition(":")[2], sym_new[k])
            if rewrite:                        # the frozen symbol places the frozen footprint, as the schematic will
                fps = {fpid: f"{nick}:{fp_new[(fpid, ds[0])]}" for fpid, ds in names.items()}
                for prop in sexpr.find(node, "property"):
                    if len(prop) > 2 and str(prop[1]) == "Footprint" and str(prop[2]) in fps:
                        prop[2] = sexpr.Q(fps[str(prop[2])])
            lib.append(node)
        f = out_dir / f"{nick}.kicad_sym"
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            f.write_text(sexpr.dumps(lib) + "\n", encoding="utf-8")
        written.append(f)
    if variants:
        d = out_dir / f"{nick}.pretty"
        for key, u in variants.items():
            f = d / f"{fp_new[key]}.kicad_mod"
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)
                f.write_text(sexpr.dumps(_clean_fp(u, fp_new[key])) + "\n", encoding="utf-8")
            written.append(f)
    backup = None
    if rewrite:
        backup = pdir / ".ecadlib" / f"freeze-{LG.now().replace(':', '').replace('+0000', 'Z')[:17]}"
        fps = {fpid: f"{nick}:{fp_new[(fpid, ds[0])]}" for fpid, ds in names.items()}
        changed = {}
        for f, t in trees.items():
            if _point_schematic(t, nick, sym_new, fps):
                changed[f] = t
        if board is not None:
            for fnode in sexpr.find(board, "footprint"):
                fnode[1] = sexpr.Q(f"{nick}:{fp_new[fp_of[id(fnode)]]}")
            changed[kp["pcb"]] = board
        rel = os.path.relpath(out_dir, pdir).replace("\\", "/")
        prefix = "${KIPRJMOD}" + ("" if rel == "." else "/" + rel)
        tables = []
        if keys:
            tables.append((pdir / "sym-lib-table", "sym_lib_table", f"{prefix}/{nick}.kicad_sym"))
        if variants:
            tables.append((pdir / "fp-lib-table", "fp_lib_table", f"{prefix}/{nick}.pretty"))
        if not dry_run:
            for f in list(changed) + [p for p, _, _ in tables if p.is_file()]:
                dst = _backup_path(backup, pdir, f)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
            for f, t in changed.items():
                f.write_text(sexpr.dumps(t) + "\n", encoding="utf-8")
        for p, head, uri in tables:
            _add_to_table(p, head, nick, uri, dry_run)
        written += list(changed) + [p for p, _, _ in tables]
    return {"nick": nick, "symbols": {k: f"{nick}:{v}" for k, v in sym_new.items()},
            "footprints": {f"{fpid}" + (f" (version {n + 1})" if n else ""): f"{nick}:{fp_new[(fpid, d)]}"
                           for fpid, ds in names.items() for n, d in enumerate(ds)},
            "files": [str(f) for f in written], "backup": str(backup) if backup and not dry_run and backup.exists() else None,
            "dry_run": dry_run}


def _backup_path(backup, pdir, f):
    """Where freeze backs up f: its path inside the project folder (sub.kicad_sch and sub2/sub.kicad_sch are two
    files); a sheet outside it (Sheetfile "../shared/x.kicad_sch") under _outside/, numbered when the name is taken."""
    try:
        rel = Path(os.path.relpath(f, pdir))
    except ValueError:                         # another drive (Windows)
        rel = None
    if rel is None or rel.parts[:1] == ("..",):
        rel = Path("_outside") / Path(f).name
    dst, n = backup / rel, 1
    while dst.exists():
        n += 1
        dst = dst.with_name(f"{rel.stem}.{n}{rel.suffix}")
    return dst


def _point_schematic(tree, nick, sym_new, fps):
    """Point one sheet at the frozen libraries (in place). -> True when something changed."""
    changed = False
    for s in sexpr.find(sexpr.first(tree, "lib_symbols") or [], "symbol"):
        k = str(s[1])
        if k in sym_new:
            s[1] = sexpr.Q(f"{nick}:{sym_new[k]}")
            _rename_units(s, k.rpartition(":")[2], sym_new[k])
            changed = True
    for s in sexpr.find(tree, "symbol"):
        lid = sexpr.first(s, "lib_id")
        if not lid:
            continue
        ln = sexpr.first(s, "lib_name")
        k = str(ln[1]) if ln else str(lid[1])
        if k in sym_new:
            lid[1] = sexpr.Q(f"{nick}:{sym_new[k]}")
            if ln:
                s.remove(ln)
            changed = True
    for s in list(sexpr.find(tree, "symbol")) + list(sexpr.find(sexpr.first(tree, "lib_symbols") or [], "symbol")):
        for p in sexpr.find(s, "property"):
            if len(p) > 2 and str(p[1]) == "Footprint" and str(p[2]) in fps:
                p[2] = sexpr.Q(fps[str(p[2])])
                changed = True
    return changed


# ---------------------------------------------------------------- scan: the library's usage index
def scan(path, library, variant=None):
    """Which parts of an ecadlib library (its plan.json folder) a project or BOM uses -> the usage index row."""
    from . import altium365 as A365, bom_triage as BT, dedupe as DD, ledger as LG
    folder = CG.lib_folder(library)
    items = CG.read_plan(folder)
    if not items:
        raise ProjectError(f"{library}: no plan.json (give the folder of an ecadlib library)")
    src = Path(path)
    if src.is_dir() or src.suffix.lower() in (".kicad_pro", ".kicad_pcb"):
        kp = kicad_paths(src)
        src = kp["sch"] or kp["pcb"]          # a KiCad project's parts are its schematic's (a board alone: the board's)
    lines = board_bom(src) if src.suffix.lower() == ".kicad_pcb" else BT.read_bom(str(src))[0]
    by_mpn = {}
    for it in items:
        for m in (A365.mpn_of(it), it.get("name") or ""):     # its MPN parameter, and its name (often the MPN too)
            k = DD.mpn_key(m)
            if k:
                by_mpn.setdefault(k, it["name"])
    lg = LG.Ledger(folder)
    parts, unknown = {}, []
    for ln in lines:
        mpn = str(ln.get("mpn") or "")
        refs = [r.strip() for r in str(ln.get("designator") or "").split(",") if r.strip()]
        name = by_mpn.get(DD.mpn_key(mpn or ""))
        if not name:
            if mpn:
                unknown.append(mpn)
            continue
        rec = parts.setdefault(name, {"revision": (lg.head(name) or {}).get("revision"), "designators": []})
        rec["designators"] = sorted(set(rec["designators"]) | set(refs or []), key=_natkey)
    tool = "kicad" if src.suffix.lower() in KICAD_EXT else "altium" if src.suffix.lower() in ALTIUM_EXT else "bom"
    LG.record_usage(folder, CG.project_key(folder, path), parts, tool=tool)
    return {"project": CG.project_key(folder, path), "library": str(folder), "parts": parts, "not_in_library": sorted(set(unknown))}


def board_bom(pcb):
    """BOM lines ({mpn, designator}) from a board's footprints, for a project without a schematic: KiCad copies each
    symbol's fields (MPN, Manufacturer Part Number, ...) onto its footprint."""
    by = {}
    for f in board_footprints(pcb):
        props = {str(x[1]): str(x[2]) for x in sexpr.find(f["node"], "property") if len(x) > 2}
        mpn = FP.identity(props)["mpn"]
        if f["ref"] and not f["ref"].startswith("#"):
            by.setdefault(mpn, []).append(f["ref"])
    if not by:
        raise ProjectError(f"{Path(pcb).name}: no footprints on the board")
    return [{"mpn": m, "designator": ",".join(sorted(r, key=_natkey))} for m, r in sorted(by.items())]


# ---------------------------------------------------------------- report and CLI
def write_report(res, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    lines = [f"# Project check: {res['project']}", "",
             "Each part the project uses, compared with the library today. " +
             ("Out of date: the workspace has a newer revision than the one placed." if res["tool"] == "altium" else
              "Changed: updating it from the library would make these changes."), "",
             "| Part | Used by | State | Policy | What changed |", "|---|---|---|---|---|"]
    order = {"hold": 0, "warn": 1, "pass": 2}
    for r in sorted(res["rows"], key=lambda r: (order.get(r["action"], 3), r["state"] == "same", r["id"])):
        what = r["summary"] or r["why"]
        lines.append(f"| `{r['id']}` | {', '.join(r['refs'][:12])}{' ...' if len(r['refs']) > 12 else ''} | {r['state']} | "
                     f"{r['action'] if r['changes'] else ''} | {what.replace('|', '/')} |")
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out.with_suffix(".md")


def print_check(res):
    rows = [r for r in res["rows"] if r["state"] != "same"]
    same = len(res["rows"]) - len(rows)
    quiet = [r for r in rows if r["state"] == "changed" and r["action"] == "pass"]
    for r in sorted((r for r in rows if r not in quiet), key=lambda r: ({"hold": 0, "warn": 1}.get(r["action"], 2), r["id"])):
        refs = ", ".join(r["refs"][:8]) + (" ..." if len(r["refs"]) > 8 else "")
        print(f"  {r['state']:<14} {r['kind']:<9} {r['id']}  ({refs})")
        for c in r["changes"][:12]:
            print(f"      {c['class']:<15} {c['text']}")
        if len(r["changes"]) > 12:
            print(f"      ... and {len(r['changes']) - 12} more")
        if r["why"]:
            print(f"      {r['why']}")
    if quiet:
        refs = sorted({x for r in quiet for x in r["refs"]}, key=_natkey)
        print(f"  {len(quiet)} part(s) differ only in what the policy lets pass (texts, silkscreen, graphics): "
              + ", ".join(refs[:16]) + (" ..." if len(refs) > 16 else ""))
    word = "current" if res["tool"] == "altium" else "the same as the library"
    print(f"{res['project']}: {same} part(s) {word}, " + ", ".join(f"{n} {k}" for k, n in sorted(res["counts"].items()) if k != "same")
          + (f"; {res['held']} would be held by the [change] policy" if res["held"] else "") if rows else
          f"{res['project']}: all {same} part(s) {word}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib project", description="Project drift, freeze and usage (change control)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="what changed in the library since the project placed its parts")
    c.add_argument("project", help=".kicad_pro / .kicad_sch / .kicad_pcb / folder, or an Altium .PrjPcb")
    c.add_argument("--lib", action="append", default=[], help="KiCad: a folder of .kicad_sym / .pretty libraries (repeatable)")
    c.add_argument("--no-global", action="store_true", help="KiCad: leave out KiCad's global library tables")
    c.add_argument("--workspace", help="Altium: the Altium 365 workspace URL")
    c.add_argument("--library", help="Altium: the ecadlib library that pushed the components (classified changes)")
    c.add_argument("-o", "--out", help="write REPORT.md and REPORT.json")
    c.add_argument("--json", action="store_true")
    f = sub.add_parser("freeze", help="KiCad: copy the parts the project uses into project libraries and use those")
    f.add_argument("project")
    f.add_argument("--name", help="library nickname (default <project>-frozen)")
    f.add_argument("--dry-run", action="store_true", help="say what would be written; change nothing")
    f.add_argument("--json", action="store_true")
    s = sub.add_parser("scan", help="record which library parts a project or BOM uses (library.usage.json)")
    s.add_argument("project", help="a KiCad or Altium project, or a BOM file")
    s.add_argument("--library", required=True, help="the ecadlib library folder (its plan.json)")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            if Path(a.project).suffix.lower() in ALTIUM_EXT:
                from . import altium365 as A, usercfg as UC
                if not a.workspace:
                    raise ProjectError("an Altium project is checked against its workspace: give --workspace URL")
                token = UC.secret("NEXAR_TOKEN")
                if not token:
                    raise ProjectError("set NEXAR_TOKEN (Nexar portal token with design.domain, user.access, openid scopes)")
                res = check_altium(a.project, A.Nexar(token), a.workspace, a.library)
            else:
                res = check_kicad(a.project, a.lib, use_global=not a.no_global)
            if a.json:
                print(json.dumps(res, indent=1))
            else:
                print_check(res)
            if a.out:
                print(f"-> {write_report(res, a.out)}")
            return 1 if res["held"] else 0
        if a.cmd == "freeze":
            res = freeze_kicad(a.project, a.name, a.dry_run)
            if a.json:
                print(json.dumps(res, indent=1))
            else:
                verb = "would point" if a.dry_run else "now points"
                print(f"{len(res['symbols'])} symbol(s) and {len(res['footprints'])} footprint(s) copied to library {res['nick']}; "
                      f"the project {verb} at it.")
                for p in res["files"]:
                    print(f"  {'would write' if a.dry_run else 'wrote'} {p}")
                if res["backup"]:
                    print(f"  the files it changed are backed up in {res['backup']}")
                print("  3D models keep their paths (they are not copied).")
            return 0
        res = scan(a.project, a.library)
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            print(f"{res['project']}: uses {len(res['parts'])} part(s) of {res['library']}"
                  + (f"; {len(res['not_in_library'])} MPN(s) are not in it" if res["not_in_library"] else ""))
        return 0
    except (ProjectError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
