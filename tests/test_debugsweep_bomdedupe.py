"""Regression tests for the dedupe / BOM debug sweep (DS2-xx).
Run:  python3 -m tests.run_all test_debugsweep_bomdedupe"""
import contextlib, io, json
from pathlib import Path

from ecadlib import altium_export as X, dedupe as DD, librarian as L
from tests.fixtures_lite import lite


@lite
def test_ds2_01_mpnless_passives_10x_apart_are_not_one_part(tmp_path):
    """Passives without an MPN are named by value ('CAP-0603-2.2u'); their names are not MPNs. Stripping the
    punctuation made 2.2u == 22u and 4.7k == 47k, and --merge marked one a duplicate of the other: from the plan
    itself, from ecadlib's own DbLib of the same BOM (Part Number, empty MPN) and from a check-a365 --state file
    (names only) once both parts are in the workspace."""
    bom = tmp_path / "caps.csv"
    bom.write_text("Designator,Qty,Value,Footprint\nC1,1,2.2u,0603\nC2,1,22u,0603\nR1,1,4.7k,0603\nR2,1,47k,0603\n")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache", log=lambda *a, **k: None)
    plan = tmp_path / "lib" / "plan.json"
    names = sorted(i["name"] for i in json.loads(plan.read_text(encoding="utf-8")))
    assert names == ["CAP-0603-2.2u", "CAP-0603-22u", "RES-0603-4.7k", "RES-0603-47k"], names
    with contextlib.redirect_stdout(io.StringIO()):
        assert X.main([str(bom), "-o", str(tmp_path / "dbl")]) in (0, None)
    (tmp_path / "lib" / "a365_state.json").write_text(json.dumps(
        {n: {"revision": "r1", "lifecycle": None, "symbols": [], "footprints": []} for n in names}))
    r = DD.run(plan, libs=[tmp_path / "dbl"], do_merge=True, kinds=("mpn",), log=lambda *a, **k: None)
    mpn = [(g["key"], [(m["name"], m["origin"]) for m in g["members"]]) for g in r["groups"] if g["kind"] == "mpn"]
    assert mpn == [], mpn
    after = {i["name"]: i for i in json.loads(plan.read_text(encoding="utf-8"))}
    assert not [n for n, i in after.items() if i.get("status") == "duplicate" or i.get("duplicate_of")], after
