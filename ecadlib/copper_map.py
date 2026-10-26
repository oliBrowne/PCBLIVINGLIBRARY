"""Copper of a footprint as a map of small cells, so two footprints' copper can be compared whatever shapes drew it.

parity compares pads one by one; this compares what the board gets on each copper layer: pads, custom-pad shapes and
footprint copper graphics together. A KiCad unfilled rectangle (four lines) and Altium's four tracks, a filled circle
and Altium's thick arc, a plain pad plus a touching polygon and one custom pad: the same copper, the same cells.

Every shape becomes polygons (KiCad frame, mm, y down); polygons are filled row by row on a grid. Two maps differ
where one has copper farther than a tolerance from any copper of the other."""
import math

ARC_STEP = 10.0                             # degrees per segment when an arc becomes a polyline


def rect(cx, cy, w, h, rot=0.0, radius=0.0):
    """A w x h rectangle (corners rounded by radius) at (cx, cy) turned counter-clockwise on screen (KiCad frame)."""
    r = max(0.0, min(radius, w / 2, h / 2))
    if r <= 1e-9:
        local = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    else:
        local = []
        for (ux, uy), a0 in (((w / 2 - r, -h / 2 + r), -90), ((w / 2 - r, h / 2 - r), 0), ((-w / 2 + r, h / 2 - r), 90),
                             ((-w / 2 + r, -h / 2 + r), 180)):
            local += [(ux + r * math.cos(math.radians(a0 + k * 15)), uy + r * math.sin(math.radians(a0 + k * 15)))
                      for k in range(7)]
    return [place(u, v, cx, cy, rot) for u, v in local]


def place(u, v, cx, cy, rot):
    """A pad-frame point -> footprint frame (KiCad, y down; rot counter-clockwise on screen, as parity._rot_pts)."""
    a = math.radians(rot)
    return cx + u * math.cos(a) + v * math.sin(a), cy - u * math.sin(a) + v * math.cos(a)


def disc(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def capsule(x1, y1, x2, y2, w):
    """A line of width w with round ends."""
    h = w / 2
    if h <= 1e-9:
        return []
    d = math.hypot(x2 - x1, y2 - y1)
    if d < 1e-9:
        return disc(x1, y1, h, 16)
    a = math.atan2(y2 - y1, x2 - x1)
    pts = [(x2 + h * math.cos(a + math.radians(t)), y2 + h * math.sin(a + math.radians(t))) for t in range(-90, 91, 20)]
    pts += [(x1 + h * math.cos(a + math.radians(t)), y1 + h * math.sin(a + math.radians(t))) for t in range(90, 271, 20)]
    return pts


def polyline(pts, w, closed=False):
    segs = list(zip(pts, pts[1:] + (pts[:1] if closed else [])))
    return [c for c in (capsule(*a, *b, w) for a, b in segs) if c]


def arc_points(cx, cy, r, a0, sweep):
    """Points along an arc from angle a0 (degrees, atan2 in the frame given) over sweep degrees."""
    n = max(2, int(abs(sweep) / ARC_STEP) + 1)
    return [(cx + r * math.cos(math.radians(a0 + sweep * k / (n - 1))), cy + r * math.sin(math.radians(a0 + sweep * k / (n - 1))))
            for k in range(n)]


def bbox(polys):
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def raster(polys, frame, step):
    """Cells (i, j) whose centre is inside any polygon (even-odd per polygon). frame: (x0, y0)."""
    x0, y0 = frame
    cells = set()
    for poly in polys:
        if len(poly) < 3:
            continue
        ys = [y for _, y in poly]
        j0, j1 = int(math.floor((min(ys) - y0) / step)), int(math.ceil((max(ys) - y0) / step))
        edges = list(zip(poly, poly[1:] + poly[:1]))
        for j in range(j0, j1 + 1):
            yc = y0 + (j + 0.5) * step
            xs = sorted(xa + (yc - ya) * (xb - xa) / (yb - ya) for (xa, ya), (xb, yb) in edges
                        if (ya <= yc < yb) or (yb <= yc < ya))
            for a, b in zip(xs[::2], xs[1::2]):
                i0, i1 = int(math.ceil((a - x0) / step - 0.5)), int(math.floor((b - x0) / step - 0.5))
                cells.update((i, j) for i in range(i0, i1 + 1))
    return cells


def compare(k_layers, a_layers, tol=0.03, max_cells=360000, soft=()):
    """{layer: [polygon]} for both sides -> [(layer, kicad-only box, altium-only box, area mm2, soft)] where they
    differ by more than tol (mm). Boxes in the KiCad frame, None when that side has nothing extra. soft: [(layer,
    box)] where a difference is a documented approximation (a pad the converter approximates): differences inside
    such a box come as their own entry with soft True, every other difference with soft False (R-FX9-1)."""
    every = [p for d in (k_layers, a_layers) for ps in d.values() for p in ps]
    box = bbox(every)
    if not box:
        return []
    span = max(box[2] - box[0], box[3] - box[1], 1e-3)
    step = max(tol / 3, span / math.sqrt(max_cells))
    frame = (box[0] - 2 * tol, box[1] - 2 * tol)
    reach = max(1, int(math.ceil(tol / step)))
    out = []
    for layer in sorted(set(k_layers) | set(a_layers)):
        k, a = raster(k_layers.get(layer, []), frame, step), raster(a_layers.get(layer, []), frame, step)
        extra = []
        for mine, other in ((k, a), (a, k)):
            lone = [c for c in mine - other if not any((c[0] + di, c[1] + dj) in other
                                                        for di in range(-reach, reach + 1) for dj in range(-reach, reach + 1))]
            extra.append(lone)
        out += _entries(layer, extra, frame, step, [b for l, b in soft if l == layer])
    return out


def _entries(layer, extra, frame, step, soft_boxes):
    """Lone cells of each side -> entries (hard first, then the soft ones inside soft_boxes)."""
    centre = lambda c: (frame[0] + (c[0] + 0.5) * step, frame[1] + (c[1] + 0.5) * step)
    inside = lambda c: any(b[0] <= centre(c)[0] <= b[2] and b[1] <= centre(c)[1] <= b[3] for b in soft_boxes)
    cell = lambda e: (round(frame[0] + min(i for i, _ in e) * step, 3), round(frame[1] + min(j for _, j in e) * step, 3),
                      round(frame[0] + (max(i for i, _ in e) + 1) * step, 3), round(frame[1] + (max(j for _, j in e) + 1) * step, 3))
    out = []
    for is_soft in (False, True):
        part = [[c for c in e if inside(c) == is_soft] for e in extra]
        if not any(len(e) > 2 for e in part):              # a speck at the tolerance edge is no copper
            continue
        out.append((layer, cell(part[0]) if len(part[0]) > 2 else None, cell(part[1]) if len(part[1]) > 2 else None,
                    round(sum(len(e) for e in part) * step * step, 4), is_soft))
    return out
