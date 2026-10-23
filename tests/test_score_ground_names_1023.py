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
