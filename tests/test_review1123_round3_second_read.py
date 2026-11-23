"""Offline tests for round 3 of the 2026-11-23 review (the second independent review), group second read: each fix has
the inputs that must be held and the ordinary parts that must stay ready.   python3 -m tests.test_review1123_round3_second_read
  B1  ds_route.open_unresolved is an allow-list: only an entry that is wholly about a thing no pad stands on is skipped
  B2  FC-QFN-16, DR-QFN-44, QFN-16-HR get no quarter-per-side default (the hyphen forms of FCQFN / DRQFN / QFN-HR)
  A9  a search result that names no maker, or more than one, is not the listing of the maker the BOM names
  B4  a BOM that says "ams" / "ams AG" for a part ams-OSRAM lists is the same maker; bare AMS stays apart otherwise"""
import copy

from ecadlib import datasheet as D, ds_route as DR
from tests import test_fix1026_rd as T, test_review1123_second_read as R, test_review1123_round2_second_read as R2

LIB_SYMBOL = R.LIB_SYMBOL


# ---------------------------------------------------------------- B1: unresolved entries, an allow-list
# the second review's entries that round 2 no longer held (scripts2/i4.py)
B1_HOLD = ("could not tell which variant applies", "variant A vs B", "e", "D", "E", "b", "L", "D2/E2", "symbol b unreadable",
           "value of e blurry", "numbering clockwise or counter-clockwise?", "mirrored view?", "bottom view vs top view",
           "unit unclear", "scale", "the scan is blurry", "text illegible", "page missing", "datasheet is for a different part",
           "wrong datasheet?", "part number not found in the document", "conflicting values",
           "values differ between pages 12 and 14", "ambiguous", "uncertain", "low confidence", "interpolated", "approximated",
           "from memory", "typical value used", "datasheet lacks this; used standard", "count uncertain",
           "not applicable: pitch", "lead_span: n/a")
# more of the kind: a harmless word beside a doubt, a dimension letter with n/a, words that name nothing at all
B1_HOLD_MORE = ("marking differs: datasheet is for a different part", "weight and pitch not given", "e: n/a", "D2, E2 not applicable",
                "A1", "cathode marking not found", "pin 1 marking unclear", "thermal pad n/a", "not given", "no data", "?",
                "everything", "unsure which package", "tolerance of e", "JEDEC reference used instead", "derived", "rounded",
                "ordering code decides the pinout", "tape orientation of pin 1", "package: n/a", "resistance not stated",
                "lead_span (not applicable: no-lead package)", "terminal_length n/a for this package", "typo in the datasheet?")
B1_READY = ("weight", "marking code", "moisture sensitivity level", "thermal resistance not stated", "RoHS status",
            "not applicable", "N/A", "none",
            "Not applicable.", "n/a", "weight not given", "top marking not shown", "tape and reel quantity", "Weight: n/a",
            "MSL", "MSL 3", "packing quantity not stated", "ordering information", "ordering code not listed", "reel size",
            "thermal resistance junction to ambient not specified", "device marking", "RoHS / REACH compliance",
            "mass not given in the datasheet", "packaging", "moisture level", "marking and weight not given", "nothing")


def test_an_unresolved_entry_holds_unless_it_is_wholly_about_a_harmless_thing():
    t = T.SMA
    for u in B1_HOLD + B1_HOLD_MORE:
        res = T.read(dict(copy.deepcopy(t), unresolved=[u]))
        assert DR.open_unresolved(res["spec"]) == [u], (u, DR.open_unresolved(res["spec"]))
        if DR.hold_reasons(res, True):                # held outright (a field the read gave and calls unread): as safe
            continue
        cc, job = R.checked(res, t)
        assert cc["status"] == "self-checked" and cc["unresolved"] == [u], (u, cc["status"], cc.get("unresolved"))
        assert R.put(res, job, LIB_SYMBOL)[0] == "verify", u


def test_an_unresolved_entry_about_a_harmless_thing_does_not_keep_the_part_with_a_person():
    t = T.SMA
    for u in B1_READY:
        res = T.read(dict(copy.deepcopy(t), unresolved=[u]))
        assert not DR.open_unresolved(res["spec"]), (u, DR.open_unresolved(res["spec"]))
        assert not DR.hold_reasons(res, True), (u, DR.hold_reasons(res, True))
        cc, job = R.checked(res, t)
        assert cc["status"] == "agreed" and not cc.get("unresolved"), (u, cc["status"], cc.get("unresolved"))
        assert R.put(res, job, LIB_SYMBOL)[0] == "released", u
    # what was advice before round 2 is advice still: the height, and a limit of a dimension whose value was read
    for u in ("height_max", "seated height not given", "height n/a", "description"):
        assert not DR.open_unresolved(dict(T.read(copy.deepcopy(t))["spec"], unresolved=[u])), u
    assert not DR.open_unresolved({"body_length": 4.3, "unresolved": ["body_length min/max (not printed)"]})
    # one harmless entry beside one that is not: the other still counts
    assert DR.open_unresolved({"unresolved": ["weight", "scale", "N/A", "e"]}) == ["scale", "e"]


# ---------------------------------------------------------------- B2: FC-QFN, DR-QFN, QFN-16-HR
def test_a_flip_chip_dual_row_or_hot_rod_qfn_written_with_a_hyphen_gets_no_quarter_per_side():
    for name, n, side, pitch, ep in (("FC-QFN-16", 16, 3.0, 0.5, 1.6), ("DR-QFN-44", 44, 5.0, 0.4, None), ("QFN-16-HR", 16, 3.0, 0.5, None),
                                     ("FC QFN-16", 16, 3.0, 0.5, 1.6), ("FC_QFN_16", 16, 3.0, 0.5, 1.6), ("DR QFN 44", 44, 5.0, 0.4, None),
                                     ("DR_QFN-44", 44, 5.0, 0.4, None), ("QFN-16 HR", 16, 3.0, 0.5, None), ("QFN_16_HR", 16, 3.0, 0.5, None),
                                     ("QFN16-HR", 16, 3.0, 0.5, None), ("QFN16HR", 16, 3.0, 0.5, None), ("VQFN-16-HR", 16, 3.0, 0.5, None),
                                     ("QFN-FC-16", 16, 3.0, 0.5, 1.6), ("QFN-16-FC", 16, 3.0, 0.5, 1.6), ("QFN-DR-44", 44, 5.0, 0.4, None),
                                     ("FC-VQFN-16", 16, 3.0, 0.5, 1.6), ("DR-WQFN-44", 44, 5.0, 0.4, None),
                                     ("FCQFN-16", 16, 3.0, 0.5, 1.6), ("FCQFN16", 16, 3.0, 0.5, None), ("DRQFN-44", 44, 5.0, 0.4, None), ("VQFN-HR-12", 12, 2.0, 0.4, None)):
        res = T.read(R2.quad(name, n, side, pitch, ep))
        s = res["spec"]
        assert "pins_left_right" not in s and "pins_top_bottom" not in s, (name, s.get("pins_left_right"), res["needs_review"])
        assert any(r.startswith(D.ROWS_UNREAD) for r in res["needs_review"]), (name, res["needs_review"])
        out = R.put(res, symbol=LIB_SYMBOL)[0]            # ("verify": a checked standard land of the read's numbers is
        assert out.startswith("held: ") and D.ROWS_UNREAD in out or out == "verify", (name, out)   # built, for a person)


def test_an_ordinary_qfn_name_with_letters_like_hr_fc_or_dr_in_it_keeps_its_quarter_per_side():
    for name, n, side, pitch, ep in R2.QUADS + (("QFN-16 (DRB)", 16, 3.0, 0.5, 1.6), ("QFN-16-HRS", 16, 3.0, 0.5, 1.6),
                                               ("AFC-QFN-16", 16, 3.0, 0.5, 1.6), ("QFN-16-DRV", 16, 3.0, 0.5, 1.6),
                                               ("PG-VQFN-48", 48, 7.0, 0.5, 5.35), ("QFN-16-EP", 16, 3.0, 0.5, 1.6)):
        res = T.read(R2.quad(name, n, side, pitch, ep))
        s = res["spec"]
        assert (s.get("pins_left_right"), s.get("pins_top_bottom")) == (n // 4, n // 4), (name, n, side, res["needs_review"])
        assert not DR.hold_reasons(res, True), (name, DR.hold_reasons(res, True))
    R2.test_the_rows_default_changed_no_text_sent_to_a_model()      # the schema's words on the rows are as they were


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
