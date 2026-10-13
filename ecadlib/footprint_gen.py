"""Parametric IPC-7351B-style SMD footprint generator -> KiCad .kicad_mod (S-expression).

Supported package_type: chip (2-terminal R/C/L), gullwing (dual-row SOIC/SOP/TSSOP), qfn (+EP).
Input: JSON matching package_schema.json (the contract an LLM fills from a datasheet drawing).

IPC-7351B land-pattern equations (all mm; implemented in ipc_land()):

    Z = Lmin + 2*Jt + sqrt(CL^2 + F^2 + P^2)          outer pad-to-pad extent (toe)
    G = Smax - 2*Jh - sqrt(CS^2 + F^2 + P^2)          inner gap between opposing pads (heel)
    X = Wmin + 2*Js + sqrt(CW^2 + F^2 + P^2)          pad width (side)

  L  = terminal-to-terminal span (lead span E for gull-wing, body length for chip/QFN)
  S  = inner span between terminals: Smin = Lmin - 2*Tmax, Smax = Lmax - 2*Tmin
       (IPC uses an RMS tolerance for S: CS = sqrt(CL^2 + 2*CT^2) and pulls Smin/Smax
       inward by (Stol - CS)/2, so stacked worst cases do not over-shrink the heel)
  W  = terminal width; C* = max-min tolerance of each dimension
  Jt/Jh/Js = toe/heel/side solder fillet goals for the density level (FILLETS table)
  F  = PCB fabrication tolerance, P = part placement tolerance
Pad length = (Z - G)/2, pad centre = (Z + G)/4 from origin.
Courtyard = bounding box of (pads U body) + courtyard excess (density dependent).

Output targets KiCad file format version 20221018 (KiCad 7) which KiCad 8/9 load
natively and upgrade on save. (KiCad's own library is now at 20260206 / KiCad 10.)
"""
import argparse, json, math, sys
from pathlib import Path

# IPC-7351B fillet goals (toe, heel, side, courtyard excess) per density A/B/C.
FILLETS = {
    # Table 3-5: rectangular/square-end chip components >= 1608 (0603)
    "chip": {"A": (0.55, 0.00, 0.05, 0.50), "B": (0.35, 0.00, 0.00, 0.25), "C": (0.15, 0.00, -0.05, 0.10)},
    # Table 3-6: chip components < 1608 (0402, 0201). Values per IPC-7351B as commonly
    # reproduced; verify against the standard before production use.
    "chip_small": {"A": (0.20, 0.00, 0.05, 0.20), "B": (0.10, 0.00, 0.00, 0.15), "C": (0.05, 0.00, 0.00, 0.10)},
    # Table 3-2: gull-wing leads, pitch > 0.625 mm
    "gullwing": {"A": (0.55, 0.45, 0.05, 0.50), "B": (0.35, 0.35, 0.03, 0.25), "C": (0.15, 0.25, 0.01, 0.10)},
    # Table 3-2: gull-wing leads, pitch <= 0.625 mm (side fillet goes negative)
    "gullwing_fine": {"A": (0.55, 0.45, 0.01, 0.50), "B": (0.35, 0.35, -0.02, 0.25), "C": (0.15, 0.25, -0.04, 0.10)},
    # Table 3-15: QFN / bottom-only terminations (no heel fillet, negative side)
    "qfn": {"A": (0.40, 0.00, -0.04, 0.50), "B": (0.30, 0.00, -0.04, 0.25), "C": (0.20, 0.00, -0.04, 0.10)},
    # PROVISIONAL until checked against the research notes
    "molded": {"A": (0.25, 0.80, 0.01, 0.50), "B": (0.15, 0.50, -0.05, 0.25), "C": (0.07, 0.20, -0.10, 0.10)},
    "flat": {"A": (0.55, 0.45, 0.05, 0.50), "B": (0.35, 0.35, 0.03, 0.25), "C": (0.15, 0.25, 0.01, 0.10)},
}
FAB_TOL, PLACE_TOL = 0.05, 0.025      # IPC-7351B default F and P
MIN_PAD_GAP = 0.2                      # minimum copper gap between adjacent pads (solder-mask web)
SILK_W, FAB_W, CRT_W, SILK_PAD_CLEAR = 0.12, 0.10, 0.05, 0.2
SILK_FAB_OFFSET = 0.11                 # silk outline sits this far outside the fab body (KiCad generator default)


# ---------------------------------------------------------------- dimensions & IPC math
def dim(d):
    """Normalise a schema dimension to (min, nom, max)."""
    if d is None:
        return None
    if isinstance(d, (int, float)):
        return (d, d, d)
    if "min" in d and "max" in d:
        return (d["min"], d.get("nom", (d["min"] + d["max"]) / 2), d["max"])
    t = d.get("tol", 0.0)                                  # a one-sided min or max still wins over nom +- tol
    return (d.get("min", d["nom"] - t), d["nom"], d.get("max", d["nom"] + t))


def rup(x, g=0.01):
    return math.ceil(round(x / g, 6)) * g


def rdn(x, g=0.01):
    return math.floor(round(x / g, 6)) * g


def ipc_land(L, T, W, fillet, F=FAB_TOL, P=PLACE_TOL):
    """L,T,W = (min,nom,max) tuples. Returns dict(Z,G,X,pad_len,pad_ctr) - see module docstring."""
    jt, jh, js, _ = fillet
    CL, CT, CW = L[2] - L[0], T[2] - T[0], W[2] - W[0]
    smin, smax = L[0] - 2 * T[2], L[2] - 2 * T[0]
    cs = math.sqrt(CL ** 2 + 2 * CT ** 2)              # RMS tolerance of S
    adj = ((smax - smin) - cs) / 2
    smin, smax = smin + adj, smax - adj
    Z = rup(L[0] + 2 * jt + math.sqrt(CL ** 2 + F ** 2 + P ** 2))
    G = rdn(smax - 2 * jh - math.sqrt(cs ** 2 + F ** 2 + P ** 2))
    X = rup(W[0] + 2 * js + math.sqrt(CW ** 2 + F ** 2 + P ** 2))
    G = max(G, 0.0)
    return {"Z": Z, "G": G, "X": X, "pad_len": round((Z - G) / 2, 4), "pad_ctr": round((Z + G) / 4, 4),
            "Smin": smin, "Smax": smax}


# ---------------------------------------------------------------- S-expression writer
def n(x):
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def line(x1, y1, x2, y2, layer, w):
    return f'  (fp_line (start {n(x1)} {n(y1)}) (end {n(x2)} {n(y2)}) (stroke (width {n(w)}) (type solid)) (layer {q(layer)}))'


def rect(x1, y1, x2, y2, layer, w):
    return f'  (fp_rect (start {n(x1)} {n(y1)}) (end {n(x2)} {n(y2)}) (stroke (width {n(w)}) (type solid)) (fill none) (layer {q(layer)}))'


def poly(pts, layer, w, fill="none"):
    xy = " ".join(f"(xy {n(x)} {n(y)})" for x, y in pts)
    return f'  (fp_poly (pts {xy}) (stroke (width {n(w)}) (type solid)) (fill {fill}) (layer {q(layer)}))'


def text(kind, val, x, y, layer, size=1.0, hide=False):
    th = round(size * 0.15, 3)
    return (f'  (fp_text {kind} {q(val)} (at {n(x)} {n(y)}) (layer {q(layer)}){" hide" if hide else ""}\n'
            f'    (effects (font (size {n(size)} {n(size)}) (thickness {n(th)}))))')


def pad(num, x, y, w, h, shape="roundrect", layers=("F.Cu", "F.Paste", "F.Mask"), extra=""):
    lay = " ".join(q(l) for l in layers)
    rr = f" (roundrect_rratio {n(min(0.25, 0.25 / min(w, h)))})" if shape == "roundrect" else ""
    return f'  (pad {q(num)} smd {shape} (at {n(x)} {n(y)}) (size {n(w)} {n(h)}) (layers {lay}){rr}{extra})'


# ---------------------------------------------------------------- package builders
def family(spec):
    """IPC-7351B lead family (FILLETS key) for a spec: `lead_type` overrides the default per package_type."""
    t = spec["package_type"]
    lt = spec.get("lead_type")
    if t == "chip":
        if lt in ("molded", "concave", "convex"):
            return lt
        return "chip_small" if dim(spec["body_length"])[1] < 1.55 else "chip"
    if t == "molded":
        return "molded"
    if t == "gullwing":
        if lt == "flat":
            return "flat"
        pitch = spec.get("pitch") or _min_pitch(spec)
        return "gullwing" if pitch is None or pitch > 0.625 else "gullwing_fine"
    if t == "qfn":
        return "qfn"
    if t == "qfp":
        return "gullwing" if spec["pitch"] > 0.625 else "gullwing_fine"
    raise ValueError(f"unsupported package_type {t}")


def _min_pitch(spec):
    pos = spec.get("pin_positions") or []
    best = None
    for side in ("L", "R"):
        ys = sorted(p["offset"] for p in pos if p["side"] == side)
        for a, b in zip(ys, ys[1:]):
            best = b - a if best is None else min(best, b - a)
    return best


def _two_terminal(spec, fil):
    L = dim(spec["body_length"])
    W = dim(spec.get("terminal_width") or spec["body_width"])
    r = ipc_land(L, dim(spec["terminal_length"]), W, fil)
    pads = [("1", -r["pad_ctr"], 0, r["pad_len"], r["X"]), ("2", r["pad_ctr"], 0, r["pad_len"], r["X"])]
    return pads, (L[1] / 2, dim(spec["body_width"])[1] / 2), {k: r[k] for k in ("Z", "G", "X")}


def _gullwing(spec, fil):
    T, bw, span = dim(spec["terminal_length"]), dim(spec["terminal_width"]), dim(spec["lead_span"])
    r = ipc_land(span, T, bw, fil)
    pos = spec.get("pin_positions")
    if not pos:                                               # standard dual row: 1..n/2 down the left, then up the right
        e, per = spec["pitch"], spec["pin_count"] // 2
        pos = [{"number": str(i + 1), "side": "L", "offset": (i - (per - 1) / 2) * e} for i in range(per)]
        pos += [{"number": str(per + i + 1), "side": "R", "offset": ((per - 1) / 2 - i) * e} for i in range(per)]
    pitch = _min_pitch({"pin_positions": pos})
    X = min(r["X"], rdn(pitch - MIN_PAD_GAP)) if pitch else r["X"]
    pads = [(p["number"], (-1 if p["side"] == "L" else 1) * r["pad_ctr"], p["offset"], r["pad_len"], X) for p in pos]
    rep = {"Z": r["Z"], "G": r["G"], "X": X, "X_unclamped": r["X"]}
    tab = spec.get("tab")
    if tab:                                                   # SOT-223 / DPAK style wide tab on one side
        tr = ipc_land(span, dim(tab.get("length") or spec["terminal_length"]), dim(tab["width"]), fil)
        sgn = -1 if tab.get("side", "R") == "L" else 1
        pads.append((str(tab["number"]), sgn * tr["pad_ctr"], tab.get("offset", 0.0), tr["pad_len"], tr["X"]))
        rep["tab"] = {"Z": tr["Z"], "G": tr["G"], "X": tr["X"]}
    body_x, body_y = dim(spec["body_width"])[1] / 2, dim(spec["body_length"])[1] / 2   # E1 across, D along the rows
    return pads, (body_x, body_y), rep


def _qfn(spec, fil):
    """Quad (or dual) rows: QFN/DFN toes sit at the body edge; QFP toes at the lead span."""
    npins, e = spec["pin_count"], spec["pitch"]
    body_x, body_y, T, bw = dim(spec["body_length"]), dim(spec["body_width"]), dim(spec["terminal_length"]), dim(spec["terminal_width"])
    nlr = spec.get("pins_left_right", npins // 4)
    ntb = spec.get("pins_top_bottom", (npins - 2 * nlr) // 2)
    span_x = dim(spec["lead_span"]) if spec.get("lead_span") else body_x
    span_y = dim(spec.get("lead_span_y") or spec["lead_span"]) if spec.get("lead_span") else body_y
    rx = ipc_land(span_x, T, bw, fil)                         # left/right pads
    ry = ipc_land(span_y, T, bw, fil)                         # top/bottom pads
    X = min(rx["X"], rdn(e - MIN_PAD_GAP))
    pads, k = [], 1
    for i in range(nlr):                                      # left, top->bottom
        pads.append((str(k), -rx["pad_ctr"], (i - (nlr - 1) / 2) * e, rx["pad_len"], X)); k += 1
    for i in range(ntb):                                      # bottom, left->right
        pads.append((str(k), (i - (ntb - 1) / 2) * e, ry["pad_ctr"], X, ry["pad_len"])); k += 1
    for i in range(nlr):                                      # right, bottom->top
        pads.append((str(k), rx["pad_ctr"], ((nlr - 1) / 2 - i) * e, rx["pad_len"], X)); k += 1
    for i in range(ntb):                                      # top, right->left
        pads.append((str(k), ((ntb - 1) / 2 - i) * e, -ry["pad_ctr"], X, ry["pad_len"])); k += 1
    return pads, (body_x[1] / 2, body_y[1] / 2), {"Z": rx["Z"], "G": rx["G"], "X": X, "Zy": ry["Z"], "Gy": ry["G"]}


def _clip(seg, pads, c):
    """Cut an axis-aligned silk segment wherever it comes within c of a pad (pads as (num,x,y,w,h))."""
    (x1, y1), (x2, y2) = seg
    horiz = abs(y1 - y2) < 1e-9
    a, b = (min(x1, x2), max(x1, x2)) if horiz else (min(y1, y2), max(y1, y2))
    keep = [(a, b)]
    for _, px, py, w, h in pads:
        if horiz and abs(y1 - py) < h / 2 + c:
            lo, hi = px - w / 2 - c, px + w / 2 + c
        elif not horiz and abs(x1 - px) < w / 2 + c:
            lo, hi = py - h / 2 - c, py + h / 2 + c
        else:
            continue
        keep = [part for s, e in keep for part in ((s, min(e, lo)), (max(s, hi), e)) if part[1] - part[0] > 1e-6]
    keep = [(s, e) for s, e in keep if e - s >= 0.1]                       # drop slivers
    return [((s, y1), (e, y1)) if horiz else ((x1, s), (x1, e)) for s, e in keep]


def build(spec, density=None):
    """Return (kicad_mod_text, report_dict)."""
    density = density or spec.get("density", "B")
    t = spec["package_type"]
    fam = family(spec)
    fil = FILLETS[fam][density]
    rep = {"name": spec["name"], "type": t, "density": density, "ipc_family": fam}
    if t in ("chip", "molded"):
        pads, (bx, by), r = _two_terminal(spec, fil)
    elif t == "gullwing":
        pads, (bx, by), r = _gullwing(spec, fil)
    else:                                                     # qfn, qfp
        pads, (bx, by), r = _qfn(spec, fil)
    rep.update(r)
    polarized = bool(spec.get("polarized"))
    two_term = t in ("chip", "molded") or (t == "gullwing" and len(pads) == 2)

    ep = spec.get("exposed_pad")
    ep_lines = []
    if ep:
        ew, eh = dim(ep["length"])[1], dim(ep["width"])[1]
        epnum = str(ep.get("number") or spec["pin_count"] + 1)
        lr = (lambda p: abs(p[1]) > abs(p[2])) if t in ("qfn", "qfp") else (lambda p: True)   # gullwing: all pads are L/R
        gaps = [abs(p[1]) - p[3] / 2 - ew / 2 for p in pads if lr(p)]                 # left/right rows vs EP
        gaps += [abs(p[2]) - p[4] / 2 - eh / 2 for p in pads if not lr(p)]            # qfn top/bottom rows vs EP
        if gaps and min(gaps) < MIN_PAD_GAP:
            rep["warning"] = f"EP to pad gap {min(gaps):.3f} < {MIN_PAD_GAP}"
        ep_lines.append(pad(epnum, 0, 0, ew, eh, shape="rect", layers=("F.Cu", "F.Mask")))
        cov = ep.get("paste_coverage", 0.6)                           # split paste into N x N windows
        g = ep.get("paste_grid") or max(1, min(4, round(max(ew, eh) / 1.2)))
        sw, sh = ew * math.sqrt(cov) / g, eh * math.sqrt(cov) / g
        for i in range(g):
            for j in range(g):
                cx, cy = (i - (g - 1) / 2) * ew / g, (j - (g - 1) / 2) * eh / g
                ep_lines.append(pad("", cx, cy, round(sw, 3), round(sh, 3), layers=("F.Paste",)))
        rep["EP"] = (ew, eh, f"paste {g}x{g} @ {cov:.0%}")
    copper = pads + ([(None, 0, 0, dim(ep["length"])[1], dim(ep["width"])[1])] if ep else [])

    # ---- courtyard: union(pads, body) + excess, rounded up to the 0.01 mm grid (KLC F5.3)
    exc = fil[3]
    px = max([abs(p[1]) + p[3] / 2 for p in pads] + [bx])
    py = max([abs(p[2]) + p[4] / 2 for p in pads] + [by])
    cx, cy = rup(px + exc), rup(py + exc)
    rep["courtyard"] = (round(2 * cx, 2), round(2 * cy, 2))

    out = [f'(footprint {q(spec["name"])} (version 20221018) (generator "ecadlib_footprint_gen")',
           '  (layer "F.Cu")',
           f'  (descr {q(descr(spec, rep))})',
           f'  (tags {q(" ".join(filter(None, [t, fam, "polarized" if polarized else "", "IPC-7351B density-" + density])))})',
           '  (attr smd)',
           text("reference", "REF**", 0, -cy - 0.7, "F.SilkS"),
           text("value", spec["name"], 0, cy + 0.7, "F.Fab"),
           text("user", "${REFERENCE}", 0, 0, "F.Fab", size=round(min(1.0, max(0.25, min(bx, by) * 0.8)), 2))]

    # ---- fab: body outline; pin-1 / polarity chamfer on everything except plain 2-terminal parts (KLC F5.2)
    if two_term and not polarized:
        out.append(rect(-bx, -by, bx, by, "F.Fab", FAB_W))
    else:
        ch = min(1.0, 0.25 * min(2 * bx, 2 * by))
        out.append(poly([(-bx + ch, -by), (bx, -by), (bx, by), (-bx, by), (-bx, -by + ch)], "F.Fab", FAB_W))

    # ---- silk: body outline pushed out by SILK_FAB_OFFSET, cut back to SILK_PAD_CLEAR from every pad (KLC F5.1)
    c = SILK_PAD_CLEAR + SILK_W / 2
    sx, sy = bx + SILK_FAB_OFFSET, by + SILK_FAB_OFFSET
    p1 = next(p for p in pads if p[0] == "1")
    if two_term and polarized:
        # KiCad LED/diode style: a bracket that closes on the pin-1 (cathode / +) side
        yl = rup(max(sy, max(abs(p[2]) + p[4] / 2 for p in pads) + c))
        xl = -rup(max(sx, -(p1[1] - p1[3] / 2) + c))
        xe = max(bx, max(p[1] for p in pads))
        out += [line(xl, -yl, xl, yl, "F.SilkS", SILK_W), line(xl, -yl, xe, -yl, "F.SilkS", SILK_W),
                line(xl, yl, xe, yl, "F.SilkS", SILK_W)]
    else:
        box = [((-sx, -sy), (sx, -sy)), ((sx, -sy), (sx, sy)), ((sx, sy), (-sx, sy)), ((-sx, sy), (-sx, -sy))]
        segs = [s for b in box for s in _clip(b, copper, c)]
        out += [line(a[0], a[1], b[0], b[1], "F.SilkS", SILK_W) for a, b in segs]
        if not two_term:                                              # pin-1 triangle just above pad 1
            if t == "qfn" and spec.get("pins_top_bottom", 1) != 0:
                ax, ay = -sx, -sy
            else:
                ax, ay = p1[1], p1[2] - p1[4] / 2 - c
            out.append(pin1_triangle(ax, ay))

    out.append(rect(-cx, -cy, cx, cy, "F.CrtYd", CRT_W))
    out += [pad(*p) for p in pads] + ep_lines
    if spec.get("model_3d"):
        out.append(f'  (model {q(spec["model_3d"])} (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
    out.append(")")
    text_out = "\n".join(out) + "\n"
    rep["pads"] = [{"num": p[0], "x": p[1], "y": p[2], "w": p[3], "h": p[4]} for p in pads]
    from . import footprint_check
    rep["checks"] = [{"level": l, "rule": r_, "message": m}
                     for l, r_, m in footprint_check.check(text_out, allow_duplicate_pads=[str(spec["tab"]["number"])] if spec.get("tab") else None)]
    return text_out, rep


def pin1_triangle(x, y):
    """Filled silk triangle pointing down at (x, y) - the KiCad-library pin-1 convention."""
    return poly([(x, y), (x - 0.24, y - 0.33), (x + 0.24, y - 0.33)], "F.SilkS", SILK_W, fill="solid")


def descr(spec, r):
    s = spec.get("description", spec["name"])
    s += f" | IPC-7351B {r['ipc_family']} density {r['density']}: Z={r['Z']:.2f} G={r['G']:.2f} X={r['X']:.2f}"
    if spec.get("source", {}).get("datasheet"):
        s += f" | src {spec['source']['datasheet']}"
    return s + " | generated by ecadlib (prototype - review before use)"


def validate(spec):
    schema = json.loads((Path(__file__).with_name("package_schema.json")).read_text())
    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.validate(spec, schema)


# ---------------------------------------------------------------- comparison vs a reference footprint
def compare(mine_path, ref_text):
    from . import sexpr
    a, b = sexpr.parse(Path(mine_path).read_text()), sexpr.parse(ref_text)
    pa = {p["number"]: p for p in sexpr.pads(a) if p["number"]}
    pb = {p["number"]: p for p in sexpr.pads(b) if p["number"]}
    rows = []
    for k in sorted(set(pa) & set(pb), key=lambda s: int(s) if s.isdigit() else 999):
        x, y = pa[k], pb[k]
        rows.append({"pad": k, "mine": (x["x"], x["y"], x["w"], x["h"]), "ref": (y["x"], y["y"], y["w"], y["h"]),
                     "d_pos": (round(x["x"] - y["x"], 4), round(x["y"] - y["y"], 4)),
                     "d_size": (round(x["w"] - y["w"], 4), round(x["h"] - y["h"], 4))})
    return rows, sexpr.courtyard_bbox(a), sexpr.courtyard_bbox(b)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="generate .kicad_mod from package JSON files")
    g.add_argument("specs", nargs="+")
    g.add_argument("-o", "--out", default="out/footprints/ecadlib.pretty")
    g.add_argument("--density", choices="ABC")
    c = sub.add_parser("compare", help="compare a generated footprint's pads with a reference (path or URL)")
    c.add_argument("mine")
    c.add_argument("ref")
    a = ap.parse_args(argv)

    if a.cmd == "gen":
        outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
        for sp in a.specs:
            spec = json.loads(Path(sp).read_text())
            validate(spec)
            txt, rep = build(spec, a.density)
            from . import sexpr
            sexpr.parse(txt)                                          # sanity: balanced S-expression
            (outdir / f"{spec['name']}.kicad_mod").write_text(txt)
            p1 = rep["pads"][0]
            print(f"{spec['name']:<44} {rep['ipc_family']:<13} Z={rep['Z']:.2f} G={rep['G']:.2f} X={rep['X']:.2f} "
                  f"pad1 {p1['w']:.3f}x{p1['h']:.3f} @({p1['x']:.3f},{p1['y']:.3f}) "
                  f"crtyd {rep['courtyard'][0]:.2f}x{rep['courtyard'][1]:.2f}"
                  + (f"  WARN {rep['warning']}" if "warning" in rep else ""))
    else:
        if a.ref.startswith("http"):
            from .httpcache import CachedHTTP
            r = CachedHTTP(min_interval=0.5).get(a.ref)
            if not r.ok:
                sys.exit(f"fetch failed: {r.error or r.status}")
            ref = r.text
        else:
            ref = Path(a.ref).read_text()
        rows, ca, cb = compare(a.mine, ref)
        print(f"{'pad':>4} {'mine (x,y,w,h)':>30} {'kicad (x,y,w,h)':>30}  d_pos       d_size")
        for r in rows[:4] + (rows[-1:] if len(rows) > 4 else []):
            print(f"{r['pad']:>4} {str(r['mine']):>30} {str(r['ref']):>30}  {r['d_pos']} {r['d_size']}")
        if ca and cb:
            print(f"courtyard mine {ca[2]-ca[0]:.2f}x{ca[3]-ca[1]:.2f}  kicad {cb[2]-cb[0]:.2f}x{cb[3]-cb[1]:.2f}")


if __name__ == "__main__":
    main()
