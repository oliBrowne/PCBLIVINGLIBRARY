"""Altium interoperability: what real Altium-saved libraries contain that our reader/writer must handle, and an
independent cross-check of our files with pyaltiumlib (skipped when it is not installed).

The layouts below were observed in Altium-saved libraries of the Celestial Altium Library
(github.com/issus/altium-library; its license does not allow redistribution here, so no file from it is committed):
the fixtures are built from our own writer and patched to Altium's byte layout. Nothing was opened in real Altium.
    python3 -m tests.run_all test_altium_interop"""
import json, struct, tempfile
from pathlib import Path

import olefile

from ecadlib import altium_lib as AL, cfb, rules_infer as RI

ROOT = Path(__file__).parent.parent


def _fp(name, layer=AL.MECH16, body_layer=AL.MECH16):
    pads = [{"designator": "1", "x": -1, "y": 0, "w": 0.6, "h": 0.6, "shape": "rect"},
            {"designator": "2", "x": 1, "y": 0, "w": 0.6, "h": 0.6, "shape": "rect"}]
    box = [(-2, -1, 2, -1), (2, -1, 2, 1), (2, 1, -2, 1), (-2, 1, -2, -1)]
    return {"name": name, "pads": pads, "tracks": [(*t, 0.05, layer) for t in box] + [(-1.2, -0.5, 1.2, -0.5, 0.1, AL.MECH13)],
            "fills": [(-0.1, -0.1, 0.1, 0.1, layer)], "regions": [([(-0.5, -0.4), (0.5, -0.4), (0, 0.4)], layer)],
            "arcs": [(0, 0, 1.5, 0, 360, 0.05, layer)], "texts": [(".Comment", -1, 1.2, 1, 0.15, 0, layer)],
            "bodies": [{"pts": [(-1, -0.5), (1, -0.5), (1, 0.5), (-1, 0.5)], "height": 1.0}], "body_layer": body_layer}


def _as_mech31(data):
    """Altium's encoding of Mechanical 31 (TE USB-C 2129691-1 in Celestial): the one-byte layer says Mechanical 16 (72),
    the record's v7 layer id says 0x0102001F, regions and bodies say V7_LAYER=MECHANICAL31."""
    b = bytearray(data)
    old, new = struct.pack("<I", AL.v7_layer(AL.MECH16)), struct.pack("<I", 0x0102001F)
    i = b.find(old)
    while i >= 0:
        b[i:i + 4] = new
        i = b.find(old, i + 4)
    return bytes(b).replace(b"V7_LAYER=MECHANICAL16", b"V7_LAYER=MECHANICAL31")


def test_reader_decodes_mechanical_17_to_32():
    """Records on Mechanical 17..32 were read as Mechanical 16 (the legacy layer byte), so rules inference proposed
    Mechanical16 as the courtyard layer of a library that keeps it on Mechanical 31, and '87' as the 3D body layer."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m31.PcbLib"
        p.write_bytes(_as_mech31(AL.pcblib_bytes([_fp(f"FP{k}") for k in range(3)])))
        recs = AL.read_pcblib(p)[0]["records"]
        by = {}
        for r in recs:
            by.setdefault(r["type"], set()).add(r["layer"])
        assert by["track"] == {66 + 31, AL.MECH13}, by
        for kind in ("fill", "region", "arc", "text", "body"):
            assert by[kind] == {66 + 31}, (kind, by)
        assert by["pad"] == {AL.TOP}
        L = RI.Library()
        RI.scan_pcblib(L, p)
        got = {s["key"]: s["value"] for s in RI.infer_layers(L)}
        assert "layers.courtyard" not in got and "layers.body3d" not in got, got      # was Mechanical16 and '87'
        p.write_bytes(AL.pcblib_bytes([_fp("FP")]))                 # a real Mechanical 16 stays Mechanical 16
        assert {r["layer"] for r in AL.read_pcblib(p)[0]["records"] if r["type"] != "pad"} >= {AL.MECH16}
        assert AL.extended_layer(AL.MECH16, b"", "track") == AL.MECH16
        assert AL.extended_layer(AL.TOP_OVERLAY, b"", None, "V7_LAYER=MECHANICAL31") == AL.TOP_OVERLAY


def _utf8_record(prefix, key, text, rest=""):
    """A |KEY=VALUE| record as Altium writes a value cp1252 can't hold: '|%UTF8%KEY=<utf-8>||' then '|KEY=?'."""
    return (prefix.encode() + b"|%UTF8%" + key.encode() + b"=" + text.encode("utf-8") + b"||"
            + b"|" + key.encode() + b"=" + text.encode("cp1252", "replace") + rest.encode() + b"\0")


def test_reader_prefers_utf8_twins():
    """Altium writes '?' in TEXT / ComponentDescription and the real text in a %UTF8% twin; we returned the '?'."""
    sym = AL.resistor_symbol("R")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.SchLib"
        rec = _utf8_record("|RECORD=4|OwnerPartId=1|Location.X=1", "TEXT", "光 ≤5V", "|FontID=1")
        fh = _utf8_record("|HEADER=Protel for Windows - Schematic Library Editor Binary File Version 5.0|Weight=9|CompCount=1"
                          "|LibRef0=R", "CompDescr0", "1MHz, 80μA", "|PartCount0=2")
        cfb.write_cfb(p, {"FileHeader": AL.block(fh), "R": {"Data": AL._sch_component(sym)[0] + AL.block(rec)},
                          "Storage": AL.pblock("|HEADER=Icon storage")})
        h, syms = AL.read_schlib(p)
        assert h["COMPDESCR0"] == "1MHz, 80μA", h
        assert [r["TEXT"] for r in syms[0]["records"] if r.get("RECORD") == "4"] == ["光 ≤5V"]


def test_writer_keeps_text_cp1252_cannot_hold():
    """'Resistor 10kΩ ≤0.1W' became 'Resistor 10k? ?0.1W' in the .SchLib; now the %UTF8% twin carries it (plain ASCII
    records are byte-for-byte what they were)."""
    sym = dict(AL.resistor_symbol("R_10K", "Resistor 10kΩ ±1% ≤0.1W"), params=[("Tolerance", "≤1%")])
    with tempfile.TemporaryDirectory() as d:
        p = AL.write_schlib(Path(d) / "u.SchLib", [sym])
        h, syms = AL.read_schlib(p)
        assert h["COMPDESCR0"] == "Resistor 10kΩ ±1% ≤0.1W"
        assert syms[0]["records"][0]["COMPONENTDESCRIPTION"] == "Resistor 10kΩ ±1% ≤0.1W"
        assert [r["TEXT"] for r in syms[0]["records"] if r.get("NAME") == "Tolerance"] == ["≤1%"]
        with olefile.OleFileIO(str(p)) as ole:                                 # closed: Windows can't delete open files
            raw = ole.openstream("R_10K/Data").read()
        assert b"|%UTF8%ComponentDescription=Resistor 10k\xce\xa9 \xc2\xb11% \xe2\x89\xa40.1W|||ComponentDescription=Resistor 10k? \xb11% ?0.1W|" in raw
    assert AL.pblock("|RECORD=1|Text=abc", utf8=True) == AL.pblock("|RECORD=1|Text=abc")
    # Altium twins every non-ASCII value, cp1252-encodable ones too (Celestial library: '|%UTF8%Text=90\xc2\xb0, Right
    # Angle|||Text=90\xb0, Right Angle'); without the twin a non-Western Windows reads 0xB0 in its own code page
    assert AL.pblock("|A=90°|B=x", utf8=True) == AL.block(b"|%UTF8%A=90\xc2\xb0|||A=90\xb0|B=x\0")


def test_regions_on_house_mechanical_layers():
    """A filled F.Fab polygon becomes a region on the assembly layer; with the rules' assembly layer on Mechanical 2
    the writer raised KeyError: 58 (only Mechanical 13 / 15 had a V7_LAYER name)."""
    mod = """(footprint "LED_Test" (layer "F.Cu")
  (fp_poly (pts (xy -0.3 -0.3) (xy 0.3 0) (xy -0.3 0.3)) (stroke (width 0.1) (type solid)) (fill solid) (layer "F.Fab"))
  (pad "1" smd rect (at -0.8 0) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 0.8 0) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask")))"""
    fp = dict(AL.footprint_from_kicad(mod), bodies=[{"pts": [(-1, -0.5), (1, -0.5), (1, 0.5), (-1, 0.5)], "height": 1.0}])
    with tempfile.TemporaryDirectory() as d:
        p = AL.write_pcblib(Path(d) / "a.PcbLib", [fp], {"assembly": 58, "body3d": 57, "courtyard": 72})
        recs = AL.read_pcblib(p)[0]["records"]
        reg = [r for r in recs if r["type"] == "region"]
        assert [r["layer"] for r in reg] == [58] and "V7_LAYER=MECHANICAL2|" in reg[0]["params"], reg
        assert [AL.parse_params(r["params"])["V7_LAYER"] for r in recs if r["type"] == "body"] == ["MECHANICAL1"]
    assert AL.v7_name(3) == "MID2" and AL.v7_name(AL.KEEPOUT) == "KEEPOUT"


def _pyaltiumlib():
    try:
        import logging
        logging.getLogger("pyaltiumlib").setLevel(logging.CRITICAL)
        import pyaltiumlib
        return pyaltiumlib
    except ImportError:
        print("  skip: pyaltiumlib not installed")
        return None


PY_KIND = {"PcbTrack": "track", "PcbPad": "pad", "PcbArc": "arc", "PcbRegion": "region", "PcbComponentBody": "body",
           "PcbFill": "fill", "PcbVia": "via", "PcbString": "text"}


def test_pyaltiumlib_reads_our_libraries_like_we_do():
    """pyaltiumlib (an independent reader) sees the same primitives, pads, holes, layers and texts in our .PcbLib files,
    and the same records in our .SchLib files. Bodies are written last: pyaltiumlib swallows the record after a body."""
    pal = _pyaltiumlib()
    if not pal:
        return
    specs = ["SOIC-8_3.9x4.9mm_P1.27mm", "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", "R_0603_1608Metric",
             "advanced/JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "advanced/Example_Custom_LPad_Slot",
             "advanced/BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"]
    fps = [AL.footprint_from_spec(json.loads((ROOT / f"ecadlib/package_specs/{s}.json").read_text()))[0] for s in specs]
    fps.append(_fp("BODY_AND_ARCS", layer=AL.MECH15, body_layer=AL.MECH13))
    syms = [AL.symbol_from_json(json.loads(f.read_text())) for f in sorted((ROOT / "examples/symbols").glob("*.json"))]
    pads = 0
    with tempfile.TemporaryDirectory() as d:
        for fp in fps:
            p = AL.write_pcblib(Path(d) / f"{AL.file_stem(fp['name'])}.PcbLib", [fp])
            mine = AL.read_pcblib(p)[0]
            part = pal.read(str(p)).Parts[0]
            assert part.Name == fp["name"]
            theirs = sorted(PY_KIND.get(type(r).__name__, type(r).__name__) for r in part.Records)
            assert theirs == sorted(r["type"] for r in mine["records"]), (fp["name"], theirs)
            pp = [r for r in part.Records if type(r).__name__ == "PcbPad"]
            for a, b in zip(pp, [r for r in mine["records"] if r["type"] == "pad"]):
                mil = lambda u: round(u / 10000, 3)
                assert (a.designator, a.layer, a.is_plated, a.expansion_manual_paste_mask, a.expansion_manual_solder_mask) == \
                       (b["designator"], b["layer"], b["plated"], b["paste_mode"], b["mask_mode"]), fp["name"]
                assert (round(float(a.location.x), 3), round(-float(a.location.y), 3), round(float(a.size_top.x), 3),
                        round(-float(a.size_top.y), 3), round(float(a.hole_size), 3), a.rotation) == \
                       (mil(b["x"]), mil(b["y"]), mil(b["w"]), mil(b["h"]), mil(b["hole"]), b["rot"]), fp["name"]
                if a.hole_slot_length is not None:
                    assert (round(float(a.hole_slot_length), 3), a.hole_rotation) == (mil(b["slot"]), b["hole_rot"])
                pads += 1
            for a, b in zip([r for r in part.Records if type(r).__name__ == "PcbString"], [r for r in mine["records"] if r["type"] == "text"]):
                assert (a.text, a.layer, a.widestring_index) == (b["text"], b["layer"], b["ws"])
        for s in syms:
            p = AL.write_schlib(Path(d) / f"{s['name']}.SchLib", [s])
            part = pal.read(str(p)).Parts[0]
            n = sum(type(r).__name__ == "SchPin" for r in part.Records)
            assert part.Name == s["name"] and n == sum(1 for pr in s["prims"] if pr[0] == "pin"), (s["name"], n)
    assert pads > 120, pads
