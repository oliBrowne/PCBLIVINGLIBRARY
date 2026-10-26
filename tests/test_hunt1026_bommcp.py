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


# ---------------------------------------------------------------- bommcp-02: designators on two lines
def test_ref_keys_reads_ranges_as_count_refs_does():
    # shared_designators (R2 = 10k on one line and R2 = 1k on another) expands with ref_keys, which knew only 'R1-R4':
    # 'R1..R4', 'R1 to R4', 'R1:R4', a reversed 'R5-R1' and 'R1/R2' were missed (or kept whole as 'R1..R4')
    for s, want in (("R1..R4", {"R1", "R2", "R3", "R4"}), ("R1 to R3", {"R1", "R2", "R3"}), ("R1:R3", {"R1", "R2", "R3"}),
                    ("R5-R3", {"R3", "R4", "R5"}), ("R1/R2", {"R1", "R2"}), ("R1&R2+R3", {"R1", "R2", "R3"}),
                    ("U1A, U1B", {"U1"}), ("R1_CH1, R1_CH2", {"R1_CH1", "R1_CH2"}), ("C1~C3", {"C1", "C2", "C3"})):
        assert T.ref_keys(s) == want, (s, T.ref_keys(s))
        assert T.count_refs(s) == len(want), (s, T.count_refs(s))
    lines = [{"designator": "R1..R4", "mpn": "A"}, {"designator": "R2", "mpn": "B"}, {"designator": "R9-R5", "mpn": "C"},
             {"designator": "R7", "mpn": "D"}]
    assert set(T.shared_designators(lines)) == {1, 2, 3, 4}, T.shared_designators(lines)


@lite
def test_workspace_triage_says_a_designator_is_on_two_lines_as_the_cli_does(tmp_path, monkeypatch):
    # the web UI / MCP triage_bom ran triage() one line at a time, so no line ever saw the others and the CLI's
    # 'designator on two lines' note never reached them
    from ecadlib.app import Workspace
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    ws = Workspace(str(tmp_path / "ws"), [], [], True)
    (ws.root / "b.csv").write_text("Designator,Qty,Value,MPN\nR1,1,10k,RC0603FR-0710KL\n\"R2,R3\",2,1k,RC0603FR-071KL\n"
                                   "R3,1,100R,RC0603FR-07100RL\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = ws.triage("b.csv")
    notes = {x["line"]: x["notes"] for x in r["rows"]}
    assert "R3 is also on line 3" in notes[2] and "R3 is also on line 2" in notes[3], notes
    assert "designator on two lines" not in notes[1], notes
