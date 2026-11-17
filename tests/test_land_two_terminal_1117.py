"""A two-terminal molded part (power inductor) whose read gives the maker's whole land (pad_length, pad_width,
pad_center) but no terminal length is built from that land instead of failing for the terminal length: the builder
needs the terminal only to work out an IPC land, which the maker's land replaces (as for a gullwing, LTV-352T,
test_land_without_foot_1025). NRS4018T100MDGJ (Taiyo Yuden, PC 1000-part read sample 2026-11-17): the catalogue prints
the recommended land (1.2 x 3.7 mm pads at +-1.4 mm) and the outline leaves the terminals undimensioned, so the read
failed with "terminal_length or inner_span blank". With no terminal read, the land must still reach the body's ends:
a land short of them, or pads that meet in the middle, is held (nothing checks it against a terminal)."""
import copy

from ecadlib import datasheet as D, ds_route as DR, footprint_gen as F

NRS = {"name": "L_NRS4018", "description": "NRS4018 shielded power inductor", "package_type": "molded",
       "pin_count": 2, "body_length": {"min": 3.8, "nom": 4.0, "max": 4.2},
       "body_width": {"min": 3.8, "nom": 4.0, "max": 4.2}, "height_max": 1.8,
       "land": {"pad_length": 1.2, "pad_width": 3.7, "pad_center": 1.4}}


def test_the_makers_land_builds_a_molded_part_without_a_terminal_length():
    mod, rep = F.build(copy.deepcopy(NRS))
    assert rep["land_source"] == "datasheet" and rep["X"] == 3.7 and rep["Z"] == 4.0 and rep["G"] == 1.6, rep
    assert "(at -1.4 0)" in mod and "(size 1.2 3.7)" in mod, mod


def test_blockers_and_unresolved_drop_the_terminal_only_with_the_whole_land():
    assert D.build_blockers(NRS) == []
    s = copy.deepcopy(NRS)
    del s["land"]
    assert D.build_blockers(s) == ["terminal_length or inner_span"]
    s["land"] = {"pad_length": 1.2, "pad_width": 3.7}            # part of a land: IPC still needs the terminal
    assert D.build_blockers(s) == ["terminal_length or inner_span"]
    for t in ("chip", "molded"):
        s = dict(copy.deepcopy(NRS), package_type=t)
        assert F.land_sizes_leads(s), t


def test_a_land_that_does_not_reach_the_body_ends_is_held():
    s = copy.deepcopy(NRS)
    s["land"] = {"pad_length": 0.6, "pad_width": 3.7, "pad_center": 1.0}      # outer end 1.3, body end 1.9
    assert any("short of the body end" in r for r in F.land_short(s)), F.land_short(s)
    s["land"] = {"pad_length": 3.0, "pad_width": 3.7, "pad_center": 1.4}      # inner ends cross the middle
    assert any("meet" in r for r in F.land_short(s)), F.land_short(s)
    assert F.land_short(NRS) == []
    s = dict(copy.deepcopy(NRS), terminal_length={"min": 0.8, "max": 1.2})   # a terminal read: unchanged (no check)
    s["land"] = {"pad_length": 0.6, "pad_width": 3.7, "pad_center": 1.0}
    assert F.land_short(s) == []


def test_the_nrs4018_read_builds_and_passes_its_checks():
    from tests import test_datasheet as TD
    pkg = copy.deepcopy(TD.GOOD["package"])
    keys = D.output_schema()["properties"]["package"]["properties"]["land"]["anyOf"][0]["properties"]
    d = lambda lab: TD.dim(lab, 3.8, 4.0, 4.2, page=1, figure="External Dimensions")
    pkg.update({"name": "L_NRS4018", "package_type": "molded", "pin_count": 2, "exposed_pad": None, "pitch": None,
                "pins_left_right": None, "pins_top_bottom": None, "lead_span": None, "terminal_width": None,
                "body_length": d("L"), "body_width": d("W"),
                "terminal_length": None, "unresolved": ["terminal_length"],
                "land": dict(dict.fromkeys(keys), pad_length=1.2, pad_width=3.7, pad_center=1.4)})
    review = []
    spec, _ = D.to_spec(pkg, "NRS4018T100MDGJ", "ds.pdf", "m", 0, review)
    assert "unresolved" not in spec, spec.get("unresolved")
    assert any("terminal_length" in r and "maker's land" in r for r in review), review
    assert DR.assess({"spec": spec, "needs_review": []})[0] == "ok"
    F.build(spec)


def test_a_bad_land_without_a_terminal_fails_the_reads_sanity_check():
    s = copy.deepcopy(NRS)
    s["land"] = {"pad_length": 0.6, "pad_width": 3.7, "pad_center": 1.0}
    review = []
    D.sanity(s, review)
    assert any("short of the body end" in r for r in review), review
    assert DR.assess({"spec": s, "needs_review": review})[0] != "ok"
    review = []
    D.sanity(copy.deepcopy(NRS), review)
    assert not any("land pattern" in r for r in review), review
