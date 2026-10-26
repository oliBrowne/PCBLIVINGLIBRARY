"""2026-10-26 bug hunt (gate): dedupe fingerprints, lifecycle maker match, release gate statuses.
Run:  python3 -m tests.test_hunt1026_gate"""
import math, random, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, dedupe as DD, kicad_import as K

FP = """(footprint "X"
  (layer "F.Cu")
  (fp_line (start -3 -3) (end 3 -3) (layer "F.CrtYd") (width 0.05))
  (fp_line (start 3 -3) (end 3 3) (layer "F.CrtYd") (width 0.05))
  (fp_line (start 3 3) (end -3 3) (layer "F.CrtYd") (width 0.05))
  (fp_line (start -3 3) (end -3 -3) (layer "F.CrtYd") (width 0.05))
  (pad "1" smd rect (at -1.5 0 {r1}) (size {s1}) (layers "F.Cu" "F.Mask" "F.Paste"))
  (pad "2" smd rect (at 1.5 0 {r2}) (size {s2}) (layers "F.Cu" "F.Mask" "F.Paste"))
)"""


def _fp(r, s="1 2"):
    return FP.format(r1=r, r2=r, s1=s, s2=s)


def _hk(text):
    return DD.fp_hash(DD.fp_geometry_kicad(text))


def _hp(text):
    with tempfile.TemporaryDirectory() as d:
        fp, _ = K.convert_footprint(text, "X")
        AL.write_pcblib(Path(d, "a.PcbLib"), [fp])
        return DD.fp_hash(DD.fp_geometry_pcblib(AL.read_pcblib(Path(d, "a.PcbLib"))[0]))


def test_gate01_diagonal_pads_45_and_135_are_different_footprints():
    # a 1x2 pad at 45 degrees and the same pad at 135 (or -45) have their long side on opposite diagonals
    assert _hk(_fp(45)) != _hk(_fp(135))
    assert _hk(_fp(45)) != _hk(_fp(-45))
    # ... while a 1x2 pad at 135 IS a 2x1 pad at 45, and a turn of 180 is no change
    assert _hk(_fp(135)) == _hk(_fp(45, "2 1"))
    assert _hk(_fp(30)) == _hk(_fp(210))
    for a, b in ((0, 360), (90, 270), (90, -90), (0, 180), (0, 359.99999), (90, 89.99999)):
        assert _hk(_fp(a)) == _hk(_fp(b)), (a, b)
    for r in (45, 135, 30):
        assert _hk(_fp(r)) == _hp(_fp(r)), r            # .PcbLib and .kicad_mod still hash alike


def _corners(x, y, w, h, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return sorted((round(x + c * u - s * v, 3), round(y + s * u + c * v, 3))
                  for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)))


def test_gate01_pad_tuple_equal_iff_same_copper_property():
    # seeded property: two rectangular pads get the same tuple exactly when their copper outlines coincide
    rnd = random.Random(1026)
    angles = [0, 15, 30, 45, 60, 90, 120, 135, 180, 225, 270, 315, -45, -90, 360]
    sizes = [(1.0, 2.0), (2.0, 1.0), (1.5, 1.5)]
    for _ in range(400):
        (w1, h1), (w2, h2) = rnd.choice(sizes), rnd.choice(sizes)
        a1, a2 = rnd.choice(angles), rnd.choice(angles)
        same = _corners(0, 0, w1, h1, a1) == _corners(0, 0, w2, h2, a2)
        t1, t2 = DD._pad_tuple("1", 0, 0, w1, h1, a1, False), DD._pad_tuple("1", 0, 0, w2, h2, a2, False)
        assert (t1 == t2) == same, ((w1, h1, a1), (w2, h2, a2), t1, t2)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
