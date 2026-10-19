"""Let Altium Designer build the .PcbLib / .SchLib files itself, through its own scripting API, instead of trusting
ecadlib's byte-level writer (altium_lib) with a format Altium does not document.

    ecadlib altium-native write LIBFOLDER [--out DIR]     collect the build recipes under LIBFOLDER into DIR
                                                          (default LIBFOLDER): ecadlib_build.txt + the script project
    ecadlib altium-native compare LIBFOLDER [--json]      after Altium has built them: read every library Altium saved
                                                          and compare it with its recipe, pad by pad and pin by pin

How it fits together:
  1. Every time ecadlib writes a .PcbLib or .SchLib (altium_lib.write_pcblib / write_schlib), it also writes the same
     content as a plain-text recipe next to it: <file>.ecadbuild. A recipe is a list of primitives (pads, tracks,
     arcs, fills, regions, text, 3D bodies; pins, rectangles, lines, arcs, labels, parameters, the footprint link)
     in Altium's own units, nothing Altium-specific about the file format.
  2. `write` gathers the recipes of a library folder into ecadlib_build.txt (STEP models referenced by a footprint are
     written beside it as files, ecadlib_3d/<footprint>_<n>/<model name>) and writes ecadlib_build.PrjScr + ecadlib_build.pas.
  3. In Altium, File > Run Script > BuildLibrariesInAltium (or the install script's procedures, see altium_pkg) reads
     ecadlib_build.txt and, for each library, opens a new PcbLib / SchLib document, creates every primitive with
     PCBServer / SchServer object factories, and saves the document over the file ecadlib wrote. The file on disk
     is then one Altium itself wrote. It writes ecadlib_build_report.txt: one line per library, OK or FAILED.
  4. `compare` reads those files back and checks them against the recipes (pad designators, positions, sizes,
     holes; pin numbers, names, positions, lengths): proof that what Altium built is what ecadlib meant.
  5. Edits made in Altium are kept. Beside each library, <file>.ecadstate remembers the hash of the bytes ecadlib
     wrote ("source") and of the file Altium built from them ("built"; taken the first time the file on disk differs
     from ecadlib's bytes and still matches its recipe, i.e. after Altium's build). A library whose file is neither
     is one someone changed in Altium: `write` leaves it out of ecadlib_build.txt and dblib-sync / altium-pkg do not
     copy over it (they report it instead); `write --rebuild-all` / overwrite_edits=True replace it anyway. A library
     Altium already built and ecadlib has not changed since is not rebuilt either. The state is kept where libraries
     are copied to (dblib-sync, altium-pkg from a folder: the folder Altium builds in), not where ecadlib writes them;
     libraries with no .ecadstate (written in place, or before this) are built as before.

Recipe format (UTF-8, one primitive per line, fields separated by TAB; PCB lengths in Altium internal units:
1 mil = 10000; schematic lengths in 1/1000 mil; angles in 1/1000 degree; layers as Altium's legacy layer numbers):
  ECADLIB-BUILD 1
  PCBLIB <file name>                        then one or more:
    FOOTPRINT name description height
    PAD designator x y w h rot shape(rect|round|roundrect|octagonal) corner% layer hole hole_shape(round|square|slot)
        slot hole_rot plated(1|0) paste_mode(rule|manual) paste mask_mode mask offset_x offset_y
    TRACK x1 y1 x2 y2 width layer             ARC cx cy r start end width layer
    FILL x1 y1 x2 y2 layer                    REGION layer keepout(0|1) n x1 y1 ... xn yn
    TEXT text x y height stroke rot layer     BODY layer height standoff step_file rx ry rz dz n x1 y1 ...
  SCHLIB <file name>
    SYMBOL name description designator comment parts modes
    PIN part mode number name etype orient x y length show_name show_number hidden inner_edge outer_edge inner outer
        description hidden_net
    RECT part mode x1 y1 x2 y2 width fill     LINE part mode x1 y1 x2 y2 width
    POLYLINE part mode width n x1 y1 ...      POLYGON part mode width fill n x1 y1 ...
    SARC part mode cx cy r start end width    CIRCLE part mode cx cy r width fill
    LABEL part mode x y orientation text      PARAM name value
    DESIGNATOR x y                            COMMENT x y
    FOOTPRINTLINK name
  END                                        closes a PCBLIB / SCHLIB block

The script is written from Altium's scripting references (TR0120 DelphiScript, TR0135 Client/System, the PCB and
Schematic API references) and Altium's own example scripts; ALTIUM_API lists every identifier it calls and where it is
documented. tests/test_altium_native.py checks that the script calls nothing else. Run in Altium Designer 26 on a
real PC before it is trusted: see docs/altium-native.md for the result of that run."""
import argparse, hashlib, json, re, sys
from pathlib import Path

from . import altium_lib as AL

EXT = ".ecadbuild"
STATE_EXT = ".ecadstate"                # {"source": sha256 of ecadlib's bytes, "built": sha256 of Altium's build}
BUILD, PROJECT, UNIT, REPORT = "ecadlib_build.txt", "ecadlib_build.PrjScr", "ecadlib_build.pas", "ecadlib_build_report.txt"
FILL = {"body": "body", "line": "line"}


# ------------------------------------------------------------------ recipe text
def _t(s):
    """A text field: tabs and line breaks would split the record, so they become spaces."""
    return re.sub(r"[\t\r\n]+", " ", str(s if s is not None else ""))


def _a(deg):
    return str(int(round(float(deg or 0) * 1000)))


def _s(v):
    """Schematic length (DXP units, 10 mil) -> 1/1000 mil."""
    return str(int(round(float(v) * 10000)))


def _line(*f):
    return "\t".join(_t(x) for x in f)


def step_file(fp, k, body):
    """Where a body's embedded STEP model is written beside its PcbLib (relative, '/' separators): its own folder,
    so the file keeps the model's name and Altium embeds it under that name."""
    name = Path(str((body.get("step") or {}).get("name") or f"{AL.file_stem(fp['name'])}.step").replace("\\", "/")).name
    stem = AL.file_stem(fp["name"])
    if stem != AL.lib_name(fp["name"]):       # 'A/B' and 'A_B' would share a folder (and one STEP file): keep them apart
        stem += "_" + hashlib.sha1(AL.lib_name(fp["name"]).encode("utf-8")).hexdigest()[:6]
    return f"ecadlib_3d/{stem}_{k}/{_t(name)}"


def footprint_lines(fp):
    """Recipe lines for one footprint dict (altium_lib's model: mm, y up)."""
    mm = AL.mm
    out = [_line("FOOTPRINT", AL.lib_name(fp["name"]), AL.clean(fp.get("description", "")), mm(fp.get("height_mm") or 0))]
    for p in fp.get("pads", []):
        w, h = mm(p["w"]), mm(p["h"])
        def exp(v):
            if v is None:
                return "rule", 0
            if v == "none":                 # closed aperture: the expansion altium_lib uses
                return "manual", -max(w, h) // 2 - mm(0.01)
            return "manual", mm(v)          # mm, as altium_lib writes it
        pm, pv = exp(p.get("paste"))
        mm_, mv = exp(p.get("mask"))
        ox, oy = (mm(v) for v in p["offset"]) if p.get("offset") else (0, 0)
        shape = "round" if p["shape"] in ("circle", "oval") else p["shape"]
        out.append(_line("PAD", p["designator"], mm(p["x"]), mm(p["y"]), w, h, _a(p.get("rot")), shape,
                         int(round(p.get("corner", 0))) if p["shape"] == "roundrect" else 0, p.get("layer", AL.TOP),
                         mm(p.get("hole", 0)), p.get("hole_shape", "round"), mm(p.get("slot", 0)), _a(p.get("hole_rot")),
                         1 if p.get("plated", True) else 0, pm, pv, mm_, mv, ox, oy))
    for x1, y1, x2, y2, w, layer in fp.get("tracks", []):
        out.append(_line("TRACK", mm(x1), mm(y1), mm(x2), mm(y2), mm(w), layer))
    for cx, cy, r, a0, a1, w, layer in fp.get("arcs", []):
        out.append(_line("ARC", mm(cx), mm(cy), mm(r), _a(a0), _a(a1), mm(w), layer))
    for x1, y1, x2, y2, layer in fp.get("fills", []):
        out.append(_line("FILL", mm(x1), mm(y1), mm(x2), mm(y2), layer))
    for pts, layer, *ko in fp.get("regions", []):
        out.append(_line("REGION", layer, 1 if ko and ko[0] is not None else 0, len(pts), *[mm(v) for xy in pts for v in xy]))
    for txt, x, y, h, w, rot, layer in fp.get("texts", []):
        out.append(_line("TEXT", txt, mm(x), mm(y), mm(h), mm(w), _a(rot), layer))
    for k, b in enumerate(fp.get("bodies", [])):
        st = b.get("step") or {}
        step = step_file(fp, k, b) if st.get("data") else ""
        rot = st.get("rot") or (0, 0, 0)
        out.append(_line("BODY", fp.get("body_layer", AL.MECH13), mm(b["height"]), mm(b.get("standoff", 0)), step,
                         _a(rot[0]), _a(rot[1]), _a(rot[2]), mm(st.get("dz", 0)), len(b["pts"]),
                         *[mm(v) for xy in b["pts"] for v in xy]))
    return out


def symbol_lines(sym):
    """Recipe lines for one symbol dict (altium_lib's model: DXP units, y up)."""
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    modes = sym.get("prim_modes") or [0] * len(sym["prims"])
    out = [_line("SYMBOL", AL.lib_name(sym["name"]), AL.clean(sym.get("description", "")), sym.get("designator", "U?"),
                 sym.get("comment", "*"), sym.get("parts", 1), sym.get("modes", 1))]
    for pr, part, mode in zip(sym["prims"], parts, modes):
        k = pr[0]
        if k == "rect":
            fill = pr[6] if len(pr) > 6 else "body"
            out.append(_line("RECT", part, mode, *map(_s, (min(pr[1], pr[3]), min(pr[2], pr[4]), max(pr[1], pr[3]), max(pr[2], pr[4]))),
                             pr[5] if len(pr) > 5 and pr[5] else 1, FILL.get(fill, "none")))
        elif k == "line":
            out.append(_line("LINE", part, mode, *map(_s, pr[1:5]), pr[5]))
        elif k in ("polyline", "polygon"):
            pts = [_s(v) for xy in pr[1] for v in xy]
            if k == "polyline":
                out.append(_line("POLYLINE", part, mode, pr[2], len(pr[1]), *pts))
            else:
                out.append(_line("POLYGON", part, mode, pr[2], FILL.get(pr[3], "none"), len(pr[1]), *pts))
        elif k == "arc":
            out.append(_line("SARC", part, mode, _s(pr[1]), _s(pr[2]), _s(pr[3]), _a(pr[4]), _a(pr[5]), pr[6]))
        elif k == "circle":
            out.append(_line("CIRCLE", part, mode, _s(pr[1]), _s(pr[2]), _s(pr[3]), pr[4], FILL.get(pr[5], "none")))
        elif k == "text":
            out.append(_line("LABEL", part, mode, _s(pr[1]), _s(pr[2]), pr[4] or 0, AL.clean(pr[3])))
        else:                                   # ("pin", p, side, x, y, length[, show])
            _, p, side, x, y, length, *show = pr
            sn = show[0] if show else True
            sn_name, sn_num = (sn if p.get("show_name") is None else p["show_name"]), (sn if p.get("show_num") is None else p["show_num"])
            out.append(_line("PIN", part, mode, p["num"], AL.overline(p["name"]), AL.ETYPE[p.get("type", "passive")], AL.ORIENT[side],
                             _s(x), _s(y), _s(length), int(bool(sn_name)), int(bool(sn_num)), int(bool(p.get("hidden"))),
                             *AL.pin_symbols(p.get("shape")), AL.clean(p.get("description", "")), AL.clean(p.get("net") or "")))
    (dx, dy), (cx, cy) = sym["label_at"]
    out += [_line("DESIGNATOR", _s(dx), _s(dy)), _line("COMMENT", _s(cx), _s(cy))]
    out += [_line("PARAM", AL.clean(n), AL.clean(v)) for n, v in sym.get("params", [])]
    if sym.get("footprint"):
        out.append(_line("FOOTPRINTLINK", AL.lib_name(sym["footprint"])))
    return out


def recipe_text(kind, filename, items):
    """kind 'PCBLIB' (items: footprint dicts) or 'SCHLIB' (symbol dicts) -> one recipe block (with the header line)."""
    body = []
    for it in items:
        body += footprint_lines(it) if kind == "PCBLIB" else symbol_lines(it)
    return "\n".join(["ECADLIB-BUILD\t1", _line(kind, filename), *body, "END"]) + "\n"


def write_recipe(lib_path, kind, items):
    """Write <lib_path>.ecadbuild next to a library ecadlib just wrote (and any STEP files its bodies embed)."""
    lib_path = Path(lib_path)
    text = recipe_text(kind, lib_path.name, items)
    rp = lib_path.with_name(lib_path.name + EXT)
    if not (rp.is_file() and rp.read_text(encoding="utf-8") == text):
        rp.write_text(text, encoding="utf-8")
    if kind == "PCBLIB":
        for fp in items:
            for k, b in enumerate(fp.get("bodies", [])):
                data = (b.get("step") or {}).get("data")
                if data:
                    sp = lib_path.parent / step_file(fp, k, b)
                    sp.parent.mkdir(parents=True, exist_ok=True)
                    if not (sp.is_file() and sp.read_bytes() == data):
                        sp.write_bytes(data)
    return rp


def safe_step(rel):
    """True for a STEP path a recipe may name: ecadlib_3d/... relative to the library's folder, '/' separators, no
    '..', '.', empty or drive parts. A recipe comes with any library someone shares, so a BODY path like
    '../../startup/x.bat' must never be read, copied or handed to Altium."""
    s = str(rel or "")
    if "\\" in s or ":" in s or s.startswith("/"):
        return False
    parts = s.split("/")
    return len(parts) >= 2 and parts[0] == "ecadlib_3d" and all(p not in ("", ".", "..") for p in parts)


def _inside(path, folder):
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except (OSError, ValueError):
        return False


def copy_recipe(src_lib, dst_lib):
    """A library copied elsewhere (dblib-sync copies each part's files into the library folder) takes its recipe along:
    <dst_lib>.ecadbuild and the STEP files its BODY lines name (relative to the library's folder). The recipe's
    library name is the copy's. -> the files written (paths). Nothing when src_lib has no recipe; then a recipe an
    earlier copy left at dst_lib is removed (when src_lib is a library), or Altium would rebuild the new file from it."""
    src_lib, dst_lib = Path(src_lib), Path(dst_lib)
    rp = src_lib.with_name(src_lib.name + EXT)
    if not rp.is_file():
        for ext in (EXT, STATE_EXT):
            stale = dst_lib.with_name(dst_lib.name + ext)
            if src_lib.is_file() and stale.is_file():
                stale.unlink()
        return []
    lines = rp.read_text(encoding="utf-8").splitlines()
    out, written = [], []
    for ln in lines:
        f = ln.split("\t")
        if f[0] in ("PCBLIB", "SCHLIB") and len(f) > 1:
            ln = _line(f[0], dst_lib.name)
        elif f[0] == "BODY" and len(f) > 4 and f[4]:
            src, dst = src_lib.parent / f[4], dst_lib.parent / f[4]
            if not (safe_step(f[4]) and _inside(src, src_lib.parent) and _inside(dst, dst_lib.parent)):
                f[4] = ""                       # not a STEP file beside the library: the body keeps no model
                out.append("\t".join(f))
                continue
            if src.is_file() and not (dst.is_file() and dst.read_bytes() == src.read_bytes()):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                written.append(dst)
        out.append(ln)
    text = "\n".join(out) + "\n"
    dst = dst_lib.with_name(dst_lib.name + EXT)
    if not (dst.is_file() and dst.read_text(encoding="utf-8") == text):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        written.append(dst)
    if src_lib.is_file():
        record(dst_lib, _sha(src_lib.read_bytes()))
    return written


# ------------------------------------------------------------------ edits made in Altium (<file>.ecadstate)
def _sha(data):
    return hashlib.sha256(data).hexdigest()


def state_of(lib):
    """The library's .ecadstate ({"source", "built"}), or None (none, or not readable)."""
    lib = Path(lib)
    try:
        st = json.loads(lib.with_name(lib.name + STATE_EXT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) and st.get("source") else None


def _save_state(lib, st):
    lib = Path(lib)
    p, text = lib.with_name(lib.name + STATE_EXT), json.dumps(st, sort_keys=True) + "\n"
    if not (p.is_file() and p.read_text(encoding="utf-8") == text):
        p.write_text(text, encoding="utf-8")


def record(lib, source):
    """ecadlib's bytes for lib (sha256 `source`) are, or are about to be, on disk. An earlier build is only kept when
    it was built from these same bytes."""
    old = state_of(lib) or {}
    _save_state(lib, {"source": source, "built": old.get("built") if old.get("source") == source else None})


def lib_state(lib, rp=None, save=True):
    """'new' (ecadlib's bytes, not built yet), 'built' (as Altium built it), 'edited' (changed after that) or
    'unknown' (no .ecadstate: written before ecadlib kept one). rp: its recipe, to recognise Altium's first build."""
    lib = Path(lib)
    st = state_of(lib)
    if st is None or not lib.is_file():
        return "unknown"
    h = _sha(lib.read_bytes())
    if h == st["source"]:
        return "new"
    if h == st.get("built"):
        return "built"
    rp = Path(rp) if rp else lib.with_name(lib.name + EXT)
    if not st.get("built") and rp.is_file():        # the first file after ecadlib's that still matches the recipe:
        try:                                         # what Altium built. Remember it, so a later edit shows.
            same = not compare_one(lib, rp)
        except (ValueError, OSError, KeyError, IndexError):
            same = False
        if same:
            if save:
                _save_state(lib, dict(st, built=h))
            return "built"
    return "edited"


def replace_action(src_data, dst, overwrite_edits=False, save=True):
    """May a copy of ecadlib's library (bytes src_data) go over dst? -> 'copy', 'keep' (dst is Altium's build of these
    same bytes: nothing to do) or 'edited' (someone changed dst in Altium: leave it, unless overwrite_edits)."""
    dst = Path(dst)
    if not dst.is_file():
        return "copy"
    s = lib_state(dst, save=save)
    if s == "edited" and not overwrite_edits:
        return "edited"
    if s == "built" and state_of(dst)["source"] == _sha(src_data):
        return "keep"
    return "copy"


EDITED_NOTE = ("changed in Altium since ecadlib last built it: left as it is (dblib-sync --overwrite-edits, "
               "altium-native write --rebuild-all replace it)")


# ------------------------------------------------------------------ collecting a library folder
def recipe_steps(lib_path):
    """The STEP files the recipe next to a library names (ecadlib_3d/<footprint>_<n>/<model>, relative to the library's
    folder, '/' separators); [] when it has no recipe."""
    rp = Path(lib_path).with_name(Path(lib_path).name + EXT)
    if not rp.is_file():
        return []
    return [f[4] for f in (ln.split("\t") for ln in rp.read_text(encoding="utf-8").splitlines())
            if f[0] == "BODY" and len(f) > 4 and f[4] and safe_step(f[4])]


def recipes(folder):
    """[(library path relative to folder, recipe path)] for every recipe under folder whose library exists."""
    folder = Path(folder)
    out = []
    for rp in sorted(folder.rglob("*" + EXT)):
        lib = rp.with_name(rp.name[:-len(EXT)])
        if lib.is_file():
            out.append((lib.relative_to(folder), rp))
    return out


def to_build(folder, rebuild_all=False):
    """-> (recipes to build, {"built": [rel], "edited": [rel]}): what Altium still has to build, and what it leaves
    alone (already built from ecadlib's current bytes, or changed in Altium since)."""
    folder = Path(folder)
    build, left = [], {"built": [], "edited": []}
    for rel, rp in recipes(folder):
        s = "new" if rebuild_all else lib_state(folder / rel, rp)
        if s in ("built", "edited"):
            left[s].append(rel.as_posix())
        else:
            build.append((rel, rp))
    return build, left


def build_text(folder, found=None):
    """ecadlib_build.txt for a folder: each recipe with its library path (Windows separators, relative to the folder).
    found: the recipes to take (default: every one, recipes(folder))."""
    lines = ["ECADLIB-BUILD\t1"]
    for rel, rp in (recipes(folder) if found is None else found):
        for ln in rp.read_text(encoding="utf-8").splitlines():
            if ln.startswith("ECADLIB-BUILD"):
                continue
            kind = ln.split("\t", 1)[0]
            if kind in ("PCBLIB", "SCHLIB"):
                ln = _line(kind, str(rel).replace("/", "\\"))
            elif kind == "BODY":           # STEP files sit beside their PcbLib: make the name folder-relative
                f = ln.split("\t")
                if len(f) > 4 and f[4]:         # only ecadlib_3d/... beside the library (safe_step), else no model
                    f[4] = str(rel.parent / f[4]).replace("/", "\\") if safe_step(f[4]) else ""
                ln = "\t".join(f)
            lines.append(ln)
    return "\n".join(lines) + "\n"


def write(folder, out=None, rebuild_all=False):
    """Write ecadlib_build.txt and the script project into out (default: folder). -> report dict. Libraries Altium
    already built from ecadlib's current bytes, and libraries changed in Altium since, are left out (to_build);
    rebuild_all takes every one."""
    folder = Path(folder)
    out = Path(out) if out else folder
    out.mkdir(parents=True, exist_ok=True)
    found, left = to_build(folder, rebuild_all)
    text = build_text(folder, found)
    if out.resolve() != folder.resolve():       # the script resolves library paths from its own folder
        import os
        prefix = Path(os.path.relpath(folder, out))
        fixed = []
        for ln in text.splitlines():
            f = ln.split("\t")
            if f[0] in ("PCBLIB", "SCHLIB") or (f[0] == "BODY" and f[4]):
                i = 1 if f[0] != "BODY" else 4
                f[i] = str(prefix / f[i].replace("\\", "/")).replace("/", "\\")
            fixed.append("\t".join(f))
        text = "\n".join(fixed) + "\n"
    written = []
    for name, data in ((BUILD, text.encode("utf-8-sig")),   # BOM: TStringList.LoadFromFile reads it as UTF-8
                       (PROJECT, prjscr_text().encode("utf-8")), (UNIT, pas_text().encode("utf-8"))):
        if not ((out / name).is_file() and (out / name).read_bytes() == data):   # unchanged files keep their date
            (out / name).write_bytes(data)
            written.append(name)
    return {"folder": str(folder), "out": str(out), "libraries": [r.as_posix() for r, _ in found],
            "up_to_date": left["built"], "edited": left["edited"], "files": [BUILD, PROJECT, UNIT], "written": written}


def prjscr_text():
    return "\r\n".join(["[Design]", "Version=1.0", "", "[Document1]", f"DocumentPath={UNIT}", ""])


# ------------------------------------------------------------------ the DelphiScript
# Every identifier the script calls from Altium, and where it is documented (TRxxxx = Altium scripting reference PDFs
# as kept in github.com/Altium-Designer-addons/scripting-reference; "examples" = Altium's own PCB / SCH example scripts
# shipped with Altium Designer: CreateComponentInLibrary.pas (PCB), CreateSchObjects / LibraryIterator scripts (SCH)).
ALTIUM_API = {
    "Client": "TR0135 System Reference (Client object)",
    "OpenNewDocument": "TR0135 IClient.OpenNewDocument(Kind, FileName, NickName, ReadOnly) : IServerDocument",
    "ShowDocument": "TR0135 IClient.ShowDocument(Document)",
    "CloseDocument": "TR0135 IClient.CloseDocument(Document)",
    "GetDocumentByPath": "TR0135 IClient.GetDocumentByPath(Path) : IServerDocument",
    "DoFileSave": "TR0135 IServerDocument.DoFileSave(Kind)",
    "SetModified": "TR0135 IServerDocument.SetModified",
    "PCBServer": "PCB API reference: global PCBServer : IPCB_ServerInterface",
    "GetCurrentPCBLibrary": "PCB API: IPCB_ServerInterface.GetCurrentPCBLibrary : IPCB_Library",
    "CreatePCBLibComp": "PCB API: IPCB_ServerInterface.CreatePCBLibComp (example CreateComponentInLibrary)",
    "RegisterComponent": "PCB API: IPCB_Library.RegisterComponent",
    "RemoveComponent": "PCB API: IPCB_Library.RemoveComponent",
    "CurrentComponent": "PCB API: IPCB_Library.CurrentComponent",
    "PCBObjectFactory": "PCB API: IPCB_ServerInterface.PCBObjectFactory(ObjectId, DimensionKind, CreationMode)",
    "PCBContourFactory": "PCB API: IPCB_ServerInterface.PCBContourFactory : IPCB_Contour",
    "AddPoint": "PCB API: IPCB_Contour.AddPoint(X, Y)",
    "SetOutlineContour": "PCB API: IPCB_Region / IPCB_ComponentBody.SetOutlineContour",
    "AddPCBObject": "PCB API: IPCB_LibComponent.AddPCBObject (example CreateComponentInLibrary)",
    "PreProcess": "PCB API: IPCB_ServerInterface.PreProcess", "PostProcess": "PCB API: IPCB_ServerInterface.PostProcess",
    "SendMessageToRobots": "PCB API: IPCB_ServerInterface.SendMessageToRobots (example CreateComponentInLibrary)",
    "I_ObjectAddress": "PCB / SCH API: object address for robot messages",
    "GetState_Cache": "PCB API: IPCB_Pad.GetState_Cache : TPadCache (AD26 SDK IPCB_Pad.Internal_GetState_Cache)",
    "SetState_Cache": "PCB API: IPCB_Pad.SetState_Cache(TPadCache) (AD26 SDK; ScriptingSystem wraps TPadCache with setters)",
    "SetState_StackCRPctOnLayer": "PCB API: IPCB_Pad.SetState_StackCRPctOnLayer(Layer, Percent) (rounded-rectangle corner)",
    "ModelFactory_CreateExtruded": "PCB API: IPCB_ComponentBody.ModelFactory_CreateExtruded(Standoff, Height, Colour)",
    "ModelFactory_FromFilename": "PCB API: IPCB_ComponentBody.ModelFactory_FromFilename(FileName, Embedded)",
    "SetState_FromModel": "PCB API: IPCB_ComponentBody.SetState_FromModel",
    "SetState": "PCB API: IPCB_Model.SetState(RotX, RotY, RotZ, Dz)",
    "MechanicalLayer": "PCB API: LayerUtils.MechanicalLayer(N) (AD19+ layer ids)",
    "LayerUtils": "PCB API: LayerUtils",
    "SchServer": "Schematic API reference: global SchServer : ISch_ServerInterface",
    "GetCurrentSchDocument": "SCH API: ISch_ServerInterface.GetCurrentSchDocument (an ISch_Lib in the library editor)",
    "SchObjectFactory": "SCH API: ISch_ServerInterface.SchObjectFactory(ObjectId, CreationMode)",
    "AddSchComponent": "SCH API: ISch_Lib.AddSchComponent", "RemoveSchComponent": "SCH API: ISch_Lib.RemoveSchComponent",
    "CurrentSchComponent": "SCH API: ISch_Lib.CurrentSchComponent",
    "AddSchObject": "SCH API: ISch_BasicContainer.AddSchObject",
    "AddSchImplementation": "SCH API: ISch_Component.AddSchImplementation : ISch_Implementation",
    "AddDataFileLink": "SCH API: ISch_Implementation.AddDataFileLink(EntityName, Location, FileKind)",
    "ClearAllDatafileLinks": "SCH API: ISch_Implementation.ClearAllDatafileLinks",
    "RobotManager": "SCH API: ISch_ServerInterface.RobotManager", "SendMessage": "SCH API: ISch_RobotManager.SendMessage",
    "GraphicallyInvalidate": "SCH API: ISch_Document.GraphicallyInvalidate",
    "Point": "SCH API: Point(X, Y) : TLocation", "MilsToCoord": "TR0135 / SCH API: MilsToCoord",
    "GetRunningScriptProjectName": "used in Altium-Designer-addons scripts; ecadlib altium-pkg's install script",
    "ExtractFilePath": "TR0136 p29", "FileExists": "TR0135 p143", "DeleteFile": "TR0120 File IO Routines",
    "RenameFile": "TR0120 File IO Routines", "FileOpen": "TR0120 File IO Routines", "FileSeek": "TR0120 File IO Routines",
    "FileClose": "TR0120 File IO Routines",
    "TStringList": "TR0135 Helper classes; TR0120 p10", "LoadFromFile": "TStringList.LoadFromFile",
    "SaveToFile": "TR0135 p196 (TStringList)", "Add": "TStringList.Add", "Clear": "TStringList.Clear",
    "Count": "TStringList.Count", "Strings": "TStringList.Strings", "Create": "constructors", "Free": "destructor, TR0120 p10",
    "StrToInt": "TR0120 String Routines", "IntToStr": "TR0120 p6", "Copy": "TR0120 p17", "Length": "TR0120 p17",
    "Pos": "TR0120 p17", "Trim": "TR0120 p17", "ShowMessage": "TR0120 p3", "Inc": "TR0120 Ordinal Routines",
}

# Object properties and enum constants the script sets, from the same references: IPCB_Pad / IPCB_Track / IPCB_Arc /
# IPCB_Fill / IPCB_Region / IPCB_Text / IPCB_ComponentBody / IPCB_LibComponent (PCB API reference, "PCB Objects
# Interfaces"; TLayer, TShape, TObjectId, TPadMode, TMaskExpansionMode, THoleType enums) and ISch_Component /
# ISch_Pin / ISch_Rectangle / ISch_Line / ISch_Polyline / ISch_Arc / ISch_Ellipse / ISch_Label / ISch_Parameter /
# ISch_Implementation (Schematic API reference; TObjectId, TPinElectrical, TRotationBy90 enums).
PROPERTIES = {
    "pcb": ["X", "Y", "Name", "Mode", "TopXSize", "TopYSize", "MidXSize", "MidYSize", "BotXSize", "BotYSize", "TopShape",
            "MidShape", "BotShape", "Rotation", "Layer", "HoleSize", "HoleType", "HoleWidth", "HoleRotation", "Plated",
            "PasteMaskExpansion", "PasteMaskExpansionValid", "SolderMaskExpansion", "SolderMaskExpansionValid", "X1", "Y1",
            "X2", "Y2", "Width", "XCenter", "YCenter", "Radius", "StartAngle", "EndAngle", "LineWidth", "X1Location",
            "Y1Location", "X2Location", "Y2Location", "IsKeepout", "UseTTFonts", "Text", "XLocation", "YLocation", "Size",
            "BodyProjection", "Model", "Description", "Height",
            "ePadObject", "eTrackObject", "eArcObject", "eFillObject", "eRegionObject", "eTextObject",
            "eComponentBodyObject", "eNoDimension", "eCreate_Default", "ePadMode_Simple", "eRectangular", "eRounded",
            "eRoundedRectangular", "eOctagonal", "eRoundHole", "eSquareHole", "eSlotHole", "eCacheManual", "eCacheValid",
            "eTopLayer", "eMidLayer1", "eBottomLayer", "eTopOverlay", "eBottomOverlay", "eTopPaste", "eBottomPaste",
            "eTopSolder", "eBottomSolder", "eKeepOutLayer", "eMultiLayer", "eBoardSide_Top", "c_Broadcast",
            "PCBM_BoardRegisteration"],
    "sch": ["Designator", "Electrical", "Orientation", "Location", "PinLength", "ShowName", "ShowDesignator", "IsHidden",
            "Symbol_InnerEdge", "Symbol_OuterEdge", "Symbol_Inner", "Symbol_Outer", "HiddenNetName", "Corner", "Color",
            "AreaColor", "IsSolid", "VerticesCount", "Vertex", "SecondaryRadius", "OwnerPartId", "OwnerPartDisplayMode",
            "CurrentPartID", "DisplayMode", "DisplayModeCount", "PartCount", "LibReference", "ComponentDescription",
            "Comment", "ModelName", "ModelType", "IsCurrent",
            "eSchComponent", "ePin", "eRectangle", "eLine", "ePolyline", "ePolygon", "eArc", "eEllipse", "eLabel",
            "eParameter", "c_BroadCast", "SCHM_PrimitiveRegistration"],
}


def pas_text():
    """The DelphiScript unit (CRLF)."""
    return PAS.replace("\r\n", "\n").replace("\n", "\r\n")


PAS = r"""{ ecadlib: build libraries inside Altium Designer (written by `ecadlib altium-native write`).

  BuildLibrariesInAltium   reads ecadlib_build.txt next to this script project and, for every library listed in it,
                           makes a new PcbLib / SchLib document, creates each footprint / symbol with Altium's own
                           object factories, and saves it over the file named there (the old file is kept as
                           NAME.ecadlib_old until the new one is saved, and put back when the build fails). Libraries that are open are
                           closed first (unsaved changes in them are lost: they would be overwritten anyway).
                           Writes ecadlib_build_report.txt: one line per library, OK or FAILED.
  BuildLibraries(Folder)   the same for the ecadlib_build.txt in Folder (other scripts call this).

  Units in the recipe: PCB lengths in Altium internal units (1 mil = 10000), schematic lengths in 1/1000 mil,
  angles in 1/1000 degree, layers as legacy layer numbers. }

Var
    EbF      : TStringList;     { fields of the current recipe line }
    EbLines  : TStringList;
    EbReport : TStringList;
    EbIdx    : Integer;
    EbErr    : String;      { set by a Make* routine that cannot build its primitive }

Procedure EbSplit(S : String);
Var
    i   : Integer;
    Cur : String;
Begin
    EbF.Clear;
    Cur := '';
    For i := 1 To Length(S) Do
    Begin
        If S[i] = #9 Then
        Begin
            EbF.Add(Cur);
            Cur := '';
        End
        Else
            Cur := Cur + S[i];
    End;
    EbF.Add(Cur);
End;

Function EbN(i : Integer) : Integer;
Begin
    Result := StrToInt(Trim(EbF[i]));
End;

Function EbAng(i : Integer) : Double;
Begin
    Result := StrToInt(Trim(EbF[i])) / 1000;
End;

Function EbSchC(i : Integer) : Integer;
Begin
    Result := MilsToCoord(StrToInt(Trim(EbF[i])) / 1000);
End;

Function PcbLayer(L : Integer) : Integer;
Begin
    Case L Of
        1  : Result := eTopLayer;
        32 : Result := eBottomLayer;
        33 : Result := eTopOverlay;
        34 : Result := eBottomOverlay;
        35 : Result := eTopPaste;
        36 : Result := eBottomPaste;
        37 : Result := eTopSolder;
        38 : Result := eBottomSolder;
        56 : Result := eKeepOutLayer;
        74 : Result := eMultiLayer;
    Else
        If (L >= 57) And (L <= 72) Then
            Result := LayerUtils.MechanicalLayer(L - 56)
        Else
            Result := L;
    End;
End;

Function PadShape(S : String) : Integer;
Begin
    If S = 'rect' Then Result := eRectangular
    Else If S = 'roundrect' Then Result := eRoundedRectangular
    Else If S = 'octagonal' Then Result := eOctagonal
    Else Result := eRounded;
End;

Function HoleShape(S : String) : Integer;
Begin
    If S = 'square' Then Result := eSquareHole
    Else If S = 'slot' Then Result := eSlotHole
    Else Result := eRoundHole;
End;

Procedure SkipBlock;
Begin
    While EbIdx < EbLines.Count Do
    Begin
        EbSplit(EbLines[EbIdx]);
        If EbF[0] = 'END' Then Break;
        Inc(EbIdx);
    End;
End;

Function MissingStep(Folder : String) : String;
{ The first STEP file a BODY line of the current block names that is not there ('' when all are). Checked before the
  old library is moved aside, so a library Altium cannot build is left on disk as it was. }
Var
    k : Integer;
Begin
    Result := '';
    k := EbIdx + 1;
    While k < EbLines.Count Do
    Begin
        EbSplit(EbLines[k]);
        If EbF[0] = 'END' Then Break;
        If (EbF[0] = 'BODY') And (EbF.Count > 4) Then
            If (EbF[4] <> '') And (Not FileExists(Folder + EbF[4])) Then
            Begin
                Result := 'STEP file missing: ' + Folder + EbF[4];
                Break;
            End;
        Inc(k);
    End;
End;

Procedure CloseIfOpen(Path : String);
Var
    Doc : IServerDocument;
Begin
    Doc := Client.GetDocumentByPath(Path);
    If Doc <> Nil Then
        Client.CloseDocument(Doc);
End;

{ The old library file is moved aside (Path + '.ecadlib_old') before Altium writes the new one, and removed only once
  the new one is on disk and not empty: a build that fails puts the old file back, so it never leaves the library
  missing. }
Function EbSize(Path : String) : Integer;
Var
    H : Integer;
Begin
    Result := 0;
    If Not FileExists(Path) Then Exit;
    H := FileOpen(Path, 64);                   { fmOpenRead or fmShareDenyNone }
    If H < 0 Then
    Begin
        Result := 1;                           { there, but it cannot be read now: not counted as missing }
        Exit;
    End;
    Result := FileSeek(H, 0, 2);
    FileClose(H);
End;

Function EbSetAside(Path : String) : String;
{ '' when Path can be written: the old file, if any, is now Path + '.ecadlib_old'. Else why not (Path is untouched). }
Var
    Bak : String;
Begin
    Result := '';
    Bak := Path + '.ecadlib_old';
    If FileExists(Bak) And Not FileExists(Path) Then
        RenameFile(Bak, Path);                 { left by a build that was cut short }
    If Not FileExists(Path) Then Exit;
    If FileExists(Bak) Then
        DeleteFile(Bak);
    If Not RenameFile(Path, Bak) Then
        Result := 'could not move the old file aside to ' + Bak + ' (is it open in another program?); the library file was left as it was';
End;

Function EbPutBack(Path : String) : String;
{ After a failed build: the old file back at Path. Says what happened ('' when there was no old file). }
Var
    Bak : String;
Begin
    Result := '';
    Bak := Path + '.ecadlib_old';
    CloseIfOpen(Path);
    If Not FileExists(Bak) Then Exit;
    If FileExists(Path) Then
        DeleteFile(Path);
    If RenameFile(Bak, Path) Then
        Result := ' (the previous library file was kept)'
    Else
        Result := ' (the previous library file is at ' + Bak + ')';
End;

Function EbFinish(Path : String; Kind : String; Done : String) : String;
{ The report line once Altium has saved Path: OK only when the file is there and not empty. }
Begin
    If EbSize(Path) > 0 Then
    Begin
        If FileExists(Path + '.ecadlib_old') Then
            DeleteFile(Path + '.ecadlib_old');
        Result := 'OK' + #9 + Kind + #9 + Path + #9 + Done;
    End
    Else
        Result := 'FAILED' + #9 + Kind + #9 + Path + #9 + 'Altium did not save the file' + EbPutBack(Path);
End;

{ ------------------------------------------------------------------ footprints }
Procedure AddPrim(Comp : IPCB_LibComponent; Prim : IPCB_Primitive);
Begin
    Comp.AddPCBObject(Prim);
    PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
End;

Function EbContour(First : Integer) : IPCB_Contour;
Var
    k, Cnt : Integer;
Begin
    Result := PCBServer.PCBContourFactory;
    Cnt := EbN(First);
    For k := 0 To Cnt - 1 Do
        Result.AddPoint(EbN(First + 1 + 2 * k), EbN(First + 2 + 2 * k));
End;

Procedure MakePad(Comp : IPCB_LibComponent);
Var
    Pad   : IPCB_Pad;
    Cache : TPadCache;
Begin
    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    Pad.Mode := ePadMode_Simple;
    Pad.Name := EbF[1];
    Pad.X := EbN(2);
    Pad.Y := EbN(3);
    Pad.TopXSize := EbN(4);
    Pad.TopYSize := EbN(5);
    Pad.MidXSize := EbN(4);
    Pad.MidYSize := EbN(5);
    Pad.BotXSize := EbN(4);
    Pad.BotYSize := EbN(5);
    Pad.TopShape := PadShape(EbF[7]);
    Pad.MidShape := PadShape(EbF[7]);
    Pad.BotShape := PadShape(EbF[7]);
    Pad.Rotation := EbAng(6);
    Pad.Layer := PcbLayer(EbN(9));
    If EbN(10) > 0 Then
    Begin
        Pad.HoleSize := EbN(10);
        Pad.HoleType := HoleShape(EbF[11]);
        If EbF[11] = 'slot' Then
            Pad.HoleWidth := EbN(12);
        Pad.HoleRotation := EbAng(13);
        Pad.Plated := EbN(14) = 1;
    End
    Else
        Pad.HoleSize := 0;
    If EbF[7] = 'roundrect' Then
    Begin
        Pad.SetState_StackCRPctOnLayer(eTopLayer, EbN(8));
        Pad.SetState_StackCRPctOnLayer(eMidLayer1, EbN(8));
        Pad.SetState_StackCRPctOnLayer(eBottomLayer, EbN(8));
    End;
    AddPrim(Comp, Pad);
    { mask expansions go through the pad cache: in Altium 26 PasteMaskExpansion / SolderMaskExpansion are read-only
      on the pad (assigning them crashes the script engine). Rule pads are set too: the first footprint of a session
      otherwise keeps an invalid cache and is saved with no mask expansion mode at all. }
    Cache := Pad.GetState_Cache;
    If EbF[15] = 'manual' Then
    Begin
        Cache.PasteMaskExpansion := EbN(16);
        Cache.PasteMaskExpansionValid := eCacheManual;
    End
    Else
        Cache.PasteMaskExpansionValid := eCacheValid;
    If EbF[17] = 'manual' Then
    Begin
        Cache.SolderMaskExpansion := EbN(18);
        Cache.SolderMaskExpansionValid := eCacheManual;
    End
    Else
        Cache.SolderMaskExpansionValid := eCacheValid;
    Pad.SetState_Cache(Cache);
End;

Procedure MakeTrack(Comp : IPCB_LibComponent);
Var
    T : IPCB_Track;
Begin
    T := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    T.X1 := EbN(1); T.Y1 := EbN(2); T.X2 := EbN(3); T.Y2 := EbN(4);
    T.Width := EbN(5);
    T.Layer := PcbLayer(EbN(6));
    AddPrim(Comp, T);
End;

Procedure MakeArc(Comp : IPCB_LibComponent);
Var
    A : IPCB_Arc;
Begin
    A := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
    A.XCenter := EbN(1); A.YCenter := EbN(2); A.Radius := EbN(3);
    A.StartAngle := EbAng(4); A.EndAngle := EbAng(5);
    A.LineWidth := EbN(6);
    A.Layer := PcbLayer(EbN(7));
    AddPrim(Comp, A);
End;

Procedure MakeFill(Comp : IPCB_LibComponent);
Var
    Fl : IPCB_Fill;
Begin
    Fl := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
    Fl.X1Location := EbN(1); Fl.Y1Location := EbN(2); Fl.X2Location := EbN(3); Fl.Y2Location := EbN(4);
    Fl.Rotation := 0;
    Fl.Layer := PcbLayer(EbN(5));
    AddPrim(Comp, Fl);
End;

Procedure MakeRegion(Comp : IPCB_LibComponent);
Var
    R : IPCB_Region;
Begin
    R := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    R.SetOutlineContour(EbContour(3));
    R.Layer := PcbLayer(EbN(1));
    If EbN(2) = 1 Then
        R.IsKeepout := True;
    AddPrim(Comp, R);
End;

Procedure MakeText(Comp : IPCB_LibComponent);
Var
    T : IPCB_Text;
Begin
    T := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
    T.UseTTFonts := False;
    T.Text := EbF[1];
    T.XLocation := EbN(2); T.YLocation := EbN(3);
    T.Size := EbN(4);
    T.Width := EbN(5);
    T.Rotation := EbAng(6);
    T.Layer := PcbLayer(EbN(7));
    AddPrim(Comp, T);
End;

Procedure MakeBody(Comp : IPCB_LibComponent; Folder : String);
Var
    B : IPCB_ComponentBody;
    M : IPCB_Model;
Begin
    B := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
    B.SetOutlineContour(EbContour(9));
    B.Layer := PcbLayer(EbN(1));
    B.BodyProjection := eBoardSide_Top;
    If EbF[4] <> '' Then
    Begin
        If Not FileExists(Folder + EbF[4]) Then
        Begin
            EbErr := 'STEP file missing: ' + Folder + EbF[4];
            Exit;
        End;
        M := B.ModelFactory_FromFilename(Folder + EbF[4], True);
        If M <> Nil Then
            M.SetState(EbAng(5), EbAng(6), EbAng(7), EbN(8));
    End
    Else
        M := B.ModelFactory_CreateExtruded(EbN(3), EbN(2), $808080);
    If M = Nil Then
    Begin
        EbErr := 'Altium made no 3D model for a body (' + EbF[4] + ')';
        Exit;
    End;
    B.Model := M;
    B.SetState_FromModel;
    AddPrim(Comp, B);
End;

Function BuildPcbLib(Folder : String) : String;
Var
    Path    : String;
    Doc     : IServerDocument;
    Lib     : IPCB_Library;
    Blank   : IPCB_LibComponent;
    Comp    : IPCB_LibComponent;
    Kind    : String;
    Count   : Integer;
Begin
    Path := Folder + EbF[1];
    EbErr := MissingStep(Folder);
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Result := 'FAILED' + #9 + 'PCBLIB' + #9 + Path + #9 + EbErr + ' (the library file was left as it was)';
        Exit;
    End;
    CloseIfOpen(Path);
    EbErr := EbSetAside(Path);
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Result := 'FAILED' + #9 + 'PCBLIB' + #9 + Path + #9 + EbErr;
        Exit;
    End;
    Doc := Client.OpenNewDocument('PCBLIB', Path, '', False);
    Client.ShowDocument(Doc);
    Lib := PCBServer.GetCurrentPCBLibrary;
    Blank := Lib.CurrentComponent;
    Comp := Nil;
    Count := 0;
    PCBServer.PreProcess;
    Inc(EbIdx);
    While EbIdx < EbLines.Count Do
    Begin
        EbSplit(EbLines[EbIdx]);
        Kind := EbF[0];
        If Kind = 'END' Then Break;
        If Kind = 'FOOTPRINT' Then
        Begin
            Comp := PCBServer.CreatePCBLibComp;
            Comp.Name := EbF[1];
            Comp.Description := EbF[2];
            Comp.Height := EbN(3);
            Lib.RegisterComponent(Comp);
            Inc(Count);
        End
        Else If Kind = 'PAD' Then MakePad(Comp)
        Else If Kind = 'TRACK' Then MakeTrack(Comp)
        Else If Kind = 'ARC' Then MakeArc(Comp)
        Else If Kind = 'FILL' Then MakeFill(Comp)
        Else If Kind = 'REGION' Then MakeRegion(Comp)
        Else If Kind = 'TEXT' Then MakeText(Comp)
        Else If Kind = 'BODY' Then MakeBody(Comp, Folder);
        If EbErr <> '' Then Break;
        Inc(EbIdx);
    End;
    PCBServer.PostProcess;
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Client.CloseDocument(Doc);
        Result := 'FAILED' + #9 + 'PCBLIB' + #9 + Path + #9 + EbErr + EbPutBack(Path);
        Exit;
    End;
    If (Blank <> Nil) And (Count > 0) Then
        Lib.RemoveComponent(Blank);
    If Comp <> Nil Then
        Lib.CurrentComponent := Comp;
    Doc.SetModified(True);
    Doc.DoFileSave('PCBLIB');
    Client.CloseDocument(Doc);
    Result := EbFinish(Path, 'PCBLIB', IntToStr(Count) + ' footprint(s)');
End;

{ ------------------------------------------------------------------ symbols }
Procedure Own(Comp : ISch_Component; O : ISch_GraphicalObject);
Begin
    O.OwnerPartId := EbN(1);
    O.OwnerPartDisplayMode := EbN(2);
    Comp.AddSchObject(O);
End;

Procedure Filled(O : ISch_GraphicalObject; Fill : String);
Begin
    O.Color := 128;
    If Fill = 'body' Then
    Begin
        O.AreaColor := 11599871;
        O.IsSolid := True;
    End
    Else If Fill = 'line' Then
    Begin
        O.AreaColor := 128;
        O.IsSolid := True;
    End
    Else
        O.IsSolid := False;
End;

Procedure Vertices(O : ISch_Polyline; First : Integer);
Var
    k, Cnt : Integer;
Begin
    Cnt := EbN(First);
    O.VerticesCount := Cnt;
    For k := 0 To Cnt - 1 Do
        O.Vertex[k + 1] := Point(EbSchC(First + 1 + 2 * k), EbSchC(First + 2 + 2 * k));
End;

Procedure MakePin(Comp : ISch_Component);
Var
    P : ISch_Pin;
Begin
    P := SchServer.SchObjectFactory(ePin, eCreate_Default);
    P.Designator := EbF[3];
    P.Name := EbF[4];
    P.Electrical := EbN(5);
    P.Orientation := EbN(6);
    P.Location := Point(EbSchC(7), EbSchC(8));
    P.PinLength := EbSchC(9);
    P.ShowName := EbN(10) = 1;
    P.ShowDesignator := EbN(11) = 1;
    P.IsHidden := EbN(12) = 1;
    P.Symbol_InnerEdge := EbN(13);
    P.Symbol_OuterEdge := EbN(14);
    P.Symbol_Inner := EbN(15);
    P.Symbol_Outer := EbN(16);
    P.Description := EbF[17];
    If EbF[18] <> '' Then
        P.HiddenNetName := EbF[18];
    Own(Comp, P);
End;

Procedure MakeRect(Comp : ISch_Component);
Var
    R : ISch_Rectangle;
Begin
    R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    R.Location := Point(EbSchC(3), EbSchC(4));
    R.Corner := Point(EbSchC(5), EbSchC(6));
    R.LineWidth := EbN(7);
    Filled(R, EbF[8]);
    Own(Comp, R);
End;

Procedure MakeLine(Comp : ISch_Component);
Var
    L : ISch_Line;
Begin
    L := SchServer.SchObjectFactory(eLine, eCreate_Default);
    L.Location := Point(EbSchC(3), EbSchC(4));
    L.Corner := Point(EbSchC(5), EbSchC(6));
    L.LineWidth := EbN(7);
    L.Color := 128;
    Own(Comp, L);
End;

Procedure MakePoly(Comp : ISch_Component; Closed : Boolean);
Var
    P : ISch_Polyline;
Begin
    If Closed Then
    Begin
        P := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
        P.LineWidth := EbN(3);
        Filled(P, EbF[4]);
        Vertices(P, 5);
    End
    Else
    Begin
        P := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
        P.LineWidth := EbN(3);
        P.Color := 128;
        Vertices(P, 4);
    End;
    Own(Comp, P);
End;

Procedure MakeSchArc(Comp : ISch_Component);
Var
    A : ISch_Arc;
Begin
    A := SchServer.SchObjectFactory(eArc, eCreate_Default);
    A.Location := Point(EbSchC(3), EbSchC(4));
    A.Radius := EbSchC(5);
    A.StartAngle := EbAng(6);
    A.EndAngle := EbAng(7);
    A.LineWidth := EbN(8);
    A.Color := 128;
    Own(Comp, A);
End;

Procedure MakeCircle(Comp : ISch_Component);
Var
    E : ISch_Ellipse;
Begin
    E := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
    E.Location := Point(EbSchC(3), EbSchC(4));
    E.Radius := EbSchC(5);
    E.SecondaryRadius := EbSchC(5);
    E.LineWidth := EbN(6);
    Filled(E, EbF[7]);
    Own(Comp, E);
End;

Procedure MakeLabel(Comp : ISch_Component);
Var
    L : ISch_Label;
Begin
    L := SchServer.SchObjectFactory(eLabel, eCreate_Default);
    L.Location := Point(EbSchC(3), EbSchC(4));
    L.Orientation := EbN(5);
    L.Text := EbF[6];
    L.Color := 128;
    Own(Comp, L);
End;

Procedure MakeParam(Comp : ISch_Component);
Var
    P : ISch_Parameter;
Begin
    P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    P.Name := EbF[1];
    P.Text := EbF[2];
    P.IsHidden := True;
    Comp.AddSchObject(P);
End;

Procedure LinkFootprint(Comp : ISch_Component);
Var
    Impl : ISch_Implementation;
Begin
    Impl := Comp.AddSchImplementation;
    Impl.ClearAllDatafileLinks;
    Impl.ModelName := EbF[1];
    Impl.ModelType := 'PCBLIB';
    Impl.AddDataFileLink(EbF[1], '', 'PCBLIB');
    Impl.IsCurrent := True;
End;

Function BuildSchLib(Folder : String) : String;
Var
    Path  : String;
    Doc   : IServerDocument;
    Lib   : ISch_Lib;
    Blank : ISch_Component;
    Comp  : ISch_Component;
    Kind  : String;
    Count : Integer;
Begin
    Path := Folder + EbF[1];
    CloseIfOpen(Path);
    EbErr := EbSetAside(Path);
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Result := 'FAILED' + #9 + 'SCHLIB' + #9 + Path + #9 + EbErr;
        Exit;
    End;
    Doc := Client.OpenNewDocument('SCHLIB', Path, '', False);
    Client.ShowDocument(Doc);
    Lib := SchServer.GetCurrentSchDocument;
    Blank := Lib.CurrentSchComponent;
    Comp := Nil;
    Count := 0;
    Inc(EbIdx);
    While EbIdx < EbLines.Count Do
    Begin
        EbSplit(EbLines[EbIdx]);
        Kind := EbF[0];
        If Kind = 'END' Then Break;
        If Kind = 'SYMBOL' Then
        Begin
            If Comp <> Nil Then
                SchServer.RobotManager.SendMessage(Lib.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
            Comp := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
            Comp.CurrentPartID := 1;
            Comp.DisplayMode := 0;
            Comp.LibReference := EbF[1];
            Comp.ComponentDescription := EbF[2];
            Comp.Designator.Text := EbF[3];
            Comp.Comment.Text := EbF[4];
            Comp.PartCount := EbN(5);          { always: a new component has 0 parts (saved as PARTCOUNT=1, Altium wants 2) }
            If EbN(6) > 1 Then
                Comp.DisplayModeCount := EbN(6);
            Lib.AddSchComponent(Comp);
            Inc(Count);
        End
        Else If Kind = 'PIN' Then MakePin(Comp)
        Else If Kind = 'RECT' Then MakeRect(Comp)
        Else If Kind = 'LINE' Then MakeLine(Comp)
        Else If Kind = 'POLYLINE' Then MakePoly(Comp, False)
        Else If Kind = 'POLYGON' Then MakePoly(Comp, True)
        Else If Kind = 'SARC' Then MakeSchArc(Comp)
        Else If Kind = 'CIRCLE' Then MakeCircle(Comp)
        Else If Kind = 'LABEL' Then MakeLabel(Comp)
        Else If Kind = 'PARAM' Then MakeParam(Comp)
        Else If Kind = 'DESIGNATOR' Then Comp.Designator.Location := Point(EbSchC(1), EbSchC(2))
        Else If Kind = 'COMMENT' Then Comp.Comment.Location := Point(EbSchC(1), EbSchC(2))
        Else If Kind = 'FOOTPRINTLINK' Then LinkFootprint(Comp);
        Inc(EbIdx);
    End;
    If Comp <> Nil Then
    Begin
        SchServer.RobotManager.SendMessage(Lib.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
        Lib.CurrentSchComponent := Comp;
    End;
    If (Blank <> Nil) And (Count > 0) Then
        Lib.RemoveSchComponent(Blank);
    Lib.GraphicallyInvalidate;
    Doc.SetModified(True);
    Doc.DoFileSave('SCHLIB');
    Client.CloseDocument(Doc);
    Result := EbFinish(Path, 'SCHLIB', IntToStr(Count) + ' symbol(s)');
End;

{ ------------------------------------------------------------------ entry points }
Procedure BuildLibraries(Folder : String);
Var
    Kind, Name, Line : String;
Begin
    EbF := TStringList.Create;
    EbLines := TStringList.Create;
    EbReport := TStringList.Create;
    Try
        If Not FileExists(Folder + 'ecadlib_build.txt') Then
        Begin
            EbReport.Add('FAILED' + #9 + 'ecadlib_build.txt' + #9 + Folder + #9 + 'no build file here');
            Exit;
        End;
        EbLines.LoadFromFile(Folder + 'ecadlib_build.txt');
        EbIdx := 0;
        While EbIdx < EbLines.Count Do
        Begin
            EbSplit(EbLines[EbIdx]);
            Kind := EbF[0];
            If (Kind = 'PCBLIB') Or (Kind = 'SCHLIB') Then
            Begin
                Name := EbF[1];
                EbErr := '';
                Try
                    If Kind = 'PCBLIB' Then
                        Line := BuildPcbLib(Folder)
                    Else
                        Line := BuildSchLib(Folder);
                    EbReport.Add(Line);
                Except
                    EbReport.Add('FAILED' + #9 + Kind + #9 + Folder + Name + #9 + 'Altium raised an error while building it' + EbPutBack(Folder + Name));
                    SkipBlock;
                End;
            End;
            Inc(EbIdx);
        End;
    Finally
        EbReport.SaveToFile(Folder + 'ecadlib_build_report.txt');
        EbReport.Free;
        EbLines.Free;
        EbF.Free;
    End;
End;

Procedure BuildLibrariesInAltium;
Begin
    BuildLibraries(ExtractFilePath(GetRunningScriptProjectName));
    ShowMessage('ecadlib: libraries built by Altium. See ecadlib_build_report.txt next to this script.');
End;
"""


# ------------------------------------------------------------------ compare what Altium saved with the recipes
def _recipe_items(rp):
    """{name: {"pads"|"pins": [fields...]}} from one recipe file."""
    out, cur = {}, None
    for ln in Path(rp).read_text(encoding="utf-8").splitlines():
        f = ln.split("\t")
        if f[0] in ("FOOTPRINT", "SYMBOL"):
            cur = out.setdefault(f[1], {"pads": [], "pins": [], "params": {}, "footprint": None})
            if f[0] == "SYMBOL":
                cur["parts"] = int(f[5])
        elif f[0] == "PARAM" and cur is not None:
            cur["params"][f[1]] = f[2]
        elif f[0] == "FOOTPRINTLINK" and cur is not None:
            cur["footprint"] = f[1]
        elif f[0] == "PAD" and cur is not None:
            pad = {"designator": f[1], "x": int(f[2]), "y": int(f[3]), "w": int(f[4]), "h": int(f[5]), "hole": int(f[10])}
            if len(f) > 18:                     # rotation, layer, plating, paste / mask: an edit there is an edit too
                pad.update(rot=int(f[6]) / 1000, layer=int(f[9]), plated=f[14] == "1",
                           paste=(f[15] == "manual", int(f[16])), mask=(f[17] == "manual", int(f[18])))
            cur["pads"].append(pad)
        elif f[0] == "PIN" and cur is not None:
            cur["pins"].append({"num": f[3], "name": f[4], "x": round(int(f[7]) / 10000), "y": round(int(f[8]) / 10000),
                                "length": round(int(f[9]) / 10000), "orient": int(f[6]), "etype": int(f[5])})
    return out


def _key(d):
    """Sort key of a pad (designator) or pin (num); an unnumbered pad (NPTH mounting hole) has designator ''."""
    return (str((d["designator"] if "designator" in d else d.get("num")) or ""), d["x"], d["y"])


def _pad_diffs(a, b, tol):
    """The fields beyond x/y/w/h/hole where Altium's pad b differs from the recipe's pad a (old recipes: none)."""
    bad = []
    if "rot" not in a:
        return bad
    d = abs(a["rot"] - float(b.get("rot") or 0)) % 360
    if min(d, 360 - d) > 0.01:
        bad.append(f"rot {float(b.get('rot') or 0):g} != {a['rot']:g}")
    if b.get("layer") is not None and b["layer"] != a["layer"]:
        bad.append(f"layer {b['layer']} != {a['layer']}")
    if a["hole"] and bool(b.get("plated", True)) != a["plated"]:
        bad.append(f"plated {bool(b.get('plated'))} != {a['plated']}")
    for k in ("paste", "mask"):
        manual, v = a[k]
        mode = b.get(f"{k}_mode")
        if mode is None:
            continue
        if (mode == 2) != manual:
            bad.append(f"{k} {'manual' if mode == 2 else 'rule'} != {'manual' if manual else 'rule'}")
        elif manual and b.get(k) is not None and abs(b[k] - v) > tol:
            bad.append(f"{k} {b[k]} != {v}")
    return bad


def best_fit_name(want, got):
    """True when got is want as a cp1252 pin record holds it: every character cp1252 can store is the same, and each
    one it can't ('Φ', 'ϕ') became one character - '?' as ecadlib writes it, or Windows' best-fit substitute ('F')
    that Altium writes when it rebuilds the library. The SchLib pin record has no UTF-8 copy of the name."""
    if not AL.not_cp1252(want) or len(want) != len(got):
        return False
    return all(a == b or (AL.not_cp1252(a) and not AL.not_cp1252(b)) for a, b in zip(want, got))


def compare_one(lib, rp, tol=10, known=None):
    """-> list of problems ('' list = match) between an Altium-saved library and its recipe. tol: internal units.
    known: a list that gets the expected differences (pin names cp1252 can't hold, see best_fit_name) instead."""
    known = [] if known is None else known
    want = _recipe_items(rp)
    probs = []
    if lib.suffix.lower() == ".pcblib":
        got = {c["name"]: [r for r in c["records"] if r["type"] == "pad"] for c in AL.read_pcblib(lib)}
        for name, w in want.items():
            if name not in got:
                probs.append(f"{lib.name}: footprint {name} missing")
                continue
            g = got[name]
            if len(g) != len(w["pads"]):
                probs.append(f"{lib.name}/{name}: {len(g)} pads, recipe has {len(w['pads'])}")
            for a, b in zip(sorted(w["pads"], key=_key), sorted(g, key=_key)):
                bad = [f"{k} {b[k]} != {a[k]}" for k in ("x", "y", "w", "h", "hole") if abs(a[k] - b[k]) > tol]
                bad += _pad_diffs(a, b, tol)
                if a["designator"] != b["designator"] or bad:
                    probs.append(f"{lib.name}/{name} pad {a['designator']}: " + ", ".join(bad)
                                 + ("" if a["designator"] == b["designator"] else f" (got {b['designator']})"))
    else:
        _, comps = AL.read_schlib(lib)
        recs = {c["name"]: c["records"] for c in comps}
        got = {n: [r for r in rs if r.get("RECORD") == "2"] for n, rs in recs.items()}
        for name, w in want.items():
            if name not in got:
                probs.append(f"{lib.name}: symbol {name} missing")
                continue
            g = got[name]
            head = next((r for r in recs[name] if r.get("RECORD") == "1"), {})
            if str(head.get("PARTCOUNT")) != str(w["parts"] + 1):      # Altium saves parts + 1
                probs.append(f"{lib.name}/{name}: PARTCOUNT {head.get('PARTCOUNT')} != {w['parts'] + 1}")
            params = {r.get("NAME"): r.get("TEXT", "") for r in recs[name] if r.get("RECORD") == "41"}
            for k, v in w["params"].items():
                if params.get(k) != v:
                    probs.append(f"{lib.name}/{name}: parameter {k} {params.get(k)!r} != {v!r}")
            models = [r.get("MODELNAME") for r in recs[name] if r.get("RECORD") == "45"]
            if w["footprint"] and w["footprint"] not in models:
                probs.append(f"{lib.name}/{name}: footprint link {w['footprint']} missing (got {models})")
            if len(g) != len(w["pins"]):
                probs.append(f"{lib.name}/{name}: {len(g)} pins, recipe has {len(w['pins'])}")
            for a, b in zip(sorted(w["pins"], key=_key), sorted(g, key=_key)):
                bad = [k for k in ("x", "y", "length", "orient", "etype") if a[k] != b[k]]
                if a["name"] != b["name"] and best_fit_name(a["name"], b["name"]):
                    known.append(f"{lib.name}/{name} pin {a['num']}: name {b['name']!r} for {a['name']!r} (cp1252 can't hold "
                                 f"{AL.not_cp1252(a['name'])!r}; the SchLib pin record has no Unicode copy)")
                    b = dict(b, name=a["name"])
                if a["num"] != b["num"] or a["name"] != b["name"] or bad:
                    probs.append(f"{lib.name}/{name} pin {a['num']}: " + ", ".join(f"{k} {b[k]} != {a[k]}" for k in bad)
                                 + ("" if a["name"] == b["name"] else f" name {b['name']!r} != {a['name']!r}"))
    return probs


def compare(folder):
    """-> {"checked": n, "problems": [...], "unreadable": [...], "known": [...]} for every library under folder that
    has a recipe. known: expected differences (best_fit_name) that are reported but are not problems."""
    folder = Path(folder)
    rep = {"checked": 0, "problems": [], "unreadable": [], "known": []}
    for rel, rp in recipes(folder):
        try:
            rep["problems"] += compare_one(folder / rel, rp, known=rep["known"])
            rep["checked"] += 1
        except (ValueError, OSError) as e:
            rep["unreadable"].append(f"{rel}: {e}")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib altium-native", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="collect the recipes of a library folder and write the Altium script project")
    w.add_argument("folder")
    w.add_argument("--out", help="where the script project goes (default: the library folder)")
    w.add_argument("--rebuild-all", action="store_true",
                   help="build every library again, also ones already built and ones changed in Altium since")
    c = sub.add_parser("compare", help="compare the libraries Altium saved with their recipes")
    c.add_argument("folder")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "write":
        rep = write(a.folder, a.out, a.rebuild_all)
        print(f"{len(rep['libraries'])} libraries in {Path(rep['out']) / BUILD}"
              + (f"; {len(rep['up_to_date'])} already built by Altium" if rep["up_to_date"] else ""))
        for rel in rep["edited"]:
            print(f"  kept {rel}: {EDITED_NOTE}")
        print(f"In Altium: File > Open {Path(rep['out']) / PROJECT}, then File > Run Script > BuildLibrariesInAltium")
        return 0 if rep["libraries"] or rep["up_to_date"] or rep["edited"] else 1
    rep = compare(a.folder)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"{rep['checked']} libraries checked, {len(rep['problems'])} differences, {len(rep['unreadable'])} unreadable")
        for p in rep["problems"] + rep["unreadable"]:
            print("  " + p)
        if rep["known"]:
            print(f"{len(rep['known'])} known differences (not errors):")
            for p in rep["known"]:
                print("  " + p)
    return 1 if rep["problems"] or rep["unreadable"] else 0


if __name__ == "__main__":
    sys.exit(main())
