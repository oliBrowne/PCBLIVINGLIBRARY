"""Review of #183-#188 (2026-10-25): a maker's land read without a foot length is still checked against the lead it
must solder, and a short form of a pin name keeps the name's numbers."""
import copy

from ecadlib import footprint_gen as F
from ecadlib.eval import score as S
from tests.test_land_without_foot_1025 import LTV


def test_a_land_short_of_the_lead_tip_is_caught_without_a_foot_length():
    s = copy.deepcopy(LTV)
    s["land"]["pad_center"] = 2.4                     # pads end at 2.9 mm, the lead tip is at 3.4 mm
    assert any("short of the lead tip" in m for m in F.land_short(s)), F.land_short(s)
    s["land"]["pad_center"] = 4.5                     # pads start at 4.0 mm, past the lead tip at 3.6 mm: no lead under them
    assert any("inner end" in m for m in F.land_short(s)), F.land_short(s)


def test_the_makers_land_that_solders_the_lead_still_builds_without_a_foot_length():
    assert F.land_short(copy.deepcopy(LTV)) == []


def test_a_short_form_keeps_the_names_numbers():
    for ours, ref in (("AIN13", "A3"), ("IN10", "I1"), ("CH12", "C1"), ("GPIO12", "G1")):
        assert not S.short_form(ours, ref), (ours, ref)
    for ours, ref in (("AIN3", "A3"), ("Switch Collector", "SwC"), ("GND/HEATSINK", "GND/HS"), ("collector TR2", "C2"),
                      ("RESET", "RST")):
        assert S.short_form(ours, ref), (ours, ref)


def test_a_bank_name_on_another_channels_pin_fails_the_read():
    from ecadlib import datasheet as D
    pins = [{"num": str(i), "name": f"AIN{i - 1}"} for i in range(1, 17)]
    pins[4]["name"] = "AIN13"                         # the table has AIN4 on pin 5
    lines = D.bank_slips(pins)
    assert lines and lines[0].startswith("symbol:") and "AIN4" in lines[0], lines
    from ecadlib import ds_route as DR
    assert DR.FAILED.search(lines[0])


def test_a_name_on_several_pins_with_its_bank_whole_passes():
    from ecadlib import datasheet as D
    pins = [{"num": "1", "name": "LX1"}, {"num": "2", "name": "LX1"}, {"num": "3", "name": "LX2"},
            {"num": "4", "name": "LX2"}, {"num": "5", "name": "IN1"}, {"num": "6", "name": "IN2"}, {"num": "7", "name": "OUT"},
            {"num": "8", "name": "OUT"}, {"num": "9", "name": "GND"}, {"num": "10", "name": "GND"}]
    assert D.bank_slips(pins) == []
    pins = [{"num": str(i), "name": f"IO{i}"} for i in range(1, 9)] + [{"num": "9", "name": "IO3"}]   # IO3 twice, no gap
    assert D.bank_slips(pins) == []
