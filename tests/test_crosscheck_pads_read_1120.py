"""A footprint whose pads the read placed itself (a pad table, or a land block with the signal pads' size and centre)
is confirmed only by a check of the pads, not by a second read that agrees on the body, pin count and pin names.
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


def _run(monkeypatch, spec, extra=()):
    agreed = {"source": "second read", "status": "agreed", "compared": ["package_type", "pin_count", "body_length",
              "body_width"], "agreed": ["package_type", "pin_count", "body_length", "body_width"], "disagreed": [],
              "not_compared": []}
    monkeypatch.setattr(CC, "second_read", lambda *a, **k: dict(agreed))
    monkeypatch.setattr(CC, "standard_matches", lambda *a, **k: [])
    monkeypatch.setattr(CC, "held_anyway", lambda *a, **k: None)
    monkeypatch.setattr(CC, "land_printed", lambda job, res: extra[0] if extra else None)
    return CC.run(Reader(), {"part": "X", "data": None}, {"spec": spec, "symbol": {"pins": []}}, "claude-haiku-5-5")


def test_pads_read_spots_pad_tables_and_maker_lands():
    assert CC.pads_read(SIT)
    assert CC.pads_read({"package_type": "chip", "land": {"pad_length": 1, "pad_width": 1, "pad_center": 1}})
    assert not CC.pads_read({"package_type": "gullwing", "land": {"ep_across": 3.4, "ep_along": 5.0}})   # thermal only
    assert not CC.pads_read({"package_type": "gullwing", "pitch": 1.27, "pin_count": 8})


@lite
def test_a_second_read_of_the_body_does_not_confirm_read_pads(monkeypatch):
    out = _run(monkeypatch, SIT)
    assert out["status"] != "agreed", out                  # held for a person's look, not released
    gull = {"name": "SOIC-8", "package_type": "gullwing", "pin_count": 8, "pitch": 1.27,
            "body_length": 4.9, "body_width": 3.9, "lead_span": 6.0}
    assert _run(monkeypatch, gull)["status"] == "agreed"   # IPC pads from the agreed numbers: as before


@lite
def test_the_land_drawings_printed_numbers_still_confirm_two_pads(monkeypatch):
    two = {"name": "L", "package_type": "chip", "pin_count": 2, "body_length": 4.0, "body_width": 4.0,
           "land": {"pad_length": 1.5, "pad_width": 3.4, "pad_center": 1.6}}
    land = {"source": "pdf land text", "status": "agreed", "compared": ["land"], "agreed": ["land"], "disagreed": [],
            "not_compared": []}
    assert _run(monkeypatch, two, [land])["status"] == "agreed"
    assert _run(monkeypatch, two)["status"] != "agreed"

