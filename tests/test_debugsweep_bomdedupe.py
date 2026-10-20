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


@lite
def test_ds2_02_merge_onto_multi_entry_library_checks_the_named_entry(tmp_path):
    """dedupe --merge points a plan item at one footprint / symbol of a company library that holds many. The release
    check and resolve read the file's first footprint (a SOIC-16 here: pads 9-16 without a pin) and the pins of every
    symbol in the SchLib (pins 9-14 without a pad), so the merged part was held on another part's geometry."""
    from ecadlib import altium_lib as AL, kicad_import as K, release_check as RC, resolve as R
    from tests.test_release_check import SOIC, make
    it = make(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([it]))
    lib = tmp_path / "company"
    lib.mkdir()
    soic16 = (Path(__file__).parent.parent / "out" / "footprints" / "ecadlib.pretty" / "SOIC-16_3.9x9.9mm_P1.27mm.kicad_mod").read_text()
    fps = []
    for text, name in ((soic16, "SO16_HOUSE"), (SOIC, "SO8_HOUSE")):
        fp, t = K.convert_footprint(text, name)
        K.add_body(fp, t, None, None)
        fps.append(fp)
    AL.write_pcblib(lib / "Company.PcbLib", fps)
    pins = lambda n: [{"num": str(p), "name": f"P{p}", "type": "io", "side": "left"} for p in range(1, n + 1)]
    AL.write_schlib(lib / "Company.SchLib", [AL.ic_symbol("QUAD_HOUSE", pins(14)), AL.ic_symbol("PART_HOUSE", pins(8))])
    DD.run(plan, libs=[lib], do_merge=True, kinds=("footprint", "symbol"), log=lambda *a, **k: None)
    it = json.loads(plan.read_text(encoding="utf-8"))[0]
    assert it["footprint"] == {"ref": "SO8_HOUSE", "file": "company/Company.PcbLib"}, it["footprint"]
    assert it["symbol"] == {"ref": "PART_HOUSE", "file": "company/Company.SchLib"}, it["symbol"]
    f, _, _ = RC.check_item(it, tmp_path)
    assert f == [], f
    assert [p for p, _ in R.item_pads(it, tmp_path)] == [str(n) for n in range(1, 9)], R.item_pads(it, tmp_path)
    assert R.item_pins(it, tmp_path) == [str(n) for n in range(1, 9)], R.item_pins(it, tmp_path)
    it["footprint"]["ref"] = "SO8_RENAMED"                 # renamed in the company library since: held, not the first one
    f, _, _ = RC.check_item(it, tmp_path)
    assert [r for r, m in f] == ["files"] and "SO8_RENAMED" in f[0][1] and "Company.PcbLib" in f[0][1], f
    assert R.item_pads(it, tmp_path) is None
