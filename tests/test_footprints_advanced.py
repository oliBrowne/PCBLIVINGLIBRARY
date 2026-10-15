"""Harder footprints: BGA/LGA/CSP grids, pin headers, connectors and odd shapes from a datasheet pad table.

  1. against KiCad's official footprints: every pad (centre, size, drill) and the courtyard, for 57 parts (the first 20: 5 BGAs,
     7 pin headers, 7 JST/Molex/USB-C connectors, SOT-23-6), from the numbers-only fixture of the KiCad files
     (tests/fixtures/kicad_bench.json, made by `ecadlib footprint-bench --save-fixture`).
  2. footprint_check: the BGA rules (A1 marks, grid, land/pitch, row letters) and the connector rules (mounting
     pads, zig-zag numbering, stacked USB-C pads) catch what they should and pass KiCad's own footprints.
  3. the pad table: frames (unit, y axis, bottom view, origin) give the same copper; the consistency checks find
     mils typed as mm, a wrong pin count, a misread coordinate, a broken symmetry and a wrong pin-1 origin.
  4. odd shapes: polygon pads, split paste, thermal vias, slots, keep-outs, union courtyard.
  5. every new spec validates, builds, passes the checker, converts to Altium and gets a STEP model.

Run:  python3 -m tests.test_footprints_advanced"""
import copy, json, math, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_adv as A, footprint_bench as B, footprint_check as C, footprint_gen as F, \
    sexpr, step_gen as S

ROOT = Path(__file__).resolve().parent.parent
ADV = ROOT / "ecadlib" / "package_specs" / "advanced"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kicad_bench.json"
SPEC = lambda n: json.loads((ADV / f"{n}.json").read_text())
USB_C = "USB_C_Receptacle_HRO_TYPE-C-31-M-12"


def build(spec, density=None):
    F.validate(spec)
    txt, rep = F.build(spec, density)
    sexpr.parse(txt)
    return txt, rep


def rules(txt, **kw):
    return {(lvl, rule) for lvl, rule, _ in C.check(txt, **kw)}


def msgs(txt, **kw):
    return [m for _, _, m in C.check(txt, **kw)]


# ---------------------------------------------------------------- 1. KiCad benchmark
def test_matches_kicad_official_footprints():
    """Every pad of 57 footprints within 0.01 mm of KiCad's (position, size, drill): 13 BGAs (rows up to AF, A1
    depopulated, datasheet lands), 17 pin headers, 10 connectors, 2 QFN/DFN with thermal vias, 15 IPC chip / SOT / SO /
    QFP / QFN parts; courtyards too, except the four dual-row SMD pin headers whose KiCad courtyard also wraps the
    bent lead tails (not in our spec)."""
    results, s = B.run(fixture=json.loads(FIXTURE.read_text()))
    assert s["footprints"] == 57 and s["unavailable"] == 0, s
    assert s["pads_ok"] == s["pads"] == 2942, s
    assert s["footprints_all_pads_ok"] == 57 and s["check_clean"] == 57, s
    crt_miss = sorted(r["name"] for r in results if not r["courtyard_ok"])
    assert crt_miss == ["PinHeader_2x05_P1.00mm_Vertical_SMD", "PinHeader_2x05_P1.27mm_Vertical_SMD", "PinHeader_2x05_P2.00mm_Vertical_SMD",
                        "PinHeader_2x05_P2.54mm_Vertical_SMD"], crt_miss
    for r in results:                                   # ours is never bigger than KiCad's where they differ
        if not r["courtyard_ok"]:
            assert all(abs(a) <= abs(b) + 1e-9 for a, b in zip(r["courtyard_ours"], r["courtyard_kicad"])), r


def test_bench_compare_counts_missing_and_moved_pads():
    ref = {"pads": [["1", 0, 0, 1, 1, 0], ["2", 2.54, 0, 1, 1, 0], ["MP", 5, 0, 2, 2, 0]], "courtyard": [-1, -1, 6, 1]}
    mine = {"pads": [["1", 0, 0, 1, 1, 0], ["2", 2.5, 0, 1, 1, 0]], "courtyard": [-1, -1, 6, 1.05]}
    r = B.compare(mine, ref)
    assert r["pads"] == 3 and r["pads_ok"] == 1 and r["missing_in_ours"] == ["MP"] and not r["all_pads_ok"], r
    assert r["worst_pos"] == 0.04 and not r["courtyard_ok"], r


def test_bench_cli_offline_from_fixture():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "res.json"
        rc = B.main(["--offline", "--cache", td, "--fixture", str(FIXTURE), "--json", str(out)])
        assert rc == 0, rc
        assert json.loads(out.read_text())["summary"]["pads_ok"] == 2942
        assert B.main(["--offline", "--cache", td]) == 1           # nothing cached, no fixture: nothing to compare


# ---------------------------------------------------------------- BGA
def test_jedec_row_letters():
    rows = [A.row_name(i) for i in range(24)]
    assert rows[:20] == list("ABCDEFGHJKLMNPRTUVWY") and rows[20:] == ["AA", "AB", "AC", "AD"], rows
    assert not set("IOQSXZ") & set("".join(rows[:20]))
    assert A.row_name(8, jedec=False) == "I"
    assert A.ball_index("AB3") == (21, 2) and A.ball_index("J1") == (8, 0) and A.ball_index("I1") is None


def test_ipc_bga_land_from_ball_size():
    """IPC-7351B collapsing balls: -25 % from 0.55 mm, -20 % down to 0.25 mm, -15 % below; rounded to 0.05 / 0.01."""
    land = {b: A.ipc_bga_land(b)[0] for b in (0.75, 0.65, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35, 0.3, 0.25, 0.2, 0.15)}
    assert land == {0.75: 0.55, 0.65: 0.5, 0.6: 0.45, 0.55: 0.4, 0.5: 0.4, 0.45: 0.35, 0.4: 0.3, 0.35: 0.3, 0.3: 0.25,
                    0.25: 0.2, 0.2: 0.17, 0.15: 0.13}, land
    assert A.ipc_bga_land(0.5, "non_collapsing")[0] == 0.53 and A.ipc_bga_land(0.3, "lga")[0] == 0.3


def test_bga_depopulated_ranges_and_ball_map_agree():
    _, rep = build(SPEC("BGA-144_7.0x7.0mm_Layout13x13_P0.5mm_Ball0.3mm_Pad0.25mm_NSMD"))
    names = {p["num"] for p in rep["pads"]}
    assert len(names) == 144 and not {"E5", "G7", "J9", "E9", "J5"} & names and {"D4", "K10", "E4", "J10"} <= names
    spec = SPEC("UFBGA-132_7x7mm_Layout12x12_P0.5mm")
    _, rep = build(spec)
    missing = {f"{r}{c}" for r in "ABCDEFGHJKLM" for c in range(1, 13)} - {p["num"] for p in rep["pads"]}
    assert missing == {"E5", "E6", "E7", "E8", "F5", "F8", "G5", "G8", "H5", "H6", "H7", "H8"}, missing
    alt = copy.deepcopy(spec)                                 # the same ring as depopulated ranges
    del alt["ball_map"]
    alt["depopulated"] = ["E5:E8", "H5:H8", "F5:G5", "F8:G8"]
    assert [p["num"] for p in build(alt)[1]["pads"]] == [p["num"] for p in rep["pads"]]
    assert rep["land"] == 0.27 and rep["land_rule"] == "datasheet land"
    bad = copy.deepcopy(spec)
    bad["pin_count"] = 144
    try:
        F.build(bad)
        raise AssertionError("pin count mismatch not caught")
    except ValueError as e:
        assert "leaves 132 balls" in str(e), e
    bad = copy.deepcopy(alt)
    bad["depopulated"] = ["I5"]                               # JEDEC has no row I
    try:
        F.build(bad)
        raise AssertionError("row I accepted")
    except ValueError as e:
        assert "not a ball" in str(e), e


def test_bga_nsmd_and_smd_lands():
    spec = SPEC("BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD")
    txt, rep = build(spec)
    assert rep["copper"] == 0.4 and '(size 0.4 0.4)' in txt and "(solder_mask_margin 0.05)" in txt
    spec["land_style"] = "SMD"
    txt, rep = build(spec)
    assert rep["copper"] == 0.5 and "(solder_mask_margin -0.05)" in txt and "(solder_paste_margin -0.05)" in txt
    assert not [c for c in rep["checks"] if c["level"] == "error"], rep["checks"]
    assert rep["courtyard"] == (13.0, 13.0)                   # body + 1.0 mm (KLC / IPC density B)
    assert build(spec, "A")[1]["courtyard"] == (15.0, 15.0) and build(spec, "C")[1]["courtyard"] == (12.0, 12.0)


def test_bga_mistakes_are_explained():
    spec = SPEC("BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD")
    for change, words in (({"pitch": 1.27}, "doesn't fit"), ({"ball_diameter": {"nom": 19.7}}, "mils")):
        bad = dict(spec, **change)
        try:
            F.build(bad)
            raise AssertionError(change)
        except ValueError as e:
            assert words in str(e), (change, e)
    import jsonschema
    bad = {k: v for k, v in spec.items() if k != "ball_diameter"}
    try:
        F.validate(bad)
        raise AssertionError("bga without a ball or land size validated")
    except jsonschema.ValidationError:
        pass


def test_bga_checker_rules():
    txt, rep = build(SPEC("BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"))
    assert C.check(txt) == [], C.check(txt)
    lines = txt.splitlines()
    no_mark = "\n".join(l for l in lines if not ("F.SilkS" in l and "fill solid" in l))
    assert any("not marked on the silkscreen" in m for m in msgs(no_mark))
    fab = next(l for l in lines if "F.Fab" in l and "fp_poly" in l)
    square = "\n".join(l if l != fab else '  (fp_rect (start -5.5 -5.5) (end 5.5 5.5) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))'
                       for l in lines)
    assert any("not marked on the fab" in m for m in msgs(square))
    off = txt.replace('(pad "C3" smd circle (at -2.5 -2.5)', '(pad "C3" smd circle (at -2.45 -2.5)')
    assert ("error", "bga") in rules(off) and any("C3" in m and "off the" in m for m in msgs(off))
    fat = txt.replace("(size 0.4 0.4)", "(size 0.7 0.7)")
    assert any("70%" in m and "no room to route" in m for m in msgs(fat)), msgs(fat)
    lettered = txt.replace('(pad "H', '(pad "I')
    assert any("JEDEC skips" in m for m in msgs(lettered))
    swapped = txt.replace('(pad "A1" ', '(pad "Q9" ').replace('(pad "K10" ', '(pad "A1" ')
    assert any("A1" in m and "not the top-left" in m for m in msgs(swapped)), msgs(swapped)


def test_checker_passes_kicad_connectors():
    """KiCad's own USB-C (stacked GND/VBUS pads) is not an error any more; a non-USB overlap still is."""
    txt = (Path(__file__).parent / "fixtures" / "kicad" / f"{USB_C}.kicad_mod").read_text()
    lv = [l for l, _, _ in C.check(txt)]
    assert "error" not in lv and lv.count("warning") == 4, C.check(txt)
    other = txt.replace('"B12"', '"B13"')
    assert ("error", "copper") in rules(other)
    assert ("error", "copper") not in rules(other, same_net=[["A1", "B13"], ["A4", "B9"], ["A9", "B4"], ["A12", "B1"]])


# ---------------------------------------------------------------- connectors / pin headers
def test_connector_rules_mounting_pads_and_zigzag():
    txt, _ = build(SPEC("JST_PH_S4B-PH-SM4-TB_1x04-1MP_P2.00mm_Horizontal"))
    assert C.check(txt) == [], C.check(txt)                 # MP pads: further left than pin 1, used twice: fine
    renum = txt.replace('(pad "MP" ', '(pad "7" ', 1)
    assert any("not the top-left" in m for m in msgs(renum)), msgs(renum)
    txt, _ = build(SPEC("PinHeader_2x05_P2.54mm_Vertical"))
    assert C.check(txt) == []                               # 1 3 5 7 9 / 2 4 6 8 10
    swapped = txt.replace('(pad "4" ', '(pad "X" ').replace('(pad "5" ', '(pad "4" ').replace('(pad "X" ', '(pad "5" ')
    assert any("doubles back" in m or "clockwise" in m for m in msgs(swapped)), msgs(swapped)


def test_pin_header_through_hole_and_smd():
    _, rep = build(SPEC("PinHeader_2x05_P2.54mm_Vertical"))
    p = {q["num"]: q for q in rep["pads"]}
    assert (p["1"]["x"], p["1"]["y"], p["2"]["x"], p["3"]["y"]) == (0, 0, 2.54, 2.54)
    assert p["1"]["shape"] == "rect" and p["2"]["shape"] == "circle" and p["1"]["drill"] == (1.0, 1.0)
    txt, rep = build(SPEC("PinHeader_1x05_P2.54mm_Vertical_SMD_Pin1Left"))
    xs = [q["x"] for q in rep["pads"]]
    assert xs == [-1.655, 1.655, -1.655, 1.655, -1.655] and "(attr smd)" in txt
    try:
        F.build(dict(SPEC("PinHeader_2x05_P2.54mm_Vertical"), pin_count=12))
        raise AssertionError
    except ValueError as e:
        assert "2 x 5 = 10" in str(e)


def test_tht_pads_sized_from_the_drill():
    """KiCad's connector rule: drill + 1.0 mm, no wider than pitch - 0.8 across the row; pin 1 rounded square."""
    _, rep = build(SPEC("JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical"))
    p = rep["pads"]
    assert [(q["w"], q["h"]) for q in p] == [(1.7, 1.95)] * 4
    assert p[0]["shape"] == "roundrect" and {q["shape"] for q in p[1:]} == {"oval"}
    txt, _ = build(SPEC("JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical"))
    assert "(attr through_hole)" in txt and "(drill 0.95)" in txt


def test_usb_c_slots_holes_and_same_net():
    txt, rep = build(SPEC(USB_C))
    assert "(drill oval 0.6 1.7)" in txt and txt.count("np_thru_hole") == 2 and "(attr smd)" in txt
    assert [c for c in rep["checks"] if c["level"] == "error"] == [], rep["checks"]
    assert {p["num"] for p in rep["pads"] if p["role"] == "pin"} == {f"A{i}" for i in (1, 4, 5, 6, 7, 8, 9, 12)} | \
        {f"B{i}" for i in (1, 4, 5, 6, 7, 8, 9, 12)}
    assert rep["table_checks"] == [], rep["table_checks"]   # symmetric, regular pitch, 16 pins
    assert rep["courtyard_bbox"] == (-5.32, -5.27, 5.32, 4.15)
    spec = SPEC(USB_C)
    del spec["same_net"]
    try:
        F.build(spec)
        raise AssertionError("stacked pads without same_net accepted")
    except ValueError as e:
        assert "overlap" in str(e) and "same_net" in str(e)


# ---------------------------------------------------------------- the pad table
def _table_spec(table, **kw):
    return dict({"name": "T", "package_type": "connector", "pin_count": 4, "pad_table": table}, **kw)


def _pads(spec):
    _, rep = build(spec)
    return sorted((p["num"], round(p["x"], 4), round(p["y"], 4), round(p["w"], 4), round(p["h"], 4)) for p in rep["pads"])


def test_pad_table_frames_give_the_same_copper():
    """One JST SH land four ways: top view y up in mm; y down in mil; bottom view; origin at pin 1 with center_at."""
    base = _pads(SPEC("JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"))
    mil = 1 / 0.0254
    yd = {"unit": "mil", "y_axis": "down", "arrays": [{"count": 4, "x": -1.5 * mil, "y": -2.0 * mil, "dx": mil, "w": 0.6 * mil, "h": 1.55 * mil}],
          "pads": [{"number": "MP", "x": s * 2.8 * mil, "y": 1.875 * mil, "w": 1.2 * mil, "h": 1.8 * mil} for s in (-1, 1)]}
    bottom = {"unit": "mm", "y_axis": "up", "view": "bottom",
              "arrays": [{"count": 4, "x": 1.5, "y": 2.0, "dx": -1.0, "w": 0.6, "h": 1.55}],
              "pads": [{"number": "MP", "x": s * 2.8, "y": -1.875, "w": 1.2, "h": 1.8} for s in (-1, 1)]}
    pin1 = {"unit": "mm", "y_axis": "up", "origin": "pin1", "center_at": {"x": 1.5, "y": -2.0},
            "arrays": [{"count": 4, "x": 0, "y": 0, "dx": 1.0, "w": 0.6, "h": 1.55}],
            "pads": [{"number": "MP", "x": 1.5 + s * 2.8, "y": -3.875, "w": 1.2, "h": 1.8} for s in (-1, 1)]}
    for t in (yd, bottom, pin1):
        assert _pads(_table_spec(t)) == base, (t, _pads(_table_spec(t)), base)


def test_pad_table_consistency_checks():
    good = {"unit": "mm", "y_axis": "up", "origin": "pin1",
            "arrays": [{"count": 4, "x": 0, "y": 0, "dx": 2.5, "type": "tht", "drill": 0.95}]}
    spec = _table_spec(good, pitch=2.5)
    assert [c for c in A.check_pad_table(spec) if c["level"] != "info"] == []

    def problems(table=None, **kw):
        return A.check_pad_table(_table_spec(table or good, **dict({"pitch": 2.5}, **kw)))
    lv = lambda ps, rule: {p["level"] for p in ps if p["rule"] == rule}
    assert lv(problems(pin_count=5), "count") == {"error"}
    mm_as_mil = dict(good, arrays=[dict(good["arrays"][0], dx=98.4, drill=37.4)])
    assert any("mils" in p["message"] for p in problems(mm_as_mil) if p["level"] == "error")
    in_mil = dict(good, unit="mil")                            # 2.5 and 0.95 "mil": really mm
    assert lv(problems(in_mil), "units") == {"warning"}
    misread = dict(good, arrays=[], pads=[{"number": str(i + 1), "x": x, "y": 0, "type": "tht", "drill": 0.95}
                                          for i, x in enumerate((0, 2.5, 4.95, 7.5))])
    ps = problems(misread)
    assert any(p["rule"] == "pitch" and "pads 2 and 3" in p["message"] for p in ps), ps
    assert any(p["rule"] == "symmetry" and "pad 3" in p["message"] for p in ps), ps
    shifted = dict(good, arrays=[dict(good["arrays"][0], x=0.5)])
    assert any(p["rule"] == "pin1" and p["level"] == "error" for p in problems(shifted))
    clash = dict(good, pads=[{"number": "5", "x": 1.0, "y": 0, "w": 1.0, "h": 1.0}])
    assert lv(problems(clash, pin_count=5), "overlap") == {"error"}
    gap = dict(good, arrays=[], pads=[{"number": n, "x": 2.5 * i, "y": 0, "type": "tht", "drill": 0.95}
                                      for i, n in enumerate(("1", "2", "3", "5"))])
    assert any(p["rule"] == "numbers" and "skip 4" in p["message"] for p in problems(gap))
    thin = dict(good, arrays=[dict(good["arrays"][0], w=1.0, h=1.0)])
    assert lv(problems(thin), "drill") == {"warning"}
    try:                                                      # errors stop the build, with the reason
        F.build(_table_spec(shifted, pitch=2.5))
        raise AssertionError
    except ValueError as e:
        assert "origin is pin 1" in str(e), e


# ---------------------------------------------------------------- odd shapes
def test_union_outline():
    L = A.union_outline([(0, 0, 4, 1), (0, 0, 1, 3)])
    assert sorted(L) == sorted([(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]), L
    assert A.union_outline([(0, 0, 1, 1), (2, 0, 3, 1)]) is None                    # two islands
    ring = A.union_outline([(0, 0, 3, 1), (0, 2, 3, 3), (0, 0, 1, 3), (2, 0, 3, 3)])
    assert sorted(ring) == [(0, 0), (0, 3), (3, 0), (3, 3)]                           # the hole is filled


def test_custom_polygon_pad_paste_and_vias():
    spec = SPEC("Example_Custom_LPad_Slot")
    txt, rep = build(spec)
    assert C.check(txt) == [], C.check(txt)
    assert rep["table_checks"] == [] and rep["notes"] == [], rep
    t = sexpr.parse(txt)
    custom = next(p for p in sexpr.find(t, "pad") if p[3] == "custom")
    poly = [(float(a[1]), float(a[2])) for a in sexpr.find(sexpr.first(sexpr.first(sexpr.first(custom, "primitives"), "gr_poly"), "pts"), "xy")]
    at = sexpr.first(custom, "at")
    poly = [(x + float(at[1]), y + float(at[2])) for x, y in poly]
    assert C._point_in_poly(float(at[1]), float(at[2]), poly)                         # the anchor is inside the L
    wins = [p for p in C._pad_rects(t) if p["paste"] and not p["copper"]]
    assert wins and all(C._point_in_poly(w["x"] + sx * w["w"] / 2, w["y"] + sy * w["h"] / 2, poly)
                        for w in wins for sx in (-1, 1) for sy in (-1, 1))
    cov = sum(w["w"] * w["h"] for w in wins) / A._poly_area(poly)
    assert 0.5 < cov < 0.7, cov
    vias = [p for p in sexpr.find(t, "pad") if p[1] == "1" and p[2] == "thru_hole"]
    assert len(vias) == 4 and all(C._point_in_poly(float(sexpr.first(v, "at")[1]), float(sexpr.first(v, "at")[2]), poly) for v in vias)
    assert "(zone" in txt and "(vias not_allowed)" in txt and "(drill oval 0.8 1.8)" in txt
    crt = C._graphics(t, "F.CrtYd")[1]
    assert len(crt) == 1 and len(crt[0][0]) > 4, crt                                   # follows the pads, not a box
    more = copy.deepcopy(spec)
    more["pad_table"]["pads"][0]["thermal_vias"]["ny"] = 3
    assert "2 of 6 thermal vias left out" in build(more)[1]["notes"][0]
    # pad 2 sits inside the L's bounding box but 1 mm clear of its copper: no overlap; moved into the L: overlap
    moved = copy.deepcopy(spec)
    moved["pad_table"]["pads"][1]["x"] = -1.2
    try:
        F.build(moved)
        raise AssertionError("pad inside the polygon accepted")
    except ValueError as e:
        assert "overlap" in str(e)
    close = txt.replace('(pad "2" smd roundrect (at 0.5 1.5)', '(pad "2" smd roundrect (at -0.4 1.5)')
    assert any("0.100 mm" in m for m in msgs(close)), msgs(close)


# ---------------------------------------------------------------- every new spec
def test_every_advanced_spec_builds_checks_converts_and_has_a_model():
    specs = sorted(ADV.glob("*.json"))
    assert len(specs) >= 20
    with tempfile.TemporaryDirectory() as td:
        for p in specs:
            spec = json.loads(p.read_text())
            txt, rep = build(spec)
            assert not [c for c in rep["checks"] if c["level"] == "error"], (p.stem, rep["checks"])
            fp, _ = F.altium_footprint(spec)
            out = AL.write_pcblib(Path(td) / f"{p.stem}.PcbLib", [fp])
            assert out.stat().st_size > 1000 and len(fp["pads"]) >= len(rep["pads"]), p.stem
            step, info = S.generate(spec)
            assert S.check(step)["errors"] == [], p.stem
            bb = info["bbox"]
            if spec["package_type"] == "bga":                                          # model = body, balls under it
                assert abs(bb[3] - bb[0] - F.dim(spec["body_length"])[1]) < 1e-6 and bb[2] == 0, (p.stem, bb)
                assert len([1 for _ in range(info["solids"])]) == len(rep["pads"]) + 2


def test_schema_reaches_the_datasheet_reader():
    """New top-level dimensions become printed-value dims for the LLM; the pad table is passed as a nested object
    with no $ref left in it (structured outputs has no $defs)."""
    from ecadlib import datasheet as D
    out = D.output_schema()
    pkg = out["properties"]["package"]["properties"]
    assert "ball_diameter" in D.DIMS and "land_diameter" in D.DIMS and "drill" in D.DIMS
    assert "pad_table" in pkg and "$ref" not in json.dumps(pkg["pad_table"])
    assert {"bga", "pin_header", "connector", "custom"} <= set(json.dumps(pkg["package_type"]).replace('"', " ").split())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
