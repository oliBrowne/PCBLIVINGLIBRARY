"""The footprint rule book, as code: naming, metadata, layers, pads, text, 3D model, pin 1 and connectivity.

    ecadlib check path/to/*.kicad_mod                  geometry (footprint_check) + every rule below
    ecadlib check --summary path/to/Lib.pretty/        counts per rule, for running over a whole library

docs/footprint-rulebook.md is the human version: each rule id below has a section there saying what it asks, why,
and where it comes from (KLC, IPC-7351B, Altium's IPC wizard, Bogatin). footprint_check keeps the copper, silk
clearance, courtyard enclosure, numbering-direction and BGA rules; this module adds what a librarian checks next.

check(text, filename=None, symbol_pins=None) -> [(level, rule, message)], like footprint_check.check (level is
"error" or "warning"). filename lets the name rules compare the footprint name with its file. symbol_pins
({number: name} or a list of numbers) adds the connectivity and polarity rules for the symbol it is paired with.
check_pcblib(fp) runs the Altium-side rules on one footprint read back by altium_lib.read_pcblib.

Rule ids (RULES) and their levels:
  name      the name follows KLC (F2/F3) or IPC-7351 and says what the copper does: pin count, EP / MP count,
            pitch, body size, chip size code, BGA layout and pad, ball count, _ThermalVias
  meta      footprint name = file name = Value; Reference REF**; a description and keywords (KLC F9.1)
  attr      SMD parts are "smd", THT parts "through_hole", real parts are not excluded from the position file (F6.1/F7.1)
  origin    SMD: origin on the body / land centre (F6.2); THT: origin on pin 1 (F7.2)
  layers    SMD pads F.Cu + F.Mask + F.Paste; THT pads all copper + both masks, no paste; no silk on pads (F6.3/F7.4)
  padshape  SMD pads rounded rectangles, radius <= 25 % of the short side and <= 0.25 mm (F6.3, IPC-7351C);
            THT pin 1 rectangular, other pins round or oval, on polarized parts (F7.3)
  tht       annular ring >= 0.15 mm (F7.5, IPC-2221), slot clearance, no zero drills
  vias      thermal vias carry the exposed pad's number (F4.3), 0.2-0.3 mm drill, no paste over vias >= 0.3 mm (F4.4)
  text      reference on F.SilkS 1.0 mm / 0.15 mm; value and ${REFERENCE} on F.Fab, 0.5-1.0 mm, ~15 % stroke (F5.1/F5.2)
  silktext  silkscreen text 0.5-2.0 mm and off the pads, the designator off the body; also the Altium .Designator
  model     a 3D model reference, named after the footprint, STEP, scale 1 / rotation 0, a portable path (F9.3)
  margin    courtyard clearance around pads and body: 0.25 mm (0.15 under 1.5 mm), 0.5 connectors / crystals /
            canned capacitors, 1.0 BGA (F5.3)
  polarity  pin 1 / polarity is marked on the silkscreen and on the fab layer (F5.1, F5.2): the drawing is not
            the same when turned 180 degrees
  graphics  no duplicate graphic elements on one layer (F5.4)
  connect   (with symbol_pins) every symbol pin has a pad and every numbered pad a pin; an exposed pad is wired
  pinmap    (with symbol_pins) diode / LED cathode on pad 1, polarized capacitor + on pad 1 (KiCad's symbols)
  altium    (check_pcblib) SMD pads on Top Layer, THT on Multi-Layer, courtyard on its mechanical layer, an
            assembly outline, .Designator, a 3D body or a height
"""
import math, re, sys
from pathlib import Path

from . import footprint_check as FC, sexpr

EPS = 0.005
RULES = {
    "name": "Footprint name follows KLC / IPC-7351 and matches the copper",
    "meta": "Name, file name and Value agree; Reference REF**; description and keywords",
    "attr": "Footprint type: SMD / through-hole, not excluded from the position file",
    "origin": "Origin on the body centre (SMD) or on pin 1 (THT)",
    "layers": "Pad layers: SMD F.Cu/F.Mask/F.Paste, THT all copper and both masks, no paste, no silk",
    "padshape": "SMD pads are rounded rectangles; THT pin 1 is rectangular on polarized parts",
    "tht": "Annular ring and slot clearance",
    "vias": "Thermal vias share the exposed pad's number, 0.2-0.3 mm drill, no paste on them",
    "text": "Reference, value and ${REFERENCE} on the right layers at the right size",
    "silktext": "Silkscreen text 0.5-2.0 mm high and clear of copper pads; the designator outside the body",
    "model": "3D model reference named after the footprint, STEP, unscaled, portable path",
    "margin": "Courtyard clearance for the part class",
    "polarity": "Pin 1 / polarity visible on silkscreen and fab",
    "graphics": "No duplicate graphic elements",
    "connect": "Symbol pins and pads match one to one; exposed pads are wired",
    "pinmap": "Cathode / + on pad 1, as KiCad's symbols expect",
    "altium": "Altium layers: Top Layer / Multi-Layer pads, courtyard and assembly layers, designator, 3D body",
}

# rule-book errors the release check holds a part for (ecadlib's own and converted footprints; KiCad library parts
# get them as warnings). pinmap holds every part: the symbol-to-footprint pairing is ours either way.
RELEASE_HOLDS = ("name", "layers", "attr", "tht", "vias", "silktext", "pinmap")

# KLC F3.1: imperial <-> metric chip codes, and the nominal body (length x width) the metric code names
CHIP_CODES = {"01005": "0402", "0201": "0603", "0402": "1005", "0603": "1608", "0805": "2012", "1008": "2520",
              "1206": "3216", "1210": "3225", "1806": "4516", "1812": "4532", "1825": "4564", "2010": "5025",
              "2512": "6332", "2920": "7451"}
CHIP_BODY = {"0402": (0.4, 0.2), "0603": (0.6, 0.3), "1005": (1.0, 0.5), "1608": (1.6, 0.8), "2012": (2.0, 1.25),
             "2520": (2.5, 2.0), "3216": (3.2, 1.6), "3225": (3.2, 2.5), "4516": (4.5, 1.6), "4532": (4.5, 3.2),
             "4564": (4.5, 6.4), "5025": (5.0, 2.5), "6332": (6.3, 3.2), "7451": (7.4, 5.1)}
POLAR_PREFIX = re.compile(r"^(D|LED|CP|Diode|LED_RGB)_", re.I)          # two-terminal parts that have a pin 1 that matters
NONPOLAR_PREFIX = re.compile(r"^(R|C|L|F|Fuse|FB|Ferrite|Inductor|Resistor|Crystal|Jumper|NetTie|SolderJumper|"
                             r"MountingHole|TestPoint|Fiducial|Varistor|RV|Bead|Shunt|R_Array|Wire|Pad|Mounting|"
                             r"Hole|Symbol|Logo|Kelvin|PCB|Altium|Spark)[_-]", re.I)
CONNECTOR = re.compile(r"(^|_)(Connector|PinHeader|PinSocket|Pin_Header|JST|Molex|USB|TerminalBlock|Hirose|TE_|"
                       r"Samtec|Phoenix|Wuerth_.*Conn|IDC|DSUB|HDMI|RJ\d|SD|MicroSD|Barrel|BarrelJack|FFC|FPC|"
                       r"Harwin|AMASS|XT\d|Stocko|Amphenol|Jack|Plug|Socket|Header|Conn)", re.I)
CRYSTAL = re.compile(r"(^|_)(Crystal|Oscillator|Resonator|XTAL|HC49)", re.I)
CANNED = re.compile(r"^CP_Elec|^C_Elec|^CP_Radial|^C_Radial", re.I)
BGA_NAME = re.compile(r"(BGA|WLCSP|CSP|WLP|DSBGA|AQFN)", re.I)
VIRTUAL = re.compile(r"^(MountingHole|TestPoint|Fiducial|NetTie|SolderJumper|Jumper|Symbol|Logo|Kelvin|"
                     r"SolderWire|Wire|Pad|Altium|PCB|Spark|Hole|Mounting)[_-]?", re.I)
MODEL_SUFFIXES = re.compile(r"(_ThermalVias\d*|_ThermalVia|_HandSolder(ing)?|_Pad[\d.x]+mm(_HandSolder)?|"
                            r"_Mask[\d.x]+mm|_TopVias|_ViaInPad|_Hand_Soldering|_CircularHoles|_LongPads|"
                            r"_SMDSolder|_Drill[\d.]+mm|_[MNL])$")
CATHODE = {"k", "c", "cathode", "kathode", "-", "neg", "n"}
ANODE = {"a", "anode", "+", "pos", "p"}


def _f(v):
    return float(v)


def _name(tree):
    return str(tree[1]) if len(tree) > 1 and not isinstance(tree[1], list) else ""


def _prop(tree, key):
    """(value, layer, size, thickness) of a KiCad 8+ (property "Reference" ...) or a KiCad 6/7 (fp_text reference ...)."""
    for node in list(sexpr.find(tree, "property")) + list(sexpr.find(tree, "fp_text")):
        k = node[1] if node[0] == "property" else node[1]
        if node[0] == "property" and str(k).lower() != key:
            continue
        if node[0] == "fp_text" and k != key:
            continue
        val = node[2] if len(node) > 2 and not isinstance(node[2], list) else ""
        return val, _layer(node), *_font(node)
    return None


def _user_texts(tree):
    return [(n[2], _layer(n), *_font(n)) for n in sexpr.find(tree, "fp_text") if len(n) > 2 and n[1] == "user"]


def _layer(node):
    return (sexpr.first(node, "layer") or [None, None])[1]


def _font(node):
    font = sexpr.first(sexpr.first(node, "effects") or [], "font") or []
    size, thick = sexpr.first(font, "size"), sexpr.first(font, "thickness")
    return (_f(size[1]) if size else None, _f(thick[1]) if thick else None)


def _raw_pads(tree):
    """Per pad: number, type, shape, centre, unrotated size, rotation, layers, drill (w, h) or None, rratio, props."""
    out = []
    for p in sexpr.find(tree, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        drill = sexpr.first(p, "drill")
        dw = dh = None
        if drill:
            vals = [a for a in drill[1:] if not isinstance(a, list)]
            oval = bool(vals) and vals[0] == "oval"
            nums = [_f(v) for v in vals if v != "oval"]
            if nums:
                dw = nums[0]
                dh = nums[1] if oval and len(nums) > 1 else dw
        rr = sexpr.first(p, "roundrect_rratio")
        num_of = lambda *keys: next((_f(n[1]) for k in keys for n in [sexpr.first(p, k)] if n and len(n) > 1), None)
        out.append({"num": str(p[1]), "type": p[2], "shape": p[3] if len(p) > 3 else "rect",
                    "mask_margin": num_of("solder_mask_margin"), "paste_margin": num_of("solder_paste_margin"),
                    "paste_ratio": num_of("solder_paste_margin_ratio", "solder_paste_ratio"),
                    "x": _f(at[1]), "y": _f(at[2]), "rot": _f(at[3]) if len(at) > 3 else 0.0,
                    "w": _f(size[1]), "h": _f(size[2]), "layers": [str(l) for l in (sexpr.first(p, "layers") or ["l"])[1:]],
                    "drill": (dw, dh) if dw is not None else None, "rratio": _f(rr[1]) if rr else None,
                    "prop": (sexpr.first(p, "property") or [None, None])[1]})
    return out


def _bbox_pads(pads):
    if not pads:
        return None
    return (min(p["x"] - p["w"] / 2 for p in pads), min(p["y"] - p["h"] / 2 for p in pads),
            max(p["x"] + p["w"] / 2 for p in pads), max(p["y"] + p["h"] / 2 for p in pads))


def _union(*boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# ------------------------------------------------------------------ what the copper says
def survey(text_or_tree):
    """Facts the rules (and the name parser) compare against: pins, exposed pads, mounting pads, pitch, body, vias."""
    tree = sexpr.parse(text_or_tree) if isinstance(text_or_tree, str) else text_or_tree
    rects = FC._pad_rects(tree)
    raw = _raw_pads(tree)
    cu = [p for p in rects if p["copper"]]
    nums = [p["num"] for p in cu if p["num"]]
    mount = FC.mount_rx(nums)
    sig = [p for p in cu if p["num"] and not mount.fullmatch(p["num"])]
    vias = [p for p, r in zip(rects, raw) if p["copper"] and r["drill"] and max(r["drill"]) <= 0.5 and
            p["w"] <= 1.0 and p["h"] <= 1.0 and any(_inside(p, q) for q in sig if q is not p and not q["drill"]
                                                 and q["w"] * q["h"] > 1.5 * p["w"] * p["h"])]
    land = [p for p in sig if p not in vias]
    areas = sorted(p["w"] * p["h"] for p in land)
    typical = areas[len(areas) // 2] if areas else 0
    others = lambda p: [q for q in land if q is not p]
    ep = [p for p in land if len(land) > 2 and p["w"] * p["h"] > 3 * typical and _interior(p, others(p))]
    ep_nums = {p["num"] for p in ep}
    only_ep = {n for n in ep_nums if all(q in ep or q in vias for q in sig if q["num"] == n)}
    pins = sorted({p["num"] for p in sig} - only_ep, key=_natural)
    fab, _ = FC._graphics(tree, "F.Fab")
    fb = FC._bbox(fab)
    pitch = _pitches([p for p in land if p not in ep])
    big_nums = {n for n in {p["num"] for p in land} if all(q["w"] * q["h"] > 3 * typical for q in land if q["num"] == n)} \
        if len(land) > 2 else set()
    coords = {(round(p["x"], 3), round(p["y"], 3)) for p in land if p not in ep}
    spans = {round(abs(a[k] - b[k]), 3) for a in coords for b in coords for k in (0, 1) if abs(a[k] - b[k]) > EPS}
    smd = [r for r in raw if r["type"] == "smd"]
    tht = [r for r in raw if r["type"] == "thru_hole"]
    return {"tree": tree, "rects": rects, "raw": raw, "copper": cu, "signal": sig, "pins": pins, "ep": ep,
            "ep_nums": only_ep, "vias": vias, "mount": sorted({p["num"] for p in cu if p["num"] and mount.fullmatch(p["num"])}),
            "pitches": pitch, "big_nums": big_nums, "coords": coords, "spans": spans, "fab_box": fb, "pad_box": _bbox_pads(cu), "smd": smd, "tht": tht,
            "npth": [r for r in raw if r["type"] == "np_thru_hole"], "name": _name(tree)}


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def _inside(p, q):
    return abs(p["x"] - q["x"]) <= q["w"] / 2 + EPS and abs(p["y"] - q["y"]) <= q["h"] / 2 + EPS


def _interior(p, others):
    """An exposed pad sits between the other pads: its centre is inside their bounding box, not on its edge."""
    if not others:
        return False
    b = _bbox_pads(others)
    return b[0] + EPS < p["x"] < b[2] - EPS and b[1] + EPS < p["y"] < b[3] - EPS


def _pitches(pads):
    """Steps between neighbouring pads of a row or a column, and between the distinct pad positions along each axis
    (staggered and alternating rows: their pitch is the step along the row, not within one side), to 0.001 mm."""
    steps = set()
    for k in ("x", "y"):
        vals = sorted({round(p[k], 4) for p in pads})
        steps |= {round(b - a, 3) for a, b in zip(vals, vals[1:]) if b - a > EPS}
    for k, other in (("x", "y"), ("y", "x")):
        rows = {}
        for p in pads:
            rows.setdefault(round(p[other], 2), []).append(p[k])
        for vals in rows.values():
            vals = sorted(set(round(v, 4) for v in vals))
            steps |= {round(b - a, 3) for a, b in zip(vals, vals[1:]) if b - a > EPS}
    return steps


# ------------------------------------------------------------------ names
NUM = r"(\d+(?:\.\d+)?)"
# package names that carry a number of their own (SOT-23, TO-220F, SOD-123): the pin count, if any, comes after it
NUMBERED_PKG = re.compile(r"^((?:TSOT|SOT|TO|SOD|SC|DO|MO|HVSOF|SuperSOT|PowerDI|TDSON|TSDSON|DPAK|D2PAK|SMPA?|"
                          r"PG-TO|PG-HSOF|PG-DSO|PG-TSDSON)-\d{1,3}[A-Za-z]*)(?:-(\d+)(?:-(\d+))?(?:-(\d+)EP)?)?(?:_|$)")

# module names whose trailing number is a variant (ESP32-S3-WROOM-1, ESP32-WROVER-E), not a pin count
MODULE_NAME = re.compile(r"WROOM|WROVER|MODULE", re.I)


def parse_name(name):
    """What a footprint name claims, as a dict: style ("klc" / "ipc"), family, pins, ep, mp, sh, pitch (tuple),
    body (x, y), ep_size, chip (imperial, metric), layout (cols, rows), balls, pad, thermal_vias. Unknown -> {}."""
    out = {}
    ipc = _parse_ipc(name)
    if ipc:
        return ipc
    m = re.match(r"^(R|C|L|LED|D|CP|F|FB|Fuse|Inductor)_(\d{4,5})_(\d{4})Metric(?:_|$)", name)
    if m:
        out.update(style="klc", family="chip", prefix=m.group(1), chip=(m.group(2), m.group(3)), pins=2)
    else:
        m = re.match(r"^(R|C|L|LED|D|CP)_(\d{4})Metric(?:_|$)", name)
        if m:
            out.update(style="klc", family="chip", prefix=m.group(1), chip=(None, m.group(2)), pins=2)
    m = NUMBERED_PKG.match(name) or re.match(r"^([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*)-(\d+)(?:-(\d+))?"
                                              r"(?:-(\d+)EP)?(?:_|$)", name)
    generic = m and m.re is not NUMBERED_PKG
    if generic and (int(m.group(2)) < 2 or MODULE_NAME.search(m.group(1))):
        m = None                    # ESP32-S3-WROOM-1: a module variant number, not a pin count
    if m and not out and m.group(2) and len(m.group(2)) <= 3 and not re.match(r"^\d{4}", m.group(2)):
        out.update(style="klc", family=m.group(1), pins=int(m.group(2)))
        if m.group(3) and int(m.group(3)) > int(m.group(2)):
            out["pins_with_gaps"] = int(m.group(3))         # KLC F2.4: MSOP-12-16 (12 pads, numbered up to 16)
        if m.group(4):
            out["ep"] = int(m.group(4))
    m = re.search(r"_(\d+)x(\d+)(?:-(\d+)MP)?(?:-(\d+)SH)?_P" + NUM + r"mm", name)
    if m and (CONNECTOR.search(name) or "PinHeader" in name or "PinSocket" in name):
        out.update(style="klc", family=out.get("family") or "connector", layout=(int(m.group(1)), int(m.group(2))),
                   pins=int(m.group(1)) * int(m.group(2)))
        if m.group(3):
            out["mp"] = int(m.group(3))
        if m.group(4):
            out["sh"] = int(m.group(4))
    m = re.search(r"_P" + NUM + r"(?:x" + NUM + r")?mm", name)
    if m:
        out["pitch"] = tuple(float(v) for v in m.groups() if v)
    m = re.search(r"Layout(\d+)x(\d+)(?:_|$)", name)
    if m:
        out["layout"] = (int(m.group(1)), int(m.group(2)))
    if out.get("family") and not out.get("layout") and out.get("pins") is not None:
        m = re.search(r"-\d+(?:-\d+EP)?_" + NUM + r"x" + NUM + r"(?:x" + NUM + r")?mm", name)
        if m:
            out["body"] = (float(m.group(1)), float(m.group(2)))
    elif out.get("layout") and BGA_NAME.search(name):
        m = re.search(r"_" + NUM + r"x" + NUM + r"(?:x" + NUM + r")?mm_Layout", name)
        if m:
            out["body"] = (float(m.group(1)), float(m.group(2)))
    m = re.search(r"_EP" + NUM + r"x" + NUM + r"mm", name)
    if m:
        out["ep_size"] = (float(m.group(1)), float(m.group(2)))
    m = re.search(r"_Pad" + NUM + r"mm", name)
    if m and BGA_NAME.search(name):
        out["pad"] = float(m.group(1))
    if re.search(r"_ThermalVias?(?:\d*)(_|$)", name):
        out["thermal_vias"] = True
    if out:
        out.setdefault("style", "klc")
    return out


def _parse_ipc(name):
    """IPC-7351 land pattern names (what Altium's IPC Compliant Footprint Wizard writes). Dimensions are in
    hundredths of a mm (chip size codes in tenths), the suffix is the density: N nominal, M most, L least."""
    n = name.upper()
    m = re.match(r"^(RES|CAP|IND|LED|DIO|FUS|BEAD)C(P)?(\d{2})(\d{2})X(\d{2,3})([NML])$", n)
    if m:
        return {"style": "ipc", "family": "chip", "pins": 2, "body": (int(m.group(3)) / 10, int(m.group(4)) / 10),
                "height": int(m.group(5)) / 100, "density": m.group(6), "prefix": m.group(1) + (m.group(2) or "")}
    m = re.match(r"^(SOIC|SOP|SSOP|TSSOP|MSOP|TSOP|SOT|SOD|QFP|SOJ|PLCC|SON\d*)(\d{2,3})P(\d{3,4})X(\d{3,4})(?:X(\d{2,3}))?-(\d+)([NML])$", n)
    if m and m.group(1) in ("QFP", "PLCC") and m.group(5):
        return {"style": "ipc", "family": m.group(1), "pitch": (int(m.group(2)) / 100,), "span": (int(m.group(3)) / 100, int(m.group(4)) / 100),
                "height": int(m.group(5)) / 100, "pins": int(m.group(6)), "density": m.group(7)}
    if m and not m.group(5):
        return {"style": "ipc", "family": m.group(1), "pitch": (int(m.group(2)) / 100,), "span": (int(m.group(3)) / 100,),
                "height": int(m.group(4)) / 100, "pins": int(m.group(6)), "density": m.group(7)}
    m = re.match(r"^(QFN|DFN|SON|LGA|LCC|PQFN|VQFN|WQFN|UQFN|QFNS|SONS)(\d{2,3})P(\d{3,4})X(\d{3,4})X(\d{2,3})-(\d+)([NML])$", n)
    if m:
        return {"style": "ipc", "family": m.group(1), "pitch": (int(m.group(2)) / 100,),
                "body": (int(m.group(3)) / 100, int(m.group(4)) / 100), "height": int(m.group(5)) / 100,
                "pins_total": int(m.group(6)), "density": m.group(7)}         # IPC counts the thermal pad as a pin
    m = re.match(r"^(?:BGA|CSP|WLCSP)(\d+)C(\d{2,3})P(\d+)X(\d+)_(\d{3,4})X(\d{3,4})X(\d{2,3})([NML])?$", n)
    if m:
        return {"style": "ipc", "family": "BGA", "balls": int(m.group(1)), "pitch": (int(m.group(2)) / 100,),
                "layout": (int(m.group(3)), int(m.group(4))), "body": (int(m.group(5)) / 100, int(m.group(6)) / 100),
                "height": int(m.group(7)) / 100}
    return {}


def _close(a, b, tol):
    return abs(a - b) <= tol


def _name_rules(s, add):
    name = s["name"]
    claim = parse_name(name)
    if not claim:
        return claim
    pins, eps, mps = s["pins"], s["ep_nums"], s["mount"]
    fam = claim.get("family", "")
    bga = fam == "BGA" or bool(BGA_NAME.search(name)) and claim.get("layout")
    # pin count: KLC counts uniquely numbered pads without the exposed pad(s); IPC counts the thermal pad too
    if claim.get("pins_total") is not None:
        have = len(pins) + len(eps)
        if have != claim["pins_total"]:
            add("error", "name", f"the name says {claim['pins_total']} pins (IPC counts the thermal pad), the footprint "
                                 f"has {len(pins)} pins + {len(eps)} exposed pad(s)")
    elif claim.get("pins") is not None and not bga:
        want = claim["pins"]
        top = max((int(p) for p in pins if p.isdigit()), default=0)
        if claim.get("pins_with_gaps") and top != claim["pins_with_gaps"] and len(pins) != claim["pins_with_gaps"]:
            add("warning", "name", f"the name says pins are numbered up to {claim['pins_with_gaps']} (KLC F2.4), "
                                   f"the highest pad number is {top}")
        leads = [n for n in pins if n not in s["big_nums"]]            # without tabs (TO-252-3_TabPin4: 3 leads + tab)
        ok = want in (len(pins), len(leads), top) or (claim.get("ep") and not eps and len(pins) - claim["ep"] == want)
        if fam == "chip":
            if len(s["signal"]) != 2:
                add("error", "name", f"a chip footprint has 2 pads, this one has {len(s['signal'])}")
        elif not ok:
            add("error", "name", f"the name says {want} pins, the footprint has {len(pins)} numbered pads "
                                 f"({FC._ranges(pins)}){' plus exposed pad ' + ', '.join(sorted(eps)) if eps else ''}")
    if claim.get("ep") is not None and claim["ep"] != len(eps):
        add("error" if eps else "warning", "name", f"the name says {claim['ep']} exposed pad(s), the footprint has "
                                                   f"{len(eps) or 'none that could be told apart'}")
    elif claim.get("ep") is None and eps and claim.get("style") == "klc" and claim.get("pins") is not None \
            and not bga and fam not in ("chip", "connector") and not CONNECTOR.search(name):
        add("warning", "name", f"the footprint has an exposed pad ({', '.join(sorted(eps))}) but the name has no "
                               f"'-{len(eps)}EP' field (KLC F2.1)")
    if claim.get("mp") is not None and claim["mp"] != len([m for m in mps if m.upper().startswith("MP") or m == ""]) \
            and claim["mp"] != len(mps):
        add("error", "name", f"the name says {claim['mp']} mounting pad(s) (MP), the footprint has {len(mps)}")
    # pitch
    tiny_row = claim.get("layout") and min(claim["layout"]) == 1 and max(claim["layout"]) <= 2 and len(claim["layout"]) == 2 \
        and claim["layout"][1] == 1
    if claim.get("pitch") and s["pitches"] and not bga and not tiny_row:
        for p in claim["pitch"]:
            if any(_close(p, st, 0.011) for st in s["pitches"]):
                continue
            if len(s["coords"]) <= 4 and any(_close(p, st, 0.011) for st in s["spans"]):
                continue                              # 4-terminal shunts, axial parts: P is the outer pitch
            near = min(s["pitches"], key=lambda st: abs(st - p))
            add("error" if abs(near - p) > 0.11 else "warning", "name",
                f"the name says pitch {p:g} mm, the pads are {near:g} mm apart")
    if bga:
        balls = [p for p in s["signal"] if FC.BALL.match(p["num"])]
        if claim.get("pitch") and balls:
            steps = _pitches(balls)
            for p in claim["pitch"]:
                if steps and not any(_close(p, st, 0.011) for st in steps):
                    add("error", "name", f"the name says ball pitch {p:g} mm, the balls are {min(steps):g} mm apart")
        if claim.get("layout") and balls:
            px = min(claim["pitch"]) if claim.get("pitch") else min(_pitches(balls) or {1})
            py = max(claim["pitch"]) if claim.get("pitch") else px
            span = lambda k, pt: round((max(p[k] for p in balls) - min(p[k] for p in balls)) / pt) + 1
            cols, rows = span("x", px), span("y", py)
            if (cols, rows) != tuple(claim["layout"]) and (rows, cols) != tuple(claim["layout"]):
                cols, rows = span("x", py), span("y", px)
            if (cols, rows) != tuple(claim["layout"]) and (rows, cols) != tuple(claim["layout"]):
                add("error", "name", f"the name says a {claim['layout'][0]}x{claim['layout'][1]} ball layout, the "
                                     f"balls form {cols}x{rows}")
        want = claim.get("balls") if claim.get("balls") is not None else claim.get("pins")
        if want is not None and balls and len({p["num"] for p in balls}) != want:
            add("error", "name", f"the name says {want} balls, the footprint has {len({p['num'] for p in balls})}")
        if claim.get("pad") and balls:
            d = min(min(p["w"], p["h"]) for p in balls)
            if not _close(d, claim["pad"], 0.011):
                add("error", "name", f"the name says {claim['pad']:g} mm pads, the balls' lands are {d:g} mm")
    # body size against the fab outline (nominal body)
    if claim.get("body") and s["fab_box"]:
        fb = s["fab_box"]
        bw, bh = fb[2] - fb[0], fb[3] - fb[1]
        x, y = claim["body"]
        tol = lambda v: max(0.1, 0.05 * v)
        if not ((_close(bw, x, tol(x)) and _close(bh, y, tol(y))) or (_close(bw, y, tol(y)) and _close(bh, x, tol(x)))):
            add("warning", "name", f"the name says a {x:g} x {y:g} mm body, the fab outline is {bw:.2f} x {bh:.2f} mm")
    if claim.get("chip"):
        imp, met = claim["chip"]
        if imp and CHIP_CODES.get(imp) and CHIP_CODES[imp] != met:
            add("error", "name", f"imperial {imp} is metric {CHIP_CODES[imp]}, not {met} (KLC F3.1)")
        body = CHIP_BODY.get(met)
        if body and s["fab_box"]:
            fb = s["fab_box"]
            bw, bh = sorted((fb[2] - fb[0], fb[3] - fb[1]), reverse=True)
            # the length is the size code; widths vary by part type (an 0805 inductor is often 0.9-1.0 mm wide)
            if not (_close(bw, body[0], max(0.1, 0.2 * body[0])) and 0.5 * body[1] - 0.05 <= bh <= 1.3 * body[1] + 0.05):
                add("warning", "name", f"metric {met} is a {body[0]:g} x {body[1]:g} mm body, the fab outline is "
                                       f"{bw:.2f} x {bh:.2f} mm")
    if claim.get("ep_size") and s["ep"]:
        b = _bbox_pads([p for p in s["ep"]])
        ew, eh = b[2] - b[0], b[3] - b[1]
        x, y = claim["ep_size"]
        ok = lambda a, b: _close(a, b, max(0.05, 0.1 * b))
        if not ((ok(ew, x) and ok(eh, y)) or (ok(ew, y) and ok(eh, x))) and "Mask" not in name:
            add("warning", "name", f"the name says a {x:g} x {y:g} mm exposed pad, its copper is {ew:.2f} x {eh:.2f} mm")
    if claim.get("thermal_vias") and not s["vias"]:
        add("warning", "name", "the name says _ThermalVias but there are no vias in the exposed pad")
    elif s["vias"] and not claim.get("thermal_vias") and claim.get("style") == "klc":
        add("warning", "name", f"{len(s['vias'])} thermal vias but no _ThermalVias in the name (KLC F4.4)")
    return claim


def _meta_rules(tree, s, filename, add):
    name = s["name"]
    if not name:
        add("error", "meta", "the footprint has no name")
        return
    if filename:
        stem = Path(filename).name
        stem = stem[: -len(".kicad_mod")] if stem.endswith(".kicad_mod") else Path(stem).stem
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
        if stem not in (name, safe):
            add("error", "meta", f"footprint name '{name}' is not its file name '{stem}': KiCad loads it by file name, "
                                 "Altium by the name, so the two tools would link different footprints")
    if re.search(r"\s", name) or re.search(r'[<>:"/\\|?*]', name):
        add("warning", "meta", f"footprint name '{name}' has spaces or characters files and Altium refs cannot carry")
    ref = _prop(tree, "reference")
    val = _prop(tree, "value")
    if ref is None:
        add("error", "meta", "no Reference field")
    elif ref[0] not in ("REF**", "REF*", "${REFERENCE}"):
        add("warning", "meta", f"Reference field is '{ref[0]}' (KLC F9.1: REF**)")
    if val is None:
        add("warning", "meta", "no Value field")
    elif val[0] != name:
        add("warning", "meta", f"Value '{val[0]}' is not the footprint name (KLC F9.1)")
    descr = sexpr.first(tree, "descr")
    if not descr or not str(descr[1]).strip():
        add("warning", "meta", "no description (KLC F9.1: device information and a datasheet link)")
    tags = sexpr.first(tree, "tags")
    if not tags or not str(tags[1]).strip():
        add("warning", "meta", "no keywords / tags (KLC F9.1)")


def _attr_rules(tree, s, add):
    attr = sexpr.first(tree, "attr") or []
    flags = {str(a) for a in attr[1:] if not isinstance(a, list)}
    virtual = bool(VIRTUAL.match(s["name"])) or not s["copper"]
    if virtual:
        return
    if s["tht"]:
        if "through_hole" not in flags and "smd" not in flags:
            add("warning", "attr", "a through-hole footprint should have the 'Through hole' type (KLC F7.1)")
    elif s["smd"]:
        if "smd" not in flags:
            add("error", "attr", "an SMD footprint without the 'SMD' type is left out of the pick-and-place file "
                                 "(KLC F6.1): the assembler would not place it")
    if "exclude_from_pos_files" in flags and s["smd"] and not s["tht"]:
        add("error", "attr", "a real SMD part is excluded from the position files (KLC F6.1)")
    if "exclude_from_bom" in flags:
        add("warning", "attr", "the footprint is excluded from the BOM (KLC F6.1: only for non-parts)")
    if "board_only" in flags:
        add("warning", "attr", "'Not in schematic' is set (KLC F6.1)")


def _origin_rules(s, add):
    if not s["copper"] or VIRTUAL.match(s["name"]):
        return
    if s["tht"] and not s["smd"]:
        ones = [r for r in s["raw"] if r["num"] in ("1", "A1") and r["type"] == "thru_hole"]
        if ones and not any(abs(r["x"]) < EPS and abs(r["y"]) < EPS for r in ones) and not CONNECTOR.search(s["name"]) \
                and not re.search(r"_Horizontal|TO-|Bridge|Transformer|Relay|Potentiometer|Switch", s["name"]):
            add("warning", "origin", f"a through-hole footprint's origin should be pin 1 (KLC F7.2); pin 1 is at "
                                     f"({ones[0]['x']:.3f}, {ones[0]['y']:.3f})")
        return
    if s["tht"]:
        return                                        # hybrid parts: the datasheet's datum
    boxes = [b for b in (s["pad_box"], s["fab_box"]) if b]
    size = max(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    tol = max(0.05, 0.03 * size)
    centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes]
    if all(math.hypot(*c) > tol for c in centres) and not CONNECTOR.search(s["name"]):
        cx, cy = centres[-1]
        add("warning", "origin", f"the origin is {math.hypot(cx, cy):.2f} mm from the body centre ({cx:.2f}, {cy:.2f}); "
                                 "pick-and-place uses it (KLC F6.2)")


def _layer_rules(s, add):
    paste_only = [r for r in s["raw"] if r["type"] in ("smd", "connect") and not any(l.endswith(".Cu") for l in r["layers"])
                  and any("Paste" in l for l in r["layers"])]
    bad_smd, nomask, nopaste, silk, tht_paste, tht_mask = [], [], [], [], [], []
    for r in s["raw"]:
        L = set(r["layers"])
        if any(l.endswith("SilkS") or l.endswith("Silkscreen") for l in L):
            silk.append(r["num"] or "(unnumbered)")
        if r["type"] == "smd":
            cu = {l for l in L if l.endswith(".Cu")}
            if not cu:
                continue
            side = "F" if "F.Cu" in cu else "B"
            if len(cu) > 1 or "*.Cu" in cu:
                bad_smd.append(r["num"])
            spreader = side == "B" and not any("Paste" in l for l in L) and any(   # KLC F4.4: B.Cu under thermal vias
                q["num"] == r["num"] and "F.Cu" in q["layers"] for q in s["raw"])
            if f"{side}.Mask" not in L and "*.Mask" not in L and not spreader and not _opened(r, s["raw"], side + ".Mask"):
                nomask.append(r)                      # (a separate mask aperture over it is a mask-defined pad: fine)
            if f"{side}.Paste" not in L and "*.Paste" not in L and r["num"] and not spreader and \
                    not _opened(r, s["raw"], side + ".Paste"):
                nopaste.append(r)
        elif r["type"] == "thru_hole":
            if any(abs(v["x"] - r["x"]) < EPS and abs(v["y"] - r["y"]) < EPS for v in s["vias"]) or (
                    r["drill"] and max(r["drill"]) <= 0.5 and any(q["type"] == "smd" and q["num"] == r["num"] for q in s["raw"])):
                continue                              # thermal vias may be tented (KLC F4.4)
            if any("Paste" in l for l in L):
                tht_paste.append(r["num"])
            masks = {l for l in L if l.endswith(".Mask")}
            if not ("*.Mask" in masks or {"F.Mask", "B.Mask"} <= masks):
                tht_mask.append(r["num"])
    if bad_smd:
        add("error", "layers", f"SMD pads {FC._ranges(bad_smd)} are on more than one copper layer")
    if nomask:
        add("error", "layers", f"SMD pads {FC._ranges([r['num'] for r in nomask])} have no solder-mask opening: "
                               "mask would cover the copper")
    if nopaste:
        ep = {p["num"] for p in s["ep"]}
        sig = [r["num"] for r in nopaste if r["num"] not in ep]
        if sig:
            add("error", "layers", f"SMD pads {FC._ranges(sig)} have no paste: the stencil leaves them unsoldered")
        elif not paste_only:
            add("error", "layers", f"exposed pad {', '.join(sorted({r['num'] for r in nopaste}))} has no paste and no "
                                   "paste windows")
    if silk:
        add("error", "layers", f"pads {FC._ranges(silk)} are on a silkscreen layer (KLC F7.4)")
    if tht_paste:
        add("warning", "layers", f"through-hole pads {FC._ranges(tht_paste)} have paste (KLC F7.4: only for pin-in-paste)")
    if tht_mask:
        add("error", "layers", f"through-hole pads {FC._ranges(tht_mask)} are not open in both solder masks (KLC F7.4)")


def _side_margin_rules(tree, s, add):
    """Copper the same soldering pass cannot reach (one SMD pad on the other side), a mask margin that closes the
    opening, and paste margins that put solder past the copper (onto the mask or a neighbouring pad)."""
    top = lambda k: next((_f(n[1]) for n in [sexpr.first(tree, k)] if n and len(n) > 1), None)
    fp_mask, fp_paste = top("solder_mask_margin"), top("solder_paste_margin")
    fp_ratio = top("solder_paste_margin_ratio") if top("solder_paste_margin_ratio") is not None else top("solder_paste_ratio")
    pick = lambda v, d: v if v is not None else (d or 0.0)
    smd = [r for r in s["raw"] if r["type"] == "smd" and r["num"] and any(l.endswith(".Cu") for l in r["layers"])]
    sides = {}
    for r in smd:
        cu = {l for l in r["layers"] if l.endswith(".Cu")}
        if cu == {"B.Cu"} and any("Paste" in l for l in r["layers"]):   # (B.Cu without paste: a heat spreader, KLC F4.4)
            sides.setdefault("B", []).append(r["num"])
        elif cu == {"F.Cu"}:
            sides.setdefault("F", []).append(r["num"])
    if len(sides) == 2:
        few = min(sides.values(), key=len)
        add("warning", "layers", f"SMD pads {FC._ranges(few)} are on the other side of the board from the rest: "
                                 "one reflow pass cannot solder both (fine only for a card edge or a two-sided part)")
    dims = lambda r: (r["h"], r["w"]) if round(r["rot"] / 90) % 2 else (r["w"], r["h"])
    closed, apertures = [], []
    for r in s["raw"]:
        if not any(l.endswith(".Cu") for l in r["layers"]) or r["type"] == "np_thru_hole":
            continue
        w, h = dims(r)
        if min(w, h) > EPS and any(l.endswith(".Mask") for l in r["layers"]) and \
                min(w, h) + 2 * pick(r["mask_margin"], fp_mask) <= EPS:            # (zero-size copper: footprint_check)
            closed.append(r["num"] or "(unnumbered)")
        if r["type"] == "smd" and any("Paste" in l for l in r["layers"]) and abs(r["rot"] / 90 - round(r["rot"] / 90)) < 1e-6:
            m, k = pick(r["paste_margin"], fp_paste), pick(r["paste_ratio"], fp_ratio)
            gw, gh = m + k * w, m + k * h                  # KiCad: each side grows by margin + ratio x size
            apertures.append((r, (r["x"] - w / 2 - gw, r["y"] - h / 2 - gh, r["x"] + w / 2 + gw, r["y"] + h / 2 + gh),
                              max(gw, gh)))
    if closed:
        add("error", "layers", f"pads {FC._ranges(closed)}: the solder-mask margin closes the opening, mask would cover "
                               "the copper")
    over = lambda a, b: a[0] < b[2] - EPS and b[0] < a[2] - EPS and a[1] < b[3] - EPS and b[1] < a[3] - EPS
    bridge, past = [], []
    for r, box, grow in apertures:
        if grow <= EPS:
            continue
        hit = next((q for q in s["raw"] if q is not r and q["num"] != r["num"] and any(l.endswith(".Cu") for l in q["layers"])
                    and over(box, (q["x"] - dims(q)[0] / 2, q["y"] - dims(q)[1] / 2, q["x"] + dims(q)[0] / 2,
                                   q["y"] + dims(q)[1] / 2))), None)
        hit = hit or next((q for q, qb, _ in apertures if q is not r and q["num"] != r["num"] and over(box, qb)), None)
        if hit is not None:
            bridge.append(f"{r['num']} (reaches pad {hit['num']})")
        else:
            past.append(r["num"])
    if bridge:
        add("error", "paste", f"the paste opening of pad {', '.join(bridge[:4])} overlaps another pad: the stencil would "
                              "bridge them")
    if past:
        add("warning", "paste", f"pads {FC._ranges(past)}: the paste opening is bigger than the copper, solder lands on "
                                "the mask (solder balls, bridges); use a zero or negative paste margin")


def _opened(r, raw, layer):
    """Another pad (an aperture, a pad of the same number) has `layer` over part of this pad."""
    star = "*." + layer.split(".")[1]
    dims = lambda p: (p["h"], p["w"]) if round(p["rot"] / 90) % 2 else (p["w"], p["h"])
    rw, rh = dims(r)
    for q in raw:
        if q is r or not (layer in q["layers"] or star in q["layers"]):
            continue
        if q["num"] and q["num"] == r["num"]:
            return True
        qw, qh = dims(q)
        if abs(q["x"] - r["x"]) < (qw + rw) / 2 - EPS and abs(q["y"] - r["y"]) < (qh + rh) / 2 - EPS:
            return True
    return False


def _polarized(s, claim):
    name = s["name"]
    if VIRTUAL.match(name) or NONPOLAR_PREFIX.match(name):
        return False
    if POLAR_PREFIX.match(name):
        return True
    return len(s["pins"]) >= 3 or bool(CONNECTOR.search(name) and len(s["pins"]) >= 2)


def _padshape_rules(s, claim, add):
    ep = {id(p) for p in s["ep"]}
    rect_pads = []
    big_r = []
    land = {(round(p["x"], 4), round(p["y"], 4), p["num"]) for p in s["signal"] if id(p) not in ep}
    for r in s["raw"]:
        if r["type"] != "smd" or not r["num"] or (round(r["x"], 4), round(r["y"], 4), r["num"]) not in land:
            continue
        if r["shape"] == "rect":
            rect_pads.append(r["num"])
        if r["shape"] == "roundrect" and r["rratio"] is not None:
            rad = r["rratio"] * min(r["w"], r["h"])
            if r["rratio"] > 0.25 + 1e-6 or rad > 0.25 + EPS:
                big_r.append((r["num"], r["rratio"], rad))
    if rect_pads and len(rect_pads) == len(land):
        add("warning", "padshape", "SMD pads are sharp rectangles; KLC F6.3 / IPC-7351C use rounded rectangles "
                                   "(radius 25 % of the short side, at most 0.25 mm)")
    if big_r:
        n, rr, rad = big_r[0]
        add("warning", "padshape", f"pad {n} corner radius {rad:.3f} mm (ratio {rr:g}); IPC-7351C: <= 25 % of the "
                                   f"short side and <= 0.25 mm{f' ({len(big_r)} pads)' if len(big_r) > 1 else ''}")
    tht = [r for r in s["tht"] if r["num"] and not FC.mount_rx([r["num"]]).fullmatch(r["num"])]
    if len({r["num"] for r in tht}) >= 2 and _polarized(s, claim) and not s["smd"]:
        ones = [r for r in tht if r["num"] in ("1", "A1")]
        if ones and not any(r["shape"] in ("rect", "roundrect") for r in ones):
            add("warning", "padshape", "pin 1 of a polarized through-hole part should be the rectangular pad (KLC F7.3)")
        squares = sorted({r["num"] for r in tht if r["shape"] in ("rect",) and r["num"] not in ("1", "A1")}, key=_natural)
        if ones and squares and any(r["shape"] in ("rect", "roundrect") for r in ones):
            add("warning", "padshape", f"pads {FC._ranges(squares)} are rectangular too: only pin 1 should be (KLC F7.3)")


def _tht_rules(s, add):
    thin, slot, zero = [], [], []
    for r in s["tht"]:
        if not r["drill"]:
            continue
        dw, dh = r["drill"]
        if not dw:
            zero.append(r["num"])
            continue
        if r["shape"] == "custom":
            continue
        w, h = r["w"], r["h"]
        ring = min((w - dw) / 2, (h - dh) / 2) if dw != dh else (min(w, h) - dw) / 2
        if ring < 0.15 - EPS:
            thin.append((ring, r["num"]))
        if dw != dh and min(dw, dh) > 0 and max(dw, dh) / min(dw, dh) < 1.0 + 1e-6:
            slot.append(r["num"])
    if zero:
        add("error", "tht", f"through-hole pads {FC._ranges(zero)} have a zero drill")
    if thin:
        ring, n = min(thin)
        lvl = "error" if ring < 0.05 else "warning"
        add(lvl, "tht", f"annular ring {max(ring, 0):.3f} mm on pad {n}{f' and {len(thin) - 1} more' if len(thin) > 1 else ''} "
                        "(KLC F7.5 / IPC-2221: >= 0.15 mm)")


def _via_rules(s, add):
    if not s["vias"]:
        return
    pads = {id(p) for p in s["vias"]}
    for v in s["vias"]:
        hosts = [q for q in s["signal"] if id(q) not in pads and _inside(v, q) and not q["drill"]]
        host = hosts[0] if hosts else None          # (hosts: copper the via sits in)
        if host and all(v["num"] != q["num"] for q in hosts):
            add("error", "vias", f"a thermal via at ({v['x']:.2f}, {v['y']:.2f}) is numbered '{v['num']}', its pad "
                                 f"'{host['num']}': connected copper must share a number (KLC F4.3)")
            break
    drills = [r["drill"][0] for r in s["raw"] if r["drill"] and any(abs(r["x"] - v["x"]) < EPS and abs(r["y"] - v["y"]) < EPS
                                                                       for v in s["vias"])]
    off = [d for d in drills if not 0.2 - EPS <= d <= 0.3 + EPS]
    if off:
        add("warning", "vias", f"thermal via drill {min(off):g}-{max(off):g} mm (KLC F4.4: 0.2-0.3 mm unless the "
                               "datasheet says otherwise)")
    windows = [r for r in s["raw"] if any("Paste" in l for l in r["layers"]) and r["type"] in ("smd", "connect")]
    for v, d in zip(s["vias"], drills):
        if d >= 0.3 - EPS and any(abs(v["x"] - w["x"]) < w["w"] / 2 - EPS and abs(v["y"] - w["y"]) < w["h"] / 2 - EPS
                                  for w in windows):
            add("warning", "vias", f"paste covers a {d:g} mm thermal via at ({v['x']:.2f}, {v['y']:.2f}): solder wicks "
                                   "into it (KLC F4.4)")
            break


def _text_rules(tree, s, add):
    ref = _prop(tree, "reference")
    if ref:
        _, layer, size, thick = ref
        if layer not in ("F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen"):
            add("warning", "text", f"the reference is on {layer}, not the silkscreen (KLC F5.1)")
        elif size and (abs(size - 1.0) > 0.01 or (thick and abs(thick - 0.15) > 0.01)):
            add("warning", "text", f"reference text {size:g} mm / {thick or 0:g} mm (KLC F5.1: 1.00 mm, 0.15 mm stroke)")
    val = _prop(tree, "value")
    if val and val[1] not in ("F.Fab", "B.Fab"):
        add("warning", "text", f"the value is on {val[1]}, not F.Fab (KLC F5.2)")
    fab_ref = [t for t in _user_texts(tree) if t[0] in ("${REFERENCE}", "%R") and t[1] in ("F.Fab", "B.Fab")]
    if not fab_ref and s["copper"] and not VIRTUAL.match(s["name"]):
        add("warning", "text", "no ${REFERENCE} on F.Fab: the assembly drawing has no designators (KLC F5.2)")
    for t in fab_ref[:1] + ([val] if val and val[1] in ("F.Fab", "B.Fab") else []):
        _, _, size, thick = t
        if size and not 0.25 - EPS <= size <= 1.0 + EPS:
            add("warning", "text", f"fab text {size:g} mm (KLC F5.2: 0.5-1.0 mm)")
            break
        if size and thick and not 0.10 <= thick / size <= 0.2 + 1e-6:
            add("warning", "text", f"fab text stroke {thick:g} mm is {thick / size:.0%} of its {size:g} mm size (KLC F5.2: ~15 %)")
            break


SILK = ("F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen")
SILK_TEXT = (0.5, 2.0)        # mm: below this a board house cannot print it, above it the text swamps the part
NOMINAL_REF = 3               # characters a designator shows on a board ("U12"), whatever the library string says


def text_box(x, y, size, n, rot=0.0, just=0.5):
    """Box (x0, y0, x1, y1) of n stroke characters of height size whose anchor (x, y) sits at fraction just
    (0 left, 0.5 centre, 1 right) of the width and at the middle of the height, rotated by a multiple of 90 degrees."""
    from .altium_lib import CHAR_W
    w = CHAR_W * size * n
    x0, x1, y0, y1 = -w * just, w * (1 - just), -size / 2, size / 2
    if round(rot / 90) % 2:
        x0, x1, y0, y1 = y0, y1, x0, x1
    if round(rot / 90) % 4 in (1, 2):
        x0, x1 = -x1, -x0
    if round(rot / 90) % 4 in (2, 3):
        y0, y1 = -y1, -y0
    return (x + x0, y + y0, x + x1, y + y1)


def _boxes_overlap(a, b, gap=0.0):
    return a[0] < b[2] - gap and b[0] < a[2] - gap and a[1] < b[3] - gap and b[1] < a[3] - gap


def _silk_texts(tree):
    """Visible silkscreen texts: (kind, layer, size, box) with the box in KiCad coordinates (y down)."""
    out = []
    for n in list(sexpr.find(tree, "property")) + list(sexpr.find(tree, "fp_text")):
        layer, at = _layer(n), sexpr.first(n, "at")
        eff = sexpr.first(n, "effects") or []
        hidden = "hide" in n or "hide" in eff or any(isinstance(c, list) and c[0] == "hide" and c[1:2] != ["no"]
                                                      for c in list(n) + list(eff))
        if layer not in SILK or not at or hidden:
            continue
        kind = (str(n[1]) if n[0] == "fp_text" else str(n[1]).lower())
        val = str(n[2]) if len(n) > 2 and not isinstance(n[2], list) else ""
        if kind not in ("reference", "value", "user"):
            continue
        size = _font(n)[0] or 1.0
        ref = kind == "reference" or val in ("${REFERENCE}", "%R")
        just = sexpr.first(eff, "justify") or []
        frac = 0.0 if "left" in just else 1.0 if "right" in just else 0.5
        rot = _f(at[3]) if len(at) > 3 and at[3] != "unlocked" else 0.0
        out.append(("reference" if ref else kind, layer, size,
                    text_box(_f(at[1]), _f(at[2]), size, NOMINAL_REF if ref else max(len(val), 1), rot, frac)))
    return out


def _silktext_rules(tree, s, add):
    lo, hi = SILK_TEXT
    for kind, layer, size, box in _silk_texts(tree):
        if not lo - EPS <= size <= hi + EPS:
            add("error", "silktext", f"{kind} text on {layer} is {size:g} mm high (readable silkscreen text is "
                                     f"{lo:g}-{hi:g} mm; KLC F5.1 uses 1.0 mm)")
        side = "F" if layer.startswith("F.") else "B"
        hit = [p["num"] or "?" for p in s["rects"] if (p["copper"] or p["hole"]) and
               any(l in (f"{side}.Cu", "*.Cu") for l in p["layers"]) and
               _boxes_overlap(box, (p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2), 0.01)]
        if hit:
            add("error", "silktext", f"{kind} text on {layer} lies over pad{'s' if len(hit) > 1 else ''} "
                                     f"{FC._ranges(hit)}: the board house clips it off the copper (KLC F5.1)")
        fb = s["fab_box"]
        if kind == "reference" and fb and not hit and _boxes_overlap(box, fb, 0.01) and s["copper"]:
            add("warning", "silktext", f"the designator on {layer} sits over the part body: it is hidden once the part "
                                       "is placed (KLC F5.1 puts it outside the body)")
    if any(kind == "reference" for kind, *_ in _silk_texts(tree)) or not s["copper"]:
        return
    # no designator on silk: the Altium export still puts .Designator on Top Overlay where the reference was
    from . import altium_lib as AL
    texts = [(t, x, y, h, rot, True) for t, x, y, h, _, rot, layer in AL.texts_from_kicad(tree) if layer == AL.TOP_OVERLAY]
    flip = lambda b: (b[0], -b[3], b[2], -b[1])
    pads = [(p["num"] or "?", flip((p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)),
             None if any(l == "*.Cu" for l in p["layers"]) else "F.Cu" in p["layers"]) for p in s["rects"] if p["copper"]]
    body = flip(s["fab_box"]) if s["fab_box"] else flip(s["pad_box"]) if s["pad_box"] else None
    for lvl, msg in overlay_text_issues(texts, pads, body):
        add(lvl, "silktext", f"in the Altium export: {msg}")


def _model_rules(tree, s, add, require=False):
    models = list(sexpr.find(tree, "model"))
    if not models:
        if s["copper"] and not VIRTUAL.match(s["name"]):
            add("error" if require else "warning", "model", "no 3D model reference (KLC F9.3: every real part has one, "
                                                           "even before the model exists)")
        return
    m = models[0]
    path = str(m[1])
    fname = re.split(r"[/\\]", path)[-1]
    stem, ext = (fname.rsplit(".", 1) + [""])[:2]
    base = MODEL_SUFFIXES.sub("", s["name"])
    while MODEL_SUFFIXES.search(base):
        base = MODEL_SUFFIXES.sub("", base)
    if stem not in (s["name"], base) and not s["name"].startswith(stem) and not base.startswith(stem):
        add("warning", "model", f"3D model '{fname}' is not named after the footprint (KLC F9.3)")
    if ext.lower() not in ("step", "stp", "wrl"):
        add("error", "model", f"3D model '{fname}' is not a STEP or VRML file")
    elif ext.lower() == "wrl":
        add("warning", "model", f"3D model '{fname}' is VRML; KLC F9.3 references the .step (Altium cannot use .wrl)")
    if re.match(r"^([A-Za-z]:[/\\]|/|~)", path):
        add("error", "model", f"3D model path '{path}' is absolute: it breaks on every other machine (use "
                              "${KICADn_3DMODEL_DIR}, ${KIPRJMOD} or a library variable)")
    for key, want in (("scale", 1.0), ("rotate", 0.0)):
        node = sexpr.first(sexpr.first(m, key) or [], "xyz")
        if node and any(abs(_f(v) - want) > 1e-6 for v in node[1:4]):
            add("warning", "model", f"3D model {key} is {' '.join(node[1:4])} (KLC F9.3: {'1 1 1' if want else '0 0 0'})")
    off = sexpr.first(sexpr.first(m, "offset") or [], "xyz")
    if off and any(abs(_f(v)) > 1e-6 for v in off[1:3]):
        add("warning", "model", f"3D model offset {' '.join(off[1:4])} mm (KLC F9.3: the model is aligned in the file)")


def _margin_rules(tree, s, add):
    cb = sexpr.courtyard_bbox(tree)
    if not cb or not s["copper"]:
        return
    ext = _union(_bbox_pads([p for p in s["rects"] if p["copper"] or p["hole"]]), s["fab_box"])
    name = s["name"]
    w, h = ext[2] - ext[0], ext[3] - ext[1]
    if BGA_NAME.search(name) and any(FC.BALL.match(p["num"]) for p in s["signal"]):
        need, why = 1.0, "BGA"
    elif CONNECTOR.search(name):
        need, why = 0.5, "connector"
    elif CRYSTAL.search(name):
        need, why = 0.5, "crystal"
    elif CANNED.search(name):
        need, why = 0.5, "canned capacitor"
    elif min(w, h) < 1.5 or (s["fab_box"] and min(s["fab_box"][2] - s["fab_box"][0], s["fab_box"][3] - s["fab_box"][1]) < 1.0):
        need, why = 0.15, "part under 1.5 mm"
    else:
        need, why = 0.25, "default"
    gap = min(ext[0] - cb[0], ext[1] - cb[1], cb[2] - ext[2], cb[3] - ext[3])
    words = " ".join(str((sexpr.first(tree, k) or ["", ""])[1]) for k in ("descr", "tags"))
    if re.search(r"density[- ]C\b|least density|IPC-7351B?\s+\w+\s+density\s+C", words, re.I) or re.search(r"_?L$", name) \
            and parse_name(name).get("density") == "L":
        need, why = 0.1, "IPC density C (least)"             # IPC-7351B's least-material courtyard excess
    floor = {"BGA": 0.25, "connector": 0.25, "crystal": 0.25, "canned capacitor": 0.25}.get(why, need)
    if -EPS <= gap < floor - 0.011:                   # (below zero: footprint_check's enclosure rule reports it)
        add("warning", "margin", f"courtyard is {gap:.2f} mm from the pads / body ({why}: KLC F5.3 asks {need:g} mm)")


def _sym(segs, polys, tol=0.02):
    """True when a layer's drawing is the same turned 180 degrees about the origin (so it cannot show pin 1)."""
    keys = lambda a, b: (round(a[0] / tol), round(a[1] / tol), round(b[0] / tol), round(b[1] / tol))
    have = set()
    for s in segs:
        have.add(keys((s[0], s[1]), (s[2], s[3])))
        have.add(keys((s[2], s[3]), (s[0], s[1])))
    near = lambda k: any((k[0] + i, k[1] + j, k[2] + m, k[3] + n) in have for i in (-1, 0, 1) for j in (-1, 0, 1)
                         for m in (-1, 0, 1) for n in (-1, 0, 1))
    for s in segs:
        if not near(keys((-s[0], -s[1]), (-s[2], -s[3]))):
            return False
    fills = [((sum(x for x, _ in p) / len(p)), (sum(y for _, y in p) / len(p))) for p, f in polys if f]
    for cx, cy in fills:
        if not any(math.hypot(cx + dx, cy + dy) < 0.05 for dx, dy in fills):
            return False
    return True


def _polarity_rules(tree, s, claim, add):
    if not _polarized(s, claim) or not s["copper"]:
        return
    if BGA_NAME.search(s["name"]) and claim.get("layout"):
        return                                        # footprint_check's BGA rules mark A1
    silk, spolys = FC._graphics(tree, "F.SilkS")
    fab, fpolys = FC._graphics(tree, "F.Fab")
    ones = [p for p in s["signal"] if p["num"] in ("1", "A1")]
    if POLAR_PREFIX.match(s["name"]) and s["pins"] == ["1", "2"]:
        twos = [p for p in s["signal"] if p["num"] == "2" and p not in s["vias"]]
        ones_ = [p for p in ones if p not in s["vias"]]
        if ones_ and twos:
            mid = lambda g: (sum(p["x"] for p in g) / len(g), sum(p["y"] for p in g) / len(g))
            (x1, y1), (x2, y2) = mid(ones_), mid(twos)
            if x1 > x2 + EPS or (abs(x1 - x2) <= EPS and y1 > y2 + EPS):
                add("error", "polarity", f"pad 1 (cathode / +) is {'right of' if x1 > x2 + EPS else 'below'} pad 2: KiCad "
                                         "puts pad 1 of a polarized 2-terminal part on the left (or top), under the "
                                         "polarity mark (KLC F4.2); pads 1 and 2 look swapped")
    centred = ones and all(abs(p["x"]) < EPS and abs(p["y"]) < EPS for p in ones)
    if centred:
        return
    if not silk and not spolys:
        add("warning", "polarity", "no silkscreen at all: pin 1 / polarity is not marked (KLC F5.1)")
    elif _sym(silk, spolys):
        add("warning", "polarity", "the silkscreen is the same turned 180 degrees: pin 1 / polarity is not marked "
                                   "(KLC F5.1)")
    if fab and _sym(fab, fpolys):
        add("warning", "polarity", "the fab outline is the same turned 180 degrees: chamfer the pin-1 corner "
                                   "(KLC F5.2: 1 mm or 25 % of the body)")


def _graphics_rules(tree, add):
    seen = {}
    dup = {}
    for kind in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
        for g in sexpr.find(tree, kind):
            layer = _layer(g)
            pts = []
            for k in ("start", "mid", "end", "center"):
                n = sexpr.first(g, k)
                if n:
                    pts.append((round(_f(n[1]), 3), round(_f(n[2]), 3)))
            pts += [(round(_f(xy[1]), 3), round(_f(xy[2]), 3)) for xy in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
            key = (kind, layer, tuple(sorted(pts)))
            if key in seen:
                dup[layer] = dup.get(layer, 0) + 1
            seen[key] = True
    for layer, n in sorted(dup.items()):
        add("warning", "graphics", f"{n} duplicate graphic element(s) on {layer} (KLC F5.4)")


# ------------------------------------------------------------------ connectivity
def _pin_map(symbol_pins):
    if isinstance(symbol_pins, dict):
        return {str(k): str(v or "") for k, v in symbol_pins.items()}
    return {str(k): "" for k in symbol_pins}


def connect_rules(s, symbol_pins, add):
    """Symbol pins vs pads (rules connect and pinmap)."""
    pins = _pin_map(symbol_pins)
    pads = {p["num"] for p in s["copper"] if p["num"]}
    mount = set(s["mount"])
    numbered = pads - mount
    missing = sorted(set(pins) - pads, key=_natural)
    extra = sorted(numbered - set(pins), key=_natural)
    if missing:
        hint = ""
        if s["ep_nums"] and any(re.fullmatch(r"(EP|PAD|TAB|THERMAL|GND_?PAD|EPAD|PAD\d*)", m, re.I) for m in missing):
            hint = f" (the exposed pad is numbered {', '.join(sorted(s['ep_nums'], key=_natural))}: number the symbol pin the same)"
        add("error", "connect", f"symbol pins without a pad: {FC._ranges(missing)}{hint}")
    ep_extra = [n for n in extra if n in s["ep_nums"]]
    other = [n for n in extra if n not in s["ep_nums"]]
    if other:
        add("error", "connect", f"pads without a symbol pin: {FC._ranges(other)} (they would be left unconnected)")
    if ep_extra:
        add("error", "connect", f"exposed pad {', '.join(ep_extra)} has no symbol pin: it would float. It is the part's "
                                "heat path and usually its ground return (datasheet; Bogatin: every return path engineered)")
    # polarity: KiCad's D / LED symbols have the cathode on pin 1, CP_ has + on pin 1
    name = s["name"]
    if re.match(r"^(D|LED)_", name) and len(pins) == 2 and {"1", "2"} <= set(pins):
        p1, p2 = pins["1"].strip().lower(), pins["2"].strip().lower()
        if p1 in ANODE and p2 in CATHODE:
            add("error", "pinmap", f"the symbol puts the anode on pin 1 but {name.split('_')[0]} footprints put the cathode "
                                   "on pad 1 (the marked end): the part would be placed backwards")
    if re.match(r"^CP_", name) and len(pins) == 2 and {"1", "2"} <= set(pins):
        p1 = pins["1"].strip().lower()
        if p1 in ("-", "neg", "n", "minus", "k", "cathode"):
            add("error", "pinmap", "the symbol puts - on pin 1 but polarized-capacitor footprints mark + on pad 1: the "
                                   "capacitor would be placed backwards")


# ------------------------------------------------------------------ driver
def check(text, filename=None, symbol_pins=None, require_model=False, geometry=True):
    """footprint_check (geometry) + the rule book. -> [(level, rule, message)]."""
    issues = list(FC.check(text)) if geometry else []
    add = lambda lvl, rule, msg: issues.append((lvl, rule, msg))
    tree = sexpr.parse(text)
    s = survey(tree)
    claim = _name_rules(s, add)
    _meta_rules(tree, s, filename, add)
    _attr_rules(tree, s, add)
    _origin_rules(s, add)
    _layer_rules(s, add)
    _side_margin_rules(tree, s, add)
    _padshape_rules(s, claim, add)
    _tht_rules(s, add)
    _via_rules(s, add)
    _text_rules(tree, s, add)
    _silktext_rules(tree, s, add)
    _model_rules(tree, s, add, require=require_model)
    _margin_rules(tree, s, add)
    _polarity_rules(tree, s, claim, add)
    _graphics_rules(tree, add)
    if symbol_pins is not None:
        connect_rules(s, symbol_pins, add)
    return issues


# ------------------------------------------------------------------ Altium
def overlay_text_issues(texts, pads, body=None):
    """Altium overlay text, y up, in mm: texts [(string, x, y, height, rotation, on top)] anchored at the bottom-left
    corner as Altium anchors stroke text; pads [(number, (x0, y0, x1, y1), on top / None for Multi-Layer)].
    -> [(level, message)]: height outside SILK_TEXT, text over a pad, the designator over the body."""
    from .altium_lib import CHAR_W
    lo, hi = SILK_TEXT
    out = []
    for name, x, y, h, rot, on_top in texts:
        if not lo - EPS <= h <= hi + EPS:
            out.append(("error", f"overlay text '{name}' is {h:g} mm high (readable silkscreen text is {lo:g}-{hi:g} mm)"))
        a = math.radians(rot)
        corners = [(u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a))
                   for u in (0, CHAR_W * h * (NOMINAL_REF if name == ".Designator" else len(name))) for v in (0, h)]
        box = (x + min(c[0] for c in corners), y + min(c[1] for c in corners),
               x + max(c[0] for c in corners), y + max(c[1] for c in corners))
        hit = [n for n, b, side in pads if side in (None, on_top) and _boxes_overlap(box, b, 0.01)]
        if hit:
            out.append(("error", f"overlay text '{name}' lies over pad{'s' if len(hit) > 1 else ''} {FC._ranges(hit)}"))
        elif name == ".Designator" and body and _boxes_overlap(box, body, 0.01):
            out.append(("warning", "the .Designator sits over the part body: it is hidden once the part is placed"))
    return out


def pcblib_text_issues(fp):
    """overlay_text_issues on one footprint read back by altium_lib.read_pcblib; the body is the box through the
    pad centres. -> [(level, message)]"""
    from . import altium_lib as AL
    pads = [r for r in fp["records"] if r["type"] == "pad"]
    top = lambda layer: layer in (AL.TOP, AL.TOP_OVERLAY)
    boxes = []
    for p in pads:
        w, h = AL.to_mm(p["w"]), AL.to_mm(p["h"])
        if round((p.get("rot") or 0) / 90) % 2:
            w, h = h, w
        x, y = AL.to_mm(p["x"]), AL.to_mm(p["y"])
        boxes.append((p["designator"] or "?", (x - w / 2, y - h / 2, x + w / 2, y + h / 2),
                      None if p["layer"] == AL.MULTI else top(p["layer"])))
    texts = [(t.get("text") or "?", AL.to_mm(t["x"]), AL.to_mm(t["y"]), AL.to_mm(t["h"]), t.get("rot") or 0.0,
              top(t["layer"])) for t in fp["records"] if t["type"] == "text" and t.get("layer") in (AL.TOP_OVERLAY, AL.BOTTOM_OVERLAY)]
    ctr = [(AL.to_mm(p["x"]), AL.to_mm(p["y"])) for p in pads]
    body = (min(c[0] for c in ctr), min(c[1] for c in ctr), max(c[0] for c in ctr), max(c[1] for c in ctr)) if ctr else None
    return overlay_text_issues(texts, boxes, body)


def check_pcblib(fp, rules=None, pin1=None):
    """Altium-side rules on one footprint from altium_lib.read_pcblib: [(level, "altium", message)]."""
    from . import altium_lib as AL, rules as RU
    rules = rules or RU.DEFAULTS
    out = []
    add = lambda lvl, msg: out.append((lvl, "altium", msg))
    recs = fp["records"]
    pads = [r for r in recs if r["type"] == "pad"]
    crt = RU.layer_id(rules["layers"]["courtyard"])
    asm = RU.layer_id(rules["layers"]["assembly"])
    if not pads:
        add("error", "no pads")
    smd_wrong = [p["designator"] for p in pads if not p["hole"] and p["layer"] not in (AL.TOP, AL.BOTTOM)]
    tht_wrong = [p["designator"] for p in pads if p["hole"] and p["plated"] and p["layer"] != AL.MULTI]
    if smd_wrong:
        add("error", f"SMD pads {FC._ranges(smd_wrong)} are not on Top Layer / Bottom Layer")
    if tht_wrong:
        add("error", f"plated through-hole pads {FC._ranges(tht_wrong)} are not on Multi-Layer")
    graphics = lambda layer: [r for r in recs if r["type"] in ("track", "arc", "region") and r.get("layer") == layer]
    if not graphics(crt):
        add("error", f"no courtyard on {rules['layers']['courtyard']}")
    if not graphics(asm):
        add("warning", f"no assembly outline on {rules['layers']['assembly']}")
    texts = [r.get("text", "") for r in recs if r["type"] == "text"]
    if ".Designator" not in texts:
        add("warning", "no .Designator special string")
    elif pads and not any(r["type"] == "text" and r.get("text") == ".Designator" and r.get("layer") == asm for r in recs):
        add("warning", f"no .Designator on {rules['layers']['assembly']}: the assembly drawing has no designators (KLC F5.2)")
    for lvl, msg in pcblib_text_issues(fp):
        add(lvl, msg)
    if not any(r["type"] == "body" for r in recs) and not fp["params"].get("HEIGHT"):
        add("warning", "no 3D body and no height")
    nums = {p["designator"] for p in pads}
    if not set(pin1 or ("1", "A1")) & nums and not any(FC.BALL.match(n) for n in nums):
        add("error", "no pin 1 pad")
    if re.search(r'[<>:"/\\|?*]', fp["name"]):
        add("warning", f"footprint name '{fp['name']}' has characters Windows file names cannot carry")
    return out


def summary(results):
    """{(level, rule): count} and the number of files with an error, over [(path, issues)]."""
    counts, bad = {}, 0
    for _, issues in results:
        for lvl, rule, _ in issues:
            counts[(lvl, rule)] = counts.get((lvl, rule), 0) + 1
        bad += any(l == "error" for l, _, _ in issues)
    return counts, bad


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="ecadlib check", description="Check KiCad footprints against the ecadlib rule "
                                 "book (docs/footprint-rulebook.md): geometry, naming, layers, text, 3D, pin 1")
    ap.add_argument("files", nargs="+", help=".kicad_mod files or .pretty folders")
    ap.add_argument("--geometry-only", action="store_true", help="only footprint_check's copper / silk / courtyard rules")
    ap.add_argument("--summary", action="store_true", help="counts per rule instead of every finding")
    ap.add_argument("--errors", action="store_true", help="print errors only")
    a = ap.parse_args(argv)
    files = []
    for f in a.files:
        p = Path(f)
        files += sorted(p.glob("*.kicad_mod")) if p.is_dir() else [p]
    results, bad = [], 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        issues = FC.check(text) if a.geometry_only else check(text, filename=f.name)
        if a.errors:
            issues = [i for i in issues if i[0] == "error"]
        results.append((f, issues))
        bad += any(l == "error" for l, _, _ in issues)
        if not a.summary:
            print(f"{'OK  ' if not issues else 'WARN' if all(l == 'warning' for l, _, _ in issues) else 'FAIL'} {f}")
            for lvl, rule, msg in issues:
                print(f"     {lvl:<7} {rule:<9} {msg}")
    if a.summary:
        counts, nbad = summary(results)
        print(f"{len(files)} footprints, {nbad} with errors, {sum(1 for _, i in results if not i)} with no findings")
        for (lvl, rule), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {n:>6}  {lvl:<7} {rule}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
