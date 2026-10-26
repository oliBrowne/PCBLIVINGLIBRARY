"""Fixes 2026-10-26 (fixes12 review: R-FX12-1..5). Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_fx12"""
import math

from tests.fixtures_lite import lite


def _hole_findings(text):
    from ecadlib import footprint_check as FC
    return [i for i in FC.check(text) if "from the hole" in i[2]]


# ---------------------------------------------------------------- R-FX12-1: NPTH clearance to a custom pad's real copper
def _ring_fp(ring, hole="0.5 0.5", other=""):
    """An NPTH at the origin and custom pad 5 whose copper rings it (Knowles LGA-5 / Molex Nano-Fit style)."""
    return f'''(footprint "T" (layer "F.Cu")
  (fp_rect (start -3 -3) (end 3 3) (stroke (width 0.05)) (fill none) (layer "F.CrtYd"))
  (pad "" np_thru_hole circle (at 0 0) (size {hole}) (drill {hole.split()[0]}) (layers "*.Cu" "*.Mask"))
  (pad "5" smd custom (at 0.75 0) (size 0.2 0.2) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor circle))
    (primitives {ring}))
  {other}
)'''


RING = "(gr_circle (center -0.75 0) (end -0.15 0) (width 0.2))"      # copper from r 0.5 to 0.7 round the hole


def test_fx12_1_ring_round_the_hole_is_measured_from_the_hole():
    """Before: the ring's bounding box covers the hole: 'pad 5 is 0.000 mm from the hole' (a hard, false error)."""
    got = _hole_findings(_ring_fp(RING))               # real gap: 0.5 - 0.25 = 0.25 mm
    assert not got, got


def test_fx12_1_ring_too_close_and_copper_over_the_hole_still_caught():
    near = _ring_fp("(gr_circle (center -0.75 0) (end -0.45 0) (width 0.2))")   # copper from r 0.2: in the hole
    got = _hole_findings(near)
    assert got and got[0][0] == "error", got
    tight = _ring_fp("(gr_circle (center -0.75 0) (end -0.38 0) (width 0.1))")   # copper from r 0.32: 0.07 mm
    got = _hole_findings(tight)
    assert got and got[0][0] == "warning" and 0.06 < float(got[0][2].split(" is ")[1].split()[0]) < 0.08, got
    disc = _ring_fp("(gr_circle (center -0.75 0) (end -0.15 0) (width 0) (fill yes))")   # a filled disc over the hole
    got = _hole_findings(disc)
    assert got and got[0][0] == "error", got


def test_fx12_1_oval_hole_measured_along_its_slot():
    """A 1.2 x 0.5 mm slot: the ring 0.5 mm from its centre is inside the slot's ends (x +-0.6)."""
    got = _hole_findings(_ring_fp(RING, hole="1.2 0.5").replace("(drill 1.2)", "(drill oval 1.2 0.5)"))
    assert got and got[0][0] == "error", got


# ---------------------------------------------------------------- R-FX12-5: a pad at an odd angle is its turned shape
def _diamond_ep(p1, size1="0.2 0.2", ep_shape="rect", extra=""):
    """UDFN-4-1EP_1x1mm / OnSemi XDFN4-1EP style: the exposed pad turned 45 degrees between corner pads."""
    return f'''(footprint "T" (layer "F.Cu")
  (fp_rect (start -1 -1) (end 1 1) (stroke (width 0.05)) (fill none) (layer "F.CrtYd"))
  (pad "1" smd rect (at {p1}) (size {size1}) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "5" smd {ep_shape} (at 0 0 45) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask"){extra})
)'''


def _copper(text):
    from ecadlib import footprint_check as FC
    return [i for i in FC.check(text) if i[1] == "copper" and "pad" in i[2]]


def test_fx12_5_pad_beside_a_turned_pad_is_no_overlap():
    """Before: the 45 degree pad was its bounding box (+-0.354 mm): 'pads 1 and 5 overlap', a hard, false error.
    Its real edge (x + y = 0.354) is 0.10 mm from pad 1's corner (0.25, 0.25)."""
    got = _copper(_diamond_ep("0.35 0.35"))
    assert not any(i[0] == "error" for i in got), got
    assert any("0.10" in i[2] for i in got), got                       # still below the solder-mask web: a warning


def test_fx12_5_turned_pad_overlap_still_caught():
    got = _copper(_diamond_ep("0.2 0.2"))
    assert any(i[0] == "error" and "overlap" in i[2] for i in got), got
    # roundrect and a turned circle: a circle is round at any angle (its box is no bigger)
    rr = _diamond_ep("0.35 0.35", ep_shape="roundrect", extra=" (roundrect_rratio 0.25)")
    assert not any(i[0] == "error" for i in _copper(rr)), _copper(rr)
    circ = _diamond_ep("0.45 0", ep_shape="circle")                   # circle r 0.25, pad 1 from x 0.35: 0.10 mm
    assert not any(i[0] == "error" for i in _copper(circ)), _copper(circ)


def test_fx12_5_turned_pad_shape():
    from ecadlib import footprint_check as FC, sexpr
    p = [q for q in FC._pad_rects(sexpr.parse(_diamond_ep("3 3"))) if q["num"] == "5"][0]
    assert p["polys"] and abs(FC._pt_pad(0.354, 0.0, p)) < 0.002 and 0.16 < FC._pt_pad(0.3, 0.3, p) < 0.18, p
    assert abs(p["w"] - 0.707) < 0.002 and abs(p["h"] - 0.707) < 0.002, p      # the box is still the turned box


# ---------------------------------------------------------------- R-FX12-3: a KiCad pin type beats a signed-supply name
def _sides(pins):
    from ecadlib import symbol_layout as SL
    return {p["name"]: p["side"] for p in SL.arrange_pins([{"num": n, "name": nm, "type": t} for n, nm, t in pins])}


def test_fx12_3_typed_signal_pins_named_like_supplies_keep_their_type():
    """Before: ZXCT1009F's Vs+ / Vs- (typed input: the sense inputs) went top / bottom, and MAX232's VS+ / VS- (typed
    power_out: the charge pump's outputs) too."""
    s = _sides([("1", "Vs-", "input"), ("2", "Vs+", "input"), ("3", "Iout", "output")])
    assert s["Vs+"] == "left" and s["Vs-"] == "left", s
    s = _sides([("2", "VS+", "power_out"), ("6", "VS-", "power_out"), ("16", "VCC", "power_in"), ("15", "GND", "power_in"),
                ("11", "T1IN", "input"), ("14", "T1OUT", "output")])
    assert s["VS+"] == "right" and s["VS-"] == "right" and s["VCC"] == "top" and s["GND"] == "bottom", s


def test_fx12_3_signed_supplies_still_top_and_bottom_when_power_passive_or_untyped():
    for t in ("power_in", "power", "passive", "", "unspecified"):
        s = _sides([("1", "+VS", t), ("2", "-VS", t), ("3", "VCC+", t), ("4", "-5V", t), ("5", "IN+", "input"),
                    ("6", "OUT", "output")])
        assert s["+VS"] == "top" and s["VCC+"] == "top" and s["-VS"] == "bottom" and s["-5V"] == "bottom", (t, s)


def test_fx12_3_counter_carry_and_borrow_outputs_go_right():
    """74LS192 (untyped, EasyEDA): ~{CO} / ~{BO} were 'other' (left, or right only to square the symbol); TC / RCO
    (74HC161) likewise. They are outputs: the right side, whatever else the symbol holds."""
    from ecadlib import symbol_layout as SL
    for n in ("~{CO}", "~{BO}", "CO", "BO", "TC", "~{TC}", "RCO", "~{RCO}"):
        assert SL.classify({"num": "12", "name": n, "type": ""}) == "other_out", n
        assert SL.classify({"num": "12", "name": n, "type": "input"}) == "other_in", n     # a type still says it
    s = _sides([("1", "A", ""), ("2", "B", ""), ("3", "C", ""), ("4", "D", ""), ("5", "UP", ""), ("6", "DOWN", ""),
                ("12", "~{CO}", ""), ("13", "~{BO}", ""), ("8", "GND", ""), ("16", "VCC", "")])
    assert s["~{CO}"] == "right" and s["~{BO}"] == "right", s
