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


@lite
def test_ds2_36_add_to_altium_in_a_non_ascii_windows_workspace_installs_without_the_menu_file(tmp_path, monkeypatch):
    import os
    if os.name != "nt":                  # the menu file is only built on Windows (Windows CI runs this)
        return
    from ecadlib import altium_menu as AM, easy_setup as ES
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))     # no Altium here: the menu bar step says so
    ws = tmp_path.resolve() / "José" / "ws"
    r = ES.install_altium_menu(ws)       # was: ValueError from the menu file, so "Add to Altium" installed nothing
    out = ws / "altium-menu"
    assert r["menu_file"] is None and not (out / AM.PREFS).exists(), r
    assert (out / AM.PROJECT).is_file() and (out / "altium_menu.json").is_file()
    assert "no Altium Designer settings folder" in r["menu_bar"].get("error", ""), r["menu_bar"]   # install-menu ran
    assert not any(AM.PREFS in s for s in r["steps"]), r["steps"]      # no step names a file that is not there


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


# ---------------------------------------------------------------- DS2-38: --prune --svn and the recipe / .ecadstate
@lite
def test_ds2_38_prune_svn_deletes_the_recipe_and_ecadstate_of_a_pruned_library(tmp_path):
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    wc = tmp_path / "wc"
    first = S.sync(_plan(tmp_path, _base_items()), wc, mode="svndblib", svn_url="file:///repo/lib")
    sidecars = ["PcbLib/C_0402_1005Metric.PcbLib.ecadbuild", "PcbLib/C_0402_1005Metric.PcbLib.ecadstate",
                "SchLib/Capacitor.SchLib.ecadbuild", "SchLib/Capacitor.SchLib.ecadstate"]
    assert set(sidecars) <= set(first["written"]), first["written"]           # svn-added by the first run
    no_cap = [i for i in _base_items() if i["name"] != "CAP-0402-100nF"]
    out = S.sync(_plan(tmp_path, no_cap), wc, mode="svndblib", svn_url="file:///repo/lib", prune=True)
    delete = next(c for c in S.svn_commands(out["written"], out["removed"], "m") if c[1] == "delete")
    assert set(sidecars) <= set(delete), delete
    left = sorted(p.relative_to(wc).as_posix() for p in wc.rglob("*") if "C_0402" in p.name or "Capacitor" in p.name)
    assert left == [], left


STEP_REL = "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step"


@lite
def test_ds2_38_prune_svn_deletes_the_step_file_and_emptied_folders_of_a_pruned_footprint(tmp_path):
    from ecadlib import altium_dblib_sync as S
    from tests.test_altium_ad26_overnight import _models_with_step
    from tests.test_dblib_sync import _base_items, _plan
    _models_with_step(tmp_path)
    wc = tmp_path / "wc"
    first = S.sync(_plan(tmp_path, _base_items()), wc, mode="svndblib", svn_url="file:///repo/lib")
    assert STEP_REL in first["written"], first["written"]           # svn add --parents: the file and its folders
    no_res = [i for i in _base_items() if i["name"] != "RES-0603-10k"]
    out = S.sync(_plan(tmp_path, no_res), wc, mode="svndblib", svn_url="file:///repo/lib", prune=True)
    delete = next(c for c in S.svn_commands(out["written"], out["removed"], "m") if c[1] == "delete")
    want = {STEP_REL, "PcbLib/ecadlib_3d/R_0603_1608Metric_0", "PcbLib/ecadlib_3d"}
    assert want <= set(delete), sorted(want - set(delete))
    assert not (wc / "PcbLib/ecadlib_3d").exists()


@lite
def test_ds2_38_prune_commit_leaves_no_step_file_in_a_real_svn_repository(tmp_path):
    import shutil, subprocess
    if not (shutil.which("svn") and shutil.which("svnadmin")):     # CI has no svn: the test above covers the commands
        return
    from ecadlib import altium_dblib_sync as S
    from tests.test_altium_ad26_overnight import _models_with_step
    from tests.test_dblib_sync import _base_items, _plan
    _models_with_step(tmp_path)
    repo, wc = tmp_path.resolve() / "repo", tmp_path.resolve() / "wc"
    subprocess.run(["svnadmin", "create", str(repo)], check=True, capture_output=True)
    subprocess.run(["svn", "checkout", repo.as_uri(), str(wc)], check=True, capture_output=True)
    first = S.sync(_plan(tmp_path, _base_items()), wc, mode="svndblib", svn_url=repo.as_uri())
    assert S.svn_step(wc, first["written"], first["removed"], commit=True)["ran"]
    no_res = [i for i in _base_items() if i["name"] != "RES-0603-10k"]
    out = S.sync(_plan(tmp_path, no_res), wc, mode="svndblib", svn_url=repo.as_uri(), prune=True)
    res = S.svn_step(wc, out["written"], out["removed"], commit=True)
    assert res["ran"], res
    status = subprocess.run(["svn", "status", str(wc)], capture_output=True, text=True).stdout
    assert status.strip() == "", status                             # was '!' for the STEP file and both folders
    ls = subprocess.run(["svn", "ls", "-R", repo.as_uri()], capture_output=True, text=True).stdout
    assert "ecadlib_3d" not in ls and "R_0603" not in ls, ls


# ---------------------------------------------------------------- DS2-39: altium-pkg rows of a non-ASCII DbLib name
@lite
def test_ds2_39_pkg_check_reads_the_rows_of_a_dblib_with_a_non_ascii_name(tmp_path):
    from ecadlib import altium_pkg as P
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    out = tmp_path / "pkg"
    rep = P.build(_plan(tmp_path, _base_items()), out, name="Bibliothèque", log=lambda *a: None)
    assert rep["dblibs"] == ["Bibliothèque.DbLib"], rep["dblibs"]
    table = out / "Bibliotheque.csv"                     # dblib-sync's ASCII name for the parts table
    table.write_bytes(table.read_bytes().replace(b"PcbLib\\R_0603_1608Metric.PcbLib", b"PcbLib\\MISSING.PcbLib"))
    probs, facts = P.check_folder(out, rep["libpkg"], rep["dblibs"])
    assert facts["dblib_rows"] == 2, facts
    assert probs == ["Bibliothèque.DbLib: RES-0603-10k Footprint Path PcbLib\\MISSING.PcbLib does not exist"], probs
