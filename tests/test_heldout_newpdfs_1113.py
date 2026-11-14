"""Held-out test 2026-11-13, the parts whose datasheets were found later: a D2PAK-5L drawn with its middle lead cut
(KiCad's TO-263-4, not the 5-lead land), op-amp pin names KiCad writes as +, V+ and V-, and supply voltages printed in
brackets ("AVDD (1.8V)", KiCad's AVDD_1V8)."""
import copy

from ecadlib import stdparts as SP
from ecadlib.eval import score as S

AUIPS7081S = {"name": "TO-263-5_TabPin3", "package_type": "gullwing", "pin_count": 5, "pitch": 1.7,
              "body_length": {"min": 9.65, "max": 10.67}, "body_width": {"min": 8.38, "max": 9.65},
              "lead_span": {"min": 14.61, "max": 15.88}, "terminal_length": {"min": 1.78, "max": 2.79},
              "terminal_width": {"min": 0.51, "max": 0.99}, "height_max": 4.83,
              "pin_positions": [{"number": "1", "side": "L", "offset": -3.4}, {"number": "2", "side": "L", "offset": -1.7},
                                {"number": "4", "side": "L", "offset": 1.7}, {"number": "5", "side": "L", "offset": 3.4}],
              "tab": {"number": "3", "side": "R", "offset": 0, "width": {"min": 9.65, "max": 10.67}}}


def test_a_d2pak_drawn_with_its_middle_lead_cut_gets_the_land_without_that_pad():
    name, how = SP.identify_tab(AUIPS7081S)
    assert name == "TO-263-4", how
    assert [p["number"] for p in SP.packages()[name]["pad_table"]["pads"]] == ["1", "2", "3", "4", "5"]


def test_a_d2pak_drawn_with_all_five_leads_keeps_the_five_lead_land():
    s = copy.deepcopy(AUIPS7081S)
    s["pin_positions"].insert(2, {"number": "3", "side": "L", "offset": 0.0})
    assert SP.identify_tab(s)[0] == "TO-263-5_TabPin3"


def test_op_amp_names_kicad_writes_as_signs():
    ref = [("1", "+"), ("2", "V+"), ("5", "-"), ("6", ""), ("11", "V-"), ("12", "+")]
    ours = [("1", "1IN+"), ("2", "VCC"), ("5", "2IN-"), ("6", "2OUT"), ("11", "GND"), ("12", "4IN+")]
    r = S.score_symbol(ours, ref)["symbol_names"]
    assert r["ok"] and r["matched"] == 4, r
    bad = [("1", "1IN-"), ("2", "VCC"), ("5", "2IN-"), ("6", "2OUT"), ("11", "GND"), ("12", "4IN-")]
    assert not S.score_symbol(bad, ref)["symbol_names"]["ok"]           # an inverting input on KiCad's +: wrong
    swapped = [("1", "1IN+"), ("2", "GND"), ("5", "2IN-"), ("6", "2OUT"), ("11", "VCC"), ("12", "4IN+")]
    assert not S.score_symbol(swapped, ref)["symbol_names"]["ok"]       # supply and ground swapped: wrong


def test_a_voltage_in_brackets_is_kicads_voltage_suffix():
    assert S.norm_name("AVDD (1.8V)") == S.norm_name("AVDD_1V8")
    assert S.norm_name("DVDD_I/O (3.3V)") == S.norm_name("DVDD_IO_3V3")
    assert S.norm_name("AVDD (3.3V)") != S.norm_name("AVDD_1V8")


def _lfcsp(ep):
    d = lambda a, b, c: {"min": a, "nom": b, "max": c}
    return {"name": "LFCSP-16", "package_type": "qfn", "pin_count": 16, "pitch": d(None, 0.65, None),
            "body_length": d(3.9, 4.0, 4.1), "body_width": d(3.9, 4.0, 4.1), "terminal_length": d(0.3, 0.4, 0.5),
            "terminal_width": d(0.25, 0.3, 0.35), "height_max": 0.8,
            "exposed_pad": {"length": d(ep - 0.1, ep, ep + 0.1), "width": d(ep - 0.1, ep, ep + 0.1)}}


def test_a_package_kicad_lands_as_its_maker_does_gets_that_makers_outline():
    """AD8231 (held-out 2026-11-13): an LFCSP-16 4x4 with a 2.1 mm pad fits KiCad's generic name, which the table does
    not carry; KiCad's footprint for the part is ADI's own outline, which the table has: an ADI part gets it."""
    name, why = SP.identify(_lfcsp(2.1))
    assert name is None and "not in the checked table" in why, why
    assert SP.identify(_lfcsp(2.1), maker="Analog Devices")[0] == "Analog_LFCSP-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm"
    assert SP.identify(_lfcsp(2.1), maker="Texas Instruments")[0] is None      # another maker's: not ADI's land
    assert SP.UNCHECKED_FIT.search("52 pins, pitch 0.65, body 10 x 10: fits LQFP-52_10x10mm_P0.65mm, but the read's "
                                   "other numbers disagree")
    assert not SP.UNCHECKED_FIT.search("16 pins, pitch 0.65, body 4 x 4: no standard package fits")
    assert not SP.UNCHECKED_FIT.search("package type bga: no JEDEC family in the table")   # BGAs build as before


def test_the_librarian_holds_a_no_land_read_that_disagrees_with_the_package_it_fits():
    """ADUC816BSZ (held-out 2026-11-13): a PQFP-52 whose lead span (13.9) is no LQFP-52's (12): the read fits LQFP-52 by
    pins, pitch and body but not by the rest, and the datasheet prints no land: held, not IPC pads from the body."""
    from tests import test_datasheet as TD, test_holds_misses_1022 as HM, test_ds_route as TR
    e = copy.deepcopy(TD.INCH)                                               # an SOIC-8 read with a 0.3 in span
    e["package"]["lead_span"] = TD.dim("E", 0.29, 0.3, 0.31, "in", 12, "D0008A")
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "SOIC-8"], HM.POINTER])
    from ecadlib import datasheet as D, stdparts as SP2
    why = SP2.identify(D.process(TD._complete(copy.deepcopy(e)), "DSPART1", "x")["spec"])[1]
    if not SP2.UNCHECKED_FIT.search(why):                                    # the fixture must disagree as intended
        raise AssertionError(why)
    a, _ = HM.read_through_librarian(e, pdf)
    assert not (a.get("footprint") or {}).get("file"), a["librarian"]["tried"]
    assert any("prints no recommended land" in t for t in a["librarian"]["tried"]), a["librarian"]["tried"]


def test_a_drawing_that_pulls_the_terminals_back_is_found_on_its_page():
    from ecadlib import ds_route as DR
    texts = ["AS5055A pinout", "Figure 33 Package FN ... L1 0 - 0.15 Note 3: Dimension L1 represents terminal full "
             "back from package edge up to 0.15mm", "Ordering information"]
    assert DR.pullback_pages(texts, [1, 2, 3]) == [2]
    assert DR.pullback_pages(texts, [1, 3]) == []                         # only pages the read was sent
    assert DR.pullback_pages(["terminal pull-back 0.1 max"], [1]) == [1]
    assert DR.pullback_pages(["the output pulls the line back to ground"], [1]) == []


def test_the_librarian_holds_a_no_land_qfn_read_whose_terminals_are_pulled_back():
    from tests import test_datasheet as TD, test_holds_misses_1022 as HM, test_ds_route as TR
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "VQFN-16 3x3 mm"],
                        ["Package outline", "L1 0 0.15", "Dimension L1 represents terminal pull back from package edge"]])
    a, _ = HM.read_through_librarian(TD.GOOD, pdf)
    assert not (a.get("footprint") or {}).get("file"), a["librarian"]["tried"]
    assert any("pulled back from the package edge" in t for t in a["librarian"]["tried"]), a["librarian"]["tried"]
