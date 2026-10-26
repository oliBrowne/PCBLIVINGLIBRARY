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


# ---------------------------------------------------------------- R-3D-2
def test_every_standard_table_height_sits_in_its_release_band():
    """release_check's plausibility band never flags the standard table's own height (MS-013 SOIC-20W at 2.65, SOP-8
    6.6 wide at 5.08, UFQFPN at 0.6): a correct body is not warned, nor held under [release] height = "hold"."""
    from ecadlib import footprint_gen as F, release_check as RC, stdparts as SP
    out = []
    for name, rec in SP.packages().items():
        hm = rec.get("height_max")
        if not hm:
            continue
        h = F.dim(hm)[2] if isinstance(hm, dict) else float(hm)
        for n in (name, f"{rec.get('lib') or 'Package_SO'}:{name}"):
            lo, hi, _ = RC.height_range(n)
            if not lo - 0.01 <= h <= hi + 0.01:
                out.append((n, h, lo, hi))
    assert not out, out[:10]


def test_wide_soic_band_by_body_width_and_narrow_band_kept():
    from ecadlib import release_check as RC
    for n in ("SOIC-20_L12.8-W7.5-P1.27-LS10.3-BL", "SOIC-24W_7.5x15.4mm_P1.27mm_Custom", "SOP-28_8.4x18.0mm_P1.27mm_X"):
        lo, hi, _ = RC.height_range(n)
        assert lo <= 2.65 <= hi, (n, lo, hi)
    assert RC.height_range("SOIC-8_3.9x4.9mm_P1.27mm")[1] < 2.0          # a narrow SOIC at 2.65 is still flagged
    assert RC.height_range("SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL")[1] < 2.0


# ---------------------------------------------------------------- R-3D-3
def test_generated_height_takes_the_standard_table_height():
    for n, h in (("SOP-20_7.5x12.8mm_P1.27mm", 2.65), ("PQFP-208_28x28mm_P0.5mm", 3.6), ("SOP-8_6.605x9.655mm_P2.54mm", 5.08)):
        s = SG.spec_from_name(n)
        assert s and SG.default_height(s) == h, (n, s and SG.default_height(s))
        _, info = SG.build(s)
        assert info["height"] == h and "standard table" in info["height_src"], info


def _offline_kicad_body(name):
    """The KiCad route with no KiCad STEP (offline / cache miss): the model and its height are made here."""
    from ecadlib import footprint_gen as F, kicad_import as K
    spec = json.loads((Path(SG.__file__).with_name("package_specs") / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    text, _ = F.build(spec)
    notes = []
    fp, t = K.convert_footprint(text, name, "kicad:x", notes)
    t = (t[0], name, *t[2:])
    K.add_body(fp, t, None, None, False, notes, gen_step=True)
    return fp, [n for n in notes if n.startswith("3D")]


def test_offline_family_height_is_labelled_a_package_default():
    """A height from step_gen's per-family guess (no spec, no table height) says so: release_check's package-default
    warning ("verify against the datasheet") then fires; a table height is labelled as the table's."""
    from ecadlib import stdparts as SP
    n = "SOP-8_3.9x4.9mm_P1.27mm"
    assert n not in SP.packages()
    fp, notes = _offline_kicad_body(n)
    assert any(b.get("step") for b in fp["bodies"]), notes
    assert any("package default" in x and "verify" in x for x in notes), notes
    fp, notes = _offline_kicad_body("SOP-20_7.5x12.8mm_P1.27mm")       # in the table: its height and its label
    assert any("2.65 mm (standard table height_max)" in x for x in notes), notes
