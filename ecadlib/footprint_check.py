"""Rule checks for a KiCad SMD footprint: the things a librarian looks at before trusting one.

    python -m ecadlib.footprint_check path/to/*.kicad_mod        (or: ecadlib check ...)

Works on any .kicad_mod (ours or KiCad's own), so the same rules can be run on the official
library as a baseline. Rules follow the KiCad Library Conventions (KLC F5.x/F6.x) and common
fab limits; each finding is (level, rule, message) with level "error" or "warning".

  copper    pads must not overlap; gap between pads >= 0.2 mm (solder-mask web; warning below,
            error below 0.1 mm)
  silk      silkscreen line edge >= 0.2 mm from any pad's real (rounded) outline (KLC F5.1), or 0.1 mm on
            tiny parts where KiCad does the same; silk line width 0.10-0.15 mm
  fab       a body outline on F.Fab, line width 0.10 mm (KLC F5.2)
  courtyard present, a closed outline on F.CrtYd, encloses every pad and the F.Fab body,
            line width 0.05 mm, corners on the 0.01 mm grid (KLC F5.3)
  pin 1     a pad numbered "1" (or "A1" for BGAs and lettered connector rows) exists; for multi-row parts it is the top-left-most pad (KLC F6.x
            orientation) and the numbers run counter-clockwise from it; pad numbers are unique except for deliberate duplicates (a SOT-223 tab)
  paste     an exposed pad's paste windows cover 50-90 % of its copper
  geometry  every pad position / size and outline coordinate is a number (no nan / inf, which KiCad cannot load)
  bga       ball grids (pads named A1, B2, ... on a regular grid): A1 is the top-left ball and marked on the silk
            (a filled mark or a gap near the A1 corner) and on the fab layer (a chamfered corner); every ball on
            the pitch grid; land diameter 35-65 % of the pitch (outside that: weak joints or no room to escape);
            JEDEC row letters (no I, O, Q, S, X, Z)
  connector mounting pads (MP, MH, SH, S1, unnumbered NPTH ...) are left out of the pin-1 and numbering rules;
            dual-row zig-zag numbering (1 3 5 / 2 4 6) is accepted; stacked USB-C GND (A1/A12/B1/B12) and VBUS
            (A4/A9/B4/B9) pads may overlap (a warning), as may pads listed in same_net
"""
import math, re, sys
from pathlib import Path

from . import sexpr

MIN_WEB, HARD_WEB, SILK_CLEAR, SILK_CLEAR_SMALL = 0.2, 0.1, 0.2, 0.1
EPS = 0.005
MOUNT = re.compile(r"^((MP|MH|SH|MTG|MOUNT|MECH|NPTH|S|H)\d*)?$", re.I)   # mounting / shield pads: not pins
BALL = re.compile(r"^([A-Z]{1,2})(\d{1,3})$")
GRID_ROW = re.compile(r"^(?:[A-GJ-NPRTUVWY]|[A-GJ-NPRTUVWY][A-HJ-NPRTUVWY])\d{1,3}$")   # JEDEC ball names, H aside


def mount_rx(nums):
    """The mounting-pad pattern for a footprint with these pad numbers. 'H<n>' names a mounting hole, except in a ball
    grid (other JEDEC row letters present), where H is a row of balls (A..G, H, J..) and those pads are pins."""
    if sum(1 for n in nums if GRID_ROW.match(str(n))) >= 2 and any(re.fullmatch(r"H\d{1,3}", str(n)) for n in nums):
        return MOUNT_GRID
    return MOUNT


MOUNT_GRID = re.compile(r"^((MP|MH|SH|MTG|MOUNT|MECH|NPTH|S)\d*)?$", re.I)
USB_C_STACKS = ({"A1", "A12", "B1", "B12"}, {"A4", "A9", "B4", "B9"})     # GND, VBUS: stacked on purpose


def _f(v):
    return float(v)


ARC_TOL = 0.002                                      # mm a sampled arc's chords may lie inside the real arc


def _arc_points(g):
    """The points along an arc node (fp_arc / gr_arc), in file coordinates: KiCad 6+ (start) (mid) (end), or KiCad 5
    (start = centre) (end = arc start) (angle, degrees, clockwise on screen: y points down). Sampled so no chord lies
    more than ARC_TOL inside the arc; a straight "arc" (start, mid, end in a line) is its two ends."""
    s, e, m = sexpr.first(g, "start"), sexpr.first(g, "end"), sexpr.first(g, "mid")
    if m:
        (x1, y1), (xm, ym), (x2, y2) = ((_f(n[1]), _f(n[2])) for n in (s, m, e))
        d = 2 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
        if abs(d) < 1e-12:
            return [(x1, y1), (x2, y2)]
        cx = ((x1 * x1 + y1 * y1) * (ym - y2) + (xm * xm + ym * ym) * (y2 - y1) + (x2 * x2 + y2 * y2) * (y1 - ym)) / d
        cy = ((x1 * x1 + y1 * y1) * (x2 - xm) + (xm * xm + ym * ym) * (x1 - x2) + (x2 * x2 + y2 * y2) * (xm - x1)) / d
        a0, am, a2 = (math.atan2(y - cy, x - cx) for x, y in ((x1, y1), (xm, ym), (x2, y2)))
        span = (a2 - a0) % (2 * math.pi)
        if (am - a0) % (2 * math.pi) > span:         # the mid point is the other way round
            span -= 2 * math.pi
        r = math.hypot(x1 - cx, y1 - cy)
    else:
        ang = sexpr.first(g, "angle")
        if not ang:
            return [(_f(s[1]), _f(s[2])), (_f(e[1]), _f(e[2]))]
        cx, cy = _f(s[1]), _f(s[2])
        r = math.hypot(_f(e[1]) - cx, _f(e[2]) - cy)
        a0, span = math.atan2(_f(e[2]) - cy, _f(e[1]) - cx), math.radians(_f(ang[1]))
    if not (math.isfinite(r) and math.isfinite(span)):
        return [(_f(s[1]), _f(s[2])), (_f(e[1]), _f(e[2]))]
    step = 2 * math.acos(max(-1.0, 1 - ARC_TOL / r)) if r > ARC_TOL else math.pi / 2
    n = min(720, max(2, math.ceil(abs(span) / step)))
    return [(cx + r * math.cos(a0 + span * i / n), cy + r * math.sin(a0 + span * i / n)) for i in range(n + 1)]


def _graphics(tree, layer, raw_arcs=False):
    """Segments (x1, y1, x2, y2, width) on a layer, from fp_line / fp_rect / fp_poly / fp_circle bounding boxes and
    sampled fp_arcs. raw_arcs: an arc as the points written in the file (start, mid, end), for the grid rule."""
    segs, polys = [], []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexpr.find(tree, kind):
            if (sexpr.first(g, "layer") or [None, None])[1] != layer:
                continue
            w = _f((sexpr.first(sexpr.first(g, "stroke") or [], "width") or sexpr.first(g, "width") or [None, 0])[1])
            if kind == "fp_poly":
                pts = [(_f(a[1]), _f(a[2])) for a in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
                filled = (sexpr.first(g, "fill") or [None, "none"])[1] in ("solid", "yes")
                polys.append((pts, filled))
                segs += [(*a, *b, w) for a, b in zip(pts, pts[1:] + pts[:1])]
                continue
            s, e = sexpr.first(g, "start"), sexpr.first(g, "end")
            if kind == "fp_circle":
                s = sexpr.first(g, "center") or s          # KiCad 6+ writes (center ...), KiCad 5 (start ...)
                cx, cy = _f(s[1]), _f(s[2]); r = math.hypot(_f(e[1]) - cx, _f(e[2]) - cy)
                segs += [(cx + r * math.cos(a), cy + r * math.sin(a), cx + r * math.cos(b), cy + r * math.sin(b), w)
                         for a, b in ((i * math.pi / 8, (i + 1) * math.pi / 8) for i in range(16))]
                continue
            if kind == "fp_arc":                         # the real arc, sampled (not a line or two chords)
                m = sexpr.first(g, "mid")
                pts = ([(_f(s[1]), _f(s[2]))] + ([(_f(m[1]), _f(m[2]))] if m else []) + [(_f(e[1]), _f(e[2]))]
                       if raw_arcs else _arc_points(g))
                segs += [(*a, *b, w) for a, b in zip(pts, pts[1:])]
                continue
            x1, y1, x2, y2 = _f(s[1]), _f(s[2]), _f(e[1]), _f(e[2])
            if kind == "fp_rect":
                c = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                segs += [(*a, *b, w) for a, b in zip(c, c[1:] + c[:1])]
            else:
                segs.append((x1, y1, x2, y2, w))
    return segs, polys


def _capsule(a, b, hw, n=8):
    """The copper of a line from a to b, hw its half width (round ends): a polygon."""
    ang = math.atan2(b[1] - a[1], b[0] - a[0]) if a != b else 0.0
    pts = [(b[0] + hw * math.cos(ang - math.pi / 2 + math.pi * i / n), b[1] + hw * math.sin(ang - math.pi / 2 + math.pi * i / n))
           for i in range(n + 1)]
    return pts + [(a[0] + hw * math.cos(ang + math.pi / 2 + math.pi * i / n), a[1] + hw * math.sin(ang + math.pi / 2 + math.pi * i / n))
                  for i in range(n + 1)]


def _circle(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _custom_polys(p, w, h):
    """A KiCad custom pad's copper in its own frame (y down, not turned): the anchor and every primitive as polygons.
    Filled gr_poly / gr_rect / gr_circle are their area; strokes (gr_line, gr_arc, gr_curve, outlines and the
    edges of a stroked shape) are round-ended bands of the stroke width."""
    anchor = (sexpr.first(sexpr.first(p, "options") or [], "anchor") or [None, "rect"])[1]
    polys = [_circle(0.0, 0.0, w / 2) if anchor == "circle" else [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]]
    xy = lambda n: (_f(n[1]), _f(n[2]))
    for g in (sexpr.first(p, "primitives") or [])[1:]:
        if not isinstance(g, list) or not g:
            continue
        hw = _f((sexpr.first(g, "width") or sexpr.first(sexpr.first(g, "stroke") or [], "width") or [None, 0])[1]) / 2
        fill = sexpr.first(g, "fill")
        filled = fill[1] in ("yes", "solid") if fill and len(fill) > 1 else hw == 0 or g[0] == "gr_poly"
        band = lambda pts, closed: [_capsule(a, b, hw) for a, b in zip(pts, pts[1:] + (pts[:1] if closed else []))] if hw > 0 else []
        kind = g[0]
        if kind in ("gr_poly", "gr_rect"):
            if kind == "gr_poly":
                pts = [xy(n) for n in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
            else:
                (x1, y1), (x2, y2) = xy(sexpr.first(g, "start")), xy(sexpr.first(g, "end"))
                pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            if filled and len(pts) >= 3:
                polys.append(pts)
            polys += band(pts, True)
        elif kind == "gr_circle":
            c, e = xy(sexpr.first(g, "center") or sexpr.first(g, "start")), xy(sexpr.first(g, "end"))
            r = math.hypot(e[0] - c[0], e[1] - c[1])
            if filled:
                polys.append(_circle(c[0], c[1], r + hw))
            elif hw > 0:
                ring = _circle(c[0], c[1], r)
                polys += band(ring, True)
        elif kind == "gr_line":
            polys += band([xy(sexpr.first(g, "start")), xy(sexpr.first(g, "end"))], False)
        elif kind == "gr_arc":
            polys += band(_arc_points(g), False)
        elif kind == "gr_curve":
            c = [xy(n) for n in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
            if len(c) == 4:
                pts = [tuple((1 - t) ** 3 * c[0][k] + 3 * (1 - t) ** 2 * t * c[1][k] + 3 * (1 - t) * t * t * c[2][k] + t ** 3 * c[3][k]
                             for k in (0, 1)) for t in (i / 32 for i in range(33))]
                polys += band(pts, False)
    return [q for q in polys if len(q) >= 3]


def _pad_rects(tree):
    out = []
    for p in sexpr.find(tree, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y, w, h = _f(at[1]), _f(at[2]), _f(size[1]), _f(size[2])
        rot = _f(at[3]) if len(at) > 3 else 0.0
        shape0 = p[3] if len(p) > 3 else "rect"
        polys = None
        if shape0 == "custom":                       # anchor + primitives: the copper as polygons, turned with the pad
            c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            turn = lambda u, v: (x + u * c + v * s_, y - u * s_ + v * c)      # KiCad's pad angle (y points down)
            polys = [[turn(u, v) for u, v in q] for q in _custom_polys(p, w, h)]
            xs = [u for q in polys for u, _ in q]
            ys = [v for q in polys for _, v in q]
            x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            w, h = max(xs) - min(xs), max(ys) - min(ys)
        odd_angle = abs(rot / 90 - round(rot / 90)) > 1e-6 and polys is None
        if odd_angle:                                # any other angle: use the rotated pad's bounding box
            c, s_ = abs(math.cos(math.radians(rot))), abs(math.sin(math.radians(rot)))
            w, h = w * c + h * s_, w * s_ + h * c
        elif round(rot / 90) % 2 and polys is None:
            w, h = h, w
        layers = (sexpr.first(p, "layers") or ["layers"])[1:]
        shape = p[3] if len(p) > 3 else "rect"
        rr = sexpr.first(p, "roundrect_rratio")
        r = (_f(rr[1]) * min(w, h) if shape == "roundrect" and rr else
             min(w, h) / 2 if shape in ("circle", "oval") else 0.0)
        if odd_angle:
            r = 0.0                                  # the bounding box has square corners
        drill = sexpr.first(p, "drill")
        out.append({"num": p[1], "x": x, "y": y, "w": w, "h": h, "r": r, "layers": layers, "rot": rot, "kind": p[2], "polys": polys,
                    "shape": shape, "drill": drill is not None,
                    "copper": any(l.endswith(".Cu") for l in layers) and p[2] != "np_thru_hole",
                    "hole": p[2] == "np_thru_hole", "paste": any("Paste" in l for l in layers)})
    return out


def _rect_gap(a, b):
    """Copper gap between two pads. A roundrect is its inner rectangle grown by a disc of radius r,
    so the gap is the inner rectangles' distance minus both radii (exact for rounded corners)."""
    ra, rb = a.get("r", 0.0), b.get("r", 0.0)
    ix = abs(a["x"] - b["x"]) - (a["w"] + b["w"]) / 2 + ra + rb      # the inner rectangles' gaps: round pads whose
    iy = abs(a["y"] - b["y"]) - (a["h"] + b["h"]) / 2 + ra + rb      # boxes meet only at a corner don't touch
    if ix < 0 and iy < 0:
        return max(ix, iy) - ra - rb             # overlap (negative)
    return math.hypot(max(ix, 0), max(iy, 0)) - ra - rb


def _pt_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def _pt_pad(px, py, r):
    """Distance from a point to a pad's copper (0 inside); custom pads exactly (their polygons), others as rounded
    rectangles."""
    if r.get("polys"):
        if any(_point_in_poly(px, py, q) for q in r["polys"]):
            return 0.0
        return min(_pt_seg(px, py, a, b) for q in r["polys"] for a, b in zip(q, q[1:] + q[:1]))
    rad = r.get("r", 0.0)
    dx = max(abs(px - r["x"]) - (r["w"] / 2 - rad), 0)
    dy = max(abs(py - r["y"]) - (r["h"] / 2 - rad), 0)
    return max(math.hypot(dx, dy) - rad, 0)


def _depth(x, y, q):
    """How far a point inside a pad's copper is from its edge (0 when it is not inside)."""
    if q.get("polys"):
        return max((min(_pt_seg(x, y, a, b) for a, b in zip(o, o[1:] + o[:1])) for o in q["polys"] if _point_in_poly(x, y, o)),
                   default=0.0)
    return max(min(q["w"] / 2 - abs(x - q["x"]), q["h"] / 2 - abs(y - q["y"])), 0.0)


def _poly_gap(a, b):
    """Copper gap when a custom (polygon) pad is involved: sample each outline against the other (negative = overlap,
    by how deep the deepest sampled point lies in the other pad)."""
    def outlines(p):
        if p.get("polys"):
            return p["polys"]
        return [[(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2), (p["x"] + p["w"] / 2, p["y"] - p["h"] / 2),
                 (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2), (p["x"] - p["w"] / 2, p["y"] + p["h"] / 2)]]
    best, deep = 1e9, None
    for p, q in ((a, b), (b, a)):
        for pts in outlines(p):
            for u, v in zip(pts, pts[1:] + pts[:1]):
                n = max(2, int(math.hypot(v[0] - u[0], v[1] - u[1]) / 0.01))
                for i in range(n + 1):
                    x, y = u[0] + (v[0] - u[0]) * i / n, u[1] + (v[1] - u[1]) * i / n
                    d = _pt_pad(x, y, q)
                    if d == 0.0 and (q.get("polys") or (abs(x - q["x"]) < q["w"] / 2 - EPS and abs(y - q["y"]) < q["h"] / 2 - EPS)):
                        deep = max(deep or 0.0, _depth(x, y, q))
                    best = min(best, d)
    return -max(deep, 2 * EPS) if deep is not None else best


def _seg_rect_dist(seg, r):
    """Distance from a segment to a pad's real outline (rounded corners included; 0 if they touch), by dense sampling."""
    x1, y1, x2, y2 = seg[:4]
    n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.005) + 1)
    best = 1e9
    for i in range(n + 1):
        t = i / n
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        if r.get("polys"):
            best = min(best, _pt_pad(px, py, r))
            continue
        rad = r.get("r", 0.0)
        dx = max(abs(px - r["x"]) - (r["w"] / 2 - rad), 0)
        dy = max(abs(py - r["y"]) - (r["h"] / 2 - rad), 0)
        best = min(best, max(math.hypot(dx, dy) - rad, 0))
    return best


def _point_in_poly(x, y, pts):
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _closed(segs):
    """True when the outline's segment ends all meet another end (every corner used an even number of times)."""
    if len(segs) < 3:
        return False
    ends = {}
    for s in segs:
        for pt in ((s[0], s[1]), (s[2], s[3])):
            k = (round(pt[0], 3), round(pt[1], 3))
            ends[k] = ends.get(k, 0) + 1
    return all(v % 2 == 0 for v in ends.values())


def _bbox(segs):
    xs = [v for s in segs for v in (s[0], s[2])]
    ys = [v for s in segs for v in (s[1], s[3])]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _ranges(nums):
    """['1','2','3','5'] -> '1-3, 5'."""
    ints = sorted({int(n) for n in nums if str(n).isdecimal()})
    out, i = [], 0
    while i < len(ints):
        j = i
        while j + 1 < len(ints) and ints[j + 1] == ints[j] + 1:
            j += 1
        out.append(f"{ints[i]}-{ints[j]}" if j > i else str(ints[i]))
        i = j + 1
    return ", ".join(out + sorted(str(n) for n in nums if not str(n).isdecimal()))


def _share_copper(a, b):
    """Two pads on at least one common copper layer (through-hole '*.Cu' is on all of them)."""
    la = {l for l in a["layers"] if l.endswith(".Cu")}
    lb = {l for l in b["layers"] if l.endswith(".Cu")}
    return "*.Cu" in la or "*.Cu" in lb or bool(la & lb) or (a["drill"] and bool(lb)) or (b["drill"] and bool(la))


def _usb_c_stack(a, b, nums):
    """Two overlapping pads of a USB-C receptacle that the USB-C pinout ties together (GND or VBUS)."""
    return {"A1", "B12", "A4", "B9"} <= nums and any({a, b} <= g for g in USB_C_STACKS)


def check(text, allow_duplicate_pads=None, same_net=None):
    """Return a list of (level, rule, message). Empty means every rule passed. same_net: groups of pad numbers that are
    one net (overlapping is then deliberate, e.g. [["A1", "B12"]])."""
    tree = sexpr.parse(text)
    issues = []
    add = lambda lvl, rule, msg: issues.append((lvl, rule, msg))
    pads = _pad_rects(tree)
    cu = [p for p in pads if p["copper"]]
    holes = [p for p in pads if p["hole"]]
    nums = [p["num"] for p in cu if p["num"]]
    MOUNT = mount_rx(nums)                           # noqa: N806 (H<n> is a ball row in a grid, a mounting hole otherwise)
    groups = [set(map(str, g)) for g in same_net or []]
    numset = set(nums)

    # ---- numbers: a nan / inf coordinate passes every comparison below (all False) and KiCad cannot load the file
    odd = [p["num"] or "(unnumbered)" for p in pads if not all(math.isfinite(v) for v in (p["x"], p["y"], p["w"], p["h"]))]
    if odd:
        add("error", "geometry", f"pad {odd[0]} has a position or size that is not a number (nan / inf)")
    for layer in ("F.CrtYd", "F.Fab", "F.SilkS"):
        if any(not math.isfinite(v) for s in _graphics(tree, layer)[0] for v in s):
            add("error", "geometry", f"a {layer} outline has a coordinate that is not a number (nan / inf)")
    if issues:
        return issues                                # the other rules cannot measure it (some would raise)

    # ---- copper
    for p in cu:                                     # zero-area copper: nothing to solder (size typed as 0, or a lost unit)
        if p["w"] <= EPS or p["h"] <= EPS:
            add("error", "copper", f"pad {p['num'] or '(unnumbered)'} has no copper: size {p['w']:g} x {p['h']:g} mm")
    close = {}                                       # (level, gap) -> [(a, b)]: one finding per distinct gap
    # only pairs whose boxes come within MIN_WEB along x can have a finding (rounding and polygons only widen the
    # gap): a sweep finds them, so a 2500-ball grid is not 3 million pair checks (9 s); the pairs keep their order
    near, live = {i: [] for i in range(len(cu))}, []
    for k in sorted(range(len(cu)), key=lambda k: cu[k]["x"] - cu[k]["w"] / 2):
        left = cu[k]["x"] - cu[k]["w"] / 2
        live = [j for j in live if cu[j]["x"] + cu[j]["w"] / 2 > left - MIN_WEB]
        for j in live:
            near[min(j, k)].append(max(j, k))
        live.append(k)
    for i, a in enumerate(cu):
        for b in (cu[j] for j in sorted(near[i])):
            if a["num"] and a["num"] == b["num"]:
                continue                             # same net (e.g. SOT-223 pin 2 + tab): no web needed
            if not _share_copper(a, b):
                continue                             # F.Cu lead vs a B.Cu heat spreader: different layers
            tied = any({a["num"], b["num"]} <= g for g in groups) or _usb_c_stack(a["num"], b["num"], numset)
            g = _rect_gap(a, b)
            if g < MIN_WEB - EPS and (a.get("polys") or b.get("polys")):
                g = _poly_gap(a, b)                  # the bounding box is not the copper: measure the polygon
            if g < -EPS:
                if tied:
                    add("warning", "copper", f"pads {a['num']} and {b['num']} overlap by {-g:.3f} mm (same net: stacked on purpose)")
                else:
                    add("error", "copper", f"pads {a['num']} and {b['num']} overlap by {-g:.3f} mm")
            elif g < MIN_WEB - EPS and not tied:
                close.setdefault(("error" if g < HARD_WEB - EPS else "warning", round(g, 3)), []).append((a["num"], b["num"]))
    for (lvl, g), pairs in sorted(close.items()):
        common = set(pairs[0]).intersection(*map(set, pairs)) if len(pairs) > 1 else set()
        if common:
            hub = common.pop()
            others = [x for pr in pairs for x in pr if x != hub]
            who = f"pads {_ranges(others)} are {g:.3f} mm from pad {hub}"
        else:
            who = ", ".join(f"{a}-{b}" for a, b in pairs[:6]) + (" …" if len(pairs) > 6 else "")
            who = f"pad pairs {who} are {g:.3f} mm apart"
        add(lvl, "copper", who + (f" (< {HARD_WEB} mm)" if lvl == "error" else
                                  f": below a {MIN_WEB} mm solder-mask web, check the fab's minimum"))
    for h in holes:                                  # NPTH: copper must keep clear of the hole
        near = [(_rect_gap(dict(h, r=min(h["w"], h["h"]) / 2), p), p["num"]) for p in cu]
        if near and min(near)[0] < MIN_WEB - EPS:
            d, n = min(near)
            add("error" if d < 0 else "warning", "copper", f"pad {n} is {max(d, 0):.3f} mm from the hole at "
                f"({h['x']:.3f}, {h['y']:.3f}) (< {MIN_WEB} mm)")

    # ---- pin 1 / numbering (mounting and shield pads are not pins)
    pin1 = "1" if "1" in nums else "A1" if "A1" in nums else None    # A1: BGA balls, USB-C and other lettered rows
    if pin1 is None and not _a1_spot([p for p in cu if BALL.match(p["num"])]):
        add("error", "pin1", "no pad numbered 1")           # (a ball grid may leave A1 out: its corner is still pin 1)
    dup = {n for n in nums if nums.count(n) > 1 and not MOUNT.fullmatch(n)} - set(allow_duplicate_pads or ())
    for n in sorted(dup):
        if not any(p["num"] == n and p["w"] * p["h"] > 2 * min(q["w"] * q["h"] for q in cu) for p in cu):
            add("warning", "pin1", f"pad number {n} is used more than once")
    sig = [p for p in cu if p["num"] and not MOUNT.fullmatch(p["num"])]
    if pin1 and len(sig) > 2:
        left = min(p["x"] for p in sig)
        top = min(p["y"] for p in sig if abs(p["x"] - left) < EPS)
        ones = [p for p in sig if p["num"] == pin1]   # a pad 1 with vias or a second land: any of them may be the corner
        if not any((p["x"] <= left + EPS and p["y"] <= top + EPS) or (abs(p["x"]) < EPS and abs(p["y"]) < EPS) for p in ones):
            p1 = ones[0]
            add("warning", "pin1", f"pad {pin1} at ({p1['x']:.3f}, {p1['y']:.3f}) is not the top-left pad")
        for k, o in (("x", "y"), ("y", "x")):            # a single row (column): pin 1 at one of its ends
            if len({round(p[o], 3) for p in sig}) == 1 and len({p["num"] for p in sig}) >= 3 and \
                    not any(abs(p[k] - min(q[k] for q in sig)) < EPS or abs(p[k] - max(q[k] for q in sig)) < EPS for p in ones):
                add("warning", "pin1", f"pad {pin1} is in the middle of its row: pin 1 is normally at an end (check numbering)")

    # numbering direction: KiCad counts counter-clockwise (seen from the top) from pin 1
    areas = sorted(p["w"] * p["h"] for p in sig)
    typical = areas[len(areas) // 2] if areas else 0
    big = {p["num"] for p in sig if p["w"] * p["h"] > 3 * typical}      # exposed pads, tabs (and their vias)
    uniq = {}
    for p in sig:
        if p["num"] not in big:
            uniq.setdefault(p["num"], p)
    order = sorted((n for n in uniq if str(n).isdecimal()), key=int)
    if len(order) >= 3 and not _zigzag(order, uniq):
        mx = sum(uniq[n]["x"] for n in order) / len(order)
        my = sum(uniq[n]["y"] for n in order) / len(order)
        pts = [(uniq[n]["x"] - mx, uniq[n]["y"] - my) for n in order]
        xs, ys = {round(x, 3) for x, _ in pts}, {round(y, 3) for _, y in pts}
        if len(xs) > 1 and len(ys) > 1:              # skip single-row parts
            ang = [math.atan2(y, x) for x, y in pts]
            steps = [(b - a + math.pi) % (2 * math.pi) - math.pi for a, b in zip(ang, ang[1:])]
            if sum(1 for d in steps if d > 1e-6) > len(steps) // 2:      # y points down: counter-clockwise is negative
                add("error", "pin1", "pads are numbered clockwise; KiCad and IPC number counter-clockwise from pin 1 "
                                     "(seen from the top)")
            elif any(d > 1e-6 for d in steps):
                bad = next(order[i + 1] for i, d in enumerate(steps) if d > 1e-6)
                add("warning", "pin1", f"pad numbering doubles back at pad {bad}")

    # ---- BGA
    balls = [p for p in sig if BALL.match(p["num"])]
    row_y = {}
    for p in balls:
        row_y.setdefault(BALL.match(p["num"]).group(1), set()).add(round(p["y"], 2))
    if len(balls) >= 4 and len(balls) >= 0.9 * len(sig) and len(row_y) >= 2 \
            and len({round(p["x"], 2) for p in balls}) >= 2:      # a ball grid: each row letter is one row of pads
        ys_of = {}
        for p in balls:
            ys_of.setdefault(round(p["y"], 2), set()).add(BALL.match(p["num"]).group(1))
        one = sum(1 for v in row_y.values() if len(v) == 1) >= 0.8 * len(row_y) and \
            sum(1 for v in ys_of.values() if len(v) == 1) >= 0.8 * len(ys_of)
        if one:                                      # mostly one letter per row: a grid with (maybe) a misnamed ball
            letter = lambda p: BALL.match(p["num"]).group(1)
            cnt = {}
            for p in balls:
                cnt[(letter(p), round(p["y"], 2))] = cnt.get((letter(p), round(p["y"], 2)), 0) + 1
            y_of = {L: max(row_y[L], key=lambda y: cnt[(L, y)]) for L in row_y}                 # each letter's usual row
            l_of = {y: max(ys_of[y], key=lambda L: cnt[(L, y)]) for y in ys_of}                 # each row's usual letter
            mixed = sorted({p["num"] for p in balls if y_of[letter(p)] != round(p["y"], 2) or l_of[round(p["y"], 2)] != letter(p)},
                           key=lambda t: (len(t), t))
            if mixed:
                add("error", "bga", f"row letters and rows disagree at balls {', '.join(mixed[:8])}: a row letter on two "
                                    "rows, or two letters on one row (misnamed balls?)")
            _bga_rules(tree, balls, add)

    # ---- silk
    silk, silk_polys = _graphics(tree, "F.SilkS")
    for s in silk:
        if s[4] and not 0.1 - EPS <= s[4] <= 0.15 + EPS:
            add("warning", "silk", f"silk line width {s[4]} mm (KLC: 0.10-0.15)")
            break
    worst = None
    for s in silk:
        for p in cu + [dict(h, r=min(h["w"], h["h"]) / 2) for h in holes]:
            # a pad whose box is already farther than the worst distance so far cannot change the result
            lb = max(min(s[0], s[2]) - (p["x"] + p["w"] / 2), (p["x"] - p["w"] / 2) - max(s[0], s[2]),
                     min(s[1], s[3]) - (p["y"] + p["h"] / 2), (p["y"] - p["h"] / 2) - max(s[1], s[3]), 0) - s[4] / 2
            if worst is not None and lb >= worst[0]:
                continue
            d = _seg_rect_dist(s, p) - s[4] / 2
            if worst is None or d < worst[0]:
                worst = (d, p["num"])
    for pts, filled in silk_polys:
        if filled:
            hit = next((p for p in cu if _point_in_poly(p["x"], p["y"], pts)), None)
            if hit:
                add("error", "silk", f"a filled silkscreen shape covers pad {hit['num']}")
                worst = None
                break
    if worst and worst[0] < SILK_CLEAR - 0.011:
        # KiCad itself drops to 0.1 mm on tiny parts (0402 and below) where 0.2 mm would leave no silk at all
        small = cu and max(p["x"] + p["w"] / 2 for p in cu) - min(p["x"] - p["w"] / 2 for p in cu) < 1.7
        if worst[0] < SILK_CLEAR_SMALL - 0.011:
            add("error", "silk", f"silkscreen is {max(worst[0], 0):.3f} mm from pad {worst[1]} (KLC: >= {SILK_CLEAR} mm, "
                f"{SILK_CLEAR_SMALL} mm on tiny parts)")
        elif not small:
            add("warning", "silk", f"silkscreen is {worst[0]:.3f} mm from pad {worst[1]} (KLC: >= {SILK_CLEAR} mm)")

    # ---- fab
    fab, _ = _graphics(tree, "F.Fab")
    if not fab:
        add("warning", "fab", "no body outline on F.Fab")
    elif any(abs(s[4] - 0.1) > EPS for s in fab if s[4]):
        add("warning", "fab", "F.Fab line width is not 0.10 mm")

    # ---- courtyard
    crt, _ = _graphics(tree, "F.CrtYd")
    if not crt:
        add("error", "courtyard", "no courtyard on F.CrtYd")
    else:
        cb = _bbox(crt)
        ext = [(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2) for p in pads]
        fb = _bbox(fab)
        if fb:
            ext.append(fb)
        for e in ext:
            if e[0] < cb[0] - EPS or e[1] < cb[1] - EPS or e[2] > cb[2] + EPS or e[3] > cb[3] + EPS:
                add("error", "courtyard", "courtyard does not enclose every pad and the body outline")
                break
        crt_polys = [pts for pts, _ in _graphics(tree, "F.CrtYd")[1]]
        if crt_polys and not any(e[0] < cb[0] - EPS or e[2] > cb[2] + EPS for e in ext[:1]):
            for p in pads:                           # an outline that isn't a rectangle: every pad corner inside it
                corners = [(p["x"] + sx * (p["w"] / 2 - EPS), p["y"] + sy * (p["h"] / 2 - EPS)) for sx in (-1, 1) for sy in (-1, 1)]
                if not all(any(_point_in_poly(x, y, pts) for pts in crt_polys) for x, y in corners):
                    add("error", "courtyard", f"pad {p['num'] or '(hole)'} sticks out of the courtyard outline")
                    break
        if not _closed(crt):
            add("error", "courtyard", "courtyard is not a closed outline")
        if any(abs(s[4] - 0.05) > EPS for s in crt if s[4]):
            add("warning", "courtyard", "courtyard line width is not 0.05 mm")
        if any(abs(v * 100 - round(v * 100)) > 1e-3 for s in _graphics(tree, "F.CrtYd", raw_arcs=True)[0] for v in s[:4]):
            add("warning", "courtyard", "courtyard corners are not on the 0.01 mm grid")

    # ---- exposed-pad paste
    for p in cu:
        others = [q for q in cu if q is not p]
        if not others or p["w"] * p["h"] <= EPS or p["w"] * p["h"] < 3 * max(q["w"] * q["h"] for q in others):
            continue                                 # not an exposed pad
        if not any(abs(q["x"]) < p["w"] / 2 and abs(q["y"]) < p["h"] / 2 for q in others) and p["x"] == 0 and p["y"] == 0:
            windows = [q for q in pads if q["paste"] and not q["copper"]
                       and abs(q["x"] - p["x"]) < p["w"] / 2 and abs(q["y"] - p["y"]) < p["h"] / 2]
            # a solder-mask-defined land (TI PowerPAD) is pasted inside its mask-only opening: coverage of the opening
            opening = [q for q in pads if not q["copper"] and not q["num"] and {"F.Mask", "*.Mask"} & set(q["layers"])
                       and abs(q["x"] - p["x"]) + q["w"] / 2 <= p["w"] / 2 + EPS and abs(q["y"] - p["y"]) + q["h"] / 2 <= p["h"] / 2 + EPS]
            area = opening[0]["w"] * opening[0]["h"] if len(opening) == 1 and not any("Mask" in l for l in p["layers"]) else p["w"] * p["h"]
            if p["paste"] and not windows:
                cov = 1.0
            else:
                cov = sum(q["w"] * q["h"] for q in windows) / area
            if not 0.5 - EPS <= cov <= 0.9 + EPS:
                add("warning", "paste", f"exposed pad {p['num']} paste covers {cov:.0%} of its "
                    f"{'mask opening' if area != p['w'] * p['h'] else 'copper'} (typical 50-80%)")
    return issues


def ep_on_lead(text):
    """Pad numbers an exposed pad shares with a lead: a large pad near the middle of the leads (not a tab at one edge)
    with the number of a lead-sized pad that does not touch it. check() lets a large pad repeat a number (an exposed
    pad given its GND lead's number on purpose, SOT-223's tab and pin 2), so the release check judges these with the
    symbol's pin names. -> sorted numbers."""
    tree = sexpr.parse(text) if isinstance(text, str) else text
    cu = [p for p in _pad_rects(tree) if p["copper"] and p["num"]]
    if len(cu) < 3:
        return []
    areas = sorted(p["w"] * p["h"] for p in cu)
    typical = areas[len(areas) // 2]
    big = [p for p in cu if p["w"] * p["h"] > 3 * typical]
    leads = [p for p in cu if p["w"] * p["h"] <= 3 * typical]
    if not big or len(leads) < 2:
        return []
    x0, x1 = min(p["x"] for p in leads), max(p["x"] for p in leads)
    y0, y1 = min(p["y"] for p in leads), max(p["y"] for p in leads)
    inside = lambda p: x0 + EPS < p["x"] < x1 - EPS and y0 + EPS < p["y"] < y1 - EPS   # between rows of leads (a
    #                                                  single row's tab, TO-252 / TO-263 / SOT-223, never is)
    out = set()
    for e in big:
        if not inside(e):
            continue                                 # a tab past the leads (SOT-223, DPAK): numbered as a lead by design
        for p in leads:
            if p["num"] == e["num"] and _rect_gap(p, e) > EPS:
                out.add(e["num"])
    return sorted(out)


def perimeter_order(text):
    """How a perimeter part's pads are numbered against KiCad's rule (pin 1 top-left, counter-clockwise seen from the
    top), for the release check's numbering hold. -> None when the rule does not apply: fewer than 4 numbered leads,
    a single row, 2-row zig-zag (connector) numbering, lettered or BGA numbers, or leads of more than one size besides
    one longer pin-1 lead (a module, relay or power part whose datasheet numbers the pads where they fall). Else
    {"order": the lead numbers
    counter-clockwise from the top-left lead, "want": the numbers in order, "shift": how far round pin 1 sits from
    the top-left (0: there), "misplaced": [(number that belongs there, number there)] at the best rotation}.
    Exposed pads and tabs are left out, as check's numbering rule leaves them out."""
    tree = sexpr.parse(text) if isinstance(text, str) else text
    cu = [p for p in _pad_rects(tree) if p["copper"] and p["num"]]
    rx = mount_rx([p["num"] for p in cu])
    sig = [p for p in cu if not rx.fullmatch(p["num"])]
    if not sig or not all(str(p["num"]).isdecimal() for p in sig):
        return None
    areas = sorted(p["w"] * p["h"] for p in sig)
    typical = areas[len(areas) // 2]
    uniq = {}
    for p in sig:
        if p["w"] * p["h"] <= 3 * typical:
            uniq.setdefault(p["num"], p)
    leads = list(uniq.values())
    want = sorted(uniq, key=int)
    if len(want) < 4 or _zigzag(want, uniq):
        return None
    size = lambda p: tuple(sorted((round(p["w"], 2), round(p["h"], 2))))
    cx, cy = sum(p["x"] for p in leads) / len(leads), sum(p["y"] for p in leads) / len(leads)
    far = sorted(math.hypot(p["x"] - cx, p["y"] - cy) for p in leads)
    leads = [p for p in leads if math.hypot(p["x"] - cx, p["y"] - cy) >= far[len(far) // 2] / 2]   # a small centre pad
    want = sorted((p["num"] for p in leads), key=int)                                       # is no lead
    if len(want) < 4:
        return None
    sizes = [size(p) for p in leads]
    if len(leads) - max(sizes.count(s) for s in sizes) > 1:      # one odd lead allowed: a longer pin-1 marker lead
        return None
    mx, my = sum(p["x"] for p in leads) / len(leads), sum(p["y"] for p in leads) / len(leads)
    if len({round(p["x"], 3) for p in leads}) == 1 or len({round(p["y"], 3) for p in leads}) == 1:
        return None
    left = min(p["x"] for p in leads)
    top = min(p["y"] for p in leads if abs(p["x"] - left) < EPS)
    start = math.atan2(top - my, left - mx)
    ang = lambda p: (start - math.atan2(p["y"] - my, p["x"] - mx)) % (2 * math.pi)   # y down: counter-clockwise
    order = [p["num"] for p in sorted(leads, key=lambda p: (round(ang(p), 6), p["y"]))]
    n = len(want)
    best = min(range(n), key=lambda s: (sum(order[(i + s) % n] != want[i] for i in range(n)), s))
    rot = [order[(i + best) % n] for i in range(n)]
    return {"order": order, "want": want, "shift": best,
            "misplaced": [(w, g) for w, g in zip(want, rot) if w != g]}


def _zigzag(order, uniq):
    """Dual-row connector numbering: odd pins in one row, even pins in the other (1 3 5 / 2 4 6)."""
    for k in ("x", "y"):
        odd = {round(uniq[n][k], 3) for n in order if int(n) % 2}
        even = {round(uniq[n][k], 3) for n in order if not int(n) % 2}
        if len(odd) == 1 and len(even) == 1 and odd != even:
            return True
    return False


def _pitch(groups):
    """The most common step between neighbouring balls of the same row (or column)."""
    counts = {}
    for vals in groups:
        vals = sorted(vals)
        for a, b in zip(vals, vals[1:]):
            if b - a > EPS:
                counts[round(b - a, 3)] = counts.get(round(b - a, 3), 0) + 1
    return max(counts, key=lambda d: (counts[d], -d)) if counts else None


def _a1_spot(balls):
    """Where ball A1 is: the ball, or, when A1 is depopulated (some BGAs leave the index corner empty), the crossing of
    row A and column 1 when both have balls. None when there is no row A or no column 1."""
    a1 = next((p for p in balls if p["num"] == "A1"), None)
    if a1 is not None:
        return a1
    row_a = [p for p in balls if BALL.match(p["num"]).group(1) == "A"]
    col_1 = [p for p in balls if BALL.match(p["num"]).group(2) == "1"]
    if not row_a or not col_1:
        return None
    return {"num": "A1", "x": col_1[0]["x"], "y": row_a[0]["y"], "w": 0, "h": 0}


def _bga_rules(tree, balls, add):
    a1 = _a1_spot(balls)
    if a1 is None:
        add("error", "bga", "ball grid without a ball A1")
        return
    rows_x, cols_y = {}, {}
    for p in balls:                                  # steps inside each row / column, not between all coordinates
        rows_x.setdefault(round(p["y"], 2), []).append(p["x"])
        cols_y.setdefault(round(p["x"], 2), []).append(p["y"])
    px, py = _pitch(rows_x.values()), _pitch(cols_y.values())
    xs = sorted({round(p["x"], 4) for p in balls})
    ys = sorted({round(p["y"], 4) for p in balls})
    pitch = min(v for v in (px, py) if v) if (px or py) else None
    if pitch:
        px, py = px or pitch, py or pitch
        x0 = min(xs, key=lambda x: sum(abs((q - x) / px - round((q - x) / px)) for q in xs))     # the grid origin most balls share
        y0 = min(ys, key=lambda y: sum(abs((q - y) / py - round((q - y) / py)) for q in ys))
        off = [p["num"] for p in balls if abs((p["x"] - x0) / px - round((p["x"] - x0) / px)) * px > 0.01
               or abs((p["y"] - y0) / py - round((p["y"] - y0) / py)) * py > 0.01]
        if off:
            add("error", "bga", f"balls {', '.join(off[:6])} are off the {pitch:g} mm grid")
        d = min(min(p["w"], p["h"]) for p in balls)
        ratio = d / pitch
        if ratio > 0.65:
            add("warning", "bga", f"land {d:g} mm is {ratio:.0%} of the {pitch:g} mm pitch: only {pitch - d:.3f} mm between "
                                  "pads, no room to route between balls (typical 40-55 %)")
        elif ratio < 0.35:
            add("warning", "bga", f"land {d:g} mm is only {ratio:.0%} of the {pitch:g} mm pitch: check the ball/land diameter")
    if abs(a1["x"] - xs[0]) > EPS or abs(a1["y"] - ys[0]) > EPS:
        add("warning", "bga", f"ball A1 at ({a1['x']:.3f}, {a1['y']:.3f}) is not the top-left ball")
    # handedness: A1->A2 (columns) and A1->B1 (rows) must turn like the top view; a rotation keeps it, a mirror flips it
    by_row, by_col = {}, {}
    for p in balls:
        L, c = BALL.match(p["num"]).groups()
        by_row.setdefault(L, []).append((int(c), p))
        by_col.setdefault(int(c), []).append(((len(L), L), p))
    cv, rv = [0.0, 0.0], [0.0, 0.0]
    for groups, v in ((by_row, cv), (by_col, rv)):
        for g in groups.values():
            g.sort(key=lambda t: t[0])
            v[0] += g[-1][1]["x"] - g[0][1]["x"]
            v[1] += g[-1][1]["y"] - g[0][1]["y"]
    cross = cv[0] * rv[1] - cv[1] * rv[0]            # y points down: the top view (A1 top-left, 1.. right, A.. down) is > 0
    if cross < -0.5 * math.hypot(*cv) * math.hypot(*rv):
        add("error", "bga", "ball map is mirrored (rows or columns run the wrong way: a bottom-view drawing?); "
                            "no rotation of the part fits it")
    rows = {BALL.match(p["num"]).group(1) for p in balls}
    bad = sorted(r for r in rows if set(r) & set("IOQSXZ"))
    if bad:
        add("warning", "bga", f"row letters {', '.join(bad)}: JEDEC skips I, O, Q, S, X and Z")
    # A1 corner marks: silk (a filled shape or a gap near the corner) and fab (a chamfer)
    cx, cy = (xs[0] + xs[-1]) / 2, (ys[0] + ys[-1]) / 2
    sgn = (1 if a1["x"] > cx else -1, 1 if a1["y"] > cy else -1)
    for layer, what in (("F.SilkS", "silkscreen"), ("F.Fab", "fab")):
        segs, polys = _graphics(tree, layer)
        bb = _bbox(segs)
        if not bb:
            add("warning", "bga", f"no {what} outline to mark ball A1 on")
            continue
        corner = (bb[2] if sgn[0] > 0 else bb[0], bb[3] if sgn[1] > 0 else bb[1])
        reach = max(1.5, 0.15 * min(bb[2] - bb[0], bb[3] - bb[1]))
        near = lambda x, y: math.hypot(x - corner[0], y - corner[1]) <= reach
        diag = any(abs(s[0] - s[2]) > EPS and abs(s[1] - s[3]) > EPS and near(s[0], s[1]) and near(s[2], s[3]) for s in segs)
        filled = any(f and near(sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)) for pts, f in polys)
        others = []
        for sx_, sy_ in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            c2 = (bb[2] if sx_ > 0 else bb[0], bb[3] if sy_ > 0 else bb[1])
            others.append(any(math.hypot(x - c2[0], y - c2[1]) < 0.3 for s in segs for x, y in ((s[0], s[1]), (s[2], s[3]))))
        k = [(-1, -1), (1, -1), (1, 1), (-1, 1)].index(sgn)
        gap = not others[k] and sum(others) == 3     # outline closed at three corners, open at A1's
        if layer == "F.SilkS" and not (filled or gap or diag):
            add("warning", "bga", "ball A1 is not marked on the silkscreen (a mark or gap at its corner)")
        if layer == "F.Fab" and not (diag or filled):
            add("warning", "bga", "ball A1 corner is not marked on the fab layer (chamfer the body outline there)")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="ecadlib check", description="Rule-check KiCad SMD footprints (KLC-style)")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    bad = 0
    for f in a.files:
        issues = check(Path(f).read_text(encoding="utf-8"))          # footprint_gen and KiCad write UTF-8
        bad += any(l == "error" for l, _, _ in issues)
        print(f"{'OK  ' if not issues else 'WARN' if all(l == 'warning' for l, _, _ in issues) else 'FAIL'} {f}")
        for lvl, rule, msg in issues:
            print(f"     {lvl:<7} {rule:<9} {msg}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
