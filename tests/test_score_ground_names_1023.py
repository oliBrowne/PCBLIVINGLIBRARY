"""The ground pin a maker calls VSS is KiCad's GND, and the exposed pad KiCad calls GNDPAD is the pad a datasheet
calls GND: one net, two spellings (ENS210 pins 4/5 VSS, LTC4060EDHC pin 17 GND; free re-score 2026-10-23). Other
grounds stay apart: AGND, PGND and DGND are nets of their own, and a ground on a supply pin is still a short."""
from ecadlib.eval import score as S


def _names(mine, ref):
    return S.score_symbol(mine, ref)["symbol_names"]


def test_vss_is_kicad_gnd():
    ref = [("1", "VDD"), ("2", "SCL"), ("3", "SDA"), ("4", "GND"), ("5", "GND")]
    mine = [("1", "VDD"), ("2", "SCL"), ("3", "SDA"), ("4", "VSS"), ("5", "VSS")]
    r = _names(mine, ref)
    assert r["ok"] and r["matched"] == 5, r


def test_a_gnd_exposed_pad_is_kicad_gndpad():
    ref = [("1", "VIN"), ("2", "EN"), ("3", "SW"), ("4", "FB"), ("5", "GNDPAD")]
    mine = [("1", "VIN"), ("2", "EN"), ("3", "SW"), ("4", "FB"), ("5", "GND")]
    r = _names(mine, ref)
    assert r["ok"] and r["matched"] == 5, r


def test_named_grounds_stay_apart():
    for ours, kicads in (("AGND", "GND"), ("PGND", "GND"), ("GND", "DGND"), ("PGND", "AGND"), ("VSS", "AGND")):
        ref = [("1", "A"), ("2", kicads)]
        mine = [("1", "A"), ("2", ours)]
        assert not _names(mine, ref)["ok"], (ours, kicads)


def test_ground_on_a_supply_pin_is_still_wrong():
    ref = [("1", "VDD"), ("2", "GND"), ("3", "A"), ("4", "B"), ("5", "C")]
    mine = [("1", "VSS"), ("2", "VDD"), ("3", "A"), ("4", "B"), ("5", "C")]
    r = _names(mine, ref)
    assert not r["ok"] and r["moved"], r


def test_a_symbol_with_both_gnd_and_vss_keeps_them_apart():
    # Greptile #174: IR4301's KiCad symbol has GND on pin 2 and VSS on pin 7, two connections; a swap still fails
    ref = [("1", "IN"), ("2", "GND"), ("3", "A"), ("4", "B"), ("5", "C"), ("6", "D"), ("7", "VSS"), ("8", "OUT")]
    mine = [("1", "IN"), ("2", "VSS"), ("3", "A"), ("4", "B"), ("5", "C"), ("6", "D"), ("7", "GND"), ("8", "OUT")]
    r = _names(mine, ref)
    assert not r["ok"] and r["matched"] == 6 and r["moved"] == 2, r


def test_one_ground_on_kicads_gnd_and_gndpad_is_one_net():
    # LTC4060EDHC (free re-score 62b4697): KiCad's 16 GND and 17 GNDPAD, ours GND on both; not a swap
    ref = [(str(i), f"P{i}") for i in range(1, 16)] + [("16", "GND"), ("17", "GNDPAD")]
    mine = [(str(i), f"P{i}") for i in range(1, 16)] + [("16", "GND"), ("17", "GND")]
    r = _names(mine, ref)
    assert r["ok"] and r["matched"] == 17 and not r["moved"], r


def test_a_regulators_vin_vout_are_kicads_vi_vo():
    # AP1117-18: the datasheet's GND / VOUT / VIN, KiCad's GND / VO / VI
    r = _names([("1", "GND"), ("2", "VOUT"), ("3", "VIN")], [("1", "GND"), ("2", "VO"), ("3", "VI")])
    assert r["ok"] and r["matched"] == 3, r
    # swapped, they are still on the wrong pins
    r = _names([("1", "GND"), ("2", "VIN"), ("3", "VOUT")], [("1", "GND"), ("2", "VO"), ("3", "VI")])
    assert not r["ok"], r
