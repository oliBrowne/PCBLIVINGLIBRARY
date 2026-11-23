"""A maker's two-pad land read from a datasheet, checked against the numbers the PDF prints in its land drawing
(ecadlib.land_text, 2026-11-19). Every wrong read below must stay "unchecked": only "agreed" lets a part release with
no person's look. Plain functions: python3 -m tests.run_all test_land_text_1119"""
import tempfile

from ecadlib import crosscheck as CC, land_text as LT

TITLE = (400.0, 330.0, "Typical Pad Layout", 7.0, False)


def run(x, y, text, vertical=False):
    return (float(x), float(y), text, 7.0, vertical)


def vishay(*extra):
    """IHLP-1616's pad layout as the PDF places it: pad length 1.930 and the span 5.639 under the pads, the pad width
    2.286 beside them, each in inches with the mm in brackets below."""
    return {1: [TITLE, run(420, 309, "0.222"), run(418, 302, "[5.639]"), run(380, 287, "0.090"),
                run(378, 280, "[2.286]"), run(420, 265, "0.076"), run(418, 258, "[1.930]")] + list(extra)}


def land(l, w, c, body=(4.45, 4.06)):
    return {"pin_count": 2, "body_length": body[0], "body_width": body[1],
            "land": {"pad_length": l, "pad_width": w, "pad_center": c / 2}}


def test_a_land_whose_sizes_the_drawing_prints_agrees():
    # 2026-11-23 (review round 2): Vishay's own drawing no longer agrees. Span, pad width and pad length are printed
    # one under the other with nothing to show which pad size runs along the pads, and the nearly square pad turned
    # (2.286 mm along a 4.45 mm body) was ruled out only by "a pad is at most half the body long", which real lands
    # break (a 0603's 0.9 mm pads on 1.6 mm): a turned read of such a land would have agreed the same way
    v = LT.check(vishay(), land(1.93, 2.286, 3.709), "IHLP1616BZER2R2M11")
    assert v["status"] == "unchecked" and "turned" in v["why"], v
    # the same drawing with the pad width printed turned, as a drawing that turns its text with the dimension line
    # prints it. 2026-11-23 (review round 3): this agreed ("the places show which size is which"). They do not: a
    # drawing that turns the pad-length label alone, to fit it in, gives the turned read the same three directions
    pages = {1: [r if r[2] not in ("0.090", "[2.286]") else r[:4] + (True,) for r in vishay()[1]]}
    v = LT.check(pages, land(1.93, 2.286, 3.709), "IHLP1616BZER2R2M11")
    assert v["status"] == "unchecked" and "turned" in v["why"], v


def test_wrong_reads_of_the_same_drawing_are_never_agreed():
    pages = vishay()
    for l, w, c in [(2.286, 1.93, 3.709),          # turned
                    (1.93, 2.286, 7.418),          # pad_center given as the whole centre distance
                    (1.93, 2.286, 5.639),          # the span taken for the centre distance
                    (1.93 * 1.1, 2.286 * 1.1, 3.709 * 1.1),
                    (1.93, 2.0, 3.709),            # a width the drawing does not print
                    (0.076, 0.09, 0.146)]:         # the inch numbers read as mm
        v = LT.check(pages, land(l, w, c), "IHLP1616BZER2R2M11")
        assert v["status"] == "unchecked", ((l, w, c), v)


def test_a_body_number_near_the_land_is_no_land_size():
    # '2.0 Max.' (a height) and '3.18 ± 0.3' (a body size) are printed beside the land: a read that took either for
    # the pad width finds no width of its own
    pages = vishay(run(380, 270, "[2.0] Max."), run(372, 294, "[3.18 ± 0.3]"))
    assert LT.check(pages, land(1.93, 2.0, 3.709), "x")["status"] == "unchecked"
    assert LT.check(pages, land(1.93, 3.18, 3.709), "x")["status"] == "unchecked"
    # the true read finds all three of its numbers; since round 2 of the review of 2026-11-23 this drawing does not
    # show which way the pads lie (see the first test), so its verdict is the turned doubt, not a missing number
    v = LT.check(pages, land(1.93, 2.286, 3.709), "x")
    assert v["status"] == "unchecked" and "turned" in v["why"] and "not printed" not in v["why"], v


def test_the_land_must_fit_the_body_the_read_gives():
    assert LT.check(vishay(), land(1.93, 2.286, 3.709, body=(9.0, 9.0)), "x")["status"] == "unchecked"
    v = LT.check(vishay(), {"pin_count": 2, "land": {"pad_length": 1.93, "pad_width": 2.286, "pad_center": 1.8545}}, "x")
    assert v["status"] == "unchecked" and "body size" in v["why"], v
    assert "touch" in LT.fits_body((2.0, 2.0, 1.9), (6.9, 7.2))
    assert "short" in LT.fits_body((2.13, 3.81, 6.37), (3.81, 10.1))      # the gap read as the pad length
    assert "outside" in LT.fits_body((1.78, 3.56, 11.18), (10.0, 10.0))    # the span read as the gap
    assert LT.fits_body((1.78, 3.56, 7.62), (10.0, 10.0)) is None


def test_a_size_labelled_on_both_pads_is_the_pad_length():
    # CLF7045: 2.0 | 3.9 | 2.0 under the pads, 2.2 beside: 3.9 between the two 2.0 is the gap, never the pad length
    pages = {1: [(330.0, 500.0, "Recommended Land Pattern (Unit : mm)", 10.0, False), run(363, 450, "2.0"),
                 run(418, 450, "3.9"), run(474, 449, "2.0"), run(476, 473, "2.2")]}
    assert LT.check(pages, land(2.0, 2.2, 5.9, body=(7.2, 6.9)), "CLF7045T-100M-H")["status"] == "agreed"
    assert LT.check(pages, land(3.9, 2.2, 5.9, body=(7.2, 6.9)), "CLF7045T-100M-H")["status"] == "unchecked"


def test_a_width_equal_to_a_size_along_the_line_is_not_trusted():
    # a drawing that labels the pad length on both pads prints that number twice: one of them is no pad width
    pages = {1: [(330.0, 500.0, "Recommended Land Pattern", 10.0, False), run(470, 430, "2.0", True),
                 run(470, 462, "4.0", True), run(470, 496, "2.0", True), run(420, 520, "1.7")]}
    assert LT.check(pages, land(2.0, 1.7, 6.0, body=(7.3, 7.3)), "x")["status"] == "agreed"
    v = LT.check(pages, land(2.0, 2.0, 6.0, body=(7.3, 7.3)), "x")
    assert v["status"] == "unchecked", v


def test_no_verdict_without_a_land_drawing_or_with_two():
    other = {1: [(400.0, 330.0, "Package Outline", 7.0, False)] + vishay()[1][1:]}
    assert LT.check(other, land(1.93, 2.286, 3.709), "x")["status"] == "unchecked"
    two = {1: vishay()[1], 2: [TITLE, run(420, 309, "8.255"), run(380, 287, "3.429"), run(420, 265, "2.413")]}
    v = LT.check(two, land(1.93, 2.286, 3.709), "x")
    assert v["status"] == "unchecked" and "2 land drawings" in v["why"], v


def test_a_table_of_sizes_agrees_only_on_the_row_that_names_the_part():
    rows = [("SRX4018", "1.2", "3.7", "1.6"), ("SRX5020", "1.5", "4.0", "2.1"), ("SRX6028", "2.0", "5.2", "2.6")]
    runs = [(300.0, 400.0, "Recommended Land Pattern", 8.0, False)]
    for i, (name, a, b, g) in enumerate(rows):
        y = 380 - 12 * i
        runs += [run(300, y, name), run(340, y, a), run(360, y, b), run(380, y, g)]
    spec = land(1.5, 4.0, 3.6, body=(5.0, 5.0))
    assert LT.check({1: runs}, spec, "SRX5020T-4R7")["status"] == "agreed"
    assert LT.check({1: runs}, spec, "SRX6028T-4R7")["status"] == "unchecked"   # another size's row


def test_numbers_as_printed():
    n = lambda text: [t for _, t, *_ in LT.numbers((0.0, 0.0, text, 7.0, False))]
    # a number printed with its tolerance is a body size, never a land size: it is left out
    assert n("6,36 ±0,2") == [] and n("0.8 +0.2/-0.3") == [] and n("R 1,3") == [] and n("4.04") == ["4.04"]
    assert n("4,04") == ["4.04"]
    assert n("[2.286]") == ["2.286"] and n("(.146)") == [".146"] and n("±0.05") == []
    assert LT.two_pads({"pin_count": 2, "land": {"pad_length": 1.2, "pad_width": 3.7, "pad_center": 1.4}}) == (1.2, 3.7, 2.8)
    table = {"unit": "mm", "pads": [{"number": "1", "x": -4.75, "y": 0, "w": 5.6, "h": 2.1},
                                    {"number": "2", "x": 4.75, "y": 0, "w": 5.6, "h": 2.1}]}
    assert LT.two_pads({"pad_table": table}) == (5.6, 2.1, 9.5)
    assert LT.two_pads({"pin_count": 3, "land": {"pad_length": 1, "pad_width": 1, "pad_center": 1}}) is None


# ---------------------------------------------------------------- the cross-check
class _Reader:
    cfg = {}
    effort = "medium"

    def __init__(self):
        self.out = tempfile.mkdtemp()


def test_an_agreeing_land_is_the_cross_checks_independent_source():
    real = LT.check_pdf
    LT.check_pdf = lambda data, spec, part=None: {"status": "agreed", "page": 3,
                                                  "printed": {"pad length": "1.930", "span": "5.639", "pad width": "2.286"}}
    try:
        res = {"spec": dict(land(1.93, 2.286, 3.709), name="IHLP-1616BZ-11", package_type="molded"), "symbol": {}}
        rec = CC.run(_Reader(), {"data": b"%PDF", "part": "IHLP1616BZER2R2M11", "fp_needed": True}, res, "m")
    finally:
        LT.check_pdf = real
    assert rec["status"] == "agreed", rec
    assert [c["source"] for c in rec["checks"]] == ["pdf land text"], rec      # no paid second read was asked for
    assert "PDF page 3" in rec["checks"][0]["against"], rec
    assert "agrees" in CC.summary_line(rec), CC.summary_line(rec)


def test_a_land_the_text_does_not_confirm_keeps_its_hold():
    rec = CC.land_printed({"data": None, "part": "x"}, {"spec": land(1.93, 2.286, 3.709)})
    assert rec is None                                 # no PDF: nothing to compare
    real = LT.check_pdf
    LT.check_pdf = lambda data, spec, part=None: {"status": "unchecked", "why": "pad width not printed"}
    try:
        rec = CC.land_printed({"data": b"%PDF", "part": "x"}, {"spec": land(1.93, 2.286, 3.709)})
    finally:
        LT.check_pdf = real
    assert rec["status"] == "unchecked" and rec["why"] == "pad width not printed", rec


def test_an_agreeing_land_does_not_vouch_for_which_pad_is_which():
    # a read that placed its pins on sides (a diode's cathode, a tantalum's +) needs a check of those sides: the land
    # text agreeing checks only the pads' sizes, so the part is not "agreed" on it alone
    real_lt, real_sides, real_second = LT.check_pdf, CC.sides, CC.second_read
    LT.check_pdf = lambda data, spec, part=None: {"status": "agreed", "page": 1,
                                                  "printed": {"pad length": "1.930", "span": "5.639", "pad width": "2.286"}}
    CC.sides = lambda spec: [("1", "left"), ("2", "right")]
    CC.second_read = lambda *a: {"source": "second read", "status": "unchecked", "why": "no key", "compared": []}
    try:
        res = {"spec": dict(land(1.93, 2.286, 3.709), name="x", package_type="molded"), "symbol": {}}
        rec = CC.run(_Reader(), {"data": b"%PDF", "part": "x", "fp_needed": True}, res, "m")
    finally:
        LT.check_pdf, CC.sides, CC.second_read = real_lt, real_sides, real_second
    assert rec["status"] != "agreed", rec
