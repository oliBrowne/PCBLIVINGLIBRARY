"""Review 2026-10-22, footprint area: scoring credit for a read body only with independent evidence, the pin-1 lead
cover check on no-lead packages, pin 1 at the bottom of a single column, and through-hole pads sized per axis."""
import math

from ecadlib import footprint_gen as F, footprint_bench as B
from ecadlib.eval import score as S

SOIC16 = {"name": "SOIC-16_3.9x9.9mm_P1.27mm", "package_type": "gullwing", "pitch": 1.27, "pin_count": 16,
          "body_length": {"min": 9.8, "nom": 9.9, "max": 10.0}, "body_width": {"min": 3.8, "nom": 3.9, "max": 4.0},
          "lead_span": {"min": 5.8, "max": 6.2}, "terminal_length": {"min": 0.4, "max": 1.27},
          "terminal_width": {"min": 0.31, "max": 0.51}, "height_max": 1.75}


def _pads(txt):
    return {p[0]: p[1:] for p in B.numbers(txt)["pads"]}


def test_a_misread_body_is_not_right_by_the_datasheet_when_kicads_name_gives_the_body():
    ref = B.numbers(F.build(SOIC16)[0])
    name = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
    for L in (11.0, 12.0):                                     # body length misread; really 9.9
        bad = dict(SOIC16, body_length={"min": L - 0.1, "nom": L, "max": L + 0.1})
        mine = B.numbers(F.build(bad)[0])
        f = S.score_footprint(mine, ref)
        assert [k for k, v in f.items() if not v["ok"]] == ["courtyard"]
        assert S.datasheet_body(f, mine, bad), "no evidence given: the read body is taken on its own"
        assert S.body_evidence(bad, name, ref) is False
        assert S.datasheet_body(f, mine, bad, name, ref) is None
    assert S.body_evidence(SOIC16, name, ref) is True
    # names that round the body, either way round: TSSOP-16_4.4x5mm, MSOP-8_3x3mm
    ts = {"body_length": {"min": 4.9, "max": 5.1}, "body_width": {"min": 4.3, "max": 4.5}}
    assert S.body_evidence(ts, "Package_SO:TSSOP-16_4.4x5mm_P0.65mm") is True
    assert S.body_evidence(ts, "Package_SO:MSOP-8_3x3mm_P0.65mm") is False
    # the EP size in the name is not the body; a name with no body falls back on KiCad's courtyard, then on nothing
    qfn = {"body_length": {"nom": 4.0}, "body_width": {"nom": 4.0}}
    assert S.body_evidence(qfn, "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.75x2.75mm") is True
    assert S.body_evidence(dict(qfn, body_length={"nom": 2.75}, body_width={"nom": 2.75}),
                           "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.75x2.75mm") is False
    assert S.body_evidence(qfn, "Package_DIP:DIP-16_W7.62mm") is None
    assert S.body_evidence({}, "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm") is None


def _dip16(court):
    pads = [[str(i + 1), 0.0, 2.54 * i, 1.6, 1.6, 0.8] for i in range(8)] + \
           [[str(16 - i), 7.62, 2.54 * i, 1.6, 1.6, 0.8] for i in range(8)]
    return {"pads": pads, "courtyard": court}


def test_with_no_body_in_the_name_kicads_courtyard_bounds_a_body_read_too_small():
    # AM26LS31CN: KiCad's DIP-16_W7.62mm courtyard is around a nominal 20.32 mm body; TI's 18.92-19.69 is still right
    dip = {"body_length": {"min": 18.92, "max": 19.69}, "body_width": {"min": 6.1, "max": 6.6}}
    mine, ref = _dip16([-1.05, -1.05, 8.67, 18.83]), _dip16([-1.06, -1.52, 8.67, 19.3])
    f = S.score_footprint(mine, ref)
    assert S.datasheet_body(f, mine, dip, "Package_DIP:DIP-16_W7.62mm", ref)["body"] == [6.35, 19.305]
    # KiCad's courtyard far past the pads and the largest body read: the body was read too small
    big = _dip16([-1.06, -3.5, 8.67, 21.3])
    assert S.body_evidence(dip, "Package_DIP:DIP-16_W7.62mm", big) is False
    assert S.datasheet_body(S.score_footprint(mine, big), mine, dip, "Package_DIP:DIP-16_W7.62mm", big) is None


def test_a_maker_land_with_a_courtyard_from_a_misread_body_is_not_right():
    land = {"pad_length": 1.95, "pad_width": 0.6, "pad_center": 2.475}
    spec = dict(SOIC16, land=land)
    ref = B.numbers(F.build(SOIC16)[0])
    bad = dict(spec, body_length={"min": 11.9, "nom": 12.0, "max": 12.1})
    mine = B.numbers(F.build(bad)[0])
    f = S.score_footprint(mine, ref)
    ml = S.maker_land(mine, bad)
    assert ml["followed"] and not f["courtyard"]["ok"]
    name = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
    assert not S.only_maker_land(f, ml, S.body_evidence(bad, name, ref))
    good = B.numbers(F.build(spec)[0])                         # the body read right: the land still counts
    f2, ml2 = S.score_footprint(good, ref), S.maker_land(good, spec)
    assert S.only_maker_land(f2, ml2, S.body_evidence(spec, name, ref)) == S.only_maker_land(f2, ml2)


DFN8 = {"name": "WDFN-8_2x2mm_P0.5mm", "package_type": "qfn", "pitch": 0.5, "body_length": {"nom": 2.0},
        "body_width": {"nom": 2.0}, "terminal_length": {"min": 0.25, "max": 0.35}, "terminal_width": {"min": 0.2, "max": 0.3},
        "height_max": 0.8, "pin_count": 8, "pins_left_right": 4, "pins_top_bottom": 0,
        "land": {"pad_length": 0.78, "pad_width": 0.3, "pad_center": 0.76}}


def test_a_no_lead_land_narrower_than_the_lead_is_not_the_makers_land():
    # a QFN / DFN has no lead_span: its toes are at the body's edge, the span the builder uses
    ref = B.numbers(F.build(dict(DFN8, land=None))[0])
    assert S.maker_land(B.numbers(F.build(DFN8)[0]), DFN8)["followed"]
    for land in (dict(DFN8["land"], pin1_width=0.08), dict(DFN8["land"], pad_width=0.08)):
        spec = dict(DFN8, land=land)
        mine = B.numbers(F.build(spec)[0])
        ml = S.maker_land(mine, spec)
        assert not ml["followed"] and ml.get("implausible")
        assert not S.only_maker_land(S.score_footprint(mine, ref), ml)
    # a quad: the top / bottom rows checked against body_width
    qfn = {"name": "QFN-16", "package_type": "qfn", "pitch": 0.5, "body_length": {"nom": 3.0}, "body_width": {"nom": 3.0},
           "terminal_length": {"min": 0.3, "max": 0.5}, "terminal_width": {"min": 0.18, "max": 0.3}, "height_max": 0.8,
           "pin_count": 16, "land": {"pad_length": 0.8, "pad_width": 0.28, "pad_center": 1.45}}
    assert S.maker_land(B.numbers(F.build(qfn)[0]), qfn)["followed"]
    far = dict(qfn, land=dict(qfn["land"], pad_center_y=2.2))  # top / bottom pads past the leads' heels
    assert not S.maker_land(B.numbers(F.build(far)[0]), far)["followed"]



def test_a_dfn_read_with_only_pins_left_right_has_no_top_bottom_row_to_check():
    # (Greptile on #160) rows() gives 0 top / bottom pins for 8 pins, 4 a side: no top / bottom row may fail the land
    spec = {k: v for k, v in DFN8.items() if k != "pins_top_bottom"}
    spec = dict(spec, name="WDFN-8_2x3mm_P0.5mm", body_width={"nom": 3.0})
    assert F.rows(spec) == (4, 0)
    ml = S.maker_land(B.numbers(F.build(spec)[0]), spec)
    assert ml["followed"] and not ml.get("implausible"), ml

def test_a_single_column_with_pin_1_at_the_bottom_is_turned_pin_1_up():
    spec = {"name": "PinHeader_1x04_P2.54mm_Vertical", "package_type": "connector", "pin_count": 4, "pitch": 2.54,
            "pad_table": {"unit": "mm", "y_axis": "down", "view": "top", "origin": "pin1", "footprint_origin": "pin1",
                          "arrays": [{"count": 4, "x": 0, "y": 0, "dx": 0, "dy": -2.54, "first_number": 1,
                                      "number_step": 1, "type": "tht", "drill": 1.0, "w": 1.7, "h": 1.7}]}}
    txt, rep = F.build(spec)
    p = _pads(txt)
    assert [p[n][:2] for n in "1234"] == [[0.0, 0.0], [0.0, 2.54], [0.0, 5.08], [0.0, 7.62]], p
    assert any("turned half a turn" in n for n in rep["notes"])
    ref = {"pads": [[str(i + 1), 0.0, 2.54 * i, 1.7, 1.7, 1.0] for i in range(4)], "courtyard": []}
    f = S.score_footprint(B.numbers(txt), ref)
    assert f["pin1"]["ok"] and f["pads"]["ok"]
    up = dict(spec, pad_table=dict(spec["pad_table"], arrays=[dict(spec["pad_table"]["arrays"][0], dy=2.54)]))
    txt, rep = F.build(up)                                     # pin 1 already on top: left alone
    assert _pads(txt)["2"][:2] == [0.0, 2.54] and not rep["notes"]


def _rows(row_dy, stagger, pitch=2.5, drill=0.9, n=6):
    spec = {"name": "CONN_2x06", "package_type": "connector", "pin_count": 2 * n, "pitch": pitch,
            "pad_table": {"unit": "mm", "y_axis": "down", "view": "top", "origin": "pin1", "footprint_origin": "pin1",
                          "arrays": [{"count": n, "x": 0, "y": 0, "dx": pitch, "dy": 0, "first_number": 1,
                                      "number_step": 2, "type": "tht", "drill": drill},
                                     {"count": n, "x": stagger, "y": row_dy, "dx": pitch, "dy": 0, "first_number": 2,
                                      "number_step": 2, "type": "tht", "drill": drill}]}}
    txt, rep = F.build(spec)
    return _pads(txt), rep


def test_tht_pads_are_held_back_by_the_nearest_pad_on_each_axis():
    # two rows 1.8 mm apart at 2.5 mm pitch: 1.7 mm round pads left 0.1 mm of copper between the rows
    p, rep = _rows(1.8, 0)
    w, h = p["1"][2], p["1"][3]
    assert abs(w - 1.7) < 1e-6 and abs(h - 1.2) < 1e-6, (w, h)   # along: 2.5 - 0.8; across: 1.8 - 0.8 -> 0.9 + 2 x 0.15
    assert 1.8 - h >= 0.6 - 1e-6
    # staggered rows: the diagonal neighbour 1.95 mm away holds the pad to the minimum ring
    p, rep = _rows(1.5, 1.25)
    a, b = p["1"], p["2"]
    d = math.hypot(a[0] - b[0], a[1] - b[1])
    assert max(a[2], a[3]) <= max(d - 0.8, 0.9 + 0.3) + 1e-6 and min(a[2], a[3]) >= 0.9 + 0.3 - 1e-6, a
    # rows far apart: the round 1.9 mm pad (drill + 2 x 0.5) where it fits, held to pitch - 0.8 along the row
    p, _ = _rows(5.0, 0)
    assert abs(p["1"][2] - 1.7) < 1e-6 and abs(p["1"][3] - 1.9) < 1e-6, p["1"]
    # rows so close the ring cannot fit: the minimum ring is kept and the check reports the clearance
    p, rep = _rows(1.25, 0)
    assert abs(p["1"][2] - 1.7) < 1e-6 and abs(p["1"][3] - 1.2) < 1e-6, p["1"]
    assert any(c["level"] == "error" and "apart" in c["message"] for c in rep.get("checks", [])), rep.get("checks")


def test_pads_swapped_across_the_rows_are_never_a_maker_land():
    # (second-pass review) an SOIC-8's pads 2 and 7 sit at one y: an along-the-row check alone would call the swap a
    # land choice. A wrong part is never counted right, so a fine-pitch land moved half the pitch out is not either
    so8 = {"name": "SOIC-8_3.9x4.9mm_P1.27mm", "package_type": "gullwing", "pitch": 1.27, "pin_count": 8,
           "body_length": {"min": 4.8, "max": 5.0}, "body_width": {"min": 3.8, "max": 4.0},
           "lead_span": {"min": 5.8, "max": 6.2}, "terminal_length": {"min": 0.4, "max": 1.27},
           "terminal_width": {"min": 0.31, "max": 0.51}, "height_max": 1.75,
           "land": {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.7}}
    ref = B.numbers(F.build(dict(so8, land=None))[0])
    mine = B.numbers(F.build(so8)[0])
    ml = S.maker_land(mine, so8)
    pos = {p[0]: p[1:3] for p in mine["pads"]}
    swapped = dict(mine, pads=[[p[0], *pos[{"2": "7", "7": "2"}.get(p[0], p[0])], *p[3:]] for p in mine["pads"]])
    f = S.score_footprint(swapped, ref)
    assert f["pads"]["worst"]["row"] < 1e-6 and f["pads"]["worst"]["pos"] > 1
    assert not S.only_maker_land(f, ml)
    q = {"name": "QFN-32_4x4mm_P0.4mm", "package_type": "qfn", "pitch": 0.4, "body_length": {"min": 3.9, "max": 4.1},
         "body_width": {"min": 3.9, "max": 4.1}, "terminal_length": {"min": 0.3, "max": 0.5},
         "terminal_width": {"min": 0.15, "max": 0.25}, "height_max": 0.8, "pin_count": 32}
    qref = B.numbers(F.build(q)[0])
    p1 = [p for p in qref["pads"] if p[0] == "1"][0]
    spec = dict(q, land={"pad_length": p1[3], "pad_width": p1[4], "pad_center": abs(p1[1]) + 0.25})
    qmine = B.numbers(F.build(spec)[0])
    assert not S.only_maker_land(S.score_footprint(qmine, qref), S.maker_land(qmine, spec))


def test_the_hand_downloaded_line_counts_right_as_the_headline_does():
    from ecadlib.eval import runner as R
    row = lambda i, ok, local, **k: {"id": i, "pass": ok, "usd": 0, "tokens_in": 0, "tokens_out": 0, "source": "datasheet",
                                     "family": "SOIC", "fields": {}, "datasheet": {"url": "https://a", **({"local": "X.pdf"} if local else {})}, **k}
    rows = [row("a", True, True), row("b", False, False, kicad_differs_maker_land=True), row("c", True, False)]
    rep = {"meta": {"set": "t", "set_version": 1, "model": "m", "sources": ["datasheet"], "mode": "live", "cases": 3,
                    "wall_s": 1, "wall_s_per_part": 0.3}, "summary": R.summarize(rows), "cases": []}
    md = R.report_md(rep)
    assert "1/1 of them right. Without them: **2/2 (100.0 %)**." in md, md
