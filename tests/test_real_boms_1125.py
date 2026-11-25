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
