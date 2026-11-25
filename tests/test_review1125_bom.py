"""Findings of the 2026-11-25 full code review, BOM side. Plain functions, no pytest needed:
python3 -m tests.run_all test_review1125_bom"""
from ecadlib import bom_triage as BT
from tests.fixtures_lite import lite


def _read(tmp_path, text):
    p = tmp_path / "bom.csv"
    p.write_text(text, encoding="utf-8")
    lines, _ = BT.read_bom(p)
    return lines


@lite
def test_a_value_in_the_mpn_column_does_not_hide_a_part_number_in_another_column(tmp_path):
    # review 2026-11-25 F1: 'PART' holds the value, the real part (a tantalum) sits in an unread column. Before #300 the
    # value stayed as the MPN (held); after it the line had no MPN and was released as a plain ceramic chip
    for head, mpn in [("PART,MAN PART NUMBER", "TAJA106K016RNJ"), ("Part,Vendor Part", "T491A106K016AT"),
                      ("PART,Order Code", "TAJA106K016RNJ")]:
        lines = _read(tmp_path, f"Designator,Qty,Value,Footprint,{head},Manufacturer\nC1,1,10uF,1206,10uF,{mpn},AVX\n")
        assert len(lines) == 1 and lines[0].get("bom_hold"), (head, lines)
        assert mpn in lines[0]["bom_hold"], lines[0]["bom_hold"]


@lite
def test_altium_second_mpn_is_used_when_the_first_holds_a_value(tmp_path):
    lines = _read(tmp_path, "Designator,Qty,Value,Footprint,Manufacturer Part Number 1,Manufacturer Part Number 2\n"
                            "C1,1,10uF,1206,10uF,TAJA106K016RNJ\n")
    assert lines[0].get("mpn") == "TAJA106K016RNJ", lines


@lite
def test_a_value_in_the_mpn_column_with_nothing_else_still_reads(tmp_path):
    lines = _read(tmp_path, "Designator,Qty,Value,Footprint,PART,Manufacturer\nC1,1,4.7uF,0603,4.7uF,Murata\n")
    assert not lines[0].get("mpn") and not lines[0].get("bom_hold"), lines   # #300's own case still released
    lines = _read(tmp_path, "Designator,Qty,Value,Footprint,PART,Notes,Stock\nC1,1,4.7uF,0603,4.7uF,keep away from U1,B12\n")
    assert not lines[0].get("bom_hold"), lines

