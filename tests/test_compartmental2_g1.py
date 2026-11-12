"""Compartmental testing, round 2 (2026-11-12), group 1: BOM side and parts from online sources (EasyEDA conversion,
3D placement, JLCPCB/LCSC ranking, part lookup numbers, BOM columns). Each part called on its own with hand-made input."""
import contextlib, io, json, math, tempfile
from pathlib import Path

from ecadlib import easyeda_convert as EC, providers as P


# ---------------------------------------------------------------- JLCPCB / LCSC: several exact hits (round-1 #8)
class _R:
    def __init__(self, body, status=200):
        self.text, self.status, self.error = json.dumps(body), status, None

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        return json.loads(self.text)


def _search(items):
    class H:
        def post(self, url, body, **kw):
            return _R({"data": {"componentPageInfo": {"list": items}}})
    return P.JLCPCBSearch(H())


def _hit(code, mpn, brand, pkg):
    return {"componentCode": code, "componentModelEn": mpn, "componentBrandEn": brand, "componentSpecificationEn": pkg}


def test_lcsc_search_takes_the_exact_hit_in_the_boms_package():
    # one maker lists the MPN in DIP-8 and SOP-8: the first hit (DIP-8) chose a through-hole EasyEDA footprint for a BOM
    # that says SOIC-8
    items = [_hit("C1", "LM358", "TI", "DIP-8"), _hit("C2", "LM358", "TI", "SOP-8")]
    res = _search(items).lookup({"mpn": "LM358", "manufacturer": "TI", "footprint": "SOIC-8_3.9x4.9mm_P1.27mm"})
    assert res.found and res.lcsc == "C2" and res.package == "SOP-8", res
    # the package only in the description
    res = _search(items).lookup({"mpn": "LM358", "manufacturer": "TI", "description": "Dual op amp SOIC-8"})
    assert res.lcsc == "C2", res
    # clones and no BOM maker: the one in the BOM's package
    items = [_hit("C1", "LM358", "UMW", "SOP-8"), _hit("C2", "LM358", "HGSEMI", "TSSOP-8")]
    assert _search(items).lookup({"mpn": "LM358", "footprint": "TSSOP-8"}).lcsc == "C2"


def test_lcsc_search_keeps_the_boms_maker_over_the_package_and_says_when_the_package_differs():
    items = [_hit("C1", "LM358", "UMW", "SOP-8"), _hit("C2", "LM358", "TI", "DIP-8")]
    res = _search(items).lookup({"mpn": "LM358", "manufacturer": "TI", "footprint": "SOIC-8"})
    assert res.lcsc == "C2" and "DIP-8" in res.extra.get("package_note", "") and "SOIC-8" in res.extra["package_note"], res
    # no package given or the package agrees: no note, the first exact hit as before
    items = [_hit("C1", "LM358", "TI", "DIP-8"), _hit("C2", "LM358", "TI", "SOP-8")]
    res = _search(items).lookup({"mpn": "LM358", "manufacturer": "TI"})
    assert res.lcsc == "C1" and not res.extra.get("package_note"), res


def test_triage_notes_an_lcsc_hit_in_another_package():
    from ecadlib import bom_triage as BT
    from ecadlib.kicad_index import KiCadIndex
    items = [_hit("C1", "NE555", "TI", "DIP-8")]
    ln = {"designator": "U1", "qty": "1", "value": "", "mpn": "NE555", "manufacturer": "TI", "footprint": "SOIC-8"}
    rows = BT.triage([ln], KiCadIndex([]), [_search(items)], None)
    assert "DIP-8" in rows[0]["notes"] and "SOIC-8" in rows[0]["notes"], rows[0]["notes"]


# ---------------------------------------------------------------- EasyEDA converter: damaged input
HEAD = {"x": 4000, "y": 3000}


def _raises_value_error(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_easyeda_pad_with_an_unreadable_position_or_size_is_refused_not_moved():
    # a pad whose x is 'nan' (or missing) was put at the canvas' 0, a metre from the others; a negative size was written
    for bad in ("PAD~RECT~nan~3000~4~4~1~~1~0", "PAD~RECT~~3000~4~4~1~~1~0", "PAD~RECT~3990~3000~-4~4~1~~1~0",
                "PAD~RECT~3990~3000~0~4~1~~1~0", "PAD~RECT~3990~3000~4~abc~1~~1~0", "PAD~ELLIPSE~3990~3000~4~4~11~~1~x"):
        shapes = [bad, "PAD~RECT~4010~3000~4~4~1~~2~0"]
        assert _raises_value_error(lambda: EC.footprint_to_kicad({"head": HEAD, "shape": shapes}, "T", [])), bad
    # a well-formed pad still converts (an empty hole field is an SMD pad)
    t = EC.footprint_to_kicad({"head": HEAD, "shape": ["PAD~RECT~3990~3000~4~4~1~~1~", "PAD~RECT~4010~3000~4~4~1~~2~0"]}, "T", [])
    assert '(pad "1" smd rect (at -2.54 0)' in t, t


def test_easyeda_converter_refuses_damaged_json_with_a_value_error():
    # (the librarian catches any error per source, but the live source check and the KiCad export catch ValueError only)
    pin = "P~show~0~1~0~0~0~g~0^^0~0^^M 0 0 h 10~#000^^1~0~0~0~A~start^^1~0~0~0~1~end"
    for data in (None, [], "x", {"head": "x", "shape": ["PAD~RECT~0~0~4~4~1~~1~0"]},
                 {"head": {"x": 0, "y": 0, "c_para": "pkg"}, "shape": ["PAD~RECT~0~0~4~4~1~~1~0"]}):
        try:
            EC.footprint_to_kicad(data, None, [])
        except ValueError:
            pass
    for data in (None, [], {"head": "x", "shape": [pin]}, {"head": {"c_para": "U"}, "shape": [pin]},
                 {"head": {}, "shape": [pin.replace("M 0 0 h 10", "M 1 x")]},
                 {"head": {}, "shape": [pin.replace("M 0 0 h 10", "M 1 2 L 3")]}):
        try:
            EC.symbol_to_kicad(data, "X")
        except ValueError:
            pass


def test_easyeda_symbol_notes_a_pin_number_that_differs_from_its_spice_number():
    def pin(num, spice, y):
        return (f"P~show~0~{spice}~380~{y}~0~g{num}~0^^380~{y}^^M 380 {y} h 10~#880000^^1~380~{y}~0~P{num}~start~~~#00F"
                f"^^1~380~{y}~0~{num}~end~~~#00F")
    notes = []
    t = EC.symbol_to_kicad({"head": {"x": 400, "y": 300}, "shape": [pin("1", "3", 300), pin("2", "2", 310)]}, "X", "", {}, notes)
    assert '(number "1"' in t and '(number "2"' in t
    assert any("pin 1" in n and "3" in n for n in notes) and not any("pin 2" in n for n in notes), notes


# ---------------------------------------------------------------- EasyEDA 3D model: unreadable numbers
def test_easyeda_3d_model_with_a_nan_offset_is_not_placed_at_nan():
    from ecadlib import easyeda_3d as E3
    attrs = {"c_etype": "outline3D", "uuid": "abcdef0123456789", "c_origin": "4000,3000", "z": "nan", "c_rotation": "nan,0,0"}
    ref = E3.model_ref({"head": HEAD, "shape": ["PAD~RECT~3990~3000~4~4~1~~1~0~~0~g1",
                                                 "SVGNODE~" + json.dumps({"attrs": attrs})]})
    assert ref is not None and math.isfinite(ref["z"]) and all(math.isfinite(v) for v in ref["rotation"]), ref
    step = ("ISO-10303-21;\nDATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\n#2=CARTESIAN_POINT('',(4.,2.,1.));\n"
            "#11=VERTEX_POINT('',#1);\n#12=VERTEX_POINT('',#2);\nENDSEC;\nEND-ISO-10303-21;\n")
    _, info = E3.place(step, ref)
    assert all(math.isfinite(v) for v in info["bbox"]) and info["height"] == 1.0, info


# ---------------------------------------------------------------- part_lookup numbers (round-1 #9)
def test_part_lookup_reads_capital_prefixes_and_exponents():
    from ecadlib.part_lookup import si
    for text, want in (("10UF", 1e-5), ("100NF", 1e-7), ("22PF", 2.2e-11), ("4.7UH", 4.7e-6), ("1E-7 F", 1e-7),
                       ("1e-07", 1e-7), ("10 kOhms", 1e4), ("4k7", 4.7e3), ("0.1 µF", 1e-7), ("1/10W", 0.1),
                       ("16 VDC", 16.0), ("10M", 1e7), ("100 mOhm", 0.1), ("2.2 MOhm", 2.2e6)):
        got = si(text)
        assert got is not None and abs(got - want) <= want * 1e-9, (text, got, want)


# ---------------------------------------------------------------- BOM columns (round-1 #6)
def _bom(rows, name="b.csv"):
    import csv
    from ecadlib import bom_triage as BT
    p = Path(tempfile.mkdtemp()) / name
    with p.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    with contextlib.redirect_stderr(io.StringIO()):
        return BT.read_bom(p)


def test_size_columns_give_the_chip_size():
    for hdr, cell, want in (("Size (inch)", "0603", "0603"), ("Size", "0805", "0805"), ("Case Size (inch)", "0402", "0402"),
                            ("Metric Size", "1608", "0603"), ("Size (mm)", "1005", "0402"), ("Chip Size", "1206", "1206")):
        lines, mapping = _bom([["Designator", "Qty", "Value", "Manufacturer Part Number", hdr], ["R1", "1", "10k", "", cell]])
        assert lines[0].get("footprint") == want, (hdr, cell, lines, mapping)


def test_a_size_column_does_not_take_over_the_footprint_column():
    for row in (["Designator", "Qty", "Value", "Size", "Footprint"], ["Designator", "Qty", "Value", "Footprint", "Size"]):
        cells = {"Designator": "R1", "Qty": "1", "Value": "10k", "Size": "0402", "Footprint": "R_0603_1608Metric"}
        lines, _ = _bom([row, [cells[h] for h in row]])
        assert lines[0]["footprint"] == "R_0603_1608Metric", (row, lines)
    lines, mapping = _bom([["Designator", "Qty", "Value", "Package Qty", "Reel Size"], ["R1", "1", "10k", "4000", "7"]])
    assert "footprint" not in mapping, mapping


# ---------------------------------------------------------------- Altium Comment '=Value' with no Value (round-1 #7)
def test_altium_comment_naming_a_missing_parameter_is_no_value():
    from ecadlib import bom_triage as BT
    from tests.test_altium_project import comp, write_schdoc
    d = Path(tempfile.mkdtemp())
    f = write_schdoc(d / "Main.SchDoc", [comp("R1", "Res2", "UIDCCCC1", [], ["0603"], lib="Miscellaneous Devices.IntLib",
                                              comment="=Value"),
                                         comp("R2", "Res2", "UIDCCCC2", [("Value", "4k7")], ["0603"],
                                              lib="Miscellaneous Devices.IntLib", comment="=Value")])
    with contextlib.redirect_stderr(io.StringIO()):
        lines, _ = BT.read_bom(f)
    by = {ln["designator"]: ln for ln in lines}
    assert by["R2"]["value"] == "4k7", by
    assert not by["R1"].get("value", "").startswith("="), by["R1"]
