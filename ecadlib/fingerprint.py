"""Part fingerprint: one canonical, tool-neutral description of a part, the thing change control compares (#105).

    from ecadlib import fingerprint as FP
    fp = FP.of_item(item, base)                       a plan item: its .SchLib / .PcbLib and its fields
    fp = FP.of_kicad(chain_or_symbol, kicad_mod_text)  a KiCad symbol (+ footprint), e.g. one cached in a schematic
    fp = FP.of_files("X.PcbLib", "Lib.kicad_sym:Name", ...)   any mix of files
    FP.hashes(fp)  ->  {"eid", "gid", "mid", "cid", "digest"}

Built on the readers ecadlib.parity already trusts (kicad_footprint / altium_footprint / kicad_symbol / altium_symbol),
so KiCad and Altium copies of the same part give the same electrical and geometry fingerprint:

  identity    MPN, manufacturer, and the value as a number (100nF = 0.1uF = 100n), its unit and what follows it (a
              rating, a tolerance) from the part's fields
  symbol      pins {number: canonical name, electrical type}, plus what is not electrical: the name as written (RESET#
              or ~{RESET}), hidden, hidden net, unit, pin shape, position, length; every copy of the number ("inst":
              stacked pins, a power pin in each unit, De Morgan views) with its unit, body style and KiCad alternate
              functions; units and display modes; a digest of the symbol graphics
  footprint   pads (number, position, size, rotation, shape, corner %, side, drill and slot, copper offset, copper
              layers, paste and mask on/off and margins, custom outline and a digest of its shape; KiCad zone
              connection, pin function / type, fabrication property), paste / mask windows, footprint copper,
              courtyard extent; KiCad attributes, footprint-wide settings and zones; and what is not geometry:
              silkscreen, fab drawing, texts and Edge.Cuts as digests and counts
  model       the 3D model: KiCad (model file, offset, rotation, scale) or Altium bodies (height, standoff, 2D offset,
              rotations, z offset, embedded STEP name and checksum)
  fields      every other parameter (description, datasheet, supplier numbers, ...)

Hashes (sha256 over the canonical JSON):
  eid     electrical: identity + pins (canonical name, type) + pad numbers + pin -> pad map
  gid     land pattern: every pad, window, footprint copper and the courtyard
  mid     3D model (tool-specific: KiCad points at a STEP file, Altium embeds bodies, so it is kept out of gid)
  cid     cosmetic: names as written, pin geometry and shapes, symbol graphics, silk, fab, texts, other fields
  digest  everything, including the details the cross-tool hashes leave out (KiCad's finer pin types, hidden pins)
eid and gid are the same for a part written as KiCad and as Altium (tests/test_change_control.py runs that over the
parity survey and the demo library); mid and cid are compared within one tool.

Conventions (parity's, applied once): mm in KiCad's frame (y down) for footprints, pad (w, h, rotation) reduced to
rotation in [0, 90), and every length quantised to Altium's internal unit (2.54 nm) with altium_lib.mm, the exact
rational conversion the writer uses, so a KiCad 0.9525 and the Altium unit it was written as land on the same number.
Pin names: active-low notations (Altium R\\E\\S\\E\\T\\, KiCad ~{RESET}, RESET#, /RESET, nRESET, RESET_N) and case are
one canonical name; the spelling is cosmetic. Pin types: Altium's eight electrical types; KiCad's finer ones
(power_in / power_out, tri_state, free ...) are kept as `ktype` and compared KiCad to KiCad."""
import copy, hashlib, json, math, re
from pathlib import Path

from . import altium_lib as AL, parity as PA, sexpr

SCHEMA = 2
ETYPE_NAME = {0: "input", 1: "io", 2: "output", 3: "open_collector", 4: "passive", 5: "hiz", 6: "open_emitter", 7: "power"}
MPN_KEYS = ("manufacturer part number", "manufacturer part number 1", "mpn", "mfr part number", "manufacturer_part_number",
            "manufacturerpartnumber", "manufacturer part #", "mfr. part number", "mfr part #", "mfr. #", "mpn1", "mfr_pn",
            "part number", "partnumber")
MFR_KEYS = ("manufacturer", "manufacturer 1", "mfr", "mfg", "manufacturer name", "manufacturer_name")
VALUE_KEYS = ("value",)
SCH_MM = 0.254                                # Altium schematic unit (10 mil) in mm


# ---------------------------------------------------------------- quantising (the one place)
def qmm(v):
    """A length in mm on Altium's internal grid (2.54 nm), as a readable float."""
    if v is None:
        return None
    return round(AL.to_mm(AL.mm(repr(float(v)) if isinstance(v, float) else v)), 7)


def qdeg(a, period=360.0):
    if a is None:
        return None
    a = round(float(a) % period, 3)
    return 0.0 if abs(a - period) < 1e-6 else a


def qpt(p):
    return [qmm(p[0]), qmm(p[1])]


def qbox(b):
    return [qmm(v) for v in b] if b else None


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


# ---------------------------------------------------------------- pin names
def _overbar_runs(name):
    """Altium 'R\\E\\S\\E\\T\\' (a backslash after each overlined character) -> KiCad '~{RESET}' runs."""
    return re.sub(r"((?:[^\\]\\)+)", lambda m: "~{" + m.group(1).replace("\\", "") + "}", name)


def canon_pin_name(name):
    """The name as a signal: active-low notation and case folded. 'RESET#', '~{RESET}', 'R\\E\\S\\E\\T\\', '/RESET',
    'nRESET' and 'RESET_N' -> '~RESET'; 'Vin' -> 'VIN'; KiCad's '~' (no name) -> ''."""
    from .house_style import pin_low
    n = str(name or "").strip()
    if n == "~":
        return ""
    if "\\" in n:
        n = _overbar_runs(n)
    m = re.fullmatch(r"~\{([^{}]*)\}", n)
    if m:
        return "~" + m.group(1).strip().upper() if m.group(1).strip() else ""
    base, style = pin_low(n)
    if style and style != "overbar":
        return "~" + base.upper()
    if "~{" in n:                              # a partly overlined name: keep where the bar is
        return re.sub(r"~\{([^{}]*)\}", lambda q: "~{" + q.group(1).upper() + "}", n).upper()
    return n.upper()


def kicad_ktype(t):
    return str(t or "passive")


def coarse_type(t):
    """KiCad type name or Altium ETYPE number -> one of ETYPE_NAME's names."""
    if isinstance(t, int):
        return ETYPE_NAME.get(t, f"type {t}")
    return ETYPE_NAME.get(PA.kicad_type(t), "passive")


# ---------------------------------------------------------------- identity and fields
def _pick_key(params, keys):
    """The first of keys (in their order) the part has a value for -> (its key as written, value), else (None, "")."""
    low = {}
    for k, v in (params or {}).items():
        low.setdefault(str(k).strip().lower(), (k, v))
    for k in keys:
        if k in low and str(low[k][1] or "").strip() not in ("", "~", "*"):
            return low[k][0], str(low[k][1]).strip()
    return None, ""


def _pick(params, keys):
    return _pick_key(params, keys)[1]


def value_number(v):
    """'100nF' / '0.1uF' / '100n' -> 1e-07 (6 significant digits), else None."""
    from .kicad_index import parse_value
    try:
        x = parse_value(v)
    except Exception:
        return None
    return float(f"{x:.6g}") if x is not None else None


def value_parts(v):
    """A value's unit ('F', 'H', 'R', 'V', 'W', 'A', 'Hz', '' when not stated) and the rest of it, canonical:
    '100nF 50V X7R' -> ('F', '50V X7R'), '10k 1%' -> ('', '1%'), '0.1uF/16V' -> ('F', '16V'). (None, None) when the
    value does not start with a number."""
    from .kicad_index import split_value
    try:
        x, unit, rest = split_value(v)
    except Exception:
        return None, None
    if x is None:
        return None, None
    toks = []
    for t in re.split(r"[\s/,;]+", rest):
        if not t:
            continue
        y, u, left = split_value(t)
        toks.append(f"{y:.6g}{u}" if y is not None and not left else t.upper())
    return unit, " ".join(toks)


def identity(params):
    """{mpn, manufacturer, value, value_num, value_unit, value_rest} from a part's fields (any of the usual column
    names)."""
    v = _pick(params, VALUE_KEYS)
    unit, rest = value_parts(v)
    return {"mpn": _pick(params, MPN_KEYS), "manufacturer": _pick(params, MFR_KEYS), "value": v, "value_num": value_number(v),
            "value_unit": unit, "value_rest": rest}


def other_fields(params):
    """Every field but the ones identity() took. A second MPN or manufacturer column (MPN next to Manufacturer Part
    Number) stays a field; change_diff compares it with the identity."""
    skip = {str(k).strip().lower() for k in (_pick_key(params, keys)[0] for keys in (MPN_KEYS, MFR_KEYS)) if k is not None}
    skip |= set(VALUE_KEYS)
    return {str(k): str(v) for k, v in sorted((params or {}).items()) if str(k).strip().lower() not in skip}


# ---------------------------------------------------------------- footprints
def _canon_pads(pads):
    out = []
    for p in pads:
        d = p.get("drill")
        q = {"num": str(p.get("num") or ""), "x": qmm(p["x"]), "y": qmm(p["y"]), "w": qmm(p["w"]), "h": qmm(p["h"]),
             "rot": qdeg(p["rot"], 90.0), "shape": p["shape"], "corner": int(round(p.get("corner") or 0)),
             "side": p["side"], "drill": [qmm(d[0]), qmm(d[1]), qdeg(d[2], 180.0)] if d else None,
             "offset": qpt(p.get("offset") or (0, 0)), "paste": p.get("paste"), "mask": bool(p.get("mask")),
             "paste_margin": qmm(p.get("paste_margin")) if p.get("paste_margin") else None,
             "mask_margin": qmm(p.get("mask_margin")) if p.get("mask_margin") else None,
             "outline": qbox(p.get("outline")) if p["shape"] == "custom" else None,
             "cu": p.get("cu"), **{k: p.get(k) for k in PAD_TOOL_KEYS}}
        if q["side"] == "NPTH":                # Altium's NPTH pad is the hole: size, shape and mask follow the drill
            q.update(w=None, h=None, rot=0.0, shape="hole", corner=0, mask=None, mask_margin=None, cu=None, cu_in=None)
        if q["shape"] == "round" and q["w"] == q["h"]:
            q["rot"] = 0.0
        out.append(q)
    return sorted(out, key=lambda q: (q["num"], q["x"] or 0, q["y"] or 0))


# per-pad details one tool has and the other has not (kept out of gid, compared within one tool):
# cu_in  KiCad copper on the inner layers (*.Cu) or only outside (F&B.Cu); zone  KiCad zone_connect (-1 = the
# footprint's); pintype / pinfunc  KiCad's pad pin type and function; prop  KiCad fabrication property (heatsink,
# fiducial, testpoint ...); prim  a digest of a custom pad's copper shape ("k:" KiCad primitives, "a:" Altium regions)
PAD_TOOL_KEYS = ("cu_in", "zone", "pintype", "pinfunc", "prop", "prim")


def _k_cu(kind, layers):
    """A KiCad pad's copper layers -> (outer layers ["B", "F"], inner layers too); NPTH -> (None, None). *.Cu and F&B.Cu
    are both sides (kicad_import writes either as an Altium multi-layer pad); they differ only on the inner layers."""
    if kind == "np_thru_hole":
        return None, None
    s = set()
    for l in layers:
        if not l.endswith(".Cu"):
            continue
        s |= {"*.Cu": {"F", "B", "in"}, "F&B.Cu": {"F", "B"}, "F.Cu": {"F"}, "B.Cu": {"B"}}.get(l, {"in"})
    return sorted(s - {"in"}), "in" in s


def _k_prim(p, rot):
    """A custom pad's primitives and anchor -> digest, turned with the pad into the footprint's frame (so a pad rotated
    180 degrees, which canon_pad folds to 0, is another shape) and relative to the pad's position."""
    recs = [["anchor", str((sexpr.first(sexpr.first(p, "options") or [], "anchor") or [None, "circle"])[1])]]
    for g in (sexpr.first(p, "primitives") or [])[1:]:
        if not isinstance(g, list) or not g:
            continue
        raw = [(float(n[1]), float(n[2])) for n in (sexpr.first(g, k) for k in ("start", "mid", "end", "center")) if n]
        if sexpr.first(g, "pts"):
            raw += [(float(a[1]), float(a[2])) for a in sexpr.find(sexpr.first(g, "pts"), "xy")]
        fill = sexpr.first(g, "fill")
        recs.append([g[0], [[round(v, 4) + 0.0 for v in PA._turn(q, rot)] for q in raw], _k_width(g),
                     bool(fill is None and g[0] == "gr_poly" or fill and fill[1] in ("yes", "solid"))])
    return "k:" + digest(recs)


def _kicad_pad_extras(t):
    """The KiCad pad details parity's reader leaves out, in parity's pad order (window-only pads skipped)."""
    out = []
    for p in sexpr.find(t, "pad"):
        kind, shape = p[2], p[3]
        layers = [str(l) for l in (sexpr.first(p, "layers") or ["layers"])[1:]]
        if kind in ("smd", "connect") and not any(l.endswith(".Cu") for l in layers):
            continue
        at = sexpr.first(p, "at")
        rot = float(at[3]) if at and len(at) > 3 else 0.0
        cu, cu_in = _k_cu(kind, layers)
        zc, prop = sexpr.first(p, "zone_connect"), sexpr.first(p, "property")
        pt, pf = sexpr.first(p, "pintype"), sexpr.first(p, "pinfunction")
        out.append({"cu": cu, "cu_in": cu_in, "zone": int(float(zc[1])) if zc else -1,
                    "pintype": str(pt[1]) if pt else "", "pinfunc": str(pf[1]) if pf else "",
                    "prop": str(prop[1]) if prop and len(prop) > 1 else "",
                    "prim": _k_prim(p, rot) if shape == "custom" else None})
    return out


_K_FOOT_SETTINGS = ("zone_connect", "solder_mask_margin", "solder_paste_margin", "solder_paste_margin_ratio",
                    "solder_paste_ratio", "clearance", "thermal_width", "thermal_gap")


def _kicad_foot_extras(t):
    """Footprint-level KiCad details: attributes (smd / through_hole, exclude_from_bom, dnp ...), the settings every pad
    inherits (zone_connect, mask / paste margins, clearance), and zones (keepouts, copper zones)."""
    attr = sexpr.first(t, "attr")
    sets = {}
    for k in _K_FOOT_SETTINGS:
        n = sexpr.first(t, k)
        if n and len(n) > 1:
            try:
                sets[k] = round(float(n[1]), 6)
            except ValueError:
                sets[k] = str(n[1])
    zones = []
    for z in sexpr.find(t, "zone"):
        lays = [str(l) for n in (sexpr.first(z, "layer"), sexpr.first(z, "layers")) if n for l in n[1:]]
        ko = sexpr.first(z, "keepout")
        rules = sorted([str(r[0]), str(r[1])] for r in (ko or [])[1:] if isinstance(r, list) and len(r) > 1)
        pts = [[qmm(float(a[1])), qmm(float(a[2]))] for poly in sexpr.find(z, "polygon")
               for a in sexpr.find(sexpr.first(poly, "pts") or [], "xy")]
        zones.append({"keepout": bool(ko), "rules": rules, "layers": sorted(lays), "pts": pts})
    return {"attr": sorted(str(a) for a in attr[1:] if isinstance(a, str)) if attr else [], "settings": sets,
            "zones": sorted(zones, key=json.dumps)}


def _canon_land(c):
    """parity's canonical footprint dict -> the geometry part of a fingerprint."""
    return {"name": c.get("name") or "", "pads": _canon_pads(c["pads"]),
            "windows": sorted([w, qbox(b)] for w, b in c.get("windows") or [] if b),
            "copper": sorted(qbox(b) for b in c.get("copper") or [] if b),
            "courtyard": qbox(c.get("courtyard"))}


_K_GRAPHICS = ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc", "fp_curve")


def _k_pts(g):
    pts = []
    for k in ("start", "mid", "end", "center"):
        n = sexpr.first(g, k)
        if n:
            pts.append(qpt((float(n[1]), float(n[2]))))
    if sexpr.first(g, "pts"):
        pts += [qpt((float(a[1]), float(a[2]))) for a in sexpr.find(sexpr.first(g, "pts"), "xy")]
    return pts


def _k_width(g):
    w = sexpr.first(sexpr.first(g, "stroke") or [], "width") or sexpr.first(g, "width")
    return qmm(float(w[1])) if w else 0.0


def _kicad_art(t):
    """KiCad footprint graphics and texts -> {silk: [...], fab: [...], text: [...]} (canonical, format-neutral)."""
    art = {"silk": [], "fab": [], "text": [], "other": [], "edge": []}
    for g in (x for x in t[1:] if isinstance(x, list) and x and x[0] in _K_GRAPHICS):
        lay = str((sexpr.first(g, "layer") or [None, ""])[1])
        if lay.endswith((".Cu", ".Mask", ".Paste", ".CrtYd")):
            continue                           # copper / windows / courtyard are geometry
        fill = sexpr.first(g, "fill")
        rec = [g[0], lay, _k_pts(g), _k_width(g), bool(fill and fill[1] in ("yes", "solid"))]
        art["silk" if "SilkS" in lay else "fab" if "Fab" in lay else "edge" if lay == "Edge.Cuts" else "other"].append(rec)
    for g in (x for x in t[1:] if isinstance(x, list) and x and x[0] in ("fp_text", "property", "fp_text_box")):
        if g[0] == "property" and str(g[1]) not in ("Reference", "Value"):
            continue
        at, lay = sexpr.first(g, "at"), str((sexpr.first(g, "layer") or [None, ""])[1])
        size = sexpr.first(sexpr.first(sexpr.first(g, "effects") or [], "font") or [], "size")
        kind = str(g[1]) if g[0] != "fp_text_box" else "box"
        text = str(g[2]) if len(g) > 2 and isinstance(g[2], str) else ""
        art["text"].append([kind, text, lay, qpt((float(at[1]), float(at[2]))) if at else None,
                            qdeg(float(at[3])) if at and len(at) > 3 else 0.0,
                            qmm(float(size[1])) if size else None, "hide" in g or bool(sexpr.first(g, "hide"))])
    return {k: sorted(v, key=json.dumps) for k, v in art.items()}


def _kicad_models(t):
    out = []
    for m in sexpr.find(t, "model"):
        rec = {"file": Path(str(m[1]).replace("\\", "/")).name.lower(), "hide": "hide" in m[1:]}
        for k in ("offset", "scale", "rotate"):
            xyz = sexpr.first(sexpr.first(m, k) or [], "xyz")
            rec[k] = [round(float(v), 4) for v in xyz[1:4]] if xyz else None
        out.append(rec)
    return {"tool": "kicad", "models": sorted(out, key=json.dumps)}


def _num_s(v):
    out = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if out in ("-0", "") else out


def _flip_layer(s):
    if s.startswith(("B.", "F.")):
        return type(s)(("F." if s[0] == "B" else "B.") + s[2:])
    return s


_PT_HEADS = ("start", "end", "mid", "center", "xy", "offset")


def unplace(node, mirror="y"):
    """A footprint placed on a board (a .kicad_pcb `footprint` node) -> the same footprint in its library frame:
    position and rotation taken off, and a bottom-side footprint turned back to the top. Inside a footprint KiCad keeps
    positions in the footprint's own frame but pad and text angles absolute (footprint angle added), and a flipped
    footprint's items are mirrored top to bottom in that frame (y negated, angles negated, B.* and F.* layers swapped).
    (KiCad's FOOTPRINT::Flip flips every child top to bottom in the footprint's frame, then a left / right flip turns the
    whole footprint 180 degrees, which only changes the footprint angle.) mirror="x" undoes a left / right mirror instead,
    for files written by other tools. Returns a new tree."""
    t = copy.deepcopy(node)
    at = sexpr.first(t, "at")
    rot = float(at[3]) if at and len(at) > 3 else 0.0
    back = str((sexpr.first(t, "layer") or [None, "F.Cu"])[1]).startswith("B.")
    axis = 2 if mirror == "y" else 1

    def angle(a):
        a = float(a) - rot
        if back:
            a = -a if mirror == "y" else 180.0 - a
        return _num_s(((a + 180.0) % 360.0) - 180.0 if abs(a) > 1e-9 else 0.0)

    def walk(n):
        for k, c in enumerate(n):
            if isinstance(c, str):
                if k and n[0] in ("layer", "layers"):
                    n[k] = _flip_layer(c) if back else c
                continue
            if not c:
                continue
            h = c[0]
            if h == "model":                   # the 3D model's offset / rotation are its own, never placed
                continue
            if h in _PT_HEADS and len(c) >= 3 and not isinstance(c[1], list):
                if back:
                    c[axis] = _num_s(-float(c[axis]))
            elif h == "at" and len(c) >= 3:
                if back:
                    c[axis] = _num_s(-float(c[axis]))
                if len(c) > 3 or back or rot:
                    a = angle(c[3] if len(c) > 3 else 0)
                    c[3:] = [a] if a != "0" else []
            walk(c)

    body = [c for c in t[2:] if not (isinstance(c, list) and c and c[0] in ("at", "layer"))]
    t[2:] = body
    walk(t)
    t[2:2] = [["layer", sexpr.Q("F.Cu")]]
    if isinstance(t[1], str) and ":" in t[1]:  # the board names it Lib:Name, the library file Name
        t[1] = type(t[1])(t[1].split(":", 1)[1])
    return t


def kicad_footprint(text):
    """A .kicad_mod (text or parsed tree, also a footprint placed on a board after unplace()) -> (land, art, model)."""
    t = sexpr.parse(text) if isinstance(text, str) else text
    c = PA.kicad_footprint(t)
    for pad, extra in zip(c["pads"], _kicad_pad_extras(t)):
        pad.update(extra)
    land = _canon_land(c)
    land.update(_kicad_foot_extras(t))
    return land, _kicad_art(t), _kicad_models(t)


def _a_xy(x, y):
    return qpt(PA._a2k(x, y))


def _altium_art(fp):
    art = {"silk": [], "fab": [], "text": [], "other": []}
    skip = {AL.TOP, AL.BOTTOM, AL.MULTI, AL.TOP_PASTE, AL.BOTTOM_PASTE, AL.TOP_SOLDER, AL.BOTTOM_SOLDER, AL.MECH15}
    for r in fp["records"]:
        t, lay = r["type"], r.get("layer")
        if t in ("pad", "body", "via") or lay in skip:
            continue
        where = "silk" if lay in (AL.TOP_OVERLAY, AL.BOTTOM_OVERLAY) else "fab" if lay == AL.MECH13 else "other"
        if t == "text":
            art["text"].append([r.get("text"), lay, _a_xy(r["x"], r["y"]), qdeg(r.get("rot") or 0), qmm(AL.to_mm(r.get("h") or 0)),
                                bool(r.get("mirror"))])
            continue
        if t == "track":
            rec = ["line", lay, [_a_xy(r["x1"], r["y1"]), _a_xy(r["x2"], r["y2"])], qmm(AL.to_mm(r.get("w") or 0))]
        elif t == "arc":
            rec = ["arc", lay, [_a_xy(r["cx"], r["cy"])], qmm(AL.to_mm(r["r"])), qdeg(r["a0"]), qdeg(r["a1"]),
                   qmm(AL.to_mm(r.get("w") or 0))]
        elif t == "fill":
            rec = ["fill", lay, [_a_xy(r["x1"], r["y1"]), _a_xy(r["x2"], r["y2"])], qdeg(r.get("rot") or 0)]
        elif t == "region":
            rec = ["region", lay, [_a_xy(px, py) for px, py in r["pts"]]]
        else:
            rec = [t, lay]
        art[where].append(rec)
    return {k: sorted(v, key=json.dumps) for k, v in art.items()}


def _altium_models(fp):
    out = []
    for r in fp["records"]:
        if r["type"] != "body":
            continue
        p = AL.parse_params(r.get("params") or "") if isinstance(r.get("params"), str) else (r.get("params") or {})
        mil = lambda k: qmm(float(re.sub(r"[^0-9eE+.\-]", "", str(p.get(k) or "0")) or 0) * 0.0254)
        num = lambda k: round(float(re.sub(r"[^0-9eE+.\-]", "", str(p.get(k) or "0")) or 0), 3)
        out.append({"layer": r.get("layer"), "height": mil("OVERALLHEIGHT"), "standoff": mil("STANDOFFHEIGHT"),
                    "x": mil("MODEL.2D.X"), "y": -mil("MODEL.2D.Y") if mil("MODEL.2D.Y") else 0.0,
                    "rot": num("MODEL.2D.ROTATION"), "rx": num("MODEL.3D.ROTX"), "ry": num("MODEL.3D.ROTY"),
                    "rz": num("MODEL.3D.ROTZ"), "dz": mil("MODEL.3D.DZ"),
                    "step": str(p.get("MODEL.NAME") or "").lower(), "checksum": str(p.get("MODEL.CHECKSUM") or ""),
                    "outline": qbox(PA._bbox(PA._a2k(x, y) for x, y in r.get("pts") or []))})
    return {"tool": "altium", "models": sorted(out, key=json.dumps)}


def altium_footprint(fp):
    """One footprint from altium_lib.read_pcblib -> (land, art, model)."""
    c = PA.altium_footprint(fp)
    for pad, extra in zip(c["pads"], _altium_pad_extras(fp)):
        pad.update(extra)
    land = _canon_land(c)
    land.update(attr=None, settings=None, zones=None)   # KiCad-only details: not compared for Altium
    return land, _altium_art(fp), _altium_models(fp)


def _altium_pad_extras(fp):
    """The pad details parity's Altium reader leaves out, in its pad order: copper layers and the custom copper shape."""
    from .kicad_export import _custom_pads
    recs = fp["records"]
    custom, _ = _custom_pads(recs)
    out = []
    for r in (x for x in recs if x["type"] == "pad"):
        multi = r["layer"] == AL.MULTI
        cu = (None if not r["plated"] else ["B", "F"]) if multi else ["F"] if r["layer"] == AL.TOP else \
            ["B"] if r["layer"] == AL.BOTTOM else [f"layer {r['layer']}"]
        prim = None
        if id(r) in custom:
            cx, cy = PA._a2k(r["x"], r["y"])
            prim = "a:" + digest(sorted([[round(x - cx, 4) + 0.0, round(y - cy, 4) + 0.0] for x, y in (PA._a2k(px, py) for px, py in poly)]
                                        for poly in custom[id(r)]))
        out.append({"cu": cu, "cu_in": None, "zone": None, "pintype": None, "pinfunc": None, "prop": None, "prim": prim})
    return out


def _pick_named(entries, name, what):
    if not entries:
        raise ValueError(f"no {what} in the file")
    if name is None:
        return entries[0]
    hit = next((e for e in entries if e["name"] == name), None) or \
        next((e for e in entries if e["name"].lower() == str(name).lower()), None)
    if hit is None:
        raise ValueError(f"{what} {name!r} not in the file (it has {', '.join(e['name'] for e in entries[:8])})")
    return hit


def pcblib(path, name=None):
    return altium_footprint(_pick_named(AL.read_pcblib(path), name, "footprint"))


# ---------------------------------------------------------------- symbols
def _k_hidden(node):
    return "hide" in node[1:] or bool(sexpr.first(node, "hide") and PA._yes(sexpr.first(node, "hide")))


def flatten(chain):
    """[symbol, parent, ...] (an `extends` chain) -> one symbol node with the parent's units and the derived properties."""
    if isinstance(chain, list) and chain and isinstance(chain[0], list) and chain[0] and chain[0][0] == "symbol":
        chain = list(chain)
    else:
        chain = [chain]
    if len(chain) == 1:
        return chain[0]
    from .kicad_import import _props
    base = chain[-1]
    props = _props(chain)
    out = [x for x in base if not (isinstance(x, list) and x and x[0] == "property")]
    out[1] = chain[0][1]
    out += [["property", k, v] for k, v in props.items()]
    return out


def kicad_symbol(sym):
    """A KiCad symbol (node or `extends` chain) -> the symbol part of a fingerprint, and its properties."""
    sym = flatten(sym)
    props = {str(p[1]): "" if str(p[2]) == "~" else str(p[2]) for p in sexpr.find(sym, "property")}
    pins, art, units, styles = [], [], set(), set()
    for u in sexpr.find(sym, "symbol"):
        parts = str(u[1]).rsplit("_", 2)
        unit = int(parts[-2]) if len(parts) == 3 and parts[-2].isdigit() else 0
        style = int(parts[-1]) if len(parts) == 3 and parts[-1].isdigit() else 1
        units.add(unit)
        styles.add(style)
        for g in u[2:]:
            if not isinstance(g, list):
                continue
            if g[0] == "pin":
                num = str((sexpr.first(g, "number") or ["", ""])[1])
                num = "" if num == "~" else num    # KiCad 9 wrote an empty name, number or field as ~, 10 as ""
                raw = str((sexpr.first(g, "name") or ["", ""])[1])
                raw = "" if raw == "~" else raw
                at = sexpr.first(g, "at")
                hidden = _k_hidden(g)
                pins.append((num, {
                    "name": canon_pin_name(raw), "raw": raw, "type": coarse_type(str(g[1])), "ktype": kicad_ktype(g[1]),
                    "hidden": hidden, "net": canon_pin_name(raw) if hidden and str(g[1]) == "power_in" else None,
                    "unit": unit, "style": max(1, style), "shape": str(g[2]) if len(g) > 2 and isinstance(g[2], str) else "line",
                    "at": qpt((float(at[1]), float(at[2]))) if at else None,
                    "orient": int(float(at[3])) % 360 if at and len(at) > 3 else 0,
                    "len": qmm(float((sexpr.first(g, "length") or [0, 2.54])[1])),
                    "alt": sorted([canon_pin_name("" if str(a[1]) == "~" else a[1]), coarse_type(str(a[2])), kicad_ktype(a[2])]
                                  for a in sexpr.find(g, "alternate") if len(a) > 2)}))
            elif g[0] in ("rectangle", "polyline", "circle", "arc", "bezier", "text", "text_box"):
                pts = _k_pts(g)
                rad = sexpr.first(g, "radius")
                fill = (sexpr.first(sexpr.first(g, "fill") or [], "type") or [None, "none"])[1]
                art.append([g[0], unit, style, pts, qmm(float(rad[1])) if rad and not isinstance(rad[1], list) else None,
                            str(g[1]) if g[0] == "text" else "", fill, _k_width(g)])
    n_units = max(1, len([u for u in units if u > 0]))
    sym_d = {"name": str(sym[1]), "pins": _pin_table(pins, n_units), "units": n_units,
             "modes": max(1, len([s for s in styles if s > 0])), "art": sorted(art, key=json.dumps),
             "footprint_link": (props.get("Footprint") or "").split(":")[-1]}
    return sym_d, props


def _pin_table(found, n_units):
    """[(number, pin record)] in file order -> {number: the first copy (body style 1 first), with "inst": every copy}.
    A pin number can appear more than once: stacked pins, and a multi-unit symbol's power pins repeated in each unit
    (Altium's usual layout). Every copy counts. In a one-unit symbol, unit 0 (common) and unit 1 are the same unit."""
    out = {}
    for num, rec in found:
        if n_units <= 1:
            rec["unit"] = 1
        out.setdefault(num, []).append(rec)
    table = {}
    for num, recs in out.items():
        first = min(recs, key=lambda r: r.get("style") or 1)
        inst = sorted(recs, key=lambda r: (r.get("style") or 1, r.get("unit") or 0, json.dumps(r, sort_keys=True, default=str)))
        table[num] = dict(first, inst=inst)
    return table


_SCH_ART = {"4": "label", "6": "polyline", "7": "polygon", "8": "ellipse", "10": "round_rect", "11": "elliptical_arc",
            "12": "arc", "13": "line", "14": "rectangle", "5": "bezier", "30": "image", "28": "text_frame"}


def _sch_num(r, k):
    v = float(r.get(k) or 0) + float(r.get(k + "_FRAC") or 0) / 100000.0
    return qmm(v * SCH_MM)


def altium_symbol(entry):
    """One symbol from altium_lib.read_schlib -> the symbol part of a fingerprint, and its parameters."""
    recs = entry["records"]
    net = {int(r["OWNERINDEX"]): r.get("TEXT", "") for r in recs if r.get("RECORD") == "41" and r.get("NAME") == "HiddenNetName"
           and str(r.get("OWNERINDEX", "")).lstrip("-").isdigit()}
    pins, art, params, link, parts, modes = [], [], {}, None, 1, 1
    head = recs[0] if recs else {}
    try:
        parts = max(1, int(head.get("PARTCOUNT") or 1) - 1) if head.get("PARTCOUNT") else 1
        modes = max(1, int(head.get("DISPLAYMODECOUNT") or 1))
    except ValueError:
        pass
    for i, r in enumerate(recs):
        rec = r.get("RECORD")
        if rec == "2":
            hidden = bool(r["conglomerate"] & 0x04)
            pins.append((str(r["num"]), {
                "name": canon_pin_name(r["name"]), "raw": r["name"], "type": coarse_type(r["etype"]), "hidden": hidden,
                "net": canon_pin_name(net.get(i)) if hidden and net.get(i) else None, "unit": max(0, r.get("part") or 0),
                "style": (r.get("mode") or 0) + 1, "shape": "-".join(str(s) for s in r.get("symbols") or ()) or "line",
                "at": [qmm(r["x"] * SCH_MM), qmm(r["y"] * SCH_MM)], "orient": (r.get("orient") or 0) * 90,
                "len": qmm((r.get("length") or 0) * SCH_MM), "alt": []}))
        elif rec == "41" and r.get("NAME") not in (None, "HiddenNetName", "Comment"):
            params[r["NAME"]] = r.get("TEXT", "")
        elif rec == "45" and str(r.get("MODELTYPE", "")).upper() == "PCBLIB" and (link is None or r.get("ISCURRENT") == "T"):
            link = r.get("MODELNAME")
        elif rec in _SCH_ART:
            pts = [[_sch_num(r, f"X{k}"), _sch_num(r, f"Y{k}")] for k in range(1, int(r.get("LOCATIONCOUNT") or 0) + 1)]
            if "LOCATION.X" in r or "CORNER.X" in r:
                pts += [[_sch_num(r, "LOCATION.X"), _sch_num(r, "LOCATION.Y")]]
            if "CORNER.X" in r:
                pts += [[_sch_num(r, "CORNER.X"), _sch_num(r, "CORNER.Y")]]
            art.append([_SCH_ART[rec], int(r.get("OWNERPARTID") or 0), int(r.get("OWNERPARTDISPLAYMODE") or 0), pts,
                        _sch_num(r, "RADIUS") if "RADIUS" in r else None, r.get("TEXT", ""), r.get("ISSOLID", ""),
                        r.get("LINEWIDTH", "")])
    if "Description" not in params and head.get("COMPONENTDESCRIPTION"):
        params["Description"] = head["COMPONENTDESCRIPTION"]
    sym_d = {"name": entry["name"], "pins": _pin_table(pins, parts), "units": parts, "modes": modes, "art": sorted(art, key=json.dumps),
             "footprint_link": link or ""}
    return sym_d, params


def schlib(path, name=None):
    _, entries = AL.read_schlib(path)
    return altium_symbol(_pick_named(entries, name, "symbol"))


def kicad_sym_file(path, name=None):
    """'Lib.kicad_sym' + a symbol name (default: the first) -> (symbol, properties), `extends` resolved in the file."""
    lib = sexpr.parse(Path(path).read_text(encoding="utf-8"))
    syms = {str(s[1]): s for s in sexpr.find(lib, "symbol")}
    if not syms:
        raise ValueError(f"{path}: no symbols")
    name = name or next(iter(syms))
    if name not in syms:
        raise ValueError(f"symbol {name!r} not in {path}")
    chain = [syms[name]]
    while sexpr.first(chain[-1], "extends") and len(chain) < 8:
        parent = str(sexpr.first(chain[-1], "extends")[1])
        if parent not in syms:
            sib = Path(path).with_name(f"{parent}.kicad_sym")    # KiCad 9 symdir: the parent in its own file
            if not sib.is_file():
                raise ValueError(f"{name} extends {parent}, which is not in {path}")
            syms.update({str(s[1]): s for s in sexpr.find(sexpr.parse(sib.read_text(encoding="utf-8")), "symbol")})
        chain.append(syms[parent])
    return kicad_symbol(chain)


# ---------------------------------------------------------------- the fingerprint
def build(part, symbol=None, land=None, art=None, model=None, params=None, tool="", footprint_ref=None, symbol_ref=None,
          extra=None):
    """Assemble a fingerprint. symbol: kicad_symbol / altium_symbol's first value; land, art, model: a footprint
    reader's three values; params: the part's fields (plan parameters, symbol properties)."""
    params = dict(params or {})
    fp = {"schema": SCHEMA, "part": part, "tool": tool, "identity": identity(params), "fields": other_fields(params),
          "symbol": symbol, "footprint": None, "model": model}
    if land is not None:
        fp["footprint"] = dict(land, art=art or {"silk": [], "fab": [], "text": [], "other": [], "edge": []})
        if footprint_ref:
            fp["footprint"]["name"] = footprint_ref
    if symbol is not None and symbol_ref:
        fp["symbol"] = dict(symbol, name=symbol_ref)
    if extra:
        fp.update(extra)
    fp["hashes"] = hashes(fp)
    return fp


def pin_pad_map(fp):
    """{pin number: number of pads with that number} (0 = a pin with no pad), and pad numbers no pin has."""
    sym, foot = fp.get("symbol"), fp.get("footprint")
    if not sym or not foot:
        return {}, []
    count = {}
    for p in foot["pads"]:
        count[p["num"]] = count.get(p["num"], 0) + 1
    pins = sym["pins"]
    return {n: count.get(n, 0) for n in sorted(pins)}, sorted(n for n in count if n and n not in pins)


def _pin_signals(p):
    """[name, type] of a pin, or the sorted distinct [name, type] of its copies when they differ."""
    sig = sorted({(c["name"], c["type"]) for c in p.get("inst") or [p]})
    return list(sig[0]) if len(sig) == 1 else [list(x) for x in sig]


def electrical(fp):
    sym, foot = fp.get("symbol"), fp.get("footprint")
    ident = {k: v for k, v in fp["identity"].items() if k not in ("value_unit", "value_rest")}   # change_diff compares those
    if ident.get("value_num") is not None:
        ident["value"] = None                  # the number is the value; 100nF and 0.1uF are one part
    pmap, orphans = pin_pad_map(fp)
    return {"identity": {k: (str(v).strip().upper() if isinstance(v, str) else v) for k, v in ident.items()},
            "pins": {n: _pin_signals(p) for n, p in sorted((sym or {}).get("pins", {}).items())},
            "pads": sorted(p["num"] for p in (foot or {}).get("pads", [])), "pinmap": pmap, "orphan_pads": orphans}


def geometry(fp):
    foot = fp.get("footprint")
    if not foot:
        return None
    out = {k: foot[k] for k in ("pads", "windows", "copper", "courtyard")}
    out["pads"] = [{k: v for k, v in p.items() if k not in PAD_TOOL_KEYS} for p in out["pads"]]
    return out


def cosmetic(fp):
    sym, foot = fp.get("symbol") or {}, fp.get("footprint") or {}
    return {"fields": fp.get("fields"), "value": fp["identity"].get("value"),
            "names": {n: [p.get("raw"), p.get("shape"), p.get("at"), p.get("orient"), p.get("len")]
                      for n, p in sorted(sym.get("pins", {}).items())},
            "symbol_art": sym.get("art"), "symbol_name": sym.get("name"), "footprint_name": foot.get("name"),
            "art": foot.get("art")}


def hashes(fp):
    body = {k: v for k, v in fp.items() if k != "hashes"}
    return {"eid": digest(electrical(fp)), "gid": digest(geometry(fp)), "mid": digest(fp.get("model")),
            "cid": digest(cosmetic(fp)), "digest": digest(body)}


# ---------------------------------------------------------------- entry points
def _resolve(base, p):
    if not p:
        return None
    p = Path(p)
    return p if p.is_absolute() else Path(base) / p


def item_params(item):
    out = {p["name"]: str(p.get("value", "")) for p in item.get("parameters") or []}
    for k in item.get("cleared") or []:
        out.pop(k, None)
    for k in ("description", "comment"):
        if item.get(k):
            out.setdefault(k.capitalize(), item[k])
    return out


def of_item(item, base="."):
    """A plan item -> its fingerprint: the .SchLib and .PcbLib it releases (the item's refs pick the entries), its
    parameters, description and comment. A missing file leaves that half empty (None)."""
    sym = land = art = model = None
    notes = []
    sf = _resolve(base, (item.get("symbol") or {}).get("file"))
    ff = _resolve(base, (item.get("footprint") or {}).get("file"))
    if sf and sf.is_file():
        try:
            _, entries = AL.read_schlib(sf)
            ref = (item.get("symbol") or {}).get("ref")
            e = next((x for x in entries if x["name"] == ref), entries[0] if entries else None)
            if e:
                sym, _ = altium_symbol(e)
        except (ValueError, OSError) as ex:
            notes.append(f"symbol unreadable: {ex}")
    if ff and ff.is_file():
        try:
            entries = AL.read_pcblib(ff)
            ref = (item.get("footprint") or {}).get("ref")
            e = next((x for x in entries if x["name"] == ref), entries[0] if entries else None)
            if e:
                land, art, model = altium_footprint(e)
        except (ValueError, OSError) as ex:
            notes.append(f"footprint unreadable: {ex}")
    fp = build(item.get("name") or "", sym, land, art, model, item_params(item), "altium",
               footprint_ref=(item.get("footprint") or {}).get("ref"), symbol_ref=(item.get("symbol") or {}).get("ref"),
               extra={"notes": notes} if notes else None)
    return fp


def of_kicad(symbol=None, kicad_mod=None, params=None, part=None, footprint_ref=None):
    """A KiCad symbol (node / chain / None) and footprint (.kicad_mod text / tree / None) -> fingerprint. The symbol's
    properties are the fields unless params is given."""
    sym, props = kicad_symbol(symbol) if symbol is not None else (None, {})
    land = art = model = None
    if kicad_mod is not None:
        land, art, model = kicad_footprint(kicad_mod)
    fields = dict(props if params is None else params)
    for k in ("Reference", "Footprint", "Datasheet", "ki_keywords", "ki_fp_filters", "ki_description", "ki_locked"):
        if params is None and k not in ("Datasheet",):
            fields.pop(k, None)
    return build(part or (sym or {}).get("name") or (land or {}).get("name") or "", sym, land, art, model, fields, "kicad",
                 footprint_ref=footprint_ref)


def of_files(*paths, part=None, params=None):
    """Any mix of 'X.PcbLib[:Name]', 'X.SchLib[:Name]', 'X.kicad_mod', 'Lib.kicad_sym[:Name]' -> one fingerprint."""
    sym = land = art = model = None
    props, tool = {}, set()
    for spec in paths:
        m = re.match(r"(.+?\.(?:PcbLib|SchLib|kicad_mod|kicad_sym))(?::(.+))?$", str(spec), re.I)
        if not m:
            raise ValueError(f"{spec}: give a .PcbLib, .SchLib, .kicad_mod or .kicad_sym (optionally :NAME)")
        path, name = Path(m.group(1)), m.group(2)
        if not path.is_file():
            raise FileNotFoundError(f"{path}: no such file")
        ext = path.suffix.lower()
        if ext == ".pcblib":
            land, art, model = pcblib(path, name); tool.add("altium")
        elif ext == ".schlib":
            sym, props = schlib(path, name); tool.add("altium")
        elif ext == ".kicad_mod":
            land, art, model = kicad_footprint(path.read_text(encoding="utf-8")); tool.add("kicad")
        else:
            sym, props = kicad_sym_file(path, name); tool.add("kicad")
            for k in ("Reference", "Footprint", "ki_keywords", "ki_fp_filters", "ki_description", "ki_locked"):
                props.pop(k, None)
    return build(part or (sym or {}).get("name") or (land or {}).get("name") or "", sym, land, art, model,
                 props if params is None else params, "+".join(sorted(tool)))


def short(h, n=10):
    return (h or "")[:n]
