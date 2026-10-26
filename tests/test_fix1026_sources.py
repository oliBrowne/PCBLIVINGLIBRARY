"""2026-10-26 second review of PR #196 (sources): BOM size headers, datasheet SSRF, maker names, KiCad land choice,
LCSC numbers in links. Plain functions: python -m tests.run_all test_fix1026_sources"""
import contextlib, io

from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


def _read(f, text):
    f.write_bytes(text.encode("utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        return T.read_bom(f)


# ---------------------------------------------------------------- R-SRC-1: a header naming metric and imperial
@lite
def test_a_header_naming_metric_and_imp_is_not_metric_only(tmp_path):
    # 'Case (Metric / Imp.)' was read as metric-only ('imp' was no imperial word), so a 0603 resistor became an 0201
    for h in ("Case (Metric / Imp.)", "Case (metric/imp)", "Package (Imp/Metric)", "Size (metric, US)",
              "Case Code Metric / Imperial", "Footprint (Inch/MM)", "Case (mm / mil)", "Case Code (EIA/mm)"):
        assert not T.metric_header(T._hnorm(h)), h
        assert T.mixed_header(T._hnorm(h)), h
    for h in ("Case Code (mm)", "Metric size", "Package", "Case Code (in)", "Package (EIA)", "Case Code in mm"):
        assert not T.mixed_header(T._hnorm(h)), h
    assert T.metric_header(T._hnorm("Case Code in mm"))                   # 'in' there is no inch


@lite
def test_a_code_valid_in_both_systems_under_a_mixed_header_is_held_not_converted(tmp_path):
    for hdr in ("Case (Metric / Imp.)", "Package (Imp/Metric)", "Case (Imperial/Metric)"):
        lines, m = _read(tmp_path / "imp.csv", f"Designator,Qty,MPN,Manufacturer,{hdr}\n"
                                               "R1,1,RC0603FR-0710KL,Yageo,0603\nC1,1,XYZ,Acme,1608\n"
                                               "C2,1,XYZ2,Acme,0805\nU1,1,LDO,Acme,SOT-23-5\nR2,1,XYZ3,Acme,0402\n"
                                               "C3,1,XYZ4,Acme,0603 mm\n")
        assert m["footprint"] == hdr, m
        by = {l["designator"]: l for l in lines}
        assert by["R1"].get("footprint", "") not in ("0201",), (hdr, by["R1"])     # never the wrong size
        assert not by["R1"].get("footprint") and "0603" in by["R1"].get("bom_note", ""), (hdr, by["R1"])
        assert not by["R2"].get("footprint") and "0402" in by["R2"].get("bom_note", ""), (hdr, by["R2"])
        assert by["C1"]["footprint"] == "0603", (hdr, by["C1"])      # 1608 is no inch code: metric, so an 0603
        assert by["C2"]["footprint"] == "0805", (hdr, by["C2"])      # 0805 is no metric code: an inch 0805
        assert by["U1"]["footprint"] == "SOT-23-5", (hdr, by["U1"])
        assert by["C3"]["footprint"] == "0201", (hdr, by["C3"])      # the cell itself says mm


@lite
def test_plain_and_inch_headers_keep_their_0603(tmp_path):
    for hdr in ("Package", "Case Code", "Package (EIA)", "Case (inch)"):
        lines, _ = _read(tmp_path / "p.csv", f"Designator,Qty,MPN,{hdr}\nR1,1,RC0603FR-0710KL,0603\n")
        assert lines[0]["footprint"] == "0603", (hdr, lines)


@lite
def test_an_inch_column_wins_over_a_mixed_one(tmp_path):
    for hdr, row in (("Case (Metric / Imp.),Case Code (in)", "0603,0603"), ("Case Code (in),Case (Metric / Imp.)", "0603,0603")):
        lines, m = _read(tmp_path / "b.csv", f"Designator,Qty,MPN,{hdr}\nR1,1,RC0603FR-0710KL,{row}\n")
        assert m["footprint"] == "Case Code (in)", (hdr, m)
        assert lines[0]["footprint"] == "0603", (hdr, lines)
