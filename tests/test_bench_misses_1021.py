"""Live benchmark 2026-10-21 (Opus, zoom run): datasheet reads that were right but built or scored wrong.
Each test is a general rule, shown on the part that exposed it."""
from ecadlib import datasheet as D, footprint_gen as F, footprint_bench as B
from ecadlib.eval import score as S
from tests.fixtures_lite import lite


def _pads(txt):
    return {p[0]: p[1:] for p in B.numbers(txt)["pads"]}


def test_a_basic_lead_span_gets_a_tolerance_so_the_toes_are_not_short():
    # ATSAME70 LQFP100: "16.00 BSC" read as {nom: 16}; IPC-7351B with no span tolerance gave 1.475 mm pads, KiCad
    # (NXP SOT407-1, 15.75-16.25) 1.6 mm. With +-0.2 the pads come out 1.55 mm, inside the 0.1 mm size tolerance
    spec = {"name": "LQFP-100", "package_type": "qfp", "pin_count": 100, "pitch": 0.5,
            "body_length": {"nom": 14.0}, "body_width": {"nom": 14.0}, "lead_span": {"nom": 16.0},
            "lead_span_y": {"nom": 16.0}, "terminal_length": {"min": 0.45, "nom": 0.6, "max": 0.75},
            "terminal_width": {"min": 0.17, "nom": 0.2, "max": 0.27}, "height_max": 1.6}
    review = []
    D.basic_span(spec, review)
    assert spec["lead_span"] == {"nom": 16.0, "min": 15.8, "max": 16.2} and spec["lead_span_y"]["min"] == 15.8
    assert len(review) == 2 and "basic size" in review[0]
    p1 = _pads(F.build(spec)[0])["1"]
    assert abs(p1[2] - 1.55) < 1e-6 and abs(p1[0] + 7.675) < 1e-6


def test_a_span_with_a_tolerance_or_a_package_without_leads_is_left_alone():
    for spec in ({"package_type": "qfp", "lead_span": {"min": 15.8, "max": 16.2}},
                 {"package_type": "qfp", "lead_span": {"nom": 16.0, "tol": 0.25}},
                 {"package_type": "qfn", "lead_span": {"nom": 4.0}}):
        before, review = repr(spec), []
        D.basic_span(spec, review)
        assert repr(spec) == before and not review


def test_the_basic_span_line_does_not_fail_or_resend_the_read():
    from ecadlib import ds_route as DR
    review = []
    D.basic_span({"package_type": "gullwing", "lead_span": {"nom": 6.4}}, review)
    assert DR.assess({"needs_review": review, "spec": {}}) == ("ok", [])


S28B = {"name": "JST_PHD_S28B-PHDSS_2x14_P2.00mm_Horizontal", "package_type": "connector", "pin_count": 28,
        "pitch": 2.0, "pad_table": {"unit": "mm", "y_axis": "up", "view": "top", "origin": "pin1",
                                    "footprint_origin": "pin1", "default_pad": {"type": "tht", "drill": 0.7, "role": "pin"},
                                    "arrays": [{"count": 14, "x": 0, "y": 0, "dx": -2, "dy": 0, "first_number": 1,
                                                "number_step": 2, "type": "tht", "drill": 0.7},
                                               {"count": 14, "x": 0, "y": 2, "dx": -2, "dy": 0, "first_number": 2,
                                                "number_step": 2, "type": "tht", "drill": 0.7}]}}


def test_a_table_with_pin_1_at_the_far_end_is_turned_so_pin_1_is_upper_left():
    # JST draws the side-entry layout with No. 1 at the right end of the lower row; a half turn puts it upper left
    # (KiCad's JST_PHD_S28B-PHDSS: pad 1 at 0,0, pad 2 at 0,2, pad 3 at 2,0); no connection changes
    txt, rep = F.build(dict(S28B))
    p = _pads(txt)
    assert p["1"][:2] == [0, 0] and p["2"][:2] == [0, 2] and p["3"][:2] == [2, 0] and p["28"][:2] == [26, 2]
    assert any("upper left" in n for n in rep["notes"])


def test_a_table_with_pin_1_upper_left_already_is_not_turned():
    spec = dict(S28B, pad_table=dict(S28B["pad_table"], arrays=[
        dict(a, dx=2, y=-a["y"]) for a in S28B["pad_table"]["arrays"]]))
    txt, rep = F.build(spec)
    p = _pads(txt)
    assert p["1"][:2] == [0, 0] and p["3"][:2] == [2, 0] and not any("upper left" in n for n in rep["notes"])


def test_a_pad_on_a_grid_is_held_back_both_ways():
    # two rows at the 2 mm pitch: drill 0.7 + 2 x 0.5 = 1.7 is too wide both along and across the rows (KiCad 1.2 round)
    p = _pads(F.build(dict(S28B))[0])
    assert p["2"][2:4] == [1.2, 1.2] and p["1"][2:4] == [1.2, 1.2]


def test_a_single_row_keeps_its_long_pads():
    spec = dict(S28B, pin_count=14, pad_table=dict(S28B["pad_table"], arrays=[dict(S28B["pad_table"]["arrays"][0], number_step=1)]))
    p = _pads(F.build(spec)[0])
    assert sorted(p["2"][2:4]) == [1.2, 1.7]


def test_nc_pins_kicad_leaves_out_are_not_wrong_pins():
    # LTC7810: the datasheet lists 15 NC pins, KiCad's symbol leaves them out (on 54 other kicad-v1 parts it keeps them)
    ref = [("1", "SENSE1-"), ("2", "DRVSET"), ("3", "INTVCC")]
    mine = ref + [("4", "NC"), ("5", "N.C.")]
    f = S.score_symbol(mine, ref)["symbol_pins"]
    assert f["ok"] and f["nc_not_in_kicad"] == ["4", "5"]
    f = S.score_symbol(ref + [("4", "EN")], ref)["symbol_pins"]
    assert not f["ok"] and "extra ['4']" in f["error"]
    f = S.score_symbol([("1", "SENSE1-"), ("2", "NC")], ref)["symbol_pins"]   # an NC where KiCad has a pin: missing
    assert not f["ok"]


@lite
def test_a_read_with_no_drawing_says_its_dimensions_are_blank(tmp_path):
    # BD33FC0FP: the ROHM PDF has no TO252 drawing, every dimension unresolved; the build's "pin_count 3 is odd"
    # error hid that, so the part scored no_footprint instead of blank_dimension
    from ecadlib import librarian as L
    run = object.__new__(L.Run)
    run.out, run.rules = tmp_path, {}
    spec = {"name": "TO-252-2", "package_type": "gullwing", "pin_count": 3, "height_max": 2.5,
            "unresolved": ["body_length", "body_width", "lead_span", "pitch"]}
    st = {"name": "BD33FC0FP", "footprint": None, "symbol": None, "review": [], "tried": [], "verify": []}
    try:
        run.put_datasheet(st, {"spec": spec, "needs_review": []}, {"url": "u", "where": "w", "part": "BD33FC0FP"}, "fp", "sym")
    except D.DatasheetError as e:
        assert str(e).startswith("no footprint: the datasheet read left body_length, body_width, lead_span, pitch unresolved")
    else:
        raise AssertionError("built a footprint from no dimensions")
