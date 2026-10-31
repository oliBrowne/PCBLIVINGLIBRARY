"""Review 2026-10-29 (round two), geometry: footprint maths checked against KiCad's own conventions."""
from ecadlib import footprint_rules as FR


def _fp(pad_y, rot, just="left", unlocked=False):
    """A reference "REF**" (1 mm) anchored at (3, 0), turned rot, and pad 1 at (3, pad_y)."""
    at = f"3 0 {rot}" + (" unlocked" if unlocked else "")
    return f'''(footprint "X" (layer "F.Cu")
  (fp_text reference "REF**" (at {at}) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15)) (justify {just})))
  (fp_text value "X" (at 0 5) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
  (pad "1" smd roundrect (at 3 {pad_y}) (size 0.6 0.6) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
  (pad "2" smd roundrect (at -3 0) (size 0.6 0.6) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
)'''


def _over_pad(text):
    return [m for _, r, m in FR.check(text, geometry=False) if r == "silktext" and "lies over pad" in m]


def test_text_at_90_reads_upwards_in_kicad_frame():
    """footprint_rules.text_box turned a 90 degree text towards +y (KiCad y down): a left-justified reference that runs
    up over a pad was passed, and one clear of a pad below it was held."""
    # KiCad turns text counter-clockwise on screen (y down): at 90 it reads bottom to top, so left-justified text runs
    # from its anchor towards -y. "REF**" counts as 3 characters (NOMINAL_REF) of 0.927 x 1 mm: y from -2.78 to 0
    assert FR.text_box(3, 0, 1.0, 3, 90, 0.0) == (2.5, -2.781, 3.5, 0.0)
    assert _over_pad(_fp(-1.8, 90)), "the reference runs over pad 1 above its anchor"
    assert not _over_pad(_fp(1.8, 90)), "pad 1 below the anchor is clear of the text"


def test_text_kept_upright_like_kicad_and_altium_export():
    """footprint_rules.text_box drew a left-justified 180 degree reference to the left of its anchor; KiCad keeps
    footprint text upright (no "unlocked"), drawing it as at 0, as altium_lib.texts_from_kicad does."""
    assert FR.text_box(0, 0, 1.0, 3, 180, 0.0) == (0.0, -0.5, 2.781, 0.5)
    assert FR.text_box(0, 0, 1.0, 3, 270, 0.0) == FR.text_box(0, 0, 1.0, 3, 90, 0.0)
    # "unlocked": the angle as written (270: reads top to bottom, towards +y)
    assert FR.text_box(0, 0, 1.0, 3, 270, 0.0, upright=False) == (-0.5, 0.0, 0.5, 2.781)
    assert _over_pad(_fp(1.8, 270, unlocked=True)) and not _over_pad(_fp(-1.8, 270, unlocked=True))
    # centred text is the same box whichever way it is turned
    assert FR.text_box(1, 2, 1.0, 3, 90) == FR.text_box(1, 2, 1.0, 3, 270, upright=False)
