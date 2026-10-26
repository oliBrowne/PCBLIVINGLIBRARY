"""Fixes 2026-10-26 (fx18): second re-review of the shared publish targets (R-FX18-1..6). A plan folder is its id
(change_gate.plan_id); a copy of it is another plan, and a plan that moved (or whose target moved) is still itself.
Plain functions: python -m tests.run_all test_fix1026_fx18. Altium 365 / Nexar is always a fake here."""
import json
import shutil
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_fix1026_pubtgt import _acts, _plan_folder, _res
from tests.test_fix1026_fx15 import _new_land, _pads, _signed_off


def _pid(d):
    return json.loads((Path(d) / ".ecadlib/plan_id.json").read_text())


# ---------------------------------------------------------------- R-FX18-1: a copy of a renamed plan folder
@lite
def test_plan_id_follows_a_rename_so_a_later_copy_gets_an_id_of_its_own(tmp_path):
    """plan_id.json kept the folder the id was made in: after a rename it pointed at the gone path for ever, so a copy
    of the renamed folder kept the same id (the copy was "me"). The recorded folder follows the plan; a copy beside
    it gets its own id."""
    from ecadlib import change_gate as CG
    (tmp_path / "A").mkdir()
    pid = CG.plan_id(tmp_path / "A")
    shutil.move(tmp_path / "A", tmp_path / "A2")
    assert CG.plan_id(tmp_path / "A2") == pid
    assert Path(_pid(tmp_path / "A2")["folder"]) == (tmp_path / "A2").resolve()
    shutil.copytree(tmp_path / "A2", tmp_path / "C")
    assert CG.plan_id(tmp_path / "C") not in (None, pid)
    assert CG.plan_id(tmp_path / "A2") == pid
    # a dry run (create=False) never writes
    shutil.move(tmp_path / "A2", tmp_path / "A3")
    assert CG.plan_id(tmp_path / "A3", create=False) == pid
    assert Path(_pid(tmp_path / "A3")["folder"]) == (tmp_path / "A2").resolve()


@lite
def test_dblib_copy_of_a_renamed_plan_cannot_change_the_originals_land(tmp_path):
    """F1: sync A, rename A -> A2 and sync, copy A2 -> C; the land changes and is signed off in C only. C's sync
    changed the DbLib land A2 released (A2 never approved it), and A2's next sync flipped it back. C is another plan:
    held, the land stays."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    S.sync(_plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")]), lib)
    before = _pads(lib)
    shutil.move(tmp_path / "A", tmp_path / "A2")
    assert _acts(S.sync(tmp_path / "A2/plan.json", lib))["RES-0603-10k"] == "up_to_date"
    shutil.copytree(tmp_path / "A2", tmp_path / "C")
    _new_land(tmp_path / "C")
    res = next(r for r in S.sync(tmp_path / "C/plan.json", lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "another plan" in res["detail"] and "A2" in res["detail"], res
    assert _pads(lib) == before
    assert _acts(S.sync(tmp_path / "A2/plan.json", lib))["RES-0603-10k"] == "up_to_date"


@lite
def test_dblib_second_live_folder_with_the_same_id_is_a_copy(tmp_path):
    """Renamed, then copied before either synced: both folders hold the same id and both record the gone folder. The
    library saw the id last at A2 (still there, still that id): C arriving with it is a copy, given an id of its own,
    and its land change is held for A2."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    S.sync(_plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")]), lib)
    before = _pads(lib)
    pid = _pid(tmp_path / "A")["id"]
    shutil.move(tmp_path / "A", tmp_path / "A2")
    shutil.copytree(tmp_path / "A2", tmp_path / "C")
    assert _acts(S.sync(tmp_path / "A2/plan.json", lib))["RES-0603-10k"] == "up_to_date"
    _new_land(tmp_path / "C")
    res = next(r for r in S.sync(tmp_path / "C/plan.json", lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "another plan" in res["detail"], res
    assert _pads(lib) == before
    assert _pid(tmp_path / "C")["id"] != pid and _pid(tmp_path / "A2")["id"] == pid


@lite
def test_output_owner_and_a365_claims_tell_a_copy_of_a_renamed_plan(tmp_path):
    """.ecadlib_owner.json and the Altium 365 claims keyed by the same id: a copy made after a rename (before either
    published) wrote over the original's KiCad library / made a new revision of the component it pushed. The copy is
    another plan folder there too."""
    from ecadlib import kicad_export as X
    from tests.test_change_control import FakeA365
    from tests.test_fix1026_fx15 import _a365_push, _quiet
    _signed_off(tmp_path)
    nx = FakeA365()
    _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    out = tmp_path / "kicad"
    X.export(tmp_path / "A/plan.json", out, "lib", log=_quiet)
    assert _a365_push(tmp_path / "A/plan.json", nx)["RES-0603-10k"]["action"] == "release"
    shutil.move(tmp_path / "A", tmp_path / "A2")
    shutil.copytree(tmp_path / "A2", tmp_path / "C")
    X.export(tmp_path / "A2/plan.json", out, "lib", log=_quiet)
    assert _a365_push(tmp_path / "A2/plan.json", nx)["RES-0603-10k"]["action"] == "up_to_date"
    try:
        X.export(tmp_path / "C/plan.json", out, "lib", log=_quiet)
        raise AssertionError("the copy wrote over the original's library")
    except X.PlanError as e:
        assert "another plan folder (../A2)" in str(e), e
    _new_land(tmp_path / "C")
    r = _a365_push(tmp_path / "C/plan.json", nx)["RES-0603-10k"]
    assert r["action"] == "skip" and "another plan folder" in r["detail"] and "A2" in r["detail"], r
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1
