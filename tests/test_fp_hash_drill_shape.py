"""Footprint sharing / dedupe compared pads and courtyard but not the hole or the pad shape: a header with 1.0 mm
drills got the file of one with 0.8 mm drills. Run:  python3 -m tests.test_fp_hash_drill_shape"""
import tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, dedupe as DD, fp_share as FS, kicad_import as K

HDR = """(footprint "HDR_1x2"
  (layer "F.Cu")
  (fp_line (start -1.5 -2.8) (end 1.5 -2.8) (layer "F.CrtYd") (width 0.05))
  (fp_line (start 1.5 -2.8) (end 1.5 2.8) (layer "F.CrtYd") (width 0.05))
  (fp_line (start 1.5 2.8) (end -1.5 2.8) (layer "F.CrtYd") (width 0.05))
  (fp_line (start -1.5 2.8) (end -1.5 -2.8) (layer "F.CrtYd") (width 0.05))
  (pad "1" thru_hole rect (at 0 -1.27) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole circle (at 0 1.27) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask"))
)
"""


def _h(text):
    return DD.fp_hash(DD.fp_geometry_kicad(text))


def test_drill_and_shape_change_the_hash():
    base = _h(HDR)
    assert _h(HDR.replace("(drill 1.0)", "(drill 0.8)", 1)) != base, "drill"
    assert _h(HDR.replace("thru_hole circle", "thru_hole rect", 1)) != base, "pad shape"
    assert _h(HDR.replace("(drill 1.0)", "(drill oval 1.0 1.6)", 1)) != base, "slot"
    assert _h(HDR.replace("thru_hole circle", "thru_hole oval", 1)) == base, "circle and oval are both round"
    assert _h(HDR) == base


def test_pcblib_and_kicad_still_hash_alike_with_holes():
    for text in (HDR, HDR.replace("(drill 1.0)", "(drill oval 1.0 1.6)", 1)):
        with tempfile.TemporaryDirectory() as d:
            fp, _ = K.convert_footprint(text, "HDR_1x2")
            AL.write_pcblib(Path(d, "a.PcbLib"), [fp])
            g = DD.fp_geometry_pcblib(AL.read_pcblib(Path(d, "a.PcbLib"))[0])
            assert g["kinds"] == DD.fp_geometry_kicad(text)["kinds"], (g["kinds"], DD.fp_geometry_kicad(text)["kinds"])
            assert DD.fp_hash(g) == _h(text)


def test_share_gives_a_different_drill_its_own_file():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        models = out / "models"
        name, pcb, same, h = FS.pick({}, models, "HDR_1x2", HDR, "", "J1", out, {})
        written = {pcb: {"name": name, "file": str(Path(pcb).relative_to(out)), "hash": h}}
        n2, pcb2, same2, h2 = FS.pick(written, models, "HDR_1x2", HDR.replace("(drill 1.0)", "(drill 0.8)"), "", "J2", out, {})
        assert same2 is None and pcb2 != pcb and n2 == "HDR_1x2_J2", (n2, pcb2)
        n3, pcb3, same3, _ = FS.pick(written, models, "HDR_1x2", HDR, "", "J3", out, {})
        assert same3 is not None and pcb3 == pcb, "identical holes still share"
