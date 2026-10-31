"""Footprint generator tests, a few parts of each kind (resistors, capacitors, inductors, LEDs, diodes,
tantalum, SOT, SOIC/TSSOP/MSOP, QFP, QFN/DFN), checked three ways:

  1. against KiCad's official footprints: pad 1 size/position and courtyard (numbers only, copied from
     gitlab.com/kicad/libraries/kicad-footprints, 2026-10). Parts KiCad *generates* from the same body
     dimensions must match exactly; hand-drawn ones (diodes, SOT-223, tantalum) only roughly.
  2. ecadlib.footprint_check finds nothing wrong with any of them (KLC silk/fab/courtyard/pin-1 rules).
  3. each converts to an Altium .PcbLib.
Plus edge cases: density levels, the fine-pitch switch, exposed-pad clearance, tiny parts, datasheet lands,
polarity marks and bad specs.

Run:  python3 -m tests.test_footprints"""
import json, math, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_check as C, footprint_gen as F, sexpr

SPECS = Path(__file__).resolve().parent.parent / "ecadlib" / "package_specs"
SPEC = lambda n: json.loads((SPECS / f"{n}.json").read_text())

# KiCad official footprint: pad 1 (w, h, x, y) and courtyard (w, h)
KICAD_GENERATED = {          # built by kicad-footprint-generator from the same inputs as our example specs
    "R_0402_1005Metric": ((0.54, 0.64, -0.51, 0), (1.86, 0.94)),
    "R_0603_1608Metric": ((0.8, 0.95, -0.825, 0), (2.96, 1.46)),
    "R_0805_2012Metric": ((1.025, 1.4, -0.9125, 0), (3.36, 1.9)),
    "R_1206_3216Metric": ((1.125, 1.75, -1.4625, 0), (4.56, 2.26)),
    "C_0805_2012Metric": ((1.0, 1.45, -0.95, 0), (3.4, 1.96)),
    "LED_0603_1608Metric": ((0.875, 0.95, -0.7875, 0), (2.96, 1.46)),
    "LED_0805_2012Metric": ((0.975, 1.4, -0.9375, 0), (3.36, 1.9)),
    "SOT-23": ((1.475, 0.6, -0.9375, -0.95), (3.86, 3.4)),
    "SOT-23-5": ((1.325, 0.6, -1.1375, -0.95), (4.1, 3.4)),
    "SOIC-8_3.9x4.9mm_P1.27mm": ((1.95, 0.6, -2.475, -1.905), (7.4, 5.4)),
    "SOIC-16_3.9x9.9mm_P1.27mm": ((1.95, 0.6, -2.475, -4.445), (7.4, 10.4)),
    "TSSOP-16_4.4x5mm_P0.65mm": ((1.475, 0.4, -2.8625, -2.275), (7.7, 5.5)),
    "LQFP-48_7x7mm_P0.5mm": ((1.475, 0.3, -4.1625, -2.75), (10.3, 10.3)),
    "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm": ((0.775, 0.25, -1.4625, -0.75), (4.2, 4.2)),
    "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm": ((0.875, 0.25, -2.4375, -1.75), (6.26, 6.26)),
}
KICAD_OTHER = {              # hand-drawn in KiCad, or KiCad used body sizes we don't have: (pad, courtyard, tolerance)
    "C_0402_1005Metric": ((0.56, 0.62, -0.48, 0), (1.82, 0.92), 0.05),   # spec has datasheet sizes, not KiCad's (footprint-rules.md)
    "L_0805_2012Metric": ((0.875, 1.2, -1.0625, 0), (3.5, 1.7), 0.3),
    "MSOP-8_3x3mm_P0.65mm": ((1.625, 0.4, -2.1125, -0.975), (6.36, 3.5), 0.15),
    "DFN-8-1EP_3x3mm_P0.5mm_EP1.66x2.38mm": ((0.7, 0.25, -1.4, -0.75), (4.0, 3.6), 0.3),
    "D_SMA": ((2.5, 1.8, -2.0, 0), (7.0, 3.5), 0.5),
    "D_SMB": ((2.5, 2.3, -2.15, 0), (7.3, 4.5), 0.4),
    "CP_EIA-3216-18_Kemet-A": ((1.415, 1.39, -1.3525, 0), (4.62, 2.1), 0.4),
    "SOT-223-3_TabPin2": ((2.0, 1.5, -3.15, -2.3), (8.8, 7.2), 0.6),
    # SOD packages: KiCad copies the maker's recommended land, far from an IPC calculation on lead sizes
    "D_SOD-123": ((0.9, 1.2, -1.65, 0), (4.7, 2.3), 0.7),
    "D_SOD-323": ((0.6, 0.45, -1.05, 0), (3.2, 1.9), 0.7),
}
EXACT = 0.0051


def pad1(rep):
    p = next(p for p in rep["pads"] if p["num"] == "1")
    return (p["w"], p["h"], p["x"], p["y"])


def build(name, **kw):
    spec = SPEC(name)
    F.validate(spec)
    txt, rep = F.build(spec, kw.get("density"))
    sexpr.parse(txt)
    return txt, rep


def test_matches_kicad_generated():
    bad = []
    for name, (ref_pad, ref_crt) in KICAD_GENERATED.items():
        _, rep = build(name)
        got = pad1(rep) + tuple(rep["courtyard"])
        want = ref_pad + ref_crt
        if any(abs(a - b) > EXACT for a, b in zip(got, want)):
            bad.append(f"{name}: got {got}, KiCad {want}")
    assert not bad, "\n".join(bad)


def test_close_to_kicad_hand_drawn():
    bad = []
    for name, (ref_pad, ref_crt, tol) in KICAD_OTHER.items():
        _, rep = build(name)
        d = max(abs(a - b) for a, b in zip(pad1(rep) + tuple(rep["courtyard"]), ref_pad + ref_crt))
        if d > tol:
            bad.append(f"{name}: off by {d:.3f} mm (allowed {tol})")
    assert not bad, "\n".join(bad)


def test_every_example_passes_the_checker_and_converts_to_altium():
    with tempfile.TemporaryDirectory() as td:
        for p in sorted(SPECS.glob("*.json")):
            txt, rep = build(p.stem)
            assert rep["checks"] == [], (p.stem, rep["checks"])
            assert C.check(txt) == [] or p.stem.startswith("SOT-223"), (p.stem, C.check(txt))
            fp, _ = AL.footprint_from_spec(SPEC(p.stem))
            out = AL.write_pcblib(Path(td) / f"{p.stem}.PcbLib", [fp])
            assert out.stat().st_size > 1000, p.stem


def test_kicad_silk_lines_reproduced():
    """Chip silk is cut back 0.2 mm (+ half the line) from the *rounded* pad outline, exactly where KiCad cuts it;
    0402 falls back to KiCad's 0.1 mm small-part clearance."""
    for name, x_end, y in (("R_0603_1608Metric", 0.237258, 0.5225), ("R_0402_1005Metric", 0.153641, 0.38)):
        txt, _ = build(name)
        silk = [s for s in C._graphics(sexpr.parse(txt), "F.SilkS")[0]]
        assert len(silk) == 2, (name, silk)
        assert all(abs(abs(s[1]) - y) < 1e-3 and abs(max(s[0], s[2]) - x_end) < 1e-3 for s in silk), (name, silk)


def test_ipc_family_selection():
    fam = {n: build(n)[1]["ipc_family"] for n in ("R_0402_1005Metric", "R_0603_1608Metric", "TSSOP-16_4.4x5mm_P0.65mm",
                                                  "LQFP-48_7x7mm_P0.5mm", "D_SMA", "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm")}
    assert fam == {"R_0402_1005Metric": "chip_small", "R_0603_1608Metric": "chip",
                   "TSSOP-16_4.4x5mm_P0.65mm": "gullwing",           # 0.65 pitch is still the large-pitch table
                   "LQFP-48_7x7mm_P0.5mm": "gullwing_fine", "D_SMA": "molded",
                   "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm": "qfn"}, fam
    spec = SPEC("TSSOP-16_4.4x5mm_P0.65mm")
    spec["pitch"] = 0.5
    assert F.family(spec) == "gullwing_fine"
    spec = SPEC("R_0805_2012Metric")
    spec["lead_type"] = "melf"
    assert F.family(spec) == "melf"


def test_density_levels_order():
    """Most (A) > nominal (B) > least (C): bigger pads and courtyard as density drops, all still clean."""
    for name in ("R_0603_1608Metric", "SOIC-8_3.9x4.9mm_P1.27mm", "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", "D_SMA"):
        reps = [build(name, density=d)[1] for d in "ABC"]
        z = [r["Z"] for r in reps]
        crt = [r["courtyard"][0] for r in reps]
        assert z[0] > z[1] > z[2] and crt[0] > crt[1] > crt[2], (name, z, crt)
        assert all(not r["checks"] for r in reps), (name, [r["checks"] for r in reps])


def test_exposed_pad_keeps_clearance():
    """A big EP pulls the pad heels back so every pad stays >= 0.2 mm from it (KLC F6.3)."""
    spec = SPEC("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm")
    spec["exposed_pad"] = {"length": {"nom": 2.1}, "width": {"nom": 2.1}}
    txt, rep = F.build(spec)
    gap = min(abs(p["x"]) - p["w"] / 2 if abs(p["x"]) > abs(p["y"]) else abs(p["y"]) - p["h"] / 2 for p in rep["pads"]) - 2.1 / 2
    assert gap >= 0.2 - 1e-6, gap
    assert not rep["checks"] and "warning" not in rep, rep
    pads = C._pad_rects(sexpr.parse(txt))
    windows = [p for p in pads if p["paste"] and not p["copper"]]
    cov = sum(p["w"] * p["h"] for p in windows) / 2.1 ** 2
    assert len(windows) == 4 and 0.6 < cov < 0.7, (len(windows), cov)     # 2x2 windows at ~65 %


def test_gullwing_minimum_heel_gap():
    """KiCad never lets opposite gull-wing rows come closer than 0.4 mm (why SOT-23's G is 0.40, not 0.35)."""
    _, rep = build("SOT-23")
    assert abs(rep["G"] - 0.4) < 1e-9, rep["G"]


def test_tiny_0201_chip():
    spec = {"name": "R_0201_0603Metric", "package_type": "chip", "pin_count": 2, "body_length": {"min": 0.57, "max": 0.65},
            "body_width": {"min": 0.27, "max": 0.33}, "terminal_length": {"min": 0.1, "max": 0.2}}
    F.validate(spec)
    txt, rep = F.build(spec)
    assert rep["ipc_family"] == "chip_small" and not any(c["level"] == "error" for c in rep["checks"]), rep["checks"]
    assert rep["G"] > 0.1, rep                                              # pads never merge
    assert C._rect_gap(*[dict(p, r=0) for p in rep["pads"]]) >= 0.1


def test_polarity_marked_on_pin1_side():
    """LEDs/diodes: pin 1 is the cathode (KiCad symbols), on the left, and the silk bracket closes on that side."""
    for name in ("LED_0603_1608Metric", "D_SOD-123", "D_SMA", "CP_EIA-3216-18_Kemet-A"):
        txt, rep = build(name)
        silk = C._graphics(sexpr.parse(txt), "F.SilkS")[0]
        p1 = next(p for p in rep["pads"] if p["num"] == "1")
        p2 = next(p for p in rep["pads"] if p["num"] == "2")
        verticals = [s for s in silk if abs(s[0] - s[2]) < 1e-9]
        assert p1["x"] < 0 < p2["x"], name
        assert verticals and all(s[0] < p1["x"] - p1["w"] / 2 for s in verticals), (name, verticals)
        assert "polarized" in txt.split("(tags", 1)[1].split(")", 1)[0], name
    txt, _ = build("R_0603_1608Metric")                                       # unpolarized: no bracket, plain fab box
    assert not [s for s in C._graphics(sexpr.parse(txt), "F.SilkS")[0] if abs(s[0] - s[2]) < 1e-9]


def test_pin1_marker_and_numbering():
    for name in ("SOIC-8_3.9x4.9mm_P1.27mm", "LQFP-48_7x7mm_P0.5mm", "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", "SOT-23-5"):
        txt, rep = build(name)
        _, polys = C._graphics(sexpr.parse(txt), "F.SilkS")
        assert any(filled for _, filled in polys), name                        # filled pin-1 triangle
        nums = [p["num"] for p in rep["pads"]]
        assert nums == [str(i + 1) for i in range(len(nums))], (name, nums)  # counter-clockwise from top-left
    _, rep = build("LQFP-48_7x7mm_P0.5mm")
    p = {q["num"]: q for q in rep["pads"]}
    assert p["1"]["x"] < 0 and p["13"]["y"] > 0 and p["25"]["x"] > 0 and p["37"]["y"] < 0


def test_sot223_tab_shares_pin2():
    txt, rep = build("SOT-223-3_TabPin2")
    twos = [p for p in rep["pads"] if p["num"] == "2"]
    assert len(twos) == 2 and twos[1]["h"] > 2.5 and twos[1]["x"] > 0, twos
    assert rep["checks"] == []                                                 # duplicate "2" is deliberate


def test_datasheet_land_overrides_ipc():
    spec = SPEC("D_SOD-123")
    spec["land"] = {"pad_length": 0.9, "pad_width": 1.2, "pad_center": 1.65}   # the maker's recommended land
    F.validate(spec)
    _, rep = F.build(spec)
    assert pad1(rep) == (0.9, 1.2, -1.65, 0) and rep["land_source"] == "datasheet", rep
    assert not rep["checks"]


def test_checker_catches_broken_footprints():
    txt, _ = build("SOIC-8_3.9x4.9mm_P1.27mm")
    rules = lambda t: {(lvl, rule) for lvl, rule, _ in C.check(t)}
    assert ("error", "courtyard") in rules("\n".join(l for l in txt.splitlines() if "F.CrtYd" not in l))
    moved = txt.replace('(pad "2" smd roundrect (at -2.475 -0.635)', '(pad "2" smd roundrect (at -2.475 -1.6)')
    assert ("error", "copper") in rules(moved)
    renum = txt.replace('(pad "1" ', '(pad "9" ', 1)
    assert ("error", "pin1") in rules(renum)
    over_pad = txt.rstrip()[:-1] + '  (fp_line (start -3 -1.905) (end -2 -1.905) (stroke (width 0.12) (type solid)) (layer "F.SilkS")))\n'
    assert ("error", "silk") in rules(over_pad)


def test_bad_specs_are_clear_errors():
    import jsonschema
    spec = SPEC("R_0603_1608Metric")
    del spec["terminal_length"]                                               # neither T nor inner span
    try:
        F.validate(spec)
        raise AssertionError("expected a validation error")
    except jsonschema.ValidationError:
        pass
    spec = SPEC("R_0603_1608Metric")
    spec["package_type"] = "bga"
    try:
        F.validate(spec)
        raise AssertionError("expected a validation error")
    except jsonschema.ValidationError:
        pass


def test_common_spec_mistakes_are_explained():
    """Troubleshooting: the datasheet-reading slips that otherwise give a plausible but wrong footprint."""
    def err(name, **change):
        spec = SPEC(name)
        spec.update(change)
        try:
            F.build(spec)
        except ValueError as e:
            return str(e)
        raise AssertionError(f"{name} {change}: no error")
    assert "swapped" in err("SOIC-8_3.9x4.9mm_P1.27mm", lead_span={"nom": 3.9}, body_width={"nom": 6.0, "tol": 0.2})
    assert "mils" in err("SOIC-8_3.9x4.9mm_P1.27mm", lead_span={"min": 228, "max": 244})
    assert "whole part" in err("R_0603_1608Metric", terminal_length={"min": 1.5, "max": 1.7})
    assert "pitch and pin_count" in err("TSSOP-16_4.4x5mm_P0.65mm", pin_count=32)
    assert "smaller than the" in err("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", terminal_width={"min": 0.5, "max": 0.6})
    assert "min 1.7 is bigger" in err("R_0603_1608Metric", body_length={"min": 1.7, "max": 1.5})
    assert "exposed pad" in err("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", exposed_pad={"length": {"nom": 3.2}, "width": {"nom": 3.2}})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)


def test_a_powerpad_read_long_side_first_runs_with_the_body():
    # TLC5951DAP (benchmark 2026-10-18): HTSSOP-38, EP read 5.05-6.35 x 2.69-3.39 with "length" (across the rows)
    # the long side; across, it pushed the heels out to 3.05 mm. Turned, the pads are KiCad's (1.575 at +-3.7125).
    spec = {"name": "HTSSOP-38-1EP", "package_type": "gullwing", "pin_count": 38, "pitch": 0.65,
            "body_length": {"min": 12.4, "max": 12.6}, "body_width": {"min": 6.0, "max": 6.2},
            "lead_span": {"min": 7.9, "max": 8.3}, "terminal_length": {"min": 0.5, "max": 0.75},
            "terminal_width": {"min": 0.19, "max": 0.3}, "height_max": 1.2,
            "exposed_pad": {"length": {"min": 5.05, "max": 6.35}, "width": {"min": 2.69, "max": 3.39}}}
    from ecadlib import footprint_bench as FB
    txt, rep = F.build(spec, "B")
    pads = FB.numbers(txt)["pads"]
    assert abs(pads[0][1] + 3.7125) < 1e-6 and abs(pads[0][3] - 1.575) < 1e-6, pads[0]
    assert pads[-1][3] < pads[-1][4] and "turned" in rep["notes"][0], (pads[-1], rep)
    square = dict(spec, exposed_pad={"length": {"nom": 3.0}, "width": {"nom": 3.0}})
    assert not F.build(square, "B")[1].get("notes")


def test_the_makers_thermal_land_is_the_exposed_pads_copper():
    # TPS26600PWP (benchmark 2026-10-18): TI's PWP0016A land page gives a 3.4 x 5 thermal land around the 3 x 3
    # exposed pad (tps2660.pdf p53); KiCad draws the 3.4 x 5. Without ep_across / ep_along the package's EP stays.
    spec = {"name": "HTSSOP-16-1EP", "package_type": "gullwing", "pin_count": 16, "pitch": 0.65,
            "body_length": {"min": 4.9, "max": 5.1}, "body_width": {"min": 4.3, "max": 4.5},
            "lead_span": {"min": 6.2, "max": 6.6}, "terminal_length": {"min": 0.5, "max": 0.75},
            "terminal_width": {"min": 0.19, "max": 0.3}, "height_max": 1.2,
            "exposed_pad": {"length": {"min": 2.7, "max": 3.3}, "width": {"min": 2.7, "max": 3.3}},
            "land": {"pad_length": 1.5, "pad_width": 0.45, "pad_center": 2.9}}
    from ecadlib import footprint_bench as FB
    ep = lambda s: FB.numbers(F.build(s, "B")[0])["pads"][-1]
    assert ep(spec)[3:5] == [3.0, 3.0]
    spec["land"].update(ep_across=3.4, ep_along=5.0)
    txt, rep = F.build(spec, "B")
    assert FB.numbers(txt)["pads"][-1][3:5] == [3.4, 5.0] and rep["ep_land"] == "datasheet"



def _gw2(pin1_side):
    """SOD-123 read as a 2-lead gullwing (reviewer 2026-10-31: variants2.py V_gw2_pin1R), cathode / pin 1 on that side."""
    far = {"L": "R", "R": "L"}[pin1_side]
    return {"name": "D_gw2", "package_type": "gullwing", "pin_count": 2, "polarized": True,
            "body_length": {"nom": 1.6}, "body_width": {"nom": 2.7}, "lead_span": {"min": 3.55, "max": 3.85},
            "terminal_length": {"min": 0.25, "max": 0.5}, "terminal_width": {"min": 0.5, "max": 0.7},
            "pin_positions": [{"number": "1", "side": pin1_side, "offset": 0}, {"number": "2", "side": far, "offset": 0}]}


def _marks(txt):
    """Silk lines and the fab outline as sets of rounded points, and pad number -> (x, y)."""
    t = sexpr.parse(txt)
    r = lambda v: round(float(v), 4) + 0.0
    silk = {tuple(sorted([(r(sexpr.first(n, "start")[1]), r(sexpr.first(n, "start")[2])),
                          (r(sexpr.first(n, "end")[1]), r(sexpr.first(n, "end")[2]))]))
            for n in sexpr.find(t, "fp_line") if sexpr.first(n, "layer")[1] == "F.SilkS"}
    fab = [n for n in sexpr.find(t, "fp_poly") if sexpr.first(n, "layer")[1] == "F.Fab"]
    fab = {(r(a[1]), r(a[2])) for n in fab for a in sexpr.find(sexpr.first(n, "pts"), "xy")}
    pads = {n[1]: (r(sexpr.first(n, "at")[1]), r(sexpr.first(n, "at")[2])) for n in sexpr.find(t, "pad")}
    return silk, fab, pads


def test_a_polarized_two_pad_part_marks_pin_1_wherever_it_sits():
    # pin 1 on the right: the bracket and the fab chamfer are the pin-1-left footprint's, mirrored (they sat on pad 2)
    m = lambda seg: tuple(sorted((-x + 0.0, y) for x, y in seg))
    left, right = F.build(_gw2("L"))[0], F.build(_gw2("R"))[0]
    sl, fl, pl = _marks(left)
    sr, fr, pr = _marks(right)
    assert pr["1"][0] > 0 > pr["2"][0] and pl["1"][0] < 0 < pl["2"][0], (pl, pr)
    assert sr == {m(s) for s in sl}, (sl, sr)
    assert fr == {(-x + 0.0, y) for x, y in fl}, (fl, fr)
    assert C.check(left) == [] and C.check(right) == [], (C.check(left), C.check(right))
