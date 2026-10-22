"""Parametric IPC-7351B-style footprint generator -> KiCad .kicad_mod (S-expression).

Supported package_type: chip (2-terminal R/C/L), molded, gullwing (dual-row SOIC/SOP/TSSOP/SOT), qfp, qfn (+EP);
and, in footprint_adv.py: bga (BGA/LGA/CSP), pin_header, connector and custom (explicit pad table from the
datasheet drawing: polygon pads, slots, mounting pads, keep-outs).
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
import argparse, json, math, re, sys
from pathlib import Path

# IPC-7351B fillet goals per density A/B/C (most/nominal/least):
#   (Jz, Jg, Js, courtyard excess, round-off)
# Jz is the goal at the outer end of the land (sets Z), Jg at the inner end (sets G), Js at the side (sets X).
# For most families Jz is the toe and Jg the heel; for inward-L molded parts it is the other way round.
# Values are the ones KiCad builds its library with (kicad-footprint-generator src/kilibs/config/ipc_configs/
# ipc_7351b.yaml and ipc7351B_2terminal.yaml); docs/footprint-rules.md lists the sources per family.
FILLETS = {
    # Table 3-5: rectangular end-cap chips >= 0603 (1608)
    "chip": {"A": (0.55, 0.00, 0.05, 0.50, 0.05), "B": (0.35, 0.00, 0.00, 0.25, 0.05), "C": (0.15, 0.00, -0.05, 0.10, 0.05)},
    # Table 3-6: chips < 0603 (0402, 0201) - smaller toe, 0.15 courtyard, 0.02 round-off
    "chip_small": {"A": (0.30, 0.00, 0.05, 0.20, 0.02), "B": (0.20, 0.00, 0.00, 0.15, 0.02), "C": (0.10, 0.00, -0.05, 0.10, 0.02)},
    # Table 3-2: gull-wing, pitch >= 0.625 mm (SOIC, SOT-23, SOT-223, TSSOP-0.65)
    "gullwing": {"A": (0.55, 0.45, 0.05, 0.50, 0.05), "B": (0.35, 0.35, 0.03, 0.25, 0.05), "C": (0.15, 0.25, 0.01, 0.10, 0.05)},
    # Table 3-3: gull-wing, pitch < 0.625 mm (side fillet goes negative)
    "gullwing_fine": {"A": (0.55, 0.45, 0.01, 0.50, 0.05), "B": (0.35, 0.35, -0.02, 0.25, 0.05), "C": (0.15, 0.25, -0.04, 0.10, 0.05)},
    # Table 3-15: QFN / DFN / SON with a toe fillet (no heel, negative side)
    "qfn": {"A": (0.40, 0.00, -0.04, 0.50, 0.05), "B": (0.30, 0.00, -0.04, 0.25, 0.05), "C": (0.20, 0.00, -0.04, 0.10, 0.05)},
    # Table 3-13: inward-L / molded body (tantalum, SMA/SMB/SMC, SOD-123F): big fillet at the outer
    # (heel) end, small at the inner (toe) end under the body
    "molded": {"A": (0.80, 0.25, 0.01, 0.50, 0.05), "B": (0.50, 0.15, -0.05, 0.25, 0.05), "C": (0.20, 0.07, -0.10, 0.10, 0.05)},
    # IPC-7351 (2005) Table 3-7: MELF / MiniMELF cylindrical end caps (no 7351B table in KiCad)
    "melf": {"A": (0.60, 0.20, 0.10, 0.50, 0.05), "B": (0.40, 0.10, 0.05, 0.25, 0.05), "C": (0.20, 0.02, 0.01, 0.10, 0.05)},
    # IPC-7352 flat lead (outward flat ribbon leads)
    "flat": {"A": (0.30, 0.00, 0.05, 0.20, 0.05), "B": (0.20, 0.00, 0.00, 0.15, 0.05), "C": (0.10, 0.00, 0.00, 0.10, 0.05)},
}
FAB_TOL, PLACE_TOL = 0.10, 0.05       # F and P as KiCad's generator uses them
MIN_PAD_GAP = 0.2                      # minimum copper gap between adjacent pads (solder-mask web)
MIN_EP_CLEAR = 0.2                     # exposed pad to signal pad (KLC F6.3); also KiCad's minimum heel gap / 2
SILK_W, FAB_W, CRT_W, SILK_PAD_CLEAR = 0.12, 0.10, 0.05, 0.2
SILK_CLEAR_SMALL, SILK_MIN_LEN = 0.1, 0.2   # KiCad falls back to 0.1 clearance when 0.2 leaves no line >= 0.2 long
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


def rnd(x, g):
    """Round to the nearest multiple of g, the way KiCad's generator does (Python round)."""
    return round(round(x / g) * g, 4)


def ipc_land(L, T, W, fillet, F=FAB_TOL, P=PLACE_TOL, S=None, min_gap=None):
    """L,T,W(,S) = (min,nom,max) tuples. Returns dict(Z,G,X,pad_len,pad_ctr) - see module docstring.
    S, when the drawing gives the inner span directly, is used as is (no RMS adjustment).
    min_gap: the heel gap G is never allowed below this (EP clearance; KiCad's gull-wing 0.4 mm floor)."""
    jz, jg, js, _, base = fillet
    CL, CW = L[2] - L[0], W[2] - W[0]
    if S is not None:
        smin, smax = S[0], S[2]
        cs = smax - smin
    else:
        CT = T[2] - T[0]
        smin, smax = L[0] - 2 * T[2], L[2] - 2 * T[0]
        cs = math.sqrt(CL ** 2 + 2 * CT ** 2)              # RMS tolerance of S
        adj = ((smax - smin) - cs) / 2
        smin, smax = smin + adj, smax - adj
    Z = rnd(L[0] + 2 * jz + math.sqrt(CL ** 2 + F ** 2 + P ** 2), base)
    G = rnd(smax - 2 * jg - math.sqrt(cs ** 2 + F ** 2 + P ** 2), base)
    X = rnd(W[0] + 2 * js + math.sqrt(CW ** 2 + F ** 2 + P ** 2), base)
    G = max(G, 0.0, min_gap or 0.0)
    return {"Z": Z, "G": G, "X": X, "pad_len": round((Z - G) / 2, 4), "pad_ctr": round((Z + G) / 4, 4),
            "Smin": smin, "Smax": smax}


# ---------------------------------------------------------------- S-expression writer
def n(x):
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def q(s):
    s = re.sub(r"\s+", " ", str(s)).strip()          # one line: KiCad's lexer doesn't take raw newlines in strings
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
    if spec.get("ipc_family") in FILLETS:                     # forced (ecadlib.stdparts: KiCad's force_small_pitch_ipc_definition)
        return spec["ipc_family"]
    lt = spec.get("lead_type")
    if t == "chip":
        if lt in ("molded", "melf", "flat"):
            return lt
        return "chip_small" if dim(spec["body_length"])[1] < 1.55 else "chip"
    if t == "molded":
        return lt if lt in ("melf", "flat") else "molded"
    if lt and (t in ("qfn", "qfp") or (t == "gullwing" and lt != "flat")):
        raise ValueError(f"lead_type {lt!r} doesn't apply to a {t} package"
                         + (" (only \"flat\" does)" if t == "gullwing" else ""))
    if t == "gullwing":
        if lt == "flat":
            return "flat"
        pitch = spec.get("pitch") or _min_pitch(spec)
        return "gullwing" if pitch is None or pitch >= 0.625 else "gullwing_fine"
    if t == "qfn":
        return "qfn"
    if t == "qfp":
        return "gullwing" if spec["pitch"] >= 0.625 else "gullwing_fine"
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
    r = ipc_land(L, dim(spec.get("terminal_length")), W, fil, S=dim(spec.get("inner_span")))
    pads = [("1", -r["pad_ctr"], 0, r["pad_len"], r["X"]), ("2", r["pad_ctr"], 0, r["pad_len"], r["X"])]
    return pads, (L[1] / 2, dim(spec["body_width"])[1] / 2), {k: r[k] for k in ("Z", "G", "X")}


def _gullwing(spec, fil):
    T, bw, span = dim(spec["terminal_length"]), dim(spec["terminal_width"]), dim(spec["lead_span"])
    ep = spec.get("exposed_pad")
    min_gap = (dim(ep["length"])[1] if ep else 0) + 2 * MIN_EP_CLEAR    # KiCad: G >= EP + 0.4, so >= 0.4 without EP
    r = ipc_land(span, T, bw, fil, min_gap=min_gap)
    pos = spec.get("pin_positions")
    if not pos:                                               # standard dual row: 1..n/2 down the left, then up the right
        e, per = spec["pitch"], spec["pin_count"] // 2
        pos = [{"number": str(i + 1), "side": "L", "offset": (i - (per - 1) / 2) * e} for i in range(per)]
        pos += [{"number": str(per + i + 1), "side": "R", "offset": ((per - 1) / 2 - i) * e} for i in range(per)]
    pitch = _min_pitch({"pin_positions": pos})
    X = min(r["X"], rdn(pitch - MIN_PAD_GAP)) if pitch else r["X"]
    pads = [(str(p["number"]), (-1 if p["side"] == "L" else 1) * r["pad_ctr"], p["offset"], r["pad_len"], X) for p in pos]
    rep = {"Z": r["Z"], "G": r["G"], "X": X, "X_unclamped": r["X"]}
    tab = spec.get("tab")
    if tab:                                                   # SOT-223 / DPAK style wide tab on one side
        tr = ipc_land(span, dim(tab.get("length") or spec["terminal_length"]), dim(tab["width"]), fil, min_gap=min_gap)
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
    ep = spec.get("exposed_pad")                              # heels pulled back to keep MIN_EP_CLEAR to the EP (KLC F6.3)
    gx = dim(ep["length"])[1] + 2 * MIN_EP_CLEAR if ep else None
    gy = dim(ep["width"])[1] + 2 * MIN_EP_CLEAR if ep else None
    rx = ipc_land(span_x, T, bw, fil, min_gap=gx)             # left/right pads
    ry = ipc_land(span_y, T, bw, fil, min_gap=gy)             # top/bottom pads
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


def corner_r(num, w, h):
    """Corner radius of a pad as pad() draws it: roundrect 25 % of the short side, capped at 0.25; EP is a plain rect."""
    return 0.0 if num is None else min(0.25 * min(w, h), 0.25)


def _clip(seg, pads, c, min_len=SILK_MIN_LEN):
    """Cut an axis-aligned silk segment wherever its centre line comes within c of a pad's real (rounded)
    outline, as KiCad measures it. pads as (num, x, y, w, h). Pieces shorter than min_len are dropped."""
    (x1, y1), (x2, y2) = seg
    horiz = abs(y1 - y2) < 1e-9
    a, b = (min(x1, x2), max(x1, x2)) if horiz else (min(y1, y2), max(y1, y2))
    keep = [(a, b)]
    for num, px, py, w, h in pads:
        r = corner_r(num, w, h)
        along, across = (px, py) if horiz else (py, px)
        half_along, half_across = (w / 2, h / 2) if horiz else (h / 2, w / 2)
        e = max(abs((y1 if horiz else x1) - across) - (half_across - r), 0)   # distance past the straight edge
        if e >= r + c:
            continue
        half = half_along - r + math.sqrt((r + c) ** 2 - e ** 2)
        lo, hi = along - half, along + half
        keep = [part for s, e_ in keep for part in ((s, min(e_, lo)), (max(s, hi), e_)) if part[1] - part[0] > 1e-6]
    keep = [(s, e) for s, e in keep if e - s >= min_len - 1e-6]
    return [((round(s, 4), y1), (round(e, 4), y1)) if horiz else ((x1, round(s, 4)), (x1, round(e, 4))) for s, e in keep]


def _left_right(spec, pads):
    """For each pad (in builder order), True when it sits in a left/right row (its length runs along X).
    Taken from how the builders lay pads out, not guessed from coordinates: the end pads of a tall dual-row part
    (DFN 2 x 4) or of a rectangular QFN have |y| > |x| but are still left/right pads."""
    t = spec["package_type"]
    if t not in ("qfn", "qfp"):
        return [True] * len(pads)                             # chips, gull-wings (and their tab): all along X
    npins = spec["pin_count"]
    nlr = spec.get("pins_left_right", npins // 4)
    ntb = spec.get("pins_top_bottom", (npins - 2 * nlr) // 2)
    side = ([True] * nlr + [False] * ntb) * 2                 # _qfn(): left, bottom, right, top
    return [side[i] if i < len(side) else True for i in range(len(pads))]


def _apply_land(spec, pads):
    """Replace the IPC-computed signal pads with the datasheet's recommended land (the tab keeps its IPC size). A pin 1
    the land draws another size (pin1_length / pin1_width) keeps the row's outer end and its lead's centre line."""
    land = spec["land"]
    lcy = land.get("pad_center_y", land["pad_center"])
    n_sig = len(pads) - (1 if spec.get("tab") else 0)
    lr = _left_right(spec, pads)
    out = []
    for i, (num, x, y, w, h) in enumerate(pads):
        ll, lw, lc, cy = land["pad_length"], land["pad_width"], land["pad_center"], lcy
        if str(num) == "1" and (land.get("pin1_length") or land.get("pin1_width")):
            l1 = land.get("pin1_length") or ll
            lc, cy = lc + (ll - l1) / 2, cy + (ll - l1) / 2
            ll, lw = l1, land.get("pin1_width") or lw
        if i >= n_sig:
            out.append((num, x, y, w, h))
        elif lr[i]:                                           # left/right row: length runs along X
            out.append((num, math.copysign(lc, x), y, ll, lw))
        else:                                                 # top/bottom row (QFN/QFP)
            out.append((num, x, math.copysign(cy, y), lw, ll))
    return out


def sanity(spec):
    """Catch the usual datasheet-reading mistakes before they turn into a plausible-looking but wrong footprint.
    Returns a list of plain-language problems (empty when the numbers hang together)."""
    errs, t = [], spec["package_type"]
    nom = lambda k: dim(spec[k])[1] if spec.get(k) is not None else None
    for k in ("body_length", "body_width", "lead_span", "terminal_length", "terminal_width", "inner_span", "pitch"):
        v = spec.get(k)
        lo, _, hi = dim(v) if v is not None and k != "pitch" else (v, v, v)
        if v is None:
            continue
        if lo > hi:
            errs.append(f"{k}: min {lo} is bigger than max {hi}")
        if hi > 60:
            errs.append(f"{k} = {hi}: that looks like mils or 0.01 mm units; the spec is in millimetres")
    if errs:
        return errs
    L, W, T = nom("body_length"), nom("body_width"), nom("terminal_length")
    if t in ("chip", "molded") and L is not None and L < 0.2:     # the smallest chip (008004) is 0.25 mm long
        errs.append(f"body_length {L} mm is smaller than any chip part: is the drawing in inches? The spec is in millimetres")
    pos = spec.get("pin_positions") or []
    if t == "gullwing" and pos and spec.get("body_length") is not None:
        lim = dim(spec["body_length"])[2] / 2
        far = [p for p in pos if abs(p["offset"]) > lim + 1e-6]
        if far:
            errs.append(f"pin {far[0]['number']} offset {far[0]['offset']} is outside the {round(L, 3)} mm body length "
                        f"(offsets run from -{round(lim, 3)} to +{round(lim, 3)}): check the drawing")
    if t in ("chip", "molded") and T and not spec.get("inner_span") and 2 * dim(spec["terminal_length"])[0] >= dim(spec["body_length"])[2]:
        errs.append(f"terminal_length {T} is half the body length or more: it should be one end-cap/foot, not the whole part")
    if t in ("gullwing", "qfp"):
        span = nom("lead_span")
        if span is not None and span < W + 0.1:                     # leads must stick out past the plastic
            errs.append(f"lead_span {span} is not bigger than body_width {round(W, 3)}: lead_span is the tip-to-tip size "
                        "across the leads (E), body_width the plastic (E1); are they swapped?")
        if span is not None and T and T >= span / 2:
            errs.append(f"terminal_length {T} is half the lead span or more")
    n = spec.get("pin_count")
    if t == "gullwing" and n and not spec.get("pin_positions") and n % 2:
        errs.append(f"pin_count {n} is odd: a dual-row part needs an even count, or list the pads in pin_positions "
                    "(as for SOT-23)")
    dep = re.match(r"[A-Za-z][A-Za-z0-9]*?-(\d+)-(\d+)(?=_|$)", str(spec.get("name") or ""))
    if t == "gullwing" and n and not spec.get("pin_positions") and dep and n < int(dep.group(2)) and \
            int(dep.group(1)) < int(dep.group(2)):        # UCC256301's SOIC-14-16 built as 7 + 7 would be a wrong land
        errs.append(f"{spec['name']} puts {dep.group(1)} pins on {dep.group(2)} places: list the pads in pin_positions "
                    f"(a full dual row of {n} pads puts them on the wrong places)")
    if t in ("qfn", "qfp") and n:
        nlr = spec.get("pins_left_right", n // 4)
        ntb = spec.get("pins_top_bottom", (n - 2 * nlr) // 2)
        if 2 * nlr + 2 * ntb != n:
            errs.append(f"pin_count {n} doesn't equal 2 x {nlr} (left/right) + 2 x {ntb} (top/bottom); "
                        "set pins_left_right / pins_top_bottom")
    if t in ("gullwing", "qfp", "qfn") and spec.get("pitch") and spec.get("pin_count") and not spec.get("pin_positions"):
        n = spec["pin_count"]
        per = n // 2 if t == "gullwing" else spec.get("pins_left_right", n // 4)
        row = (per - 1) * spec["pitch"]
        if per > 1 and row >= (L if t == "gullwing" else max(L, W)):
            errs.append(f"{per} pins at {spec['pitch']} mm pitch need {row:.2f} mm, more than the {L} mm body: check pitch and pin_count")
        tw = spec.get("terminal_width")
        if tw and dim(tw)[0] >= spec["pitch"]:
            errs.append(f"terminal_width {dim(tw)[0]} is not smaller than the {spec['pitch']} mm pitch")
    if t == "qfn" and T and T >= min(L, W) / 2:
        errs.append(f"terminal_length {T} reaches the middle of the {L} x {W} body")
    ep = spec.get("exposed_pad")
    bx, by = (W, L) if t == "gullwing" else (L, W)            # EP length is along X: gullwing X is body_width (E1)
    if ep and (dim(ep["length"])[1] >= bx or dim(ep["width"])[1] >= by):
        errs.append("the exposed pad is as big as the body: check its length/width")
    elif ep and t == "qfn" and T:
        tmin = dim(spec["terminal_length"])[0]
        tb = spec.get("pins_top_bottom", 1)
        if dim(ep["length"])[1] + 2 * tmin > dim(spec["body_length"])[2] or (
                tb and dim(ep["width"])[1] + 2 * tmin > dim(spec["body_width"])[2]):
            errs.append("the exposed pad would overlap the terminals: check its size against the body and terminal_length")
    return errs


def orient_ep(spec):
    """(spec, notes): the spec with its exposed pad the way build() lays it. Each turn needs the read's own numbers to
    rule the other way out, never a reference footprint; the cross-check compares what is built (crosscheck.critical).
    - gullwing: a PowerPAD read long side first in a body that runs down the rows turns to run with the body
      (TLC5951DAP, benchmark 2026-10-18: 5.7 x 3.04 across the rows pushed every heel out past the body).
    - qfn with pins on two sides only (DFN): an exposed pad that cannot sit between the two rows of terminals as read
      (pad + 2 terminal lengths wider than the body, at nominal) but clears them turned, by MIN_EP_CLEAR a side, is
      turned (LT3652IDD, benchmark 2026-10-21: DFN-12 3 x 3, EP 2.38 x 1.65 read with 0.4 mm terminals; 1.65 across
      the rows). When both ways fit, or neither, the pad stays as read."""
    ep, t = spec.get("exposed_pad"), spec.get("package_type")
    if not isinstance(ep, dict) or ep.get("length") is None or ep.get("width") is None:
        return spec, []
    try:
        el, ew = dim(ep["length"])[1], dim(ep["width"])[1]
        turned = dict(spec, exposed_pad=dict(ep, length=ep["width"], width=ep["length"]))
        if t == "gullwing" and spec.get("body_length") and spec.get("body_width") and \
                el > ew and dim(spec["body_length"])[1] > dim(spec["body_width"])[1]:
            return turned, [f"exposed pad turned to run with the body: {round(ew, 3)} across the rows, "
                            f"{round(el, 3)} along them"]
        if t == "qfn" and spec.get("pins_top_bottom") == 0 and spec.get("terminal_length") and spec.get("body_length"):
            room = dim(spec["body_length"])[1] - 2 * dim(spec["terminal_length"])[1]     # between the two rows
            if el > room + 1e-9 and ew <= room - 2 * MIN_EP_CLEAR + 1e-9 and (not spec.get("body_width") or el < dim(spec["body_width"])[1]):
                return turned, [f"exposed pad turned to clear the terminals: {round(ew, 3)} across the rows, "
                                f"{round(el, 3)} along them ({round(el, 3)} does not fit the {round(room, 3)} mm "
                                "between the rows)"]
    except (TypeError, KeyError, ValueError):         # a malformed number: sanity() says what is wrong
        pass
    return spec, []


ADVANCED = ("bga", "pin_header", "connector", "custom")


def build(spec, density=None):
    """Return (kicad_mod_text, report_dict)."""
    if spec["package_type"] in ADVANCED:
        from . import footprint_adv
        return footprint_adv.build(spec, density)
    spec, turned = orient_ep(spec)
    problems = sanity(spec)
    if problems:
        raise ValueError("the package numbers don't add up: " + "; ".join(problems))
    density = density or spec.get("density", "B")
    t = spec["package_type"]
    fam = family(spec)
    fil = FILLETS[fam][density]
    rep = {"name": spec["name"], "type": t, "density": density, "ipc_family": fam, **({"notes": turned} if turned else {})}
    if t in ("chip", "molded"):
        pads, (bx, by), r = _two_terminal(spec, fil)
    elif t == "gullwing":
        pads, (bx, by), r = _gullwing(spec, fil)
    else:                                                     # qfn, qfp
        pads, (bx, by), r = _qfn(spec, fil)
    rep.update(r)
    want, numbered = spec.get("pin_count"), len({p[0] for p in pads})
    # package_schema.json: signal pins, so a tab that is pin 2 again (SOT-223-3_TabPin2) is not counted twice; a spec
    # file written when this said "a tab counts" (every pad) still builds. Only with pin_positions: in the standard
    # dual row such a tab lands on the other row's leads
    if want and want == numbered != len(pads) and not spec.get("pin_positions"):
        raise ValueError(f"the tab repeats a lead number, so the {want} pad numbers are on {len(pads)} pads: a tab "
                         "that repeats a lead number needs pin_positions (in the standard dual row it lands on the "
                         "other row's leads)")
    if want and want != len(pads) and not (want == numbered and spec.get("pin_positions")):
        raise ValueError(f"pin_count is {want} but the layout gives {numbered} pad numbers"
                         f"{f' on {len(pads)} pads' if len(pads) != numbered else ''} (count each pad number once, "
                         "without an exposed pad; a tab with a number of its own counts, a tab that repeats a lead "
                         "number needs pin_positions)")
    short = [p for p in pads if min(p[3], p[4]) < 0.1]
    if short:
        why = "the exposed pad leaves no room for the pads" if spec.get("exposed_pad") else "the lead dimensions leave no room"
        raise ValueError(f"{why}: pad {short[0][0]} would be {min(short[0][3], short[0][4]):.2f} mm (check the drawing)")
    if spec.get("land"):                                      # the datasheet's recommended land pattern wins over IPC
        pads = _apply_land(spec, pads)
        lw, ll, lc = spec["land"]["pad_width"], spec["land"]["pad_length"], spec["land"]["pad_center"]
        rep.update({"Z": round(2 * lc + ll, 4), "G": round(2 * lc - ll, 4), "X": lw, "land_source": "datasheet"})
    polarized = bool(spec.get("polarized"))
    two_term = t in ("chip", "molded") or (t == "gullwing" and len(pads) == 2)

    ep = spec.get("exposed_pad")
    ep_lines = []
    if ep:
        ew, eh = dim(ep["length"])[1], dim(ep["width"])[1]
        land = spec.get("land") or {}
        if land.get("ep_across") and land.get("ep_along"):   # the maker's thermal land (TI PowerPAD: 3.4 x 5 around a
            ew, eh = land["ep_across"], land["ep_along"]      # 3 x 3 exposed pad), as KiCad draws it
            rep["ep_land"] = "datasheet"
        epnum = str(ep.get("number") or (spec.get("pin_count") or len(pads)) + 1)   # pin_positions specs may omit pin_count
        lr = _left_right(spec, pads)                                                  # gullwing: all pads are L/R
        gaps = [abs(p[1]) - p[3] / 2 - ew / 2 for p, s in zip(pads, lr) if s]         # left/right rows vs EP
        gaps += [abs(p[2]) - p[4] / 2 - eh / 2 for p, s in zip(pads, lr) if not s]    # qfn top/bottom rows vs EP
        if gaps and min(gaps) < MIN_PAD_GAP - 1e-6:
            rep["warning"] = f"EP to pad gap {min(gaps):.3f} < {MIN_PAD_GAP}"
        ep_lines.append(pad(epnum, 0, 0, ew, eh, shape="rect", layers=("F.Cu", "F.Mask")))
        cov = ep.get("paste_coverage", 0.65)                          # KLC F6.3: 50-80 %, 65 % recommended
        grid = ep.get("paste_grid")                                   # split into windows of about 1.2 mm or less
        gx = grid or max(1, min(5, math.ceil(ew / 1.2 - 1e-9)))
        gy = grid or max(1, min(5, math.ceil(eh / 1.2 - 1e-9)))
        sw, sh = ew * math.sqrt(cov) / gx, eh * math.sqrt(cov) / gy
        for i in range(gx):
            for j in range(gy):
                cx, cy = (i - (gx - 1) / 2) * ew / gx, (j - (gy - 1) / 2) * eh / gy
                ep_lines.append(pad("", round(cx, 4), round(cy, 4), round(sw, 3), round(sh, 3), layers=("F.Paste",)))
        rep["EP"] = (ew, eh, f"paste {gx}x{gy} @ {cov:.0%}")
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
           text("user", "${REFERENCE}", 0, 0, "F.Fab", size=math.floor(100 * min(1.0, max(0.25, 0.5 * max(bx, by)))) / 100)]

    # ---- fab: body outline; pin-1 / polarity chamfer on everything except plain 2-terminal parts (KLC F5.2)
    if two_term and not polarized:
        out.append(rect(-bx, -by, bx, by, "F.Fab", FAB_W))
    else:
        ch = min(1.0, 0.25 * min(2 * bx, 2 * by))
        if two_term:                                                  # KiCad chip LED/diode marker: 0.3 .. 1.0
            ch = max(ch, min(0.3, bx, by))
        out.append(poly([(-bx + ch, -by), (bx, -by), (bx, by), (-bx, by), (-bx, -by + ch)], "F.Fab", FAB_W))

    # ---- silk: body outline pushed out by SILK_FAB_OFFSET, cut back to SILK_PAD_CLEAR from every pad (KLC F5.1)
    c = SILK_PAD_CLEAR + SILK_W / 2
    sx, sy = bx + SILK_FAB_OFFSET, by + SILK_FAB_OFFSET
    p1 = next((p for p in pads if p[0] == "1"), None)
    if p1 is None:                                                    # e.g. A/K numbering: mark the first pad,
        p1 = pads[0]                                                  # the checker reports the missing "1"
        rep["warning"] = (rep.get("warning", "") + "; " if rep.get("warning") else "") + \
            f"no pad numbered 1; KiCad numbers pins 1, 2, ... (diodes: 1 = cathode), marked pad {p1[0]} as pin 1"
    if two_term and polarized:
        # KiCad LED/diode style: a bracket that closes on the pin-1 (cathode / +) side
        yl = round(max(sy, max(abs(p[2]) + p[4] / 2 for p in pads) + c), 4)
        xl = -round(max(sx, -(p1[1] - p1[3] / 2) + c), 4)
        xe = max(bx, max(p[1] for p in pads))
        out += [line(xl, -yl, xl, yl, "F.SilkS", SILK_W), line(xl, -yl, xe, -yl, "F.SilkS", SILK_W),
                line(xl, yl, xe, yl, "F.SilkS", SILK_W)]
        silk_y = yl
    else:
        box = [((-sx, -sy), (sx, -sy)), ((sx, -sy), (sx, sy)), ((sx, sy), (-sx, sy)), ((-sx, sy), (-sx, -sy))]
        segs = [s for b in box for s in _clip(b, copper, c)]
        if not segs:                                                  # tiny part (0402): KiCad's small-part clearance
            c = SILK_CLEAR_SMALL + SILK_W / 2
            segs = [s for b in box for s in _clip(b, copper, c)]
        out += [line(a[0], a[1], b[0], b[1], "F.SilkS", SILK_W) for a, b in segs]
        silk_y = max((abs(v[1]) for sg in segs for v in sg), default=0.0)
        if not two_term:                                              # pin-1 triangle just above pad 1
            if t == "qfn" and spec.get("pins_top_bottom", 1) != 0:
                ax, ay = -sx, -sy
                for _ in pads:                                        # tiny QFN: the top row reaches the corner,
                    hit = [p for p in pads if p[2] - p[4] / 2 - c < ay and p[2] + p[4] / 2 + c > ay - 0.33 and
                           p[1] - p[3] / 2 - c < ax + 0.24 and p[1] + p[3] / 2 + c > ax - 0.24]
                    if not hit:                                       # so the triangle moves out past it (KLC F5.1)
                        break
                    ax = round(min(p[1] - p[3] / 2 for p in hit) - c - 0.24, 4)
            else:
                ax, ay = p1[1], p1[2] - p1[4] / 2 - c
            out.append(pin1_triangle(ax, ay))

    if silk_y + SILK_W / 2 > cy:                                      # silk outline past the courtyard (density C):
        out[5] = text("reference", "REF**", 0, -round(silk_y + SILK_W / 2 + 0.7, 4), "F.SilkS")  # keep REF** off it
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


def shorts(rep):
    """build()'s copper errors (overlapping or touching pads: a short on the board) as one sentence, or None.
    `footprint gen` refuses to write such a footprint; the librarian's release check holds it."""
    short = [c["message"] for c in rep.get("checks") or [] if c["level"] == "error" and c["rule"] == "copper"]
    return ("the pads would short: " + "; ".join(short) + " (check pitch, pin_count and terminal_length against the "
            "drawing)") if short else None


def altium_footprint(spec, density=None):
    """Altium footprint dict (altium_lib's writer model) for any spec, from the same .kicad_mod geometry:
    altium_lib.footprint_from_spec handles SMD, through-hole (round, square, slots), NPTH holes, polygon pads (anchor
    pad + copper / mask / paste regions) and keep-outs. Returns (footprint, notes)."""
    from . import altium_lib as AL
    fp, rep = AL.footprint_from_spec(spec, density)
    return fp, rep.get("altium_notes", [])


def pin1_triangle(x, y):
    """Filled silk triangle pointing down at (x, y) - the KiCad-library pin-1 convention."""
    return poly([(x, y), (x - 0.24, y - 0.33), (x + 0.24, y - 0.33)], "F.SilkS", SILK_W, fill="solid")


def descr(spec, r):
    s = spec.get("description", spec["name"])
    s += f" | IPC-7351B {r['ipc_family']} density {r['density']}: Z={r['Z']:.2f} G={r['G']:.2f} X={r['X']:.2f}"
    if spec.get("source", {}).get("datasheet"):
        s += f" | src {spec['source']['datasheet']}"
    return s


_VALIDATOR = []


def validate(spec):
    """Raise jsonschema.ValidationError (the best match, as jsonschema.validate does) when the spec breaks the schema.
    The validator is built once: jsonschema.validate re-checks the whole schema on every call (~80 ms)."""
    import jsonschema                                   # a runtime dependency: never skip validation silently
    if not _VALIDATOR:
        schema = json.loads((Path(__file__).with_name("package_schema.json")).read_text(encoding="utf-8"))
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
        _VALIDATOR.append(cls(schema))
    err = jsonschema.exceptions.best_match(_VALIDATOR[0].iter_errors(spec))
    if err is not None:
        raise err


# ---------------------------------------------------------------- comparison vs a reference footprint
def compare(mine_path, ref_text):
    """Pad rows for every pad number on either side: {pad, mine, ref, d_pos, d_size} for pads on both, {pad, only:
    "mine"|"ref", at} for pads on one side. Pads sharing a number (an EP and its vias) pair up in position order."""
    from . import sexpr
    a, b = sexpr.parse(Path(mine_path).read_text(encoding="utf-8")), sexpr.parse(ref_text)
    pa, pb = {}, {}
    for t, d in ((a, pa), (b, pb)):
        for p in sexpr.pads(t):
            if p["number"]:
                d.setdefault(p["number"], []).append(p)
    rows = []
    for k in sorted(set(pa) | set(pb), key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, len(s), s)):
        xs, ys = (sorted(d.get(k, []), key=lambda p: (p["x"], p["y"])) for d in (pa, pb))
        for i in range(max(len(xs), len(ys))):
            tag = k if max(len(xs), len(ys)) == 1 else f"{k}#{i + 1}"
            if i >= len(xs) or i >= len(ys):
                p, side = (xs[i], "mine") if i < len(xs) else (ys[i], "ref")
                rows.append({"pad": tag, "only": side, "at": (p["x"], p["y"], p["w"], p["h"])})
                continue
            x, y = xs[i], ys[i]
            rows.append({"pad": tag, "mine": (x["x"], x["y"], x["w"], x["h"]), "ref": (y["x"], y["y"], y["w"], y["h"]),
                         "d_pos": (round(x["x"] - y["x"], 4), round(x["y"] - y["y"], 4)),
                         "d_size": (round(x["w"] - y["w"], 4), round(x["h"] - y["h"], 4))})
    return rows, sexpr.courtyard_bbox(a), sexpr.courtyard_bbox(b)


def differs(r):
    return "only" in r or any(r["d_pos"]) or any(r["d_size"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="generate .kicad_mod from package JSON files")
    g.add_argument("specs", nargs="+")
    g.add_argument("-o", "--out", default="out/footprints/ecadlib.pretty")
    g.add_argument("--density", choices="ABC")
    g.add_argument("--rules", help="library rules file: [footprint] density (default: ecadlib.toml found from here)")
    c = sub.add_parser("compare", help="compare a generated footprint's pads with a reference (path or URL)")
    c.add_argument("mine")
    c.add_argument("ref")
    a = ap.parse_args(argv)

    if a.cmd == "gen":
        from jsonschema import ValidationError
        from . import rules as RU
        outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
        rules = RU.load(a.rules, start=outdir)
        bad = 0
        import jsonschema
        for sp in a.specs:
            spec = None
            try:
                spec = json.loads(Path(sp).read_text(encoding="utf-8-sig"))     # UTF-8 (Notepad's BOM too), not the locale's
                validate(spec)
                txt, rep = build(spec, RU.density_for(spec, rules, a.density))
            except (ValueError, OSError, ValidationError) as e:   # a spec that doesn't add up: say why, write nothing
                if isinstance(e, ValidationError):                # (and go on with the next spec)
                    where = "/".join(map(str, e.absolute_path))
                    e = f"the package spec isn't valid{' at ' + where if where else ''}: {e.message}"
                print(f"{spec.get('name', sp) if isinstance(spec, dict) else sp}: not written: {e}", file=sys.stderr)
                bad += 1
                continue
            if shorts(rep):
                print(f"{spec['name']}: not written: {shorts(rep)}", file=sys.stderr)
                bad += 1
                continue
            from . import sexpr
            sexpr.parse(txt)                                          # sanity: balanced S-expression
            (outdir / f"{spec['name']}.kicad_mod").write_text(txt, encoding="utf-8", newline="\n")
            p1 = rep["pads"][0]
            zgx = f"Z={rep['Z']:.2f} G={rep['G']:.2f} X={rep['X']:.2f}" if "G" in rep else f"{len(rep['pads'])} pads"
            print(f"{spec['name']:<44} {rep['ipc_family']:<13} {zgx} "
                  f"pad1 {p1['w']:.3f}x{p1['h']:.3f} @({p1['x']:.3f},{p1['y']:.3f}) "
                  f"crtyd {rep['courtyard'][0]:.2f}x{rep['courtyard'][1]:.2f}"
                  + (f"  WARN {rep['warning']}" if "warning" in rep else "")
                  + "".join(f"\n    {c['level']}: {c['message']}" for c in rep.get("table_checks", []) if c["level"] != "info")
                  + "".join(f"\n    error: {c['message']}" for c in rep.get("checks", []) if c["level"] == "error"))
            bad += any(c["level"] == "error" for c in rep.get("checks", []))
        if bad:
            sys.exit(f"{bad} footprint spec(s) failed: fix them before using the footprints")
    else:
        if a.ref.startswith("http"):
            from .httpcache import CachedHTTP
            r = CachedHTTP(min_interval=0.5).get(a.ref)
            if not r.ok:
                sys.exit(f"fetch failed: {r.error or r.status}")
            ref = r.text
        else:
            ref = Path(a.ref).read_text(encoding="utf-8")
        rows, ca, cb = compare(a.mine, ref)
        print(f"{'pad':>4} {'mine (x,y,w,h)':>30} {'kicad (x,y,w,h)':>30}  d_pos       d_size")
        for r in rows:
            if "only" in r:
                print(f"{r['pad']:>4} only in {'mine' if r['only'] == 'mine' else 'the reference'} {r['at']}")
            else:
                print(f"{r['pad']:>4} {str(r['mine']):>30} {str(r['ref']):>30}  {r['d_pos']} {r['d_size']}")
        if ca and cb:
            print(f"courtyard mine {ca[2]-ca[0]:.2f}x{ca[3]-ca[1]:.2f}  kicad {cb[2]-cb[0]:.2f}x{cb[3]-cb[1]:.2f}")
        bad = [r["pad"] for r in rows if differs(r)]
        if bad:
            sys.exit(f"{len(bad)} pad(s) differ: {', '.join(bad[:12])}{' ...' if len(bad) > 12 else ''}")
        print(f"OK: all {len(rows)} pads match")


if __name__ == "__main__":
    main()
