"""Pinouts confirmed from more of the datasheet's own text, free and with no model (PC read sample 2026-11-17: 13 of
17 assistant reads were built but held, because nothing independent confirmed their pins).

1. A pinout DRAWING ("Figure 2. 8-pin package connections, top view"): its text layer comes out scrambled (M24C02:
   "VSS / WCE1 / E0 VCC / E2 / 1 2 3 4 8 7 6 5"), but each pin number sits beside its name. pin_text.figure_lines pairs
   them by position (a column of numbers 1..K, each with the one name on its line), and check() reads each drawing as
   a table of its own, so two drawings of two packages on one page are compared like two tables.
2. A table split by package (TL074: page 7 "TL074x D ... 14-Pin SOIC", page 8 "TL074M FK, 20-Pin LCCC"): a table on
   another page that prints a pin number this part does not have (20 on a 14-pin part) is another package's, not a
   doubt about this one. A table of the same size or smaller still keeps the part with a person."""
from ecadlib import pin_text as PT

# M24C02-WMN6TP (ST DS9398 p2, Figure 2) as pypdf places it: (x, y, text, size); "V" + "SS" is a subscript
M24 = [(270.1, 158.2, "V", 8.0), (275.4, 158.0, "SS", 5.2), (356.2, 189.5, "WC", 8.0), (273.4, 189.5, "E1", 8.0),
       (271.0, 205.2, "E0", 8.0), (356.2, 205.2, "V", 8.0), (361.6, 205.0, "CC", 5.2), (272.2, 173.9, "E2", 8.0),
       (297.5, 205.2, "1", 8.0), (297.5, 189.5, "2", 8.0), (297.5, 173.9, "3", 8.0), (297.5, 158.2, "4", 8.0),
       (338.4, 205.2, "8", 8.0), (338.4, 189.5, "7", 8.0), (338.4, 173.9, "6", 8.0), (338.4, 158.2, "5", 8.0),
       (354.3, 172.8, "SCL", 8.0), (354.3, 155.9, "SDA", 8.0), (306.5, 525.0, "M24xxx", 8.0)]
M24_PINS = [("1", "E0"), ("2", "E1"), ("3", "E2"), ("4", "VSS"), ("5", "SDA"), ("6", "SCL"), ("7", "WC"), ("8", "VCC")]
# Si4401BDY (Vishay 73140 p1): SO-8, sources on 1-3, gate 4, drain 5-8
SI = [(75.6, 529.5, "S", 7.0), (75.6, 515.1, "S", 7.0), (180.3, 499.1, "D", 7.0), (180.3, 528.1, "D", 7.0),
      (180.3, 515.1, "D", 7.0), (75.6, 500.7, "S", 7.0), (74.7, 486.3, "G", 7.0), (180.3, 486.3, "D", 7.0),
      (121.0, 552.3, "SO-8", 8.0), (163.0, 485.1, "5", 7.0), (163.0, 499.5, "6", 7.0), (163.0, 513.9, "7", 7.0),
      (163.0, 528.3, "8", 7.0), (93.9, 513.9, "2", 7.0), (93.9, 499.5, "3", 7.0), (93.9, 485.1, "4", 7.0),
      (93.9, 528.3, "1", 7.0)]
SI_PINS = [("1", "S"), ("2", "S"), ("3", "S"), ("4", "G"), ("5", "D"), ("6", "D"), ("7", "D"), ("8", "D")]


def test_a_pinout_drawing_pairs_each_number_with_the_name_beside_it():
    (lines,) = PT.figure_lines(M24)
    assert sorted(lines.splitlines()) == sorted(f"{nm} {n}" for n, nm in M24_PINS), lines
    r = PT.check({2: "scrambled text layer\nVSS\nWCE1\nE0 VCC"}, M24_PINS, figures=[(2, lines)])
    assert r["status"] == "agreed" and r["page"] == 2, r
    (lines,) = PT.figure_lines(SI)
    assert PT.check({1: ""}, SI_PINS, figures=[(1, lines)])["status"] == "agreed"


def test_a_drawing_never_agrees_with_swapped_pins():
    (lines,) = PT.figure_lines(M24)
    swapped = [(n, {"SDA": "SCL", "SCL": "SDA"}.get(nm, nm)) for n, nm in M24_PINS]
    assert PT.check({}, swapped, figures=[(2, lines)])["status"] != "agreed"


def test_two_drawings_of_two_packages_on_one_page_keep_the_part_with_a_person():
    """The same names numbered otherwise by a second drawing (SOIC beside SOT-23, MLF beside SOT): which is this
    part's is not in the text."""
    other = [(x + 300, y, {"E0": "E2", "E2": "E0"}.get(t, t), s) for x, y, t, s in M24]
    figs = PT.figure_lines(M24 + other)
    assert len(figs) == 2, figs
    assert PT.check({}, M24_PINS, figures=[(2, f) for f in figs])["status"] == "unchecked"


def test_a_name_between_two_numbers_is_not_guessed():
    """A name as near to the number on its left as to the one on its right is paired with neither."""
    runs = [(100.0, 100.0, "1", 8.0), (100.0, 90.0, "2", 8.0), (140.0, 100.0, "4", 8.0), (140.0, 90.0, "3", 8.0),
            (118.0, 100.0, "IN", 8.0), (80.0, 90.0, "GND", 8.0), (155.0, 90.0, "OUT", 8.0), (150.0, 100.0, "EN", 8.0)]
    (lines,) = PT.figure_lines(runs)
    assert "IN" not in lines.split() and "GND 2" in lines and "EN 4" in lines, lines


def test_no_drawing_without_a_whole_run_of_numbers():
    assert PT.figure_lines([(10.0, 10.0, "1", 8.0), (30.0, 10.0, "VCC", 8.0)]) == []
    gap = [r for r in M24 if r[2] != "7"]                    # 1..8 with 7 missing: not a pinout
    assert PT.figure_lines(gap) == []


TL071 = """Table 4-6. Pin Functions: TL074x
NAME NO.
1IN– 2 Input Inverting input
1IN+ 3 Input Noninverting input
1OUT 1 Output Output
2IN– 6 Input Inverting input
2IN+ 5 Input Noninverting input
2OUT 7 Output Output
3IN– 9 Input Inverting input
3IN+ 10 Input Noninverting input
3OUT 8 Output Output
4IN– 13 Input Inverting input
4IN+ 12 Input Noninverting input
4OUT 14 Output Output
VCC– 11 — Power supply
VCC+ 4 — Power supply"""
LCCC = """Table 4-7. Pin Functions: TL074M
NAME NO.
1IN– 3 Input Inverting input
1IN+ 4 Input Noninverting input
1OUT 2 Output Output
2IN– 9 Input Inverting input
2IN+ 8 Input Noninverting input
2OUT 10 Output Output
3IN– 13 Input Inverting input
3IN+ 14 Input Noninverting input
3OUT 12 Output Output
4IN– 19 Input Inverting input
4IN+ 18 Input Noninverting input
4OUT 20 Output Output
NC 1, 5, 7, 11, 15, 17 — Do not connect
VCC– 16 — Power supply
VCC+ 6 — Power supply"""
TL074 = [("1", "1OUT"), ("2", "1IN-"), ("3", "1IN+"), ("4", "VCC+"), ("5", "2IN+"), ("6", "2IN-"), ("7", "2OUT"),
         ("8", "3OUT"), ("9", "3IN-"), ("10", "3IN+"), ("11", "VCC-"), ("12", "4IN+"), ("13", "4IN-"), ("14", "4OUT")]


def test_a_bigger_packages_table_on_another_page_is_not_a_doubt():
    r = PT.check({7: TL071, 8: LCCC}, TL074)
    assert r["status"] == "agreed" and r["page"] == 7, r


def test_a_smaller_or_same_size_table_still_is():
    """Read as the 20-pin LCCC, the 14-pin table on page 7 could be this part's: still held. And a variant of the
    same size (MCP6L01 / MCP6L01R, tests.test_release_auto) is still unchecked."""
    lccc = [("2", "1OUT"), ("3", "1IN-"), ("4", "1IN+"), ("6", "VCC+"), ("8", "2IN+"), ("9", "2IN-"), ("10", "2OUT"),
            ("12", "3OUT"), ("13", "3IN-"), ("14", "3IN+"), ("16", "VCC-"), ("18", "4IN+"), ("19", "4IN-"),
            ("20", "4OUT"), ("1", "NC")]
    assert PT.check({8: LCCC}, lccc)["status"] == "agreed"
    assert PT.check({7: TL071, 8: LCCC}, lccc)["status"] == "unchecked"
