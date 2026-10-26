"""2026-10-26 superpowers bug hunt (bommcp): BOM import / triage and MCP-vs-CLI parity.
Plain functions: python -m tests.run_all test_hunt1026_bommcp"""
import contextlib, io, json

from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


def _read(f, text):
    f.write_bytes(text.encode("utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        return T.read_bom(f)


# ---------------------------------------------------------------- bommcp-01: a millimetre case-code column
@lite
def test_metric_case_code_column_is_not_read_as_an_imperial_size(tmp_path):
    # 'Case Code (mm)' 0603 is the metric code of an imperial 0201 part: read as the footprint it was an 0603, and the
    # librarian built a ready 0603 land for a 0201 capacitor
    lines, m = _read(tmp_path / "mm.csv", "Designator,Qty,Value,Manufacturer,MPN,Case Code (mm)\n"
                                          "C1,1,100nF,Acme,XYZCAP100N,0603\nR1,1,10k,Acme,XYZRES10K,1608\n"
                                          "R2,1,1k,Acme,XYZRES1K,1206\nU1,1,LDO,Acme,XYZLDO,SOT-23-5\n")
    from ecadlib.kicad_index import package_of
    by = {l["designator"]: l for l in lines}
    assert package_of(by["C1"].get("footprint")) == "0201", by["C1"]
    assert package_of(by["R1"].get("footprint")) == "0603", by["R1"]
    # 1206 is no metric chip code: which size it is cannot be told, so it is not used (the part is held, not guessed)
    assert not by["R2"].get("footprint") and "mm" in by["R2"].get("bom_note", ""), by["R2"]
    assert by["U1"]["footprint"] == "SOT-23-5", by["U1"]                  # a package name is not a size code


@lite
def test_imperial_case_column_wins_over_the_metric_one_in_either_order(tmp_path):
    for hdr, row in (("Case Code (mm),Case Code (in)", "0603,0201"), ("Case Code (in),Case Code (mm)", "0201,0603")):
        lines, m = _read(tmp_path / "both.csv", f"Designator,Qty,Value,MPN,{hdr}\nC1,1,100nF,XYZCAP100N,{row}\n")
        assert m["footprint"] == "Case Code (in)", (hdr, m)
        assert lines[0]["footprint"] == "0201", (hdr, lines)
