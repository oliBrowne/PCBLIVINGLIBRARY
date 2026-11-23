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
    # a 5.2 x 5.0 mm inductor, true land 1.4 x 2.2 with a 3.0 gap
    assert status(GAP, land(1.4, 2.2, 4.4, (5.2, 5.0))) == "agreed"
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
    # the same stacked span, on a 7.0 x 3.0 body: turned, the pad (2.2 mm) would be longer than the body allows... the
    # turned land (pad 2.6 long on a 4.5 body) is no land of this body, so the read as given is the only one
    pages = {1: [TITLE, run(259, 528, "1.2"), run(287, 514, "5.4"), run(335, 551, "2.6")]}
    assert LT.fits_body((2.6, 1.2, 2.8), (3.0, 4.5)) is not None
    assert status(pages, land(1.2, 2.6, 4.2, (4.5, 3.0))) == "agreed"


def test_the_texts_direction_shows_which_size_runs_along_the_pads():
    # pad length and gap printed turned (along a vertical dimension line), the width printed level: only the read that
    # takes the two turned numbers along the pads' line agrees
    pages = {1: [TITLE, run(300, 520, "1.4", True), run(310, 548, "3.0", True), run(270, 590, "2.2")]}
    assert status(pages, land(1.4, 2.2, 4.4, (5.2, 5.0))) == "agreed"
    assert status(pages, land(2.2, 1.4, 5.2, (5.2, 5.0))) == "unchecked"
