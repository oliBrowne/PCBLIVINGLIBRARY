"""SMD aluminium electrolytics: their terminals are narrow strips under a wide base plate (2026-11-22).
Plain functions: python3 -m tests.run_all test_electrolytic_terminals_1122"""
from ecadlib import datasheet as D

d = lambda nom: {"nom": nom}
LAND = {"pad_length": 4.0, "pad_width": 2.0, "pad_center": 3.55}


def can(desc="Aluminum electrolytic capacitor, SMD, Panasonic FK series", land=LAND, w=0.9, name="CP_Elec_8x10.2"):
    s = {"name": name, "package_type": "molded", "description": desc, "body_length": d(10.0), "body_width": d(8.3),
         "terminal_length": d(3.4), "terminal_width": d(w), "inner_span": d(3.1)}
    if land:
        s["land"] = dict(land)
    return s


def twenty(spec):
    return [r for r in D._two_terminal_terms(spec) if "under 20%" in r]


def test_electrolytic_with_maker_land_passes_the_terminal_width_floor():
    """EEE-FK1V101P: W 0.90 on an 8.3 mm base plate is real (Panasonic FK drawing); the maker's land sizes the pads."""
    assert twenty(can()) == []
    assert twenty(can(desc="SMD aluminium electrolytic capacitor, 6.3 x 7.7 mm", w=0.65)) == []
    assert twenty(can(desc="", name="CP_Elec_6.3x7.7", w=0.65)) == []      # named as KiCad names the can


def test_electrolytic_without_a_land_keeps_the_rule():
    """With no maker land the pads would come from the terminal width: the check stays (UWX1E101MCL1GB)."""
    assert twenty(can(land=None))


def test_inches_read_as_mm_are_still_caught_on_an_electrolytic():
    """0.035 "mm" (inches) on an 8.3 mm can: far under any real strip terminal."""
    assert D._two_terminal_terms(can(w=0.035))
    assert any("implausible" in r for r in D._two_terminal_terms(can(w=0.3)))


def test_other_molded_parts_keep_the_rule():
    """A molded diode / tantalum with a narrow terminal is still a misread, land or not."""
    spec = can(desc="Schottky diode SMA", name="D_SMA")
    assert twenty(spec)
    assert twenty(can(desc="Tantalum capacitor, case D", name="CP_EIA-7343-31"))
