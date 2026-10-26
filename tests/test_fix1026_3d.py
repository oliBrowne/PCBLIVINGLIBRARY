"""Fix 2026-10-26 (3d): generated 3D models and their heights (review 196, R-3D-1..4). Pads are written the way KiCad
draws them (model coordinates, y up, pin 1 top left, counted anticlockwise)."""
import json
from pathlib import Path

from ecadlib import step_gen as SG


def _pad(n, x, y, w, h):
    return {"designator": str(n), "x": x, "y": y, "w": w, "h": h, "rot": 0}


def _dual(n, e, x, w=1.9, h=0.6):
    k = n // 2
    left = [_pad(i + 1, -x, (k - 1) / 2 * e - i * e, w, h) for i in range(k)]
    right = [_pad(k + i + 1, x, -(k - 1) / 2 * e + i * e, w, h) for i in range(k)]
    return left + right


def _quad(nlr, ntb, e, x, y, plen, pw, ep=None):
    """Left top->bottom, bottom left->right, right bottom->top, top right->left."""
    pads, k = [], 1
    for side, cnt in (("L", nlr), ("B", ntb), ("R", nlr), ("T", ntb)):
        for i in range(cnt):
            o = (i - (cnt - 1) / 2) * e
            if side == "L":
                pads.append(_pad(k, -x, -o, plen, pw))
            elif side == "R":
                pads.append(_pad(k, x, o, plen, pw))
            elif side == "B":
                pads.append(_pad(k, o, -y, pw, plen))
            else:
                pads.append(_pad(k, -o, y, pw, plen))
            k += 1
    if ep:
        pads.append(_pad(k, 0, 0, *ep))
    return pads


# ---------------------------------------------------------------- R-3D-1
def test_long_axial_lead_foot_sits_on_its_hole():
    """A long axial lead's bounding box centre is half way between the body and the hole; its foot is in the hole."""
    s = SG.spec_from_name("R_Axial_DIN0207_L6.3mm_D2.5mm_P15.24mm_Horizontal")
    pads = [_pad(1, 0, 0, 1.6, 1.6), _pad(2, 15.24, 0, 1.6, 1.6)]
    assert SG.fit_rotation(s, pads) == 0


def test_kicad_so20_name_is_length_by_width():
    """KiCad's SO-20_12.8x7.5mm is 12.8 long (along the pin rows) by 7.5 wide: the model is not turned 90 degrees."""
    n = "SO-20_12.8x7.5mm_P1.27mm"
    s = SG.spec_from_name(n)
    assert (SG._n(s, "body_width"), SG._n(s, "body_length")) == (7.5, 12.8), s
    _, info = SG.generate(n, pads=_dual(20, 1.27, 4.65, 2.0, 0.6))
    assert info.get("leads_on_pads") is True and info["rotation"] == 0, info
    assert SG._n(SG.spec_from_name("SOIC-8_3.9x4.9mm_P1.27mm"), "body_width") == 3.9          # the usual W x L untouched


def test_rectangular_quad_takes_its_per_side_counts_from_the_pads():
    for n, pads in (("QFN-28-1EP_3x6mm_P0.5mm_EP1.7x4.75mm", _quad(10, 4, 0.5, 1.45, 2.95, 0.8, 0.25, (1.7, 4.75))),
                    ("QFN-20-1EP_4x5mm_P0.5mm_EP2.65x3.65mm", _quad(6, 4, 0.5, 1.95, 2.45, 0.8, 0.25, (2.65, 3.65))),
                    ("LQFP-128_14x20mm_P0.5mm", _quad(38, 26, 0.5, 7.75, 10.75, 1.5, 0.3)),
                    ("QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", _quad(4, 4, 0.5, 1.45, 1.45, 0.8, 0.25, (1.7, 1.7)))):
        _, info = SG.generate(n, pads=pads)
        assert info.get("leads_on_pads") is True and info["rotation"] == 0, (n, info)


def test_no_rotation_fits_raises_and_add_body_extrudes_with_a_note():
    """Leads that sit on no pad in any rotation: no generated STEP (it would ship leads off the pads); the
    footprint gets the extruded body over its outline instead, with a note saying why."""
    from ecadlib import footprint_gen as F, kicad_import as K
    n = "SOIC-8_3.9x4.9mm_P1.27mm"
    wrong = [_pad(i + 1, i * 1.27, 0, 0.6, 1.9) for i in range(8)]          # eight pads in one row
    try:
        SG.generate(n, pads=wrong)
    except ValueError as e:
        assert "pad" in str(e), e
    else:
        raise AssertionError("a model whose leads sit on no pad was kept")
    spec = json.loads((Path(SG.__file__).with_name("package_specs") / f"{n}.json").read_text())
    text, _ = F.build(spec)
    notes = []
    fp, t = K.convert_footprint(text, n, "datasheet:x", notes)
    for p in fp["pads"]:
        p["x"], p["y"] = p["y"] * 3, p["x"] / 3            # rows moved where no turn of the model reaches them
    K.add_body(fp, t, None, 1.75, False, notes, gen_step=True, spec=spec, height_src="datasheet height_max")
    assert not any(b.get("step") for b in fp["bodies"]), fp["bodies"]
    assert any("no generated STEP" in x for x in notes) and any("extruded body" in x for x in notes), notes
    _, ok = SG.generate(n, pads=_dual(8, 1.27, 2.475, 1.95, 0.6))           # the right pads still get the model
    assert ok["leads_on_pads"] is True
