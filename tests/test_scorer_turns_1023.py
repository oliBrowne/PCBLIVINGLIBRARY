"""The scorer credits a footprint that is the reference turned a quarter turn (a footprint is placed at any angle),
never a mirrored one, and scores a tab numbered on its own as the pin whose net it is on when the reference numbers it
that way (C3D02065E: Wolfspeed numbers the DPAK tab 3, CATHODE like pin 1; KiCad's TO-252-2_TabPin1 numbers it 1).
Benchmark 2026-10-23: C3D02065E was built to Wolfspeed's recommended land, leads down, and failed against KiCad's
TabPin1, leads left. Its reference is now the maker's land (errata, checked by hand)."""
from ecadlib.eval import runner as R, score as S

TABPIN1 = {"pads": [["1", -5.375, -2.3, 3.5, 2.0, 0.0], ["1", 2.025, 0.0, 7.0, 7.0, 0.0], ["2", -5.375, 2.28, 3.5, 2.0, 0.0]],
           "courtyard": [-7.38, -3.75, 5.78, 3.75]}
SOT23_5 = {"pads": [["1", -1.1375, -0.95, 1.325, 0.6, 0.0], ["2", -1.1375, 0.0, 1.325, 0.6, 0.0],
                    ["3", -1.1375, 0.95, 1.325, 0.6, 0.0], ["4", 1.1375, 0.95, 1.325, 0.6, 0.0],
                    ["5", 1.1375, -0.95, 1.325, 0.6, 0.0]], "courtyard": [-2.05, -1.5, 2.05, 1.5]}


def test_a_footprint_turned_a_quarter_turn_is_the_same_footprint():
    for k in (1, 2, 3):
        res = S.score(S._turn(SOT23_5, k), SOT23_5)
        assert res["pass"], (k, res["errors"])
        assert res["fields"]["rotation"]["turns"] == (4 - k) % 4, res["fields"]["rotation"]
    assert "rotation" not in S.score(SOT23_5, SOT23_5)["fields"]


def test_a_mirrored_footprint_still_fails_at_any_angle():
    mir = {"pads": [[p[0], -p[1], p[2], p[3], p[4], p[5]] for p in SOT23_5["pads"]], "courtyard": SOT23_5["courtyard"]}
    for k in range(4):
        res = S.score(S._turn(mir, k), SOT23_5)
        assert not res["pass"] and not res["fields"]["pads"]["ok"], (k, res["errors"])


def test_a_tab_numbered_on_its_own_is_scored_as_the_pin_whose_net_it_is_on():
    ours = {"pads": [["1", -5.375, -2.3, 3.5, 2.0, 0.0], ["3", 2.025, 0.0, 7.0, 7.0, 0.0], ["2", -5.375, 2.28, 3.5, 2.0, 0.0]],
            "courtyard": TABPIN1["courtyard"]}
    kicad_pins = [["1", "K"], ["2", "A"]]
    res = S.score(ours, TABPIN1, [("1", "CATHODE"), ("2", "ANODE"), ("3", "CATHODE")], kicad_pins)
    assert res["pass"], res["errors"]
    assert res["fields"]["renumbered"]["pads"] == {"3": "1"}
    # on the anode's net, the tab is not KiCad's pad 1: it stays 3 and fails
    res = S.score(ours, TABPIN1, [("1", "CATHODE"), ("2", "ANODE"), ("3", "ANODE")], kicad_pins)
    assert not res["pass"] and not res["fields"]["pad_count"]["ok"], res["errors"]
    # with no symbol to say which net the tab is on, nothing is renumbered
    assert not S.score(ours, TABPIN1)["pass"]


def test_c3d02065e_is_scored_against_wolfspeeds_land_turned_and_renumbered_or_not():
    data, er = R.load_set("kicad-v1"), R.load_errata("kicad-v1")
    case = next(c for c in data["cases"] if c["mpn"] == "C3D02065E")
    ref = R.reference_numbers(case, er, data["references"])
    assert R.reference_for(case, er).startswith("Maker:Wolfspeed")
    built = {"pads": [["1", -2.27, 4.175, 1.3, 2.0, 0.0], ["2", 2.27, 4.175, 1.3, 2.0, 0.0], ["3", 0.0, -2.25, 5.6, 5.85, 0.0]],
             "courtyard": [-3.52, -5.43, 3.52, 5.43]}         # the 10-23 run's build, from Wolfspeed's p6 land
    pins = [("1", "CATHODE"), ("2", "ANODE"), ("3", "CATHODE")]
    assert S.score(built, ref, pins, case["pins"])["pass"]
    assert S.score(S._turn(built, 1), ref, pins, case["pins"])["pass"]       # the same land drawn leads-left
    # and against KiCad's TabPin1 the pins now line up (turned, tab 3 = 1); only the maker's sizes differ
    res = S.score(built, data["references"][case["footprint"]], pins, case["pins"])
    assert res["fields"]["pad_count"]["ok"] and res["fields"]["rotation"]["turns"] == 1
    assert not res["fields"]["pads"]["ok"] and "size off" in res["fields"]["pads"]["error"]


def test_a_part_with_two_pads_swapped_is_not_turned_round():
    # AH1806-W (benchmark 2026-10-23): pads 2 and 3 of an SC-59 swapped; turned half round its pads lie closer in sum,
    # but pad 1 then sits on the other side: it is the swap that is wrong, so the part is scored as built
    ref = {"pads": [["1", -1.2, -0.95, 1.4, 0.8, 0.0], ["2", -1.2, 0.95, 1.4, 0.8, 0.0], ["3", 1.2, 0.0, 1.4, 0.8, 0.0]],
           "courtyard": [-2.2, -1.7, 2.2, 1.7]}
    swapped = {"pads": [["1", -1.2, -0.95, 1.4, 0.8, 0.0], ["3", -1.2, 0.95, 1.4, 0.8, 0.0], ["2", 1.2, 0.0, 1.4, 0.8, 0.0]],
               "courtyard": ref["courtyard"]}
    res = S.score(swapped, ref)
    assert not res["pass"] and "rotation" not in res["fields"] and res["fields"]["pin1"]["ok"], res["errors"]
