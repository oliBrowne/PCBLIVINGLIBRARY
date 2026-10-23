"""Blank dimensions the read left although the datasheet gives them (live benchmark 2026-10-23, Opus zoom run)."""
from ecadlib import datasheet as D, ds_route as DR
from tests.test_bughunt_dsb import _bga, _proc
from tests.test_datasheet import dim


def _grid(rows, cols):
    return [f"{r}{c}" for r in "ABCDEFGH"[:rows] for c in range(1, cols + 1)]


def _blank_grid(pins, **pkg):
    e = _bga(pins)
    e["package"].update(rows=None, cols=None, pin_count=len(pins), unresolved=["rows", "cols"], **pkg)
    return e


def test_bga_rows_cols_from_a_full_pin_table_of_ball_names():
    # CY14U256LA-BA: TFBGA-48, 8 rows A-H x 6 columns; the read left rows / cols blank, the pin table names every ball
    e = _blank_grid(_grid(8, 6), name="TFBGA-48_6x10mm", pitch=dim("e", None, 0.75, None),
                    body_length=dim("D", 9.9, 10.0, 10.1), body_width=dim("E", 5.9, 6.0, 6.1),
                    ball_diameter=dim("b", 0.25, 0.30, 0.35))
    r = _proc(e)
    assert (r["spec"]["rows"], r["spec"]["cols"]) == (8, 6), r["spec"]
    assert "rows" not in (r["spec"].get("unresolved") or []) and "cols" not in (r["spec"].get("unresolved") or [])
    assert any("rows / cols were left blank: 8 x 6 from the pin table" in x for x in r["needs_review"]), r["needs_review"]
    assert DR.assess(r)[0] == "ok", DR.assess(r)


def test_bga_grid_not_filled_when_the_pin_table_leaves_balls_out():
    # a pin table that misses balls of the grid (NC balls left out, or a depopulated grid): which ones isn't known
    pins = _grid(3, 3)[:-1]
    r = _proc(_blank_grid(pins))
    assert r["spec"].get("rows") is None and r["spec"].get("cols") is None
    assert DR.assess(r)[0] != "ok"


def test_bga_grid_kept_when_the_read_gave_it():
    r = _proc(_bga(_grid(3, 3)))
    assert (r["spec"]["rows"], r["spec"]["cols"]) == (3, 3)
    assert not any("rows / cols were left blank" in x for x in r["needs_review"])


def test_bga_grid_not_filled_from_numbered_pins():
    r = _proc(_blank_grid([str(i) for i in range(1, 10)]))
    assert r["spec"].get("rows") is None


def test_075_mm_is_a_standard_pitch():
    # JEDEC MO-207 / MO-219 fine-pitch BGAs: 0.75 mm (CY14U256LA-BA's retry was failed by this self-check)
    review = []
    D.self_check({"name": "TFBGA-48", "package_type": "bga", "pitch": 0.75}, {"pins": []}, review)
    assert not [x for x in review if "standard pitch" in x], review
