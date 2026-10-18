"""Assembly-ready parts: every part's pads checked against what the assembler places, and its placement rotation known.

    ecadlib assembly part FOOTPRINT.kicad_mod --lcsc C25744 [--profile jlcpcb] [--png overlay.png]
    ecadlib assembly check BOARD.kicad_pcb|PNP.csv             one report: pad verdicts, rotation evidence, LCSC numbers
    ecadlib assembly export BOARD.kicad_pcb|PNP.csv --profile jlcpcb -o out/   the assembler's BOM + CPL files
    ecadlib assembly profiles                                  the assembly profiles and where each rule comes from

Everything assembler-specific lives in a profile (ecadlib/assembly_profiles/<name>.json, docs/assembly.md): the 0 deg
pose per package family, the rotation sense on each board side, the CPL / BOM columns, the part-number field, pad
tolerances, per-part overrides, and a source for every rule. This module is the geometry, the same for every profile.

Frames. A footprint is in its own frame, mm, KiCad's: +x right, +y DOWN (top view). Angles are counter-clockwise as
seen from the top, KiCad's and the CPL's sense. rot(x, y, a) turns a point that way.

1. Canonical frame (frame()): from the pads, not the name: pin 1, the pad box centre, pin 1's corner or side, the
   direction the numbers run, the end a polarity mark (silk bar, '+', dot) points at, the package family.
2. Registration (register()): the assembler's own footprint for the part (JLCPCB: the EasyEDA footprint of the LCSC
   number, behind [providers] unofficial) against ours. Our pads are tried in the 8 orientations (4 turns x mirror),
   paired with theirs by pad number (by function, cathode / anode or + / -, for two-pin polarized parts when both
   symbols name the pins), and the translation is solved by least squares; the best proper turn wins. A mirror image
   that fits better is a finding (numbering runs the other way), never a rotation. Result: the turn c that carries our
   pads onto theirs, their origin in our frame, the per-pad deltas and the fit residual.
3. Verdict (assess()): pads identical / compatible / risky / incompatible (tolerances in the profile), rotation
   verified (registration) / override (profile, cited) / table (the family's pose on the canonical frame) / unknown.
   Placement on the board: CPL rotation = board angle - c; the CPL point = the board position of their origin.
   Parameters (parameters()): Assembly Profile, LCSC Part, Assembly Rotation (= -c, added to the board angle),
   Assembly Offset (their origin in our frame, mm, x right / y up), Assembly Check, Assembly Evidence.
4. Board export and preflight: ecadlib/assembly_io.py. Overlay picture: render_overlay()."""
import json, math, re
from dataclasses import dataclass, field
from pathlib import Path

from . import sexpr

PROFILE_DIR = Path(__file__).with_name("assembly_profiles")
PARAMS = ("Assembly Profile", "LCSC Part", "Assembly Rotation", "Assembly Offset", "Assembly Check", "Assembly Evidence")
PAD_VERDICTS = ("identical", "compatible", "risky", "incompatible")
ROT_EVIDENCE = ("verified", "override", "table", "unknown")
MOUNT = re.compile(r"^((MP|MH|SH|MTG|MOUNT|MECH|NPTH|S|H)\d*)?$", re.I)   # mounting / shield pads: not pins
CATHODE = re.compile(r"^(K|KA|C|CATHODE|-|NEG|NEGATIVE|VSS)$", re.I)
ANODE = re.compile(r"^(A|AN|ANODE|\+|POS|POSITIVE)$", re.I)


class AssemblyError(ValueError):
    pass


# ---------------------------------------------------------------- profiles
def profiles():
    """The bundled profile names (file stems in ecadlib/assembly_profiles)."""
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json"))


def load_profile(name="jlcpcb"):
    """A profile by name (bundled) or path (your own file), checked: every family and override cites a source."""
    p = Path(str(name))
    f = p if p.suffix == ".json" and p.exists() else PROFILE_DIR / f"{name}.json"
    if not f.is_file():
        raise AssemblyError(f"no assembly profile {name!r} (bundled: {', '.join(profiles())}; or a path to a .json file)")
    try:
        prof = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise AssemblyError(f"{f}: not JSON ({e})") from None
    problems = validate_profile(prof)
    if problems:
        raise AssemblyError(f"{f}: " + "; ".join(problems))
    prof["_path"] = str(f)
    return prof


def validate_profile(prof):
    """Problems with a profile's shape, [] when it is usable."""
    out = []
    for k in ("profile", "version", "sources", "families", "rotation", "tolerances", "exports", "part_number"):
        if k not in prof:
            out.append(f"missing '{k}'")
    if out:
        return out
    srcs = prof["sources"]
    cite = lambda what, s: [f"{what}: source {x!r} is not in 'sources'" for x in ([s] if isinstance(s, str) else s or [None])
                            if x not in srcs]
    for name, fam in prof["families"].items():
        out += cite(f"family {name}", fam.get("source"))
        z = fam.get("zero")
        if z is not None and not (isinstance(z, dict) and (z.get("pin1") in SIDES + CORNERS or z.get("pin1") is None)):
            out.append(f"family {name}: zero.pin1 must be one of {', '.join(SIDES + CORNERS)}")
        if z and z.get("run") not in (None,) + SIDES:
            out.append(f"family {name}: zero.run must be one of {', '.join(SIDES)}")
        for rx in fam.get("names", []):
            try:
                re.compile(rx)
            except re.error as e:
                out.append(f"family {name}: bad regex {rx!r} ({e})")
    for o in prof.get("overrides", []):
        out += cite(f"override {o.get('part')}", o.get("source"))
        if not isinstance(o.get("rotation"), (int, float)):
            out.append(f"override {o.get('part')}: rotation must be a number")
    for side in ("top", "bottom"):
        out += cite(f"rotation.{side}", (prof["rotation"].get(side) or {}).get("source"))
    return out


# ---------------------------------------------------------------- geometry
@dataclass
class Pad:
    num: str
    x: float
    y: float
    w: float
    h: float
    rot: float = 0.0                      # the pad's own angle in the footprint, deg CCW
    shape: str = "rect"
    kind: str = "smd"                     # smd | thru_hole | np_thru_hole
    func: str = ""                        # the symbol pin's name on this pad, when known

    def size(self, turn=0.0):
        """(width along x, height along y) after the footprint is turned by `turn` deg (exact at multiples of 90)."""
        q = round(((self.rot + turn) % 180) / 90) % 2
        return (self.h, self.w) if q else (self.w, self.h)


@dataclass
class Footprint:
    name: str
    pads: list
    silk: list = field(default_factory=list)       # [((x1, y1), (x2, y2))] segments on silk (polarity marks)
    fab: list = field(default_factory=list)        # segments on the assembly / fab layer (body outline)
    source: str = ""
    package: str = ""                              # the assembler's package name (EasyEDA title), when known
    meta: dict = field(default_factory=dict)       # the assembler's record of the part (easyeda_meta), when fetched

    @property
    def pins(self):
        """Pads that are part pins: copper, numbered, not a mounting / shield pad."""
        return [p for p in self.pads if p.kind != "np_thru_hole" and p.num and not MOUNT.match(p.num)]


def rot(x, y, a):
    """(x, y) turned a degrees counter-clockwise as seen from the top, in a +y-down frame."""
    r = math.radians(a)
    c, s = math.cos(r), math.sin(r)
    return (x * c + y * s, -x * s + y * c)


def _snap(a, tol=0.5):
    """a (deg) on [0, 360), snapped to a multiple of 90 within tol."""
    a %= 360
    k = round(a / 90) * 90
    return k % 360 if abs(a - k) <= tol else round(a, 2) % 360


def _num(n):
    """Pad number as compared: '01' and '1' are one number, case does not matter."""
    n = str(n or "").strip().upper()
    return str(int(n)) if n.isdigit() else n


def _polarity(func, pin1="cathode"):
    """'P' for the terminal a two-pin polarized part numbers 1 in ecadlib and KiCad (the cathode of a diode / LED, the +
    of a capacitor: pin1 = "cathode" | "positive"), 'N' for the other, '' when the pin name says neither."""
    f = re.sub(r"[\s_()]", "", str(func or ""))
    k, a = bool(CATHODE.match(f)), bool(ANODE.match(f))
    if pin1 == "positive":
        return "P" if a else "N" if k else ""
    return "P" if k else "N" if a else ""


def roles(fp, pin1="cathode", default_pad1=False):
    """({pad number: 'P' | 'N'}, how) for a two-pin polarized footprint: from the pin names on its pads, else from its
    polarity mark (a cathode bar or '+' marks the 'P' end in KiCad and EasyEDA drawings alike), else, with default_pad1,
    from ecadlib's rule (cathode / + on pad 1). ({}, '') when nothing tells."""
    pins = fp.pins
    if len({_num(p.num) for p in pins}) != 2 or len(pins) != 2:
        return {}, ""
    by = {_num(p.num): _polarity(p.func, pin1) for p in pins}
    if sorted(by.values()) == ["N", "P"]:
        return by, "pin names"
    fr = frame(fp)
    nums = sorted(by, key=lambda n: (n != "1", n))
    if fr.get("marked") and nums[0] == "1":
        p_end = nums[0] if fr["marked"] == "pin1" else nums[1]
        return {n: ("P" if n == p_end else "N") for n in nums}, "polarity mark"
    if default_pad1 and nums[0] == "1":
        return {nums[0]: "P", nums[1]: "N"}, "ecadlib's rule (cathode / + on pad 1)"
    return {}, ""


def _segs_kicad(layers, names):
    out = []
    for n in names:
        for g in layers.get(n, []):
            for ln in g["lines"]:
                out += list(zip(ln, ln[1:]))
    return out


def from_kicad_mod(text, source=""):
    """A .kicad_mod (text or path) -> Footprint (pads, silk and fab segments)."""
    from . import visual_check as VC
    if isinstance(text, Path) or (isinstance(text, str) and "(" not in text[:200] and Path(text).is_file()):
        text = Path(text).read_text(encoding="utf-8")
    try:
        fp = VC.parse_footprint(text)
    except VC.VisualCheckError as e:
        raise AssemblyError(str(e)) from None
    return _from_vc(fp, source)


def _from_vc(fp, source="", flip=False, angle=0.0):
    pads = []
    for p in fp["pads"]:
        if not p["copper"] and p["kind"] != "np_thru_hole":
            continue                                   # paste-only apertures are not pads
        r = p["rot"] - angle                           # a board file keeps each pad's angle with the footprint's added
        y, r = (-p["y"], -r) if flip else (p["y"], r)
        pads.append(Pad(p["num"], p["x"], y, p["w"], p["h"], r, p["shape"], p["kind"]))
    mirror = (lambda s: [((a[0], -a[1]), (b[0], -b[1])) for a, b in s]) if flip else (lambda s: s)
    side = "B" if flip else "F"
    return Footprint(fp["name"], pads, mirror(_segs_kicad(fp["layers"], (f"{side}.SilkS", f"{side}.Silkscreen"))),
                     mirror(_segs_kicad(fp["layers"], (f"{side}.Fab",))), source)


def from_node(node, source="", flip=False, angle=0.0):
    """A parsed (footprint ...) node (a library footprint, or one placed on a board) -> Footprint. flip: the node is on
    the bottom side, as KiCad keeps it (pads mirrored, y -> -y): mirrored back, so it reads like the library one.
    angle: the footprint's angle on the board (taken off the pad angles, which a board file stores with it added)."""
    from . import visual_check as VC
    layers = {}
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexpr.find(node, kind):
            layer, gr = VC._graphic(g)
            if layer:
                layers.setdefault(layer, []).append(gr)
    pads = []
    for p in sexpr.find(node, "pad"):
        try:
            pads.append(VC._pad(p))
        except (TypeError, ValueError, IndexError):
            continue
    fp = {"name": str(node[1]) if len(node) > 1 else "", "pads": pads, "layers": layers}
    out = _from_vc(fp, source, flip, angle)
    funcs = {}
    for p in sexpr.find(node, "pad"):
        f = sexpr.first(p, "pinfunction")
        if f and len(f) > 1:
            funcs.setdefault(_num(p[1]), str(f[1]))
    for p in out.pads:
        p.func = funcs.get(_num(p.num), "")
    return out


def easyeda_pin_names(result):
    """{pad number: pin name} from an EasyEDA component (its symbol's pins, every unit of a multi-unit symbol)."""
    names = {}
    docs = [result.get("dataStr") or {}] + [(s or {}).get("dataStr") or {} for s in (result.get("subparts") or [])
                                              if isinstance(s, dict)]
    for ds in docs:
        for s in ds.get("shape") or []:
            if not isinstance(s, str) or not s.startswith("P~"):
                continue
            seg = [g.split("~") for g in s.split("^^")]
            st = seg[0] + [""] * 9
            num = (seg[4][4] if len(seg) > 4 and len(seg[4]) > 4 else "") or st[3]
            nm = seg[3][4] if len(seg) > 3 and len(seg[3]) > 4 else ""
            if num:
                names.setdefault(_num(num), nm)
    return names


def easyeda_meta(payload):
    """What the assembler's own record says about the part: LCSC number, MPN, manufacturer, package, JLCPCB part class
    (Basic / Extended), LCSC stock and unit price, whether JLCPCB assembles it, and the library's credit line."""
    r = (payload or {}).get("result") or {} if isinstance(payload, dict) else {}
    head = ((r.get("dataStr") or {}).get("head") or {})
    cp = head.get("c_para") or {}
    lc = r.get("lcsc") or {}
    return {"lcsc": str(lc.get("number") or cp.get("Supplier Part") or ""), "mpn": cp.get("Manufacturer Part") or r.get("title") or "",
            "manufacturer": cp.get("Manufacturer") or "", "package": cp.get("package") or (r.get("packageDetail") or {}).get("title") or "",
            "part_class": cp.get("JLCPCB Part Class") or "", "stock": lc.get("stock"), "price": lc.get("price"),
            "min_qty": lc.get("min"), "smt": r.get("SMT"), "on_sale": r.get("jlcOnSale"),
            "credit": "JLCEDA/EasyEDA Official Library (https://lceda.cn/, https://easyeda.com)" if head.get("license") else ""}


def from_easyeda(payload, source=""):
    """An EasyEDA component answer (dict, or a recorded file) -> the assembler's Footprint, with the symbol's pin
    names on its pads. Raises AssemblyError when it has no footprint."""
    from . import easyeda_convert as EC
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    r = (payload or {}).get("result") if isinstance(payload, dict) else None
    pkg = (r or {}).get("packageDetail") or {}
    if not isinstance(pkg.get("dataStr"), dict):
        raise AssemblyError("the EasyEDA answer has no footprint")
    try:
        text = EC.footprint_to_kicad(pkg["dataStr"], "ASSEMBLER", [])
    except ValueError as e:
        raise AssemblyError(f"EasyEDA footprint: {e}") from None
    fp = from_kicad_mod(text, source)
    fp.name = fp.package = str(pkg.get("title") or "")
    names = easyeda_pin_names(r)
    for p in fp.pads:
        p.func = names.get(_num(p.num), "")
    fp.meta = easyeda_meta(payload)
    return fp


def transform(pads, turn, mirror=False):
    """Pads as Pad copies, mirrored (x -> -x) first when mirror, then turned `turn` deg CCW."""
    out = []
    for p in pads:
        x = -p.x if mirror else p.x
        x, y = rot(x, p.y, turn)
        r = (-p.rot if mirror else p.rot) + turn
        out.append(Pad(p.num, x, y, p.w, p.h, r, p.shape, p.kind, p.func))
    return out


def _bbox(pts):
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _extent(p):
    w, h = p.size()
    return [(p.x - w / 2, p.y - h / 2), (p.x + w / 2, p.y + h / 2)]


# ---------------------------------------------------------------- canonical frame
SIDES = ("L", "R", "T", "B")
CORNERS = ("TL", "TR", "BL", "BR")


def _side(dx, dy):
    """Screen direction of a vector (+y down): L / R / T / B."""
    return ("R" if dx > 0 else "L") if abs(dx) >= abs(dy) else ("B" if dy > 0 else "T")


def _where(x, y, box, eps=0.05):
    """Pin 1's place on the pad box: a corner (TL ..), a side (L ..) or C (centre)."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hx, hy = max((box[2] - box[0]) / 2, 1e-9), max((box[3] - box[1]) / 2, 1e-9)
    u, v = (x - cx) / hx, (y - cy) / hy
    h = "L" if u < -eps else "R" if u > eps else ""
    t = "T" if v < -eps else "B" if v > eps else ""
    if h and t:
        if abs(abs(u) - abs(v)) > 0.5:            # far nearer one side than the other: a side, not a corner
            return h if abs(u) > abs(v) else t
        return t + h
    return h or t or "C"


def turn_pose(pose, turn):
    """A (pin1, run) pose after the footprint is turned `turn` deg CCW (a multiple of 90)."""
    k = round(turn / 90) % 4
    ccw = {"L": "B", "B": "R", "R": "T", "T": "L", "TL": "BL", "BL": "BR", "BR": "TR", "TR": "TL", "C": "C", None: None}
    p1, run = pose
    for _ in range(k):
        p1, run = ccw[p1], ccw[run]
    return p1, run


def frame(fp, family=None):
    """The footprint's canonical frame from its geometry: pin 1 and where it sits, the way the numbers run, the end a
    polarity mark points at (two-pin parts), the pad box and its centre."""
    pins = fp.pins or [p for p in fp.pads if p.kind != "np_thru_hole"]
    out = {"pins": len({_num(p.num) for p in pins}), "pin1": None, "where": None, "run": None, "marked": None,
           "center": None, "box": None}
    if not pins:
        return out
    box = _bbox([q for p in pins for q in _extent(p)])
    out["box"] = tuple(round(v, 4) for v in box)
    out["center"] = (round((box[0] + box[2]) / 2, 4), round((box[1] + box[3]) / 2, 4))
    by = {}
    for p in pins:
        by.setdefault(_num(p.num), p)
    p1 = by.get("1") or by.get("A1")
    if p1 is None:
        return out
    out["pin1"] = {"num": p1.num, "x": round(p1.x, 4), "y": round(p1.y, 4)}
    if len(by) == 2:                                   # two pins: pin 1's side, and the polarity-marked end
        other = next(p for k, p in by.items() if p is not p1)
        out["where"] = _side(p1.x - other.x, p1.y - other.y)
        out["marked"] = _marked_end(fp, p1, other)
        return out
    out["where"] = _where(p1.x, p1.y, box)
    nxt = by.get("2")
    if nxt is None and p1.num.upper() == "A1":
        nxt = by.get("A2") or by.get("B1")
    if nxt is not None and (abs(nxt.x - p1.x) > 1e-6 or abs(nxt.y - p1.y) > 1e-6):
        out["run"] = _side(nxt.x - p1.x, nxt.y - p1.y)
    return out


def _marked_end(fp, a, b):
    """For a two-pin part: 'pin1' / 'pin2' when the silk (else fab) marks one end more than the other (a cathode bar,
    a '+', a dot), None when the drawing is symmetric. Ink is counted beyond the body centre on each pad's side."""
    ax, ay = b.x - a.x, b.y - a.y
    L = math.hypot(ax, ay)
    if L < 1e-9:
        return None
    ux, uy = ax / L, ay / L
    mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
    for segs in (fp.silk, fp.fab):
        ink = [0.0, 0.0]
        for p, q in segs:
            n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 0.05))
            for i in range(n):
                t0, t1 = i / n, (i + 1) / n
                x0, y0 = p[0] + (q[0] - p[0]) * t0, p[1] + (q[1] - p[1]) * t0
                x1, y1 = p[0] + (q[0] - p[0]) * t1, p[1] + (q[1] - p[1]) * t1
                s = ((x0 + x1) / 2 - mx) * ux + ((y0 + y1) / 2 - my) * uy
                d = math.hypot(x1 - x0, y1 - y0)
                if abs(s) > 0.02 * L:
                    ink[0 if s < 0 else 1] += d
        tot = ink[0] + ink[1]
        if tot > 0.05:
            if ink[0] > 1.35 * ink[1] + 0.1:
                return "pin1"
            if ink[1] > 1.35 * ink[0] + 0.1:
                return "pin2"
            return None
    return None


def family_of(prof, names=(), fp=None):
    """The profile family for a footprint: the first family whose name patterns match one of `names` (footprint
    name, the assembler's package name, the LCSC package), else by pad count; None when nothing fits."""
    names = [str(n) for n in names if n]
    for key, fam in prof["families"].items():
        for rx in fam.get("names", []):
            if any(re.search(rx, n, re.I) for n in names):
                pins = len({_num(p.num) for p in fp.pins}) if fp else None
                if fp is not None and fam.get("pins") and pins not in fam["pins"]:
                    continue
                return key
    return None


def table_turn(prof, family, fr, p_end=None):
    """The turn c (deg CCW) that carries a footprint in canonical frame `fr` to the profile family's 0 deg pose, or
    (None, why). p_end: for a two-pin polarized part, the side ('L' ..) its cathode / + terminal is on, which is what
    the family rule places (its pad number does not matter)."""
    fam = prof["families"].get(family or "") or {}
    z = fam.get("zero")
    if not z:
        return None, f"no 0 deg rule for {family or 'this package'} in the profile"
    if not fr.get("where"):
        return None, "pin 1 not found"
    want = (z.get("pin1"), z.get("run"))
    have = (p_end or fr["where"], fr["run"] if want[1] else None)
    for k in range(4):
        if turn_pose(have, 90 * k) == want:
            return 90 * k, ""
    return None, f"pin 1 {have[0]}{', numbers run ' + str(have[1]) if have[1] else ''} matches no turn of the 0 deg pose"


# ---------------------------------------------------------------- registration
@dataclass
class Fit:
    turn: float                    # deg CCW that carries our pads onto theirs
    mirror: bool
    tx: float                      # translation (their frame) after the turn
    ty: float
    residual: float                # RMS distance of paired pad centres, mm
    pairs: int
    keys: str = "number"           # what paired the pads: number | function

    def apply(self, x, y):
        if self.mirror:
            x = -x
        x, y = rot(x, y, self.turn)
        return x + self.tx, y + self.ty


def _keyed(pads, role=None):
    """{key: [pads]} for the pairing: pad number, or the polarity role ('P' / 'N') when `role` maps the pads."""
    out = {}
    for p in pads:
        k = role.get(_num(p.num), "") if role else _num(p.num)
        if k:
            out.setdefault(k, []).append(p)
    return out


def _fit(ka, kb, turn, mirror):
    """Least-squares translation for one orientation over the pads paired by key (repeated keys: nearest after a first
    estimate). Returns Fit, or None when fewer than one pair."""
    uniq = [k for k in ka if k in kb and len(ka[k]) == 1 and len(kb[k]) == 1]
    t = transform
    pa = {k: t(v, turn, mirror) for k, v in ka.items() if k in kb}
    if uniq:
        dx = sum(kb[k][0].x - pa[k][0].x for k in uniq) / len(uniq)
        dy = sum(kb[k][0].y - pa[k][0].y for k in uniq) / len(uniq)
    else:
        allb = [p for k in pa for p in kb[k]]
        alla = [p for k in pa for p in pa[k]]
        if not allb:
            return None
        dx = sum(p.x for p in allb) / len(allb) - sum(p.x for p in alla) / len(alla)
        dy = sum(p.y for p in allb) / len(allb) - sum(p.y for p in alla) / len(alla)
    pairs = []
    for k in pa:
        bs = list(kb[k])
        for a in sorted(pa[k], key=lambda a: min(math.hypot(b.x - a.x - dx, b.y - a.y - dy) for b in bs) if bs else 0):
            if not bs:
                break
            b = min(bs, key=lambda b: math.hypot(b.x - a.x - dx, b.y - a.y - dy))
            bs.remove(b)
            pairs.append((a, b))
    if not pairs:
        return None
    dx = sum(b.x - a.x for a, b in pairs) / len(pairs)
    dy = sum(b.y - a.y for a, b in pairs) / len(pairs)
    res = math.sqrt(sum((b.x - a.x - dx) ** 2 + (b.y - a.y - dy) ** 2 for a, b in pairs) / len(pairs))
    return Fit(turn % 360, mirror, dx, dy, res, len(pairs))


def _kabsch(ka, kb):
    """The best proper turn (any angle) over uniquely keyed pairs, or None (fewer than 2 pairs, or all on one point)."""
    ks = [k for k in ka if k in kb and len(ka[k]) == 1 and len(kb[k]) == 1]
    if len(ks) < 2:
        return None
    A = [(ka[k][0].x, ka[k][0].y) for k in ks]
    B = [(kb[k][0].x, kb[k][0].y) for k in ks]
    ca = (sum(p[0] for p in A) / len(A), sum(p[1] for p in A) / len(A))
    cb = (sum(p[0] for p in B) / len(B), sum(p[1] for p in B) / len(B))
    sdot = scross = 0.0
    for (ax, ay), (bx, by) in zip(A, B):
        ax, ay, bx, by = ax - ca[0], ay - ca[1], bx - cb[0], by - cb[1]
        sdot += ax * bx + ay * by
        scross += ax * by - ay * bx                   # +y down: a positive cross is a clockwise-on-screen turn
    if abs(sdot) + abs(scross) < 1e-9:
        return None
    return (-math.degrees(math.atan2(scross, sdot))) % 360


def register(ours, theirs, role_ours=None, role_theirs=None):
    """Fit our pads onto the assembler's. role_ours / role_theirs ({pad: 'P' | 'N'}, from roles()): pair a two-pin
    polarized part by terminal instead of pad number. Returns a dict: fit (best proper Fit), mirror (best mirrored Fit),
    candidates [Fit], by ('number' | 'function'), ambiguous (two turns fit equally), or {'fit': None, 'why': ...}."""
    a_pins, b_pins = ours.pins, theirs.pins
    by_fn = bool(role_ours) and bool(role_theirs)
    ka, kb = _keyed(a_pins, role_ours if by_fn else None), _keyed(b_pins, role_theirs if by_fn else None)
    common = [k for k in ka if k in kb]
    if len(common) < (1 if any(len(ka[k]) > 1 for k in common) else 2):
        return {"fit": None, "why": f"too few pads pair by {'function' if by_fn else 'number'} "
                                    f"(ours {_names(a_pins)}, theirs {_names(b_pins)})", "by": "function" if by_fn else "number"}
    cands = []
    for mirror in (False, True):
        for k in range(4):
            f = _fit(ka, kb, 90 * k, mirror)
            if f:
                f.keys = "function" if by_fn else "number"
                cands.append(f)
    ang = _kabsch(ka, kb)
    if ang is not None and abs(ang - 90 * round(ang / 90)) > 0.5:     # off the 90 deg grid: try that turn too
        f = _fit(ka, kb, ang, False)
        if f:
            f.keys = "function" if by_fn else "number"
            cands.append(f)
    proper = sorted((c for c in cands if not c.mirror), key=lambda c: (round(c.residual, 4), c.turn % 90 != 0, c.turn))
    mirrored = sorted((c for c in cands if c.mirror), key=lambda c: c.residual)
    best = proper[0]
    if best.turn % 90:                                  # an off-grid turn wins only when it fits clearly better
        grid = next((c for c in proper if c.turn % 90 == 0), None)
        if grid and grid.residual <= best.residual + 0.01:
            best = grid
    amb = len(proper) > 1 and abs(proper[1].residual - best.residual) < 1e-4 and proper[1].turn != best.turn \
        and proper[1] is not best
    return {"fit": best, "mirror": mirrored[0] if mirrored else None, "candidates": cands, "ambiguous": amb,
            "by": "function" if by_fn else "number"}


def _names(pads):
    from .footprint_check import _ranges
    return _ranges({p.num for p in pads}) or "none"


def compare(ours, theirs, fit, tol):
    """Per-pad comparison after the fit: [{ours, theirs, dpos, dw, dh}], our unmatched pads, their unmatched pads,
    pairs whose numbers differ."""
    mov = []
    for p in ours.pads:
        if p.kind == "np_thru_hole":
            continue
        x, y = fit.apply(p.x, p.y)
        w, h = p.size(fit.turn)
        mov.append((p, x, y, w, h))
    theirs_c = [p for p in theirs.pads if p.kind != "np_thru_hole"]
    used, rows, lost_theirs = set(), [], []
    for b in sorted(theirs_c, key=lambda b: -b.w * b.h):
        bw, bh = b.size()
        cand = [(math.hypot(x - b.x, y - b.y), i) for i, (p, x, y, w, h) in enumerate(mov) if i not in used
                and abs(x - b.x) < (w + bw) / 2 and abs(y - b.y) < (h + bh) / 2]
        if not cand:
            lost_theirs.append(b)
            continue
        d, i = min(cand)
        used.add(i)
        p, x, y, w, h = mov[i]
        along_x = bw >= bh                          # their pad's long axis: the lead's direction
        across = abs(b.y - y) if along_x else abs(b.x - x)
        if abs(bw - bh) < 0.05 * max(bw, bh):        # square / round land: no lead axis
            across = max(abs(b.x - x), abs(b.y - y))
        rows.append({"ours": p.num, "theirs": b.num, "dpos": round(d, 4), "dx": round(b.x - x, 4), "dy": round(b.y - y, 4),
                     "across": round(across, 4), "dwidth": round((bh - h) if along_x else (bw - w), 4),
                     "dw": round(bw - w, 4), "dh": round(bh - h, 4), "w": round(w, 4), "h": round(h, 4),
                     "tw": round(bw, 4), "th": round(bh, 4), "overlap": round(_overlap(x, y, w, h, b.x, b.y, bw, bh), 3)})
    lost_ours = [mov[i][0] for i in range(len(mov)) if i not in used]
    renum = [r for r in rows if _num(r["ours"]) != _num(r["theirs"]) and r["ours"] and r["theirs"]
             and not (MOUNT.match(r["ours"]) or MOUNT.match(r["theirs"]))]
    return rows, lost_ours, lost_theirs, renum


def _overlap(x, y, w, h, bx, by, bw, bh):
    """Share of their pad's copper (the part's terminal land) that our pad covers."""
    ox = max(0.0, min(x + w / 2, bx + bw / 2) - max(x - w / 2, bx - bw / 2))
    oy = max(0.0, min(y + h / 2, by + bh / 2) - max(y - h / 2, by - bh / 2))
    return ox * oy / max(bw * bh, 1e-12)


def _is_ep(p, fp):
    """An exposed / thermal pad: the biggest pad, well over the median pin's area, near the pad box centre."""
    pins = fp.pins
    if len(pins) < 3:
        return False
    areas = sorted(q.w * q.h for q in pins)
    med = areas[len(areas) // 2]
    box = _bbox([q for q in (_extent(x) for x in pins) for q in q])
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    span = max(box[2] - box[0], box[3] - box[1], 1e-9)
    return p.w * p.h >= 2.5 * med and math.hypot(p.x - cx, p.y - cy) <= 0.2 * span


def pad_verdict(ours, theirs, reg, tol):
    """(verdict, reasons, rows) for a registration: identical / compatible / risky / incompatible."""
    fit = reg.get("fit")
    if fit is None:
        return "incompatible", [reg.get("why") or "no fit"], []
    reasons = []
    na = {_num(p.num) for p in ours.pins if not _is_ep(p, ours)}     # an exposed pad on one side only is a risk, below
    nb = {_num(p.num) for p in theirs.pins if not _is_ep(p, theirs)}
    if reg.get("by") == "number" and len(na) != len(nb):
        reasons.append(f"pin count differs: ours {len(na)} ({_names(ours.pins)}), theirs {len(nb)} ({_names(theirs.pins)})")
    m = reg.get("mirror")
    if m and m.residual + 1e-3 < 0.5 * fit.residual and fit.residual > tol["position_mm"]:
        reasons.append(f"the pad numbers run the other way round (a mirror image fits: {m.residual:.3f} mm against "
                       f"{fit.residual:.3f} mm for the best turn)")
    rows, lost_o, lost_t, renum = compare(ours, theirs, fit, tol)
    pitch = _pitch(theirs)
    off = [r for r in rows if r["across"] > max(tol["incompatible_mm"], 0.5 * min(r["tw"], r["th"]),
                                                  tol["incompatible_pitch_share"] * (pitch or 0)) or r["overlap"] <= 0]
    if off:
        w = max(off, key=lambda r: r["across"])
        reasons.append(f"pads do not line up: {len(off)} pad(s) off their land sideways, worst pad {w['ours']} by "
                       f"{w['across']:.3f} mm (pitch or spacing differs)")
    real_renum = [r for r in renum if not (_is_ep(_pad(ours, r["ours"]), ours) and _is_ep(_pad(theirs, r["theirs"]), theirs))]
    if real_renum and reg.get("by") == "number":
        reasons.append("pads in the same place carry other numbers: " +
                       ", ".join(f"ours {r['ours']} = theirs {r['theirs']}" for r in real_renum[:6]))
    lost_t_pins = [p for p in lost_t if p.num and not MOUNT.match(p.num)]
    if lost_t_pins and not all(_is_ep(p, theirs) for p in lost_t_pins):
        reasons.append("the assembler's footprint has pads ours lacks: " + ", ".join(p.num for p in lost_t_pins[:8]))
    if reasons:
        return "incompatible", reasons, rows
    risky = []
    if lost_t_pins:
        risky.append(f"their footprint has an exposed pad ({', '.join(p.num for p in lost_t_pins)}) that ours lacks")
    lost_o_pins = [p for p in lost_o if p.num and not MOUNT.match(p.num)]
    if lost_o_pins:
        risky.append("our footprint has pads the assembler's lacks: " + ", ".join(p.num for p in lost_o_pins[:8])
                     + (" (exposed pad)" if all(_is_ep(p, ours) for p in lost_o_pins) else ""))
    a2, b2 = ours.pins, theirs.pins
    if len(a2) == 2 and len(b2) == 2 and len(rows) == 2:     # two-pin parts: the pad spacing is what an assembler checks
        da, db = math.hypot(a2[0].x - a2[1].x, a2[0].y - a2[1].y), math.hypot(b2[0].x - b2[1].x, b2[0].y - b2[1].y)
        if db > 0 and abs(da - db) / db > tol["two_pin_span_share"]:
            risky.append(f"pad spacing differs: {da:.3f} mm centre to centre ours, {db:.3f} mm theirs "
                         f"({100 * (da - db) / db:+.0f}%, more than {100 * tol['two_pin_span_share']:.0f}%)")
    worst = []
    for r in rows:
        short = min(r["tw"], r["th"])
        across_lim = max(tol["position_mm"], tol["position_share"] * short)
        width_lim = max(tol["size_mm"], tol["size_share"] * short)
        if r["across"] > across_lim or abs(r["dwidth"]) > width_lim or r["overlap"] < tol["min_overlap"]:
            worst.append(r)
    if worst:
        w = min(worst, key=lambda r: (r["overlap"], -r["across"]))
        risky.append(f"{len(worst)} pad(s) differ beyond tolerance; worst: pad {w['ours']}, "
                     f"{w['w']:.2f}x{w['h']:.2f} mm ours vs {w['tw']:.2f}x{w['th']:.2f} mm theirs, {w['dpos']:.3f} mm apart "
                     f"({w['across']:.3f} mm sideways), {100 * w['overlap']:.0f}% of their land covered")
    if risky:
        return "risky", risky, rows
    same = all(r["dpos"] <= tol["identical_mm"] and abs(r["dw"]) <= tol["identical_mm"] and abs(r["dh"]) <= tol["identical_mm"]
               for r in rows) and not lost_o and not lost_t
    if same:
        return "identical", [], rows
    w = max(rows, key=lambda r: max(r["dpos"], abs(r["dw"]), abs(r["dh"]))) if rows else None
    note = [f"same pads within tolerance; largest difference pad {w['ours']}: {max(w['dpos'], abs(w['dw']), abs(w['dh'])):.3f} mm"] if w else []
    return "compatible", note, rows


def _pad(fp, num):
    return next((p for p in fp.pads if p.num == num), Pad(num, 0, 0, 0, 0))


def _pitch(fp):
    pins = fp.pins
    d = [min(math.hypot(p.x - q.x, p.y - q.y) for q in pins if q is not p) for p in pins] if len(pins) > 1 else []
    d = [round(v, 3) for v in d if v > 1e-6]
    return max(set(d), key=d.count) if d else None


# ---------------------------------------------------------------- assessment
TOL_DEFAULT = {"identical_mm": 0.02, "position_mm": 0.05, "position_share": 0.15, "size_mm": 0.1, "size_share": 0.25,
               "min_overlap": 0.7, "two_pin_span_share": 0.15, "incompatible_mm": 0.15, "incompatible_pitch_share": 0.25}


def override_for(prof, lcsc="", names=()):
    """The profile's per-part override for an LCSC number (or a footprint name pattern), else None."""
    for o in prof.get("overrides", []):
        if lcsc and str(o.get("part", "")).upper() == str(lcsc).upper():
            return o
        if o.get("footprint") and any(re.search(o["footprint"], str(n)) for n in names if n):
            return o
    return None


def assess(ours, theirs=None, prof=None, lcsc="", names=(), strict=False, polarized=None, ours_rule=True):
    """The assembly verdict for one part. ours: our Footprint; theirs: the assembler's Footprint or None (not fetched,
    not found, [providers] unofficial = false). Returns a dict:
      pads        identical | compatible | risky | incompatible | unchecked
      rotation    verified | override | table | unknown
      turn        c, deg CCW carrying our pads onto theirs (None when unknown); correction = -c (Assembly Rotation)
      offset      their origin in our frame (x right, y down; mm)
      family, polarized, frame, residual, reasons, review (things a person should look at), hold (strict findings),
      rows (per-pad deltas), fit (the Fit) and notes.
    ours_rule: our footprint follows ecadlib's rule (cathode / + on pad 1) when its pads name no pins (library parts);
    False for a board footprint of unknown origin."""
    prof = prof or load_profile()
    tol = {**TOL_DEFAULT, **prof.get("tolerances", {})}
    names = [n for n in ([ours.name] + list(names) + ([theirs.package] if theirs else [])) if n]
    fam = family_of(prof, names, ours)
    famd = prof["families"].get(fam or "") or {}
    pol = bool(famd.get("polarized")) if polarized is None else polarized
    fr = frame(ours, fam)
    out = {"profile": prof["profile"], "lcsc": lcsc, "family": fam, "polarized": pol, "frame": fr, "pads": "unchecked",
           "rotation": "unknown", "turn": None, "offset": (0.0, 0.0), "residual": None, "reasons": [], "review": [],
           "hold": [], "rows": [], "fit": None, "notes": [], "theirs": theirs.package if theirs else ""}
    ov = override_for(prof, lcsc, names)
    if theirs is not None:
        ra = rb = {}
        if pol:
            p1 = famd.get("pin1", "cathode")
            ra, how_a = roles(ours, p1, default_pad1=ours_rule)
            rb, how_b = roles(theirs, p1)
            if ra and rb:
                out["notes"].append(f"terminals paired by polarity: ours from {how_a}, theirs from {how_b}")
                if how_b == "pin names":
                    mk = roles(Footprint(theirs.name, [Pad(q.num, q.x, q.y, q.w, q.h, q.rot, q.shape, q.kind) for q in theirs.pads],
                                         theirs.silk, theirs.fab), p1)[0]
                    if mk and mk != rb:
                        out["review"].append("the assembler's pin names and its polarity mark point at opposite ends; "
                                             "the pin names were used: check the overlay")
        reg = register(ours, theirs, ra or None, rb or None)
        verdict, reasons, rows = pad_verdict(ours, theirs, reg, tol)
        out.update(pads=verdict, reasons=reasons, rows=rows)
        fit = reg.get("fit")
        if fit is not None and verdict != "incompatible":
            out["fit"] = fit
            out["residual"] = round(fit.residual, 4)
            out["turn"] = _snap(fit.turn)
            inv = rot(-fit.tx, -fit.ty, -fit.turn)          # their origin carried back into our frame
            out["offset"] = (round(inv[0], 3) + 0.0, round(inv[1], 3) + 0.0)
            out["rotation"] = "verified"
            out["notes"].append(f"pads paired by {reg['by']}" + (", two turns fit equally" if reg.get("ambiguous") else ""))
            if pol and reg["by"] == "number":
                out["rotation"] = "unknown"
                out["turn"] = None
                out["notes"].append("polarized part: nothing on the assembler's footprint tells which pad is the "
                                    f"{'cathode' if famd.get('pin1', 'cathode') == 'cathode' else '+'} (no pin names, no "
                                    "polarity mark): the pads were checked by number, the turn is not taken from them")
        elif fit is not None:
            out["fit"] = fit
            out["residual"] = round(fit.residual, 4)
    if ov is not None:
        out["rotation"] = "override"
        out["turn"] = _snap(-float(ov["rotation"]))
        if ov.get("offset"):
            out["offset"] = (float(ov["offset"][0]), -float(ov["offset"][1]))
        out["notes"].append(f"profile override for {ov.get('part') or ov.get('footprint')}: {ov.get('note', '')}".rstrip(": "))
    if out["pads"] == "incompatible" and out["rotation"] == "unknown":
        out["notes"].append("rotation unknown: the pads do not match the assembler's footprint")
    elif out["rotation"] == "unknown" and out["turn"] is None:
        p_end = None
        if pol:
            ra = roles(ours, famd.get("pin1", "cathode"), default_pad1=ours_rule)[0]
            pp = [q for q in ours.pins if ra.get(_num(q.num)) == "P"]
            nn = [q for q in ours.pins if ra.get(_num(q.num)) == "N"]
            if pp and nn:
                p_end = _side(pp[0].x - nn[0].x, pp[0].y - nn[0].y)
        c, why = table_turn(prof, fam, fr, p_end) if (p_end or not pol) else (None, "which pad is the cathode / + is not known")
        if c is not None:
            out["turn"], out["rotation"] = c, "table"
            out["notes"].append(f"rotation from the {fam} 0 deg rule ({famd['zero']}) on the canonical frame")
        else:
            out["notes"].append(f"rotation unknown: {why}")
    if out["rotation"] in ("table", "unknown") and pol:
        out["review"].append(f"polarized part with rotation from {'the family table' if out['rotation'] == 'table' else 'nowhere'}"
                             ": confirm the cathode / + orientation in the assembler's placement preview")
    if out["pads"] == "incompatible":
        out["hold"].append("pads: " + "; ".join(out["reasons"]))
    if strict and out["pads"] == "risky":
        out["hold"].append("pads risky ([assembly] strict): " + "; ".join(out["reasons"]))
    if strict and out["rotation"] == "unknown":
        out["hold"].append("rotation unknown ([assembly] strict)")
    return out


def correction(a):
    """Assembly Rotation: the degrees added to a part's board angle in the CPL (= -c), or None when unknown."""
    if a.get("turn") is None:
        return None
    v = (-a["turn"]) % 360
    return int(v) if float(v).is_integer() else round(v, 2)


def check_text(a):
    """'pads identical; rotation verified' (the Assembly Check parameter)."""
    return f"pads {a['pads']}; rotation {a['rotation']}"


def evidence_text(a, date=""):
    """Where the verdict comes from, in one line (the Assembly Evidence parameter)."""
    bits = []
    if a.get("theirs"):
        bits.append(f"{a['profile']} footprint {a['theirs']}" + (f" ({a['lcsc']})" if a.get("lcsc") else ""))
    if a.get("residual") is not None:
        bits.append(f"fit {a['residual']:.3f} mm RMS")
    if a["rotation"] == "table":
        bits.append(f"family {a['family']} 0 deg rule")
    if a["rotation"] == "override":
        bits.append("profile override")
    if not bits:
        bits.append("no assembler footprint" + ("" if a.get("lcsc") else ", no LCSC number"))
    return "; ".join(bits) + (f"; {date}" if date else "")


def parameters(a, date=""):
    """The part parameters for an assessment (PARAMS order). Assembly Offset: x right, y up (the CPL's sense), mm."""
    c = correction(a)
    ox, oy = a.get("offset") or (0.0, 0.0)
    return {"Assembly Profile": a["profile"], "LCSC Part": a.get("lcsc") or "",
            "Assembly Rotation": "" if c is None else str(c),
            "Assembly Offset": f"{ox:.3f}, {-oy + 0.0:.3f}" if c is not None else "",
            "Assembly Check": check_text(a), "Assembly Evidence": evidence_text(a, date)}


def parse_offset(s):
    """'0.150, -0.200' (x right, y up, mm) -> (x, y) in our +y-down frame, else (0, 0)."""
    v = re.findall(r"-?\d+(?:\.\d+)?", str(s or ""))
    return (float(v[0]), -float(v[1])) if len(v) >= 2 else (0.0, 0.0)


# ---------------------------------------------------------------- the assembler's footprint
def lcsc_number(text):
    m = re.search(r"(?<![A-Za-z0-9])C\d{1,9}(?![0-9])", str(text or ""))
    return m.group(0) if m else ""


def fetch_theirs(lcsc, http, rules=None, prof=None):
    """(Footprint or None, note) for an LCSC number: the EasyEDA component (the footprint JLCPCB places by), through
    `http` (ecadlib's cached client). None with a note when [providers] unofficial = false, offline, or not found."""
    from . import providers as P
    prof = prof or load_profile()
    if not lcsc:
        return None, "no LCSC number"
    if (prof.get("reference_footprints") or {}).get("provider") != "easyeda":
        return None, f"profile {prof['profile']} names no reference footprints"
    if not P.unofficial_allowed(rules):
        return None, "[providers] unofficial = false: the assembler's footprint is not fetched (rotation from the table)"
    if http is None:
        return None, "no network client"
    r = http.get(P.EasyEDA.URL.format(lcsc), headers=P.EasyEDA.HEADERS)
    if not r.ok:
        return None, f"EasyEDA {lcsc}: {r.error or f'HTTP {r.status}'}"
    try:
        data = r.json()
    except ValueError:
        return None, f"EasyEDA {lcsc}: not JSON"
    if not isinstance(data, dict) or not data.get("result"):
        return None, f"EasyEDA {lcsc}: no component"
    try:
        return from_easyeda(data, f"easyeda:{lcsc}"), ""
    except AssemblyError as e:
        return None, f"EasyEDA {lcsc}: {e}"


# ---------------------------------------------------------------- overlay
def render_overlay(ours, theirs, a, out_png, scale=None):
    """Our pads (outlined, red) over the assembler's (filled, blue) after the chosen turn, in the assembler's frame,
    with pad numbers, both pin 1s, the polarity-marked ends, the verdict and the CPL rotation. Returns the path."""
    from . import visual_check as VC
    Image, ImageDraw = VC._pil()
    fit = a.get("fit")
    turn = a.get("turn") or 0.0
    if fit is None:
        class _F:
            def apply(self, x, y):
                return rot(x, y, turn)
        fit = _F()
    mine = []
    for p in ours.pads:
        if p.kind == "np_thru_hole":
            continue
        x, y = fit.apply(p.x, p.y)
        w, h = p.size(turn)
        mine.append((p, x, y, w, h))
    theirs_p = [(p, p.x, p.y) + p.size() for p in (theirs.pads if theirs else []) if p.kind != "np_thru_hole"]
    pts = [(x + s * w / 2, y + t * h / 2) for _, x, y, w, h in mine + theirs_p for s in (-1, 1) for t in (-1, 1)]
    if not pts:
        raise AssemblyError(f"{ours.name}: nothing to draw")
    box = _bbox(pts)
    span = max(box[2] - box[0], box[3] - box[1], 0.5)
    s = scale or max(40.0, min(400.0, 900 / span))
    corr = correction(a)
    lines = [f"{ours.name}  vs  {a.get('theirs') or 'no assembler footprint'}" + (f"  ({a['lcsc']})" if a.get("lcsc") else ""),
             f"pads {a['pads']}   rotation {a['rotation']}   CPL rotation = board angle + {corr if corr is not None else '?'} deg"
             + (f"   fit {a['residual']:.3f} mm" if a.get("residual") is not None else ""),
             "red outline + red label: our pads, turned as the assembler places them; red ring: our pin 1",
             "blue fill + blue label: the assembler's pads (number, pin name); blue dot: its pin 1"]
    f, fb = VC._font(14), VC._font(16, True)
    tw = max(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(t, font=fb if i < 2 else f) for i, t in enumerate(lines))
    pad_px, head = 60, 30 * len(lines) + 20
    W = max(int((box[2] - box[0]) * s + 2 * pad_px), int(tw) + 32, 720)
    H = int((box[3] - box[1]) * s + 2 * pad_px + head)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img, "RGBA")
    ox = (W - (box[2] - box[0]) * s) / 2
    V = lambda x, y: (ox + (x - box[0]) * s, head + pad_px + (y - box[1]) * s)
    for p, x, y, w, h in theirs_p:
        d.rectangle([V(x - w / 2, y - h / 2), V(x + w / 2, y + h / 2)], fill=(40, 110, 230, 110), outline=(20, 70, 180, 255))
    for p, x, y, w, h in mine:
        d.rectangle([V(x - w / 2, y - h / 2), V(x + w / 2, y + h / 2)], outline=(205, 30, 30, 255), width=3)
    lab = lambda p: p.num + (f" {p.func}" if p.func and _num(p.func) != _num(p.num) else "")
    for p, x, y, w, h in mine:
        VC._text(d, (V(x, y)[0], V(x, y)[1] - 9), lab(p), f, (150, 0, 0), "mm")
    for p, x, y, w, h in theirs_p:
        VC._text(d, (V(x, y)[0], V(x, y)[1] + 9), lab(p), f, (10, 50, 150), "mm")
    for p, x, y, w, h in theirs_p:
        if _num(p.num) in ("1", "A1"):
            c = V(x - w / 2, y - h / 2)
            d.ellipse([c[0] - 7, c[1] - 7, c[0] + 7, c[1] + 7], fill=(20, 70, 180, 255))
    for p, x, y, w, h in mine:
        if _num(p.num) in ("1", "A1"):
            c = V(x + w / 2, y + h / 2)
            d.ellipse([c[0] - 7, c[1] - 7, c[0] + 7, c[1] + 7], outline=(205, 30, 30, 255), width=3)
    for i, t in enumerate(lines):
        VC._text(d, (16, 12 + 30 * i), t, fb if i < 2 else f, (0, 0, 0))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png


# ---------------------------------------------------------------- the library side (librarian / release check)
def item_lcsc(item, prof=None):
    """A plan item's LCSC (assembler) part number: Supplier N = LCSC / JLCPCB with its Supplier Part Number N, else a
    part-number parameter the profile names; '' when none."""
    par = {p["name"]: str(p.get("value") or "").strip() for p in item.get("parameters", [])}
    for i in range(1, 10):
        if par.get(f"Supplier {i}", "").upper() in ("LCSC", "JLCPCB", "JLC") and par.get(f"Supplier Part Number {i}"):
            n = lcsc_number(par[f"Supplier Part Number {i}"])
            if n:
                return n
    pn = (prof or {}).get("part_number") or {}
    for k in [pn.get("parameter", "LCSC Part")] + list(pn.get("fields", [])):
        n = lcsc_number(par.get(k, ""))
        if n:
            return n
    return ""


def _symbol_pins(item, base):
    """{pad number: pin name} from the item's symbol (.SchLib), {} when it cannot be read."""
    from . import altium_lib as AL
    f = item["symbol"].get("file")
    p = (Path(base) / f) if f and not Path(f).is_absolute() else Path(f) if f else None
    if not p or not p.exists():
        return {}
    try:
        syms = AL.read_schlib(p)[1]
    except Exception:                               # noqa: BLE001 - unreadable: the release check says so
        return {}
    return {_num(r["num"]): r.get("name", "") for s in syms for r in s["records"] if r.get("RECORD") == "2" and r.get("num")}


def annotate_items(items, base, http, rules, log=print, date=""):
    """The librarian's assembly step ([assembly] check): every item with an LCSC number is checked against the
    assembler's footprint; its Assembly parameters are written, its findings stored as item['assembly'] (the release
    check holds on them) and its review points added to the review queue. Returns the number of items checked."""
    from . import release_check as RC
    asm = (rules or {}).get("assembly") or {}
    if not asm.get("check", True):
        return 0
    try:
        prof = load_profile(asm.get("profile") or "jlcpcb")
    except AssemblyError as e:
        log(f"  assembly: {e}")
        return 0
    n = 0
    for it in items:
        lcsc = item_lcsc(it, prof)
        if not lcsc:
            it.pop("assembly", None)
            continue
        fp_file = RC._path(base, it["footprint"].get("file"))
        kmod = RC._kicad_mod(it, base, fp_file)
        if kmod is None:
            continue
        try:
            ours = from_kicad_mod(Path(kmod).read_text(encoding="utf-8"), str(kmod))
        except (AssemblyError, OSError) as e:
            log(f"  assembly {it['name']}: {e}")
            continue
        pins = _symbol_pins(it, base)
        for p in ours.pads:
            p.func = pins.get(_num(p.num), "")
        theirs, note = fetch_theirs(lcsc, http, rules, prof)
        a = assess(ours, theirs, prof, lcsc, [it["footprint"].get("ref", "")], strict=bool(asm.get("strict")))
        if note:
            a["notes"].append(note)
        par = parameters(a, date)
        for k in PARAMS:
            it["parameters"] = [q for q in it.get("parameters", []) if q["name"] != k] + [{"name": k, "value": par[k]}]
        png = ""
        if theirs is not None:
            try:
                png = str(render_overlay(ours, theirs, a, Path(base) / "assembly" / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', it['name'])[:80]}.png")
                          .relative_to(Path(base)).as_posix())
            except Exception as e:                  # noqa: BLE001 - a picture never stops the check
                a["notes"].append(f"overlay not drawn: {e}")
        it["assembly"] = {"profile": prof["profile"], "lcsc": lcsc, "theirs": a["theirs"], "pads": a["pads"],
                          "rotation": a["rotation"], "correction": correction(a), "reasons": a["reasons"],
                          "review": a["review"], "notes": a["notes"], "family": a["family"], "overlay": png,
                          "why_unknown": next((x[len("rotation unknown: "):] for x in a["notes"] if x.startswith("rotation unknown: ")), ""),
                          "meta": (theirs.meta if theirs is not None else {})}
        lib = it.setdefault("librarian", {}).setdefault("review", [])
        lib[:] = [r for r in lib if not str(r).startswith("assembly (")]
        lib += [f"assembly ({lcsc}): {r}" for r in a["review"]]
        n += 1
        log(f"  assembly {it['name']}: {lcsc} pads {a['pads']}, rotation {a['rotation']}"
            + (f" (+{correction(a)} deg)" if correction(a) is not None else ""))
    return n


def holds(item, rules=None):
    """The release-check failures for an item's stored assembly findings, under the current [assembly] strict."""
    a = item.get("assembly") or {}
    if not a:
        return []
    strict = bool(((rules or {}).get("assembly") or {}).get("strict"))
    pic = f" [overlay {a['overlay']}]" if a.get("overlay") else ""
    out = []
    if a.get("pads") == "incompatible":
        out.append(f"pads incompatible with {a.get('theirs') or a['profile']} ({a['lcsc']}): " + "; ".join(a.get("reasons") or []) + pic)
    elif a.get("pads") == "risky" and strict:
        out.append(f"pads risky against {a.get('theirs') or a['profile']} ({a['lcsc']}): " + "; ".join(a.get("reasons") or []) + pic)
    if a.get("rotation") == "unknown" and strict:
        out.append("rotation unknown" + (f" ({a['why_unknown']})" if a.get("why_unknown") else ""))
    return out
