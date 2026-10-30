"""Property-based fuzz round (parsers, 2026-10-30): the counterexamples hypothesis found, as plain regression tests."""
import json, struct
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]


def _soic_pcblib(tmp_path):
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    p = tmp_path / "a.PcbLib"
    AL.write_pcblib(p, [AL.footprint_from_spec(spec)[0]])
    return p


@lite
def test_ole_header_with_a_bad_sector_size_is_a_clean_error(tmp_path):
    """One damaged header byte (sector shift 34: 16 GB sectors) made olefile read a 16 GB sector: a raw MemoryError,
    not the 'damaged file' message; a bad mini sector shift (3) read the mini streams wrong without a word."""
    from ecadlib import altium_lib as AL
    good = _soic_pcblib(tmp_path).read_bytes()
    for off, val in ((0x1E, 34), (0x1E, 31), (0x20, 3)):
        b = bytearray(good)
        struct.pack_into("<H", b, off, val)
        p = tmp_path / "bad.PcbLib"
        p.write_bytes(bytes(b))
        try:
            AL.read_pcblib(p)
        except ValueError as e:
            assert "damaged" in str(e), e
        else:
            raise AssertionError(f"header {off:#x}={val} read without an error")
    assert AL.read_pcblib(tmp_path / "a.PcbLib")      # the good file still reads


def _read(tmp_path, text):
    import contextlib, io
    from ecadlib import bom_triage as BT
    p = tmp_path / "bom.csv"
    p.write_text(text, encoding="utf-8")
    with contextlib.redirect_stderr(io.StringIO()):
        return {ln["designator"]: ln for ln in BT.read_bom(p)[0]}


@lite
def test_stray_comma_left_of_value_is_not_a_decimal_comma(tmp_path):
    """An unquoted 'Cap, X7R' in a column before Value shifted the row by one, and the cell shifted into Value (the
    footprint 0603) was joined with the real value as '0603,100n' (603.1 nF), footprint 'X7R': built wrong, not held."""
    got = _read(tmp_path, "Designator,Qty,MPN,Description,Footprint,Value\nR1,1,RC0603A,Resistor,0603,10k\n"
                          "C1,1,GRM188A,Cap, X7R,0603,100n\nC2,1,GRM188B,Capacitor,0805,1u\n")
    c1 = got["C1"]
    assert c1.get("bom_hold") and c1.get("value") != "0603,100n" and not c1.get("footprint"), c1
    assert got["R1"]["value"] == "10k" and got["C2"]["footprint"] == "0805"


@lite
def test_stray_comma_before_value_does_not_turn_qty_into_text(tmp_path):
    """Description first: 'Cap, X7R,1,10k,...' was read as Qty 'X7R', Value '1,10k' (1.1k), not held: the qty shifted
    into Value."""
    got = _read(tmp_path, "Description,Qty,Value,MPN,Footprint,Designator\nCap, X7R,1,10k,GRM188A,0603,C1\n")
    c1 = got.get("C1") or next(iter(got.values()))
    assert c1.get("bom_hold") and c1.get("value") != "1,10k", c1


@lite
def test_decimal_comma_value_still_joined(tmp_path):
    """The real case stays: an unquoted '4,7k' in the Value column is one value."""
    got = _read(tmp_path, "Designator,Qty,Footprint,Value,MPN\nR1,1,0603,4,7k,RC0603A\nR2,1,0603,10k,RC0603B\n")
    assert got["R1"]["value"] == "4,7k" and got["R1"]["mpn"] == "RC0603A" and not got["R1"].get("bom_hold"), got


@lite
def test_decimal_comma_value_with_empty_last_column(tmp_path):
    """'R1,1,4,7k,RC0603A,0603,' (Description empty): the stray comma went unseen (the row's last cell is empty, so it
    looked as wide as the header): Value 4, MPN '7k', Footprint 'RC0603A'."""
    got = _read(tmp_path, "Designator,Qty,Value,MPN,Footprint,Description\nR1,1,4,7k,RC0603A,0603,\n"
                          "R2,1,10k,RC0603B,0603,Resistor\n")
    r1 = got["R1"]
    assert r1["value"] == "4,7k" and r1["mpn"] == "RC0603A" and r1["footprint"] == "0603" or r1.get("bom_hold"), r1
    assert r1.get("mpn") != "7k", r1
    assert got["R2"]["mpn"] == "RC0603B" and not got["R2"].get("bom_hold")


@lite
def test_trailing_empty_cells_on_every_row_are_not_a_stray_comma(tmp_path):
    """Excel pads every row (header too) to the widest one: those trailing empty cells are no stray comma."""
    got = _read(tmp_path, "Designator,Qty,Value,MPN,,\nR1,1,10k,RC0603A,,\nR2,1,4k7,RC0603B,,\n")
    assert all(not ln.get("bom_hold") for ln in got.values()) and got["R1"]["mpn"] == "RC0603A", got


@lite
def test_stray_comma_shifting_a_footprint_into_qty_is_held(tmp_path):
    """'Cap, X7R' before a Qty / Value pair: the footprint 0603 shifted into Qty (a number, so it passed), the Qty 1 into
    Value: '1,10k' (1.1k), MPN 'X7R'. Qty 0603 does not count the one designator R2."""
    got = _read(tmp_path, "Description,MPN,Footprint,Qty,Value,Designator\nResistor,AA000,0603,1,4,7k,R1\n"
                          "Cap, X7R,AB000,0603,1,10k,R2\n")
    r2 = got.get("R2") or next(ln for ln in got.values() if ln.get("description") == "Cap")
    assert r2.get("bom_hold") and r2.get("value") != "1,10k", r2
    assert got["R1"]["value"] == "4,7k" and got["R1"]["mpn"] == "AA000", got


@lite
def test_data_rows_with_one_trailing_comma_the_header_lacks_still_read(tmp_path):
    """Review: a script-written BOM ending every data row in ',' (the header not): each row a cell wider than the
    header, but no value is split, so nothing was shifted; every row was held, its MPN and footprint dropped."""
    got = _read(tmp_path, "Designator,Qty,Value,Footprint,MPN\nR1,1,10k,0603,RC0603FR-0710KL,\n"
                          "C1,1,100n,0402,CL05B104KO5NNNC,\n")
    assert not got["R1"].get("bom_hold") and got["R1"]["mpn"] == "RC0603FR-0710KL", got["R1"]
    assert got["C1"]["footprint"] == "0402" and got["C1"]["value"] == "100n", got["C1"]


@lite
def test_decimal_comma_with_a_package_only_its_row_has_still_joined(tmp_path):
    """Review: Footprint before Value, and the zener's SOD-123 the only one in the BOM: a footprint shaped unlike the
    other rows' is weak evidence of a shift (the Qty count and a leading zero are the strong ones); '3,3V' is one value."""
    got = _read(tmp_path, "Footprint,Designator,Value,MPN\n0603,R1,10k,RC0603A\nSOD-123,D1,3,3V,BZT52C3V3\n")
    assert got["D1"]["value"] == "3,3V" and got["D1"]["mpn"] == "BZT52C3V3" and not got["D1"].get("bom_hold"), got
