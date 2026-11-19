"""Parts built and then held for a person's look on the 1000-part run (2026-11-19) that a free check can settle.
Run:  python3 -m tests.run_all test_review_holds_1119"""
from ecadlib import altium_export as X
from ecadlib.librarian import land_named_after


def test_a_land_named_after_the_maker_and_its_part_number_is_that_parts_own():
    land = "Connector_USB:USB_Micro-B_Molex-105017-0001"
    assert land_named_after(land, "1050170001", None)            # Molex writes it without the dash
    assert land_named_after(land, "105017-0001", None)
    assert not land_named_after(land, "1050170002", None)        # another part of the series
    assert not land_named_after(land, "105017", None)            # the series alone
    # a series with an ordering code after it still waits for a person
    assert not land_named_after("Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", "B2B-PH-K-S(LF)(SN)", None)


def test_a_ferrite_beads_impedance_written_with_a_space_is_its_value():
    assert X.description_value("FERRITE BEAD 1 KOHM 0603 1LN", "Inductors") == "1 KOHM"
    assert X.description_value("FERRITE BEAD 2.2 KOHM 0603 1LN", "Inductors") == "2.2 KOHM"
    assert X.description_value("FERRITE BEAD 600 OHM 0402 1LN", "Inductors") == "600 OHM"
    # 'M' before OHM reads two ways (milli, mega): nothing is guessed from it
    assert X.description_value("RES 10 MOHM 1% 2512", "Resistors") == ""


def test_bead_lines_with_no_value_column_get_the_impedance_from_the_part_search():
    import pandas as pd
    tri = pd.DataFrame([{"line": "1", "mpn": "BLM18AG102SN1D", "src_description": "FERRITE BEAD 1 KOHM 0603 1LN",
                         "local_footprint": ""}])
    rows = X.to_rows([{"designator": "L7", "mpn": "BLM18AG102SN1D", "manufacturer": "Murata"}], triage=tri)
    got = [r for df in rows.values() for r in df.to_dict("records")]
    assert got and got[0].get("Value") == "1 KOHM", got


def test_names_that_differ_only_once_stored_get_the_part_number():
    # 'RES-0Ω' is stored as RES-0Ohm: beside a 'RES-0OHM' that is one name to Altium, so the later part is suffixed
    lines = [{"designator": "R151", "value": "0 OHM", "mpn": "CRCW12060000Z0EA"},
             {"designator": "R152", "value": "0Ω", "mpn": "RC0603JR-070RL"},
             {"designator": "R153", "value": "0Ω", "mpn": "RC0402JR-070RL"}]
    names = list(X.to_rows(lines)["Resistors"]["Part Number"])
    assert len({X._safe_name(n, []).casefold() for n in names}) == 3, names
    assert names[0] == "RES-0OHM" and names[1].endswith("RC0603JR-070RL"), names


def test_pads_kicads_own_symbol_leaves_unconnected_are_a_warning_not_a_hold():
    import tempfile
    from tests.test_release_check import make, fails
    with tempfile.TemporaryDirectory() as d:
        it = make(d, pins=range(1, 8))
        it["source"] = {"symbol": "kicad:Sensor:PART", "footprint": "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}
        assert fails(it, d)[0] == ["pins: pads without a symbol pin: 8"]          # not KiCad's own pair: held
        it["librarian"] = {"tried": [], "review": [], "kicad_pair": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}
        f, w = fails(it, d)
        assert not [x for x in f if x.startswith("pins")] and any("KiCad's symbol for this land" in x for x in w), (f, w)
        it["source"]["symbol"] = "standard:PART"                                    # ecadlib's own symbol: held
        assert fails(it, d)[0] == ["pins: pads without a symbol pin: 8"]
        it["source"] = {"symbol": "kicad:Sensor:PART", "footprint": "kicad:Package_SO:SOIC-8_5.3x5.3mm_P1.27mm"}
        assert fails(it, d)[0] == ["pins: pads without a symbol pin: 8"]          # another land than the pair's
    with tempfile.TemporaryDirectory() as d:                                      # a symbol pin with no pad: held
        it = make(d, pins=[1, 2, 3, 4, 5, 6, 7, 9])
        it["source"] = {"symbol": "kicad:Sensor:PART", "footprint": "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}
        it["librarian"] = {"tried": [], "review": [], "kicad_pair": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}
        assert any(x.startswith("pins: pads without a symbol pin") for x in fails(it, d)[0])


def test_a_land_the_symbols_filters_leave_out_is_released_only_on_an_agreeing_pin_table():
    from ecadlib import pin_text as PT
    from tests.test_name_match_autocheck import _variant
    pins = [(str(i), f"P{i}") for i in range(1, 15)]
    real = PT.check_pdf
    try:
        PT.check_pdf = lambda data, pins, **k: {"status": "agreed", "page": 3, "column": 0, "pins": len(pins)}
        ok, tried, pdf = _variant("SN74HC14DR", "74xx:74HC14", pins, "", '14-SOIC (0.154", 3.90mm Width)',
                                  filters="DIP*W7.62mm*")
        assert ok and pdf == ["SN74HC14DR"] and "footprint filters leave out" in tried[-1], tried
        PT.check_pdf = lambda data, pins, **k: {"status": "unchecked", "why": "no pin table found in the text layer"}
        ok, tried, _ = _variant("SN74HC14DR", "74xx:74HC14", pins, "", '14-SOIC (0.154", 3.90mm Width)',
                                filters="DIP*W7.62mm*")
        assert not ok, tried
        # a discrete's reel-code shortcut never applies to a land its filters leave out: the pin table must agree
        ok, tried, pdf = _variant("BSS138-7-F", "Transistor_FET:BSS138", [("1", "G"), ("2", "S"), ("3", "D")], "",
                                  "TO-236-3, SC-59, SOT-23-3", filters="SOT?323*")
        assert not ok and pdf == ["BSS138-7-F"], tried
    finally:
        PT.check_pdf = real


def test_an_error_after_a_source_built_a_part_keeps_it_held():
    # 2026-11-19: a TypeError inside the name match's variant check (after the KiCad symbol was put) dropped the
    # check's hold, and the part came out ready with its pins never compared. An error now holds what it built.
    from ecadlib import librarian as L

    class Boom(L.Run):
        def __init__(self):
            self.sources = ["kicad"]

        def local(self, *a):
            pass

        def src_kicad(self, st, *a):
            st["symbol"] = {"source": "kicad:Lib:PART", "file": "x.SchLib", "notes": []}
            st["footprint"] = {"source": "kicad:Lib:LAND", "file": "x.PcbLib", "notes": []}
            raise TypeError("check_pdf() got an unexpected keyword argument")

    item = {"name": "PART1", "folder": "Components/ICs", "description": "", "parameters": [
        {"name": "Manufacturer Part Number", "value": "PART1DR"}], "symbol": {"ref": "PART1"},
        "footprint": {"ref": "PART1", "kicad": ""}}
    st = Boom().resolve(item, {"mpn": "PART1DR", "designator": "U1"}, {})
    assert any("an error stopped its checks" in v for v in st["verify"]), st
    assert any(t.startswith("kicad: failed (TypeError") for t in st["tried"]), st["tried"]


def test_a_pin_table_that_checks_fewer_than_half_the_pins_releases_nothing():
    from ecadlib.librarian import few_named
    agreed = {"status": "agreed", "page": 3, "column": 0, "pins": 2}
    hex_inverter = [(str(i), "") for i in range(1, 15) if i not in (7, 14)] + [("7", "GND"), ("14", "VCC")]
    assert few_named(agreed, hex_inverter)["status"] == "unchecked"
    lm358 = [("1", ""), ("2", "-"), ("3", "+"), ("4", "V-"), ("5", "+"), ("6", "-"), ("7", ""), ("8", "V+")]
    assert few_named({**agreed, "pins": 6}, lm358)["status"] == "agreed"
