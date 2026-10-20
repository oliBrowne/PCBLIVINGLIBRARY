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
