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
"""
import math, re, sys
from pathlib import Path

from . import sexpr

MIN_WEB, HARD_WEB, SILK_CLEAR, SILK_CLEAR_SMALL = 0.2, 0.1, 0.2, 0.1
EPS = 0.005


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
        out.append({"num": p[1], "x": x, "y": y, "w": w, "h": h, "r": r, "layers": layers, "rot": rot,
                    "copper": any(l.endswith(".Cu") for l in layers), "paste": any("Paste" in l for l in layers)})
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


def _seg_rect_dist(seg, r):
    """Distance from a segment to a pad's real outline (rounded corners included; 0 if they touch), by dense sampling."""
    x1, y1, x2, y2 = seg[:4]
    n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.005) + 1)
    best = 1e9
    for i in range(n + 1):
        t = i / n
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
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


def check(text, allow_duplicate_pads=None):
    """Return a list of (level, rule, message). Empty means every rule passed."""
    tree = sexpr.parse(text)
    issues = []
    add = lambda lvl, rule, msg: issues.append((lvl, rule, msg))
    pads = _pad_rects(tree)
    cu = [p for p in pads if p["copper"]]
    nums = [p["num"] for p in cu if p["num"]]

    # ---- copper
    close = {}                                       # (level, gap) -> [(a, b)]: one finding per distinct gap
    for i, a in enumerate(cu):
        for b in cu[i + 1:]:
            if a["num"] and a["num"] == b["num"]:
                continue                             # same net (e.g. SOT-223 pin 2 + tab): no web needed
            g = _rect_gap(a, b)
            if g < -EPS:
                add("error", "copper", f"pads {a['num']} and {b['num']} overlap by {-g:.3f} mm")
            elif g < MIN_WEB - EPS:
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

    # ---- pin 1 / numbering
    pin1 = "1" if "1" in nums else "A1" if "A1" in nums else None    # A1: BGA balls, USB-C and other lettered rows
    if pin1 is None:
        add("error", "pin1", "no pad numbered 1")
    dup = {n for n in nums if nums.count(n) > 1} - set(allow_duplicate_pads or ())
    for n in sorted(dup):
        if not any(p["num"] == n and p["w"] * p["h"] > 2 * min(q["w"] * q["h"] for q in cu) for p in cu):
            add("warning", "pin1", f"pad number {n} is used more than once")
    sig = [p for p in cu if p["num"]]
    if "1" in nums and len(sig) > 2:
        p1 = next(p for p in sig if p["num"] == "1")
        left = min(p["x"] for p in sig)
        if p1["x"] > left + EPS or p1["y"] > min(p["y"] for p in sig if abs(p["x"] - left) < EPS) + EPS:
            if not (abs(p1["x"]) < EPS and abs(p1["y"]) < EPS):
                add("warning", "pin1", f"pad 1 at ({p1['x']:.3f}, {p1['y']:.3f}) is not the top-left pad")

    # numbering direction: KiCad counts counter-clockwise (seen from the top) from pin 1
    areas = sorted(p["w"] * p["h"] for p in sig)
    typical = areas[len(areas) // 2] if areas else 0
    uniq = {}
    for p in sig:
        if p["w"] * p["h"] <= 3 * typical:           # leave out exposed pads and tabs
            uniq.setdefault(p["num"], p)
    order = sorted((n for n in uniq if str(n).isdigit()), key=int)
    if len(order) >= 3:
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

    # ---- silk
    silk, silk_polys = _graphics(tree, "F.SilkS")
    for s in silk:
        if s[4] and not 0.1 - EPS <= s[4] <= 0.15 + EPS:
            add("warning", "silk", f"silk line width {s[4]} mm (KLC: 0.10-0.15)")
            break
    worst = None
    for s in silk:
        for p in cu:
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
