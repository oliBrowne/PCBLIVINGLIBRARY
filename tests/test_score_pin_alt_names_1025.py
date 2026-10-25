"""Pin names KiCad writes as a pin's several names joined by "/" (re-score of the 10-25 full run on #183,
2026-10-25: TPS70345's exposed pad 25 is ours "GND", KiCad's "GND/HS"; MC33063MNTXG's pad 9 is ours "EP", KiCad's
"PAD"). One of the pin's own names is that pin, and EP / PAD / EPAD name the same exposed pad. A name that is none of
them, or two names swapped, still fails.
python3 -m tests.run_all test_score_pin_alt_names_1025"""
from ecadlib.eval import score as S


def names(ours, ref):
    return S.score_symbol(list(enumerate(ours, 1)), list(enumerate(ref, 1)))["symbol_names"]


TPS70345 = ["GND/HS", "VIN1", "VIN1", "NC", "~{MR2}", "~{MR1}", "~{EN}", "SEQ", "GND", "VIN2", "VIN2", "GND/HS",
            "GND/HS", "VOUT2", "VOUT2", "VSENSE2/FB2", "NC", "~{RESET}", "PG1", "NC", "VSENSE1/FB1", "VOUT1", "VOUT1",
            "GND/HS", "GND/HS"]


def test_one_of_kicads_slash_names_is_that_pin():
    ours = TPS70345[:24] + ["GND"]                     # the exposed pad named by its ground net alone
    assert names(ours, TPS70345)["ok"]
    assert S.one_of("FB1", "VSENSE1/FB1") and S.one_of("GND", "GND/HS")
    assert names(["IN", "OUT", "FB1", "EN"], ["IN", "OUT", "VSENSE1/FB1", "EN"])["ok"]


def test_a_name_not_among_them_or_swapped_still_fails():
    assert not S.one_of("FB2", "VSENSE1/FB1") and not S.one_of("GND", "GND") and not S.one_of("", "GND/HS")
    assert not names(["IN", "OUT", "FB2", "EN"], ["IN", "OUT", "VSENSE1/FB1", "EN"])["ok"]
    assert not names(["SCL", "SDA", "VCC", "GND"], ["SDA/SDI", "SCL/SCK", "VCC", "GND"])["ok"]      # swapped
    assert not S.one_of("VOUT1", "GND/HS") and not S.one_of("RESET", "/RESET")


def test_exposed_pad_spellings_agree():
    assert names(["IN", "OUT", "GND", "EP"], ["IN", "OUT", "GND", "PAD"])["ok"]
    assert names(["IN", "OUT", "GND", "EPAD"], ["IN", "OUT", "GND", "EP"])["ok"]
    assert names(["IN", "OUT", "GND", "Exposed Pad"], ["IN", "OUT", "GND", "PAD"])["ok"]
    assert not names(["IN", "OUT", "GND", "EP"], ["IN", "OUT", "GND", "FB"])["ok"]


def test_mc33063_figure_names_are_a_checked_erratum():
    # the pinout figure prints the numbers apart from the names, so only a checked erratum ties them (10-25 re-score)
    from ecadlib.eval import runner as R
    ref = [["1", "SwC"], ["2", "SwE"], ["3", "TC"], ["4", "GND"], ["5", "Vfb"], ["6", "Vin"], ["7", "Ipk"],
           ["8", "DC"], ["9", "PAD"]]
    ours = [("1", "Switch Collector"), ("2", "Switch Emitter"), ("3", "Timing Capacitor"), ("4", "GND"),
            ("5", "Comparator Inverting Input"), ("6", "VCC"), ("7", "Ipk Sense"), ("8", "Driver Collector"), ("9", "EP")]
    e = R.load_errata("kicad-v1")["MC33063MNTXG"]
    sc = {"fields": S.score_symbol(ours, ref), "errors": [], "pass": False}
    assert not sc["fields"]["symbol_names"]["ok"]
    assert R.apply_errata(sc, ours, e, ref) == ["symbol_names"]
    swapped = ours[:4] + [("5", "VCC"), ("6", "Comparator Inverting Input")] + ours[6:]
    sc = {"fields": S.score_symbol(swapped, ref), "errors": [], "pass": False}
    assert R.apply_errata(sc, swapped, e, ref) == []
