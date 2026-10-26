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


# ---------------------------------------------------------------- R-FX18-2: the target moves, its plans are still there
@lite
def test_dblib_moved_library_never_calls_a_live_plan_gone(tmp_path):
    """F2: A and B share a DbLib; the DbLib folder moves (dblib -> shared/dblib) and B approves a new land. B was held
    saying live A "is not there any more ... --forget-missing-plans", and following that shipped B's land on A's
    released row unsigned. Where each plan was seen is kept absolute too: A is found, named as another plan, and
    --forget-missing-plans does not drop it."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    S.sync(a, lib)
    S.sync(b, lib)
    (tmp_path / "shared").mkdir()
    shutil.move(lib, tmp_path / "shared/dblib")
    lib = tmp_path / "shared/dblib"
    before = _pads(lib)
    _new_land(tmp_path / "B")
    res = next(r for r in S.sync(b, lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "another plan" in res["detail"], res
    assert "--forget-missing-plans" not in res["detail"] and "not found" not in res["detail"], res
    out = S.sync(b, lib, forget_missing=True)
    assert not any(r["action"] == "forgotten" for r in out["results"]), out["results"]
    assert _acts(out)["RES-0603-10k"] == "error", out["results"]
    assert _pads(lib) == before
    st = json.loads((lib / "ecadlib_sync.json").read_text())
    assert {Path(v["abs"]) for v in st["writers"]["plans"].values()} == {(tmp_path / "A").resolve(), (tmp_path / "B").resolve()}, st
    assert _acts(S.sync(a, lib))["RES-0603-10k"] == "up_to_date"     # A from where it is: the same plan, still


@lite
def test_moved_output_folder_never_calls_its_live_writer_gone(tmp_path):
    """.ecadlib_owner.json kept the writer relative to -o: after -o moved, B's refusal said A "is not there any more:
    --take-over if that plan no longer exists" while A was live. A is found and named; no take-over advice for it."""
    from ecadlib import kicad_export as X
    from tests.test_fix1026_fx15 import _quiet
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    X.export(a, tmp_path / "kicad", "lib", log=_quiet)
    (tmp_path / "shared").mkdir()
    shutil.move(tmp_path / "kicad", tmp_path / "shared/kicad")
    try:
        X.export(b, tmp_path / "shared/kicad", "lib", log=_quiet)
        raise AssertionError("B wrote over A's library")
    except X.PlanError as e:
        assert "another plan folder" in str(e) and "no longer exists" not in str(e) and "not found" not in str(e), e
    X.export(a, tmp_path / "shared/kicad", "lib", log=_quiet)          # A itself: fine


# ---------------------------------------------------------------- R-FX18-3: Altium 365 claims across projects
def _two_projects(tmp_path):
    for p in ("projA", "projB"):
        (tmp_path / p).mkdir()
        _signed_off(tmp_path / p)
    a = _plan_folder(tmp_path / "projA/A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "projB/B", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    return a, b


@lite
def test_a365_component_shared_by_plans_of_two_projects_waits_for_both(tmp_path):
    """F4: plan folders in two projects (each its own ecadlib.toml) push to one workspace; B adopts A's component.
    ecadlib_publishers.json is per project, so A's approval alone made a new revision of the component B's boards use.
    This computer's record of who pushed what to the workspace covers both: A's change waits for B."""
    from tests.test_change_control import FakeA365
    from tests.test_fix1026_fx15 import _a365_push
    nx = FakeA365()
    a, b = _two_projects(tmp_path)
    assert _a365_push(a, nx)["RES-0603-10k"]["action"] == "release"
    r = _a365_push(b, nx)["RES-0603-10k"]
    assert r["action"] == "up_to_date" and r.get("adopted") and "Neither this project" not in r["adopted"], r
    _new_land(tmp_path / "projA/A")
    r = _a365_push(a, nx)["RES-0603-10k"]
    assert r["action"] == "skip" and "another plan folder" in r["detail"] and "projB" in r["detail"], r
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1
    _new_land(tmp_path / "projB/B")                    # B signs it off too: one revision, A adopts it
    assert _a365_push(b, nx)["RES-0603-10k"]["action"] == "new_revision"
    assert _a365_push(a, nx)["RES-0603-10k"].get("adopted")
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 2


@lite
def test_a365_adopting_a_component_no_record_names_says_its_changes_are_not_held(tmp_path, monkeypatch):
    """A component pushed from a plan folder neither this project nor this computer has a record of (another
    computer's): its later changes cannot wait for this plan. The adoption says so, plainly, instead of nothing."""
    from tests.test_change_control import FakeA365
    from tests.test_fix1026_fx15 import _a365_push
    nx = FakeA365()
    a, b = _two_projects(tmp_path)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "computer1")
    _a365_push(a, nx)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "computer2")
    r = _a365_push(b, nx)["RES-0603-10k"]
    assert r["action"] == "up_to_date" and "Neither this project nor this computer" in r["adopted"] and "not held" in r["adopted"], r


# ---------------------------------------------------------------- R-FX18-4: kicad-pcm staging keyed by package
@lite
def test_kicad_pcm_two_packages_with_their_own_identifier_share_one_out(tmp_path):
    """F3: plans A and B build separate packages (--identifier) into one --out under one --name. The plan export staged
    in <out>/.kicad-export claimed kicad-export:<name> for A, so B was refused although the packages are separate.
    The staging folder is the package's own: both build, and each package holds only its plan's parts; the same
    identifier from another plan is still refused."""
    import datetime
    import zipfile
    from ecadlib import kicad_pcm as PCM
    from tests.test_fix1026_fx15 import _quiet, _refused
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    day = datetime.date(2026, 10, 26)
    pcm = lambda p, ident: PCM.build(p, tmp_path / "pcm", "parts", identifier=ident, offline=True,
                                     cache=str(tmp_path / "c"), log=_quiet, today=day)
    ra = pcm(a, "com.acme.boarda")
    rb = pcm(b, "com.acme.boardb")
    assert pcm(a, "com.acme.boarda")["zip"] == ra["zip"]
    syms = lambda r: zipfile.ZipFile(r["zip"]).read(
        next(n for n in zipfile.ZipFile(r["zip"]).namelist() if n.endswith(".kicad_sym"))).decode()
    assert "RES-0603-10k" in syms(ra) and "RES-0603-1k" not in syms(ra)
    assert "RES-0603-1k" in syms(rb) and "RES-0603-10k" not in syms(rb)
    _refused(lambda: pcm(b, "com.acme.boarda"))


@lite
def test_kicad_pcm_keyed_staging_starts_from_the_folder_an_earlier_build_kept(tmp_path):
    """A package built before the staging folder was keyed kept its export in <out>/.kicad-export (a released part
    held again keeps the version the last package shipped, R-FL-3): the package's own staging folder starts from it."""
    import datetime
    from ecadlib import kicad_pcm as PCM
    from tests.test_fix1026_fx15 import _quiet
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    pcm = lambda: PCM.build(a, tmp_path / "pcm", "parts", identifier="com.acme.boarda", offline=True,
                            cache=str(tmp_path / "c"), log=_quiet, today=datetime.date(2026, 10, 26))
    pcm()
    keyed = [p for p in (tmp_path / "pcm").iterdir() if p.name.startswith(".kicad-export") and p.name != ".kicad-export"]
    assert len(keyed) == 1, sorted(p.name for p in (tmp_path / "pcm").iterdir())
    shutil.rmtree(tmp_path / "pcm/.kicad-export", ignore_errors=True)
    keyed[0].rename(tmp_path / "pcm/.kicad-export")                # as an earlier ecadlib left it
    (tmp_path / "pcm/.kicad-export/kept.txt").write_text("kept")
    pcm()
    assert (keyed[0] / "kept.txt").read_text() == "kept"
