"""Review regressions and property tests for the footprint / symbol / Altium / KiCad / STEP writers.

Regressions (each failed before its fix):
  - stacked power pins: VDD1 / VDD2 (an isolator's two sides) and VDD18 / VDD33 were stacked on one spot (Altium joins
    them: two supplies shorted); a hidden pin's Altium HiddenNetName was lost in the KiCad export (KiCad tied it to a
    net named after the pin, VDD_2, not VDD)
  - single-row SMD pin headers used the dual-row land table; KiCad's 2.00 mm dual-row land was off; no 1.00 mm table
  - through-hole pads sized from the drill ignored the pad rotation (rotated rows refused as overlapping)
  - BGA row H was taken for mounting holes (H1, H2 ..): its balls escaped the grid checks and release_check's
    pads-without-a-pin rule; a BGA with A1 depopulated (KiCad's own BGA-672) failed "no pad 1"; a misnamed ball
    switched the BGA rules off silently
  - Altium: an SMD-defined land's paste margin was dropped; PcbLib -> KiCad dropped mask margins and tenting
  - STEP: SMD pin headers had through-hole posts going 3 mm into the board; rotated connector pads were not turned
Property tests: random valid BGA / header / connector specs -> pad count = pins, no overlaps, courtyard holds every
pad, a full BGA grid is symmetric, and the PcbLib read back equals the .kicad_mod (position, size, mask, paste);
random pin lists -> every pin once, on the grid, on the body edge, nothing of a different net stacked.

Run:  python3 -m tests.test_footprint_review"""
import collections, json, random, re, struct, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_adv as A, footprint_bench as B, footprint_check as C, footprint_gen as F, \
    kicad_export as X, rules as RU, sexpr, step_gen as S, symbol_layout as SL

ADV = Path(__file__).resolve().parent.parent / "ecadlib" / "package_specs" / "advanced"
SPEC = lambda n: json.loads((ADV / f"{n}.json").read_text())


def _spots(sym):
    at = {}
    for pr, u in zip(sym["prims"], sym.get("prim_parts") or [1] * len(sym["prims"])):
        if pr[0] == "pin":
            at.setdefault((u, pr[3], pr[4]), []).append(pr[1])
    return at


# ---------------------------------------------------------------- symbols
def test_different_supplies_are_never_stacked():
    pins = [{"num": str(i + 1), "name": n, "type": "power_in"} for i, n in
            enumerate(["VDD1", "GND1", "VDD2", "GND2", "VDD18", "VDD33", "VDD_1", "VDD_2", "VDD_3", "VCC_18", "VCC_33"])]
    pins += [{"num": str(i), "name": f"IO{i}", "type": "io"} for i in range(12, 50)]
    sym = SL.build("ISO", pins, rules={"symbols": dict(RU.DEFAULTS["symbols"], stack_power="stack")})
    stacks = sorted(sorted(p["name"] for p in ps) for ps in _spots(sym).values() if len(ps) > 1)
    assert stacks == [["VDD_1", "VDD_2", "VDD_3"]], stacks             # instance numbers 1..n only
    assert SL.stack_key("VDD1") == "VDD1" and SL.stack_key("VDD_2") == "VDD"
    assert SL.stack_keys(["VCC_18", "VCC_33"]) == {"VCC_18": "VCC_18", "VCC_33": "VCC_33"}
    assert SL.check(sym) == []


def test_hidden_power_pins_keep_their_net_in_kicad():
    pins = [{"num": str(i), "name": f"VDD_{i}", "type": "power_in"} for i in range(1, 4)]
    pins += [{"num": str(i), "name": f"IO{i}", "type": "io"} for i in range(4, 44)]
    sym = SL.build("T", pins, rules={"symbols": {"stack_power": "hide"}})
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "t.SchLib", [sym])
        ks = X.symbol_from_schlib(Path(d) / "t.SchLib", "T", [])
    hidden = [(p[1], str(sexpr.first(p, "name")[1]).strip('"'), str(sexpr.first(p, "number")[1]).strip('"'))
              for u in sexpr.find(ks, "symbol") for p in sexpr.find(u, "pin") if "hide" in p]
    assert sorted(hidden) == [("power_in", "VDD", "2"), ("power_in", "VDD", "3")], hidden   # KiCad joins hidden power pins by name


def test_random_pin_lists_lay_out_cleanly():
    R = random.Random(7)
    voc = ["VDD", "VSS", "GND", "VDDA", "VDD1", "VDD2", "VDD_1", "VDD_2", "VDD_18", "VDD_33", "AGND", "NRST", "BOOT0", "OSC_IN",
           "SWDIO", "USB_DP", "SDA", "SCL", "MOSI", "TXD", "RXD", "EN", "NC", "EP", "VCAP_1", "VOUT", "FOO"]
    for it in range(40):
        pins = []
        for i in range(R.randint(30, 200)):
            r = R.random()
            name = f"P{R.choice('ABCDE')}{R.randint(0, 15)}" if r < 0.4 else f"GPIO{R.randint(0, 40)}" if r < 0.5 else R.choice(voc)
            pins.append({"num": str(i + 1), "name": name, "type": R.choice(["io", "input", "output", "power_in", "passive"])})
        rules = {"symbols": {"stack_power": R.choice(SL.STACKS), "split_pins": R.choice([0, 40, 100]), "unit_pins": R.choice([8, 16, 48])}}
        sym = SL.build("X", pins, rules=rules)
        got = collections.Counter(pr[1]["num"] for pr in sym["prims"] if pr[0] == "pin")
        assert sorted(got) == sorted(p["num"] for p in pins) and set(got.values()) == {1}, it   # every pin, once
        assert SL.check(sym) == [], (it, SL.check(sym))
        for ps in _spots(sym).values():
            if len(ps) > 1:
                assert rules["symbols"]["stack_power"] != "none" and len(set(SL.stack_keys([p["name"] for p in ps]).values())) == 1, (it, ps)
        parts = sym.get("prim_parts") or [1] * len(sym["prims"])
        body = {u: pr for pr, u in zip(sym["prims"], parts) if pr[0] == "rect"}
        for pr, u in zip(sym["prims"], parts):
            if pr[0] == "pin":
                _, p, side, x, y, *_ = pr
                r = body[u]
                x0, y0, x1, y1 = min(r[1], r[3]), min(r[2], r[4]), max(r[1], r[3]), max(r[2], r[4])
                assert {"left": x == x0 and y0 < y < y1, "right": x == x1 and y0 < y < y1,
                        "top": y == y1 and x0 < x < x1, "bottom": y == y0 and x0 < x < x1}[side], (it, p, side, x, y, r)


# ---------------------------------------------------------------- headers, rotated THT pads
def test_smd_header_default_lands_match_kicad_tables():
    pads = lambda **kw: [(p["w"], p["h"], p["x"]) for p in F.build(dict({"name": "H", "package_type": "pin_header", "rows": 5,
                                                                         "mount": "smd"}, **kw))[1]["pads"]][:2]
    assert pads(cols=1, pitch=2.0, pin_count=5) == [(2.35, 0.85, -1.175), (2.35, 0.85, 1.175)]       # was the 2-row 2.5 @ 2.125
    assert pads(cols=1, pitch=2.54, pin_count=5, pin1_side="right") == [(2.51, 1.0, 1.655), (2.51, 1.0, -1.655)]
    assert pads(cols=2, pitch=2.0, pin_count=10) == [(2.58, 1.0, -2.085), (2.58, 1.0, 2.085)]
    assert pads(cols=2, pitch=1.0, pin_count=10)[0] == (2.0, 0.5, -1.65)                              # was refused


def test_rotated_drill_only_pads_are_sized_across_the_row():
    for rot in (0, 90, 270):
        spec = {"name": "X", "package_type": "connector", "pin_count": 4, "pitch": 1.5, "pad_table": {"unit": "mm", "y_axis": "up",
                "arrays": [{"count": 4, "x": -2.25, "y": 0, "dx": 1.5, "type": "tht", "drill": 0.8, "rotation": rot}]}}
        txt, rep = F.build(spec)
        assert not [c for c in rep["checks"] if c["level"] == "error"], rep["checks"]
        r = [q for q in C._pad_rects(sexpr.parse(txt)) if q["num"] == "2"][0]
        assert (round(r["w"], 3), round(r["h"], 3)) == (1.1, 1.8), (rot, r)                  # narrow along the row, as drawn


# ---------------------------------------------------------------- BGA naming in the checks
def test_bga_row_h_is_balls_not_mounting_holes():
    txt, _ = F.build(SPEC("BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"))
    bad = re.sub(r'(\(pad "H3" smd circle \(at )(\S+) ', lambda m: f"{m.group(1)}{float(m.group(2)) + 0.3:g} ", txt)
    assert bad != txt and ("error", "bga", "balls H3 are off the 1 mm grid") in C.check(bad), C.check(bad)
    assert C.mount_rx(["A1", "B1", "H1"]) is C.MOUNT_GRID and C.mount_rx(["1", "2", "H1"]) is C.MOUNT
    from tests.test_release_check import fails, make
    with tempfile.TemporaryDirectory() as d:                     # a symbol without the H row: H1..H10 pads have no pin
        balls = [r[0] for r in B.numbers(txt)["pads"]]
        item = make(d, kmod=txt, pins=[b for b in balls if not b.startswith("H")], fp_name="BGA100", height=1.2)
        f, _ = fails(item, d)
        assert any("pads without a symbol pin" in m and "H1" in m for m in f), f


def test_bga_without_a1_and_misnamed_balls():
    txt, rep = F.build(SPEC("BGA-672_27.0x27.0mm_Layout26x26_P1.0mm_Ball0.6mm_Pad0.5mm_NSMD"))
    assert rep["checks"] == [] and C.check(txt) == []                      # A1 depopulated: its corner is still pin 1
    assert {p["num"] for p in rep["pads"]} >= {"AA1", "AF2", "AF25", "Y26"} and "A1" not in {p["num"] for p in rep["pads"]}
    found = C.check(txt.replace('"A2"', '"Q2"'))
    assert ("error", "bga", "row letters and rows disagree at balls Q2: a row letter on two rows, or two letters on one row "
                            "(misnamed balls?)") in found, found


# ---------------------------------------------------------------- Altium both ways
def test_smd_defined_land_paste_and_mask_reach_altium_and_come_back():
    spec = dict(SPEC("BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"), land_style="SMD")
    fp, _ = F.altium_footprint(spec)
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "b.PcbLib", [fp])
        pad = next(r for r in AL.read_pcblib(Path(d) / "b.PcbLib")[0]["records"] if r["type"] == "pad")
        assert (pad["paste_mode"], pad["paste"], pad["mask_mode"], pad["mask"]) == (2, AL.mm(-0.05), 2, AL.mm(-0.05)), pad
        back = X.footprint_from_pcblib(Path(d) / "b.PcbLib", "b", [])
    p = next(sexpr.find(sexpr.parse(back), "pad"))
    assert sexpr.first(p, "solder_mask_margin")[1] == "-0.05" and sexpr.first(p, "solder_paste_margin")[1] == "-0.05", p


def test_tented_thermal_vias_stay_tented_through_a_pcblib():
    fp, _ = F.altium_footprint(SPEC("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm_ThermalVias"))
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "q.PcbLib", [fp])
        back = sexpr.parse(X.footprint_from_pcblib(Path(d) / "q.PcbLib", "q", []))
    vias = [p for p in sexpr.find(back, "pad") if p[2] == "thru_hole"]
    assert len(vias) == 4 and all(sexpr.first(p, "layers")[1:] == ["*.Cu"] for p in vias), vias


# ---------------------------------------------------------------- STEP
def test_smd_header_model_stays_on_the_board():
    for name in ("PinHeader_1x05_P2.54mm_Vertical_SMD_Pin1Left", "PinHeader_2x05_P1.27mm_Vertical_SMD"):
        spec = SPEC(name)
        solids, _ = S.build(spec, 5.0)
        assert min(v[2] for so in solids for v in so.verts) >= 0, name               # nothing below the board
        pads = {p["num"]: p for p in F.build(spec)[1]["pads"]}
        for so in solids:
            if so.name.startswith("foot "):
                p = pads[so.name[5:]]
                xs = [v[0] for v in so.verts]
                assert min(xs) - 1e-6 <= p["x"] <= max(xs) + 1e-6, (name, so.name)    # the foot reaches its land
        text, _ = S.generate(spec)
        assert S.check(text)["errors"] == []


def test_rotated_connector_pads_turn_in_the_model():
    spec = {"name": "R", "package_type": "connector", "pin_count": 2, "pitch": 3.0, "height_max": 3.0,
            "pad_table": {"unit": "mm", "y_axis": "up", "pads": [
                {"number": 1, "x": 0, "y": 0, "w": 0.6, "h": 2.0, "rotation": 90},
                {"number": 2, "x": 3, "y": 0, "type": "tht", "drill": 0.8, "drill_h": 1.6, "w": 1.4, "h": 2.4, "rotation": 90}]}}
    solids, _ = S.build(spec, 3.0)
    box = lambda n: next((max(v[0] for v in so.verts) - min(v[0] for v in so.verts), max(v[1] for v in so.verts) - min(v[1] for v in so.verts))
                         for so in solids if so.name == f"pin {n}")
    w1, h1 = box(1)
    w2, h2 = box(2)
    assert w1 > h1 and w2 > h2, (w1, h1, w2, h2)                   # both pads lie along X once turned by 90 degrees


# ---------------------------------------------------------------- thermal vias, benchmark
def test_thermal_via_rows_and_columns_pitch():
    _, rep = F.build(SPEC("DFN-8-1EP_3x3mm_P0.5mm_EP1.65x2.38mm_ThermalVias"))
    txt, _ = F.build(SPEC("DFN-8-1EP_3x3mm_P0.5mm_EP1.65x2.38mm_ThermalVias"))
    vias = sorted((r["x"], r["y"]) for r in C._pad_rects(sexpr.parse(txt)) if r["kind"] == "thru_hole")
    assert vias == sorted((x, y) for x in (-0.575, 0.575) for y in (-0.94, 0.0, 0.94)), vias   # vias touching the EP edge kept


def test_bench_leaves_bottom_only_heat_spreader_pads_out():
    t = ('(footprint "x" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask")) '
         '(pad "1" smd rect (at 0 0) (size 1 1) (layers "B.Cu")) (pad "1" thru_hole circle (at 0 0) (size 0.5 0.5) (drill 0.2) (layers "*.Cu")))')
    assert [p[0] for p in B.numbers(t)["pads"]] == ["1", "1"] and [p[5] for p in B.numbers(t)["pads"]] == [0.0, 0.2]


# ---------------------------------------------------------------- property tests over random specs
def _random_spec(R):
    kind = R.choice(["bga", "bga", "header", "conn"])
    if kind == "bga":
        e = R.choice([0.4, 0.5, 0.65, 0.8, 1.0, 1.27])
        rows, cols = R.randint(2, 26), R.randint(2, 26)
        s = {"name": f"BGA_{rows}x{cols}_{e}", "package_type": "bga", "rows": rows, "cols": cols, "pitch": e, "height_max": 1.2,
             "ball_diameter": {"nom": round(e * R.uniform(0.45, 0.62), 2)}, "land_style": R.choice(["NSMD", "SMD"]),
             "body_length": {"nom": round((cols - 1) * e + R.uniform(0.6, 2.0), 2)}, "body_width": {"nom": round((rows - 1) * e + R.uniform(0.6, 2.0), 2)}}
        n = rows * cols
        if rows > 4 and cols > 4 and R.random() < 0.5:
            i, j = R.randint(1, rows - 3), R.randint(1, cols - 3)
            s["depopulated"] = [f"{A.row_name(i)}{j + 1}:{A.row_name(i + 1)}{j + 2}"]
            n -= 4
        return dict(s, pin_count=n)
    if kind == "header":
        cols, rows, e = R.choice([1, 2]), R.randint(2, 20), R.choice([1.0, 1.27, 2.0, 2.54])
        return {"name": f"H{cols}x{rows}", "package_type": "pin_header", "rows": rows, "cols": cols, "pitch": e, "pin_count": rows * cols,
                "mount": R.choice(["tht", "smd"])}
    e, n = R.choice([1.25, 1.5, 2.0, 2.5, 2.54]), R.randint(2, 12)
    unit = R.choice(["mm", "mil"])
    f = 1 / 0.0254 if unit == "mil" else 1
    tht = R.random() < 0.5
    arr = {"count": n, "x": round(-(n - 1) * e / 2 * f, 3), "y": 0, "dx": round(e * f, 3), "rotation": R.choice([0, 90])}
    arr.update({"type": "tht", "drill": round(0.8 * f, 3)} if tht else {"w": round(0.6 * e * f, 3), "h": round(1.5 * f, 3)})
    if not tht and arr["rotation"]:
        arr["w"], arr["h"] = arr["h"], arr["w"]
    mp = lambda sx: {"number": "MP", "x": round(sx * ((n - 1) * e / 2 + 2.5) * f, 3), "y": round(-2 * f, 3), "w": round(1.5 * f, 3), "h": round(2.5 * f, 3)}
    return {"name": f"C{n}", "package_type": "connector", "pin_count": n, "pitch": e,
            "pad_table": {"unit": unit, "y_axis": R.choice(["up", "down"]), "view": R.choice(["top", "bottom"]), "arrays": [arr],
                          "pads": [mp(-1), mp(1)], "body": {"x1": round(-((n - 1) * e / 2 + 3.5) * f, 3), "y1": round(-4 * f, 3),
                                                            "x2": round(((n - 1) * e / 2 + 3.5) * f, 3), "y2": round(2 * f, 3)}}}


def test_random_specs_hold_their_invariants():
    R = random.Random(2026)
    for it in range(60):
        spec = _random_spec(R)
        F.validate(spec)
        txt, rep = F.build(spec)
        t = sexpr.parse(txt)
        assert not [c for c in rep["checks"] if c["level"] == "error"], (spec["name"], rep["checks"])
        rects = C._pad_rects(t)
        cu = [r for r in rects if r["copper"]]
        mount = C.mount_rx([r["num"] for r in cu])
        assert len({r["num"] for r in cu if r["num"] and not mount.fullmatch(r["num"])}) == spec["pin_count"], spec["name"]
        for i, a in enumerate(cu):                                           # no two pads of different numbers touch
            for b in cu[i + 1:]:
                assert a["num"] == b["num"] or C._rect_gap(a, b) > 0, (spec["name"], a["num"], b["num"])
        crt = sexpr.courtyard_bbox(t)
        assert all(crt[0] - 1e-6 <= r["x"] - r["w"] / 2 and r["x"] + r["w"] / 2 <= crt[2] + 1e-6 and
                   crt[1] - 1e-6 <= r["y"] - r["h"] / 2 and r["y"] + r["h"] / 2 <= crt[3] + 1e-6 for r in rects), spec["name"]
        if spec["package_type"] == "bga" and not spec.get("depopulated"):    # a full grid is symmetric both ways
            at = {(round(p["x"], 4), round(p["y"], 4)) for p in rep["pads"]}
            assert at == {(-x + 0, y) for x, y in at} == {(x, -y + 0) for x, y in at}, spec["name"]
        fp, _ = AL.footprint_from_spec(spec)                                 # PcbLib round trip = the .kicad_mod
        with tempfile.TemporaryDirectory() as d:
            AL.write_pcblib(Path(d) / "x.PcbLib", [fp])
            recs = AL.read_pcblib(Path(d) / "x.PcbLib")[0]["records"]
        kp = [p for p in sexpr.find(t, "pad") if p[2] != "smd" or "F.Cu" in sexpr.first(p, "layers")[1:]]
        ap = [r for r in recs if r["type"] == "pad"]
        assert [a["designator"] for a in ap] == [k[1] for k in kp], spec["name"]
        for k, a in zip(kp, ap):
            at, size = sexpr.first(k, "at"), sexpr.first(k, "size")
            rot = float(at[3]) if len(at) > 3 else 0.0
            w, h = float(size[1]), float(size[2])
            aw, ah = AL.to_mm(a["w"]), AL.to_mm(a["h"])
            if round((a["rot"] - rot) / 90) % 2:
                aw, ah = ah, aw
            assert abs(AL.to_mm(a["x"]) - float(at[1])) < 1e-4 and abs(AL.to_mm(a["y"]) + float(at[2])) < 1e-4, (spec["name"], k[1])
            assert abs(aw - w) < 1e-4 and abs(ah - h) < 1e-4, (spec["name"], k[1], (aw, ah, a["rot"]), (w, h, rot))
            for key, off, mode in (("solder_mask_margin", 90, 102), ("solder_paste_margin", 86, 101)):
                v = sexpr.first(k, key)
                if v:
                    m = a["raw"][4]
                    assert m[mode] == 2 and struct.unpack_from("<i", m, off)[0] == AL.mm(float(v[1])), (spec["name"], k[1], key)


# ---------------------------------------------------------------- follow-up: custom pads back to KiCad, pin lines, via units, IPC
def test_polygon_pads_come_back_from_a_pcblib_as_kicad_custom_pads():
    from ecadlib import kicad_import as K
    src = ('(footprint "R" (version 20221018) (layer "F.Cu")\n'
           ' (pad "1" smd custom (at 1 2 90) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask") (solder_mask_margin 0.05)'
           ' (options (clearance outline) (anchor rect))'
           ' (primitives (gr_poly (pts (xy -0.25 -0.25) (xy 2 -0.25) (xy 2 0.25) (xy -0.25 0.25)) (width 0) (fill yes))))\n'
           ' (pad "2" thru_hole custom (at -3 0) (size 1 1) (drill 0.5) (layers "*.Cu" "*.Mask") (options (clearance outline) (anchor circle))'
           ' (primitives (gr_poly (pts (xy -0.5 -0.5) (xy 1.5 -0.5) (xy 1.5 0.5) (xy -0.5 0.5)) (width 0) (fill yes)))))')
    notes = []
    fp, _ = K.convert_footprint(src, "R", notes=notes)
    assert any("inner layers get the anchor" in n for n in notes), notes
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "r.PcbLib", [fp])
        back = sexpr.parse(X.footprint_from_pcblib(Path(d) / "r.PcbLib", "R", []))
    assert not [g for g in sexpr.find(back, "fp_poly") if sexpr.first(g, "layer")[1] in ("F.Cu", "B.Cu", "F.Mask", "F.Paste")]
    orig = sexpr.parse(src)
    for a, b in zip(sexpr.find(orig, "pad"), sexpr.find(back, "pad")):            # same pad, primitive, anchor, layers, margin
        assert b[3] == "custom" and sexpr.first(b, "at") == sexpr.first(a, "at") and sexpr.first(b, "layers") == sexpr.first(a, "layers")
        assert sexpr.first(sexpr.first(b, "options"), "anchor") == sexpr.first(sexpr.first(a, "options"), "anchor")
        pa = [[float(v) for v in xy[1:]] for xy in sexpr.find(sexpr.first(sexpr.first(sexpr.first(a, "primitives"), "gr_poly"), "pts"), "xy")]
        pb = [[float(v) for v in xy[1:]] for xy in sexpr.find(sexpr.first(sexpr.first(sexpr.first(b, "primitives"), "gr_poly"), "pts"), "xy")]
        assert sorted(map(tuple, pa)) == sorted(map(tuple, pb)), (pa, pb)
        assert sexpr.first(b, "solder_mask_margin") == sexpr.first(a, "solder_mask_margin")
    fp2, _ = F.altium_footprint(SPEC("Example_Custom_LPad_Slot"))                 # the shipped polygon-pad example too
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "x.PcbLib", [fp2])
        back2 = X.footprint_from_pcblib(Path(d) / "x.PcbLib", "x", [])
    ref = B.numbers(F.build(SPEC("Example_Custom_LPad_Slot"))[0])
    assert B.compare(B.numbers(back2), ref)["all_pads_ok"] and C.check(back2) == []


def test_pin_lying_on_or_crossing_another_pin_is_reported():
    pin = lambda n, side, x, y: ("pin", {"num": n, "name": f"P{n}"}, side, x, y, 30)
    sym = {"prims": [("rect", 0, 0, 40, -60), pin("1", "left", 0, -10), pin("2", "left", -20, -10), pin("3", "right", 40, -20)]}
    assert [m for m in SL.check(sym) if "line" in m] == ["pin 2 lies on or crosses pin 1's line in part 1"], SL.check(sym)
    sym["prims"][2] = pin("2", "bottom", -10, 0)                                  # a vertical pin line through pin 1's
    assert [m for m in SL.check(sym) if "line" in m] == ["pin 2 lies on or crosses pin 1's line in part 1"], SL.check(sym)
    sym["prims"][2] = pin("2", "left", 0, -20)
    assert SL.check(sym) == []


def test_thermal_vias_in_the_tables_unit():
    base = SPEC("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm_ThermalVias")
    vias = lambda s: sorted((round(r["x"], 4), round(r["y"], 4), round(r["w"], 4)) for r in C._pad_rects(sexpr.parse(F.build(s)[0]))
                            if r["kind"] == "thru_hole")
    want = vias(base)
    tv = {"drill": 0.2 / 0.0254, "diameter": 0.5 / 0.0254, "nx": 2, "ny": 2, "pitch": 1.25 / 0.0254, "unit": "mil"}
    s = json.loads(json.dumps(base))
    s["pad_table"]["pads"][0]["thermal_vias"] = tv
    F.validate(s)
    assert vias(s) == want and len(want) == 4, (vias(s), want)
    t = json.loads(json.dumps(base))                                                # "table": the pad table's own unit
    t["pad_table"]["unit"] = "mil"
    for a in t["pad_table"]["arrays"]:
        for k in ("x", "y", "dx", "dy", "w", "h"):
            if k in a:
                a[k] = round(a[k] / 0.0254, 6)
    for k in ("w", "h"):
        t["pad_table"]["pads"][0][k] = round(t["pad_table"]["pads"][0][k] / 0.0254, 6)
    t["pad_table"]["body"] = {k: round(v / 0.0254, 6) for k, v in t["pad_table"]["body"].items()}
    t["pad_table"]["pads"][0]["thermal_vias"] = dict(tv, unit="table")
    F.validate(t)
    assert vias(t) == want, (vias(t), want)


def test_ipc_bga_land_table():
    assert [A.ipc_bga_land(b)[0] for b, _ in A.IPC_BGA_COLLAPSING] == [l for _, l in A.IPC_BGA_COLLAPSING]
    assert A.ipc_bga_land(0.17)[0] == 0.15 and A.ipc_bga_land(0.15)[0] == 0.13          # 0.17 gave 0.14 before


def test_bga_grid_size_is_a_clear_error():
    for cols in (1e6, 2.5, 0):                         # was a TypeError / a million-column loop (tests/test_fuzz.py seed 16)
        try:
            A.bga_balls({"rows": 13, "cols": cols, "pitch": 0.8})
            raise AssertionError(cols)
        except ValueError as e:
            assert "rows / cols" in str(e)


def test_staggered_single_row_header_pitch_in_the_visual_facts():
    from ecadlib import visual_check as V
    f = V.facts(V.parse_footprint(F.build(SPEC("PinHeader_1x05_P1.00mm_Vertical_SMD_Pin1Left"))[0]))
    assert f["pitch"] == 1.0, f["pitch"]                # nearest pads are the same-side ones 2.0 mm apart


if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_"):
            fn(); print("ok", n)
