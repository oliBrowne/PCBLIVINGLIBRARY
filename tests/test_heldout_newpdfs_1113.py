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
