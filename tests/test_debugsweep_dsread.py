"""Regression tests for the datasheet-reading debug sweep (group dsread: DS2-12 ..).
Plain functions (tests.run_all, no pytest). Every API is faked; nothing is sent anywhere."""
from ecadlib import datasheet as D
from tests.test_bughunt_dsa import _rejected, _review, _wire


# ---- DS2-12: a toleranced or range value with its own unit skipped the DS-3 unit check
def test_ds2_12_toleranced_value_in_another_unit_is_rejected():
    for v in ("0.016~0.050 in", "0.016±0.002 in", "16±2 mil", "0.016 to 0.050 inch"):
        _rejected(_wire(lambda w, d: d["terminal_length"].update(min="", nom=v, max="")),
                  "terminal_length.nom", "'mm'")

    def printed_in_inches(w, d):                      # the same value, seen only in the unresolved note
        d["terminal_length"].update(min="", nom="0.4", max="")
        w["package"]["unresolved"] = ["terminal_length min/max (printed as 0.4±0.1 in)"]
    _rejected(_wire(printed_in_inches), "terminal_length", "'in'", "'mm'")

    res, (kind, _) = _review(lambda w, d: d["terminal_length"].update(min="", nom="0.3~0.5 mm", max=""))
    assert kind == "ok", (kind, res["needs_review"])  # the record's own unit written in the cell is fine
    assert {k: res["spec"]["terminal_length"][k] for k in ("min", "nom", "max")} == {"min": 0.3, "nom": 0.4, "max": 0.5}


# ---- DS2-14: a pin number printed as a circled digit ('①', ROHM / Toshiba) crashed the read with ValueError
def test_ds2_14_circled_digit_pin_number_is_a_review_line_not_a_crash():
    from ecadlib import crosscheck as CC
    for num in ("①", "1²"):                          # str.isdigit() is True for both, int() reads neither
        def mut(w, d, num=num):
            w["pins"][0]["num"] = num
        res, (kind, _) = _review(mut)
        assert f"symbol: non-numeric pin numbers [{num!r}] (footprint pads are 1..N, exposed pad N+1)" \
            in res["needs_review"], res["needs_review"]
        assert kind != "ok", kind
    diff = CC.compare_pins([("1", "VIN"), ("2", "GND")], [("①", "VIN"), ("2", "GND")])   # the cross-check's second read
    assert diff[0]["field"] == "pins" and "only here: ①" in diff[0]["second"], diff
