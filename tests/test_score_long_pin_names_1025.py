"""Pin names our symbol takes from the maker's datasheet in full where KiCad's symbol abbreviates them (full run on main
48dc8ac, 2026-10-24: MC33063MNTXG "Switch Collector" vs SwC, TPS70345 "GND/HEATSINK" vs GND/HS, AP1117-18
"Adj (GND)" vs GND, BCV61 "collector TR2" vs C2). The footprints matched; the parts were counted built wrong on the
names alone. The maker's spelling is the truth, so KiCad's short form of the same name counts; a different name, or a
name KiCad has on another pin, still fails.
python3 -m tests.run_all test_score_long_pin_names_1025"""
from ecadlib.eval import score as S


def names(ours, ref):
    return S.score_symbol(list(enumerate(ours, 1)), list(enumerate(ref, 1)))["symbol_names"]


def test_kicads_short_form_of_the_makers_name_agrees():
    assert names(["Switch Collector", "Switch Emitter", "Timing Capacitor", "GND"], ["SwC", "SwE", "TC", "GND"])["ok"]
    assert names(["GND/HEATSINK", "IN", "OUT"], ["GND/HS", "IN", "OUT"])["ok"]
    assert names(["Adj (GND)", "VOUT", "VIN"], ["GND", "VO", "VI"])["ok"]
    assert names(["emitter TR1", "base TR1", "collector TR2", "base TR2"], ["E1", "B1", "C2", "B2"])["ok"]
    assert names(["collector TR2; base TR1 and TR2", "collector TR1", "emitter TR1", "emitter TR2"],
                 ["C2", "C1", "E1", "E2"])["ok"]                                      # BCV61, as the run read it


def test_another_name_or_a_swap_still_fails():
    assert not names(["Enable", "IN", "OUT"], ["FB", "IN", "OUT"])["ok"]
    assert not names(["IN", "OUT", "GND"], ["OUT", "IN", "GND"])["ok"]                 # swapped
    assert not names(["INHIBIT", "IN", "OUT"], ["NC", "IN", "OUT"])["ok"]
    assert not names(["INHIBIT", "VCC", "OUT"], ["IN", "VCC", "OUT"])["ok"]            # a start is no short form
    assert not names(["Switch Emitter", "Switch Collector", "TC"], ["SwC", "SwE", "TC"])["ok"]   # swapped long names
