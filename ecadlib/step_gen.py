"""Package-shaped 3D models as STEP files (ISO 10303-21, AP214), built from a package spec or a footprint name.

    python3 -m ecadlib.step_gen ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json -o out/SOIC-8.step [--height MM]
    python3 -m ecadlib.step_gen SOIC-8_3.9x4.9mm_P1.27mm -o out/SOIC-8.step        (a footprint name)
    python3 -m ecadlib.step_gen PinHeader_1x04_P2.54mm_Vertical -o out/hdr.step
    python3 -m ecadlib.step_gen --check out/SOIC-8.step [more.step ...]            (validate any STEP file)

Pure Python, no CAD kernel. Each model is a set of manifold solid B-reps with planar faces (round parts are
24-sided prisms), styled with STEP colours (dark body, silver leads, light pin-1 / polarity mark). Units mm.
Placement follows the KiCad library models, so a model lines up with the footprint at offset 0, rotation 0:
origin at the footprint origin, z = 0 on the board, x right, y up (KiCad footprint y is down), pin 1 where the
footprint has it (2-terminal parts: pin 1 at -x; dual/quad rows: pin 1 top left; through-hole: pin 1 at 0,0).

Families (package_schema.json `package_type`, else parsed from the footprint name):
  chip      R/C/L/LED chips: body + two metallised end caps (LED: white body, green cathode mark; MELF: round)
  molded    tantalum (CP_EIA), SMA/SMB/SMC, molded 2-terminal: body + terminals folded under it, polarity band
  gullwing  SOIC/SSOP/TSSOP/MSOP/SOT-23(-5/-6)/SOT-223/SOD-123/SOD-323: two-tier tapered body, gull-wing leads,
            tab lead (SOT-223), flat leads (lead_type flat, SOT-89); pin-1 dot or polarity band
  qfp       LQFP/TQFP/QFP: four rows of gull-wing leads
  qfn       QFN/DFN/SON: body, bottom terminals flush with the edge, exposed pad, mould between them
  TH        PinHeader_RxC_P2.54/2.00/1.27mm_Vertical, C/CP_Radial_D..mm_P..mm, R/D_Axial / D_DO-35/41/201AD
  bga       BGA/LGA/CSP: balls on the footprint_adv grid (depopulated balls left out) under a flat body, A1 dot
  connector connector / custom pad_table specs: box over the body, pins through the plated holes, flat SMD contacts
EasyEDA / LCSC names are read too: SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL (body L along the pin rows x W, pitch P, lead span
LS; gullwing, SOT-23-3/-5, QFP), QFN-32_L5.0-W5.0-P0.50-BL-EP3.4 (qfn/dfn), R0603 / C0402 / LED0603 (imperial chips).
Their footprints draw the pin rows along x (pin 1 bottom left), so those models are turned 90 degrees (spec rotation).
Also SOT-23_L.. / SOT-323_L.. (no pin count: the family's standard count), SOT-223(-3)_L.. (3 leads + tab) and
SOD-123F_L2.7-W1.6-LS3.8 (2 leads; F flat). Some EasyEDA footprints with these names are drawn turned another way, so
when the footprint's pads are known (generate(pads=), kicad_import.add_body) the model is turned to put each lead on
its pad (fit_rotation).
Height: --height, else the spec's height_max, else the CP_EIA-xxxx-HH suffix, else HEIGHT below, else the middle
of release_check.HEIGHTS for the family.

check() parses a STEP file (any producer) and verifies: every #reference resolves; each CLOSED_SHELL is closed
(every edge used by exactly two faces, in opposite directions); loops are connected; face vertices lie on their
PLANE and the loop turns the right way round it; LINE geometry passes through its vertices; each solid's
volume is positive; it reports the bounding box and which solids carry a colour.
kicad_import.add_body(gen_step=True) and the librarian embed these models where they would otherwise
extrude a plain box (the box stays the last resort)."""
import argparse, json, math, re, sys
from pathlib import Path

from .footprint_gen import dim

SPECS = Path(__file__).with_name("package_specs")
COL = {"body": (0.1, 0.1, 0.1), "metal": (0.82, 0.82, 0.85), "mark": (0.78, 0.78, 0.78), "mlcc": (0.62, 0.49, 0.33),
       "led": (0.95, 0.95, 0.92), "led_mark": (0.1, 0.6, 0.2), "tant": (0.88, 0.66, 0.12), "tant_mark": (0.45, 0.28, 0.08),
       "gold": (0.86, 0.7, 0.3), "ecap": (0.12, 0.14, 0.32), "resistor": (0.76, 0.66, 0.46), "inductor": (0.25, 0.25, 0.27)}
MARK_T = 0.01                                   # pin-1 / polarity mark thickness (the body top sits this much lower)
SEG = 24                                        # sides of a "round" prism
# overall height (mm) when neither the caller nor the spec gives one: first regex on the footprint name wins
HEIGHT = [(r"^R_0201", 0.26), (r"^R_0402", 0.35), (r"^R_0603", 0.45), (r"^R_0805", 0.5), (r"^R_(1206|1210)", 0.55), (r"^R_", 0.6),
          (r"^C_0201", 0.3), (r"^C_0402", 0.5), (r"^C_0603", 0.8), (r"^C_0805", 1.25), (r"^C_1206", 1.6), (r"^C_", 2.0),
          (r"^L_0402", 0.5), (r"^L_0603", 0.8), (r"^L_0805", 1.0), (r"^L_", 1.2), (r"^LED_0402", 0.4), (r"^LED_0603", 0.6),
          (r"^LED_0805", 0.8), (r"^LED_", 1.1), (r"^CP_EIA", 1.6), (r"^(D_)?SOD-?123", 1.17), (r"^(D_)?SOD-?323", 0.9),
          (r"^(D_)?SOD-?523", 0.6), (r"^(D_)?SM[ABC]\b", 2.3), (r"^SOT-223", 1.8), (r"^SOT-89", 1.5), (r"^SOT-?(323|363|353)", 0.95),
          (r"^SC-?70", 0.95), (r"^TSOT-23", 1.0), (r"^SOT-23", 1.1), (r"^(TSSOP|MSOP|VSSOP|HTSSOP|TSOP)", 1.1), (r"^SSOP", 1.75), (r"^(SOIC|[EH]?SOP|SO-)", 1.75),
          (r"(QFN|DFN|SON)", 0.8), (r"^LQFP", 1.6), (r"^TQFP", 1.2), (r"QFP", 2.0)]


# ---------------------------------------------------------------- vectors
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _len(a): return math.sqrt(_dot(a, a))
def _unit(a): return _mul(a, 1.0 / _len(a))


def _newell(pts):
    n = (0.0, 0.0, 0.0)
    for a, b in zip(pts, pts[1:] + pts[:1]):
        n = _add(n, ((a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])))
    return n


def _area2(poly):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1])) / 2


# ---------------------------------------------------------------- solids
class Solid:
    """Closed polyhedron: verts [(x, y, z)], faces [[outer loop, *hole loops]] of vertex indices, each loop
    counter-clockwise seen from outside the solid (holes clockwise)."""
    def __init__(self, name, verts, faces, color):
        self.name, self.verts, self.faces, self.color = name, verts, faces, color


def prism(poly, depth, origin=(0, 0, 0), u=(1, 0, 0), v=(0, 1, 0), holes=(), name="", color=COL["body"]):
    """2D polygon (in the u, v frame at origin) extruded by depth along u x v; holes are inner polygons."""
    w = _cross(u, v)
    poly = list(poly) if _area2(poly) > 0 else list(poly)[::-1]
    loops = [poly] + [list(h) if _area2(h) < 0 else list(h)[::-1] for h in holes]
    P = lambda p, d: _add(_add(_add(origin, _mul(u, p[0])), _mul(v, p[1])), _mul(w, d))
    verts, bot, top = [], [], []
    for lp in loops:
        bot.append(list(range(len(verts), len(verts) + len(lp)))); verts += [P(p, 0) for p in lp]
        top.append(list(range(len(verts), len(verts) + len(lp)))); verts += [P(p, depth) for p in lp]
    faces = [[t for t in top], [b[::-1] for b in bot]]
    for b, t in zip(bot, top):
        k = len(b)
        faces += [[[b[i], b[(i + 1) % k], t[(i + 1) % k], t[i]]] for i in range(k)]
    return Solid(name, verts, faces, color)


def box(x0, y0, z0, x1, y1, z1, name="", color=COL["body"]):
    return prism([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], z1 - z0, (0, 0, z0), name=name, color=color)


def loft(rings, zs, name="", color=COL["body"]):
    """Stack of horizontal polygons with the same vertex count; consecutive rings must have parallel edges
    (offset_poly of one another) so every side quad is planar."""
    rings = [r if _area2(r) > 0 else r[::-1] for r in rings]
    k, verts = len(rings[0]), []
    for r, z in zip(rings, zs):
        verts += [(x, y, z) for x, y in r]
    idx = lambda j, i: j * k + i % k
    faces = [[[idx(len(rings) - 1, i) for i in range(k)]], [[idx(0, i) for i in range(k)][::-1]]]
    for j in range(len(rings) - 1):
        faces += [[[idx(j, i), idx(j, i + 1), idx(j + 1, i + 1), idx(j + 1, i)]] for i in range(k)]
    return Solid(name, verts, faces, color)


def offset_poly(poly, d):
    """Move every edge of a counter-clockwise polygon inward by d (edges stay parallel)."""
    lines = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        t = (b[0] - a[0], b[1] - a[1]); L = math.hypot(*t); t = (t[0] / L, t[1] / L)
        nrm = (-t[1], t[0])
        lines.append(((a[0] + nrm[0] * d, a[1] + nrm[1] * d), t))
    out = []
    for (p1, t1), (p2, t2) in zip(lines[-1:] + lines[:-1], lines):
        den = t1[0] * t2[1] - t1[1] * t2[0]
        s = ((p2[0] - p1[0]) * t2[1] - (p2[1] - p1[1]) * t2[0]) / den
        out.append((p1[0] + t1[0] * s, p1[1] + t1[1] * s))
    return out


def rect(hx, hy, cx=0.0, cy=0.0):
    return [(cx - hx, cy - hy), (cx + hx, cy - hy), (cx + hx, cy + hy), (cx - hx, cy + hy)]


def circle(r, cx=0.0, cy=0.0, n=SEG):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def thick_polyline(pts, t):
    """Polygon around a 2D polyline, t thick, mitred corners."""
    def nrm(a, b):
        d = (b[0] - a[0], b[1] - a[1]); L = math.hypot(*d)
        return (-d[1] / L, d[0] / L)
    left, right = [], []
    for i, p in enumerate(pts):
        ns = [nrm(pts[i - 1], p)] if i else []
        ns += [nrm(p, pts[i + 1])] if i + 1 < len(pts) else []
        m = (sum(n[0] for n in ns), sum(n[1] for n in ns)); L = math.hypot(*m); m = (m[0] / L, m[1] / L)
        k = (t / 2) / max(0.25, m[0] * ns[0][0] + m[1] * ns[0][1])
        left.append((p[0] + m[0] * k, p[1] + m[1] * k)); right.append((p[0] - m[0] * k, p[1] - m[1] * k))
    return left + right[::-1]


def side_profile(profile, out_dir, lateral, width, name, color=COL["metal"]):
    """A lead: profile polygon in (s = distance from the centre along out_dir, z), `width` wide, centred at
    `lateral` (the pin's coordinate along the row: y for out_dir +-x, x for out_dir +-y)."""
    o = (out_dir[0], out_dir[1], 0.0)
    w = _cross(o, (0, 0, 1))
    c = lateral * (w[0] + w[1])                 # w is +-x or +-y: the pin's coordinate along w
    return prism(profile, width, _mul(w, c - width / 2), o, (0, 0, 1), name=name, color=color)


def mark_dot(x, y, r, z, name="pin 1 mark"):
    return prism(circle(r, x, y, 8), MARK_T, (0, 0, z), name=name, color=COL["mark"])


# ---------------------------------------------------------------- package builders
def _n(spec, k):
    return dim(spec[k])[1] if spec.get(k) is not None else None


def _kind(name):
    m = re.match(r"(LED|CP|R|C|L|D|F)(?:_|(?=\d{4}))", name or "")     # KiCad R_0603_..., EasyEDA R0603
    return m.group(1) if m else ""


def chip(spec, H):
    L, W = _n(spec, "body_length"), _n(spec, "body_width")
    T = _n(spec, "terminal_length") or (L - _n(spec, "inner_span")) / 2
    kind, pol = _kind(spec["name"]), bool(spec.get("polarized"))
    out = []
    if spec.get("lead_type") == "melf":         # round body and caps along x
        r = min(W, H) / 2
        cyl = lambda x0, x1, rr, nm, col: prism(circle(rr, 0, r), x1 - x0, (x0, 0, 0), (0, 1, 0), (0, 0, 1), name=nm, color=col)
        out += [cyl(-L / 2, -L / 2 + T, r, "terminal 1", COL["metal"]), cyl(L / 2 - T, L / 2, r, "terminal 2", COL["metal"])]
        bx0, bx1 = -L / 2 + T, L / 2 - T
        if pol:
            band = bx0 + 0.25 * (bx1 - bx0)
            out += [cyl(bx0, band, r * 0.97, "cathode band", COL["mark"]), cyl(band, bx1, r * 0.97, "body", COL["body"])]
        else:
            out.append(cyl(bx0, bx1, r * 0.97, "body", COL["resistor"] if kind == "R" else COL["body"]))
        return out
    e = min(0.025, 0.04 * min(W, H))            # body sits slightly inside the caps
    out += [box(-L / 2, -W / 2, 0, -L / 2 + T, W / 2, H, "terminal 1", COL["metal"]),
            box(L / 2 - T, -W / 2, 0, L / 2, W / 2, H, "terminal 2", COL["metal"])]
    col = {"C": COL["mlcc"], "LED": COL["led"], "L": COL["inductor"]}.get(kind, COL["body"])
    top = H - e - (MARK_T if pol else 0)
    out.append(box(-L / 2 + T, -W / 2 + e, e, L / 2 - T, W / 2 - e, top, "body", col))
    if pol:                                     # cathode (LED, diode) / + mark on the pin-1 end
        x0 = -L / 2 + T
        out.append(box(x0, -W / 2 + 2 * e, top, x0 + 0.3 * (L - 2 * T), W / 2 - 2 * e, top + MARK_T, "polarity mark",
                       COL["led_mark"] if kind == "LED" else COL["mark"]))
    return out


def molded(spec, H):
    L, W = _n(spec, "body_length"), _n(spec, "body_width")
    Wt = _n(spec, "terminal_width") or W
    T = _n(spec, "terminal_length") or (L - _n(spec, "inner_span")) / 2
    tant = _kind(spec["name"]) in ("CP", "C")
    t = 0.1 if tant else 0.15                   # terminal sheet thickness
    hl = H * (0.6 if tant else 0.45)            # terminal height up the end face
    prof = [(L / 2 - T, 0), (L / 2, 0), (L / 2, hl), (L / 2 - t, hl), (L / 2 - t, t), (L / 2 - T, t)]
    out = [side_profile(prof, (-1, 0), 0, Wt, "terminal 1"), side_profile(prof, (1, 0), 0, Wt, "terminal 2")]
    top = H - (MARK_T if spec.get("polarized") else 0)
    out.append(box(-L / 2 + t, -W / 2, t, L / 2 - t, W / 2, top, "body", COL["tant"] if tant else COL["body"]))
    if spec.get("polarized"):
        x0 = -L / 2 + t + 0.06 * L
        out.append(box(x0, -W / 2 + 0.1, top, x0 + 0.18 * L, W / 2 - 0.1, H, "polarity band", COL["tant_mark"] if tant else COL["mark"]))
    return out


def _ic_body(hx, hy, A1, zlo, zhi, H):
    """Two-tier moulded body: tapered from the standoff up to the lead band, straight band, tapered to the top."""
    base = rect(hx, hy)
    dt = min(0.1, 0.05 * min(2 * hx, 2 * hy))
    di = min(dt / 2, max(zlo - A1, 0) * 0.15)
    rings, zs = [offset_poly(base, di), base, base, offset_poly(base, dt)], [A1, zlo, zhi, H - MARK_T]
    if di <= 1e-6 or zlo - A1 < 1e-6:           # no lower taper (flat leads at the board)
        rings, zs = rings[1:], [A1, zhi, H - MARK_T]
    return loft(rings, zs, "body"), dt


def _lead_path(s_body, s_toe, Lf, zm, c, flat):
    if flat:
        return [(s_body, c / 2), (s_toe, c / 2)]
    s_knee = max(s_toe - Lf, s_body + c)
    s_knee = min(s_knee, s_toe - c)
    s_sh = s_body + 0.35 * max(s_knee - s_body, 0)
    return [(s_body, zm), (s_sh, zm), (s_knee, c / 2), (s_toe, c / 2)]


def _lead_params(spec, H, pitch):
    flat = spec.get("lead_type") == "flat"
    c = 0.25 if spec.get("tab") and not flat else 0.2 if (pitch or 1) >= 1.0 else 0.15
    A1 = 0.0 if flat else 0.1 if H > 1.3 else 0.05
    zm = c / 2 if flat else max(A1 + (H - A1) * 0.3, c + 0.1)
    return flat, c, A1, zm


def gullwing(spec, H):
    E, E1, D = _n(spec, "lead_span"), _n(spec, "body_width"), _n(spec, "body_length")
    b, Lf = _n(spec, "terminal_width"), _n(spec, "terminal_length")
    pos = spec.get("pin_positions")
    if not pos:
        e, per = spec["pitch"], spec["pin_count"] // 2
        pos = [{"number": str(i + 1), "side": "L", "offset": (i - (per - 1) / 2) * e} for i in range(per)]
        pos += [{"number": str(per + i + 1), "side": "R", "offset": ((per - 1) / 2 - i) * e} for i in range(per)]
    offs = sorted(p["offset"] for p in pos)
    pitch = spec.get("pitch") or min([b2 - a for a, b2 in zip(offs, offs[1:]) if b2 - a > 1e-6] or [1.0])
    flat, c, A1, zm = _lead_params(spec, H, pitch)
    body, dt = _ic_body(E1 / 2, D / 2, A1, zm - c / 2, zm + c / 2, H)
    out = [body]
    path = thick_polyline(_lead_path(E1 / 2, E / 2, Lf, zm, c, flat), c)
    for p in pos:
        out.append(side_profile(path, (-1, 0) if p["side"] == "L" else (1, 0), -p["offset"], b, f"pin {p['number']}"))
    tab = spec.get("tab")
    if tab:
        tl = dim(tab["length"])[1] if tab.get("length") else Lf
        tpath = thick_polyline(_lead_path(E1 / 2, E / 2, tl, zm, c, flat), c)
        out.append(side_profile(tpath, (-1, 0) if tab.get("side", "R") == "L" else (1, 0), -tab.get("offset", 0.0),
                                dim(tab["width"])[1], f"tab {tab['number']}"))
    p1 = next((p for p in pos if str(p["number"]) == "1"), pos[0])
    if spec.get("polarized") and len(pos) == 2:
        x0 = -E1 / 2 + dt + 0.05
        out.append(box(x0, -D / 2 + dt + 0.05, H - MARK_T, x0 + 0.2 * E1, D / 2 - dt - 0.05, H, "polarity band", COL["mark"]))
    else:
        r = min(0.3, 0.12 * min(E1, D))
        sx = -1 if p1["side"] == "L" else 1
        sy = -math.copysign(1, p1["offset"]) if abs(p1["offset"]) > 1e-6 else 0
        out.append(mark_dot(sx * (E1 / 2 - dt - 1.6 * r), sy * (D / 2 - dt - 1.6 * r), r, H - MARK_T))
    return out


def _quad_pins(spec):
    """[(number, side, offset along the side)] in KiCad's order: left top->bottom, bottom, right, top (model y up)."""
    n, e = spec["pin_count"], spec["pitch"]
    nlr = spec.get("pins_left_right", n // 4)
    ntb = spec.get("pins_top_bottom", (n - 2 * nlr) // 2)
    pins, k = [], 1
    for side, cnt, sgn in (("L", nlr, -1), ("B", ntb, 1), ("R", nlr, 1), ("T", ntb, -1)):
        for i in range(cnt):
            pins.append((str(k), side, sgn * (i - (cnt - 1) / 2) * e)); k += 1
    return pins                                 # offset: model y for L/R rows, model x for B/T rows


OUT = {"L": (-1, 0), "R": (1, 0), "B": (0, -1), "T": (0, 1)}


def qfp(spec, H):
    Bx, By = _n(spec, "body_length"), _n(spec, "body_width")
    Ex = _n(spec, "lead_span")
    Ey = dim(spec.get("lead_span_y") or spec["lead_span"])[1]
    b, Lf = _n(spec, "terminal_width"), _n(spec, "terminal_length")
    flat, c, A1, zm = _lead_params(spec, H, spec["pitch"])
    body, dt = _ic_body(Bx / 2, By / 2, A1, zm - c / 2, zm + c / 2, H)
    out = [body]
    px = thick_polyline(_lead_path(Bx / 2, Ex / 2, Lf, zm, c, flat), c)
    py = thick_polyline(_lead_path(By / 2, Ey / 2, Lf, zm, c, flat), c)
    for num, side, off in _quad_pins(spec):
        out.append(side_profile(px if side in "LR" else py, OUT[side], off, b, f"pin {num}"))
    r = min(0.5, 0.08 * min(Bx, By))
    out.append(mark_dot(-(Bx / 2 - dt - 2 * r), By / 2 - dt - 2 * r, r, H - MARK_T))
    return out


def qfn(spec, H):
    Bx, By = _n(spec, "body_length"), _n(spec, "body_width")
    b, T = _n(spec, "terminal_width"), _n(spec, "terminal_length")
    tt = min(0.2, 0.3 * H)                      # lead frame thickness
    pins = _quad_pins(spec)
    out, notches = [], {"L": [], "R": [], "B": [], "T": []}
    for num, side, off in pins:
        if side in "LR":
            sx = -1 if side == "L" else 1
            out.append(box(min(sx * Bx / 2, sx * (Bx / 2 - T)), off - b / 2, 0, max(sx * Bx / 2, sx * (Bx / 2 - T)), off + b / 2, tt,
                           f"pin {num}", COL["metal"]))
        else:
            sy = -1 if side == "B" else 1
            out.append(box(off - b / 2, min(sy * By / 2, sy * (By / 2 - T)), 0, off + b / 2, max(sy * By / 2, sy * (By / 2 - T)), tt,
                           f"pin {num}", COL["metal"]))
        notches[side].append(off)
    # mould between the terminals: the body outline (counter-clockwise) with a notch per terminal
    poly = []
    for side, start, d in (("B", (-Bx / 2, -By / 2), (1, 0)), ("R", (Bx / 2, -By / 2), (0, 1)),
                           ("T", (Bx / 2, By / 2), (-1, 0)), ("L", (-Bx / 2, By / 2), (0, -1))):
        poly.append(start)
        n_ = (-d[1], d[0])                      # inward
        along = sorted(((o - start[0]) * d[0] if side in "BT" else (o - start[1]) * d[1]) for o in notches[side])
        for a in along:
            for s, k in ((a - b / 2, 0), (a - b / 2, T), (a + b / 2, T), (a + b / 2, 0)):
                poly.append((start[0] + d[0] * s + n_[0] * k, start[1] + d[1] * s + n_[1] * k))
    ep = spec.get("exposed_pad")
    holes = []
    if ep:
        ex, ey = dim(ep["length"])[1], dim(ep["width"])[1]
        out.append(box(-ex / 2, -ey / 2, 0, ex / 2, ey / 2, tt, "exposed pad", COL["metal"]))
        holes.append(rect(ex / 2, ey / 2)[::-1])
    out.append(prism(poly, tt, holes=holes, name="mould", color=COL["body"]))
    out.append(box(-Bx / 2, -By / 2, tt, Bx / 2, By / 2, H - MARK_T, "body", COL["body"]))
    r = min(0.25, 0.08 * min(Bx, By))
    out.append(mark_dot(-(Bx / 2 - 2 * r), By / 2 - 2 * r, r, H - MARK_T))
    return out


def pin_header(spec, H=None):
    rows, cols, p = spec["rows"], spec["cols"], spec["pitch"]
    hb, top, a = {2.54: (2.5, 8.5, 0.64), 2.0: (2.0, 6.0, 0.5), 1.27: (1.5, 4.0, 0.4)}.get(round(p, 2), (p, 3 * p, 0.25 * p))
    pins = [(c * p, -r * p) for r in range(rows) for c in range(cols)]
    holes = [rect(a / 2, a / 2, x, y)[::-1] for x, y in pins]
    outline = [(-p / 2, -(rows - 1) * p - p / 2), ((cols - 1) * p + p / 2, -(rows - 1) * p - p / 2),
               ((cols - 1) * p + p / 2, p / 2), (-p / 2, p / 2)]
    out = [prism(outline, hb, holes=holes, name="body", color=COL["body"])]
    out += [box(x - a / 2, y - a / 2, -3.0, x + a / 2, y + a / 2, top, f"pin {i + 1}", COL["gold"]) for i, (x, y) in enumerate(pins)]
    return out


def radial(spec, H):
    D, P = spec["diameter"], spec["pitch"]
    wd = 0.5 if D <= 6.3 else 0.6
    out = [prism(circle(D / 2, P / 2, 0), H, name="body", color=COL["ecap"] if spec.get("polarized") else COL["mlcc"])]
    out += [box(x - wd / 2, -wd / 2, -3.0, x + wd / 2, wd / 2, 0, f"pin {i + 1}", COL["metal"]) for i, x in enumerate((0.0, P))]
    return out


def axial(spec, H=None):
    L, D, P = spec["length"], spec["diameter"], spec["pitch"]
    wd = 0.8 if D > 3.5 else 0.6
    x0, x1, zc = P / 2 - L / 2, P / 2 + L / 2, D / 2
    cyl = lambda a, b2, nm, col: prism(circle(D / 2, 0, zc), b2 - a, (a, 0, 0), (0, 1, 0), (0, 0, 1), name=nm, color=col)
    if spec.get("polarized"):                   # cathode band at the pin-1 end
        out = [cyl(x0, x0 + 0.15 * L, "cathode band", COL["mark"]), cyl(x0 + 0.15 * L, x1, "body", COL["body"])]
    else:
        out = [cyl(x0, x1, "body", COL["resistor"])]
    for i, path in enumerate(([(x0, zc), (0.0, zc), (0.0, -3.0)], [(x1, zc), (P, zc), (P, -3.0)])):
        out.append(prism(thick_polyline(path, wd), wd, (0, wd / 2, 0), (1, 0, 0), (0, 0, 1), name=f"pin {i + 1}", color=COL["metal"]))
    return out


def bga(spec, H):
    """BGA / LGA / CSP: balls (8-sided prisms, the collapsed ball height) under a flat body, A1 dot on top.
    Balls sit where footprint_adv puts the pads (same grid, same depopulated balls)."""
    from .footprint_adv import bga_balls
    Bx, By = _n(spec, "body_length"), _n(spec, "body_width")
    e = spec["pitch"]
    ey = spec.get("pitch_y") or e
    rows, cols = spec["rows"], spec["cols"]
    d = _n(spec, "ball_diameter") or 1.25 * _n(spec, "land_diameter")
    lga = spec.get("ball_type") == "lga"
    A1 = 0.05 if lga else min(0.8 * d, 0.5 * H)        # standoff: lands are flush, balls collapse to ~80 %
    out = []
    for name, i, j in bga_balls(spec):
        x, y = (j - (cols - 1) / 2) * e, -((i - (rows - 1) / 2) * ey)
        out.append(prism(rect(d / 2, d / 2, x, y) if lga else circle(d / 2, x, y, 8), A1, (0, 0, 0),
                         name=f"ball {name}", color=COL["metal"]))
    out.append(box(-Bx / 2, -By / 2, A1, Bx / 2, By / 2, H - MARK_T, "body", COL["body"]))
    r = min(0.4, 0.06 * min(Bx, By))
    out.append(mark_dot(-(Bx / 2 - 2 * r), By / 2 - 2 * r, r, H - MARK_T))
    return out


def connector(spec, H):
    """Connector / custom part from its pad table: a box over the body outline, a pin through every plated hole
    (3 mm below the board) and a flat contact on every SMD pad; pin 1 marked on the body top."""
    from .footprint_adv import table_geometry
    pads, body, _, _, _, _ = table_geometry(spec)
    out = []
    if body is None:
        xs = [p["x"] + s * p["w"] / 2 for p in pads for s in (-1, 1)]
        ys = [p["y"] + s * p["h"] / 2 for p in pads for s in (-1, 1)]
        body = (min(xs), min(ys), max(xs), max(ys))
    x0, y0, x1, y1 = body[0], -body[3], body[2], -body[1]          # KiCad y down -> y up
    for p in pads:
        if p.get("via") or p["kind"] == "np_thru_hole":
            continue
        x, y = p["x"], -p["y"]
        turn = round((p.get("rot") or 0) / 90) % 2           # pad sizes and drills are in the pad's frame
        if p["kind"] == "thru_hole":
            a = 0.64 * min(p["drill"]) / 0.95 if min(p["drill"]) < 0.95 else 0.64
            hx, hy = (a / 2, (max(p["drill"]) - min(p["drill"]) + a) / 2) if p["drill"][1] > p["drill"][0] else \
                ((max(p["drill"]) - min(p["drill"]) + a) / 2, a / 2)
            hx, hy = (hy, hx) if turn else (hx, hy)
            out.append(box(x - hx, y - hy, -3.0, x + hx, y + hy, min(H, 1.0), f"pin {p['num']}", COL["gold"]))
        else:
            w, h = (p["h"], p["w"]) if turn and not p.get("poly") else (p["w"], p["h"])
            out.append(box(x - 0.4 * w, y - 0.4 * h, 0, x + 0.4 * w, y + 0.4 * h, 0.1, f"pin {p['num']}", COL["metal"]))
    out.append(box(x0, y0, 0.1, x1, y1, H - MARK_T, "body", COL["led"] if spec["package_type"] == "connector" else COL["body"]))
    p1 = next((p for p in pads if p["num"] in ("1", "A1")), None)
    if p1:
        mx = min(max(p1["x"], x0 + 0.4), x1 - 0.4)
        my = min(max(-p1["y"], y0 + 0.4), y1 - 0.4)
        out.append(mark_dot(mx, my, 0.25, H - MARK_T))
    return out


def _header_any(spec, H=None):
    """pin_header specs from package_schema: SMD headers have their origin at the centre (footprint_adv), not pin 1."""
    out = pin_header(dict(spec, cols=spec.get("cols", 1)), H)
    if spec.get("mount") == "smd":
        dx, dy = -(spec.get("cols", 1) - 1) * spec["pitch"] / 2, (spec["rows"] - 1) * spec["pitch"] / 2
        for so in out:                     # SMD: no holes in the board, so the posts stop at the board surface
            so.verts = [(x + dx, y + dy, max(z, 0.0) if so.name.startswith("pin ") else z) for x, y, z in so.verts]
        from .footprint_adv import _header
        posts = {so.name: so for so in out if so.name.startswith("pin ")}
        for p in _header(spec, spec.get("density", "B"))[2]:      # a foot from each post out to its land
            so = posts.get(f"pin {p['num']}")
            if so is None:
                continue
            xs, ys = [v[0] for v in so.verts], [v[1] for v in so.verts]
            a, px, py = max(xs) - min(xs), (min(xs) + max(xs)) / 2, -p["y"]
            end = p["x"] + (p["w"] / 2 - 0.05) * (1 if p["x"] >= px else -1)
            out.append(box(min(px, end), py - a / 2, 0.0, max(px, end), py + a / 2, a / 2, f"foot {p['num']}", COL["gold"]))
    return out


BUILDERS = {"chip": chip, "molded": molded, "gullwing": gullwing, "qfp": qfp, "qfn": qfn,
            "pin_header": _header_any, "radial": radial, "axial": axial, "bga": bga, "connector": connector, "custom": connector}


# ---------------------------------------------------------------- footprint names -> spec
def _d(v):
    return {"nom": v}


def _chip_spec(name, kind, code):
    L, W = int(code[:2]) / 10, int(code[2:]) / 10
    T = round(min(0.6, max(0.1, 0.2 * L)), 2)
    if kind == "LED":
        return {"name": name, "package_type": "chip", "pin_count": 2, "body_length": _d(L), "body_width": _d(W),
                "terminal_length": _d(T), "polarized": True}
    return {"name": name, "package_type": "chip", "pin_count": 2, "body_length": _d(L), "body_width": _d(W), "terminal_length": _d(T)}


# common packages that have no file in package_specs (typical JEDEC outlines)
NAMED = {
    "D_SMC": {"package_type": "molded", "body_length": _d(7.0), "body_width": _d(5.9), "terminal_width": _d(3.1), "terminal_length": _d(1.1),
              "polarized": True},
    "D_SOD-523": {"package_type": "gullwing", "lead_span": _d(1.6), "body_width": _d(1.2), "body_length": _d(0.8), "terminal_width": _d(0.3),
                  "terminal_length": _d(0.2), "lead_type": "flat", "polarized": True,
                  "pin_positions": [{"number": "1", "side": "L", "offset": 0}, {"number": "2", "side": "R", "offset": 0}]},
    "SOT-23-6": {"package_type": "gullwing", "lead_span": _d(2.8), "body_width": _d(1.6), "body_length": _d(2.9), "terminal_width": _d(0.4),
                 "terminal_length": _d(0.45), "pitch": 0.95, "pin_count": 6},
    "SOT-323_SC-70": {"package_type": "gullwing", "lead_span": _d(2.1), "body_width": _d(1.25), "body_length": _d(2.0), "terminal_width": _d(0.3),
                      "terminal_length": _d(0.3), "pin_positions": [{"number": "1", "side": "L", "offset": -0.65},
                                                                    {"number": "2", "side": "L", "offset": 0.65}, {"number": "3", "side": "R", "offset": 0}]},
    "SOT-363_SC-70-6": {"package_type": "gullwing", "lead_span": _d(2.1), "body_width": _d(1.25), "body_length": _d(2.0),
                        "terminal_width": _d(0.25), "terminal_length": _d(0.3), "pitch": 0.65, "pin_count": 6},
    "SOT-89-3": {"package_type": "gullwing", "lead_span": _d(4.1), "body_width": _d(2.5), "body_length": _d(4.5), "terminal_width": _d(0.48),
                 "terminal_length": _d(0.9), "lead_type": "flat",
                 "pin_positions": [{"number": "1", "side": "L", "offset": -1.5}, {"number": "2", "side": "L", "offset": 0},
                                   {"number": "3", "side": "L", "offset": 1.5}],
                 "tab": {"number": "2", "side": "R", "offset": 0, "width": _d(1.7)}},
}
GW_SPAN = {3.9: 6.0, 7.5: 10.3, 5.3: 7.8, 4.4: 6.4, 3.0: 4.9, 6.1: 8.1}      # body width E1 -> lead span E
IMPERIAL = {"0201": "0603", "0402": "1005", "0603": "1608", "0805": "2012", "1008": "2520", "1206": "3216", "1210": "3225",
            "1812": "4532", "2010": "5025", "2220": "5750", "2512": "6332"}             # EIA inch code -> metric code
# EasyEDA / LCSC names: <family>-<pins>_L<body along the pin rows>-W<body across>-P<pitch>[-LS<lead span>][-BL|BR|TL..][-EP<w>[x<l>]]
EASYEDA = re.compile(r"([A-Z][A-Z0-9-]*?)-(\d+)_L([\d.]+)-W([\d.]+)-P([\d.]+)(?:-LS([\d.]+))?(?:-[A-Z]{2})?(?:-EP([\d.]+)(?:[xX\-]([\d.]+))?)?")
SOT_ODD = re.compile(r"T?(SOT-23|SOT-323|SOT-353|SOT-416|SOT-523|SC-59|SC-70|SC-88)$")    # 3 / 5 pins: 2+1 / 3+2 leads
SOT_PINS = {"SOT-23": 3, "TSOT-23": 3, "SOT-323": 3, "SOT-353": 5, "SOT-363": 6, "SOT-563": 6}     # the family without a pin count
# EasyEDA SOT-223 with 3 leads (SOT-223-3 or no count): one row of 3 leads, the tab on the other side
EASYEDA_SOT223 = re.compile(r"SOT-223(?:-3)?_L([\d.]+)-W([\d.]+)-P([\d.]+)-LS([\d.]+)(?:-[A-Z]{2})?$")
# EasyEDA two-lead diodes: SOD-123F_L2.7-W1.6-LS3.8-RD (L body along the leads, W across, LS lead span; F flat leads)
EASYEDA_SOD = re.compile(r"(SOD-\d+)(F?)_L([\d.]+)-W([\d.]+)-LS([\d.]+)(?:-[A-Z]{2})?$")
SOD_LEAD = {"SOD-123": (0.6, 0.4), "SOD-323": (0.3, 0.3)}               # lead width, foot length (D_SOD-123 / -323 specs)
# two rows of gull-wing leads; not DIP-8 (through-hole), TO-252-2 / SOT-223 / SOT-89 (leads on one side and a tab)
GULLWING_FAM = re.compile(r"(?:[A-Z]*SOP|[A-Z]*SOIC|SO|T?SOT-(?:23|26|323|353|363|416|523|563|666)|SC-(?:59|70|74|88))$")


def _easyeda_spec(name, m):
    """EasyEDA/LCSC footprint name match -> spec. Their footprints draw the pin rows along x with pin 1 bottom-left,
    so the spec carries rotation 90 (the KiCad-orientated model turned counter-clockwise)."""
    fam, n = m.group(1), int(m.group(2))
    L, W, e = float(m.group(3)), float(m.group(4)), float(m.group(5))
    LS = float(m.group(6)) if m.group(6) else None
    if re.search(r"QFN|DFN|SON", fam):
        quad = "QFN" in fam
        if quad and n % 4 and (n - 1) % 4 == 0:            # pin count includes the exposed pad
            n -= 1
        s = {"name": name, "package_type": "qfn", "pin_count": n, "pitch": e, "body_length": _d(W), "body_width": _d(L),
             "terminal_width": _d(round(min(0.3, 0.5 * e), 3)), "terminal_length": _d(0.4), "rotation": 90}
        if not quad:
            s.update(pins_left_right=n // 2, pins_top_bottom=0)
        if m.group(7):
            s["exposed_pad"] = {"length": _d(float(m.group(7))), "width": _d(float(m.group(8) or m.group(7)))}
        return s
    if LS is None or LS <= W:
        return None
    if "QFP" in fam:
        return {"name": name, "package_type": "qfp", "pin_count": n, "pitch": e, "body_length": _d(W), "body_width": _d(L),
                "lead_span": _d(LS), "lead_span_y": _d(round(LS - W + L, 3)), "terminal_width": _d(round(min(0.37, 0.45 * e), 3)),
                "terminal_length": _d(0.6), "rotation": 90}
    if not GULLWING_FAM.match(fam):
        return None
    s = {"name": name, "package_type": "gullwing", "pin_count": n, "lead_span": _d(LS), "body_width": _d(W), "body_length": _d(L),
         "terminal_width": _d(round(min(0.42, 0.33 * e), 3)),
         "terminal_length": _d(round(min(0.8 if e >= 1.0 else 0.6, 0.7 * (LS - W) / 2), 2)), "rotation": 90}
    if n % 2 == 0:
        s["pitch"] = e
    elif n == 3 and SOT_ODD.match(fam):                       # P is the pin 1 - pin 2 spacing on the two-lead side
        s["pin_positions"] = [{"number": "1", "side": "L", "offset": -e / 2}, {"number": "2", "side": "L", "offset": e / 2},
                              {"number": "3", "side": "R", "offset": 0}]
    elif n == 5 and SOT_ODD.match(fam):
        s["pin_positions"] = [{"number": str(i + 1), "side": "L", "offset": (i - 1) * e} for i in range(3)] + \
                             [{"number": "4", "side": "R", "offset": e}, {"number": "5", "side": "R", "offset": -e}]
    else:
        return None
    return s


def _easyeda_sot223(name, m):
    """SOT-223 (TO-261AA): leads 1-3 in one row at pitch P, the tab (pin 2) opposite; turned like the other EasyEDA names."""
    L, W, e, LS = (float(g) for g in m.groups())
    if not (LS > W and 6.0 <= L <= 7.0 and 3.0 <= W <= 4.0 and abs(e - 2.3) < 0.05):       # not a SOT-223 outline
        return None
    return {"name": name, "package_type": "gullwing", "pin_count": 4, "lead_span": _d(LS), "body_width": _d(W), "body_length": _d(L),
            "terminal_width": _d(0.75), "terminal_length": _d(round(min(1.0, 0.7 * (LS - W) / 2), 2)),
            "pin_positions": [{"number": str(i + 1), "side": "L", "offset": (i - 1) * e} for i in range(3)],
            "tab": {"number": "2", "side": "R", "offset": 0, "width": _d(3.0)}, "rotation": 90}


def _easyeda_sod(name, m):
    """SOD-123(F) / SOD-323(F): two leads along x, pin 1 (cathode) at -x like KiCad's D_SOD-123."""
    fam, flat, L, W, LS = m.group(1), m.group(2), float(m.group(3)), float(m.group(4)), float(m.group(5))
    if fam not in SOD_LEAD or LS <= L or W >= L:
        return None
    b, Lf = SOD_LEAD[fam]
    s = {"name": name, "package_type": "gullwing", "pin_count": 2, "lead_span": _d(LS), "body_width": _d(L), "body_length": _d(W),
         "terminal_width": _d(b), "terminal_length": _d(round(min(Lf, 0.7 * (LS - L) / 2), 2)), "polarized": True,
         "pin_positions": [{"number": "1", "side": "L", "offset": 0}, {"number": "2", "side": "R", "offset": 0}]}
    if flat:                                                  # SOD-123F / SOD-323F: flat leads, lower body
        s.update(lead_type="flat", height_max={"SOD-123": 1.0, "SOD-323": 0.8}[fam])
    return s


AXIAL = {"DO-35": (4.0, 2.0), "DO-41": (5.2, 2.7), "DO-201AD": (9.5, 5.3), "DO-201": (9.5, 5.3), "DO-15": (7.6, 3.6)}


def spec_from_name(name):
    """Footprint name (KiCad style, `Lib:` prefix allowed) -> spec dict, or None when the family is unknown.
    A file in package_specs wins; otherwise the dimensions come from the name and typical JEDEC/EIA outlines."""
    name = name.split(":")[-1]
    f = SPECS / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text())
    key = name if name in NAMED else name[2:] if name.startswith("D_") and name[2:] in NAMED else None
    if key:
        s = dict(NAMED[key], name=name)
        s.setdefault("pin_count", len(s.get("pin_positions", [])) + (1 if s.get("tab") else 0) or 2)
        return s
    m = re.match(r"(R|C|L|LED|D|F|Fuse)_(\d{4})_(\d{4})Metric", name)
    if m:
        return _chip_spec(name, m.group(1), m.group(3))
    m = re.match(r"(R|C|L|LED|D|F)(\d{4})(?:[-_].*)?$", name)          # EasyEDA / LCSC chip: R0603, C0402, LED0603-RD
    if m and m.group(2) in IMPERIAL:
        s = _chip_spec(name, m.group(1), IMPERIAL[m.group(2)])
        km = f"{m.group(1)}_{m.group(2)}_{IMPERIAL[m.group(2)]}Metric"                # family height as for the KiCad name
        h = next((v for rx, v in HEIGHT if re.search(rx, km)), None)
        return dict(s, height_max=h) if h else s
    if re.fullmatch(r"SOD-\d+", name) and (SPECS / f"D_{name}.json").exists():        # SOD-123 = KiCad's D_SOD-123
        return dict(json.loads((SPECS / f"D_{name}.json").read_text()), name=name)
    m = EASYEDA_SOT223.match(name)
    if m:
        return _easyeda_sot223(name, m)
    m = EASYEDA_SOD.match(name)
    if m:
        return _easyeda_sod(name, m)
    m = re.match(r"(T?SOT-\d+)(_L.*)$", name)                  # SOT-23_L2.9-...: the family's standard pin count
    if m and m.group(1) in SOT_PINS:
        m2 = EASYEDA.fullmatch(f"{m.group(1)}-{SOT_PINS[m.group(1)]}{m.group(2)}")
        return _easyeda_spec(name, m2) if m2 else None
    m = EASYEDA.match(name)
    if m:
        return _easyeda_spec(name, m)
    m = re.match(r"CP_EIA-(\d{4})-(\d{2})", name)
    if m:
        L, W = int(m.group(1)[:2]) / 10, int(m.group(1)[2:]) / 10
        return {"name": name, "package_type": "molded", "pin_count": 2, "body_length": _d(L), "body_width": _d(W),
                "terminal_width": _d(round(0.7 * W, 2)), "terminal_length": _d(round(min(1.3, 0.25 * L), 2)), "polarized": True,
                "height_max": int(m.group(2)) / 10}
    m = re.match(r"(SOIC|SO|SOP|SSOP|TSSOP|MSOP|VSSOP|HTSSOP|TSOP|QSOP)-(\d+)(?:-1EP)?_([\d.]+)x([\d.]+)mm_P([\d.]+)mm", name)
    if m:
        n, E1, D, e = int(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5))
        return {"name": name, "package_type": "gullwing", "pin_count": n, "pitch": e, "lead_span": _d(GW_SPAN.get(E1, E1 + 2.0)),
                "body_width": _d(E1), "body_length": _d(D), "terminal_width": _d(round(min(0.42, 0.33 * e), 3)),
                "terminal_length": _d(0.8 if e >= 1.0 else 0.6)}
    m = re.match(r"([A-Z]*(?:QFN|DFN|SON))-(\d+)(?:-\d*EP)?_([\d.]+)x([\d.]+)mm_P([\d.]+)mm(?:_EP([\d.]+)x([\d.]+)mm)?", name)
    if m:
        n, Bx, By, e = int(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5))
        s = {"name": name, "package_type": "qfn", "pin_count": n, "pitch": e, "body_length": _d(Bx), "body_width": _d(By),
             "terminal_width": _d(round(min(0.3, 0.5 * e), 3)), "terminal_length": _d(0.4)}
        if "QFN" not in m.group(1):
            s.update(pins_left_right=n // 2, pins_top_bottom=0)
        if m.group(6):
            s["exposed_pad"] = {"length": _d(float(m.group(6))), "width": _d(float(m.group(7)))}
        return s
    m = re.match(r"([A-Z]*QFP)-(\d+)(?:-1EP)?_([\d.]+)x([\d.]+)mm_P([\d.]+)mm", name)
    if m:
        n, Bx, By, e = int(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5))
        return {"name": name, "package_type": "qfp", "pin_count": n, "pitch": e, "body_length": _d(Bx), "body_width": _d(By),
                "lead_span": _d(Bx + 2.0), "lead_span_y": _d(By + 2.0), "terminal_width": _d(round(min(0.37, 0.45 * e), 3)),
                "terminal_length": _d(0.6)}
    m = re.match(r"PinHeader_(\d+)x(\d+)_P([\d.]+)mm_Vertical$", name)
    if m:
        return {"name": name, "package_type": "pin_header", "cols": int(m.group(1)), "rows": int(m.group(2)), "pitch": float(m.group(3))}
    m = re.match(r"(CP|C)_Radial_D([\d.]+)mm(?:_H([\d.]+)mm)?_P([\d.]+)mm", name)
    if m:
        D = float(m.group(2))
        H = float(m.group(3)) if m.group(3) else 11.0 if D <= 6.3 else 11.5 if D <= 8 else 16.0 if D <= 10 else 20.0 if D <= 13 else 25.0
        return {"name": name, "package_type": "radial", "diameter": D, "pitch": float(m.group(4)), "height_max": H,
                "polarized": m.group(1) == "CP"}
    m = re.match(r"(R|C|L|D)_Axial_.*?_L([\d.]+)mm_D([\d.]+)mm_P([\d.]+)mm_Horizontal", name) or \
        re.match(r"(D)_(DO-[\dA-Z]+)\w*?_P([\d.]+)mm_Horizontal", name)
    if m:
        if len(m.groups()) == 4:
            L, D, P = float(m.group(2)), float(m.group(3)), float(m.group(4))
        else:
            key = next((k for k in sorted(AXIAL, key=len, reverse=True) if m.group(2).startswith(k)), None)
            if not key:
                return None
            (L, D), P = AXIAL[key], float(m.group(3))
        return {"name": name, "package_type": "axial", "length": L, "diameter": D, "pitch": P, "polarized": m.group(1) == "D"}
    return None


def default_height(spec):
    if spec.get("height_max"):
        return float(spec["height_max"])
    if spec["package_type"] == "pin_header":
        return {2.54: 8.5, 2.0: 6.0, 1.27: 4.0}.get(round(spec["pitch"], 2), 3 * spec["pitch"])
    if spec["package_type"] == "axial":
        return spec["diameter"]
    m = re.match(r"CP_EIA-\d{4}-(\d{2})", spec["name"])
    if m:
        return int(m.group(1)) / 10
    h = next((v for rx, v in HEIGHT if re.search(rx, spec["name"])), None)
    if h is None:
        from .release_check import height_range
        lo, hi, rx = height_range(spec["name"])
        h = round((lo + hi) / 2, 2) if rx else None
    return h


# ---------------------------------------------------------------- STEP writer
def _r(x):
    s = f"{x:.6f}".rstrip("0")
    return "0." if s in ("-0.", "0.") else s


def _s(text):
    return "'" + str(text).replace("'", "''").encode("ascii", "replace").decode() + "'"


def write_step(solids, name, description="ecadlib generated package model"):
    """Solids -> STEP AP214 text: one product, one ADVANCED_BREP_SHAPE_REPRESENTATION, colours per solid."""
    ents = []
    def add(s):
        ents.append(s); return len(ents)
    dirs = {}
    def direction(v):
        k = tuple(round(c, 9) + 0.0 for c in v)
        if k not in dirs:
            dirs[k] = add(f"DIRECTION('',({','.join(_r(c) for c in k)}))")
        return dirs[k]
    app = add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app})")
    pctx = add(f"PRODUCT_CONTEXT('',#{app},'mechanical')")
    prod = add(f"PRODUCT({_s(name)},{_s(name)},{_s(description)},(#{pctx}))")
    add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{prod}))")
    pdf = add(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
    pdc = add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app},'design')")
    pd = add(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
    pds = add(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
    mm = add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    rad = add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    sr = add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{mm},'distance_accuracy_value','confusion accuracy')")
    ctx = add(f"( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc})) "
              f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{mm},#{rad},#{sr})) REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') )")
    o = add("CARTESIAN_POINT('',(0.,0.,0.))")
    ax0 = add(f"AXIS2_PLACEMENT_3D('',#{o},#{direction((0, 0, 1))},#{direction((1, 0, 0))})")
    breps, styled, styles = [], [], {}
    for sol in solids:
        cps = [add(f"CARTESIAN_POINT('',({_r(x)},{_r(y)},{_r(z)}))") for x, y, z in sol.verts]
        vps = [add(f"VERTEX_POINT('',#{c})") for c in cps]
        edges, faces = {}, []
        def edge(a, b):
            k = (min(a, b), max(a, b))
            if k not in edges:
                d = _sub(sol.verts[k[1]], sol.verts[k[0]])
                vec = add(f"VECTOR('',#{direction(_unit(d))},{_r(_len(d))})")
                ln = add(f"LINE('',#{cps[k[0]]},#{vec})")
                edges[k] = add(f"EDGE_CURVE('',#{vps[k[0]]},#{vps[k[1]]},#{ln},.T.)")
            return edges[k], ".T." if a < b else ".F."
        for loops in sol.faces:
            bounds = []
            for j, lp in enumerate(loops):
                oes = [add("ORIENTED_EDGE('',*,*,#%d,%s)" % edge(a, b)) for a, b in zip(lp, lp[1:] + lp[:1])]
                el = add(f"EDGE_LOOP('',({','.join(f'#{x}' for x in oes)}))")
                bounds.append(add(f"{'FACE_OUTER_BOUND' if j == 0 else 'FACE_BOUND'}('',#{el},.T.)"))
            outer = [sol.verts[i] for i in loops[0]]
            nrm = _unit(_newell(outer))
            ref = _unit(_sub(outer[1], outer[0]))
            ax = add(f"AXIS2_PLACEMENT_3D('',#{cps[loops[0][0]]},#{direction(nrm)},#{direction(ref)})")
            pl = add(f"PLANE('',#{ax})")
            faces.append(add(f"ADVANCED_FACE('',({','.join(f'#{x}' for x in bounds)}),#{pl},.T.)"))
        shell = add(f"CLOSED_SHELL('',({','.join(f'#{x}' for x in faces)}))")
        breps.append(add(f"MANIFOLD_SOLID_BREP({_s(sol.name)},#{shell})"))
        if sol.color not in styles:
            rgb = add(f"COLOUR_RGB('',{','.join(_r(c) for c in sol.color)})")
            fasc = add(f"FILL_AREA_STYLE_COLOUR('',#{rgb})")
            fas = add(f"FILL_AREA_STYLE('',(#{fasc}))")
            ssfa = add(f"SURFACE_STYLE_FILL_AREA(#{fas})")
            sss = add(f"SURFACE_SIDE_STYLE('',(#{ssfa}))")
            ssu = add(f"SURFACE_STYLE_USAGE(.BOTH.,#{sss})")
            styles[sol.color] = add(f"PRESENTATION_STYLE_ASSIGNMENT((#{ssu}))")
        styled.append(add(f"STYLED_ITEM('color',(#{styles[sol.color]}),#{breps[-1]})"))
    rep = add(f"ADVANCED_BREP_SHAPE_REPRESENTATION({_s(name)},(#{ax0},{','.join(f'#{b}' for b in breps)}),#{ctx})")
    add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
    add(f"MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',({','.join(f'#{s}' for s in styled)}),#{ctx})")
    head = ["ISO-10303-21;", "HEADER;", f"FILE_DESCRIPTION(({_s(description)}),'2;1');",
            f"FILE_NAME({_s(name + '.step')},'2026-01-01T00:00:00',('ecadlib'),('ecadlib'),'ecadlib step_gen','ecadlib step_gen','');",
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));", "ENDSEC;", "DATA;"]
    return "\n".join(head + [f"#{i + 1}={e};" for i, e in enumerate(ents)] + ["ENDSEC;", "END-ISO-10303-21;", ""])


# ---------------------------------------------------------------- public entry points
def build(spec, height=None):
    """spec dict -> (solids, info). info: family, height, height_src."""
    t = spec["package_type"]
    if t not in BUILDERS:
        raise ValueError(f"no 3D builder for package_type {t!r}")
    H, src = (float(height), "caller") if height else (default_height(spec), "spec/table")
    if not H:
        raise ValueError(f"{spec['name']}: no height (no height_max, no table entry); pass a height")
    solids = BUILDERS[t](spec, H)
    rot = int(spec.get("rotation") or 0) % 360       # footprint drawn turned (EasyEDA: 90): model turned counter-clockwise
    if rot:
        for sol in solids:
            sol.verts = [_turn(x, y, rot) + (z,) for x, y, z in sol.verts]
    return solids, {"family": t, "height": H, "height_src": src, "solids": len(solids)}


def _turn(x, y, rot):
    """(x, y) turned counter-clockwise by rot (0 / 90 / 180 / 270) degrees."""
    return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[rot % 360]


def _on_pad(cx, cy, p, slack=0.6):
    w, h = (p["w"], p["h"]) if round(p.get("rot", 0) or 0) % 180 == 0 else (p["h"], p["w"])
    if round(p.get("rot", 0) or 0) % 90:
        w = h = max(p["w"], p["h"])
    return abs(cx - p["x"]) <= w / 2 + slack and abs(cy - p["y"]) <= h / 2 + slack


def fit_rotation(spec, pads):
    """Rotation (0 / 90 / 180 / 270) that puts each lead of the model on the footprint pad with the same number, or None.
    A footprint name does not fix the orientation: EasyEDA draws the same SOT-23-3_L2.9-W1.3-... with the pin rows
    along x (C2002) or along y (C8545). The spec's own rotation is kept while its leads sit on their pads; another one
    is returned only when all of its leads sit on their pads (and the spec's do not); None when no rotation fits."""
    t = spec.get("package_type")
    if t not in BUILDERS or not pads:
        return None
    try:
        solids = BUILDERS[t](spec, float(spec.get("height_max") or 1.0))
    except (KeyError, TypeError, IndexError, ZeroDivisionError, ValueError):
        return None
    by_num = {}
    for p in pads:
        if str(p.get("designator") or ""):
            by_num.setdefault(str(p["designator"]), []).append(p)
    leads = []
    for sol in solids:
        m = re.fullmatch(r"(?:pin|terminal) (\S+)", sol.name)
        if m:
            xs, ys = [v[0] for v in sol.verts], [v[1] for v in sol.verts]
            leads.append((m.group(1), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    if len(leads) < 2 or any(n not in by_num for n, _, _ in leads):
        return None
    def fits(rot):
        return all(any(_on_pad(*_turn(cx, cy, rot), p) for p in by_num[n]) for n, cx, cy in leads)
    own = int(spec.get("rotation") or 0) % 360
    if fits(own):
        return own
    return next((r for r in (0, 90, 180, 270) if r != own and fits(r)), None)


def generate(src, height=None, name=None, pads=None):
    """spec dict, spec .json path or footprint name -> (STEP text, info). Raises ValueError when the
    footprint family is unknown. pads: the footprint's pads (designator, x, y, w, h, rot; model coordinates, y up);
    the model is then turned so its leads sit on them (fit_rotation), info["rotation"]."""
    if isinstance(src, dict):
        spec = src
    elif str(src).endswith(".json") and Path(src).exists():
        spec = json.loads(Path(src).read_text())
    else:
        spec = spec_from_name(str(src))
        if spec is None:
            raise ValueError(f"{src}: no package spec and the footprint name is not a known package family")
    if pads:
        rot = fit_rotation(spec, pads)
        if rot is not None and rot != int(spec.get("rotation") or 0) % 360:
            spec = dict(spec, rotation=rot)
    try:
        solids, info = build(spec, height)
        info["name"] = name or spec["name"]
        text = write_step(solids, info["name"])                      # zero-size dimensions fail here (zero-length edge)
    except (KeyError, TypeError, IndexError, ZeroDivisionError) as e:     # incomplete spec: no model, not a crash
        raise ValueError(f"{spec.get('name', src)}: spec incomplete for a 3D model ({type(e).__name__}: {e})")
    rep = check(text)
    if rep["errors"]:
        raise ValueError(f"{info['name']}: generated STEP failed its own check: {rep['errors'][:3]}")
    info.update(bbox=rep["bbox"], volume=rep["volume"], bytes=len(text), rotation=int(spec.get("rotation") or 0) % 360)
    return text, info


# ---------------------------------------------------------------- STEP reader / checker
TOK = re.compile(r"\s*(?:(#\d+)|('(?:[^']|'')*')|(\.[A-Z_0-9]+\.)|([-+]?(?:\d+\.?\d*(?:[Ee][-+]?\d+)?|\.\d+(?:[Ee][-+]?\d+)?))|"
                 r"([A-Za-z_][A-Za-z_0-9]*)|([()=,;$*]))")


def parse(text):
    """STEP DATA section -> {id: (TYPE, args)}; complex entities -> ('COMPLEX', [(TYPE, args), ...]).
    args: nested lists; ('ref', n), str (quoted strings keep their quotes), float, ('enum', X), None for $ and *."""
    m = re.search(r"\bDATA\s*;(.*?)\bENDSEC\s*;", text, re.S)
    if not m:
        raise ValueError("no DATA section")
    data = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    toks, pos = [], 0
    while pos < len(data):
        mt = TOK.match(data, pos)
        if not mt or mt.end() == pos:
            if data[pos:].strip():
                raise ValueError(f"bad token at {data[pos:pos + 30]!r}")
            break
        pos = mt.end()
        ref, st, en, num, kw, p = mt.groups()
        toks.append(("ref", int(ref[1:])) if ref else ("str", st) if st else ("enum", en[1:-1]) if en else
                    ("num", float(num)) if num else ("kw", kw) if kw else ("p", p))
    i = 0
    def args():
        nonlocal i
        out = []                                # toks[i] is '('
        i += 1
        while toks[i] != ("p", ")"):
            k, v = toks[i]
            if (k, v) == ("p", ","):
                i += 1; continue
            if (k, v) == ("p", "("):
                out.append(args()); continue
            if k == "kw" and toks[i + 1] == ("p", "("):
                i += 1; out.append(("typed", v, args())); continue
            out.append(("ref", v) if k == "ref" else ("enum", v) if k == "enum" else None if k == "p" else v)
            i += 1
        i += 1
        return out
    ents = {}
    while i < len(toks):
        k, v = toks[i]
        if k != "ref" or toks[i + 1] != ("p", "="):
            raise ValueError(f"expected '#n =' at token {i}: {toks[i:i + 3]}")
        eid, i = v, i + 2
        if toks[i] == ("p", "("):                # complex entity: ( A(..) B(..) )
            i += 1; parts = []
            while toks[i] != ("p", ")"):
                kw = toks[i][1]; i += 1
                parts.append((kw, args()))
            i += 1
            ents[eid] = ("COMPLEX", parts)
        else:
            kw = toks[i][1]; i += 1
            ents[eid] = (kw, args())
        if toks[i] != ("p", ";"):
            raise ValueError(f"#{eid}: expected ';'")
        i += 1
    return ents


def _refs(a):
    if isinstance(a, tuple) and a and a[0] == "ref":
        yield a[1]
    elif isinstance(a, tuple) and a and a[0] == "typed":
        yield from _refs(a[2])
    elif isinstance(a, list):
        for x in a:
            yield from _refs(x)


def check(text, tol=1e-6):
    """Validate a STEP file's B-rep (see module docstring) -> {errors, solids, bbox, volume, unstyled, ...}."""
    errs = []
    try:
        E = parse(text)
    except (ValueError, IndexError) as e:
        return {"errors": [f"parse: {e}"], "solids": 0, "bbox": None, "volume": 0.0, "unstyled": [], "entities": 0}
    for eid, (t, a) in E.items():
        for r in _refs(a):
            if r not in E:
                errs.append(f"#{eid} {t} refers to missing #{r}")
    if errs:
        return {"errors": errs, "solids": 0, "bbox": None, "volume": 0.0, "unstyled": [], "entities": len(E)}
    try:
        return _check_breps(E, tol)
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as e:   # wrong entity types / arity
        return {"errors": [f"malformed B-rep: {type(e).__name__}: {e}"], "solids": 0, "bbox": None, "volume": 0.0,
                "unstyled": [], "entities": len(E)}


def _check_breps(E, tol):
    errs = []
    ref = lambda x: E[x[1]]
    pt = lambda r: tuple(E[r][1][1])           # CARTESIAN_POINT id -> xyz
    vtx = lambda r: pt(E[r][1][1][1])           # VERTEX_POINT id -> xyz
    def dirv(r):
        t, a = E[r]
        return tuple(a[1])
    breps = [k for k, (t, a) in E.items() if t in ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS")]
    styled = {r for k, (t, a) in E.items() if t in ("STYLED_ITEM", "OVER_RIDING_STYLED_ITEM") for r in [a[2][1]] if isinstance(a[2], tuple)}
    allp, vols, per = [], [], []
    for b in breps:
        sh = E[b][1][1][1]
        if E[sh][0] != "CLOSED_SHELL":
            errs.append(f"#{b}: shell #{sh} is {E[sh][0]}"); continue
        use, vol, pts = {}, 0.0, []
        for fr in E[sh][1][1]:
            fid = fr[1]
            ft, fa = E[fid]
            if ft not in ("ADVANCED_FACE", "FACE_SURFACE"):
                errs.append(f"#{fid}: face type {ft}"); continue
            surf = E[fa[2][1]]
            sense = fa[3] == ("enum", "T")
            plane = None
            if surf[0] == "PLANE":
                ax = E[surf[1][1][1]][1]
                plane = (pt(ax[1][1]), _unit(dirv(ax[2][1])))
            area = (0.0, 0.0, 0.0)
            p0 = None
            for bi, br in enumerate(fa[1]):
                bt, ba = E[br[1]]
                lt, la = E[ba[1][1]]
                if lt != "EDGE_LOOP":
                    if lt == "VERTEX_LOOP":
                        continue
                    errs.append(f"#{br[1]}: loop type {lt}"); continue
                seq = []
                for oe in la[1]:
                    ot, oa = E[oe[1]]
                    ec = oa[3][1]
                    et, ea = E[ec]
                    v1, v2 = ea[1][1], ea[2][1]
                    fwd = oa[4] == ("enum", "T")
                    s, e = (v1, v2) if oa[4] == ("enum", "T") else (v2, v1)
                    seq.append((s, e))
                    use.setdefault(ec, []).append((fid, fwd))
                    g = E[ea[3][1]]
                    if g[0] == "LINE":
                        lp, d = pt(g[1][1][1]), _unit(dirv(E[g[1][2][1]][1][1][1]))
                        for v in (vtx(v1), vtx(v2)):
                            if _len(_cross(_sub(v, lp), d)) > 1e-5:
                                errs.append(f"#{ec}: vertex {v} not on its LINE"); break
                for (s1, e1), (s2, e2) in zip(seq, seq[1:] + seq[:1]):
                    if e1 != s2:
                        errs.append(f"#{ba[1][1]}: loop not connected (#{e1} -> #{s2})"); break
                poly = [vtx(s) for s, _ in seq]
                if bt == "FACE_BOUND" and ba[2] == ("enum", "F") or bt == "FACE_OUTER_BOUND" and ba[2] == ("enum", "F"):
                    poly = poly[::-1]
                pts += poly
                nw = _newell(poly)
                area = _add(area, nw)
                p0 = p0 or poly[0]
                if plane:
                    for v in poly:
                        if abs(_dot(_sub(v, plane[0]), plane[1])) > 1e-5:
                            errs.append(f"#{fid}: vertex {v} off its plane"); break
                    if bi == 0 and _dot(nw, plane[1]) * (1 if sense else -1) <= 0:
                        errs.append(f"#{fid}: outer loop runs the wrong way round the face normal")
            if p0 is not None:
                vol += _dot(p0, area) / 6.0     # Newell vector = 2 x area vector
        for ec, us in use.items():
            if len(us) != 2:
                errs.append(f"#{b}: edge #{ec} used by {len(us)} face(s), not 2 (shell not closed)")
            elif us[0][1] == us[1][1]:
                errs.append(f"#{b}: edge #{ec} used twice in the same direction (inconsistent orientation)")
        if vol <= tol:
            errs.append(f"#{b} {E[b][1][0]}: volume {vol:.6g} is not positive")
        vols.append(vol); allp += pts
        per.append({"id": b, "name": E[b][1][0].strip("'"), "volume": round(vol, 6), "faces": len(E[sh][1][1]),
                    "styled": b in styled})
    if not breps:
        errs.append("no MANIFOLD_SOLID_BREP")
    bbox = tuple(round(min(p[i] for p in allp), 6) for i in range(3)) + tuple(round(max(p[i] for p in allp), 6) for i in range(3)) if allp else None
    return {"errors": errs, "solids": len(breps), "bbox": bbox, "volume": round(sum(vols), 6), "entities": len(E),
            "unstyled": [s["name"] for s in per if not s["styled"]], "per_solid": per}


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a package-shaped STEP model, or check STEP files")
    ap.add_argument("src", nargs="+", help="package spec .json, footprint name (e.g. SOIC-8_3.9x4.9mm_P1.27mm), or with --check STEP files")
    ap.add_argument("-o", "--out", help="output .step (default <name>.step)")
    ap.add_argument("--height", type=float, help="overall height, mm (default: spec height_max, else a per-package table)")
    ap.add_argument("--check", action="store_true", help="validate the given STEP files instead")
    a = ap.parse_args(argv)
    if a.check:
        bad = 0
        for f in a.src:
            r = check(Path(f).read_text(errors="replace"))
            bb = " x ".join(f"{r['bbox'][i + 3] - r['bbox'][i]:.3f}" for i in range(3)) if r["bbox"] else "-"
            print(f"{'ok  ' if not r['errors'] else 'FAIL'} {f}: {r['solids']} solids, volume {r['volume']:.4g} mm3, bbox {bb} mm "
                  f"{r['bbox']}" + (f", no colour: {r['unstyled']}" if r["unstyled"] else ""))
            for e in r["errors"][:20]:
                print(f"     {e}")
            bad += bool(r["errors"])
        return 1 if bad else 0
    if len(a.src) != 1:
        ap.error("give one spec or footprint name (or --check FILES)")
    try:
        text, info = generate(a.src[0], a.height)
    except ValueError as e:
        print(f"step_gen: {e}", file=sys.stderr)
        return 2
    out = Path(a.out or f"{info['name']}.step")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    bb = info["bbox"]
    print(f"{info['name']}: {info['family']}, {info['solids']} solids, height {info['height']} mm ({info['height_src']}), "
          f"bbox x {bb[0]:.3f}..{bb[3]:.3f} y {bb[1]:.3f}..{bb[4]:.3f} z {bb[2]:.3f}..{bb[5]:.3f}, {info['bytes'] // 1024} KiB -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
