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
USB_C_STACKS = ({"A1", "A12", "B1", "B12"}, {"A4", "A9", "B4", "B9"})     # GND, VBUS: stacked on purpose


def _f(v):
    return float(v)


def _graphics(tree, layer):
    """Segments (x1, y1, x2, y2, width) on a layer, from fp_line / fp_rect / fp_poly / fp_circle bounding boxes."""
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
            if kind == "fp_arc":
                m = sexpr.first(g, "mid")
                pts = [(_f(s[1]), _f(s[2]))] + ([(_f(m[1]), _f(m[2]))] if m else []) + [(_f(e[1]), _f(e[2]))]
                segs += [(*a, *b, w) for a, b in zip(pts, pts[1:])]
                continue
            x1, y1, x2, y2 = _f(s[1]), _f(s[2]), _f(e[1]), _f(e[2])
            if kind == "fp_rect":
                c = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                segs += [(*a, *b, w) for a, b in zip(c, c[1:] + c[:1])]
            else:
                segs.append((x1, y1, x2, y2, w))
    return segs, polys


def _pad_rects(tree):
    out = []
    for p in sexpr.find(tree, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y, w, h = _f(at[1]), _f(at[2]), _f(size[1]), _f(size[2])
        rot = _f(at[3]) if len(at) > 3 else 0.0
        shape0 = p[3] if len(p) > 3 else "rect"
        poly = None
        if shape0 == "custom":                       # anchor + primitives: bounding box, and the polygon for exact gaps
            xs, ys = [-w / 2, w / 2], [-h / 2, h / 2]
            prims = [g for g in (sexpr.first(p, "primitives") or [])[1:] if isinstance(g, list)]
            for g in prims:
                for xy in sexpr.find(sexpr.first(g, "pts") or [], "xy"):
                    xs.append(_f(xy[1])); ys.append(_f(xy[2]))
            if len(prims) == 1 and prims[0][0] == "gr_poly" and not rot:
                poly = [(x + _f(xy[1]), y + _f(xy[2])) for xy in sexpr.find(sexpr.first(prims[0], "pts"), "xy")]
            x, y = x + (min(xs) + max(xs)) / 2, y + (min(ys) + max(ys)) / 2
            w, h = max(xs) - min(xs), max(ys) - min(ys)
        odd_angle = abs(rot / 90 - round(rot / 90)) > 1e-6
        if odd_angle:                                # any other angle: use the rotated pad's bounding box
            c, s_ = abs(math.cos(math.radians(rot))), abs(math.sin(math.radians(rot)))
            w, h = w * c + h * s_, w * s_ + h * c
        elif round(rot / 90) % 2:
            w, h = h, w
        layers = (sexpr.first(p, "layers") or ["layers"])[1:]
        shape = p[3] if len(p) > 3 else "rect"
        rr = sexpr.first(p, "roundrect_rratio")
        r = (_f(rr[1]) * min(w, h) if shape == "roundrect" and rr else
             min(w, h) / 2 if shape in ("circle", "oval") else 0.0)
        if odd_angle:
            r = 0.0                                  # the bounding box has square corners
        drill = sexpr.first(p, "drill")
        out.append({"num": p[1], "x": x, "y": y, "w": w, "h": h, "r": r, "layers": layers, "rot": rot, "kind": p[2], "poly": poly,
                    "shape": shape, "drill": drill is not None,
                    "copper": any(l.endswith(".Cu") for l in layers) and p[2] != "np_thru_hole",
                    "hole": p[2] == "np_thru_hole", "paste": any("Paste" in l for l in layers)})
    return out


def _rect_gap(a, b):
    """Copper gap between two pads. A roundrect is its inner rectangle grown by a disc of radius r,
    so the gap is the inner rectangles' distance minus both radii (exact for rounded corners)."""
    dx = abs(a["x"] - b["x"]) - (a["w"] + b["w"]) / 2
    dy = abs(a["y"] - b["y"]) - (a["h"] + b["h"]) / 2
    if dx < 0 and dy < 0:
        return max(dx, dy)                       # overlap (negative)
    ra, rb = a.get("r", 0.0), b.get("r", 0.0)
    return math.hypot(max(dx + ra + rb, 0), max(dy + ra + rb, 0)) - ra - rb


def _pt_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def _pt_pad(px, py, r):
    """Distance from a point to a pad's copper (0 inside); polygon pads exactly, others as rounded rectangles."""
    if r.get("poly"):
        if _point_in_poly(px, py, r["poly"]):
            return 0.0
        return min(_pt_seg(px, py, a, b) for a, b in zip(r["poly"], r["poly"][1:] + r["poly"][:1]))
    rad = r.get("r", 0.0)
    dx = max(abs(px - r["x"]) - (r["w"] / 2 - rad), 0)
    dy = max(abs(py - r["y"]) - (r["h"] / 2 - rad), 0)
    return max(math.hypot(dx, dy) - rad, 0)


def _poly_gap(a, b):
    """Copper gap when a polygon pad is involved: sample each outline against the other (negative = overlap)."""
    def outline(p):
        if p.get("poly"):
            return p["poly"]
        return [(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2), (p["x"] + p["w"] / 2, p["y"] - p["h"] / 2),
                (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2), (p["x"] - p["w"] / 2, p["y"] + p["h"] / 2)]
    best = 1e9
    for p, q in ((a, b), (b, a)):
        pts = outline(p)
        for u, v in zip(pts, pts[1:] + pts[:1]):
            n = max(2, int(math.hypot(v[0] - u[0], v[1] - u[1]) / 0.01))
            for i in range(n + 1):
                x, y = u[0] + (v[0] - u[0]) * i / n, u[1] + (v[1] - u[1]) * i / n
                d = _pt_pad(x, y, q)
                if d == 0.0 and (not q.get("poly") or _point_in_poly(x, y, q["poly"])) and \
                        (q.get("poly") or (abs(x - q["x"]) < q["w"] / 2 - EPS and abs(y - q["y"]) < q["h"] / 2 - EPS)):
                    return -1.0
                best = min(best, d)
    return best


def _seg_rect_dist(seg, r):
    """Distance from a segment to a pad's real outline (rounded corners included; 0 if they touch), by dense sampling."""
    x1, y1, x2, y2 = seg[:4]
    n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.005) + 1)
    best = 1e9
    for i in range(n + 1):
        t = i / n
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        if r.get("poly"):
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
    ints = sorted({int(n) for n in nums if str(n).isdigit()})
    out, i = [], 0
    while i < len(ints):
        j = i
        while j + 1 < len(ints) and ints[j + 1] == ints[j] + 1:
            j += 1
        out.append(f"{ints[i]}-{ints[j]}" if j > i else str(ints[i]))
        i = j + 1
    return ", ".join(out + sorted(str(n) for n in nums if not str(n).isdigit()))


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
    groups = [set(map(str, g)) for g in same_net or []]
    numset = set(nums)

    # ---- copper
    close = {}                                       # (level, gap) -> [(a, b)]: one finding per distinct gap
    for i, a in enumerate(cu):
        for b in cu[i + 1:]:
            if a["num"] and a["num"] == b["num"]:
                continue                             # same net (e.g. SOT-223 pin 2 + tab): no web needed
            tied = any({a["num"], b["num"]} <= g for g in groups) or _usb_c_stack(a["num"], b["num"], numset)
            g = _rect_gap(a, b)
            if g < MIN_WEB - EPS and (a.get("poly") or b.get("poly")):
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
    if pin1 is None:
        add("error", "pin1", "no pad numbered 1")
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

    # numbering direction: KiCad counts counter-clockwise (seen from the top) from pin 1
    areas = sorted(p["w"] * p["h"] for p in sig)
    typical = areas[len(areas) // 2] if areas else 0
    big = {p["num"] for p in sig if p["w"] * p["h"] > 3 * typical}      # exposed pads, tabs (and their vias)
    uniq = {}
    for p in sig:
        if p["num"] not in big:
            uniq.setdefault(p["num"], p)
    order = sorted((n for n in uniq if str(n).isdigit()), key=int)
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
    if len(balls) >= 4 and len(balls) >= 0.9 * len(sig) and len(row_y) >= 2 and all(len(v) == 1 for v in row_y.values()) \
            and len(set().union(*row_y.values())) == len(row_y) \
            and len({round(p["x"], 2) for p in balls}) >= 2:      # a ball grid: each row letter is one row of pads
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
        if any(abs(v * 100 - round(v * 100)) > 1e-3 for s in crt for v in s[:4]):
            add("warning", "courtyard", "courtyard corners are not on the 0.01 mm grid")

    # ---- exposed-pad paste
    for p in cu:
        others = [q for q in cu if q is not p]
        if not others or p["w"] * p["h"] < 3 * max(q["w"] * q["h"] for q in others):
            continue                                 # not an exposed pad
        if not any(abs(q["x"]) < p["w"] / 2 and abs(q["y"]) < p["h"] / 2 for q in others) and p["x"] == 0 and p["y"] == 0:
            windows = [q for q in pads if q["paste"] and not q["copper"]
                       and abs(q["x"] - p["x"]) < p["w"] / 2 and abs(q["y"] - p["y"]) < p["h"] / 2]
            if p["paste"] and not windows:
                cov = 1.0
            else:
                cov = sum(q["w"] * q["h"] for q in windows) / (p["w"] * p["h"])
            if not 0.5 - EPS <= cov <= 0.9 + EPS:
                add("warning", "paste", f"exposed pad {p['num']} paste covers {cov:.0%} of its copper (typical 50-80%)")
    return issues


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


def _bga_rules(tree, balls, add):
    a1 = next((p for p in balls if p["num"] == "A1"), None)
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
        issues = check(Path(f).read_text())
        bad += any(l == "error" for l, _, _ in issues)
        print(f"{'OK  ' if not issues else 'WARN' if all(l == 'warning' for l, _, _ in issues) else 'FAIL'} {f}")
        for lvl, rule, msg in issues:
            print(f"     {lvl:<7} {rule:<9} {msg}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
