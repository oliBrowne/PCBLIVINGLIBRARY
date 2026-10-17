"""Tidy a converted footprint (EasyEDA today) to the rule book, the way KiCad's own footprints are drawn.

    tidy(kicad_mod_text, notes) -> kicad_mod_text

EasyEDA's library footprints are drawn for its own editor: the reference sits on the fab layer at the centre (so
Altium's .Designator lands over the part body), SMD pads are sharp rectangles, the fab outline has no pin-1 corner,
and pin-1 dots are often drawn on silk under the part, where nobody sees them once it is placed. Each fix below
answers one rule of docs/footprint-rulebook.md and is noted, and nothing touches the copper position or size:
  text      the reference goes on F.SilkS (1.0 mm, 0.15 mm stroke) just above the courtyard, the value below it
            (F.Fab), and a ${REFERENCE} sized to the body goes on F.Fab at its centre (KLC F5.1 / F5.2)
  padshape  sharp rectangular SMD pads become rounded rectangles: radius 25 % of the short side, at most 0.25 mm
            (KLC F6.3, IPC-7351C)
  silk      silk drawn wholly inside an SMD body is removed (it is under the part)
  pin1      a part with 3 or more pads and no silk mark near pad 1 (silk the same turned 180 degrees is no mark) gets a 0.2 mm silk dot beside pad 1, outside
            the body and 0.2 mm clear of copper
  polarity  a rectangular fab outline gets its pin-1 corner chamfered (1 mm or 25 % of the body, KLC F5.2)
"""
import math

from . import sexpr
from .sexpr import Q

SILK_CLEAR = 0.2


def _num(v):
    return float(v)


def _fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _layer(it):
    lay = sexpr.first(it, "layer")
    return str(lay[1]) if lay else ""


def _pts(it):
    """Points of a drawing item (lines, rects, circles as their bounding points, polys)."""
    k = it[0]
    if k in ("fp_line", "fp_rect"):
        a, b = sexpr.first(it, "start"), sexpr.first(it, "end")
        if k == "fp_rect":
            x1, y1, x2, y2 = (_num(a[1]), _num(a[2]), _num(b[1]), _num(b[2]))
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        return [(_num(a[1]), _num(a[2])), (_num(b[1]), _num(b[2]))]
    if k == "fp_circle":
        c, e = sexpr.first(it, "center"), sexpr.first(it, "end")
        cx, cy = _num(c[1]), _num(c[2])
        r = math.hypot(_num(e[1]) - cx, _num(e[2]) - cy)
        return [(cx - r, cy - r), (cx + r, cy + r), (cx - r, cy + r), (cx + r, cy - r)]
    if k == "fp_arc":
        return [(_num(p[1]), _num(p[2])) for p in (sexpr.first(it, "start"), sexpr.first(it, "mid"), sexpr.first(it, "end")) if p]
    if k == "fp_poly":
        return [(_num(p[1]), _num(p[2])) for p in sexpr.find(sexpr.first(it, "pts") or [], "xy")]
    return []


def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _pads(tree):
    out = []
    for p in sexpr.find(tree, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        if not at or not size:
            continue
        rot = _num(at[3]) if len(at) > 3 else 0.0
        w, h = _num(size[1]), _num(size[2])
        if round(rot) % 180 == 90:
            w, h = h, w
        out.append({"num": str(p[1]), "x": _num(at[1]), "y": _num(at[2]), "w": w, "h": h, "kind": str(p[2]),
                    "shape": str(p[3]), "node": p, "rot": rot})
    return out


def _pad_gap(x, y, p):
    """Distance from a point to a pad's bounding box (0 inside)."""
    dx = max(abs(x - p["x"]) - p["w"] / 2, 0)
    dy = max(abs(y - p["y"]) - p["h"] / 2, 0)
    return math.hypot(dx, dy)


def _fab_rect(tree):
    """The body: the F.Fab outline's bounding box when the outline is a plain axis-aligned rectangle, with its
    items -> (bbox, items, is_rect)."""
    items = [it for it in tree if isinstance(it, list) and it and it[0] in ("fp_line", "fp_rect", "fp_poly")
             and _layer(it) == "F.Fab"]
    pts = [p for it in items for p in _pts(it)]
    if not pts:
        return None, [], False
    bb = _bbox(pts)
    axis = all(it[0] == "fp_rect" or (it[0] == "fp_line" and (abs(_pts(it)[0][0] - _pts(it)[1][0]) < 1e-6
                                                              or abs(_pts(it)[0][1] - _pts(it)[1][1]) < 1e-6)) for it in items)
    rect = axis and (len(items) == 1 and items[0][0] == "fp_rect" or len(items) == 4 and all(it[0] == "fp_line" for it in items)
                     and all(min(abs(x - bb[0]), abs(x - bb[2])) < 1e-6 and min(abs(y - bb[1]), abs(y - bb[3])) < 1e-6
                             for it in items for x, y in _pts(it)))
    return bb, items, rect


def _stroke(w):
    return ["stroke", ["width", _fmt(w)], ["type", "solid"]]


def tidy(text, notes=None):
    notes = [] if notes is None else notes
    tree = sexpr.parse(text, keep_quotes=True)
    if not isinstance(tree, list) or tree[0] not in ("footprint", "module"):
        return text
    pads = _pads(tree)
    if not pads:
        return text
    smd = (sexpr.first(tree, "attr") or [None, ""])[1] == "smd" or all(p["kind"] == "smd" for p in pads)
    body, fab_items, fab_rect = _fab_rect(tree)
    copper = _bbox([(p["x"] + sx * p["w"] / 2, p["y"] + sy * p["h"] / 2) for p in pads for sx in (-1, 1) for sy in (-1, 1)])
    crt = _bbox([q for it in tree if isinstance(it, list) and it and it[0] in ("fp_line", "fp_rect", "fp_poly")
                 and _layer(it) == "F.CrtYd" for q in _pts(it)])
    outer = crt or _bbox([(copper[0], copper[1]), (copper[2], copper[3])] + ([(body[0], body[1]), (body[2], body[3])] if body else []))
    numbered = [p for p in pads if p["num"]]
    pin1 = next((p for p in numbered if p["num"] in ("1", "A1")), None)

    # padshape: sharp SMD rectangles -> rounded rectangles (radius 25 % of the short side, at most 0.25 mm)
    rounded = 0
    for p in pads:
        n = p["node"]
        if p["kind"] == "smd" and p["shape"] == "rect" and not sexpr.first(n, "primitives"):
            ratio = min(0.25, 0.25 / max(min(p["w"], p["h"]), 1e-6))
            n[3] = "roundrect"
            i = n.index(sexpr.first(n, "size")) + 1
            n.insert(i, ["roundrect_rratio", _fmt(ratio)])
            p["shape"] = "roundrect"
            rounded += 1
    if rounded:
        notes.append(f"pads: {rounded} sharp SMD rectangle(s) given rounded corners (25 % of the short side, at most 0.25 mm; KLC F6.3)")

    # silk wholly under an SMD body is hidden by the part
    if smd and body:
        x0, y0, x1, y1 = body
        under = lambda q: x0 + 0.05 < q[0] < x1 - 0.05 and y0 + 0.05 < q[1] < y1 - 0.05
        gone = [it for it in tree if isinstance(it, list) and it and it[0] in ("fp_line", "fp_circle", "fp_poly", "fp_arc", "fp_rect")
                and _layer(it) == "F.SilkS" and _pts(it) and all(under(q) for q in _pts(it))]
        for it in gone:
            tree.remove(it)
        if gone:
            notes.append(f"silk: {len(gone)} item(s) under the part body removed (hidden once the part is placed)")

    # pin 1 mark on silk, outside the body and clear of copper
    if pin1 and len(numbered) >= 3:
        silk = [q for it in tree if isinstance(it, list) and it and it[0] in ("fp_line", "fp_circle", "fp_poly", "fp_arc", "fp_rect")
                and _layer(it) == "F.SilkS" for q in _pts(it)]
        reach = max(pin1["w"], pin1["h"]) / 2 + 1.0
        others = [p for p in pads if p is not pin1]
        mx, my = (copper[0] + copper[2]) / 2, (copper[1] + copper[3]) / 2
        twin = lambda q: any(abs(2 * mx - q[0] - s[0]) < 0.05 and abs(2 * my - q[1] - s[1]) < 0.05 for s in silk)
        near1 = [q for q in silk if math.hypot(q[0] - pin1["x"], q[1] - pin1["y"]) <= reach and not twin(q)   # a symmetric
                 and all(math.hypot(q[0] - pin1["x"], q[1] - pin1["y"]) < math.hypot(q[0] - o["x"], q[1] - o["y"]) for o in others)]
        if not near1:
            r = 0.1
            cx, cy = ((body[0] + body[2]) / 2, (body[1] + body[3]) / 2) if body else (0.0, 0.0)
            ox, oy = (1 if pin1["x"] > cx else -1), (1 if pin1["y"] > cy else -1)
            d = SILK_CLEAR + r + 0.06
            cands = [(pin1["x"] + ox * (pin1["w"] / 2 + d), pin1["y"]), (pin1["x"], pin1["y"] + oy * (pin1["h"] / 2 + d)),
                     (pin1["x"] + ox * (pin1["w"] / 2 + d), pin1["y"] + oy * (pin1["h"] / 2 + d))]
            inbody = lambda x, y: body and body[0] - r <= x <= body[2] + r and body[1] - r <= y <= body[3] + r
            spot = next(((x, y) for x, y in cands if not inbody(x, y) and all(_pad_gap(x, y, p) >= SILK_CLEAR + r for p in pads)), None)
            if spot:
                x, y = spot
                tree.append(["fp_circle", ["center", _fmt(x), _fmt(y)], ["end", _fmt(x + r), _fmt(y)], _stroke(2 * r),
                             ["fill", "solid"], ["layer", Q("F.SilkS")]])
                outer = (min(outer[0], x - 2 * r), min(outer[1], y - 2 * r), max(outer[2], x + 2 * r), max(outer[3], y + 2 * r))
                notes.append(f"silk: pin 1 dot added beside pad {pin1['num']} (no pin 1 mark was visible on silk)")

    # polarity: chamfer the fab outline's pin-1 corner
    if fab_rect and pin1 and len(numbered) >= 3:
        x0, y0, x1, y1 = body
        w = sexpr.first(sexpr.first(fab_items[0], "stroke") or [], "width")
        lw = _num(w[1]) if w else 0.1
        c = min(1.0, 0.25 * min(x1 - x0, y1 - y0))
        cx, cy = (x0 if pin1["x"] <= (x0 + x1) / 2 else x1), (y0 if pin1["y"] <= (y0 + y1) / 2 else y1)
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        k = corners.index((cx, cy))
        nxt, opp, prv = corners[(k + 1) % 4], corners[(k + 2) % 4], corners[(k + 3) % 4]
        toward = lambda q: (cx + c * (q[0] > cx) - c * (q[0] < cx), cy + c * (q[1] > cy) - c * (q[1] < cy))
        pts = [toward(nxt), nxt, opp, prv, toward(prv)]
        pts.append(pts[0])
        for it in fab_items:
            tree.remove(it)
        for a, b in zip(pts, pts[1:]):
            tree.append(["fp_line", ["start", _fmt(a[0]), _fmt(a[1])], ["end", _fmt(b[0]), _fmt(b[1])], _stroke(lw), ["layer", Q("F.Fab")]])
        notes.append(f"fab: pin 1 corner of the body outline chamfered {_fmt(c)} mm (KLC F5.2)")

    # text: reference on silk above the courtyard, ${REFERENCE} on fab inside the body
    texts = [it for it in tree if isinstance(it, list) and it and it[0] in ("fp_text", "property")]
    ref = next((it for it in texts if str(it[1]) in ("reference", "Reference")), None)
    if ref is not None and _layer(ref) != "F.SilkS":
        at = sexpr.first(ref, "at")
        y = outer[1] - 1.2      # Altium writes the literal ".Designator", whose g hangs below the baseline: keep it clear
        at[1:] = [_fmt((outer[0] + outer[2]) / 2), _fmt(y)]
        ref[ref.index(sexpr.first(ref, "layer"))] = ["layer", Q("F.SilkS")]
        eff = sexpr.first(ref, "effects")
        if eff is not None:
            eff[eff.index(sexpr.first(eff, "font"))] = ["font", ["size", "1", "1"], ["thickness", "0.15"]]
        notes.append("text: reference moved to the silkscreen above the part (it was on the fab layer at the centre)")
    val = next((it for it in texts if str(it[1]) in ("value", "Value")), None)
    vat = sexpr.first(val, "at") if val is not None else None
    if vat and outer[0] < _num(vat[1]) < outer[2] and outer[1] < _num(vat[2]) < outer[3]:
        vat[1:] = [_fmt((outer[0] + outer[2]) / 2), _fmt(outer[3] + 1.0)]
        if not _layer(val):
            val.append(["layer", Q("F.Fab")])
        notes.append("text: value moved below the part (it sat over the body, where Altium's .Comment crossed the outline)")
    has_fab_ref = any(str(it[1]) == "user" and str(it[2]) == "${REFERENCE}" for it in texts if it[0] == "fp_text" and len(it) > 2)
    if not has_fab_ref and body:
        bw, bh = body[2] - body[0], body[3] - body[1]
        fit = lambda along, across: min(1.0, 0.9 * along / (3 * 0.93), 0.8 * across)    # "U12" at 0.93 x height per character
        rot = fit(bh, bw) > fit(bw, bh)
        size = max(0.5, fit(bh, bw) if rot else fit(bw, bh))
        at = ["at", _fmt((body[0] + body[2]) / 2), _fmt((body[1] + body[3]) / 2)] + (["90"] if rot else [])
        tree.append(["fp_text", "user", Q("${REFERENCE}"), at, ["layer", Q("F.Fab")],
                     ["effects", ["font", ["size", _fmt(size), _fmt(size)], ["thickness", _fmt(round(0.15 * size, 3))]]]])
        notes.append("text: ${REFERENCE} added on the fab layer inside the body (assembly drawing, KLC F5.2)")
    return sexpr.dumps(tree) + "\n"
