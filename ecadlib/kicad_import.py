"""Official KiCad library footprints and symbols -> Altium .PcbLib / .SchLib (with 3D bodies).

    python3 -m ecadlib.kicad_import batch examples/kicad_import_parts.json -o out/altium_models_kicad [--step]
    python3 -m ecadlib.kicad_import footprint Package_TO_SOT_SMD:SOT-23 [--as NAME] [--height MM] [--step] -o DIR
    python3 -m ecadlib.kicad_import symbol Transistor_FET:AO3401A [--as NAME] [--footprint REF] [--param K=V] -o DIR

A <Lib>:<Name> reference is fetched from gitlab.com/kicad/libraries (kicad-footprints, kicad-symbols with
KiCad 9+ per-symbol <Lib>.kicad_symdir/<Name>.kicad_sym files or a KiCad 7/8 <Lib>.kicad_sym, kicad-packages3D)
through httpcache.CachedHTTP. A local path works too: X.kicad_mod, Lib.kicad_sym:Name or X.kicad_sym.
Output: <out>/PcbLib/<name>.PcbLib and <out>/SchLib/<name>.SchLib, the layout `altium365 plan --models` reads.

Footprints: SMD, plated and non-plated through-hole pads (round and slotted drills), rect / roundrect / oval /
circle pads, trapezoid and custom pads approximated by their bounding rectangle, pad rotation, fp_line / fp_rect /
fp_poly / fp_circle / fp_arc on silkscreen, fab (Mechanical 13), courtyard (Mechanical 15), F.Cu, F.Mask, F.Paste.
Not converted (listed in the notes): text, zones, per-pad mask margins, drill offsets, pin-graphic styles.
3D: an extruded ComponentBody on Mechanical 13 over the fab outline (else the courtyard), its height from the
KiCad STEP model (max z after the model transform), else DEFAULT_HEIGHT, else --height. --step embeds the STEP
model itself (Library/Models, AltiumSharp layout and checksum) when the KiCad model has no offset or rotation.
Symbols: pins (number, name, type, orientation, length, hidden), rectangles, polylines, circles, arcs, text,
multi-unit symbols as Altium multi-part, `extends` chains; Value/Footprint/Datasheet/Description/MPN as parameters.

KiCad's libraries are CC-BY-SA 4.0 with a design-use exception: converted files are not committed (.gitignore)."""
import argparse, json, math, re, sys
from pathlib import Path

from . import altium_lib as AL, sexpr
from .httpcache import CachedHTTP

GITLAB = "https://gitlab.com/kicad/libraries/{repo}/-/raw/master/{path}"
LAYERS = {"F.SilkS": AL.TOP_OVERLAY, "B.SilkS": AL.BOTTOM_OVERLAY, "F.Fab": AL.MECH13, "F.CrtYd": AL.MECH15,
          "F.Cu": AL.TOP, "F.Mask": AL.TOP_SOLDER, "F.Paste": AL.TOP_PASTE}
# overall height (mm) when the KiCad model gives none: first regex matching the footprint name wins
DEFAULT_HEIGHT = [(r"^SOT-23", 1.2), (r"^SOT-223", 1.8), (r"^(LQFP|TQFP)", 1.6), (r"^(SOIC|SOP)", 1.75), (r"^(TSSOP|MSOP)", 1.2),
                  (r"QFN|DFN|WSON|SON", 0.8), (r"_0402_", 0.6), (r"_0603_", 0.9), (r"_0805_", 1.3), (r"_1206_", 1.6),
                  (r"^Crystal_SMD_3225", 1.0), (r"^Crystal_SMD_2016", 0.8), (r"^USB_C_Receptacle", 3.3), (r"^JST_SH_", 2.95),
                  (r"^PinHeader_.*P1.27mm.*SMD", 4.3), (r"^ESP32-S3-WROOM", 3.25)]
KTYPE = {"input": "input", "output": "output", "bidirectional": "io", "tri_state": "tristate", "passive": "passive",
         "free": "passive", "unspecified": "passive", "power_in": "power_in", "power_out": "power_out",
         "open_collector": "open_collector", "open_emitter": "open_emitter", "no_connect": "nc"}
SIDE = {0: "right", 90: "top", 180: "left", 270: "bottom"}


# ---------------------------------------------------------------- fetching
def fetch(http, repo, path):
    r = http.get(GITLAB.format(repo=repo, path=path))
    if r.status == 404:
        return None
    if not r.ok:
        raise IOError(f"{repo}/{path}: {r.error}")
    return r.text


def load_footprint(ref, http):
    """'Lib:Name' or a .kicad_mod path -> (text, source label)."""
    if Path(ref).suffix == ".kicad_mod":
        return Path(ref).read_text(), Path(ref).name
    lib, name = ref.split(":", 1)
    text = fetch(http, "kicad-footprints", f"{lib}.pretty/{name}.kicad_mod")
    if text is None:
        raise FileNotFoundError(f"KiCad footprint {ref} not found")
    return text, f"KiCad {ref}"


def load_symbol(ref, http):
    """'Lib:Name', 'path/Lib.kicad_sym:Name' or 'path/Name.kicad_sym' -> [symbol, parent, ...] (extends chain), label."""
    m = re.match(r"(.+\.kicad_sym)(?::(.+))?$", ref)
    if m:                                     # local file; parents from the same file or sibling files (symdir)
        path = Path(m.group(1))
        def get(name):
            for f in (path, path.with_name(f"{name}.kicad_sym")):
                if f.exists():
                    s = _find_symbol(sexpr.parse(f.read_text()), name)
                    if s:
                        return s
            raise FileNotFoundError(f"symbol {name} not in {path}")
        name = m.group(2) or [s[1] for s in sexpr.find(sexpr.parse(path.read_text()), "symbol")][0]
        label = path.name
    else:
        lib, name = ref.split(":", 1)
        cache = {}
        def get(name):
            text = fetch(http, "kicad-symbols", f"{lib}.kicad_symdir/{name}.kicad_sym")        # KiCad 9+
            if text is None:                                                                       # KiCad 7/8 single file
                if "lib" not in cache:
                    cache["lib"] = fetch(http, "kicad-symbols", f"{lib}.kicad_sym")
                text = cache["lib"]
            s = _find_symbol(sexpr.parse(text), name) if text else None
            if not s:
                raise FileNotFoundError(f"KiCad symbol {lib}:{name} not found")
            return s
        label = f"KiCad {ref}"
    chain = [get(name)]
    while sexpr.first(chain[-1], "extends") and len(chain) < 8:
        chain.append(get(sexpr.first(chain[-1], "extends")[1]))
    return chain, label


def _find_symbol(lib, name):
    return next((s for s in sexpr.find(lib, "symbol") if s[1] == name), None)


# ---------------------------------------------------------------- geometry helpers
def _f(node, i=1):
    return float(node[i])


def _xy(node):                                # KiCad footprint point -> Altium (y up)
    return float(node[1]), -float(node[2])


def _rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def _rect_pts(cx, cy, w, h, deg=0.0):
    return [(cx + x, cy + y) for x, y in (_rot(sx * w / 2, sy * h / 2, deg) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)))]


def _arc3(s, m, e):
    """Arc through three points -> (cx, cy, r, a0, a1), counter-clockwise from a0 to a1 (degrees)."""
    (ax, ay), (bx, by), (cx_, cy_) = s, m, e
    d = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    if abs(d) < 1e-12:
        raise ValueError("degenerate arc")
    ux = ((ax * ax + ay * ay) * (by - cy_) + (bx * bx + by * by) * (cy_ - ay) + (cx_ * cx_ + cy_ * cy_) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx_ - bx) + (bx * bx + by * by) * (ax - cx_) + (cx_ * cx_ + cy_ * cy_) * (bx - ax)) / d
    ang = lambda x, y: math.degrees(math.atan2(y - uy, x - ux)) % 360
    a0, a1 = ang(ax, ay), ang(cx_, cy_)
    ccw = (bx - ax) * (cy_ - by) - (by - ay) * (cx_ - bx) > 0
    return ux, uy, math.hypot(ax - ux, ay - uy), *((a0, a1) if ccw else (a1, a0))


def _arc_pts(s, m, e, step=10.0):
    cx, cy, r, a0, a1 = _arc3(s, m, e)
    span = (a1 - a0) % 360 or 360
    n = max(2, int(span / step) + 1)
    pts = [(cx + r * math.cos(math.radians(a0 + span * k / n)), cy + r * math.sin(math.radians(a0 + span * k / n))) for k in range(n + 1)]
    return pts if math.dist(pts[0], s) < math.dist(pts[0], e) else pts[::-1]


def _pts(node, conv=_xy):
    """(pts (xy ..) (arc (start)(mid)(end)) ...) -> point list, arcs expanded to segments."""
    out = []
    for c in node[1:]:
        if c[0] == "xy":
            out.append(conv(c))
        elif c[0] == "arc":
            s, m, e = (conv(sexpr.first(c, k)) for k in ("start", "mid", "end"))
            out += _arc_pts(s, m, e)[0 if not out else 1:]
    return out


def _hull(pts):
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in pts))
    if len(pts) < 3:
        return pts
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def _filled(g):
    f = sexpr.first(g, "fill")
    if not f or len(f) < 2:
        return False
    v = f[1] if isinstance(f[1], str) else (sexpr.first(f, "type") or [None, "none"])[1]
    return v in ("yes", "solid", "background", "outline", "color")


def _width(g):
    w = sexpr.first(sexpr.first(g, "stroke") or [], "width") or sexpr.first(g, "width")
    return float(w[1]) if w else 0.0


def _note(notes, msg):
    if msg not in notes:
        notes.append(msg)


def _hidden(node):                            # KiCad 7 bare `hide` atom or KiCad 9 `(hide yes)`
    h = sexpr.first(node, "hide")
    return "hide" in node[1:] or bool(h and h[1:2] == ["yes"])


# ---------------------------------------------------------------- footprints
def convert_footprint(text, name=None, source="", notes=None):
    """.kicad_mod text -> altium_lib footprint dict (mm, y up), no 3D body yet."""
    notes = [] if notes is None else notes
    t = sexpr.parse(text)
    descr = (sexpr.first(t, "descr") or ["", ""])[1]
    fp = {"name": name or t[1], "description": AL.clean("; ".join(x for x in (descr, f"from {source} (CC-BY-SA 4.0)" if source else "") if x)),
          "height_mm": 0.0, "pads": [], "tracks": [], "fills": [], "regions": [], "arcs": [], "bodies": []}
    for p in sexpr.find(t, "pad"):
        _pad(fp, p, notes)
    skipped = {}
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexpr.find(t, kind):
            ln = (sexpr.first(g, "layer") or [None, None])[1]
            layer = LAYERS.get(ln)
            if layer is None:
                skipped[ln] = skipped.get(ln, 0) + 1
                continue
            _graphic(fp, kind, g, layer)
    if skipped:
        notes.append("graphics on unmapped layers dropped: " + ", ".join(f"{k} x{v}" for k, v in sorted(skipped.items(), key=str)))
    for z in sexpr.find(t, "zone"):           # rule areas -> Altium keep-out region (all objects, all layers)
        ko = sexpr.first(z, "keepout")
        rules = {r[0]: r[1] for r in (ko or [])[1:] if isinstance(r, list)}
        if not ko or "not_allowed" not in rules.values():
            notes.append("zone without keep-out rules not converted"); continue
        fp["regions"].append((_pts(sexpr.first(sexpr.first(z, "polygon"), "pts")), AL.KEEPOUT, 31))
        notes.append("keep-out zone -> Keep-Out layer region (KiCad rules: " + ", ".join(f"{k} {v}" for k, v in rules.items()) + ")")
    for k in ("fp_text", "fp_text_box", "dimension", "image"):
        n = len(list(sexpr.find(t, k)))
        if n:
            notes.append(f"{n} {k} not converted")
    return fp, t


def _pad(fp, p, notes):
    num, kind, shape = p[1], p[2], p[3]
    at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
    x, y = _xy(at)
    rot = float(at[3]) % 360 if len(at) > 3 else 0.0
    w, h = _f(size), _f(size, 2)
    layers = (sexpr.first(p, "layers") or ["layers"])[1:]
    if shape == "custom":                     # bounding rectangle of anchor + primitives, in the pad frame
        xs, ys = [-w / 2, w / 2], [-h / 2, h / 2]
        for g in (sexpr.first(p, "primitives") or [])[1:]:
            hw = _width(g) / 2
            pts = []
            if g[0] in ("gr_poly", "gr_line", "gr_rect", "gr_arc", "gr_curve"):
                pts = _pts(sexpr.first(g, "pts"), lambda c: (float(c[1]), float(c[2]))) if sexpr.first(g, "pts") else \
                    [(float(c[1]), float(c[2])) for c in (sexpr.first(g, k) for k in ("start", "mid", "end")) if c]
            elif g[0] == "gr_circle":
                (cx, cy), (ex, ey) = [(float(c[1]), float(c[2])) for c in (sexpr.first(g, "center"), sexpr.first(g, "end"))]
                r = math.hypot(ex - cx, ey - cy)
                pts = [(cx - r, cy - r), (cx + r, cy + r)]
            xs += [px + s * hw for px, _ in pts for s in (-1, 1)]; ys += [py + s * hw for _, py in pts for s in (-1, 1)]
        ox, oy = (min(xs) + max(xs)) / 2, -(min(ys) + max(ys)) / 2
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        dx, dy = _rot(ox, oy, rot)
        x, y, shape = x + dx, y + dy, "rect"
        _note(notes, f"pad {num}: custom shape approximated by its bounding rectangle {w:.3f}x{h:.3f} mm")
    elif shape == "trapezoid":
        shape = "rect"; _note(notes, f"pad {num}: trapezoid written as rectangle")
    if sexpr.first(p, "chamfer_ratio") and sexpr.first(p, "chamfer"):
        _note(notes, f"pad {num}: chamfered corners written as {shape}")
    rr = sexpr.first(p, "roundrect_rratio")
    pad = {"designator": num, "x": x, "y": y, "w": w, "h": h, "rot": rot, "shape": {"oval": "round", "circle": "round"}.get(shape, shape),
           "corner": min(100.0, float(rr[1]) * 200) if rr and shape == "roundrect" else 0, "paste": None}
    if kind in ("thru_hole", "np_thru_hole"):
        d = sexpr.first(p, "drill")
        vals = [v for v in d[1:] if isinstance(v, str) and v != "oval"]
        dw, dh = float(vals[0]), float(vals[1] if len(vals) > 1 else vals[0])
        if sexpr.first(d, "offset"):
            _note(notes, f"pad {num}: drill offset ignored")
        pad.update(layer=AL.MULTI, plated=kind == "thru_hole", hole=min(dw, dh))
        if kind == "np_thru_hole":            # no copper ring on an NPTH: pad = hole
            pad.update(w=dw, h=dh, shape="round", corner=0)
        if abs(dw - dh) > 1e-9:
            pad.update(hole_shape="slot", slot=max(dw, dh), hole_rot=0.0 if dw > dh else 90.0)
            if rot % 90 == 0:                 # bake 90-degree steps in so pad and hole rotation are both axis-aligned
                if rot % 180:
                    pad.update(w=pad["h"], h=pad["w"], hole_rot=90.0 - pad["hole_rot"])
                pad["rot"] = 0.0
            else:
                _note(notes, f"pad {num}: slot on a {rot:g} deg pad; hole rotation assumed relative to the pad")
        if not {"B.Mask", "*.Mask", "F&B.Mask"} & set(layers):
            _note(notes, f"pad {num}: mask opening only on {[l for l in layers if 'Mask' in l]} in KiCad, both sides in Altium")
        fp["pads"].append(pad)
        return
    cu = [l for l in layers if l.endswith(".Cu")]
    if not cu:                                # paste / mask aperture without copper
        layer = AL.TOP_PASTE if "F.Paste" in layers else AL.TOP_SOLDER if "F.Mask" in layers else None
        if layer is None:
            _note(notes, f"pad {num}: no copper, paste or mask on {layers}; dropped"); return
        if rot % 180 == 0 and layer == AL.TOP_PASTE:
            fp["fills"].append((x - w / 2, y - h / 2, x + w / 2, y + h / 2, layer))
        else:
            fp["regions"].append((_rect_pts(x, y, w, h, rot), layer))
        return
    pad["layer"] = AL.TOP if "F.Cu" in cu else AL.BOTTOM
    if pad["layer"] == AL.BOTTOM:
        _note(notes, f"pad {num}: bottom-side SMD pad")
    paste = "F.Paste" in layers or "B.Paste" in layers
    pm = sexpr.first(p, "solder_paste_margin")
    pad["paste"] = float(pm[1]) if paste and pm else None if paste else "none"
    fp["pads"].append(pad)


def _graphic(fp, kind, g, layer):
    w = _width(g)
    if kind == "fp_circle":
        (cx, cy), (ex, ey) = _xy(sexpr.first(g, "center")), _xy(sexpr.first(g, "end"))
        r = math.hypot(ex - cx, ey - cy)
        if _filled(g):                        # filled disc = arc of radius R/2 and width R
            ro = r + w / 2
            fp["arcs"].append((cx, cy, ro / 2, 0.0, 360.0, ro, layer))
        else:
            fp["arcs"].append((cx, cy, r, 0.0, 360.0, w, layer))
        return
    if kind == "fp_arc":
        if sexpr.first(g, "mid"):
            cx, cy, r, a0, a1 = _arc3(*(_xy(sexpr.first(g, k)) for k in ("start", "mid", "end")))
        else:                                 # KiCad 5: (start centre) (end arc start) (angle deg, clockwise on screen)
            (cx, cy), (sx, sy) = _xy(sexpr.first(g, "start")), _xy(sexpr.first(g, "end"))
            r, a = math.hypot(sx - cx, sy - cy), math.degrees(math.atan2(sy - cy, sx - cx))
            ang = float(sexpr.first(g, "angle")[1])
            a0, a1 = (a - ang, a) if ang > 0 else (a, a - ang)
        fp["arcs"].append((cx, cy, r, a0 % 360, a1 % 360 or 360.0, w, layer))
        return
    if kind == "fp_poly":
        pts = _pts(sexpr.first(g, "pts"))
    else:
        (x1, y1), (x2, y2) = _xy(sexpr.first(g, "start")), _xy(sexpr.first(g, "end"))
        pts = [(x1, y1), (x2, y2)] if kind == "fp_line" else [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    if kind != "fp_line" and _filled(g):
        fp["regions"].append((pts, layer))
        if w:                                 # stroke around the filled shape
            fp["tracks"] += [(a[0], a[1], b[0], b[1], w, layer) for a, b in zip(pts, pts[1:] + pts[:1])]
    else:
        segs = zip(pts, pts[1:] + pts[:1]) if kind != "fp_line" else [pts]
        fp["tracks"] += [(a[0], a[1], b[0], b[1], w or 0.1, layer) for a, b in segs]


# ---------------------------------------------------------------- 3D
STEP_PT = re.compile(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-+0-9.Ee]+)\s*,\s*([-+0-9.Ee]+)\s*,\s*([-+0-9.Ee]+)\s*\)")


def model_info(t):
    """First (model ...) of a footprint -> dict(path, offset mm, scale, rotate deg) or None."""
    m = sexpr.first(t, "model")
    if not m:
        return None
    xyz = lambda k, d: [float(v) for v in (sexpr.first(sexpr.first(m, k) or [], "xyz") or ["xyz", *d])[1:4]]
    off = xyz("offset", (0, 0, 0))
    if not sexpr.first(m, "offset") and sexpr.first(m, "at"):        # KiCad 5: (at (xyz)) in inches
        off = [v * 25.4 for v in xyz("at", (0, 0, 0))]
    return {"path": m[1], "offset": off, "scale": xyz("scale", (1, 1, 1)), "rotate": xyz("rotate", (0, 0, 0))}


def model_url_path(path):
    """'${KICAD9_3DMODEL_DIR}/Lib.3dshapes/X.wrl' -> 'Lib.3dshapes/X.step' in kicad-packages3D."""
    m = re.search(r"([^/\\]+\.3dshapes)[/\\]([^/\\]+?)(?:\.wrl|\.step|\.stp)?$", path, re.I)
    return f"{m.group(1)}/{m.group(2)}.step" if m else None


def step_extent(text, mi):
    """Bounding box (xmin, ymin, zmin, xmax, ymax, zmax) in mm of a STEP file's points after KiCad's model transform
    (scale, then rotate z, y, x by the negated angles, then offset)."""
    unit = 25.4 if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text) else 1.0
    (sx, sy, sz), (rx, ry, rz), off = mi["scale"], mi["rotate"], mi["offset"]
    def tf(x, y, z):
        x, y, z = x * sx, y * sy, z * sz
        y, z = _rot(y, z, -rx); z, x = _rot(z, x, -ry); x, y = _rot(x, y, -rz)
        return x + off[0], y + off[1], z + off[2]
    pts = [tf(*(float(v) * unit for v in m.groups())) for m in STEP_PT.finditer(text)]
    if not pts:
        return None
    return tuple(min(p[i] for p in pts) for i in range(3)) + tuple(max(p[i] for p in pts) for i in range(3))


def add_body(fp, t, http=None, height=None, step=False, notes=None):
    """Extruded body over the fab outline (else courtyard, else pads), or the embedded STEP model with step=True."""
    notes = [] if notes is None else notes
    mi, text, src = model_info(t), None, None
    if mi and http is not None and model_url_path(mi["path"]):
        text = fetch(http, "kicad-packages3D", model_url_path(mi["path"]))
        if text is None:
            notes.append(f"3D model {model_url_path(mi['path'])} not in kicad-packages3D")
    ext = step_extent(text, mi) if text else None
    if ext:
        h, src = round(ext[5], 3), "KiCad STEP model"
    else:
        h = next((v for rx, v in DEFAULT_HEIGHT if re.search(rx, t[1])), None)
        src = "default table" if h else None
        if h is None and height:
            h, src = height, "caller"
    if not h:
        raise ValueError(f"{t[1]}: no 3D height (no KiCad model, no DEFAULT_HEIGHT entry); pass --height")
    if step and ext and mi["rotate"] == [0, 0, 0] and mi["offset"] == [0, 0, 0] and mi["scale"] == [1, 1, 1]:
        x0, y0, _, x1, y1, _ = ext
        fp["bodies"].append({"pts": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "height": h,
                             "step": {"name": Path(model_url_path(mi["path"])).name, "data": text.encode("utf-8"), "rot": (0.0, 0.0, 0.0)}})
        notes.append(f"3D: embedded STEP {Path(model_url_path(mi['path'])).name}, height {h} mm")
    else:
        if step:
            notes.append("3D: STEP not embedded (" + ("model has an offset/rotation/scale" if ext else "no STEP model") + "), extruded body instead")
        outline = []
        for layer in (AL.MECH13, AL.MECH15):
            outline = [(x, y) for x1, y1, x2, y2, _, l in fp["tracks"] if l == layer for x, y in ((x1, y1), (x2, y2))]
            outline += [pt for pts, l, *_ in fp["regions"] if l == layer for pt in pts]
            outline += [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
                        for cx, cy, r, a0, a1, _, l in fp["arcs"] if l == layer for a in (a0, a1, (a0 + ((a1 - a0) % 360 or 360) / 2))]
            if len(outline) >= 3:
                kind = "fab" if layer == AL.MECH13 else "courtyard"
                break
        else:
            outline, kind = [pt for p in fp["pads"] for pt in _rect_pts(p["x"], p["y"], p["w"], p["h"], p["rot"])], "pads"
        fp["bodies"].append({"pts": _hull(outline), "height": h})
        notes.append(f"3D: extruded body over the {kind} outline, height {h} mm ({src})")
    fp["height_mm"] = h
    return fp


# ---------------------------------------------------------------- symbols
def _u(v):                                    # mm -> schematic units (10 mil)
    return round(float(v) / 0.254, 5)


def _lw(w):
    return 1 if w <= 0.3 else 2 if w <= 0.6 else 3


def _pin_text(s):                             # KiCad '~{RESET}' overbar -> Altium 'R\E\S\E\T\'
    return "" if s == "~" else re.sub(r"~\{([^}]*)\}", lambda m: "".join(c + "\\" for c in m.group(1)), s)


def _props(chain):
    out = {}
    for s in reversed(chain):                 # parent first, derived symbols override
        for p in sexpr.find(s, "property"):
            out[p[1]] = p[2]
    return out


def convert_symbol(chain, name=None, footprint=None, extra=(), pin_map=None, notes=None):
    """[symbol, parent, ...] -> altium_lib symbol dict. Units become Altium parts; De Morgan bodies are dropped."""
    notes = [] if notes is None else notes
    base, props = chain[-1], _props(chain)
    hide_names = _hidden(sexpr.first(base, "pin_names") or [])
    hide_nums = _hidden(sexpr.first(base, "pin_numbers") or [])
    units = {}
    for sub in sexpr.find(base, "symbol"):
        u, style = (int(v) for v in sub[1].rsplit("_", 2)[1:])
        if style > 1:
            _note(notes, f"unit {u} body style {style} (De Morgan) dropped"); continue
        units.setdefault(u, []).extend(sub[2:])
    n = max([u for u in units if u > 0] or [1])
    prims, parts, pins_at = [], [], {}
    pin_nodes = [(u, it) for u, items in units.items() for it in items if isinstance(it, list) and it[0] == "pin"]
    for u, it in pin_nodes:
        if not _hidden(it):
            at = sexpr.first(it, "at"); pins_at.setdefault((u, at[1], at[2]), True)
    for u in sorted(units):
        for it in units[u]:
            if not isinstance(it, list):
                continue
            for part in (range(1, n + 1) if u == 0 else [u]):
                pr = _sym_prim(it, hide_names, hide_nums, pins_at, u, pin_map or {}, notes)
                if pr:
                    prims.append(pr); parts.append(part)
    if n > 1:
        notes.append(f"{n} units written as {n} Altium parts")
    xs = [v for pr in prims if pr[0] != "pin" for v in _prim_xs(pr)[0]] + [pr[3] for pr in prims if pr[0] == "pin"]
    ys = [v for pr in prims if pr[0] != "pin" for v in _prim_xs(pr)[1]] + [pr[4] for pr in prims if pr[0] == "pin"]
    left, top, bot = min(xs or [0]), max(ys or [0]), min(ys or [0])
    ref = props.get("Reference", "U")
    keep = [("KiCad Footprint" if k == "Footprint" else k, props.get(k, "")) for k in ("Value", "Footprint", "Datasheet", "Description", "MPN")]
    params = [(k, v) for k, v in keep + list(extra) if v and v != "~"]
    params = list({k: (k, v) for k, v in params}.values())         # extra overrides
    return {"name": name or chain[0][1], "description": props.get("Description", ""), "designator": ref if ref.endswith("?") else ref + "?",
            "comment": "*", "footprint": footprint or (props.get("Footprint", "").split(":")[-1] or None), "prims": prims,
            "prim_parts": parts, "parts": n, "params": params, "label_at": ((left, top + 3), (left, bot - 8))}


def _prim_xs(pr):
    k = pr[0]
    if k == "rect":
        return [pr[1], pr[3]], [pr[2], pr[4]]
    if k in ("polyline", "polygon"):
        return [p[0] for p in pr[1]], [p[1] for p in pr[1]]
    if k in ("arc", "circle"):
        return [pr[1] - pr[3], pr[1] + pr[3]], [pr[2] - pr[3], pr[2] + pr[3]]
    return [pr[1]], [pr[2]]


def _sym_prim(it, hide_names, hide_nums, pins_at, unit, pin_map, notes):
    k = it[0]
    fill = (sexpr.first(sexpr.first(it, "fill") or [], "type") or [None, "none"])[1]
    fill = {"background": "body", "outline": "line", "color": "body"}.get(fill)
    lw = _lw(_width(it))
    pt = lambda node: (_u(node[1]), _u(node[2]))
    if k == "rectangle":
        (x1, y1), (x2, y2) = pt(sexpr.first(it, "start")), pt(sexpr.first(it, "end"))
        return ("rect", x1, y1, x2, y2, lw, fill)
    if k in ("polyline", "bezier"):
        pts = _pts(sexpr.first(it, "pts"), pt)
        if k == "bezier":
            _note(notes, "bezier drawn through its control points")
        closed = len(pts) > 2 and pts[0] == pts[-1]
        return ("polygon", pts[:-1] if closed else pts, lw, fill) if fill and len(pts) > 2 else ("polyline", pts, lw)
    if k == "circle":
        (cx, cy), r = pt(sexpr.first(it, "center")), _u(sexpr.first(it, "radius")[1])
        return ("circle", cx, cy, r, lw, fill)
    if k == "arc":
        if sexpr.first(it, "mid"):
            cx, cy, r, a0, a1 = _arc3(*(pt(sexpr.first(it, q)) for q in ("start", "mid", "end")))
        else:                                 # KiCad 6: (radius (at) (length) (angles a0 a1))
            rad = sexpr.first(it, "radius")
            (cx, cy), r = pt(sexpr.first(rad, "at")), _u(sexpr.first(rad, "length")[1])
            a0, a1 = (float(v) for v in sexpr.first(rad, "angles")[1:3])
        return ("arc", round(cx, 5), round(cy, 5), round(r, 5), round(a0 % 360, 3), round(a1 % 360 or 360, 3), lw)
    if k == "text":
        at = sexpr.first(it, "at")
        return ("text", _u(at[1]), _u(at[2]), it[1], int(float(at[3]) // 90) % 4 if len(at) > 3 else 0)
    if k == "pin":
        etype, at = it[1], sexpr.first(it, "at")
        x, y, a = float(at[1]), float(at[2]), int(float(at[3])) % 360 if len(at) > 3 else 0
        length = float((sexpr.first(it, "length") or [0, 2.54])[1])
        bx, by = x + length * math.cos(math.radians(a)), y + length * math.sin(math.radians(a))   # body end
        num = (sexpr.first(it, "number") or ["", ""])[1]
        name = _pin_text((sexpr.first(it, "name") or ["", ""])[1])
        hidden, p = _hidden(it), {"num": pin_map.get(num, num), "name": name, "type": KTYPE.get(etype, "passive")}
        if hidden and (unit, at[1], at[2]) in pins_at:      # stacked on a visible pin: keep visible (Altium joins pins at one point)
            p.update(show_name=False, show_num=False)
        elif hidden:
            p["hidden"] = True
            if etype == "power_in":
                p["net"] = name
        else:
            p.update(show_name=not hide_names and bool(name), show_num=not hide_nums)
        gx, gy = round(_u(bx)), round(_u(by))
        if abs(gx - _u(bx)) > 0.01 or abs(gy - _u(by)) > 0.01:
            _note(notes, f"pin {num}: off the 10 mil grid, rounded")
        if a not in SIDE:
            raise ValueError(f"pin {num}: angle {a} not a multiple of 90")
        return ("pin", p, SIDE[(a + 180) % 360], gx, gy, round(_u(length)))
    if k not in ("property", "text_box"):
        _note(notes, f"symbol item {k} not converted")
    return None


# ---------------------------------------------------------------- checks & driver
def pin_pad_check(sym, fp):
    """Pin numbers without a pad and numbered pads without a pin (unnamed mechanical pads are ignored)."""
    pins = {pr[1]["num"] for pr in sym["prims"] if pr[0] == "pin"}
    pads = {p["designator"] for p in fp["pads"] if p["designator"]}
    return sorted(pins - pads), sorted(pads - pins)


def import_part(spec, out, http, step=False, write=True):
    """spec: {footprint, symbol, mpn?, footprint_as?, symbol_as?, height_mm?, pin_map?, note?} -> report dict."""
    rep = {"mpn": spec.get("mpn", ""), "footprint": spec["footprint"], "symbol": spec.get("symbol"), "notes": [], "ok": False}
    try:
        text, src = load_footprint(spec["footprint"], http)
        fp, t = convert_footprint(text, spec.get("footprint_as"), src, rep["notes"])
        add_body(fp, t, http, spec.get("height_mm"), step, rep["notes"])
        rep["pcblib"] = str(AL.write_pcblib(Path(out) / "PcbLib" / f"{fp['name']}.PcbLib", [fp])) if write else None
        rep["pads"] = len(fp["pads"])
        if spec.get("symbol"):
            chain, ssrc = load_symbol(spec["symbol"], http)
            extra = [("MPN", spec["mpn"])] if spec.get("mpn") else []
            sym = convert_symbol(chain, spec.get("symbol_as") or spec.get("mpn"), fp["name"], extra, spec.get("pin_map"), rep["notes"])
            sym["description"] = sym["description"] or ssrc
            rep["schlib"] = str(AL.write_schlib(Path(out) / "SchLib" / f"{sym['name']}.SchLib", [sym])) if write else None
            rep["pins"] = sum(pr[0] == "pin" for pr in sym["prims"])
            if len(chain) > 1:
                rep["notes"].append("symbol extends " + " -> ".join(s[1] for s in chain[1:]))
            nopad, nopin = pin_pad_check(sym, fp)
            if nopad:
                raise ValueError(f"symbol pins without a footprint pad: {nopad}")
            if nopin:
                rep["notes"].append(f"pads without a symbol pin: {nopin}")
        if spec.get("note"):
            rep["notes"].append(spec["note"])
        rep["ok"] = True
    except Exception as e:                    # report per part, keep going
        rep["error"] = f"{type(e).__name__}: {e}"
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert KiCad library footprints/symbols to Altium PcbLib/SchLib")
    ap.add_argument("-o", "--out", default="out/altium_models_kicad")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="only use the HTTP cache")
    ap.add_argument("--step", action="store_true", help="embed the KiCad STEP model instead of an extruded body")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="convert the parts listed in a JSON file")
    b.add_argument("parts")
    f = sub.add_parser("footprint", help="one footprint: Lib:Name or path.kicad_mod")
    f.add_argument("ref"); f.add_argument("--as", dest="as_"); f.add_argument("--height", type=float, help="fallback height, mm")
    s = sub.add_parser("symbol", help="one symbol: Lib:Name or path.kicad_sym[:Name]")
    s.add_argument("ref"); s.add_argument("--as", dest="as_"); s.add_argument("--footprint", help="PcbLib footprint name to link")
    s.add_argument("--param", action="append", default=[], help="extra parameter K=V")
    a = ap.parse_args(argv)
    http = CachedHTTP(a.cache, offline=a.offline, timeout=60)
    out = Path(a.out)
    if a.cmd == "symbol":
        notes = []
        chain, src = load_symbol(a.ref, http)
        sym = convert_symbol(chain, a.as_, a.footprint, [tuple(kv.split("=", 1)) for kv in a.param], None, notes)
        (out / "SchLib").mkdir(parents=True, exist_ok=True)
        path = AL.write_schlib(out / "SchLib" / f"{sym['name']}.SchLib", [sym])
        print(f"{sym['name']}: {sum(pr[0] == 'pin' for pr in sym['prims'])} pins -> {path}" + "".join(f"\n   {n}" for n in notes))
        return
    specs = json.loads(Path(a.parts).read_text()) if a.cmd == "batch" else [{"footprint": a.ref, "footprint_as": a.as_, "height_mm": a.height}]
    (out / "PcbLib").mkdir(parents=True, exist_ok=True); (out / "SchLib").mkdir(parents=True, exist_ok=True)
    reps = [import_part(sp, out, http, a.step) for sp in specs]
    for r in reps:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['mpn'] or r['footprint']:<26} {r.get('pads', '-'):>3} pads {r.get('pins', '-'):>3} pins  "
              f"{Path(r.get('pcblib') or '-').name} {Path(r.get('schlib') or '-').name}")
        for n in r["notes"] + ([r["error"]] if r.get("error") else []):
            print(f"     {n}")
    if a.cmd == "batch":
        (out / "report.json").write_text(json.dumps(reps, indent=2))
    if not all(r["ok"] for r in reps):
        sys.exit(f"{sum(not r['ok'] for r in reps)} of {len(reps)} failed")


if __name__ == "__main__":
    main()
