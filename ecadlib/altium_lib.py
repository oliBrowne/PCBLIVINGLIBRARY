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
Field values were cross-checked against real Altium files (docs/altium.md, "Altium library writer").

Also from AltiumSharp: through-hole pad holes (size, round/square/slot, slot length, hole rotation, plated),
the 60-byte arc, the ComponentBody record (extruded or embedded STEP) and the Library/Models storage with
Altium's STEP checksum, the 252-byte stroke text record (.Designator / .Comment) with its WideStrings entry,
SchLib pin symbols (dot, clock, active-low ...) and display modes (De Morgan). Each was checked byte for byte
against Altium-saved records (AltiumSharp TestData; display modes against an Altium 24 sample library).

Conventions (mirroring Altium-made libraries in AltiumSharp's TestData):
  Top Layer (1) SMD pads, Multi-Layer (74) through-hole pads; 3D bodies on Mechanical 13;
  Top Overlay (33) silkscreen; Mechanical 13 (69) fab/body outline;
  Mechanical 15 (71) courtyard; EP paste windows as fills on Top Paste (35).
  Units: 1 mil = 10000 internal units, so 1 mm = 10^8/254 units (rounded to the nearest unit,
  2.54 nm). KiCad's Y axis points down, Altium's up: y is negated on import.
Schematic units: 1 = 10 mil (DXP); pins on a 100 mil (10 unit) grid."""
import argparse, functools, gzip, hashlib, json, math, re, struct, sys, time, unicodedata, uuid, warnings, zlib
from fractions import Fraction
from pathlib import Path

from . import cfb, sexpr

# ---------------------------------------------------------------- units & low-level encoders
TOP, MID1, BOTTOM, TOP_OVERLAY, BOTTOM_OVERLAY, TOP_PASTE, TOP_SOLDER, MECH13, MECH15, MULTI = 1, 2, 32, 33, 34, 35, 37, 69, 71, 74
BOTTOM_PASTE, BOTTOM_SOLDER = 36, 38
KEEPOUT = 56
MECH16 = 72
KICAD_LAYER = {"F.SilkS": TOP_OVERLAY, "F.Fab": MECH13, "F.CrtYd": MECH15}
LAYER_NAME = {TOP: "TOP", BOTTOM: "BOTTOM", TOP_OVERLAY: "TOPOVERLAY", BOTTOM_OVERLAY: "BOTTOMOVERLAY", TOP_PASTE: "TOPPASTE",
              BOTTOM_PASTE: "BOTTOMPASTE", TOP_SOLDER: "TOPSOLDER", BOTTOM_SOLDER: "BOTTOMSOLDER", MECH13: "MECHANICAL13", MECH15: "MECHANICAL15", KEEPOUT: "KEEPOUT"}
SHAPE = {"round": 1, "circle": 1, "oval": 1, "rect": 2, "octagonal": 3, "roundrect": 9}
HOLE = {"round": 0, "square": 1, "slot": 2}
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


def nfc(s):
    """Composed Unicode: 'E' + combining acute (macOS file names, PDF text) is 'É', which cp1252 holds."""
    return unicodedata.normalize("NFC", str(s))


def enc(s):
    return nfc(s).encode("cp1252", "replace")


def block(b, flag=0):
    return struct.pack("<I", (flag << 24) | len(b)) + b


def sblock(s):                                # [u32 len][u8 len][bytes]  (Pascal string block)
    b = enc(s)[:255]
    return block(bytes([len(b)]) + b)


def pblock(text, utf8=False):                 # [u32 len][cp1252 text + NUL]  (C-string parameter block)
    """utf8: a |KEY=VALUE| whose value is not plain ASCII also gets Altium's '|%UTF8%KEY=<utf-8>||' twin in front, as
    Altium writes it for every non-ASCII value ('80µA', '90°' as well as CJK): the plain cp1252 copy turns 'Ω' into '?'
    and is read in the reader's ANSI code page, the twin is what Altium reads back."""
    if not utf8:
        return block(enc(text) + b"\0")
    out = b""
    for seg in text.split("|")[1:] if text.startswith("|") else [text]:
        k, eq, v = seg.partition("=")
        if eq and not v.isascii():
            out += b"|%UTF8%" + k.encode("utf-8") + b"=" + v.encode("utf-8") + b"||"
        out += (b"|" if text.startswith("|") else b"") + enc(seg)
    return block(out + b"\0")


def schblock(text):                           # SchLib parameter block: non-ASCII values get a %UTF8% twin
    return pblock(text, utf8=True)


def params(pairs):
    for k, v in pairs:
        if "|" in str(v) or "\0" in str(v):
            raise ValueError(f"'|' or NUL in parameter {k}={v!r}")
    return "".join(f"|{k}={v}" for k, v in pairs)


def clean(s):
    """Text going into a |KEY=VALUE| block may not contain '|' or line breaks (TOC streams are line based)."""
    return re.sub(r"\s+", " ", re.sub(r"\s*\|\s*", "; ", str(s))).strip()


_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))      # zero-width space / joiners, BOM


def storable(s):
    """s with the look-alikes cp1252 can hold: NFC ('E' + combining acute -> 'É'), Greek mu -> micro sign 'µ',
    'Ω' -> 'Ohm', zero-width characters (pasted from web pages) dropped, and any other character cp1252 lacks whose
    compatibility form it has (fullwidth 'ＬＭ358' -> 'LM358')."""
    s = nfc(s).translate(_INVISIBLE).replace("\u03bc", "\u00b5").replace("\u2126", "Ohm").replace("\u03a9", "Ohm")
    out = []
    for c in s:
        if c.encode("cp1252", "replace") == b"?" and c != "?":
            k = unicodedata.normalize("NFKC", c)
            c = k if k.encode("cp1252", "replace") != b"?" * len(k) or k == "?" else c
        out.append(c)
    return "".join(out)


def lib_name(s):
    """Library entry name as Altium stores it (cp1252 in the binary records). 'Ω' becomes 'Ohm';
    anything else cp1252 can't hold is an error rather than a silent '?' that makes the entry unreadable."""
    s = storable(s)
    bad = re.search(r"[\x00-\x1f\x7f]", s)
    if bad:                                   # a line break would add a ghost entry to the line-based TOC streams
        raise ValueError(f"name {s!r} has a line break or control character ({bad.group()!r}); rename it")
    try:
        s.encode("cp1252")
    except UnicodeEncodeError as e:
        raise ValueError(f"name {s!r} has characters Altium libraries can't store ({s[e.start:e.end]!r}); rename it") from None
    return s


def pin_number(s, what="pin number"):
    """A pin number / pad designator for a binary record (cp1252). One cp1252 can't hold is an error: written as '?',
    two of them ('α', 'β') would become the same pin and the symbol-to-footprint mapping would break silently."""
    s = nfc(s)
    try:
        s.encode("cp1252")
    except UnicodeEncodeError as e:
        raise ValueError(f"{what} {s!r} has characters Altium libraries can't store ({s[e.start:e.end]!r}); rename it") from None
    return s


class PinNameWarning(UserWarning):
    """A pin name holds characters the SchLib binary pin record can't store (see not_cp1252 / pin_rec)."""


def not_cp1252(s):
    """-> the characters of s, in order and without repeats, that cp1252 can't encode ('' when it all fits)."""
    return "".join(dict.fromkeys(c for c in nfc(s) if c.encode("cp1252", "replace") == b"?" and c != "?"))


def file_stem(s):
    """Filename for one-entry-per-file libraries: path separators and Windows-illegal characters -> '_', and no
    Windows device name (AUX -> AUX_: fsutil.portable_stem)."""
    from .fsutil import portable_stem
    return portable_stem(re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", lib_name(s)).rstrip(". ") or "_")


def section_key(name):
    """OLE storage name for a library entry: <=31 chars, no OLE/Windows-illegal characters."""
    return re.sub(r'[<>:"/\\|?*!\x00]', "_", name)[:31] or "_"


PCBLIB_RESERVED = {"FILEHEADER", "SECTIONKEYS", "LIBRARY", "FILEVERSIONINFO"}
SCHLIB_RESERVED = {"FILEHEADER", "SECTIONKEYS", "STORAGE"}


def _section_keys(names, reserved, what):
    """Storage names for a library's entries: section_key(name), except that a name whose key an earlier entry already
    has (two names sharing their first 31 characters, 'PinHeader_1x05_P2.54mm_Vertical' + '..._SMD_Pin1Left') gets
    the key cut shorter and ending in '~1', '~2' ...; the SectionKeys stream maps every such name to its key."""
    upper = [n.upper() for n in names]
    dup = sorted({n for n, u in zip(names, upper) if upper.count(u) > 1})
    if dup:
        raise ValueError(f"{what} names occur more than once (names are not case-sensitive): {dup}")
    bad = [n for n in names if section_key(n).upper() in reserved]
    if bad:
        raise ValueError(f"{what} name(s) {bad} clash with a reserved library stream/storage name")
    keys, used = [], set()
    for n in names:
        k, i = section_key(n), 0
        while k.upper() in used or k.upper() in reserved:
            i += 1
            k = section_key(n)[:31 - len(f"~{i}")] + f"~{i}"
        used.add(k.upper()); keys.append(k)
    return keys


def unique_id(seed):                          # Altium's 8-letter UNIQUEID, deterministic per seed
    h = hashlib.sha256(seed.encode()).digest()
    return "".join(chr(65 + b % 26) for b in h[:8])


def guid(seed):
    return uuid.uuid5(uuid.NAMESPACE_URL, "ecadlib:" + seed)


def _now():
    import os
    sde = os.environ.get("SOURCE_DATE_EPOCH")       # reproducible builds: the same bytes in every time zone (UTC)
    t = time.gmtime(int(sde)) if sde else time.localtime()
    return time.strftime("%d/%m/%Y", t), time.strftime("%H:%M:%S", t)


# ---------------------------------------------------------------- PCB primitive records
def common(layer, polygon=0xFFFF, flags=FLAGS):
    """13-byte header: layer, flags, net, polygon, component, reserved."""
    return struct.pack("<BHHHHI", layer, flags, 0xFFFF, polygon, 0xFFFF, 0xFFFFFFFF)


def track_rec(x1, y1, x2, y2, w, layer):
    body = common(layer) + struct.pack("<5ihihIB3s", x1, y1, x2, y2, w, 0, 0, 0, v7_layer(layer), 0, b"")
    return b"\x04" + block(body)


def fill_rec(x1, y1, x2, y2, layer, rot=0.0):
    body = common(layer) + struct.pack("<4idiBIB3s", x1, y1, x2, y2, rot, 0, 0, v7_layer(layer), 0, b"")
    return b"\x06" + block(body)


def v7_name(layer):
    """V7_LAYER name in region / body parameter text: TOP, MID1.., MECHANICAL1..16, TOPOVERLAY ..."""
    if layer in LAYER_NAME:
        return LAYER_NAME[layer]
    if 57 <= layer <= 72:
        return f"MECHANICAL{layer - 56}"
    if 2 <= layer <= 31:
        return f"MID{layer - 1}"
    raise ValueError(f"layer {layer}: no Altium V7_LAYER name for it")


def region_rec(pts, layer, kind=0, keepout=None):
    """keepout: restriction bits (31 = all objects) for a keep-out region (flags bit 9, as in TestData KEEPOUT_REGION)."""
    ptxt = (f"V7_LAYER={v7_name(layer)}|NAME= |KIND={kind}|SUBPOLYINDEX=-1|UNIONINDEX=0|ARCRESOLUTION=0.5mil"
            "|ISSHAPEBASED=FALSE|CAVITYHEIGHT=0mil" + (f"|KEEPOUTRESTRICTIONS={keepout}" if keepout is not None else ""))
    body = common(layer, flags=FLAGS | (0x200 if keepout is not None else 0)) + b"\0\0\0\0\0" + pblock(ptxt) + struct.pack("<I", len(pts))
    body += b"".join(struct.pack("<2d", float(x), float(y)) for x, y in pts)
    return b"\x0b" + block(body)


def arc_rec(cx, cy, r, a0, a1, w, layer):
    """Arc counter-clockwise from a0 to a1 (degrees); 0..360 is a full circle (AltiumSharp WriteArc)."""
    body = common(layer) + struct.pack("<3i2di", cx, cy, r, float(a0), float(a1), w)
    body += struct.pack("<hiBIB3s", 0, 0, 0, v7_layer(layer), 0, b"")
    return b"\x01" + block(body)


# Cached text extents (Default stroke font) Altium stored for these special strings at 50 mil height
# (TestData TEXT_DESIGNATOR / TEXT_COMMENT); other strings get an estimate. Altium recomputes them.
TEXT_BOX = {".Designator": (5098381, 766451), ".Comment": (3682173, 599838)}
CHAR_W = 0.927                                # stroke-font advance per character / height (same records)
ARIAL = "Arial".encode("utf-16-le").ljust(64, b"\0")


def text_rec(text, x, y, h, sw, layer, rot=0.0, ws=0, font=1, mirror=False, box=None):
    """Stroke-font text (record 5, AltiumSharp WriteText): 252-byte block, then the text as a string block.
    Anchored at its bottom-left corner (justification 3, as Altium writes it); ws = WideStrings index."""
    bw, bh = box or [round(v * h / 500000) for v in TEXT_BOX.get(text, (round(CHAR_W * 500000 * len(text)), 600000))]
    b = common(layer) + struct.pack("<3ihd?i6x", x, y, h, font, rot, mirror, sw) + ARIAL
    b += struct.pack("<?3i?2iBi5i4B", 0, 0, ws, 0, 0, bw, bh, 3, 0, 10500000, 2100000, 200000, 200000, 0, 0, 1, 1, 0) + ARIAL
    b += struct.pack("<?I2B2i4x2i", 1, v7_layer(layer), 0, 0, -1 << 31, -1 << 31, x, y)
    assert len(b) == 252
    return b"\x05" + block(b) + sblock(text)


KTEXT = {"reference": (".Designator", TOP_OVERLAY, 3), "value": (".Comment", MECH13, 8),    # + nominal characters shown
         "fabref": (".Designator", MECH13, 3)}     # user ${REFERENCE} on F.Fab: the assembly drawing's designator
FAB_REF = ("${REFERENCE}", "%R")                    # KiCad 6+ / KiCad 5 reference variable in user text


def _fab_ref(n):
    """True for a KiCad user text that shows the reference on the fab layer (fp_text user "${REFERENCE}" ... F.Fab)."""
    return (n[0] == "fp_text" and len(n) > 2 and str(n[1]) == "user" and str(n[2]) in FAB_REF
            and str((sexpr.first(n, "layer") or [None, ""])[1]) in ("F.Fab", "B.Fab"))


def texts_from_kicad(t):
    """KiCad Reference / Value (fp_text or KiCad 8+ property) and the fab ${REFERENCE} -> [(text, x, y, height, stroke,
    rot, layer)] in mm, y up: .Designator on Top Overlay when KiCad has the reference on silk (F.SilkS / B.SilkS), else
    on Mechanical 13 with the rest of the fab drawing (a Fab reference is assembly text, not silk), .Comment and the
    ${REFERENCE} (a second .Designator) on Mechanical 13; at most one .Designator per layer. KiCad centres text on (at); Altium anchors stroke text at
    the bottom-left, so the anchor is moved left by half the estimated width (all of it for right-justified text) of a
    nominal designator (3 characters, "U12") or comment (8 characters) and down by half the height, rotated with the text."""
    out = []
    for n in list(sexpr.find(t, "fp_text")) + [p for p in sexpr.find(t, "property") if p[1] in ("Reference", "Value")]:
        kind = "fabref" if _fab_ref(n) else n[1].lower()
        if kind not in KTEXT or not sexpr.first(n, "at"):
            continue
        at, eff = sexpr.first(n, "at"), sexpr.first(n, "effects") or []
        font, just = sexpr.first(eff, "font") or [], sexpr.first(eff, "justify") or []
        size, th = sexpr.first(font, "size"), sexpr.first(font, "thickness")
        h = float(size[2]) if size else 1.0
        rot = float(at[3]) % 360 if len(at) > 3 and at[3] != "unlocked" else 0.0    # KiCad 6/7: (at x y [rot] unlocked)
        rot = rot - 180 if 90 < rot <= 270 else rot        # KiCad keeps footprint text upright; Altium doesn't
        txt, layer, chars = KTEXT[kind]
        src = (sexpr.first(n, "layer") or [None, ""])[1]
        if kind == "reference" and "SilkS" not in str(src):
            layer = MECH13
        a, dx, dy = math.radians(rot), -CHAR_W * h * chars * (0 if "left" in just else 1 if "right" in just else 0.5), -h / 2
        x, y = float(at[1]) + dx * math.cos(a) - dy * math.sin(a), -float(at[2]) + dx * math.sin(a) + dy * math.cos(a)
        if any(o[0] == txt and o[6] == layer for o in out):      # Fab reference and ${REFERENCE}: keep the first
            continue
        out.append((txt, round(x, 4), round(y, 4), h, float(th[1]) if th else round(0.15 * h, 3), rot, layer))
    return out


def text_centre(txt, x, y, h, rot):
    """Centre (mm, y up) of a stroke text written by texts_from_kicad / add_fab_designator from its bottom-left anchor:
    the inverse of their nominal-width shift."""
    chars = 3 if txt == ".Designator" else 8 if txt == ".Comment" else len(txt)
    a, dx, dy = math.radians(rot), CHAR_W * h * chars / 2, h / 2
    return x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a)


def add_fab_designator(fp):
    """fp with a .Designator on Mechanical 13 (the assembly layer) centred on the body, as KiCad's ${REFERENCE} on
    F.Fab (rule book: reference on silk, designator on the assembly drawing). The body is the box round the
    Mechanical 13 outline, else round the pads. Text height as footprint_gen: half the longer body side, 0.25-1 mm.
    A footprint that already has one, or has no pads, comes back unchanged (the same dict)."""
    texts = fp.get("texts") or []
    if any(t[0] == ".Designator" and t[6] == MECH13 for t in texts) or not fp.get("pads"):
        return fp
    pts = [q for x1, y1, x2, y2, _, l in fp.get("tracks", []) if l == MECH13 for q in ((x1, y1), (x2, y2))]
    pts += [q for r in fp.get("regions", []) if r[1] == MECH13 for q in r[0]]
    pts += [(cx + s * r, cy + u * r) for cx, cy, r, _, _, _, l in fp.get("arcs", []) if l == MECH13 for s in (-1, 1) for u in (-1, 1)]
    if not pts:
        for p in fp["pads"]:
            w, h = (p["h"], p["w"]) if round((p.get("rot") or 0) / 90) % 2 else (p["w"], p["h"])
            pts += [(p["x"] - w / 2, p["y"] - h / 2), (p["x"] + w / 2, p["y"] + h / 2)]
    x0, y0 = min(q[0] for q in pts), min(q[1] for q in pts)
    x1, y1 = max(q[0] for q in pts), max(q[1] for q in pts)
    h = math.floor(100 * min(1.0, max(0.25, 0.25 * max(x1 - x0, y1 - y0)))) / 100
    rot = 90.0 if y1 - y0 > 1.5 * (x1 - x0) else 0.0          # along a tall body, as KiCad's generators do
    a, dx, dy = math.radians(rot), -CHAR_W * h * 3 / 2, -h / 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x, y = cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)
    return dict(fp, texts=list(texts) + [(".Designator", round(x, 4), round(y, 4), h, round(0.15 * h, 3), rot, MECH13)])


def body_params(layer, height, model_id, checksum, standoff=0, step=None):
    """ComponentBody parameter text in Altium's key order (AltiumSharp BuildComponentBodyParamText).
    step=None: extruded body from standoff to height; else (name, rotx, roty, rotz, dz) of an embedded STEP model."""
    mil = lambda u: f"{to_mil(u)}mil"
    kv = [("V7_LAYER", v7_name(layer)), ("NAME", " "), ("KIND", 0), ("SUBPOLYINDEX", -1), ("UNIONINDEX", 0),
          ("ARCRESOLUTION", "0.5mil"), ("ISSHAPEBASED", "FALSE"), ("CAVITYHEIGHT", "0mil"), ("STANDOFFHEIGHT", mil(standoff)),
          ("OVERALLHEIGHT", mil(height)), ("BODYPROJECTION", 0), ("ARCRESOLUTION", "0.5mil"), ("BODYCOLOR3D", 8421504),
          ("BODYOPACITY3D", "1.000"), ("IDENTIFIER", ""), ("TEXTURE", ""), ("TEXTURECENTERX", "0mil"), ("TEXTURECENTERY", "0mil"),
          ("TEXTURESIZEX", "0mil"), ("TEXTURESIZEY", "0mil"), ("TEXTUREROTATION", " 0.00000000000000E+0000"),
          ("MODELID", model_id), ("MODEL.CHECKSUM", checksum), ("MODEL.EMBED", "TRUE" if step else "FALSE"),
          ("MODEL.NAME", step[0] if step else ""), ("MODEL.2D.X", "0mil"), ("MODEL.2D.Y", "0mil"), ("MODEL.2D.ROTATION", "0.000")]
    if step:
        kv += [("MODEL.3D.ROTX", f"{step[1]:.3f}"), ("MODEL.3D.ROTY", f"{step[2]:.3f}"), ("MODEL.3D.ROTZ", f"{step[3]:.3f}"),
               ("MODEL.3D.DZ", mil(step[4])), ("MODEL.MODELTYPE", 1), ("MODEL.MODELSOURCE", "Undefined")]
    else:
        kv += [("MODEL.3D.ROTX", "0.000"), ("MODEL.3D.ROTY", "0.000"), ("MODEL.3D.ROTZ", "0.000"), ("MODEL.3D.DZ", "0mil"),
               ("MODEL.MODELTYPE", 0), ("MODEL.EXTRUDED.MINZ", mil(standoff)), ("MODEL.EXTRUDED.MAXZ", mil(height))]
    return params(kv)[1:]                     # no leading '|'


def body_rec(pts, ptxt, layer=MECH13):
    """ComponentBody (record 12): common header, 5 reserved bytes, parameter block, outline as doubles."""
    body = common(layer) + b"\0" * 5 + pblock(ptxt) + struct.pack("<I", len(pts))
    body += b"".join(struct.pack("<2d", float(x), float(y)) for x, y in pts)
    return b"\x0c" + block(body)


def step_checksum(data):
    """Altium's 3D model checksum: position-weighted byte sum (AltiumSharp PcbModel.ComputeChecksum), as signed int32."""
    c = sum(b * (i or 1) for i, b in enumerate(data)) & 0xFFFFFFFF
    return c - (1 << 32) if c >= 1 << 31 else c


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
    layer, paste / mask (None = from rule, else manual expansion in internal units).
    Through-hole: layer MULTI, hole (diameter / slot width), hole_shape ('round'|'square'|'slot'),
    slot (slot length), hole_rot (degrees), plated (default True)."""
    shape = SHAPE[p["shape"]]
    rr = shape == 9
    base = 1 if rr else shape                  # Altium keeps 'round' in the main block for rounded-rect pads
    hs = HOLE[p.get("hole_shape", "round")]
    main = common(p.get("layer", TOP)) + struct.pack(
        "<9i3Bd?", p["x"], p["y"], p["w"], p["h"], p["w"], p["h"], p["w"], p["h"], p.get("hole", 0), base, base, base,
        float(p.get("rot", 0.0)), p.get("plated", True))
    t = bytearray(PAD_TAIL)
    put = lambda off, fmt, v: struct.pack_into(fmt, t, off - 61, v)
    put(62, "<B", 0)                           # pad stack mode: simple
    if p.get("paste") is not None:             # manual paste-mask expansion
        put(86, "<i", p["paste"]); put(101, "<B", 2)
    if p.get("mask") is not None:              # manual solder-mask expansion (KiCad's Altium importer / pyaltiumlib layout)
        put(90, "<i", p["mask"]); put(102, "<B", 2)
    put(114, "<I", v7_layer(p.get("layer", TOP)))
    put(185, "<B", 1 if p.get("layer") == MULTI else 3)    # reserved marker: 3 on SMD, 1 on multi-layer pads (TestData)
    t[126 - 61:142 - 61], t[142 - 61:158 - 61] = guid_a.bytes_le, guid_b.bytes_le
    out = sblock(pin_number(p["designator"], "pad designator")) + sblock("") + sblock("|&|0") + block(b"\0") + block(main + bytes(t))
    ox, oy = p.get("offset") or (0, 0)        # copper offset from the hole centre (internal units, pad frame, y up)
    if not rr and not hs and not (ox or oy):
        return b"\x02" + out + block(b"")
    ss = struct.pack("<29i", *[p["w"]] * 29) + struct.pack("<29i", *[p["h"]] * 29) + bytes([base]) * 29
    ss += b"\0" + bytes([hs]) + struct.pack("<id", p.get("slot", 0), float(p.get("hole_rot", 0.0)))
    ss += struct.pack("<32i", *[ox] * 32) + struct.pack("<32i", *[oy] * 32)      # per layer: 32 X, then 32 Y
    if rr:                                     # reserved, hole shape, slot length, hole rotation, offsets, then:
        ss += b"\x01" + bytes([9]) * 32 + bytes([max(0, min(100, int(round(p.get("corner", 50)))))]) * 32
    else:                                      # no rounded rect: flag 0, layer shapes 1, corners 0 (TestData slot pads)
        ss += b"\0" + b"\x01" * 32 + b"\0" * 32
    assert len(ss) == 596
    return b"\x02" + out + block(ss)


# ---------------------------------------------------------------- footprints: model & converters
def round_rect(x, y, w, h, r, steps=16):
    """Outline of a w x h rectangle centred on (x, y) with corner radius r, each corner as `steps` chords (16: the
    area is within 0.05 % of the true rounded rectangle for KiCad's 25 % paste-window rounding)."""
    pts = []
    for cx, cy, a0 in ((x + w / 2 - r, y + h / 2 - r, 0), (x - w / 2 + r, y + h / 2 - r, 90),
                       (x - w / 2 + r, y - h / 2 + r, 180), (x + w / 2 - r, y - h / 2 + r, 270)):
        for k in range(steps + 1):
            a = math.radians(a0 + 90 * k / steps)
            q = (round(cx + r * math.cos(a), 6), round(cy + r * math.sin(a), 6))
            if not pts or q != pts[-1]:       # r = w/2: neighbouring corners meet in one point
                pts.append(q)
    return pts[:-1] if len(pts) > 1 and pts[-1] == pts[0] else pts


def footprint_from_kicad(text, height_mm=0.0, description=None, notes=None):
    """Convert a .kicad_mod (as written by footprint_gen, or a KiCad library footprint: app.build_altium_models) into the
    writer's footprint dict (mm, y up).
    SMD pads are converted here; through-hole and NPTH pads (round, square, slots), custom (polygon) pads and keep-out
    zones go through kicad_import's converter. notes: list that collects what could not be carried over exactly."""
    from . import kicad_import as K
    notes = [] if notes is None else notes
    t = K.inherit_margins(sexpr.parse(text))             # footprint-level mask / paste margins apply to every pad
    fp = {"name": t[1], "description": clean(description if description is not None else (sexpr.first(t, "descr") or ["", ""])[1]),
          "height_mm": height_mm, "pads": [], "tracks": [], "fills": [], "regions": [], "arcs": [], "texts": texts_from_kicad(t)}
    for p in sexpr.find(t, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y, w, h = float(at[1]), -float(at[2]), float(size[1]), float(size[2])
        rot = float(at[3]) if len(at) > 3 else 0.0
        layers = (sexpr.first(p, "layers") or ["layers"])[1:]
        off = sexpr.first(sexpr.first(p, "drill") or [], "offset")
        if p[2] != "smd" or p[3] == "custom" or any(str(l).startswith("B.") for l in layers) \
                or (off and any(float(v) for v in off[1:3])):
            K._pad(fp, p, notes); continue    # (a KiCad library footprint: bottom pads, copper offset from the anchor)
        if "F.Cu" not in layers:              # paste-only aperture (EP window) -> Top Paste fill or region; mask-only
            if layers not in (["F.Paste"], ["F.Mask"]) or rot:   # (a mask-defined thermal land's opening) -> Top Solder
                raise ValueError(f"{fp['name']}: unsupported non-copper pad on {layers}")
            if layers == ["F.Mask"] and p[3] != "rect":
                raise ValueError(f"{fp['name']}: unsupported {p[3]} mask-only pad")
            if layers == ["F.Mask"]:
                fp["regions"].append(([(x - w / 2, y - h / 2), (x + w / 2, y - h / 2), (x + w / 2, y + h / 2),
                                       (x - w / 2, y + h / 2)], TOP_SOLDER))      # as kicad_import writes mask windows
                continue
            rr = sexpr.first(p, "roundrect_rratio")
            r = min(float(rr[1]), 0.5) * min(w, h) if rr and p[3] == "roundrect" else 0.0
            if r > 0:                         # rounded window (the rounding controls EP voiding): a region with
                fp["regions"].append((round_rect(x, y, w, h, r), TOP_PASTE))   # the same outline (EXP-10)
            else:
                fp["fills"].append((x - w / 2, y - h / 2, x + w / 2, y + h / 2, TOP_PASTE))
            continue
        rr = sexpr.first(p, "roundrect_rratio")
        pad = {"designator": p[1], "x": x, "y": y, "w": w, "h": h, "rot": rot, "shape": p[3],
               "corner": float(rr[1]) * 200 if rr else 0,          # KiCad r/min(w,h) -> Altium r/(min(w,h)/2) %
               "paste": None if "F.Paste" in layers else "none"}
        if not {"F.Mask", "*.Mask"} & set(layers):                # tented: a mask-only pad opens it (PowerPAD land)
            pad["mask"] = "none"
        elif sexpr.first(p, "solder_mask_margin"):                 # e.g. BGA NSMD: mask opening = land + margin
            pad["mask"] = float(sexpr.first(p, "solder_mask_margin")[1])
        if "F.Paste" in layers and sexpr.first(p, "solder_paste_margin"):   # e.g. SMD-defined BGA land: paste = land
            pad["paste"] = float(sexpr.first(p, "solder_paste_margin")[1])
        if p[3] not in SHAPE:
            raise ValueError(f"{fp['name']}: pad shape {p[3]} not supported")
        fp["pads"].append(pad)
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexpr.find(t, kind):
            layer = KICAD_LAYER.get((sexpr.first(g, "layer") or [None, None])[1])
            if layer is None:
                continue
            if kind in ("fp_circle", "fp_arc"):    # a round part's courtyard, a pin-1 arc (KiCad library footprints)
                K._graphic(fp, kind, g, layer, notes)
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
    if sexpr.find(t, "zone"):
        K._zones(fp, t, notes)
    return fp


def footprint_from_spec(spec, density=None):
    """footprint_gen.build(spec) -> footprint dict (uses the same geometry as the .kicad_mod). density: A/B/C, or
    None for the spec's own (then B)."""
    from . import footprint_gen as F
    txt, rep = F.build(spec, density)
    d = f"{spec.get('description', spec['name'])}; IPC-7351B {rep['ipc_family']} density {rep['density']}"
    notes = []
    fp = footprint_from_kicad(txt, F.dim(spec.get("height_max", 0))[2] if spec.get("height_max") else 0.0, d, notes)
    if notes:
        rep = dict(rep, altium_notes=notes)
    return fp, rep


def _model_id(seed):
    return "{" + str(guid(seed)).upper() + "}"


def _pcb_component(fp, models=None):
    """-> (storage dict, primitive count, pad count) for one footprint. Embedded STEP models are
    appended to `models` as (params text, STEP bytes) for the Library/Models storage."""
    name = fp["name"]
    gb = guid(name + "/stack")
    data = bytearray(sblock(name)); n = 0
    for i, p in enumerate(fp["pads"]):
        q = dict(p, x=mm(p["x"]), y=mm(p["y"]), w=mm(p["w"]), h=mm(p["h"]), hole=mm(p.get("hole", 0)), slot=mm(p.get("slot", 0)),
                 offset=tuple(mm(v) for v in p["offset"]) if p.get("offset") else None)
        if q["shape"] != "roundrect":
            q["corner"] = 0
        if p.get("paste") == "none":         # suppress paste: manual expansion of -max(w,h)/2 closes the aperture
            q["paste"] = -max(q["w"], q["h"]) // 2 - mm(0.01)
        elif isinstance(p.get("paste"), float):    # manual expansion in mm
            q["paste"] = mm(p["paste"])
        if p.get("mask") == "none":          # tented: a manual expansion of -max(w,h)/2 closes the mask opening
            q["mask"] = -max(q["w"], q["h"]) // 2 - mm(0.01)
        elif p.get("mask") is not None:      # manual expansion in mm
            q["mask"] = mm(p["mask"])
        data += pad_rec(q, guid(f"{name}/pad{i}"), gb); n += 1
    for x1, y1, x2, y2, w, layer in fp["tracks"]:
        data += track_rec(mm(x1), mm(y1), mm(x2), mm(y2), mm(w), layer); n += 1
    for x1, y1, x2, y2, layer in fp["fills"]:
        data += fill_rec(mm(x1), mm(y1), mm(x2), mm(y2), layer); n += 1
    for pts, layer, *ko in fp["regions"]:     # optional third item: keep-out restriction bits
        data += region_rec([(mm(x), mm(y)) for x, y in pts], layer, keepout=ko[0] if ko else None); n += 1
    for cx, cy, r, a0, a1, w, layer in fp.get("arcs", []):
        data += arc_rec(mm(cx), mm(cy), mm(r), a0, a1, mm(w), layer); n += 1
    wide = []
    for txt, x, y, h, w, rot, layer in fp.get("texts", []):
        data += text_rec(txt, mm(x), mm(y), mm(h), mm(w), layer, rot, len(wide)); n += 1
        wide.append((f"ENCODEDTEXT{len(wide)}", ",".join(str(ord(c)) for c in txt)))
    for k, b in enumerate(fp.get("bodies", [])):     # last: pyaltiumlib reads a trailing block after a body
        pts, st = [(mm(x), mm(y)) for x, y in b["pts"]], b.get("step")
        if st:
            ck = step_checksum(st["data"])
            mid = _model_id(f"{name}/model/{ck}")
            models.append((params([("EMBED", "TRUE"), ("MODELSOURCE", "Undefined"), ("ID", mid), ("ROTX", f"{st['rot'][0]:.3f}"),
                                   ("ROTY", f"{st['rot'][1]:.3f}"), ("ROTZ", f"{st['rot'][2]:.3f}"), ("DZ", mm(st.get("dz", 0))),
                                   ("CHECKSUM", ck), ("NAME", clean(st["name"]))])[1:], st["data"]))
            ptxt = body_params(fp.get("body_layer", MECH13), mm(b["height"]), mid, ck & 0xFFFFFFFF, mm(b.get("standoff", 0)),
                               (clean(st["name"]), *st["rot"], mm(st.get("dz", 0))))
        else:                                 # extruded: Altium-made bodies carry a model id and checksum too
            ptxt = body_params(fp.get("body_layer", MECH13), mm(b["height"]), _model_id(f"{name}/body{k}"),
                               int.from_bytes(hashlib.sha256(f"{name}/body{k}".encode()).digest()[:4], "little"), mm(b.get("standoff", 0)))
        data += body_rec(pts, ptxt, fp.get("body_layer", MECH13)); n += 1
    height = f"{to_mil(mm(fp.get('height_mm') or 0))}mil"
    par = params([("PATTERN", name), ("HEIGHT", height), ("DESCRIPTION", clean(fp.get("description", ""))),
                  ("ITEMGUID", ""), ("REVISIONGUID", "")])
    st = {"Header": struct.pack("<I", n), "Parameters": pblock(par), "WideStrings": pblock(params(wide)), "Data": bytes(data)}
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


def pcblib_bytes(footprints, filename="ecadlib.PcbLib", stamp=None):
    """stamp: (DATE, TIME) for the library header; default now (or SOURCE_DATE_EPOCH)."""
    footprints = [dict(fp, name=lib_name(fp["name"])) for fp in footprints]
    names = [fp["name"] for fp in footprints]
    keys = _section_keys(names, PCBLIB_RESERVED, "footprint")
    tpl, (date, tm) = _template(), stamp or _now()     # cached: treat tpl as read-only
    hdr = re.sub(r"\|FILENAME=[^|]*", "|FILENAME=" + clean(filename), tpl["library_params"], count=1)
    hdr = re.sub(r"\|DATE=[^|]*", "|DATE=" + date, hdr, count=1)
    hdr = re.sub(r"\|TIME=[^|]*", "|TIME=" + tm, hdr, count=1)
    seed = "|".join(names)
    tree = {"FileHeader": struct.pack("<IB", 27, 27) + b"PCB 6.0 Binary Library File" + struct.pack("<d", 5.01)
            + struct.pack("<IB", 8, 8) + unique_id(seed).encode()}
    long = [(n, k) for n, k in zip(names, keys) if len(n) >= 31 or k != n]
    if long:                                  # binary: [u32 count] then (name, key) string blocks
        tree["SectionKeys"] = struct.pack("<I", len(long)) + b"".join(sblock(n) + sblock(k) for n, k in sorted(long, key=lambda t: t[0].upper()))
    toc, comps, models = "", {}, []
    for fp, key in zip(footprints, keys):
        st, npads, h = _pcb_component(fp, models)
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
        "Models": {"Header": hs(len(models)), "Data": b"".join(pblock(t) for t, _ in models),
                   **{str(i): zlib.compress(d, 9) for i, (_, d) in enumerate(models)}},
        "ModelsNoEmbed": empty(), "Textures": empty()}
    tree["FileVersionInfo"] = {"Header": hs(1), "Data": pblock(tpl["file_version_info"])}
    tree.update(comps)
    return cfb.to_bytes(tree)


def remap_layers(fp, layers):
    """Footprint dict with ecadlib's layers moved to the library rules' ones. layers: {role: layer id} from
    rules.layers() (courtyard, assembly, body3d, silkscreen, designator); None or {} leaves fp as it is."""
    if not layers:
        return fp
    graphics = {MECH15: layers.get("courtyard", MECH15), MECH13: layers.get("assembly", MECH13),
                TOP_OVERLAY: layers.get("silkscreen", TOP_OVERLAY)}
    g = lambda l: graphics.get(l, l)
    text = lambda t: (layers.get("designator", TOP_OVERLAY) if t[0] == ".Designator" and t[6] == TOP_OVERLAY else g(t[6]))
    out = dict(fp, tracks=[(*t[:5], g(t[5])) for t in fp.get("tracks", [])],
               fills=[(*f[:4], g(f[4])) for f in fp.get("fills", [])],
               regions=[(r[0], g(r[1]), *r[2:]) for r in fp.get("regions", [])],
               arcs=[(*a[:6], g(a[6])) for a in fp.get("arcs", [])],
               texts=[(*t[:6], text(t)) for t in fp.get("texts", [])])
    if layers.get("body3d", MECH13) != MECH13:
        out["body_layer"] = layers["body3d"]
    return out


def write_pcblib(path, footprints, layers=None):
    """layers: {role: layer id} (rules.layers(rules)) to put courtyard, assembly, 3D body and silkscreen where the
    library keeps them; None = ecadlib's layers (Mechanical 15 / 13, Top Overlay)."""
    from .fsutil import write_bytes_atomic
    path = Path(path)
    fps = [remap_layers(add_fab_designator(fp), layers) for fp in footprints]
    from . import altium_native as AN
    old = _pcblib_stamp(path)
    if old and path.read_bytes() == pcblib_bytes(fps, path.name, old):
        AN.write_recipe(path, "PCBLIB", fps)
        return path                    # the same library: leave the file (and the date in it) alone, so a re-run
    write_bytes_atomic(path, pcblib_bytes(fps, path.name))    # changes nothing on disk or in git
    AN.write_recipe(path, "PCBLIB", fps)   # the same content as a recipe Altium can build from (ecadlib.altium_native)
    return path


def _pcblib_stamp(path):
    """(DATE, TIME) in an existing .PcbLib's library header, or None."""
    if not Path(path).is_file():
        return None
    try:
        import olefile
        with olefile.OleFileIO(str(path)) as o:
            hdr = o.openstream("Library/Data").read(4096)
    except Exception:                  # not ours, damaged, not OLE: just write the new file
        return None
    d, t = re.search(rb"\|DATE=([^|]*)", hdr), re.search(rb"\|TIME=([^|]*)", hdr)
    return (d.group(1).decode("latin-1"), t.group(1).decode("latin-1")) if d and t else None


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


# Pin IEEE symbols (Altium TIeeeSymbol ids). Altium's pin dialog offers clock on the inside edge; dot and active-low
# input/output on the outside edge; signal-flow arrows, analog/digital in and not-logic outside; the rest inside.
PIN_SYMBOL = {"dot": 1, "right_left_signal_flow": 2, "clock": 3, "active_low_input": 4, "analog_signal_in": 5,
              "not_logic_connection": 6, "postponed_output": 8, "open_collector": 9, "hiz": 10, "high_current": 11, "pulse": 12,
              "schmitt": 13, "active_low_output": 17, "open_collector_pull_up": 22, "open_emitter": 23, "open_emitter_pull_up": 24,
              "digital_signal_in": 25, "shift_left": 30, "open_output": 32, "left_right_signal_flow": 33, "bidirectional_signal_flow": 34}
PIN_SLOTS = ("inside_edge", "outside_edge", "inside", "outside")         # binary pin record order
# KiCad pin shapes; falling-edge clock has no Altium symbol and becomes clock + active-low input
PIN_SHAPE = {"line": {}, "inverted": {"outside_edge": "dot"}, "clock": {"inside_edge": "clock"},
             "inverted_clock": {"inside_edge": "clock", "outside_edge": "dot"}, "input_low": {"outside_edge": "active_low_input"},
             "clock_low": {"inside_edge": "clock", "outside_edge": "active_low_input"}, "output_low": {"outside_edge": "active_low_output"},
             "edge_clock_high": {"inside_edge": "clock", "outside_edge": "active_low_input"},
             "non_logic": {"outside": "not_logic_connection"}}


def pin_symbols(shape):
    """None, a PIN_SHAPE name or {slot: PIN_SYMBOL name or id} -> (inside edge, outside edge, inside, outside) ids."""
    d = PIN_SHAPE.get(shape) if isinstance(shape, str) else shape or {}
    if not isinstance(d, dict) or set(d) - set(PIN_SLOTS):
        raise ValueError(f"pin shape {shape!r}: use one of {list(PIN_SHAPE)} or a dict with keys {list(PIN_SLOTS)}")
    out = tuple(PIN_SYMBOL.get(d.get(k, 0), d.get(k, 0)) for k in PIN_SLOTS)
    if any(v not in (0, *PIN_SYMBOL.values()) for v in out):
        raise ValueError(f"pin shape {shape!r}: symbols are {list(PIN_SYMBOL)}")
    return out


def overline(s):
    """KiCad '~{RESET}' overbar -> Altium 'R\\E\\S\\E\\T\\' (a backslash after each overlined character)."""
    return "" if s == "~" else re.sub(r"~\{([^}]*)\}", lambda m: "".join(c + "\\" for c in m.group(1)), s)


def pin_rec(num, name, etype, orient, x, y, length, show=True, hidden=False, part=1, show_name=None, show_num=None,
            symbols=(0, 0, 0, 0), mode=0, desc=""):
    """symbols: (inside edge, outside edge, inside, outside) IEEE symbol ids; mode: owner display mode (0 = normal)."""
    sn, sd = show if show_name is None else show_name, show if show_num is None else show_num
    lost = not_cp1252(name)
    if lost:                                  # the binary pin record holds cp1252 Pascal strings only (no %UTF8% twin)
        warnings.warn(f"pin {num}: name {name!r} has characters a SchLib pin record can't store ({lost!r}); it is written "
                      f"as {enc(name).decode('cp1252')!r}, and a library Altium rebuilds may show a best-fit substitute "
                      f"(e.g. 'F' for 'Φ')", PinNameWarning, stacklevel=2)
    cong = orient | 0x20 | (0x04 if hidden else 0) | (0x08 if sn else 0) | (0x10 if sd else 0)   # 0x04 hidden, 0x08 name, 0x10 number
    d = enc(desc)[:255]
    b = struct.pack("<iBhB4B", 2, 0, part, mode, *symbols) + bytes([len(d)]) + d      # owner part, display mode, symbols, description
    b += struct.pack("<BBBhhhI", 1, ETYPE[etype], cong, length, x, y, 0)
    strs = [enc(s)[:255] for s in (name, pin_number(num), "", "|&|", "")]       # Pascal short strings
    b += b"".join(bytes([len(e)]) + e for e in strs)
    return block(b, flag=1)


def ic_symbol(name, pins, description="", designator="U?", footprint=None, comment="*", layout=None, rules=None):
    """Rectangular IC: pins = [{'num','name','type','side' in left/right/top/bottom, optional 'shape'}], listed
    top->bottom (left/right) or left->right (top/bottom). 'shape' is a PIN_SHAPE name ('inverted', 'clock', ...) or
    {slot: PIN_SYMBOL name} (see pin_symbols); '~{EN}' in a name is overlined. Body grows to fit pin names (100 mil grid).
    layout: None = pins grouped by function (ecadlib.symbol_layout, 'side' ignored) when there are at least the rules'
    [symbols] layout_pins pins (default 1: always), True = always, False = never. rules: loaded rules or [symbols] overrides (None = defaults)."""
    bad = [p.get("num") for p in pins if p.get("side", "left") not in ORIENT]
    if bad:
        raise ValueError(f"{name}: pins {bad} have a side other than {list(ORIENT)}")
    from . import symbol_layout as SL
    pins = [dict(p, type=SL._ptype(p.get("type"))) for p in pins]   # 'Open Drain', 'Input': ETYPE keys (pin_rec)
    if layout is not False:
        if layout or SL.wants_layout(pins, rules):
            return SL.build(name, pins, description, designator, footprint, comment, rules)
    side = {s: [p for p in pins if p.get("side", "left") == s] for s in ORIENT}
    rows = max(len(side["left"]), len(side["right"]), 1)
    cols = max(len(side["top"]), len(side["bottom"]))
    nlen = lambda p: len(overline(p["name"]).replace("\\", ""))                # overline backslashes take no room
    chars = max([nlen(p) for p in side["left"]] or [0]) + max([nlen(p) for p in side["right"]] or [0])
    w = max(40, 10 * -(-(chars * 7 + 20) // 10), 10 * (cols + 1))   # ~7 units (70 mil) per 10 pt char
    room = lambda ps: 10 * -(-(max(nlen(p) for p in ps) * 7 + 10) // 10) if ps else 0
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


def resistor_symbol(name="Resistor", description="Resistor", footprint=None, style="us"):
    """style "us": zigzag body; "iec": box body ([symbols] resistor)."""
    pins = [{"num": "1", "name": "1", "type": "passive"}, {"num": "2", "name": "2", "type": "passive"}]
    body = (("rect", -15, 4, 15, -4) if style == "iec" else
            ("polyline", [(-15, 0), (-12.5, 4), (-7.5, -4), (-2.5, 4), (2.5, -4), (7.5, 4), (12.5, -4), (15, 0)], 1))
    prims = [body, ("pin", pins[0], "left", -15, 0, 15, False), ("pin", pins[1], "right", 15, 0, 15, False)]
    return {"name": name, "description": description, "designator": "R?", "comment": "*", "footprint": footprint,
            "prims": prims, "label_at": ((-15, 7), (-15, -14))}


def capacitor_symbol(name="Capacitor", description="Capacitor (non-polarised)", footprint=None):
    pins = [{"num": "1", "name": "1", "type": "passive"}, {"num": "2", "name": "2", "type": "passive"}]
    prims = [("line", -3, 8, -3, -8, 2), ("line", 3, 8, 3, -8, 2),
             ("pin", pins[0], "left", -3, 0, 27, False), ("pin", pins[1], "right", 3, 0, 27, False)]
    return {"name": name, "description": description, "designator": "C?", "comment": "*", "footprint": footprint,
            "prims": prims, "label_at": ((-15, 11), (-15, -18))}


def symbol_from_json(d, rules=None):
    """Symbol JSON -> symbol dict. An IC's optional "layout": true/false forces the grouped layout on/off (see ic_symbol)."""
    kind = d.get("template", "ic")
    if kind == "resistor":
        return resistor_symbol(d.get("name", "Resistor"), d.get("description", "Resistor"), d.get("footprint"))
    if kind == "capacitor":
        return capacitor_symbol(d.get("name", "Capacitor"), d.get("description", "Capacitor"), d.get("footprint"))
    return ic_symbol(d["name"], d["pins"], d.get("description", ""), d.get("designator", "U?"), d.get("footprint"),
                     layout=d.get("layout"), rules=rules)


def _xy(kv, pts):                             # polyline/polygon vertices: LocationCount, X1/Y1 (+ _Frac), ...
    kv.append(("LocationCount", len(pts)))
    for k, (x, y) in enumerate(pts, 1):
        _coord(kv, f"X{k}", x); _coord(kv, f"Y{k}", y)


def _fill(kv, fill):                          # 'body' = Altium symbol fill, 'line' = solid in line colour, else hollow
    kv += [("Color", BLUE), ("AreaColor", {"body": BODY_FILL, "line": BLUE}.get(fill, 16777215))]
    kv += [("IsSolid", "T")] if fill in ("body", "line") else []


def _sch_component(sym):
    """-> (Data stream, record count, pin count). sym['parts'] (default 1) and sym['prim_parts'] (owner part
    per primitive) describe multi-part symbols; sym['modes'] (default 1) and sym['prim_modes'] (display mode per
    primitive, 0 = normal, 1 = first alternate/De Morgan) describe alternate views; sym['params'] = [(name, value)]
    become hidden parameters."""
    nm, recs, idx_sheet = sym["name"], [], 0
    uid = lambda k: unique_id(f"{nm}/{k}")
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    modes = sym.get("prim_modes") or [0] * len(sym["prims"])
    npins = sum(1 for p, m in zip(sym["prims"], modes) if p[0] == "pin" and not m)     # AllPinCount: normal view only (AD24)
    comp = [("RECORD", 1), ("LibReference", nm)]
    if sym.get("description"):
        comp.append(("ComponentDescription", clean(sym["description"])))
    comp += [("PartCount", sym.get("parts", 1) + 1), ("DisplayModeCount", sym.get("modes", 1)), ("IndexInSheet", -1), ("OwnerPartId", -1), ("CurrentPartId", 1),
             ("LibraryPath", "*"), ("SourceLibraryName", "*"), ("SheetPartFileName", "*"), ("TargetFileName", "*"),
             ("UniqueID", uid("comp")), ("AreaColor", BODY_FILL), ("Color", BLUE), ("PartIDLocked", "T"),
             ("DesignItemId", nm), ("AllPinCount", npins)]
    recs.append(schblock(params(comp)))
    for i, pr in enumerate(sym["prims"]):
        common_ = [("IsNotAccesible", "T")] + ([("IndexInSheet", i)] if i else []) + [("OwnerPartId", parts[i])]
        common_ += [("OwnerPartDisplayMode", modes[i])] if modes[i] else []
        if pr[0] == "rect":                   # ("rect", x1, y1, x2, y2[, line width, fill])
            kv = [("RECORD", 14)] + common_
            for k, v in zip(("Location.X", "Location.Y", "Corner.X", "Corner.Y"), (min(pr[1], pr[3]), min(pr[2], pr[4]), max(pr[1], pr[3]), max(pr[2], pr[4]))):
                _coord(kv, k, v)
            kv += [("LineWidth", pr[5])] if len(pr) > 5 and pr[5] else []
            _fill(kv, pr[6] if len(pr) > 6 else "body")
            recs.append(schblock(params(kv + [("UniqueID", uid(i))])))
        elif pr[0] in ("polyline", "polygon"):   # (kind, [(x, y)...], line width[, fill])
            kv = [("RECORD", 6 if pr[0] == "polyline" else 7)] + common_ + [("LineWidth", pr[2])]
            if pr[0] == "polygon":
                _fill(kv, pr[3])
            else:
                kv.append(("Color", BLUE))
            _xy(kv, pr[1])
            recs.append(schblock(params(kv + [("UniqueID", uid(i))])))
        elif pr[0] == "arc":                  # ("arc", cx, cy, r, start deg, end deg, line width), counter-clockwise
            kv = [("RECORD", 12)] + common_
            _coord(kv, "Location.X", pr[1]); _coord(kv, "Location.Y", pr[2]); _coord(kv, "Radius", pr[3])
            kv += [("LineWidth", pr[6])] + ([("StartAngle", f"{pr[4]:.3f}")] if pr[4] else []) + [("EndAngle", f"{pr[5]:.3f}"), ("Color", BLUE)]
            recs.append(schblock(params(kv + [("UniqueID", uid(i))])))
        elif pr[0] == "circle":               # ("circle", cx, cy, r, line width, fill)
            kv = [("RECORD", 8)] + common_
            _coord(kv, "Location.X", pr[1]); _coord(kv, "Location.Y", pr[2]); _coord(kv, "Radius", pr[3]); _coord(kv, "SecondaryRadius", pr[3])
            kv.append(("LineWidth", pr[4])); _fill(kv, pr[5])
            recs.append(schblock(params(kv + [("UniqueID", uid(i))])))
        elif pr[0] == "text":                 # ("text", x, y, text, orientation 0..3)
            kv = [("RECORD", 4)] + common_
            _coord(kv, "Location.X", pr[1]); _coord(kv, "Location.Y", pr[2])
            kv += ([("Orientation", pr[4])] if pr[4] else []) + [("Color", BLUE), ("FontID", 1), ("Text", clean(pr[3]))]
            recs.append(schblock(params(kv + [("UniqueID", uid(i))])))
        elif pr[0] == "line":
            kv = [("RECORD", 13)] + common_
            for k, v in zip(("Location.X", "Location.Y", "Corner.X", "Corner.Y"), pr[1:5]):
                _coord(kv, k, v)
            kv += [("LineWidth", pr[5]), ("Color", BLUE), ("UniqueID", uid(i))]
            recs.append(schblock(params(kv)))
        else:
            _, p, side, x, y, length, *show = pr
            recs.append(pin_rec(p["num"], overline(p["name"]), p.get("type", "passive"), ORIENT[side], x, y, length, show[0] if show else True,
                                p.get("hidden", False), parts[i], p.get("show_name"), p.get("show_num"), pin_symbols(p.get("shape")), modes[i],
                                clean(p.get("description", ""))))
            if p.get("net"):                  # hidden power pin: connects to this net (Altium 'HiddenNetName')
                recs.append(schblock(params([("RECORD", 41), ("OwnerIndex", len(recs) - 1), ("OwnerPartId", -1), ("Color", PARAM_COLOR),
                                           ("FontID", 1), ("Text", clean(p["net"])), ("Name", "HiddenNetName")])))
    (dx, dy), (cx, cy) = sym["label_at"]
    for rec, nm_, text, (x, y) in ((34, "Designator", sym.get("designator", "U?"), (dx, dy)), (41, "Comment", sym.get("comment", "*"), (cx, cy))):
        kv = [("RECORD", rec), ("IndexInSheet", -1), ("OwnerPartId", -1)]
        _coord(kv, "Location.X", x); _coord(kv, "Location.Y", y)
        kv += [("Color", PARAM_COLOR), ("FontID", 1), ("Text", clean(text)), ("Name", nm_)]
        kv += [("ReadOnlyState", 1)] if rec == 34 else []
        kv += [("UniqueID", uid(nm_))]
        recs.append(schblock(params(kv)))
    for k, (pn, pv) in enumerate(sym.get("params", [])):
        kv = [("RECORD", 41), ("IndexInSheet", -1), ("OwnerPartId", -1)]
        _coord(kv, "Location.X", cx); _coord(kv, "Location.Y", cy - 10 * (k + 1))
        kv += [("Color", PARAM_COLOR), ("FontID", 1), ("IsHidden", "T"), ("Text", clean(pv)), ("Name", clean(pn)), ("UniqueID", uid(f"param{k}"))]
        recs.append(schblock(params(kv)))
    recs.append(schblock("|RECORD=44"))
    if sym.get("footprint"):
        il = len(recs) - 1
        fpn = clean(sym["footprint"])
        recs.append(schblock(params([("RECORD", 45), ("OwnerIndex", il), ("IndexInSheet", -1), ("ModelName", fpn),
                                   ("ModelType", "PCBLIB"), ("DatafileCount", 1), ("ModelDatafileEntity0", fpn),
                                   ("ModelDatafileKind0", "PCBLib"), ("IsCurrent", "T"), ("UniqueID", uid("impl"))])))
        recs += [schblock(f"|RECORD=46|OwnerIndex={il + 1}"), schblock(f"|RECORD=48|OwnerIndex={il + 1}")]
    return b"".join(recs), len(recs), npins


def schlib_bytes(symbols):
    symbols = [dict(s, name=lib_name(s["name"]), **({"footprint": lib_name(s["footprint"])} if s.get("footprint") else {}))
               for s in symbols]
    names = [s["name"] for s in symbols]
    keys = _section_keys(names, SCHLIB_RESERVED, "symbol")
    tree, total, lib = {}, 0, []
    for i, (s, k) in enumerate(zip(symbols, keys)):
        data, n, _ = _sch_component(s)
        tree[k] = {"Data": data}
        total += n
        lib += [(f"LibRef{i}", s["name"])] + ([(f"CompDescr{i}", clean(s["description"]))] if s.get("description") else []) + [(f"PartCount{i}", s.get("parts", 1) + 1)]
    hdr = [("HEADER", "Protel for Windows - Schematic Library Editor Binary File Version 5.0"), ("Weight", total + 1),
           ("MinorVersion", 9), ("UniqueID", unique_id("|".join(names))), ("FontIdCount", 1), ("Size1", 10),
           ("FontName1", "Times New Roman"), ("UseMBCS", "T"), ("IsBOC", "T"), ("SheetStyle", 9), ("BorderOn", "T"),
           ("SheetNumberSpaceSize", 12), ("AreaColor", 16317695), ("SnapGridOn", "T"), ("SnapGridSize", 10),
           ("VisibleGridOn", "T"), ("VisibleGridSize", 10), ("CustomX", 18000), ("CustomY", 18000),
           ("UseCustomSheet", "T"), ("ReferenceZonesOn", "T"), ("Display_Unit", 0), ("CompCount", len(symbols))] + lib
    tree["FileHeader"] = schblock(params(hdr))
    long = [(n, k) for n, k in zip(names, keys) if k != n]
    if long:
        kv = [("KeyCount", len(long))]
        for i, (n, k) in enumerate(long):
            kv += [(f"LibRef{i}", n), (f"SectionKey{i}", k)]
        tree["SectionKeys"] = schblock(params(kv))
    tree["Storage"] = schblock("|HEADER=Icon storage")
    return cfb.to_bytes(tree)


def write_schlib(path, symbols):
    from .fsutil import write_bytes_atomic
    path = Path(path)
    from . import altium_native as AN
    data = schlib_bytes(symbols)
    if not (path.is_file() and path.read_bytes() == data):      # unchanged: not rewritten
        write_bytes_atomic(path, data)
    AN.write_recipe(path, "SCHLIB", [dict(s, name=lib_name(s["name"])) for s in symbols])
    return path


def relink_schlib(path, old, new):
    """Point the footprint links (RECORD=45) of a .SchLib on disk that name footprint `old` at `new`, and its recipe's
    FOOTPRINTLINK lines: write_schlib(dict(sym, footprint=new)) for a file whose symbol dicts are gone (resolve).
    Nothing else in the file changes. -> True when a link was changed."""
    import olefile
    from .fsutil import write_bytes_atomic
    from . import altium_native as AN
    path, old, new = Path(path), lib_name(old), lib_name(new)
    tree, hit = {}, False
    with olefile.OleFileIO(str(path)) as o:
        for parts in o.listdir(streams=True, storages=False):
            data = o.openstream(parts).read()
            if parts[-1] == "Data" and len(parts) == 2:
                out, j = b"", 0
                while j < len(data):
                    flag, n = data[j + 3], struct.unpack_from("<I", data, j)[0] & 0xFFFFFF
                    b, j = data[j + 4:j + 4 + n], j + 4 + n
                    rec = parse_param_bytes(b) if flag == 0 else {}
                    if rec.get("RECORD") == "45" and rec.get("MODELNAME") == old:
                        kv = []
                        for seg in b.rstrip(b"\0").split(b"|"):
                            k, eq, v = seg.partition(b"=")
                            if eq and not k.startswith(b"%UTF8%"):
                                k = k.decode("cp1252")
                                kv.append((k, new if k.upper() in ("MODELNAME", "MODELDATAFILEENTITY0") else rec[k.upper()]))
                        out += schblock(params(kv))
                        hit = True
                    else:
                        out += block(b, flag)
                data = out
            node = tree
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = data
    if not hit:
        return False
    order = ("FileHeader", "SectionKeys", "Storage")      # schlib_bytes' order: the same symbol, the same bytes
    tree = dict(sorted(tree.items(), key=lambda kv: (kv[0] in order, order.index(kv[0]) if kv[0] in order else 0)))
    write_bytes_atomic(path, cfb.to_bytes(tree))
    rp = path.with_name(path.name + AN.EXT)
    if rp.is_file():
        text = rp.read_text(encoding="utf-8")
        rp.write_text("\n".join(f"FOOTPRINTLINK\t{new}" if ln == f"FOOTPRINTLINK\t{old}" else ln
                                for ln in text.split("\n")), encoding="utf-8")
    return True


# ---------------------------------------------------------------- minimal reader (verification / dump)
def _blk(d, i):
    n = struct.unpack_from("<I", d, i)[0] & 0xFFFFFF
    return d[i + 4:i + 4 + n], i + 4 + n


def _str(b):
    return b[1:1 + b[0]].decode("cp1252") if b else ""


def _param_stream(o, path):
    return parse_param_bytes(_blk(o.openstream(path).read(), 0)[0])


def parse_params(text):
    out = {}
    for kv in text.split("|"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.upper()] = v
    return out


def parse_param_bytes(b):
    """A |KEY=VALUE| block as Altium writes it: cp1252, except '%UTF8%KEY' twins (UTF-8) that hold the real text when
    cp1252 can't (Altium then writes '?' in the plain KEY); the twin wins, under the plain key."""
    out, utf = {}, {}
    for kv in b.rstrip(b"\0").split(b"|"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            k = k.decode("cp1252", "replace").upper()
            if k.startswith("%UTF8%"):
                utf[k[6:]] = v.decode("utf-8", "replace")
            else:
                out[k] = v.decode("cp1252", "replace")
    out.update(utf)
    return out


V7_AT = {"track": 41, "fill": 42, "arc": 52, "text": 226, "pad": 114}     # v7 layer id offset in the record's (main) block


def extended_layer(layer, block_, kind=None, params_=None):
    """Altium's layer id for a record on Mechanical 17..32 (66 + n, as Altium's layer enum and pyaltiumlib number them).
    The one-byte layer of such records says Mechanical 16 (72); the real layer is in the v7 layer id (0x0102_00nn) of the
    record's tail, or V7_LAYER=MECHANICALnn in a region / body's parameters. Other layers come back unchanged."""
    if layer != MECH16:
        return layer
    n = 0
    if params_ is not None:
        m = re.search(r"(?:^|\|)V7_LAYER=MECHANICAL(\d+)", params_, re.I)
        n = int(m.group(1)) if m else 0
    elif kind in V7_AT and len(block_) >= V7_AT[kind] + 4:
        v = struct.unpack_from("<I", block_, V7_AT[kind])[0]
        n = v & 0xFFFF if v >> 16 == 0x0102 else 0
    return 66 + n if 17 <= n <= 32 else layer


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
            recs.append({"type": "pad", "designator": _str(blocks[0]), "layer": extended_layer(m[0], m, "pad"), "x": x, "y": y, "w": w, "h": h,
                         "hole": struct.unpack_from("<i", m, 45)[0], "shape": shape, "rot": struct.unpack_from("<d", m, 52)[0],
                         "corner": ss[564] if len(ss) >= 596 and ss[531] else None,
                         "paste_mode": m[101] if len(m) > 102 else None, "paste": struct.unpack_from("<i", m, 86)[0] if len(m) > 90 else None,
                         "mask_mode": m[102] if len(m) > 102 else None, "mask": struct.unpack_from("<i", m, 90)[0] if len(m) > 94 else None,
                         "plated": bool(m[60]), "hole_shape": ss[262] if len(ss) >= 596 else 0,
                         "slot": struct.unpack_from("<i", ss, 263)[0] if len(ss) >= 596 else 0,
                         "hole_rot": struct.unpack_from("<d", ss, 267)[0] if len(ss) >= 596 else 0.0,
                         "offset": (struct.unpack_from("<i", ss, 275)[0], struct.unpack_from("<i", ss, 403)[0]) if len(ss) >= 596 else (0, 0),
                         "raw": blocks})
        elif t == 5:
            b1, i = _blk(d, i); b2, i = _blk(d, i)
            x, y, h = struct.unpack_from("<3i", b1, 13)
            recs.append({"type": "text", "layer": extended_layer(b1[0], b1, "text"), "text": _str(b2), "x": x, "y": y, "h": h, "font": struct.unpack_from("<h", b1, 25)[0],
                         "rot": struct.unpack_from("<d", b1, 27)[0], "mirror": bool(b1[35]), "w": struct.unpack_from("<i", b1, 36)[0],
                         "ws": struct.unpack_from("<i", b1, 115)[0], "box": struct.unpack_from("<2i", b1, 124), "just": b1[132], "raw": [b1, b2]})
        elif t in (1, 3, 4, 6, 11, 12):
            b, i = _blk(d, i)
            r = {"type": {1: "arc", 3: "via", 4: "track", 6: "fill", 11: "region", 12: "body"}[t], "layer": b[0], "raw": [b]}
            if t == 4:
                r.update(zip(("x1", "y1", "x2", "y2", "w"), struct.unpack_from("<5i", b, 13)))
            elif t == 6:
                r.update(zip(("x1", "y1", "x2", "y2"), struct.unpack_from("<4i", b, 13)))
            elif t == 1:
                r.update(zip(("cx", "cy", "r", "a0", "a1", "w"), struct.unpack_from("<3i2di", b, 13)))
            elif t in (11, 12):
                pl = struct.unpack_from("<I", b, 18)[0]
                n = struct.unpack_from("<I", b, 22 + pl)[0]
                r["params"] = b[22:22 + pl - 1].decode("cp1252", "replace")
                r["pts"] = [struct.unpack_from("<2d", b, 26 + pl + 16 * k) for k in range(n)]
            r["layer"] = extended_layer(r["layer"], b, r["type"], r.get("params"))
            recs.append(r)
        else:
            raise ValueError(f"unknown PCB record id {t} at {i - 1}")
    return name, recs


def read_pcblib(path):
    """Raises ValueError for a damaged or unknown-format file (OSError when it is not an OLE file at all)."""
    import olefile
    with olefile.OleFileIO(str(path)) as o:
        if not o.exists("Library/Data"):
            raise ValueError(f"{Path(path).name} is not a .PcbLib (no Library/Data stream"
                             + ("; it looks like a .SchLib)" if o.exists("FileHeader") and not o.exists("Library") else ")"))
        try:
            return _read_pcblib(o)
        except (struct.error, zlib.error, IndexError, KeyError, EOFError) as e:
            raise ValueError(f"{Path(path).name}: damaged or unsupported .PcbLib ({type(e).__name__}: {e})") from e


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
    if o.exists("Library/Models/Header") and out:     # embedded STEP models: [(params, decompressed bytes)]
        md, j, models = o.openstream("Library/Models/Data").read(), 0, []
        while j < len(md):
            b, j = _blk(md, j)
            models.append((parse_param_bytes(b), zlib.decompress(o.openstream(f"Library/Models/{len(models)}").read())))
        out[0]["models"] = models
    return out


def read_schlib(path):
    """Raises ValueError for a damaged or unknown-format file (OSError when it is not an OLE file at all)."""
    import olefile
    with olefile.OleFileIO(str(path)) as o:
        if o.exists("Library/Data") or not o.exists("FileHeader"):
            raise ValueError(f"{Path(path).name} is not a .SchLib"
                             + (" (it looks like a .PcbLib)" if o.exists("Library/Data") else " (no FileHeader stream)"))
        try:
            return _read_schlib(o)
        except (struct.error, zlib.error, IndexError, KeyError, EOFError) as e:
            raise ValueError(f"{Path(path).name}: damaged or unsupported .SchLib ({type(e).__name__}: {e})") from e


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
                             "x": x, "y": y, "length": length, "conglomerate": cong, "part": struct.unpack_from("<h", b, 5)[0],
                             "mode": b[7], "symbols": tuple(b[8:12])})
            else:
                recs.append(parse_param_bytes(b))
        out.append({"name": nm, "records": recs})
    return hdr, out


# ---------------------------------------------------------------- CLI
def main(argv=None):
    try:
        return _main(argv)
    except (ValueError, KeyError) as e:          # a spec / symbol file that can't be written: the user's input
        msg = f"missing field {e}" if isinstance(e, KeyError) else str(e)
        print(f"ecadlib altium-lib: {msg}", file=sys.stderr)
        sys.exit(2)


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Write Altium .PcbLib/.SchLib files (one entry per file)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pcblib", help="package JSON specs -> <out>/<name>.PcbLib")
    p.add_argument("specs", nargs="+"); p.add_argument("-o", "--out", default="out/altium_models/PcbLib")
    p.add_argument("--density", choices="ABC"); p.add_argument("--single", help="write all footprints into this one file")
    p.add_argument("--rules", help="library rules file: layers and density (default: ecadlib.toml found from here)")
    s = sub.add_parser("schlib", help="symbol JSON files -> <out>/<name>.SchLib")
    s.add_argument("symbols", nargs="+"); s.add_argument("-o", "--out", default="out/altium_models/SchLib")
    d = sub.add_parser("dump", help="print what the minimal reader sees in a .PcbLib/.SchLib")
    d.add_argument("files", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "dump":
        for f in a.files:
            if f.lower().endswith(".pcblib"):
                for fp in _dump_read(read_pcblib, f):
                    pads = [r for r in fp["records"] if r["type"] == "pad"]
                    kinds = {}
                    for r in fp["records"]:
                        kinds[(r["type"], r["layer"])] = kinds.get((r["type"], r["layer"]), 0) + 1
                    print(f"{f}: {fp['name']} (storage {fp['storage']}) {len(pads)} pads, {dict(sorted(kinds.items()))}")
                    for r in pads:
                        hole = (f" layer {r['layer']} hole {to_mm(r['hole']):.4f}" + (f" slot {to_mm(r['slot']):.4f} rot {r['hole_rot']:g}" if r['hole_shape'] == 2 else
                                f" square" if r['hole_shape'] == 1 else "") + ("" if r['plated'] else " NPTH")) if r['hole'] else ""
                        print(f"   pad {r['designator']:>3} ({to_mm(r['x']):8.4f},{to_mm(r['y']):8.4f}) {to_mm(r['w']):.4f}x{to_mm(r['h']):.4f} "
                              f"rot {r['rot']:g} shape {r['shape']} corner {r['corner']} paste {'rule' if r['paste_mode'] == 1 else r['paste']}{hole}")
                    for r in fp["records"]:
                        if r["type"] == "arc":
                            print(f"   arc layer {r['layer']} c ({to_mm(r['cx']):.4f},{to_mm(r['cy']):.4f}) r {to_mm(r['r']):.4f} {r['a0']:g}..{r['a1']:g} w {to_mm(r['w']):.4f}")
                        elif r["type"] == "text":
                            print(f"   text {r['text']} layer {r['layer']} at ({to_mm(r['x']):.4f},{to_mm(r['y']):.4f}) h {to_mm(r['h']):.4f} "
                                  f"stroke {to_mm(r['w']):.4f} rot {r['rot']:g} font {r['font']}")
                        elif r["type"] == "body":
                            bp = parse_params(r["params"])
                            print(f"   body {bp['V7_LAYER']} {len(r['pts'])} pts height {bp['OVERALLHEIGHT']} standoff {bp['STANDOFFHEIGHT']} "
                                  f"model type {bp['MODEL.MODELTYPE']} {bp.get('MODEL.NAME') or ''}")
                    for mp, d in fp.get("models", []):
                        print(f"   model {mp['NAME']} {len(d)} B checksum {mp['CHECKSUM']} (computed {step_checksum(d)})")
            else:
                _, syms = _dump_read(read_schlib, f)
                for sy in syms:
                    pins = [r for r in sy["records"] if r["RECORD"] == "2"]
                    modes = int(sy["records"][0].get("DISPLAYMODECOUNT", 1))
                    print(f"{f}: {sy['name']} {len(pins)} pins" + (f" in {modes} display modes" if modes > 1 else "") + ": " + ", ".join(
                        f"{r['num']}:{r['name']}" + (f"[{','.join(map(str, r['symbols']))}]" if any(r["symbols"]) else "")
                        + (f"@{r['mode']}" if r["mode"] else "") for r in pins))
        return
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "pcblib":
        from jsonschema import ValidationError
        from . import footprint_gen as F, rules as RU
        rules = RU.load(a.rules, start=out)
        lay = RU.layers(rules)
        fps, bad = [], 0
        for sp in a.specs:
            spec = None
            try:                                          # UTF-8 (Notepad's BOM too), not the locale's
                spec = json.loads(Path(sp).read_text(encoding="utf-8-sig"))
                F.validate(spec)
                fp, rep = footprint_from_spec(spec, RU.density_for(spec, rules, a.density))
            except (ValueError, ValidationError) as e:    # a spec that doesn't add up: say why, go on with the next
                if isinstance(e, ValidationError):
                    where = "/".join(map(str, e.absolute_path))
                    e = f"the package spec isn't valid{' at ' + where if where else ''}: {e.message}"
                print(f"{spec.get('name', sp) if isinstance(spec, dict) else sp}: not written: {e}", file=sys.stderr)
                bad += 1
                continue
            fps.append(fp)
            if not a.single:
                path = write_pcblib(out / f"{file_stem(fp['name'])}.PcbLib", [fp], lay)
                print(f"{fp['name']:<44} {len(fp['pads']):>3} pads {len(fp['tracks']):>3} tracks {len(fp['fills'])} fills "
                      f"{len(fp['regions'])} regions -> {path} ({path.stat().st_size} B)")
        if a.single and fps:
            print(f"{len(fps)} footprints -> {write_pcblib(out / a.single, fps, lay)}")
        if bad:
            sys.exit(f"{bad} footprint spec(s) failed: fix them before using the footprints")
    else:
        for f in a.symbols:
            sym = symbol_from_json(json.loads(Path(f).read_text(encoding="utf-8-sig")))
            path = write_schlib(out / f"{file_stem(sym['name'])}.SchLib", [sym])
            print(f"{sym['name']:<24} {sum(1 for p in sym['prims'] if p[0] == 'pin'):>3} pins -> {path}")


def _dump_read(reader, f):
    """reader(f), naming the file when it is no Altium library at all (olefile's "not an OLE2 structured storage
    file" says nothing of which one, with several given)."""
    try:
        return reader(f)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise ValueError(f"{f}: {e}") from e


if __name__ == "__main__":                     # `python3 -m ecadlib.altium_lib dump ... | head`: quiet, as `ecadlib` is
    from .cli import _pipe_gone, _quiet_stdout
    try:
        main()
        sys.stdout.flush()
    except OSError as e:
        if not _pipe_gone(e):
            raise
        sys.exit(_quiet_stdout())
