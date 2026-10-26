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
