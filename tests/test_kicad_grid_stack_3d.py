"""Follow-ups from the AD26 placement check (2026-10-19, #47): KiCad-library diodes ended their pins on 150 mil (off
Altium's 100 mil grid), KiCad ICs stacked VDD / GND pins on one end although [symbols] stack_power = "none", and parts
whose KiCad footprint has a KiCad STEP model got a plain extruded box instead of a package-shaped model."""
import tempfile
from pathlib import Path
from types import SimpleNamespace

from ecadlib import altium_lib as AL, kicad_import as K, librarian as L, rules as RU, sexpr, step_gen as S, symbol_layout as SL

STEP_DIR = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}

# drawn like KiCad's Device:D / Diode:SM6T6V8A: body at ±1.27 mm, pins at ±3.81 mm (150 mil), 2.54 mm long
DIODE = """(kicad_symbol_lib (symbol "D_test" (pin_numbers hide) (pin_names (offset 1.016) hide)
  (property "Reference" "D" (at 0 2.54 0)) (property "Value" "D_test" (at 0 -2.54 0))
  (symbol "D_test_0_1"
    (polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none)))
    (polyline (pts (xy 1.27 1.27) (xy 1.27 -1.27) (xy -1.27 0) (xy 1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type none))))
  (symbol "D_test_1_1"
    (pin passive line (at -3.81 0 0) (length 2.54) (name "K" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 3.81 0 180) (length 2.54) (name "A" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27))))))))"""

# a vertical 2-pin part on 50 mil (pins at ±6.35 mm), and an IC whose pins are already on the 100 mil grid
VERTICAL = DIODE.replace("D_test", "V_test").replace("(at -3.81 0 0)", "(at 0 6.35 270)").replace("(at 3.81 0 180)", "(at 0 -6.35 90)")


def _pin(name, num, x, y, a, etype="power_in", hide=False):
    return (f'(pin {etype} line (at {x} {y} {a}) (length 2.54){" hide" if hide else ""} '
            f'(name "{name}" (effects (font (size 1.27 1.27)))) (number "{num}" (effects (font (size 1.27 1.27)))))')


# drawn like KiCad's TPS62130: three VIN pins and four GND pins on one end each (the extra ones hidden)
REG_PINS = [("VIN", 10, -10.16, 5.08, 0), ("VIN", 11, -10.16, 5.08, 0, "power_in", True), ("VIN", 12, -10.16, 5.08, 0, "power_in", True),
            ("EN", 13, -10.16, 0, 0, "input"), ("SW", 1, 10.16, 5.08, 180, "output"), ("FB", 9, 10.16, 0, 180, "input"),
            ("GND", 6, 0, -10.16, 90), ("GND", 15, 0, -10.16, 90, "power_in", True), ("GND", 16, 0, -10.16, 90, "power_in", True),
            ("GND", 17, 0, -10.16, 90, "power_in", True)]
BOX = "(rectangle (start -7.62 7.62) (end 7.62 -7.62) (stroke (width 0.254) (type default)) (fill (type background)))"
TRIANGLE = "(polyline (pts (xy -7.62 7.62) (xy 7.62 0) (xy -7.62 -7.62) (xy -7.62 7.62)) (stroke (width 0.254) (type default)) (fill (type none)))"


def _reg(pins=REG_PINS, body=BOX):
    return ('(kicad_symbol_lib (symbol "REG_test" (property "Reference" "U" (at 0 10 0)) (property "Value" "REG_test" (at 0 -10 0)) '
            f'(symbol "REG_test_0_1" {body}) (symbol "REG_test_1_1" ' + " ".join(_pin(*p) for p in pins) + ")))")


REG = _reg()


def _sym(text, name):
    notes = []
    return K.convert_symbol([K._find_symbol(sexpr.parse(text), name)], name, None, [], None, notes), notes


def _tips(sym):
    out = {}
    for pr in sym["prims"]:
        if pr[0] == "pin":
            dx, dy = STEP_DIR[pr[2]]
            out[pr[1]["num"]] = (pr[3] + dx * pr[5], pr[4] + dy * pr[5])
    return out


def _put(text, name, rules=None):
    """Librarian.put_kicad_sym on a KiCad symbol (as src_kicad calls it) -> (symbol written, notes)."""
    st, got = {"name": name}, {}
    me = SimpleNamespace(rules=rules, put_sym=lambda st, sym, src, notes, kicad_types=None: got.update(sym=sym, notes=notes))
    L.Run.put_kicad_sym(me, st, [K._find_symbol(sexpr.parse(text), name)], name, None, f"kicad:Test:{name}")
    return got["sym"], got["notes"]


def test_kicad_50_mil_pin_ends_move_out_onto_the_100_mil_grid():
    for text, name in ((DIODE, "D_test"), (VERTICAL, "V_test")):
        sym, notes = _sym(text, name)
        tips = _tips(sym)
        assert all(v % 10 == 0 for t in tips.values() for v in t), (name, tips)
        assert any("100 mil grid" in n for n in notes), notes
    sym, _ = _sym(DIODE, "D_test")
    assert _tips(sym) == {"1": (-20, 0), "2": (20, 0)}                          # 150 mil -> 200 mil, outward
    pins = {pr[1]["num"]: pr for pr in sym["prims"] if pr[0] == "pin"}
    assert (pins["1"][3], pins["2"][3]) == (-5, 5)                                # the body end stays on the body
    assert (pins["1"][5], pins["2"][5]) == (15, 15)                               # 100 mil long -> 150 mil
    sym, _ = _sym(VERTICAL, "V_test")
    assert _tips(sym) == {"1": (0, 30), "2": (0, -30)}                            # 250 mil -> 300 mil


def test_pins_already_on_the_grid_are_left_alone():
    sym, notes = _sym(REG, "REG_test")
    assert not any("100 mil grid" in n for n in notes)
    assert {pr[5] for pr in sym["prims"] if pr[0] == "pin"} == {10}


def test_grid_fix_survives_the_altium_file():
    sym, _ = _sym(DIODE, "D_test")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "D_test.SchLib"
        AL.write_schlib(f, [sym])
        recs = AL.read_schlib(f)[1][0]["records"]
    tips = []
    for r in (r for r in recs if r.get("RECORD") == "2"):
        dx, dy = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}[r["orient"]]
        tips.append((r["x"] + dx * r["length"], r["y"] + dy * r["length"]))
    assert sorted(tips) == [(-20, 0), (20, 0)], tips


def test_stacked_kicad_power_pins_get_their_own_ends_by_default():
    sym, _ = _sym(REG, "REG_test")
    assert sorted(L.stacked_pins(sym)) == ["10, 11, 12 (VIN)", "6, 15, 16, 17 (GND)"]
    out, notes = _put(REG, "REG_test")
    assert not L.stacked_pins(out), L.stacked_pins(out)
    assert {pr[1]["num"] for pr in out["prims"] if pr[0] == "pin"} == {"1", "6", "9", "10", "11", "12", "13", "15", "16", "17"}
    assert not any(pr[1].get("hidden") for pr in out["prims"] if pr[0] == "pin")
    assert SL.check(out) == [], SL.check(out)
    assert any("stacked pins" in n and "VIN" in n for n in notes), notes


def test_stacking_kept_when_the_house_style_asks_for_it():
    rules = {"symbols": dict(RU.DEFAULTS["symbols"], stack_power="stack", layout_pins=0)}
    out, notes = _put(REG, "REG_test", rules)
    assert L.stacked_pins(out), "stack_power = stack keeps KiCad's drawing"
    assert not any("stacked pins" in n for n in notes)


def test_stacked_pins_on_a_non_box_symbol_are_reported_not_redrawn():
    out, notes = _put(_reg(body=TRIANGLE), "REG_test")
    assert L.stacked_pins(out)
    assert any("share one pin end" in n and "check it" in n for n in notes), notes


def test_unstacked_kicad_ic_is_kept_as_drawn():
    text = _reg([p for p in REG_PINS if len(p) < 7])                          # one pin per end: nothing to redraw
    rules = {"symbols": dict(RU.DEFAULTS["symbols"], layout_pins=0)}
    out, notes = _put(text, "REG_test", rules)
    assert not L.stacked_pins(out) and not any("stacked" in n for n in notes), notes
    assert out["prims"] == _sym(text, "REG_test")[0]["prims"]


# ------------------------------------------------------------------ 3D
def _pad(num, x, y, w=0.35, h=0.5):
    return f'(pad "{num}" smd rect (at {x} {y}) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask"))'


# a Bosch-style LGA-8: two rows along x, numbered clockwise from top left
LGA = ('(footprint "Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering" (layer "F.Cu") '
       '(fp_line (start -1.25 -1.25) (end 1.25 -1.25) (stroke (width 0.1) (type solid)) (layer "F.Fab")) '
       '(fp_line (start 1.25 -1.25) (end 1.25 1.25) (stroke (width 0.1) (type solid)) (layer "F.Fab")) '
       '(fp_line (start 1.25 1.25) (end -1.25 1.25) (stroke (width 0.1) (type solid)) (layer "F.Fab")) '
       '(fp_line (start -1.25 1.25) (end -1.25 -1.25) (stroke (width 0.1) (type solid)) (layer "F.Fab")) '
       + " ".join(_pad(n, x, y) for n, x, y in ((1, -0.975, -1.025), (2, -0.325, -1.025), (3, 0.325, -1.025), (4, 0.975, -1.025),
                                                (5, 0.975, 1.025), (6, 0.325, 1.025), (7, -0.325, 1.025), (8, -0.975, 1.025)))
       + ' (model "${KICAD9_3DMODEL_DIR}/Package_LGA.3dshapes/Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering.step"'
         ' (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0))))')


def _leads(fp):
    info = next(b for b in fp["bodies"] if b.get("step"))
    data = S.parse(info["step"]["data"].decode("ascii"))
    return data, info


def test_lga_name_gives_a_model_with_terminals_on_the_pads():
    fp, t = K.convert_footprint(LGA, None, "test", [], "")
    spec = S._lga_fit(S.spec_from_name(t[1]), fp["pads"])
    assert spec["rotation"] == 0
    terms = {n: (side, off) for n, side, off in spec["terminals"]}
    assert terms["1"] == ("T", -0.975) and terms["4"] == ("T", 0.975) and terms["5"] == ("B", 0.975)   # pad 1 top left, by the mark
    assert S.fit_rotation(spec, fp["pads"]) == 0                                 # every lead on its own numbered pad
    text, info = S.generate(t[1], 0.93, name="x", pads=fp["pads"])
    assert info["family"] == "qfn" and info["bbox"][5] == 0.93


def test_lga_turned_footprint_still_puts_pad_1_at_the_mark():
    pads = [dict(p, x=-p["y"], y=p["x"]) for p in K.convert_footprint(LGA, None, "test", [], "")[0]["pads"]]   # turned 90
    spec = S._lga_fit(S.spec_from_name("LGA-8_2.5x2.5mm_P0.65mm"), pads)
    assert S.fit_rotation(spec, pads) == spec["rotation"]
    one = next(t for t in spec["terminals"] if t[0] == "1")
    assert one[1] in "LT"


def test_kicad_step_download_no_longer_means_a_box():
    """The librarian does not embed KiCad's STEP (step=False); it used to extrude a box whenever one was downloaded.
    Now the downloaded model only sets the height of a generated, package-shaped one."""
    fetch, extent = K.fetch, K.step_extent
    K.fetch, K.step_extent = (lambda *a, **k: "ISO-10303-21;"), (lambda text, mi: (-1.2, -1.2, 0, 1.2, 1.2, 0.93))
    try:
        for text in (LGA, LGA.replace("Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering", "D_SMA")):
            notes = []
            fp, t = K.convert_footprint(text, None, "test", notes, "")
            K.add_body(fp, t, object(), None, False, notes, gen_step=True)
            assert any(b.get("step") for b in fp["bodies"]), (t[1], notes)
            assert any("generated STEP" in n and "0.93 mm (KiCad STEP model)" in n for n in notes), notes
            assert not any("extruded" in n for n in notes), notes
        notes = []
        fp, t = K.convert_footprint(LGA, None, "test", notes, "")
        K.add_body(fp, t, object(), None, False, notes, gen_step=False)                  # --box still means the box
        assert any("extruded body" in n for n in notes) and not any(b.get("step") for b in fp["bodies"])
    finally:
        K.fetch, K.step_extent = fetch, extent
