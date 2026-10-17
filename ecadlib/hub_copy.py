"""Copy a part from one tool's library to another, for the library hub: Altium (SchLib / PcbLib) -> KiCad and
KiCad (.kicad_sym / .kicad_mod) -> Altium, through the converters kicad-export and the librarian already use.

The copy never touches the library it came from. It goes into the hub library, <workspace>/hub/library/:

    kicad/ecadlib_hub.kicad_sym, kicad/ecadlib_hub.pretty/<footprint>.kicad_mod, kicad/{sym,fp}-lib-table
    altium/<part>.SchLib, altium/<footprint>.PcbLib

and the hub reads that folder back on its next look, so the part shows up in the target tool's column. With a
project, the library is also imported there: a KiCad project gets the ecadlib_hub rows in its own sym-lib-table /
fp-lib-table (other rows are kept); an Altium .PrjPcb gets the SchLib / PcbLib added as project libraries (a copy
of the project file is kept next to it as <name>.PrjPcb.ecadlib-bak the first time)."""
import re, shutil
from pathlib import Path

LIB = "ecadlib_hub"
TARGETS = ("altium", "kicad")


def library_dir(ws):
    return Path(ws.root).resolve() / "hub" / "library"


def _file(c, *suffixes):
    return next((Path(f) for f in c.get("files") or [] if Path(f).suffix.lower() in suffixes), None)


def source_for(copies, target):
    """The copy a target tool's version can be made from: an Altium copy with its SchLib (and PcbLib) for KiCad, a
    KiCad copy with its .kicad_sym (and .kicad_mod) for Altium. ecadlib's own copies first, then the newest."""
    want = {"kicad": ("altium", (".schlib",)), "altium": ("kicad", (".kicad_sym",))}.get(target)
    if not want:
        return None
    tool, need = want
    ok = [c for c in copies if c["tool"] == tool and _file(c, *need) and c.get("pins") is not None]
    ok.sort(key=lambda c: c["updated"] or "", reverse=True)
    ok.sort(key=lambda c: c["origin"] != "ecadlib")
    return ok[0] if ok else None


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(name)).strip("_") or "part"


def to_kicad(ws, c, name):
    """Altium copy -> the hub's KiCad library. -> (written files, footprint ref, notes)."""
    from . import kicad_export as KE, sexpr
    from .sexpr import Q
    out = library_dir(ws) / "kicad"
    pretty = out / f"{LIB}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    notes, written, fp_ref = [], [], ""
    pcb = _file(c, ".pcblib")
    if pcb and c.get("footprint"):
        fpn = _safe(c["footprint"])
        (pretty / f"{fpn}.kicad_mod").write_text(KE.footprint_from_pcblib(pcb, fpn, notes, pick=c["footprint"]), encoding="utf-8")
        written.append(pretty / f"{fpn}.kicad_mod")
        fp_ref = f"{LIB}:{fpn}"
    sname = KE.kname(name)
    sym = KE.sym_kicad8(KE.symbol_from_schlib(_file(c, ".schlib"), sname, notes, pick=c.get("symbol") or c["name"]))
    fields = [("Footprint", fp_ref)] + [(k, v) for k, v in (("MPN", c.get("mpn")), ("Manufacturer", c.get("manufacturer")),
                                                             ("Description", c.get("description"))) if v]
    KE.set_fields(sym, fields)
    lib = out / f"{LIB}.kicad_sym"
    symbols = {}
    if lib.is_file():
        for s in sexpr.find(sexpr.parse(lib.read_text(encoding="utf-8"), True), "symbol"):
            symbols[str(s[1]).strip('"')] = s
    symbols[sname] = sym
    tree = ["kicad_symbol_lib", ["version", KE.SYM_VERSION], ["generator", Q("ecadlib")], ["generator_version", Q("8.0")]]
    tree += [symbols[k] for k in sorted(symbols)]
    lib.write_text(sexpr.dumps(tree) + "\n", encoding="utf-8")
    written.append(lib)
    from .kicad_dblib import add_lib_row
    for kind, table, uri in (("sym", "sym_lib_table", lib), ("fp", "fp_lib_table", pretty)):
        add_lib_row(out / f"{kind}-lib-table", LIB, uri.as_posix(), "parts copied by the ecadlib library hub", "KiCad", table)
    return written, fp_ref, notes


def to_altium(ws, c, name):
    """KiCad copy -> the hub's Altium library (one SchLib per part, one PcbLib per footprint)."""
    from . import altium_lib as AL, kicad_import as K
    out = library_dir(ws) / "altium"
    out.mkdir(parents=True, exist_ok=True)
    notes, written, fpn = [], [], ""
    mod = _file(c, ".kicad_mod")
    if mod:
        fpn = mod.stem
        fp, _ = K.convert_footprint(mod.read_text(encoding="utf-8"), fpn, mod.name, notes)
        AL.write_pcblib(out / f"{AL.file_stem(fpn)}.PcbLib", [fp])
        written.append(out / f"{AL.file_stem(fpn)}.PcbLib")
    sym_name = (c.get("symbol") or c["name"]).split(":")[-1]
    chain, _ = K.load_symbol(f"{_file(c, '.kicad_sym')}:{sym_name}", None)
    extra = [(k, v) for k, v in (("Manufacturer Part Number", c.get("mpn")), ("Manufacturer", c.get("manufacturer"))) if v]
    sym = K.convert_symbol(chain, name, fpn or None, extra, None, notes)
    AL.write_schlib(out / f"{AL.file_stem(name)}.SchLib", [sym])
    written.append(out / f"{AL.file_stem(name)}.SchLib")
    return written, fpn, notes


def projects(dirs):
    """KiCad (.kicad_pro) and Altium (.PrjPcb) projects under dirs -> [{name, path, tool}]."""
    out = []
    for d in dirs:
        d = Path(d).expanduser()
        found = [d] if d.is_file() else sorted(d.rglob("*")) if d.is_dir() else []
        for f in found:
            suf = f.suffix.lower()
            if suf in (".kicad_pro", ".prjpcb") and f.is_file():
                out.append({"name": f.stem, "path": str(f.resolve()), "tool": "kicad" if suf == ".kicad_pro" else "altium"})
    return list({p["path"]: p for p in out}.values())


def import_into_project(ws, project, tool):
    """Make the hub library available in a project. -> what was changed, in words."""
    p = Path(project)
    lib = library_dir(ws)
    if tool == "kicad":
        if p.suffix.lower() != ".kicad_pro":
            raise ValueError(f"{p.name} is not a KiCad project: KiCad copies can only be added to a .kicad_pro")
        from .kicad_dblib import add_lib_row
        add_lib_row(p.parent / "sym-lib-table", LIB, (lib / "kicad" / f"{LIB}.kicad_sym").as_posix(),
                    "parts copied by the ecadlib library hub", "KiCad", "sym_lib_table")
        add_lib_row(p.parent / "fp-lib-table", LIB, (lib / "kicad" / f"{LIB}.pretty").as_posix(),
                    "parts copied by the ecadlib library hub", "KiCad", "fp_lib_table")
        return f"{LIB} added to {p.parent.name}'s sym-lib-table and fp-lib-table"
    if p.suffix.lower() != ".prjpcb":
        raise ValueError(f"{p.name} is not an Altium project: Altium copies can only be added to a .PrjPcb")
    return add_to_prjpcb(p, sorted((lib / "altium").glob("*.SchLib")) + sorted((lib / "altium").glob("*.PcbLib")))


def add_to_prjpcb(prj, libs):
    """Add library documents to an Altium project ([DocumentN] DocumentPath=...), skipping ones it lists already."""
    text = prj.read_text(encoding="utf-8-sig", errors="replace")
    have = {Path(m.strip()).name.upper() for m in re.findall(r"(?mi)^DocumentPath=(.*)$", text)}
    n = max([int(x) for x in re.findall(r"(?mi)^\[Document(\d+)\]", text)] or [0])
    add = [f for f in libs if f.name.upper() not in have]
    if not add:
        return f"{prj.name} already lists the hub's Altium libraries"
    bak = prj.with_name(prj.name + ".ecadlib-bak")
    if not bak.exists():
        shutil.copy2(prj, bak)
    nl = "\r\n" if "\r\n" in text else "\n"
    secs = []
    for f in add:
        n += 1
        try:
            rel = Path(f).resolve().relative_to(prj.parent.resolve())
            path = str(rel).replace("/", "\\")
        except ValueError:
            path = str(Path(f).resolve())
        secs.append(f"[Document{n}]{nl}DocumentPath={path}{nl}AnnotationEnabled=1{nl}AnnotateStartValue=1{nl}"
                    f"AnnotationIndexControlEnabled=0{nl}AnnotateSuffix={nl}AnnotateScope=All{nl}AnnotateOrder=-1{nl}"
                    f"DoLibraryUpdate=1{nl}DoDatabaseUpdate=1{nl}ClassGenCCAutoEnabled=1{nl}ClassGenCCAutoRoomEnabled=1{nl}"
                    f"ClassGenNCAutoScope=None{nl}DItemRevisionGUID={nl}GenerateClassCluster=0{nl}")
    last = list(re.finditer(r"(?mi)^\[Document\d+\][^\[]*", text))
    at = last[-1].end() if last else len(text)
    block = nl.join(secs) + nl
    text = text[:at].rstrip("\r\n") + nl + nl + block + (nl + text[at:].lstrip("\r\n") if text[at:].strip() else "")
    prj.write_text(text, encoding="utf-8")
    return f"{len(add)} librar{'y' if len(add) == 1 else 'ies'} added to {prj.name} (a copy of the old file is {bak.name})"
