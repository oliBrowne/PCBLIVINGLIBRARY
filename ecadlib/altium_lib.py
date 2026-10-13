"""Altium .PcbLib (footprints) and .SchLib (symbols) writer, pure Python (+ a minimal reader for checks).

    python3 -m ecadlib.altium_lib pcblib ecadlib/package_specs/*.json -o out/altium_models/PcbLib
    python3 -m ecadlib.altium_lib schlib examples/symbols/*.json -o out/altium_models/SchLib
    python3 -m ecadlib.altium_lib dump out/altium_models/PcbLib/SOIC-8_3.9x4.9mm_P1.27mm.PcbLib

One footprint per .PcbLib / one symbol per .SchLib by default (what altium365.py uploads), but
write_pcblib/write_schlib take lists. Files are OLE2 compound files written by ecadlib.cfb.

Portions are ported from AltiumSharp (github.com/issus/AltiumSharp, Apache-2.0, Copyright the
AltiumSharp authors): the PcbLib/SchLib stream layout (PcbLibWriter.cs, SchLibWriter.cs), the
primitive record layouts (13-byte common header, 49-byte track, 50-byte fill, region, 202-byte pad
main block with its 141-byte extended-tail template, 596-byte pad size/shape block, the SchLib pin
record) and v7 layer ids. ecadlib/assets/pcblib_template.json.gz holds the Library/Data board
header and FileVersionInfo of an Altium-saved PcbLib from AltiumSharp's TestData (see NOTICE).
Field values were cross-checked against real Altium files (README "Altium library writer").

Conventions (mirroring Altium-made libraries in AltiumSharp's TestData):
  Top Layer (1) SMD pads; Top Overlay (33) silkscreen; Mechanical 13 (69) fab/body outline;
  Mechanical 15 (71) courtyard; EP paste windows as fills on Top Paste (35).
  Units: 1 mil = 10000 internal units, so 1 mm = 10^8/254 units (rounded to the nearest unit,
  2.54 nm). KiCad's Y axis points down, Altium's up: y is negated on import.
Schematic units: 1 = 10 mil (DXP); pins on a 100 mil (10 unit) grid."""
import argparse, functools, gzip, hashlib, json, re, struct, sys, time, uuid
from fractions import Fraction
from pathlib import Path

from . import cfb, sexpr

# ---------------------------------------------------------------- units & low-level encoders
TOP, MID1, BOTTOM, TOP_OVERLAY, TOP_PASTE, TOP_SOLDER, MECH13, MECH15, MULTI = 1, 2, 32, 33, 35, 37, 69, 71, 74
KICAD_LAYER = {"F.SilkS": TOP_OVERLAY, "F.Fab": MECH13, "F.CrtYd": MECH15}
SHAPE = {"round": 1, "circle": 1, "oval": 1, "rect": 2, "octagonal": 3, "roundrect": 9}
FLAGS = 0x0C                                  # bit3 "saved" (always set) | bit2 unlocked


def mm(v):
    """mm -> internal units (exact rational conversion, rounded to the nearest unit)."""
    return round(Fraction(str(v)) * 100_000_000 / 254)


def to_mm(u):
    return u * 254 / 100_000_000


def v7_layer(layer):
    """Redundant 'v7 saved layer id' Altium stores in primitive tails (AltiumSharp V7LayerId)."""
    if layer == 32:
        return 0x0100FFFF
    if 1 <= layer <= 31:
        return 0x01000000 + layer
    if 39 <= layer <= 54:
        return 0x01010000 + layer - 38
    if 57 <= layer <= 72:
        return 0x01020000 + layer - 56
    return 0x01030000 + {33: 6, 34: 7, 35: 8, 36: 9, 37: 10, 38: 11, 55: 12, 56: 13, 73: 14}.get(layer, 15)


def enc(s):
    return str(s).encode("cp1252", "replace")


def block(b, flag=0):
    return struct.pack("<I", (flag << 24) | len(b)) + b


def sblock(s):                                # [u32 len][u8 len][bytes]  (Pascal string block)
    b = enc(s)[:255]
    return block(bytes([len(b)]) + b)


def pblock(text):                             # [u32 len][cp1252 text + NUL]  (C-string parameter block)
    return block(enc(text) + b"\0")


def params(pairs):
    for k, v in pairs:
        if "|" in str(v) or "\0" in str(v):
            raise ValueError(f"'|' or NUL in parameter {k}={v!r}")
    return "".join(f"|{k}={v}" for k, v in pairs)


def clean(s):
    """Text going into a |KEY=VALUE| block may not contain '|'."""
    return re.sub(r"\s*\|\s*", "; ", str(s)).strip()


def section_key(name):
    """OLE storage name for a library entry: <=31 chars, no OLE/Windows-illegal characters."""
    return re.sub(r'[<>:"/\\|?*!\x00]', "_", name)[:31] or "_"


PCBLIB_RESERVED = {"FILEHEADER", "SECTIONKEYS", "LIBRARY", "FILEVERSIONINFO"}
SCHLIB_RESERVED = {"FILEHEADER", "SECTIONKEYS", "STORAGE"}


def _check_keys(keys, names, reserved, what):
    upper = [k.upper() for k in keys]
    if len(set(upper)) != len(upper):
        raise ValueError(f"{what} names collide after truncation to 31 chars: {names}")
    bad = [n for n, k in zip(names, upper) if k in reserved]
    if bad:
        raise ValueError(f"{what} name(s) {bad} clash with a reserved library stream/storage name")


def unique_id(seed):                          # Altium's 8-letter UNIQUEID, deterministic per seed
    h = hashlib.sha256(seed.encode()).digest()
    return "".join(chr(65 + b % 26) for b in h[:8])


def guid(seed):
    return uuid.uuid5(uuid.NAMESPACE_URL, "ecadlib:" + seed)


def _now():
    import os
    t = time.localtime(int(os.environ.get("SOURCE_DATE_EPOCH", time.time())))
    return time.strftime("%d/%m/%Y", t), time.strftime("%H:%M:%S", t)


# ---------------------------------------------------------------- PCB primitive records
def common(layer, polygon=0xFFFF):
    """13-byte header: layer, flags, net, polygon, component, reserved."""
    return struct.pack("<BHHHHI", layer, FLAGS, 0xFFFF, polygon, 0xFFFF, 0xFFFFFFFF)


def track_rec(x1, y1, x2, y2, w, layer):
    body = common(layer) + struct.pack("<5ihihIB3s", x1, y1, x2, y2, w, 0, 0, 0, v7_layer(layer), 0, b"")
    return b"\x04" + block(body)


def fill_rec(x1, y1, x2, y2, layer, rot=0.0):
    body = common(layer) + struct.pack("<4idiBIB3s", x1, y1, x2, y2, rot, 0, 0, v7_layer(layer), 0, b"")
    return b"\x06" + block(body)


def region_rec(pts, layer, kind=0):
    names = {TOP_OVERLAY: "TOPOVERLAY", TOP: "TOP", TOP_PASTE: "TOPPASTE", MECH13: "MECHANICAL13", MECH15: "MECHANICAL15"}
    ptxt = (f"V7_LAYER={names[layer]}|NAME= |KIND={kind}|SUBPOLYINDEX=-1|UNIONINDEX=0|ARCRESOLUTION=0.5mil"
            "|ISSHAPEBASED=FALSE|CAVITYHEIGHT=0mil")
    body = common(layer) + b"\0\0\0\0\0" + pblock(ptxt) + struct.pack("<I", len(pts))
    body += b"".join(struct.pack("<2d", float(x), float(y)) for x, y in pts)
    return b"\x0b" + block(body)


# Canonical pad SubRecord-5 tail, offsets 61..201 (AltiumSharp PcbLibWriter.PadExtendedTailTemplate,
# itself captured from an Altium pad). Thermal relief 10/10 mil x4, plane relief/clearance 20 mil,
# mask modes "from rule" (101/102 = 1), hole tolerances unset (0x7FFFFFFF).
PAD_TAIL = bytes.fromhex(
    "00000000000000a08601000400a0860100400d0300400d030000000000409c00"
    "0000000000000000010100000000000000000000000f000301000000409c0000"
    "00649a922610c7e441a32b2917a5352e677fab2120c30b3247adce6cb7b8c97e"
    "6800000000ffffff7fffffff7f00011a00000000000000000000000101000000"
    "00000000000000000000000000")
assert len(PAD_TAIL) == 141


def pad_rec(p, guid_a, guid_b):
    """p: designator, x, y, w, h (internal units), shape ('rect'|'roundrect'|'round'), corner (%),
    layer, paste (None = from rule, else manual expansion in internal units)."""
    shape = SHAPE[p["shape"]]
    rr = shape == 9
    base = 1 if rr else shape                  # Altium keeps 'round' in the main block for rounded-rect pads
    main = common(p.get("layer", TOP)) + struct.pack(
        "<9i3Bd?", p["x"], p["y"], p["w"], p["h"], p["w"], p["h"], p["w"], p["h"], 0, base, base, base,
        float(p.get("rot", 0.0)), True)
    t = bytearray(PAD_TAIL)
    put = lambda off, fmt, v: struct.pack_into(fmt, t, off - 61, v)
    put(62, "<B", 0)                           # pad stack mode: simple
    if p.get("paste") is not None:             # manual paste-mask expansion
        put(86, "<i", p["paste"]); put(101, "<B", 2)
    put(114, "<I", v7_layer(p.get("layer", TOP)))
    put(185, "<B", 3)                          # reserved marker, 0x03 in Altium-saved pads
    t[126 - 61:142 - 61], t[142 - 61:158 - 61] = guid_a.bytes_le, guid_b.bytes_le
    out = sblock(p["designator"]) + sblock("") + sblock("|&|0") + block(b"\0") + block(main + bytes(t))
    if not rr:
        return b"\x02" + out + block(b"")
    ss = struct.pack("<29i", *[p["w"]] * 29) + struct.pack("<29i", *[p["h"]] * 29) + bytes([base]) * 29
    ss += b"\0" + b"\0" + struct.pack("<id", 0, 0.0) + b"\0" * 256      # reserved, hole shape, slot, rot, offsets
    ss += b"\x01" + bytes([9]) * 32 + bytes([max(0, min(100, int(round(p.get("corner", 50)))))]) * 32
    assert len(ss) == 596
    return b"\x02" + out + block(ss)


# ---------------------------------------------------------------- footprints: model & converters
def footprint_from_kicad(text, height_mm=0.0, description=None):
    """Convert a .kicad_mod (as written by footprint_gen) into the writer's footprint dict (mm, y up)."""
    t = sexpr.parse(text)
    fp = {"name": t[1], "description": clean(description if description is not None else (sexpr.first(t, "descr") or ["", ""])[1]),
          "height_mm": height_mm, "pads": [], "tracks": [], "fills": [], "regions": []}
    for p in sexpr.find(t, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y, w, h = float(at[1]), -float(at[2]), float(size[1]), float(size[2])
        rot = float(at[3]) if len(at) > 3 else 0.0
        layers = (sexpr.first(p, "layers") or ["layers"])[1:]
        if p[2] != "smd":
            raise ValueError(f"{fp['name']}: only SMD pads are supported (pad {p[1]} is {p[2]})")
        if "F.Cu" not in layers:              # paste-only aperture (EP window) -> Top Paste fill
            if layers != ["F.Paste"] or rot:
                raise ValueError(f"{fp['name']}: unsupported non-copper pad on {layers}")
            fp["fills"].append((x - w / 2, y - h / 2, x + w / 2, y + h / 2, TOP_PASTE)); continue
        rr = sexpr.first(p, "roundrect_rratio")
        pad = {"designator": p[1], "x": x, "y": y, "w": w, "h": h, "rot": rot, "shape": p[3],
               "corner": float(rr[1]) * 200 if rr else 0,          # KiCad r/min(w,h) -> Altium r/(min(w,h)/2) %
               "paste": None if "F.Paste" in layers else "none"}
        if p[3] not in SHAPE:
            raise ValueError(f"{fp['name']}: pad shape {p[3]} not supported")
        fp["pads"].append(pad)
    for kind in ("fp_line", "fp_rect", "fp_poly"):
        for g in sexpr.find(t, kind):
            layer = KICAD_LAYER.get((sexpr.first(g, "layer") or [None, None])[1])
            if layer is None:
                continue
            w = float((sexpr.first(sexpr.first(g, "stroke") or [], "width") or [None, 0.1])[1])
            if kind == "fp_poly":
                pts = [(float(a[1]), -float(a[2])) for a in sexpr.find(sexpr.first(g, "pts"), "xy")]
            else:
                (x1, y1), (x2, y2) = [(float(a[1]), -float(a[2])) for a in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
                pts = [(x1, y1), (x2, y2)] if kind == "fp_line" else [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            fill = (sexpr.first(g, "fill") or [None, "none"])[1]
            if kind != "fp_line" and fill in ("solid", "yes"):
                fp["regions"].append((pts, layer))
            else:
                segs = zip(pts, pts[1:] + pts[:1]) if kind != "fp_line" else [pts]
                fp["tracks"] += [(a[0], a[1], b[0], b[1], w, layer) for a, b in segs]
    return fp


def footprint_from_spec(spec, density=None):
    """footprint_gen.build(spec) -> footprint dict (uses the same geometry as the .kicad_mod)."""
    from . import footprint_gen as F
    txt, rep = F.build(spec, density)
    d = f"{spec.get('description', spec['name'])}; IPC-7351B {rep['ipc_family']} density {rep['density']}; generated by ecadlib"
    fp = footprint_from_kicad(txt, F.dim(spec.get("height_max", 0))[2] if spec.get("height_max") else 0.0, d)
    return fp, rep


def _pcb_component(fp):
    """-> (storage dict, primitive count, pad count) for one footprint."""
    name = fp["name"]
    gb = guid(name + "/stack")
    data = bytearray(sblock(name)); n = 0
    for i, p in enumerate(fp["pads"]):
        q = dict(p, x=mm(p["x"]), y=mm(p["y"]), w=mm(p["w"]), h=mm(p["h"]))
        if q["shape"] != "roundrect":
            q["corner"] = 0
        if p.get("paste") == "none":         # suppress paste: manual expansion of -max(w,h)/2 closes the aperture
            q["paste"] = -max(q["w"], q["h"]) // 2 - mm(0.01)
        data += pad_rec(q, guid(f"{name}/pad{i}"), gb); n += 1
    for x1, y1, x2, y2, w, layer in fp["tracks"]:
        data += track_rec(mm(x1), mm(y1), mm(x2), mm(y2), mm(w), layer); n += 1
    for x1, y1, x2, y2, layer in fp["fills"]:
        data += fill_rec(mm(x1), mm(y1), mm(x2), mm(y2), layer); n += 1
    for pts, layer in fp["regions"]:
        data += region_rec([(mm(x), mm(y)) for x, y in pts], layer); n += 1
    height = f"{to_mil(mm(fp.get('height_mm') or 0))}mil"
    par = params([("PATTERN", name), ("HEIGHT", height), ("DESCRIPTION", clean(fp.get("description", ""))),
                  ("ITEMGUID", ""), ("REVISIONGUID", "")])
    st = {"Header": struct.pack("<I", n), "Parameters": pblock(par), "WideStrings": pblock(""), "Data": bytes(data)}
    if fp["pads"]:                            # pads are written first, so PRIMITIVEINDEX = pad index
        st["UniqueIDPrimitiveInformation"] = {"Header": struct.pack("<I", len(fp["pads"])), "Data": b"".join(
            pblock(f"|PRIMITIVEINDEX={i}|PRIMITIVEOBJECTID=Pad|UNIQUEID={unique_id(f'{name}/pad{i}')}") for i in range(len(fp["pads"])))}
    return st, len(fp["pads"]), height[:-3]


def to_mil(u):
    s = f"{u / 10000:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


@functools.lru_cache(maxsize=1)
def _template():
    return json.loads(gzip.decompress((Path(__file__).with_name("assets") / "pcblib_template.json.gz").read_bytes()))


def pcblib_bytes(footprints, filename="ecadlib.PcbLib"):
    names = [fp["name"] for fp in footprints]
    keys = [section_key(n) for n in names]
    _check_keys(keys, names, PCBLIB_RESERVED, "footprint")
    tpl, (date, tm) = _template(), _now()          # cached: treat tpl as read-only
    hdr = re.sub(r"\|FILENAME=[^|]*", "|FILENAME=" + clean(filename), tpl["library_params"], count=1)
    hdr = re.sub(r"\|DATE=[^|]*", "|DATE=" + date, hdr, count=1)
    hdr = re.sub(r"\|TIME=[^|]*", "|TIME=" + tm, hdr, count=1)
    seed = "|".join(names)
    tree = {"FileHeader": struct.pack("<IB", 27, 27) + b"PCB 6.0 Binary Library File" + struct.pack("<d", 5.01)
            + struct.pack("<IB", 8, 8) + unique_id(seed).encode()}
    long = [(n, k) for n, k in zip(names, keys) if len(n) >= 31 or k != n]
    if long:                                  # binary: [u32 count] then (name, key) string blocks
        tree["SectionKeys"] = struct.pack("<I", len(long)) + b"".join(sblock(n) + sblock(k) for n, k in sorted(long, key=lambda t: t[0].upper()))
    toc, comps = "", {}
    for fp, key in zip(footprints, keys):
        st, npads, h = _pcb_component(fp)
        comps[key] = st
        toc += f"Name={fp['name']}|Pad Count={npads}|Height={h}|Description={clean(fp.get('description', ''))}\r\n"
    hs = lambda n: struct.pack("<I", n)
    empty = lambda: {"Header": hs(0), "Data": b""}
    tree["Library"] = {
        "Header": hs(1),
        "Data": pblock(hdr) + hs(len(footprints)) + b"".join(sblock(n) for n in names),
        "EmbeddedFonts": hs(0),
        "LayerKindMapping": {"Header": hs(1), "Data": hs(8) + "1.0\0".encode("utf-16-le") + hs(0) + hs(0)},
        "PadViaLibrary": {"Header": hs(0), "Data": pblock(
            f"|PADVIALIBRARY.LIBRARYID={{{str(guid(seed + '/pvl')).upper()}}}|PADVIALIBRARY.LIBRARYNAME=<Local>|PADVIALIBRARY.DISPLAYUNITS=1")},
        "ComponentParamsTOC": {"Header": hs(1), "Data": pblock(toc)},
        "Models": empty(), "ModelsNoEmbed": empty(), "Textures": empty()}
    tree["FileVersionInfo"] = {"Header": hs(1), "Data": pblock(tpl["file_version_info"])}
    tree.update(comps)
    return cfb.to_bytes(tree)


def write_pcblib(path, footprints):
    path = Path(path)
    path.write_bytes(pcblib_bytes(footprints, path.name))
    return path


# ---------------------------------------------------------------- schematic symbols
ETYPE = {"input": 0, "io": 1, "bidir": 1, "output": 2, "open_collector": 3, "passive": 4, "hiz": 5, "tristate": 5,
         "open_emitter": 6, "power": 7, "power_in": 7, "power_out": 7, "ground": 7, "nc": 4}
ORIENT = {"right": 0, "top": 1, "left": 2, "bottom": 3}   # direction the pin points away from the body
BLUE, BODY_FILL, PARAM_COLOR = 128, 11599871, 8388608


def _coord(pairs, name, v):                   # DXP units (10 mil); Altium omits zeros, adds _Frac for sub-units
    whole, frac = int(v), round((v - int(v)) * 100000)
    if whole:
        pairs.append((name, whole))
    if frac:
        pairs.append((name + "_Frac", frac))


def pin_rec(num, name, etype, orient, x, y, length, show=True):
    cong = orient | 0x20 | (0x18 if show else 0)       # 0x08 name, 0x10 number visible; 0x20 as Altium writes it
    b = struct.pack("<iBhBBBBB", 2, 0, 1, 0, 0, 0, 0, 0) + bytes([0])          # ... + empty description
    b += struct.pack("<BBBhhhI", 1, ETYPE[etype], cong, length, x, y, 0)
    b += b"".join(bytes([len(enc(s))]) + enc(s) for s in (name, num, "", "|&|", ""))
    return block(b, flag=1)


def ic_symbol(name, pins, description="", designator="U?", footprint=None, comment="*"):
    """Rectangular IC: pins = [{'num','name','type','side' in left/right/top/bottom}], listed top->bottom
    (left/right) or left->right (top/bottom). Body grows to fit pin names; all on a 100 mil grid."""
    bad = [p.get("num") for p in pins if p.get("side", "left") not in ORIENT]
    if bad:
        raise ValueError(f"{name}: pins {bad} have a side other than {list(ORIENT)}")
    side = {s: [p for p in pins if p.get("side", "left") == s] for s in ORIENT}
    rows = max(len(side["left"]), len(side["right"]), 1)
    cols = max(len(side["top"]), len(side["bottom"]))
    chars = max([len(p["name"]) for p in side["left"]] or [0]) + max([len(p["name"]) for p in side["right"]] or [0])
    w = max(40, 10 * -(-(chars * 7 + 20) // 10), 10 * (cols + 1))   # ~7 units (70 mil) per 10 pt char
    room = lambda ps: 10 * -(-(max(len(p["name"]) for p in ps) * 7 + 10) // 10) if ps else 0
    t_room, b_room = room(side["top"]), room(side["bottom"])          # vertical names inside the body
    top, bot = 0, -(t_room + 10 * (rows + 1) + b_room)
    prims = [("rect", 0, top, w, bot)]
    L = 30
    for i, p in enumerate(side["left"]):                              # below the top pins' names
        prims.append(("pin", p, "left", 0, -t_room - 10 * (i + 1), L))
    for i, p in enumerate(side["right"]):
        prims.append(("pin", p, "right", w, -t_room - 10 * (i + 1), L))
    for i, p in enumerate(side["top"]):
        prims.append(("pin", p, "top", 10 * (i + 1), top, L))
    for i, p in enumerate(side["bottom"]):
        prims.append(("pin", p, "bottom", 10 * (i + 1), bot, L))
    return {"name": name, "description": description, "designator": designator, "comment": comment,
            "footprint": footprint, "prims": prims, "label_at": ((0, top + 2), (0, bot - 8))}


def resistor_symbol(name="Resistor", description="Resistor", footprint=None):
    pins = [{"num": "1", "name": "1", "type": "passive"}, {"num": "2", "name": "2", "type": "passive"}]
    prims = [("rect", -15, 4, 15, -4), ("pin", pins[0], "left", -15, 0, 15, False), ("pin", pins[1], "right", 15, 0, 15, False)]
    return {"name": name, "description": description, "designator": "R?", "comment": "*", "footprint": footprint,
            "prims": prims, "label_at": ((-15, 7), (-15, -14))}


def capacitor_symbol(name="Capacitor", description="Capacitor (non-polarised)", footprint=None):
    pins = [{"num": "1", "name": "1", "type": "passive"}, {"num": "2", "name": "2", "type": "passive"}]
    prims = [("line", -3, 8, -3, -8, 2), ("line", 3, 8, 3, -8, 2),
             ("pin", pins[0], "left", -3, 0, 27, False), ("pin", pins[1], "right", 3, 0, 27, False)]
    return {"name": name, "description": description, "designator": "C?", "comment": "*", "footprint": footprint,
            "prims": prims, "label_at": ((-15, 11), (-15, -18))}


def symbol_from_json(d):
    kind = d.get("template", "ic")
    if kind == "resistor":
        return resistor_symbol(d.get("name", "Resistor"), d.get("description", "Resistor"), d.get("footprint"))
    if kind == "capacitor":
        return capacitor_symbol(d.get("name", "Capacitor"), d.get("description", "Capacitor"), d.get("footprint"))
    return ic_symbol(d["name"], d["pins"], d.get("description", ""), d.get("designator", "U?"), d.get("footprint"))


def _sch_component(sym):
    """-> (Data stream, record count, pin count)."""
    nm, recs, idx_sheet = sym["name"], [], 0
    uid = lambda k: unique_id(f"{nm}/{k}")
    npins = sum(1 for p in sym["prims"] if p[0] == "pin")
    comp = [("RECORD", 1), ("LibReference", nm)]
    if sym.get("description"):
        comp.append(("ComponentDescription", clean(sym["description"])))
    comp += [("PartCount", 2), ("DisplayModeCount", 1), ("IndexInSheet", -1), ("OwnerPartId", -1), ("CurrentPartId", 1),
             ("LibraryPath", "*"), ("SourceLibraryName", "*"), ("SheetPartFileName", "*"), ("TargetFileName", "*"),
             ("UniqueID", uid("comp")), ("AreaColor", BODY_FILL), ("Color", BLUE), ("PartIDLocked", "T"),
             ("DesignItemId", nm), ("AllPinCount", npins)]
    recs.append(pblock(params(comp)))
    for i, pr in enumerate(sym["prims"]):
        common_ = [("IsNotAccesible", "T")] + ([("IndexInSheet", i)] if i else []) + [("OwnerPartId", 1)]
        if pr[0] == "rect":
            kv = [("RECORD", 14)] + common_
            for k, v in zip(("Location.X", "Location.Y", "Corner.X", "Corner.Y"), (pr[1], min(pr[2], pr[4]), pr[3], max(pr[2], pr[4]))):
                _coord(kv, k, v)
            kv += [("Color", BLUE), ("AreaColor", BODY_FILL), ("IsSolid", "T"), ("UniqueID", uid(i))]
            recs.append(pblock(params(kv)))
        elif pr[0] == "line":
            kv = [("RECORD", 13)] + common_
            for k, v in zip(("Location.X", "Location.Y", "Corner.X", "Corner.Y"), pr[1:5]):
                _coord(kv, k, v)
            kv += [("LineWidth", pr[5]), ("Color", BLUE), ("UniqueID", uid(i))]
            recs.append(pblock(params(kv)))
        else:
            _, p, side, x, y, length, *show = pr
            recs.append(pin_rec(p["num"], p["name"], p.get("type", "passive"), ORIENT[side], x, y, length, show[0] if show else True))
    (dx, dy), (cx, cy) = sym["label_at"]
    for rec, nm_, text, (x, y) in ((34, "Designator", sym.get("designator", "U?"), (dx, dy)), (41, "Comment", sym.get("comment", "*"), (cx, cy))):
        kv = [("RECORD", rec), ("IndexInSheet", -1), ("OwnerPartId", -1)]
        _coord(kv, "Location.X", x); _coord(kv, "Location.Y", y)
        kv += [("Color", PARAM_COLOR), ("FontID", 1), ("Text", clean(text)), ("Name", nm_)]
        kv += [("ReadOnlyState", 1)] if rec == 34 else []
        kv += [("UniqueID", uid(nm_))]
        recs.append(pblock(params(kv)))
    recs.append(pblock("|RECORD=44"))
    if sym.get("footprint"):
        il = len(recs) - 1
        fpn = clean(sym["footprint"])
        recs.append(pblock(params([("RECORD", 45), ("OwnerIndex", il), ("IndexInSheet", -1), ("ModelName", fpn),
                                   ("ModelType", "PCBLIB"), ("DatafileCount", 1), ("ModelDatafileEntity0", fpn),
                                   ("ModelDatafileKind0", "PCBLib"), ("IsCurrent", "T"), ("UniqueID", uid("impl"))])))
        recs += [pblock(f"|RECORD=46|OwnerIndex={il + 1}"), pblock(f"|RECORD=48|OwnerIndex={il + 1}")]
    return b"".join(recs), len(recs), npins


def schlib_bytes(symbols):
    names = [s["name"] for s in symbols]
    keys = [section_key(n) for n in names]
    _check_keys(keys, names, SCHLIB_RESERVED, "symbol")
    tree, total, lib = {}, 0, []
    for i, (s, k) in enumerate(zip(symbols, keys)):
        data, n, _ = _sch_component(s)
        tree[k] = {"Data": data}
        total += n
        lib += [(f"LibRef{i}", s["name"])] + ([(f"CompDescr{i}", clean(s["description"]))] if s.get("description") else []) + [(f"PartCount{i}", 2)]
    hdr = [("HEADER", "Protel for Windows - Schematic Library Editor Binary File Version 5.0"), ("Weight", total + 1),
           ("MinorVersion", 9), ("UniqueID", unique_id("|".join(names))), ("FontIdCount", 1), ("Size1", 10),
           ("FontName1", "Times New Roman"), ("UseMBCS", "T"), ("IsBOC", "T"), ("SheetStyle", 9), ("BorderOn", "T"),
           ("SheetNumberSpaceSize", 12), ("AreaColor", 16317695), ("SnapGridOn", "T"), ("SnapGridSize", 10),
           ("VisibleGridOn", "T"), ("VisibleGridSize", 10), ("CustomX", 18000), ("CustomY", 18000),
           ("UseCustomSheet", "T"), ("ReferenceZonesOn", "T"), ("Display_Unit", 0), ("CompCount", len(symbols))] + lib
    tree["FileHeader"] = pblock(params(hdr))
    long = [(n, k) for n, k in zip(names, keys) if k != n]
    if long:
        kv = [("KeyCount", len(long))]
        for i, (n, k) in enumerate(long):
            kv += [(f"LibRef{i}", n), (f"SectionKey{i}", k)]
        tree["SectionKeys"] = pblock(params(kv))
    tree["Storage"] = pblock("|HEADER=Icon storage")
    return cfb.to_bytes(tree)


def write_schlib(path, symbols):
    path = Path(path)
    path.write_bytes(schlib_bytes(symbols))
    return path


# ---------------------------------------------------------------- minimal reader (verification / dump)
def _blk(d, i):
    n = struct.unpack_from("<I", d, i)[0] & 0xFFFFFF
    return d[i + 4:i + 4 + n], i + 4 + n


def _str(b):
    return b[1:1 + b[0]].decode("cp1252") if b else ""


def _param_stream(o, path):
    return parse_params(_blk(o.openstream(path).read(), 0)[0].rstrip(b"\0").decode("cp1252"))


def parse_params(text):
    out = {}
    for kv in text.split("|"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.upper()] = v
    return out


def parse_pcb_data(d):
    """Footprint Data stream -> (name, [records]); each record keeps its raw blocks for re-encoding checks."""
    b, i = _blk(d, 0)
    name, recs = _str(b), []
    while i < len(d):
        t = d[i]; i += 1
        if t == 2:
            blocks = []
            for _ in range(6):
                b, i = _blk(d, i); blocks.append(b)
            m = blocks[4]
            x, y, w, h = struct.unpack_from("<4i", m, 13)
            ss = blocks[5]
            shape = ss[532] if len(ss) >= 596 and ss[531] else m[49]
            recs.append({"type": "pad", "designator": _str(blocks[0]), "layer": m[0], "x": x, "y": y, "w": w, "h": h,
                         "hole": struct.unpack_from("<i", m, 45)[0], "shape": shape, "rot": struct.unpack_from("<d", m, 52)[0],
                         "corner": ss[564] if len(ss) >= 596 and ss[531] else None,
                         "paste_mode": m[101] if len(m) > 102 else None, "paste": struct.unpack_from("<i", m, 86)[0] if len(m) > 90 else None,
                         "raw": blocks})
        elif t == 5:
            b1, i = _blk(d, i); b2, i = _blk(d, i)
            recs.append({"type": "text", "layer": b1[0], "raw": [b1, b2]})
        elif t in (1, 3, 4, 6, 11, 12):
            b, i = _blk(d, i)
            r = {"type": {1: "arc", 3: "via", 4: "track", 6: "fill", 11: "region", 12: "body"}[t], "layer": b[0], "raw": [b]}
            if t == 4:
                r.update(zip(("x1", "y1", "x2", "y2", "w"), struct.unpack_from("<5i", b, 13)))
            elif t == 6:
                r.update(zip(("x1", "y1", "x2", "y2"), struct.unpack_from("<4i", b, 13)))
            elif t == 11:
                pl = struct.unpack_from("<I", b, 18)[0]
                n = struct.unpack_from("<I", b, 22 + pl)[0]
                r["params"] = b[22:22 + pl - 1].decode("cp1252")
                r["pts"] = [struct.unpack_from("<2d", b, 26 + pl + 16 * k) for k in range(n)]
            recs.append(r)
        else:
            raise ValueError(f"unknown PCB record id {t} at {i - 1}")
    return name, recs


def read_pcblib(path):
    import olefile
    with olefile.OleFileIO(str(path)) as o:
        return _read_pcblib(o)


def _read_pcblib(o):
    d = o.openstream("Library/Data").read()
    hb, i = _blk(d, 0)
    n = struct.unpack_from("<I", d, i)[0]; i += 4
    names = []
    for _ in range(n):
        b, i = _blk(d, i); names.append(_str(b))
    keymap = {}
    if o.exists("SectionKeys"):
        s = o.openstream("SectionKeys").read(); k = struct.unpack_from("<I", s)[0]; j = 4
        for _ in range(k):
            a, j = _blk(s, j); b, j = _blk(s, j); keymap[_str(a)] = _str(b)
    out = []
    for nm in names:
        key = keymap.get(nm, section_key(nm))
        pr = _param_stream(o, f"{key}/Parameters")
        nm2, recs = parse_pcb_data(o.openstream(f"{key}/Data").read())
        out.append({"name": nm, "storage": key, "params": pr, "records": recs,
                    "header_count": struct.unpack("<I", o.openstream(f"{key}/Header").read())[0]})
    return out


def read_schlib(path):
    import olefile
    with olefile.OleFileIO(str(path)) as o:
        return _read_schlib(o)


def _read_schlib(o):
    hdr = _param_stream(o, "FileHeader")
    keymap = {}
    if o.exists("SectionKeys"):
        sk = _param_stream(o, "SectionKeys")
        keymap = {sk[f"LIBREF{i}"]: sk[f"SECTIONKEY{i}"] for i in range(int(sk["KEYCOUNT"]))}
    out = []
    for i in range(int(hdr.get("COMPCOUNT", 0))):
        nm = hdr[f"LIBREF{i}"]
        d = o.openstream(f"{keymap.get(nm, section_key(nm))}/Data").read()
        j, recs = 0, []
        while j < len(d):
            n = struct.unpack_from("<I", d, j)[0]; flag, n = n >> 24, n & 0xFFFFFF
            b = d[j + 4:j + 4 + n]; j += 4 + n
            if flag == 1:                     # binary pin
                k = 12 + 1 + b[12]
                ftype, etype, cong, length, x, y = struct.unpack_from("<BBBhhh", b, k)
                k += 13
                strs = []
                for _ in range(5):
                    strs.append(b[k + 1:k + 1 + b[k]].decode("cp1252")); k += 1 + b[k]
                recs.append({"RECORD": "2", "name": strs[0], "num": strs[1], "etype": etype, "orient": cong & 3,
                             "x": x, "y": y, "length": length, "conglomerate": cong})
            else:
                recs.append(parse_params(b.rstrip(b"\0").decode("cp1252")))
        out.append({"name": nm, "records": recs})
    return hdr, out


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Write Altium .PcbLib/.SchLib files (one entry per file)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pcblib", help="package JSON specs -> <out>/<name>.PcbLib")
    p.add_argument("specs", nargs="+"); p.add_argument("-o", "--out", default="out/altium_models/PcbLib")
    p.add_argument("--density", choices="ABC"); p.add_argument("--single", help="write all footprints into this one file")
    s = sub.add_parser("schlib", help="symbol JSON files -> <out>/<name>.SchLib")
    s.add_argument("symbols", nargs="+"); s.add_argument("-o", "--out", default="out/altium_models/SchLib")
    d = sub.add_parser("dump", help="print what the minimal reader sees in a .PcbLib/.SchLib")
    d.add_argument("files", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "dump":
        for f in a.files:
            if f.lower().endswith(".pcblib"):
                for fp in read_pcblib(f):
                    pads = [r for r in fp["records"] if r["type"] == "pad"]
                    kinds = {}
                    for r in fp["records"]:
                        kinds[(r["type"], r["layer"])] = kinds.get((r["type"], r["layer"]), 0) + 1
                    print(f"{f}: {fp['name']} (storage {fp['storage']}) {len(pads)} pads, {dict(sorted(kinds.items()))}")
                    for r in pads:
                        print(f"   pad {r['designator']:>3} ({to_mm(r['x']):8.4f},{to_mm(r['y']):8.4f}) {to_mm(r['w']):.4f}x{to_mm(r['h']):.4f} "
                              f"shape {r['shape']} corner {r['corner']} paste {'rule' if r['paste_mode'] == 1 else r['paste']}")
            else:
                _, syms = read_schlib(f)
                for sy in syms:
                    pins = [r for r in sy["records"] if r["RECORD"] == "2"]
                    print(f"{f}: {sy['name']} {len(pins)} pins: " + ", ".join(f"{r['num']}:{r['name']}" for r in pins))
        return
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "pcblib":
        from . import footprint_gen as F
        fps = []
        for sp in a.specs:
            spec = json.loads(Path(sp).read_text())
            F.validate(spec)
            fp, rep = footprint_from_spec(spec, a.density)
            fps.append(fp)
            if not a.single:
                path = write_pcblib(out / f"{fp['name']}.PcbLib", [fp])
                print(f"{fp['name']:<44} {len(fp['pads']):>3} pads {len(fp['tracks']):>3} tracks {len(fp['fills'])} fills "
                      f"{len(fp['regions'])} regions -> {path} ({path.stat().st_size} B)")
        if a.single:
            print(f"{len(fps)} footprints -> {write_pcblib(out / a.single, fps)}")
    else:
        for f in a.symbols:
            sym = symbol_from_json(json.loads(Path(f).read_text()))
            path = write_schlib(out / f"{sym['name']}.SchLib", [sym])
            print(f"{sym['name']:<24} {sum(1 for p in sym['prims'] if p[0] == 'pin'):>3} pins -> {path}")


if __name__ == "__main__":
    main()
