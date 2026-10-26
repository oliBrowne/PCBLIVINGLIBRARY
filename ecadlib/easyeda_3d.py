"""EasyEDA / LCSC 3D models: the STEP file an EasyEDA footprint names, downloaded and placed on the footprint.

An EasyEDA footprint (packageDetail.dataStr) carries its 3D model as one 'SVGNODE~{json}' shape whose attrs have
c_etype outline3D, the model's uuid, c_origin (the model's centre on the canvas, 10 mil units like the pads),
c_width / c_height (its outline there), z (its offset above the board, 10 mil) and c_rotation ("rx,ry,rz", degrees).
The STEP file is served by EasyEDA's model host (STEP_URL, the same undocumented endpoint EasyEDA's own editor and
easyeda2kicad use; written here from those facts, no code taken from anywhere).

place() moves the model so it sits on the footprint the way Altium and KiCad read a STEP with no offset or rotation:
  * c_rotation is applied first (x, then y, then z, about the model's own origin);
  * its x/y bounding box is centred on c_origin, taken relative to the footprint origin (head x/y) with y flipped
    (EasyEDA's canvas is y down, a STEP model y up);
  * z: the model's lowest point goes to the outline's z. An SMD part has z = 0 and rests on the board; a through-hole
    part's z is negative, its pins' depth below the board (a 2.54 mm header: z = -3 mm, pins -3 mm, housing 0 to 2.5
    mm). A through-hole part with z = 0 keeps the model's own z: EasyEDA gave no pin depth to place it by.
Only 3D CARTESIAN_POINTs move and only 3D DIRECTIONs turn: a rigid move, so radii, lengths and the file's units stay
as they are, and 2D points (curves in a face's parameter space) are left alone.

The body height is the placed model's top. When the download fails (EasyEDA blocked, no model, not a STEP file) the
librarian falls back to the generated package model (step_gen) or the extruded box, as before."""
import json, math, re

U = 0.254                                     # one EasyEDA unit (10 mil) in mm
STEP_URL = "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{}"
HEADERS = {"Referer": "https://easyeda.com/", "Accept": "*/*"}
MAX_BYTES = 30 * 1024 * 1024                  # a footprint's model, not a whole board
# CARTESIAN_POINT('name',(x,y,z)) and DIRECTION('name',(x,y,z)): three numbers only (2D ones are parameter space)
NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?"
ENTITY = re.compile(r"\b(CARTESIAN_POINT|DIRECTION)(\s*\(\s*'(?:[^']|'')*'\s*,\s*\(\s*)(" + NUM + r")(\s*,\s*)(" + NUM +
                    r")(\s*,\s*)(" + NUM + r")(\s*\)\s*\))")


def _f(v, d=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return d


def model_ref(data):
    """packageDetail.dataStr -> {uuid, title, origin (x, y) in mm from the footprint origin, y up, z mm, rotation
    (rx, ry, rz), size (w, h) mm or None, through_hole} or None when the footprint names no 3D model."""
    if not isinstance(data, dict):
        return None
    from .easyeda_convert import origin
    ox, oy = origin(data)                     # the footprint's own origin, as easyeda_convert placed the pads
    shapes = [s for s in data.get("shape") or [] if isinstance(s, str)]
    th = any(s.startswith("PAD~") and _f((s.split("~") + [""] * 10)[9]) > 0 for s in shapes)
    for s in shapes:
        if not s.startswith("SVGNODE~"):
            continue
        try:
            attrs = (json.loads(s.split("~", 1)[1]) or {}).get("attrs") or {}
        except (ValueError, AttributeError):
            continue
        if not isinstance(attrs, dict):
            continue
        uuid = str(attrs.get("uuid") or "").strip()
        if attrs.get("c_etype") != "outline3D" or not re.fullmatch(r"[0-9A-Za-z]{8,64}", uuid):
            continue
        cx, _, cy = str(attrs.get("c_origin") or f"{ox},{oy}").partition(",")
        rot = [_f(v) for v in (str(attrs.get("c_rotation") or "0,0,0").split(",") + ["0", "0"])[:3]]
        w, h = _f(attrs.get("c_width")) * U, _f(attrs.get("c_height")) * U
        return {"uuid": uuid, "title": str(attrs.get("title") or ""), "url": STEP_URL.format(uuid),
                "origin": ((_f(cx, ox) - ox) * U, -(_f(cy, oy) - oy) * U), "z": _f(attrs.get("z")) * U,
                "rotation": tuple(rot), "size": (w, h) if w > 0 and h > 0 else None, "through_hole": th}
    return None


def fetch(http, ref):
    """(STEP text, None) or (None, why not)."""
    r = http.get(ref["url"], headers=HEADERS, timeout=60)
    if not r.ok:
        return None, r.error or f"HTTP {r.status}"
    text = r.text or ""
    if len(text) > MAX_BYTES:
        return None, f"model is {len(text) // 1048576} MB, more than {MAX_BYTES // 1048576} MB"
    if not text.lstrip().startswith("ISO-10303-21"):
        return None, "the answer is not a STEP file"
    return text, None


def _unit(text):
    """mm per unit of the file's length unit (SI millimetre, metre, or an inch conversion)."""
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text, re.I):
        return 25.4
    m = re.search(r"SI_UNIT\s*\(\s*(\.\w+\.|\$)\s*,\s*\.METRE\.\s*\)", text)
    if not m:
        return 1.0
    return {"$": 1000.0, ".CENTI.": 10.0, ".DECI.": 100.0, ".MICRO.": 0.001}.get(m.group(1).upper(), 1.0)


def _rotate(p, rot):
    x, y, z = p
    for axis, deg in zip("xyz", rot):
        if not deg:
            continue
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        if axis == "x":
            y, z = y * c - z * s, y * s + z * c
        elif axis == "y":
            z, x = z * c - x * s, z * s + x * c
        else:
            x, y = x * c - y * s, x * s + y * c
    return x, y, z


def _num(v):
    t = f"{v:.9g}"
    return t if any(ch in t for ch in ".eE") else t + "."


REF = re.compile(r"#(\d+)")
ROW = re.compile(r"#(\d+)\s*=\s*([A-Z0-9_]+)\s*\((.*?)\)\s*;", re.S)


def _extent_points(text, rot):
    """Points that bound the model's solid, turned by rot: every vertex, every B-spline control point and each circle
    or ellipse's reach in its own plane. The placements' origins (AXIS2_PLACEMENT_3D, a LINE's point) are left out:
    the product's own placement at 0,0,0 would stretch the box to the file's origin."""
    rows = {int(m.group(1)): (m.group(2), m.group(3)) for m in ROW.finditer(text)}
    pts, dirs = {}, {}
    for m in ENTITY.finditer(text):
        row = re.search(r"#(\d+)\s*=\s*$", text[max(0, m.start() - 24):m.start()])
        if row:
            v = _rotate((float(m.group(3)), float(m.group(5)), float(m.group(7))), rot)
            (pts if m.group(1) == "CARTESIAN_POINT" else dirs)[int(row.group(1))] = v
    out = []
    for kind, args in rows.values():
        refs = [int(r) for r in REF.findall(args)]
        if kind == "VERTEX_POINT":
            out += [pts[r] for r in refs if r in pts]
        elif kind.startswith("B_SPLINE"):
            out += [pts[r] for r in refs if r in pts]
        elif kind in ("CIRCLE", "ELLIPSE") and refs and refs[0] in rows:
            nums = [float(v) for v in re.findall(NUM, REF.sub("", args.split(",", 1)[1]))]
            ax = [int(r) for r in REF.findall(rows[refs[0]][1])]
            if not nums or not ax or ax[0] not in pts:
                continue
            c, r = pts[ax[0]], max(nums)
            n = dirs.get(ax[1], (0.0, 0.0, 1.0)) if len(ax) > 1 else (0.0, 0.0, 1.0)
            ln = math.sqrt(sum(v * v for v in n)) or 1.0
            reach = [r * math.sqrt(max(0.0, 1 - (v / ln) ** 2)) for v in n]
            out += [tuple(c[i] - reach[i] for i in range(3)), tuple(c[i] + reach[i] for i in range(3))]
    return out or list(pts.values())


def place(text, ref):
    """STEP text -> (placed STEP text, info {bbox (x0, y0, z0, x1, y1, z1) mm, height mm, unit}). Raises ValueError for
    a file with no 3D points."""
    k, rot = _unit(text), ref.get("rotation") or (0, 0, 0)
    pts = _extent_points(text, rot)
    if not pts:
        raise ValueError("the STEP file has no 3D points")
    lo, hi = [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]
    tx = ref["origin"][0] / k - (lo[0] + hi[0]) / 2
    ty = ref["origin"][1] / k - (lo[1] + hi[1]) / 2
    z = ref.get("z", 0) or 0
    tz = 0.0 if ref.get("through_hole") and not z else z / k - lo[2]      # AD26 2026-10-25: z is where the bottom goes
    move = (tx, ty, tz)

    def sub(m):
        p = _rotate((float(m.group(3)), float(m.group(5)), float(m.group(7))), rot)
        if m.group(1) == "CARTESIAN_POINT":
            p = tuple(a + b for a, b in zip(p, move))
        return m.group(1) + m.group(2) + _num(p[0]) + m.group(4) + _num(p[1]) + m.group(6) + _num(p[2]) + m.group(8)
    out = ENTITY.sub(sub, text)
    bbox = tuple(round((v + move[i % 3]) * k, 4) for i, v in enumerate(lo + hi))
    return out, {"bbox": bbox, "height": round(bbox[5], 3), "unit": k}


def extent(text):
    """Bounding box (mm) of a STEP file as place() measures it, without moving it."""
    k = _unit(text)
    pts = _extent_points(text, (0, 0, 0))
    return tuple(round(min(p[i] for p in pts) * k, 4) for i in range(3)) + tuple(round(max(p[i] for p in pts) * k, 4) for i in range(3))


def checks(info, ref, pads=()):
    """Review notes on a placed model: its outline against EasyEDA's own (c_width / c_height: a 90 degree turn
    shows here) and its centre against the pads' centre."""
    notes = []
    x0, y0, z0, x1, y1, z1 = info["bbox"]
    w, h = x1 - x0, y1 - y0
    if ref.get("size"):
        ew, eh = ref["size"]
        near = lambda a, b: abs(a - b) <= max(0.3, 0.2 * b)
        if not (near(w, ew) and near(h, eh)):
            notes.append(f"3D model is {w:.2f} x {h:.2f} mm, EasyEDA's outline {ew:.2f} x {eh:.2f} mm"
                         + (" (turned 90 degrees?)" if near(w, eh) and near(h, ew) else "") + ": check it in the 3D view")
    if pads:
        px = [p["x"] for p in pads]
        py = [p["y"] for p in pads]
        cx, cy = (min(px) + max(px)) / 2, (min(py) + max(py)) / 2
        if math.hypot((x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy) > max(0.5, 0.15 * max(w, h)):
            notes.append(f"3D model centre ({(x0 + x1) / 2:.2f}, {(y0 + y1) / 2:.2f}) mm is off the pads' centre "
                         f"({cx:.2f}, {cy:.2f}) mm: check it in the 3D view")
    if z1 <= 0.05:
        notes.append(f"3D model top is at {z1:.2f} mm: check it in the 3D view")
    return notes


def add_body(fp, text, info, ref, notes, embed=True):
    """The placed model as fp's 3D body (embedded STEP; embed=False: an extruded box of its outline and height)."""
    x0, y0, z0, x1, y1, z1 = info["bbox"]
    body = {"pts": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "height": info["height"]}
    if embed:
        body["step"] = {"name": f"{ref['uuid']}.step", "data": text.encode("utf-8"), "rot": (0.0, 0.0, 0.0)}
    fp["bodies"].append(body)
    fp["height_mm"] = info["height"]
    notes.append(f"3D: {'embedded ' if embed else 'box from the '}EasyEDA STEP model {ref['title'] or ref['uuid']} "
                 f"(uuid {ref['uuid']}), height {info['height']} mm (EasyEDA 3D model)")
    return fp
