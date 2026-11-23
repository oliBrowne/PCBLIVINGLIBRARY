"""The land drawing's printed numbers as a second source (ecadlib.land_text), round 2 of the review of 2026-11-23,
finding C3: "pad length and spacing share an x OR a y" was taken for one dimension line, so a read that turned the
pads 90 degrees was "agreed" wherever the drawing prints the gap between the pads and the pad width beside them at
one height (inductors, 0603 chips, SMA diodes alike), and the true read of the same drawing was "unchecked".
Every layout is tried both ways: the true read and the read with the pad's two sizes swapped (centre distance worked
out from the same printed gap). Plain functions: python3 -m tests.run_all test_review1123_round2_land_text"""
from ecadlib import land_text as LT

TITLE = (250.0, 600.0, "Recommended Land Pattern", 8.0, False)


def run(x, y, text, vertical=False, size=7.0):
    return (float(x), float(y), text, size, vertical)


def land(l, w, c, body):
    return {"pin_count": 2, "body_length": body[0], "body_width": body[1],
            "land": {"pad_length": l, "pad_width": w, "pad_center": c / 2}}


def both(runs, true, body):
    """(verdict of the true read, verdict of the read turned 90 degrees) for one page. true: (pad length, pad width,
    gap) in mm; the turned read swaps the two pad sizes and keeps the printed gap."""
    l, w, g = true
    pages = {1: [TITLE] + list(runs)}
    return (LT.check(pages, land(l, w, g + l, body), "XYZ5252-100M"),
            LT.check(pages, land(w, l, g + w, body), "XYZ5252-100M"))


def statuses(runs, true, body):
    a, b = both(runs, true, body)
    return a["status"], b["status"]


# three lands whose turned twin fits the body as well, so only the places of the numbers can tell them apart
IND = ((1.4, 2.2, 3.0), (5.2, 5.0))            # a 5.2 x 5.0 mm inductor: pads 1.4 long, 2.2 wide, 3.0 apart
SMA = ((2.0, 1.5, 2.5), (4.3, 2.6))            # an SMA diode
CHIP = ((0.9, 0.8, 0.7), (1.6, 0.8))           # the review's 0603 numbers


def test_the_lands_used_here_fit_their_body_both_ways():
    # otherwise fits_body, not the layout, would decide and the tests below would prove nothing
    for (l, w, g), body in (IND, SMA):
        assert LT.fits_body((l, w, g + l), (min(body), max(body))) is None
        assert LT.fits_body((w, l, g + w), (min(body), max(body))) is None


# ------------------------------------------------------------------ the turned read is never agreed
def test_gap_and_width_at_one_height_do_not_make_a_dimension_line():
    # the review's layouts A, G, H: the gap printed in the gap, the pad width beside the right pad at the same
    # height, the pad length over the left pad. The turned read saw "its pad length and the gap on one line"
    for (l, w, g), body in (IND, SMA, CHIP):
        runs = [run(259, 560, f"{l}"), run(287, 540, f"{g}"), run(335, 540, f"{w}")]
        a, b = both(runs, (l, w, g), body)
        assert b["status"] == "unchecked", (l, w, g, b)
        assert a["status"] == "unchecked", (l, w, g, a)        # nothing here shows the direction: no verdict


def test_the_same_drawing_with_the_pads_one_over_the_other():
    # layout A turned a quarter: the gap in the gap, the pad width above the upper pad in the same column, the pad
    # length beside the upper pad
    (l, w, g), body = IND
    runs = [run(287, 540, f"{g}"), run(287, 590, f"{w}"), run(330, 566, f"{l}")]
    assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked")


def test_a_width_a_hair_off_the_gaps_line_is_still_on_it():
    # the review's layout F: 2 pt off
    (l, w, g), body = IND
    for dy in (-2, -1, 1, 2):
        runs = [run(259, 560, f"{l}"), run(287, 540, f"{g}"), run(335, 540 + dy, f"{w}")]
        assert statuses(runs, (l, w, g), body)[1] == "unchecked", dy


def test_a_width_over_the_gap_shows_nothing():
    # the review's layout D: the width in the gap's column, the pad length off to the side at another height
    (l, w, g), body = IND
    runs = [run(259, 528, f"{l}"), run(287, 514, f"{g}"), run(287, 560, f"{w}")]
    assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked")


def test_labels_on_leader_lines_show_nothing():
    # no two numbers share a line or a column
    (l, w, g), body = IND
    for runs in ([run(259, 566, f"{l}"), run(287, 514, f"{g}"), run(340, 545, f"{w}")],
                 [run(240, 520, f"{l}"), run(300, 575, f"{g}"), run(338, 538, f"{w}")],
                 [run(262, 571, f"{w}"), run(291, 509, f"{g}"), run(331, 533, f"{l}")]):
        assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked"), runs


def test_all_three_numbers_on_one_line_show_nothing():
    # the review's layout E
    (l, w, g), body = IND
    runs = [run(259, 528, f"{l}"), run(287, 528, f"{g}"), run(335, 528, f"{w}")]
    assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked")


def test_a_span_shares_no_dimension_line_with_a_pad_length():
    # a span's dimension line runs over both pads, so a pad's own length cannot be printed on it: a number that
    # shares the span's line is something else, and the places show nothing
    (l, w, g), body = IND
    runs = [run(259, 528, f"{l}"), run(300, 528, f"{g + 2 * l}"), run(345, 551, f"{w}")]
    pages = {1: [TITLE] + runs}
    assert LT.check(pages, land(l, w, g + l, body))["status"] == "unchecked"
    assert LT.check(pages, land(w, l, g + 2 * l - w, body))["status"] == "unchecked"


# ------------------------------------------------------------------ ruling the turned land out by the body
def test_a_rule_of_thumb_does_not_rule_a_real_land_out():
    # where the places show nothing, the turned land must be no land of the body at all. "A pad is at most half the
    # body long" is a doubt about a read (it holds the part), not a proof: real lands break it
    for pads, body in (((0.9, 0.8, 1.6), (0.8, 1.6)),          # 0603
                       ((1.0, 0.6, 1.9), (1.25, 1.7)),         # SOD-323
                       ((0.4, 0.3, 0.7), (0.3, 0.6)),          # 0201
                       ((2.286, 1.93, 3.353), (4.06, 4.45))):  # a nearly square pad on a nearly square inductor
        assert LT.fits_body(pads, body, sure=True) is None, (pads, body)
    # the body may lie either way under the pads: a land is ruled out only when it fits neither way
    assert LT.fits_body((0.5, 3.0, 1.2), (1.6, 3.2)) is not None            # pads end short of a 3.2 mm body
    assert LT.fits_body((0.5, 3.0, 1.2), (1.6, 3.2), sure=True) is None     # a wide-terminal chip, 1.6 mm along
    # and what no body takes is still ruled out
    assert "touch" in LT.fits_body((3.4, 1.2, 2.0), (3.0, 4.5), sure=True)
    assert "span" in LT.fits_body((3.15, 1.96, 6.1), (2.69, 6.1), sure=True)
    assert "0.8 x" in LT.fits_body((2.6, 1.0, 3.0), (3.0, 3.0), sure=True)


def test_the_turned_0603_is_not_agreed_because_the_true_land_breaks_a_rule_of_thumb():
    # the true 0603 land (0.9 mm pads on a 1.6 mm body) is itself "more than half the body": that held the true
    # read (safe) and, taken as proof against the turned read's twin, let the turned read through
    (l, w, g), body = CHIP
    assert LT.fits_body((l, w, g + l), (min(body), max(body))) is not None
    for runs in ([run(259, 566, f"{l}"), run(287, 514, f"{g}"), run(340, 545, f"{w}")],      # leader lines
                 [run(259, 560, f"{l}"), run(287, 540, f"{g}"), run(335, 540, f"{w}")]):     # gap and width level
        assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked")


# ------------------------------------------------------------------ the true read agrees where the layout shows it
def test_pad_length_and_gap_on_one_dimension_line_with_the_width_beside():
    # "1.4 | 3.0" under the left pad and the gap, the width beside the right pad at the pads' height
    for (l, w, g), body in (IND, SMA):
        runs = [run(259, 528, f"{l}"), run(287, 528, f"{g}"), run(335, 551, f"{w}")]
        assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked"), (l, w, g)
        # the dimension line above the pads, the width beside the left pad
        runs = [run(315, 575, f"{l}"), run(287, 575, f"{g}"), run(238, 551, f"{w}")]
        assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked"), (l, w, g)


def test_the_pad_length_on_both_pads_with_the_width_over_one_of_them():
    # 1.4 | 3.0 | 1.4 on one line, the width above the right pad's own number (TDK CLF's drawing)
    (l, w, g), body = IND
    runs = [run(259, 528, f"{l}"), run(287, 528, f"{g}"), run(315, 528, f"{l}"), run(317, 551, f"{w}")]
    assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked")


def test_pads_one_over_the_other_with_the_dimension_line_beside_them():
    # the review's layout C: pad length and gap in one column right of the pads, the width above the upper pad
    for (l, w, g), body in (IND, SMA):
        runs = [run(340, 560, f"{l}"), run(340, 540, f"{g}"), run(300, 580, f"{w}")]
        assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked"), (l, w, g)


def test_text_turned_with_its_dimension_line():
    # the review's layout B: the width printed turned, pad length and gap level at two heights
    (l, w, g), body = IND
    runs = [run(259, 528, f"{l}"), run(287, 514, f"{g}"), run(240, 545, f"{w}", True)]
    assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked")
    # and the other way: pads one over the other, pad length and gap turned, the width level
    runs = [run(330, 566, f"{l}", True), run(342, 540, f"{g}", True), run(287, 590, f"{w}")]
    assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked")


# ------------------------------------------------------------------ every ordinary placement, both ways
def drawings(true, scales=(6.0, 9.0, 12.0)):
    """Every drawing of the true land made of ordinary label places: pads side by side or one over the other; the
    gap's number in the gap or on a dimension line on either side of the pads; the pad length over or under either
    pad; the pad width beside either pad at the pads' height. -> [(what, runs, the pad length is on the gap's line)]"""
    l, w, g = true
    out = []
    for k in scales:
        off = k * w / 2 + 14                                  # a dimension line this far from the pads' centre line
        for s_at in (0.0, -off, off):
            for l_side in (-1, 1):
                for l_at in (-off, off):
                    for w_side in (-1, 1):
                        pts = [(l_side * k * (g + l) / 2, l_at, f"{l}"), (0.0, s_at, f"{g}"),
                               (w_side * (k * (g / 2 + l) + 12), 0.0, f"{w}")]
                        for stacked in (False, True):
                            runs = [run(300 + (v if stacked else u), 520 + (u if stacked else v), t) for u, v, t in pts]
                            out.append(((k, s_at, l_side, l_at, w_side, stacked), runs, s_at == l_at))
    return out


def test_no_ordinary_drawing_agrees_with_the_turned_read():
    agreed = lines = 0
    for true, body in (IND, SMA, CHIP):
        for what, runs, on_line in drawings(true):
            a, b = both(runs, true, body)
            assert b["status"] == "unchecked", (true, what, b)
            if true is not CHIP[0]:
                lines += on_line
                agreed += a["status"] == "agreed"
                # the true read agrees exactly where pad length and gap are on one dimension line
                assert (a["status"] == "agreed") == on_line, (true, what, a)
    assert agreed == lines and agreed >= 48, (agreed, lines)


def test_the_places_never_say_kept_both_ways():
    # whatever the three places are, the read and its turned twin cannot both be shown by them
    spots = [(x, y) for x in (250, 252, 275, 300, 340) for y in (500, 502, 520, 560)]
    for vl in (False, True):
        for vw in (False, True):
            for pl in spots:
                for ps in spots:
                    for pw in spots:
                        L, S, W = (1.4, "1.4", *pl, vl), (3.0, "3.0", *ps, False), (2.2, "2.2", *pw, vw)
                        fwd = LT._layout(L, S, W, (1.4, 2.2, 4.4), (2.2, 1.4, 5.2), "gap")
                        back = LT._layout(W, S, L, (2.2, 1.4, 5.2), (1.4, 2.2, 4.4), "gap")
                        assert not (fwd == "kept" and back == "kept"), (L, S, W)
                        assert (fwd == "kept") == (back == "turned"), (L, S, W, fwd, back)
