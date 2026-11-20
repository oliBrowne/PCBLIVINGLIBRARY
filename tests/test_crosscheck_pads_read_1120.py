"""A footprint whose pads the read placed itself (a custom or connector read's pad table) is confirmed only by a check of the pads, not by a second read that agrees on the body, pin count and pin names.
SiT1533 on Haiku 5.5 (2026-11-20): pad 3 read 0.2 wide at x 0.9 where the drawing has 0.4 at 0.8; the second read agreed
on everything it compared and the part was released.   python3 -m tests.test_crosscheck_pads_read_1120"""
from ecadlib import crosscheck as CC
from tests.fixtures_lite import lite

SIT = {"name": "SIT1533_2.0x1.2mm_SPL", "package_type": "custom", "pin_count": 4,
       "body_length": {"min": 1.95, "nom": 2.0, "max": 2.05}, "body_width": {"min": 1.15, "nom": 1.2, "max": 1.25},
       "pad_table": {"unit": "mm", "pads": [
           {"number": "1", "x": -0.8, "y": 0, "w": 0.4, "h": 1.4, "type": "smd"},
           {"number": "3", "x": 0.9, "y": 0, "w": 0.2, "h": 1.4, "type": "smd"},
           {"number": "4", "x": 0.0, "y": 0.45, "w": 0.3, "h": 0.5, "type": "smd"},
           {"number": "2", "x": -0.1, "y": -0.45, "w": 0.3, "h": 0.5, "type": "smd"}]}}


class Reader:
    cfg = {"cross_check": "auto"}


AGREED = ["package_type", "pin_count", "body_length", "body_width"]


def _run(monkeypatch, spec, extra=(), agreed=AGREED, settled=None):
    second = {"source": "second read", "against": "a second read", "status": "agreed", "compared": list(agreed),
              "agreed": list(agreed), "disagreed": [], "not_compared": []}
    monkeypatch.setattr(CC, "second_read", lambda *a, **k: dict(second))
    monkeypatch.setattr(CC, "standard_matches", lambda *a, **k: [])
    monkeypatch.setattr(CC, "held_anyway", lambda *a, **k: None)
    monkeypatch.setattr(CC, "land_printed", lambda job, res: extra[0] if extra else None)
    monkeypatch.setattr(CC, "_settle_turn", lambda checks: settled)
    return CC.run(Reader(), {"part": "X", "data": None}, {"spec": spec, "symbol": {"pins": []}}, "claude-haiku-5-5")


def test_pads_read_spots_pad_tables_only():
    assert CC.pads_read(SIT)
    assert CC.pads_read(dict(SIT, package_type="connector"))
    assert not CC.pads_read(dict(SIT, package_type="qfn"))         # a table the builder does not use
    # a land block's three numbers size a row the package places, checked by the build's copper check
    assert not CC.pads_read({"package_type": "chip", "land": {"pad_length": 1, "pad_width": 1, "pad_center": 1}})
    assert not CC.pads_read({"package_type": "gullwing", "land": {"tab_length": 2, "tab_width": 3, "tab_center": 4}})
    assert not CC.pads_read({"package_type": "gullwing", "land": {"ep_across": 3.4, "ep_along": 5.0}})   # thermal only
    assert not CC.pads_read({"package_type": "gullwing", "pitch": 1.27, "pin_count": 8})


@lite
def test_a_second_read_of_the_body_does_not_confirm_read_pads(monkeypatch):
    out = _run(monkeypatch, SIT)
    assert out["status"] == "unchecked", out                # held for a person's look, not released
    assert "no source checked them" in CC.summary_line(out)  # and the review says why
    gull = {"name": "SOIC-8", "package_type": "gullwing", "pin_count": 8, "pitch": 1.27,
            "body_length": 4.9, "body_width": 3.9, "lead_span": 6.0}
    assert _run(monkeypatch, gull)["status"] == "agreed"   # IPC pads from the agreed numbers: as before


@lite
def test_agreed_pin_sides_do_not_confirm_read_pads_either(monkeypatch):
    sides = dict(SIT, pin_sides={"left": ["1", "2"], "right": ["3", "4"]})
    out = _run(monkeypatch, sides, agreed=AGREED + ["pin_positions", "pin_names"])
    assert out["status"] == "unchecked", out


@lite
def test_a_pad_table_that_fits_the_package_is_checked_by_the_second_read(monkeypatch):
    qfn = {"name": "DFN-8-1EP", "package_type": "custom", "pin_count": 9, "pitch": 0.5,
           "body_length": 3.0, "body_width": 3.0, "ep_length": 1.6, "ep_width": 2.4,
           "pad_table": {"unit": "mm", "pads": [
               *({"number": str(i + 1), "x": -1.45, "y": 0.75 - 0.5 * i, "w": 0.8, "h": 0.3} for i in range(4)),
               *({"number": str(i + 5), "x": 1.45, "y": -0.75 + 0.5 * i, "w": 0.8, "h": 0.3} for i in range(4)),
               {"number": "9", "x": 0, "y": 0, "w": 1.6, "h": 2.4}]}}
    monkeypatch.setattr(CC, "table_fits", lambda spec: True)
    assert _run(monkeypatch, qfn, agreed=AGREED + list(CC.TABLE_CHECKED_BY))["status"] == "agreed"
    assert _run(monkeypatch, qfn)["status"] == "unchecked"     # the body alone: not the pads


@lite
def test_a_footprint_built_from_a_standard_table_is_not_held_for_the_reads_pads(monkeypatch):
    out = _run(monkeypatch, SIT, settled="SOT-23-3")
    assert out["status"] == "agreed" and out["standard"] == "SOT-23-3" and "pads" not in out, out
    # blanks the read left do not exempt it: with no standard package that fits, its own pad table is built
    assert _run(monkeypatch, dict(SIT, unresolved=["height_max", "lead_span"]))["status"] == "unchecked"


@lite
def test_the_land_drawings_printed_numbers_confirm_a_pad_table(monkeypatch):
    land = {"source": "pdf land text", "against": "the land drawing's text", "status": "agreed", "compared": ["land"],
            "agreed": ["land"], "disagreed": [], "not_compared": []}
    assert _run(monkeypatch, SIT, [land])["status"] == "agreed"
    assert _run(monkeypatch, SIT, [dict(land, same_pages="web search (x)")])["status"] != "agreed"
