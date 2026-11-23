"""Offline tests for round 2 of the 2026-11-23 review, group R-D (the second read and the datasheet read): each fix has
the inputs that must be held and the ordinary parts that must stay ready.   python3 -m tests.test_review1123_round2_second_read
  I1  a small terminal read half or 1.5 times is a disagreement (the 0.10 mm tolerance let 0.1 and 0.3 pass for 0.2)
  I2  a square four-sided QFN of 12 pins or more with blank rows gets a quarter per side again; 8 pins stay held"""
import copy, io, json, tempfile

from ecadlib import crosscheck as CC, datasheet as D, ds_assistant as DA, ds_route as DR, librarian as L
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_assistant as TA
from tests import test_fix1026_rd as T, test_review1123_second_read as R

LIB_SYMBOL = R.LIB_SYMBOL


# ---------------------------------------------------------------- I1: a small terminal read half or 1.5 times
def small(tl, tw=0.25):
    return {"name": "DFN-8", "package_type": "qfn", "pin_count": 8, "pitch": 0.5, "body_length": 2.0, "body_width": 2.0,
            "pins_left_right": 4, "pins_top_bottom": 0, "terminal_length": tl, "terminal_width": tw, "height_max": 0.8}


def chip(tl):
    return {"name": "C0402", "package_type": "chip", "pin_count": 2, "body_length": 1.0, "body_width": 0.5,
            "terminal_length": tl, "height_max": 0.5}


def fields(first, second):
    rec = CC.compare(CC.critical(first), dict(CC.critical(second), rows_given=True), "read")
    return rec["status"], [d["field"] for d in rec["disagreed"]]


def test_a_small_terminal_read_half_or_one_and_a_half_times_disagrees():
    for make, true, wrong in ((small, 0.2, (0.1, 0.3, 0.4)), (chip, 0.2, (0.1, 0.3)), (small, 0.25, (0.125, 0.375)),
                              (small, 0.4, (0.2, 0.6)), (chip, 0.35, (0.175, 0.525))):
        for tl in wrong:
            assert fields(make(tl), make(true)) == ("disagreed", ["terminal_length"]), (make.__name__, tl, true)
            assert fields(make(true), make(tl)) == ("disagreed", ["terminal_length"]), (make.__name__, true, tl)
    for tw, true in ((0.125, 0.25), (0.375, 0.25), (0.1, 0.2), (0.3, 0.2)):
        assert fields(small(0.4, tw), small(0.4, true)) == ("disagreed", ["terminal_width"]), (tw, true)


def test_a_small_terminal_misread_is_not_released_with_a_second_read_that_reads_it_right():
    t = dict(T.VQFN16, terminal_length=(0.15, 0.2, 0.25))          # a 0.2 mm terminal: half is 0.1, 1.5 times is 0.3
    for f in (0.5, 1.5):
        res = T.read(R.scaled(t, "terminal_length", f))
        if DR.hold_reasons(res, True):                # held before any second read: as safe
            continue
        cc, job = R.checked(res, t)
        assert cc["status"] == "disagreed", (f, cc["status"], CC.summary_line(cc))
        assert "terminal_length" in [d["field"] for c in cc["checks"] for d in c["disagreed"]]
        assert R.put(res, job, LIB_SYMBOL)[0] != "released"


def test_two_reads_of_one_terminal_that_differ_by_a_rounding_still_agree():
    for make, a, b in ((small, 0.2, 0.25), (small, 0.2, 0.15), (small, 0.4, 0.45), (small, 0.4, 0.35), (chip, 0.2, 0.25),
                       (chip, 0.25, 0.3), (small, 0.55, 0.6)):
        assert fields(make(a), make(b)) == ("agreed", []), (make.__name__, a, b)
    # a long terminal keeps the tolerance it had (0.10 mm, 5 % from 2 mm): never looser than before
    sma = lambda tl: {"name": "D_SMA", "package_type": "molded", "pin_count": 2, "body_length": 5.0, "body_width": 2.6,
                      "terminal_length": tl, "terminal_width": 1.45, "polarized": True}
    assert fields(sma(1.14), sma(1.2)) == ("agreed", []) and fields(sma(1.14), sma(1.24)) == ("agreed", [])
    assert fields(sma(1.14), sma(1.3))[0] == "disagreed"
    assert fields(small(0.4, 0.25), small(0.4, 0.3)) == ("agreed", [])        # the width's tolerance is as it was
    # the whole way: a right read of a 0.2 mm terminal and a right second read are released
    t = dict(T.VQFN16, terminal_length=(0.15, 0.2, 0.25))
    res = T.read(copy.deepcopy(t))
    cc, job = R.checked(res, t)
    assert cc["status"] == "agreed" and R.put(res, job, LIB_SYMBOL)[0] == "released", (cc["status"], CC.summary_line(cc))


# ---------------------------------------------------------------- I2: a square four-sided QFN with blank rows
def quad(name, n, side, pitch, ep=None, width=None, b=0.25):
    lim = lambda x: (round(x - 0.1, 3), x, round(x + 0.1, 3))
    t = dict(name=name, package_type="qfn", pin_count=n, pitch=pitch, body_length=lim(side), body_width=lim(width or side),
             terminal_width=(round(b - 0.05, 3), b, round(b + 0.05, 3)), terminal_length=(0.3, 0.4, 0.5))
    if ep:
        t["exposed_pad"] = {"length": lim(ep), "width": lim(ep)}
    return t


# (the exposed pads are sizes no checked standard package has, so the table cannot give the rows)
QUADS = (("QFN-16", 16, 3.0, 0.5, 1.6), ("QFN-16", 16, 4.0, 0.65, 2.3), ("QFN-20", 20, 4.0, 0.5, 2.55), ("QFN-24", 24, 4.0, 0.5, 2.35),
         ("QFN-32", 32, 5.0, 0.5, 3.2), ("QFN-48", 48, 7.0, 0.5, 5.35), ("QFN-12", 12, 3.0, 0.65, 1.3),
         ("VQFN-16", 16, 3.0, 0.5, 1.6), ("WQFN-24", 24, 4.0, 0.5, 2.35), ("TQFN-20", 20, 4.0, 0.5, 2.55),
         ("UQFN-20", 20, 3.0, 0.4, 1.6), ("HVQFN32", 32, 5.0, 0.5, 3.2), ("X2QFN-16", 16, 3.0, 0.5, 1.6),
         ("UFQFPN32", 32, 5.0, 0.5, 3.2), ("VFQFPN-48", 48, 7.0, 0.5, 5.35), ("LFCSP-16", 16, 3.0, 0.5, 1.6),
         ("32-Lead LFCSP_WQ", 32, 5.0, 0.5, 3.2), ("Texas_RGT0016X_VQFN-16", 16, 3.0, 0.5, 1.6), ("16-pin QFN (RTE)", 16, 3.0, 0.5, 1.6))


def test_a_square_four_sided_qfn_with_blank_rows_gets_a_quarter_per_side_and_is_not_held():
    for name, n, side, pitch, ep in QUADS:
        res = T.read(quad(name, n, side, pitch, ep))
        s = res["spec"]
        assert (s.get("pins_left_right"), s.get("pins_top_bottom")) == (n // 4, n // 4), (name, n, side, res["needs_review"])
        assert not DR.hold_reasons(res, True), (name, DR.hold_reasons(res, True))
        line = [r for r in res["needs_review"] if "were left blank" in r]
        assert line and f"{n // 4} per side" in line[0] and "check against the drawing" in line[0], (name, res["needs_review"])
        assert not any(r.startswith(D.ROWS_UNREAD) for r in res["needs_review"])


def test_the_quarter_per_side_is_released_only_when_the_second_read_gives_the_same_sides():
    t = quad("QFN-16", 16, 3.0, 0.5, 1.6)
    true = dict(t, pins_left_right=4, pins_top_bottom=4)
    res = T.read(copy.deepcopy(t))
    cc, job = R.checked(res, true)
    assert cc["status"] == "agreed" and "pin_positions" in cc["checks"][-1]["agreed"], (cc["status"], CC.summary_line(cc))
    assert R.put(res, job, LIB_SYMBOL)[0] == "released"
    # the drawing has 5 + 3 + 5 + 3 and the second read sees it: not agreed
    res = T.read(copy.deepcopy(t))
    cc, job = R.checked(res, dict(t, pins_left_right=5, pins_top_bottom=3))
    assert cc["status"] == "disagreed" and R.put(res, job, LIB_SYMBOL)[0] != "released", (cc["status"], CC.summary_line(cc))
    # a second read that gives no sides: the default was nobody's reading, so it waits for a person
    res = T.read(copy.deepcopy(t))
    cc, job = R.checked(res, true, rows=False)
    assert cc["status"] == "self-checked" and R.put(res, job, LIB_SYMBOL)[0] == "verify", cc["status"]


def test_blank_rows_stay_held_where_a_quarter_per_side_would_be_a_guess():
    held = (quad("QFN-8", 8, 3.0, 0.65), quad("VQFN-8", 8, 2.0, 0.5), quad("MLP-8", 8, 3.0, 0.65),      # under 12 pins
            quad("QFN-4", 4, 1.0, 0.65), quad("QFN-10", 10, 3.0, 0.5), quad("QFN-14", 14, 3.5, 0.5),   # not four equal rows
            quad("UQFN-16", 16, 2.6, 0.4, width=1.8), quad("QFN-20", 20, 3.0, 0.5, 1.2, width=4.0),    # not a square body
            quad("MLP-16", 16, 3.0, 0.5, 1.6), quad("LLP-16", 16, 4.0, 0.5, 2.3),                      # the name says no QFN
            quad("PQFN-16", 16, 3.0, 0.5, 1.6), quad("DRQFN-64", 64, 7.0, 0.5, 4.25),                  # power / dual-row QFN
            quad("VQFN-HR-12", 12, 2.0, 0.4), quad("QFN-64 dual row", 64, 7.0, 0.5, 4.25),
            quad("QFN-12", 12, 3.0, 0.45, 1.3),       # 6 a side fit on two sides of 3 mm at 0.45: it may be a DFN-12
            quad("QFN-16", 16, 4.0, 0.4, 2.3),        # 8 a side fit on two sides of 4 mm at 0.4
            dict(quad("QFN-16", 16, 3.0, 0.5, 1.6), pitch=None))
    for t in held:
        res = T.read(copy.deepcopy(t))
        s = res["spec"]
        assert "pins_left_right" not in s and "pins_top_bottom" not in s, (t["name"], s.get("pins_left_right"), res["needs_review"])
        assert any(r.startswith(D.ROWS_UNREAD) for r in res["needs_review"]), (t["name"], res["needs_review"])
        if t["pitch"]:
            out = R.put(res, symbol=LIB_SYMBOL)[0]
            assert out.startswith("held: ") and D.ROWS_UNREAD in out, (t["name"], out)


def test_rows_the_read_gave_and_a_two_sided_name_are_untouched():
    res = T.read(dict(quad("QFN-16", 16, 3.0, 0.5, 1.6), pins_left_right=5, pins_top_bottom=3))
    assert (res["spec"]["pins_left_right"], res["spec"]["pins_top_bottom"]) == (5, 3)
    assert not any("were left blank" in r for r in res["needs_review"])
    res = T.read(quad("DFN-12", 12, 3.0, 0.45, 1.3))
    assert (res["spec"]["pins_left_right"], res["spec"]["pins_top_bottom"]) == (6, 0)
    res = T.read(quad("LFCSP-8", 8, 3.0, 0.5, 1.3))                           # an LFCSP of up to 10 leads is two-sided
    assert (res["spec"]["pins_left_right"], res["spec"]["pins_top_bottom"]) == (4, 0)


def test_the_rows_default_changed_no_text_sent_to_a_model():
    """Saved answers stay valid: the schema's own words on the rows are as they were (they are in every first read's
    cache key), and the default is made in code after the answer."""
    p = D.SCHEMA["properties"]
    assert p["pins_left_right"]["description"] == "qfn: pins on each of the left and right sides (default pin_count/4)"
    assert p["pins_top_bottom"]["description"] == "qfn: pins on each of the top and bottom sides (default pin_count/4)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
