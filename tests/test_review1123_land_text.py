"""The land drawing's printed numbers as a second source (ecadlib.land_text), review of 2026-11-23, findings 1.5 and
1.6: a read that turned the pads 90 degrees, and a read that took another size's row of a family table, were both
"agreed" and released with no person's look. Plain functions: python3 -m tests.run_all test_review1123_land_text"""
from ecadlib import land_text as LT


def run(x, y, text, vertical=False, size=7.0):
    return (float(x), float(y), text, size, vertical)


def land(l, w, c, body):
    return {"pin_count": 2, "body_length": body[0], "body_width": body[1],
            "land": {"pad_length": l, "pad_width": w, "pad_center": c / 2}}


def status(pages, spec, part="XYZ5252-100M"):
    return LT.check(pages, spec, part)["status"]


# ------------------------------------------------------------------------------- 1.5 the pads turned 90 degrees
CLF = {1: [(330.0, 500.0, "Recommended Land Pattern (Unit : mm)", 10.0, False), run(363, 450, "2.0"),
           run(418, 450, "3.9"), run(474, 449, "2.0"), run(476, 473, "2.2")]}
TITLE = (250.0, 600.0, "Recommended Land Pattern", 8.0, False)
# two pads left and right: 1.4 under the left pad and 3.0 under the gap (one dimension line), 2.2 beside the right pad
GAP = {1: [TITLE, run(259, 528, "1.4"), run(287, 528, "3.0"), run(335, 551, "2.2")]}
# the span on a dimension line of its own, below: nothing in the three places shows which way the pads lie
SPAN = {1: [TITLE, run(259, 528, "1.4"), run(287, 514, "6.0"), run(335, 551, "2.2")]}


def test_a_turned_read_made_of_the_printed_gap_is_not_agreed():
    # the repo's own fixture (TDK CLF7045): the read swapped the pad's sizes and worked the centres out from the gap
    assert status(CLF, land(2.0, 2.2, 5.9, (7.2, 6.9)), "CLF7045T-100M-H") == "agreed"
    v = LT.check(CLF, land(2.2, 2.0, 6.1, (7.2, 6.9)), "CLF7045T-100M-H")
    assert v["status"] == "unchecked" and "turned" in v["why"], v
    # a 5.2 x 5.0 mm inductor, true land 1.4 x 2.2 with a 3.0 gap. 2026-11-23 (review round 3): the true read agreed
    # here because pad length and gap share a line. One pad length on the gap's line shows nothing (the pad width
    # beside the pads at the gap's height looks the same to the turned read); TDK's drawing above prints the pad
    # length on both pads, which does
    assert status(GAP, land(1.4, 2.2, 4.4, (5.2, 5.0))) == "unchecked"
    both_pads = {1: GAP[1] + [run(315, 528, "1.4")]}
    assert status(both_pads, land(1.4, 2.2, 4.4, (5.2, 5.0))) == "agreed"
    assert status(both_pads, land(2.2, 1.4, 5.2, (5.2, 5.0))) == "unchecked"
    v = LT.check(GAP, land(2.2, 1.4, 5.2, (5.2, 5.0)))
    assert v["status"] == "unchecked" and "turned" in v["why"], v


def test_a_turned_read_made_of_the_printed_span_is_not_agreed():
    v = LT.check(SPAN, land(2.2, 1.4, 3.8, (5.2, 5.0)))
    assert v["status"] == "unchecked" and "turned" in v["why"], v
    # the layout does not show the direction and the turned land fits the body as well: no verdict for the true read
    # either (a second read is asked for), which is the safe side
    v = LT.check(SPAN, land(1.4, 2.2, 4.6, (5.2, 5.0)))
    assert v["status"] == "unchecked" and "turned" in v["why"], v


def test_a_land_the_body_allows_one_way_only_still_agrees():
    # the same stacked span, on a 4.5 x 3.0 body: turned, the two pads (3.4 mm long, 5.4 mm over both) would overlap,
    # so the read as given is the only one
    pages = {1: [TITLE, run(259, 528, "1.2"), run(287, 514, "5.4"), run(335, 551, "3.4")]}
    assert LT.fits_body((3.4, 1.2, 2.0), (3.0, 4.5), sure=True) is not None
    assert status(pages, land(1.2, 3.4, 4.2, (4.5, 3.0))) == "agreed"
    # round 2 of the review: this test's first land (pads 2.6 mm wide) agreed because, turned, a pad 2.6 mm long is
    # more than half the 4.5 mm body. That rule of thumb turns real lands away (a 0603's pads are 0.9 mm long on
    # 1.6 mm, an SOD-323's 1.0 on 1.7), so it cannot rule the turned land out: no verdict
    pages = {1: [TITLE, run(259, 528, "1.2"), run(287, 514, "5.4"), run(335, 551, "2.6")]}
    assert LT.fits_body((2.6, 1.2, 2.8), (3.0, 4.5)) is not None
    assert LT.fits_body((2.6, 1.2, 2.8), (3.0, 4.5), sure=True) is None
    v = LT.check(pages, land(1.2, 2.6, 4.2, (4.5, 3.0)))
    assert v["status"] == "unchecked" and "turned" in v["why"], v


def test_the_texts_direction_does_not_show_which_size_runs_along_the_pads():
    # pad length and gap printed turned (along a vertical dimension line), the width printed level. 2026-11-23 (review
    # round 3): the true read agreed here. The direction of three labels shows nothing: a drawing that turns the
    # pad-length label alone gives the turned read "its pad length and the gap level, its width turned"
    pages = {1: [TITLE, run(300, 520, "1.4", True), run(310, 548, "3.0", True), run(270, 590, "2.2")]}
    assert status(pages, land(1.4, 2.2, 4.4, (5.2, 5.0))) == "unchecked"
    assert status(pages, land(2.2, 1.4, 5.2, (5.2, 5.0))) == "unchecked"
    pages = {1: [TITLE, run(269.4, 558.8, "1.4", True), run(287, 540, "3.0"), run(320.2, 540, "2.2")]}
    assert status(pages, land(1.4, 2.2, 4.4, (5.2, 5.0))) == "unchecked"
    assert status(pages, land(2.2, 1.4, 5.2, (5.2, 5.0))) == "unchecked"


# ------------------------------------------------------------------------------- 1.6 another size's row
def table(rows, pair_last=False):
    runs = [TITLE, run(262, 550, "A"), run(290, 550, "C"), run(335, 571, "B"), run(240, 530, "Type A B C")]
    for i, (name, a, b, g) in enumerate(rows):
        y = 518 - 11 * i
        runs += [run(240, y, name), run(288, y, a), run(313, y, b)] + ([run(338, y, g)] if g else [])
    return {1: runs}


TWO = [("SRX4018", "1.2", "3.7", "1.6"), ("SRX4030", "1.6", "3.3", "1.3")]
BODY = (4.0, 4.0)


def test_a_table_of_two_sizes_agrees_only_on_the_row_that_names_the_part():
    assert status(table(TWO), land(1.2, 3.7, 2.8, BODY), "SRX4018T-100M") == "agreed"
    v = LT.check(table(TWO), land(1.6, 3.3, 2.9, BODY), "SRX4018T-100M")       # SRX4030's row
    assert v["status"] == "unchecked" and "table" in v["why"], v
    assert status(table(TWO), land(1.6, 3.3, 2.9, BODY), "SRX4030T-100M") == "agreed"


def test_a_row_short_of_a_number_is_still_a_row_of_the_table():
    # the third row's last number is not read as one (an image, a dash, a broken run): the table is still a table
    rows = TWO + [("SRX4045", "1.9", "3.0", "")]
    v = LT.check(table(rows), land(1.6, 3.3, 2.9, BODY), "SRX4018T-100M")
    assert v["status"] == "unchecked" and "table" in v["why"], v
    # two short rows under a drawing's own three numbers: more aligned numbers than one land needs
    pages = {1: [TITLE, run(288, 528, "1.6"), run(313, 528, "3.3"), run(338, 528, "1.3"),
                 run(288, 517, "1.2"), run(313, 517, "3.7"), run(288, 506, "1.9"), run(313, 506, "3.0")]}
    assert status(pages, land(1.6, 3.3, 2.9, BODY), "SRX4018T-100M") == "unchecked"


def test_a_drawings_own_labels_are_no_table():
    # 2.0 | 3.9 | 2.0 on one dimension line with one more number below it is a drawing, not a table
    pages = {1: CLF[1] + [run(418, 436, "7.9")]}
    assert status(pages, land(2.0, 2.2, 5.9, (7.2, 6.9)), "CLF7045T-100M-H") == "agreed"


def chars(text, x, y, size=8.0):
    """Glyph boxes as pdfium gives them for Helvetica: a digit about 0.5 x 0.7 of the size, a point 0.1 x 0.1 of it,
    0.15 of the size between glyphs."""
    out = []
    for ch in text:
        w, h = (0.1 * size, 0.1 * size) if ch in ".," else (0.0, 0.0) if ch == " " else (0.5 * size, 0.7 * size)
        out.append((ch, x, y, x + w, y + h, 0.0))
        x += w + (0.19 * size if ch != " " else 0.0)
    return out


def test_a_number_is_not_broken_after_its_decimal_point():
    # 2026-11-23 (review): the gap after a point was measured against the point's own height (about 1 pt), so '1.1'
    # became '1.' and '1' and its table row dropped out of the table
    text = lambda cs: [r[2] for r in LT._runs_of(cs)]
    assert text(chars("1.1", 338, 496)) == ["1.1"]
    assert text(chars(".5", 338, 496)) == [".5"]
    assert text(chars("3.0", 313, 496) + chars("1.1", 338, 496)) == ["3.0", "1.1"]      # columns stay apart
    assert text(chars("Type A", 240, 530)) == ["Type A"]


def test_the_table_rule_holds_for_a_row_that_prints_1_1():
    rows = TWO + [("SRX4045", "1.9", "3.0", "1.1")]
    cs = chars("Recommended Land Pattern", 250, 600)
    for i, row in enumerate(rows):
        for x, cell in zip((240, 288, 313, 338), row):
            cs += chars(cell, x, 518 - 11 * i)
    runs = LT._runs_of(cs)
    assert "1.1" in [r[2] for r in runs], [r[2] for r in runs]
    region = LT.regions(runs)[0][1]
    assert len(LT._lines(region)) == 3, LT._lines(region)
    v = LT.check({1: runs}, land(1.6, 3.3, 2.9, BODY), "SRX4018T-100M")
    assert v["status"] == "unchecked" and "table" in v["why"], v
    assert status({1: runs}, land(1.2, 3.7, 2.8, BODY), "SRX4018T-100M") == "agreed"
