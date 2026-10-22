"""Harder footprints for footprint_gen: ball grids, pin headers, connectors and odd shapes.

package_type (package_schema.json), all built by build() below and reached through footprint_gen.build():

  bga         BGA / LGA / CSP / WLCSP: rows x cols balls at pitch (pitch_y), lettered A, B, ... from the top
              skipping I O Q S X Z (JEDEC JEP95), then AA, AB, ...; numbered 1.. from the left. Missing balls from
              `depopulated` (names or ranges "E5:H8") or a `ball_map` (one string per row, "-" or "." = no ball).
              Land: the datasheet's land_diameter, else IPC-7351B from the nominal ball size (collapsing balls
              shrink 25 % from 0.55 mm up, 20 % from 0.25 mm, 15 % below; non-collapsing grow 5 %; LGA lands
              = terminal). NSMD (copper = land, mask opening 0.05 bigger all round) or SMD (copper 0.05 bigger,
              mask opening = land). Courtyard = body + 2.0 / 1.0 / 0.5 mm (density A / B / C, KLC uses 1.0).
  pin_header  1 or 2 columns (cols) of rows pins at pitch. Through-hole: pin 1 at the origin, square pad 1,
              round others (KiCad's drill/pad table per pitch unless drill / land_diameter are given). SMD:
              pads from `land` (or KiCad's table), single-row SMD headers alternate left/right from pin 1.
  connector   any connector from the datasheet's recommended PCB layout, as a pad_table (below): signal pads,
              mounting pads (MP), shield tabs (SH), NPTH locating holes, slots, keep-outs. THT pads without a
              size get KiCad's connector rule (pad = drill + 1.0, no wider than pitch - 0.8 across the row).
  custom      the escape hatch for odd shapes: a pad_table with polygon pads, slots, paste split into windows
              and thermal vias for big pads.

pad_table: the dimension drawing as a table, in the drawing's own frame so an LLM can copy it without arithmetic:
  unit (mm | mil | in), y_axis (up | down), view (top | bottom: bottom views are mirrored), origin (what the
  drawing's 0,0 is: center | pin1 | datum), center_at (the package centre in drawing coordinates when the origin is
  not the centre; default the middle of the pads), footprint_origin (center | pin1; default pin1 when there are
  through-hole pins, as KiCad does), default_pad, pads[], arrays[] (a row of equal pads: count, pitch dx/dy),
  body (rectangle) and keepouts[]. Everything is converted to mm, KiCad's y-down top view and the chosen origin.

check_pad_table() checks a table before it becomes copper: units (mils typed as mm), pad count against pin_count,
duplicate / missing numbers, overlapping pads, pitch regularity along each row, mirror symmetry (an odd pad out in
an otherwise symmetric pattern is usually a misread number), pin 1 at the end of its row and at 0,0 when the
drawing says the origin is pin 1, drill vs pad (annular ring). Errors stop build(); warnings land in the report.

Courtyard for connector / custom / pin_header: the union of every pad and the body (and the lead span of SMD
headers), each grown by the courtyard excess (0.5 mm for connectors, KLC F5.3) and rounded out to 0.01 mm, traced
as one rectilinear outline (falls back to the bounding box when the pieces don't touch).
"""
import math, re
from collections import Counter

UNIT = {"mm": 1.0, "mil": 0.0254, "in": 25.4}
JEDEC_SKIP = set("IOQSXZ")
LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in JEDEC_SKIP]          # 20 row letters
ALL_LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
# Pads that are not pins. KiCad's library names them MP (mounting) and SH (shield), holes "" (kicad-footprints f8440fa:
# 4400+ MP / SH pads; no MH / MTG / MOUNT / MECH / NPTH, kept because datasheet tables use them). Never a bare letter:
# H and S (and G, R, T) name BGA ball rows, audio-jack contacts (S = sleeve), rotary-encoder switch pins (S1, S2) and
# edge-connector fingers (H), all of them pins.
MOUNT = re.compile(r"^((MP|MH|SH|MTG|MOUNT|MECH|NPTH)\d*)?$", re.I)
PIN_ROLES = ("pin", "thermal")                                                    # numbered pads that are pins
BGA_EXCESS = {"A": 2.0, "B": 1.0, "C": 0.5}
CONN_EXCESS = {"A": 0.5, "B": 0.5, "C": 0.5}                                      # KLC F5.3: connectors 0.5 mm
CUSTOM_EXCESS = {"A": 0.5, "B": 0.25, "C": 0.1}
NSMD_MASK = 0.05                                     # NSMD mask opening this much bigger than the land, all round
SMD_GROW = 0.05                                      # SMD-defined: copper this much bigger, mask = land
TH_RING, TH_RING_MIN, TH_PAD_GAP = 0.5, 0.15, 0.8    # KiCad connector generator: annular ring, minimum, pad-to-pad
# KiCad pin header tables: pitch -> (drill, pad diameter) for through-hole, (pad length, width, centre) for SMD
HEADER_TH = {2.54: (1.0, 1.7), 2.0: (0.8, 1.35), 1.27: (0.65, 1.0), 1.0: (0.5, 0.85)}
HEADER_SMD = {2.54: (3.15, 1.0, 2.525), 2.0: (2.58, 1.0, 2.085), 1.27: (2.4, 0.74, 1.95), 1.0: (2.0, 0.5, 1.65)}
HEADER_SMD1 = {2.54: (2.51, 1.0, 1.655), 2.0: (2.35, 0.85, 1.175), 1.27: (3.0, 0.65, 1.5), 1.0: (1.75, 0.6, 0.875)}  # 1 row
GRID = 0.01
# IPC-7351B land approximation for collapsing BGA balls (nominal ball -> nominal land, mm): 25 % smaller from 0.55 mm,
# 20 % from 0.25 mm, 15 % below. Source: IPC-7351B section 14 (BGA), table "Collapsing ball land approximation", as
# reproduced in maker app notes; not re-checked online (ipc.org and the usual mirrors are blocked here). KiCad's
# library agrees where it names both sizes (Ball0.25 -> Pad0.2, Ball0.3 -> 0.25, Ball0.5 -> 0.4); it has no 0.17 mm ball.
IPC_BGA_COLLAPSING = ((0.75, 0.55), (0.65, 0.50), (0.60, 0.45), (0.55, 0.40), (0.50, 0.40), (0.45, 0.35), (0.40, 0.30),
                      (0.35, 0.30), (0.30, 0.25), (0.25, 0.20), (0.20, 0.17), (0.17, 0.15), (0.15, 0.13))


def _F():
    from . import footprint_gen
    return footprint_gen


def _dim(v):
    return _F().dim(v)


def _r4(x):
    return round(x + 0.0, 4)


# ---------------------------------------------------------------- BGA naming
def row_name(i, jedec=True):
    """0 -> A, 19 -> Y, 20 -> AA (JEDEC JEP95: I O Q S X Z are never used as row letters)."""
    L = LETTERS if jedec else ALL_LETTERS
    if i < len(L):
        return L[i]
    i -= len(L)
    return L[i // len(L)] + L[i % len(L)]


def ball_index(name, jedec=True, rows=200):
    """'AB12' -> (row, col) zero-based, or None."""
    m = re.fullmatch(r"([A-Z]{1,2})(\d+)", str(name).strip().upper())
    if not m:
        return None
    names = {row_name(i, jedec): i for i in range(rows)}
    return (names[m.group(1)], int(m.group(2)) - 1) if m.group(1) in names else None


def bga_balls(spec):
    """[(name, row, col)] of the populated balls, row-major from A1."""
    rows, cols = spec["rows"], spec["cols"]
    if not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 200 for v in (rows, cols)):
        raise ValueError(f"rows / cols must be whole numbers from 1 to 200 (got {rows!r} x {cols!r})")
    jedec = spec.get("row_letters", "jedec") == "jedec"
    if spec.get("ball_map"):
        bm = spec["ball_map"]
        if len(bm) != rows or any(len(r.replace(" ", "")) != cols for r in bm):
            raise ValueError(f"ball_map must have {rows} rows of {cols} characters (one per column, '-' or '.' = no ball); "
                             f"got {len(bm)} rows of {sorted({len(r.replace(' ', '')) for r in bm})}")
        have = {(i, j) for i, r in enumerate(bm) for j, ch in enumerate(r.replace(" ", "")) if ch not in "-._0"}
    else:
        have = {(i, j) for i in range(rows) for j in range(cols)}
    for item in spec.get("depopulated") or []:
        a, _, b = str(item).partition(":")
        ia, ib = ball_index(a, jedec, rows), ball_index(b or a, jedec, rows)
        if ia is None or ib is None or max(ia[0], ib[0]) >= rows or max(ia[1], ib[1]) >= cols:
            raise ValueError(f"depopulated {item!r}: not a ball of a {rows} x {cols} grid "
                             f"(rows {row_name(0, jedec)}..{row_name(rows - 1, jedec)}, columns 1..{cols})")
        for i in range(min(ia[0], ib[0]), max(ia[0], ib[0]) + 1):
            for j in range(min(ia[1], ib[1]), max(ia[1], ib[1]) + 1):
                have.discard((i, j))
    if not have:
        raise ValueError(f"no balls left: depopulated / ball_map remove every ball of the {rows} x {cols} grid "
                         "(check the ranges)")
    return [(f"{row_name(i, jedec)}{j + 1}", i, j) for i, j in sorted(have)]


def bga_land(spec):
    """(land diameter, rule text): the datasheet's land_diameter, else the IPC-7351B land for ball_diameter."""
    if spec.get("land_diameter"):
        return _dim(spec["land_diameter"])[1], "datasheet land"
    if not spec.get("ball_diameter"):
        raise ValueError("bga needs ball_diameter (or the datasheet's land_diameter)")
    return ipc_bga_land(_dim(spec["ball_diameter"])[1], spec.get("ball_type", "collapsing"))


def bga_body(spec, land=None):
    """(half x, half y, note): the body as the footprint draws it (x along the columns, y along the rows). A body the
    ball grid fits only turned, or whose long side runs across the grid's long side, is turned a quarter turn; note
    says so (else None). step_gen uses the same body so the 3D model sits on the courtyard and fab outline."""
    rows, cols, e = spec["rows"], spec["cols"], spec["pitch"]
    ey = spec.get("pitch_y") or e
    if land is None:
        land = bga_land(spec)[0]
    bx, by = _dim(spec["body_length"])[1] / 2, _dim(spec["body_width"])[1] / 2
    gx, gy = (cols - 1) * e / 2, (rows - 1) * ey / 2
    turned = None
    if (gx + land / 2 > bx or gy + land / 2 > by) and gx + land / 2 <= by and gy + land / 2 <= bx:
        # a datasheet gives the body as length x width and the ball grid as rows x columns with no shared axis
        # (TPS628436YKA: 3 x 2 at 0.35 mm, body 1.05 x 0.797): the grid fits the body turned a quarter turn
        bx, by = by, bx
        turned = f"body {2 * by:g} x {2 * bx:g} mm turned to {2 * bx:g} x {2 * by:g} to fit the {rows} x {cols} grid"
    elif (gx - gy) * (bx - by) < -1e-9 and gx + land / 2 <= by and gy + land / 2 <= bx:
        # the grid is long one way and the body the other (CY14U256LA: 6 x 8 balls, body read as 10 x 6 mm): the
        # long side of a BGA body runs with the long side of its ball grid
        bx, by = by, bx
        turned = f"body {2 * by:g} x {2 * bx:g} mm turned to {2 * bx:g} x {2 * by:g} to run with the {rows} x {cols} grid"
    return bx, by, turned


def ipc_bga_land(ball, ball_type="collapsing"):
    """IPC-7351B land diameter for a nominal ball (or LGA terminal) diameter. Returns (land, rule text)."""
    if ball_type == "lga":
        return round(ball, 3), "LGA land = terminal size"
    if ball_type == "non_collapsing":
        d = ball * 1.05
        return round(math.floor(d / 0.01 + 0.5) * 0.01, 2), "non-collapsing ball: land 5 % larger"
    red = 0.25 if ball >= 0.55 - 1e-9 else 0.20 if ball >= 0.25 - 1e-9 else 0.15
    hit = next((land for b, land in IPC_BGA_COLLAPSING if abs(b - ball) < 1e-6), None)
    if hit is not None:                                   # a ball size the IPC table lists: its land
        return hit, f"collapsing ball {ball:g} mm: land {round(red * 100)} % smaller (IPC-7351B table)"
    g = 0.05 if ball >= 0.25 - 1e-9 else 0.01             # between table sizes: the table's rounding (0.05 mm; 0.01 mm
    d = (math.floor(ball * (1 - red) / g + 0.5) if g > 0.01 else math.ceil(round(ball * (1 - red) / g, 6))) * g  # up below 0.25)
    return round(d, 3), f"collapsing ball {ball:g} mm: land {round(red * 100)} % smaller"


# ---------------------------------------------------------------- geometry helpers
def _rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)     # KiCad: CCW on screen, y down


def pad_bbox(p):
    """(x1, y1, x2, y2) of a pad's copper (rotation and polygons included)."""
    if p.get("poly"):
        xs, ys = [q[0] for q in p["poly"]], [q[1] for q in p["poly"]]
        return min(xs), min(ys), max(xs), max(ys)
    w, h, rot = p["w"], p["h"], p.get("rot", 0) % 180
    if abs(rot - 90) < 1e-6:
        w, h = h, w
    elif rot:
        c, s = abs(math.cos(math.radians(rot))), abs(math.sin(math.radians(rot)))
        w, h = w * c + h * s, w * s + h * c
    return p["x"] - w / 2, p["y"] - h / 2, p["x"] + w / 2, p["y"] + h / 2


def _pt_in_poly(x, y, pts):
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _seg_dist(px, py, a, b):
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def _pad_dist(px, py, p):
    """Distance from a point to a pad's copper outline (0 inside)."""
    if p.get("poly"):
        if _pt_in_poly(px, py, p["poly"]):
            return 0.0
        return min(_seg_dist(px, py, a, b) for a, b in zip(p["poly"], p["poly"][1:] + p["poly"][:1]))
    x1, y1, x2, y2 = pad_bbox(p)
    w, h = x2 - x1, y2 - y1
    r = min(w, h) / 2 if p["shape"] in ("circle", "oval") else _corner(p) if p["shape"] == "roundrect" else 0.0
    dx = max(abs(px - p["x"]) - (w / 2 - r), 0)
    dy = max(abs(py - p["y"]) - (h / 2 - r), 0)
    return max(math.hypot(dx, dy) - r, 0.0)


def _corner(p):
    return min(0.25, 0.25 * min(p["w"], p["h"]))


def _pad_gap(a, b):
    """Copper gap between two non-polygon pads (negative = overlap), their round ends and corners included, the same
    measure footprint_check uses on the written footprint. A pad turned by an odd angle is its square-cornered box."""
    from .footprint_check import _rect_gap

    def rect(p):
        x1, y1, x2, y2 = pad_bbox(p)
        w, h = x2 - x1, y2 - y1
        odd = abs(p.get("rot", 0) / 90 - round(p.get("rot", 0) / 90)) > 1e-6
        r = 0.0 if odd else min(w, h) / 2 if p["shape"] in ("circle", "oval") else _corner(p) if p["shape"] == "roundrect" else 0.0
        return {"x": p["x"], "y": p["y"], "w": w, "h": h, "r": r}
    return _rect_gap(rect(a), rect(b))


def _interior_point(pts):
    """A point well inside a polygon (centroid when it is inside, else the best of a grid)."""
    cx, cy = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
    edge = lambda x, y: min(_seg_dist(x, y, a, b) for a, b in zip(pts, pts[1:] + pts[:1]))
    best = (edge(cx, cy), cx, cy) if _pt_in_poly(cx, cy, pts) else (-1, cx, cy)
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    for i in range(1, 24):
        for j in range(1, 24):
            x = min(xs) + (max(xs) - min(xs)) * i / 24
            y = min(ys) + (max(ys) - min(ys)) * j / 24
            if _pt_in_poly(x, y, pts):
                d = edge(x, y)
                if d > best[0] * 1.5:
                    best = (d, x, y)
    if best[0] <= 0:
        raise ValueError("polygon pad has no inside (check its points)")
    return best


def _poly_area(pts):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def _rup(x):
    return round(math.ceil(round(x / GRID, 6)) * GRID, 2)


def _rdn(x):
    return round(math.floor(round(x / GRID, 6)) * GRID, 2)


def union_outline(rects):
    """Outer outline of the union of axis-aligned rectangles (holes filled) as a list of corner points, or None when
    the rectangles form more than one island."""
    xs = sorted({v for r in rects for v in (r[0], r[2])})
    ys = sorted({v for r in rects for v in (r[1], r[3])})
    nx, ny = len(xs) - 1, len(ys) - 1
    cov = [[False] * ny for _ in range(nx)]
    for x1, y1, x2, y2 in rects:
        i1, i2, j1, j2 = xs.index(x1), xs.index(x2), ys.index(y1), ys.index(y2)
        for i in range(i1, i2):
            for j in range(j1, j2):
                cov[i][j] = True
    # flood the outside (one cell of margin) so enclosed holes count as filled
    out = set()
    stack = [(-1, -1)]
    while stack:
        i, j = stack.pop()
        if (i, j) in out or not (-1 <= i <= nx and -1 <= j <= ny):
            continue
        if 0 <= i < nx and 0 <= j < ny and cov[i][j]:
            continue
        out.add((i, j))
        stack += [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]
    filled = lambda i, j: 0 <= i < nx and 0 <= j < ny and (i, j) not in out
    cells = [(i, j) for i in range(nx) for j in range(ny) if filled(i, j)]
    seen, stack = set(), [cells[0]]
    while stack:                                           # one island?
        c = stack.pop()
        if c in seen or not filled(*c):
            continue
        seen.add(c)
        stack += [(c[0] + 1, c[1]), (c[0] - 1, c[1]), (c[0], c[1] + 1), (c[0], c[1] - 1)]
    if len(seen) != len(cells):
        return None
    edges = {}                                             # directed boundary edges, filled cell on the right (y down)
    for i, j in cells:
        if not filled(i, j - 1):
            edges.setdefault((xs[i], ys[j]), []).append((xs[i + 1], ys[j]))
        if not filled(i + 1, j):
            edges.setdefault((xs[i + 1], ys[j]), []).append((xs[i + 1], ys[j + 1]))
        if not filled(i, j + 1):
            edges.setdefault((xs[i + 1], ys[j + 1]), []).append((xs[i], ys[j + 1]))
        if not filled(i - 1, j):
            edges.setdefault((xs[i], ys[j + 1]), []).append((xs[i], ys[j]))
    start = min(edges)
    loop, cur, prev_dir = [start], start, None
    total = sum(len(v) for v in edges.values())
    for _ in range(total):
        cands = edges[cur]
        if len(cands) > 1 and prev_dir:                    # pinch point: keep turning right (stay on this island)
            cands.sort(key=lambda q: _turn(prev_dir, (q[0] - cur[0], q[1] - cur[1])))
        nxt = cands.pop(0)
        prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
        cur = nxt
        if cur == start:
            break
        loop.append(cur)
    pts = []                                               # drop collinear corners
    for k, p in enumerate(loop):
        a, b = loop[k - 1], loop[(k + 1) % len(loop)]
        if (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0]) != 0:
            pts.append(p)
    return pts


def _turn(d0, d1):
    """Sort key at a pinch point: right turn (towards the filled side, y down) first, then straight, then left."""
    sg = lambda v: ((v[0] > 0) - (v[0] < 0), (v[1] > 0) - (v[1] < 0))
    a, b = sg(d0), sg(d1)
    cross = a[0] * b[1] - a[1] * b[0]
    return 0 if cross > 0 else 1 if cross == 0 else 2


# ---------------------------------------------------------------- pad table -> pads
def _num(v):
    return "" if v is None else str(v)


def _expand_table(t):
    """pad_table rows + arrays -> list of raw pad dicts (drawing units, drawing frame)."""
    base = dict(t.get("default_pad") or {})
    rows = []
    for i, p in enumerate(t.get("pads") or []):
        rows.append(dict(base, **{k: v for k, v in p.items() if v is not None}, _src=f"pads[{i}]"))
    for i, a in enumerate(t.get("arrays") or []):
        step = a.get("number_step", 1)
        first = a.get("first_number", 1)
        for k in range(a["count"]):
            p = dict(base, **{kk: v for kk, v in a.items() if v is not None and kk not in
                              ("count", "dx", "dy", "first_number", "number_step", "prefix", "x", "y")})
            p["number"] = f"{a.get('prefix', '')}{first + k * step}"
            p["x"], p["y"] = a["x"] + k * a.get("dx", 0), a["y"] + k * a.get("dy", 0)
            p["_src"] = f"arrays[{i}] #{k + 1}"
            rows.append(p)
    for p in rows:                                         # a polygon pad without x/y sits at the middle of its points
        if p.get("polygon"):
            xs, ys = [v["x"] for v in p["polygon"]], [v["y"] for v in p["polygon"]]
            p.setdefault("x", (min(xs) + max(xs)) / 2)
            p.setdefault("y", (min(ys) + max(ys)) / 2)
    return rows


def _role(p, mount=MOUNT):
    if p.get("role"):
        return p["role"]
    if p.get("type") == "npth":
        return "npth"
    n = _num(p.get("number"))
    if not n:
        return "npth" if p.get("type") == "npth" else "mount"
    if mount.fullmatch(n):
        return "shield" if n.upper().startswith("SH") else "mount"
    return "pin"


def table_layout(spec):
    """pad_table -> (pads in mm / KiCad frame, body rect or None, keepouts, info). Pads keep their source row (_src)."""
    t = spec["pad_table"]
    f = UNIT[t.get("unit", "mm")]
    raw = _expand_table(t)
    if not raw:
        raise ValueError("pad_table has no pads")
    for p in raw:                                          # polygon pads: extent from their points (position: _expand_table)
        if p.get("polygon"):
            xs, ys = [v["x"] for v in p["polygon"]], [v["y"] for v in p["polygon"]]
            p["_ext"] = (min(xs), min(ys), max(xs), max(ys))
        elif "x" not in p or "y" not in p:
            raise ValueError(f"{p['_src']} (pad {p.get('number')}): no x/y")
    sx =-1 if t.get("view", "top") == "bottom" else 1
    sy = -1 if t.get("y_axis", "up") == "up" else 1
    if t.get("center_at"):
        cx, cy = t["center_at"]["x"], t["center_at"]["y"]
    elif t.get("origin", "center") == "center":
        cx = cy = 0.0
    else:                                                  # the middle of the pads' extent
        ext = [p.get("_ext") or (p["x"] - (p.get("w") or p.get("d") or 0) / 2, p["y"] - (p.get("h") or p.get("d") or 0) / 2,
                                 p["x"] + (p.get("w") or p.get("d") or 0) / 2, p["y"] + (p.get("h") or p.get("d") or 0) / 2)
               for p in raw]
        cx = (min(e[0] for e in ext) + max(e[2] for e in ext)) / 2
        cy = (min(e[1] for e in ext) + max(e[3] for e in ext)) / 2
    mount = MOUNT                                          # H1, S2 .. are pins (balls, contacts), MP / SH are not
    X = lambda x: _r4(sx * (x - cx) * f)
    Y = lambda y: _r4(sy * (y - cy) * f)
    pads = []
    for p in raw:
        kind = {"smd": "smd", "tht": "thru_hole", "npth": "np_thru_hole"}[p.get("type") or ("tht" if p.get("drill") else "smd")]
        role = _role(dict(p, type={"thru_hole": "tht", "np_thru_hole": "npth"}.get(kind, kind)), mount)
        q = {"num": "" if kind == "np_thru_hole" else _num(p.get("number")), "kind": kind, "role": role,
             "x": X(p["x"]), "y": Y(p["y"]), "rot": (p.get("rotation", 0) or 0) * sx, "_src": p["_src"]}
        if p.get("polygon"):
            pts = [(X(v["x"]), Y(v["y"])) for v in p["polygon"]]
            if len(pts) < 3:
                raise ValueError(f"{p['_src']}: a polygon pad needs at least 3 points")
            xs, ys = [a for a, _ in pts], [b for _, b in pts]
            q.update(shape="custom", poly=pts, w=_r4(max(xs) - min(xs)), h=_r4(max(ys) - min(ys)), rot=0)
        else:
            d = p.get("d")
            w, h = (d, d) if d else (p.get("w"), p.get("h"))
            drill = p.get("drill")
            if (w is None or h is None) and kind == "np_thru_hole" and drill:
                w = h = drill
            if (w is None or h is None) and kind == "thru_hole" and drill:
                q["auto_size"] = True
                w = h = drill                               # sized later by tht_pad_size (needs the row direction)
            if w is None or h is None:
                raise ValueError(f"{p['_src']} (pad {p.get('number')}): no size (give w and h, or d)")
            q.update(w=_r4(w * f), h=_r4(h * f), shape=p.get("shape") or ("circle" if d else "roundrect" if kind == "smd"
                                                                             else "circle" if abs(w - h) < 1e-9 else "oval"))
            q["_auto_shape"] = not p.get("shape")
            if q["shape"] == "polygon":
                raise ValueError(f"{p['_src']}: shape polygon needs polygon points")
            if q["shape"] == "circle" and abs(q["w"] - q["h"]) > 1e-9:
                q["shape"] = "oval"
        if kind != "smd":
            dr = p.get("drill")
            if not dr:
                raise ValueError(f"{p['_src']} (pad {p.get('number')}): a {p.get('type')} pad needs a drill")
            q["drill"] = (_r4(dr * f), _r4((p.get("drill_h") or dr) * f))
        for k in ("mask_margin", "paste_margin"):
            if p.get(k) is not None:
                q[k] = _r4(p[k] * f)
        q["paste"] = p.get("paste", "none" if kind != "smd" else "full")
        for k in ("paste_coverage", "paste_grid", "thermal_vias"):
            if p.get(k) is not None:
                q[k] = p[k]
        if q.get("thermal_vias"):                          # vias in mm by default (as before); unit mil / in / table
            tv = dict(q["thermal_vias"])
            u = tv.pop("unit", "mm")
            g = f if u == "table" else UNIT[u]
            q["thermal_vias"] = {k: (_r4(v * g) if k in ("drill", "diameter", "pitch", "pitch_y") else v) for k, v in tv.items()}
        pads.append(q)
    body = None
    if t.get("body"):
        b = t["body"]
        xa, xb, ya, yb = X(b["x1"]), X(b["x2"]), Y(b["y1"]), Y(b["y2"])
        body = (min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb))
    elif spec.get("body_length") and spec.get("body_width"):
        bx, by = _dim(spec["body_length"])[1] / 2, _dim(spec["body_width"])[1] / 2
        # the body's length runs with the pin rows (27C256: a DIP table with its rows down y; D is the length, not the
        # width). A row is the pins sharing one x (down y) or one y (along x), not the pads' extent: a DIP-8's extent
        # is square and a SOT-223's lead span is longer than its pin row.
        pins = [q for q in pads if q["num"]]
        col = max(Counter(round(q["x"], 3) for q in pins).values(), default=0)
        row = max(Counter(round(q["y"], 3) for q in pins).values(), default=0)
        if (col > row and bx > by) or (row > col and by > bx):
            bx, by = by, bx
        body = (-bx, -by, bx, by)
    keepouts = []
    for k in t.get("keepouts") or []:
        if k.get("polygon"):
            pts = [(X(v["x"]), Y(v["y"])) for v in k["polygon"]]
        else:
            xa, xb, ya, yb = X(k["x1"]), X(k["x2"]), Y(k["y1"]), Y(k["y2"])
            pts = [(min(xa, xb), min(ya, yb)), (max(xa, xb), min(ya, yb)), (max(xa, xb), max(ya, yb)), (min(xa, xb), max(ya, yb))]
        keepouts.append({"pts": pts, "layers": k.get("layers") or ["F.Cu"], "rules": k.get("rules") or ["tracks", "vias", "pads", "copperpour"]})
    return pads, body, keepouts, {"unit": t.get("unit", "mm"), "factor": f, "center": (cx, cy)}


def tht_pad_size(pads, pitch=None):
    """KiCad's connector rule for through-hole pads given only a drill: drill + 2 x 0.5 mm, but no wider than
    pitch - 0.8 mm across the row (then oval), never less than a 0.15 mm ring. The spacing is measured per axis
    from the pads themselves; the pitch stands for any spacing within 10 % of it."""
    pins = [p for p in pads if p["kind"] == "thru_hole"]
    for p in pins:
        if not p.pop("auto_size", False):
            continue
        d = max(p["drill"])
        full = _r4(d + 2 * TH_RING)
        near = [(abs(q["x"] - p["x"]), abs(q["y"] - p["y"])) for q in pins if q is not p]
        # each axis by its nearest neighbour on that axis: along x the next pad in the row (dy 0), along y the next
        # row (dx 0; S28B-PHDSS 2 x 14 at 2 mm: KiCad 1.2 mm round, not a 1.2 x 1.7 oval). Not the pitch across the
        # rows: two rows 1.8 mm apart at 2.5 mm pitch got 1.7 mm pads, 0.1 mm of copper between the rows. A pad off
        # both axes (staggered rows) by its distance, either way. Never under the minimum ring: then the pad keeps
        # it and check_pad_table reports the clearance.
        # A neighbour about the pitch away is taken at the pitch: one pad drawn 0.05 off keeps the row's size, so the
        # pitch check still sees the row as one (and names the odd pad)
        ring = d + 2 * TH_RING_MIN
        lim = lambda g: _r4(max((pitch if pitch and abs(g - pitch) <= 0.1 * pitch else g) - TH_PAD_GAP, ring))
        ax = min((v[0] for v in near if v[1] < 1e-6), default=None)
        ay = min((v[1] for v in near if v[0] < 1e-6), default=None)
        diag = min((math.hypot(*v) for v in near if v[0] >= 1e-6 and v[1] >= 1e-6), default=None)
        w = min(full, lim(ax)) if ax is not None else full
        h = min(full, lim(ay)) if ay is not None else full
        if diag and lim(diag) < max(w, h):
            w, h = min(w, lim(diag)), min(h, lim(diag))
        if round((p.get("rot") or 0) / 90) % 2:            # w / h are in the pad's own frame: KiCad turns the pad by rot
            w, h = h, w
        p["w"], p["h"] = w, h
        p["shape"] = "circle" if abs(w - h) < 1e-9 else "oval"
    for p in pins:                                         # KiCad / KLC: pin 1 of a through-hole connector is (rounded) square
        if p["num"] == "1" and p["role"] == "pin" and p.pop("_auto_shape", False):
            p["shape"] = "roundrect"


# ---------------------------------------------------------------- consistency checks on a table
def check_pad_table(spec, pads=None):
    """Consistency checks on a pad table (mm, KiCad frame). Returns [{level, rule, message}]."""
    out = []
    add = lambda lvl, rule, msg: out.append({"level": lvl, "rule": rule, "message": msg})
    t = spec.get("pad_table") or {}
    if pads is None:
        pads = table_layout(spec)[0]
        tht_pad_size(pads, spec.get("pitch"))
    unit = t.get("unit", "mm")
    raw = _expand_table(t) if t else []
    sizes = [abs(v) for p in raw for k, v in p.items() if k in ("w", "h", "d", "drill") and isinstance(v, (int, float))]
    # mils typed as mm: everything 39 times too big. Not the pads' distance from the origin (a long mm connector drawn
    # from pin 1, DIN41612 at 86 mm, is still mm) and not the spacing alone (a 2-terminal 18650 holder has its two
    # 6 x 8 mm pads 77 mm apart). Any of: a pad or hole over 60; the table over 200 across (a 288-pin DIMM is 122 mm);
    # the closest two pads the spec's pitch in mils; the closest two pads over 10 apart, the table over 60 across and
    # pads more than a quarter of that spacing (mm parts that far apart have small pads: holders, axial leads).
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    at = sorted({(p["x"], p["y"]) for p in raw if num(p.get("x")) and num(p.get("y"))})
    gap = None                                             # the closest two pad centres
    for i, (ax, ay) in enumerate(at):
        for bx, by in at[i + 1:]:
            if gap is not None and bx - ax >= gap:
                break
            gap = math.hypot(bx - ax, by - ay) if gap is None else min(gap, math.hypot(bx - ax, by - ay))
    big = max(sizes, default=0)
    span = max(max(a) - min(a) for a in zip(*at)) if at else 0
    sp = spec.get("pitch")
    what = None
    if big > 60:
        what = f"pad / hole sizes up to {big:g}"
    elif span > 200:
        what = f"pads spread over {span:g}"
    elif gap and num(sp) and sp > 0 and abs(gap * UNIT["mil"] - sp) <= 0.03 * sp:
        what = f"the closest pads {gap:g} apart, the spec's {sp:g} mm pitch in mils,"
    elif gap and gap > 10 and span > 60 and big > 0.25 * gap:
        what = f"no two pads closer than {gap:g} (pads up to {big:g})"
    if raw and unit == "mm" and what:
        add("error", "units", f"pad_table {what} with unit mm: that looks like mils (set unit to mil)")
    if raw and unit == "mil" and sizes and max(sizes) < 4:
        add("warning", "units", f"pad_table unit is mil but no pad or hole is bigger than {max(sizes):g} mil "
                                f"({max(sizes) * 0.0254:.3f} mm): is the table in mm?")
    pins = [p for p in pads if p["role"] in PIN_ROLES]
    nums = [p["num"] for p in pins]
    distinct = sorted(set(nums), key=lambda s: (len(s), s))
    if spec.get("pin_count") and len(distinct) != spec["pin_count"]:
        add("error", "count", f"pin_count is {spec['pin_count']} but the table has {len(distinct)} numbered pins "
                              f"(mounting pads, shield tabs and holes not counted)")
    ints = sorted(int(n) for n in distinct if n.isdigit())
    if ints and len(ints) == len(distinct) and ints[0] >= 1:
        missing = sorted(set(range(1, ints[-1] + 1)) - set(ints))
        if missing:
            add("warning", "numbers", f"pin numbers skip {', '.join(map(str, missing[:10]))}{' ...' if len(missing) > 10 else ''}")
    same = [set(map(str, g)) for g in spec.get("same_net") or []]
    for n in sorted({n for n in nums if nums.count(n) > 1}):
        locs = [p for p in pins if p["num"] == n]
        same_place = lambda q, r: all(abs(q[k] - r[k]) < 1e-6 for k in ("x", "y", "w", "h"))
        if any(same_place(q, r) for i, q in enumerate(locs) for r in locs[i + 1:]):
            # a copy of the same pad (place and size): a misread row. Same number, same place, another size (an
            # exposed pad drawn twice for its thermal vias) is one net and is fine.
            add("error", "numbers", f"pad {n} is listed twice at the same place")
    if not ({"1", "A1"} & set(nums)):
        add("error", "pin1", "no pad numbered 1 (or A1)")
    # overlaps between different pads
    cu = [p for p in pads if p["kind"] != "np_thru_hole"]
    for i, a in enumerate(cu):
        for b in cu[i + 1:]:
            if a["num"] and a["num"] == b["num"]:
                continue
            if any({a["num"], b["num"]} <= g for g in same):
                continue
            if a.get("poly") or b.get("poly"):
                ax1, ay1, ax2, ay2 = pad_bbox(a)
                bx1, by1, bx2, by2 = pad_bbox(b)
                hit = (min(ax2, bx2) - max(ax1, bx1) > 0.005 and min(ay2, by2) - max(ay1, by1) > 0.005 and
                       min(_pad_dist(b["x"], b["y"], a), _pad_dist(a["x"], a["y"], b)) == 0)
            else:                                          # round and rounded corners: boxes that meet only at a
                hit = _pad_gap(a, b) < -0.005              # corner are not an overlap (NSJ12HF-1 G / S2: 0.198 mm)
            if hit:
                add("error", "overlap", f"pads {a['num'] or '(hole)'} ({a['_src']}) and {b['num'] or '(hole)'} ({b['_src']}) overlap: "
                                        "check their x/y (list same-net pads in same_net)")
    # drill vs pad
    for p in pads:
        if p["kind"] == "thru_hole":
            ring = min(p["w"] - p["drill"][0], p["h"] - p["drill"][1]) / 2
            if ring <= 0:
                add("error", "drill", f"pad {p['num']} ({p['_src']}): drill {p['drill'][0]:g} is not smaller than the pad")
            elif ring < TH_RING_MIN - 1e-6:
                add("warning", "drill", f"pad {p['num']} ({p['_src']}): annular ring {ring:.3f} mm < {TH_RING_MIN} mm")
    # pitch regularity along rows and columns of equal pads
    row_pitches = []
    for axis in (0, 1):
        key = "y" if axis == 0 else "x"
        along = "x" if axis == 0 else "y"
        groups = {}
        for p in pins:
            groups.setdefault((round(p[key], 2), round(p["w"], 3), round(p["h"], 3)), []).append(p)
        for g in groups.values():
            g = sorted({round(p[along], 4): p for p in g}.values(), key=lambda p: p[along])
            if len(g) < 3:
                continue
            steps = [round(b[along] - a[along], 4) for a, b in zip(g, g[1:])]
            counts = {}
            for st in steps:
                counts[round(st, 2)] = counts.get(round(st, 2), 0) + 1
            sp = spec.get("pitch")
            if sp and any(abs(st - sp) <= 0.011 for st in steps):
                base = sp                                  # the spec's pitch, when this row uses it
            else:
                base, cnt = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
                if cnt < 2:
                    continue                               # no clear pitch in this row
            off = lambda st: abs(st - round(st / base) * base)
            bad = [(a, b, st) for (a, b), st in zip(zip(g, g[1:]), steps)
                   if (st < 1.5 * base and abs(st - base) > 0.011) or (st >= 1.5 * base and 0.011 < off(st) < 0.1 * base)]
            for a, b, st in bad[:3]:
                add("warning", "pitch", f"pads {a['num']} and {b['num']} are {st:g} mm apart but the row pitch is {base:g} mm "
                                        f"({b['_src']}): misread coordinate?")
            row_pitches.append((base, g[0]["num"], g[-1]["num"]))
    if spec.get("pitch") and row_pitches and not any(abs(b - spec["pitch"]) <= 0.011 for b, _, _ in row_pitches):
        b, a, z = max(row_pitches, key=lambda r: r[0])
        add("warning", "pitch", f"no row of pads has the spec's {spec['pitch']:g} mm pitch (pads {a}..{z}: {b:g} mm)")
    # symmetry about the package centre
    cu_all = [p for p in pads if p["role"] != "npth"]
    if len(cu_all) >= 4 and t:
        best = None                                        # mirrored about the package centre (0, 0 before any origin shift)
        for name, fx, fy in (("left/right", -1, 1), ("top/bottom", 1, -1), ("180 deg", -1, -1)):
            if all(abs(fx * p["x"] - p["x"]) < 0.02 and abs(fy * p["y"] - p["y"]) < 0.02 for p in cu_all):
                continue                                   # every pad on the mirror line: says nothing
            odd = []
            for p in cu_all:
                if not any(abs(q["x"] - fx * p["x"]) < 0.02 and abs(q["y"] - fy * p["y"]) < 0.02 and
                           abs(pad_bbox(q)[2] - pad_bbox(q)[0] - (pad_bbox(p)[2] - pad_bbox(p)[0])) < 0.02 and
                           abs(pad_bbox(q)[3] - pad_bbox(q)[1] - (pad_bbox(p)[3] - pad_bbox(p)[1])) < 0.02 for q in cu_all):
                    odd.append(p)
            if best is None or len(odd) < len(best[1]):
                best = (name, odd)
        name, odd = best if best is not None else ("", [])   # every pad on every mirror line (all at one point)
        if odd and len(odd) <= max(2, len(cu_all) // 10):
            add("warning", "symmetry", f"the pads are {name} symmetric except " +
                ", ".join(f"pad {p['num'] or '(hole)'} at ({p['x']:g}, {p['y']:g}) ({p['_src']})" for p in odd[:4]) +
                ": probably a misread number")
        elif odd:
            add("info", "symmetry", "the pad pattern is not symmetric about the package centre (normal for many connectors; "
                                    "if this part is symmetric, check center_at / origin)")
    # pin 1 location
    p1 = next((p for p in pins if p["num"] in ("1", "A1")), None)
    if p1 is not None:
        row = [p for p in pins if abs(p["y"] - p1["y"]) < 0.01]
        col = [p for p in pins if abs(p["x"] - p1["x"]) < 0.01]
        ends = lambda g, k: abs(p1[k] - min(q[k] for q in g)) < 0.01 or abs(p1[k] - max(q[k] for q in g)) < 0.01
        mid_r, mid_c = len(row) >= 3 and not ends(row, "x"), len(col) >= 3 and not ends(col, "y")
        if (mid_r and (mid_c or len(col) == 1)) or (mid_c and len(row) == 1):     # a single row/column counts too
            add("warning", "pin1", f"pad {p1['num']} is in the middle of its row: pin 1 is normally at an end (check numbering)")
        if t.get("origin") == "pin1":
            src = next(r for r in raw if _num(r.get("number")) == p1["num"])
            if abs(src["x"]) > 1e-6 or abs(src["y"]) > 1e-6:
                add("error", "pin1", f"the drawing's origin is pin 1 but pad {p1['num']} is at ({src['x']:g}, {src['y']:g}) "
                                     f"{unit}: origin or pad coordinates misread")
    return out


# ---------------------------------------------------------------- S-expression output
def _n(x):
    return _F().n(x)


def _q(s):
    return _F().q(s)


def pad_sexpr(p):
    kind, shape = p["kind"], p["shape"]
    at = f"(at {_n(p['x'])} {_n(p['y'])}{' ' + _n(p['rot']) if p.get('rot') else ''})"
    if kind == "smd":
        lay = ["F.Cu"] + (["F.Paste"] if p.get("paste") == "full" else []) + ["F.Mask"]
    elif p.get("via"):
        lay = ["*.Cu"]
    else:
        lay = ["*.Cu", "*.Mask"]
    if p.get("layers"):
        lay = p["layers"]
    s = f'  (pad {_q(p["num"])} {kind} {shape} {at}'
    if shape == "custom":
        ax, ay, size = p["anchor"]
        s = f'  (pad {_q(p["num"])} {kind} custom (at {_n(ax)} {_n(ay)}) (size {_n(size)} {_n(size)})'
    else:
        s += f' (size {_n(p["w"])} {_n(p["h"])})'
    if p.get("drill"):
        dw, dh = p["drill"]
        s += f" (drill {_n(dw)})" if abs(dw - dh) < 1e-9 else f" (drill oval {_n(dw)} {_n(dh)})"
    s += " (layers " + " ".join(_q(l) for l in lay) + ")"
    if shape == "roundrect":
        s += f" (roundrect_rratio {_n(min(0.25, 0.25 / min(p['w'], p['h'])))})"
    if p.get("mask_margin") is not None:
        s += f" (solder_mask_margin {_n(p['mask_margin'])})"
    if p.get("paste_margin") is not None:
        s += f" (solder_paste_margin {_n(p['paste_margin'])})"
    if shape == "custom":
        ax, ay, _ = p["anchor"]
        pts = " ".join(f"(xy {_n(x - ax)} {_n(y - ay)})" for x, y in p["poly"])
        s += f"\n    (options (clearance outline) (anchor circle))\n    (primitives (gr_poly (pts {pts}) (width 0) (fill yes)))"
    return s + ")"


def _paste_windows(p, cov=0.65, grid=None):
    """Split a big pad's paste into windows of about 1.2 mm (KLC F6.3 style), coverage ~cov of the copper."""
    x1, y1, x2, y2 = pad_bbox(p)
    w, h = x2 - x1, y2 - y1
    gx = (grid[0] if isinstance(grid, list) else grid) if grid else max(1, min(6, math.ceil(w / 1.2 - 1e-9)))
    gy = (grid[1] if isinstance(grid, list) else grid) if grid else max(1, min(6, math.ceil(h / 1.2 - 1e-9)))
    area = _poly_area(p["poly"]) if p.get("poly") else w * h
    sw, sh = w * math.sqrt(cov) / gx, h * math.sqrt(cov) / gy
    wins = []
    for i in range(gx):
        for j in range(gy):
            cx, cy = x1 + (i + 0.5) * w / gx, y1 + (j + 0.5) * h / gy
            if p.get("poly"):                               # keep windows wholly inside the polygon
                corners = [(cx + a * sw / 2, cy + b * sh / 2) for a in (-1, 1) for b in (-1, 1)]
                if not all(_pt_in_poly(x, y, p["poly"]) and _pad_dist(x, y, p) == 0 for x, y in corners):
                    continue
            wins.append((_r4(cx), _r4(cy), round(sw, 3), round(sh, 3)))
    if p.get("poly") and wins:                              # scale up to reach the coverage over the polygon
        k = min(math.sqrt(cov * area / sum(a[2] * a[3] for a in wins)), w / gx / sw * 0.95, h / gy / sh * 0.95)
        trial = [(x, y, round(a * k, 3), round(b * k, 3)) for x, y, a, b in wins]
        if all(all(_pt_in_poly(x + s * a / 2, y + t * b / 2, p["poly"]) for s in (-1, 1) for t in (-1, 1)) for x, y, a, b in trial):
            wins = trial
    return wins, (gx, gy)


def _thermal_vias(p, tv, notes=None):
    """A grid of vias (same number as the pad) centred on the pad (polygon pads: on their anchor); vias that would
    stick out of the copper are left out and listed in notes."""
    skipped = []
    x1, y1, x2, y2 = pad_bbox(p)
    nx, ny = tv.get("nx", 2), tv.get("ny", 2)
    pitch = tv.get("pitch")
    dx = pitch or (x2 - x1) / nx
    dy = tv.get("pitch_y") or pitch or (y2 - y1) / ny
    out = []
    for i in range(nx):
        for j in range(ny):
            x, y = p["x"] + (i - (nx - 1) / 2) * dx, p["y"] + (j - (ny - 1) / 2) * dy
            d = tv.get("diameter", tv["drill"] + 0.3)
            ring = [(x + d / 2 * math.cos(k * math.pi / 4), y + d / 2 * math.sin(k * math.pi / 4)) for k in range(8)]
            if not all(_pad_dist(a, b, p) <= 1e-6 for a, b in ring + [(x, y)]):    # (a via may touch the edge)
                skipped.append((round(x, 3), round(y, 3)))
                continue
            out.append({"num": p["num"], "kind": "thru_hole", "shape": "circle", "x": _r4(x), "y": _r4(y), "w": d, "h": d,
                        "drill": (tv["drill"], tv["drill"]), "via": True, "role": "thermal_via", "_src": p["_src"]})
    if skipped and notes is not None:
        notes.append(f"pad {p['num']}: {len(skipped)} of {nx * ny} thermal vias left out (outside the copper at {skipped[:4]})")
    return out


def _clip_any(a, b, pads, c, min_len=0.2):
    """Pieces of the segment a-b that stay at least c from every pad (sampled every 0.01 mm)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(2, int(L / 0.01))
    lo_x, hi_x, lo_y, hi_y = min(a[0], b[0]) - c, max(a[0], b[0]) + c, min(a[1], b[1]) - c, max(a[1], b[1]) + c
    pads = [p for p in pads if not (pad_bbox(p)[0] > hi_x or pad_bbox(p)[2] < lo_x or pad_bbox(p)[1] > hi_y or pad_bbox(p)[3] < lo_y)]
    ok = []
    for k in range(n + 1):
        t = k / n
        x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        ok.append(all(_pad_dist(x, y, p) >= c for p in pads))
    pieces, start = [], None
    for k, v in enumerate(ok + [False]):
        if v and start is None:
            start = k
        if not v and start is not None:
            t0, t1 = start / n, (k - 1) / n
            if (t1 - t0) * L >= min_len:
                pieces.append(((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                               (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)))
            start = None
    return [((_r4(p[0]), _r4(p[1])), (_r4(q[0]), _r4(q[1]))) for p, q in pieces]


def _pin1_mark(p1, pads, box, c):
    """A filled silk triangle next to pin 1, outside the silk outline when possible, pointing at the pad."""
    from .footprint_check import _point_in_poly
    x1, y1, x2, y2 = pad_bbox(p1)
    s, depth = 0.24, 0.33
    cands = []
    for d in ((-1, 0), (0, -1), (0, 1), (1, 0)):
        for push in (True, False):
            if d[0]:
                edge = x1 if d[0] < 0 else x2
                ax = edge + d[0] * (c + 0.02)
                if push and box:
                    ax = min(ax, box[0] - 0.15) if d[0] < 0 else max(ax, box[2] + 0.15)
                tri = [(ax, p1["y"]), (ax + d[0] * depth, p1["y"] - s), (ax + d[0] * depth, p1["y"] + s)]
            else:
                edge = y1 if d[1] < 0 else y2
                ay = edge + d[1] * (c + 0.02)
                if push and box:
                    ay = min(ay, box[1] - 0.15) if d[1] < 0 else max(ay, box[3] + 0.15)
                tri = [(p1["x"], ay), (p1["x"] - s, ay + d[1] * depth), (p1["x"] + s, ay + d[1] * depth)]
            cands.append((0 if push else 1, math.hypot(tri[0][0] - p1["x"], tri[0][1] - p1["y"]), len(cands), tri))
    for *_, tri in sorted(cands):
        segs = list(zip(tri, tri[1:] + tri[:1]))
        clear = all(min(_pad_dist(x, y, p) for p in pads) >= c
                    for a, b in segs for x, y in ((a[0] + (b[0] - a[0]) * k / 20, a[1] + (b[1] - a[1]) * k / 20) for k in range(21)))
        if clear and not any(_point_in_poly(p["x"], p["y"], tri) for p in pads):
            return [(_r4(x), _r4(y)) for x, y in tri]
    return None


def _emit(spec, pads, body, rep, excess, extra_rects=(), keepouts=(), corner_mark=None, courtyard_union=True):
    """Common writer: fab (body + pin-1 chamfer), silk (body outline cut back from the pads + pin-1 mark),
    courtyard (union outline or rectangle), pads, keep-out zones. Returns kicad_mod text; fills rep."""
    F = _F()
    copper = pads
    p1 = next((p for p in pads if p["num"] in ("1", "A1") and p["role"] in PIN_ROLES), None) or \
        next((p for p in pads if p["role"] in PIN_ROLES), pads[0])
    # ---- courtyard
    rects = [pad_bbox(p) for p in pads] + ([body] if body else []) + list(extra_rects)
    grown = [(_rdn(r[0] - excess), _rdn(r[1] - excess), _rup(r[2] + excess), _rup(r[3] + excess)) for r in rects]
    bb = (min(r[0] for r in grown), min(r[1] for r in grown), max(r[2] for r in grown), max(r[3] for r in grown))
    outline = union_outline(grown) if courtyard_union and len(grown) > 1 else None
    if outline is None or len(outline) <= 4:
        outline = None
    rep["courtyard"] = (round(bb[2] - bb[0], 2), round(bb[3] - bb[1], 2))
    rep["courtyard_bbox"] = tuple(round(v, 2) for v in bb)
    rep["courtyard_shape"] = f"union outline, {len(outline)} corners" if outline else "rectangle"
    attr = "through_hole" if any(p["kind"] == "thru_hole" and p["role"] in PIN_ROLES and not p.get("via") for p in pads) \
        else "smd"                                         # KiCad: THT shield / mounting pads alone don't make it THT
    density = rep["density"]
    tags = " ".join(filter(None, [spec["package_type"], rep.get("ipc_family", ""), f"IPC-7351B density-{density}"]))
    top, bot = bb[1], bb[3]
    out = [f'(footprint {_q(spec["name"])} (version 20221018) (generator "ecadlib_footprint_gen")',
           '  (layer "F.Cu")',
           f'  (descr {_q(rep["descr"])})',
           f'  (tags {_q(tags)})',
           f'  (attr {attr})',
           F.text("reference", "REF**", _r4((bb[0] + bb[2]) / 2), _r4(top - 0.7), "F.SilkS"),
           F.text("value", spec["name"], _r4((bb[0] + bb[2]) / 2), _r4(bot + 0.7), "F.Fab")]
    if body:
        bx, by = (body[2] - body[0]) / 2, (body[3] - body[1]) / 2
        out.append(F.text("user", "${REFERENCE}", _r4((body[0] + body[2]) / 2), _r4((body[1] + body[3]) / 2), "F.Fab",
                          size=math.floor(100 * min(1.0, max(0.25, 0.5 * max(bx, by)))) / 100))
        # fab: body with the corner nearest pin 1 chamfered
        ch = min(1.0, 0.25 * min(body[2] - body[0], body[3] - body[1]))
        corners = [(body[0], body[1]), (body[2], body[1]), (body[2], body[3]), (body[0], body[3])]
        k = min(range(4), key=lambda i: math.hypot(corners[i][0] - p1["x"], corners[i][1] - p1["y"]))
        pts = []
        for i in range(4):
            if i != k:
                pts.append(corners[i]); continue
            prev, nxt = corners[i - 1], corners[(i + 1) % 4]
            u = lambda a, b: ((b[0] - a[0]) / (math.hypot(b[0] - a[0], b[1] - a[1]) or 1), (b[1] - a[1]) / (math.hypot(b[0] - a[0], b[1] - a[1]) or 1))
            d1, d2 = u(corners[i], prev), u(corners[i], nxt)
            pts += [(corners[i][0] + d1[0] * ch, corners[i][1] + d1[1] * ch), (corners[i][0] + d2[0] * ch, corners[i][1] + d2[1] * ch)]
        out.append(F.poly([(_r4(x), _r4(y)) for x, y in pts], "F.Fab", F.FAB_W))
    # ---- silk: body outline pushed out 0.11 mm, cut back from every pad (and hole) by 0.2 mm + half the line
    c = F.SILK_PAD_CLEAR + F.SILK_W / 2
    silk_box = None
    if body:
        o = F.SILK_FAB_OFFSET
        silk_box = (body[0] - o, body[1] - o, body[2] + o, body[3] + o)
        sb = silk_box
        box = [((sb[0], sb[1]), (sb[2], sb[1])), ((sb[2], sb[1]), (sb[2], sb[3])), ((sb[2], sb[3]), (sb[0], sb[3])), ((sb[0], sb[3]), (sb[0], sb[1]))]
        holes = [dict(p, shape="circle", w=p["drill"][0], h=p["drill"][1]) for p in pads if p["kind"] == "np_thru_hole"]
        for a, b in box:
            for s, e in _clip_any(a, b, copper + holes, c):
                out.append(F.line(s[0], s[1], e[0], e[1], "F.SilkS", F.SILK_W))
    if corner_mark is not None:                             # BGA: KiCad-style triangle outside the A1 corner of the silk
        out.append(F.poly(corner_mark, "F.SilkS", F.SILK_W, fill="solid"))
    else:
        tri = _pin1_mark(p1, copper, silk_box, c)
        if tri:
            out.append(F.poly(tri, "F.SilkS", F.SILK_W, fill="solid"))
        else:
            rep.setdefault("notes", []).append("no room for a pin-1 mark on the silkscreen")
    # ---- courtyard
    if outline:
        out.append(F.poly(outline, "F.CrtYd", F.CRT_W))
    else:
        out.append(F.rect(bb[0], bb[1], bb[2], bb[3], "F.CrtYd", F.CRT_W))
    # ---- pads, paste windows
    for p in pads:
        out.append(pad_sexpr(p))
        for (x, y, w, h) in p.get("windows", []):
            out.append(F.pad("", x, y, w, h, layers=("F.Paste",)))
    for k in keepouts:
        rules = set(k["rules"])
        ko = " ".join(f"({r} {'not_allowed' if r in rules else 'allowed'})" for r in ("tracks", "vias", "pads", "copperpour", "footprints"))
        pts = " ".join(f"(xy {_n(x)} {_n(y)})" for x, y in k["pts"])
        lay = " ".join(_q(l) for l in k["layers"])
        out.append(f'  (zone (net 0) (net_name "") (layers {lay}) (hatch edge 0.5) (connect_pads (clearance 0)) (min_thickness 0.25)'
                   f' (keepout {ko}) (fill (thermal_gap 0.5) (thermal_bridge_width 0.5)) (polygon (pts {pts})))')
    if spec.get("model_3d"):
        out.append(f'  (model {_q(spec["model_3d"])} (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
    out.append(")")
    return "\n".join(out) + "\n"


def _finish_pads(pads, notes=None):
    """Custom-pad anchors, paste windows and thermal vias."""
    out = []
    for p in pads:
        if p.get("poly"):
            d, ax, ay = _interior_point(p["poly"])
            p["anchor"] = (_r4(ax), _r4(ay), round(max(0.05, min(1.6 * d, 1.0)), 3))
            p["x"], p["y"] = _r4(ax), _r4(ay)
        if p["kind"] == "smd" and p.get("paste") == "grid":
            wins, g = _paste_windows(p, p.get("paste_coverage", 0.65), p.get("paste_grid"))
            p["windows"] = wins
        out.append(p)
        if p.get("thermal_vias"):
            out += _thermal_vias(p, p["thermal_vias"], notes)
    return out


# ---------------------------------------------------------------- builders
def _bga(spec, density):
    F = _F()
    rows, cols, e = spec["rows"], spec["cols"], spec["pitch"]
    ey = spec.get("pitch_y") or e
    balls = bga_balls(spec)
    if spec.get("pin_count") and spec["pin_count"] != len(balls):
        raise ValueError(f"pin_count is {spec['pin_count']} but the {rows} x {cols} grid minus depopulated balls leaves "
                         f"{len(balls)} balls (check depopulated / ball_map)")
    land, rule = bga_land(spec)
    if land < 0.05:
        raise ValueError(f"land {land:g} mm: ball_diameter / land_diameter must be a real size (> 0.05 mm)")
    style = spec.get("land_style", "NSMD")
    cu, mask = (land, NSMD_MASK) if style == "NSMD" else (round(land + 2 * SMD_GROW, 3), -SMD_GROW)
    smd_note = None
    if style == "SMD" and cu >= min(e, ey) - 0.1 and land < min(e, ey) - 0.1:
        # TPS628436YKA (0.35 mm WCSP, 0.2 mm land read as SMD-defined): the copper an SMD-defined land needs around its
        # mask opening doesn't fit the pitch, the land itself does. Fine-pitch WCSP lands are copper-defined in practice
        smd_note = (f"SMD-defined land {land:g} mm needs {cu:g} mm copper, too big for {min(e, ey):g} mm pitch: "
                    f"built copper-defined (NSMD) at {land:g} mm")
        style, cu, mask = "NSMD", land, NSMD_MASK
    if cu >= min(e, ey) - 0.1:                            # the copper, which SMD-defined lands make bigger than the land
        raise ValueError(f"land {land:g} mm{f' (copper {cu:g} mm, SMD-defined)' if cu != land else ''} leaves less than "
                         f"0.1 mm between pads at {min(e, ey):g} mm pitch: check ball_diameter / pitch (mils?)")
    pads = []
    for name, i, j in balls:
        p = {"num": name, "kind": "smd", "shape": "circle", "x": _r4((j - (cols - 1) / 2) * e), "y": _r4((i - (rows - 1) / 2) * ey),
             "w": cu, "h": cu, "role": "pin", "paste": "full", "mask_margin": mask, "_src": name}
        if style == "SMD":
            p["paste_margin"] = -SMD_GROW
        pads.append(p)
    bx, by, turned = bga_body(spec, land)
    gx, gy = (cols - 1) * e / 2, (rows - 1) * ey / 2
    if gx + land / 2 > bx or gy + land / 2 > by:
        raise ValueError(f"the {rows} x {cols} grid at {e:g} mm pitch ({2 * gx:.2f} x {2 * gy:.2f} mm) doesn't fit the "
                         f"{2 * bx:g} x {2 * by:g} mm body: rows/cols swapped or pitch misread?")
    body = (-bx, -by, bx, by)
    o = F.SILK_FAB_OFFSET
    sx, sy = bx + o, by + o
    mark = [(-sx, -sy), (-sx - 0.5, -sy), (-sx, -sy - 0.5)]
    nominal = f"{rows}x{cols} grid, {len(balls)} balls, pitch {e:g}{'/' + format(ey, 'g') if ey != e else ''} mm"
    rep = {"name": spec["name"], "type": "bga", "density": density, "ipc_family": "bga", "land": land, "land_rule": rule,
           "land_style": style, "copper": cu, "balls": len(balls), "grid": (rows, cols),
           "Z": round(2 * gx + cu, 4), "G": round(min(e, ey) - cu, 4), "X": cu}
    rep["descr"] = (f"{spec.get('description', spec['name'])} | {nominal}, {style} land {land:g} mm ({rule})"
                    + (f" | src {spec['source']['datasheet']}" if spec.get("source", {}).get("datasheet") else ""))
    for n in (turned, smd_note):
        if n:
            rep.setdefault("notes", []).append(n)
    txt = _emit(spec, pads, body, rep, spec.get("courtyard_excess", BGA_EXCESS[density]), corner_mark=mark, courtyard_union=False)
    return txt, rep, pads


def _header(spec, density):
    cols, rows, e = spec.get("cols", 1), spec["rows"], spec["pitch"]
    if cols not in (1, 2):
        raise ValueError("pin_header: cols is 1 or 2 (use connector with a pad_table for anything else)")
    n = rows * cols
    if spec.get("pin_count") and spec["pin_count"] != n:
        raise ValueError(f"pin_count is {spec['pin_count']} but {cols} x {rows} = {n}")
    key = min(HEADER_TH, key=lambda k: abs(k - e))
    pads, extra = [], []
    if spec.get("mount", "tht") == "tht":
        drill = _dim(spec["drill"])[1] if spec.get("drill") else HEADER_TH[key][0] if abs(key - e) < 0.01 else None
        table = abs(key - e) < 0.01 and abs(drill - HEADER_TH[key][0]) < 1e-6 if drill is not None else False
        dia = _dim(spec["land_diameter"])[1] if spec.get("land_diameter") else HEADER_TH[key][1] if table else None
        if drill is None:
            raise ValueError(f"no default drill for a {e:g} mm pitch header: give drill (and land_diameter)")
        if dia is None:                                 # a custom drill gets its own land, not the table's
            dia = round(min(drill + 2 * TH_RING, e - 0.5), 2)
        if (dia - drill) / 2 < TH_RING_MIN - 1e-6:
            raise ValueError(f"pin_header: drill {drill:g} mm leaves a {(dia - drill) / 2:.3f} mm ring in a {dia:g} mm land "
                             f"(need >= {TH_RING_MIN} mm): drill and land_diameter are in mm, not mils")
        for k in range(n):
            r, c = divmod(k, cols)
            pads.append({"num": str(k + 1), "kind": "thru_hole", "shape": "rect" if k == 0 else "circle", "x": _r4(c * e),
                         "y": _r4(r * e), "w": dia, "h": dia, "drill": (drill, drill), "role": "pin", "_src": f"pin {k + 1}"})
        cx, cy = (cols - 1) * e / 2, (rows - 1) * e / 2
        family = "pin_header_tht"
    else:
        land = spec.get("land")
        if land:
            ll, lw, lc = land["pad_length"], land["pad_width"], land["pad_center"]
        elif abs(key - e) < 0.01 and key in (HEADER_SMD1 if cols == 1 else HEADER_SMD):
            ll, lw, lc = (HEADER_SMD1 if cols == 1 else HEADER_SMD)[key]     # single-row headers have their own land
        else:
            raise ValueError(f"no default SMD land for a {e:g} mm header: give land (pad_length, pad_width, pad_center)")
        for k in range(n):
            if cols == 2:
                r, c = divmod(k, 2)
                side = -1 if c == 0 else 1
            else:                                          # single row SMD: alternate sides, pin 1 on pin1_side
                r = k
                side = (-1 if k % 2 == 0 else 1) * (1 if spec.get("pin1_side", "left") == "left" else -1)
            pads.append({"num": str(k + 1), "kind": "smd", "shape": "rect", "x": _r4(side * lc),
                         "y": _r4((r - (rows - 1) / 2) * e), "w": ll, "h": lw, "role": "pin", "paste": "full", "_src": f"pin {k + 1}"})
        cx, cy = 0.0, 0.0
        family = "pin_header_smd"
        if spec.get("lead_span"):
            ls = _dim(spec["lead_span"])[1] / 2
            extra.append((-ls, -(rows - 1) * e / 2 - lw / 2, ls, (rows - 1) * e / 2 + lw / 2))
    bl = _dim(spec["body_length"])[1] if spec.get("body_length") else cols * e if spec.get("mount", "tht") == "tht" else cols * e
    bw = _dim(spec["body_width"])[1] if spec.get("body_width") else rows * e
    body = (_r4(cx - bl / 2), _r4(cy - bw / 2), _r4(cx + bl / 2), _r4(cy + bw / 2))
    rep = {"name": spec["name"], "type": "pin_header", "density": density, "ipc_family": family}
    rep["descr"] = (f"{spec.get('description', spec['name'])} | {cols}x{rows} pins, pitch {e:g} mm"
                    + (f", drill {pads[0]['drill'][0]:g} mm, pad {pads[0]['w']:g} mm" if pads[0].get("drill") else
                       f", pads {pads[0]['w']:g}x{pads[0]['h']:g} mm"))
    txt = _emit(spec, pads, body, rep, spec.get("courtyard_excess", CONN_EXCESS[density]), extra_rects=extra)
    return txt, rep, pads


def table_geometry(spec):
    """pad_table -> (pads, body, keepouts, info, problems, notes) in the footprint's frame (origin applied, polygon
    anchors, paste windows and vias added). Raises ValueError when check_pad_table finds an error."""
    pads, body, keepouts, info = table_layout(spec)
    tht_pad_size(pads, spec.get("pitch"))
    problems = check_pad_table(spec, pads)
    errs = [p for p in problems if p["level"] == "error"]
    if errs:
        raise ValueError("the pad table doesn't add up: " + "; ".join(p["message"] for p in errs))
    t = spec["pad_table"]
    turned, body = _pin1_upper_left(pads, body, keepouts)
    has_th = any(p["kind"] == "thru_hole" and p["role"] in PIN_ROLES for p in pads)
    if t.get("footprint_origin", "pin1" if has_th else "center") == "pin1":
        p1 = next((p for p in pads if p["num"] in ("1", "A1") and p["role"] in PIN_ROLES), None)
        if p1 is None:
            raise ValueError("footprint_origin pin1 but there is no pad 1")
        ox, oy = p1["x"], p1["y"]
        for p in pads:
            p["x"], p["y"] = _r4(p["x"] - ox), _r4(p["y"] - oy)
            if p.get("poly"):
                p["poly"] = [(_r4(x - ox), _r4(y - oy)) for x, y in p["poly"]]
        if body:
            body = (_r4(body[0] - ox), _r4(body[1] - oy), _r4(body[2] - ox), _r4(body[3] - oy))
        for k in keepouts:
            k["pts"] = [(_r4(x - ox), _r4(y - oy)) for x, y in k["pts"]]
    notes = [turned] if turned else []
    pads = _finish_pads(pads, notes)
    return pads, body, keepouts, info, problems, notes


def _pin1_upper_left(pads, body, keepouts):
    """Turn a pad table whose pin 1 lies right of the pins' centre and not above it half a turn about that centre, so
    pin 1 sits upper left (IPC-7351B zero orientation, KLC F1.2). A half turn changes no connection, only the angle
    the part is placed at. S28B-PHDSS (live benchmark 2026-10-21): JST draws its side-entry layout with No. 1 at
    the right end of the lower row; KiCad, like any IPC library, has it upper left. Returns (note or None, body);
    pads and keepouts are changed in place."""
    pins = [p for p in pads if p["role"] in PIN_ROLES]
    p1 = next((p for p in pins if p["num"] in ("1", "A1")), None)
    if p1 is None or len(pins) < 2:
        return None, body
    xs, ys = [p["x"] for p in pins], [p["y"] for p in pins]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # y grows downwards here (KiCad): right of centre and not above it, or on the centre line and below it (a single
    # column, 1 x N, drawn with pin 1 at the bottom)
    if not (p1["x"] > cx + 1e-6 and p1["y"] >= cy - 1e-6 or abs(p1["x"] - cx) <= 1e-6 and p1["y"] > cy + 1e-6):
        return None, body
    for p in pads:
        p["x"], p["y"] = _r4(2 * cx - p["x"]), _r4(2 * cy - p["y"])
        if p.get("poly"):
            p["poly"] = [(_r4(2 * cx - x), _r4(2 * cy - y)) for x, y in p["poly"]]
    if body:
        body = (_r4(2 * cx - body[2]), _r4(2 * cy - body[3]), _r4(2 * cx - body[0]), _r4(2 * cy - body[1]))
    for k in keepouts:
        k["pts"] = [(_r4(2 * cx - x), _r4(2 * cy - y)) for x, y in k["pts"]]
    return "turned half a turn so pin 1 is upper left (IPC-7351B zero orientation); no connection changes", body



def _table(spec, density):
    pads, body, keepouts, info, problems, notes = table_geometry(spec)
    t_ = spec["package_type"]
    exc = spec.get("courtyard_excess", (CONN_EXCESS if t_ == "connector" else CUSTOM_EXCESS)[density])
    pins = len({p["num"] for p in pads if p["role"] in PIN_ROLES})
    rep = {"name": spec["name"], "type": t_, "density": density, "ipc_family": t_, "land_source": "datasheet",
           "table_checks": [p for p in problems if p["level"] != "error"],
           "units": f"{info['unit']} (x{info['factor']:g} to mm)", "notes": notes}
    rep["descr"] = (f"{spec.get('description', spec['name'])} | {pins} pins"
                    + (f", {sum(1 for p in pads if p['role'] in ('mount', 'shield'))} mounting/shield pads" if any(p['role'] in ('mount', 'shield') for p in pads) else "")
                    + (f" | src {spec['source']['datasheet']}" if spec.get("source", {}).get("datasheet") else "")
                    + " | land pattern from the datasheet drawing")
    txt = _emit(spec, pads, body, rep, exc, keepouts=keepouts)
    return txt, rep, pads


BUILDERS = {"bga": _bga, "pin_header": _header, "connector": _table, "custom": _table}


def build(spec, density=None):
    """(kicad_mod text, report) for the advanced package types."""
    density = density or spec.get("density", "B")
    txt, rep, pads = BUILDERS[spec["package_type"]](spec, density)
    rep["pads"] = [{"num": p["num"], "x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"], "kind": p["kind"], "shape": p["shape"],
                    **({"drill": p["drill"]} if p.get("drill") else {}), "role": p["role"]}
                   for p in pads if not p.get("via")]
    from . import footprint_check
    same = [list(map(str, g)) for g in spec.get("same_net") or []]
    rep["checks"] = [{"level": l, "rule": r_, "message": m} for l, r_, m in footprint_check.check(txt, same_net=same)]
    return txt, rep
