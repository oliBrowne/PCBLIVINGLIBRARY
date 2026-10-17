"""A librarian plan's library as one Autodesk Fusion Electronics / EAGLE library (.lbr): packages, symbols, devicesets.

    ecadlib eagle-export out/librarian/plan.json -o out/eagle --name mylib \
        [--kicad out/kicad] [--lib examples/kicad_lib] [--include-held] [--cache out/http_cache] [--offline] [--json]

The parts go through kicad-export first (or --kicad reuses a kicad-export folder of the same plan), so the KiCad and
the Eagle library hold the same symbols, footprints and fields. DIR gets:
  <lib>.lbr            EAGLE 9 XML library; Fusion Electronics opens it (Library > Open, or Library Manager > Import).
                       One deviceset per part (named like the plan item) with its package, gates and pin -> pad
                       connects, and a technology carrying MPN, MANUFACTURER, DATASHEET, DESCRIPTION, supplier numbers
                       and the plan's other fields as attributes
  <lib>.3dshapes/      the STEP models of the KiCad export. EAGLE / Fusion libraries link 3D packages from Fusion's own
                       cloud, not files, so attach these in Fusion's 3D package editor (Create 3D package > Insert)
  eagle_export.json    what was written and the read-back checks below

How KiCad things are written (Eagle draws with y up in mm, like this module's cad_model):
  pads      SMD pads with their size, rotation, roundness (KiCad rounded-rect ratio x 200), side, paste and mask on / off.
            A KiCad mask or paste margin becomes stop="no" / cream="no" plus the exact opening drawn on tStop / tCream
            (Eagle has no per-pad margins; without a margin the design rules apply, as in KiCad).
            Through-hole pads: round / square pads; oval and rectangular pads (which Eagle pads cannot be) are a pad of
            the shorter side plus the rest of the copper as a round-ended wire / rectangle on Top and Bottom and the
            matching tStop / bStop opening, so the copper and the mask are what KiCad had. Slots: a hole of the slot
            width plus the slot outline on Milling (46). Non-plated holes: <hole>. Custom pads: the anchor pad plus
            the shape as polygons on the copper, tStop and tCream layers.
  names     pad numbers stay; a number used twice (USB-C shield SH x 4) gets @2, @3 (Eagle needs unique pad names,
            and ignores what follows @ on screen); unnumbered copper pads become _U1, _U2. Pins: KiCad names, ~{X}
            overbars as !X!, a name used twice gets @2 ...; a pin's length is Eagle's next shorter (point, short,
            middle, long) plus a line for the rest, so connection points and the drawing match KiCad
  layers    silkscreen 21/22, fab 51/52, courtyard 39/40 (tKeepout, which KiCad reads back as the courtyard),
            copper 1/16, mask 29/30, paste 31/32, edge 20, drawings 48; symbol body 94, >NAME 95, >VALUE 96
  types     input in, output / power output out, bidirectional io, tri-state hiz, passive pas, power input pwr,
            open collector / emitter oc, not connected nc
Checks: the .lbr is read back with Python's XML parser and every package is compared with its KiCad footprint
(ecadlib.parity: pads, numbers, positions, sizes, rotations, shapes, drills, slots, paste, mask, courtyard) and every
deviceset with its KiCad symbol (pins, names, types, positions, directions, pin -> pad connects, fields). When a
Python with KiCad's pcbnew is found, KiCad's own Eagle importer reads the .lbr as a second, independent reader and its
footprints are compared too. Exit code 1 when a check fails; documented approximations are warnings."""
import argparse, html, json, math, re, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import cad_model as CM, kicad_export as X, parity as PY, sexpr

EAGLE_VERSION = "9.6.2"
LAYERS = [(1, "Top", 4, 1), (16, "Bottom", 1, 1), (17, "Pads", 2, 1), (18, "Vias", 2, 1), (19, "Unrouted", 6, 1),
          (20, "Dimension", 24, 1), (21, "tPlace", 7, 1), (22, "bPlace", 7, 1), (23, "tOrigins", 15, 1),
          (24, "bOrigins", 15, 1), (25, "tNames", 7, 1), (26, "bNames", 7, 1), (27, "tValues", 7, 1),
          (28, "bValues", 7, 1), (29, "tStop", 7, 3), (30, "bStop", 7, 6), (31, "tCream", 7, 4), (32, "bCream", 7, 5),
          (33, "tFinish", 6, 3), (34, "bFinish", 6, 6), (35, "tGlue", 7, 4), (36, "bGlue", 7, 5), (37, "tTest", 7, 1),
          (38, "bTest", 7, 1), (39, "tKeepout", 4, 11), (40, "bKeepout", 1, 11), (41, "tRestrict", 4, 10),
          (42, "bRestrict", 1, 10), (43, "vRestrict", 2, 10), (44, "Drills", 7, 1), (45, "Holes", 7, 1),
          (46, "Milling", 3, 1), (47, "Measures", 7, 1), (48, "Document", 7, 1), (49, "Reference", 7, 1),
          (51, "tDocu", 7, 1), (52, "bDocu", 7, 1), (88, "SimResults", 9, 1), (89, "SimProbes", 9, 1),
          (90, "Modules", 5, 1), (91, "Nets", 2, 1), (92, "Busses", 1, 1), (93, "Pins", 2, 1), (94, "Symbols", 4, 1),
          (95, "Names", 7, 1), (96, "Values", 7, 1), (97, "Info", 7, 1), (98, "Guide", 6, 1)]
ROLE_LAYER = {"cu_f": 1, "cu_b": 16, "silk_f": 21, "silk_b": 22, "fab_f": 51, "fab_b": 52, "crtyd_f": 39, "crtyd_b": 40,
              "mask_f": 29, "mask_b": 30, "paste_f": 31, "paste_b": 32, "edge": 20, "doc": 48}
TOP, BOTTOM, TSTOP, BSTOP, TCREAM, BCREAM, MILLING, SYMBOLS, NAMES, VALUES = 1, 16, 29, 30, 31, 32, 46, 94, 95, 96
PIN_LENGTHS = [(7.62, "long"), (5.08, "middle"), (2.54, "short"), (0.0, "point")]
LENGTH_MM = {n: v for v, n in PIN_LENGTHS}
DIRECTION = {"input": "in", "output": "out", "bidirectional": "io", "tri_state": "hiz", "passive": "pas",
             "free": "pas", "unspecified": "pas", "power_in": "pwr", "power_out": "out", "open_collector": "oc",
             "open_emitter": "oc", "no_connect": "nc"}
BACK = {"in": "input", "out": "output", "io": "bidirectional", "hiz": "tri_state", "pas": "passive", "pwr": "power_in",
        "sup": "power_out", "oc": "open_collector", "nc": "no_connect"}
FUNCTION = {"inverted": "dot", "clock": "clk", "inverted_clock": "dotclk"}
STOP_FRAME = 0.1016                           # Eagle's default solder stop frame (4 mil), for drawn mask openings
SKIP_FIELDS = {"Reference", "Value", "Footprint", "ki_keywords", "ki_fp_filters", "ki_description", "ki_locked"}


def ename(s):
    """A name Eagle accepts for a package, symbol, deviceset or pad (no blanks or special characters)."""
    return re.sub(r"[^A-Za-z0-9_\-.+,/()$]", "_", str(s)).strip() or "_"


def attr_name(s):
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(s)).strip("_").upper() or "ATTR"


def n(v):
    """mm -> Eagle number text (0.1 micron)."""
    v = round(float(v), 4) + 0.0
    return f"{v:.4f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def rot_attr(a, mirror=False):
    a = round(float(a) % 360, 2) % 360
    if not a and not mirror:
        return None
    return ("M" if mirror else "") + "R" + n(a)


def _el(parent, tag, **kw):
    return ET.SubElement(parent, tag, {k.rstrip("_"): v for k, v in kw.items() if v is not None})


# ---------------------------------------------------------------- shapes
def _poly(parent, layer, pts, width=0.0, curves=None):
    """A filled polygon; curves: {vertex index: degrees} for an arc from that vertex to the next."""
    p = _el(parent, "polygon", width=n(width), layer=str(layer))
    for i, (x, y) in enumerate(pts):
        c = (curves or {}).get(i)
        _el(p, "vertex", x=n(x), y=n(y), curve=n(c) if c else None)
    return p


def _pad_outline(parent, layer, x, y, w, h, a, shape, rratio=0.0, poly=False):
    """The outline of a pad-like shape, filled, on one layer (exact arcs). poly: always as a <polygon> (paste / mask
    windows, so a reader tells them from a pad's own opening, which is a circle, wire or rectangle like the pad)."""
    if shape == "circle" or (shape == "oval" and abs(w - h) < 1e-9):
        if poly:
            _poly(parent, layer, [(x + w / 2, y), (x - w / 2, y)], 0.0, {0: 180, 1: 180})
        else:
            _el(parent, "circle", x=n(x), y=n(y), radius=n(w / 2), width="0", layer=str(layer))
        return
    if shape == "oval":
        r = min(w, h) / 2
        L = (max(w, h) - min(w, h)) / 2
        u = (L, 0.0) if w >= h else (0.0, L)
        e1, e2 = CM.rot(*u, a), CM.rot(-u[0], -u[1], a)
        if poly:
            v = CM.rot(-u[1] / L * r, u[0] / L * r, a)            # r across the long axis
            pts = [(x + e1[0] - v[0], y + e1[1] - v[1]), (x + e1[0] + v[0], y + e1[1] + v[1]),
                   (x + e2[0] + v[0], y + e2[1] + v[1]), (x + e2[0] - v[0], y + e2[1] - v[1])]
            _poly(parent, layer, pts, 0.0, {0: 180, 2: 180})
        else:
            _el(parent, "wire", x1=n(x + e1[0]), y1=n(y + e1[1]), x2=n(x + e2[0]), y2=n(y + e2[1]), width=n(2 * r), layer=str(layer))
        return
    r = rratio * min(w, h) if shape == "roundrect" else 0.0
    hw, hh = w / 2, h / 2
    if r <= 1e-9:
        if not poly:
            if abs(a % 90) < 1e-9:
                ww, hh2 = (w, h) if abs(a % 180) < 1e-9 else (h, w)
                _el(parent, "rectangle", x1=n(x - ww / 2), y1=n(y - hh2 / 2), x2=n(x + ww / 2), y2=n(y + hh2 / 2), layer=str(layer))
            else:
                _el(parent, "rectangle", x1=n(x - w / 2), y1=n(y - h / 2), x2=n(x + w / 2), y2=n(y + h / 2), layer=str(layer), rot=rot_attr(a))
            return
        pts, curves = [(hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)], {}
    else:
        pts = [(hw, -hh + r), (hw, hh - r), (hw - r, hh), (-hw + r, hh), (-hw, hh - r), (-hw, -hh + r), (-hw + r, -hh), (hw - r, -hh)]
        curves = {1: 90, 3: 90, 5: 90, 7: 90}
    pts = [(x + p[0], y + p[1]) for p in (CM.rot(u, v, a) for u, v in pts)]
    _poly(parent, layer, pts, 0.0, curves)


def _graphic(parent, g, layer):
    w = g.get("w", 0.0)
    if g["kind"] == "line":
        _el(parent, "wire", x1=n(g["a"][0]), y1=n(g["a"][1]), x2=n(g["b"][0]), y2=n(g["b"][1]), width=n(w), layer=str(layer))
    elif g["kind"] == "arc":
        _el(parent, "wire", x1=n(g["a"][0]), y1=n(g["a"][1]), x2=n(g["b"][0]), y2=n(g["b"][1]), width=n(w),
            layer=str(layer), curve=n(g["sweep"]))
    elif g["kind"] == "circle":
        if g["fill"]:
            _el(parent, "circle", x=n(g["c"][0]), y=n(g["c"][1]), radius=n(g["r"] + w / 2), width="0", layer=str(layer))
        else:
            _el(parent, "circle", x=n(g["c"][0]), y=n(g["c"][1]), radius=n(g["r"]), width=n(w), layer=str(layer))
    elif g["kind"] == "poly" and len(g["pts"]) >= 2:
        if g["fill"] and not g.get("open") and len(g["pts"]) >= 3:
            _poly(parent, layer, g["pts"], w)
        else:
            pts = list(g["pts"]) + ([] if g.get("open") else [g["pts"][0]])
            for a, b in zip(pts, pts[1:]):
                if a != b:
                    _el(parent, "wire", x1=n(a[0]), y1=n(a[1]), x2=n(b[0]), y2=n(b[1]), width=n(w), layer=str(layer))


# ---------------------------------------------------------------- packages
def _unique(base, used):
    k, name = 1, base
    while name.upper() in used:
        k += 1
        name = f"{base}@{k}"
    used.add(name.upper())
    return name


def package(fp, name):
    """A cad_model footprint -> (<package>, {pad number: [Eagle pad names]}, approximations)."""
    pk = ET.Element("package", name=name)
    if fp["descr"]:
        ET.SubElement(pk, "description").text = fp["descr"]
    names, used, approx, unnamed = {}, set(), [], 0
    for p in fp["pads"]:
        x, y, a, w, h = p["x"], p["y"], p["rot"], p["w"], p["h"]
        side_b = p.get("side") == "B"
        approx += [f"pad {p['num'] or '(unnumbered)'}: {m}" for m in p["approx"]]
        if p["kind"] == "window":
            for lay in p["layers"]:
                layer = ROLE_LAYER[lay]
                if p["shape"] == "custom":
                    for part in p["parts"]:
                        (_poly(pk, layer, part[1], part[2]) if part[0] == "poly" else
                         _el(pk, "circle", x=n(part[1][0]), y=n(part[1][1]), radius=n(part[2]), width="0", layer=str(layer)))
                    _pad_outline(pk, layer, x, y, w, h, a, "circle" if p.get("anchor") == "circle" else "rect", poly=True)
                else:
                    _pad_outline(pk, layer, x, y, w, h, a, p["shape"], p["rratio"], poly=True)
            continue
        if p["kind"] == "npth":
            d = p["drill"] or (w, h)
            _el(pk, "hole", x=n(x), y=n(y), drill=n(min(d)))
            size = max(w, h)
            if size > min(d) + 1e-6:          # copper / mask opening bigger than the hole: drawn round it
                if p.get("copper"):
                    for layer in (TOP, BOTTOM):
                        _el(pk, "circle", x=n(x), y=n(y), radius=n(size / 2), width="0", layer=str(layer))
                if p["mask"]:
                    g = p["mask_margin"] if p.get("mask_margin") is not None else STOP_FRAME
                    for layer in (TSTOP, BSTOP):
                        _el(pk, "circle", x=n(x), y=n(y), radius=n(size / 2 + g), width="0", layer=str(layer))
            if not p["mask"]:
                approx.append(f"hole at ({n(x)}, {n(-y)}): solder mask over the hole not kept (EAGLE opens the mask "
                              "over every hole)")
            if abs(d[0] - d[1]) > 1e-6:
                _slot_outline(pk, x, y, d, a)
                approx.append(f"hole at ({n(x)}, {n(-y)}): slot written as a hole plus its outline on Milling (46)")
            continue
        if p["num"]:
            pname = _unique(ename(p["num"]), used)
        else:
            unnamed += 1
            pname = _unique(f"_U{unnamed}", used)
        names.setdefault(p["num"], []).append(pname)
        cu, stop, cream = (BOTTOM, BSTOP, BCREAM) if side_b else (TOP, TSTOP, TCREAM)
        if p["kind"] == "smd":
            shape = p["shape"]
            ww, hh = (w, w) if shape == "circle" else (w, h)
            if shape == "custom":                 # anchor + the shape as copper (and mask / paste) polygons
                anchor = p.get("anchor") == "circle"
                rnd = 100 if anchor else 0
                approx.append(f"pad {p['num'] or pname}: custom shape written as its anchor pad plus copper polygons "
                              "(Eagle pads have no custom shapes)")
            else:
                rnd = 100 if shape in ("circle", "oval") else (int(round(p["rratio"] * 200)) if shape == "roundrect" else 0)
            mm_, pm = p.get("mask_margin"), p.get("paste_margin")
            draw_stop = p["mask"] and mm_ is not None and abs(mm_) > 1e-9
            draw_cream = p["paste"] and pm is not None and abs(pm) > 1e-9
            _el(pk, "smd", name=pname, x=n(x), y=n(y), dx=n(ww), dy=n(hh), layer=str(cu),
                roundness=str(rnd) if rnd else None, rot=rot_attr(a),
                stop=None if p["mask"] and not draw_stop and shape != "custom" else "no",
                cream=None if p["paste"] and not draw_cream and shape != "custom" else "no")
            eshape = {"circle": "circle", "oval": "oval", "roundrect": "roundrect"}.get(shape, "rect")
            if shape == "custom":
                for part in p["parts"]:
                    sg = STOP_FRAME if mm_ is None else mm_     # Eagle frames only its own pads: the frame drawn
                    for layer, on, grow in ((cu, True, 0.0), (stop, p["mask"], sg), (cream, p["paste"], pm or 0.0)):
                        if not on:
                            continue
                        if part[0] == "poly":
                            _poly(pk, layer, part[1], part[2] + 2 * grow if part[2] + 2 * grow > 0 else 0.0)
                        else:
                            _el(pk, "circle", x=n(part[1][0]), y=n(part[1][1]), radius=n(part[2] + grow), width="0", layer=str(layer))
                for layer, on, grow in ((stop, p["mask"], STOP_FRAME if mm_ is None else mm_), (cream, p["paste"], pm or 0.0)):
                    if on:
                        _pad_outline(pk, layer, x, y, ww + 2 * grow, hh + 2 * grow, a, "circle" if anchor else "rect")
                continue
            if draw_stop:
                _pad_outline(pk, stop, x, y, ww + 2 * mm_, hh + 2 * mm_, a, eshape, _grown_ratio(p, mm_))
            if draw_cream:
                _pad_outline(pk, cream, x, y, ww + 2 * pm, hh + 2 * pm, a, eshape, _grown_ratio(p, pm))
            continue
        # ---- through-hole
        d = p["drill"] or (min(w, h) / 2, min(w, h) / 2)
        drill = min(d)
        ox, oy = CM.rot(*p["offset"], a)              # copper centre - hole, footprint frame
        shape = p["shape"]
        custom = shape == "custom"
        if custom:
            approx.append(f"pad {p['num'] or pname}: custom through-hole pad written as a round pad plus copper polygons")
            shape = "circle"
        square = shape in ("rect", "roundrect")
        if shape == "roundrect":
            approx.append(f"pad {p['num'] or pname}: rounded corners written square (Eagle through-hole pads are "
                          "round, square or octagon)")
        side_len = min(w, h)
        exact = abs(w - h) < 1e-9 and abs(ox) < 1e-9 and abs(oy) < 1e-9
        diameter = side_len
        mm_ = p.get("mask_margin")
        own_stop = exact and p["mask"] and not custom and mm_ is not None and abs(mm_) > 1e-9
        _el(pk, "pad", name=pname, x=n(x), y=n(y), drill=n(drill), diameter=n(diameter),
            shape="square" if square and exact else None, rot=rot_attr(a),
            stop=None if p["mask"] and not own_stop else "no")
        if own_stop:                          # Eagle's own stop frame has no per-pad margin: draw the opening
            g = STOP_FRAME if mm_ is None else mm_
            for layer in (TSTOP, BSTOP):
                _pad_outline(pk, layer, x, y, diameter + 2 * g, diameter + 2 * g, a, "rect" if square else "circle")
        if custom:                            # the custom copper on both sides, and its mask opening
            for part in p.get("parts") or []:
                sg = STOP_FRAME if mm_ is None else mm_
                for layer, on, grow in ((TOP, True, 0.0), (BOTTOM, True, 0.0), (TSTOP, p["mask"], sg), (BSTOP, p["mask"], sg)):
                    if not on:
                        continue
                    if part[0] == "poly":
                        _poly(pk, layer, part[1], part[2] + 2 * grow if part[2] + 2 * grow > 0 else 0.0)
                    else:
                        _el(pk, "circle", x=n(part[1][0]), y=n(part[1][1]), radius=n(part[2] + grow), width="0", layer=str(layer))
        if not exact:                         # the rest of the copper, on both sides, and its mask opening
            cx, cy = x + ox, y + oy
            grow = mm_ if mm_ is not None else STOP_FRAME
            for layer, on, g in ((TOP, True, 0.0), (BOTTOM, True, 0.0), (TSTOP, p["mask"], grow), (BSTOP, p["mask"], grow)):
                if on:
                    _pad_outline(pk, layer, cx, cy, w + 2 * g, h + 2 * g, a, "rect" if square else "oval")
            if abs(ox) > 1e-9 or abs(oy) > 1e-9:
                approx.append(f"pad {p['num'] or pname}: copper offset from the hole written as copper next to the pad")
        if abs(d[0] - d[1]) > 1e-6:
            _slot_outline(pk, x, y, d, a)
            approx.append(f"pad {p['num'] or pname}: plated slot written as a hole of the slot width plus the slot "
                          "outline on Milling (46)")
    for g in fp["graphics"]:
        layer = ROLE_LAYER.get(g["layer"])
        if layer:
            _graphic(pk, g, layer)
    rx, ry = fp["texts"].get("reference", (0.0, 1.5))
    vx, vy = fp["texts"].get("value", (0.0, -1.5))
    for txt, (tx, ty), layer in ((">NAME", (rx, ry), 25), (">VALUE", (vx, vy), 27)):
        _el(pk, "text", x=n(tx), y=n(ty), size="1", layer=str(layer), ratio="15", align="center").text = txt
    return pk, names, approx


def _grown_ratio(p, g):
    """Rounded-rect ratio of a pad grown by g on every side (same corner centres)."""
    if p["shape"] != "roundrect":
        return 0.0
    m = min(p["w"], p["h"])
    return (p["rratio"] * m + g) / (m + 2 * g) if m + 2 * g > 0 else 0.0


def _slot_outline(parent, x, y, d, a):
    """A slot's outline (two lines, two half circles) on Milling, the slot's long axis along the pad's x if d[0] > d[1]."""
    r, L = min(d) / 2, (max(d) - min(d)) / 2
    ax = (1.0, 0.0) if d[0] >= d[1] else (0.0, 1.0)
    u, v = CM.rot(*ax, a), CM.rot(-ax[1], ax[0], a)
    pts = [(x + u[0] * s * L + v[0] * t * r, y + u[1] * s * L + v[1] * t * r) for s, t in ((1, -1), (1, 1), (-1, 1), (-1, -1))]
    for (p1, p2), curve in zip(zip(pts, pts[1:] + pts[:1]), (180, None, 180, None)):
        _el(parent, "wire", x1=n(p1[0]), y1=n(p1[1]), x2=n(p2[0]), y2=n(p2[1]), width="0", layer=str(MILLING),
            curve=n(curve) if curve else None)


# ---------------------------------------------------------------- symbols and devicesets
def overbar(s):
    return "!" + s + "!"


def _pin_length(L):
    for v, name in PIN_LENGTHS:
        if L >= v - 1e-6:
            return v, name
    return 0.0, "point"


def symbols(sym, base):
    """A cad_model symbol -> [(gate name, <symbol>, {KiCad pin number: Eagle pin name})], approximations."""
    units = sorted(u for u in sym["units"] if u > 0) or [1]
    common = sym["units"].get(0, {"pins": [], "shapes": []})
    gates, approx = [], []
    multi = len(units) > 1
    for i, u in enumerate(units):
        unit = sym["units"].get(u, {"pins": [], "shapes": []})
        name = f"{base}_{chr(65 + i) if i < 26 else i + 1}" if multi else base
        el = ET.Element("symbol", name=name)
        for s in common["shapes"] + unit["shapes"]:
            _sym_shape(el, s)
        pins, used = {}, set()
        for p in unit["pins"] + (common["pins"] if i == 0 else []):
            label = CM.pin_text(p["name"], overbar) or p["num"] or "P"
            pname = _unique(re.sub(r"[\s@]+", "_", label), used)
            L, lname = _pin_length(p["length"])
            if p["length"] - L > 1e-6:            # the rest of the line, so the body edge is reached as in KiCad
                dx, dy = CM.rot(1.0, 0.0, p["angle"])
                _el(el, "wire", x1=n(p["x"] + dx * L), y1=n(p["y"] + dy * L), x2=n(p["x"] + dx * p["length"]),
                    y2=n(p["y"] + dy * p["length"]), width="0.1524", layer=str(SYMBOLS), cap="flat")
            name_vis = not sym["pin_names_hidden"] and CM.pin_text(p["name"], overbar) != ""
            num_vis = not sym["pin_numbers_hidden"]
            vis = "off" if p["hidden"] else {(True, True): "both", (True, False): "pin", (False, True): "pad"}.get((name_vis, num_vis), "off")
            if p["type"] in ("power_out", "open_emitter", "free", "unspecified"):
                approx.append(f"pin {p['num']}: KiCad type {p['type']} written as Eagle {DIRECTION[p['type']]}")
            _el(el, "pin", name=pname, x=n(p["x"]), y=n(p["y"]), length=lname, direction=DIRECTION.get(p["type"], "pas"),
                function=FUNCTION.get(p["shape"]), visible=vis if vis != "both" else None, rot=rot_attr(p["angle"]))
            if p["num"] in pins:
                approx.append(f"pin number {p['num']} used by two pins: the second is not connected")
                continue
            pins[p["num"]] = (pname, f"G{i}")
        rx, ry, _, _ = sym["prop_at"].get("Reference", (0.0, 2.54, 0, False))
        vx, vy, _, _ = sym["prop_at"].get("Value", (0.0, -2.54, 0, False))
        _el(el, "text", x=n(rx), y=n(ry), size="1.778", layer=str(NAMES), align="center").text = ">NAME"
        _el(el, "text", x=n(vx), y=n(vy), size="1.778", layer=str(VALUES), align="center").text = ">VALUE"
        gates.append(("G$1" if not multi else chr(65 + i) if i < 26 else f"G{i + 1}", el, pins))
    return gates, approx


def _sym_shape(el, s):
    w = n(s.get("w") or CM.DEFAULT_STROKE)
    filled = s.get("fill") == "outline"
    if s["kind"] == "rect":
        (x1, y1), (x2, y2) = s["a"], s["b"]
        if filled:
            _el(el, "rectangle", x1=n(min(x1, x2)), y1=n(min(y1, y2)), x2=n(max(x1, x2)), y2=n(max(y1, y2)), layer=str(SYMBOLS))
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        for a, b in zip(pts, pts[1:]):
            _el(el, "wire", x1=n(a[0]), y1=n(a[1]), x2=n(b[0]), y2=n(b[1]), width=w, layer=str(SYMBOLS))
    elif s["kind"] == "poly":
        if filled and len(s["pts"]) >= 3:
            _poly(el, SYMBOLS, s["pts"], float(w))
        else:
            for a, b in zip(s["pts"], s["pts"][1:]):
                _el(el, "wire", x1=n(a[0]), y1=n(a[1]), x2=n(b[0]), y2=n(b[1]), width=w, layer=str(SYMBOLS))
    elif s["kind"] == "circle":
        _el(el, "circle", x=n(s["c"][0]), y=n(s["c"][1]), radius=n(s["r"]), width="0" if filled else w, layer=str(SYMBOLS))
    elif s["kind"] == "arc":
        _el(el, "wire", x1=n(s["a"][0]), y1=n(s["a"][1]), x2=n(s["b"][0]), y2=n(s["b"][1]), width=w, layer=str(SYMBOLS),
            curve=n(s["sweep"]))
    elif s["kind"] == "text" and s["text"].strip():
        _el(el, "text", x=n(s["at"][0]), y=n(s["at"][1]), size=n(max(0.5, s["size"])), layer=str(SYMBOLS),
            rot=rot_attr(s["angle"]), align="center").text = s["text"]


def deviceset(name, sym, gates, pkg_name, pad_names, fields):
    """<deviceset> with its gates, one device (the package) and the fields as technology attributes."""
    ref = re.sub(r"[^A-Za-z]", "", sym["props"].get("Reference", "U")) or "U" if sym else "U"
    ds = ET.Element("deviceset", name=name, prefix=ref)
    desc = fields.get("Description", "")
    ds_link = fields.get("Datasheet", "")
    text = html.escape(desc) + (f'<p><a href="{html.escape(ds_link)}">Datasheet</a></p>' if ds_link.startswith("http") else "")
    if text:
        ET.SubElement(ds, "description").text = text
    gs = ET.SubElement(ds, "gates")
    x = 0.0
    for gname, sel, _ in gates:
        _el(gs, "gate", name=gname, symbol=sel.get("name"), x=n(x), y="0")
        x += 25.4
    devs = ET.SubElement(ds, "devices")
    dev = _el(devs, "device", name="", package=pkg_name)
    missing = []
    if pkg_name:
        cons = ET.SubElement(dev, "connects")
        for gname, _, pins in gates:
            for num, (pname, _) in sorted(pins.items(), key=lambda kv: kv[1][0]):
                pads = pad_names.get(num)
                if not pads:
                    missing.append(num)
                    continue
                _el(cons, "connect", gate=gname, pin=pname, pad=" ".join(pads), route="any" if len(pads) > 1 else None)
    techs = ET.SubElement(dev, "technologies")
    tech = _el(techs, "technology", name="")
    seen = set()
    for k, v in fields.items():
        a = attr_name(k)
        if k in SKIP_FIELDS or a in seen or a in ("NAME", "VALUE", "PART", "GATE") or not str(v).strip():
            continue
        seen.add(a)
        _el(tech, "attribute", name=a, value=str(v))
    return ds, missing


def layers_el(parent):
    ls = ET.SubElement(parent, "layers")
    for num, name, color, fill in LAYERS:
        _el(ls, "layer", number=str(num), name=name, color=str(color), fill=str(fill), visible="yes", active="yes")


def library_xml(packages, symbols_, devicesets, description):
    root = ET.Element("eagle", version=EAGLE_VERSION)
    dr = ET.SubElement(root, "drawing")
    st = ET.SubElement(dr, "settings")
    _el(st, "setting", alwaysvectorfont="no")
    _el(st, "setting", verticaltext="up")
    _el(dr, "grid", distance="0.1", unitdist="inch", unit="inch", style="lines", multiple="1", display="no",
        altdistance="0.01", altunitdist="inch", altunit="inch")
    layers_el(dr)
    lib = ET.SubElement(dr, "library")
    ET.SubElement(lib, "description").text = description
    for tag, items in (("packages", packages), ("symbols", symbols_), ("devicesets", devicesets)):
        sec = ET.SubElement(lib, tag)
        for e in items:
            sec.append(e)
    ET.indent(root, "  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE eagle SYSTEM "eagle.dtd">\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------- reading back
def _f(e, k, d=0.0):
    v = e.get(k)
    return float(v) if v not in (None, "") else d


def _rot(e):
    m = re.search(r"R(-?[\d.]+)", e.get("rot") or "")
    return float(m.group(1)) if m else 0.0


def pad_number(name):
    """Eagle pad name -> the KiCad pad number it was written from (@2 dropped, _U1 = unnumbered)."""
    return "" if re.fullmatch(r"_U\d+(@\d+)?", name) else re.sub(r"@\d+$", "", name)


def _bbox_of(e):
    """Bounding box (y up) of a wire / rectangle / polygon / circle element, and whether it is filled."""
    t = e.tag
    if t == "wire":
        w = _f(e, "width") / 2
        x1, y1, x2, y2 = (_f(e, k) for k in ("x1", "y1", "x2", "y2"))
        if e.get("curve"):
            c, r, _ = _wire_arc(e)
            pts = CM.arc_points((x1, y1), c, float(e.get("curve")), 2.0)
        else:
            pts = [(x1, y1), (x2, y2)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs) - w, min(ys) - w, max(xs) + w, max(ys) + w)
    if t == "rectangle":
        x1, y1, x2, y2 = (_f(e, k) for k in ("x1", "y1", "x2", "y2"))
        a = _rot(e)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        pts = [(cx + p[0], cy + p[1]) for p in (CM.rot(u, v, a) for u, v in
               (((x1 - cx), (y1 - cy)), ((x2 - cx), (y1 - cy)), ((x2 - cx), (y2 - cy)), ((x1 - cx), (y2 - cy))))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    if t == "circle":
        r = _f(e, "radius") + _f(e, "width") / 2
        return (_f(e, "x") - r, _f(e, "y") - r, _f(e, "x") + r, _f(e, "y") + r)
    if t == "polygon":
        vs = list(e.iter("vertex"))
        pts = []
        for i, v in enumerate(vs):
            p = (_f(v, "x"), _f(v, "y"))
            pts.append(p)
            if v.get("curve"):
                q = vs[(i + 1) % len(vs)]
                c, r, sweep = _arc_from_curve(p, (_f(q, "x"), _f(q, "y")), float(v.get("curve")))
                pts += CM.arc_points(p, c, sweep, 2.0)
        w = _f(e, "width") / 2
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs) - w, min(ys) - w, max(xs) + w, max(ys) + w)
    return None


def _arc_from_curve(a, b, curve):
    """Eagle arc from a to b turning `curve` degrees counter-clockwise -> (centre, radius, sweep)."""
    (x1, y1), (x2, y2) = a, b
    chord = math.hypot(x2 - x1, y2 - y1)
    t = math.radians(curve)
    r = chord / (2 * math.sin(abs(t) / 2)) if abs(math.sin(t / 2)) > 1e-12 else 0.0
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    h = r * math.cos(t / 2) * (1 if t > 0 else -1)
    nx, ny = (-(y2 - y1) / chord, (x2 - x1) / chord) if chord else (0.0, 0.0)
    return (mx + nx * h, my + ny * h), abs(r), curve


def _wire_arc(e):
    return _arc_from_curve((_f(e, "x1"), _f(e, "y1")), (_f(e, "x2"), _f(e, "y2")), float(e.get("curve")))


def _centre(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _k(b):
    """y-up bounding box -> KiCad frame (y down) box."""
    return (b[0], -b[3], b[2], -b[1])


def read_package(pk):
    """An Eagle <package> -> ecadlib.parity's canonical footprint (KiCad frame, mm), reading the conventions above back:
    copper extensions and tStop / tCream openings centred on a pad are that pad's real shape and margins."""
    out = {"name": pk.get("name"), "pads": [], "windows": [], "courtyard": None, "bodies": 0, "embedded": 0, "notes": [],
           "copper": [], "openings": eagle_openings(pk)}
    kids = list(pk)
    used = set()                              # graphics taken as part of a pad
    by_layer = {}
    for i, e in enumerate(kids):
        if e.tag in ("wire", "rectangle", "polygon", "circle"):
            by_layer.setdefault(int(e.get("layer")), []).append((i, e))

    def near(lst, x, y, test=None):
        found = []
        for i, e in lst:
            if i in used:
                continue
            b = _bbox_of(e)
            if b and abs(_centre(b)[0] - x) < 1e-3 and abs(_centre(b)[1] - y) < 1e-3 and (test is None or test(e, b)):
                found.append((i, e, b))
        return found

    centres = {(round(c[0], 4), round(c[1], 4)) for _, e in by_layer.get(TOP, []) if e.tag in ("wire", "rectangle")
               for c in [_centre(_bbox_of(e))]}
    tht = [e for e in kids if e.tag in ("pad", "hole")]
    order = ([e for e in tht if (round(_f(e, "x"), 4), round(_f(e, "y"), 4)) in centres] +      # pads with their own
             [e for e in tht if (round(_f(e, "x"), 4), round(_f(e, "y"), 4)) not in centres] +  # copper first, then
             [e for e in kids if e.tag == "smd"])                                                 # the rest, then SMD
    for e in order:
        if e.tag == "smd":
            x, y, w, h, a = _f(e, "x"), _f(e, "y"), _f(e, "dx"), _f(e, "dy"), _rot(e)
            rnd = _f(e, "roundness")
            layer = int(e.get("layer"))
            shape = "rect" if rnd < 0.5 else ("round" if rnd >= 99.5 else "roundrect")
            corner = rnd if shape == "roundrect" else 0.0
            pad = {"num": pad_number(e.get("name")), "x": x, "y": -y, "shape": shape, "corner": corner, "approx": [],
                   "side": "F" if layer == TOP else "B", "drill": None, "offset": (0.0, 0.0)}
            cu, stop, cream = (TOP, TSTOP, TCREAM) if layer == TOP else (BOTTOM, BSTOP, BCREAM)
            extra = _custom_copper(by_layer.get(cu, []), used, x, y, w, h, a) if e.get("stop") == "no" else []
            pw, ph, prot = w, h, a
            if extra:                          # custom pad: anchor + copper polygons
                seen = {i for i, _, _ in extra}
                boxes = [b for i, _, b in extra]
                for i in seen:
                    used.add(i)
                anc = CM.round_rect(x, y, w, h, 0.0, a) if shape != "round" else [(x - w / 2, y - h / 2), (x + w / 2, y + h / 2)]
                xs = [p[0] for p in anc] + [b[0] for b in boxes] + [b[2] for b in boxes]
                ys = [p[1] for p in anc] + [b[1] for b in boxes] + [b[3] for b in boxes]
                pad["shape"], pad["anchor"] = "custom", "circle" if shape == "round" else "rect"
                # the outline is the primitives' box, as KiCad's is: the anchor is compared by its size
                pad["outline"] = _k((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                                     max(b[3] for b in boxes)))
                ux, uy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                for key, lay in (("mask", stop), ("paste", cream)):
                    hit = _overlapping(by_layer.get(lay, []), used, ux, uy, max(xs) - min(xs), max(ys) - min(ys))
                    pad[key] = bool(hit)
                    used.update(i for i, _, _ in hit)
                pad["mask_margin"] = pad["paste_margin"] = None
            else:
                pad["mask"], pad["paste"] = e.get("stop") != "no", e.get("cream") != "no"
                pad["mask_margin"] = pad["paste_margin"] = None
                for key, lay in (("mask", stop), ("paste", cream)):
                    if e.get("stop" if key == "mask" else "cream") != "no":
                        continue
                    hit = near(by_layer.get(lay, []), x, y, lambda o, b: _opening_like(o, shape))
                    if hit:
                        i, _, b = hit[0]
                        used.add(i)
                        bw, bh = _rot_extent(b, a, w, h)
                        pad[key] = True
                        pad[key + "_margin"] = round((bw - w) / 2, 6) if abs((bw - w) - (bh - h)) < 2e-3 else (bw - w) / 2
            pad["w"], pad["h"], pad["rot"] = PY.canon_pad(pw, ph, prot, pad["shape"] == "round")
            out["pads"].append(pad)
        elif e.tag == "pad":
            x, y, a, d = _f(e, "x"), _f(e, "y"), _rot(e), _f(e, "drill")
            dia = _f(e, "diameter") or d * 1.5
            shape = {"square": "rect", "octagon": "octagonal", "long": "round", "offset": "round"}.get(e.get("shape"), "round")
            w = h = dia
            pad = {"num": pad_number(e.get("name")), "x": x, "y": -y, "shape": shape, "corner": 0.0, "approx": [],
                   "side": "THT", "offset": (0.0, 0.0), "mask": e.get("stop") != "no", "mask_margin": None}
            cx, cy = x, y
            ext = _extension(by_layer, used, x, y, dia, a)
            if ext:                            # copper extension on both sides: the pad's real shape
                (i1, e1, b1), (i2, e2, b2) = ext
                used.update((i1, i2))
                w, h = _rot_extent(b1, a, None, None, e1)
                cx, cy = _centre(b1)
                shape = "round" if e1.tag == "wire" else "rect"
                for lay in (TSTOP, BSTOP):
                    for i, _, _ in near(by_layer.get(lay, []), cx, cy):
                        used.add(i)
                if abs(cx - x) > 1e-4 or abs(cy - y) > 1e-4:
                    pad["offset"] = (cx - x, -(cy - y))
            pad["shape"] = shape
            pad["drill"] = _slot(by_layer.get(MILLING, []), used, x, y, d)
            pad["w"], pad["h"], pad["rot"] = PY.canon_pad(w, h, a, shape == "round")
            out["pads"].append(pad)
        elif e.tag == "hole":
            x, y, d = _f(e, "x"), _f(e, "y"), _f(e, "drill")
            pad = {"num": "", "x": x, "y": -y, "shape": "round", "corner": 0.0, "approx": [], "side": "NPTH",
                   "offset": (0.0, 0.0), "mask": True, "mask_margin": None, "w": d, "h": d, "rot": 0.0}
            for layer in (TOP, BOTTOM, TSTOP, BSTOP):          # the copper / opening drawn round the hole
                for i, c, b in near(by_layer.get(layer, []), x, y, lambda o, b: o.tag == "circle" and _f(o, "radius") >= d / 2 - 1e-6):
                    used.add(i)
                    if layer in (TOP, BOTTOM):
                        pad["w"] = pad["h"] = max(pad["w"], 2 * _f(c, "radius"))
            pad["drill"] = _slot(by_layer.get(MILLING, []), used, x, y, d)
            out["pads"].append(pad)
    crt = []
    for i, e in enumerate(kids):
        if i in used or e.tag not in ("wire", "rectangle", "polygon", "circle"):
            continue
        layer, b = int(e.get("layer")), _bbox_of(e)
        if not b:
            continue
        filled = e.tag in ("rectangle", "polygon") or (e.tag == "circle" and _f(e, "width") == 0)
        if layer in (TSTOP, BSTOP, TCREAM, BCREAM) and filled:
            out["windows"].append(("mask" if layer in (TSTOP, BSTOP) else "paste", _k(_inner(e, b))))
        elif layer in (TOP, BOTTOM):
            out["copper"].append(_k(b))
        elif layer == 39:
            crt.append(_k(_inner(e, b)))
    if crt:
        out["courtyard"] = (min(b[0] for b in crt), min(b[1] for b in crt), max(b[2] for b in crt), max(b[3] for b in crt))
    for p in out["pads"]:
        if p["side"] in ("F", "B") and p.get("shape") != "custom":
            p.setdefault("paste_margin", None)
    return out


def _inner(e, b):
    """The geometric extent (line centres) of a wire or an outlined circle: the courtyard compares drawn coordinates."""
    if e.tag == "circle" and _f(e, "width") > 0:
        r = _f(e, "radius")
        return (_f(e, "x") - r, _f(e, "y") - r, _f(e, "x") + r, _f(e, "y") + r)
    if e.tag == "wire":
        w = _f(e, "width") / 2
        return (b[0] + w, b[1] + w, b[2] - w, b[3] - w)
    return b


def _rot_extent(b, a, w=None, h=None, e=None):
    """Width and height (in the pad's own frame) of a shape whose bounding box is b, for a pad turned by a."""
    if e is not None and e.tag == "wire":
        L = math.hypot(_f(e, "x2") - _f(e, "x1"), _f(e, "y2") - _f(e, "y1"))
        wd = _f(e, "width")
        ang = math.degrees(math.atan2(_f(e, "y2") - _f(e, "y1"), _f(e, "x2") - _f(e, "x1")))
        along_x = abs(math.sin(math.radians(ang - a))) < 0.5
        return (L + wd, wd) if along_x else (wd, L + wd)
    if e is not None and e.tag == "rectangle":
        x1, y1, x2, y2 = (_f(e, k) for k in ("x1", "y1", "x2", "y2"))
        rw, rh = abs(x2 - x1), abs(y2 - y1)
        ra = _rot(e)
        if abs((ra - a) % 180) < 1e-6:
            return rw, rh
        if abs((a - ra) % 90) < 1e-6:      # an axis-aligned rectangle of a pad at 90 / 270
            return (rh, rw) if abs(a % 180 - 90) < 1e-6 else (rw, rh)
        return rw, rh
    bw, bh = b[2] - b[0], b[3] - b[1]
    if abs(a % 180 - 90) < 1e-6:
        return bh, bw
    if abs(a % 90) > 1e-6 and w and h:      # a turned shape: grow the pad by the same margin on both axes
        c, s = abs(math.cos(math.radians(a))), abs(math.sin(math.radians(a)))
        g = ((bw - (w * c + h * s)) / (c + s)) if c + s else 0.0
        return w + g, h + g
    return bw, bh


def _shape_polys(e):
    """A filled wire / rectangle / polygon / circle element as polygons (y up)."""
    t = e.tag
    if t == "wire":
        p1, p2 = (_f(e, "x1"), _f(e, "y1")), (_f(e, "x2"), _f(e, "y2"))
        w = _f(e, "width")
        if e.get("curve"):
            c, r, _ = _wire_arc(e)
            pts = CM.arc_points(p1, c, float(e.get("curve")), 5.0)
            return [CM.capsule(a, b, w) for a, b in zip(pts, pts[1:])]
        return [CM.capsule(p1, p2, w)] if w > 0 else []
    if t == "rectangle":
        x1, y1, x2, y2 = (_f(e, k) for k in ("x1", "y1", "x2", "y2"))
        return [CM.round_rect((x1 + x2) / 2, (y1 + y2) / 2, abs(x2 - x1), abs(y2 - y1), 0.0, _rot(e))]
    if t == "circle":
        return [CM.disc((_f(e, "x"), _f(e, "y")), _f(e, "radius") + _f(e, "width") / 2)]
    if t == "polygon":
        vs = list(e.iter("vertex"))
        pts = []
        for i, v in enumerate(vs):
            p = (_f(v, "x"), _f(v, "y"))
            pts.append(p)
            if v.get("curve"):
                q = vs[(i + 1) % len(vs)]
                c, r, sweep = _arc_from_curve(p, (_f(q, "x"), _f(q, "y")), float(v.get("curve")))
                pts += CM.arc_points(p, c, sweep, 5.0)[1:-1]
        w = _f(e, "width")
        return [CM.grown_points(pts, w / 2) if w > 0 else pts]   # the outline is drawn too: wider by half its width
    return []


def eagle_openings(pk):
    """The mask and paste openings of an Eagle <package> per layer, as CAM output makes them: smd and pad openings
    (stop with the default frame) plus everything drawn on tStop / bStop / tCream / bCream."""
    out = {k: [] for k in ("mask_F", "mask_B", "paste_F", "paste_B")}
    lay = {TSTOP: "mask_F", BSTOP: "mask_B", TCREAM: "paste_F", BCREAM: "paste_B"}
    for e in pk:
        if e.tag == "smd":
            x, y, w, h, a = _f(e, "x"), _f(e, "y"), _f(e, "dx"), _f(e, "dy"), _rot(e)
            r = _f(e, "roundness") / 200 * min(w, h)
            s = "F" if int(e.get("layer")) == TOP else "B"
            if e.get("stop") != "no":
                out[f"mask_{s}"].append(CM.round_rect(x, y, w + 2 * STOP_FRAME, h + 2 * STOP_FRAME, r + STOP_FRAME, a))
            if e.get("cream") != "no":
                out[f"paste_{s}"].append(CM.round_rect(x, y, w, h, r, a))
        elif e.tag in ("pad", "hole") and e.get("stop") != "no":
            x, y, a = _f(e, "x"), _f(e, "y"), _rot(e)
            d = (_f(e, "diameter") or _f(e, "drill") * 1.5) if e.tag == "pad" else _f(e, "drill")
            g = d + 2 * STOP_FRAME                # holes get the stop frame too
            shape = e.get("shape", "round") if e.tag == "pad" else "round"
            if shape == "square":
                poly = CM.round_rect(x, y, g, g, STOP_FRAME, a)     # grown with round corners, as KiCad grows
            elif shape == "long":
                poly = CM.round_rect(x, y, 2 * d + 2 * STOP_FRAME, g, g / 2, a)
            elif shape == "octagon":
                poly = [(x + p[0], y + p[1]) for p in (CM.rot(g / 2 / math.cos(math.pi / 8) * math.cos(math.radians(22.5 + 45 * k)),
                        g / 2 / math.cos(math.pi / 8) * math.sin(math.radians(22.5 + 45 * k)), a) for k in range(8))]
            else:
                poly = CM.disc((x, y), g / 2)
            out["mask_F"].append(poly)
            out["mask_B"].append(poly)
        elif e.tag in ("wire", "rectangle", "polygon", "circle") and int(e.get("layer")) in lay:
            out[lay[int(e.get("layer"))]] += _shape_polys(e)
    return out


def _opening_like(e, shape):
    """Whether a mask / paste shape is the opening _pad_outline draws for a pad of this shape (not a window)."""
    if shape == "round":
        return e.tag in ("circle", "wire")
    if shape == "rect":
        return e.tag == "rectangle"
    if shape == "roundrect":
        return e.tag == "polygon" and len(e.findall("vertex")) == 8
    return e.tag in ("wire", "polygon")


def _overlapping(lst, used, x, y, w, h, a=0.0):
    """Unused shapes overlapping a pad's own w x h area (turned by a): a custom pad's copper, a pad's openings."""
    out = []
    box = CM.round_rect(x, y, w, h, 0.0, a)
    bx = (min(p[0] for p in box), min(p[1] for p in box), max(p[0] for p in box), max(p[1] for p in box))
    for i, e in lst:
        if i in used or e.tag not in ("polygon", "circle", "rectangle", "wire"):
            continue
        b = _bbox_of(e)
        if b and b[0] < bx[2] - 1e-6 and b[2] > bx[0] + 1e-6 and b[1] < bx[3] - 1e-6 and b[3] > bx[1] + 1e-6:
            out.append((i, e, b))
    return out


def _extension(by_layer, used, x, y, dia, a):
    """The copper the writer adds to an oval / rectangular through-hole pad: the same wire or rectangle on Top and
    Bottom, centred on the hole (or, for a copper offset, containing it). -> ((i, e, box) top, bottom) or None."""
    def pairs(test):
        for i1, e1 in by_layer.get(TOP, []):
            if i1 in used or e1.tag not in ("wire", "rectangle") or (e1.tag == "wire" and e1.get("curve")):
                continue
            b1 = _bbox_of(e1)
            if not test(b1) or abs(min(_rot_extent(b1, a, None, None, e1)) - dia) > 1e-4:   # the pad is the short side
                continue
            for i2, e2 in by_layer.get(BOTTOM, []):
                if i2 not in used and e2.tag == e1.tag and all(abs(p - q) < 1e-4 for p, q in zip(b1, _bbox_of(e2))):
                    return (i1, e1, b1), (i2, e2, _bbox_of(e2))
        return None
    return pairs(lambda b: abs(_centre(b)[0] - x) < 1e-4 and abs(_centre(b)[1] - y) < 1e-4) or \
        pairs(lambda b: b[0] < x < b[2] and b[1] < y < b[3])


def _inside(pt, e):
    """Whether a point lies in a filled polygon / circle / rectangle element (y up)."""
    if e.tag == "circle":
        return math.hypot(pt[0] - _f(e, "x"), pt[1] - _f(e, "y")) <= _f(e, "radius") + _f(e, "width") / 2 + 1e-6
    b = _bbox_of(e)
    if e.tag == "rectangle" or e.tag == "wire":
        return b[0] - 1e-6 <= pt[0] <= b[2] + 1e-6 and b[1] - 1e-6 <= pt[1] <= b[3] + 1e-6
    vs = [(_f(v, "x"), _f(v, "y")) for v in e.iter("vertex")]
    inside, j = False, len(vs) - 1
    for i in range(len(vs)):
        (xi, yi), (xj, yj) = vs[i], vs[j]
        if (yi > pt[1]) != (yj > pt[1]) and pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside or (b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3] and _f(e, "width") > 0 and False)


def _custom_copper(lst, used, x, y, w, h, a):
    """The copper polygons / circles of a custom pad: those holding the pad's centre or one of its anchor corners."""
    box = CM.round_rect(x, y, w, h, 0.0, a)
    probes = [(x, y)] + box
    return [(i, e, b) for i, e, b in _overlapping(lst, used, x, y, w, h, a) if e.tag in ("polygon", "circle")
            and any(_inside(pt, e) for pt in probes)]


def _slot(mill, used, x, y, d):
    """A slot outline on Milling around the hole at (x, y) (four wires whose ends are all as far from the hole centre)
    -> parity's drill (width, length, direction in the KiCad frame); a plain hole when there is none."""
    by_r = {}
    for i, e in mill:
        if i in used or e.tag != "wire":
            continue
        r1 = math.hypot(_f(e, "x1") - x, _f(e, "y1") - y)
        r2 = math.hypot(_f(e, "x2") - x, _f(e, "y2") - y)
        if abs(r1 - r2) < 1e-3 and r1 >= d / 2 - 1e-3:
            by_r.setdefault(round(r1, 3), []).append((i, e))
    four = next((v for v in by_r.values() if len(v) == 4), None)
    if not four:
        return (d, d, None)
    straight = [e for _, e in four if not e.get("curve")]
    if len(straight) != 2:
        return (d, d, None)
    used.update(i for i, _ in four)
    R = math.hypot(_f(straight[0], "x1") - x, _f(straight[0], "y1") - y)
    length = 2 * math.sqrt(max(0.0, R * R - (d / 2) ** 2)) + d
    dx, dy = _f(straight[0], "x2") - _f(straight[0], "x1"), _f(straight[0], "y2") - _f(straight[0], "y1")
    return (d, length, math.degrees(math.atan2(dy, dx)) % 180.0)      # counter-clockwise, as the pad rotation


def read_lbr(path):
    """-> {"packages": {name: canonical footprint}, "devicesets": {name: {...}}, "symbols": {name: <symbol>}}."""
    root = ET.parse(path).getroot()
    lib = root.find("drawing/library")
    pk = {p.get("name"): read_package(p) for p in lib.iter("package")} if lib is not None else {}
    syms = {s.get("name"): s for s in lib.iter("symbol")} if lib is not None else {}
    dss = {}
    for ds in (lib.iter("deviceset") if lib is not None else []):
        gates = {g.get("name"): g.get("symbol") for g in ds.iter("gate")}
        dev = ds.find("devices/device")
        pins = {}
        con = {(c.get("gate"), c.get("pin")): c.get("pad").split() for c in (dev.iter("connect") if dev is not None else [])}
        for gname, sname in gates.items():
            s = syms.get(sname)
            if s is None:
                continue
            for p in s.iter("pin"):
                pads = con.get((gname, p.get("name")), [])
                key = pad_number(pads[0]) if pads else f"(unconnected {p.get('name')})"
                length = LENGTH_MM.get(p.get("length") or "long", 7.62)
                ang = _rot(p)
                dx, dy = CM.rot(1.0, 0.0, ang)
                for w in s.iter("wire"):          # a line continuing the pin (written for lengths Eagle has not got)
                    if w.get("cap") == "flat" and abs(_f(w, "x1") - (_f(p, "x") + dx * length)) < 1e-4 and abs(_f(w, "y1") - (_f(p, "y") + dy * length)) < 1e-4 \
                            and abs((_f(w, "x2") - _f(w, "x1")) * dy - (_f(w, "y2") - _f(w, "y1")) * dx) < 1e-6:
                        length += math.hypot(_f(w, "x2") - _f(w, "x1"), _f(w, "y2") - _f(w, "y1"))
                        break
                pins[key] = {"name": eagle_pin_name(p.get("name")), "type": BACK.get(p.get("direction") or "io", "bidirectional"),
                             "hidden": p.get("visible") == "off", "x": _f(p, "x"), "y": _f(p, "y"), "angle": ang,
                             "length": length, "gate": gname, "pads": [pad_number(x) for x in pads]}
        tech = dev.find("technologies/technology") if dev is not None else None
        attrs = {a.get("name"): a.get("value") for a in (tech.iter("attribute") if tech is not None else [])}
        dss[ds.get("name")] = {"pins": pins, "package": dev.get("package") if dev is not None else None, "attrs": attrs,
                               "prefix": ds.get("prefix")}
    return {"packages": pk, "devicesets": dss, "symbols": syms}


def eagle_pin_name(s):
    """Eagle pin name -> KiCad spelling: @n dropped, !X! overbars -> ~{X}."""
    s = re.sub(r"@\d+$", "", s or "")
    return re.sub(r"!([^!]*)!?", lambda m: "~{" + m.group(1) + "}" if m.group(1) else "", s)


# ---------------------------------------------------------------- comparing
def _relabel(diffs, other):
    for d in diffs:
        d["msg"] = d["msg"].replace(" / Altium ", f" / {other} ")
    return diffs


def kicad_canon(text):
    """parity's canonical KiCad footprint, with the footprint-wide mask / paste margins applied to pads without their
    own (KiCad does; the writers write each pad's effective margin), pads in a stable order for pairing."""
    k = PY.kicad_footprint(text)
    t = sexpr.parse(text)
    fm, fpm = sexpr.first(t, "solder_mask_margin"), sexpr.first(t, "solder_paste_margin")
    for p in k["pads"]:
        if fm and p.get("mask_margin") is None and p.get("mask"):
            p["mask_margin"] = float(fm[1])
        if fpm and p.get("paste_margin") is None and p.get("paste"):
            p["paste_margin"] = float(fpm[1])
    k["pads"] = order_pads(k["pads"])
    return k


def order_pads(pads):
    """Pads at the same place with the same number (stacked top / bottom / through-hole) in one order, by side."""
    return sorted(pads, key=lambda p: (p["num"], round(p["x"], 3), round(p["y"], 3), p["side"]))


def compare_package(kicad_text, eagle_fp, approx=()):
    """Diffs between a KiCad footprint and its Eagle package read back (3D is not in an .lbr: not compared)."""
    k = kicad_canon(kicad_text)
    k["models"] = []
    cover = eagle_fp.pop("openings", None)
    eagle_fp["pads"] = order_pads(eagle_fp["pads"])
    for p in eagle_fp["pads"]:
        p.setdefault("paste", False)
    if approx:                                # what the writer had to approximate: shape / size differences are warnings
        names = {re.match(r"pad (\S+):", a).group(1) for a in approx if re.match(r"pad (\S+):", a)}
        for p in eagle_fp["pads"]:
            if p["num"] in names or f"{p['num']}" in names:
                p["approx"].append("written approximately")
    diffs = _relabel(PY.compare_footprints(k, eagle_fp), "Eagle")
    return diffs if cover is None else _check_cover(diffs, CM.openings(CM.footprint(kicad_text), STOP_FRAME), cover, "Eagle",
                                                    approx=any(a.startswith(("pad ", "hole at ")) for a in approx))


def _mask_paste(d):
    return any(w in f"{d['what']} {d['field']}" for w in ("paste", "mask"))


def _check_cover(diffs, want, got, tool, approx=False):
    """Settle mask / paste diffs on the layers themselves: a pad-by-pad reading can hand an opening to a different
    pad (a paste window over a pad without paste) when the layer is the same. Layers that differ are errors (warnings
    when the writer had to approximate pad shapes: the difference is then the approximation, reported already)."""
    bad = []
    for lay in sorted(want):
        same, diff, total = CM.same_cover(want[lay], got[lay])
        if not same:
            bad.append(lay)
            PY._d(diffs, "warn" if approx else "error", f"{lay.replace('_', ' ')} layer", "coverage", f"{total} mm2",
                  f"{diff} mm2 differs", "area covered by one and not the other")
            diffs[-1]["msg"] = diffs[-1]["msg"].replace(" / Altium ", f" / {tool} ")
    if not bad:
        for d in diffs:
            if d["level"] == "error" and _mask_paste(d):
                d["level"] = "warn"
                d["msg"] += " (the layer itself matches: the opening belongs to a neighbouring pad or window)"
    return diffs


def check_footprint(kicad_text, name="part"):
    """One .kicad_mod written as an Eagle package and read back: the parity diffs (the release check's [release] eagle)."""
    fp = CM.footprint(kicad_text)
    pk, _, approx = package(fp, ename(name))
    return compare_package(kicad_text, read_package(ET.fromstring(ET.tostring(pk, encoding="unicode"))), approx)


def compare_symbol(sym, ds):
    """A cad_model symbol vs a read-back deviceset: pins (number, name, type class, position, direction, length,
    pad connects)."""
    out = []
    kp = {p["num"]: p for u in sorted(sym["units"]) for p in sym["units"][u]["pins"]}
    ep = ds["pins"]
    if len(kp) != len([k for k in ep if not k.startswith("(unconnected")]):
        PY._d(out, "error", "symbol", "pin count", len(kp), len(ep))
    for num, p in sorted(kp.items()):
        e = ep.get(num)
        if not e:
            PY._d(out, "error", f"pin {num}", "missing", p["name"], "missing (no pad connected)")
            continue
        want = CM.pin_text(p["name"], lambda s: "~{" + s + "}") or ""
        got = e["name"]
        if want and got != re.sub(r"\s+", "_", want) and got != want:
            PY._d(out, "error", f"pin {num}", "name", p["name"], got)
        kt = DIRECTION.get(p["type"], "pas")
        if BACK.get(kt) != e["type"]:
            PY._d(out, "error", f"pin {num}", "electrical type", p["type"], e["type"])
        if abs(p["x"] - e["x"]) > 1e-3 or abs(p["y"] - e["y"]) > 1e-3:
            PY._d(out, "error", f"pin {num}", "position", (p["x"], p["y"]), (e["x"], e["y"]), "mm, y up")
        if PY._angle_diff(p["angle"], e["angle"], 360.0) > 0.01:
            PY._d(out, "error", f"pin {num}", "direction", p["angle"], e["angle"], "degrees")
        if abs(p["length"] - e["length"]) > 1e-3:
            PY._d(out, "error", f"pin {num}", "length", p["length"], e["length"], "mm")
        if p["hidden"] != e["hidden"]:
            PY._d(out, "error", f"pin {num}", "hidden", p["hidden"], e["hidden"])
    return _relabel(out, "Eagle")


def compare_fields(fields, attrs):
    out = []
    for k, v in fields.items():
        a = attr_name(k)
        if k in SKIP_FIELDS or a in ("NAME", "VALUE", "PART", "GATE") or not str(v).strip():
            continue
        if attrs.get(a) != str(v):
            PY._d(out, "error", "fields", k, v, attrs.get(a))
    return _relabel(out, "Eagle")


def pcbnew_python():
    """A Python interpreter that can import pcbnew (KiCad's), or None."""
    for exe in dict.fromkeys([sys.executable, "/usr/bin/python3", shutil.which("python3") or ""]):
        if exe and Path(exe).exists():
            try:
                if subprocess.run([exe, "-c", "import pcbnew"], capture_output=True, timeout=60).returncode == 0:
                    return exe
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def kicad_reread(lib_path, work, timeout=900):
    """KiCad's own importer reads `lib_path` (.lbr / .elibz): {"kicad", "footprints": {name: .kicad_mod text}} or None
    when no Python with pcbnew is found."""
    exe = pcbnew_python()
    if not exe:
        return None
    job = Path(__file__).parent / "assets" / "kicad_reread.py"
    r = subprocess.run([exe, str(job), str(lib_path), str(work)], capture_output=True, text=True, timeout=timeout)
    line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith("{")), None)
    if r.returncode or not line:
        return {"kicad": None, "error": (r.stderr or r.stdout).strip()[-400:] or f"exit {r.returncode}", "footprints": {}}
    got = json.loads(line)
    got["footprints"] = {k: Path(v).read_text(encoding="utf-8") for k, v in (got.get("footprints") or {}).items() if v}
    return got


KICAD_IGNORED = {"mask margin", "paste margin", "paste/mask windows", "extent", "paste", "presence"}


def compare_kicad_reread(src_text, reread_text, approx=()):
    """Source .kicad_mod vs what KiCad's importer made of the written library. KiCad's importers apply the other
    tool's default mask / paste rules and read drawn openings as graphics, so mask / paste are left to the first
    reader; copper is merged (pad + copper next to it = the pad's copper) before comparing."""
    k, r = kicad_canon(src_text), PY.kicad_footprint(reread_text)
    k["models"] = r["models"] = []
    r["bodies"] = r["embedded"] = 0
    for p in r["pads"]:
        p["num"] = pad_number(p["num"])
        p.pop("mask_margin", None)
        p.pop("paste_margin", None)
    merge_copper(r)
    r["pads"] = order_pads(r["pads"])
    if approx:
        for p in r["pads"]:
            p["approx"].append("written approximately")
    diffs = PY.compare_footprints(k, r)
    slots = {re.match(r"pad (\S+):", a).group(1) for a in approx if "slot" in a and re.match(r"pad (\S+):", a)}
    for d in diffs:                           # KiCad's Eagle importer ignores Milling: the slot is the round hole
        if d["field"] in ("drill", "slot direction") and d["what"].split(" ", 1)[-1] in slots:
            d["level"], d["msg"] = "warn", d["msg"] + " (slot on Milling: KiCad's importer reads only the hole)"
    keep = []
    for d in diffs:
        if d["field"] in KICAD_IGNORED and d["what"] in ("footprint", "exposed pad") or d["field"] in (
                "mask margin", "paste margin", "paste", "solder mask") or d["what"].endswith("window"):
            continue
        keep.append(d)
    for d in keep:
        d["msg"] = d["msg"].replace(" / Altium ", " / KiCad's importer ")
    return keep


def merge_copper(fp):
    """Copper graphics centred on a through-hole pad become that pad's copper (the writer's oval / rectangle trick)."""
    left = []
    for b in fp.get("copper") or []:
        c = _centre(b)
        hit = next((p for p in fp["pads"] if p["side"] == "THT" and abs(p["x"] + (p.get("offset") or (0, 0))[0] - c[0]) < 1e-3
                    and abs(p["y"] + (p.get("offset") or (0, 0))[1] - c[1]) < 1e-3), None)
        if hit is None:
            hit = next((p for p in fp["pads"] if p["side"] == "THT" and b[0] - 1e-3 <= p["x"] <= b[2] + 1e-3
                        and b[1] - 1e-3 <= p["y"] <= b[3] + 1e-3), None)
        if hit is None:
            left.append(b)
            continue
        bw, bh = b[2] - b[0], b[3] - b[1]
        if bw * bh > hit["w"] * hit["h"] - 1e-9:
            w, h, rot = PY.canon_pad(bw, bh, 0.0, False)
            hit["w"], hit["h"], hit["rot"] = w, h, rot
            if hit["shape"] in ("round",) and abs(bw - bh) > 1e-6:
                hit["shape"] = "round"
            hit.setdefault("merged", True)
    fp["copper"] = left


# ---------------------------------------------------------------- driver
def _kicad_side(plan, out_tmp, name, include_held, libs, http, rules, kicad_dir, log):
    if kicad_dir:
        kdir = Path(kicad_dir)
        rep = json.loads((kdir / "kicad_export.json").read_text())
        return kdir, rep
    kdir = Path(out_tmp) / "kicad"
    rep = X.export(plan, kdir, name, include_held, libs, http, log=log, rules=rules)
    return kdir, rep


def plan_parts(kdir, krep):
    """The KiCad export read back: [(row, symbol tree or None, footprint text or None)] and the symbol fields."""
    lib = krep["library"]
    sym_file = kdir / f"{lib}.kicad_sym"
    syms = {str(s[1]): s for s in sexpr.find(sexpr.parse(sym_file.read_text(encoding="utf-8")), "symbol")} if sym_file.is_file() else {}
    rows = []
    for row in krep["parts"]:
        sym = syms.get(row["symbol_name"]) if row.get("symbol") else None
        fpf = kdir / f"{lib}.pretty" / f"{row['footprint']}.kicad_mod" if row.get("footprint") else None
        rows.append((row, sym, fpf.read_text(encoding="utf-8") if fpf and fpf.is_file() else None))
    return rows


def export(plan, out, name="ecadlib", include_held=False, libs=(), http=None, log=print, rules=None, kicad_dir=None,
           second_reader=True):
    """Write <lib>.lbr for plan.json into `out`; returns the report dict (also eagle_export.json)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lib = X.kname(name)
    rep = {"plan": str(plan), "library": lib, "file": f"{lib}.lbr", "parts": [], "packages": {}, "errors": [],
           "warnings": [], "models": {}, "kicad_reread": None}
    with tempfile.TemporaryDirectory() as tmp:
        kdir, krep = _kicad_side(plan, tmp, name, include_held, libs, http, rules, kicad_dir, lambda *a, **k: None)
        rep["skipped"] = krep.get("skipped", [])
        rep["errors"] += [f"kicad-export: {e}" for e in krep.get("errors", [])]
        packages, symbols_, devicesets, pk_names, sym_used, ds_used = [], [], [], {}, set(), set()
        sources, approx_by_pkg = {}, {}
        for row, sym, fptext in plan_parts(kdir, krep):
            part = {"part": row["part"], "deviceset": None, "package": None, "notes": list(row.get("notes") or [])}
            pads = {}
            if fptext is not None:
                fpn = row["footprint"]
                if fpn not in pk_names:
                    fp = CM.footprint(fptext)
                    pname = _unique(ename(fpn), {k.upper() for k in pk_names.values()} | set())
                    pk, pads, approx = package(fp, pname)
                    packages.append(pk)
                    pk_names[fpn] = pname
                    sources[pname] = (fptext, approx)
                    approx_by_pkg[pname] = pads
                    rep["packages"][pname] = {"from": fpn, "approximations": approx, "notes": fp["notes"], "diffs": []}
                pads = approx_by_pkg[pk_names[fpn]]
                part["package"] = pk_names[fpn]
            if sym is not None:
                cs = CM.symbol(sym)
                fields = {k: v for k, v in cs["props"].items()}
                dname = _unique(ename(row["symbol_name"]), ds_used)
                gates, approx = symbols(cs, _unique(ename(row["symbol_name"]), sym_used))
                for _, sel, _ in gates:
                    sym_used.add(sel.get("name").upper())
                symbols_ += [g[1] for g in gates]
                ds, missing = deviceset(dname, cs, gates, part["package"], pads, fields)
                devicesets.append(ds)
                part["deviceset"] = dname
                part["approximations"] = approx + cs["notes"]
                part["_sym"], part["_fields"] = cs, fields
                if missing and part["package"]:
                    rep["errors"].append(f"{row['part']}: symbol pins without a pad in {part['package']}: {', '.join(missing)} "
                                         "(Eagle cannot place a device with unconnected pins)")
            rep["parts"].append(part)
        desc = f"ecadlib export of {Path(plan).name}: {len(devicesets)} parts. Generated by ecadlib; check against the datasheets."
        text = library_xml(packages, symbols_, devicesets, desc)
        lbr = out / f"{lib}.lbr"
        lbr.write_text(text, encoding="utf-8")
        shapes = kdir / f"{krep['library']}.3dshapes"
        if shapes.is_dir():
            dest = out / f"{lib}.3dshapes"
            dest.mkdir(exist_ok=True)
            for f in sorted(shapes.iterdir()):
                shutil.copyfile(f, dest / f.name)
                rep["models"][f.name] = f"{lib}.3dshapes/{f.name}"
        if (kdir / "NOTICE.txt").is_file():
            shutil.copyfile(kdir / "NOTICE.txt", out / "NOTICE.txt")
        # ---- read back what was written
        back = read_lbr(lbr)
        for pname, (src, approx) in sources.items():
            got = back["packages"].get(pname)
            if got is None:
                rep["errors"].append(f"package {pname} missing from the written .lbr")
                continue
            diffs = compare_package(src, got, approx)
            rep["packages"][pname]["diffs"] = diffs
            rep["errors"] += [f"package {pname}: {d['msg']}" for d in diffs if d["level"] == "error"]
        for part in rep["parts"]:
            cs = part.pop("_sym", None)
            fields = part.pop("_fields", None)
            if cs is None:
                continue
            ds = back["devicesets"].get(part["deviceset"])
            if ds is None:
                rep["errors"].append(f"deviceset {part['deviceset']} missing from the written .lbr")
                continue
            diffs = compare_symbol(cs, ds) + compare_fields(fields, ds["attrs"])
            if not part["package"]:
                diffs = [d for d in diffs if d["field"] != "missing"]
            part["diffs"] = diffs
            rep["errors"] += [f"deviceset {part['deviceset']}: {d['msg']}" for d in diffs if d["level"] == "error"]
        if second_reader:
            rr = kicad_reread(lbr, Path(tmp) / "reread")
            if rr is not None:
                rep["kicad_reread"] = {"kicad": rr.get("kicad"), "error": rr.get("error"), "checked": 0, "mismatched": 0}
                for pname, (src, approx) in sources.items():
                    got = rr["footprints"].get(pname)
                    if got is None:
                        rep["errors"].append(f"KiCad's Eagle importer did not load package {pname}"
                                             + (f" ({rr['errors'][pname]})" if pname in (rr.get("errors") or {}) else ""))
                        continue
                    diffs = compare_kicad_reread(src, got, approx)
                    rep["kicad_reread"]["checked"] += 1
                    errs = [d for d in diffs if d["level"] == "error"]
                    rep["kicad_reread"]["mismatched"] += bool(errs)
                    rep["packages"][pname]["kicad_reread"] = diffs
                    rep["errors"] += [f"package {pname} (KiCad's importer): {d['msg']}" for d in errs]
    for pk in rep["packages"].values():
        rep["warnings"] += [f"{a}" for a in pk["approximations"]]
    for part in rep["parts"]:
        rep["warnings"] += [f"{part['part']}: {a}" for a in part.get("approximations") or []]
    rep["counts"] = {"devicesets": len([p for p in rep["parts"] if p["deviceset"]]), "packages": len(rep["packages"]),
                     "symbols": len(symbols_)}
    (out / "eagle_export.json").write_text(json.dumps(rep, indent=2, default=str) + "\n")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a librarian plan's library as an Autodesk Fusion Electronics / EAGLE .lbr")
    ap.add_argument("plan", help="librarian plan.json (model paths are relative to its folder)")
    ap.add_argument("-o", "--out", default="out/eagle")
    ap.add_argument("--name", default="ecadlib", help="library name (default %(default)s)")
    ap.add_argument("--kicad", help="a kicad-export folder of the same plan to convert (default: export to a temporary folder)")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir for local: symbols (repeatable)")
    ap.add_argument("--include-held", action="store_true", help="also export parts the release check held")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan)")
    ap.add_argument("--no-kicad-check", action="store_true", help="skip the second read-back with KiCad's Eagle importer")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    from .httpcache import CachedHTTP
    rep = export(a.plan, a.out, a.name, a.include_held, a.lib, CachedHTTP(a.cache, offline=a.offline, timeout=60),
                 rules=a.rules, kicad_dir=a.kicad, second_reader=not a.no_kicad_check)
    if a.json:
        print(json.dumps(rep, default=str))
        return 1 if rep["errors"] else 0
    _print(rep, Path(a.out), "Fusion Electronics / EAGLE",
           "open it in Fusion (Electronics > Library > Open) or EAGLE (Library > Open), or add it in the Library Manager")
    return 1 if rep["errors"] else 0


def _print(rep, out, tool, how):
    for p in rep["parts"]:
        print(f"  {p['part']:<28} {('device ' + p['deviceset']) if p['deviceset'] else 'no symbol':<34} "
              f"{('package ' + p['package']) if p['package'] else 'no footprint'}")
    for s in rep.get("skipped", []):
        print(f"  skipped {s['part']}: {s['why'][:120]}")
    for w in rep["warnings"]:
        print(f"  approx  {w}")
    for e in rep["errors"]:
        print(f"  ERROR   {e}")
    rr = rep.get("kicad_reread")
    second = (f"; KiCad {rr['kicad']} importer re-read {rr['checked']} footprints, {rr['mismatched']} differ" if rr and rr.get("kicad")
              else "; KiCad's importer not available for a second read" if rr is None else f"; KiCad re-read failed: {rr.get('error')}")
    print(f"{rep['counts']['devicesets']} parts, {rep['counts']['packages']} footprints -> {out / rep['file']}: "
          f"read back {'with differences' if rep['errors'] else 'the same as the KiCad library'}{second}")
    print(f"{how}; {len(rep['models'])} STEP models in {out / (rep['library'] + '.3dshapes')}")


if __name__ == "__main__":
    sys.exit(main())
