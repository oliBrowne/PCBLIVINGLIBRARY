"""Regression tests for the debug-sweep findings on the Altium side (altium_native, dblib-sync, altium-menu, altium-pkg).
Run:  python3 -m tests.run_all test_debugsweep_altium"""
from ecadlib import altium_lib as AL, altium_native as AN, kicad_import as K
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- DS2-34: a pad's drill offset survives the rebuild
OFFSET_PADS = '''(footprint "OffsetPad" (layer "F.Cu")
  (pad "1" thru_hole rect (at 0 0) (size 3 1.6) (drill 1 (offset 0.7 0)) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole oval (at 0 2.54) (size 3 1.6) (drill 1 (offset 0.7 0)) (layers "*.Cu" "*.Mask"))
)'''


@lite
def test_ds2_34_compare_reports_a_rebuild_that_dropped_the_drill_offset(tmp_path):
    fp, _ = K.convert_footprint(OFFSET_PADS, notes=[])
    lib = tmp_path / "OffsetPad.PcbLib"
    AL.write_pcblib(lib, [fp])
    rp = lib.with_name(lib.name + AN.EXT)
    AN.record(lib, AN._sha(lib.read_bytes()))
    kept = tmp_path / "kept.PcbLib"                     # a rebuild that kept the 0.7 mm copper offset: a match
    kept.write_bytes(AL.pcblib_bytes([fp], lib.name, ("02/02/2031", "01:01:01")))
    assert AN.compare_one(kept, rp) == []
    centred = [dict(fp, pads=[dict(p, offset=None) for p in fp["pads"]])]    # copper centred on the hole
    lib.write_bytes(AL.pcblib_bytes(centred, lib.name, ("02/02/2031", "01:01:01")))
    probs = AN.compare_one(lib, rp)
    assert len(probs) == 2 and all("offset (0, 0) != (275591, 0)" in p for p in probs), probs
    assert AN.lib_state(lib, save=False) == "edited"


# ---------------------------------------------------------------- DS2-35: a hand-added workbook row with a bad ref
@lite
def test_ds2_35_hand_row_with_a_ref_altium_cannot_store_is_reported_not_fatal(tmp_path):
    import openpyxl
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    plan = _plan(tmp_path, _base_items())
    S.sync(plan, tmp_path / "lib")
    x = tmp_path / "lib/ecadlib.xlsx"
    wb = openpyxl.load_workbook(x)
    ws = wb["Resistors"]
    hdr = [c.value for c in ws[1]]
    row = dict.fromkeys(hdr, "")
    row.update({"Part Number": "HAND-R1", "Library Ref": "Res", "Footprint Ref": "R_0603→"})
    ws.append([row[h] for h in hdr])
    wb.save(x)
    out = S.sync(plan, tmp_path / "lib")
    acts = {r["name"]: r["action"] for r in out["results"]}
    assert acts["RES-0603-10k"] == "up_to_date" and acts["CAP-0402-100nF"] == "up_to_date", out["results"]
    bad = [r for r in out["results"] if r["name"] == "HAND-R1" and r["action"] == "error"]
    assert len(bad) == 1 and "Footprint Ref 'R_0603→'" in bad[0]["detail"] and bad[0]["table"] == "Resistors", out["results"]
    kept = {r["Part Number"]: r for r in S.read_tables(x)["Resistors"][1]}
    assert kept["HAND-R1"]["Footprint Ref"] == "R_0603\u2192", kept


# ---------------------------------------------------------------- DS2-36: altium-menu build with an accented folder
@lite
def test_ds2_36_menu_build_with_a_non_ascii_win_dir_fails_cleanly_before_writing(tmp_path):
    from ecadlib import altium_menu as AM
    out = tmp_path / "menu"
    try:
        AM.build(out, None, None, win_dir="C:\\Users\\Jos\u00e9\\ecadlib-menu")
        raise AssertionError("a menu entry the .DXPPrf cannot hold must be refused")
    except ValueError as e:
        msg = str(e)
    assert "--win-dir" in msg and "codec" not in msg, msg
    assert [p.name for p in out.rglob("*")] == [], "nothing written: no half-built menu folder"


# ---------------------------------------------------------------- DS2-37: svn commit of a new nested STEP folder
def _r0603_with_step(models):
    """The test plan's R_0603 footprint, written again with an embedded STEP body (ecadlib_3d/<fp>_0/R_0603.step)."""
    import json
    from pathlib import Path
    spec = json.loads((Path(__file__).resolve().parent.parent / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    fp = AL.footprint_from_spec(spec)[0]
    fp["bodies"] = [{"pts": [(-0.8, -0.4), (0.8, -0.4), (0.8, 0.4), (-0.8, 0.4)], "height": 0.5,
                     "step": {"name": "R_0603.step", "data": b"ISO-10303-21;\nEND-ISO-10303-21;\n", "rot": (0, 0, 0)}}]
    AL.write_pcblib(models / "PcbLib/R_0603_1608Metric.PcbLib", [fp])


@lite
def test_ds2_37_svn_commit_targets_every_new_folder_add_parents_schedules(tmp_path):
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    _r0603_with_step(tmp_path / "models")
    out = S.sync(_plan(tmp_path, _base_items()), tmp_path / "wc", mode="svndblib", svn_url="file:///repo/lib")
    assert "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step" in out["written"], out["written"]
    commit = next(c for c in S.svn_commands(out["written"], out["removed"], "m") if c[1] == "commit")
    targets = set(commit[6:])
    want = {"PcbLib", "SchLib", "PcbLib/ecadlib_3d", "PcbLib/ecadlib_3d/R_0603_1608Metric_0"}
    assert want <= targets, sorted(want - targets)
