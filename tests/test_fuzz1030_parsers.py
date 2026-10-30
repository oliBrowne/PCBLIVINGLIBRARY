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
