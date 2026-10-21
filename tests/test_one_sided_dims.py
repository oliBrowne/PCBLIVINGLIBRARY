"""A dimension the drawing prints with only one bound ("0.9 MIN" for a SOT-223 lead length, a tab width "6.22 MIN").

Live 2026-10-21 (Opus 5.5, zoom on): LP38693MP-1.8 was read three times with terminal_length {"min": 0.9}; each time
the schema check rejected it (a dim needs min+max or nom), the 4th read gave up and the part had no footprint.
IRFS4115's tab.width {"min": 6.22} failed the same way on 2026-10-19."""
import copy

from ecadlib import datasheet as D, ds_route as DR
from tests.test_datasheet import INCH, run


def test_a_min_only_lead_length_becomes_the_nominal_with_a_review_line():
    e = copy.deepcopy(INCH)
    e["package"]["terminal_length"] = dict(e["package"]["terminal_length"], max=None)     # "0.016 MIN"
    res = run(e)
    assert res["spec"]["terminal_length"] == {"min": 0.4064, "nom": 0.4064}
    assert not any(r.startswith("schema:") for r in res["needs_review"])
    line = [r for r in res["needs_review"] if r.startswith("terminal_length: printed only as")]
    assert line == ["terminal_length: printed only as 0.4064 mm MIN; used as the nominal, check the land against the drawing"]
    assert DR.assess(res)[0] == "ok"                  # a note for the person, not a failed read that is sent again
    assert not any(r.startswith("footprint_gen.build failed") for r in res["needs_review"])


def test_a_max_only_value_and_a_full_range_are_left_as_they_are():
    e = copy.deepcopy(INCH)
    e["package"]["terminal_width"] = dict(e["package"]["terminal_width"], min=None)      # "0.020 MAX"
    res = run(e)
    assert res["spec"]["terminal_width"] == {"max": 0.508, "nom": 0.508}
    assert run(INCH)["spec"]["terminal_length"] == {"min": 0.4064, "max": 1.27}


def test_a_one_sided_tab_width_is_kept_too():
    review = []
    tab = D._one_sided({"number": "4", "side": "R", "width": {"min": 6.22}, "length": {"min": 1.4, "max": 1.8}},
                       "tab", review)
    assert tab["width"] == {"min": 6.22, "nom": 6.22} and tab["length"] == {"min": 1.4, "max": 1.8}
    assert review == ["tab.width: printed only as 6.22 mm MIN; used as the nominal, check the land against the drawing"]
