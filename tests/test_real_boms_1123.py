"""BOM shapes found in a second sweep of real open-source hardware projects (2026-11-23: about 1,900 more BOM files
from GitHub run through triage and the free pipeline). The BOMs here are written for the test, in the same layout as
the real exports. Plain functions, no pytest needed: python3 -m tests.run_all test_real_boms_1123"""
from pathlib import Path

from ecadlib import bom_triage as BT
from tests.fixtures_lite import lite


def _read(tmp_path, text, name="bom.csv"):
    p = Path(tmp_path) / name
    p.write_text(text, encoding="utf-8")
    return BT.read_bom(p)


# A Chinese BOM written by hand (simplified): 参考位号 = reference designator, 器件型号 = part number.
CN_SIMPLIFIED = """参考位号,数量,器件型号,描述,封装,供应商,备注
U1,1,AMS1117-3.3,3.3V LDO稳压,SOT-223,AMS,电源稳压
R1-4,4,RC0805FR-0710KL,10K电阻,0805,不限,上拉
C1,1,CL21B104KBCNNNC,100nF电容,0805,不限,去耦
"""

# A KiCad BOM exported with the Traditional Chinese user interface: 指定符 = Designator, 數量 = Qty, 封裝 = Footprint.
CN_TRADITIONAL = """"編號";"指定符";"封裝";"數量";"名稱";"供應商編號";
1;"U1";"SOT223_AMS1117-5.0";1;"AMS1117-5.0";;;
2;"C2,C1";"C_0402_1005Metric";2;"10µ";;;
3;"R1,R2";"R_0402_1005Metric";2;"470k";;;
"""


@lite
def test_chinese_reference_designator_and_part_number_headers(tmp_path):
    lines, mapping = _read(tmp_path, CN_SIMPLIFIED)
    assert mapping.get("designator") == "参考位号" and mapping.get("mpn") == "器件型号", mapping
    by = {d["designator"]: d for d in lines}
    assert by["U1"]["mpn"] == "AMS1117-3.3" and by["U1"]["footprint"] == "SOT-223"
    assert by["R1-4"]["qty"] == "4"


@lite
def test_traditional_chinese_kicad_headers(tmp_path):
    lines, mapping = _read(tmp_path, CN_TRADITIONAL)
    assert mapping.get("designator") == "指定符" and mapping.get("qty") == "數量", mapping
    assert mapping.get("footprint") == "封裝", mapping
    assert {d["designator"] for d in lines} == {"U1", "C2,C1", "R1,R2"}
    assert "mpn" not in mapping and "value" not in mapping          # 名稱 (a name) is not guessed to be the value


# A Mouser project cart: 'Part' holds the board's designators, the part number is in 'Mfr. #'.
MOUSER_CART = """Bill of Materials,,,,,
Xmas Tree,,,,,
,,,,,
Part,Qty,Mouser #,Description,Mfr. #,Manufacturer
B1,1,534-2489,2 AAA cell holder,2489,Keystone Electronics
C1,1,187-CL21A475KPFNNNE,MLCC 4.7uF 10V X5R 0805,CL21A475KPFNNNE,Samsung Electro-Mechanics
C2,1,187-CL21A106KPCLQNC,MLCC 10uF 10V X5R 0805,CL21A106KPCLQNC,Samsung Electro-Mechanics
D1,1,941-C513AWSNCX0Z0342,LED White,C513A-WSN-CX0Z0342,Cree LED
"""

# A Mouser / Digi-Key style list where the designators sit in 'Customer #'.
CUSTOMER_REF = """Mfr. #,Manufacturer,Qty,Mouser #,Customer #,Footprint,Description
CL31A476MPHNNNE,Samsung,2,187-CL31A476MPHNNNE,"C1,C2",1206,47 uF 10V X5R 1206
CL21B105KBFNNNE,Samsung,1,187-CL21B105KBFNNNE,C10,0805,1 uF 50V X7R 0805
RC0805FR-0710KL,Yageo,3,603-RC0805FR-0710KL,"R1,R2,R5",0805,10k 1% 0805
"""

# An OrCAD-style export: 'Component Name' is the designator, 'Part Name' a library name.
COMPONENT_NAME = """Component Name,Value,Part Name,Description,Footprint,Part Number,
C1,"100pF","C 0805","Capacitor","SM0805","",
C2,"0.1","C 0805","Capacitor","SM0805","",
R1,"10k","R 0805","Resistor","SM0805","",
U1,"LM358","LM358","Op amp","SO8","LM358DR",
"""


@lite
def test_designators_found_in_an_unnamed_column(tmp_path):
    for text, header in ((MOUSER_CART, "Part"), (CUSTOMER_REF, "Customer #"), (COMPONENT_NAME, "Component Name")):
        lines, mapping = _read(tmp_path, text)
        assert mapping.get("designator") == header, (header, mapping)
        assert all(d.get("designator") for d in lines), lines
    lines, _ = _read(tmp_path, MOUSER_CART)
    assert {d["designator"]: d["mpn"] for d in lines}["C1"] == "CL21A475KPFNNNE"


# Columns that look like designators but are not: a revision code repeated on every row, LCSC numbers.
NOT_DESIGNATORS = """MPN,Value,Revision,Code
RC0603FR-0710KL,10k,R01,C2040
RC0603FR-071KL,1k,R01,C1525
GRM188R71H104KA93D,100n,R01,C1554
CL10A106KP8NNNC,10u,R01,C7519
"""

# A 'Part' column of real part numbers next to the MPN: S8050, M7 look like designators but repeat no pattern.
PART_NUMBERS = """Part,Qty,MPN,Description
S8050,2,S8050-J3Y,NPN transistor
M7,4,M7-13-F,Rectifier diode
SS34,1,SS34,Schottky diode
"""


@lite
def test_lookalike_columns_are_not_read_as_designators(tmp_path):
    _, mapping = _read(tmp_path, NOT_DESIGNATORS)
    assert "designator" not in mapping, mapping
    _, mapping = _read(tmp_path, PART_NUMBERS)
    assert "designator" not in mapping, mapping


# A home-made purchasing sheet: 'symbol' holds the board's designators (read as the LibRef on main).
PART_NAME_SHEET = """part name,symbol,size (imp),unit price,units per build,Manufacturer ID,source url
C 0.1uF,"C1, C4, C6",603,$0.02,3,C0603C104K4RACTU,http://www.digikey.com/product-detail/en/C0603C104K4RACTU
R 10k,R1,603,$0.01,1,RC0603FR-0710KL,http://www.digikey.com/product-detail/en/RC0603FR-0710KL
64kbit eeprom,U1,,$0.61,1,AT24CS64-MAHM-T,http://www.digikey.com/product-detail/en/AT24CS64-MAHM-T
"""


@lite
def test_symbol_column_of_designators_is_the_designator(tmp_path):
    lines, mapping = _read(tmp_path, PART_NAME_SHEET)
    assert mapping.get("designator") == "symbol", mapping
    assert "libref" not in mapping, mapping
    assert {d["designator"] for d in lines} == {"C1, C4, C6", "R1", "U1"}


# Review cases: a shelf location (C3, R12) or a column of short discrete part numbers (D882, M7) is unique per row and
# looks like designators. Taking it would give the 10k resistor a capacitor land (class read from the designator).
STOCK_LOCATION = """Value,Package,Qty,Location,MPN
10k,0603,1,C3,RC0603FR-0710KL
100nF,0603,1,R12,CL10B104KB8NNNC
1k,0603,1,D7,RC0603FR-071KL
"""

SHORT_PART_NUMBERS = """Comment,Model,Qty
NPN transistor,D882,2
PNP transistor,B772,2
Rectifier,M7,4
Diode,S1M,1
Schottky,B340,1
"""

PART_SHORT_PART_NUMBERS = """Part,Qty,MPN,Description
D882,2,D882-Y,NPN transistor
B772,2,B772-Y,PNP transistor
M7,4,M7-13-F,Rectifier
"""

# A designator-looking 'Name' column whose classes contradict the values: a resistor value on C3.
NAME_CLASH = """Name,Value,Footprint
C3,10k,0603
R12,100nF,0603
"""


@lite
def test_location_model_and_clashing_columns_are_not_designators(tmp_path):
    for text in (STOCK_LOCATION, SHORT_PART_NUMBERS, PART_SHORT_PART_NUMBERS, NAME_CLASH):
        _, mapping = _read(tmp_path, text)
        assert "designator" not in mapping, (text.splitlines()[0], mapping)


# 'Manufacturer ID' / 'Mfr ID' are ambiguous (a maker's name or code, or its part number): read as on main.
@lite
def test_manufacturer_id_header_stays_the_maker():
    assert BT.map_headers(["Designator", "Manufacturer ID", "Qty", "Value"]).get("manufacturer") == "Manufacturer ID"
    assert BT.map_headers(["Ref", "Mfr ID", "Qty", "Value"]).get("manufacturer") == "Mfr ID"


# Built wrong in the 2026-11-23 sweep, now held. Ferrite beads (FB1 '120R@100MHz' on KiCad's R_0603 land, a Murata
# BLM18 / Wurth 742792 part number) were filed and released as plain resistors.
@lite
def test_ferrite_bead_on_a_resistor_land_is_not_filed_as_a_resistor():
    from ecadlib.altium_export import to_rows
    lines = [{"designator": "FB201", "value": "120R@100MHz", "footprint": "Resistor_SMD:R_0603_1608Metric",
              "mpn": "BLM18PG121SN1D"},
             {"designator": "FB1, FB2", "value": "600R", "footprint": "R_0603_1608Metric", "mpn": "742792651"},
             {"designator": "FB3", "value": "120R", "footprint": "Resistor_SMD:R_0603_1608Metric"},
             {"designator": "R1", "value": "120R", "footprint": "Resistor_SMD:R_0603_1608Metric"}]
    rows = to_rows(lines)
    res = list(rows["Resistors"]["Part Number"]) if "Resistors" in rows else []
    assert res == ["RES-0603-120R"], rows.keys()                     # only R1 is a resistor


# A resistor Value that disagrees with the value its part number codes was released as typed when the designator was
# 'R_AUX1' (letters read as 'AUX') or the Value was a spelling the reader skips ('1mR' on a 1 MOhm code, '40B' on a
# 39.2 ohm code). Spellings that do agree ('24.9kR', '470r', '22E', '75B', '2k49B') still release.
@lite
def test_resistor_value_against_its_part_number_code():
    from ecadlib import mpn_decode as MD
    held = [("R_AUX1-R_AUX2", "45k", "RC0603FR-0710KL"), ("R607, R617", "1mR", "ERJ-2RKF1004X"),
            ("R119,R120", "40B", "RT0805BRD0739R2L")]
    fine = [("R1", "24.9kR", "0603WAF2492T5E"), ("R87", "470r", "RK73H2ATTD4700F"), ("R4", "22E", "RC0402FR-0722RL"),
            ("R101", "75B", "RT0805BRD0775RL"), ("R112", "2k49B", "RT0805BRD072K49L"), ("R2", "10k 1%", "RC0603FR-0710KL"),
            ("R3", "10K", "RC0603FR-0710KL")]
    lines = [{"designator": d, "value": v, "mpn": m} for d, v, m in held + fine]
    MD.fill_lines(lines)
    got = {ln["value"]: bool(ln.get("bom_hold")) for ln in lines}
    assert got == {v: (v in {"45k", "1mR", "40B"}) for _, v, _ in held + fine}, got


# Review of the value check: a rating after the value, milliohms, India's 4E7, a tolerance letter, a 0R in a bead's
# place, and designators that name the kind elsewhere in the token.
@lite
def test_resistor_value_check_review_cases():
    from ecadlib import mpn_decode as MD
    from ecadlib.altium_export import to_rows
    held = [("R1", "220 1%", "RC0603FR-0710KL"), ("R2", "47E 1%", "RC0603FR-0710KL"), ("R3", "4K7E 1%", "RC0603FR-0710KL"),
            ("AUX_R1", "45k", "RC0603FR-0710KL"), ("R4", "10mR", "RC0603FR-0710RL")]
    fine = [("R5", "100mR", "RL0603FR-070R1L"), ("R6", "4E7", "RC0603FR-074R7L"), ("R7", "10kF", "RC0603FR-0710KL"),
            ("R8", "10K 1%", "RC0603FR-0710KL"), ("R9", "103 1%", "RC0603FR-0710KL"), ("R10", "22E 5%", "RC0402FR-0722RL")]
    lines = [{"designator": d, "value": v, "mpn": m} for d, v, m in held + fine]
    MD.fill_lines(lines)
    got = {ln["value"]: bool(ln.get("bom_hold")) for ln in lines}
    assert got == {v: (v in {x for _, x, _ in held}) for _, v, _ in held + fine}, got
    one = [{"designator": "R1/C1", "value": "100", "mpn": "RC0603FR-0710KL"}]
    MD.fill_lines(one)
    assert not one[0].get("bom_hold")                                 # an R code on a mixed line: not checked
    rows = to_rows([{"designator": "FB4", "value": "0R", "footprint": "R_0603_1608Metric", "mpn": "RC0603JR-070RL",
                     "description": "RES 0R 0603"}])
    assert "Resistors" in rows, rows.keys()
