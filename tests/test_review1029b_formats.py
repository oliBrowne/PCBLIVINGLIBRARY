"""Round two review (formats): KiCad / Altium file round trips."""
from tests.fixtures_lite import lite

from ecadlib import sexpr


def test_courtyard_bbox_counts_arc_segments_of_a_polygon():
    """sexpr.courtyard_bbox ignored the (arc ...) segments KiCad 7+ writes inside an fp_poly's (pts ...)."""
    t = sexpr.parse('(footprint "X" (fp_poly (pts (xy -3 -3) (xy 3 -3) (xy 3 3) (xy -3 3) '
                    '(arc (start -3 3) (mid -3.5 0) (end -3 -3))) (stroke (width 0.05) (type solid)) (fill none) '
                    '(layer "F.CrtYd")))')
    bb = sexpr.courtyard_bbox(t)
    assert bb is not None and abs(bb[0] - -3.5) < 1e-9 and bb[1:] == (-3.0, 3.0, 3.0), bb


@lite
def test_libpkg_is_ansi_like_the_install_scripts(tmp_path):
    """The .LibPkg was UTF-8 with no BOM: Altium reads it as ANSI (as the .PrjScr), so 'Résistance' was not found."""
    import json
    from pathlib import Path
    from ecadlib import altium_lib as AL, altium_pkg as PKG
    spec = json.loads((Path(__file__).resolve().parents[1] / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    fp = dict(AL.footprint_from_spec(spec)[0], name="Résistance")
    src = tmp_path / "src"
    (src / "PcbLib").mkdir(parents=True)
    AL.write_pcblib(src / "PcbLib" / "Résistance.PcbLib", [fp])
    rep = PKG.build(src, tmp_path / "pkg", "mylib", log=lambda *a: None, unchecked=True)
    data = (tmp_path / "pkg" / "mylib.LibPkg").read_bytes()
    assert b"DocumentPath=PcbLib\\R\xe9sistance.PcbLib\r\n" in data, data[-400:]
    assert not rep["problems"] and rep["footprints"] == 1, rep["problems"]
