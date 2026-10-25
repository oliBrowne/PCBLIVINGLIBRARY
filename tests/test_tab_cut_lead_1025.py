"""A tab package whose middle lead is cut (no lead on the board, N.C. in the pin table) and whose maker numbers the
tab on its own: KiCad's checked land gives the tab the cut lead's number, so the read's tab number is no table tab
number and no checked land fitted (BD33FC0FP, benchmark 2026-10-25: ROHM TO252-3 "2 N.C.", FIN GND numbered 4;
KiCad's TO-252-2 has leads 1 and 3 and tab 2). The checked land is built with its tab under the read's number."""
import copy

from ecadlib import stdparts as SP

BD33 = {"name": "TO252-3", "description": "TO252-3 (DPAK), lead 2 cut", "package_type": "gullwing", "pin_count": 3,
        "pitch": 2.3, "body_length": {"min": 6.4, "max": 6.8}, "body_width": {"min": 5.9, "max": 6.3},
        "lead_span": {"min": 9.0, "nom": 9.5, "max": 10.0}, "terminal_length": {"min": 1.3, "max": 1.7},
        "terminal_width": {"min": 0.65, "max": 0.95}, "height_max": 2.5,
        "pin_positions": [{"number": "1", "side": "L", "offset": -2.3}, {"number": "3", "side": "L", "offset": 2.3}],
        "tab": {"number": "4", "side": "R", "offset": 0, "width": {"min": 5.0, "max": 5.3}}}
BD33_PINS = [("1", "VCC"), ("2", "N.C."), ("3", "VO"), ("4", "GND")]


def test_a_tab_numbered_past_a_cut_nc_lead_takes_the_checked_land():
    name, how = SP.identify_tab(BD33, BD33_PINS)
    assert name == "TO-252-2", how
    assert "numbered 4" in how, how
    assert SP.tab_renumber(BD33, name, BD33_PINS) == {"2": "4"}
    assert SP.identify_tab(dict(BD33, name="TO-252-2"), BD33_PINS)[0] == "TO-252-2"   # the name without the cut lead


def test_the_cut_lead_must_be_no_connect_in_the_pin_table():
    assert SP.identify_tab(BD33)[0] is None                              # no pin table: nothing says lead 2 is cut
    pins = [("1", "VCC"), ("2", "GND"), ("3", "VO"), ("4", "GND")]
    assert SP.identify_tab(BD33, pins)[0] is None                         # lead 2 is a pin of its own
    assert SP.identify_tab(BD33, [("1", "VCC"), ("3", "VO"), ("4", "GND")])[0] is None


def test_a_tab_on_a_lead_the_land_has_is_not_renumbered():
    s = copy.deepcopy(BD33)
    s["tab"]["number"] = "3"                                             # the read's tab number is one of the leads
    assert SP.identify_tab(s, BD33_PINS)[0] is None
    assert SP.tab_renumber(s, "TO-252-2", BD33_PINS) == {}


def test_the_tab_lands_with_their_own_numbers_are_not_renumbered():
    from tests.test_stdparts_tab_package_1023 import IRFS4115, AP1117
    assert SP.tab_renumber(IRFS4115, "TO-263-2", [("1", "G"), ("2", "D"), ("3", "S")]) == {}
    assert SP.tab_renumber(AP1117, "SOT-223-3_TabPin2", [("1", "ADJ"), ("2", "VO"), ("3", "VI")]) == {}


def test_the_built_land_numbers_the_tab_as_the_datasheet_does():
    text, _ = SP.build_renumbered("TO-252-2", {"2": "4"})
    pads = {ln.split()[1].strip('"') for ln in text.splitlines() if ln.strip().startswith("(pad ")} - {""}
    assert sorted(pads) == ["1", "3", "4"], pads
    plain, _ = SP.build("TO-252-2")
    assert text.replace('(pad "4"', '(pad "2"') == plain


def test_the_cross_check_counts_the_renumbered_tab_as_a_pad_of_the_land():
    from ecadlib import crosscheck as CC
    second = dict(BD33, pin_positions=None, tab=None, pin_count=4,
                  exposed_pad={"length": {"min": 5.0, "max": 5.3}, "width": {"min": 5.0, "max": 5.3}})
    second = {k: v for k, v in second.items() if v is not None}
    c = CC.against_read(CC.critical(BD33), CC.critical(second), BD33_PINS, BD33_PINS)
    c.update(source="second read", against="second read (m)")
    assert CC._settle_tab([c], BD33, BD33_PINS) == "TO-252-2" and c["status"] == "agreed", c


def _bd33_extraction():
    from tests import test_datasheet as TD
    d = lambda lab, mn, mx: TD.dim(lab, mn, None, mx, page=35, figure="TO252-3 package outline")
    e = copy.deepcopy(TD.GOOD)
    e["package"].update({
        "name": "TO252-3", "description": "TO252-3 (DPAK), lead 2 cut", "package_type": "gullwing", "pin_count": 3,
        "pitch": TD.dim("e", None, 2.3, None, page=35, figure="TO252-3 package outline"),
        "body_length": d("D", 6.4, 6.8), "body_width": d("E", 5.9, 6.3), "lead_span": d("HE", 9.0, 10.0),
        "terminal_length": d("L", 1.3, 1.7), "terminal_width": d("b", 0.65, 0.95), "height_max": d("A", 2.2, 2.5),
        "pins_left_right": None, "pins_top_bottom": None, "exposed_pad": None, "drawing": "TO252-3",
        "pin_positions": [{"number": "1", "side": "L", "offset": -2.3}, {"number": "3", "side": "L", "offset": 2.3}],
        "tab": {"number": "4", "side": "R", "offset": 0, "width": {"min": 5.0, "nom": None, "max": 5.3, "tol": None},
                "length": None}})
    e["pin_table"] = {"page": 3, "figure": "Pin descriptions", "rows": 4}
    e["pins"] = [TD.pin(1, "VCC", "power", "left"), TD.pin(2, "N.C.", "nc", "left"),
                 TD.pin(3, "VO", "power", "right"), TD.pin(4, "GND", "power", "bottom")]
    e["symbol_description"] = "Synthetic 1 A LDO regulator, TO252-3"
    return e


def test_the_librarian_builds_the_checked_land_with_the_tab_under_the_datasheets_number():
    from tests import test_ds_route as TR, test_holds_misses_1022 as HM
    from pathlib import Path
    pdf = TR.pdf_lines([["TESTPART 1 A LDO regulator", "TO252-3"], ["Package outline", "TO252-3"]])
    a, _ = HM.read_through_librarian(_bd33_extraction(), pdf)
    assert (a.get("footprint") or {}).get("file") and a["source"]["footprint"] == "standard:Package_TO_SOT_SMD:TO-252-2", \
        (a.get("source"), a["librarian"]["tried"])
    assert any("numbers the tab 4, and lead 2 is cut" in r for r in a["librarian"]["review"]), a["librarian"]["review"]


def test_the_cut_leads_nc_pin_leaves_the_symbol_so_the_part_is_not_held_for_a_padless_pin():
    from tests import test_ds_route as TR, test_holds_misses_1022 as HM
    pdf = TR.pdf_lines([["TESTPART 1 A LDO regulator", "TO252-3"], ["Package outline", "TO252-3"]])
    a, _ = HM.read_through_librarian(_bd33_extraction(), pdf)
    assert a["source"]["footprint"] == "standard:Package_TO_SOT_SMD:TO-252-2"
    assert not any(str(h).startswith("pins: symbol pins without a pad") for h in a.get("hold_reasons") or []), \
        a.get("hold_reasons")
    assert any("pin 2 (N.C., the cut lead) left out" in r for r in a["librarian"]["review"]), a["librarian"]["review"]


def test_the_scorer_moves_kicads_pin_with_its_renumbered_pad():
    from ecadlib.eval import runner as R, score as S
    case = {"mpn": "BD33FC0FP", "pins": [["1", "VCC"], ["2", "GND"], ["3", "VO"]]}
    errata = {"BD33FC0FP": {"reference": {"renumber": {"2": "4"}}}}
    ref = R.reference_pins(case, errata)
    assert ref == [["1", "VCC"], ["4", "GND"], ["3", "VO"]]
    sc = S.score_symbol([("1", "Vcc"), ("3", "Vo"), ("4", "GND")], ref)
    assert sc["symbol_pins"]["ok"] and sc["symbol_names"]["ok"], sc
    swap = {"X": {"reference": {"renumber": {"2": "3", "3": "2"}}}}    # a swap among KiCad's numbers moves pads only
    assert R.reference_pins({"mpn": "X", "pins": [["1", "A"], ["2", "B"], ["3", "C"]]}, swap) == \
        [["1", "A"], ["2", "B"], ["3", "C"]]
