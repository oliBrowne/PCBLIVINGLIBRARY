"""Open items of the 2026-11-12 deep dive, BOM side:
  * a spreadsheet formula in the part-number cell (=cmd|'/c calc'!A1) became the part's Manufacturer Part Number and
    the part was ready;
  * two lines with no part number, the same value and footprint but different descriptions (a red and a green LED,
    a 50 V X7R and a 16 V X5R capacitor) became one part with no word;
  * a designator on the lines of two parts (R3 = 10k and R3 = 1k) gave no warning in the plan."""
from ecadlib import altium365 as A365, release_check as RC, rules as RU


def L(des, value, fp, mpn="", desc=""):
    return {"designator": des, "value": value, "footprint": fp, "mpn": mpn, "description": desc}


def plan(lines):
    return {i["name"]: i for i in A365.plan(lines, rules=RU.load(None))}


def fails(item):
    return [text for _kind, text in RC.check_item(dict(item, symbol={"ref": "", "file": None},
                                                       footprint={"ref": "", "file": None}), rules=RU.load(None))[0]]


def test_a_formula_in_the_part_number_cell_is_not_a_part_number():
    for cell in ("=cmd|'/c calc'!A1", "+SUM(A1:A2)", "@SUM(1)", "-2+3+cmd|' /C calc'!A0", "=HYPERLINK(\"http://x\")"):
        (item,) = plan([L("R1", "10k", "0603", cell, "RES 10K 0603")]).values()
        f = [x for x in fails(item) if "formula" in x]
        assert f and "Manufacturer Part Number" in f[0], (cell, fails(item))


def test_a_real_part_number_is_not_called_a_formula():
    for mpn in ("RC0603FR-0710KL", "GRM188R71H104KA93D", "1N4148W-7-F", "74HC595", "LM358", "2N7002", "0603WAF1002T5E",
                "MAX3232+", "AD8605ARTZ-REEL7", "BSS138@NXP", "SN74LVC1G08DBVR=", "5-1814832-1", "1-5"):
        (item,) = plan([L("U1", mpn, "SOT-23", mpn, "part")]).values()
        assert not [x for x in fails(item) if "formula" in x], (mpn, fails(item))


def test_lines_with_no_part_number_and_different_descriptions_are_not_one_part_silently():
    for a, b in ((L("D1", "LED", "0603", desc="LED red 0603"), L("D2", "LED", "0603", desc="LED green 0603")),
                 (L("C1", "100n", "0603", desc="CAP 100nF 50V X7R"), L("C2", "100n", "0603", desc="CAP 100nF 16V X5R"))):
        (item,) = plan([a, b]).values()
        h = [x for x in item.get("bom_holds") or [] if "no part number" in x]
        assert h and a["designator"] in h[0] and b["designator"] in h[0], item.get("bom_holds")
        assert any("no part number" in x for x in fails(item))


def test_lines_with_no_part_number_and_one_description_are_one_part():
    for a, b in ((L("R1", "10k", "0603", desc="RES 10K 0603"), L("R2", "10k", "0603", desc="res  10k 0603 ")),
                 (L("R1", "10k", "0603", desc="RES 10K 0603"), L("R2", "10k", "0603")),
                 (L("R1", "10k", "0603"), L("R2", "10k", "0603"))):
        (item,) = plan([a, b]).values()
        assert not item.get("bom_holds"), item.get("bom_holds")


def test_lines_with_one_part_number_may_describe_it_differently():
    (item,) = plan([L("R1", "10k", "0603", "RC0603FR-0710KL", "RES 10K 0603"),
                    L("R2", "10k", "0603", "RC0603FR-0710KL", "Resistor 10k 1%")]).values()
    assert not item.get("bom_holds"), item.get("bom_holds")


def test_a_designator_on_the_lines_of_two_parts_is_said_on_both():
    p = plan([L("R3", "10k", "0603", "RC0603FR-0710KL", "RES 10K"), L("R3, R4", "1k", "0603", "RC0603FR-071KL", "RES 1K"),
              L("R5", "2k", "0603", "RC0603FR-072KL", "RES 2K")])
    said = {n: [x for x in i.get("bom_notes") or [] if "R3" in x and "two parts" in x] for n, i in p.items()}
    assert len(said) == 3 and sum(1 for v in said.values() if v) == 2, said
    assert not said[[n for n in p if "2k" in n][0]]


def test_a_designator_twice_on_one_part_or_on_a_dnp_line_is_not_two_parts():
    p = plan([L("R3", "10k", "0603", "RC0603FR-0710KL", "RES 10K"), L("R3", "10k", "0603", "RC0603FR-0710KL", "RES 10K")])
    assert not any("two parts" in x for i in p.values() for x in i.get("bom_notes") or [])
    p = plan([L("R3", "10k", "0603", "RC0603FR-0710KL", "RES 10K"),
              dict(L("R3", "1k", "0603", "RC0603FR-071KL", "RES 1K"), dnp=True)])
    assert not any("two parts" in x for i in p.values() for x in i.get("bom_notes") or [])
