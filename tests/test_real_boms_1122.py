"""BOM shapes found in real open-source hardware projects (2026-11-22: about 1,500 BOM files from GitHub run through
triage and the free pipeline). The BOMs here are written for the test, in the same layout as the real exports.
Plain functions, no pytest needed: python3 -m tests.run_all test_real_boms_1122"""
from pathlib import Path

from ecadlib import bom_triage as BT
from tests.fixtures_lite import lite

# EAGLE's "Export > Partlist" text: one row per part, columns padded with spaces, no delimiter.
EAGLE_PARTLIST = """Partlist exported from C:/eagle/demo/board.sch at 3/15/2011 1:50:45 PM

Part     Value          Device              Package           Library    Sheet

C1       100n           C-EUC0603           C0603             rcl        1
C2       10uF           C-EUC0805           C0805             rcl        1
IC1      LM358D         LM358D              SO08              linear     1
LED1     red            LED_CHIPLED_0805    CHIPLED_0805      led        1
R1       4k7            R-EU_R0603          R0603             rcl        1
U$1      ATMEGA328P-AU  ATMEGA328P-AU       TQFP32-08         atmel      1
"""

# EAGLE's "Export > Values" text: one row per value, designators in Parts, a free-text Description that has spaces.
EAGLE_VALUES = """Partlist exported from /home/me/eagle/charger/PowerBoard.sch at 1/22/2017 10:29 PM

Qty Value                   Device                  Package            Parts      Description
3   10uF                    CHIP_CAP0805            0805_(2012_METRIC) C1, C2, C5 Chip Capacitor
1   10uH                    CHIP_IND                0805_(2012_METRIC) L1         Chip Inductor
1   5.1k                    CHIP_RES0805            0805_(2012_METRIC) R1
1   AP301                   AP301                   SOT-23             IC3
"""


def _read(tmp_path, text, name="board.bom"):
    p = Path(tmp_path) / name
    p.write_text(text, encoding="utf-8")
    return BT.read_bom(p)


@lite
def test_eagle_partlist_text_is_read_by_column_position(tmp_path):
    lines, mapping = _read(tmp_path, EAGLE_PARTLIST)
    by = {d["designator"]: d for d in lines}
    assert set(by) == {"C1", "C2", "IC1", "LED1", "R1", "U$1"}, by
    assert by["C1"]["value"] == "100n" and by["C1"]["footprint"] == "C0603"
    assert by["R1"]["value"] == "4k7" and by["R1"]["footprint"] == "R0603"
    assert by["IC1"]["value"] == "LM358D" and by["IC1"]["footprint"] == "SO08"
    assert "mpn" not in mapping                                  # 'Part' is the designator here, not a part number


@lite
def test_eagle_values_text_keeps_designator_lists_and_spaced_descriptions(tmp_path):
    lines, _ = _read(tmp_path, EAGLE_VALUES, "board.txt")
    by = {d["value"]: d for d in lines}
    assert by["10uF"]["designator"] == "C1, C2, C5" and by["10uF"]["qty"] == "3"
    assert by["10uF"]["footprint"] == "0805_(2012_METRIC)"
    assert by["10uF"]["description"] == "Chip Capacitor"
    assert by["10uH"]["description"] == "Chip Inductor" and by["10uH"]["designator"] == "L1"
    assert by["AP301"]["designator"] == "IC3" and by["AP301"]["footprint"] == "SOT-23"
    assert len(lines) == 4


@lite
def test_eagle_text_saved_under_an_excel_name_is_read_too(tmp_path):
    lines, _ = _read(tmp_path, EAGLE_VALUES, "bom.xls")          # text named .xls: read as text, as CSVs are
    assert len(lines) == 4


@lite
def test_a_plain_spaced_table_that_is_not_eagle_is_not_cut_by_position(tmp_path):
    lines, _ = _read(tmp_path, "Designator,Value,Footprint\nR1,10k,R_0603_1608Metric\nC1,100n,C_0402_1005Metric\n",
                     "b.csv")
    assert [d["designator"] for d in lines] == ["R1", "C1"]


@lite
def test_eagle_partlist_part_column_is_the_designator_even_for_named_parts(tmp_path):
    text = EAGLE_PARTLIST.replace("U$1      ATMEGA328P-AU ", "BATT     ATMEGA328P-AU ")
    text = text.replace("Library    Sheet", "MPN        Sheet")
    lines, mapping = _read(tmp_path, text)
    assert mapping["designator"] == "Designator"
    assert {d["designator"] for d in lines} == {"C1", "C2", "IC1", "LED1", "R1", "BATT"}


# EAGLE's CSV export (ULP bom.ulp): 'Part' holds the designators and a separate column holds the maker part number.
EAGLE_CSV = '''"Part";"Value";"Device";"Package";"Description";"MANUFACTURER_PART_NUMBER"
"ANT1";"2450AT18B100E";"2450AT18B100E";"ANTC3216";"Antenna";"2450AT18B100E"
"C1";"12pF";"CAP_0201";"RESC0201";"Generic chip capacitor";""
"C2";"100nF";"CAP_0201";"RESC0201";"Generic chip capacitor";""
'''


@lite
def test_part_column_of_designators_beside_a_real_mpn_column_is_the_designator(tmp_path):
    lines, mapping = _read(tmp_path, EAGLE_CSV, "b.csv")
    assert mapping["designator"] == "Part" and mapping["mpn"] == "MANUFACTURER_PART_NUMBER"
    assert [d["designator"] for d in lines] == ["ANT1", "C1", "C2"]


@lite
def test_a_footprint_name_altium_cannot_store_holds_its_part_and_does_not_stop_the_run(tmp_path):
    from ecadlib import librarian as L
    d = Path(tmp_path)
    bom = d / "bom.csv"
    bom.write_text('"Comment","Designator","Footprint","Quantity"\n"10k","R1","R_0603_1608Metric","1"\n'
                   '"6.25u卫星轴","U6","6.25卫星轴","1"\n', encoding="utf-8")
    items = L.run(bom, out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={},
                  log=lambda *a, **k: None)
    by = {it["name"]: it for it in items}
    assert len(items) == 2
    odd = next(it for n, it in by.items() if n.startswith("6.25"))
    assert odd["status"] != "ready"


@lite
def test_a_resistor_bare_number_value_that_contradicts_its_part_number_is_held(tmp_path):
    from ecadlib import mpn_decode as MD
    lines = [{"designator": "R31", "value": "430", "mpn": "0603WAF4302T5E", "footprint": "0603"},   # 43 kOhm code
             {"designator": "R32", "value": "43000", "mpn": "0603WAF4302T5E", "footprint": "0603"},
             {"designator": "R33", "value": "10", "mpn": "RC0603FR-0710RL", "footprint": "0603"}]
    MD.fill_lines(lines)
    assert "0603WAF4302T5E" in lines[0].get("bom_hold", "")
    assert not lines[1].get("bom_hold") and not lines[2].get("bom_hold")


@lite
def test_mpn_cells_that_hold_no_part_number(tmp_path):
    text = ("Designator,Part,Value,Quantity\n"
            '"C1, C2","0805, 4.7uF",4.7uF,2\n'          # a 'Part' column describing size and value
            "R1,\"0805, 10k\",10k,1\n"
            "D1,160-1176-1-ND,LED,1\n"                   # a Digi-Key order code
            "D2,Blue LED,LED,1\n"                        # words
            "U1,LM358DR,LM358,1\n"                       # a real part number stays
            "U2,ATMEGA328P-AU,ATMEGA328P,1\n")
    lines, _ = _read(tmp_path, text, "b.csv")
    by = {d["designator"]: d for d in lines}
    assert not by["C1, C2"].get("mpn") and "0805" in by["C1, C2"]["description"] and not by["C1, C2"].get("bom_hold")
    assert not by["R1"].get("mpn") and "0805" in by["R1"]["description"]
    assert not by["D1"].get("mpn") and "Digi-Key" in by["D1"]["bom_note"]
    assert not by["D2"].get("mpn") and "words" in by["D2"]["bom_note"] and by["D2"]["description"] == "Blue LED"
    assert by["U1"]["mpn"] == "LM358DR" and by["U2"]["mpn"] == "ATMEGA328P-AU"
    assert not by["U1"].get("bom_hold")
    lines, _ = _read(tmp_path, "Part Name,Qty\nBrass Stock,1\nAluminum Rod,2\n", "m.csv")
    assert [d["description"] for d in lines] == ["Brass Stock", "Aluminum Rod"]     # a line is never lost


@lite
def test_real_part_numbers_are_not_mistaken_for_sizes_values_or_words(tmp_path):
    for pn in ("CL21A106KAYNNNE", "RC0603FR-0710KL", "0603WAF4302T5E", "1N4148W-7-F", "BAT54C", "PESD5V0S1BL,315",
               "S2B-PH-K-S(LF)(SN)", "06033C104KAT2A", "0805L100WR", "10uF 0805 X5R"):
        assert BT._not_a_part_number(pn) in (None, "size_value"), pn
    assert BT._not_a_part_number("10uF 0805 X5R") is None          # 'X5R' is no size or value: left alone
    assert BT._not_a_part_number("N/A") is None and BT._not_a_part_number("Generic") is None
    assert BT._not_a_part_number("Do not place") is None           # a DNP mark: read as DNP below, not dropped


def _xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(path)


@lite
def test_a_workbook_openpyxl_refuses_is_read_by_the_plain_reader(tmp_path, monkeypatch):
    p = Path(tmp_path) / "b.xlsx"
    _xlsx(p, [["Designator", "Value", "MPN"], ["R1", "10k", "RC0603FR-0710KL"]])

    def refuse(*a, **k):                     # openpyxl: PatternFill.__init__() got an unexpected keyword 'extLst'
        raise TypeError("PatternFill.__init__() got an unexpected keyword argument 'extLst'")
    monkeypatch.setattr(BT.pd, "read_excel", refuse)
    lines, _ = BT.read_bom(p)
    assert lines[0]["designator"] == "R1" and lines[0]["mpn"] == "RC0603FR-0710KL"


@lite
def test_the_plain_xlsx_reader_decodes_excel_character_escapes():
    import xml.etree.ElementTree as ET
    from ecadlib import bom_inputs as BI
    ns = BI.NS["m"]
    si = ET.fromstring(f'<si xmlns="{ns}"><t>KMR731NG LFS_x0009_</t></si>')
    assert BI._rich_text(si) == "KMR731NG LFS\t"
    si = ET.fromstring(f'<si xmlns="{ns}"><t>A_x005F_x0009_B</t></si>')   # a literal '_x0009_', escaped
    assert BI._rich_text(si) == "A_x0009_B"


@lite
def test_a_git_lfs_pointer_says_so(tmp_path):
    p = Path(tmp_path) / "bom.xlsx"
    p.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:24540d6b\nsize 11148\n")
    try:
        BT.read_bom(p)
    except ValueError as e:
        assert "Git LFS" in str(e)
    else:
        raise AssertionError("a Git LFS pointer was read as a BOM")


@lite
def test_a_metric_size_in_the_description_is_not_read_as_megohms(tmp_path):
    from ecadlib import librarian as L
    for t in ("0603M", "1005M", "1608M", "2012 Metric"):
        assert L.passive_value(t, "Resistors") is None, t
    assert L.passive_value("1M", "Resistors") == 1e6 and L.passive_value("4M7", "Resistors") == 4.7e6
    d = Path(tmp_path)
    bom = d / "bom.csv"                                       # a Digi-Key cart: no Value column, size in the words
    bom.write_text("Index,Quantity,Part Number,Manufacturer Part Number,Description,Customer Reference\n"
                   "1,1,1026-XR1-ND,XR1,RES SMD 0603M,R27\n", encoding="utf-8")
    items = L.run(bom, out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={},
                  log=lambda *a, **k: None)
    val = next((p["value"] for p in items[0]["parameters"] if p["name"] == "Value"), "")
    assert val != "0603M" and not (items[0]["status"] == "ready" and not val), (val, items[0]["status"])


# Review findings (2026-11-22): each failed on the first version of the fixes above.
@lite
def test_a_tab_separated_part_value_file_is_not_cut_by_position(tmp_path):
    rows = ["Part\tValue\tPackage\tMPN"] + [f"R{i}\t10k\t0603\tRC0603FR-0710KL" for i in range(1, 11)]
    rows.append("C1\t10uF 25V\t0805\tCL21A106KAYNNNE")
    lines, _ = _read(tmp_path, "\n".join(rows) + "\n", "b.tsv")
    c1 = lines[-1]
    assert c1["footprint"] == "0805" and c1["mpn"] == "CL21A106KAYNNNE"


@lite
def test_eia_coded_resistor_values_that_match_the_part_number_are_not_held():
    from ecadlib import mpn_decode as MD
    lines = [{"designator": "R1", "value": "103", "mpn": "RC0603FR-0710KL"},
             {"designator": "R2", "value": "1002", "mpn": "RC0603FR-0710KL"},
             {"designator": "R3", "value": "4702", "mpn": "CRCW060347K0FKEA"},
             {"designator": "R4", "value": "4,7", "mpn": "RC0603FR-074K7L"}]           # 4.7 ohm on a 4.7k code
    MD.fill_lines(lines)
    assert [bool(ln.get("bom_hold")) for ln in lines] == [False, False, False, True]


@lite
def test_a_part_column_of_short_part_numbers_is_not_taken_for_designators(tmp_path):
    text = "Part,Qty,Manufacturer Part Number,Footprint\nM7,1,M7-13-F,SMA\nS8050,1,S8050,SOT-23\n" \
           "MP1584,1,MP1584EN-LF-Z,SOIC-8\nAMS1117,1,AMS1117-3.3,SOT-223\nC1815,1,2SC1815,TO-92\n"
    _, mapping = _read(tmp_path, text, "b.csv")
    assert "designator" not in mapping


@lite
def test_more_size_and_value_mpn_cells_are_caught():
    for cell in ("0603 10 ohm", "10K 0603 1%", "0603 1k 5%", "1608 330", "0805 100nF 50V"):
        assert BT._not_a_part_number(cell) == "size_value", cell


@lite
def test_eagle_library_column_is_not_read_as_a_libref(tmp_path):
    _, mapping = _read(tmp_path, EAGLE_PARTLIST)
    assert "libref" not in mapping
