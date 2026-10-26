"""Fixes for the 2026-10-26 PR #196 review, KiCad writers (R-KW-1..4 in reviews/2026-10-26_review_196/kicadw.md)."""
import json, re, sqlite3, sys, types
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecadlib import altium_lib as AL, httpcache, kicad_export as X, kicad_import as K, sexpr
from tests.fixtures_lite import lite

FIX = ROOT / "tests" / "fixtures" / "kicad"


def _models(folder, lib="ecadlib"):
    return {f.name: re.findall(r'\(model "([^"]+)"', f.read_text()) for f in sorted(Path(folder, f"{lib}.pretty").glob("*.kicad_mod"))}


# ---- R-KW-1: a ready part whose footprint is only a .PcbLib (no .kicad_mod) is exported (fp_source's fallback)
@lite
def test_rkw1_pcblib_only_footprint_is_exported(tmp_path):
    from tests import test_kicad_export as T
    from ecadlib import kicad_dblib as KD
    plan, items, http = T._plan(tmp_path)
    km = plan.parent / "models" / "KiCad" / "R_0603_1608Metric.kicad_mod"
    km.rename(tmp_path / "aside.kicad_mod")                 # an Altium-only footprint: no KiCad file at all
    its = json.loads(plan.read_text())
    res = next(it for it in its if it["name"] == "RES-0603-10k")
    res["footprint"].pop("kicad_mod", None)
    assert res.get("status", "ready") == "ready" and res["footprint"]["file"].endswith(".PcbLib")
    plan.write_text(json.dumps(its, indent=2))
    out = tmp_path / "kx"
    rep = KD.build(plan, out, "lib", export=True, http=http, log=lambda *a, **k: None)
    xr = json.loads((out / "kicad_export.json").read_text())
    assert xr["errors"] == [] and not xr.get("held_changes"), xr["errors"]          # kicad-export exits 0
    row = next(r for r in xr["parts"] if r["part"] == "RES-0603-10k")
    assert row["footprint"] == "R_0603_1608Metric", row
    assert "converted back" in xr["footprints"]["R_0603_1608Metric"]["from"]
    assert (out / "lib.pretty" / "R_0603_1608Metric.kicad_mod").is_file()
    with closing(sqlite3.connect(out / "lib.sqlite")) as con:
        got = [r for t, in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
               for r in con.execute(f'SELECT "Part ID", "Footprint" FROM "{t}"') if r[0] == "RES-0603-10k"]
    assert got == [("RES-0603-10k", "lib:R_0603_1608Metric")], got
    assert not any("RES-0603-10k" in w and "footprint" in w for w in rep["warnings"]), rep["warnings"]


if __name__ == "__main__":
    fails = 0
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            try:
                f()
                print("ok", k)
            except Exception as e:
                fails += 1
                print("FAIL", k, type(e).__name__, e)
    sys.exit(1 if fails else 0)
