"""Regression tests for the 2026-10-18 review of footprint and symbol generation (stdparts name parsing,
symbol_layout power_side "left").
Run:  python3 -m tests.test_review_1018_footprints"""
from ecadlib import stdparts as SP, symbol_layout as SL


def test_chip_size_metric_code_that_disagrees_is_a_conflict():
    # '0603 (1005)': the metric 1005 is an 0402, so the text names two sizes; it used to build the 0603 land
    for text in ("0603 (1005)", "0402 (1608)", "0805 / 3216"):
        r = SP.resolve([text], "C")
        assert r["footprint"] is None and "disagree" in r["fp_how"], (text, r)
    # agreeing pairs, metric markers and plain codes are unchanged; a bare metric code alone is still not read
    assert SP.resolve(["0402 (1005)"], "R")["footprint"] == "R_0402_1005Metric"
    assert SP.resolve(["1005 (0402)"], "R")["footprint"] == "R_0402_1005Metric"
    assert SP.resolve(["0603/1608"], "R")["footprint"] == "R_0603_1608Metric"
    assert SP.resolve(["10k 0603 1% 0.1W"], "R")["footprint"] == "R_0603_1608Metric"
    assert SP.chip_size("1608") is None and SP.chip_size("1005M") == "0402" and SP.chip_size("0402M") == "01005"


def test_land_variant_names_are_not_the_plain_land():
    # KiCad's _HandSolder / _Handsoldering lands have longer pads and _ThermalVias adds vias: the plain table entry
    # is a different footprint, so nothing is built
    for text, kind in (("R_0603_1608Metric_Pad0.98x0.95mm_HandSolder", "R"), ("C_0402_1005Metric_HandSolder", "C"),
                       ("SOT-23-6_Handsoldering", "U"), ("D_SOD-123_HandSolder", "D"),
                       ("TSSOP-16_4.4x5mm_P0.65mm_HandSolder", "U"),
                       ("Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm_ThermalVias", "U"),
                       ("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm_PullBack", "U")):
        r = SP.resolve([text], kind)
        assert r["footprint"] is None and "land variant" in r["fp_how"], (text, r)
    # nor through the MPN family fallback (MMBT3904 comes in SOT-23)
    r = SP.resolve(["SOT-23_HandSoldering"], "Q", "MMBT3904")
    assert r["footprint"] is None and r["symbol"] is None, r
    # the plain names still resolve
    assert SP.resolve(["R_0603_1608Metric"], "R")["footprint"] == "R_0603_1608Metric"
    assert SP.resolve(["QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"], "U")["footprint"] == "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"
    assert SP.resolve(["SOT-23"], "Q", "MMBT3904")["footprint"] == "SOT-23"


def test_named_package_with_an_exposed_pad_is_refused():
    # 'SOT-23-5-EP' used to give SOT-23-5, which has no pad under the body
    for text in ("SOT-23-5-EP", "SOT-23-6 EP", "SOT-23-5 exposed pad"):
        r = SP.resolve([text], "U")
        assert r["footprint"] is None and "exposed pad" in r["fp_how"], (text, r)
    assert SP.resolve(["SOT-23 EP"], "Q", "MMBT3904")["footprint"] is None
    assert SP.resolve(["SOT-23-5"], "U")["footprint"] == "SOT-23-5"
    assert SP.resolve(["SOT-23-6L"], "U")["footprint"] == "SOT-23-6"


def test_soic_w_with_a_narrow_body_is_refused():
    # 'SOIC-8W 3.9x4.9mm' contradicts itself (W = wide body); it used to give the narrow SOIC-8
    name, why = SP.ic_package("SOIC-8W 3.9x4.9mm")
    assert name is None, (name, why)
    assert SP.ic_package("SOIC-16W 7.5x10.3mm")[0] == "SOIC-16W_7.5x10.3mm_P1.27mm"
    assert SP.ic_package("SOIC-8 3.9x4.9mm")[0] == "SOIC-8_3.9x4.9mm_P1.27mm"


def _ys(sym, names):
    return {pr[1]["name"]: (pr[2], pr[4]) for pr in sym["prims"] if pr[0] == "pin" and pr[1]["name"] in names}


def test_power_side_left_ground_alone_goes_to_the_bottom():
    # power_side "left": ground at the bottom of the left side, also when ground is all the left side holds (it used to
    # sit at the top-left, next to nothing, with eight outputs on the right)
    pins = [{"num": "1", "name": "GND", "type": "power_in"}] + \
        [{"num": str(i + 2), "name": f"Q{i}", "type": "output"} for i in range(8)]
    sym = SL.build("X", pins, rules={"symbols": {"power_side": "left"}})
    body = [pr for pr in sym["prims"] if pr[0] == "rect"][0]
    q_low = min(pr[4] for pr in sym["prims"] if pr[0] == "pin" and pr[1]["name"].startswith("Q"))
    side, y = _ys(sym, {"GND"})["GND"]
    assert side == "left" and y == q_low and y > body[4], (side, y, q_low, body)
    assert not SL.check(sym), SL.check(sym)
    # with power too: power top-left, ground bottom-left (unchanged)
    pins2 = [{"num": "10", "name": "VDD", "type": "power_in"}] + pins
    sym2 = SL.build("X", pins2, rules={"symbols": {"power_side": "left"}})
    at = _ys(sym2, {"VDD", "GND"})
    assert at["VDD"][0] == at["GND"][0] == "left" and at["VDD"][1] > at["GND"][1], at
    # power_side "top" unchanged: ground on the bottom edge
    assert _ys(SL.build("X", pins, rules={"symbols": {"power_side": "top"}}), {"GND"})["GND"][0] == "bottom"


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_review_1018_footprints"]))
