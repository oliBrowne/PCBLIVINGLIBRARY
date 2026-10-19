"""The board side of assembly-ready parts: read where every part sits (a KiCad .kicad_pcb, or an Altium pick-and-place
CSV), settle each part's assembly rotation, and write the assembler's BOM and CPL with a report of what was checked.

Each part's rotation comes from, in order (the first that knows wins; nothing is ever guessed silently):
  1. override    the profile's per-part override (cited)
  2. verified    the board's own pads registered on the assembler's footprint for the part's LCSC number
                 (JLCPCB: the EasyEDA footprint, behind [providers] unofficial); 'assumed' when a polarized part's
                 cathode / + came from a polarity mark or ecadlib's pad-1 rule, not pin names (a review item)
  3. parameter   the part's Assembly Rotation / Assembly Offset parameters (written by the librarian's check), when
                 they were measured for this LCSC number (and footprint); the pads stay unchecked
  4. table       the profile's 0 deg pose for the package family, on the pads' canonical frame
  5. unknown     the part is left out of the BOM and CPL and listed in the report
Held parts (pads incompatible, conflicting LCSC fields, a designator used twice, an unreadable row, strict findings)
are left out of the files too: nothing goes to the assembler on a guess.

CPL point and angle (assembly.py, docs/assembly.md): Mid X / Mid Y are the board position of the assembler's footprint
origin (our footprint origin + Assembly Offset, turned with the part), top-view coordinates in mm, +y up, from the
board's origin (KiCad: the drill / place origin when the Gerbers use it). Rotation is the angle as the assembler looks at
the part (top: the board angle; bottom: seen from the bottom) plus Assembly Rotation."""
import csv, io, json, math, re
from dataclasses import dataclass, field
from pathlib import Path

from . import assembly as A
from . import sexpr

LAYER_TOP, LAYER_BOTTOM = "top", "bottom"


@dataclass
class Placement:
    ref: str
    value: str = ""
    footprint: str = ""               # footprint name, without the library
    x: float = 0.0                    # the footprint origin, CPL frame: mm, +y up, from the board origin
    y: float = 0.0
    angle: float = 0.0                # the board angle, deg CCW seen from the top (KiCad / Altium)
    side: str = LAYER_TOP
    tool: str = "kicad"               # kicad | altium: how a bottom-side part is mirrored
    fields: dict = field(default_factory=dict)
    fp: object = None                 # assembly.Footprint in the library frame (bottom parts un-mirrored), when known
    skip: str = ""                    # why the part is not placed (excluded from position files, DNP, no pads)
    tht: bool = False
    point: str = "origin"             # what x / y are: the footprint origin, or Altium's Center-X/Y (no offset known)
    problem: str = ""                 # what is wrong with the part's row in the file (held: never placed by a guess)


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def field_of(fields, names):
    """The first non-empty field among `names`, case-insensitive."""
    low = {str(k).strip().lower(): v for k, v in (fields or {}).items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, "") and str(v).strip() not in ("", "~"):
            return str(v).strip()
    return ""


def lcsc_fields(fields, prof):
    """[(field, value)]: every field of a part that names its LCSC (assembler) part number, in the order they are
    trusted: the profile's part-number fields, then Supplier N = LCSC / JLCPCB with its Supplier Part Number N (as the
    librarian reads them)."""
    pn = prof.get("part_number") or {}
    low = {str(k).strip().lower(): (str(k).strip(), str(v).strip()) for k, v in (fields or {}).items()}
    out = []
    for n in [pn.get("parameter", "LCSC Part")] + list(pn.get("fields", [])):
        k, v = low.get(n.lower(), ("", ""))
        if v and v != "~" and (k, v) not in out:
            out.append((k, v))
    for i in range(1, 10):
        k, v = low.get(f"supplier part number {i}", ("", ""))
        if low.get(f"supplier {i}", ("", ""))[1].upper() in ("LCSC", "JLCPCB", "JLC") and v and (k, v) not in out:
            out.append((k, v))
    return out


def part_number(v, prof):
    """v as an LCSC part number ('c25804 ' -> 'C25804'), '' when it is not one."""
    v = str(v or "").upper().replace(" ", "")
    return v if re.fullmatch((prof.get("part_number") or {}).get("pattern", r"C\d{1,9}"), v) else ""


def lcsc_of(fields, prof):
    """The LCSC (assembler) part number from a part's fields, '' when none or not a part number."""
    return next((n for n in (part_number(v, prof) for _, v in lcsc_fields(fields, prof)) if n), "")


def lcsc_problems(fields, prof):
    """What is wrong with a part's LCSC fields: two fields naming different parts (the order would follow one of them
    silently), a value that is no part number."""
    got = [(k, v, part_number(v, prof)) for k, v in lcsc_fields(fields, prof)]
    out = []
    if len({n for _, _, n in got if n}) > 1:
        out.append("conflicting LCSC numbers: " + ", ".join(f"{k} = {n}" for k, _, n in got if n)
                   + ": keep one, or make them agree")
    out += [f"{k} {v!r} is not an LCSC part number (C and digits)" for k, v, n in got if not n]
    return out


# ---------------------------------------------------------------- KiCad board
def _yes(v):
    return str(v).strip().lower() in ("yes", "true", "1")


def board_origin(tree, mode="auto"):
    """(x, y, why): the board origin the CPL is measured from. auto: the drill / place (aux) origin when the plot
    settings use it for the Gerbers (useauxorigin), else the page origin; aux; page."""
    setup = sexpr.first(tree, "setup") or []
    aux = sexpr.first(setup, "aux_axis_origin")
    ax, ay = (float(aux[1]), float(aux[2])) if aux and len(aux) > 2 else (0.0, 0.0)
    pp = sexpr.first(setup, "pcbplotparams") or []
    use = sexpr.first(pp, "useauxorigin")
    if mode == "aux" or (mode == "auto" and use and len(use) > 1 and _yes(use[1])):
        return ax, ay, "drill / place file origin" + (" (the Gerbers use it)" if mode == "auto" else "")
    return 0.0, 0.0, "page origin" + (" (the Gerbers use it)" if mode == "auto" else "")


def _props(node):
    """A board footprint's fields: KiCad 6+ (property "Name" "value"), KiCad 5 / 6 fp_text reference / value."""
    out = {}
    for t in sexpr.find(node, "fp_text"):
        if len(t) > 2 and str(t[1]) in ("reference", "value"):
            out.setdefault("Reference" if t[1] == "reference" else "Value", str(t[2]))
    for p in sexpr.find(node, "property"):
        if len(p) > 2 and not isinstance(p[1], list):
            out[str(p[1])] = str(p[2]) if not isinstance(p[2], list) else ""
    return out


def read_kicad_board(path, origin="auto"):
    """(placements, info) from a .kicad_pcb. info: origin, origin_why, file, kicad version."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        tree = sexpr.parse(text)
    except Exception as e:                                  # noqa: BLE001 - any parse failure is a bad file
        raise A.AssemblyError(f"{Path(path).name}: not a KiCad board ({e})") from None
    if not tree or str(tree[0]) != "kicad_pcb":
        raise A.AssemblyError(f"{Path(path).name}: not a KiCad board (no kicad_pcb)")
    ox, oy, why = board_origin(tree, origin)
    ver = sexpr.first(tree, "version")
    out = []
    for node in list(sexpr.find(tree, "footprint")) + list(sexpr.find(tree, "module")):
        lib_id = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
        layer = (sexpr.first(node, "layer") or ["layer", "F.Cu"])[1]
        at = sexpr.first(node, "at") or ["at", "0", "0"]
        x, y = float(at[1]), float(at[2])
        ang = float(at[3]) if len(at) > 3 else 0.0
        bottom = str(layer).startswith("B.")
        props = _props(node)
        attr = [str(a) for a in (sexpr.first(node, "attr") or ["attr"])[1:] if not isinstance(a, list)]
        fp = A.from_node(node, f"board:{lib_id}", flip=bottom, angle=ang)
        fp.name = lib_id.split(":")[-1]
        pl = Placement(ref=props.get("Reference", ""), value=props.get("Value", ""), footprint=fp.name,
                       x=round(x - ox, 6), y=round(-(y - oy), 6), angle=ang % 360, side=LAYER_BOTTOM if bottom else LAYER_TOP,
                       tool="kicad", fields=props, fp=fp, tht="through_hole" in attr)
        dnp = "dnp" in attr or _yes(field_of(props, ["DNP", "dnp", "Do not populate"]) or "")
        if "exclude_from_pos_files" in attr:
            pl.skip = "excluded from position files"
        elif dnp:
            pl.skip = "do not populate"
        elif not pl.ref or pl.ref.startswith("#"):
            pl.skip = "no reference designator"
        elif not fp.pins:
            pl.skip = "no pads"
        out.append(pl)
    out.sort(key=lambda p: _natural(p.ref))
    return out, {"file": str(path), "origin": (ox, oy), "origin_why": why, "format": f"KiCad board (version {ver[1] if ver else '?'})"}


# ---------------------------------------------------------------- Altium pick-and-place
_MIL = 0.0254


def _decode(raw):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _key(h):
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def _num_unit(v, unit):
    """'12.7mm' / '500mil' / '12.7' (in `unit`) -> mm."""
    s = str(v or "").strip().lower().replace(",", ".")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(mm|mil)?", s)
    if not m:
        raise ValueError(f"not a coordinate: {v!r}")
    u = m.group(2) or unit
    return float(m.group(1)) * (_MIL if u == "mil" else 1.0)


def read_altium_pnp(path):
    """(placements, info) from an Altium pick-and-place file (Outputs > Assembly Outputs > Generates pick and place
    files, CSV). Extra columns (component parameters added in the Pick and Place Setup, e.g. LCSC Part, Assembly
    Rotation, Assembly Offset) are kept as the part's fields."""
    text = _decode(Path(path).read_bytes())
    unit = "mm"
    m = re.search(r"Units used(?: in this file)?\s*:\s*([^\r\n]*)", text, re.I)
    if m and re.search(r"mil|imperial", m.group(1), re.I):
        unit = "mil"
    rows, header, hi = None, None, -1
    for delim in (",", "\t", ";"):
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        for i, r in enumerate(rows):
            ks = [_key(c) for c in r]
            if "designator" in ks and any(k.startswith("rotation") for k in ks):
                header, hi = r, i
                break
        if header:
            break
    if not header:
        raise A.AssemblyError(f"{Path(path).name}: no header row with Designator and Rotation (an Altium pick-and-place CSV?)")
    ks = [_key(c) for c in header]

    def col(*names):
        for n in names:
            for i, k in enumerate(ks):
                if k == n or (n.endswith("*") and k.startswith(n[:-1])):
                    return i
        return None
    c_ref, c_cmt, c_lay, c_fp = col("designator"), col("comment", "value"), col("layer", "tb", "side"), col("footprint", "pattern")
    c_rx, c_ry = col("refx*", "referencex*"), col("refy*", "referencey*")
    c_cx, c_cy = col("centerx*", "midx*", "centrex*"), col("centery*", "midy*", "centrey*")
    c_rot = col("rotation*")
    colunit = lambda i: "mil" if i is not None and "mil" in ks[i] else "mm" if i is not None and ks[i].endswith("mm") else unit
    use_ref = c_rx is not None and c_ry is not None
    cx_i, cy_i = (c_rx, c_ry) if use_ref else (c_cx, c_cy)
    if cx_i is None or cy_i is None:
        raise A.AssemblyError(f"{Path(path).name}: no Ref-X/Y or Center-X/Y columns")
    known = {i for i in (c_ref, c_cmt, c_lay, c_fp, c_rx, c_ry, c_cx, c_cy, c_rot) if i is not None}
    out = []
    for n, r in enumerate(rows[hi + 1:], hi + 2):
        if not r or not any(c.strip() for c in r):
            continue
        cell = lambda i: r[i].strip() if i is not None and i < len(r) else ""
        ref = cell(c_ref)
        if not ref:
            continue
        problem = ""                                     # a row that cannot be read whole is held and listed, never dropped
        if len(r) <= max(known):
            problem = f"line {n} has {len(r)} cells, the header {len(header)}: the row is cut short"
        elif not cell(c_rot):
            problem = f"line {n}: the Rotation cell is empty"
        try:
            x, y = _num_unit(cell(cx_i), colunit(cx_i)), _num_unit(cell(cy_i), colunit(cy_i))
            ang = float(cell(c_rot) or 0)
        except ValueError as e:
            if not problem:
                raise A.AssemblyError(f"{Path(path).name}: {ref}: {e}") from None
            x = y = ang = 0.0
        lay = cell(c_lay).lower() if c_lay is not None else "top"
        side = LAYER_BOTTOM if lay.startswith("b") else LAYER_TOP
        fields = {header[i].strip(): r[i].strip() for i in range(len(header)) if i not in known and i < len(r)}
        out.append(Placement(ref=ref, value=cell(c_cmt), footprint=cell(c_fp), x=x, y=y, angle=ang % 360, side=side,
                             tool="altium", fields=fields, point="origin" if use_ref else "center", problem=problem))
    out.sort(key=lambda p: _natural(p.ref))
    return out, {"file": str(path), "origin": (0.0, 0.0), "origin_why": "Altium file's own origin",
                 "format": "Altium pick-and-place CSV" + ("" if use_ref else " (Center-X/Y: no Ref-X/Y columns)")}


def merge_bom(placements, bom_path):
    """Fields from a BOM CSV (a Designator column, comma / space separated) onto the placements, where the placement
    lacks them: LCSC Part, Assembly Rotation, ... from an Altium or KiCad BOM export."""
    text = _decode(Path(bom_path).read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    hi = next((i for i, r in enumerate(rows) if "designator" in [_key(c) for c in r]), None)
    if hi is None:
        raise A.AssemblyError(f"{Path(bom_path).name}: no Designator column")
    head = [c.strip() for c in rows[hi]]
    di = [_key(c) for c in head].index("designator")
    by = {}
    for r in rows[hi + 1:]:
        if len(r) <= di:
            continue
        for ref in re.split(r"[\s,;]+", r[di].strip()):
            if ref:
                by[ref.upper()] = {head[i]: r[i].strip() for i in range(min(len(head), len(r))) if i != di}
    n = 0
    for p in placements:
        extra = by.get(p.ref.upper())
        if extra:
            n += 1
            low = {k.lower() for k, v in p.fields.items() if str(v).strip()}
            p.fields.update({k: v for k, v in extra.items() if k.lower() not in low and v})
    return n


def footprints_from(folder):
    """{footprint name: Footprint} from the .kicad_mod files in a folder (an ecadlib KiCad library's .pretty)."""
    out = {}
    for f in sorted(Path(folder).rglob("*.kicad_mod")):
        try:
            out[f.stem] = A.from_kicad_mod(f.read_text(encoding="utf-8"), f"library:{f.name}")
        except A.AssemblyError:
            continue
    return out


def read_board(path, origin="auto"):
    """A .kicad_pcb or an Altium pick-and-place CSV -> (placements, info)."""
    p = Path(path)
    if not p.is_file():
        raise A.AssemblyError(f"{path}: no such file")
    if p.suffix.lower() == ".kicad_pcb":
        return read_kicad_board(p, origin)
    if p.suffix.lower() in (".csv", ".txt"):
        return read_altium_pnp(p)
    raise A.AssemblyError(f"{p.name}: give a KiCad board (.kicad_pcb) or an Altium pick-and-place file (.csv)")


# ---------------------------------------------------------------- placement maths
def view_angle(pl):
    """The part's angle as the assembler looks at it: top as on the board; bottom as seen from the bottom (KiCad:
    180 - the board angle; Altium, whose bottom parts are mirrored left-right: -the angle)."""
    if pl.side == LAYER_TOP:
        return pl.angle % 360
    return (180 - pl.angle) % 360 if pl.tool == "kicad" else (-pl.angle) % 360


def offset_delta(pl, off):
    """The CPL-frame (+y up) vector from the footprint origin to the assembler's origin, for an offset `off` in the
    library footprint's frame (x right, y down)."""
    ox, oy = off
    if pl.side == LAYER_BOTTOM:
        ox, oy = (ox, -oy) if pl.tool == "kicad" else (-ox, oy)
    dx, dy = A.rot(ox, oy, pl.angle)
    return dx, -dy


def cpl_row(pl, correction, off):
    """(mid_x, mid_y, rotation) for a placement; correction None: the view angle uncorrected."""
    dx, dy = offset_delta(pl, off or (0.0, 0.0)) if pl.point == "origin" else (0.0, 0.0)
    r = view_angle(pl) + (correction or 0)
    r %= 360
    return pl.x + dx, pl.y + dy, (int(r) if float(r).is_integer() else round(r, 2))


# ---------------------------------------------------------------- the check
ROTATION_VALUE = re.compile(r"\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:°|º|deg|degrees?)?\s*", re.I)


def _param_assessment(pl, prof, lcsc):
    """(an assessment from the part's own Assembly parameters (the librarian's check), or None; why they were not
    used). They are trusted only for the part they were measured on: the LCSC number in their evidence (else the part's
    LCSC Part) must be the current one, and our footprint, when the evidence names it. A stored pad verdict is never
    taken over: the pads stay unchecked."""
    rv = field_of(pl.fields, ["Assembly Rotation"])
    if not rv:
        return None, ""
    m = ROTATION_VALUE.fullmatch(rv)
    if not m:
        return None, f"the part's Assembly Rotation {rv!r} is not a number of degrees: not used"
    ev = field_of(pl.fields, ["Assembly Evidence"])
    m_ev = re.search(r"\((C\d{1,9})\)", ev or "")
    was = m_ev.group(1) if m_ev else A.lcsc_number(field_of(pl.fields, ["LCSC Part"]))
    if not was:
        return None, (f"the part's Assembly Rotation {rv} does not say which part it was measured for (no LCSC Part, no "
                      "Assembly Evidence): not used (run the check again)")
    if was != lcsc:
        return None, (f"the part's Assembly Rotation {rv} was measured for {was}, the part now has "
                      f"{lcsc or 'no LCSC number'}: not used (run the check again)")
    on = re.search(r" on (\S+)", ev or "")
    if on and pl.footprint and on.group(1).rstrip(";,") != pl.footprint:
        return None, (f"the part's Assembly Rotation {rv} was measured on footprint {on.group(1).rstrip(';,')}, the board "
                      f"has {pl.footprint}: not used (run the check again)")
    v = float(m.group(1).replace(",", "."))
    chk = field_of(pl.fields, ["Assembly Check"])
    return {"profile": prof["profile"], "lcsc": lcsc, "family": None, "polarized": False, "frame": {}, "pads": "unchecked",
            "rotation": "parameter", "turn": A._snap(-v), "offset": A.parse_offset(field_of(pl.fields, ["Assembly Offset"])),
            "residual": None, "reasons": [], "review": [], "hold": [], "rows": [], "fit": None, "theirs": "",
            "notes": [f"from the part's Assembly Rotation {rv}" + (f" (its stored check '{chk}' is not taken over: the pads "
                                                                    "were not checked in this run)" if chk else "")
                      + (f"; {ev}" if ev else "")]}, ""


def _not_found(note):
    """True when the assembler's footprint was asked for and the LCSC number does not exist there (a typo?)."""
    return bool(re.search(r"HTTP 404|no component", note or ""))


def check_part(pl, prof, fetch=None, strict=False, library=None):
    """The assessment for one placement (assembly.assess plus the board's own sources), as a dict with its CPL row.
    fetch(lcsc) -> (Footprint or None, note): the assembler's footprint (cached per run)."""
    lcsc = lcsc_of(pl.fields, prof)
    fp = pl.fp or (library or {}).get(pl.footprint)
    names = [pl.footprint, field_of(pl.fields, ["Footprint"]).split(":")[-1]]
    theirs, note = (None, "no LCSC number") if not lcsc else (fetch(lcsc) if fetch else (None, "not fetched"))
    if fp is not None:
        a = A.assess(fp, theirs, prof, lcsc, names, strict=strict, ours_rule=True)
        if pl.tool == "altium" and a.get("polarity", {}).get("ours") in (A.HOW_RULE, A.HOW_MARK, "not known"):
            a["notes"].append("an Altium pick-and-place file carries no pin names: the cathode / + of our footprint "
                              "comes from its drawing or ecadlib's rule")
    else:
        fam = A.family_of(prof, names)
        a = {"profile": prof["profile"], "lcsc": lcsc, "family": fam, "polarized": bool((prof["families"].get(fam or "") or {}).get("polarized")),
             "frame": {}, "pads": "unchecked", "rotation": "unknown", "turn": None, "offset": (0.0, 0.0), "residual": None,
             "reasons": [], "review": [], "hold": [], "rows": [], "fit": None, "theirs": theirs.package if theirs else "",
             "notes": ["rotation unknown: no pads to check, the file has positions only (give --footprints with the "
                       "library's .kicad_mod files)"]}
        ov = A.override_for(prof, lcsc, names)
        if ov is not None:
            a.update(rotation="override", turn=A._snap(-float(ov["rotation"])))
            if ov.get("offset"):
                a["offset"] = (float(ov["offset"][0]), -float(ov["offset"][1]))
    a["unchecked_why"] = ""
    if theirs is None and note:
        a["notes"].append(note)
        if lcsc:
            a["unchecked_why"] = re.sub(r"^EasyEDA C\d+: ", "", note)
    if fp is None and lcsc and not a["unchecked_why"]:
        a["unchecked_why"] = "no pads to check, the file has positions only: give --footprints"
    a["not_found"] = bool(lcsc and theirs is None and _not_found(note))
    pa, why = _param_assessment(pl, prof, lcsc)
    if why:
        a["review"].append(why)
    if pa is not None:
        if a["rotation"] in ("table", "unknown"):
            if a["rotation"] == "table" and a["turn"] is not None and A._snap(a["turn"]) != pa["turn"]:
                a["review"].append(f"the part's Assembly Rotation ({A.correction(pa)}) and the {a['family']} family rule "
                                   f"({A.correction(a)}) disagree; the parameter was used")
            keep = {k: a[k] for k in ("review", "family", "polarized", "frame", "hold", "pads", "reasons", "rows",
                                      "residual", "theirs", "unchecked_why", "not_found") if k in a}
            notes = a["notes"]
            a.update(pa)
            a.update(keep)
            a["notes"] = notes + pa["notes"]
            a["notes"] = [n for n in a["notes"] if not n.startswith("rotation unknown")]
            if a["polarized"]:                             # a stored turn is no proof of which end is the cathode / +
                a["review"] = [r for r in a["review"] if not r.startswith("polarized part with rotation from")]
                a["review"].append("polarized part with rotation from the part's Assembly Rotation parameter: confirm the "
                                   "cathode / + orientation in the assembler's placement preview")
        elif A.correction(a) != A.correction(pa):
            a["review"].append(f"the part's Assembly Rotation ({A.correction(pa)}) disagrees with this check "
                               f"({A.correction(a)}, {a['rotation']}); the check was used: update the part")
    for p in lcsc_problems(pl.fields, prof):
        (a["hold"] if p.startswith("conflicting") else a["review"]).append(p)
    if pl.problem:
        a["hold"].append(pl.problem)
    if strict and lcsc and a["pads"] == "unchecked":
        a["hold"].append("pads unchecked ([assembly] strict): " + (a["unchecked_why"] or "no pads to check"))
    if a["rotation"] == "unknown" and strict and "rotation unknown ([assembly] strict)" not in a["hold"]:
        a["hold"].append("rotation unknown ([assembly] strict)")
    if pl.side == LAYER_BOTTOM:
        conf = ((prof.get("rotation") or {}).get("altium_bottom" if pl.tool == "altium" else "bottom") or {}).get("confidence", "")
        if conf.startswith("unverified"):
            a["review"].append(f"bottom-side part from Altium: the bottom rotation rule is {conf}")
    if pl.point == "center" and a.get("offset") and any(abs(v) > 1e-6 for v in a["offset"]):
        a["notes"].append("Altium Center-X/Y used (no Ref-X/Y columns): the Assembly Offset is not applied")
    corr = A.correction(a)
    mx, my, r = cpl_row(pl, corr, a.get("offset"))
    a.update(ref=pl.ref, value=pl.value, footprint=pl.footprint, side=pl.side, board_angle=pl.angle,
             mid_x=round(mx, 4), mid_y=round(my, 4), cpl_rotation=r, correction=corr, tht=pl.tht,
             meta=dict(getattr(theirs, "meta", {}) or {}) if theirs else {})
    a["_ours"], a["_theirs"] = fp, theirs
    return a


def run_check(placements, prof, fetch=None, strict=False, library=None, overlays=None, log=None):
    """Check every placed part. overlays: a folder for one overlay picture per checked footprint / LCSC pair.
    Returns the report dict (summary, parts, skipped)."""
    parts, skipped, pics = [], [], {}
    for pl in placements:
        if pl.skip:
            skipped.append({"ref": pl.ref, "footprint": pl.footprint, "why": pl.skip})
            continue
        a = check_part(pl, prof, fetch, strict, library)
        if overlays and a["_theirs"] is not None and a["_ours"] is not None:
            key = (a["lcsc"], pl.footprint, pl.side)
            if key not in pics:
                name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{a['lcsc']}_{pl.footprint}")[:100] + ".png"
                try:
                    pics[key] = str(A.render_overlay(a["_ours"], a["_theirs"], a, Path(overlays) / name))
                except Exception as e:                      # noqa: BLE001 - a picture never stops the check
                    pics[key] = ""
                    a["notes"].append(f"overlay not drawn: {e}")
            a["overlay"] = pics[key]
        parts.append(a)
        if log:
            log(f"{pl.ref}: pads {a['pads']}, rotation {a['rotation']}")
    count = {}
    for a in parts:
        count[a["ref"].upper()] = count.get(a["ref"].upper(), 0) + 1
    for a in parts:                                         # the assembler places by designator: one each
        if count[a["ref"].upper()] > 1:
            a["hold"].append(f"designator {a['ref']} is on {count[a['ref'].upper()]} parts: the assembler places by "
                             "designator, give each part its own")
    for a in parts:
        a["in_files"] = in_files(a)
    return {"profile": prof["profile"], "summary": summarize(parts, skipped), "parts": parts, "skipped": skipped}


def in_files(p):
    """True when a part goes into the assembler's BOM and CPL: not held, and its rotation known (a part with neither
    would be placed by a guess, so it is left out and listed)."""
    return not p.get("hold") and p.get("rotation") != "unknown"


def summarize(parts, skipped):
    c = lambda k, v: sum(1 for p in parts if p.get(k) == v)
    return {"placed": len(parts), "skipped": len(skipped),
            "pads": {v: c("pads", v) for v in A.PAD_VERDICTS + ("unchecked",)},
            "rotation": {v: c("rotation", v) for v in A.ROT_EVIDENCE + ("parameter",)},
            "no_lcsc": sum(1 for p in parts if not p.get("lcsc")),
            "not_fetched": sum(1 for p in parts if p.get("lcsc") and p.get("unchecked_why") and not p.get("not_found")),
            "not_found": sum(1 for p in parts if p.get("not_found")),
            "left_out": sum(1 for p in parts if not in_files(p)),
            "review": sum(1 for p in parts if p.get("review")), "hold": sum(1 for p in parts if p.get("hold"))}


def needs_a_look(p):
    """True when a person must look at a part before ordering (the menu's count, the report's lists)."""
    return bool(p["hold"] or p["review"] or p["rotation"] in ("unknown", "assumed") or not p["lcsc"] or p["pads"] == "risky"
                or (p["lcsc"] and p["pads"] == "unchecked") or p.get("not_found") or not in_files(p))


# ---------------------------------------------------------------- the assembler's files
BOM_LINE_MAX = 2048          # bytes per BOM line: JLCPCB rejects longer ones (kicad-jlcpcb-tools BOM_LINE_MAX_BYTES, #755)


def _line_bytes(row):
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    return len(buf.getvalue().rstrip("\r\n").encode("utf-8"))


def bom_rows(report, prof):
    """The assembler's BOM: one row per (Comment, Footprint, part number), designators joined; a group whose line
    would pass the profile's max_line_bytes (JLCPCB: 2048) is split over several rows. Parts left out of the files
    (held, rotation unknown) are not in it."""
    ex = (prof.get("exports") or {}).get("bom") or {}
    cols = ex.get("columns", ["Comment", "Designator", "Footprint", "LCSC Part #"])
    limit = int(ex.get("max_line_bytes") or BOM_LINE_MAX)
    groups = {}
    for p in report["parts"]:
        if not p.get("in_files", True):
            continue
        key = (p["value"], p["footprint"], p["lcsc"])
        groups.setdefault(key, []).append(p["ref"].upper() if ex.get("designator_case") == "upper" else p["ref"])
    rows = []
    for (val, fp, lc), refs in sorted(groups.items(), key=lambda kv: _natural(sorted(kv[1], key=_natural)[0])):
        row = lambda rs: [{"Comment": val, "Designator": ",".join(rs), "Footprint": fp, "LCSC Part #": lc,
                           "Quantity": str(len(rs))}.get(c, "") for c in cols]
        chunk = []
        for r in sorted(refs, key=_natural):
            if chunk and _line_bytes(row(chunk + [r])) > limit:
                rows.append(row(chunk))
                chunk = []
            chunk.append(r)
        rows.append(row(chunk))
    return cols, rows


def cpl_rows(report, prof):
    """The assembler's CPL: a row per part in the files (held parts and unknown rotations are left out and listed)."""
    ex = (prof.get("exports") or {}).get("cpl") or {}
    cols = ex.get("columns", ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    names = ex.get("layer_names", {"top": "Top", "bottom": "Bottom"})
    dec = int(ex.get("decimals", 4))
    rows = []
    for p in report["parts"]:
        if not p.get("in_files", True):
            continue
        vals = {"Designator": p["ref"], "Mid X": f"{p['mid_x']:.{dec}f}", "Mid Y": f"{p['mid_y']:.{dec}f}",
                "Layer": names.get(p["side"], p["side"]), "Rotation": str(p["cpl_rotation"])}
        rows.append([vals.get(c, "") for c in cols])
    return cols, rows


def _write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)


def not_checked(report):
    """[(why, [refs])]: the parts with an LCSC number whose pads were not checked against the assembler's footprint
    (not fetched: [providers] unofficial = false, offline with nothing cached, the network down), one entry per reason."""
    by = {}
    for p in report["parts"]:
        if p["lcsc"] and p.get("unchecked_why") and not p.get("not_found"):
            why = re.sub(r"^EasyEDA C\d+: ", "", p["unchecked_why"])
            by.setdefault(why, []).append(p["ref"])
    return sorted(by.items())


def listed(report):
    """The parts a person must look at before ordering, by reason: [(title, [lines])]."""
    P = report["parts"]
    out = []
    add = lambda title, rows: out.append((title, rows)) if rows else None
    add("Held (fix before ordering)", [f"{p['ref']}{' ' + p['lcsc'] if p['lcsc'] else ''}: " + "; ".join(p["hold"]) for p in P if p["hold"]])
    add("Left out of the BOM and CPL (place them by hand, or fix them and export again)",
        [f"{p['ref']} ({p['footprint']}): " + ("held" if p["hold"] else "rotation unknown") for p in P if not in_files(p)])
    add("Rotation unknown: not in the CPL", [f"{p['ref']} ({p['footprint']}): " +
        next((n[len("rotation unknown: "):] for n in p["notes"] if n.startswith("rotation unknown")),
             "no rule for this package") for p in P if p["rotation"] == "unknown"])
    add("LCSC number not found at the assembler (a typo, or a part withdrawn?)",
        [f"{p['ref']} {p['lcsc']}: {p['unchecked_why']}" for p in P if p.get("not_found")])
    add("Pads not checked against the assembler's footprint",
        [f"{why}: {', '.join(refs)}" for why, refs in not_checked(report)])
    add("No LCSC part number: the assembler cannot place these until one is chosen",
        [f"{p['ref']} ({p['value']}, {p['footprint']})" for p in P if not p["lcsc"]])
    add("Pads risky", [f"{p['ref']} {p['lcsc']}: " + "; ".join(p["reasons"]) for p in P if p["pads"] == "risky"])
    add("Check in the assembler's placement preview", [f"{p['ref']}: " + "; ".join(p["review"]) for p in P if p["review"]])
    add("Low or no stock", [f"{p['ref']} {p['lcsc']}: stock {p['meta'].get('stock')}" for p in P
                            if p.get("meta") and isinstance(p["meta"].get("stock"), int) and p["meta"]["stock"] < 100])
    return out


def summary_line(s):
    """'15 parts placed, 1 skipped. Pads: ... Rotation: ...' (no empty lists for an empty board)."""
    if not s["placed"]:
        return f"no parts to place ({s['skipped']} skipped)."
    return (f"{s['placed']} parts placed, {s['skipped']} skipped. Pads: " + ", ".join(f"{v} {k}" for k, v in s["pads"].items() if v)
            + ". Rotation: " + ", ".join(f"{v} {k}" for k, v in s["rotation"].items() if v) + "."
            + (f" {s['left_out']} left out of the BOM and CPL." if s.get("left_out") else ""))


def report_md(report, info, files=()):
    s = report["summary"]
    L = [f"# Assembly check: {Path(info['file']).name} ({report['profile']})", "",
         f"{info['format']}; coordinates from the {info['origin_why']}.", "", summary_line(s), ""]
    if files:
        L += ["Files: " + ", ".join(f"`{Path(f).name}`" for f in files), ""]
    for title, rows in listed(report):
        L += [f"## {title}", ""] + [f"- {r}" for r in rows] + [""]
    L += ["## Every part", "", "| Ref | LCSC | Footprint | Side | Pads | Rotation | Board angle | CPL rotation | Mid X | Mid Y | Stock |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in report["parts"]:
        st = (p.get("meta") or {}).get("stock")
        corr = "" if p["correction"] is None else f" (+{p['correction']:g})"
        cpl = p["cpl_rotation"] if p.get("in_files", True) else "left out"
        L.append(f"| {p['ref']} | {p['lcsc'] or '-'} | {p['footprint']} | {p['side']} | {p['pads']} | {p['rotation']}{corr} | "
                 f"{p['board_angle']:g} | {cpl} | {p['mid_x']:.4f} | {p['mid_y']:.4f} | {'' if st is None else st} |")
    if report["skipped"]:
        L += ["", "## Not placed", ""] + [f"- {k['ref'] or '(no reference)'} ({k['footprint']}): {k['why']}" for k in report["skipped"]]
    credits = sorted({p["meta"].get("credit") for p in report["parts"] if (p.get("meta") or {}).get("credit")})
    if credits:
        L += ["", "Assembler footprints: " + "; ".join(credits) + "."]
    return "\n".join(L) + "\n"


def report_json(report, info):
    clean = []
    for p in report["parts"]:
        q = {k: v for k, v in p.items() if not k.startswith("_") and k != "fit"}
        q["offset"] = list(q.get("offset") or (0, 0))
        clean.append(q)
    return {"file": info["file"], "format": info["format"], "origin": list(info["origin"]), "origin_why": info["origin_why"],
            "profile": report["profile"], "summary": report["summary"], "parts": clean, "skipped": report["skipped"]}


def export(report, info, prof, out_dir, stem=None):
    """Write <stem>-<profile>-bom.csv, -cpl.csv, -assembly.md and -assembly.json into out_dir. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or Path(info["file"]).stem
    b, c = out / f"{stem}-{prof['profile']}-bom.csv", out / f"{stem}-{prof['profile']}-cpl.csv"
    _write_csv(b, *bom_rows(report, prof))
    _write_csv(c, *cpl_rows(report, prof))
    md, js = out / f"{stem}-{prof['profile']}-assembly.md", out / f"{stem}-{prof['profile']}-assembly.json"
    md.write_text(report_md(report, info, (b, c)), encoding="utf-8")
    js.write_text(json.dumps(report_json(report, info), indent=1, ensure_ascii=False), encoding="utf-8")
    return [b, c, md, js]


def fetcher(http, rules, prof):
    """fetch(lcsc) for run_check: the assembler's footprint per LCSC number, fetched once per run."""
    memo = {}

    def fetch(lcsc):
        if lcsc not in memo:
            memo[lcsc] = A.fetch_theirs(lcsc, http, rules, prof)
        return memo[lcsc]
    return fetch


def recorded(folder):
    """fetch(lcsc) from recorded EasyEDA answers (<LCSC>.json in a folder): tests and offline checks."""
    def fetch(lcsc):
        f = Path(folder) / f"{lcsc}.json"
        if not f.is_file():
            return None, f"no recorded answer for {lcsc}"
        try:
            return A.from_easyeda(f, f"recorded:{lcsc}"), ""
        except A.AssemblyError as e:
            return None, f"{lcsc}: {e}"
    return fetch


# ---------------------------------------------------------------- the Altium / KiCad menus
def report_html(report, info, files=(), base=None):
    """The report as one HTML page (the web UI's look), with the overlay pictures (paths relative to `base`, the
    page's folder)."""
    import html as H, os
    from . import reports as RP
    body = RP.md_to_html(report_md(report, info, files).splitlines())
    pics = []
    seen = set()
    for p in report["parts"]:
        f = p.get("overlay")
        if f and f not in seen:
            seen.add(f)
            rel = Path(os.path.relpath(f, base)).as_posix() if base else Path(f).resolve().as_uri()
            refs = ", ".join(q["ref"] for q in report["parts"] if q.get("overlay") == f)
            pics.append(f'<figure><img src="{H.escape(rel)}" alt="{H.escape(refs)}" style="max-width:100%">'
                        f'<figcaption class="muted">{H.escape(refs)}: {H.escape(p["pads"])} pads, rotation {H.escape(p["rotation"])}'
                        f"</figcaption></figure>")
    if pics:
        body += '\n<h2>Overlays: our pads (red) on the assembler\'s (blue)</h2>\n' + "\n".join(pics)
    title = f"Assembly check: {Path(info['file']).name}"
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            f'initial-scale=1"><title>{H.escape(title)}</title><style>{RP.CSS}</style></head>\n<body><header><h1>'
            f'{H.escape(title)}</h1></header><main><div class="card">\n{body}\n</div></main></body></html>\n')


def find_pnp(project):
    """The newest Altium pick-and-place CSV in an Altium project's folder (Project Outputs ...), or None."""
    root = Path(project).parent if Path(project).is_file() else Path(project)
    cands = [f for f in root.rglob("*.csv") if re.search(r"pick.?place|pnp|centroid", f.name, re.I) and len(f.relative_to(root).parts) <= 4]
    return max(cands, key=lambda f: f.stat().st_mtime) if cands else None


def menu_check(board, out_dir, rules=None, http=None, extra=None, opener=False, library=None):
    """What the Altium / KiCad menu's Assembly check runs: the check and the assembler's files in out_dir, an HTML
    report, and the menu's result (status, message, big, rows, report, folder). extra: {designator: {field: value}}
    from the project's BOM (LCSC numbers) for a pick-and-place file that lacks them; library: {footprint name:
    Footprint} for a file with positions only."""
    from . import altium_menu as AM, rules as RU
    rules = RU.load(rules, start=Path(board).parent) if not isinstance(rules, dict) else rules
    asm = rules.get("assembly") or {}
    prof = A.load_profile(asm.get("profile") or "jlcpcb")
    pls, info = read_board(board)
    if extra:
        for p in pls:
            have = {k.lower() for k, v in p.fields.items() if str(v).strip()}
            p.fields.update({k: v for k, v in (extra.get(p.ref.upper()) or {}).items() if k.lower() not in have and v})
    out = Path(out_dir)
    rep = run_check(pls, prof, fetcher(http, rules, prof), bool(asm.get("strict")), library, overlays=out / "overlays")
    files = export(rep, info, prof, out)
    page = out / f"{Path(info['file']).stem}-{prof['profile']}-assembly.html"
    page.write_text(report_html(rep, info, files, base=out), encoding="utf-8")
    AM._open(page, opener)
    s = rep["summary"]
    look = sum(1 for p in rep["parts"] if needs_a_look(p))
    rows = [AM.row_text("pads match JLCPCB's" if prof["profile"] == "jlcpcb" else "pads match",
                        s["pads"]["identical"] + s["pads"]["compatible"], True),
            AM.row_text("rotation verified", s["rotation"]["verified"], True)]
    rows += [AM.row_text(label, n) for label, n in (
        ("rotation measured, polarity assumed", s["rotation"]["assumed"]),
        ("rotation from a rule", s["rotation"]["table"] + s["rotation"]["parameter"] + s["rotation"]["override"]),
        ("rotation unknown", s["rotation"]["unknown"]), ("no LCSC number", s["no_lcsc"]),
        ("LCSC number not found", s["not_found"]), ("pads not checked: JLCPCB footprint not fetched", s["not_fetched"]),
        ("pads risky", s["pads"]["risky"]), ("held", s["hold"]),
        ("left out of the BOM and CPL", s["left_out"])) if n]
    msg = (f"{Path(board).name}: {s['placed']} parts to place ({prof.get('name') or prof['profile']}); "
           + (f"{look} need a look before you order (the report lists them)" if look else "nothing needs a look")
           + (f"; {s['left_out']} left out of the CPL" if s["left_out"] else "")
           + f". BOM and CPL: {out}")
    res = {"status": "held" if s["hold"] else "ok", "message": msg, "report": str(page), "folder": str(out),
           "bom": str(files[0]), "cpl": str(files[1])}
    if s["hold"]:
        res["fix"] = "open the report: each held part says what does not match and shows the overlay"
    res.update(AM.rows_result(rows, f"{s['placed']} parts"))
    return res
