"""Review 2026-10-17, Altium core: native-build recipes and the menu's command=."""
import json, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, altium_menu as AM, altium_native as AN, altium_pkg as PKG

HERE = Path(__file__).resolve().parent.parent


def _soic8(name=None):
    spec = json.loads((HERE / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    fp = AL.footprint_from_spec(spec)[0]
    return dict(fp, name=name) if name else fp


def test_recipe_footprint_name_is_the_name_the_pcblib_stores():
    """A footprint name with an Ohm sign is stored as 'Ohm' in the .PcbLib (and in a symbol's footprint link); the
    recipe named it with the Ohm sign, so Altium built a footprint the symbols do not link to and compare said missing."""
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "fp.PcbLib"
        AL.write_pcblib(lib, [_soic8("R_10\u03a9_0603")])
        rec = (Path(td) / "fp.PcbLib.ecadbuild").read_text(encoding="utf-8")
        assert "FOOTPRINT\tR_10Ohm_0603\t" in rec, rec[:200]
        assert [c["name"] for c in AL.read_pcblib(lib)] == ["R_10Ohm_0603"]
        cmp = AN.compare(Path(td))
        assert cmp["problems"] == [] and cmp["checked"] == 1, cmp


def test_copy_without_recipe_removes_the_stale_one():
    """dblib-sync copies a library that has no recipe (a vendor file picked in review) over one ecadlib built: the old
    recipe must go, or Altium's native build overwrites the new file with the old footprint."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "part").mkdir()
        AL.write_pcblib(d / "part" / "fp.PcbLib", [_soic8()])
        dst = d / "lib" / "PcbLib" / "SOIC8.PcbLib"
        dst.parent.mkdir(parents=True)
        dst.write_bytes((d / "part" / "fp.PcbLib").read_bytes())
        AN.copy_recipe(d / "part" / "fp.PcbLib", dst)
        assert (dst.parent / "SOIC8.PcbLib.ecadbuild").is_file()
        (d / "vendor").mkdir()
        (d / "vendor" / "SOIC8.PcbLib").write_bytes(b"vendor library")
        dst.write_bytes(b"vendor library")
        AN.copy_recipe(d / "vendor" / "SOIC8.PcbLib", dst)
        assert not (dst.parent / "SOIC8.PcbLib.ecadbuild").is_file()
        assert AN.recipes(d / "lib") == []


def test_altium_pkg_collect_drops_a_stale_recipe():
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "src", Path(td) / "out"
        (src / "PcbLib").mkdir(parents=True)
        AL.write_pcblib(src / "PcbLib" / "x.PcbLib", [_soic8()])
        PKG.collect(src, out)
        assert (out / "PcbLib" / "x.PcbLib.ecadbuild").is_file()
        (src / "PcbLib" / "x.PcbLib.ecadbuild").unlink()
        (src / "PcbLib" / "x.PcbLib").write_bytes(b"made elsewhere")
        PKG.collect(src, out)
        assert (out / "PcbLib" / "x.PcbLib").read_bytes() == b"made elsewhere"
        assert not (out / "PcbLib" / "x.PcbLib.ecadbuild").is_file()


def test_default_command_in_the_windows_app_is_its_ecadlib_exe():
    """In the frozen Windows app sys.executable is ecadlib-app.exe / ecadlib.exe: '"...exe" -m ecadlib' made every menu
    entry fail with "unknown command '-m'"."""
    with tempfile.TemporaryDirectory() as td:
        app, cli = Path(td) / "ecadlib-app.exe", Path(td) / "ecadlib.exe"
        app.write_bytes(b""); cli.write_bytes(b"")
        assert AM.default_command(str(app), nt=True, frozen=True) == f'"{cli}"'
        assert AM.default_command(str(cli), nt=True, frozen=True) == f'"{cli}"'
    assert AM.default_command("C:\\venv\\Scripts\\python.exe", nt=True, frozen=False) == \
        '"C:\\venv\\Scripts\\python.exe" -m ecadlib'


def test_a_missing_step_file_is_found_before_the_old_library_is_deleted():
    """BuildPcbLib deleted the .PcbLib, then stopped at a BODY whose STEP file was missing and closed the new document
    unsaved: the library was gone from disk (and the DbLib pointed at nothing). The STEP check now runs first."""
    pas = AN.PAS
    build = pas[pas.index("Function BuildPcbLib"):pas.index("Procedure Own(")]
    assert build.index("MissingStep(Folder)") < build.index("DeleteFile(Path)")
    assert pas.index("Function MissingStep") < pas.index("Function BuildPcbLib")      # no forward declarations
    a, b = pas.index("Var\n    EbF"), pas.index("Procedure EbSplit")
    r = AM.pas_routines(AM.pas_tokens(pas[:a] + pas[b:]))
    assert r["MissingStep"]["kind"] == "function" and r["MissingStep"]["params"] == {"folder"}
