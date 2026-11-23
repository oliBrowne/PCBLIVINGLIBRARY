"""The land drawing's printed numbers as a second source (ecadlib.land_text), round 3 of the review of 2026-11-23,
finding A6: two rounds of rules about where three bare numbers stand were each beaten by another label layout (the
pad-length label pushed outside its pad; the pad-length label alone printed turned), and a read that turned the pads
90 degrees came out "agreed". The places and directions of three bare numbers cannot rule the turned land out: any
rule that reads them says the same of the turned read in the mirror placement. "Pad length + gap" and "pad length +
span" now count only with evidence that is not a question of layout: the pad length printed on both pads either side
of the gap on the gap's line, or the body ruling the turned land out (fits_body(sure=True)); gap + span and the
centre-distance routes never had the question. Everything else: no verdict.
Every drawing is tried both ways: the true read and the read with the pad's two sizes swapped (centre distance worked
out from the same printed gap). Plain functions: python3 -m tests.run_all test_review1123_round3_land_text"""
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


# three lands whose turned twin is a possible land of the same body, so the body cannot tell them apart
IND = ((1.4, 2.2, 3.0), (5.2, 5.0))            # a 5.2 x 5.0 mm inductor: pads 1.4 long, 2.2 wide, 3.0 apart
SMA = ((2.0, 1.5, 2.5), (4.3, 2.6))            # an SMA diode
CHIP = ((0.9, 0.8, 0.7), (1.6, 0.8))           # the review's 0603 numbers
LANDS = (IND, SMA, CHIP)


def test_the_body_does_not_rule_the_turned_twin_of_these_lands_out():
    # otherwise the body, not the drawing, would decide and the tests below would prove nothing
    for (l, w, g), body in LANDS:
        assert LT.fits_body((w, l, g + w), body, sure=True) is None, (l, w, g)


# ------------------------------------------------------------------ the review's two layouts
def test_the_pad_length_label_pushed_outside_its_pad():
    # A6 (a): the gap in the gap, the pad width beside the pads at the same height, the pad-length label outside the
    # pad (usual for a short dimension). The turned read was agreed, the true one unchecked
    runs = [run(249.8, 558.8, "1.4"), run(287, 540, "3.0"), run(320.2, 540, "2.2")]
    assert statuses(runs, *IND) == ("unchecked", "unchecked")
    for (l, w, g), body in LANDS:
        for lx, ly in ((230, 560), (222, 566), (215, 515), (249.8, 558.8)):
            for wx in (320.2, 326, 330, 332):
                runs = [run(lx, ly, f"{l}"), run(287, 540, f"{g}"), run(wx, 540, f"{w}")]
                assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked"), (l, w, g, lx, ly, wx)


def test_the_pad_length_label_alone_printed_turned():
    # A6 (b): the pad-length label a vertical run, gap and width level, wherever they stand
    runs = [run(269.4, 558.8, "1.4", True), run(287, 540, "3.0"), run(320.2, 540, "2.2")]
    assert statuses(runs, *IND) == ("unchecked", "unchecked")
    for (l, w, g), body in LANDS:
        for wx, wy in ((335, 520), (335, 540), (320.2, 540), (240, 551), (300, 580)):
            runs = [run(259, 560, f"{l}", True), run(287, 540, f"{g}"), run(wx, wy, f"{w}")]
            assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked"), (l, w, g, wx, wy)


def test_text_directions_show_nothing_either_way():
    # "pad length and gap in one direction, the width in the other" is what the turned read sees in (b): the same
    # three flags. So a drawing that does turn its text with its dimension lines is no verdict either (it was agreed)
    (l, w, g), body = IND
    runs = [run(259, 528, f"{l}"), run(287, 514, f"{g}"), run(240, 545, f"{w}", True)]
    assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked")
    runs = [run(330, 566, f"{l}", True), run(342, 540, f"{g}", True), run(287, 590, f"{w}")]
    assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked")


def test_one_pad_length_on_the_gaps_line_shows_nothing():
    # round 2 took "pad length and gap on one line, the width off it" for a dimension line. The mirror placement of
    # the width (beside the pads at the gap's height, the pad length off the line) is an ordinary drawing too
    for (l, w, g), body in (IND, SMA):
        for runs in ([run(259, 528, f"{l}"), run(287, 528, f"{g}"), run(335, 551, f"{w}")],
                     [run(315, 575, f"{l}"), run(287, 575, f"{g}"), run(238, 551, f"{w}")],
                     [run(340, 560, f"{l}"), run(340, 540, f"{g}"), run(300, 580, f"{w}")]):
            assert statuses(runs, (l, w, g), body) == ("unchecked", "unchecked"), (l, w, g, runs)


def test_the_why_names_the_turned_land():
    a, b = both([run(249.8, 558.8, "1.4"), run(287, 540, "3.0"), run(320.2, 540, "2.2")], *IND)
    assert "turned 90 degrees" in a["why"] and "turned 90 degrees" in b["why"], (a, b)
    assert "on both pads" in a["why"], a


# ------------------------------------------------------------------ what still agrees
def test_the_pad_length_on_both_pads_either_side_of_the_gap():
    # 1.4 | 3.0 | 1.4 on one dimension line: only a pad's own size is printed once per pad with the gap between
    for (l, w, g), body in (IND, SMA):
        for wx, wy in ((317, 551), (335, 540), (238, 551), (287, 560), (350, 528)):
            runs = [run(259, 528, f"{l}"), run(287, 528, f"{g}"), run(315, 528, f"{l}"), run(wx, wy, f"{w}")]
            assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked"), (l, w, g, wx, wy)
    # pads one over the other: the three in one column, as level text and as turned text
    (l, w, g), body = IND
    for v in (False, True):
        runs = [run(340, 562, f"{l}", v), run(340, 540, f"{g}", v), run(340, 518, f"{l}", v), run(300, 585, f"{w}")]
        assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked"), v


def test_both_pads_means_either_side_of_the_gap_on_its_line():
    (l, w, g), body = IND
    for what, runs in (
            ("the second pad length off the line", [run(259, 528, "1.4"), run(287, 528, "3.0"), run(315, 560, "1.4")]),
            ("both on one side of the gap", [run(231, 528, "1.4"), run(259, 528, "1.4"), run(287, 528, "3.0")]),
            ("the gap far from the middle", [run(259, 528, "1.4"), run(267, 528, "3.0"), run(315, 528, "1.4")]),
            ("the gap off the pair's line", [run(259, 528, "1.4"), run(287, 540, "3.0"), run(315, 528, "1.4")]),
            ("one of the pair turned", [run(259, 528, "1.4"), run(287, 528, "3.0"), run(315, 528, "1.4", True)])):
        assert statuses(runs + [run(335, 551, "2.2")], (l, w, g), body) == ("unchecked", "unchecked"), what


def test_a_span_has_no_line_with_the_pad_length_on_both_pads():
    # the span's dimension line runs over both pads; pad length + span is left to the body
    (l, w, g), body = IND
    runs = [run(259, 528, "1.4"), run(287, 528, "5.8"), run(315, 528, "1.4"), run(335, 551, "2.2")]
    a = LT.check({1: [TITLE] + runs}, land(l, w, g + l, body), "XYZ5252-100M")
    b = LT.check({1: [TITLE] + runs}, land(w, l, g + 2 * l - w, body), "XYZ5252-100M")
    assert (a["status"], b["status"]) == ("unchecked", "unchecked"), (a, b)


def test_gap_and_span_leave_no_question():
    # gap and span give the pad length themselves; the turned read would need the pad length for its width, and the
    # drawing does not print it. No layout is asked for
    for (l, w, g), body in (IND, SMA):
        for runs in ([run(259, 560, f"{g}"), run(287, 514, f"{g + 2 * l:.1f}"), run(335, 520, f"{w}")],
                     [run(287, 540, f"{g}"), run(287, 575, f"{g + 2 * l:.1f}"), run(335, 540, f"{w}", True)]):
            assert statuses(runs, (l, w, g), body) == ("agreed", "unchecked"), (l, w, g)


def test_the_body_rules_the_turned_land_out():
    # a 6.1 x 2.69 mm fuse, pads 1.96 long and 3.15 wide, 2.95 apart: turned, the pads would be 3.15 long and the land
    # 9.25 mm over all, no land of this body either way round. Three bare numbers, wherever they stand
    true, body = (1.96, 3.15, 2.95), (6.1, 2.69)
    l, w, g = true
    assert LT.fits_body((w, l, g + w), body, sure=True) is not None
    for runs in ([run(249.8, 558.8, f"{l}"), run(287, 540, f"{g}"), run(320.2, 540, f"{w}")],
                 [run(259, 560, f"{l}", True), run(287, 540, f"{g}"), run(320, 520, f"{w}")],
                 [run(259, 566, f"{l}"), run(287, 514, f"{g}"), run(325, 545, f"{w}")]):
        assert statuses(runs, true, body) == ("agreed", "unchecked"), runs


# ------------------------------------------------------------------ brute force: every placement, both ways
# 28 pt apart, and one 2 pt beside another (a hair off one row or column). The tests use the small grid (tests.run_all
# has no time for more); `python -m tests.test_review1123_round3_land_text` runs the same sweeps over the full one
XS, YS = (231, 259, 261, 287, 315, 343), (514, 528, 540, 542, 566)
XS_SMALL, YS_SMALL = (231, 259, 261, 287, 315), (528, 540, 542, 566)


def placements(n, xs=XS_SMALL, ys=YS_SMALL):
    """Every way to put n labels on the grid, each level or turned: [((x, y, turned), ...)]."""
    out = [()]
    for _ in range(n):
        out = [p + ((x, y, v),) for p in out for x in xs for y in ys for v in (False, True)]
    return out


def verdicts(labels, true, body):
    """check()'s verdicts on the true and the turned read of one drawing, without the page's text: labels
    [(text, (x, y, turned))] -> the drawing's numbers as regions() gives them (two numbers at one point are left
    out). The read's own fits_body, the verdict on one drawing and the "agrees turned as well" rule are check()'s
    (test_the_short_cut_is_check below)."""
    l, w, g = true
    region = [(float(t), t, float(x), float(y), v) for t, (x, y, v) in labels
              if not any(o[:2] == (x, y) and u != t for u, o in labels)]
    out = []
    for pads in ((l, w, g + l), (w, l, g + w)):
        if LT.fits_body(pads, body):
            out.append("unchecked")
            continue
        v = LT.check_region(region, pads, None, "XYZ5252-100M", body, False)["status"]
        if v == "agreed" and LT.check_region(region, (pads[1], pads[0], pads[2]), None, "XYZ5252-100M", body,
                                             False)["status"] == "agreed":
            v = "unchecked"
        out.append(v)
    return tuple(out)


def as_runs(labels):
    # numbers() puts a run's number half a character height times half its length along the run from its origin
    return [run(x - (0 if v else 0.5 * 7.0 * len(t) / 2), y - (0.5 * 7.0 * len(t) / 2 if v else 0), t, v)
            for t, (x, y, v) in labels]


def test_the_short_cut_is_check():
    # the sweeps below call check_region as check() does; here every 37th placement goes through check() itself
    for true, body in LANDS:
        l, w, g = true
        for k, p in enumerate(placements(3)):
            if k % 37 == 0:
                labels = list(zip((f"{l}", f"{g}", f"{w}"), p))
                assert statuses(as_runs(labels), true, body) == verdicts(labels, true, body), (true, p)


def sweep_three(xs=XS_SMALL, ys=YS_SMALL):
    """Pad length, gap and pad width, each level or turned, each on every spot of the grid. -> {land: (drawings,
    true read agreed)}; the turned read is asserted unchecked in every one."""
    count = {}
    for true, body in LANDS:
        l, w, g = true
        n = agreed = 0
        for p in placements(3, xs, ys):
            a, b = verdicts(list(zip((f"{l}", f"{g}", f"{w}"), p)), true, body)
            assert b == "unchecked", (true, p)
            n += 1
            agreed += a == "agreed"
        count[true] = (n, agreed)
    return count


def sweep_both_pads(xs=XS_SMALL, ys=YS_SMALL, twice="length"):
    """The same with one size printed twice (one label per pad): the two labels level on every pair of spots of one
    row, level or turned on every pair of one column; the gap on every spot, level or turned; the remaining size
    level or turned on every spot (every fourth of the small grid). twice: "length" (the pad length on both pads) or "width"."""
    count = {}
    pairs = [((x1, y, False), (x2, y, False)) for y in ys for x1 in xs for x2 in xs if x1 < x2] + \
            [((x, y1, v), (x, y2, v)) for v in (False, True) for x in xs for y1 in ys for y2 in ys if y1 < y2]
    for true, body in LANDS:
        l, w, g = true
        two, one = (f"{l}", f"{w}") if twice == "length" else (f"{w}", f"{l}")
        n = agreed = turned = 0
        for p1, p2 in pairs:
            for ps in placements(1, xs, ys):
                for po in placements(1, xs, ys)[::4 if xs is XS_SMALL else 1]:
                    a, b = verdicts([(two, p1), (two, p2), (f"{g}", ps[0]), (one, po[0])], true, body)
                    n += 1
                    agreed += a == "agreed"
                    turned += b == "agreed"
        count[true] = (n, agreed, turned)
    return count


def test_three_bare_numbers_never_agree_with_the_turned_read():
    # 64 000 drawings a land. And the true read: three bare numbers of a land whose turned twin is a possible land
    # of the body are no verdict at all, wherever they stand
    assert sweep_three() == {IND[0]: (64000, 0), SMA[0]: (64000, 0), CHIP[0]: (64000, 0)}


def test_the_pad_length_on_both_pads_never_agrees_with_the_turned_read():
    count = sweep_both_pads()
    assert all(c[2] == 0 for c in count.values()), count
    # the true read agrees where the gap is on the pair's line, midway between them (the 0603's own land breaks
    # fits_body's rule of thumb for a read, so it is never agreed)
    assert count[CHIP[0]][1] == 0, count
    assert count[IND[0]] == count[SMA[0]] and count[IND[0]][1] > 0, count


def test_the_places_never_say_kept_both_ways():
    # whatever the places are, the read and its turned twin are not both shown by them
    (l, w, g), body = IND
    for p in placements(2):
        for p2 in ((231, 528, False), (315, 528, False), (287, 540, False), (287, 566, True)):
            L, S, W = (l, f"{l}", *p[0]), (g, f"{g}", *p[1]), (w, f"{w}", 300.0, 551.0, False)
            L2, W2 = (l, f"{l}", *p2), (w, f"{w}", p2[0], p2[1] + 3.0, p2[2])
            region = [L, S, W, L2, W2]
            fwd = LT._layout(L, S, W, region, "gap")
            back = LT._layout(W, S, L, region, "gap")
            assert not (fwd == "kept" and back == "kept"), region
            assert (fwd == "kept") == (back == "turned"), (region, fwd, back)


if __name__ == "__main__":
    print("three bare numbers, full grid {land: (drawings, true agreed)}; turned agreed in none:", sweep_three(XS, YS))
    print("pad length on both pads, full grid {land: (drawings, true agreed, turned agreed)}:",
          sweep_both_pads(XS, YS))
    print("pad WIDTH on both pads, full grid {land: (drawings, true agreed, turned agreed)}:",
          sweep_both_pads(XS, YS, "width"))
