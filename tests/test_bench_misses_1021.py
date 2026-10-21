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
        run.put_datasheet(st, {"spec": spec, "needs_review": [], "sidecar": {}}, {"url": "u", "where": "w", "part": "BD33FC0FP"}, "fp", "sym")
    except D.DatasheetError as e:
        assert str(e).startswith("no footprint: the datasheet read left body_length, body_width, lead_span, pitch unresolved")
    else:
        raise AssertionError("built a footprint from no dimensions")


def test_image_only_pages_after_the_drawing_go_with_it():
    # TLC5951DAP: TI's "LAND PATTERN DATA - DAP" page (p51) has no text layer, so nothing scored it and the thermal
    # land was never read; the blank pages right after the drawing's continuation now go with it (two at most)
    from ecadlib import ds_route as R
    texts = ["TLC5951 24-channel LED driver HTSSOP-38", "Pin functions top view pin name I/O", "Electrical", "Timing",
             "Application", "PACKAGE OUTLINE HTSSOP-38 DAP mm MIN NOM MAX A1 E1 D1", "", "", "", "IMPORTANT NOTICE"]
    pages, why = R.pick_pages(texts, 6, "TLC5951DAP", "HTSSOP-38")
    assert {6, 7, 8, 9} <= set(pages) and 10 not in pages, (pages, why)
    assert "drawing (continued, no text layer)" in why[8] and "drawing (continued, no text layer)" in why[9]


def test_a_pin_table_that_runs_over_pages_goes_whole():
    # SAM E70 (1976 pages): the 144-pin table runs over the pages after the figure ("...continued"), past a BGA outline
    # page; only the figure was sent and the read came back with no pins. "64-pin LQFP" is another package (QFP64),
    # not this one: it used to match QFP144 as a bare LQFP and its outline was sent as the drawing
    from ecadlib import ds_route as R
    assert R.package_tokens("6.5.2. 64-pin LQFP Package Outline") == {"QFP64"}
    assert R.package_tokens("5-LEAD SOT") == set() and R.package_tokens("3-pin SOT-23") == {"SOT23"}
    texts = ["SAM E70 family", "Ordering information Q = 144 pins",
             "6. Package and Pinout 6.1.1 144-pin LQFP Package Outline Pin configuration (top view)",
             "6.1.2 144-ball LFBGA Package Outline pin configuration (top view)",
             "Table 6-2. 144-lead pinout ...continued LQFP Pin Signal Name I/O Type",
             "...continued PA1 PA2 PA3 PB0", "...continued PC0 PC1 PD0",
             "6.5.2 64-pin LQFP Package Outline pin configuration (top view)",
             "Electrical characteristics", "62.1 LQFP144 144-lead LQFP package outline dimensions mm MIN MAX A1 E1 D1"]
    pages, why = R.pick_pages(texts, 8, "ATSAME70Q19A-AN", "LQFP-144")
    assert {3, 5, 6, 7, 10} <= set(pages) and 8 not in pages and 4 not in pages, (pages, why)
    assert "pin table (continued)" in why[6]


def _dip16(court):
    pads = [[str(i + 1), 0.0, 2.54 * i, 1.6, 1.6, 0.8] for i in range(8)] + \
           [[str(16 - i), 7.62, 2.54 * i, 1.6, 1.6, 0.8] for i in range(8)]
    return {"pads": pads, "courtyard": court}


DIP_SPEC = {"body_length": {"min": 18.92, "max": 19.69}, "body_width": {"min": 6.1, "max": 6.6}}


def test_a_courtyard_around_the_datasheet_body_is_right_by_the_datasheet():
    # AM26LS31CN: every pad right; KiCad's DIP-16 courtyard is around a 20.32 mm body (all DIP-16s), ours around TI's
    # 18.92-19.69 mm one. Counted right by the datasheet (Oliver 2026-10-21: the maker's datasheet is the truth)
    mine, ref = _dip16([-1.05, -1.05, 8.67, 18.83]), _dip16([-1.06, -1.52, 8.67, 19.3])
    f = S.score_footprint(mine, ref)
    assert not f["courtyard"]["ok"] and all(v["ok"] for k, v in f.items() if k != "courtyard")
    assert S.datasheet_body(f, mine, DIP_SPEC)["body"] == [6.35, 19.305]


def test_a_courtyard_that_cuts_the_body_or_runs_far_past_it_is_still_wrong():
    ref = _dip16([-1.06, -1.52, 8.67, 19.3])
    for court in ([-1.05, -0.9, 8.67, 18.6],          # shorter than the 19.69 mm body
                  [-1.05, -2.5, 8.67, 20.3]):         # 1.5 mm past the body: not the IPC excess
        mine = _dip16(court)
        assert S.datasheet_body(S.score_footprint(mine, ref), mine, DIP_SPEC) is None
    off = dict(_dip16([-1.05, -1.05, 8.67, 18.83]))   # a pad wrong too: a real miss
    off["pads"] = [list(p) for p in off["pads"]]
    off["pads"][3][1] += 0.3
    assert S.datasheet_body(S.score_footprint(off, ref), off, DIP_SPEC) is None
    mine = _dip16([-1.05, -1.05, 8.67, 18.83])       # no body read: nothing to follow
    assert S.datasheet_body(S.score_footprint(mine, ref), mine, {}) is None


def test_the_headline_counts_parts_right_by_the_datasheet():
    from ecadlib.eval import runner as R
    base = {"family": "x", "source": "datasheet", "usd": 0, "tokens_in": 0, "tokens_out": 0, "tried": [], "fields": {}}
    rows = [dict(base, id="a", **{"pass": True}), dict(base, id="b", **{"pass": False, "kicad_differs_maker_land": True}),
            dict(base, id="c", **{"pass": False, "kicad_differs_body": True}), dict(base, id="d", **{"pass": False})]
    o = R.summarize(rows)["overall"]
    assert (o["pass"], o["right"], o["right_rate"]) == (1, 3, 0.75)
    assert [R.datasheet_right(r) for r in rows] == [False, True, True, False]


def test_pads_on_the_wrong_lead_are_not_a_maker_land():
    # AH1806-W (10-21 re-score): pads 2 and 3 swapped (2.4 mm off) were counted as the maker's land because each pad
    # still had the land's size and distance from the centre
    ml = {"followed": True, "pads": ["1", "2", "3"], "ep": []}
    f = lambda pos: {"pads": {"ok": False, "bad_pads": ["2", "3"], "worst": {"pos": pos, "size": 0.3, "drill": 0}},
                     "pitch": {"ok": True, "ours": 0.95, "ref": 0.95}, "pin1": {"ok": True}, "origin": {"ok": True}}
    assert not S.only_maker_land(f(2.4), ml)
    assert S.only_maker_land(f(0.225), ml)


TLC = {"name": "HTSSOP-38-1EP", "package_type": "gullwing", "pin_count": 38, "exposed_pad": {"length": {"min": 5.05, "max": 6.35}},
       "source": {"datasheet": "https://www.ti.com/lit/ds/symlink/tlc5951.pdf"}}


def test_a_ti_powerpad_read_without_its_thermal_land_is_held():
    # TLC5951DAP: TI's land page (p51, no text layer) puts a 12.5 x 5.2 board pad under the 5.7 x 3 exposed pad; the
    # read gave only the package's pad, so the copper came out 6.8 mm short
    assert D.thermal_land_missing(dict(TLC)).startswith("no footprint: TI's PowerPAD thermal land")
    assert D.thermal_land_missing(dict(TLC, land={"ep_across": 5.2, "ep_along": 12.5})) is None
    assert D.thermal_land_missing(dict(TLC, source={"datasheet": "https://www.diodes.com/x.pdf"})) is None
    assert D.thermal_land_missing(dict(TLC, exposed_pad=None)) is None
    assert D.thermal_land_missing(dict(TLC, package_type="qfn")) is None          # QFN lands follow the pad


def test_the_makers_own_thermal_pad_is_part_of_its_land():
    # ISL8117FVEZ: Renesas's land (M16.173A) has a 3 x 3 thermal pad; KiCad's reference is a TI HTSSOP-16 (3.4 x 5)
    mine = {"pads": [[str(i + 1), -2.825 if i < 4 else 2.825, -0.975 + 0.65 * (i % 4), 1.45, 0.35, 0.0] for i in range(8)]
            + [["9", 0.0, 0.0, 3.0, 3.0, 0.0]], "courtyard": []}
    spec = {"land": {"pad_length": 1.45, "pad_width": 0.35, "pad_center": 2.825, "ep_across": 3.0, "ep_along": 3.0}}
    ml = S.maker_land(mine, spec)
    assert ml["followed"] and ml["ep_land"] == ["9"]
    bad = {"pads": {"ok": False, "bad_pads": [str(i) for i in range(1, 10)], "worst": {"pos": 0.05, "size": 2.0}},
           "pitch": {"ok": True, "ref": 0.65}}
    assert S.only_maker_land(bad, ml)
    assert not S.only_maker_land(bad, dict(ml, ep_land=[]))                     # a pad the land doesn't give: a miss
