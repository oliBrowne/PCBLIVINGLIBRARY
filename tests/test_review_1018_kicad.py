"""Regression tests for the 2026-10-18 review of the KiCad output path (kicad_export, kicad_dblib, kicad_pcm).
Run:  python3 -m tests.test_review_1018_kicad"""
import json, math, shutil, sqlite3, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, kicad_dblib as KD, kicad_export as X, kicad_pcm as KP, sexpr

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "kicad_golden"
_PLAN = {}


def _tmp():
    return Path(tempfile.mkdtemp(prefix="ecadlib-1018-kicad-"))


def _plan():
    """tests.test_kicad_export's librarian run (made once), copied per test: (plan.json, http)."""
    if "dir" not in _PLAN:
        from tests import test_kicad_export as TX
        plan, _, http = TX._plan(_tmp())
        _PLAN.update(dir=plan.parent, http=http)
    d = _tmp() / "plan"
    shutil.copytree(_PLAN["dir"], d)
    return d / "plan.json", _PLAN["http"]


def _quiet(*a, **k):
    pass


# ---------------------------------------------------------------- KiCad 10 (point ...) markers
def test_kicad10_footprint_points_are_not_written_for_kicad8_or_7():
    # KiCad 10 footprints (DIP-8, TO-220 in kicad-footprints master) carry (point (at ..) (size ..) (layer ..)) markers;
    # KiCad 7, 8 and 9 have no such token, so the whole footprint fails to load
    for name in ("DIP-8_W7.62mm", "TO-220-3_Vertical"):
        text = (GOLDEN / f"{name}.kicad_mod").read_text(encoding="utf-8")
        assert "(point" in text
        t, _ = X.footprint_tree(text, name, "lib", [])
        k8 = sexpr.dumps(t) + "\n"
        assert not list(sexpr.find(t, "point")), name
        assert "(point" not in KP.footprint_kicad7(k8), name
        assert sexpr.pads(t) == sexpr.pads(sexpr.parse(text)), name


def test_every_golden_footprint_loads_in_kicad7_pcbnew():
    from tests import test_kicad_real as R
    if not R.pcbnew_python() or (R.kicad_major() or 7) >= 8:
        return R._skip("KiCad 7's pcbnew")
    d = _tmp() / "k7.pretty"
    d.mkdir()
    names = []
    for f in sorted(GOLDEN.glob("*.kicad_mod")):
        t, _ = X.footprint_tree(f.read_text(encoding="utf-8"), f.stem, "lib", [])
        (d / f.name).write_text(KP.footprint_text(sexpr.dumps(t) + "\n", "lib", "KICAD7_3RD_PARTY", "x", 7), encoding="utf-8")
        names.append(f.stem)
    got = R.pcbnew_job("load", lib=str(d))
    assert sorted(got) == sorted(names) and not [k for k, v in got.items() if not v], [k for k, v in got.items() if not v]


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_1018_kicad"]))
