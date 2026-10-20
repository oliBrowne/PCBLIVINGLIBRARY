"""Regression tests for the debug sweep's KiCad group (DS2-*): kicad_pcm, kicad_export, easyeda_convert, kicad_index.
Plain functions (tests.run_all, no pytest)."""
import io, json, zipfile
from contextlib import redirect_stderr, redirect_stdout

from ecadlib import kicad_export as X, kicad_pcm as KP
from tests import test_tool_packages as TP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _two_libs():
    """kicad-export of one plan as library 'alpha', then as 'beta', into the same folder (EXP-27)."""
    plan, http = TP._plan()
    out = plan.parent.parent / "k"
    for lib in ("alpha", "beta"):
        assert not X.export(plan, out, lib, http=http, log=QUIET)["errors"]
    return out


def test_ds2_40_kicad_pcm_packages_the_named_library_of_a_two_library_folder():
    out = _two_libs()
    err = io.StringIO()
    with redirect_stdout(io.StringIO()) as so, redirect_stderr(err):
        rc = KP.main([str(out), "--out", str(out.parent / "pcm"), "--name", "alpha", "--json"])
    assert rc == 0, err.getvalue()
    rep = json.loads(so.getvalue())
    assert (rep["library"], rep["identifier"], rep["nickname"]) == ("alpha", "local.ecadlib.alpha", "PCM_alpha"), rep
    names = zipfile.ZipFile(rep["zip"]).namelist()
    assert "symbols/alpha.kicad_sym" in names and not any("beta" in n for n in names), names
    # no --name: the folder holds two libraries, so it is refused and both are named (not the last one, silently)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = KP.main([str(out), "--out", str(out.parent / "pcm2")])
    assert rc == 2 and "alpha" in err.getvalue() and "beta" in err.getvalue() and "--name" in err.getvalue(), (rc, err.getvalue())
    assert not (out.parent / "pcm2").exists()


def test_ds2_41_easyeda_smd_footprint_with_npth_pegs_stays_smd():
    from ecadlib import easyeda_convert as EC, footprint_rules as FR
    smd = ["PAD~RECT~-10~0~4~6~1~~1~0~~0~g1~0~~Y~0", "PAD~RECT~10~0~4~6~1~~2~0~~0~g2~0~~Y~0"]
    for peg, want in (("HOLE~0~8~1.5~g3~0", "smd"),                                  # a locating peg (NPTH)
                      ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~N~0", "smd"),          # a pad with an unplated hole
                      ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~Y~0", "through_hole")):  # a plated pin: really THT
        text = EC.footprint_to_kicad({"head": {"x": 0, "y": 0}, "shape": smd + [peg]}, name="SW_Pegs")
        attrs = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("(attr")]
        assert attrs == [f"(attr {want})"], (peg, attrs)
        assert not [f for f in FR.check(text, "SW_Pegs.kicad_mod") if f[1] == "attr"], peg   # nothing to hold it on


@lite
def test_ds2_41_pcblib_smd_footprint_with_npth_pegs_stays_smd(tmp_path):
    """The same footprints through a .PcbLib (kicad-export reads library footprints back from the PcbLib)."""
    from ecadlib import altium_lib as AL, easyeda_convert as EC, footprint_rules as FR
    smd = ["PAD~RECT~-10~0~4~6~1~~1~0~~0~g1~0~~Y~0", "PAD~RECT~10~0~4~6~1~~2~0~~0~g2~0~~Y~0"]
    for i, (peg, want) in enumerate((("HOLE~0~8~1.5~g3~0", "smd"),
                                     ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~N~0", "smd"),
                                     ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~Y~0", "through_hole"))):
        text = EC.footprint_to_kicad({"head": {"x": 0, "y": 0}, "shape": smd + [peg]}, name="SW_Pegs")
        AL.write_pcblib(tmp_path / f"p{i}.PcbLib", [AL.footprint_from_kicad(text)])
        back = X.footprint_from_pcblib(tmp_path / f"p{i}.PcbLib", "SW_Pegs", [])
        attrs = [ln.strip() for ln in back.splitlines() if ln.strip().startswith("(attr")]
        assert attrs == [f"(attr {want})"], (peg, attrs)
        assert not [f for f in FR.check(back, "SW_Pegs.kicad_mod") if f[1] == "attr"], peg
    # a footprint of unplated holes alone (a mounting-hole footprint) keeps the through-hole type it always had
    text = EC.footprint_to_kicad({"head": {"x": 0, "y": 0}, "shape": ["HOLE~0~0~3~g1~0"]}, name="MH")
    AL.write_pcblib(tmp_path / "mh.PcbLib", [AL.footprint_from_kicad(text)])
    back = X.footprint_from_pcblib(tmp_path / "mh.PcbLib", "MH", [])
    assert [ln.strip() for ln in back.splitlines() if ln.strip().startswith("(attr")] == ["(attr through_hole)"], back


def test_ds2_44_kicad_pcm_packages_a_crlf_checkout_of_a_kicad_export_folder():
    _, out, _ = TP._export(False)
    lf = KP.build(out, out.parent / "pcm_lf", log=QUIET)
    for f in [out / "lib.kicad_sym", *(out / "lib.pretty").glob("*.kicad_mod")]:   # git core.autocrlf on Windows
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    crlf = KP.build(out, out.parent / "pcm_crlf", log=QUIET)     # not an edit: kicad-export does not call it one (EXP-65)
    assert crlf["content_sha256"] == lf["content_sha256"] and crlf["footprints"] == lf["footprints"] == 4, (crlf, lf)


def test_ds2_45_kicad_export_warns_of_removed_parts_after_another_library_was_exported_there():
    plan, http = TP._plan()
    other, _ = TP._plan()                                        # another BOM's plan, its own library in the same folder
    out = plan.parent.parent / "k"
    X.export(plan, out, "alpha", http=http, log=QUIET)
    X.export(other, out, "beta", http=http, log=QUIET)
    items = json.loads(plan.read_text(encoding="utf-8"))
    plan.write_text(json.dumps(items[1:]), encoding="utf-8")    # a part leaves alpha's plan
    rep = X.export(plan, out, "alpha", http=http, log=QUIET)
    assert [w for w in rep["warnings"] if "removed from" in w] == [
        f"1 part exported last time and not in this plan removed from alpha: {items[0]['name']}; a KiCad project "
        "using them loses those links"], rep["warnings"]
