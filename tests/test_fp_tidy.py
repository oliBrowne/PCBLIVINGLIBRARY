"""fp_tidy: converted (EasyEDA) footprints tidied to the rule book."""
import json
from pathlib import Path

from ecadlib import easyeda_convert as EC, footprint_rules as FR, fp_tidy as T, sexpr

FIX = Path(__file__).parent / "fixtures" / "easyeda" / "C2002.json"


def _soic8():
    """Shaped like the NE555DR footprint EasyEDA gave in Oliver's Altium run (16 Oct): sharp pads, a silk rectangle
    on the body edges, a big pin-1 dot on silk under the body, the reference on fab at the centre."""
    pads = []
    for i in range(4):
        y = -1.905 + 1.27 * i
        pads.append(f'(pad "{i + 1}" smd rect (at -2.7 {y:.3f}) (size 1.55 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))')
        pads.append(f'(pad "{8 - i}" smd rect (at 2.7 {y:.3f}) (size 1.55 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))')
    fab = [((-1.95, -2.45), (1.95, -2.45)), ((1.95, -2.45), (1.95, 2.45)), ((1.95, 2.45), (-1.95, 2.45)), ((-1.95, 2.45), (-1.95, -2.45))]
    lines = "\n".join(f'(fp_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (stroke (width 0.1) (type solid)) (layer "F.Fab"))' for a, b in fab)
    return f'''(footprint "SOIC-8_EE" (version 20221018) (generator ecadlib_easyeda) (layer "F.Cu")
  (attr smd)
  (fp_text reference "REF**" (at 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
  (fp_text value "SOIC-8_EE" (at 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
  (fp_line (start -2.0 -2.5) (end 2.0 -2.5) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_line (start -2.0 2.5) (end 2.0 2.5) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_circle (center -1.3 -1.8) (end -1.0 -1.8) (stroke (width 0.12) (type solid)) (fill solid) (layer "F.SilkS"))
  {lines}
  (fp_rect (start -3.73 -2.7) (end 3.73 2.7) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))
  {chr(10).join(pads)}
)
'''


def test_soic_like_the_altium_ne555():
    before = _soic8()
    notes = []
    after = T.tidy(before, notes)
    t = sexpr.parse(after)
    pads = list(sexpr.find(t, "pad"))
    assert all(p[3] == "roundrect" and sexpr.first(p, "roundrect_rratio")[1] == "0.25" for p in pads)
    assert [(p[1], sexpr.first(p, "at")[1:3], sexpr.first(p, "size")[1:3]) for p in pads] == \
        [(p[1], sexpr.first(p, "at")[1:3], sexpr.first(p, "size")[1:3]) for p in sexpr.find(sexpr.parse(before), "pad")]   # copper unchanged
    ref = next(x for x in sexpr.find(t, "fp_text") if x[1] == "reference")
    assert sexpr.first(ref, "layer")[1] == "F.SilkS" and float(sexpr.first(ref, "at")[2]) <= -3.9     # above the courtyard,
    #                                       far enough that the g of Altium's ".Designator" clears it (PR #29 Altium run)
    val = next(x for x in sexpr.find(t, "fp_text") if x[1] == "value")
    assert sexpr.first(val, "layer")[1] == "F.Fab" and float(sexpr.first(val, "at")[2]) >= 3.7      # below it, off the body
    assert any(x[1] == "user" and x[2] == "${REFERENCE}" and sexpr.first(x, "layer")[1] == "F.Fab" for x in sexpr.find(t, "fp_text"))
    circles = [c for c in sexpr.find(t, "fp_circle") if sexpr.first(c, "layer")[1] == "F.SilkS"]
    assert len(circles) == 1 and float(sexpr.first(circles[0], "center")[1]) < -3.4, circles             # dot outside, by pad 1
    fab = [x for x in sexpr.find(t, "fp_line") if sexpr.first(x, "layer")[1] == "F.Fab"]
    assert len(fab) == 5                                                                             # chamfered pin-1 corner
    assert any("value moved below" in n for n in notes)
    assert any("under the part body removed" in n for n in notes) and any("pin 1 dot added" in n for n in notes), notes
    rules = lambda text: {(lvl, r) for lvl, r, _ in FR.check(text)}
    assert not any(lvl == "error" for lvl, _ in rules(after)), FR.check(after)
    fixed = rules(before) - rules(after)
    assert {("warning", "padshape"), ("warning", "polarity")} <= fixed, (rules(before), rules(after))
    assert not any(r == "text" for _, r in rules(after)), FR.check(after)


def test_easyeda_fixture_loses_its_warnings_and_is_idempotent():
    d = json.loads(FIX.read_text())
    raw = EC.footprint_to_kicad(d["result"]["packageDetail"]["dataStr"], "SOT23X")
    once = T.tidy(raw, [])
    w = lambda text: [m for lvl, r, m in FR.check(text) if lvl == "warning" and r not in ("meta", "model")]
    assert len(w(raw)) >= 5 and w(once) == [], w(once)
    notes = []
    assert T.tidy(once, notes) == once and notes == []                                                # nothing left to do


def test_two_pad_parts_and_tht_left_alone():
    chip = '''(footprint "R_EE" (layer "F.Cu") (attr smd)
  (fp_text reference "REF**" (at 0 -1.5) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
  (fp_line (start -0.5 -0.25) (end 0.5 -0.25) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_line (start 0.5 -0.25) (end 0.5 0.25) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_line (start 0.5 0.25) (end -0.5 0.25) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_line (start -0.5 0.25) (end -0.5 -0.25) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (pad "1" smd rect (at -0.5 0) (size 0.6 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 0.5 0) (size 0.6 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
)
'''
    notes = []
    t = sexpr.parse(T.tidy(chip, notes))
    assert len([x for x in sexpr.find(t, "fp_line") if sexpr.first(x, "layer")[1] == "F.Fab"]) == 4    # no chamfer: not polar
    assert not list(sexpr.find(t, "fp_circle"))
    tht = chip.replace("smd rect", "thru_hole rect").replace("(attr smd)", "(attr through_hole)")
    assert all(p[3] == "rect" for p in sexpr.find(sexpr.parse(T.tidy(tht, [])), "pad"))
