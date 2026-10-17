"""Regressions from the review of the Fusion / EAGLE and EasyEDA Pro exporters: rotated slots, slots on a pad drawn as
a polygon, and the mask margin of a round or square through-hole pad must read back the same as the KiCad pad."""
from ecadlib import cad_model as CM, eagle_export as G, easyeda_pro_export as E

PADS = [
    '(pad "1" thru_hole oval (at 1 2 30) (size 1.5 2.5) (drill oval 0.8 1.6) (layers "*.Cu" "*.Mask"))',
    '(pad "" np_thru_hole oval (at 1 2 30) (size 1 2) (drill oval 1 2) (layers "*.Cu" "*.Mask"))',
    '(pad "1" thru_hole oval (at 0 0 90) (size 1.2 2.4) (drill oval 0.6 1.6 (offset 0 0.2)) (layers "*.Cu" "*.Mask"))',
    '(pad "1" thru_hole circle (at 1 2) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask") (solder_mask_margin 0.3))',
    '(pad "1" thru_hole rect (at 1 2) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask") (solder_mask_margin -0.05))',
]


def test_pads_read_back_without_errors():
    for pad in PADS:
        fp = f'(footprint "T" (layer "F.Cu")\n {pad})'
        for mod in (G, E):
            errs = [d["msg"] for d in mod.check_footprint(fp, "T") if d["level"] == "error"]
            assert not errs, (mod.__name__, pad, errs)


def test_custom_through_hole_pad_keeps_its_copper():
    fp = '''(footprint "T" (layer "F.Cu")
 (pad "1" thru_hole custom (at 0 0) (size 1.5 1.5) (drill 1) (layers "*.Cu" "*.Mask")
  (options (clearance outline) (anchor circle))
  (primitives (gr_poly (pts (xy 0 0.5) (xy 3 0.5) (xy 3 -0.5) (xy 0 -0.5)) (width 0) (fill yes)))))'''
    pk, _, _ = G.package(CM.footprint(fp), "T")
    polys = {el.get("layer") for el in pk.iter("polygon")}
    assert {"1", "16"} <= polys, polys                   # the tab is copper on both sides, not only the round pad
    errs = [d["msg"] for d in G.check_footprint(fp, "T") if d["level"] == "error"]
    assert not errs, errs
