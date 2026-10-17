"""Librarian plan -> an EasyEDA Pro device library (.elibz): symbols, footprints and devices, read back pad by pad.

    python -m ecadlib.easyeda_pro_export plan.json -o out/easyeda [--name ecadlib] [--kicad out/kicad]

The library is built the way kicad-export builds its own (the same parts, symbols and footprints; kicad-export runs
first, or --kicad names a folder it already wrote) and written natively, so nothing goes through an importer:

  <lib>.elibz          a zip: device.json (symbols, footprints and devices by uuid) + SYMBOL/<uuid>.esym +
                       FOOTPRINT/<uuid>.efoo, in EasyEDA Pro's line-per-record JSON format (the V2 format of .epro
                       projects and .elibz libraries; EasyEDA Pro opens it with File > Open / Import > EasyEDA Pro)
  <lib>.3dshapes/      the STEP models (EasyEDA links 3D models from its cloud library by uuid, which a file
                       cannot carry: upload them in the 3D model manager and attach them to the footprints)
  easyeda_export.json  what was written, what had to be approximated and every difference found on reading back

Format, as EasyEDA Pro writes it and KiCad 8's importer reads it (units: footprints in mil, symbols in 10 mil, y up):
  PAD   ["PAD", id, 0, net, layer (1 top, 2 bottom, 12 multi-layer), number, x, y, angle, hole (null | ["ROUND", w, h]
         | ["SLOT", w, h]), shape (["RECT", w, h, corner % of the shorter side] | ["ELLIPSE", w, h] | ["OVAL", w, h]
         | ["POLY", path]), special [], offset x, offset y, hole angle relative to the pad, plated, pad type,
         top / bottom solder mask expansion, top / bottom paste expansion (null = the board's rule;
         -3937.008 mil = no opening, as EasyEDA's own libraries mark it), connect mode, spokes...]
  POLY  ["POLY", id, 0, net, layer, width, path, locked]          FILL ["FILL", id, 0, net, layer, width, 0, [paths], 0]
  path  [x, y, "L", x, y, ..., "ARC", sweep (+ = counter-clockwise), x, y, ...] or ["CIRCLE", cx, cy, r]
  PIN   ["PIN", id, shown, electric, x, y (connection end), length, rotation (as KiCad's pin angle), color, shape, 0]
        with ATTR children NAME, NUMBER and "Pin Type" (IN / OUT / BI)

Two independent readers check the result: this module's own reader turns every footprint back into a .kicad_mod and
ecadlib.parity compares it with the source pad by pad (number, position, size, shape, corner radius, drill, slot, side,
paste, solder mask and their margins, paste / mask windows, courtyard), and every symbol pin by pin; then, when a
KiCad 8 or newer is installed, KiCad's own EasyEDA Pro importer reads the .elibz and parity compares its footprints
too (KiCad's importer ignores mask / paste expansions and plating, so those stay with the first reader)."""
import argparse, json, math, re, shutil, sys, tempfile, uuid, zipfile
from pathlib import Path

from . import cad_model as CM, eagle_export as EG, kicad_export as X, parity as PY, sexpr

MIL = 0.0254                                  # mm per mil (footprints)
SYM = 0.254                                   # mm per symbol unit (10 mil)
NO_OPENING = -3937.008                        # an expansion of -100 mm: no paste / mask opening at all
EDITOR = "2.2.36.7"                           # the EasyEDA Pro release whose files this mirrors
FP_LAYER = {"cu_f": 1, "cu_b": 2, "silk_f": 3, "silk_b": 4, "mask_f": 5, "mask_b": 6, "paste_f": 7, "paste_b": 8,
            "fab_f": 9, "fab_b": 10, "edge": 11, "doc": 13, "crtyd_f": 48, "crtyd_b": 48}
KI_LAYER = {1: "F.Cu", 2: "B.Cu", 3: "F.SilkS", 4: "B.SilkS", 5: "F.Mask", 6: "B.Mask", 7: "F.Paste", 8: "B.Paste",
            9: "F.Fab", 10: "B.Fab", 11: "Edge.Cuts", 13: "Dwgs.User", 48: "F.CrtYd", 49: "Cmts.User"}
LAYER_NAMES = [(1, "TOP", "Top Layer"), (2, "BOTTOM", "Bottom Layer"), (3, "TOP_SILK", "Top Silkscreen Layer"),
               (4, "BOT_SILK", "Bottom Silkscreen Layer"), (5, "TOP_SOLDER_MASK", "Top Solder Mask Layer"),
               (6, "BOT_SOLDER_MASK", "Bottom Solder Mask Layer"), (7, "TOP_PASTE_MASK", "Top Paste Mask Layer"),
               (8, "BOT_PASTE_MASK", "Bottom Paste Mask Layer"), (9, "TOP_ASSEMBLY", "Top Assembly Layer"),
               (10, "BOT_ASSEMBLY", "Bottom Assembly Layer"), (11, "OUTLINE", "Board Outline Layer"),
               (12, "MULTI", "Multi-Layer"), (13, "DOCUMENT", "Document Layer"), (14, "MECHANICAL", "Mechanical Layer"),
               (47, "HOLE", "Hole Layer"), (48, "COMPONENT_SHAPE", "Component Shape Layer"),
               (49, "COMPONENT_MARKING", "Component Marking Layer")]
ELECTRIC = {"input": (1, "IN"), "output": (2, "OUT"), "bidirectional": (3, "BI")}     # the rest: undefined
PIN_SHAPE = {"inverted": 2, "clock": 1, "inverted_clock": 2}
FIELD_MAP = {"MPN": "Manufacturer Part", "Manufacturer Part Number": "Manufacturer Part", "Manufacturer": "Manufacturer",
             "Datasheet": "Datasheet"}
SKIP_FIELDS = EG.SKIP_FIELDS | {"Description", "Reference"}
TAGS = {"parent_tag": [], "child_tag": []}


def uid(*parts):
    """A stable 32-hex uuid (the same plan writes the same file)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, "ecadlib-easyeda:" + "/".join(parts)).hex


def m(v):
    return round(v / MIL, 6) + 0.0


def s(v):
    return round(v / SYM, 4) + 0.0


class Ids:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"e{self.n}"


# ---------------------------------------------------------------- footprint paths (mil, y up)
def _pt(p):
    return [m(p[0]), m(p[1])]


def path_poly(pts, closed=True):
    out = _pt(pts[0]) + ["L"]
    for p in pts[1:] + (pts[:1] if closed else []):
        out += _pt(p)
    return out


def path_circle(c, r):
    """A full circle as EasyEDA's CIRCLE path. Not two half arcs: KiCad's EasyEDA Pro importer reads arcs in a pad
    outline as strokes only, so a round custom pad would lose its copper there."""
    return ["CIRCLE"] + _pt(c) + [m(r)]


def path_round_rect(cx, cy, w, h, r, a):
    """A w x h rectangle with corner radius r (exact arcs) centred on (cx, cy), turned by a, counter-clockwise."""
    hw, hh = w / 2, h / 2
    r = max(0.0, min(r, hw, hh))
    T = lambda u, v: (lambda q: (cx + q[0], cy + q[1]))(CM.rot(u, v, a))
    if r <= 1e-9:
        return path_poly([T(hw, -hh), T(hw, hh), T(-hw, hh), T(-hw, -hh)])
    seq = [((hw, -hh + r), None), ((hw, hh - r), None), ((hw - r, hh), 90), ((-hw + r, hh), None), ((-hw, hh - r), 90),
           ((-hw, -hh + r), None), ((-hw + r, -hh), 90), ((hw - r, -hh), None), ((hw, -hh + r), 90)]
    out = _pt(T(*seq[0][0]))
    for (u, v), arc in seq[1:]:
        if abs(u) < 1e-12 and abs(v) < 1e-12:
            continue
        out += (["ARC", arc] if arc else ["L"]) + _pt(T(u, v))
    return _tidy(out)


def _tidy(path):
    """Drop zero-length "L" steps a radius equal to a half side leaves (an oval's straight edge of length 0)."""
    out, i, last = [], 0, None
    while i < len(path):
        t = path[i]
        if isinstance(t, str):
            k = 2 if t == "ARC" else 1
            nxt = path[i + k:i + k + 2]
            if t == "L" and last and len(nxt) == 2 and abs(nxt[0] - last[0]) < 1e-6 and abs(nxt[1] - last[1]) < 1e-6:
                i += k + 2
                continue
            out += path[i:i + k] + nxt
            last = nxt
            i += k + 2
        else:
            out += path[i:i + 2]
            last = path[i:i + 2]
            i += 2
    return out


def shape_path(shape, x, y, w, h, a, rratio=0.0):
    if shape == "circle" or shape == "oval" and abs(w - h) < 1e-9:
        return path_circle((x, y), w / 2)
    r = min(w, h) / 2 if shape == "oval" else rratio * min(w, h) if shape == "roundrect" else 0.0
    return path_round_rect(x, y, w, h, r, a)


def part_path(part):
    if part[0] == "circle":
        return path_circle(part[1], part[2])
    if len(part) > 2 and part[2] > 0:         # KiCad draws the outline too: the polygon grows by half its width
        seg = CM.grown(part[1], part[2] / 2)
        out = _pt(seg[0][0])
        for i, (p, sweep) in enumerate(seg):
            nxt = seg[(i + 1) % len(seg)][0]
            out += (["ARC", round(sweep, 6)] if sweep else ["L"]) + _pt(nxt)
        return _tidy(out)
    return path_poly(part[1])


# ---------------------------------------------------------------- footprint writer
def _expansion(on, margin):
    return NO_OPENING if not on else None if margin is None else m(margin)


def _pad_shape(p):
    w, h = p["w"], p["h"]
    if p["shape"] == "circle":
        return ["ELLIPSE", m(w), m(w)]
    if p["shape"] == "oval":
        return ["OVAL", m(w), m(h)] if abs(w - h) > 1e-9 else ["ELLIPSE", m(w), m(w)]
    if p["shape"] == "roundrect":
        return ["RECT", m(w), m(h), round(p["rratio"] * 100, 4)]
    return ["RECT", m(w), m(h), 0]


def _inside_poly(pt, poly):
    x, y, inside = pt[0], pt[1], False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _custom_shapes(p, notes):
    """A custom pad -> (pad outline path, [extra copper paths]): the biggest primitive is the pad; the anchor goes
    when that primitive covers it; anything else is copper next to the pad (EasyEDA pads are one outline)."""
    parts = list(p["parts"])
    anchor = ("circle", (p["x"], p["y"]), p["w"] / 2) if p.get("anchor", "circle") == "circle" else \
        ("poly", CM.round_rect(p["x"], p["y"], p["w"], p["h"], 0.0, p["rot"]), 0.0)
    area = lambda q: math.pi * q[2] ** 2 if q[0] == "circle" else abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(q[1], q[1][1:] + q[1][:1]))) / 2
    if not parts:
        return part_path(anchor), []
    main = max(parts, key=area)
    rest = [q for q in parts if q is not main]
    apts = CM.disc(anchor[1], anchor[2], 30.0) if anchor[0] == "circle" else anchor[1]
    covered = main[0] == "poly" and all(_inside_poly(q, main[1]) or any(math.hypot(q[0] - v[0], q[1] - v[1]) < 1e-6 for v in main[1]) for q in apts) \
        or main[0] == "circle" and all(math.hypot(q[0] - main[1][0], q[1] - main[1][1]) <= main[2] + 1e-6 for q in apts)
    if not covered:
        rest.insert(0, anchor)
    if rest:
        notes.append(f"pad {p['num']}: custom pad with {len(rest) + 1} shapes written as the pad plus "
                     f"{len(rest)} copper fill{'s' if len(rest) > 1 else ''} overlapping it")
    return part_path(main), [part_path(q) for q in rest]


def footprint_lines(fp, title, designator="U?"):
    """A cad_model footprint -> (.efoo lines, approximations)."""
    ids, approx = Ids(), []
    lines = [["DOCTYPE", "FOOTPRINT", "1.8"], ["HEAD", {"editorVersion": EDITOR, "importFlag": 0}],
             ["CANVAS", 0, 0, "mm", 10, 10, 0.03937, 0.03937, 1, 1, 0, 0, 5]]
    lines += [["LAYER", n_, k, name, 3, "#ffffff", 1, "#7f7f7f", 1] for n_, k, name in LAYER_NAMES]
    lines += [["ACTIVE_LAYER", 1]]
    body = []
    for p in fp["pads"]:
        if p["kind"] == "window":
            for lay in p["layers"]:
                kind, side = lay.split("_")
                layer = {"mask_f": 5, "mask_b": 6, "paste_f": 7, "paste_b": 8}[lay]
                if p["shape"] == "custom" and p["parts"]:
                    paths = [shape_path("circle" if p.get("anchor", "circle") == "circle" else "rect", p["x"], p["y"], p["w"], p["h"], p["rot"])]
                    paths += [part_path(q) for q in p["parts"]]
                else:
                    paths = [shape_path(p["shape"], p["x"], p["y"], p["w"], p["h"], p["rot"], p["rratio"])]
                for path in paths:            # one record each: nested outlines in one FILL would be holes
                    body.append(["FILL", ids(), 0, "", layer, 0, 0, [path], 0])
            continue
        x, y, a = p["x"], p["y"], p["rot"]
        hole_a = 0.0                                  # the slot's own angle, needed once the pad angle is folded in
        hole, layer, plated = None, 1 if p["side"] == "F" else 2, 1
        mask = _expansion(p["mask"], p.get("mask_margin"))
        paste = _expansion(p.get("paste") and p["kind"] == "smd", p.get("paste_margin"))
        extra = []
        if p["kind"] in ("tht", "npth"):
            layer = 12
            dx, dy = p["drill"]
            hole = ["ROUND", m(dx), m(dx)] if abs(dx - dy) < 1e-9 else ["SLOT", m(dx), m(dy)]
            plated = 1 if p["kind"] == "tht" else 0
        if p["kind"] == "npth":
            d = max(p["w"], p["h"], p["drill"][0], p["drill"][1])
            shape = ["ELLIPSE", m(d), m(d)]
        elif p["shape"] == "custom":
            outline, extra = _custom_shapes(p, approx)
            shape, a, hole_a = ["POLY", outline], 0.0, a
        elif p["kind"] == "tht" and p["offset"] != (0.0, 0.0):    # copper off the hole: the copper outline itself
            ox, oy = CM.rot(*p["offset"], a)
            shape = ["POLY", shape_path(p["shape"], x + ox, y + oy, p["w"], p["h"], a, p["rratio"])]
            a, hole_a = 0.0, a
        else:
            shape = _pad_shape(p)
        for note in p.get("approx") or []:
            approx.append(f"pad {p['num']}: {note}")
        if p["kind"] == "tht" and hole[0] == "ROUND" and shape[0] == "POLY":
            pass
        top_mask, bot_mask = (mask, mask) if layer == 12 else (mask, None) if layer == 1 else (None, mask)
        top_paste, bot_paste = (paste, None) if layer == 1 else (None, paste) if layer == 2 else (NO_OPENING, NO_OPENING)
        pid = ids()
        body.append(["PAD", pid, 0, "", layer, p["num"], m(x), m(y), round(a % 360, 4) + 0.0, hole, shape, [], 0, 0,
                     round(hole_a % 360, 4) + 0.0, plated, 0, top_mask, bot_mask, top_paste, bot_paste, 0, None, None, None, None, []])
        for path in extra:
            body.append(["FILL", ids(), 0, "", 1 if layer != 2 else 2, 0, 0, [path], 0])
            if layer == 12:
                body.append(["FILL", ids(), 0, "", 2, 0, 0, [path], 0])
    for g in fp["graphics"]:
        layer = FP_LAYER.get(g["layer"])
        if not layer:
            continue
        w = m(g["w"])
        if g["kind"] == "line":
            body.append(["POLY", ids(), 0, "", layer, w, _pt(g["a"]) + ["L"] + _pt(g["b"]), 0])
        elif g["kind"] == "arc":
            body.append(["POLY", ids(), 0, "", layer, w, _pt(g["a"]) + ["ARC", round(g["sweep"], 4)] + _pt(g["b"]), 0])
        elif g["kind"] == "circle":
            if g["fill"]:
                body.append(["FILL", ids(), 0, "", layer, w, 0, [["CIRCLE", m(g["c"][0]), m(g["c"][1]), m(g["r"] + g["w"] / 2)]], 0])
            else:
                body.append(["POLY", ids(), 0, "", layer, w, ["CIRCLE", m(g["c"][0]), m(g["c"][1]), m(g["r"])], 0])
        elif g["kind"] == "poly" and len(g["pts"]) > 2:
            if g["fill"]:
                body.append(["FILL", ids(), 0, "", layer, w, 0, [path_poly(g["pts"])], 0])
            else:
                body.append(["POLY", ids(), 0, "", layer, w, path_poly(g["pts"]), 0])
    rx, ry = fp["texts"].get("reference", (0.0, 1.5))
    body.append(["ATTR", ids(), 0, "", 3, None, None, "Footprint", title, 0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, 0])
    body.append(["ATTR", ids(), 0, "", 3, m(rx), m(ry), "Designator", designator, 0, 1, "default", 39.37, 5.9, 0, 0, 5, 0, 0, 0, 0, 0])
    return lines + body, approx


# ---------------------------------------------------------------- symbol writer
def pin_name(name):
    """KiCad ~{X} overbars -> X# (EasyEDA pin names are plain text; # is the usual active-low mark)."""
    return CM.pin_text(name, lambda t: t + "#")


def symbol_lines(cs, title, designator):
    """A cad_model symbol -> (.esym lines, approximations)."""
    ids, approx = Ids(), []
    lines = [["DOCTYPE", "SYMBOL", "1.1"], None, ["LINESTYLE", "st1", None, None, None, None],
             ["FONTSTYLE", "st2", None, None, None, None, None, None, None, None, 0, 0]]
    common = cs["units"].get(0, {"pins": [], "shapes": []})
    units = sorted(u for u in cs["units"] if u > 0) or [1]
    lost_types = set()
    if any(pin_name(p["name"]) != p["name"] and p["name"] != "~" for u in cs["units"].values() for p in u["pins"]):
        approx.append("pin name overbars written as a trailing # (EasyEDA pin names are plain text)")
    for i, u in enumerate(units):
        d = cs["units"].get(u, {"pins": [], "shapes": []})
        pins, shapes = common["pins"] + d["pins"], common["shapes"] + d["shapes"]
        xs = [p["x"] for p in pins] + [q[0] for sh in shapes for q in _shape_pts(sh)]
        ys = [p["y"] for p in pins] + [q[1] for sh in shapes for q in _shape_pts(sh)]
        bbox = [s(min(xs)), s(max(ys)), s(max(xs)), s(min(ys))] if xs else [0, 0, 0, 0]
        lines.append(["PART", f"{title}.{i + 1}", {"BBOX": bbox}])
        if i == 0:
            lines.append(["ATTR", ids(), "", "Symbol", title, 0, 0, None, None, 0, "st2", 0])
            lines.append(["ATTR", ids(), "", "Designator", designator, 0, 0, None, None, 0, "st2", 0])
        for sh in shapes:
            lines += _sym_shape(sh, ids)
        for p in pins:
            el, ptype = ELECTRIC.get(p["type"], (0, None))
            if p["type"] not in ELECTRIC and p["type"] != "no_connect":
                lost_types.add(p["type"])
            pid = ids()
            lines.append(["PIN", pid, 0 if p["hidden"] else 1, el, s(p["x"]), s(p["y"]), s(p["length"]), int(round(p["angle"])) % 360,
                          None, PIN_SHAPE.get(p["shape"], 0), 0])
            lines.append(["ATTR", ids(), pid, "NAME", pin_name(p["name"]), 0, 0 if cs["pin_names_hidden"] else 1, None, None, 0, "st2", 0])
            lines.append(["ATTR", ids(), pid, "NUMBER", p["num"], 0, 0 if cs["pin_numbers_hidden"] else 1, None, None, 0, "st2", 0])
            if ptype:
                lines.append(["ATTR", ids(), pid, "Pin Type", ptype, 0, 0, None, None, 0, "st2", 0])
            if p["type"] == "no_connect":
                lines.append(["ATTR", ids(), pid, "NO_CONNECT", "yes", 0, 0, None, None, 0, "st2", 0])
    if lost_types:
        approx.append(f"pin types {', '.join(sorted(lost_types))} written as EasyEDA's undefined type "
                      "(EasyEDA pins are input, output, bidirectional or undefined)")
    lines[1] = ["HEAD", {"symbolType": 2, "originX": 0, "originY": 0, "version": EDITOR, "maxId": ids.n}]
    return lines, approx


def _shape_pts(sh):
    k = sh["kind"]
    if k == "rect":
        return [sh["a"], sh["b"]]
    if k == "poly":
        return sh["pts"]
    if k == "circle":
        return [(sh["c"][0] - sh["r"], sh["c"][1] - sh["r"]), (sh["c"][0] + sh["r"], sh["c"][1] + sh["r"])]
    if k == "arc":
        return [sh["a"], sh["b"]]
    return []


def _sym_shape(sh, ids):
    k = sh["kind"]
    if k == "rect":
        return [["RECT", ids(), s(sh["a"][0]), s(sh["a"][1]), s(sh["b"][0]), s(sh["b"][1]), 0, 0, 0, "st1", 0]]
    if k == "poly" and len(sh["pts"]) > 1:
        pts = [v for q in sh["pts"] for v in (s(q[0]), s(q[1]))]
        return [["POLY", ids(), pts, 0, "st1", 0]]
    if k == "circle":
        return [["CIRCLE", ids(), s(sh["c"][0]), s(sh["c"][1]), s(sh["r"]), "st1", 0]]
    if k == "arc":
        mid = CM.arc_mid(sh["a"], sh["c"], sh["sweep"])
        return [["ARC", ids(), s(sh["a"][0]), s(sh["a"][1]), s(mid[0]), s(mid[1]), s(sh["b"][0]), s(sh["b"][1]), "st1", 0]]
    if k == "text" and sh["text"].strip():
        return [["TEXT", ids(), s(sh["at"][0]), s(sh["at"][1]), sh.get("angle", 0.0), sh["text"], "st2", 0]]
    return []


# ---------------------------------------------------------------- library
def device_attrs(fields, sym_id, fp_id, designator, value):
    attrs = {"Symbol": sym_id or "", "Footprint": fp_id or "", "Name": value, "Designator": designator,
             "Add into BOM": "yes", "Convert to PCB": "yes"}
    for k, v in fields.items():
        if k in SKIP_FIELDS or not str(v).strip() or str(v) == "~":
            continue
        if re.fullmatch(r"(?i)lcsc( part( (number|#))?)?|lcsc_?pn|jlcpcb part #?", k):
            attrs["Supplier"], attrs["Supplier Part"] = "LCSC", str(v)
            continue
        attrs[FIELD_MAP.get(k, k)] = str(v)
    return attrs


def _jl(lines):
    return "\n".join(json.dumps(l, ensure_ascii=False, separators=(",", ":")) for l in lines) + "\n"


def write_elibz(path, symbols, footprints, devices):
    """symbols / footprints: {uuid: (title, lines)}; devices: {uuid: (title, description, attributes)}."""
    meta = {
        "symbols": {u: {"source": "", "desc": "", "tags": TAGS, "custom_tags": "", "title": t, "version": "", "type": 2}
                    for u, (t, _) in symbols.items()},
        "footprints": {u: {"source": "", "desc": "", "tags": TAGS, "custom_tags": "", "title": t, "version": "", "type": 4}
                       for u, (t, _) in footprints.items()},
        "devices": {u: {"source": "", "description": d, "tags": TAGS, "custom_tags": "", "title": t, "version": "",
                        "images": [], "attributes": a} for u, (t, d, a) in devices.items()},
    }
    def put(z, name, text):                   # a fixed date: the same library is the same file (clean git diffs)
        info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, text)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        put(z, "device.json", json.dumps(meta, ensure_ascii=False, indent=1))
        for u, (_, lines) in symbols.items():
            put(z, f"SYMBOL/{u}.esym", _jl(lines))
        for u, (_, lines) in footprints.items():
            put(z, f"FOOTPRINT/{u}.efoo", _jl(lines))


# ---------------------------------------------------------------- reader
def read_elibz(path):
    """An .elibz / .epro -> {"symbols": {title: lines}, "footprints": {title: lines}, "devices": {title: attrs}}."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        meta_name = next(n_ for n_ in ("device.json", "project.json", "footprint.json", "symbol.json") if n_ in names)
        meta = json.loads(z.read(meta_name).decode("utf-8-sig"))
        out = {"symbols": {}, "footprints": {}, "devices": {}, "ids": {}}
        for kind, folder, ext in (("symbols", "SYMBOL", "esym"), ("footprints", "FOOTPRINT", "efoo")):
            for u, d in (meta.get(kind) or {}).items():
                fn = f"{folder}/{u}.{ext}"
                if fn in names:
                    out[kind][d.get("display_title") or d["title"]] = parse_lines(z.read(fn).decode("utf-8-sig"))
                    out["ids"][u] = d.get("display_title") or d["title"]
        for u, d in (meta.get("devices") or {}).items():
            out["devices"][d.get("display_title") or d["title"]] = dict(d.get("attributes") or {}, _description=d.get("description", ""))
    return out


def parse_lines(text):
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def path_parts(path):
    """A path -> [(points (mm, y up), closed-able, is_circle (cx, cy, r) or None)] with arcs as points."""
    out, cur, prev, i = [], None, None, 0
    toks = list(path)
    while i < len(toks):
        t = toks[i]
        if isinstance(t, str):
            if t == "CIRCLE":
                cx, cy, r = (float(v) * MIL for v in toks[i + 1:i + 4])
                out.append((CM.disc((cx, cy), r, 2.0), (cx, cy, r)))
                i += 4
                cur = None
                continue
            if t == "R":
                x0, y0, w, h, ang = (float(v) for v in toks[i + 1:i + 6])
                cr = float(toks[i + 6]) if i + 6 < len(toks) and not isinstance(toks[i + 6], str) else 0.0
                cx, cy = CM.rot(w / 2, -h / 2, ang)
                out.append((CM.round_rect((x0 + cx) * MIL, (y0 + cy) * MIL, w * MIL, h * MIL, cr * MIL, ang, 2.0), None))
                i += 7 if cr else 6
                cur = None
                continue
            if t in ("ARC", "CARC"):
                sweep = float(toks[i + 1])
                end = (float(toks[i + 2]) * MIL, float(toks[i + 3]) * MIL)
                c = _arc_centre(prev, end, sweep)
                cur += CM.arc_points(prev, c, sweep, 2.0)[1:]
                prev = end
                cur[-1] = end
                i += 4
                continue
            if t == "L":
                i += 1
                while i + 1 < len(toks) and not isinstance(toks[i], str):
                    prev = (float(toks[i]) * MIL, float(toks[i + 1]) * MIL)
                    cur.append(prev)
                    i += 2
                continue
            i += 1
            continue
        prev = (float(toks[i]) * MIL, float(toks[i + 1]) * MIL)
        cur = [prev]
        out.append((cur, None))
        i += 2
    return out


def _arc_centre(a, b, sweep):
    """Centre of the arc from a to b turning `sweep` degrees (+ = counter-clockwise, y up)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    h = d / 2 / math.tan(math.radians(sweep) / 2)
    return mx - dy / d * h, my + dx / d * h


def _k(p):
    """(x, y) mm y up -> KiCad text."""
    return f"{p[0]:.6f} {-p[1] + 0.0:.6f}"


def footprint_to_kicad(lines, name):
    """.efoo lines -> .kicad_mod text, mask / paste expansions and all: the first reader."""
    out = [f'(footprint "{name}" (version 20221018) (generator ecadlib_easyeda_reader) (layer "F.Cu")']
    pads, fills, polys = [], [], []
    owner = None                              # copper fills right after a polygon pad: the rest of that pad's copper
    for l in lines:
        if not l:
            continue
        if l[0] == "FILL" and owner and l[4] in (1, 2):
            fills.append((l, owner))
            continue
        owner = l[1] if l[0] == "PAD" and l[10] and l[10][0] == "POLY" else None
        if l[0] == "PAD":
            pads.append(l)
        elif l[0] == "FILL":
            fills.append((l, None))
        elif l[0] == "POLY":
            polys.append(l)
        elif l[0] == "ATTR" and l[7] == "Designator" and isinstance(l[5], (int, float)):
            out.append(f'  (fp_text reference "REF**" (at {_k((l[5] * MIL, l[6] * MIL))}) (layer "F.SilkS") '
                       '(effects (font (size 1 1) (thickness 0.15))))')
    custom = {}
    for l in pads:
        _, pid, _, _, layer, num, x, y, ang, hole, shape = l[:11]
        plated, mask = l[15], [l[17], l[18]]
        paste = [l[19], l[20]]
        x, y = x * MIL, y * MIL
        if hole:
            kind = "thru_hole" if plated else "np_thru_hole"
            side_mask = mask[0]
            layers = '"*.Cu" "*.Mask"' if side_mask != NO_OPENING else '"*.Cu"'
            if not plated:
                layers = '"*.Mask"' if side_mask != NO_OPENING else ""
        else:
            kind = "smd"
            sd = "F" if layer == 1 else "B"
            i_ = 0 if layer == 1 else 1
            side_mask = mask[i_]
            layers = f'"{sd}.Cu"' + (f' "{sd}.Mask"' if mask[i_] != NO_OPENING else "") + (f' "{sd}.Paste"' if paste[i_] != NO_OPENING else "")
        extra = ""
        if side_mask not in (None, NO_OPENING):
            extra += f" (solder_mask_margin {side_mask * MIL:.6f})"
        if not hole and paste[i_] not in (None, NO_OPENING):
            extra += f" (solder_paste_margin {paste[i_] * MIL:.6f})"
        drill = ""
        if hole:
            dx, dy = hole[1] * MIL, hole[2] * MIL
            rel = (l[14] or 0) if isinstance(l[14], (int, float)) else 0
            if abs(((rel + 45) % 180) - 45) >= 45:
                dx, dy = dy, dx
            drill = f" (drill {dx:.6f})" if hole[0] == "ROUND" or abs(dx - dy) < 1e-9 else f" (drill oval {dx:.6f} {dy:.6f})"
        sh = shape[0]
        if sh == "POLY":
            prims = path_parts(shape[1])
            custom[pid] = (x, y, layer, [])
            body = " ".join(_prim(q, x, y) for q in prims)
            out.append(f'  (pad "{num}" {kind} custom (at {_k((x, y))} 0) (size @@{pid}size@@){drill} (layers {layers}){extra} '
                       f'(options (clearance outline) (anchor rect)) (primitives {body} @@{pid}@@))')
            custom[pid][3].extend(q[0] for q in prims)
            continue
        w, h = shape[1] * MIL, shape[2] * MIL
        if sh == "ELLIPSE":
            kshape = "circle" if abs(w - h) < 1e-9 else "oval"
        elif sh == "OVAL":
            kshape = "oval"
        elif sh == "RECT" and len(shape) > 3 and shape[3]:
            kshape = f"roundrect (roundrect_rratio {shape[3] / 100:.6f})"
        else:
            kshape = "rect"
        kind_shape = kshape.split(" ")[0]
        rr = kshape[len(kind_shape):]
        out.append(f'  (pad "{num}" {kind} {kind_shape} (at {_k((x, y))} {ang:g}) (size {w:.6f} {h:.6f}){drill}'
                   f' (layers {layers}){rr}{extra})')
    for l, own in fills:
        layer, paths = l[4], l[7]
        if paths and not isinstance(paths[0], list):
            paths = [paths]
        for path in paths:
            for pts, circle in path_parts(path):
                if own in custom:
                    if layer == 1 or custom[own][2] == 2:     # a through-hole pad's extras come once per side
                        custom[own][3].append(pts)
                    continue
                lay = KI_LAYER.get(layer)
                if lay:
                    out.append(f'  (fp_poly (pts {" ".join(f"(xy {_k(q)})" for q in pts)}) (stroke (width 0) (type solid)) '
                               f'(fill solid) (layer "{lay}"))')
    for l in polys:
        layer, width, path = l[4], l[5] * MIL, l[6]
        lay = KI_LAYER.get(layer)
        if not lay:
            continue
        for pts, circle in path_parts(path):
            if circle:
                out.append(f'  (fp_circle (center {_k(circle[:2])}) (end {_k((circle[0] + circle[2], circle[1]))}) '
                           f'(stroke (width {width:.6f}) (type solid)) (fill none) (layer "{lay}"))')
                continue
            for a, b in zip(pts, pts[1:]):
                out.append(f'  (fp_line (start {_k(a)}) (end {_k(b)}) (stroke (width {width:.6f}) (type solid)) (layer "{lay}"))')
    text = "\n".join(out) + "\n)\n"
    for pid, (x, y, _, shapes) in custom.items():     # the anchor: the box round the copper, centred on the pad
        first = len(path_parts(next(l[10][1] for l in pads if l[1] == pid)))
        more = shapes[first:]
        w = 2 * max(abs(q[0] - x) for pts in shapes for q in pts)
        h = 2 * max(abs(q[1] - y) for pts in shapes for q in pts)
        text = text.replace(f" @@{pid}@@", "".join(" " + _prim((q, None), x, y) for q in more))
        text = text.replace(f"@@{pid}size@@", f"{w:.6f} {h:.6f}")
    return text


def _prim(q, x, y):
    pts, circle = q
    if circle:
        return (f"(gr_circle (center {_k((circle[0] - x, circle[1] - y))}) (end {_k((circle[0] - x + circle[2], circle[1] - y))}) "
                "(width 0) (fill yes))")
    return f'(gr_poly (pts {" ".join(f"(xy {_k((p[0] - x, p[1] - y))})" for p in pts)}) (width 0) (fill yes))'


def symbol_pins(lines):
    """.esym lines -> {number: {"name", "type", "x", "y", "angle", "length", "hidden", "unit"}} (mm, y up)."""
    pins, attrs, unit, units = {}, {}, 0, {}
    for l in lines:
        if l[0] == "PART":
            unit += 1
        elif l[0] == "PIN":
            units[l[1]] = (unit, l)
        elif l[0] == "ATTR" and l[2]:
            attrs.setdefault(l[2], {})[l[3]] = l[4]
    for pid, (u, l) in units.items():
        a = attrs.get(pid, {})
        typ = {"IN": "input", "OUT": "output", "BI": "bidirectional"}.get(a.get("Pin Type"), "unspecified")
        if "NO_CONNECT" in a:
            typ = "no_connect"
        pins[str(a.get("NUMBER", ""))] = {"name": a.get("NAME", ""), "type": typ, "x": l[4] * SYM, "y": l[5] * SYM,
                                          "angle": float(l[7]) % 360, "length": l[6] * SYM, "hidden": not l[2], "unit": u,
                                          "electric": l[3]}
    return pins


def symbol_attrs(lines):
    return {l[3]: l[4] for l in lines if l[0] == "ATTR" and not l[2]}


# ---------------------------------------------------------------- compare
def compare_footprint(src_text, efoo_lines, approx=()):
    """Source .kicad_mod vs the written .efoo read back (as a .kicad_mod): parity, pad by pad."""
    back = footprint_to_kicad(efoo_lines, "back")
    k = EG.kicad_canon(src_text)
    a = PY.kicad_footprint(back)
    k["models"] = a["models"] = []
    a["bodies"] = a["embedded"] = 0
    a["pads"] = EG.order_pads(a["pads"])
    _note_approx(a, approx)
    copper = _adopt_polygon_pads(k, a, src_text, back)
    diffs = PY.compare_footprints(k, a) + copper
    if any(d["level"] == "error" and EG._mask_paste(d) for d in diffs):
        diffs = EG._check_cover(diffs, _openings(src_text), _openings(back), "EasyEDA")
    return EG._relabel(diffs, "EasyEDA")


def _openings(text):
    return CM.openings(CM.footprint(text))


def _copper_cover(src_text, back_text, importer=False):
    """Pad copper per side, source vs read back, on a fine grid -> diffs (none when the copper is the same).
    importer: the read-back is KiCad's EasyEDA Pro importer, which makes extra copper fills footprint drawings rather
    than pad copper and every hole plated, so filled copper drawings count on both sides and a plated pad where the
    source has a non-plated hole is left out."""
    src, back = CM.footprint(src_text), CM.footprint(back_text)
    npth = [(p["x"], p["y"]) for p in src["pads"] if p["kind"] == "npth"]

    def drawn(fp, side):
        if not importer:
            return []
        out = []
        for g in fp["graphics"]:
            if g["layer"] == f"cu_{side.lower()}" and g.get("fill"):
                if g["kind"] == "poly":
                    out.append(CM.grown_points(g["pts"], g["w"] / 2) if g["w"] > 0 else g["pts"])
                elif g["kind"] == "circle":
                    out.append(CM.disc(g["c"], g["r"] + g["w"] / 2))
        return out

    def hole(p):
        return importer and any(abs(p["x"] - x) < 1e-3 and abs(p["y"] - y) < 1e-3 for x, y in npth)

    out = []
    for side in ("F", "B"):
        want = [q for p in src["pads"] if p["kind"] == "tht" or p["kind"] == "smd" and p["side"] == side for q in CM.pad_polys(p)]
        got = [q for p in back["pads"] if (p["kind"] == "tht" and not hole(p)) or p["kind"] == "smd" and p["side"] == side
               for q in _back_polys(p)]
        same, diff, total = CM.same_cover(want + drawn(src, side), got + drawn(back, side), rel=0.01)
        if not same:
            PY._d(out, "error", f"{side}.Cu pads", "copper", f"{total} mm2", f"{diff} mm2 differs", "pad copper covered by one and not the other")
    return out


def _adopt_polygon_pads(k, a, src_text, back_text):
    """Pads written as an EasyEDA polygon (KiCad custom pads, copper off the hole) carry no KiCad anchor or offset:
    when the copper itself is the same (checked on a fine grid), the read-back pad takes the source's anchor, size
    and offset so the rest of the comparison (position, drill, mask, paste, exposed pad) runs as for any pad."""
    poly = [p for p in a["pads"] if p["shape"] == "custom"]
    if not poly:
        return []
    diffs = _copper_cover(src_text, back_text)
    if diffs:
        return diffs
    left = list(k["pads"])
    for p in poly:
        q = min((c for c in left if c["num"] == p["num"]), key=lambda c: math.hypot(c["x"] - p["x"], c["y"] - p["y"]), default=None)
        if q is None or math.hypot(q["x"] - p["x"], q["y"] - p["y"]) > 1e-3:
            continue
        left.remove(q)
        for key in ("shape", "w", "h", "rot", "corner", "offset", "anchor", "outline"):
            if key in q:
                p[key] = q[key]
            else:
                p.pop(key, None)
    return []


def _note_approx(fp, approx):
    names = {re.match(r"pad (\S+):", x).group(1) for x in approx if re.match(r"pad (\S+):", x)}
    for p in fp["pads"]:
        if p["num"] in names:
            p["approx"].append("written approximately")


SETTLED = ("custom anchor", "size/rotation", "custom outline", "shape", "copper offset from hole")


def _settle_custom(diffs, src_text, back_text, importer=False):
    """EasyEDA polygon pads have no KiCad anchor (size, rotation, anchor kind): those pads are compared by their copper
    instead, side by side on a fine grid; copper that differs is an error."""
    if not any(d["field"] in SETTLED for d in diffs):
        return diffs
    src, back = CM.footprint(src_text), CM.footprint(back_text)
    bad = _copper_cover(src_text, back_text, importer)
    diffs += bad
    custom = {p["num"] for p in src["pads"] if p["shape"] == "custom"} | {p["num"] for p in back["pads"] if p["shape"] == "custom"}
    if not bad:
        for d in diffs:
            num = d["what"][4:] if d["what"].startswith("pad ") else None
            if d["level"] == "error" and num in custom and d["field"] in SETTLED:
                d["level"] = "warn"
                d["msg"] += " (a polygon pad: its copper is the same)"
    return diffs


def _back_polys(p):
    if p["shape"] == "custom":                # the reader's anchor is a 0.01 mm dot: the primitives are the pad
        return [q[1] if q[0] == "poly" else CM.disc(q[1], q[2]) for q in p["parts"]] or CM.pad_polys(p)
    return CM.pad_polys(p)


def check_footprint(kicad_text, name="part"):
    """One .kicad_mod written as an EasyEDA Pro footprint and read back: the parity diffs ([release] easyeda)."""
    lines, approx = footprint_lines(CM.footprint(kicad_text), name)
    return compare_footprint(kicad_text, parse_lines(_jl(lines)), approx)


def compare_symbol(cs, pins):
    """A cad_model symbol vs the written .esym's pins read back."""
    out = []
    kp = {p["num"]: p for u in sorted(cs["units"]) for p in cs["units"][u]["pins"]}
    if len(kp) != len(pins):
        PY._d(out, "error", "symbol", "pin count", len(kp), len(pins))
    for num, p in sorted(kp.items()):
        e = pins.get(num)
        if not e:
            PY._d(out, "error", f"pin {num}", "missing", p["name"], "missing")
            continue
        if pin_name(p["name"]) != e["name"]:
            PY._d(out, "error", f"pin {num}", "name", p["name"], e["name"])
        want = p["type"] if p["type"] in ELECTRIC or p["type"] == "no_connect" else "unspecified"
        if want != e["type"]:
            PY._d(out, "error", f"pin {num}", "electrical type", p["type"], e["type"])
        if abs(p["x"] - e["x"]) > 1e-3 or abs(p["y"] - e["y"]) > 1e-3:
            PY._d(out, "error", f"pin {num}", "position", (p["x"], p["y"]), (round(e["x"], 4), round(e["y"], 4)), "mm, y up")
        if PY._angle_diff(p["angle"], e["angle"], 360.0) > 0.01:
            PY._d(out, "error", f"pin {num}", "direction", p["angle"], e["angle"], "degrees")
        if abs(p["length"] - e["length"]) > 1e-3:
            PY._d(out, "error", f"pin {num}", "length", p["length"], e["length"], "mm")
        if p["hidden"] != e["hidden"]:
            PY._d(out, "error", f"pin {num}", "hidden", p["hidden"], e["hidden"])
    return EG._relabel(out, "EasyEDA")


def compare_device(attrs, want):
    out = []
    for k, v in want.items():
        if attrs.get(k) != v:
            PY._d(out, "error", "device", k, v, attrs.get(k))
    return EG._relabel(out, "EasyEDA")


def compare_kicad_reread(src_text, reread_text, approx=()):
    """What KiCad's EasyEDA Pro importer made of a footprint vs the source. That importer reads pads' copper and
    holes only (every pad opens mask and paste, every hole is plated, copper off a hole is centred, polygon pads get
    a dot anchor and extra copper fills become drawings, and EasyEDA has no courtyard layer for it to map), so mask,
    paste, NPTH, copper offsets, anchors and the courtyard are left to the first reader."""
    diffs = EG.compare_kicad_reread(src_text, reread_text, approx)
    keep = []
    src = {p["num"] for p in CM.footprint(src_text)["pads"] if p["kind"] == "npth" or p["shape"] == "custom" or
           p["kind"] == "tht" and p["offset"] != (0.0, 0.0)}
    for d in diffs:
        num = d["what"][4:] if d["what"].startswith("pad ") else None
        if num is not None and num in src and d["field"] in ("side", "shape", "size/rotation", "custom anchor",
                                                             "custom outline", "copper offset from hole"):
            continue
        if d["field"] == "side" and d["kicad"] == "NPTH" or d["what"] == "courtyard":   # every hole plated; no
            continue                                                                    # courtyard layer
        keep.append(d)
    keep = _settle_custom(keep, src_text, reread_text, importer=True) if any(d["field"] in ("shape", "size/rotation") for d in keep) else keep
    for d in keep:
        d["msg"] = d["msg"].replace(" / EasyEDA ", " / KiCad's importer ").replace(" / Altium ", " / KiCad's importer ")
    return keep


# ---------------------------------------------------------------- driver
def export(plan, out, name="ecadlib", include_held=False, libs=(), http=None, log=print, rules=None, kicad_dir=None,
           second_reader=True):
    """Write <lib>.elibz for plan.json into `out`; returns the report dict (also easyeda_export.json)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lib = X.kname(name)
    rep = {"plan": str(plan), "library": lib, "file": f"{lib}.elibz", "parts": [], "packages": {}, "errors": [],
           "warnings": [], "models": {}, "kicad_reread": None}
    with tempfile.TemporaryDirectory() as tmp:
        kdir, krep = EG._kicad_side(plan, tmp, name, include_held, libs, http, rules, kicad_dir, lambda *a, **k: None)
        rep["skipped"] = krep.get("skipped", [])
        rep["errors"] += [f"kicad-export: {e}" for e in krep.get("errors", [])]
        symbols, footprints, devices, fp_ids, sources, titles = {}, {}, {}, {}, {}, set()
        for row, sym, fptext in EG.plan_parts(kdir, krep):
            part = {"part": row["part"], "device": None, "symbol": None, "footprint": None, "notes": list(row.get("notes") or [])}
            fields = {}
            cs = CM.symbol(sym) if sym is not None else None
            if cs is not None:
                fields = dict(cs["props"])
            ref = (fields.get("Reference") or "U").rstrip("?0123456789") or "U"
            fp_id = None
            if fptext is not None:
                fpn = row["footprint"]
                if fpn not in fp_ids:
                    fp = CM.footprint(fptext)
                    lines, approx = footprint_lines(fp, fpn, ref + "?")
                    fp_ids[fpn] = uid("footprint", fpn)
                    footprints[fp_ids[fpn]] = (fpn, lines)
                    sources[fpn] = (fptext, approx)
                    rep["packages"][fpn] = {"approximations": approx, "notes": fp["notes"], "diffs": []}
                fp_id = fp_ids[fpn]
                part["footprint"] = fpn
            sym_id = None
            if cs is not None:
                title = row["symbol_name"]
                while title in titles:
                    title += "_"
                titles.add(title)
                lines, approx = symbol_lines(cs, title, ref + "?")
                sym_id = uid("symbol", title)
                symbols[sym_id] = (title, lines)
                part["symbol"] = title
                part["approximations"] = approx + cs["notes"]
                attrs = device_attrs(fields, sym_id, fp_id, ref + "?", fields.get("Value") or title)
                devices[uid("device", title)] = (title, fields.get("Description") or fields.get("ki_description") or "", attrs)
                part["device"] = title
                part["_sym"], part["_attrs"] = cs, attrs
            rep["parts"].append(part)
        elibz = out / f"{lib}.elibz"
        write_elibz(elibz, symbols, footprints, devices)
        EG.copy_models(kdir / f"{krep['library']}.3dshapes", out / f"{lib}.3dshapes", rep, lib)
        EG.copy_notice(kdir, out)
        # ---- read back what was written
        back = read_elibz(elibz)
        for fpn, (src, approx) in sources.items():
            lines = back["footprints"].get(fpn)
            if lines is None:
                rep["errors"].append(f"footprint {fpn} missing from the written .elibz")
                continue
            diffs = compare_footprint(src, lines, approx)
            rep["packages"][fpn]["diffs"] = diffs
            rep["errors"] += [f"footprint {fpn}: {d['msg']}" for d in diffs if d["level"] == "error"]
        for part in rep["parts"]:
            cs, attrs = part.pop("_sym", None), part.pop("_attrs", None)
            if cs is None:
                continue
            lines = back["symbols"].get(part["symbol"])
            dev = back["devices"].get(part["device"])
            if lines is None or dev is None:
                rep["errors"].append(f"symbol / device {part['symbol']} missing from the written .elibz")
                continue
            diffs = compare_symbol(cs, symbol_pins(lines))
            want = dict(attrs)
            want["Symbol"] = part["symbol"]
            want["Footprint"] = part["footprint"] or ""
            got = dict(dev)
            got["Symbol"] = back["ids"].get(dev.get("Symbol"), "")
            got["Footprint"] = back["ids"].get(dev.get("Footprint"), "")
            diffs += compare_device(got, want)
            if part["footprint"]:
                pads = {p[5] for p in back["footprints"].get(part["footprint"], []) if p and p[0] == "PAD"}
                lost = sorted(n_ for n_ in symbol_pins(lines) if n_ not in pads)
                if lost:
                    PY._d(diffs, "error", "device", "pins without a pad", ", ".join(lost), "no pad")
                    diffs[-1]["msg"] = diffs[-1]["msg"].replace(" / Altium ", " / EasyEDA ")
            part["diffs"] = diffs
            rep["errors"] += [f"device {part['device']}: {d['msg']}" for d in diffs if d["level"] == "error"]
        if second_reader and sources:
            rr = EG.kicad_reread(elibz, Path(tmp) / "reread")
            if rr is not None:
                rep["kicad_reread"] = {"kicad": rr.get("kicad"), "error": rr.get("error"), "checked": 0, "mismatched": 0}
                if not rr.get("error"):
                    for fpn, (src, approx) in sources.items():
                        got = rr["footprints"].get(fpn)
                        if got is None:
                            rep["errors"].append(f"KiCad's EasyEDA Pro importer did not load footprint {fpn}"
                                                 + (f" ({rr['errors'][fpn]})" if fpn in (rr.get("errors") or {}) else ""))
                            continue
                        diffs = compare_kicad_reread(src, got, approx)
                        rep["kicad_reread"]["checked"] += 1
                        errs = [d for d in diffs if d["level"] == "error"]
                        rep["kicad_reread"]["mismatched"] += bool(errs)
                        rep["packages"][fpn]["kicad_reread"] = diffs
                        rep["errors"] += [f"footprint {fpn} (KiCad's importer): {d['msg']}" for d in errs]
    for fpn, pk in rep["packages"].items():
        rep["warnings"] += [f"{fpn}: {a}" for a in pk["approximations"]]
    for part in rep["parts"]:
        rep["warnings"] += [f"{part['part']}: {a}" for a in part.get("approximations") or []]
    if rep["models"]:
        rep["warnings"].append("3D models are not inside the .elibz (EasyEDA links them from its cloud library): "
                               f"upload the STEP files in {lib}.3dshapes and attach them to the footprints")
    rep["counts"] = {"devices": len(devices), "footprints": len(footprints), "symbols": len(symbols)}
    EG.handoff(out, rep, "easyeda", {f: EG.model_files(src, rep["models"]) for f, (src, _) in sources.items()})
    (out / "easyeda_export.json").write_text(json.dumps(rep, indent=2, default=str) + "\n")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a librarian plan's library as an EasyEDA Pro device library (.elibz)")
    ap.add_argument("plan", help="librarian plan.json (model paths are relative to its folder)")
    ap.add_argument("-o", "--out", default="out/easyeda")
    ap.add_argument("--name", default="ecadlib", help="library name (default %(default)s)")
    ap.add_argument("--kicad", help="a kicad-export folder of the same plan to convert (default: export to a temporary folder)")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir for local: symbols (repeatable)")
    ap.add_argument("--include-held", action="store_true", help="also export parts the release check held")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan)")
    ap.add_argument("--no-kicad-check", action="store_true", help="skip the second read-back with KiCad's EasyEDA Pro importer")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    from .httpcache import CachedHTTP
    rep = export(a.plan, a.out, a.name, a.include_held, a.lib, CachedHTTP(a.cache, offline=a.offline, timeout=60),
                 rules=a.rules, kicad_dir=a.kicad, second_reader=not a.no_kicad_check)
    if a.json:
        print(json.dumps(rep, default=str))
        return 1 if rep["errors"] else 0
    for p in rep["parts"]:
        print(f"  {p['part']:<28} {('device ' + p['device']) if p['device'] else 'no symbol':<34} "
              f"{('footprint ' + p['footprint']) if p['footprint'] else 'no footprint'}")
    for s_ in rep.get("skipped", []):
        print(f"  skipped {s_['part']}: {s_['why'][:120]}")
    for w in rep["warnings"]:
        print(f"  approx  {w}")
    for e in rep["errors"]:
        print(f"  ERROR   {e}")
    rr = rep.get("kicad_reread")
    second = (f"; KiCad {rr['kicad']} importer re-read {rr['checked']} footprints, {rr['mismatched']} differ" if rr and rr.get("kicad") and not rr.get("error")
              else "; KiCad 8+ not available for a second read" if rr is None or "cannot read" in str(rr.get("error"))
              else f"; KiCad re-read failed: {rr.get('error')}")
    out = Path(a.out)
    print(f"{rep['counts']['devices']} devices, {rep['counts']['footprints']} footprints -> {out / rep['file']}: "
          f"read back {'with differences' if rep['errors'] else 'the same as the KiCad library'}{second}")
    print("import it in EasyEDA Pro with File > Import > EasyEDA Pro; "
          f"{len(rep['models'])} STEP models in {out / (rep['library'] + '.3dshapes')} to attach "
          f"(steps in {out / 'INSTALL.md'}; all of it in {out / rep['bundle']})")
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
