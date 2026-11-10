"""Full review 2026-11-10, kicad-export: the export's own last checks (the footprint as written; symbol pins against
the footprint's pads) only added an error line. The footprint stayed in <lib>.pretty, the symbol in <lib>.kicad_sym, the
ledger got a publish line and kicad-pcm packaged the library. A part that fails them is now held like one the release
check held: nothing of it is written, a version exported before stays as it was."""
import tempfile
from pathlib import Path

from ecadlib import kicad_export as X
from tests import test_kicad_export as TX
from tests.test_bughunt_expk import QUIET, _files, _plan


def _patched(name, fn):
    old = getattr(X, name)
    setattr(X, name, fn)
    return old


def _worse_for(fpn_part):
    real = X.footprint_failures

    def fails(item, plan_dir, kmod):
        got = real(item, plan_dir, kmod)
        return got + ["footprint: SIMULATED new failure"] if item["name"] == fpn_part else got
    return fails


def test_a_footprint_that_fails_the_exports_own_check_is_not_written_or_published():
    with tempfile.TemporaryDirectory() as d:
        plan, _, http = _plan(d)
        out = Path(d) / "kx"
        old = _patched("footprint_failures", _worse_for("RES-0603-10k"))
        try:
            rep = X.export(plan, out, "ecadlib", http=http, log=QUIET)
        finally:
            X.footprint_failures = old
        assert any("fails more than in the release check" in e for e in rep["errors"]), rep["errors"]   # still exit 1
        assert any(s["part"] == "RES-0603-10k" and "SIMULATED" in s["why"] for s in rep["skipped"]), rep["skipped"]
        assert "RES-0603-10k" not in {r["part"] for r in rep["parts"]}
        assert not (out / "ecadlib.pretty" / "R_0603_1608Metric.kicad_mod").exists()
        assert "RES-0603-10k" not in TX._syms(out / "ecadlib.kicad_sym")
        assert "LED-RED-0603" in TX._syms(out / "ecadlib.kicad_sym")           # the other parts are exported as before


def test_a_part_exported_before_keeps_that_version_when_the_check_fails_later():
    with tempfile.TemporaryDirectory() as d:
        plan, _, http = TX._plan(d)                                             # with change control: released parts
        out = Path(d) / "kx"
        first = X.export(plan, out, "ecadlib", http=http, log=QUIET)
        assert not first["errors"], first["errors"]
        was = _files(out)
        old = _patched("footprint_failures", _worse_for("RES-0603-10k"))
        try:
            rep = X.export(plan, out, "ecadlib", http=http, log=QUIET)
        finally:
            X.footprint_failures = old
        now = _files(out)
        fp = str(Path("ecadlib.pretty") / "R_0603_1608Metric.kicad_mod")
        assert now[fp] == was[fp]
        assert "RES-0603-10k" in TX._syms(out / "ecadlib.kicad_sym")           # kept, as for a release-check hold
        assert any(k["part"] == "RES-0603-10k" for k in rep["kept"]), rep["kept"]
        assert "RES-0603-10k" not in {r["part"] for r in rep["parts"]}


def test_symbol_pins_without_a_pad_take_the_part_out_and_undo_its_footprint():
    with tempfile.TemporaryDirectory() as d:
        plan, _, http = _plan(d)
        out = Path(d) / "kx"
        real = X._pins
        old = _patched("_pins", lambda sym: real(sym) | ({"99"} if "TPS1234" in str(sym[1]) else set()))
        try:
            rep = X.export(plan, out, "ecadlib", http=http, log=QUIET)
        finally:
            X._pins = old
        assert any("TPS1234DSGR: symbol pins without a pad" in e for e in rep["errors"]), rep["errors"]
        assert any(s["part"] == "TPS1234DSGR" and "99" in s["why"] for s in rep["skipped"]), rep["skipped"]
        assert "TPS1234DSGR" not in {r["part"] for r in rep["parts"]}
        assert "TPS1234DSGR" not in TX._syms(out / "ecadlib.kicad_sym")
        fpn = next(r for r in X.export(plan, Path(d) / "ok", "ecadlib", http=http, log=QUIET)["parts"]
                   if r["part"] == "TPS1234DSGR")["footprint"]
        assert not (out / "ecadlib.pretty" / f"{fpn}.kicad_mod").exists()       # its footprint is not left behind
        assert f"ecadlib.pretty/{fpn}.kicad_mod" not in rep["written"]


def test_include_held_still_writes_and_reports():
    with tempfile.TemporaryDirectory() as d:
        plan, _, http = _plan(d)
        out = Path(d) / "kx"
        old = _patched("footprint_failures", _worse_for("RES-0603-10k"))
        try:
            rep = X.export(plan, out, "ecadlib", include_held=True, http=http, log=QUIET)
        finally:
            X.footprint_failures = old
        assert any("fails more than in the release check" in e for e in rep["errors"]), rep["errors"]
        assert (out / "ecadlib.pretty" / "R_0603_1608Metric.kicad_mod").exists()
        assert next(r for r in rep["parts"] if r["part"] == "RES-0603-10k")["held"] is True   # never kept as released
