"""Regression tests for the debug sweep's KiCad group (DS2-*): kicad_pcm, kicad_export, easyeda_convert, kicad_index.
Plain functions (tests.run_all, no pytest)."""
import io, json, zipfile
from contextlib import redirect_stderr, redirect_stdout

from ecadlib import kicad_export as X, kicad_pcm as KP
from tests import test_tool_packages as TP

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
