"""KiCad <-> Altium parity: read back BOTH libraries from the files actually written and compare them part by part.

    ecadlib parity out/librarian/plan.json                        plan mode: models/KiCad/*.kicad_mod vs .PcbLib,
                                                                   .SchLib pins vs the KiCad pads
    ecadlib parity out/librarian/plan.json --kicad out/kicad [--name mylib] [--altium D:/libs/ecadlib]
                                                                   export mode: kicad-export's .kicad_sym / .pretty /
                                                                   .3dshapes vs the Altium files; with --altium also the
                                                                   .kicad_dbl SQLite rows vs the DbLib rows
    ecadlib parity pair X.kicad_mod X.PcbLib                       one footprint (or Lib.kicad_sym:Name X.SchLib)
    ecadlib parity survey [--quick]                                every generator / standard-table footprint, both ways
    [--json] [-o REPORT_STEM] [--no-pyaltiumlib]

What is compared (per part):
  footprint  pad count and numbers; per pad position, size, rotation, shape (round / rect / rounded rect with its
             corner %, custom polygon outline), side (top / bottom SMD, plated through-hole, NPTH), drill (diameter,
             slot length and slot direction), paste and solder mask (on / off, manual margins); paste / mask windows
             without copper (exposed-pad paste); the exposed pad (size, paste windows, coverage); courtyard extent;
             3D model presence (export mode: the KiCad (model ...) file must exist next to the library)
  symbol     pin count, numbers, names (KiCad ~{X} = Altium X\\ overline), electrical type; pin -> pad mapping
             (pins without a pad in one tool but not the other)
  fields     MPN, Manufacturer, Value, Description, Datasheet: KiCad symbol properties vs the Altium values (the DbLib
             row with --altium, else the plan parameters altium365 push releases); footprint link names
  database   (--kicad with a .kicad_dbl and --altium with a DbLib) the same parts, each row naming the same
             symbol / footprint and the same fields
pyaltiumlib (when installed) re-reads every .PcbLib as an independent second reader: designators, positions,
sizes and holes must agree with ecadlib's reader.

Conventions, handled once here (_mm / _a2k / canon_pad): everything is compared in KiCad's frame, millimetres, y down.
Altium stores 1 mil = 10000 internal units (1 unit = 2.54 nm) with y up; angles are counter-clockwise on screen in both
tools, so rotations compare as they are. A pad's (w, h, rotation) is reduced to rotation in [0, 90) degrees (a pad is
the same after 180 degrees, and w x h at r equals h x w at r + 90); round pads drop their rotation. Slots compare by
length, width and axis direction (mod 180). Tolerances are TOL (mm, degrees, percent).

Levels: `error` is a real difference (the release check holds the part, `[release] parity = "hold"`); `warn` is a
documented approximation of the converters (a curved custom pad drawn as its bounding rectangle, a trapezoid as a
rectangle, ...): reported, not held."""
import argparse, copy, json, math, re, sys
from pathlib import Path

from . import altium_lib as AL, sexpr
from .fsutil import report_file

TOL = {"position": 0.001, "size": 0.001, "drill": 0.001, "margin": 0.001, "rotation": 0.01, "corner": 1.0,
       "courtyard": 0.005, "window": 0.002, "outline": 0.005, "coverage": 0.5,
       "text": 0.01}
MODES = ("hold", "warn", "off")


# ---------------------------------------------------------------- units and conventions (the one place)
def _mm(u):
    """Altium internal units -> mm."""
    return AL.to_mm(u)


def _a2k(x, y):
    """Altium point (internal units, y up) -> KiCad frame (mm, y down)."""
    return _mm(x), -_mm(y)


def _f(v):
    return float(v)


def canon_pad(w, h, rot, round_=False):
    """(w, h, rotation) of a pad outline -> one representation: rotation in [0, 90), w and h swapped when that took
    90 degrees off. A round pad (circle) has no rotation."""
    rot = round(float(rot) % 180.0, 6) % 180.0
    if rot >= 90.0:
        w, h, rot = h, w, rot - 90.0
    if rot > 90.0 - 1e-6:
        w, h, rot = h, w, 0.0
    if round_ and abs(w - h) < 1e-9:
        rot = 0.0
    return w, h, rot


def _angle_diff(a, b, period=180.0):
    d = abs(a - b) % period
    return min(d, period - d)


def _arc_bbox(cx, cy, r, a0, a1):
    """Points (x, y) bounding a counter-clockwise arc from a0 to a1 degrees (exact: ends plus axis extremes)."""
    span = (a1 - a0) % 360 or 360
    angs = [a0, a0 + span] + [k * 90.0 for k in range(-4, 9) if a0 < k * 90.0 < a0 + span]
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in angs]


def _bbox(pts):
    pts = list(pts)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _poly_area(pts):
    q = list(pts)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(q, q[1:] + q[:1]))) / 2


def _win_key(kind, box):
    return (kind, tuple(round(v, 4) for v in box))


def _rot_pts(cx, cy, w, h, rot):
    """Corners of a w x h rectangle at (cx, cy) rotated counter-clockwise on screen, KiCad frame (y down)."""
    a = math.radians(rot)
    out = []
    for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + u * math.cos(a) + v * math.sin(a), cy - u * math.sin(a) + v * math.cos(a)))
    return out


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:g}"
    if isinstance(v, tuple):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    return "-" if v is None or v == "" else str(v)


# ---------------------------------------------------------------- KiCad side
def _kicad_drill(p):
    """-> ((width, height) or None, copper offset from the hole in the pad frame (KiCad, y down))."""
    d = sexpr.first(p, "drill")
    if not d:
        return None, (0.0, 0.0)
    vals = [_f(v) for v in d[1:] if isinstance(v, str) and v != "oval"]
    off = sexpr.first(d, "offset")
    off = (_f(off[1]), _f(off[2])) if off else (0.0, 0.0)
    if not vals:
        return None, off
    return (vals[0], vals[1] if len(vals) > 1 else vals[0]), off


def _turn(v, rot):
    """A pad-frame vector (KiCad frame, y down) turned with the pad (counter-clockwise on screen) -> footprint frame."""
    a, (u, w) = math.radians(rot), v
    return (u * math.cos(a) + w * math.sin(a), -u * math.sin(a) + w * math.cos(a))


def _custom_bbox_k(p, x, y, w, h, rot):
    """Bounding box (KiCad frame) of a custom pad's anchor plus all its primitives (stroke widths included), and whether
    a primitive is curved (kicad_import then writes this box as a rectangle)."""
    xs, ys, curved = [-w / 2, w / 2], [-h / 2, h / 2], False
    for g in (sexpr.first(p, "primitives") or [])[1:]:
        if not isinstance(g, list):
            continue
        hw = _f((sexpr.first(g, "width") or sexpr.first(sexpr.first(g, "stroke") or [], "width") or [None, 0])[1]) / 2
        curved |= g[0] in ("gr_arc", "gr_circle", "gr_curve")
        if g[0] == "gr_circle":
            c, e = sexpr.first(g, "center"), sexpr.first(g, "end")
            r = math.hypot(_f(e[1]) - _f(c[1]), _f(e[2]) - _f(c[2]))
            pts = [(_f(c[1]) - r, _f(c[2]) - r), (_f(c[1]) + r, _f(c[2]) + r)]
        elif sexpr.first(g, "pts"):
            pts = [(_f(c[1]), _f(c[2])) for c in sexpr.find(sexpr.first(g, "pts"), "xy")]
        else:
            pts = [(_f(c[1]), _f(c[2])) for c in (sexpr.first(g, k) for k in ("start", "mid", "end")) if c]
        xs += [px + s * hw for px, _ in pts for s in (-1, 1)]
        ys += [py + s * hw for _, py in pts for s in (-1, 1)]
    corners = [(u, v) for u in (min(xs), max(xs)) for v in (min(ys), max(ys))]
    return _bbox((x + dx, y + dy) for dx, dy in (_turn(c, rot) for c in corners)), curved


def _custom_parts_k(p, x, y, rot):
    """Bounding boxes (KiCad frame) of a KiCad custom pad's filled primitives (polygons, rectangles, circles, lines; their
    stroke width included), or None when it has curves or unfilled outlines (the converters approximate those)."""
    boxes = []
    for g in (sexpr.first(p, "primitives") or [])[1:]:
        if not isinstance(g, list):
            continue
        hw = _f((sexpr.first(g, "width") or sexpr.first(sexpr.first(g, "stroke") or [], "width") or [None, 0])[1]) / 2
        fill = sexpr.first(g, "fill")
        filled = (fill is None and g[0] == "gr_poly") or bool(fill and fill[1] in ("yes", "solid"))
        if g[0] == "gr_line":
            pts = [(_f(c[1]), _f(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
        elif g[0] == "gr_poly" and filled and sexpr.first(g, "pts"):
            pts = [(_f(c[1]), _f(c[2])) for c in sexpr.find(sexpr.first(g, "pts"), "xy")]
        elif g[0] == "gr_rect" and filled:
            (x1, y1), (x2, y2) = [(_f(c[1]), _f(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
            pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        elif g[0] == "gr_circle" and filled:
            c, e = sexpr.first(g, "center"), sexpr.first(g, "end")
            r = math.hypot(_f(e[1]) - _f(c[1]), _f(e[2]) - _f(c[2]))
            pts = [(_f(c[1]) + r * math.cos(k * math.pi / 2), _f(c[2]) + r * math.sin(k * math.pi / 2)) for k in range(4)]
        else:
            return None
        box = []
        for u, v in pts:
            for du, dv in ((-hw, -hw), (hw, hw), (-hw, hw), (hw, -hw)):
                dx, dy = _turn((u + du, v + dv), rot)
                box.append((x + dx, y + dy))
        boxes.append(_bbox(box))
    return boxes


def _custom_outline_k(p, x, y, rot):
    """Bounding box of all of a custom pad's primitives (see _custom_parts_k), or None."""
    boxes = _custom_parts_k(p, x, y, rot)
    return None if boxes is None else _bbox([c for b in boxes if b for c in (b[:2], b[2:])])


def _filled(g):
    from .kicad_import import _filled as f
    return f(g)


def kicad_footprint(text):
    """A .kicad_mod -> canonical footprint dict (KiCad frame, mm)."""
    from . import kicad_import as K
    t = sexpr.parse(text) if isinstance(text, str) else copy.deepcopy(text)
    K.inherit_margins(t)                      # footprint-level mask / paste margins: every pad's, as KiCad applies them
    out = {"name": str(t[1]), "pads": [], "windows": [], "window_area": {}, "courtyard": None, "models": [], "notes": [],
           "approx": []}
    for p in sexpr.find(t, "pad"):
        num, kind, shape = str(p[1]), p[2], p[3]
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y = _f(at[1]), _f(at[2])
        rot = _f(at[3]) if len(at) > 3 else 0.0
        w, h = _f(size[1]), _f(size[2])
        layers = [str(l) for l in (sexpr.first(p, "layers") or ["layers"])[1:]]
        cu = [l for l in layers if l.endswith(".Cu")]
        if kind in ("smd", "connect") and not cu:            # paste / mask window without copper, on each of its layers
            kinds = ["paste" if "Paste" in l else "mask" for side in ("F", "B") for l in (f"{side}.Paste", f"{side}.Mask")
                     if l in layers or "*." + l[2:] in layers or "F&B." + l[2:] in layers]
            parts = _custom_parts_k(p, x, y, rot) if shape == "custom" else []
            for what in kinds:
                if shape == "custom" and parts is None:      # curves: one bounding rectangle
                    box, _ = _custom_bbox_k(p, x, y, w, h, rot)
                    out["windows"].append((what, box))
                    out["approx"].append(f"{what} window with curves written as its bounding rectangle")
                else:                                       # the anchor, then each primitive (Altium: one region each)
                    out["windows"] += [(what, b) for b in [_bbox(_rot_pts(x, y, w, h, rot))] + parts]
                    rr = sexpr.first(p, "roundrect_rratio")
                    r = min(_f(rr[1]), 0.5) * min(w, h) if rr and shape == "roundrect" else 0.0
                    if r > 0:                               # the true area of a rounded window (EXP-10)
                        out["window_area"][_win_key(what, _bbox(_rot_pts(x, y, w, h, rot)))] = w * h - (4 - math.pi) * r * r
            continue
        rr = sexpr.first(p, "roundrect_rratio")
        kshape = {"circle": "round", "oval": "round"}.get(shape, shape)
        corner = 0.0
        if shape == "roundrect":
            corner = min(100.0, _f(rr[1]) * 200) if rr else 0.0
            kshape = "roundrect" if corner >= 0.5 else "rect"
        pad = {"num": num, "x": x, "y": y, "shape": kshape, "corner": corner, "approx": []}
        if sexpr.first(p, "chamfer") and sexpr.first(p, "chamfer_ratio") and _f(sexpr.first(p, "chamfer_ratio")[1]) > 0:
            pad["approx"].append("chamfered corners")
        if shape == "trapezoid":
            pad["approx"].append("trapezoid")
            pad["shape"] = "rect"
        if shape == "custom":
            pad["anchor"] = (sexpr.first(sexpr.first(p, "options") or [], "anchor") or [None, "circle"])[1]
            pad["outline"] = _custom_outline_k(p, x, y, rot)
            if pad["outline"] is None:        # curves: kicad_import writes the bounding rectangle of anchor + primitives
                pad["approx"].append("custom pad with curves written as its bounding rectangle")
        if kind == "np_thru_hole":
            pad["side"] = "NPTH"
        elif kind == "thru_hole":
            pad["side"] = "THT"
        else:
            pad["side"] = "F" if "F.Cu" in cu or "*.Cu" in cu else "B"
        drill, offset = _kicad_drill(p)
        if drill:
            dmin, dmax = min(drill), max(drill)
            ang = None if dmax - dmin < 1e-9 else (rot + (0.0 if drill[0] >= drill[1] else 90.0)) % 180.0
            pad["drill"] = (dmin, dmax, ang)
        else:
            pad["drill"] = None
        pad["offset"] = _turn(offset, rot)    # copper centre - (at): the hole stays at (at)
        if not drill:                         # no hole (SMD): the offset only moves the copper
            pad["x"], pad["y"] = x + pad["offset"][0], y + pad["offset"][1]
            pad["offset"] = (0.0, 0.0)
        pad["w"], pad["h"], pad["rot"] = canon_pad(w, h, rot, pad["shape"] == "round")
        if pad["side"] in ("F", "B"):
            pad["paste"] = any("Paste" in l for l in layers)
        pad["mask"] = any("Mask" in l for l in layers)
        mm_, pm = sexpr.first(p, "solder_mask_margin"), sexpr.first(p, "solder_paste_margin")
        pad["mask_margin"] = _f(mm_[1]) if mm_ else None
        pad["paste_margin"] = _f(pm[1]) if pm and pad.get("paste") else None
        if sexpr.first(p, "solder_paste_margin_ratio") and _f(sexpr.first(p, "solder_paste_margin_ratio")[1]):
            pad["approx"].append("paste margin ratio")
        out["pads"].append(pad)
    for kind in ("fp_rect", "fp_poly", "fp_circle"):          # filled footprint graphics on mask / paste: windows too
        for g in sexpr.find(t, kind):
            lay = (sexpr.first(g, "layer") or [None, ""])[1]
            if lay not in ("F.Mask", "B.Mask", "F.Paste", "B.Paste") or not (_filled(g) or kind == "fp_poly" and not sexpr.first(g, "fill")):
                continue
            if kind == "fp_poly":
                q = [(_f(a[1]), _f(a[2])) for a in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
            elif kind == "fp_circle":
                c, e = sexpr.first(g, "center"), sexpr.first(g, "end")
                r = math.hypot(_f(e[1]) - _f(c[1]), _f(e[2]) - _f(c[2]))
                q = [(_f(c[1]) - r, _f(c[2]) - r), (_f(c[1]) + r, _f(c[2]) + r)]
            else:
                q = [(_f(c[1]), _f(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
            if _bbox(q):
                out["windows"].append(("mask" if lay.endswith("Mask") else "paste", _bbox(q)))
    out["copper"] = []                        # footprint-level copper graphics (bounding boxes)
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle"):
        for g in sexpr.find(t, kind):
            if (sexpr.first(g, "layer") or [None, None])[1] not in ("F.Cu", "B.Cu"):
                continue
            if kind == "fp_poly":
                q = [(_f(a[1]), _f(a[2])) for a in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
            elif kind == "fp_circle":
                c, e = sexpr.first(g, "center"), sexpr.first(g, "end")
                r = math.hypot(_f(e[1]) - _f(c[1]), _f(e[2]) - _f(c[2]))
                q = [(_f(c[1]) - r, _f(c[2]) - r), (_f(c[1]) + r, _f(c[2]) + r)]
            else:
                q = [(_f(c[1]), _f(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
            sw = sexpr.first(sexpr.first(g, "stroke") or [], "width") or sexpr.first(g, "width")
            hw = _f(sw[1]) / 2 if sw else 0.0
            b = _bbox(q)
            if b:
                out["copper"].append((b[0] - hw, b[1] - hw, b[2] + hw, b[3] + hw))
    pts = []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexpr.find(t, kind):
            if (sexpr.first(g, "layer") or [None, None])[1] != "F.CrtYd":
                continue
            if kind == "fp_poly":
                pts += [(_f(a[1]), _f(a[2])) for a in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
            elif kind == "fp_circle":
                c, e = sexpr.first(g, "center") or sexpr.first(g, "start"), sexpr.first(g, "end")
                r = math.hypot(_f(e[1]) - _f(c[1]), _f(e[2]) - _f(c[2]))
                pts += [(_f(c[1]) - r, _f(c[2]) - r), (_f(c[1]) + r, _f(c[2]) + r)]
            elif kind == "fp_arc" and sexpr.first(g, "mid"):
                from .kicad_import import _arc3
                s, m, e = ((_f(q[1]), _f(q[2])) for q in (sexpr.first(g, k) for k in ("start", "mid", "end")))
                try:
                    pts += _arc_bbox(*_arc3(s, m, e))
                except ValueError:
                    pts += [s, e]
            else:
                s, e = sexpr.first(g, "start"), sexpr.first(g, "end")
                if s and e:
                    pts += [(_f(s[1]), _f(s[2])), (_f(e[1]), _f(e[2]))]
    out["courtyard"] = _bbox(pts)
    out["models"] = [str(m[1]) for m in sexpr.find(t, "model")]
    fab = [x for x in AL.texts_from_kicad(t) if x[0] == ".Designator" and x[6] == AL.MECH13]
    out["fab_ref"] = (lambda c: (c[0], -c[1]))(AL.text_centre(*fab[0][:4], fab[0][5])) if fab else None
    return out


def kicad_symbol(sym):
    """A KiCad symbol tree (one `symbol` node, `extends` already flattened) -> canonical symbol dict."""
    props = {str(p[1]): str(p[2]) for p in sexpr.find(sym, "property")}
    pins = {}
    for u in sexpr.find(sym, "symbol"):
        parts = str(u[1]).rsplit("_", 2)
        style = int(parts[-1]) if len(parts) == 3 and parts[-1].isdigit() else 1
        if style > 1:                         # De Morgan alternate: the normal view is compared
            continue
        for p in sexpr.find(u, "pin"):
            num = str((sexpr.first(p, "number") or ["", ""])[1])
            name = str((sexpr.first(p, "name") or ["", ""])[1])
            hidden = "hide" in p[1:] or bool(sexpr.first(p, "hide") and _yes(sexpr.first(p, "hide")))
            pins.setdefault(num, {"name": name, "type": str(p[1]), "hidden": hidden})
    return {"name": str(sym[1]), "props": props, "pins": pins}


def _yes(node):
    return len(node) < 2 or node[1] == "yes"


def kicad_pin_name(name):
    """KiCad pin name -> the Altium spelling it is written as (overbars, '~' = no name)."""
    from .kicad_import import _pin_text
    s = _pin_text(name)
    return "" if s in ("~", "") else s


def kicad_type(t):
    """KiCad electrical type -> Altium's pin electrical type number (altium_lib.ETYPE), as kicad_import writes it."""
    from .kicad_import import KTYPE
    return AL.ETYPE[KTYPE.get(t, "passive")]


# ---------------------------------------------------------------- Altium side
def altium_footprint(fp, courtyard=AL.MECH15):
    """One footprint from altium_lib.read_pcblib -> canonical footprint dict (KiCad frame, mm)."""
    from .kicad_export import _custom_pads
    recs = fp["records"]
    custom, used = _custom_pads(recs)
    out = {"name": fp["name"], "pads": [], "windows": [], "window_area": {}, "courtyard": None, "bodies": 0, "embedded": 0,
           "notes": []}
    for r in recs:
        if r["type"] != "pad":
            continue
        x, y = _a2k(r["x"], r["y"])
        w, h = _mm(r["w"]), _mm(r["h"])
        shape = {1: "round", 2: "rect", 9: "roundrect", 3: "octagonal"}.get(r["shape"], f"shape {r['shape']}")
        corner = float(r["corner"] or 0) if shape == "roundrect" else 0.0
        if shape == "roundrect" and corner < 0.5:
            shape = "rect"
        pad = {"num": r["designator"], "x": x, "y": y, "shape": shape, "corner": corner, "approx": []}
        if id(r) in custom:
            pad["anchor"] = "circle" if r["shape"] == 1 else "rect"
            pad["shape"] = "custom"
            pts = [_a2k(px, py) for poly in custom[id(r)] for px, py in poly]
            pad["outline"] = _bbox(pts)
        if r["layer"] == AL.MULTI:
            pad["side"] = "THT" if r["plated"] else "NPTH"
        else:
            pad["side"] = "F" if r["layer"] == AL.TOP else "B" if r["layer"] == AL.BOTTOM else f"layer {r['layer']}"
        if r["hole"]:
            hole = _mm(r["hole"])
            slot = _mm(r["slot"]) if r["hole_shape"] == 2 and r["slot"] > r["hole"] else hole
            ang = None if slot - hole < 1e-9 else (r["rot"] + r["hole_rot"]) % 180.0
            pad["drill"] = (hole, slot, ang)
            ox, oy = r.get("offset") or (0, 0)
            pad["offset"] = _turn((_mm(ox), -_mm(oy)), r["rot"])
            if r["hole_shape"] == 1:
                pad["approx"].append("square hole")
        else:
            pad["drill"] = None
            pad["offset"] = (0.0, 0.0)
        pad["w"], pad["h"], pad["rot"] = canon_pad(w, h, r["rot"], pad["shape"] == "round")
        closed = lambda mode, v: mode == 2 and v is not None and v <= -max(r["w"], r["h"]) // 2
        if pad["side"] in ("F", "B"):
            pad["paste"] = not closed(r["paste_mode"], r["paste"])
            pad["paste_margin"] = _mm(r["paste"]) if pad["paste"] and r["paste_mode"] == 2 and r["paste"] else None
        else:
            pad["paste_margin"] = None
        pad["mask"] = not closed(r["mask_mode"], r["mask"])
        pad["mask_margin"] = _mm(r["mask"]) if pad["mask"] and r["mask_mode"] == 2 else None
        out["pads"].append(pad)
    win_layers = {AL.TOP_PASTE: "paste", AL.BOTTOM_PASTE: "paste", AL.TOP_SOLDER: "mask", AL.BOTTOM_SOLDER: "mask"}
    cy, out["copper"] = [], []
    for r in recs:
        if id(r) in used:
            continue
        t, layer = r["type"], r.get("layer")
        if layer in (AL.TOP, AL.BOTTOM) and t in ("region", "fill", "track"):    # copper that is not part of a pad
            q = [_a2k(px, py) for px, py in r["pts"]] if t == "region" else [_a2k(r["x1"], r["y1"]), _a2k(r["x2"], r["y2"])]
            hw = _mm(r.get("w") or 0) / 2 if t == "track" else 0.0
            b = _bbox(q)
            out["copper"].append((b[0] - hw, b[1] - hw, b[2] + hw, b[3] + hw))
        if t == "fill" and layer in win_layers:
            a, b = _a2k(r["x1"], r["y1"]), _a2k(r["x2"], r["y2"])
            out["windows"].append((win_layers[layer], _bbox([a, b])))
        elif t == "region" and layer in win_layers:
            q = [_a2k(px, py) for px, py in r["pts"]]
            out["windows"].append((win_layers[layer], _bbox(q)))
            out["window_area"][_win_key(win_layers[layer], _bbox(q))] = _poly_area(q)    # its outline, not its box
        if layer != courtyard:
            continue
        if t == "track":
            cy += [_a2k(r["x1"], r["y1"]), _a2k(r["x2"], r["y2"])]
        elif t == "arc":
            cy += [_a2k(px, py) for px, py in _arc_bbox(r["cx"], r["cy"], r["r"], r["a0"], r["a1"])]
        elif t == "region":
            cy += [_a2k(px, py) for px, py in r["pts"]]
        elif t == "fill":
            cy += [_a2k(r["x1"], r["y1"]), _a2k(r["x2"], r["y2"])]
    out["courtyard"] = _bbox(cy)
    out["bodies"] = sum(1 for r in recs if r["type"] == "body")
    out["embedded"] = len(fp.get("models") or [])
    fab = [r for r in recs if r["type"] == "text" and r.get("text") == ".Designator"     # assembly-layer designator: any
           and r.get("layer") not in (AL.TOP_OVERLAY, AL.BOTTOM_OVERLAY, AL.TOP, AL.BOTTOM)]   # layer but silk / copper
    out["fab_designator"] = (lambda c: (c[0], -c[1]))(AL.text_centre(".Designator", _mm(fab[0]["x"]), _mm(fab[0]["y"]),
                                                             _mm(fab[0]["h"]), fab[0]["rot"])) if fab else None
    return out


def altium_symbol(entry):
    """One symbol from altium_lib.read_schlib -> canonical symbol dict."""
    recs = entry["records"]
    net = {int(r["OWNERINDEX"]): r.get("TEXT", "") for r in recs if r.get("RECORD") == "41" and r.get("NAME") == "HiddenNetName"
           and str(r.get("OWNERINDEX", "")).lstrip("-").isdigit()}
    pins, params, link = {}, {}, None
    for i, r in enumerate(recs):
        if r.get("RECORD") == "2" and not r.get("mode"):
            hidden = bool(r["conglomerate"] & 0x04)
            pins.setdefault(r["num"], {"name": r["name"], "type": r["etype"], "hidden": hidden, "net": net.get(i) if hidden else None})
        elif r.get("RECORD") == "41" and r.get("NAME") not in (None, "HiddenNetName", "Comment"):
            params[r["NAME"]] = r.get("TEXT", "")
        elif r.get("RECORD") == "45" and str(r.get("MODELTYPE", "")).upper() == "PCBLIB":
            link = r.get("MODELNAME")
    desc = recs[0].get("COMPONENTDESCRIPTION", "") if recs else ""
    return {"name": entry["name"], "pins": pins, "params": params, "footprint": link, "description": desc}


def pyaltiumlib_pads(path):
    """[(designator, x, y, w, h, hole mm)] read by pyaltiumlib (KiCad frame), or None when it is not installed."""
    try:
        import logging
        logging.getLogger("pyaltiumlib").setLevel(logging.CRITICAL)
        import pyaltiumlib
    except ImportError:
        return None
    parts = pyaltiumlib.read(str(path)).Parts
    out = []
    for r in parts[0].Records if parts else []:
        if type(r).__name__ != "PcbPad":
            continue
        mil = 0.0254
        out.append((str(r.designator), float(r.location.x) * mil, float(r.location.y) * mil,     # pyaltiumlib: y down
                    abs(float(r.size_top.x)) * mil, abs(float(r.size_top.y)) * mil, float(r.hole_size or 0) * mil))
    return out


# ---------------------------------------------------------------- comparisons
def _d(out, level, what, field, k, a, note=""):
    msg = f"{what} {field}: KiCad {_fmt(k)} / Altium {_fmt(a)}" + (f" ({note})" if note else "")
    out.append({"level": level, "what": what, "field": field, "kicad": k, "altium": a, "msg": msg})


def _near(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def _pair_pads(kp, ap):
    """Match pads by number, then by position within a number. -> ([(k, a)], [k only], [a only])."""
    key = lambda p: (p["num"], round(p["x"], 3), round(p["y"], 3))
    by_k, by_a = {}, {}
    for p in sorted(kp, key=key):
        by_k.setdefault(p["num"], []).append(p)
    for p in sorted(ap, key=key):
        by_a.setdefault(p["num"], []).append(p)
    pairs, konly, aonly = [], [], []
    for n in sorted(set(by_k) | set(by_a)):
        ks, as_ = by_k.get(n, []), by_a.get(n, [])
        rest = list(as_)
        for k in ks:                          # nearest unmatched Altium pad with that number
            if not rest:
                konly.append(k); continue
            j = min(range(len(rest)), key=lambda i: math.hypot(rest[i]["x"] - k["x"], rest[i]["y"] - k["y"]))
            pairs.append((k, rest.pop(j)))
        aonly += rest
    return pairs, konly, aonly


def compare_pad(k, a, out):
    what = f"pad {k['num'] or '(unnumbered)'}"
    curved = k["shape"] == "custom" and k.get("outline") is None and a["shape"] != "custom"
    if not curved and not (_near(k["x"], a["x"], TOL["position"]) and _near(k["y"], a["y"], TOL["position"])):
        _d(out, "error", what, "position", (k["x"], k["y"]), (a["x"], a["y"]), "mm, KiCad frame")
    if k["side"] != a["side"]:
        _d(out, "error", what, "side", k["side"], a["side"])
    ko, ao = k.get("offset") or (0.0, 0.0), a.get("offset") or (0.0, 0.0)
    if abs(ko[0] - ao[0]) > TOL["position"] or abs(ko[1] - ao[1]) > TOL["position"]:
        _d(out, "error", what, "copper offset from hole", ko, ao, "mm, KiCad frame")
    kd, ad = k.get("drill"), a.get("drill")
    if bool(kd) != bool(ad):
        _d(out, "error", what, "drill", kd, ad)
    elif kd:
        if not (_near(kd[0], ad[0], TOL["drill"]) and _near(kd[1], ad[1], TOL["drill"])):
            _d(out, "error", what, "drill", kd[:2], ad[:2], "width, length mm")
        elif (kd[2] is None) != (ad[2] is None) or kd[2] is not None and _angle_diff(kd[2], ad[2]) > TOL["rotation"]:
            _d(out, "error", what, "slot direction", kd[2], ad[2], "degrees")
    npth = k["side"] == "NPTH" and a["side"] == "NPTH"
    kshape, ashape = k["shape"], a["shape"]
    approx = k["approx"] + a["approx"]
    if kshape == "custom" and k.get("outline") is None and ashape != "custom":
        return                                # curves: the bounding rectangle (kicad_import); reported once per footprint
    if kshape != ashape and not npth:
        _d(out, "warn" if approx else "error", what, "shape", kshape, ashape, ", ".join(approx))
        if not approx:
            return
    elif kshape == "roundrect" and not npth and abs(k["corner"] - a["corner"]) > TOL["corner"]:   # NPTH: no copper
        _d(out, "error", what, "corner radius", k["corner"], a["corner"], "% of the shorter side / 2")
    if kshape == "custom" and ashape == "custom":
        if k.get("anchor") != a.get("anchor"):
            _d(out, "error", what, "custom anchor", k.get("anchor"), a.get("anchor"))
        ko, ao = k.get("outline"), a.get("outline")
        if ko and ao and any(abs(p - q) > TOL["outline"] for p, q in zip(ko, ao)):
            _d(out, "error", what, "custom outline", ko, ao, "bounding box mm")
    size_k, size_a = (k["w"], k["h"], k["rot"]), (a["w"], a["h"], a["rot"])
    if not (_near(k["w"], a["w"], TOL["size"]) and _near(k["h"], a["h"], TOL["size"]) and
            _angle_diff(k["rot"], a["rot"], 90.0) <= TOL["rotation"]):
        if not npth:                          # NPTH: Altium's pad is the hole (no copper); the drill is compared
            _d(out, "warn" if approx else "error", what, "size/rotation", size_k, size_a, "w, h mm, rotation deg")
    if k["side"] in ("F", "B") and a["side"] in ("F", "B"):
        if k.get("paste") != a.get("paste"):
            _d(out, "error", what, "paste", "on" if k.get("paste") else "off", "on" if a.get("paste") else "off")
        elif k.get("paste") and abs((k.get("paste_margin") or 0) - (a.get("paste_margin") or 0)) > TOL["margin"]:
            _d(out, "error", what, "paste margin", k.get("paste_margin"), a.get("paste_margin"), "mm")
    if not npth:
        if k["mask"] != a["mask"]:
            _d(out, "error", what, "solder mask", "open" if k["mask"] else "tented", "open" if a["mask"] else "tented")
        elif k["mask"] and abs((k.get("mask_margin") or 0) - (a.get("mask_margin") or 0)) > TOL["margin"]:
            _d(out, "error", what, "mask margin", k.get("mask_margin"), a.get("mask_margin"), "mm")


def exposed_pad(fp):
    """The exposed pad: the biggest SMD pad over the footprint origin, with the paste windows inside it."""
    cands = [p for p in fp["pads"] if p["side"] in ("F", "B") and not p.get("drill")
             and abs(p["x"]) <= p["w"] / 2 + 1e-9 and abs(p["y"]) <= p["h"] / 2 + 1e-9 and len(fp["pads"]) > 2]
    if not cands:
        return None
    ep = max(cands, key=lambda p: p["w"] * p["h"])
    inside = [b for k, b in fp["windows"] if k == "paste" and b and ep["x"] - ep["w"] / 2 - 1e-6 <= (b[0] + b[2]) / 2 <= ep["x"] + ep["w"] / 2 + 1e-6
              and ep["y"] - ep["h"] / 2 - 1e-6 <= (b[1] + b[3]) / 2 <= ep["y"] + ep["h"] / 2 + 1e-6]
    wa = fp.get("window_area") or {}                  # a rounded window's own area, else its rectangle (EXP-10)
    area = sum(wa.get(_win_key("paste", b), (b[2] - b[0]) * (b[3] - b[1])) for b in inside)
    return {"num": ep["num"], "w": ep["w"], "h": ep["h"], "windows": len(inside),
            "coverage": round(100.0 * area / (ep["w"] * ep["h"]), 2) if inside else (100.0 if ep.get("paste") else 0.0)}


def copper_extent(fp):
    """Bounding box of all copper: pads (custom outlines included) and footprint copper graphics."""
    pts = []
    for p in fp["pads"]:
        if p.get("side") == "NPTH":
            continue
        pts += _rot_pts(p["x"], p["y"], p["w"], p["h"], p["rot"])
        o = p.get("outline")
        if o:
            pts += [(o[0], o[1]), (o[2], o[3])]
    for b in fp.get("copper") or []:
        pts += [(b[0], b[1]), (b[2], b[3])]
    return _bbox(pts)


def compare_footprints(k, a, export=False, model_ok=None):
    """Canonical KiCad and Altium footprints -> diffs. export: the KiCad side is a kicad-export library, so its 3D
    model must be there (model_ok: whether each (model ...) path resolves to a file)."""
    out = []
    approx = list(k.get("approx", [])) + [f"pad {p['num'] or '(unnumbered)'}: {x}" for p in k["pads"] for x in p["approx"]]
    lvl = "warn" if approx else "error"      # windows / exposed pad of a footprint with approximated shapes
    for x in approx:
        _d(out, "warn", "approximation", "shape", x, "as written", "kicad_import")
    pairs, konly, aonly = _pair_pads(k["pads"], a["pads"])
    if len(k["pads"]) != len(a["pads"]):
        _d(out, "error", "footprint", "pad count", len(k["pads"]), len(a["pads"]))
    for p in konly:
        _d(out, "error", f"pad {p['num'] or '(unnumbered)'}", "missing", "present", "missing", f"at {_fmt((p['x'], p['y']))}")
    for p in aonly:
        _d(out, "error", f"pad {p['num'] or '(unnumbered)'}", "missing", "missing", "present", f"at {_fmt((p['x'], p['y']))}")
    for kp, ap in pairs:
        compare_pad(kp, ap, out)
    if k.get("copper") or a.get("copper"):    # footprint copper next to / under pads: Altium regions belong to no pad
        ke, ae = copper_extent(k), copper_extent(a)
        same = ke and ae and all(abs(p - q) <= TOL["outline"] for p, q in zip(ke, ae))
        for d in out:
            if same and d["level"] == "error" and d["field"] in ("shape", "custom outline", "custom anchor"):
                d["level"] = "warn"
                d["msg"] += " (pad or footprint copper: the copper itself is the same, extent " + _fmt(ke) + ")"
    kw = sorted((w, tuple(round(v, 3) for v in b)) for w, b in k["windows"] if b)
    aw = sorted((w, tuple(round(v, 3) for v in b)) for w, b in a["windows"] if b)
    if len(kw) != len(aw):
        _d(out, lvl, "footprint", "paste/mask windows", len(kw), len(aw), "windows without copper")
    else:                                     # pair each KiCad window with the nearest Altium one of its kind
        left = list(aw)
        for wk, bk in kw:
            same = [x for x in left if x[0] == wk] or left
            best = min(same, key=lambda x: max(abs(p - q) for p, q in zip(bk, x[1])))
            left.remove(best)
            if best[0] != wk or any(abs(p - q) > TOL["window"] for p, q in zip(bk, best[1])):
                _d(out, lvl, f"{wk} window", "extent", bk, best[1], "x0, y0, x1, y1 mm")
    ek, ea = exposed_pad(k), exposed_pad(a)
    if bool(ek) != bool(ea):
        _d(out, lvl, "exposed pad", "presence", ek and ek["num"], ea and ea["num"])
    elif ek and (ek["windows"] != ea["windows"] or abs(ek["coverage"] - ea["coverage"]) > TOL["coverage"]):
        _d(out, lvl, "exposed pad", "paste", f"{ek['windows']} windows {ek['coverage']}%", f"{ea['windows']} windows {ea['coverage']}%")
    ck, ca = k["courtyard"], a["courtyard"]
    if bool(ck) != bool(ca):
        _d(out, "error", "courtyard", "presence", ck, ca)
    elif ck and any(abs(p - q) > TOL["courtyard"] for p, q in zip(ck, ca)):
        _d(out, "error", "courtyard", "extent", ck, ca, "x0, y0, x1, y1 mm")
    fk, fa = k.get("fab_ref"), a.get("fab_designator")
    if "fab_designator" not in a:             # the other side is not an Altium footprint
        pass
    elif fk and not fa:                       # the assembly drawing's designator (KiCad ${REFERENCE} on F.Fab):
        # a warning, so footprints older ecadlib wrote without one are not held
        _d(out, "warn", "fab designator", "presence", "${REFERENCE} on F.Fab", "no .Designator on the assembly layer",
           "an older ecadlib wrote this PcbLib; rebuild the footprint to add it")
    elif fk and any(abs(p - q) > TOL["text"] for p, q in zip(fk, fa)):
        _d(out, "warn", "fab designator", "centre", fk, fa, "x, y mm")
    has_a = a["bodies"] > 0 or a["embedded"] > 0
    if export:
        ok = [m for m, good in zip(k["models"], model_ok or [True] * len(k["models"])) if good]
        if bool(ok) != has_a:
            _d(out, "error", "3D model", "presence", (ok or ["missing" if not k["models"] else "file missing"])[0],
               f"{a['bodies']} body" + ("" if a["bodies"] == 1 else "ies") if has_a else "none")
    elif k["models"] and not has_a:
        _d(out, "error", "3D model", "presence", k["models"][0], "none")
    return out


def compare_symbols(k, a):
    out = []
    kn, an = set(k["pins"]), set(a["pins"])
    if len(kn) != len(an):
        _d(out, "error", "symbol", "pin count", len(kn), len(an))
    for n in sorted(kn - an):
        _d(out, "error", f"pin {n}", "missing", k["pins"][n]["name"], "missing")
    for n in sorted(an - kn):
        _d(out, "error", f"pin {n}", "missing", "missing", a["pins"][n]["name"])
    for n in sorted(kn & an):
        kp, ap = k["pins"][n], a["pins"][n]
        kname = kicad_pin_name(kp["name"])
        names = {ap["name"]} | ({kicad_pin_name(ap["net"]), ap["net"]} if ap.get("net") else set())
        if kname not in names:
            _d(out, "error", f"pin {n}", "name", kp["name"], ap["name"])
        kt = kicad_type(kp["type"])
        stacked = kp["hidden"] and kp["type"] == "passive" and ap["type"] == AL.ETYPE["power"]   # KLC S4.3 (kicad_export)
        if kt != ap["type"] and not stacked:
            from .kicad_export import ETYPE_K
            _d(out, "error", f"pin {n}", "electrical type", kp["type"], ETYPE_K.get(ap["type"], ap["type"]))
    return out


def compare_pin_pad(k_pins, k_pads, a_pins, a_pads):
    """Pins that have a pad in one tool but not in the other (mechanical pads may have no pin)."""
    out = []
    kmiss, amiss = set(k_pins) - set(k_pads), set(a_pins) - set(a_pads)
    for n in sorted(kmiss ^ amiss):           # missing in both: the release check's `pins` rule says so
        _d(out, "error", f"pin {n}", "pad", "no pad" if n in kmiss else "pad", "no pad" if n in amiss else "pad",
           "pin -> pad mapping differs")
    return out


KEY_FIELDS = ("Value", "Description", "Manufacturer", "MPN", "Datasheet")


def kicad_fields(props, rules=None):
    """KiCad symbol properties -> {key field: value}, honouring the rules' house names."""
    from . import rules as RU
    names = (rules or RU.DEFAULTS)["parameters"]["names"] or {}
    col = {"Value": "Value", "Description": "Description", "Datasheet": "Datasheet",
           "Manufacturer": names.get("Manufacturer", "Manufacturer"), "MPN": names.get("Manufacturer Part Number", "MPN")}
    return {k: str(props.get(c, "") or "").strip() for k, c in col.items()}


def altium_fields(row, rules=None):
    """A DbLib row (or the plan's parameters as one) -> {key field: value}."""
    from . import rules as RU
    p = lambda k: RU.param_name(k, rules)
    def get(*ks):
        v = next((str(row.get(k) or "").strip() for k in ks if str(row.get(k) or "").strip()), "")
        if v.startswith("=") and len(v) > 1:  # Altium's '=Part Number' / '=Value': the parameter it names
            ref = next((k for k in row if str(k).lower() == v[1:].strip().lower()), None)
            if ref is not None:
                return str(row.get(ref) or "").strip()
        return v
    return {"Value": get(p("Value"), "Value", "Comment"), "Description": get("Description"),
            "Manufacturer": get(p("Manufacturer"), "Manufacturer"),
            "MPN": get(p("Manufacturer Part Number"), "Manufacturer Part Number"),
            "Datasheet": get(p("ComponentLink2URL"), "ComponentLink2URL", p("Datasheet"), "Datasheet") or get(*(   # or the
                f"ComponentLink{m.group(1)}URL" for k in sorted(row) for m in [re.fullmatch(r"ComponentLink(\d+)Description", str(k))]
                if m and "datasheet" in str(row.get(k) or "").lower()))}          # link slot described "Datasheet" (as kicad-export)


def plan_row(item):
    row = {"Part Number": item["name"], "Description": item.get("description", ""), "Comment": item.get("comment", "")}
    row.update({p["name"]: str(p["value"]) for p in item.get("parameters", [])})
    return row


def compare_fields(kf, af, what="fields"):
    out = []
    for f in KEY_FIELDS:
        if kf.get(f, "") != af.get(f, ""):
            _d(out, "error", what, f, kf.get(f, ""), af.get(f, ""))
    return out


# ---------------------------------------------------------------- per part
def _courtyard_layer(rules):
    from . import rules as RU
    return RU.layer_id(((rules or RU.DEFAULTS).get("layers") or {}).get("courtyard", "Mechanical15"))


def footprint_pair(kicad_text, pcblib_fp, rules=None, export=False, model_ok=None):
    """Diffs between one .kicad_mod text and one read_pcblib footprint."""
    return compare_footprints(kicad_footprint(kicad_text), altium_footprint(pcblib_fp, _courtyard_layer(rules)), export, model_ok)


def release_diffs(kicad_text, pcblib_fp, schlib_syms=None, rules=None):
    """What release_check runs (plan mode, files already read): footprint parity plus SchLib pins -> KiCad pads."""
    k, a = kicad_footprint(kicad_text), altium_footprint(pcblib_fp, _courtyard_layer(rules))
    out = compare_footprints(k, a)
    if schlib_syms:
        pins = altium_symbol(schlib_syms[0])["pins"]
        out += compare_pin_pad(pins, {p["num"] for p in k["pads"]}, pins, {p["num"] for p in a["pads"]})
    return out


def _second_reader(path, out):
    """pyaltiumlib re-reads the .PcbLib; its pads must agree with ecadlib's reader."""
    try:
        theirs = pyaltiumlib_pads(path)
    except Exception as e:                    # its own parser failing is a finding, not a crash
        _d(out, "error", "pyaltiumlib", "read", "-", f"{type(e).__name__}: {e}")
        return False
    if theirs is None:
        return None
    raw = [r for r in AL.read_pcblib(path)[0]["records"] if r["type"] == "pad"]
    if len(theirs) != len(raw):
        _d(out, "error", "pyaltiumlib", "pad count", len(raw), len(theirs), "ecadlib reader vs pyaltiumlib")
        return False
    for (d, x, y, w, h, hole), r in zip(theirs, raw):
        mx, my = _a2k(r["x"], r["y"])
        if d != r["designator"] or abs(x - mx) > 0.01 or abs(y - my) > 0.01 or abs(w - _mm(r["w"])) > 0.01 \
                or abs(h - _mm(r["h"])) > 0.01 or abs(hole - _mm(r["hole"])) > 0.01:
            _d(out, "error", f"pad {d}", "pyaltiumlib", (r["designator"], mx, my, _mm(r["w"]), _mm(r["h"]), _mm(r["hole"])),
               (d, x, y, w, h, hole), "ecadlib reader vs pyaltiumlib")
            return False
    return True


def _rel(base, p):
    if not p:
        return None
    p = Path(p)
    return p if p.is_absolute() else Path(base) / p


class KiCadLib:
    """A kicad-export folder: <lib>.kicad_sym, <lib>.pretty/, <lib>.3dshapes/."""

    def __init__(self, folder, name=None):
        self.dir = Path(folder)
        syms = sorted(self.dir.glob("*.kicad_sym"))
        if name is None:
            if len(syms) != 1:
                raise ValueError(f"{folder}: {len(syms)} .kicad_sym files; pass --name")
            name = syms[0].stem
        elif not (self.dir / f"{name}.kicad_sym").is_file():   # the --name given to kicad-export, which names the
            from .kicad_export import libname                    # files libname(name); an older export: as given
            if not (self.dir / f"{libname(name)}.kicad_sym").is_file():
                raise ValueError(f"{folder}: no {name}.kicad_sym (the --name given to kicad-export)")
            name = libname(name)
        self.name = name
        f = self.dir / f"{name}.kicad_sym"
        self.symbols = {}
        if f.is_file():
            t = sexpr.parse(f.read_text(encoding="utf-8"))
            self.symbols = {str(s[1]): s for s in sexpr.find(t, "symbol")}
        self.pretty = self.dir / f"{name}.pretty"

    def footprint(self, fp_name):
        f = self.pretty / f"{fp_name}.kicad_mod"
        return f.read_text(encoding="utf-8") if f.is_file() else None

    def model_ok(self, path):
        p = str(path)
        if "${KIPRJMOD}" in p:
            return (self.dir / p.replace("${KIPRJMOD}/", "").replace("${KIPRJMOD}", "")).is_file()
        return Path(p).is_file()


def check_item(item, base=".", rules=None, kicad=None, altium_rows=None, second_reader=False):
    """Parity of one plan item -> {"part", "result" (match | mismatch | skipped), "diffs", "checked"}.
    kicad: a KiCadLib (export mode) or None (plan mode: the plan's .kicad_mod). altium_rows: {part: DbLib row}."""
    from . import kicad_export as X, release_check as RC
    name = item["name"]
    res = {"part": name, "result": "match", "diffs": [], "checked": []}
    diffs = res["diffs"]
    if item.get("a365") and item.get("status") in ("existing", "deleted_in_workspace"):
        return dict(res, result="skipped", why="in the Altium 365 workspace (no files here)")
    fp_f, sym_f = _rel(base, (item.get("footprint") or {}).get("file")), _rel(base, (item.get("symbol") or {}).get("file"))
    fps = AL.read_pcblib(fp_f) if fp_f and fp_f.is_file() else None
    syms = AL.read_schlib(sym_f)[1] if sym_f and sym_f.is_file() else None
    # a library of many (dedupe --merge onto a company file): the entry the plan names, as kicad-export writes it; a ref
    # the file does not hold raises (check_plan reports the part), never the file's first entry
    a_fp = altium_footprint(X._pick(fps, item["footprint"].get("ref"), fp_f, "footprint"), _courtyard_layer(rules)) if fps else None
    a_sym = altium_symbol(X._pick(syms, item["symbol"].get("ref"), sym_f, "symbol")) if syms else None
    k_fp = k_sym = None
    if kicad is None:
        kmod = RC._kicad_mod(item, base, fp_f) if fp_f else None
        if kmod:
            k_fp = kicad_footprint(kmod.read_text(encoding="utf-8"))
            res["kicad_footprint"] = str(kmod)
    else:
        sname = X.kname(name)
        sym = kicad.symbols.get(sname)
        if sym is not None:
            k_sym = kicad_symbol(sym)
            res["kicad_symbol"] = f"{kicad.name}:{sname}"
        fref = (item.get("footprint") or {}).get("ref")
        if fref:
            text = kicad.footprint(X.kname(fref))
            if text is not None:
                k_fp = kicad_footprint(text)
                res["kicad_footprint"] = f"{kicad.name}:{X.kname(fref)}"
        if k_sym is None and a_sym:
            _d(diffs, "error", "symbol", "presence", "missing", a_sym["name"], f"not in {kicad.name}.kicad_sym")
        if k_fp is None and a_fp:
            _d(diffs, "error", "footprint", "presence", "missing", a_fp["name"], f"not in {kicad.name}.pretty")
    if k_fp and a_fp:
        model_ok = [kicad.model_ok(m) for m in k_fp["models"]] if kicad is not None else None
        diffs += compare_footprints(k_fp, a_fp, kicad is not None, model_ok)
        res["checked"].append(f"footprint ({len(a_fp['pads'])} pads)")
        if second_reader and fp_f:
            ok = _second_reader(fp_f, diffs)
            if ok:
                res["checked"].append("pyaltiumlib")
    if k_sym and a_sym:
        diffs += compare_symbols(k_sym, a_sym)
        res["checked"].append(f"symbol ({len(a_sym['pins'])} pins)")
        kf = k_sym["props"].get("Footprint", "")
        want = f"{kicad.name}:{X.kname(a_fp['name'])}" if a_fp else ""
        if a_fp and kf != want:
            _d(diffs, "error", "symbol", "footprint link", kf, want)
        if a_sym.get("footprint") and a_fp and AL.lib_name(a_sym["footprint"]) != AL.lib_name(a_fp["name"]):
            _d(diffs, "error", "Altium symbol", "footprint link", a_fp["name"], a_sym["footprint"], "SchLib link vs PcbLib name")
        row = (altium_rows or {}).get(name) or plan_row(item)
        diffs += compare_fields(kicad_fields(k_sym["props"], rules), altium_fields(row, rules))
        res["checked"].append("fields (" + ("DbLib row" if (altium_rows or {}).get(name) else "plan parameters") + ")")
    if a_sym and (k_fp or a_fp):
        k_pins = k_sym["pins"] if k_sym else a_sym["pins"]
        diffs += compare_pin_pad(k_pins, {p["num"] for p in (k_fp or a_fp)["pads"]}, a_sym["pins"],
                                 {p["num"] for p in (a_fp or k_fp)["pads"]})
        res["checked"].append("pin -> pad")
    if not res["checked"]:
        res["result"], res["why"] = "skipped", "nothing to compare (" + ", ".join(
            w for w, v in (("no KiCad footprint", k_fp), ("no .PcbLib", a_fp), ("no .SchLib", a_sym)) if not v) + ")"
    elif any(d["level"] == "error" for d in diffs):
        res["result"] = "mismatch"
    return res


# ---------------------------------------------------------------- databases: .kicad_dbl + SQLite vs .DbLib
def kicad_db_rows(folder, lib):
    """{Part ID: row} from <lib>.sqlite (kicad-dblib), or None when there is none."""
    import sqlite3
    f = Path(folder) / f"{lib}.sqlite"
    if not f.is_file() or not (Path(folder) / f"{lib}.kicad_dbl").is_file():
        return None
    out = {}
    con = sqlite3.connect(f.resolve().as_uri() + "?mode=ro", uri=True)    # quoted: a '#', '?' or '%' in the path
    try:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            cur = con.execute(f'SELECT * FROM "{t}"')
            cols = [c[0] for c in cur.description]
            for r in cur:
                row = dict(zip(cols, ("" if v is None else str(v) for v in r)))
                out[row.get("Part ID", "")] = dict(row, _table=t)
    finally:
        con.close()
    return out


def altium_db_rows(folder, notes=None):
    """{Part Number: row} from the DbLib's parts table (the .csv mirror dblib-sync writes next to the workbook the DbLib
    names, else that .xlsx), or None when the folder has no .DbLib / .SVNDbLib. notes: gets a line when the folder has several."""
    import csv
    from . import sheetsafe as SS
    folder = Path(folder)
    libs = sorted(folder.glob("*.DbLib")) + sorted(folder.glob("*.SVNDbLib"))
    if not libs:
        return None
    from .altium_dblib_sync import dblib_table_paths
    found = [(lib, *dblib_table_paths(lib)) for lib in libs]
    lib, xlsx, csvf = next((f for f in found if f[1].is_file() or f[2].is_file()), found[0])
    if len(libs) > 1 and notes is not None:
        notes.append(f"{len(libs)} database libraries in {folder}; compared {lib.name}")
    out = {}
    if csvf.is_file():                        # undo the mirror's anti-formula apostrophe ("'+/-1%" is "+/-1%")
        with open(csvf, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                r = {k: SS.unquote(v) for k, v in r.items()}
                out[r.get("Part Number", "")] = r
    elif xlsx.is_file():
        from .altium_dblib_sync import read_tables
        for t, (_, rows) in read_tables(xlsx).items():
            for r in rows:
                out[r.get("Part Number", "")] = dict(r, Table=t)
    return out


def compare_databases(kicad_rows, altium_rows, kicad, altium_dir, rules=None):
    """The KiCad database library and the Altium DbLib must list the same parts, each naming a symbol and footprint
    that exist and correspond, with the same key fields."""
    from . import kicad_export as X
    out = []
    for n in sorted(set(kicad_rows) - set(altium_rows)):
        _d(out, "error", f"part {n}", "database row", "present", "missing")
    for n in sorted(set(altium_rows) - set(kicad_rows)):
        _d(out, "error", f"part {n}", "database row", "missing", "present")
    for n in sorted(set(kicad_rows) & set(altium_rows)):
        k, a = kicad_rows[n], altium_rows[n]
        what = f"part {n}"
        sym, fp = k.get("Symbol", ""), k.get("Footprint", "")
        if sym.split(":", 1)[-1] not in kicad.symbols:
            _d(out, "error", what, "KiCad Symbol", sym, a.get("Library Ref", ""), f"not in {kicad.name}.kicad_sym")
        if fp and kicad.footprint(fp.split(":", 1)[-1]) is None:
            _d(out, "error", what, "KiCad Footprint", fp, a.get("Footprint Ref", ""), f"not in {kicad.name}.pretty")
        if fp.split(":", 1)[-1] != X.kname(a.get("Footprint Ref", "")):
            _d(out, "error", what, "footprint", fp, a.get("Footprint Ref", ""))
        for col, ref in (("Library Path", "Library Ref"), ("Footprint Path", "Footprint Ref")):
            f = Path(altium_dir) / str(a.get(col, "")).replace("\\", "/")
            if a.get(col) and not f.is_file():
                _d(out, "error", what, col, "-", a.get(col), "file missing")
            elif a.get(col):
                names = {s["name"] for s in AL.read_schlib(f)[1]} if f.suffix.lower() == ".schlib" else {p["name"] for p in AL.read_pcblib(f)}
                try:
                    stored = AL.lib_name(a.get(ref, ""))
                except ValueError as e:                     # typed into the workbook: a part's error, not the check's
                    _d(out, "error", what, ref, "-", a.get(ref), str(e))
                    continue
                if stored not in names:
                    _d(out, "error", what, ref, "-", a.get(ref), f"not in {f.name}")
        kf = {f: k.get(c, "") for f, c in zip(KEY_FIELDS, ("Value", "Description", "Manufacturer", "MPN", "Datasheet"))}
        kf.update({f: v for f, v in kicad_fields(k, rules).items() if v})
        out += compare_fields(kf, altium_fields(a, rules), what)
    return out


# ---------------------------------------------------------------- plans
def check_plan(plan, kicad_dir=None, lib=None, altium_dir=None, rules=None, second_reader=None, include_held=False, log=None):
    """Every part of a plan -> report dict (`parts`, `database`, `counts`). kicad_dir: a kicad-export folder (export mode).
    altium_dir: a dblib-sync folder (its DbLib rows are the Altium field values, and the two databases are compared)."""
    from .rules import resolve
    plan = Path(plan)
    base = plan.parent
    rules = resolve(rules, base)
    items = json.loads(plan.read_text(encoding="utf-8"))
    kicad = KiCadLib(kicad_dir, lib) if kicad_dir else None
    notes = []
    a_rows = altium_db_rows(altium_dir, notes) if altium_dir else None
    if altium_dir and a_rows is None:         # a mistyped folder must not pass without comparing anything
        raise ValueError(f"--altium {altium_dir}: no .DbLib / .SVNDbLib there (the folder dblib-sync wrote)")
    if second_reader is None:
        second_reader = True
    rep = {"plan": str(plan), "mode": "export" if kicad else "plan", "kicad": str(kicad_dir or ""), "altium": str(altium_dir or ""),
           "tolerances": TOL, "parts": [], "database": []}
    for it in sorted(items, key=lambda i: str(i.get("name", ""))):
        if kicad and it.get("status") == "held" and not include_held:
            rep["parts"].append({"part": it["name"], "result": "skipped", "why": "held (kicad-export skips it)", "diffs": [], "checked": []})
            continue
        try:
            r = check_item(it, base, rules, kicad, a_rows, second_reader)
        except Exception as e:                # one unreadable part must not stop the run
            r = {"part": it["name"], "result": "mismatch", "checked": [], "diffs": []}
            _d(r["diffs"], "error", "files", "read", "-", f"{type(e).__name__}: {e}")
        rep["parts"].append(r)
        if log:
            log(f"  {r['result']:<9} {r['part']:<28} " + ("; ".join(d["msg"] for d in r["diffs"] if d["level"] == "error")[:140]
                                                          or r.get("why") or ", ".join(r["checked"])))
    if kicad and a_rows is not None:
        k_rows = kicad_db_rows(kicad_dir, kicad.name)
        if k_rows is None:
            _d(rep["database"], "warn", "database", "KiCad", "no .kicad_dbl / .sqlite", f"{len(a_rows)} DbLib rows", "run ecadlib kicad-dblib")
        else:
            rep["database"] = compare_databases(k_rows, a_rows, kicad, altium_dir, rules)
            rep["database_rows"] = {"kicad": len(k_rows), "altium": len(a_rows)}
    rep["database"] += [{"level": "warn", "what": "database", "field": "Altium", "kicad": "", "altium": n, "msg": n}
                        for n in notes]
    rep["counts"] = {s: sum(p["result"] == s for p in rep["parts"]) for s in ("match", "mismatch", "skipped")}
    rep["counts"]["database_errors"] = sum(d["level"] == "error" for d in rep["database"])
    rep["ok"] = not rep["counts"]["mismatch"] and not rep["counts"]["database_errors"]
    return rep


def write_report(rep, stem):
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    report_file(stem, ".json").write_text(json.dumps(rep, indent=2, default=str) + "\n", encoding="utf-8")
    from . import messages as M
    c = rep["counts"]
    where = Path(str(rep.get("plan") or "")).parent.name
    checked = lambda p: ", ".join(p["checked"]).replace("pyaltiumlib", "a second Altium reader").replace("pin -> pad", "pins to pads")
    md = [f"# KiCad and Altium match{': ' + where if where else ''}", "",
          f"**{c['match']} {'part matches' if c['match'] == 1 else 'parts match'}, {c['mismatch']} differ{'s' if c['mismatch'] == 1 else ''}.**"
          + (f" {c['skipped']} not compared." if c["skipped"] else "")
          + (f" Database rows: {M.plural(c['database_errors'], 'difference')}." if rep.get("database_rows") else ""), ""]
    md += ["Parts whose KiCad and Altium files differ are held until they match.",
           "**Next:** fill the library again so both are written from the same source; report it if they still differ.", ""] \
        if c["mismatch"] else ["The KiCad and Altium files of every compared part read back the same.", ""]
    bad = [p for p in rep["parts"] if p["result"] == "mismatch"]
    if bad:
        md += [f"## Differ ({len(bad)})", ""]
        for p in bad:
            md += [f"### {p['part']}", ""]
            md += [f"- {e['text']}" for e in M.explain_all("parity: " + d["msg"] for d in p["diffs"] if d["level"] == "error")]
            md.append("")
    good = [p for p in rep["parts"] if p["result"] == "match"]
    if good:
        md += [f"## Match ({len(good)})", "", "| Part | Compared |", "|---|---|"]
        cell = lambda v: str(v).replace("|", "\\|").replace("\n", " ")      # a '|' in a part name is not a column
        md += [f"| {cell(p['part'])} | {cell(checked(p) or '-')} |" for p in good] + [""]
    skip = [p for p in rep["parts"] if p["result"] not in ("match", "mismatch")]
    if skip:
        md += [f"## Not compared ({len(skip)})", ""]
        md += [f"- **{p['part']}**: {M.clean(p.get('why') or '-').replace('kicad-export skips it', 'not exported to KiCad')}" for p in skip] + [""]
    warns = [(p["part"], d["msg"]) for p in rep["parts"] for d in p["diffs"] if d["level"] == "warn"]
    if warns:
        md += ["## Approximations (not held)", ""] + [f"- **{n}**: {M.clean(m)}" for n, m in warns] + [""]
    if rep["database"]:
        md += ["## Database rows", ""] + [f"- {M.clean(d['msg'])}" for d in rep["database"]] + [""]
    md += ["## Details", "", f"Compared in {rep['mode']} mode. Every number is in the machine-readable report next to this file (.json)."]
    report_file(stem, ".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return stem


# ---------------------------------------------------------------- survey: everything the generators make
def survey(quick=False, log=None, work=None, kicad_dir=None):
    """Build every package spec (classic + advanced footprint_adv families) and every standard-table footprint as
    KiCad and Altium files, read both back and compare. Paths: footprint_gen -> altium_lib (approval / `altium_lib
    pcblib`), footprint_gen / stdparts -> kicad_import (the librarian), and .PcbLib -> kicad_export (the KiCad export of
    a part without a .kicad_mod). -> {"checked", "mismatched", "diffs": {name: [msg]}, "by_path": {...}}."""
    import tempfile
    from . import footprint_gen as F, kicad_export as X, kicad_import as K, stdparts as SP
    here = Path(__file__).with_name("package_specs")
    specs = [(p.stem, json.loads(p.read_text())) for p in sorted(here.glob("*.json")) + sorted((here / "advanced").glob("*.json"))]
    names = sorted(SP.packages())
    if quick:
        names = names[::25]
    rep = {"checked": 0, "mismatched": 0, "warned": 0, "diffs": {}, "by_path": {}}
    tmp = tempfile.TemporaryDirectory() if work is None else None
    work = Path(work or tmp.name)

    def one(label, path, text, fp_dict):
        f = work / f"{len(rep['diffs'])}_{rep['checked']}.PcbLib"
        AL.write_pcblib(f, [fp_dict])
        afp = AL.read_pcblib(f)[0]
        diffs = footprint_pair(text, afp)
        if path == "kicad-lib":              # library .kicad_mod files name models the converter never sees: no 3D check
            diffs = [d for d in diffs if d["what"] != "3D model"]
        back = X.footprint_from_pcblib(f, afp["name"], [])            # PcbLib -> KiCad export (no .kicad_mod)
        diffs += [dict(d, msg="export: " + d["msg"]) for d in compare_footprints(kicad_footprint(back), altium_footprint(afp))
                  if d["what"] != "3D model"]
        _second_reader(f, diffs)
        st = rep["by_path"].setdefault(path, {"checked": 0, "mismatched": 0})
        st["checked"] += 1
        rep["checked"] += 1
        errs = [d["msg"] for d in diffs if d["level"] == "error"]
        if errs:
            st["mismatched"] += 1
            rep["mismatched"] += 1
            rep["diffs"][f"{path}:{label}"] = errs
            if log:
                log(f"  MISMATCH {path}:{label}: {'; '.join(errs)[:200]}")
        elif diffs:
            rep["warned"] += 1
        f.unlink()

    try:
        for f in sorted(Path(kicad_dir).rglob("*.kicad_mod")) if kicad_dir else []:
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                fp, _ = K.convert_footprint(text, f.stem, "kicad")
            except Exception as e:            # the librarian reports these as unconvertible; not a parity question
                rep.setdefault("unconverted", {})[f.stem] = f"{type(e).__name__}: {e}"
                continue
            one(f"{f.parent.name}/{f.stem}", "kicad-lib", text, fp)
        if kicad_dir:
            return rep
        for stem, spec in specs:
            text, _ = F.build(spec)
            fp, _ = AL.footprint_from_spec(spec)
            one(stem, "generator", text, fp)
            fp2, _ = K.convert_footprint(text, spec["name"], "spec")
            one(stem, "librarian", text, fp2)
        for n in names:
            text, _ = SP.build(n)
            fp, _ = K.convert_footprint(text, n.split(":")[-1], "standard")
            one(n, "standard", text, fp)
    finally:
        if tmp:
            tmp.cleanup()
    return rep


# ---------------------------------------------------------------- CLI
def _print(rep, out):
    c = rep["counts"]
    for p in rep["parts"]:
        print(f"  {p['result']:<9} {p['part']:<28} {', '.join(p['checked']) or p.get('why', '')}", file=out)
        for d in p["diffs"]:
            print(f"      {'MISMATCH' if d['level'] == 'error' else 'approx'}  {d['msg']}", file=out)
    for d in rep["database"]:
        print(f"  database  {d['msg']}", file=out)
    print(f"{c['match']} match, {c['mismatch']} mismatch, {c['skipped']} skipped"
          + (f"; database rows {rep['database_rows']['kicad']} KiCad / {rep['database_rows']['altium']} Altium, "
             f"{c['database_errors']} differences" if rep.get("database_rows") else ""), file=out)


def print_result(rep, stem, plan, t=None):
    """Terminal view: a row per part (KiCad and Altium match / differ, with the first difference in plain words;
    -v: what was compared and every difference), database rows, then the summary."""
    from . import messages as M, term as TM
    t = t or TM.Term()
    rows = []
    for p in rep["parts"]:
        errs = [d for d in p["diffs"] if d["level"] == "error"]
        st = {"match": "ready", "mismatch": "held"}.get(p["result"], "skipped")
        word = {"match": "match", "mismatch": "differ"}.get(p["result"], p["result"])
        first = M.explain(errs[0]["msg"])["text"] + (f" (+{len(errs) - 1} more)" if len(errs) > 1 else "") if errs else ""
        rows.append([t.status(st, word), p["part"], first or (t.dim(", ".join(p["checked"])) if t.verbose else p.get("why", ""))])
        if t.verbose:
            rows += [["", "", ("" if d["level"] == "error" else "approx: ") + M.explain(d["msg"])["text"]] for d in p["diffs"][1 if errs else 0:]]
    t.table(rows)
    for d in rep["database"]:
        t.write(f"  {t.status('held', 'database')}  {M.clean(d['msg'])}")
    c = rep["counts"]
    head = (f"KiCad and Altium: {c['match']} match, {c['mismatch']} differ" + (f", {c['skipped']} skipped" if c["skipped"] else "")
            + (f"; database rows {rep['database_rows']['kicad']} KiCad / {rep['database_rows']['altium']} Altium, "
               f"{c['database_errors']} differences" if rep.get("database_rows") else ""))
    nxt = [(f"ecadlib parity {plan} -v", "every difference, part by part")] if c["mismatch"] else []
    t.summary(head, written=[(str(report_file(stem, ".md")), "parity report")], next=nxt)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["survey"]:
        ap = argparse.ArgumentParser(prog="ecadlib parity survey", description="Compare KiCad and Altium output of every generator "
                                     "package spec and standard-table footprint")
        ap.add_argument("survey")
        ap.add_argument("--quick", action="store_true", help="every 25th standard-table footprint only")
        ap.add_argument("--kicad-footprints", metavar="DIR", help="instead: every .kicad_mod under DIR (a kicad-footprints "
                                                                 "checkout) as the librarian converts it")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(argv)
        rep = survey(a.quick, None if a.json else print, kicad_dir=a.kicad_footprints)
        if a.json:
            print(json.dumps(rep, indent=2))
        else:
            for p, st in rep["by_path"].items():
                print(f"  {p:<10} {st['checked']:>4} footprints, {st['mismatched']} mismatched")
            print(f"{rep['checked']} footprints compared, {rep['mismatched']} mismatched, {rep['warned']} with approximations only")
        return 1 if rep["mismatched"] else 0
    if argv[:1] == ["pair"]:
        ap = argparse.ArgumentParser(prog="ecadlib parity pair", description="Compare one KiCad file with one Altium file")
        ap.add_argument("pair")
        ap.add_argument("kicad", help="X.kicad_mod, or Lib.kicad_sym:Name")
        ap.add_argument("altium", help="X.PcbLib or X.SchLib")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(argv)
        if a.altium.lower().endswith(".pcblib"):
            diffs = footprint_pair(Path(a.kicad).read_text(encoding="utf-8"), AL.read_pcblib(a.altium)[0])
            _second_reader(a.altium, diffs)
        else:
            f, _, nm = a.kicad.rpartition(":") if a.kicad.count(":") and not Path(a.kicad).is_file() else (a.kicad, "", "")
            syms = {str(s[1]): s for s in sexpr.find(sexpr.parse(Path(f).read_text(encoding="utf-8")), "symbol")}
            sym = syms.get(nm) if nm else next(iter(syms.values()), None)
            if sym is None:
                sys.exit(f"{a.kicad}: symbol not found")
            diffs = compare_symbols(kicad_symbol(sym), altium_symbol(AL.read_schlib(a.altium)[1][0]))
        if a.json:
            print(json.dumps(diffs, indent=2, default=str))
        else:
            for d in diffs:
                print(f"  {'MISMATCH' if d['level'] == 'error' else 'approx'}  {d['msg']}")
            print("match" if not any(d["level"] == "error" for d in diffs) else "mismatch")
        return 1 if any(d["level"] == "error" for d in diffs) else 0
    ap = argparse.ArgumentParser(prog="ecadlib parity", description="Read back the KiCad and the Altium library of a plan and "
                                 "compare them part by part (pads, drills, courtyard, 3D, pins, fields, database rows)",
                                 epilog="also: ecadlib parity pair X.kicad_mod X.PcbLib | ecadlib parity survey [--quick]")
    ap.add_argument("plan", help="librarian / altium365 plan.json")
    ap.add_argument("--kicad", help="kicad-export folder (default: compare the plan's own .kicad_mod files)")
    ap.add_argument("--name", help="KiCad library name in that folder (default: its one .kicad_sym)")
    ap.add_argument("--altium", help="dblib-sync folder: its DbLib rows give the Altium field values; with --kicad the "
                                     ".kicad_dbl rows are compared with them")
    ap.add_argument("--include-held", action="store_true", help="export mode: also parts the release check held")
    ap.add_argument("--no-pyaltiumlib", action="store_true", help="skip the independent pyaltiumlib re-read")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan)")
    ap.add_argument("-o", "--out", help="report stem (.md and .json); default <plan dir>/parity")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    if not Path(a.plan).is_file():
        print(f"ecadlib parity: {a.plan}: no such plan", file=sys.stderr)
        return 2
    try:
        rep = check_plan(a.plan, a.kicad, a.name, a.altium, a.rules, not a.no_pyaltiumlib, a.include_held)
    except (ValueError, OSError) as e:
        print(f"ecadlib parity: {e}", file=sys.stderr)
        return 2
    if a.out and Path(a.out).suffix.lower() in (".md", ".json"):    # -o out/p.md: the stem out/p, as before
        a.out = str(Path(a.out).with_suffix(""))
    stem = write_report(rep, a.out or Path(a.plan).parent / "parity")
    if a.json:
        print(json.dumps(dict(rep, report=str(report_file(stem, ".md"))), indent=2, default=str))
    else:
        print_result(rep, stem, a.plan)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
