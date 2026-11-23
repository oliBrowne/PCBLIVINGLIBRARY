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
