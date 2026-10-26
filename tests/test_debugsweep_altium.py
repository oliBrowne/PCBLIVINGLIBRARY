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


def _make_pad(fields):
    """Run ecadlib_build.pas's MakePad on one PAD recipe line (its fields) in the DelphiScript interpreter, Altium's
    pad faked. -> what MakePad set on the pad (properties, SetState_* calls, the pad cache), comparable with ==."""
    from tests.test_review_1018_build_fail import Fake, Pas, TStringList

    def snap(v):
        if isinstance(v, Fake):
            return (v._name, tuple(sorted((k, snap(x)) for k, x in v.__dict__.items() if k != "_name")))
        return tuple(snap(x) for x in v) if isinstance(v, (list, tuple)) else v

    class Pad(Fake):
        def __getattr__(self, k):
            if k.startswith("setstate_"):
                return lambda *a: self.__dict__.setdefault("calls", []).append((k, *map(snap, a)))
            return super().__getattr__(k)

    pad = Pad("pad")
    server = Fake("pcbserver", pcbobjectfactory=lambda *a: pad)
    pas = Pas(AN.PAS, PCBServer=server, PcbLayer=lambda n: n)     # PcbLayer is a Case: the interpreter has none
    ebf = TStringList()
    ebf.items = list(fields)
    pas.globals["ebf"] = ebf
    pas.call("MakePad", Fake("comp"))
    return snap(pad)


def test_ds2_34_the_altium_build_script_puts_the_drill_offset_on_the_pad():
    fp, _ = K.convert_footprint(OFFSET_PADS, notes=[])
    pads = [ln.split("\t") for ln in AN.footprint_lines(fp) if ln.startswith("PAD\t")]
    assert len(pads) == 2
    for f in pads:
        calls = {(c[0], c[1][0]): c[2] for c in dict(_make_pad(f)[1])["calls"] if c[0].endswith("padoffset")}
        for layer in ("etoplayer", "emidlayer1", "ebottomlayer"):    # 0.7 mm = 275591 internal units
            assert calls.get(("setstate_xpadoffset", layer)) == 275591, calls
            assert calls.get(("setstate_ypadoffset", layer)) == 0, calls


def test_every_pad_field_the_recipe_writes_changes_the_pad_the_altium_script_builds():
    """A PAD field footprint_lines writes and MakePad never reads is a property Altium's rebuild silently drops
    (DS2-34: the drill offset). Each field of a pad that uses every one of them is changed in turn."""
    fp = {"name": "ALL", "pads": [{"designator": "1", "x": 1.0, "y": 2.0, "w": 3.0, "h": 1.6, "rot": 45, "shape": "roundrect",
                                   "corner": 25, "layer": 74, "hole": 1.0, "hole_shape": "slot", "slot": 2.0,
                                   "hole_rot": 90, "plated": True, "paste": 0.05, "mask": 0.1, "offset": (0.7, 0.3)}]}
    fields = next(ln for ln in AN.footprint_lines(fp) if ln.startswith("PAD\t")).split("\t")
    base = _make_pad(fields)
    ignored = []
    for i in range(1, len(fields)):
        changed = list(fields)
        changed[i] = str(int(fields[i]) + 1000) if fields[i].lstrip("-").isdigit() else fields[i] + "X"
        if _make_pad(changed) == base:
            ignored.append((i, fields[i]))
    assert not ignored, f"PAD fields MakePad ignores: {ignored}"


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


@lite
def test_ds2_35_parity_reports_a_workbook_ref_altium_cannot_store_for_that_part(tmp_path):
    import csv, shutil
    from ecadlib import parity as P
    from tests.test_parity import _pack
    d = tmp_path / "pack"
    shutil.copytree(_pack()["dir"], d)
    t = d / "altium" / "testpack.csv"
    rows = list(csv.DictReader(open(t, encoding="utf-8-sig")))
    for r in rows:
        if r["Part Number"] == "MMBT3904LT1G":
            r["Footprint Ref"] = "SOT-23\u2192"                 # typed into the workbook by hand
    with open(t, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rep = P.check_plan(d / "library" / "plan.json", d / "kicad", "testpack", d / "altium")     # was: ValueError
    bad = [x for x in rep["database"] if x["what"] == "part MMBT3904LT1G" and x["field"] == "Footprint Ref"]
    assert len(bad) == 1 and "can't store" in bad[0]["msg"] and bad[0]["level"] == "error", rep["database"]
    assert not rep["ok"]


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
def test_ds2_36_menu_build_in_a_non_ascii_windows_folder_leaves_out_the_menu_file_and_says_why(tmp_path):
    import os
    if os.name != "nt":                  # the folder is derived (and the menu file built) only on Windows
        return
    from ecadlib import altium_menu as AM
    out = tmp_path.resolve() / "José" / "menu"
    rep = AM.build(out, None, None)
    assert rep["menu_file"] is None and not (out / AM.PREFS).exists(), rep
    assert (out / "altium_menu.json").is_file() and "ASCII" in rep.get("menu_file_note", ""), rep
    readme = (out / AM.README).read_text(encoding="utf-8")
    assert "build --win-dir" not in readme, "advice that fails the same way for this folder"
    assert f'install-menu --out "{out}"' in readme                  # a step that works here (Altium's menu file is not ASCII)


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
@lite
def test_ds2_37_svn_commit_targets_every_new_folder_add_parents_schedules(tmp_path):
    from ecadlib import altium_dblib_sync as S
    from tests.test_altium_ad26_overnight import _models_with_step     # R_0603 with an embedded STEP body
    from tests.test_dblib_sync import _base_items, _plan
    _models_with_step(tmp_path)
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


def _svn_wc(tmp_path):
    """A new local repository and its working copy, (repo, wc); None without svn (CI has none: the tests that use
    this check what the command-level tests beside them cannot, real svn's answer)."""
    import shutil, subprocess
    if not (shutil.which("svn") and shutil.which("svnadmin")):
        return None
    repo, wc = tmp_path.resolve() / "repo", tmp_path.resolve() / "wc"
    subprocess.run(["svnadmin", "create", str(repo)], check=True, capture_output=True)
    subprocess.run(["svn", "checkout", repo.as_uri(), str(wc)], check=True, capture_output=True)
    return repo, wc


@lite
def test_ds2_38_prune_commit_leaves_no_step_file_in_a_real_svn_repository(tmp_path):
    import subprocess
    from ecadlib import altium_dblib_sync as S
    from tests.test_altium_ad26_overnight import _models_with_step
    from tests.test_dblib_sync import _base_items, _plan
    if not (svn := _svn_wc(tmp_path)):
        return
    repo, wc = svn
    _models_with_step(tmp_path)
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


@lite
def test_ds2_38_prune_commit_skips_files_svn_never_added(tmp_path):
    """A working copy an older ecadlib synced: it svn-added the libraries but not their recipe / .ecadstate (or the
    commands were never run). svn delete of a path svn does not know fails, and took the whole prune commit with it."""
    import subprocess
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _base_items, _models, _plan
    if not (svn := _svn_wc(tmp_path)):
        return
    repo, wc = svn
    _models(tmp_path)
    first = S.sync(_plan(tmp_path, _base_items()), wc, mode="svndblib", svn_url=repo.as_uri())
    old = [w for w in first["written"] if not w.endswith((".ecadbuild", ".ecadstate"))]
    assert S.svn_step(wc, old, [], commit=True)["ran"]
    no_cap = [i for i in _base_items() if i["name"] != "CAP-0402-100nF"]
    out = S.sync(_plan(tmp_path, no_cap), wc, mode="svndblib", svn_url=repo.as_uri(), prune=True)
    assert "PcbLib/C_0402_1005Metric.PcbLib.ecadbuild" in out["removed"], out["removed"]
    res = S.svn_step(wc, out["written"], out["removed"], commit=True)
    assert res["ran"], res
    assert "PcbLib/C_0402_1005Metric.PcbLib.ecadbuild" in res["not_versioned"], res
    ls = subprocess.run(["svn", "ls", "-R", repo.as_uri()], capture_output=True, text=True).stdout
    assert "C_0402" not in ls and "Capacitor" not in ls, ls          # the libraries' deletion is committed


@lite
def test_ds2_38_svn_step_leaves_paths_svn_does_not_know_out_of_delete_and_commit(tmp_path):
    """The same with svn faked (CI has none): `svn status --xml` as svn 1.14 prints it, an entry for a path it knows
    (here missing: deleted by the sync), a bare <target> for one it does not."""
    from types import SimpleNamespace
    from ecadlib import altium_dblib_sync as S
    known = {"PcbLib/C_0402_1005Metric.PcbLib", "SchLib/Capacitor.SchLib"}
    removed = sorted(known | {"PcbLib/C_0402_1005Metric.PcbLib.ecadbuild", "SchLib/Capacitor.SchLib.ecadstate"})
    calls = []

    def svn(cmd, cwd=None):
        calls.append(cmd)
        if cmd[1] == "status":
            body = "".join(f'<target\n   path="{p}">\n' + (f'<entry\n   path="{p}">\n<wc-status\n   props="none"\n'
                           '   item="missing"\n   revision="1">\n</wc-status>\n</entry>\n' if p in known else "")
                           + "</target>\n" for p in cmd[cmd.index("empty") + 1:])
            return SimpleNamespace(returncode=0, stdout=f'<?xml version="1.0" encoding="UTF-8"?>\n<status>\n{body}</status>\n',
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    res = S.svn_step(tmp_path, ["ecadlib.csv"], removed, commit=True, runner=svn, which=lambda _: "svn")
    assert res["ran"] and res["not_versioned"] == ["PcbLib/C_0402_1005Metric.PcbLib.ecadbuild",
                                                   "SchLib/Capacitor.SchLib.ecadstate"], res
    delete = next(c for c in calls if c[1] == "delete")
    commit = next(c for c in calls if c[1] == "commit")
    assert delete[3:] == sorted(known), delete
    assert not any(p.endswith((".ecadbuild", ".ecadstate")) for p in commit), commit


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


def test_no_altium_settings_folder_is_said_before_altium_is_running(tmp_path):
    """PC test run 2026-10-26 (Altium open): with no settings folder the menu bar cannot go in anyway, so that is the
    refusal, not "close Altium first" (test_ds2_36 failed on any Windows machine with Altium running)."""
    from unittest import mock
    from ecadlib import altium_menu as AM
    with mock.patch.object(AM, "check_win_dir", lambda w: "D:\\ecadlib menu"), \
            mock.patch.object(AM, "altium_user_folders", lambda *a: []):
        try:
            AM.install_menu_bar(str(tmp_path), running=lambda: True)
            raise AssertionError("must refuse")
        except ValueError as e:
            assert "no Altium Designer settings folder" in str(e), e
