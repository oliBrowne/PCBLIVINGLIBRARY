"""Fixes 2026-10-26 (fx15): re-review of the shared publish targets (R-FX15-1..5). Change control is per plan folder;
a target two plan folders publish to (a DbLib folder, an Altium 365 workspace, a KiCad output folder, a PCM package)
must never let one plan change what the other released, and must not hold a plan forever on a record of itself.
Plain functions: python -m tests.run_all test_fix1026_fx15. Altium 365 / Nexar is always a fake here."""
import json
import shutil
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_fix1026_pubtgt import ROOT, _acts, _plan_folder, _res, _rows


def _land(path, name, **change):
    """A .PcbLib at path holding one 0603 land named `name` (change: spec fields, e.g. another terminal length)."""
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    spec.update(change, name=name)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    AL.write_pcblib(path, [AL.footprint_from_spec(spec)[0]])


def _my_r0603_plan(d, name="RES-0603-10k", mpn="RC0603FR-0710KL"):
    """Plan folder d whose part uses the company's own land MY_R0603 (smaller pads), as `--lib mylib` gives it."""
    from tests.test_dblib_sync import _plan
    _plan_folder(d, [])
    _land(d / "models/PcbLib/MY_R0603.PcbLib", "MY_R0603", terminal_length={"min": 0.2, "max": 0.3})
    it = _res(name, mpn)
    it["footprint"] = {"ref": "MY_R0603", "file": "models/PcbLib/MY_R0603.PcbLib"}
    return _plan(d, [it])


# ---------------------------------------------------------------- R-FX15-1: a model change of another plan's row
@lite
def test_dblib_other_plan_cannot_repoint_a_released_row_to_another_land(tmp_path):
    """Plan B has the same part (same MPN) on its own land MY_R0603: its sync rewrote A's released row (Footprint Ref,
    Footprint Path) and added MY_R0603.PcbLib, while A's ledger still says r1 (R_0603) went there. Another plan's row
    keeps its land: held, the row and the library folder unchanged."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    assert _acts(S.sync(a, lib)) == {"RES-0603-10k": "added"}
    before = _rows(lib)["RES-0603-10k"]
    out = S.sync(_my_r0603_plan(tmp_path / "B"), lib)
    res = next(r for r in out["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "held" and "another plan" in res["detail"] and "Footprint" in res["detail"], res
    assert _rows(lib)["RES-0603-10k"] == before
    assert not (lib / "PcbLib/MY_R0603.PcbLib").exists()


@lite
def test_dblib_own_plan_still_repoints_its_row_and_other_plan_still_edits_fields(tmp_path):
    """No false hold: the plan that released the row moves it to another land (its own approved change); another
    plan's change of a field that is not identity or model (the description) still goes through."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    S.sync(a, lib)
    shutil.rmtree(tmp_path / "A")
    out = S.sync(_my_r0603_plan(tmp_path / "A"), lib)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert _rows(lib)["RES-0603-10k"]["Footprint Ref"] == "MY_R0603"
    from tests.test_dblib_sync import _plan
    it = _res("RES-0603-10k", "RC0603FR-0710KL")
    it["footprint"] = {"ref": "MY_R0603", "file": "models/PcbLib/MY_R0603.PcbLib"}
    it["description"] = "another description"
    b = _plan_folder(tmp_path / "B", [])
    shutil.copy(tmp_path / "A/models/PcbLib/MY_R0603.PcbLib", tmp_path / "B/models/PcbLib/MY_R0603.PcbLib")
    out = S.sync(_plan(tmp_path / "B", [it]), lib)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert _rows(lib)["RES-0603-10k"]["Description"] == "another description"
