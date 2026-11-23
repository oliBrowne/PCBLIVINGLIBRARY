"""Offline tests for round 2 of the 2026-11-23 review, group R-D (the second read and the datasheet read): each fix has
the inputs that must be held and the ordinary parts that must stay ready.   python3 -m tests.test_review1123_round2_second_read
  I1  a small terminal read half or 1.5 times is a disagreement (the 0.10 mm tolerance let 0.1 and 0.3 pass for 0.2)"""
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
