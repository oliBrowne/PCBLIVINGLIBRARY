"""Offline tests for footprint designator/comment text, pin graphics, overline names and De Morgan display modes.
Run:  python3 -m tests.test_altium_polish

REF_* records are from AltiumSharp TestData (Altium-saved files, Apache-2.0, see NOTICE). The De Morgan / pin-shape
fixture tests/fixtures/kicad/ecadlib_demorgan.kicad_sym was written for these tests."""
import base64, json, struct, tempfile, zlib
from pathlib import Path

from ecadlib import altium_lib as AL, kicad_import as K

FIX = Path(__file__).parent / "fixtures" / "kicad"
unz = lambda s: zlib.decompress(base64.b64decode(s))
# TEXT_DESIGNATOR.PcbLib's .Designator and TEXT_TEST.PcbLib's ROTATED / MIRROR texts: both blocks, no type byte
REF_DESIGNATOR = unz("eNr7w8DAoMjD8B8OGKBAYSE7AyMDHAQchrEcGYoYMhkSGXIYqAF6T/syfN7IzcAM4iwwX8CgwK3A4MDLDMZgwMhIsZWMbAzMEL80QDEc8AAxt55LanFmel5iSX4RAL6UG+k=")
REF_ROTATED = unz("eNr7w8DAoMjD8B8OgHyGgL4v/xuk2BgYGcCgwc2BwWEOAxQ4MhQxZDIkMuQwUAOce6rJUB/EzsAM4iwwX8CgwK3A4MDLDMZgwMhIsZWMbAzMEL80QDEDzJscQJo9yD/EMcTVBQBtkR9f")
REF_MIRROR = unz("eNr7w8DAoMTD8B8OgHyGBTIv/zdIsTEwMsAAo8McGNORoYghkyGRIYeBAsAEY1QdV2KoD2JnYAbba76AQYFbgcGBlxmMIVYzUmwlI9B8iF8aoJgB5k12IM3m6xkU5B8EAPcwHVE=")
# CLOCK_PIN / INVERTED_PIN / ACTIVE_LOW_PIN.SchLib pins (TestDataGenerator puts the symbol in the inside slot)
REF_PINS = [bytes.fromhex(h) for h in (
    "260000010200000000010000000003000001003a1e00ecff00000000000003434c4b013100037c267c00",
    "250000010200000000010000000001000001003a1e00ecff00000000000002454e013100037c267c00",
    "260000010200000000010000000004000001003a1e00ecff00000000000003525354013100037c267c00")]

KICAD8_FP = """(footprint "T8" (version 20240108) (layer "F.Cu")
  (property "Reference" "REF**" (at 0 -2 90) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))
  (property "Value" "T8" (at 0 2 180) (layer "F.Fab") (effects (font (size 1 1))))
  (fp_text user "${REFERENCE}" (at 0 0 0) (layer "F.Fab") (effects (font (size 0.5 0.5) (thickness 0.08))))
  (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))"""


def strict(path):
    import olefile
    olefile.OleFileIO(str(path), raise_defects=olefile.DEFECT_INCORRECT).close()


def texts_of(path):
    return [r for r in AL.read_pcblib(path)[0]["records"] if r["type"] == "text"]


def test_text_records_match_altium_bytes():
    for ref in (REF_DESIGNATOR, REF_ROTATED, REF_MIRROR):
        b1, i = AL._blk(ref, 0)
        x, y, h = struct.unpack_from("<3i", b1, 13)
        mine = AL.text_rec(AL._str(AL._blk(ref, i)[0]), x, y, h, struct.unpack_from("<i", b1, 36)[0], b1[0],
                           struct.unpack_from("<d", b1, 27)[0], struct.unpack_from("<i", b1, 115)[0], 1, bool(b1[35]),
                           struct.unpack_from("<2i", b1, 124))
        assert mine == b"\x05" + ref
    # whole footprint: Data, Header and WideStrings streams equal TEXT_DESIGNATOR's (cached extents scale from it)
    fp = {"name": "TEXT_DESIGNATOR", "pads": [], "tracks": [], "fills": [], "regions": [],
          "texts": [(".Designator", 0, 0, 1.27, 0.127, 0.0, AL.TOP_OVERLAY)]}
    st = AL._pcb_component(fp)[0]
    assert st["Data"] == AL.sblock("TEXT_DESIGNATOR") + b"\x05" + REF_DESIGNATOR and st["Header"] == b"\x01\0\0\0"
    assert st["WideStrings"] == AL.pblock("|ENCODEDTEXT0=46,68,101,115,105,103,110,97,116,111,114")


def test_generated_footprint_designator_and_comment():
    spec = json.loads(Path("ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    fp, _ = AL.footprint_from_spec(spec)
    with tempfile.TemporaryDirectory() as d:
        path = AL.write_pcblib(Path(d) / "s.PcbLib", [fp])
        strict(path)
        q = AL.read_pcblib(path)[0]
        assert q["header_count"] == len(q["records"])
        des, com = texts_of(path)
        pads = [r for r in q["records"] if r["type"] == "pad"]
        top, bot = max(p["y"] + p["h"] // 2 for p in pads), min(p["y"] - p["h"] // 2 for p in pads)
        assert (des["text"], des["layer"], des["h"], des["w"], des["font"], des["just"]) == (".Designator", AL.TOP_OVERLAY, AL.mm(1), AL.mm(0.15), 1, 3)
        assert des["y"] > top and des["x"] < 0 < des["x"] + des["box"][0]           # above the body, straddling x = 0
        assert (com["text"], com["layer"]) == (".Comment", AL.MECH13) and com["y"] + com["h"] < bot
        assert des["box"] == (round(5098381 * des["h"] / 500000), round(766451 * des["h"] / 500000))
        import olefile
        with olefile.OleFileIO(str(path)) as o:
            ws = AL.parse_params(AL._blk(o.openstream(f"{q['storage']}/WideStrings").read(), 0)[0].rstrip(b"\0").decode())
        assert "".join(map(chr, map(int, ws["ENCODEDTEXT1"].split(",")))) == ".Comment" and com["ws"] == 1
        lib = __import__("pyaltiumlib").read(str(path))
        assert sorted((r.text, r.layer) for r in lib.Parts[0].Records if type(r).__name__ == "PcbString") == [(".Comment", 69), (".Designator", 33)]


def test_kicad_footprint_text_conversion():
    notes = []
    fp, _ = K.convert_footprint(KICAD8_FP, notes=notes)
    (dt, dx, dy, dh, dw, drot, dl), (ct, cx, cy, ch, cw, crot, cl) = fp["texts"]
    assert (dt, dl, dh, dw, drot) == (".Designator", AL.TOP_OVERLAY, 0.8, 0.12, 90.0)
    assert dx == round(0.4, 4) and abs(dy - (2 - AL.CHAR_W * 0.8 * 1.5)) < 1e-4       # rotated anchor: left of / below centre
    assert (ct, cl, ch, cw, crot) == (".Comment", AL.MECH13, 1.0, 0.15, 0.0)          # 180 kept upright, default stroke
    assert "1 user fp_text not converted" in notes and not any("2 fp_text" in n for n in notes)
    fp2, _ = K.convert_footprint((FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text())
    assert [t[0] for t in fp2["texts"]] == [".Designator", ".Comment"] and fp2["texts"][0][2] > 0
    with tempfile.TemporaryDirectory() as d:
        path = AL.write_pcblib(Path(d) / "k.PcbLib", [fp])
        strict(path)
        t = texts_of(path)[0]
        assert t["rot"] == 90.0 and (t["x"], t["y"]) == (AL.mm(dx), AL.mm(dy))


def test_pin_shapes_and_json_format():
    for ref in REF_PINS:
        b = ref[4:]
        k = 13
        _, et, cong, ln, x, y = struct.unpack_from("<BBBhhh", b, k)
        name = b[k + 14:k + 14 + b[k + 13]].decode()
        assert AL.pin_rec("1", name, "input", cong & 3, x, y, ln, symbols=tuple(b[8:12])) == ref
    assert [AL.pin_symbols(s) for s in ("inverted", "clock", "inverted_clock", "input_low", "clock_low", "output_low", "non_logic")] == \
        [(0, 1, 0, 0), (3, 0, 0, 0), (3, 1, 0, 0), (0, 4, 0, 0), (3, 4, 0, 0), (0, 17, 0, 0), (0, 0, 0, 6)]
    assert AL.pin_symbols({"inside": "schmitt", "outside": "left_right_signal_flow"}) == (0, 0, 13, 33)
    for bad in ("wobbly", {"middle": "dot"}, {"inside": "nope"}):
        try:
            AL.pin_symbols(bad)
        except ValueError:
            continue
        raise AssertionError(f"pin shape {bad!r} accepted")
    d = {"name": "SHAPES", "pins": [{"num": "1", "name": "~{EN}", "type": "input", "shape": "inverted"},
                                    {"num": "2", "name": "CLK", "type": "input", "shape": "clock"},
                                    {"num": "3", "name": "OUT", "type": "output", "side": "right", "shape": {"outside": "left_right_signal_flow"}},
                                    {"num": "4", "name": "IN", "type": "input"}]}
    with tempfile.TemporaryDirectory() as tmp:
        path = AL.write_schlib(Path(tmp) / "s.SchLib", [AL.symbol_from_json(d)])
        strict(path)
        pins = {r["num"]: r for r in AL.read_schlib(path)[1][0]["records"] if r["RECORD"] == "2"}
        assert pins["1"]["name"] == "E\\N\\" and pins["1"]["symbols"] == (0, 1, 0, 0) and pins["2"]["symbols"] == (3, 0, 0, 0)
        assert pins["3"]["symbols"] == (0, 0, 0, 33) and pins["4"]["symbols"] == (0, 0, 0, 0)
        lib = __import__("pyaltiumlib").read(str(path))
        assert len([r for r in lib.Parts[0].Records if type(r).__name__ == "SchPin"]) == 4


def test_overline_names():
    assert AL.overline("~{RESET}") == "R\\E\\S\\E\\T\\" and AL.overline("~{CS}/A") == "C\\S\\/A" and AL.overline("VCC") == "VCC"
    assert K._pin_text("~") == "" and K._pin_text("~{WR}") == "W\\R\\"
    assert K._pin_text("~RESET") == "R\\E\\S\\E\\T\\" and K._pin_text("~CS~/A") == "C\\S\\/A"        # KiCad 6 toggles


def test_kicad_pin_shapes():
    notes = []
    chain, _ = K.load_symbol(f"{FIX / 'ecadlib_demorgan.kicad_sym'}:PIN_SHAPES", None)
    sym = K.convert_symbol(chain, notes=notes)
    with tempfile.TemporaryDirectory() as d:
        path = AL.write_schlib(Path(d) / "p.SchLib", [sym])
        strict(path)
        pins = {r["num"]: r for r in AL.read_schlib(path)[1][0]["records"] if r["RECORD"] == "2"}
    want = {"1": (0, 0, 0, 0), "2": (3, 0, 0, 0), "3": (3, 1, 0, 0), "4": (0, 4, 0, 0), "5": (3, 4, 0, 0),
            "6": (0, 17, 0, 0), "7": (3, 4, 0, 0), "8": (0, 0, 0, 6), "9": (0, 1, 0, 0)}
    assert {n: p["symbols"] for n, p in pins.items()} == want
    assert pins["1"]["name"] == "R\\E\\S\\E\\T\\" and pins["4"]["name"] == "C\\S\\/A" and pins["6"]["name"] == "I\\N\\T\\"
    assert any("falling-edge" in n for n in notes) and sym["modes"] == 1


def test_demorgan_display_modes_and_units():
    notes = []
    chain, _ = K.load_symbol(f"{FIX / 'ecadlib_demorgan.kicad_sym'}:NAND2_DM", None)
    sym = K.convert_symbol(chain, notes=notes)
    assert sym["parts"] == 2 and sym["modes"] == 2 and not any("not converted" in n for n in notes), notes
    with tempfile.TemporaryDirectory() as d:
        path = AL.write_schlib(Path(d) / "n.SchLib", [sym])
        strict(path)
        hdr, syms = AL.read_schlib(path)
        recs = syms[0]["records"]
        comp = recs[0]
        assert (hdr["PARTCOUNT0"], comp["PARTCOUNT"], comp["DISPLAYMODECOUNT"]) == ("3", "3", "2")
        pins = [r for r in recs if r["RECORD"] == "2"]
        assert int(comp["ALLPINCOUNT"]) == sum(1 for p in pins if p["mode"] == 0) == 10     # AD24 counts the normal view only
        got = {(p["num"], p["part"], p["mode"]): p["symbols"][1] for p in pins}
        assert got[("3", 1, 0)] == 1 and got[("3", 1, 1)] == 0 and got[("1", 1, 0)] == 0 and got[("1", 1, 1)] == 1
        assert got[("4", 2, 1)] == 1 and got[("6", 2, 0)] == 1 and {k[1:] for k in got if k[0] == "14"} == {(1, 0), (1, 1), (2, 0), (2, 1)}
        shapes = [r for r in recs if r["RECORD"] in ("6", "7", "12", "14")]
        alt = [r for r in shapes if r.get("OWNERPARTDISPLAYMODE") == "1"]
        assert len(alt) == 4 and len(shapes) - len(alt) == 4 and {r["OWNERPARTID"] for r in alt} == {"1", "2"}
        import olefile                        # key order: OwnerPartDisplayMode right after OwnerPartId (AltiumSharp, AD24)
        with olefile.OleFileIO(str(path)) as o:
            data = o.openstream("NAND2_DM/Data").read()
        assert b"|OwnerPartId=1|OwnerPartDisplayMode=1|" in data and b"|DisplayModeCount=2|" in data
        lib = __import__("pyaltiumlib").read(str(path))
        assert len([r for r in lib.Parts[0].Records if type(r).__name__ == "SchPin"]) == 20
    # test_kicad_import's two-unit symbol: units still become parts, unit-0 pins go to every part, normal view only
    from tests.test_kicad_import import TEST_SYM
    with tempfile.TemporaryDirectory() as d:
        Path(d, "t.kicad_sym").write_text(TEST_SYM)
        s2 = K.convert_symbol(K.load_symbol(f"{d}/t.kicad_sym:DUAL", None)[0])
    pins = [(pr[1]["num"], part, m) for pr, part, m in zip(s2["prims"], s2["prim_parts"], s2["prim_modes"]) if pr[0] == "pin"]
    assert s2["parts"] == 2 and s2["modes"] == 2 and {p[2] for p in pins} == {0}
    assert sorted(p[1] for p in pins if p[0] == "8") == [1, 2] and [p[1] for p in pins if p[0] == "2"] == [2]

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
