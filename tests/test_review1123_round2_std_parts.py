"""Round 2 of the 2026-11-23 review, group R-A (standard parts): what the independent review of the fix branch still
got released wrong with a small variation of the first review's input, and the ordinary lines its rules held.
Evidence: reviews/2026-11-23_full_code_review_evidence/fix_review/ROUND2_FINDINGS.md (C1, C5, I3, I8, I10, Minor R-A).
Each finding has must-hold cases and must-stay-ready cases.
Run:  python3 -m tests.run_all test_review1123_round2_std_parts"""
import json
import tempfile
from pathlib import Path

from ecadlib import bom_triage as T, librarian as L, stdparts as SP
from tests.fixtures_lite import lite


def _run(tmp_path, bom_text, name="bom.csv"):
    """The librarian offline with the standard source only -> {part name: plan item}."""
    bom = tmp_path / name
    bom.write_text(bom_text, encoding="utf-8")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache",
          log=lambda *a, **k: None)
    return {i["name"]: i for i in json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))}


def _why(item):
    return " | ".join(str(x) for x in (item.get("release_check") or {}).get("failures") or [])


def _fp(item):
    return str((item.get("source") or {}).get("footprint") or "")


def _status(plan):
    return {n: (i["status"], _why(i)[:200]) for n, i in plan.items()}


def _read(text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.csv"
        p.write_text(text, encoding="utf-8")
        return T.read_bom(p)[0]


# ---------------------------------------------------------------- C5 a tantalum's case letter, a lone '+'
def test_a_single_letter_or_a_plus_is_not_understood():
    for kind, text, word in [("C", "10uF/16V/A", "A"), ("C", "15uF 16V Case A", "A"), ("C", "15uF/16V A-case", "A"),
                             ("C", "100uF 10V case C", "C"), ("C", "10uF 16V size A", "A"), ("C", "47uF D case", "D"),
                             ("C", "100uF 6.3V SMD-B", "B"), ("C", "10uF +", "+"), ("C", "10uF 10V +", "+"),
                             ("C", "10uF (+)", "+"), ("C", "22uF 16V T", "T"), ("C", "10uF type C", "C"),
                             ("R", "10k L", "L"), ("R", "10k M", "M"), ("R", "10k N", "N"), ("R", "10k 4", "4"),
                             ("R", "Resistor 4", "4"), ("C", "100nF 5", "5")]:
        assert SP.unread_words(text, kind) == [word], (text, SP.unread_words(text, kind))
    for text, word in [("1206_A", "A"), ("1206-A", "A"), ("1206 L", "L"), ("CASE A 1206", "A"), ("A (1206)", "A"),
                       ("1206 +", "+"), ("1210 B", "B"), ("1206 case C", "C")]:
        assert SP.unread_words(text, "C", footprint=True) == [word], (text, SP.unread_words(text, "C", footprint=True))
        r = SP.resolve([text], "C", "", "", value="10uF")
        assert r["footprint"] is None and r["symbol"] is None, (text, r)


def test_plain_words_and_signs_stay_understood():
    for kind, text in [("C", "10uF 16V X7R 0603"), ("C", "100n"), ("R", "4k7 1%"), ("C", "100nF +/-10% 50V X7R"),
                       ("C", "100nF +-10%"), ("C", "1uF +80/-20% Y5V"), ("C", "100nF X7R -55°C~+125°C"),
                       ("C", "100nF ±10%"), ("R", "10k N/A"), ("R", "RES 10K OHM 1% 1/10W 0603 n/a"),
                       ("C", "CAP CER 0.1UF 50V X7R 0603 Class II"), ("C", "100nF Class 2"), ("C", "100nF Type X7R"),
                       ("C", "100nF Size 0603"), ("C", "100nF Case 0603"), ("R", "10k package 0603"),
                       ("C", "C 100nF"), ("R", "R 10k"), ("C", "100nF +10%"), ("C", "100nF 50V +85C"), ("R", "5"),
                       ("R", "RES 10K OHM 1% 3/4W 2010"), ("R", "RES SMD 10K OHM 5% 1/8W 0805"), ("R", "10k 5 %"),
                       ("C", "CAP CER 2 PF 50V C0G 0402"), ("R", "AEC-Q200 Grade 3 10k")]:
        assert SP.unread_words(text, kind) == [], (text, SP.unread_words(text, kind))
    for kind, text, want in [("C", "C 0603", "C_0603_1608Metric"), ("C", "C_0603", "C_0603_1608Metric"),
                             ("R", "R 0603", "R_0603_1608Metric"), ("C", "Case 0603", "C_0603_1608Metric"),
                             ("C", "Size: 0805", "C_0805_2012Metric"), ("L", "0805 L", "L_0805_2012Metric"),
                             ("C", "0603M", "C_0201_0603Metric"), ("C", "1608M", "C_0603_1608Metric")]:
        r = SP.resolve([text], kind, "", "", value="")
        assert r["footprint"] == want, (kind, text, r)


C5_BOM = """Designator,Qty,Value,Footprint,Description
C1,1,10uF/16V/A,1206,
C2,1,15uF 16V Case A,1206,
C3,1,10uF,1206_A,
C4,1,10uF 25V,1206 L,
C5,1,10uF +,1206,
C6,1,15uF/16V A-case,1206,
C7,1,22uF,CASE A 1206,
C8,1,33uF,A (1206),
C9,1,10uF 16V X7R 0603,0603,
C10,1,100n,0402,
R1,1,4k7 1%,0805,
R2,1,10k,0603,RES 10K OHM 1% 1/10W 0603
"""


@lite
def test_case_letters_and_plus_end_to_end(tmp_path):
    plan = _run(tmp_path, C5_BOM)
    ready = sorted(n for n, i in plan.items() if i["status"] == "ready")
    assert len(plan) == 12 and ready == ["CAP-0402-100n", "CAP-0603-10uF16VX7R0603", "RES-0603-10k", "RES-0805-4k71%"], \
        _status(plan)
    assert not [n for n, i in plan.items() if n not in ready and _fp(i).startswith("standard:")], _status(plan)
    said = " ".join(_why(i) for i in plan.values())
    assert "says 'A'" in said and "says '+'" in said, said


def test_repeated_headers_keep_every_cell():
    lines = _read("Designator,Value,Footprint,Type,Type\nC1,10uF,1206,tantalum,SMD\nC2,1uF,0603,SMD,ceramic\n")
    assert T.other_cells(lines[0]) == {"Type": "tantalum | SMD"}, lines[0]
    assert T.other_cells(lines[1]) == {"Type": "SMD | ceramic"}, lines[1]
    assert T.other_cells({"other_cells": "Note: a | b | Remark: c"}) == {"Note": "a | b", "Remark": "c"}


@lite
def test_a_hazard_word_under_a_repeated_header_is_held(tmp_path):
    plan = _run(tmp_path, "Designator,Value,Footprint,Type,Type\nC1,10uF,1206,tantalum,SMD\nC2,1uF,0603,ceramic,SMD\n"
                          "R1,10k,0603,thick film,SMD\n")
    st = _status(plan)
    assert sorted(n for n, (s, _) in st.items() if s == "ready") == ["CAP-0603-1uF", "RES-0603-10k"], st
    assert "'tantalum'" in st["CAP-1206-10uF"][1], st


# ---------------------------------------------------------------- I10 the header's meaning for a bare cell
def test_a_bare_cell_is_read_with_its_header():
    for bom, word in [("Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,yes\n", "Polarized"),
                      ("Designator,Qty,Value,Footprint,Polarised\nC1,1,10uF,1206,TRUE\n", "Polarised"),
                      ("Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,1\n", "Polarized"),
                      ("Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,X\n", "Polarized"),
                      ("Designator,Qty,Value,Footprint,Polarity\nC1,1,10uF,1206,+\n", "Polarity"),
                      ("Designator,Qty,Value,Footprint,Tantalum\nC1,1,10uF,1206,Y\n", "Tantalum"),
                      ("Designator,Qty,Value,Footprint,Pins\nR1,1,10k,1206,8\n", "8 Pins"),
                      ("Designator,Qty,Value,Footprint,Elements\nR1,1,10k,1206,4\n", "4 Elements"),
                      ("Designator,Qty,Value,Footprint,Array\nR1,1,10k,1206,yes\n", "Array"),
                      ("Designator,Qty,Value,Footprint,No. of resistors\nR1,1,10k,1206,4\n", "4 resistors")]:
        ln = _read(bom)[0]
        cat = "Capacitors" if ln["designator"].startswith("C") else "Resistors"
        why = SP.generic_passive_hold(cat, ln["value"], "", T.said_cells(ln))
        assert why and word.lower() in why.lower(), (bom, T.said_cells(ln), why)
    for bom in ["Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,no\n",
                "Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,0\n",
                "Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,FALSE\n",
                "Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,-\n",
                "Designator,Qty,Value,Footprint,Polarity\nC1,1,10uF,1206,none\n",
                "Designator,Qty,Value,Footprint,Pins\nR1,1,10k,1206,2\n",
                "Designator,Qty,Value,Footprint,Pads\nR1,1,10k,1206,2\n",
                "Designator,Qty,Value,Footprint,Height\nR1,1,10k,1206,4\n",
                "Designator,Qty,Value,Footprint,Lead Time\nR1,1,10k,1206,4\n",
                "Designator,Qty,Value,Footprint,Cap (pF)\nC1,1,5p,0603,5\n",
                "Designator,Qty,Value,Footprint,Res\nR1,1,4R,0603,4\n",
                "Designator,Qty,Value,Footprint,Stock,Unit Price,Voltage,Tolerance\nC1,1,100n,0603,4,0.04,16,10\n",
                "Designator,Qty,Value,Footprint,Dielectric,Rating\nC1,1,100n,0603,X7R,50V\n"]:
        ln = _read(bom)[0]
        cat = "Capacitors" if ln["designator"].startswith("C") else "Resistors"
        assert SP.generic_passive_hold(cat, ln["value"], "", T.said_cells(ln)) is None, (bom, T.said_cells(ln))


@lite
def test_header_meaning_end_to_end(tmp_path):
    plan = _run(tmp_path, "Designator,Qty,Value,Footprint,Polarized,Pins\nC1,1,10uF,1206,yes,2\nC2,1,1uF,0603,no,2\n"
                          "R1,1,10k,1206,,8\nR2,1,4k7 1%,0805,no,2\nC3,1,100n,0402,,\n")
    st = _status(plan)
    assert sorted(n for n, (s, _) in st.items() if s == "ready") == ["CAP-0402-100n", "CAP-0603-1uF", "RES-0805-4k71%"], st
    assert "Polarized" in st["CAP-1206-10uF"][1] and "8 Pins" in st["RES-1206-10k"][1], st
    assert not _fp(plan["CAP-1206-10uF"]).startswith("standard:") and not _fp(plan["RES-1206-10k"]).startswith("standard:")


# ---------------------------------------------------------------- C1 every merged no-MPN line is read
MERGED_HOLD = [
    ("Designator,Qty,Value,Footprint,Description\nC1,1,10uF,1206,\nC2,1,10uF,1206,CAP TANT 10uF 16V\n", "TANT"),
    ("Designator,Qty,Value,Footprint,Description,Comment\nC1,1,10uF,1206,,\nC2,1,10uF,1206,,tantalum\n", "tantalum"),
    ("Designator,Qty,Value,Footprint,Description\nR1,1,10k,1206,\nR2,1,10k,1206,RES ARRAY 4x10k\n", "ARRAY"),
    ("Designator,Qty,Value,Footprint,Notes\nR1,1,10k,1206,\nR2,1,10k,1206,\nR3,1,10k,1206,x4\n", "x4"),
    ("Designator,Qty,Value,Footprint,Remark\nC1,1,10uF,1206,\nC2,1,10uF,1206,polarized\n", "polarized"),
    ("Designator,Qty,Value,Footprint,LibRef\nC1,1,10uF,1206,\nC2,1,10uF,1206,Cap Pol1\n", "Pol"),
    ("Designator,Qty,Value,Footprint,Polarized\nC1,1,10uF,1206,\nC2,1,10uF,1206,yes\n", "Polarized"),
]


@lite
def test_a_hazard_word_on_a_later_merged_line_is_held(tmp_path):
    for n, (bom, word) in enumerate(MERGED_HOLD):
        d = tmp_path / str(n)
        d.mkdir()
        plan = _run(d, bom)
        assert len(plan) == 1, (bom, list(plan))
        item = next(iter(plan.values()))
        assert item["status"] != "ready" and word.lower() in _why(item).lower(), (bom, item["status"], _why(item))


@lite
def test_merged_lines_with_nothing_to_hold_stay_ready(tmp_path):
    plan = _run(tmp_path, "Designator,Qty,Value,Footprint,Description,Comment\n"
                          "C1,1,100n,0603,,\nC2,1,100n,0603,CAP CER 0.1UF 50V X7R 0603,\nC3,1,100n,0603,,X7R\n"
                          "R1,1,10k,0603,,\nR2,1,10k,0603,RES SMD 10K OHM 1% 1/10W 0603,\nR3,2,10k,0603,,\n"
                          "C4,1,10uF 16V X7R 0603,0603,,\nR4,1,4k7 1%,0805,,\nR5,1,4k7 1%,0805,,\n")
    st = _status(plan)
    assert len(plan) == 4 and all(s == "ready" for s, _ in st.values()), st


# ---------------------------------------------------------------- I3 ordinary BOMs: administrative columns, symbol names
ORDINARY_MERGES = [
    '''"Item","Qty","Reference(s)","Value","LibPart","Footprint","Datasheet","DNP"
"1","2","R1, R2","10k","Device:R","Resistor_SMD:R_0603_1608Metric","~",""
"2","1","R3","10k","Device:R_Small","Resistor_SMD:R_0603_1608Metric","~",""
"3","1","C1","100n","Device:C","Capacitor_SMD:C_0603_1608Metric","~",""
"4","1","C2","100n","Device:C_Small","Capacitor_SMD:C_0603_1608Metric","~",""
''',
    "Designator,Value,Footprint,Function\nR1,10k,0603,pull-up\nR2,10k,0603,LED current limit\n"
    "C1,100nF,0603,decoupling U1\nC2,100nF,0603,decoupling U2\n",
    "Designator,Value,Footprint,Unit Price,Stock,Supplier\nR1,10k,0603,0.01,1000,LCSC\nR2,10k,0603,0.012,500,Mouser\n",
    "Reference,Value,Footprint,Sheetname,Sheetfile,Exclude from BOM\nR1,10k,0603,power,power.kicad_sch,\n"
    "R2,10k,0603,mcu,mcu.kicad_sch,\n",
    "Designator,Comment,Footprint,Description,LibRef\nR1,10K,0603,Resistor,Res1\nR2,10K,0603,Resistor,Res2\n"
    "R3,10K,0603,Resistor,RES\n",
    "Designator,Value,Footprint,LibRef,Ext Price,Lead Time,Net,Purpose\nC1,100n,0603,Cap,0.04,4 weeks,VDD,bypass\n"
    "C2,100n,0603,CAP,0.08,6 weeks,VCC,bulk\nR1,10k,0603,R,0.01,4,SDA,I2C\nR2,10k,0603,R_US,0.01,6,SCL,I2C\n",
]


@lite
def test_ordinary_boms_with_administrative_columns_stay_ready(tmp_path):
    for n, bom in enumerate(ORDINARY_MERGES):
        d = tmp_path / str(n)
        d.mkdir()
        st = _status(_run(d, bom))
        assert st and all(s == "ready" for s, _ in st.values()), (bom, st)


@lite
def test_lines_that_differ_in_what_the_part_is_are_still_held(tmp_path):
    for n, bom in enumerate([
            "Designator,Value,Footprint,Tolerance\nR1,10k,0603,1%\nR2,10k,0603,5%\n",
            "Designator,Value,Footprint,Voltage\nC1,100n,0603,16V\nC2,100n,0603,50V\n",
            "Designator,Value,Footprint,LibRef\nR1,10k,0603,Res1\nR2,10k,0603,Res Pack4\n",
            "Designator,Value,Footprint,LibPart\nC1,10uF,1206,Device:C\nC2,10uF,1206,Device:C_Polarized\n",
            "Designator,Value,Footprint,Supplier Part Number 3\nR1,10k,0603,123-456\nR2,10k,0603,123-789\n",
            "Designator,Value,Footprint,Manufacturer\nR1,10k,0603,Yageo\nR2,10k,0603,Vishay\n"]):
        d = tmp_path / str(n)
        d.mkdir()
        plan = _run(d, bom)
        assert len(plan) == 1 and all(i["status"] != "ready" for i in plan.values()), (bom, _status(plan))


# ---------------------------------------------------------------- I8 a polarized word beats KiCad's chip land name
def _run_said(tmp_path, monkeypatch, said, text):
    """As _run, with the part search's description of each MPN in said (the search itself is not run offline)."""
    real = T.triage

    def fake(lines, *a, **k):
        rows = real(lines, *a, **k)
        for r in rows:
            if r["mpn"] in said:
                r.update(src_description=said[r["mpn"]], src_description_from="digikey")
        return rows
    monkeypatch.setattr(T, "triage", fake)
    return _run(tmp_path, text)


KICAD_HEAD = "Reference,Value,Footprint,MPN,Manufacturer,Description,Comment,Polarized,LibPart\n"
C1206 = "Capacitor_SMD:C_1206_3216Metric"


@lite
def test_a_polarized_word_from_any_source_beats_the_kicad_chip_name(tmp_path, monkeypatch):
    said = {"T58A0106M016C0200": "CAP TANT POLY 10UF 16V 1206", "TMCMA1A106MTRF": "CAP TANT 10UF 10V 20% 1206"}
    plan = _run_said(tmp_path, monkeypatch, said, KICAD_HEAD +
                     f"C1,10uF,{C1206},T58A0106M016C0200,Vishay,,,,\n"            # the part search says tantalum polymer
                     f"C2,10uF,{C1206},TMCMA1A106MTRF,Vishay,,,,\n"               # the part search says tantalum
                     f"C3,10uF tantalum,{C1206},TMCMA1C106MTRF,Vishay,,,,\n"      # the Value
                     f"C4,10uF,{C1206},TMCMA1D106MTRF,Vishay,CAP TANT 10UF,,,\n"  # the Description
                     f"C5,10uF,{C1206},TMCMA1E106MTRF,Vishay,,tantalum,,\n"       # another cell
                     f"C6,10uF,{C1206},TMCMA1V106MTRF,Vishay,,,yes,\n"            # a header
                     f"C7,10uF,{C1206},TMCMA0J106MTRF,Vishay,,,,Device:C_Polarized\n")   # the symbol's name
    assert len(plan) == 7, list(plan)
    for n, i in plan.items():
        assert i["status"] != "ready" and "C_1206_3216Metric" not in _fp(i), (n, i["status"], _fp(i), _why(i))
        assert i["symbol"]["ref"] == L.POLARIZED_SYMBOL, (n, i["symbol"])


def test_passive_kind_reads_the_part_search():
    item = {"folder": "Components/Capacitors", "description": "", "symbol": {"ref": "Capacitor"},
            "footprint": {"ref": "C_1206_3216Metric", "kicad": "Capacitor_SMD:C_1206_3216Metric"},
            "parameters": [{"name": "Manufacturer Part Number", "value": "T58A0106M016C0200"}, {"name": "Value", "value": "10uF"}]}
    ln = {"mpn": "T58A0106M016C0200", "value": "10uF", "footprint": "Capacitor_SMD:C_1206_3216Metric"}
    assert L.passive_kind(dict(item), ln) == "C"                   # what remains: nothing anywhere says polarized
    assert L.passive_kind(dict(item), ln, "CAP TANT POLY 10UF 16V 1206") == "CP"
    assert L.passive_kind(dict(item), ln, "CAP CER 10UF 16V X5R 1206") == "C"
    assert L.passive_kind(dict(item), dict(ln, other_cells="Polarized: yes")) == "CP"
    assert L.passive_kind(dict(item), dict(ln, other_cells="Polarized: no")) == "C"


@lite
def test_ceramics_on_the_kicad_chip_name_stay_ready(tmp_path, monkeypatch):
    said = {"UMK107B7104KA-T": "CAP CER 0.1UF 50V X7R 0603"}
    c0603 = "Capacitor_SMD:C_0603_1608Metric"
    plan = _run_said(tmp_path, monkeypatch, said, KICAD_HEAD +
                     f"C1,100n,{c0603},UMK107B7104KA-T,Taiyo Yuden,,,,Device:C\n"
                     f"C2,1uF,{c0603},GRM188R61C105KA93D,Murata,,,no,Device:C\n"
                     f"C3,2.2uF,{c0603},EMK107BJ225KA-T,Taiyo Yuden,,,,Device:C_Small\n"   # the remaining case: an unread
                     f"C4,10n,{c0603},,,,,,Device:C\n")                                    # number, KiCad's chip name
    st = _status(plan)
    assert len(plan) == 4 and all(s == "ready" for s, _ in st.values()), st
    assert all(_fp(i) == "standard:Capacitor_SMD:C_0603_1608Metric" for i in plan.values()), {n: _fp(i) for n, i in plan.items()}
