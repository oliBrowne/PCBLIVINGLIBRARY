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


K555 = [("1", "GND"), ("2", "TR"), ("3", "Q"), ("4", "R"), ("5", "CV"), ("6", "THR"), ("7", "DIS"), ("8", "VCC")]


def test_a_short_form_never_credits_another_function_or_a_rail():
    for ours, ref in (("THRESHOLD", "TR"), ("PROG", "PG"), ("RESET", "RT"), ("SENSE", "SS"), ("Input 12", "I1"),
                      ("Source 12", "S1"), ("Gate 21", "G2"), ("GND", "GD"), ("PGND", "PD"), ("VBAT", "VT")):
        assert not S.short_form(ours, ref), (ours, ref)
    assert not (S.short_form("GND (VCC)", "VCC") and S.short_form("GND (VCC)", "GND"))
    assert not S.one_of("NC", "FB/NC") and not S.one_of("DNC", "DNC/VCC")
    swapped = [("1", "GND"), ("2", "THRESHOLD"), ("3", "Q"), ("4", "R"), ("5", "CV"), ("6", "TRIGGER"), ("7", "DIS"),
               ("8", "VCC")]
    assert not S.score_symbol(swapped, K555)["symbol_names"]["ok"]
    ref = [("1", "GD"), ("2", "VCC"), ("3", "IN"), ("4", "GND"), ("5", "OUT")]
    mine = [("1", "GND"), ("2", "VCC"), ("3", "IN"), ("4", "GND"), ("5", "OUT")]
    assert S.score_symbol(mine, ref)["symbol_names"]["matched"] == 4          # our GND on the gate drive pin GD


def _settle(first, second, first_pins, pins):
    from ecadlib import crosscheck as CC
    c = CC.against_read(CC.critical(first), CC.critical(second), first_pins, pins)
    c.update(source="second read", against="second read (m)")
    CC._settle_tab([c], first, first_pins)
    return c


def test_the_reads_own_tab_land_never_sets_aside_a_lead_it_may_have_missed():
    from tests.test_stdparts_tab_package_1023 import C3D02065E as C3D, C3D02065E_PINS as PINS
    drop = lambda d: {k: v for k, v in d.items() if v is not None}
    # one terminal more at another pitch and no pin list from the second read: a lead the first read may have missed
    assert _settle(C3D, drop(dict(C3D, pin_count=4, pitch=1.14)), PINS, [])["status"] == "disagreed"
    # the first read's symbol has no tab pin: the tab pad would have no net
    assert _settle(C3D, drop(dict(C3D, package_type="custom", pin_count=2, tab=None)), PINS[:2], PINS)["status"] == \
        "disagreed"
    # the maker's land with the tab counted apart by the second read still settles (C3D02065E)
    assert _settle(C3D, drop(dict(C3D, package_type="custom", pin_count=2, tab=None)), PINS, PINS[:2])["status"] == \
        "agreed"


def test_supply_and_ground_names_are_no_bank():
    from ecadlib import datasheet as D
    pins = [{"num": f"A{i}", "name": n} for i, n in enumerate(
        ["VCCO_0", "VCCO_0", "VCCO_1", "VCCO_3", "GND1", "GND1", "GND3", "GND4", "NC", "NC"])]
    assert D.bank_slips(pins) == []
