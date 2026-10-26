"""Minimal native EasyEDA (std) shape-string converter: footprint -> .kicad_mod text, symbol -> .kicad_sym text.

The KiCad text then goes through kicad_import (convert_footprint / convert_symbol) to Altium, and footprint_check
can read the .kicad_mod. Only what a land pattern and a pin map need is converted:
  footprint: PAD (RECT / ELLIPSE / OVAL; POLYGON as a custom pad, or a plain one when it is a rectangle), SMD and through-hole (round or slot,
             plated or not), HOLE (NPTH), TRACK, RECT, CIRCLE (as a 16-gon) and circular ARC on silk / fab layers, the body
             region (SOLIDREGION on layer 99) as a fab outline; a courtyard is drawn around pads and fab (EasyEDA
             has no courtyard layer); TEXT, VIA and other regions (layer-100 lead shapes) are dropped with a note;
             REF** goes on silk above the courtyard and the value on fab below it (1 mm, 0.15 mm stroke); a pin-1 dot
             inside the body is moved beside pad 1
  symbol:    pins (number, name, electrical type, position, length), rectangles, polylines, polygons, circles;
             arcs, paths, text and multi-unit `subparts` are not converted

FORMAT ASSUMPTIONS (easyeda.com is blocked here, so this was written against hand-made fixtures shaped after
easyeda2kicad 1.0.1's parameter dataclasses, not real payloads):
  * every shape is "TYPE~f1~f2~..." with fields in easyeda2kicad's order:
      PAD~shape~cx~cy~w~h~layer~net~number~hole_r~points~rot~id~hole_len~slot_outline~plated~locked
      TRACK~width~layer~net~"x1 y1 x2 y2 ..."~id~locked      HOLE~cx~cy~r~id~locked
      CIRCLE~cx~cy~r~width~layer~id~locked                    RECT~x~y~w~h~layer~id~locked~width
      symbol pin "P~show~type~spice_num~x~y~rot~id~locked^^dot^^path~color^^name seg^^num seg^^..." with the
      KiCad pin number at segment 4 field 4 and the name at segment 3 field 4
      R~x~y~rx~ry~w~h~...  (rx/ry may be empty)   PL / PG~"x1 y1 x2 y2 ..."~...   C~cx~cy~r~...
  * footprint units are 10 mil (x 0.254 mm), relative to dataStr.head x/y, y down like KiCad
  * symbol units are 10 mil too, relative to head x/y, y down (flipped for KiCad, whose symbols are y up)
  * layers: 1 top copper, 2 bottom, 11 multi-layer (through-hole), 3 top silk, 13 top assembly -> F.Fab,
    100 lead shapes -> F.Fab, 101 polarity marks -> silk; 99 (component shape) -> F.Fab here (easyeda2kicad
    puts it on the courtyard, but a body shape is not an IPC courtyard)
  * pin electrical type: 0 unspecified, 1 input, 2 output, 3 bidirectional, 4 power
  * a pin's connection point is (x, y); its body end is the far end of its path; pad rotation is used as given
  * a pad is plated unless its plated field says N / no / false / 0"""
import math, re

U = 0.254                                     # one EasyEDA unit (10 mil) in mm
SILK_CLEAR, SILK_HARD = 0.2, 0.1              # KLC F5.1 silk-to-pad gap; below SILK_HARD footprint_check errors
TEXT_H, TEXT_W, TEXT_GAP = 1.0, 0.15, 0.2      # reference / value text: height, stroke, gap to courtyard / silk (mm)
DOT_R = 0.5                                   # a silk circle this small (radius, mm) is a pin-1 dot
FP_LAYERS = {3: "F.SilkS", 101: "F.SilkS", 13: "F.Fab", 99: "F.Fab", 100: "F.Fab"}
# EasyEDA's library data asks anyone who reuses it to credit the source; every converted part carries this line
CREDIT = "From the JLCEDA/EasyEDA Official Library (https://easyeda.com, https://lceda.cn)"
PIN_TYPE = {"0": "unspecified", "1": "input", "2": "output", "3": "bidirectional", "4": "power_in"}


def package_words(title, pads):
    """EasyEDA's footprint name in words for the footprint's description: 'SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL', 8 ->
    'SOIC-8, 4.9 x 3.9 mm body, 1.27 mm pitch, 8 pads' (L = body length, W = width, P = pitch, as EasyEDA names them);
    a name without those fields is kept as it is."""
    title = str(title or "").strip()
    fam, _, dims = title.partition("_")
    d = dict(re.findall(r"(?:^|-)(L|W|P)(\d+(?:\.\d+)?)(?=-|$)", dims))
    words = [fam if d else title] if title else []
    if "L" in d and "W" in d:
        words.append(f"{d['L']} x {d['W']} mm body")
    if "P" in d:
        words.append(f"{d['P']} mm pitch")
    words.append(f"{pads} pad{'s' if pads != 1 else ''}")
    return ", ".join(w for w in words if w)


def _f(v, d=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d          # untrusted text: 'nan' / 'inf' are not coordinates


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _n(v):
    return f"{round(v, 4) + 0:g}"


def _nums(s):
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", s or "")]


def _no(v):
    return str(v).strip().lower() in ("n", "no", "false", "0")


def _grid(v, up):
    return (math.ceil if up else math.floor)(round(v * 100, 6)) / 100


# ---------------------------------------------------------------- footprint
def origin(data, notes=None):
    """The footprint's origin on EasyEDA's canvas (head x / y, 10 mil units). A head without a usable x / y (a changed
    format, damaged data) would leave the origin at the canvas' 0, 0, a metre from the pads: the centre of the pads
    is taken instead, and the notes say so."""
    head = (data.get("head") if isinstance(data, dict) else None) or {}
    ox, oy = _f(head.get("x"), None), _f(head.get("y"), None)
    if ox is not None and oy is not None:
        return ox, oy
    xs, ys = [], []
    for s in (data.get("shape") or []) if isinstance(data, dict) else []:
        f = s.split("~") + [""] * 4 if isinstance(s, str) and s.startswith(("PAD~", "HOLE~")) else None
        if f:
            i = 2 if f[0] == "PAD" else 1
            x, y = _f(f[i], None), _f(f[i + 1], None)
            if x is not None and y is not None:
                xs.append(x), ys.append(y)
    cx = (min(xs) + max(xs)) / 2 if xs else 0.0
    cy = (min(ys) + max(ys)) / 2 if ys else 0.0
    if notes is not None:
        notes.append("EasyEDA footprint has no origin (head x / y): the centre of its pads is the origin")
    return (cx if ox is None else ox), (cy if oy is None else oy)


def footprint_to_kicad(data, name=None, notes=None):
    """packageDetail.dataStr ({head, shape}) -> .kicad_mod text (KiCad 7 format)."""
    notes = [] if notes is None else notes
    head = data.get("head") or {}
    ox, oy = origin(data, notes)
    name = name or (head.get("c_para") or {}).get("package") or "EasyEDA"
    X, Y = (lambda x: (_f(x) - ox) * U), (lambda y: (_f(y) - oy) * U)
    pads, lines, circles, arcs, dropped, extent, copper = [], [], [], [], {}, [], []
    for s in data.get("shape") or []:
        if not isinstance(s, str):
            continue
        f = s.split("~")
        kind = f[0]
        f += [""] * (17 - len(f))                 # a shape cut short (untrusted data) reads as empty fields, not IndexError
        if kind == "PAD":
            shape, cx, cy, w, h, layer, num, hole_r = f[1], X(f[2]), Y(f[3]), _f(f[4]) * U, _f(f[5]) * U, f[6], f[8], _f(f[9]) * U
            rot, hole_len = _f(f[11]) % 360, _f(f[13]) * U
            seen = _outline_rot(shape, _nums(f[10]), _f(f[2]), _f(f[3]), _f(f[4]), _f(f[5]))
            if seen is not None and _turn_off(seen, rot, 90 if abs(w - h) < 1e-6 else 180) > 0.5:
                notes.append(f"pad {num}: rotation {_n(rot)} deg does not match the pad's own outline ({_n(seen)} deg); "
                             f"the outline is used")
                rot = seen
            poly = None
            if shape == "POLYGON":
                pts = _nums(f[10])
                xs, ys = [X(v) for v in pts[0::2]], [Y(v) for v in pts[1::2]]
                if xs:
                    cx, cy, w, h, rot = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs), max(ys) - min(ys), 0
                    poly = _polygon(list(zip(xs, ys)))
                if poly:           # an L / T shape (SOT-89's lead + tab): its bounding box would put copper next to the
                    notes.append(f"pad {num}: polygon pad kept as a KiCad custom pad ({len(poly)} points)")   # other pins
                else:
                    notes.append(f"pad {num}: polygon pad is a plain rectangle {w:.3f}x{h:.3f} mm")
            kshape = {"RECT": "rect", "OVAL": "oval", "ELLIPSE": "circle" if abs(w - h) < 1e-6 else "oval"}.get(shape, "rect")
            at = f"(at {_n(cx)} {_n(cy)}{' ' + _n(rot) if rot else ''})"
            size, prims = f"(size {_n(w)} {_n(h)})", ""
            if poly:
                from .footprint_adv import _interior_point
                d, ax, ay = _interior_point(poly)
                kshape, a = "custom", round(max(0.05, min(1.6 * d, 1.0)), 3)
                if hole_r > 0:            # KiCad drills at the pad's anchor: keep it on EasyEDA's hole, sized to the hole
                    ax, ay, a = X(f[2]), Y(f[3]), round(2 * hole_r, 3)
                ax, ay = round(ax, 4), round(ay, 4)
                at, size = f"(at {_n(ax)} {_n(ay)})", f"(size {_n(a)} {_n(a)})"
                prims = (" (options (clearance outline) (anchor circle)) (primitives (gr_poly (pts "
                         + " ".join(f"(xy {_n(x - ax)} {_n(y - ay)})" for x, y in poly) + ") (width 0) (fill yes)))")
            if hole_r > 0:
                d = 2 * hole_r
                drill = f"(drill {_n(d)})"
                if hole_len > d + 1e-6:       # slot along the pad's long side (easyeda2kicad's rule)
                    drill = f"(drill oval {_n(hole_len)} {_n(d)})" if w >= h else f"(drill oval {_n(d)} {_n(hole_len)})"
                kind_ = "np_thru_hole" if _no(f[15]) else "thru_hole"
                pads.append(f'  (pad {_q(num)} {kind_} {kshape} {at} {size} {drill} (layers "*.Cu" "*.Mask"){prims})')
            else:
                side = "B" if str(layer) == "2" else "F"
                pads.append(f'  (pad {_q(num)} smd {kshape} {at} {size} (layers "{side}.Cu" "{side}.Paste" "{side}.Mask"){prims})')
            extent += _rect(cx, cy, w, h, rot)
            copper.append({"num": num, "x": cx, "y": cy, "w": w, "h": h, "rot": rot, "shape": kshape})
        elif kind == "HOLE":
            cx, cy, d = X(f[1]), Y(f[2]), 2 * _f(f[3]) * U
            pads.append(f'  (pad "" np_thru_hole circle (at {_n(cx)} {_n(cy)}) (size {_n(d)} {_n(d)}) (drill {_n(d)}) (layers "*.Cu" "*.Mask"))')
            extent += [(cx - d / 2, cy - d / 2), (cx + d / 2, cy + d / 2)]
            copper.append({"num": "", "x": cx, "y": cy, "w": d, "h": d, "rot": 0, "shape": "circle"})
        elif kind in ("TRACK", "RECT", "CIRCLE"):
            lid = int(_f(f[2] if kind == "TRACK" else f[5] if kind in ("RECT", "CIRCLE") else 0))
            layer = FP_LAYERS.get(lid)
            if not layer:
                dropped[f"{kind} on layer {lid}"] = dropped.get(f"{kind} on layer {lid}", 0) + 1
                continue
            if kind == "TRACK":
                w, p = _f(f[1]) * U, _nums(f[4])
                pts = [(X(x), Y(y)) for x, y in zip(p[0::2], p[1::2])]
                lines += [(a, b, w, layer) for a, b in zip(pts, pts[1:])]
            elif kind == "RECT":
                x, y, w, h, sw = X(f[1]), Y(f[2]), _f(f[3]) * U, _f(f[4]) * U, _f(f[8] if len(f) > 8 else 0) * U or 0.1
                c = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
                lines += [(a, b, sw, layer) for a, b in zip(c, c[1:] + c[:1])]
            else:
                circles.append((X(f[1]), Y(f[2]), _f(f[3]) * U, _f(f[4]) * U, layer))
        elif kind == "ARC" and FP_LAYERS.get(int(_f(f[2]))) and _svg_arc(f[4]):
            arcs.append((*_svg_arc(f[4], X, Y), _f(f[1]) * U, FP_LAYERS[int(_f(f[2]))]))
        elif kind == "SOLIDREGION" and int(_f(f[1])) == 99 and _region(f[3]):
            pts = [(X(x), Y(y)) for x, y in _region(f[3])]      # the part body: its outline on F.Fab
            lines += [(a, b, 0.1, "F.Fab") for a, b in zip(pts, pts[1:] + pts[:1])]
        elif kind in ("ARC", "TEXT", "SOLIDREGION", "VIA", "SVGNODE"):
            what = f"SOLIDREGION on layer {int(_f(f[1]))}" if kind == "SOLIDREGION" else kind
            if kind != "SVGNODE":
                dropped[what] = dropped.get(what, 0) + 1
        else:
            dropped[kind] = dropped.get(kind, 0) + 1
    lines, circles = _clear_silk(lines, circles, copper, notes)
    circles = _pin1_dot(circles, lines, copper, notes)
    if dropped:
        notes.append("EasyEDA shapes not converted: " + ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())))
    if not pads:
        raise ValueError("EasyEDA footprint has no pads")
    extent += [pt for a, b, _, layer in lines if layer == "F.Fab" for pt in (a, b)]
    extent += [(cx + s * r, cy + s * r) for cx, cy, r, _, layer in circles if layer == "F.Fab" for s in (-1, 1)]
    xs, ys = [p[0] for p in extent], [p[1] for p in extent]
    c = 0.25                                  # IPC-7351B nominal courtyard excess, on the 0.01 mm grid
    crt = (_grid(min(xs) - c, False), _grid(min(ys) - c, False), _grid(max(xs) + c, True), _grid(max(ys) + c, True))
    # Reference on silk just above everything (courtyard and silk), Value on fab just below: 1 mm text, 0.15 mm stroke,
    # clear of the pads, as footprint_gen and KiCad's own library place them (EasyEDA's TEXT shapes are not converted)
    silk_y = [v for a, b, _, layer in lines if layer == "F.SilkS" for v in (a[1], b[1])] + \
        [cy + s * (r + w / 2) for cx, cy, r, w, layer in circles if layer == "F.SilkS" for s in (-1, 1)]
    ref_y = round(min([crt[1]] + silk_y) - TEXT_GAP - TEXT_H / 2, 4)
    val_y = round(max([crt[3]] + silk_y) + TEXT_GAP + TEXT_H / 2, 4)
    cx_ = round((crt[0] + crt[2]) / 2, 4)
    font = f"(effects (font (size {_n(TEXT_H)} {_n(TEXT_H)}) (thickness {_n(TEXT_W)})))"
    numbered = {p.split()[1] for p in pads} - {'""'}       # pad numbers (an unnumbered hole is not a pad)
    out = [f"(footprint {_q(name)} (version 20221018) (generator ecadlib_easyeda) (layer \"F.Cu\")",
           f"  (descr {_q(package_words(name, len(numbered)))})",
           f"  (property \"Credit\" {_q(CREDIT)})",                  # EasyEDA's credit: kept, apart from the description
           # a plated pin makes it THT; an unplated hole (a locating peg) does not (footprint_adv, footprint_rules, KiCad)
           "  (attr through_hole)" if any(" thru_hole " in p for p in pads) or not any(" smd " in p for p in pads)
           else "  (attr smd)",
           f'  (fp_text reference "REF**" (at {_n(cx_)} {_n(ref_y)}) (layer "F.SilkS") {font})',
           f'  (fp_text value {_q(name)} (at {_n(cx_)} {_n(val_y)}) (layer "F.Fab") {font})']
    # ${REFERENCE} on the assembly drawing (KLC F5.2), in the middle of the body, 0.5 .. 1.0 mm to fit it
    bpts = [pt for a, b, _, layer in lines if layer in ("F.Fab", "F.SilkS") for pt in (a, b)] or [pt for pt in extent]
    bw, bh = max(p[0] for p in bpts) - min(p[0] for p in bpts), max(p[1] for p in bpts) - min(p[1] for p in bpts)
    fs = max(0.5, min(1.0, math.floor(100 * min(bw / (len("REF**") * 0.9), bh * 0.6)) / 100))
    bc = (round((max(p[0] for p in bpts) + min(p[0] for p in bpts)) / 2, 4), round((max(p[1] for p in bpts) + min(p[1] for p in bpts)) / 2, 4))
    out.append(f'  (fp_text user "${{REFERENCE}}" (at {_n(bc[0])} {_n(bc[1])}) (layer "F.Fab") '
               f'(effects (font (size {_n(fs)} {_n(fs)}) (thickness {_n(round(0.15 * fs, 3))}))))')
    for (x1, y1), (x2, y2), w, layer in lines:
        out.append(f'  (fp_line (start {_n(x1)} {_n(y1)}) (end {_n(x2)} {_n(y2)}) (stroke (width {_n(w or 0.1)}) (type solid)) (layer "{layer}"))')
    for cx, cy, r, w, layer in circles:          # as a 16-gon: footprint_check reads fp_circle's centre from (start)
        xy = " ".join(f"(xy {_n(cx + r * math.cos(k * math.pi / 8))} {_n(cy + r * math.sin(k * math.pi / 8))})" for k in range(16))
        out.append(f'  (fp_poly (pts {xy}) (stroke (width {_n(w or 0.1)}) (type solid)) (fill none) (layer "{layer}"))')
    for a, m, b, w, layer in arcs:
        out.append(f'  (fp_arc (start {_n(a[0])} {_n(a[1])}) (mid {_n(m[0])} {_n(m[1])}) (end {_n(b[0])} {_n(b[1])}) '
                   f'(stroke (width {_n(w or 0.1)}) (type solid)) (layer "{layer}"))')
    out.append(f'  (fp_rect (start {_n(crt[0])} {_n(crt[1])}) '
               f'(end {_n(crt[2])} {_n(crt[3])}) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))')
    notes.append("courtyard drawn by ecadlib: pads and fab outline + 0.25 mm (EasyEDA has no courtyard layer)")
    return "\n".join(out + pads) + "\n)\n"


def _turn_off(a, b, period):
    d = (a - b) % period
    return min(d, period - d)


def _outline_rot(shape, v, cx, cy, w, h):
    """The KiCad rotation (degrees, counter-clockwise on screen) of a RECT / OVAL pad as its own points field draws
    it, else None. EasyEDA writes each pad's outline in canvas units beside (cx, cy, w, h, rotation): a RECT's four
    corners in order, an OVAL's long axis (two end points). The outline is what EasyEDA shows, so a rotation that
    disagrees with it (a 45 degree pad turned the other way) is not trusted. None when the points are not that
    outline (another size or centre, not a rectangle, or no points)."""
    tol = max(0.02, 0.01 * max(w, h))
    if shape == "RECT" and len(v) >= 8:
        p = list(zip(v[0:8:2], v[1:8:2]))
        e1, e2 = (p[1][0] - p[0][0], p[1][1] - p[0][1]), (p[2][0] - p[1][0], p[2][1] - p[1][1])
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        mx, my = sum(q[0] for q in p) / 4, sum(q[1] for q in p) / 4
        if (min(l1, l2) < 1e-9 or abs(e1[0] * e2[0] + e1[1] * e2[1]) > 0.01 * l1 * l2
                or math.hypot(mx - cx, my - cy) > tol):
            return None
        if abs(l1 - w) <= tol and abs(l2 - h) <= tol:
            e = e1
        elif abs(l2 - w) <= tol and abs(l1 - h) <= tol:
            e = e2
        else:
            return None
        return round(math.degrees(math.atan2(-e[1], e[0])) % 360, 3)
    if shape == "OVAL" and len(v) == 4 and abs(w - h) > tol:
        a = (v[2] - v[0], v[3] - v[1])
        mx, my = (v[0] + v[2]) / 2, (v[1] + v[3]) / 2
        if abs(math.hypot(*a) - abs(w - h)) > tol or math.hypot(mx - cx, my - cy) > tol:
            return None
        return round((math.degrees(math.atan2(-a[1], a[0])) - (0 if w > h else 90)) % 360, 3)
    return None


def _polygon(pts):
    """A pad polygon's corners (repeats and points on a straight edge dropped), or None when it is just its bounding
    rectangle (then a plain pad says the same)."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-6 and abs(out[0][1] - out[-1][1]) < 1e-6:
        out.pop()
    i = 0
    while len(out) > 3 and i < len(out):                     # collinear middle points
        a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
        if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) < 1e-9:
            out.pop(i)
        else:
            i += 1
    if len(out) < 3:
        return None
    xs, ys = {round(x, 6) for x, _ in out}, {round(y, 6) for _, y in out}
    if len(out) == 4 and len(xs) == 2 and len(ys) == 2:
        return None
    return out


def _svg_arc(path, X=lambda v: v, Y=lambda v: v):
    """'M x1,y1 A r,r rot large sweep x2,y2' (a circular SVG arc, y down) -> (start, mid, end) through X / Y, else None."""
    v = _nums(path)
    if not re.match(r"\s*M[^A]*A", path or "") or len(v) != 9 or abs(v[2] - v[3]) > 1e-6 * max(1.0, abs(v[2])):
        return None
    (x1, y1), r, large, sweep, (x2, y2) = v[0:2], v[2], v[5], v[6], v[7:9]
    d = math.hypot(x2 - x1, y2 - y1) / 2
    if d < 1e-9:
        return None
    h = math.sqrt(max(r * r - d * d, 0.0))
    sign = 1 if bool(large) != bool(sweep) else -1         # SVG 1.1 F.6.5, no x-axis rotation for a circle
    cx, cy = (x1 + x2) / 2 + sign * h / d * (y1 - y2) / 2, (y1 + y2) / 2 + sign * h / d * (x2 - x1) / 2
    a1, a2, rr = math.atan2(y1 - cy, x1 - cx), math.atan2(y2 - cy, x2 - cx), max(r, d)
    delta = (a2 - a1) % (2 * math.pi) if sweep else -((a1 - a2) % (2 * math.pi))
    am = a1 + delta / 2
    mid = (cx + rr * math.cos(am), cy + rr * math.sin(am))
    return (X(x1), Y(y1)), (X(mid[0]), Y(mid[1])), (X(x2), Y(y2))


def _region(path):
    """A SOLIDREGION path of straight segments 'M x y L x y ... Z' -> its points, else None (curves are not read)."""
    if not path or re.search(r"[AaCcQqSsTtHhVv]", path):
        return None
    v = _nums(path)
    pts = list(zip(v[0::2], v[1::2]))
    return pts if len(pts) >= 3 and len(v) % 2 == 0 else None


def _clear_silk(lines, circles, copper, notes):
    """Silk kept off the copper, as KiCad's own footprints and footprint_check want it (KLC F5.1): EasyEDA's library
    draws silk outlines up to, or over, the pads (the fab cuts silk from copper when it makes the board). Silk lines are
    cut back to SILK_CLEAR from every pad's outline (pieces shorter than 0.2 mm go); a silk circle (a pin-1 dot) is kept
    whole unless it comes within SILK_HARD of a pad, and then left out. Returns (lines, circles)."""
    from .footprint_adv import _clip_any, _pad_dist
    if not copper:
        return lines, circles
    near = lambda pts, c: min(_pad_dist(x, y, p) for x, y in pts for p in copper) < c
    out, cut = [], 0
    for a, b, w, layer in lines:
        sw = w or 0.1
        c = SILK_CLEAR + sw / 2 + 0.005
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(2, int(L / 0.01))
        if layer != "F.SilkS" or not near([(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)], c):
            out.append((a, b, w, layer))
            continue
        cut += 1
        out += [(p, q, w, layer) for p, q in _clip_any(a, b, copper, c, min_len=min(0.2, L))]
    keep, gone = [], 0
    for cx, cy, r, w, layer in circles:
        ring = [(cx + r * math.cos(k * math.pi / 16), cy + r * math.sin(k * math.pi / 16)) for k in range(32)] + [(cx, cy)]
        if layer == "F.SilkS" and near(ring, SILK_HARD + (w or 0.1) / 2 + 0.005):
            gone += 1
            continue
        keep.append((cx, cy, r, w, layer))
    if cut:
        notes.append(f"silk: {cut} line(s) cut back to {SILK_CLEAR} mm from the pads (the EasyEDA outline ran up to or over the copper)")
    if gone:
        notes.append(f"silk: {gone} circle(s) left out: within {SILK_HARD} mm of a pad")
    return out, keep


def _pin1_dot(circles, lines, copper, notes):
    """EasyEDA's pin-1 dot usually sits inside the body outline, where the part covers it once placed. A small silk
    circle (a dot, r <= DOT_R) inside the body is moved next to pad 1, outside the body and the pads: the spot beside
    pad 1 (left / right / above / below) closest to it that keeps SILK_CLEAR from every pad and from the body outline."""
    p1 = next((p for p in copper if p["num"] == "1"), None)
    if p1 is None or len([p for p in copper if p["num"]]) < 3:
        return circles
    outline = [pt for a, b, _, layer in lines if layer in ("F.SilkS", "F.Fab") for pt in (a, b)]
    pads = [pt for p in copper for pt in _rect(p["x"], p["y"], p["w"], p["h"], p["rot"])]
    pts = outline or pads
    body = (min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts), max(y for _, y in pts))
    pb = (min(x for x, _ in pads), min(y for _, y in pads), max(x for x, _ in pads), max(y for _, y in pads))
    from .footprint_adv import _pad_dist
    out = []
    for cx, cy, r, w, layer in circles:
        inside = body[0] < cx < body[2] and body[1] < cy < body[3] or pb[0] < cx < pb[2] and pb[1] < cy < pb[3]
        if layer != "F.SilkS" or r > DOT_R or not inside:
            out.append((cx, cy, r, w, layer))
            continue
        ext = r + (w or 0.1) / 2
        gap = SILK_CLEAR + ext
        x1 = _rect(p1["x"], p1["y"], p1["w"], p1["h"], p1["rot"])
        hx, hy = (max(x for x, _ in x1) - min(x for x, _ in x1)) / 2, (max(y for _, y in x1) - min(y for _, y in x1)) / 2
        best = None
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            x, y = p1["x"] + dx * (hx + gap), p1["y"] + dy * (hy + gap)
            if dx and body[0] - gap < x < body[2] + gap and body[1] < y < body[3]:    # still on the body: past its edge
                x = body[0] - gap if dx < 0 else body[2] + gap
            if dy and body[1] - gap < y < body[3] + gap and body[0] < x < body[2]:
                y = body[1] - gap if dy < 0 else body[3] + gap
            if min(_pad_dist(x, y, p) for p in copper) < gap - 1e-6:
                continue
            d = math.hypot(x - p1["x"], y - p1["y"])
            if best is None or d < best[0]:
                best = (d, round(x, 4), round(y, 4))
        if best is None:
            out.append((cx, cy, r, w, layer))
            continue
        out.append((best[1], best[2], r, w, layer))
        notes.append(f"silk: pin-1 dot moved from ({cx:.2f}, {cy:.2f}) inside the body to ({best[1]:.2f}, {best[2]:.2f}) "
                     f"beside pad 1, {SILK_CLEAR} mm clear of the pads")
    return out


def _rect(cx, cy, w, h, rot):
    a = math.radians(rot)
    return [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


# ---------------------------------------------------------------- symbol
def _path_ends(path):
    """'M 370 300 h 10' / 'M370,300L380,300' / 'v -10' -> (start, end) in canvas units."""
    toks = re.findall(r"[MLHVmlhv]|-?\d+(?:\.\d+)?", path or "")
    x = y = 0.0
    start, cmd, i = None, None, 0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd, i = t, i + 1
            continue
        if cmd in ("M", "L", "m", "l"):
            dx, dy = float(toks[i]), float(toks[i + 1]); i += 2
            x, y = (x + dx, y + dy) if cmd.islower() else (dx, dy)
        elif cmd in ("H", "h"):
            x = x + float(t) if cmd == "h" else float(t); i += 1
        elif cmd in ("V", "v"):
            y = y + float(t) if cmd == "v" else float(t); i += 1
        else:
            i += 1
        if start is None:
            start = (x, y)
    return start, (x, y)


def _path_runs(path):
    """'M 5 -6 L -5 0 L 5 7 Z' -> ([[(5, -6), (-5, 0), (5, 7), (5, -6)]], curved). Straight segments only (M L H V Z);
    curved = the path also had arc or curve commands, which are left out."""
    toks = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", path or "")
    runs, run, x, y, cmd, i, curved = [], [], 0.0, 0.0, None, 0, False
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd, i = t, i + 1
            if cmd in "Zz" and run:
                run.append(run[0]); runs.append(run); run = []; x, y = runs[-1][0]
            elif cmd not in "MLHVmlhvZz":
                curved = True
            continue
        if cmd in ("M", "m", "L", "l") and i + 1 < len(toks):
            dx, dy = float(toks[i]), float(toks[i + 1]); i += 2
            x, y = (x + dx, y + dy) if cmd.islower() else (dx, dy)
            if cmd in "Mm":
                if len(run) > 1:
                    runs.append(run)
                run = []
                cmd = "l" if cmd == "m" else "L"           # SVG: more pairs after M are line-tos
        elif cmd in ("H", "h", "V", "v"):
            v = float(t); i += 1
            x, y = (x + v if cmd == "h" else v if cmd == "H" else x), (y + v if cmd == "v" else v if cmd == "V" else y)
        else:
            i += 1
            continue
        run.append((x, y))
    if len(run) > 1:
        runs.append(run)
    return runs, curved


def symbol_to_kicad(data, name, footprint="", props=None, notes=None):
    """dataStr ({head, shape}) of an EasyEDA symbol -> .kicad_sym library text with one symbol `name`."""
    notes = [] if notes is None else notes
    head = data.get("head") or {}
    cp = head.get("c_para") or {}
    ox, oy = _f(head.get("x")), _f(head.get("y"))
    P = lambda x, y: (_n((_f(x) - ox) * U), _n(-(_f(y) - oy) * U))
    body, pins, dropped, named = [], [], {}, False
    for s in data.get("shape") or []:
        if not isinstance(s, str):
            continue
        kind = s.split("~", 1)[0]
        if kind == "P":
            seg = [g.split("~") for g in s.split("^^")]
            st = seg[0] + [""] * (9 - len(seg[0]))
            num = (seg[4][4] if len(seg) > 4 and len(seg[4]) > 4 else "") or st[3]
            nm = seg[3][4] if len(seg) > 3 and len(seg[3]) > 4 else ""
            named = named or nm.strip() not in ("", "~", num)          # "1" named "1" (a crystal): its name is noise
            cx, cy = _f(st[4]), _f(st[5])
            a, b = _path_ends(seg[2][0] if len(seg) > 2 else "")
            if a is None:
                notes.append(f"pin {num}: no pin path; length 10 assumed"); a = b = (cx - 10, cy)
            far = max((a, b), key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
            dx, dy = far[0] - cx, -(far[1] - cy)              # connection point -> body, KiCad y up
            raw = math.degrees(math.atan2(dy, dx)) if (dx or dy) else 0.0
            ang = round(raw / 90) % 4 * 90                     # KiCad only takes 0, 90, 180, 270: anything else and
            if abs((raw - ang + 180) % 360 - 180) > 0.5:       # the whole library fails to load
                notes.append(f"pin {num}: slanted pin path ({raw % 360:.0f} deg) drawn at {ang} deg")
            length = (abs(dx) if ang in (0, 180) else abs(dy)) * U or 2.54
            x, y = P(cx, cy)
            # the inversion circle ("^^1~x~y" after the name / number) and the clock mark ("^^1~path"), shown or not
            dot, clk = (len(seg) > k and seg[k][0].strip() == "1" for k in (5, 6))
            shape = "inverted_clock" if dot and clk else "inverted" if dot else "clock" if clk else "line"
            pins.append(f'      (pin {PIN_TYPE.get(st[2], "unspecified")} {shape} (at {x} {y} {ang}) (length {_n(length)})'
                        f' (name {_q(nm or "~")} (effects (font (size 1.27 1.27)))) (number {_q(num)} (effects (font (size 1.27 1.27)))))')
        elif kind == "R":
            f = s.split("~") + [""] * 8
            x, y, w, h = _f(f[1]), _f(f[2]), _f(f[5]), _f(f[6])
            (x1, y1), (x2, y2) = P(x, y), P(x + w, y + h)
            body.append(f"      (rectangle (start {x1} {y1}) (end {x2} {y2}) (stroke (width 0.254) (type default)) (fill (type background)))")
        elif kind in ("PL", "PG"):
            f = s.split("~")
            p = _nums(f[1] if len(f) > 1 else "")
            pts = [P(x, y) for x, y in zip(p[0::2], p[1::2])]
            if kind == "PG" and pts:
                pts.append(pts[0])
            if len(pts) >= 2:
                xy = " ".join(f"(xy {x} {y})" for x, y in pts)
                body.append(f"      (polyline (pts {xy}) (stroke (width 0.254) (type default)) (fill (type {'background' if kind == 'PG' else 'none'})))")
        elif kind == "PT":                                    # SVG path: a diode's triangle, arrow heads
            f = s.split("~") + [""] * 5
            runs, curved = _path_runs(f[1])
            if curved:
                dropped["PT (curve)"] = dropped.get("PT (curve)", 0) + 1
            fill = "outline" if f[4] and f[4].lower() != "none" else "none"
            for pts in runs:
                if len(pts) >= 2:
                    xy = " ".join(f"(xy {x} {y})" for x, y in (P(a, b) for a, b in pts))
                    body.append(f"      (polyline (pts {xy}) (stroke (width 0.254) (type default)) (fill (type {fill})))")
        elif kind in ("C", "E"):
            f = s.split("~") + [""] * 5
            rx, ry = _f(f[3]), _f(f[4]) if kind == "E" else _f(f[3])
            if kind == "E" and abs(rx - ry) > 1e-6:
                dropped["E (ellipse)"] = dropped.get("E (ellipse)", 0) + 1; continue
            x, y = P(f[1], f[2])
            body.append(f"      (circle (center {x} {y}) (radius {_n(rx * U)}) (stroke (width 0.254) (type default)) (fill (type none)))")
        else:
            dropped[kind] = dropped.get(kind, 0) + 1
    if dropped:
        notes.append("EasyEDA symbol shapes not converted: " + ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())))
    if not pins:
        raise ValueError("EasyEDA symbol has no pins")
    pr = {"Reference": (cp.get("pre") or "U?").rstrip("?") or "U", "Value": name, "Footprint": footprint,
          "Datasheet": "~", "Description": "", "Credit": CREDIT}     # Description: the part's (props), never the tool's
    pr.update({k: v for k, v in (props or {}).items() if v})
    out = ["(kicad_symbol_lib (version 20231120) (generator ecadlib_easyeda)",
           f"  (symbol {_q(name)}{'' if named else ' (pin_names (offset 0) hide)'} (in_bom yes) (on_board yes)"]
    out += [f"    (property {_q(k)} {_q(v)} (at 0 0 0) (effects (font (size 1.27 1.27)){'' if k in ('Reference', 'Value') else ' hide'}))" for k, v in pr.items()]
    out += [f"    (symbol {_q(name + '_1_1')}"] + body + pins + ["    )", "  )", ")"]
    return "\n".join(out) + "\n"
