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
}
FAB_TOL, PLACE_TOL = 0.05, 0.025      # IPC-7351B default F and P
MIN_PAD_GAP = 0.2                      # minimum copper gap between adjacent pads (solder-mask web)
SILK_W, FAB_W, CRT_W, SILK_PAD_CLEAR = 0.12, 0.10, 0.05, 0.2


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
def build(spec, density=None):
    """Return (kicad_mod_text, report_dict)."""
    density = density or spec.get("density", "B")
    t = spec["package_type"]
    body_x, body_y = dim(spec["body_length"]), dim(spec["body_width"])
    T = dim(spec["terminal_length"])
    pads, rep = [], {"name": spec["name"], "type": t, "density": density}

    if t == "chip":
        fam = "chip_small" if body_x[1] < 1.55 else "chip"
        fil = FILLETS[fam][density]
        r = ipc_land(body_x, T, body_y, fil)
        pads = [("1", -r["pad_ctr"], 0, r["pad_len"], r["X"]), ("2", r["pad_ctr"], 0, r["pad_len"], r["X"])]
        bx, by = body_x[1] / 2, body_y[1] / 2      # fab body = nominal L x W
        rep.update(ipc_family=fam, **{k: r[k] for k in ("Z", "G", "X")})
    elif t in ("gullwing", "qfn"):
        npins, e = spec["pin_count"], spec["pitch"]
        bw = dim(spec["terminal_width"])
        if t == "gullwing":
            fam = "gullwing" if e > 0.625 else "gullwing_fine"
            fil = FILLETS[fam][density]
            r = ipc_land(dim(spec["lead_span"]), T, bw, fil)
            X = min(r["X"], rdn(e - MIN_PAD_GAP))
            per = npins // 2
            for i in range(per):                                   # left: 1..n/2 top->bottom
                y = (i - (per - 1) / 2) * e
                pads.append((str(i + 1), -r["pad_ctr"], y, r["pad_len"], X))
            for i in range(per):                                   # right: bottom->top
                y = ((per - 1) / 2 - i) * e
                pads.append((str(per + i + 1), r["pad_ctr"], y, r["pad_len"], X))
            bx, by = body_y[1] / 2, body_x[1] / 2                  # body_width=E1 (X), body_length=D (Y)
            rep.update(ipc_family=fam, Z=r["Z"], G=r["G"], X=X, X_unclamped=r["X"])
        else:
            fam = "qfn"
            fil = FILLETS[fam][density]
            nlr = spec.get("pins_left_right", npins // 4)
            ntb = spec.get("pins_top_bottom", (npins - 2 * nlr) // 2)
            rx = ipc_land(body_x, T, bw, fil)                       # left/right pads (toe at x=+-D/2)
            ry = ipc_land(body_y, T, bw, fil)                       # top/bottom pads
            X = min(rx["X"], rdn(e - MIN_PAD_GAP))
            k = 1
            for i in range(nlr):                                    # left, top->bottom
                pads.append((str(k), -rx["pad_ctr"], (i - (nlr - 1) / 2) * e, rx["pad_len"], X)); k += 1
            for i in range(ntb):                                    # bottom, left->right
                pads.append((str(k), (i - (ntb - 1) / 2) * e, ry["pad_ctr"], X, ry["pad_len"])); k += 1
            for i in range(nlr):                                    # right, bottom->top
                pads.append((str(k), rx["pad_ctr"], ((nlr - 1) / 2 - i) * e, rx["pad_len"], X)); k += 1
            for i in range(ntb):                                    # top, right->left
                pads.append((str(k), ((ntb - 1) / 2 - i) * e, -ry["pad_ctr"], X, ry["pad_len"])); k += 1
            bx, by = body_x[1] / 2, body_y[1] / 2
            rep.update(ipc_family=fam, Z=rx["Z"], G=rx["G"], X=X, Zy=ry["Z"], Gy=ry["G"])
    else:
        raise ValueError(f"unsupported package_type {t}")

    ep = spec.get("exposed_pad")
    ep_lines = []
    if ep:
        ew, eh = dim(ep["length"])[1], dim(ep["width"])[1]
        epnum = str(spec["pin_count"] + 1)
        lr = (lambda p: abs(p[1]) > abs(p[2])) if t == "qfn" else (lambda p: True)   # gullwing: all pads are L/R
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

    # ---- courtyard: union(pads, body) + excess, rounded up to 0.01 mm
    exc = fil[3]
    px = max([abs(p[1]) + p[3] / 2 for p in pads] + [bx])
    py = max([abs(p[2]) + p[4] / 2 for p in pads] + [by])
    cx, cy = rup(px + exc), rup(py + exc)
    rep["courtyard"] = (2 * cx, 2 * cy)

    out = [f'(footprint {q(spec["name"])} (version 20221018) (generator "ecadlib_footprint_gen")',
           '  (layer "F.Cu")',
           f'  (descr {q(descr(spec, rep))})',
           f'  (tags {q(spec["package_type"] + " IPC-7351B density-" + density)})',
           '  (attr smd)',
           text("reference", "REF**", 0, -cy - 0.7, "F.SilkS"),
           text("value", spec["name"], 0, cy + 0.7, "F.Fab"),
           text("user", "${REFERENCE}", 0, 0, "F.Fab", size=round(min(1.0, max(0.25, min(bx, by) * 0.8)), 2))]

    # ---- fab: body outline, pin-1 chamfer on ICs
    if t == "chip":
        out.append(rect(-bx, -by, bx, by, "F.Fab", FAB_W))
    else:
        ch = min(1.0, 0.25 * min(2 * bx, 2 * by))
        out.append(poly([(-bx + ch, -by), (bx, -by), (bx, by), (-bx, by), (-bx, -by + ch)], "F.Fab", FAB_W))

    # ---- silk: keep >= SILK_PAD_CLEAR from copper
    so = SILK_W / 2 + 0.05                                            # silk offset outside body
    sx, sy = bx + so, by + so
    if t == "chip":
        inner_x = min(abs(p[1]) - p[3] / 2 for p in pads) - SILK_PAD_CLEAR - SILK_W / 2
        sy = max(sy, max(p[4] for p in pads) / 2 + SILK_PAD_CLEAR + SILK_W / 2) if inner_x < 0.4 else sy
        if inner_x > 0.1:                                             # too small (0402) -> no silk
            out += [line(-inner_x, -sy, inner_x, -sy, "F.SilkS", SILK_W), line(-inner_x, sy, inner_x, sy, "F.SilkS", SILK_W)]
    elif t == "gullwing":
        pad_top = max(abs(p[2]) + p[4] / 2 for p in pads)             # outermost pad edge along Y
        sy = max(sy, pad_top + SILK_PAD_CLEAR + SILK_W / 2)           # keep silk clear of corner pads
        for s in (-1, 1):
            out.append(line(-sx, s * sy, sx, s * sy, "F.SilkS", SILK_W))
        out.append(pin1_triangle(-sx - 0.54, -sy))
    else:                                                             # qfn: corner L-marks + pin-1 triangle
        gx = max(abs(p[1]) + p[3] / 2 for p in pads if abs(p[2]) > abs(p[1])) + SILK_PAD_CLEAR + SILK_W / 2
        gy = max(abs(p[2]) + p[4] / 2 for p in pads if abs(p[1]) > abs(p[2])) + SILK_PAD_CLEAR + SILK_W / 2
        for sxn in (-1, 1):
            for syn in (-1, 1):
                if gx < sx:
                    out.append(line(sxn * gx, syn * sy, sxn * sx, syn * sy, "F.SilkS", SILK_W))
                if gy < sy and not (sxn < 0 and syn < 0):
                    out.append(line(sxn * sx, syn * gy, sxn * sx, syn * sy, "F.SilkS", SILK_W))
        out.append(pin1_triangle(-sx, -sy))

    out.append(rect(-cx, -cy, cx, cy, "F.CrtYd", CRT_W))
    out += [pad(*p) for p in pads] + ep_lines
    if spec.get("model_3d"):
        out.append(f'  (model {q(spec["model_3d"])} (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
    out.append(")")
    rep["pads"] = [{"num": p[0], "x": p[1], "y": p[2], "w": p[3], "h": p[4]} for p in pads]
    return "\n".join(out) + "\n", rep


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
