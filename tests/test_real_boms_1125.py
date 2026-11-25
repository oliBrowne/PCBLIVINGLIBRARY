"""BOM shapes from a fifth sweep of real open-source hardware projects (2026-11-25). The BOMs here are written for the
test, in the same layout as the real exports. Plain functions, no pytest needed: python3 -m tests.run_all
test_real_boms_1125"""
from ecadlib import bom_triage as BT
from tests.fixtures_lite import lite

# A KiCad BOM pasted into one spreadsheet column and saved again: every row is one quoted cell. The first field's own
# quotes are lost in that save, so a row whose designators hold commas no longer lines up with the header.
ONE_CELL_ROWS = '''"Reference,""Qty"",""Value"",""DNP"",""Footprint"",""LCSC"""
"C1,C2,C3,""3"",""100 nF"","""",""Capacitor_SMD:C_0805_2012Metric"",""C49678"""
"D1,""1"",""BAT43W-V"","""",""Diode_SMD:D_SOD-123"",""C188548"""
"T1,""1"",""V_In (3,3V)"","""",""TestPoint:TestPoint_Pad_3.0x3.0mm"","""""
'''


@lite
def test_rows_saved_as_one_quoted_cell_are_unwrapped(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text(ONE_CELL_ROWS, encoding="utf-8")
    lines, mapping = BT.read_bom(p)
    assert mapping.get("designator") == "Reference" and mapping.get("footprint") == "Footprint", mapping
    by = {d["designator"]: d for d in lines}
    assert by["D1"]["value"] == "BAT43W-V" and by["D1"]["footprint"] == "Diode_SMD:D_SOD-123" and not by["D1"].get("bom_hold")
    assert by["T1"]["value"] == "V_In (3,3V)"                     # a comma inside a cell's own quotes stays in it
    assert by["C1"].get("bom_hold")                                 # 'C1,C2,C3' lost its quotes: shifted, held


def test_a_one_column_file_is_not_unwrapped():
    lines = ['"name"\n', '"a, b"\n', '"c"\n']                     # one column whose cells hold commas: not every row
    assert BT._unwrap_quoted_rows(lines) == lines
    lines = ['Reference,Value\n', '"R1,R2",10k\n']
    assert BT._unwrap_quoted_rows(lines) == lines
    lines = ['"Reference";"Value";"Footprint"\n', '"C9; C12";"10u/16";"CP_EIA-3216-18_Kemet-A"\n']   # ';' file, every
    assert BT._unwrap_quoted_rows(lines) == lines                  # cell quoted: read as it is (found on a real BOM)


def _wrap(inner):
    return "".join('"' + ln.replace('"', '""') + '"\n' for ln in inner.strip().splitlines())


@lite
def test_unwrapped_rows_shifted_by_the_lost_quotes_are_held(tmp_path):
    # review 2026-11-25: the row's last real column empty, so the extra cell is empty too: still a cell wider
    for inner in ['Reference,"Value","Footprint","MPN","Datasheet"\nC1,C2,"100nF","C_0603","",""\nD1,"LED","LED_0603","",""',
                  'Reference,"Value","Footprint","LCSC"\nR1,R2,"10k","0603",""\nD1,"LED","LED_0603",""']:
        p = tmp_path / "bom.csv"
        p.write_text(_wrap(inner), encoding="utf-8")
        lines, _ = BT.read_bom(p)
        by = {d["designator"]: d for d in lines}
        assert by["D1"]["value"] == "LED" and not by["D1"].get("bom_hold"), by
        shifted = [d for d in lines if d["designator"] != "D1"]
        assert shifted and all(d.get("bom_hold") for d in shifted), shifted


@lite
def test_an_unclosed_quote_inside_unwrapped_rows_is_still_mended(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text(_wrap('Reference,Qty,Value,Footprint\nR1,1,"10k,0603\nR2,1,22k,0402\nR3,1,47k,0805'), encoding="utf-8")
    lines, _ = BT.read_bom(p)
    by = {d["designator"]: d for d in lines}
    assert by.get("R2", {}).get("value") == "22k" and by.get("R3", {}).get("footprint") == "0805", lines


# A BOM whose 'PART' column holds the value and whose part number is under 'MAN PART NUMBER' (not read): two lines
# with the "part number" 4.7uF, an 0603 and an 0805, were built as one 0603 part (round 6, 2026-11-25).
VALUE_AS_PART = """ITEM,QTY,REFERENCE DESIGNATORS,PART,DESCRIPTION,MANUFACTURER
1,2,"C1,C2",4.7uF,CAP CER 4.7UF 6.3V X5R 0603,Murata
2,1,C7,4.7uF,CAP CER 4.7UF 50V X5R 10% 0805,TDK
"""


def test_a_bare_value_is_no_part_number():
    for s in ["4.7uF", "10k", "4K7", "100 nH", "0R", "1M", "2.2µF", "10uH", "1n5", "3R3", "10K Ohm", "470pf", "1R"]:
        assert BT._not_a_part_number(s) == "value", s
    for s in ["1N4148", "2N7002", "1N4007", "74HC14", "LM358", "100", "5001", "3V3", "16MHz", "GRM188R60J475ME19D",
              "9774030243R", "7M16000017", "5121K331", "150044M155260", "5988150107F", "3184K5", "RC0603"]:
        assert BT._not_a_part_number(s) is None, s                 # real part numbers (Würth, Keystone ...) stay


@lite
def test_lines_with_a_value_as_part_number_are_not_built_as_one_part(tmp_path):
    import json
    from ecadlib import librarian as L
    bom = tmp_path / "bom.csv"
    bom.write_text(VALUE_AS_PART, encoding="utf-8")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache", log=lambda *a, **k: None)
    plan = json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))
    mpns = {p["value"] for i in plan for p in i.get("parameters", []) if p["name"] == "Manufacturer Part Number"}
    assert "4.7uF" not in mpns, plan
    names = sorted(i["name"] for i in plan)
    assert len(names) == 2 and "0603" in names[0] and "0805" in names[1], names    # one part per size


def test_more_value_spellings_are_no_part_number():
    for s in ["4700pF", "1000pF", "10000pF", "1000uF", "100n", "10u", "22p", ".1uF", "100mOhm", "10mR", "R010",
              "4700Ω", "1000 Ohm"]:
        assert BT._not_a_part_number(s) == "value", s
    for s in ["100N", "22P", "2200U", "1000", "R12", "R1000", "R0805", "R0603", "R0402", "10000", "74HC595", "CL10"]:
        assert BT._not_a_part_number(s) is None, s


VALUE_AS_PART_4DIGIT = """ITEM,QTY,REFERENCE DESIGNATORS,PART,DESCRIPTION,MANUFACTURER
1,1,C1,4700pF,CAP CER 4700PF 50V X7R 0603,Murata
2,1,C2,4700pF,CAP CER 4700PF 50V X7R 0805,Murata
"""


@lite
def test_four_digit_values_as_part_number_are_not_built_as_one_part(tmp_path):
    import json
    from ecadlib import librarian as L
    bom = tmp_path / "bom.csv"
    bom.write_text(VALUE_AS_PART_4DIGIT, encoding="utf-8")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache", log=lambda *a, **k: None)
    plan = json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))
    mpns = {p["value"] for i in plan for p in i.get("parameters", []) if p["name"] == "Manufacturer Part Number"}
    assert "4700pF" not in mpns, plan
    assert len(plan) == 2, [i["name"] for i in plan]


@lite
def test_a_value_in_the_mpn_cell_that_is_not_the_value_is_held(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text("Reference,Value,Footprint,MPN,Description\n"
                 "C2,1uF,Capacitor_SMD:C_0603_1608Metric,10uF,CAP 1uF 0603\n"
                 "R7,1k,Resistor_SMD:R_0603_1608Metric,10k,RES 1k 0603\n"
                 "R8,4k7,Resistor_SMD:R_0603_1608Metric,4.7K,RES 4.7K 0603\n"
                 "C3,100nF,Capacitor_SMD:C_0603_1608Metric,0.1uF,CAP 0603\n", encoding="utf-8")
    lines, _ = BT.read_bom(p)
    hold = {d["designator"]: d.get("bom_hold", "") for d in lines}
    assert "10uF" in hold["C2"] and "10k" in hold["R7"], hold
    assert not hold["R8"] and not hold["C3"], hold                 # the same value spelt another way
