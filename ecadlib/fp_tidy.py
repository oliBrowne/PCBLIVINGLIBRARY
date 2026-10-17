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
  pin1      a part with 3 or more pads and no silk mark near pad 1 (silk the same turned 180 degrees is no mark) gets a 0.3 mm silk dot beside pad 1, outside
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


SILK_KINDS = ("fp_line", "fp_circle", "fp_poly", "fp_arc", "fp_rect")


def _width(it):
    w = sexpr.first(sexpr.first(it, "stroke") or [], "width")
    return _num(w[1]) if w else 0.0


def _extent(it):
    """Largest printed size of a drawing item: its bounding box plus the line width."""
    bb = _bbox(_pts(it))
    return max(bb[2] - bb[0], bb[3] - bb[1]) + _width(it)


def _covers(o, it, tol=1e-3):
    """True when line o lies along line it and spans all of it."""
    (ax, ay), (bx, by) = _pts(o)
    L = math.hypot(bx - ax, by - ay)
    if L < tol:
        return False
    ux, uy = (bx - ax) / L, (by - ay) / L
    ts = []
    for px, py in _pts(it):
        if abs((px - ax) * uy - (py - ay) * ux) > tol:
            return False
        ts.append((px - ax) * ux + (py - ay) * uy)
    return min(ts) >= -tol and max(ts) <= L + tol


def _clear_rows(tree, pads, items):
    """Dual-row parts: a silk outline whose edges run between the two pad rows but closer than SILK_CLEAR to them
    is pulled in (both edges, symmetric about the pads' centre) -> the distance moved, or 0. Copper is not touched."""
    for ax in (1, 0):                          # y (rows above and below) first, then x
        lo = [p for p in pads if p["kind"] == "smd"]
        if len(lo) < 4:
            return 0
        c = (min(p[("x", "y")[ax]] for p in lo) + max(p[("x", "y")[ax]] for p in lo)) / 2
        half = lambda p: (p["w"], p["h"])[ax] / 2
        rows = {}
        for p in lo:
            rows.setdefault(round(p[("x", "y")[ax]] - c, 3), []).append(p)
        if len(rows) != 2 or sorted(rows)[0] != -sorted(rows)[1] or sorted(rows)[1] <= 0:
            continue
        inner = min(abs(k) - max(half(p) for p in ps) for k, ps in rows.items())
        coords = [q[ax] - c for it in items for q in _pts(it)]
        if not coords:
            return 0
        edge = max(abs(v) for v in coords)
        if not (0 < edge < inner) or not any(abs(v + edge) < 1e-3 for v in coords):
            continue
        w = max((_width(it) for it in items), default=0.12)
        new = inner - SILK_CLEAR - w / 2
        if new >= edge - 1e-3 or new < 0.3:
            continue
        key = ("start", "mid", "end", "center")
        for it in items:
            for k in key:
                n = sexpr.first(it, k)
                if n and abs(abs(_num(n[1 + ax]) - c) - edge) < 1e-3:
                    n[1 + ax] = _fmt(c + math.copysign(new, _num(n[1 + ax]) - c))
        return round(edge - new, 3)
    return 0


def _join_lines(tree, pads, lines, arc_pts=(), tol=1e-3):
    """Axis-aligned silk lines on one row with gaps between them: a gap is closed when the joined line stays
    SILK_CLEAR (plus half its width) from every pad and no arc (a pin-1 notch) ends in it. Returns the gaps closed."""
    rows = {}
    for it in lines:
        (ax, ay), (bx, by) = _pts(it)
        if abs(ay - by) < tol and abs(ax - bx) > tol:
            rows.setdefault((0, round(ay, 3), round(_width(it), 3)), []).append((min(ax, bx), max(ax, bx), it))
        elif abs(ax - bx) < tol and abs(ay - by) > tol:
            rows.setdefault((1, round(ax, 3), round(_width(it), 3)), []).append((min(ay, by), max(ay, by), it))
    closed = 0
    for (ax, c, w), segs in rows.items():
        segs.sort(key=lambda t: t[0])
        cur = list(segs[0])
        for lo, hi, it in segs[1:]:
            pts = [(v, c) if ax == 0 else (c, v) for v in (cur[1], lo, (cur[1] + lo) / 2)]
            pts += [((cur[1] + (lo - cur[1]) * k / 20, c) if ax == 0 else (c, cur[1] + (lo - cur[1]) * k / 20)) for k in range(21)]
            notch = any(abs((q[1] if ax == 0 else q[0]) - c) < 0.05 and cur[1] - tol <= (q[0] if ax == 0 else q[1]) <= lo + tol
                        for q in arc_pts)
            if lo > cur[1] + tol and not notch and all(_pad_gap(x, y, p) >= SILK_CLEAR + w / 2 - tol for x, y in pts for p in pads):
                closed += 1
            elif lo > cur[1] + tol:
                cur = [lo, hi, it]
                continue
            cur[1] = max(cur[1], hi)
            tree.remove(it)
            ends = ("start", "end")
            a, b = (sexpr.first(cur[2], k) for k in ends)
            lo_, hi_ = cur[0], cur[1]
            a[1:3], b[1:3] = ([_fmt(lo_), _fmt(c)], [_fmt(hi_), _fmt(c)]) if ax == 0 else ([_fmt(c), _fmt(lo_)], [_fmt(c), _fmt(hi_)])
    return closed


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

    silk_items = lambda: [it for it in tree if isinstance(it, list) and it and it[0] in SILK_KINDS and _layer(it) == "F.SilkS" and _pts(it)]

    # silk specks (EasyEDA's 0.06 mm "polarity" circles) are too small to print or to read as a mark
    specks = [it for it in silk_items() if _extent(it) < 0.15]
    for it in specks:
        tree.remove(it)
    if specks:
        notes.append(f"silk: {len(specks)} speck(s) under 0.15 mm removed (too small to print)")

    # a dot drawn as a thick ring (EasyEDA's CIRCLE, a 16-gon from the converter) -> the same dot, filled, with a KLC width
    dots = 0
    for it in silk_items():
        if it[0] in ("fp_poly", "fp_circle") and _extent(it) <= 0.65 and not 0.1 - 1e-6 <= _width(it) <= 0.15 + 1e-6:
            bb, R = _bbox(_pts(it)), _extent(it) / 2
            x, y = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            tree[tree.index(it)] = ["fp_circle", ["center", _fmt(x), _fmt(y)], ["end", _fmt(x + max(R - 0.06, 0.03)), _fmt(y)],
                                    _stroke(0.12), ["fill", "solid"], ["layer", Q("F.SilkS")]]
            dots += 1
    if dots:
        notes.append(f"silk: {dots} dot(s) redrawn filled with a 0.12 mm line (same size)")

    # silk line width to KLC's 0.10-0.15 mm
    widths = 0
    for it in silk_items():
        w = sexpr.first(sexpr.first(it, "stroke") or [], "width")
        if w and not 0.1 - 1e-6 <= _num(w[1]) <= 0.15 + 1e-6:
            w[1] = "0.12"
            widths += 1
    if widths:
        notes.append(f"silk: {widths} item(s) set to 0.12 mm line width (KLC: 0.10-0.15 mm)")

    # a dot or other closed shape wholly under an SMD body is hidden by the part (its outline lines stay)
    if smd and body:
        x0, y0, x1, y1 = body
        under = lambda q: x0 + 0.05 < q[0] < x1 - 0.05 and y0 + 0.05 < q[1] < y1 - 0.05
        gone = [it for it in silk_items() if it[0] in ("fp_circle", "fp_poly") and _extent(it) < 1.5 and all(under(q) for q in _pts(it))]
        for it in gone:
            tree.remove(it)
        if gone:
            notes.append(f"silk: {len(gone)} mark(s) under the part body removed (hidden once the part is placed)")

    # silk segments another silk line already covers (EasyEDA draws some outlines twice)
    lines = [it for it in silk_items() if it[0] == "fp_line"]
    dup = [it for it in lines if any(o is not it and _covers(o, it) and not (_covers(it, o) and lines.index(o) > lines.index(it))
                                     for o in lines)]
    for it in dup:
        tree.remove(it)
    if dup:
        notes.append(f"silk: {len(dup)} duplicate segment(s) removed (drawn twice)")

    # silk outline edges that run along a pad row too close to it are moved in to 0.2 mm clearance
    moved = _clear_rows(tree, pads, [it for it in silk_items() if it[0] in ("fp_line", "fp_arc")])
    if moved:
        notes.append(f"silk: outline moved in {moved} mm to clear the pads by {SILK_CLEAR} mm")

    # collinear silk pieces whose gap no longer needs to be there (cut back from pads before the outline moved in)
    joined = _join_lines(tree, pads, [it for it in silk_items() if it[0] == "fp_line"],
                         [q for it in silk_items() if it[0] == "fp_arc" for q in _pts(it)])
    if joined:
        notes.append(f"silk: {joined} gap(s) in the outline closed (now clear of the pads)")

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
            r, sw = 0.09, 0.12                   # filled: 0.3 mm across, drawn with a KLC silk width
            R = r + sw / 2
            cx, cy = ((body[0] + body[2]) / 2, (body[1] + body[3]) / 2) if body else (0.0, 0.0)
            ox, oy = (1 if pin1["x"] > cx else -1), (1 if pin1["y"] > cy else -1)
            d = SILK_CLEAR + R + 0.02
            cands = [(pin1["x"] + ox * (pin1["w"] / 2 + d), pin1["y"]), (pin1["x"], pin1["y"] + oy * (pin1["h"] / 2 + d)),
                     (pin1["x"] + ox * (pin1["w"] / 2 + d), pin1["y"] + oy * (pin1["h"] / 2 + d))]
            inbody = lambda x, y: body and body[0] - R <= x <= body[2] + R and body[1] - R <= y <= body[3] + R
            spot = next(((x, y) for x, y in cands if not inbody(x, y) and all(_pad_gap(x, y, p) >= SILK_CLEAR + R for p in pads)), None)
            if spot:
                x, y = spot
                tree.append(["fp_circle", ["center", _fmt(x), _fmt(y)], ["end", _fmt(x + r), _fmt(y)], _stroke(sw),
                             ["fill", "solid"], ["layer", Q("F.SilkS")]])
                outer = (min(outer[0], x - R), min(outer[1], y - R), max(outer[2], x + R), max(outer[3], y + R))
                notes.append(f"silk: pin 1 dot added beside pad {pin1['num']} (no pin 1 mark was visible on silk)")

    # courtyard around the silk too: a pin-1 dot outside the body must not stick out of the part's keep-out
    crts = [it for it in tree if isinstance(it, list) and it and it[0] == "fp_rect" and _layer(it) == "F.CrtYd"]
    marks = [it for it in tree if isinstance(it, list) and it and it[0] in SILK_KINDS and _layer(it) == "F.SilkS" and _pts(it)]
    if len(crts) == 1 and marks:
        a, b = sexpr.first(crts[0], "start"), sexpr.first(crts[0], "end")
        x0, y0, x1, y1 = min(_num(a[1]), _num(b[1])), min(_num(a[2]), _num(b[2])), max(_num(a[1]), _num(b[1])), max(_num(a[2]), _num(b[2]))
        sx0 = min(_bbox(_pts(it))[0] - _width(it) / 2 for it in marks)
        sy0 = min(_bbox(_pts(it))[1] - _width(it) / 2 for it in marks)
        sx1 = max(_bbox(_pts(it))[2] + _width(it) / 2 for it in marks)
        sy1 = max(_bbox(_pts(it))[3] + _width(it) / 2 for it in marks)
        n0 = (math.floor(min(x0, sx0) * 100 + 1e-6) / 100, math.floor(min(y0, sy0) * 100 + 1e-6) / 100)
        n1 = (math.ceil(max(x1, sx1) * 100 - 1e-6) / 100, math.ceil(max(y1, sy1) * 100 - 1e-6) / 100)
        if n0[0] < x0 - 1e-6 or n0[1] < y0 - 1e-6 or n1[0] > x1 + 1e-6 or n1[1] > y1 + 1e-6:
            a[1:3], b[1:3] = [_fmt(n0[0]), _fmt(n0[1])], [_fmt(n1[0]), _fmt(n1[1])]
            notes.append("courtyard grown to take in the silkscreen (the pin-1 dot sat outside it)")

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
