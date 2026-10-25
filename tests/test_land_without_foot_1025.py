"""A gullwing read that gives the maker's whole lead land (pad_length, pad_width, pad_center) but no foot length
(terminal_length) is built from that land, not held for the foot: the builder needs the foot only to work out an
IPC land, which the maker's land replaces. LTV-352T (benchmark 2026-10-25): Lite-On p11 prints the recommended land
(pads 0.8 x 1, rows 6.5 mm apart) and the outline leaves the foot undimensioned, so the read left terminal_length
unresolved and the part was held."""
import copy

from ecadlib import datasheet as D, ds_route as DR, footprint_gen as F

LTV = {"name": "SO-4", "description": "SO-4 optocoupler", "package_type": "gullwing", "pin_count": 4, "pitch": 2.54,
       "body_length": {"min": 3.4, "max": 3.8}, "body_width": {"min": 4.2, "max": 4.6},
       "lead_span": {"min": 6.8, "max": 7.2}, "terminal_width": {"min": 0.3, "max": 0.5}, "height_max": 2.1,
       "land": {"pad_length": 1.0, "pad_width": 0.8, "pad_center": 3.25}}


def test_the_makers_lead_land_builds_without_a_foot_length():
    _, rep = F.build(copy.deepcopy(LTV))
    assert rep["land_source"] == "datasheet" and rep["X"] == 0.8 and rep["Z"] == 7.5


def test_a_foot_length_is_still_needed_without_the_makers_land():
    s = copy.deepcopy(LTV)
    del s["land"]
    assert D.build_blockers(s) == ["terminal_length"]
    assert D.build_blockers(LTV) == []
    s = copy.deepcopy(LTV)
    s["land"] = {"ep_across": 2.0, "ep_along": 2.0}               # a thermal land only: the leads are IPC's
    assert D.build_blockers(s) == ["terminal_length"]


def test_a_tab_sized_from_the_foot_still_needs_it():
    s = copy.deepcopy(LTV)
    s["tab"] = {"number": 5, "side": "R", "offset": 0, "width": {"min": 3.0, "max": 3.2}}
    assert D.build_blockers(s) == ["terminal_length"]


def test_the_reads_unresolved_foot_is_dropped_when_its_land_sizes_the_leads():
    from tests import test_datasheet as TD
    pkg = copy.deepcopy(TD.GOOD["package"])
    keys = D.output_schema()["properties"]["package"]["properties"]["land"]["anyOf"][0]["properties"]
    d = lambda lab, mn, mx: TD.dim(lab, mn, None, mx, page=10, figure="Package outline")
    pkg.update({"name": "SO-4", "package_type": "gullwing", "pin_count": 4, "exposed_pad": None,
                "pins_left_right": None, "pins_top_bottom": None,
                "pitch": TD.dim("e", None, 2.54, None, page=10, figure="Package outline"),
                "body_length": d("D", 3.4, 3.8), "body_width": d("E1", 4.2, 4.6), "lead_span": d("E", 6.8, 7.2),
                "terminal_width": d("b", 0.3, 0.5), "terminal_length": None,
                "unresolved": ["terminal_length (foot length not dimensioned on the drawing)"],
                "land": dict(dict.fromkeys(keys), pad_length=1.0, pad_width=0.8, pad_center=3.25)})
    review = []
    spec, _ = D.to_spec(pkg, "LTV-352T", "ds.pdf", "m", 0, review)
    assert "unresolved" not in spec, spec.get("unresolved")
    assert any("terminal_length" in r and "maker's land" in r for r in review), review
    assert DR.assess({"spec": spec, "needs_review": []})[0] == "ok"
    pkg["land"] = None                                             # no land: the foot is still asked for
    spec, _ = D.to_spec(pkg, "LTV-352T", "ds.pdf", "m", 0, [])
    assert any(u.startswith("terminal_length") for u in spec["unresolved"]), spec.get("unresolved")
