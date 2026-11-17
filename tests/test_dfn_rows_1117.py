"""A two-row DFN read with the maker's D and E the other way round from ecadlib's (body_width is the side the left
and right rows run along). STM6601 (ST TDFN-12 2 x 3 mm, PC read sample 2026-11-17): the read gave D = 3.0 as
body_length and E = 2.0 as body_width with 6 pins a side, so the rows (2.75 mm at 0.5 pitch) ran past a 2 mm side
and the read failed its checks. When the rows fit only along the other side, the body is turned to them (the read's
own numbers rule the other way out); a square body, or rows that fit neither way, stay as read."""
import copy

from ecadlib import datasheet as D, ds_route as DR, footprint_gen as F

STM = {"name": "DFN-12_2x3mm_P0.5mm", "package_type": "qfn", "pin_count": 12, "pitch": 0.5,
       "body_length": {"min": 2.9, "nom": 3.0, "max": 3.1}, "body_width": {"min": 1.9, "nom": 2.0, "max": 2.1},
       "terminal_width": {"min": 0.15, "nom": 0.2, "max": 0.25}, "terminal_length": {"min": 0.45, "nom": 0.55, "max": 0.65},
       "height_max": 0.8, "pins_left_right": 6, "pins_top_bottom": 0,
       "land": {"pad_length": 0.7, "pad_width": 0.25, "pad_center": 0.8}}


def test_rows_that_fit_only_the_other_side_turn_the_body():
    s, notes = F.orient_dfn(copy.deepcopy(STM))
    assert s["body_width"]["nom"] == 3.0 and s["body_length"]["nom"] == 2.0 and notes, (s, notes)
    s["exposed_pad"] = None
    review = []
    D.sanity(s, review)
    assert not [r for r in review if DR.FAILED.search(r)], review
    F.build(s)


def test_the_exposed_pad_turns_with_the_body():
    s = dict(copy.deepcopy(STM), exposed_pad={"length": {"nom": 2.5}, "width": {"nom": 1.6}})
    t, _ = F.orient_dfn(s)
    assert t["exposed_pad"]["length"] == {"nom": 1.6} and t["exposed_pad"]["width"] == {"nom": 2.5}


def test_a_body_that_fits_either_way_or_neither_stays_as_read():
    ok = dict(copy.deepcopy(STM), body_length=STM["body_width"], body_width=STM["body_length"])
    assert F.orient_dfn(ok) == (ok, [])                                  # already the right way
    sq = dict(copy.deepcopy(STM), body_length={"nom": 3.0}, body_width={"nom": 3.0})
    assert F.orient_dfn(sq)[1] == []
    big = dict(copy.deepcopy(STM), pins_left_right=10, pin_count=20)  # 4.75 mm of rows on a 2 x 3 body: neither way
    assert F.orient_dfn(big)[1] == []
    quad = dict(copy.deepcopy(STM), pins_left_right=3, pins_top_bottom=3)
    assert F.orient_dfn(quad)[1] == []                                 # a QFN with four sides: not this


def test_the_read_is_turned_in_to_spec_with_its_labels():
    from tests import test_datasheet as TD
    pkg = copy.deepcopy(TD.GOOD["package"])
    keys = D.output_schema()["properties"]["package"]["properties"]["land"]["anyOf"][0]["properties"]
    d = lambda lab, mn, nom, mx: TD.dim(lab, mn, nom, mx, page=40, figure="TDFN12 outline")
    pkg.update({"name": "DFN-12_2x3mm_P0.5mm", "pin_count": 12, "exposed_pad": None, "pins_left_right": 6,
                "pins_top_bottom": 0, "body_length": d("D", 2.9, 3.0, 3.1), "body_width": d("E", 1.9, 2.0, 2.1),
                "terminal_width": d("b", 0.15, 0.2, 0.25), "terminal_length": d("L", 0.45, 0.55, 0.65),
                "land": dict(dict.fromkeys(keys), pad_length=0.7, pad_width=0.25, pad_center=0.8)})
    review = []
    spec, prov = D.to_spec(pkg, "STM6601AQ2BDM6F", "ds.pdf", "m", 0, review)
    assert spec["body_width"]["nom"] == 3.0 and prov["body_width"]["label"] == "D", (spec["body_width"], prov["body_width"])
    assert any("turned" in r for r in review) and not [r for r in review if DR.FAILED.search(r)], review
    D.sanity(spec, review)
    assert not [r for r in review if DR.FAILED.search(r)], review
