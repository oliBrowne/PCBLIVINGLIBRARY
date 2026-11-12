"""Full review 2026-11-10, group 3: the benchmark must not call a part right on the model's own reading alone, and
must say which right parts the release check held. Each test is a general rule."""
from ecadlib.eval import runner as R, score as S


def _soic8(length, width, centre):
    pads = [[str(i + 1), -centre, -1.905 + 1.27 * i, length, width, 0.0] for i in range(4)]
    pads += [[str(8 - i), centre, -1.905 + 1.27 * i, length, width, 0.0] for i in range(4)]
    return {"pads": pads, "courtyard": []}


KICAD = _soic8(1.95, 0.6, 2.475)
OURS = _soic8(1.55, 0.6, 2.6)                     # the land the read gave: 1.55 x 0.6 at +-2.6
SPEC = {"package_type": "gullwing", "pin_count": 8, "land": {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.6}}


def _fields():
    return S.score_footprint(OURS, KICAD)


def test_the_reads_own_land_alone_does_not_make_a_part_right():
    f = _fields()
    assert not S.verdict(f) and S.only_maker_land(f, S.maker_land(OURS, SPEC))
    assert R.hand_land({}, OURS, SPEC, f, None) is None                       # nothing checked by hand: unverified
    row = {"id": "a", "pass": False, "source": "datasheet", "land_unverified": True}
    assert R.unverified(row) and not R.datasheet_right(row)


def test_a_land_checked_by_hand_makes_it_right_and_one_that_differs_is_a_misread():
    f = _fields()
    same = {"land": {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.6, "why": "p12 land pattern"}}
    other = {"land": {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.9, "why": "p12 land pattern"}}
    assert R.hand_land(same, OURS, SPEC, f, None) is True
    assert R.hand_land(other, OURS, SPEC, f, None) is False


def test_the_outcome_splits_unverified_and_held_and_gives_precision():
    base = {"family": "x", "usd": 0, "tokens_in": 0, "tokens_out": 0, "tried": [], "fields": {}, "errors": []}
    rows = [dict(base, id="a", source="standard", **{"pass": True}),
            dict(base, id="b", source="datasheet", **{"pass": True}, defect_holds=["footprint: name: says 1 pin"]),
            dict(base, id="c", source="datasheet", **{"pass": False}, land_unverified=True),
            dict(base, id="d", source="datasheet", **{"pass": False}, kicad_differs_maker_land=True),
            dict(base, id="e", source="datasheet", **{"pass": False}),
            dict(base, id="f", source="none", **{"pass": False})]
    oc = R.summarize(rows)["outcome"]
    assert oc["right"] == 3 and oc["built_wrong"] == ["e"] and oc["unverified"] == ["c"] and oc["not_built"] == ["f"]
    assert oc["right_held"] == ["b"] and oc["built"] == 5 and oc["precision"] == 0.6


def test_routine_sign_off_is_not_a_defect_hold():
    assert R.defect_holds(["verify: a person checks every datasheet part", "fields: no datasheet link",
                           "footprint: copper: pads 5-8 overlap"]) == ["footprint: copper: pads 5-8 overlap"]


def _qfn_row(width):
    return {"pads": [[str(i + 1), -1.0 + 0.4 * i, 2.0, width, 0.8, 0.0] for i in range(6)], "courtyard": []}


def test_a_fine_pitch_pad_that_halves_the_copper_gap_fails_inside_the_size_tolerance():
    # 0.4 mm pitch, 0.2 mm pads: 0.3 mm pads are only 0.1 mm off (the size tolerance) but leave half the gap
    f = S.score_footprint(_qfn_row(0.3), _qfn_row(0.2))
    assert not f["pads"]["ok"] and "copper gap 0.100 (KiCad 0.200)" in f["pads"]["error"]
    assert S.score_footprint(_qfn_row(0.22), _qfn_row(0.2))["pads"]["ok"]     # 0.18 of 0.20: 90 % kept
    assert S.score_footprint(_qfn_row(0.15), _qfn_row(0.2))["pads"]["ok"]     # a wider gap is never a short


def test_an_inverted_pin_is_a_wrong_name_even_when_the_rest_agree():
    kicad = [("1", "VCC"), ("2", "GND"), ("3", "~{EN}"), ("4", "OUT"), ("5", "IN"), ("6", "FB"), ("7", "SS"), ("8", "PG")]
    ours = [("1", "VCC"), ("2", "GND"), ("3", "EN"), ("4", "OUT"), ("5", "IN"), ("6", "FB"), ("7", "SS"), ("8", "PG")]
    f = S.score_symbol(ours, kicad)["symbol_names"]
    assert not f["ok"] and f["inverted"] == 1 and "active low on one side only" in f["error"]
    for low in ("EN#", "/EN", "nEN", "EN_N", "E̅N̅"):          # any active-low mark is the same sense
        assert S.score_symbol([(n, low if n == "3" else s) for n, s in ours], kicad)["symbol_names"]["ok"], low


def test_the_negative_leg_of_a_pair_is_not_active_low():
    pair = [("1", "CLK_P"), ("2", "CLK_N"), ("3", "GND"), ("4", "VDD"), ("5", "OUT")]
    assert S.score_symbol(pair, [("1", "CLK+"), ("2", "CLK-"), ("3", "GND"), ("4", "VDD"), ("5", "OUT")]
                          ).get("symbol_names", {"inverted": 0}).get("inverted", 0) == 0


def test_altiums_bar_is_active_low_too():
    # AM26LS31CD/CN (offline gate): ours G\ (Altium's bar), KiCad's ~{G}: the same sense
    assert S.active_low("G\\") and S.active_low("R\\E\\S\\E\\T\\") and not S.active_low("A\\B")
