"""KiCad footprints and symbols read into plain shapes, for the writers of other tools (eagle_export, easyeda_pro_export).

Both Eagle / Fusion Electronics and EasyEDA Pro draw with y UP, so everything here is millimetres, y up, angles
counter-clockwise in degrees (KiCad footprints are y down; KiCad symbols are already y up).

footprint(text) -> {"name", "descr", "pads": [...], "graphics": [...], "texts": {"reference": (x, y), "value": (x, y)},
                    "models": [path, ...], "notes": [...]}
  pad      {"num", "kind": smd | tht | npth | window, "side": F | B (smd / window), "x", "y", "rot", "w", "h",
            "shape": circle | rect | oval | roundrect | custom, "rratio" (roundrect: radius / shorter side),
            "drill": (dx, dy) in the pad frame or None, "offset": (ox, oy) copper centre - hole in the pad frame,
            "paste", "mask" (bool), "mask_margin", "paste_margin" (mm or None = the board's rule),
            "parts": custom pads' copper ([("poly", [(x, y), ...], outline width) | ("circle", (cx, cy), r)] in the footprint frame),
            "layers": a window's layers (paste_f, mask_f, ...), "approx": [what could not be kept exactly]}
  graphic  {"layer": role, "kind": line | arc | circle | poly, "w": stroke width, "fill": bool,
            line: "a", "b"; arc: "a" (start), "b" (end), "c" (centre), "sweep" (degrees, + = counter-clockwise);
            circle: "c", "r"; poly: "pts" (closed)}
  roles    cu_f cu_b silk_f silk_b fab_f fab_b crtyd_f crtyd_b mask_f mask_b paste_f paste_b edge doc

symbol(tree) -> {"name", "props": {...}, "units": {n: {"pins": [...], "shapes": [...]}}, "unit_count",
                 "pin_names_hidden", "pin_numbers_hidden", "name_offset", "notes"}
  unit 0 holds what KiCad draws in every unit; De Morgan alternates (body style 2) are left out (noted).
  pin      {"num", "name", "type" (KiCad electrical type), "shape" (KiCad graphic style), "x", "y" (connection point),
            "angle" (0 = the pin runs right from its connection point to the body), "length", "hidden"}
  shape    {"kind": rect | poly | circle | arc | text, "w", "fill": none | outline | background, rect: "a", "b";
            poly: "pts", "closed"; circle: "c", "r"; arc: "a", "b", "c", "sweep"; text: "at", "text", "size", "angle"}"""
import math, re

from . import sexpr

LAYER_ROLE = {"F.Cu": "cu_f", "B.Cu": "cu_b", "F.SilkS": "silk_f", "B.SilkS": "silk_b", "F.Silkscreen": "silk_f",
              "B.Silkscreen": "silk_b", "F.Fab": "fab_f", "B.Fab": "fab_b", "F.CrtYd": "crtyd_f", "B.CrtYd": "crtyd_b",
              "F.Courtyard": "crtyd_f", "B.Courtyard": "crtyd_b", "F.Mask": "mask_f", "B.Mask": "mask_b",
              "F.Paste": "paste_f", "B.Paste": "paste_b", "Edge.Cuts": "edge", "Dwgs.User": "doc", "Cmts.User": "doc",
              "User.Drawings": "doc", "User.Comments": "doc"}
DEFAULT_STROKE = 0.1524                       # KiCad draws a symbol stroke of 0 with the 6 mil default


def _f(v):
    return float(v)


def yup(x, y):
    """KiCad footprint point (y down) -> y up."""
    return (_f(x), -_f(y) + 0.0)


def rot(u, v, a):
    """(u, v) turned counter-clockwise by a degrees (y up)."""
    r = math.radians(a)
    c, s = math.cos(r), math.sin(r)
    return (u * c - v * s, u * s + v * c)


def arc3(s, m, e):
    """Arc through start, mid, end (y up) -> (centre, radius, sweep from start to end, + = counter-clockwise)."""
    (ax, ay), (bx, by), (cx, cy) = s, m, e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        raise ValueError("degenerate arc")
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    a0, am, a1 = (math.degrees(math.atan2(py - uy, px - ux)) for px, py in (s, m, e))
    ccw = (bx - ax) * (cy - by) - (by - ay) * (cx - bx) > 0
    sweep = (a1 - a0) % 360 if ccw else -((a0 - a1) % 360)
    if abs(sweep) < 1e-9:
        sweep = 360.0 if ccw else -360.0
    return (ux, uy), math.hypot(ax - ux, ay - uy), sweep


def arc_mid(a, c, sweep):
    """The middle point of an arc from a around c by sweep degrees."""
    return tuple(p + q for p, q in zip(c, rot(a[0] - c[0], a[1] - c[1], sweep / 2)))


def arc_points(a, c, sweep, step=10.0):
    """Points along an arc (a included, end included), for shapes a format cannot draw as an arc."""
    n = max(2, int(math.ceil(abs(sweep) / step)))
    return [tuple(p + q for p, q in zip(c, rot(a[0] - c[0], a[1] - c[1], sweep * k / n))) for k in range(n + 1)]


def _stroke(g):
    w = sexpr.first(sexpr.first(g, "stroke") or [], "width") or sexpr.first(g, "width")
    return _f(w[1]) if w else 0.0


def _filled(g):
    fl = sexpr.first(g, "fill")
    if not fl or len(fl) < 2:
        return g[0] in ("fp_poly", "gr_poly") and fl is None
    v = fl[1] if isinstance(fl[1], str) else (sexpr.first(fl, "type") or [None, "none"])[1]
    return v in ("yes", "solid", "outline", "background", "color")


def _xy(node):
    return (_f(node[1]), _f(node[2]))


def capsule(a, b, w, step=15.0):
    """A line of width w with round ends as one polygon (y up)."""
    (ax, ay), (bx, by), r = a, b, w / 2
    ang = math.degrees(math.atan2(by - ay, bx - ax))
    pts = [(bx + r * math.cos(math.radians(ang + t)), by + r * math.sin(math.radians(ang + t)))
           for t in [k * step - 90 for k in range(int(180 / step) + 1)]]
    pts += [(ax + r * math.cos(math.radians(ang + t)), ay + r * math.sin(math.radians(ang + t)))
            for t in [k * step + 90 for k in range(int(180 / step) + 1)]]
    return pts


def round_rect(cx, cy, w, h, r, a=0.0, step=3.0):
    """Outline of a w x h rectangle with corner radius r centred on (cx, cy), turned by a (y up)."""
    r = max(0.0, min(r, w / 2, h / 2))
    pts = []
    for (sx, sy), start in (((1, 1), 0), ((-1, 1), 90), ((-1, -1), 180), ((1, -1), 270)):
        ox, oy = sx * (w / 2 - r), sy * (h / 2 - r)
        if r <= 1e-9:
            pts.append((sx * w / 2, sy * h / 2))
            continue
        for k in range(int(90 / step) + 1):
            t = math.radians(start + k * step)
            pts.append((ox + r * math.cos(t), oy + r * math.sin(t)))
    return [(cx + p[0], cy + p[1]) for p in (rot(u, v, a) for u, v in pts)]


# ---------------------------------------------------------------- footprints
def _graphic(g, layer, notes, frame=None):
    """One fp_line / fp_rect / fp_circle / fp_arc / fp_poly (or a custom pad's gr_*) -> a graphic dict (y up).
    frame: (x, y, rot) of a pad, for primitives in the pad's frame."""
    def P(node):
        x, y = yup(*node[1:3])
        if frame:
            u, v = rot(x, y, frame[2])
            return (frame[0] + u, frame[1] + v)
        return (x, y)
    kind, w, fill = g[0][3:], _stroke(g), _filled(g)
    if kind == "line":
        return {"layer": layer, "kind": "line", "a": P(sexpr.first(g, "start")), "b": P(sexpr.first(g, "end")), "w": w, "fill": False}
    if kind == "rect":
        (x1, y1), (x2, y2) = _xy(sexpr.first(g, "start")), _xy(sexpr.first(g, "end"))
        pts = [P(["xy", x, y]) for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]
        return {"layer": layer, "kind": "poly", "pts": pts, "w": w, "fill": fill}
    if kind == "circle":
        c, e = P(sexpr.first(g, "center")), P(sexpr.first(g, "end"))
        return {"layer": layer, "kind": "circle", "c": c, "r": math.hypot(e[0] - c[0], e[1] - c[1]), "w": w, "fill": fill}
    if kind == "arc":
        if sexpr.first(g, "mid"):
            s, m, e = (P(sexpr.first(g, k)) for k in ("start", "mid", "end"))
            c, r, sweep = arc3(s, m, e)
        else:                                 # KiCad 5 / 6 arcs: (start centre) (end arc start) (angle, clockwise on screen)
            c, s = P(sexpr.first(g, "start")), P(sexpr.first(g, "end"))
            sweep = -_f(sexpr.first(g, "angle")[1])
            e = tuple(p + q for p, q in zip(c, rot(s[0] - c[0], s[1] - c[1], sweep)))
        return {"layer": layer, "kind": "arc", "a": s, "b": e, "c": c, "sweep": sweep, "w": w, "fill": False}
    if kind == "poly":
        pts = []
        for p in (sexpr.first(g, "pts") or [])[1:]:
            if p[0] == "xy":
                pts.append(P(p))
            elif p[0] == "arc":               # KiCad 7+: an arc inside a polygon outline
                s, m, e = (P(sexpr.first(p, k)) for k in ("start", "mid", "end"))
                c, _, sweep = arc3(s, m, e)
                pts += arc_points(s, c, sweep)
        return {"layer": layer, "kind": "poly", "pts": pts, "w": w, "fill": fill}
    if kind == "curve":                       # Bezier: drawn through its control points
        pts = [P(p) for p in (sexpr.first(g, "pts") or [])[1:] if p[0] == "xy"]
        notes.append("Bezier curve drawn as lines through its control points")
        return {"layer": layer, "kind": "poly", "pts": pts, "w": w, "fill": False, "open": True}
    return None


def _custom_parts(p, x, y, a, notes):
    """A custom pad's primitives -> copper parts in the footprint frame (y up)."""
    parts = []
    for g in (sexpr.first(p, "primitives") or [])[1:]:
        if not isinstance(g, list):
            continue
        d = _graphic(["fp_" + g[0][3:]] + g[1:], "cu", notes, (x, y, a))
        if not d:
            continue
        w = d["w"]
        if d["kind"] == "poly" and len(d["pts"]) >= 3:
            parts.append(("poly", d["pts"], w))           # w: the outline width, which grows the shape by w / 2
        elif d["kind"] == "circle":
            if d["fill"]:
                parts.append(("circle", d["c"], d["r"] + w / 2))
            else:                             # a ring: its outer edge
                parts.append(("circle", d["c"], d["r"] + w / 2))
                notes.append("custom pad ring drawn filled")
        elif d["kind"] == "line" and w > 0:
            parts.append(("poly", capsule(d["a"], d["b"], w), 0.0))
        elif d["kind"] == "arc" and w > 0:
            pts = arc_points(d["a"], d["c"], d["sweep"], 7.5)
            parts += [("poly", capsule(p1, p2, w), 0.0) for p1, p2 in zip(pts, pts[1:])]
    return parts


def footprint(text):
    t = sexpr.parse(text) if isinstance(text, str) else text
    notes = []
    out = {"name": str(t[1]), "descr": str((sexpr.first(t, "descr") or [None, ""])[1]), "pads": [], "graphics": [],
           "texts": {}, "models": [], "notes": notes, "attr": [str(a) for a in (sexpr.first(t, "attr") or [None])[1:]]}
    fp_mask = sexpr.first(t, "solder_mask_margin")
    fp_paste = sexpr.first(t, "solder_paste_margin")
    for p in sexpr.find(t, "pad"):
        num, kind, shape = str(p[1]), str(p[2]), str(p[3])
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y = yup(at[1], at[2])
        a = _f(at[3]) if len(at) > 3 else 0.0
        w, h = _f(size[1]), _f(size[2])
        layers = [str(l) for l in (sexpr.first(p, "layers") or ["layers"])[1:]]
        cu = [l for l in layers if l.endswith(".Cu")]
        pad = {"num": num, "x": x, "y": y, "rot": a % 360, "w": w, "h": h, "shape": shape, "rratio": 0.0, "drill": None,
               "offset": (0.0, 0.0), "approx": [], "parts": [], "layers": []}
        if kind in ("smd", "connect") and not cu:
            pad["kind"] = "window"
            for l in layers:
                for side in ("F", "B"):
                    for what in ("Paste", "Mask"):
                        if l in (f"{side}.{what}", f"*.{what}", f"F&B.{what}"):
                            pad["layers"].append(f"{what.lower()}_{side.lower()}")
            pad["layers"] = sorted(set(pad["layers"]))
        else:
            pad["kind"] = {"thru_hole": "tht", "np_thru_hole": "npth"}.get(kind, "smd")
        pad["side"] = "B" if cu and all(l.startswith("B.") for l in cu) else "F"
        pad["copper"] = bool(cu)
        if shape == "roundrect":
            rr = sexpr.first(p, "roundrect_rratio")
            pad["rratio"] = min(0.5, _f(rr[1])) if rr else 0.0
            if pad["rratio"] < 0.0025:
                pad["shape"] = "rect"
        if shape == "circle":
            pad["h"] = pad["w"]
        cr = sexpr.first(p, "chamfer_ratio")
        if sexpr.first(p, "chamfer") and cr and _f(cr[1]) > 0:
            pad["approx"].append("chamfered corners written square")
        if shape == "trapezoid":
            pad["shape"] = "rect"
            pad["approx"].append("trapezoid written as a rectangle")
        if shape == "custom":
            pad["anchor"] = str((sexpr.first(sexpr.first(p, "options") or [], "anchor") or [None, "circle"])[1])
            pad["parts"] = _custom_parts(p, x, y, a, pad["approx"])
        d = sexpr.first(p, "drill")
        if d:
            vals = [_f(v) for v in d[1:] if isinstance(v, str) and v != "oval"]
            if vals:
                pad["drill"] = (vals[0], vals[1] if len(vals) > 1 else vals[0])
            off = sexpr.first(d, "offset")
            if off:
                pad["offset"] = (_f(off[1]), -_f(off[2]) + 0.0)
        if pad["kind"] == "smd" and pad["offset"] != (0.0, 0.0):     # no hole: the offset only moves the copper
            dx, dy = rot(*pad["offset"], a)
            pad["x"], pad["y"], pad["offset"] = x + dx, y + dy, (0.0, 0.0)
        pad["paste"] = any("Paste" in l for l in layers) and pad["kind"] in ("smd", "window")
        pad["mask"] = any("Mask" in l for l in layers)
        mm_ = sexpr.first(p, "solder_mask_margin") or fp_mask
        pm = sexpr.first(p, "solder_paste_margin") or fp_paste
        pad["mask_margin"] = _f(mm_[1]) if mm_ else None
        pad["paste_margin"] = _f(pm[1]) if pm and pad["paste"] else None
        if sexpr.first(p, "solder_paste_margin_ratio") and _f(sexpr.first(p, "solder_paste_margin_ratio")[1]):
            pad["approx"].append("paste margin ratio not kept")
        out["pads"].append(pad)
    for kind in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve"):
        for g in sexpr.find(t, kind):
            lay = str((sexpr.first(g, "layer") or [None, ""])[1])
            role = LAYER_ROLE.get(lay)
            if not role:
                if lay:
                    notes.append(f"graphics on {lay} not written")
                continue
            try:
                d = _graphic(g, role, notes)
            except ValueError as e:
                notes.append(f"{kind} on {lay} skipped: {e}")
                continue
            if d:
                out["graphics"].append(d)
    for f in list(sexpr.find(t, "fp_text")) + list(sexpr.find(t, "property")):
        key = str(f[1]).lower()
        at = sexpr.first(f, "at")
        if key in ("reference", "value") and at:
            out["texts"].setdefault(key, yup(at[1], at[2]))
    if sexpr.find(t, "zone") and next(sexpr.find(t, "zone"), None):
        notes.append("keep-out / copper zones not written")
    out["models"] = [str(m[1]) for m in sexpr.find(t, "model")]
    return out


# ---------------------------------------------------------------- symbols
def _hidden(node):
    return "hide" in node[1:] or bool(sexpr.first(node, "hide") and (len(sexpr.first(node, "hide")) < 2 or sexpr.first(node, "hide")[1] == "yes"))


def _sym_fill(g):
    fl = sexpr.first(g, "fill")
    return str((sexpr.first(fl, "type") or [None, "none"])[1]) if fl else "none"


def _sym_shape(g, notes):
    w = _stroke(g) or DEFAULT_STROKE
    fill = _sym_fill(g)
    if g[0] == "rectangle":
        return {"kind": "rect", "a": _xy(sexpr.first(g, "start")), "b": _xy(sexpr.first(g, "end")), "w": w, "fill": fill}
    if g[0] == "polyline":
        pts = [_xy(p) for p in (sexpr.first(g, "pts") or [])[1:] if p[0] == "xy"]
        return {"kind": "poly", "pts": pts, "closed": len(pts) > 2 and pts[0] == pts[-1], "w": w, "fill": fill}
    if g[0] == "bezier":
        pts = [_xy(p) for p in (sexpr.first(g, "pts") or [])[1:] if p[0] == "xy"]
        notes.append("symbol Bezier drawn as lines through its control points")
        return {"kind": "poly", "pts": pts, "closed": False, "w": w, "fill": "none"}
    if g[0] == "circle":
        return {"kind": "circle", "c": _xy(sexpr.first(g, "center")), "r": _f(sexpr.first(g, "radius")[1]), "w": w, "fill": fill}
    if g[0] == "arc":
        if sexpr.first(g, "mid"):
            s, m, e = (_xy(sexpr.first(g, k)) for k in ("start", "mid", "end"))
            c, r, sweep = arc3(s, m, e)
        else:                                 # KiCad 6: (radius (at) (length) (angles a0 a1)), counter-clockwise
            rad = sexpr.first(g, "radius")
            c, r = _xy(sexpr.first(rad, "at")), _f(sexpr.first(rad, "length")[1])
            a0, a1 = (_f(v) for v in sexpr.first(rad, "angles")[1:3])
            s = (c[0] + r * math.cos(math.radians(a0)), c[1] + r * math.sin(math.radians(a0)))
            e = (c[0] + r * math.cos(math.radians(a1)), c[1] + r * math.sin(math.radians(a1)))
            sweep = (a1 - a0) % 360 or 360.0
            if sweep > 180:                   # KiCad 6 arcs are the short way round
                sweep -= 360
        return {"kind": "arc", "a": s, "b": e, "c": c, "sweep": sweep, "w": w, "fill": fill}
    if g[0] == "text":
        at = sexpr.first(g, "at")
        size = sexpr.first(sexpr.first(sexpr.first(g, "effects") or [], "font") or [], "size")
        return {"kind": "text", "at": (_f(at[1]), _f(at[2])), "angle": _f(at[3]) / (10 if _f(at[3]) >= 360 else 1) if len(at) > 3 else 0.0,
                "text": str(g[1]), "size": _f(size[1]) if size else 1.27}
    return None


def symbol(sym):
    """A KiCad symbol tree (one `symbol` node, `extends` already flattened, as kicad_export writes it)."""
    notes = []
    props = {str(p[1]): str(p[2]) for p in sexpr.find(sym, "property")}
    pos = {}
    for p in sexpr.find(sym, "property"):
        at = sexpr.first(p, "at")
        if at:
            pos[str(p[1])] = (_f(at[1]), _f(at[2]), _f(at[3]) if len(at) > 3 else 0.0, _hidden(sexpr.first(p, "effects") or []) or _hidden(p))
    pn, pnum = sexpr.first(sym, "pin_names"), sexpr.first(sym, "pin_numbers")
    off = sexpr.first(pn or [], "offset")
    out = {"name": str(sym[1]), "props": props, "prop_at": pos, "units": {}, "notes": notes,
           "pin_names_hidden": bool(pn and _hidden(pn)), "pin_numbers_hidden": bool(pnum and _hidden(pnum)),
           "name_offset": _f(off[1]) if off else 0.508, "power": bool(sexpr.first(sym, "power"))}
    alt = False
    for u in sexpr.find(sym, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", str(u[1]))
        unit, style = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        if style > 1:
            alt = True
            continue
        d = out["units"].setdefault(unit, {"pins": [], "shapes": []})
        for g in u[1:]:
            if not isinstance(g, list) or not g:
                continue
            if g[0] == "pin":
                at = sexpr.first(g, "at")
                ln = sexpr.first(g, "length")
                nm, nu = sexpr.first(g, "name") or ["name", ""], sexpr.first(g, "number") or ["number", ""]
                d["pins"].append({"num": str(nu[1]), "name": str(nm[1]), "type": str(g[1]), "shape": str(g[2]),
                                  "x": _f(at[1]), "y": _f(at[2]), "angle": (_f(at[3]) if len(at) > 3 else 0.0) % 360,
                                  "length": _f(ln[1]) if ln else 2.54, "hidden": _hidden(g)})
            else:
                try:
                    s = _sym_shape(g, notes)
                except (ValueError, TypeError, IndexError) as e:
                    notes.append(f"symbol {g[0]} skipped: {e}")
                    continue
                if s:
                    d["shapes"].append(s)
    if alt:
        notes.append("De Morgan alternate body style not written (the normal style is)")
    out["unit_count"] = max([k for k in out["units"] if k > 0] or [1])
    return out


def pin_text(name, overbar):
    """KiCad pin name with ~{...} overbars -> the target's overbar spelling (overbar(text) wraps one span)."""
    if name in ("~", ""):
        return ""
    if "~{" not in name:                      # KiCad 6 '~RESET~' / '~RESET'
        name = re.sub(r"~([^~]*)~?", lambda m: "~{" + m.group(1) + "}" if m.group(1) else "", name)
    return re.sub(r"~\{([^}]*)\}", lambda m: overbar(m.group(1)), name)


# ---------------------------------------------------------------- layer coverage
def grown(pts, d):
    """A polygon grown by d on every side, as a filled shape with an outline of width 2d is (round corners):
    [(point, sweep)] where sweep is the arc from this point to the next (+ = counter-clockwise) or None for a line."""
    if d <= 1e-12:
        return [(p, None) for p in pts]
    q = [p for i, p in enumerate(pts) if math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) > 1e-9]
    if sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(q, q[1:] + q[:1])) < 0:
        q = q[::-1]                           # counter-clockwise: the outside is on the right of each edge
    n = len(q)
    normal = []
    for i in range(n):
        (x1, y1), (x2, y2) = q[i], q[(i + 1) % n]
        L = math.hypot(x2 - x1, y2 - y1)
        normal.append(((y2 - y1) / L, -(x2 - x1) / L))
    out = []
    for i in range(n):
        a, b = normal[i - 1], normal[i]
        p = q[i]
        turn = math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]))
        if turn > 1e-6:                       # convex corner: an arc round the vertex
            out.append(((p[0] + a[0] * d, p[1] + a[1] * d), turn))
            out.append(((p[0] + b[0] * d, p[1] + b[1] * d), None))
        else:                                 # straight on or concave: where the two offset edges meet
            k = d / max(1e-9, math.cos(math.radians(turn) / 2))
            bx, by = a[0] + b[0], a[1] + b[1]
            bl = math.hypot(bx, by) or 1.0
            out.append(((p[0] + bx / bl * k, p[1] + by / bl * k), None))
    return out


def grown_points(pts, d, step=5.0):
    seg = grown(pts, d)
    out = []
    for i, (p, sweep) in enumerate(seg):
        if sweep:
            nxt = seg[(i + 1) % len(seg)][0]
            c = _arc_c(p, nxt, sweep)
            out += arc_points(p, c, sweep, step)[:-1]
        else:
            out.append(p)
    return out


def _arc_c(a, b, sweep):
    dx, dy = b[0] - a[0], b[1] - a[1]
    dd = math.hypot(dx, dy)
    h = dd / 2 / math.tan(math.radians(sweep) / 2)
    return (a[0] + b[0]) / 2 - dy / dd * h, (a[1] + b[1]) / 2 + dx / dd * h


def disc(c, r, step=3.0):
    k = max(8, int(round(360 / step)))
    return [(c[0] + r * math.cos(2 * math.pi * i / k), c[1] + r * math.sin(2 * math.pi * i / k)) for i in range(k)]


def pad_polys(p, grow=0.0):
    """A cad_model pad's copper (custom: anchor and parts) grown by `grow`, as polygons (y up)."""
    x, y, w, h, a = p["x"], p["y"], p["w"], p["h"], p["rot"]
    if p["kind"] == "tht" and p.get("offset") != (0.0, 0.0):
        dx, dy = rot(*p["offset"], a)
        x, y = x + dx, y + dy
    if p["shape"] == "custom":
        out = [disc((x, y), w / 2 + grow)] if p.get("anchor", "circle") == "circle" else [round_rect(x, y, w + 2 * grow, h + 2 * grow, grow, a)]
        for part in p.get("parts", []):
            if part[0] == "circle":
                out.append(disc(part[1], part[2] + grow))
            else:
                d = (part[2] / 2 if len(part) > 2 else 0.0) + grow
                out.append(grown_points(part[1], d) if d > 0 else part[1])
        return out
    r = {"circle": min(w, h) / 2, "oval": min(w, h) / 2, "roundrect": p.get("rratio", 0.0) * min(w, h)}.get(p["shape"], 0.0)
    if p["kind"] == "npth" and p.get("drill"):
        w = h = max(w, h)
        r = w / 2
    return [round_rect(x, y, w + 2 * grow, h + 2 * grow, r + grow if r or grow else 0.0, a)]


def openings(fp, frame=0.0):
    """Mask and paste openings of a cad_model footprint per layer {"mask_F": [polys], ...}: every pad that opens the
    layer (grown by its margin; mask openings with the board rule grown by `frame`) and every window. A pad-for-pad
    independent picture of the layers, for checking a writer that splits openings between pads differently."""
    out = {k: [] for k in ("mask_F", "mask_B", "paste_F", "paste_B")}
    for p in fp["pads"]:
        if p["kind"] == "window":
            for lay in p["layers"]:
                kind, side = lay.split("_")
                out[f"{kind}_{side.upper()}"] += pad_polys(p)
            continue
        sides = ("F", "B") if p["kind"] in ("tht", "npth") else (p["side"],)
        if p.get("mask"):
            m = p.get("mask_margin")
            for s in sides:
                out[f"mask_{s}"] += pad_polys(p, frame if m is None else m)
        if p.get("paste") and p["kind"] == "smd":
            out[f"paste_{p['side']}"] += pad_polys(p, p.get("paste_margin") or 0.0)
    for g in fp["graphics"]:                  # filled drawings on mask / paste open them too
        lay = {"mask_f": "mask_F", "mask_b": "mask_B", "paste_f": "paste_F", "paste_b": "paste_B"}.get(g["layer"])
        if lay and g.get("fill"):
            if g["kind"] == "poly":
                out[lay].append(grown_points(g["pts"], g["w"] / 2) if g["w"] > 0 else g["pts"])
            elif g["kind"] == "circle":
                out[lay].append(disc(g["c"], g["r"] + g["w"] / 2))
    return out


def raster(polys, res=0.02):
    """Cells (i, j) of a res grid covered by the union of the polygons (even-odd per polygon), as a set."""
    cells = set()
    for pts in polys:
        if len(pts) < 3:
            continue
        ys = [p[1] for p in pts]
        j0, j1 = math.floor(min(ys) / res), math.ceil(max(ys) / res)
        n = len(pts)
        for j in range(j0, j1 + 1):
            yc = (j + 0.5) * res
            xs = []
            for k in range(n):
                (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % n]
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            for a, b in zip(xs[::2], xs[1::2]):
                for i in range(math.ceil(a / res - 0.5), math.floor(b / res - 0.5) + 1):
                    cells.add((i, j))
    return cells


def same_cover(a, b, res=0.02, rel=0.03, floor=0.02):
    """Whether two polygon sets cover the same area: the area in one and not the other is under rel of the
    larger area (and over `floor` mm2). -> (same, differing mm2, total mm2)."""
    # cells are only kept where a polygon is, so the grid is capped by the polygons' own area (~360000 cells), not
    # by their spread: two small holes far apart keep a fine grid
    area = sum((max(p[0] for p in q) - min(p[0] for p in q)) * (max(p[1] for p in q) - min(p[1] for p in q))
               for q in list(a) + list(b) if q)
    res = max(res, math.sqrt(area / 360000))
    ca, cb = raster(a, res), raster(b, res)
    diff = len(ca ^ cb) * res * res
    total = max(len(ca), len(cb)) * res * res
    return diff <= max(floor, rel * total), round(diff, 4), round(total, 4)
