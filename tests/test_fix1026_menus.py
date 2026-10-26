"""Fixes for the 2026-10-26 PR #196 review, menus round (R-MN-1..6 in reviews/2026-10-26_review_196/menus.md)."""
import json, sqlite3, sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures_lite import lite


# ---- R-MN-4: kicad-dblib re-adds a kept held part whose old row had an empty Footprint (was KeyError 'Footprint')
@lite
def test_rmn4_kept_part_with_empty_footprint_rebuilds(tmp_path):
    from ecadlib import kicad_dblib as KD
    out = tmp_path / "out"
    out.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"name": "LOGO1", "status": "held", "hold_reasons": ["fields: Description is empty"],
                                 "symbol": {}, "footprint": {}}]))
    (out / "kicad_export.json").write_text(json.dumps({"library": "lib", "parts": [], "footprints": {}, "written": {},
        "kept": [{"part": "LOGO1", "symbol_name": "LOGO1", "symbol": True, "footprint": None}]}))
    with closing(sqlite3.connect(out / "lib.sqlite")) as con, con:
        con.execute('CREATE TABLE "Parts" ("Part ID" TEXT PRIMARY KEY NOT NULL, "Symbol" TEXT, "Footprint" TEXT, "Value" TEXT)')
        con.execute('INSERT INTO "Parts" VALUES (?,?,?,?)', ("LOGO1", "lib:LOGO1", "", "LOGO1"))
    rep = KD.build(plan, out, "lib", log=lambda *a: None)
    assert rep["tables"] == {"Parts": 1}, rep
    with closing(sqlite3.connect(out / "lib.sqlite")) as con:
        assert con.execute('SELECT "Part ID", "Symbol", "Footprint" FROM "Parts"').fetchall() == [("LOGO1", "lib:LOGO1", "")]


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
