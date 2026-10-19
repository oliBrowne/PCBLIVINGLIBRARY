"""Regression tests for the datasheet reader / model layer / cost guard bug-hunt findings (group dsa: DS-1..DS-33).
Plain functions (tests.run_all, no pytest). Every API is faked; nothing is sent anywhere."""
import copy, json

from ecadlib import datasheet as D, ds_route as DR
from tests.test_datasheet import GOOD, _complete, message


def _wire(mut):
    w = D.to_wire(_complete(copy.deepcopy(GOOD)))
    dims = {d["name"]: d for d in w["package"]["dims"]}
    mut(w, dims)
    return message(w)


def _rejected(msg, *words):
    try:
        D.parse_message(msg)
    except D.DatasheetError as e:
        assert all(w in str(e) for w in words), e
        return str(e)
    raise AssertionError("answer was accepted")


def _review(mut):
    ext = D.parse_message(_wire(mut))
    res = D.process(ext, "TESTPART", "https://example.com/x.pdf")
    return res, DR.assess(res)


# ---- DS-1: NaN / Infinity / 0 dimensions
def test_ds1_nan_dimension_is_rejected():
    _rejected(_wire(lambda w, d: d["height_max"].update(max="NaN")), "height_max.max", "finite")
    _rejected(_wire(lambda w, d: d["pitch"].update(nom="inf")), "pitch.nom", "finite")


def test_ds1_nan_in_an_answer_already_in_output_shape_is_rejected():
    ext = _complete(copy.deepcopy(GOOD))
    ext["package"]["height_max"]["max"] = float("nan")
    _rejected(message(ext), "height_max", "finite")


def test_ds1_zero_terminal_length_goes_to_review_and_fails_assess():
    res, (kind, why) = _review(lambda w, d: d["terminal_length"].update(min="0", nom="0", max="0"))
    assert any(r.startswith("terminal_length:") and "above 0" in r for r in res["needs_review"]), res["needs_review"]
    assert kind == "failed", (kind, why)


def test_ds1_good_answer_still_clean():
    res, (kind, _) = _review(lambda w, d: None)
    assert kind == "ok" and not res["needs_review"], res["needs_review"]


# ---- DS-3: a unit written inside the value
def test_ds3_value_unit_differs_from_record_unit_is_rejected():
    def mut(w, d):
        d["terminal_length"].update(min="0.012 in", nom="0.016 in", max="0.020 in")
    _rejected(_wire(mut), "terminal_length.min", "'in'", "'mm'")


def test_ds3_value_unit_same_as_record_unit_is_fine():
    assert D._wire_val("0.40 mm", "x") == (0.4, "mm")
    assert D._wire_val("16 mils", "x") == (16.0, "mil")
    assert D._wire_val("0.50 BSC", "x") == (0.5, None)
    res, (kind, _) = _review(lambda w, d: d["terminal_length"].update(min="0.3mm", nom="0.4 mm", max="0.5 MM"))
    assert kind == "ok" and res["spec"]["terminal_length"]["nom"] == 0.4


# ---- DS-5: the same dimension twice
def test_ds5_duplicate_dimension_with_different_values_is_rejected():
    def mut(w, d):
        w["package"]["dims"].append(dict(d["terminal_length"], label="L1", min="0.2", nom="0.25", max="0.3"))
    _rejected(_wire(mut), "terminal_length twice")


def test_ds5_identical_duplicate_is_accepted():
    def mut(w, d):
        w["package"]["dims"].append(dict(d["terminal_length"]))
    res, (kind, _) = _review(mut)
    assert kind == "ok"


# ---- DS-23: mislabelled units on height_max / terminal_length
def test_ds23_mislabelled_height_and_terminal_units_are_flagged():
    for mut, key in ((lambda w, d: d["height_max"].update(unit="in"), "height_max"),
                     (lambda w, d: d["height_max"].update(unit="mil"), "height_max"),
                     (lambda w, d: d["terminal_length"].update(unit="mil"), "terminal_length")):
        res, (kind, why) = _review(mut)
        assert kind == "failed" and any(r.startswith(key) and "implausib" in r for r in why), (key, why)
