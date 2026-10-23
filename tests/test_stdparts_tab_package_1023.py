"""A datasheet read of a power package with a tab (DPAK / D2PAK / SOT-223) names the checked standard land when its own
drawing name, lead numbers, tab number, lead positions and body length all fit exactly one table entry
(stdparts.identify_tab). The builder's IPC tab is the exposed tab only, where KiCad's (and the makers') tab land runs
under the body (2026-10-23 holds: IRFS4115 tab 8.1 long at +4.25 vs 9.4 at +1.5). Reads are the 10-23 Opus zoom run's."""
import copy

from ecadlib import stdparts as SP

IRFS4115 = {"name": "TO-263-2", "package_type": "gullwing", "pin_count": 3, "pitch": 2.54,
            "body_length": {"min": 9.65, "max": 10.67}, "body_width": {"min": 8.38, "max": 9.65},
            "lead_span": {"min": 14.61, "max": 15.88}, "terminal_length": {"min": 1.78, "max": 2.79},
            "terminal_width": {"min": 0.51, "max": 0.99}, "height_max": 4.83,
            "pin_positions": [{"number": 1, "side": "L", "offset": -2.54}, {"number": 3, "side": "L", "offset": 2.54}],
            "tab": {"number": 2, "side": "R", "offset": 0, "width": {"min": 9.65, "max": 10.67},
                    "length": {"min": 8.38, "max": 9.65}}}
IPS6011 = {"name": "TO-252-5_TabPin3", "package_type": "gullwing", "pin_count": 5, "pitch": 1.14,
           "body_length": {"min": 6.35, "max": 6.73}, "body_width": {"min": 5.97, "max": 6.22},
           "lead_span": {"min": 9.4, "max": 10.41}, "terminal_length": {"min": 1.4, "max": 1.78},
           "terminal_width": {"min": 0.51, "max": 0.89}, "height_max": 2.39,
           "pin_positions": [{"number": "1", "side": "L", "offset": -2.28}, {"number": "2", "side": "L", "offset": -1.14},
                             {"number": "4", "side": "L", "offset": 1.14}, {"number": "5", "side": "L", "offset": 2.28}],
           "tab": {"number": "3", "side": "R", "offset": 0, "width": {"min": 4.95, "max": 5.46},
                   "length": {"min": 0.89, "max": 1.27}}}
AP1117 = {"name": "SOT-223-3_TabPin2", "package_type": "gullwing", "pin_count": 3, "pitch": 2.3,
          "body_length": {"min": 6.45, "nom": 6.5, "max": 6.55}, "body_width": {"min": 3.45, "nom": 3.5, "max": 3.55},
          "lead_span": {"min": 6.9, "nom": 7.0, "max": 7.1}, "terminal_length": {"min": 0.85, "nom": 0.95, "max": 1.05},
          "terminal_width": {"min": 0.6, "nom": 0.7, "max": 0.8}, "height_max": 1.65,
          "pin_positions": [{"number": 1, "side": "L", "offset": -2.3}, {"number": 2, "side": "L", "offset": 0},
                            {"number": 3, "side": "L", "offset": 2.3}],
          "tab": {"number": 2, "side": "R", "offset": 0, "width": {"min": 2.9, "nom": 3.0, "max": 3.1}}}


def test_a_d2pak_read_names_kicads_to263_2():
    assert SP.identify_tab(IRFS4115)[0] == "TO-263-2"


def test_a_sot223_regulator_read_names_the_tab_pin_2_land():
    assert SP.identify_tab(AP1117)[0] == "SOT-223-3_TabPin2"


def test_the_lead_under_the_tab_number_may_be_left_out_when_the_name_counts_it():
    # TO-252-5: five leads by its name, the middle one numbered 3 like the tab; the read listed only 1 2 4 5
    assert SP.identify_tab(IPS6011)[0] == "TO-252-5_TabPin3"


def test_a_missing_lead_the_name_does_not_count_is_not_filled_in():
    s = copy.deepcopy(IPS6011)
    s["name"] = "DPAK"                                  # no lead count: 4 leads + tab 3 fits no entry
    assert SP.identify_tab(s)[0] is None


def test_another_tab_number_names_nothing():
    s = copy.deepcopy(IRFS4115)
    s["tab"]["number"] = 4
    assert SP.identify_tab(s)[0] is None


def test_lead_positions_must_fit():
    s = copy.deepcopy(IRFS4115)
    s["pin_positions"] = [{"number": 1, "side": "L", "offset": -1.7}, {"number": 3, "side": "L", "offset": 1.7}]
    assert SP.identify_tab(s)[0] is None


def test_the_body_must_fit():
    s = copy.deepcopy(AP1117)
    s["body_length"] = {"min": 4.4, "max": 4.6}
    assert SP.identify_tab(s)[0] is None


def test_no_tab_or_no_family_name_names_nothing():
    s = copy.deepcopy(IRFS4115)
    del s["tab"]
    assert SP.identify_tab(s)[0] is None
    s = copy.deepcopy(IRFS4115)
    s["name"] = "Power package"
    assert SP.identify_tab(s)[0] is None
    s = copy.deepcopy(IRFS4115)
    del s["pin_positions"]
    assert SP.identify_tab(s)[0] is None


def test_leads_on_the_tab_side_name_nothing():
    s = copy.deepcopy(AP1117)
    s["pin_positions"][2]["side"] = "R"
    assert SP.identify_tab(s)[0] is None


# ---------------------------------------------------------------- through the librarian
def _d2pak_extraction():
    from tests import test_datasheet as TD
    d = lambda lab, mn, mx: TD.dim(lab, mn, None, mx, page=9, figure="D2Pak package outline")
    e = copy.deepcopy(TD.GOOD)
    e["package"].update({
        "name": "TO-263-2", "description": "D2Pak (TO-263AB), 2 leads + tab", "package_type": "gullwing",
        "pin_count": 3, "pitch": TD.dim("e", None, 2.54, None, page=9, figure="D2Pak package outline"),
        "body_length": d("E", 9.65, 10.67), "body_width": d("D1", 8.38, 9.65), "lead_span": d("H", 14.61, 15.88),
        "terminal_length": d("L", 1.78, 2.79), "terminal_width": d("b", 0.51, 0.99), "height_max": d("A", 4.06, 4.83),
        "pins_left_right": None, "pins_top_bottom": None, "exposed_pad": None, "drawing": "D2Pak",
        "pin_positions": [{"number": "1", "side": "L", "offset": -2.54}, {"number": "3", "side": "L", "offset": 2.54}],
        "tab": {"number": "2", "side": "R", "offset": 0, "width": {"min": 9.65, "nom": None, "max": 10.67, "tol": None},
                "length": {"min": 8.38, "nom": None, "max": 9.65, "tol": None}}})
    e["pin_table"] = {"page": 1, "figure": "Pin assignment", "rows": 3}
    e["pins"] = [TD.pin(1, "G"), TD.pin(2, "D", "passive", "right"), TD.pin(3, "S", "passive")]
    e["symbol_description"] = "Synthetic N-channel MOSFET, D2Pak"
    return e


def test_the_librarian_builds_the_checked_tab_land_for_a_d2pak_read_and_says_so():
    from tests import test_ds_route as TR, test_holds_misses_1022 as HM
    pdf = TR.pdf_lines([["TESTPART N-channel MOSFET", "D2Pak"], ["Package outline", "D2Pak (TO-263AB)"]])
    a, _ = HM.read_through_librarian(_d2pak_extraction(), pdf)
    assert (a.get("footprint") or {}).get("file") and a["source"]["footprint"] == "standard:Package_TO_SOT_SMD:TO-263-2", \
        (a.get("source"), a["librarian"]["tried"])
    rv = [r for r in a["librarian"]["review"] if r.startswith("standard land:")]
    assert rv and "a tab land runs under the body" in rv[0], a["librarian"]["review"]


def test_a_tab_read_with_the_makers_own_land_keeps_it():
    from tests import test_datasheet as TD, test_ds_route as TR, test_holds_misses_1022 as HM
    from ecadlib import datasheet as D
    e = _d2pak_extraction()
    keys = D.output_schema()["properties"]["package"]["properties"]["land"]["anyOf"][0]["properties"]
    e["package"]["land"] = dict(dict.fromkeys(keys), pad_length=4.6, pad_width=1.1, pad_center=7.65)
    pdf = TR.pdf_lines([["TESTPART N-channel MOSFET", "D2Pak"], ["Package outline", "D2Pak (TO-263AB)"]])
    a, _ = HM.read_through_librarian(e, pdf)
    assert a["source"]["footprint"].startswith("datasheet:"), (a.get("source"), a["librarian"]["tried"])


# ---------------------------------------------------------------- the cross-check: tab counting
def _second(first, **over):
    s = {k: v for k, v in dict(first, **over).items() if v is not None}
    return s


def _settle(first, second, first_pins, pins):
    from ecadlib import crosscheck as CC
    c = CC.against_read(CC.critical(first), CC.critical(second), first_pins, pins)
    c.update(source="second read", against="second read (m)")
    return CC._settle_tab([c], first), c


IRFS_PINS = [("1", "G"), ("2", "D"), ("3", "S")]


def test_a_tab_under_a_number_of_its_own_in_the_second_read_is_built_from_the_tab_land():
    # IRFS4115 10-21: second read pins 1 GATE 2 DRAIN 3 SOURCE 4 DRAIN, the tab as an exposed pad, D x E printed turned
    second = _second(IRFS4115, tab=None, pin_positions=None, body_length=IRFS4115["body_width"],
                     body_width=IRFS4115["body_length"],
                     exposed_pad={"length": {"min": 6.86, "nom": 6.86, "max": 6.86}, "width": {"min": 6.22, "nom": 6.22, "max": 6.22}})
    name, c = _settle(IRFS4115, second, IRFS_PINS, IRFS_PINS + [("4", "D")])
    assert name == "TO-263-2" and c["status"] == "agreed" and c["settled"] == "standard table", c
    assert "how the tab is counted" in c["tie_breaks"][-1]


def test_one_pin_more_with_the_tab_counted_is_built_from_the_tab_land():
    second = _second(IRFS4115, pin_count=2)
    name, c = _settle(IRFS4115, second, IRFS_PINS, IRFS_PINS)
    assert name == "TO-263-2", c


def test_any_other_difference_still_holds():
    from ecadlib import crosscheck as CC
    for over, pins in (({"pitch": 1.7}, IRFS_PINS),                                   # a size
                       ({}, [("1", "S"), ("2", "D"), ("3", "G")]),                    # names
                       ({}, IRFS_PINS[:2]),                                           # a lead the land has: 3
                       ({"pin_count": 5}, IRFS_PINS)):                                # two pins more
        name, c = _settle(IRFS4115, _second(IRFS4115, **over), IRFS_PINS, pins)
        assert name is None and c["status"] == "disagreed", (over, pins, c)


def test_a_read_that_names_no_tab_land_still_holds():
    s = copy.deepcopy(IRFS4115)
    s["name"] = "Power package"
    name, c = _settle(s, _second(s, pin_count=2), IRFS_PINS, IRFS_PINS)
    assert name is None and c["status"] == "disagreed"


def test_a_second_read_that_calls_the_tab_package_custom_is_built_from_the_tab_land():
    # IPS6011RPBF 10-23: second read custom, pins 1-5 and 6 Vcc (the tab again)
    pins = [("1", "Gnd"), ("2", "In"), ("3", "Vcc"), ("4", "Dg"), ("5", "Out")]
    second = _second(IPS6011, package_type="custom", tab=None, pin_positions=None, lead_span=None)
    name, c = _settle(IPS6011, second, pins, pins + [("6", "Vcc")])
    assert name == "TO-252-5_TabPin3" and c["status"] == "agreed", c
    # but another type (qfn) picks another builder: still held
    name, c = _settle(IPS6011, _second(IPS6011, package_type="qfn"), pins, pins + [("6", "Vcc")])
    assert name is None


# ---------------------------------------------------------------- a tab on one lead's net
C3D = {"name": "TO-263-2", "package_type": "gullwing", "pin_count": 3, "pitch": 2.54,
       "body_length": {"min": 10.04, "max": 10.28}, "body_width": {"min": 8.89, "max": 9.4},
       "lead_span": {"min": 14.73, "max": 15.75}, "terminal_length": {"min": 2.29, "max": 2.79},
       "terminal_width": {"min": 0.71, "max": 0.94}, "height_max": 4.57, "polarized": True,
       "pin_positions": [{"number": 1, "side": "L", "offset": -2.54}, {"number": 2, "side": "L", "offset": 2.54}],
       "tab": {"number": 3, "side": "R", "offset": 0, "width": {"min": 7.535, "max": 8.425},
               "length": {"min": 6.48, "max": 6.88}}}
C3D_PINS = [("1", "CATHODE"), ("2", "ANODE"), ("3", "CATHODE")]


def test_a_tab_on_one_leads_net_takes_that_leads_number():
    # C3D08060G (10-23): Wolfspeed numbers the tab 3, on the cathode like lead 1; KiCad's TO-263-2_TabPin1 numbers it 1
    assert SP.tab_joins(C3D, C3D_PINS) == "1"
    name, how = SP.identify_tab(C3D, C3D_PINS)
    assert name == "TO-263-2_TabPin1" and "numbered 1" in how, how
    assert SP.identify_tab(C3D)[0] is None                       # without the pin table: no land has tab 3


def test_a_tab_on_two_leads_nets_or_its_own_net_joins_nothing():
    assert SP.tab_joins(C3D, [("1", "CATHODE"), ("2", "CATHODE"), ("3", "CATHODE")]) is None
    assert SP.tab_joins(C3D, [("1", "CATHODE"), ("2", "ANODE"), ("3", "SUBSTRATE")]) is None
    assert SP.identify_tab(C3D, [("1", "CATHODE"), ("2", "ANODE"), ("3", "SUBSTRATE")])[0] is None


def test_the_cross_check_settles_a_joined_tab_called_custom_with_one_pin_less():
    # C3D08060G 10-23: second read custom, pin_count 2, the tab as an exposed pad
    second = _second(C3D, package_type="custom", pin_count=2, tab=None, pin_positions=None, lead_span=None)
    name, c = _settle(C3D, second, C3D_PINS, C3D_PINS)
    assert name is None                                          # (_settle passes no pins)
    from ecadlib import crosscheck as CC
    c = CC.against_read(CC.critical(C3D), CC.critical(second), C3D_PINS, C3D_PINS)
    assert CC._settle_tab([c], C3D, C3D_PINS) == "TO-263-2_TabPin1" and c["status"] == "agreed", c


def test_the_librarian_drops_the_joined_tabs_own_pin():
    from ecadlib import librarian as L
    st = {"review": []}
    res = {"spec": C3D, "symbol": {"pins": [{"num": n, "name": s} for n, s in C3D_PINS]}}
    out = L.drop_joined_tab(res, SP.spec("TO-263-2_TabPin1"), st)
    assert [q["num"] for q in out["symbol"]["pins"]] == ["1", "2"] and "numbers the tab 3" in st["review"][0]
    assert res["symbol"]["pins"][2]["num"] == "3"                # the read itself is left as it was
    assert L.drop_joined_tab(res, SP.spec("TO-263-2"), {"review": []}) is res


# ---------------------------------------------------------------- LFPAK (Nexperia SOT669 / SOT1210)
PSMN5R2 = {"name": "LFPAK56", "package_type": "gullwing", "pin_count": 5, "pitch": 1.27,
           "body_length": {"min": 4.8, "max": 5.0}, "body_width": {"min": 3.8, "max": 4.1},
           "lead_span": {"min": 5.8, "max": 6.2}, "terminal_length": {"min": 0.4, "max": 0.85},
           "terminal_width": {"min": 0.35, "max": 0.5}, "height_max": 1.2,
           "pin_positions": [{"number": 1, "side": "L", "offset": -1.905}, {"number": 2, "side": "L", "offset": -0.635},
                             {"number": 3, "side": "L", "offset": 0.635}, {"number": 4, "side": "L", "offset": 1.905}],
           "tab": {"number": 5, "side": "R", "offset": 0, "width": {"min": 3.62, "max": 4.41},
                   "length": {"min": 0.8, "max": 1.3}}}


def test_an_lfpak56_read_names_kicads_lfpak56_and_its_drain_tab():
    # PSMN5R2-60YL 10-23: IPC built the drain tab 2.05 x 4.5 at +2.425; KiCad's mounting base runs under the body
    assert SP.identify_tab(PSMN5R2)[0] == "LFPAK56"
    assert SP.identify_tab(dict(PSMN5R2, name="SOT669"))[0] == "LFPAK56"
    s = copy.deepcopy(PSMN5R2)
    s["pin_positions"][0]["offset"] = -1.7                       # a lead elsewhere: no land
    assert SP.identify_tab(s)[0] is None
    assert SP.identify_tab(dict(PSMN5R2, name="LFPAK33"))[0] is None   # LFPAK33's leads are at 0.65 pitch


def test_the_lfpak_lands_match_kicads():
    # the table entries are KiCad's hand-made LFPAK56 / LFPAK33 (pad 5 the custom pad's outline): built, every pad
    # where KiCad's is (eval reference boxes)
    from ecadlib import footprint_bench as B
    from ecadlib.eval import score as S
    import json
    from pathlib import Path
    refs = json.loads((Path(SP.__file__).parent / "eval" / "sets" / "kicad-v1.json").read_text())["references"]
    for n in ("LFPAK56", "LFPAK33"):
        mine = B.numbers(SP.build(n)[0])
        ref = refs[f"Package_TO_SOT_SMD:{n}"]
        res = S.score_footprint(mine, ref)
        assert res["pads"]["ok"] and res["courtyard"]["ok"], (n, res)
