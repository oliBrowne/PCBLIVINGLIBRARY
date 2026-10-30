"""Real AD26 (2026-10-30): Compile Integrated Library keeps only the footprints a symbol's footprint model names, so
the demo's IntLib held 1 of its 3 footprints and the README promised every part with its footprint linked."""
import json
from pathlib import Path

from tests.fixtures_lite import lite

SPEC = Path(__file__).resolve().parents[1] / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json"


@lite
def test_footprints_no_symbol_links_are_named_in_the_report_and_readme(tmp_path):
    """altium_pkg: a footprint no symbol links to is left out of the IntLib by Altium; the package says so."""
    from ecadlib import altium_lib as AL, altium_pkg as PKG
    fp = AL.footprint_from_spec(json.loads(SPEC.read_text(encoding="utf-8")))[0]
    src = tmp_path / "src"
    (src / "PcbLib").mkdir(parents=True)
    AL.write_pcblib(src / "PcbLib" / "Lone.PcbLib", [dict(fp, name="LONE_SOIC8")])
    rep = PKG.build(src, tmp_path / "pkg", "mylib", log=lambda *a: None, unchecked=True)
    assert rep["unlinked_footprints"] == ["LONE_SOIC8"], rep.get("unlinked_footprints")
    assert not rep["problems"], rep["problems"]                     # a note, not a problem: the DbLib route pairs them
    readme = (tmp_path / "pkg" / "ALTIUM_README.txt").read_text(encoding="utf-8")
    assert "LONE_SOIC8" in readme and "database library" in readme.lower(), readme


@lite
def test_no_note_when_every_footprint_is_linked(tmp_path):
    """altium_pkg: no unlinked-footprint note when there are none."""
    from ecadlib import altium_pkg as PKG
    assert PKG.readme_text("x", "x.LibPkg", [], 1, 1).count("not linked") == 0
    assert "not linked" not in PKG.readme_text("x", "x.LibPkg", [], 1, 1, unlinked=[])
