"""Official KiCad library footprints and symbols -> Altium .PcbLib / .SchLib (with 3D bodies).

    python3 -m ecadlib.kicad_import batch examples/kicad_import_parts.json -o out/altium_models_kicad [--step] [--box]
    python3 -m ecadlib.kicad_import footprint Package_TO_SOT_SMD:SOT-23 [--as NAME] [--height MM] [--step] [--box] -o DIR
    python3 -m ecadlib.kicad_import symbol Transistor_FET:AO3401A [--as NAME] [--footprint REF] [--param K=V] -o DIR

A <Lib>:<Name> reference is fetched from gitlab.com/kicad/libraries (kicad-footprints, kicad-symbols with
KiCad 9+ per-symbol <Lib>.kicad_symdir/<Name>.kicad_sym files or a KiCad 7/8 <Lib>.kicad_sym, kicad-packages3D)
through httpcache.CachedHTTP. A local path works too: X.kicad_mod, Lib.kicad_sym:Name or X.kicad_sym.
Output: <out>/PcbLib/<name>.PcbLib and <out>/SchLib/<name>.SchLib, the layout `altium365 plan --models` reads.

Footprints: SMD, plated and non-plated through-hole pads (round and slotted drills), rect / roundrect / oval /
circle pads, custom pads as an anchor pad plus copper / mask / paste regions (curved primitives: bounding
rectangle), trapezoids as rectangles, per-pad mask margins and tented pads, pad rotation, fp_line / fp_rect /
fp_poly / fp_circle / fp_arc on silkscreen, fab (Mechanical 13), courtyard (Mechanical 15), F.Cu, F.Mask, F.Paste.
Reference / Value text become .Designator (Top Overlay from silk, Mechanical 13 from fab) / .Comment (Mechanical 13).
Not converted (listed in the notes): user text, zones other than keep-outs, drill offsets.
3D: an extruded ComponentBody on Mechanical 13 over the fab outline (else the courtyard), its height from the
KiCad STEP model (max z after the model transform), else DEFAULT_HEIGHT, else --height. --step embeds the STEP
model itself (Library/Models, AltiumSharp layout and checksum) when the KiCad model has no offset or rotation.
Where no KiCad STEP is embedded and there is no KiCad model (or --step could not embed it), a package-shaped
STEP from step_gen is embedded instead of the box when the package family is known; --box keeps the box.
Symbols: pins (number, name, type, orientation, length, hidden, overbar names), rectangles, polylines, circles,
arcs, text, pin shapes (inverted, clock, active-low ...) as Altium pin symbols, multi-unit symbols as Altium
multi-part, De Morgan body styles as Altium display modes, `extends` chains; Value/Footprint/Datasheet/Description/MPN
as parameters.

KiCad's libraries are CC-BY-SA 4.0 with a design-use exception: converted files are not committed (.gitignore)."""
import argparse, json, math, re, sys
from pathlib import Path

from . import altium_lib as AL, sexpr
from .httpcache import CachedHTTP

# KiCad's library terms ask for attribution; only parts converted from those libraries carry them (not your own files,
# EasyEDA parts or footprints ecadlib generates)
KICAD_LICENSE = "CC-BY-SA 4.0"
KICAD_CREDIT = ("From the KiCad libraries (https://gitlab.com/kicad/libraries), CC-BY-SA 4.0 with the KiCad libraries' "
                "design-use exception")
GITLAB = "https://gitlab.com/kicad/libraries/{repo}/-/raw/master/{path}"
LAYERS = {"F.SilkS": AL.TOP_OVERLAY, "B.SilkS": AL.BOTTOM_OVERLAY, "F.Fab": AL.MECH13, "F.CrtYd": AL.MECH15,
          "F.Cu": AL.TOP, "F.Mask": AL.TOP_SOLDER, "F.Paste": AL.TOP_PASTE}
# overall height (mm) when the KiCad model gives none: first regex matching the footprint name wins
DEFAULT_HEIGHT = [(r"^SOT-23", 1.2), (r"^SOT-223", 1.8), (r"^(LQFP|TQFP)", 1.6), (r"^(SOIC|SOP)", 1.75), (r"^(TSSOP|MSOP)", 1.2),
                  (r"QFN|DFN|WSON|SON", 0.8), (r"_0402_", 0.6), (r"_0603_", 0.9), (r"_0805_", 1.3), (r"_1206_", 1.6),
                  (r"^Crystal_SMD_3225", 1.0), (r"^Crystal_SMD_2016", 0.8), (r"^USB_C_Receptacle", 3.3), (r"^JST_SH_", 2.95),
                  (r"^PinHeader_.*P1.27mm.*SMD", 4.3), (r"^ESP32-S3-WROOM", 3.25)]
# package default heights (mm) for standard chip sizes when nothing better is known (no KiCad model, no datasheet): chip
# resistors at their nominal thickness (thick film, as step_gen), MLCCs at a conservative maximum for the size (a 0603
# MLCC is 0.8 mm nominal, up to 0.9 mm). Used with the label 'package default, verify': a warning, not a hold.
CHIP_HEIGHT = {"R": {"0201": 0.26, "0402": 0.35, "0603": 0.45, "0805": 0.5, "1206": 0.55, "1210": 0.55, "2010": 0.6, "2512": 0.6},
               "C": {"0201": 0.33, "0402": 0.55, "0603": 0.9, "0805": 1.35, "1206": 1.8, "1210": 2.7}}
PKG_HEIGHT = [(r"(?i)\bSOT-?23(?![0-9])", 1.2, "SOT-23"), (r"(?i)\bSOT-?23-?[56]\b", 1.2, "SOT-23-5/6"), (r"(?i)\bSOD-?123\b", 1.35, "SOD-123"),
              (r"(?i)\bSOD-?323\b", 1.1, "SOD-323"), (r"(?i)\bSOT-?323\b", 1.1, "SOT-323")]
CHIP_RE = re.compile(r"(?<![0-9])(0201|0402|0603|0805|1206|1210|2010|2512)(?![0-9])")


def package_default_height(names, kind=None):
    """(height mm, label) for a standard package named in any of names (footprint name, KiCad ref, description ...),
    or None. kind: 'R' / 'C' (CP counts as C) when the part's kind is known; else read from the names (R_ / RES / C_ /
    CAP / resistor / capacitor)."""
    text = " ".join(str(n) for n in names if n)
    k = {"CP": "C"}.get(kind, kind)
    if k not in CHIP_HEIGHT:
        k = ("R" if re.search(r"(?i)(^|[\s:/_])(R|RES|RESC)[_ ]|resistor", text) else
             "C" if re.search(r"(?i)(^|[\s:/_])(C|CAP|CAPC)[_ ]|capacitor|MLCC", text) else None)
    m = CHIP_RE.search(text)
    if k and m and m.group(1) in CHIP_HEIGHT[k]:
        return CHIP_HEIGHT[k][m.group(1)], f"{m.group(1)} {'resistor' if k == 'R' else 'capacitor'}"
    for rx, h, label in PKG_HEIGHT:
        if re.search(rx, text):
            return h, label
    return None


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
        return Path(ref).read_text(encoding="utf-8"), Path(ref).name
    lib, name = ref.split(":", 1)
    text = fetch(http, "kicad-footprints", f"{lib}.pretty/{name}.kicad_mod")
    if text is None:
        raise FileNotFoundError(f"KiCad footprint {ref} not found")
    return text, f"KiCad {ref}"


def load_symbol(ref, http, keep_quotes=False):
    """'Lib:Name', 'path/Lib.kicad_sym:Name' or 'path/Name.kicad_sym' -> [symbol, parent, ...] (extends chain), label.
    keep_quotes: parse with sexpr's Q strings so the symbols can be written back (kicad_export)."""
    parse = lambda text: sexpr.parse(text, keep_quotes)
    m = re.match(r"(.+\.kicad_sym)(?::(.+))?$", ref)
    if m:                                     # local file; parents from the same file or sibling files (symdir)
        path = Path(m.group(1))
        def get(name):
            for f in (path, path.with_name(f"{name}.kicad_sym")):
                if f.exists():
                    s = _find_symbol(parse(f.read_text(encoding="utf-8")), name)
                    if s:
                        return s
            raise FileNotFoundError(f"symbol {name} not in {path}")
        name = m.group(2) or [s[1] for s in sexpr.find(sexpr.parse(path.read_text(encoding="utf-8")), "symbol")][0]
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
            s = _find_symbol(parse(text), name) if text else None
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
MALFORMED = (IndexError, TypeError, AttributeError, KeyError, OverflowError, ZeroDivisionError)


def convert_footprint(text, name=None, source="", notes=None, license_=""):
    """.kicad_mod text -> altium_lib footprint dict (mm, y up), no 3D body yet. Raises ValueError for a file that
    does not parse or is missing what a footprint needs (a pad without a size, a line without an end ...).
    license_: the source's license, added to the description after "from <source>" (KICAD_LICENSE for KiCad parts)."""
    try:
        return _convert_footprint(text, name, source, notes, license_)
    except MALFORMED as e:
        raise ValueError(f"malformed KiCad footprint {name or ''}: {type(e).__name__}: {e}".replace("  ", " ")) from e


def _convert_footprint(text, name=None, source="", notes=None, license_=""):
    notes = [] if notes is None else notes
    t = sexpr.parse(text)
    descr = (sexpr.first(t, "descr") or ["", ""])[1]
    origin = f"from {source}" + (f" ({license_})" if license_ else "") if source else ""
    fp = {"name": name or t[1], "description": AL.clean("; ".join(x for x in (descr, origin) if x)),
          "height_mm": 0.0, "pads": [], "tracks": [], "fills": [], "regions": [], "arcs": [], "bodies": [], "texts": []}
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
            _graphic(fp, kind, g, layer, notes)
    if skipped:
        notes.append("graphics on unmapped layers dropped: " + ", ".join(f"{k} x{v}" for k, v in sorted(skipped.items(), key=str)))
    _zones(fp, t, notes)
    fp["texts"] = AL.texts_from_kicad(t)
    if fp["texts"]:
        des = next((t[6] for t in fp["texts"] if t[0] == ".Designator"), None)
        notes.append(f"Reference -> .Designator ({'Top Overlay' if des == AL.TOP_OVERLAY else 'Mechanical 13'}), Value -> .Comment (Mechanical 13)"
                     + (", ${REFERENCE} on F.Fab -> .Designator (Mechanical 13)" if any(AL._fab_ref(g) for g in sexpr.find(t, "fp_text")) else ""))
    for k in ("fp_text", "fp_text_box", "dimension", "image"):
        n = len([g for g in sexpr.find(t, k) if k != "fp_text" or g[1] not in AL.KTEXT and not AL._fab_ref(g)])
        if n:
            notes.append(f"{n} {'user ' if k == 'fp_text' else ''}{k} not converted")
    return fp, t


def _offset(pts, d):
    """Polygon grown by d (mm; negative shrinks), mitred corners. Fine for the small mask / paste margins of pads."""
    if not d or len(pts) < 3:
        return list(pts)
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))
    s = 1 if area > 0 else -1                 # counter-clockwise: outward normal of edge (dx, dy) is (dy, -dx)
    out, n = [], len(pts)
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        nrm = []
        for a, b in ((p0, p1), (p1, p2)):
            L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
            nrm.append((s * (b[1] - a[1]) / L, -s * (b[0] - a[0]) / L))
        bx, by = nrm[0][0] + nrm[1][0], nrm[0][1] + nrm[1][1]
        k = (1 + nrm[0][0] * nrm[1][0] + nrm[0][1] * nrm[1][1]) or 1e-9
        out.append((p1[0] + d * bx / k, p1[1] + d * by / k))
    return out


def _custom_shapes(p, x, y, rot, edges=True):
    """Filled primitives of a KiCad custom pad -> ([polygons], [tracks (x1, y1, x2, y2, w)]) in footprint mm, y up;
    None when a primitive can't be drawn exactly (curves, arcs, unfilled outlines)."""
    def tf(px, py):                           # pad frame (KiCad, y down) -> footprint, y up
        dx, dy = _rot(px, -py, rot)
        return x + dx, y + dy
    polys, tracks = [], []
    for g in (sexpr.first(p, "primitives") or [])[1:]:
        w = _width(g)
        filled = _filled(g) or (sexpr.first(g, "fill") is None and g[0] == "gr_poly")
        if g[0] == "gr_line":
            a, b = (tf(float(c[1]), float(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end")))
            tracks.append((*a, *b, w)); continue
        if g[0] == "gr_poly" and filled and sexpr.first(g, "pts"):
            pts = [tf(*c) for c in _pts(sexpr.first(g, "pts"), lambda c: (float(c[1]), float(c[2])))]
        elif g[0] == "gr_rect" and filled:
            (x1, y1), (x2, y2) = [(float(c[1]), float(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
            pts = [tf(*c) for c in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]
        elif g[0] == "gr_circle" and filled:
            (cx, cy), (ex, ey) = [(float(c[1]), float(c[2])) for c in (sexpr.first(g, "center"), sexpr.first(g, "end"))]
            r = math.hypot(ex - cx, ey - cy)
            pts = [tf(cx + r * math.cos(k * math.pi / 18), cy + r * math.sin(k * math.pi / 18)) for k in range(36)]
        else:
            return None
        polys.append(_offset(pts, w / 2) if w else pts)
        if w and edges:                       # stroke around the outline: exact edges, mitred corners
            tracks += [(*a, *b, w) for a, b in zip(pts, pts[1:] + pts[:1])]
    return polys, tracks


def _mask_margin(p):
    m = sexpr.first(p, "solder_mask_margin")
    return float(m[1]) if m else None


def _custom_regions(fp, pad, shapes, kind, layers, p, notes=None):
    """Regions for a custom pad's shape: copper on the pad's copper layers, solder mask grown by the pad's mask margin
    (0 when KiCad has none, as the anchor pad's manual expansion), paste by its paste margin. Altium gives primitives
    that touch a pad in a footprint the pad's net on the board."""
    polys, tracks = shapes
    lay = set(layers)
    cu = [AL.TOP, AL.BOTTOM] if kind != "smd" and lay & {"*.Cu", "F&B.Cu"} else \
        [l for n, l in (("F.Cu", AL.TOP), ("B.Cu", AL.BOTTOM)) if n in lay]
    mask = [l for n, l in (("F", AL.TOP_SOLDER), ("B", AL.BOTTOM_SOLDER)) if lay & {f"{n}.Mask", "*.Mask", "F&B.Mask"}]
    paste = [l for n, l in (("F", AL.TOP_PASTE), ("B", AL.BOTTOM_PASTE)) if lay & {f"{n}.Paste", "*.Paste"}]
    mm_, pm = _mask_margin(p) or 0.0, sexpr.first(p, "solder_paste_margin")
    for layer in cu:
        fp["regions"] += [(pts, layer) for pts in polys]
        fp["tracks"] += [(*t, layer) for t in tracks]
    for layer in mask:
        fp["regions"] += [(_offset(pts, mm_), layer) for pts in polys]
    for layer in paste:
        fp["regions"] += [(_offset(pts, float(pm[1]) if pm else 0.0), layer) for pts in polys]
    pad["mask"] = mm_ if mask else "none"
    if notes is not None and kind != "smd" and len(cu) > 1:         # Altium regions have no "all layers" (Multi-Layer is for pads and vias only)
        _note(notes, f"pad {pad['designator']}: through-hole custom shape on top and bottom copper; inner layers get the anchor "
                     "pad with the hole (connected on every layer), not the full shape")
    if tracks and (mask or paste):
        fp["regions"] += [(_rect_pts((x1 + x2) / 2, (y1 + y2) / 2, math.hypot(x2 - x1, y2 - y1) + w, w,
                                     math.degrees(math.atan2(y2 - y1, x2 - x1))), layer)
                          for x1, y1, x2, y2, w in tracks for layer in mask + paste]


def _zones(fp, t, notes):
    """Rule areas -> Altium keep-out regions (all objects, all layers)."""
    for z in sexpr.find(t, "zone"):
        ko = sexpr.first(z, "keepout")
        rules = {r[0]: r[1] for r in (ko or [])[1:] if isinstance(r, list)}
        if not ko or "not_allowed" not in rules.values():
            notes.append("zone without keep-out rules not converted"); continue
        fp["regions"].append((_pts(sexpr.first(sexpr.first(z, "polygon"), "pts")), AL.KEEPOUT, 31))
        notes.append("keep-out zone -> Keep-Out layer region (KiCad rules: " + ", ".join(f"{k} {v}" for k, v in rules.items()) + ")")


def _pad(fp, p, notes):
    num, kind, shape = p[1], p[2], p[3]
    at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
    x, y = _xy(at)
    rot = float(at[3]) % 360 if len(at) > 3 else 0.0
    w, h = _f(size), _f(size, 2)
    layers = (sexpr.first(p, "layers") or ["layers"])[1:]
    window = kind in ("smd", "connect") and not any(l.endswith(".Cu") for l in layers)
    custom = _custom_shapes(p, x, y, rot, edges=not window) if shape == "custom" else None
    if custom and window:
        anchor = (sexpr.first(sexpr.first(p, "options") or [], "anchor") or [None, "circle"])[1]
        polys, tracks = custom                # a window with a custom shape: anchor + each primitive, on each layer
        shapes = [_round_pts(x, y, w, h, rot) if anchor != "rect" else _rect_pts(x, y, w, h, rot)] + polys + [
            _rect_pts((x1 + x2) / 2, (y1 + y2) / 2, math.hypot(x2 - x1, y2 - y1) + tw, tw, math.degrees(math.atan2(y2 - y1, x2 - x1)))
            for x1, y1, x2, y2, tw in tracks]
        wl = _window_layers(layers)
        fp["regions"] += [(pts, layer) for layer in wl for pts in shapes]
        if not wl:
            _note(notes, f"pad {num}: no copper, paste or mask on {layers}; dropped")
        return
    if custom:                                # anchor pad + its shape as regions on the pad's copper, mask and paste
        anchor = (sexpr.first(sexpr.first(p, "options") or [], "anchor") or [None, "circle"])[1]
        n0 = len(fp["pads"])
        _pad_core(fp, p, notes, num, kind, "rect" if anchor == "rect" else "circle", x, y, w, h, rot, layers)
        if len(fp["pads"]) > n0:
            _custom_regions(fp, fp["pads"][-1], custom, kind, layers, p, notes)
            _note(notes, f"pad {num}: custom shape written as a {anchor} anchor pad plus regions on its copper, mask and paste layers")
        return
    if shape == "custom":                     # curves / arcs: bounding rectangle of anchor + primitives, in the pad frame
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
        _note(notes, f"pad {num}: custom shape (curved primitives) approximated by its bounding rectangle {w:.3f}x{h:.3f} mm")
    elif shape == "trapezoid":
        shape = "rect"; _note(notes, f"pad {num}: trapezoid written as rectangle")
    _pad_core(fp, p, notes, num, kind, shape, x, y, w, h, rot, layers)


def _window_layers(layers):
    """Paste / mask layers of a pad without copper -> Altium layers."""
    return [l for n, l in (("F.Paste", AL.TOP_PASTE), ("F.Mask", AL.TOP_SOLDER), ("B.Paste", AL.BOTTOM_PASTE),
                           ("B.Mask", AL.BOTTOM_SOLDER)) if n in layers or "*." + n[2:] in layers or "F&B." + n[2:] in layers]


def _round_pts(cx, cy, w, h, deg=0.0, n=36):
    """A circle (w == h) or stadium (oval) outline as a polygon, footprint frame (y up)."""
    r = min(w, h) / 2
    half = (max(w, h) - 2 * r) / 2
    along = w >= h                           # the straight part runs along x (before rotation) when w > h
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        u, v = r * math.cos(a), r * math.sin(a)
        if along:
            u += half if u >= 0 else -half
        else:
            v += half if v >= 0 else -half
        pts.append((cx + _rot(u, v, deg)[0], cy + _rot(u, v, deg)[1]))
    return pts


def _pad_core(fp, p, notes, num, kind, shape, x, y, w, h, rot, layers):
    if sexpr.first(p, "chamfer_ratio") and sexpr.first(p, "chamfer"):
        _note(notes, f"pad {num}: chamfered corners written as {shape}")
    rr = sexpr.first(p, "roundrect_rratio")
    pad = {"designator": num, "x": x, "y": y, "w": w, "h": h, "rot": rot, "shape": {"oval": "round", "circle": "round"}.get(shape, shape),
           "corner": min(100.0, float(rr[1]) * 200) if rr and shape == "roundrect" else 0, "paste": None}
    if kind in ("thru_hole", "np_thru_hole"):
        d = sexpr.first(p, "drill")
        vals = [v for v in d[1:] if isinstance(v, str) and v != "oval"]
        dw, dh = float(vals[0]), float(vals[1] if len(vals) > 1 else vals[0])
        off = sexpr.first(d, "offset")
        ox, oy = (float(off[1]), float(off[2])) if off else (0.0, 0.0)
        pad.update(layer=AL.MULTI, plated=kind == "thru_hole", hole=min(dw, dh))
        if ox or oy:                          # copper shifted from the hole, in the pad frame: Altium's offset from
            pad["offset"] = (ox, -oy)         # hole centre (y up), as KiCad's Altium importer reads it back
        if kind == "np_thru_hole":            # no copper ring on an NPTH: pad = hole
            pad.update(w=dw, h=dh, shape="round", corner=0)
        if abs(dw - dh) > 1e-9:
            pad.update(hole_shape="slot", slot=max(dw, dh), hole_rot=0.0 if dw > dh else 90.0)
            if rot % 90 == 0:                 # bake 90-degree steps in so pad and hole rotation are both axis-aligned
                if rot % 180:
                    pad.update(w=pad["h"], h=pad["w"], hole_rot=90.0 - pad["hole_rot"])
                if pad.get("offset"):         # the offset turns with the pad (counter-clockwise, y up)
                    a, (u, v) = math.radians(rot), pad["offset"]
                    pad["offset"] = (u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a))
                pad["rot"] = 0.0
            else:
                _note(notes, f"pad {num}: slot on a {rot:g} deg pad; hole rotation assumed relative to the pad")
        masks = [l for l in layers if "Mask" in l]
        if not masks:                         # tented both sides (e.g. thermal vias): close the mask opening
            pad["mask"] = "none"
        elif not {"B.Mask", "*.Mask", "F&B.Mask"} & set(masks) or not {"F.Mask", "*.Mask", "F&B.Mask"} & set(masks):
            _note(notes, f"pad {num}: mask opening only on {masks} in KiCad, both sides in Altium")
        if masks and _mask_margin(p) is not None:
            pad["mask"] = _mask_margin(p)
        fp["pads"].append(pad)
        return
    cu = [l for l in layers if l.endswith(".Cu")]
    if not cu:                                # paste / mask aperture without copper: its shape on each of its layers
        wl = _window_layers(layers)
        if not wl:
            _note(notes, f"pad {num}: no copper, paste or mask on {layers}; dropped"); return
        for layer in wl:
            if shape in ("circle", "oval"):
                fp["regions"].append((_round_pts(x, y, w, h, rot), layer))
            elif rot % 180 == 0 and layer in (AL.TOP_PASTE, AL.BOTTOM_PASTE):
                fp["fills"].append((x - w / 2, y - h / 2, x + w / 2, y + h / 2, layer))
            else:
                fp["regions"].append((_rect_pts(x, y, w, h, rot), layer))
        return
    pad["layer"] = AL.TOP if "F.Cu" in cu else AL.BOTTOM
    off = sexpr.first(sexpr.first(p, "drill") or [], "offset")
    if off and (float(off[1]) or float(off[2])):          # SMD shape offset: KiCad draws the copper at (at) + offset
        dx, dy = _rot(float(off[1]), -float(off[2]), rot)
        pad.update(x=x + dx, y=y + dy)
    if pad["layer"] == AL.BOTTOM:
        _note(notes, f"pad {num}: bottom-side SMD pad")
    paste = "F.Paste" in layers or "B.Paste" in layers
    pm = sexpr.first(p, "solder_paste_margin")
    pad["paste"] = float(pm[1]) if paste and pm else None if paste else "none"
    if not any("Mask" in l for l in layers):
        pad["mask"] = "none"
    elif _mask_margin(p) is not None:
        pad["mask"] = _mask_margin(p)
    fp["pads"].append(pad)


def _graphic(fp, kind, g, layer, notes=None):
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
            s_, m_, e_ = (_xy(sexpr.first(g, k)) for k in ("start", "mid", "end"))
            try:
                cx, cy, r, a0, a1 = _arc3(s_, m_, e_)
            except ValueError:                # zero-length or straight "arc" (KiCad's own library has some): a line or nothing
                if s_ != e_:
                    fp["tracks"].append((*s_, *e_, w or 0.1, layer))
                if notes is not None:
                    _note(notes, "degenerate arc (start, mid and end in a line) written as a line or dropped")
                return
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


def add_body(fp, t, http=None, height=None, step=False, notes=None, gen_step=False, spec=None, height_src=None, kind=None, names=()):
    """3D body: the KiCad STEP model embedded (step=True, model without offset/rotation/scale), else with
    gen_step=True a package-shaped STEP from step_gen (spec dict, else package_specs / the footprint name) when
    no KiCad STEP is embedded (a downloaded one still sets the height), else an extruded body over the fab outline
    (else courtyard, else pads).
    height_src: the caller's height is part-specific and beats the default table (label for the notes).
    kind / names: the part's kind ('R', 'C') and other names (KiCad ref, description) for package_default_height, the
    last resort ('HEIGHT from package default ..., verify')."""
    notes = [] if notes is None else notes
    mi, text, src = model_info(t), None, None
    if mi and http is not None and model_url_path(mi["path"]):
        try:
            text = fetch(http, "kicad-packages3D", model_url_path(mi["path"]))
            if text is None:
                notes.append(f"3D model {model_url_path(mi['path'])} not in kicad-packages3D")
        except IOError as e:                    # blocked host / offline cache miss: the footprint is still good; the
            notes.append(f"3D model {model_url_path(mi['path'])} not downloaded ({e}); body made here instead")
    ext = step_extent(text, mi) if text else None
    if ext:
        h, src = round(ext[5], 3), "KiCad STEP model"
    elif height and height_src:
        h, src = height, height_src
    else:
        h = next((v for rx, v in DEFAULT_HEIGHT if re.search(rx, t[1])), None)
        src = "default table" if h else None
        if h is None and height:
            h, src = height, "caller"
        if h is None:
            pd = package_default_height([t[1], *names], kind)
            if pd:
                h, src = pd[0], f"HEIGHT from package default ({pd[1]}), verify"
    embeddable = bool(ext) and mi["rotate"] == [0, 0, 0] and mi["offset"] == [0, 0, 0] and mi["scale"] == [1, 1, 1]
    if gen_step and not (step and embeddable):    # a KiCad STEP that is not embedded still gives the height
        from . import step_gen
        try:
            generic = src == "default table"            # step_gen's per-family table is more specific (R vs C 0603)
            data, info = step_gen.generate(spec or t[1], None if generic else h, name=fp["name"], pads=fp["pads"])
        except ValueError as e:
            notes.append(f"3D: no generated STEP ({e})")
        else:
            x0, y0, _, x1, y1, z1 = info["bbox"]
            h, src = round(z1, 4), "step_gen table" if generic or not src else src     # through-hole: the pin tops
            fp["bodies"].append({"pts": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "height": h,
                                 "step": {"name": f"{AL.file_stem(fp['name'])}.step", "data": data.encode("ascii"), "rot": (0.0, 0.0, 0.0)}})
            notes.append(f"3D: generated STEP ({info['family']} model from {'the package spec' if spec else 'package_specs / the footprint name'}"
                         f"), height {h} mm ({src})" + ("; KiCad STEP has an offset/rotation/scale" if ext and step else ""))
            fp["height_mm"] = h
            return fp
    if not h:
        raise ValueError(f"{t[1]}: no 3D height (no KiCad model, no DEFAULT_HEIGHT entry); pass --height")
    if step and embeddable:
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


def _pin_text(s):                             # KiCad '~{RESET}' (KiCad 6: '~RESET~') overbar -> Altium 'R\E\S\E\T\'
    if "~{" not in s and s != "~":
        s = re.sub(r"~([^~]*)~?", lambda m: "~{" + m.group(1) + "}" if m.group(1) else "~", s)
    return AL.overline(s)


def _props(chain):
    out = {}
    for s in reversed(chain):                 # parent first, derived symbols override
        for p in sexpr.find(s, "property"):
            out[p[1]] = p[2]
    return out


def convert_symbol(chain, name=None, footprint=None, extra=(), pin_map=None, notes=None):
    """See _convert_symbol. Raises ValueError for a symbol missing what it needs (a pin without (at ...), ...)."""
    try:
        return _convert_symbol(chain, name, footprint, extra, pin_map, notes)
    except MALFORMED as e:
        raise ValueError(f"malformed KiCad symbol {name or ''}: {type(e).__name__}: {e}".replace("  ", " ")) from e


def _convert_symbol(chain, name=None, footprint=None, extra=(), pin_map=None, notes=None):
    """[symbol, parent, ...] -> altium_lib symbol dict. Units become Altium parts, De Morgan body styles
    (`_U_2` sub-symbols) Altium display modes; unit 0 / style 0 items go into every part / mode."""
    notes = [] if notes is None else notes
    base, props = chain[-1], _props(chain)
    if not props.get("Description") and props.get("ki_description"):     # KiCad 7 keeps it in ki_description
        props["Description"] = props["ki_description"]
    hide_names = _hidden(sexpr.first(base, "pin_names") or [])
    hide_nums = _hidden(sexpr.first(base, "pin_numbers") or [])
    units = {}
    for sub in sexpr.find(base, "symbol"):
        u, style = (int(v) for v in sub[1].rsplit("_", 2)[1:])
        style = int((sexpr.first(sub, "body_style") or [0, style])[1])
        units.setdefault((u, style), []).extend(sub[2:])
    n = max([u for u, _ in units if u > 0] or [1])
    nm = max([st for _, st in units if st > 0] or [1])
    prims, parts, modes, pins_at = [], [], [], {}
    for (u, st), items in units.items():
        for it in items:
            if isinstance(it, list) and it[0] == "pin" and not _hidden(it):
                at = sexpr.first(it, "at"); pins_at.setdefault((u, st, at[1], at[2]), True)
    for u, st in sorted(units):
        for it in units[(u, st)]:
            if not isinstance(it, list):
                continue
            for part in (range(1, n + 1) if u == 0 else [u]):
                for mode in (range(nm) if st == 0 else [st - 1]):
                    pr = _sym_prim(it, hide_names, hide_nums, pins_at, (u, st), pin_map or {}, notes)
                    if pr:
                        prims.append(pr); parts.append(part); modes.append(mode)
    if n > 1:
        notes.append(f"{n} units written as {n} Altium parts")
    if nm > 1:
        notes.append(f"De Morgan body style written as Altium display mode 1 ({nm} display modes)")
    norm = [pr for pr, m in zip(prims, modes) if not m]                 # labels placed from the normal view
    xs = [v for pr in norm if pr[0] != "pin" for v in _prim_xs(pr)[0]] + [pr[3] for pr in norm if pr[0] == "pin"]
    ys = [v for pr in norm if pr[0] != "pin" for v in _prim_xs(pr)[1]] + [pr[4] for pr in norm if pr[0] == "pin"]
    left, top, bot = min(xs or [0]), max(ys or [0]), min(ys or [0])
    ref = props.get("Reference", "U")
    keep = [("KiCad Footprint" if k == "Footprint" else k, props.get(k, "")) for k in ("Value", "Footprint", "Datasheet", "Description", "MPN")]
    params = [(k, v) for k, v in keep + list(extra) if v and v != "~"]
    params = list({k: (k, v) for k, v in params}.values())         # extra overrides
    return {"name": name or chain[0][1], "description": props.get("Description", ""), "designator": ref if ref.endswith("?") else ref + "?",
            "comment": "*", "footprint": footprint or (props.get("Footprint", "").split(":")[-1] or None), "prims": prims,
            "prim_parts": parts, "parts": n, "prim_modes": modes, "modes": nm, "params": params, "label_at": ((left, top + 3), (left, bot - 8))}


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
    if k == "text":                           # a symbol text's angle is in tenths of a degree (KiCad's parser: "LIB_TEXT
        at = sexpr.first(it, "at")            # is really decidegrees"), unlike every other (at ...)
        return ("text", _u(at[1]), _u(at[2]), it[1], round(float(at[3]) / 900) % 4 if len(at) > 3 else 0)
    if k == "pin":
        etype, at = it[1], sexpr.first(it, "at")
        x, y, a = float(at[1]), float(at[2]), int(float(at[3])) % 360 if len(at) > 3 else 0
        length = float((sexpr.first(it, "length") or [0, 2.54])[1])
        bx, by = x + length * math.cos(math.radians(a)), y + length * math.sin(math.radians(a))   # body end
        num = (sexpr.first(it, "number") or ["", ""])[1]
        name = _pin_text((sexpr.first(it, "name") or ["", ""])[1])
        hidden, p = _hidden(it), {"num": pin_map.get(num, num), "name": name, "type": KTYPE.get(etype, "passive")}
        shape = it[2] if isinstance(it[2], str) else "line"
        if shape not in AL.PIN_SHAPE:
            _note(notes, f"pin {num}: pin shape {shape} not converted")
        elif shape != "line":
            p["shape"] = shape
            if shape == "edge_clock_high":
                _note(notes, f"pin {num}: falling-edge clock written as clock + active-low input")
        if hidden and (*unit, at[1], at[2]) in pins_at:      # stacked on a visible pin: keep visible (Altium joins pins at one point)
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
        n = round(_u(length))
        tip = gx - n * round(math.cos(math.radians(a))) if a in (0, 180) else gy - n * round(math.sin(math.radians(a)))
        if tip % 10:                          # KiCad discretes end on 50 mil (D at ±150 mil): lengthen the pin outward so its
            n += 10 - tip % 10 if a in (180, 270) else tip % 10          # end is on Altium's 100 mil grid, the body stays put
            _note(notes, "pin ends moved out onto the 100 mil grid (pins lengthened)")
        return ("pin", p, SIDE[(a + 180) % 360], gx, gy, n)
    if k not in ("property", "text_box", "body_style", "unit"):
        _note(notes, f"symbol item {k} not converted")
    return None


# ---------------------------------------------------------------- checks & driver
def pin_pad_check(sym, fp):
    """Pin numbers without a pad and numbered pads without a pin (unnamed mechanical pads are ignored)."""
    pins = {pr[1]["num"] for pr in sym["prims"] if pr[0] == "pin"}
    pads = {p["designator"] for p in fp["pads"] if p["designator"]}
    return sorted(pins - pads), sorted(pads - pins)


def import_part(spec, out, http, step=False, write=True, gen_step=True, layers=None):
    """spec: {footprint, symbol, mpn?, footprint_as?, symbol_as?, height_mm?, pin_map?, note?} -> report dict.
    gen_step: a step_gen model instead of an extruded box when there is no KiCad STEP to embed.
    layers: the library rules' layers (rules.layers()); None = ecadlib's."""
    rep = {"mpn": spec.get("mpn", ""), "footprint": spec["footprint"], "symbol": spec.get("symbol"), "notes": [], "ok": False}
    try:
        text, src = load_footprint(spec["footprint"], http)
        fp, t = convert_footprint(text, spec.get("footprint_as"), src, rep["notes"],
                                  KICAD_LICENSE if src.startswith("KiCad ") else "")
        add_body(fp, t, http, spec.get("height_mm"), step, rep["notes"], gen_step=gen_step)
        rep["pcblib"] = str(AL.write_pcblib(Path(out) / "PcbLib" / f"{AL.file_stem(fp['name'])}.PcbLib", [fp], layers)) if write else None
        rep["pads"] = len(fp["pads"])
        if spec.get("symbol"):
            chain, ssrc = load_symbol(spec["symbol"], http)
            extra = [("MPN", spec["mpn"])] if spec.get("mpn") else []
            sym = convert_symbol(chain, spec.get("symbol_as") or spec.get("mpn"), fp["name"], extra, spec.get("pin_map"), rep["notes"])
            sym["description"] = sym["description"] or ssrc
            rep["schlib"] = str(AL.write_schlib(Path(out) / "SchLib" / f"{AL.file_stem(sym['name'])}.SchLib", [sym])) if write else None
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
        if write:                             # provenance for `upstream check-kicad` (never fails the part)
            from .upstream import record
            record(out, spec, rep, http, step, gen_step, layers)
    except Exception as e:                    # report per part, keep going
        rep["error"] = f"{type(e).__name__}: {e}"
    return rep


def main(argv=None):
    def opts(p, pre=""):                                     # accepted before or after the subcommand
        d = dict(default=argparse.SUPPRESS) if pre else {}
        p.add_argument("-o", "--out", dest=pre + "out", **(d or {"default": "out/altium_models_kicad"}))
        p.add_argument("--cache", dest=pre + "cache", **(d or {"default": "out/http_cache"}))
        p.add_argument("--offline", dest=pre + "offline", action="store_true", help="only use the HTTP cache", **d)
        p.add_argument("--step", dest=pre + "step", action="store_true", help="embed the KiCad STEP model instead of an extruded body", **d)
        p.add_argument("--box", dest=pre + "box", action="store_true",
                       help="extruded box instead of a generated (step_gen) STEP model when no KiCad STEP is embedded", **d)
        p.add_argument("--rules", dest=pre + "rules", help="library rules file: layers (default: ecadlib.toml found from -o)", **d)
    ap = argparse.ArgumentParser(description="Convert KiCad library footprints/symbols to Altium PcbLib/SchLib")
    opts(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="convert the parts listed in a JSON file")
    b.add_argument("parts")
    f = sub.add_parser("footprint", help="one footprint: Lib:Name or path.kicad_mod")
    f.add_argument("ref"); f.add_argument("--as", dest="as_"); f.add_argument("--height", type=float, help="fallback height, mm")
    s = sub.add_parser("symbol", help="one symbol: Lib:Name or path.kicad_sym[:Name]")
    s.add_argument("ref"); s.add_argument("--as", dest="as_"); s.add_argument("--footprint", help="PcbLib footprint name to link")
    s.add_argument("--param", action="append", default=[], help="extra parameter K=V")
    for p in (b, f, s):
        opts(p, "sub_")
    a = ap.parse_args(argv)
    for k in ("out", "cache", "offline", "step", "box", "rules"):
        if hasattr(a, "sub_" + k):
            setattr(a, k, getattr(a, "sub_" + k))
    http = CachedHTTP(a.cache, offline=a.offline, timeout=60)
    out = Path(a.out)
    if a.cmd == "symbol":
        notes = []
        chain, src = load_symbol(a.ref, http)
        sym = convert_symbol(chain, a.as_, a.footprint, [tuple(kv.split("=", 1)) for kv in a.param], None, notes)
        (out / "SchLib").mkdir(parents=True, exist_ok=True)
        path = AL.write_schlib(out / "SchLib" / f"{AL.file_stem(sym['name'])}.SchLib", [sym])
        from .upstream import record
        record(out, {"symbol": a.ref, "symbol_as": a.as_, "symbol_footprint": a.footprint, "params": a.param}, {"schlib": str(path), "notes": notes}, http)
        print(f"{sym['name']}: {sum(pr[0] == 'pin' for pr in sym['prims'])} pins -> {path}" + "".join(f"\n   {n}" for n in notes))
        return
    specs = json.loads(Path(a.parts).read_text(encoding="utf-8")) if a.cmd == "batch" else [{"footprint": a.ref, "footprint_as": a.as_, "height_mm": a.height}]
    (out / "PcbLib").mkdir(parents=True, exist_ok=True); (out / "SchLib").mkdir(parents=True, exist_ok=True)
    from . import rules as RU
    lay = RU.layers(RU.load(getattr(a, "rules", None), start=out))
    reps = [import_part(sp, out, http, a.step, gen_step=not a.box, layers=lay) for sp in specs]
    for r in reps:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['mpn'] or r['footprint']:<26} {r.get('pads', '-'):>3} pads {r.get('pins', '-'):>3} pins  "
              f"{Path(r.get('pcblib') or '-').name} {Path(r.get('schlib') or '-').name}")
        for n in r["notes"] + ([r["error"]] if r.get("error") else []):
            print(f"     {n}")
    if a.cmd == "batch":
        (out / "report.json").write_text(json.dumps(reps, indent=2), encoding="utf-8")
    if not all(r["ok"] for r in reps):
        sys.exit(f"{sum(not r['ok'] for r in reps)} of {len(reps)} failed")


if __name__ == "__main__":
    main()
