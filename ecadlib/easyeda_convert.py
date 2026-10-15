"""Minimal native EasyEDA (std) shape-string converter: footprint -> .kicad_mod text, symbol -> .kicad_sym text.

The KiCad text then goes through kicad_import (convert_footprint / convert_symbol) to Altium, and footprint_check
can read the .kicad_mod. Only what a land pattern and a pin map need is converted:
  footprint: PAD (RECT / ELLIPSE / OVAL; POLYGON as its bounding rectangle), SMD and through-hole (round or slot,
             plated or not), HOLE (NPTH), TRACK, RECT, CIRCLE (as a 16-gon) on silk / fab layers; a courtyard is drawn around
             pads and fab (EasyEDA has no courtyard layer); ARC, TEXT, SOLIDREGION, VIA are dropped with a note
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
FP_LAYERS = {3: "F.SilkS", 101: "F.SilkS", 13: "F.Fab", 99: "F.Fab", 100: "F.Fab"}
PIN_TYPE = {"0": "unspecified", "1": "input", "2": "output", "3": "bidirectional", "4": "power_in"}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


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
def footprint_to_kicad(data, name=None, notes=None):
    """packageDetail.dataStr ({head, shape}) -> .kicad_mod text (KiCad 7 format)."""
    notes = [] if notes is None else notes
    head = data.get("head") or {}
    ox, oy = _f(head.get("x")), _f(head.get("y"))
    name = name or (head.get("c_para") or {}).get("package") or "EasyEDA"
    X, Y = (lambda x: (_f(x) - ox) * U), (lambda y: (_f(y) - oy) * U)
    pads, lines, circles, dropped, extent = [], [], [], {}, []
    for s in data.get("shape") or []:
        if not isinstance(s, str):
            continue
        f = s.split("~")
        kind = f[0]
        if kind == "PAD":
            f += [""] * (17 - len(f))
            shape, cx, cy, w, h, layer, num, hole_r = f[1], X(f[2]), Y(f[3]), _f(f[4]) * U, _f(f[5]) * U, f[6], f[8], _f(f[9]) * U
            rot, hole_len = _f(f[11]) % 360, _f(f[13]) * U
            if shape == "POLYGON":
                pts = _nums(f[10])
                xs, ys = [X(v) for v in pts[0::2]], [Y(v) for v in pts[1::2]]
                if xs:
                    cx, cy, w, h, rot = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs), max(ys) - min(ys), 0
                notes.append(f"pad {num}: polygon pad written as its bounding rectangle {w:.3f}x{h:.3f} mm")
            kshape = {"RECT": "rect", "OVAL": "oval", "ELLIPSE": "circle" if abs(w - h) < 1e-6 else "oval"}.get(shape, "rect")
            at = f"(at {_n(cx)} {_n(cy)}{' ' + _n(rot) if rot else ''})"
            if hole_r > 0:
                d = 2 * hole_r
                drill = f"(drill {_n(d)})"
                if hole_len > d + 1e-6:       # slot along the pad's long side (easyeda2kicad's rule)
                    drill = f"(drill oval {_n(hole_len)} {_n(d)})" if w >= h else f"(drill oval {_n(d)} {_n(hole_len)})"
                kind_ = "np_thru_hole" if _no(f[15]) else "thru_hole"
                pads.append(f'  (pad {_q(num)} {kind_} {kshape} {at} (size {_n(w)} {_n(h)}) {drill} (layers "*.Cu" "*.Mask"))')
            else:
                side = "B" if str(layer) == "2" else "F"
                pads.append(f'  (pad {_q(num)} smd {kshape} {at} (size {_n(w)} {_n(h)}) (layers "{side}.Cu" "{side}.Paste" "{side}.Mask"))')
            extent += _rect(cx, cy, w, h, rot)
        elif kind == "HOLE":
            cx, cy, d = X(f[1]), Y(f[2]), 2 * _f(f[3]) * U
            pads.append(f'  (pad "" np_thru_hole circle (at {_n(cx)} {_n(cy)}) (size {_n(d)} {_n(d)}) (drill {_n(d)}) (layers "*.Cu" "*.Mask"))')
            extent += [(cx - d / 2, cy - d / 2), (cx + d / 2, cy + d / 2)]
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
        elif kind in ("ARC", "TEXT", "SOLIDREGION", "VIA", "SVGNODE"):
            if kind != "SVGNODE":
                dropped[kind] = dropped.get(kind, 0) + 1
        else:
            dropped[kind] = dropped.get(kind, 0) + 1
    if dropped:
        notes.append("EasyEDA shapes not converted: " + ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())))
    if not pads:
        raise ValueError("EasyEDA footprint has no pads")
    out = [f"(footprint {_q(name)} (version 20221018) (generator ecadlib_easyeda) (layer \"F.Cu\")",
           f"  (descr {_q('converted from EasyEDA by ecadlib.easyeda_convert; check against the datasheet')})",
           "  (attr smd)" if all(" smd " in p for p in pads) else "  (attr through_hole)",
           '  (fp_text reference "REF**" (at 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
           f'  (fp_text value {_q(name)} (at 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))']
    for (x1, y1), (x2, y2), w, layer in lines:
        out.append(f'  (fp_line (start {_n(x1)} {_n(y1)}) (end {_n(x2)} {_n(y2)}) (stroke (width {_n(w or 0.1)}) (type solid)) (layer "{layer}"))')
    for cx, cy, r, w, layer in circles:          # as a 16-gon: footprint_check reads fp_circle's centre from (start)
        xy = " ".join(f"(xy {_n(cx + r * math.cos(k * math.pi / 8))} {_n(cy + r * math.sin(k * math.pi / 8))})" for k in range(16))
        out.append(f'  (fp_poly (pts {xy}) (stroke (width {_n(w or 0.1)}) (type solid)) (fill none) (layer "{layer}"))')
    extent += [pt for a, b, _, layer in lines if layer == "F.Fab" for pt in (a, b)]
    extent += [(cx + s * r, cy + s * r) for cx, cy, r, _, layer in circles if layer == "F.Fab" for s in (-1, 1)]
    xs, ys = [p[0] for p in extent], [p[1] for p in extent]
    c = 0.25                                  # IPC-7351B nominal courtyard excess, on the 0.01 mm grid
    out.append(f'  (fp_rect (start {_n(_grid(min(xs) - c, False))} {_n(_grid(min(ys) - c, False))}) '
               f'(end {_n(_grid(max(xs) + c, True))} {_n(_grid(max(ys) + c, True))}) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))')
    notes.append("courtyard drawn by ecadlib: pads and fab outline + 0.25 mm (EasyEDA has no courtyard layer)")
    return "\n".join(out + pads) + "\n)\n"


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


def symbol_to_kicad(data, name, footprint="", props=None, notes=None):
    """dataStr ({head, shape}) of an EasyEDA symbol -> .kicad_sym library text with one symbol `name`."""
    notes = [] if notes is None else notes
    head = data.get("head") or {}
    cp = head.get("c_para") or {}
    ox, oy = _f(head.get("x")), _f(head.get("y"))
    P = lambda x, y: (_n((_f(x) - ox) * U), _n(-(_f(y) - oy) * U))
    body, pins, dropped = [], [], {}
    for s in data.get("shape") or []:
        if not isinstance(s, str):
            continue
        kind = s.split("~", 1)[0]
        if kind == "P":
            seg = [g.split("~") for g in s.split("^^")]
            st = seg[0] + [""] * (9 - len(seg[0]))
            num = (seg[4][4] if len(seg) > 4 and len(seg[4]) > 4 else "") or st[3]
            nm = seg[3][4] if len(seg) > 3 and len(seg[3]) > 4 else ""
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
            pins.append(f'      (pin {PIN_TYPE.get(st[2], "unspecified")} line (at {x} {y} {ang}) (length {_n(length)})'
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
          "Datasheet": "~", "Description": "converted from EasyEDA by ecadlib.easyeda_convert"}
    pr.update({k: v for k, v in (props or {}).items() if v})
    out = ["(kicad_symbol_lib (version 20231120) (generator ecadlib_easyeda)", f"  (symbol {_q(name)} (in_bom yes) (on_board yes)"]
    out += [f"    (property {_q(k)} {_q(v)} (at 0 0 0) (effects (font (size 1.27 1.27)){'' if k in ('Reference', 'Value') else ' hide'}))" for k, v in pr.items()]
    out += [f"    (symbol {_q(name + '_1_1')}"] + body + pins + ["    )", "  )", ")"]
    return "\n".join(out) + "\n"
