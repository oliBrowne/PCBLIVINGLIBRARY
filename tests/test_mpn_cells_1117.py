"""Messy MPN cells from real BOMs (2026-11-17 hold ranking): Excel's text apostrophe, quotes, a trailing full stop or
footnote star, the maker's name typed into the cell, a DNP mark after the number, an 'alt:' second number, and a
lower-case number the part search spells in capitals. Before the fix three of these released a part whose
Manufacturer Part Number nobody can order ('IRLZ44NPBF, MCP2562-E/SN., a lower-case number); the rest were held
because the part search missed them. Plain functions: python3 -m tests.run_all test_mpn_cells_1117"""
import tempfile
from pathlib import Path

from ecadlib import bom_triage as T, providers as P
from ecadlib.kicad_index import KiCadIndex


def _read(rows, header="Designator,Manufacturer,MPN"):
    f = Path(tempfile.mkdtemp()) / "bom.csv"
    f.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    lines, _ = T.read_bom(f)
    return lines


def test_excel_apostrophe_quotes_and_trailing_marks_are_dropped():
    lines = _read(["Q1,Infineon,'IRLZ44NPBF", 'C1,Samsung,"""CL10C101JB8NNNC"""', "U1,Microchip,MCP2562-E/SN.",
                   "R1,Yageo,RC0805FR-0722RL*", "R2,Yageo,RC0603FR-0710KL;", "D1,Vishay,‘SMF5.0A’"])
    assert [l["mpn"] for l in lines] == ["IRLZ44NPBF", "CL10C101JB8NNNC", "MCP2562-E/SN", "RC0805FR-0722RL",
                                         "RC0603FR-0710KL", "SMF5.0A"], lines
    assert all("bom_note" in l for l in lines), lines      # every change is said, never silent


def test_part_number_marks_that_belong_to_it_are_kept():
    lines = _read(["U1,Maxim,MAX232ESE+", "U2,Analog Devices,LT1086CT#PBF", "U3,TI,LM358DR/NOPB", "D1,Diodes,1N4148W-7-F",
                   "U4,Microchip,25LC1024-I/SM", "J1,Molex,0022232021", "R1,Vishay,CRCW0603100RFKEA"])
    assert [l["mpn"] for l in lines] == ["MAX232ESE+", "LT1086CT#PBF", "LM358DR/NOPB", "1N4148W-7-F", "25LC1024-I/SM",
                                         "0022232021", "CRCW0603100RFKEA"], lines
    assert not any("bom_note" in l for l in lines), lines


def test_the_lines_own_maker_typed_into_the_cell_is_dropped():
    lines = _read(["R1,Yageo,Yageo RC0603FR-07470RL", "C1,KEMET,C0603C104K5RACTU (KEMET)",
                   "C2,Wurth Elektronik,885012206046 (Würth Elektronik)", "R2,KOA Speer,RK73H1JTTD1002F - KOA Speer",
                   "Q1,Nexperia,\"MMBT3904,215 (Nexperia USA Inc.)\""])
    assert [l["mpn"] for l in lines] == ["RC0603FR-07470RL", "C0603C104K5RACTU", "885012206046", "RK73H1JTTD1002F",
                                         "MMBT3904,215"], lines
    assert all("maker" in l["bom_note"] for l in lines), lines


def test_a_bracketed_word_that_is_not_the_lines_maker_stays():
    # another maker's name, a package or an ordering note in brackets is not dropped on a guess
    lines = _read(["C1,Murata,C0603C104K5RACTU (KEMET)", "U1,TI,TPS62133RGTR (QFN)", "J1,JST,B2B-XH-A(LF)(SN)",
                   "U2,,LM358DR (TI)", "R1,Yageo,Yageo"])
    assert [l.get("mpn") for l in lines] == ["C0603C104K5RACTU (KEMET)", "TPS62133RGTR (QFN)", "B2B-XH-A(LF)(SN)",
                                             "LM358DR (TI)", None], lines
    # a cell holding only the maker's name names no part (2026-11-19): held, never searched or name-matched
    assert "a maker's name or a label, not a part number" in lines[-1]["bom_hold"], lines[-1]


def test_a_dnp_mark_after_the_number_makes_the_line_dnp_like_a_value_mark():
    lines = _read(["C1,TDK,C1608X7R1E105K080AB (DNP)", "C2,Murata,GRM155R61A105KE15D [DNF]", "R1,Yageo,RC0603FR-0768KL DNP",
                   "R2,Yageo,RC0603FR-0710KL"])
    assert [l["mpn"] for l in lines] == ["C1608X7R1E105K080AB", "GRM155R61A105KE15D", "RC0603FR-0768KL",
                                         "RC0603FR-0710KL"], lines
    assert [bool(l.get("dnp")) for l in lines] == [True, True, True, False], lines
    assert not any("mpn_dnp" in l for l in lines), lines        # the reader's own mark does not leak into the line


def test_an_alt_second_number_is_an_alternate_not_part_of_the_number():
    lines = _read(['R1,Stackpole,"RMCF0402FT47K0\nalt: RC0402FR-0747KL"', "C1,Samsung,CC1206KKX7R9BB475 Alt. GRM31CR71H475KA12L",
                   "U1,TI,LM358DR 2nd source: LM358ADR"])
    assert [l["mpn"] for l in lines] == ["RMCF0402FT47K0", "CC1206KKX7R9BB475", "LM358DR"], lines
    assert "RC0402FR-0747KL" in lines[0]["bom_note"] and "LM358ADR" in lines[2]["bom_note"], lines


class _Src:
    name = "digikey"

    def __init__(self, spelt):
        self.spelt = spelt

    def lookup(self, work):
        return P.SourceResult(self.name, found=True, mpn=self.spelt, manufacturer="Murata")


def test_an_all_lower_case_number_takes_the_part_searchs_spelling():
    lines = [{"designator": "C1", "mpn": "grm21br61c106ke15l"}]
    row = T.triage(lines, KiCadIndex([]), [_Src("GRM21BR61C106KE15L")], None)[0]
    assert lines[0]["mpn"] == row["mpn"] == "GRM21BR61C106KE15L", (lines, row)
    assert "spells it" in row["notes"], row


def test_a_number_with_capitals_keeps_the_boms_spelling():
    # ATmega328P-AU / ATMEGA328P-AU: a BOM that wrote capitals keeps them (no new revision of a released part)
    lines = [{"designator": "U1", "mpn": "ATMEGA328P-AU"}]
    row = T.triage(lines, KiCadIndex([]), [_Src("ATmega328P-AU")], None)[0]
    assert lines[0]["mpn"] == row["mpn"] == "ATMEGA328P-AU", (lines, row)
    lines = [{"designator": "U1", "mpn": "lm358d"}]          # another part (a near number): never taken
    row = T.triage(lines, KiCadIndex([]), [_Src("LM358DR")], None)[0]
    assert lines[0]["mpn"] == "lm358d", lines
