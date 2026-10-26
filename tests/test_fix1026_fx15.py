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


# ---------------------------------------------------------------- R-FX15-2: a plan is its id, not its path
BIG = {"terminal_length": {"min": 0.3, "max": 0.6}}


def _signed_off(root):
    """Change control signs a land change off at once under root (as an approved change: `part approve`)."""
    (root / "ecadlib.toml").write_text('[change]\ngeometry_major = "warn"\ngeometry_minor = "warn"\n')


def _new_land(d):
    """Plan folder d's R_0603 land changes (bigger terminals): the next sync settles it as a new ledger revision."""
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    spec.update(BIG)
    AL.write_pcblib(d / "models/PcbLib/R_0603_1608Metric.PcbLib", [AL.footprint_from_spec(spec)[0]])


def _pads(lib):
    from ecadlib import altium_lib as AL
    return [(r["w"], r["h"]) for c in AL.read_pcblib(lib / "PcbLib/R_0603_1608Metric.PcbLib") for r in c["records"]
            if r.get("type") == "pad"]


@lite
def test_dblib_renamed_plan_folder_and_moved_library_are_still_the_same_plan(tmp_path):
    """Writers were keyed by the plan path relative to the library: after the plan folder was renamed (or the library
    moved) its own approved land change was an error naming its old path ("another plan folder ... ../A"), and
    --prune / --overwrite-edits could not clear it. The plan is its id: the change goes through, one writer."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    S.sync(_plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")]), lib)
    before = _pads(lib)
    shutil.move(tmp_path / "A", tmp_path / "A2")
    _new_land(tmp_path / "A2")
    out = S.sync(tmp_path / "A2/plan.json", lib)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert _pads(lib) != before
    st = json.loads((lib / "ecadlib_sync.json").read_text())
    assert len(st["writers"]["rows"]["RES-0603-10k"]) == 1 and [v["path"] for v in st["writers"]["plans"].values()] == ["../A2"], st
    # the library folder moved: the same plan still
    (tmp_path / "shared").mkdir()
    shutil.move(lib, tmp_path / "shared/dblib")
    lib = tmp_path / "shared/dblib"
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    AL.write_pcblib(tmp_path / "A2/models/PcbLib/R_0603_1608Metric.PcbLib", [AL.footprint_from_spec(spec)[0]])
    out = S.sync(tmp_path / "A2/plan.json", lib)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert _pads(lib) == before


@lite
def test_dblib_same_part_approved_in_two_plans_is_not_held_by_each_other(tmp_path):
    """The same part in two BOMs (two plan folders, one DbLib): the land change approved in both left each sync held
    by the other forever. A plan whose released head is this drawing agrees; one that has not signed it off holds."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    S.sync(a, lib)
    assert _acts(S.sync(b, lib))["RES-0603-10k"] == "up_to_date"
    before = _pads(lib)
    _new_land(tmp_path / "A")                          # A alone signed the change off: B's released part stays
    res = next(r for r in S.sync(a, lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "another plan" in res["detail"], res
    assert _pads(lib) == before
    _new_land(tmp_path / "B")                          # now B too
    assert _acts(S.sync(b, lib))["RES-0603-10k"] == "updated"
    assert _pads(lib) != before
    assert _acts(S.sync(a, lib))["RES-0603-10k"] in ("up_to_date", "updated")


@lite
def test_dblib_writer_whose_plan_folder_is_gone_is_named_stale_and_can_be_forgotten(tmp_path):
    """A plan folder that synced here was deleted: the other plan's change was held naming it as if it were there,
    with --prune of that (gone) plan as the way out. It is named as gone, and --forget-missing-plans clears it."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    S.sync(a, lib)
    S.sync(_plan_folder(tmp_path / "B", [_res("RES-0603-10k", "RC0603FR-0710KL")]), lib)
    shutil.rmtree(tmp_path / "B")
    _new_land(tmp_path / "A")
    res = next(r for r in S.sync(a, lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "../B is not found from here" in res["detail"], res
    assert "--forget-missing-plans" in res["detail"], res
    out = S.sync(a, lib, forget_missing=True)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert any(r["action"] == "forgotten" and r["name"] == "../B" for r in out["results"]), out["results"]
    st = json.loads((lib / "ecadlib_sync.json").read_text())
    assert [v["path"] for v in st["writers"]["plans"].values()] == ["../A"], st


@lite
def test_dblib_state_keyed_by_plan_path_is_read_as_plan_ids(tmp_path):
    """A state an ecadlib before plan ids wrote (writers by plan path): this plan's own path is this plan, so its
    approved change goes through; another plan's path that is there still holds."""
    from ecadlib import altium_dblib_sync as S
    _signed_off(tmp_path)
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    S.sync(a, lib)
    st = json.loads((lib / "ecadlib_sync.json").read_text())
    st["writers"] = {"rows": {"RES-0603-10k": ["../A"]},
                     "files": {k: dict(v, plan="../A") for k, v in st["writers"]["files"].items()}}
    (lib / "ecadlib_sync.json").write_text(json.dumps(st))
    _new_land(tmp_path / "A")
    assert _acts(S.sync(a, lib))["RES-0603-10k"] == "updated"
    st = json.loads((lib / "ecadlib_sync.json").read_text())
    assert all(w.startswith("plan-") for w in st["writers"]["rows"]["RES-0603-10k"]), st


# ---------------------------------------------------------------- R-FX15-3: Altium 365 follows the same rule
def _a365_push(plan, nx, **kw):
    """`ecadlib altium365 push` of plan folder plan.parent against fake workspace nx: settle, then push."""
    from ecadlib import altium365 as A, change_gate as CG, rules as RU
    from tests.test_hunt1026_altpub import _push
    lib = Path(plan).parent
    items = json.loads(Path(plan).read_text())
    CG.publish_guard(plan, items, "altium365", log=lambda *a, **k: None)
    items = json.loads(Path(plan).read_text())
    if kw:
        for i in items:
            for k in ("symbol", "footprint"):
                i[k]["file"] = str(lib / i[k]["file"])
        return {r["name"]: r for r in A.push(items, nx, WS, rules=RU.load(None, start=lib), folder=lib, **kw)}
    return _push(A, plan, lib, nx, WS, RU.load(None, start=lib), items=items)


WS = "https://test.365.altium.com"


@lite
def test_a365_change_of_a_component_another_plan_pushed_waits_for_its_sign_off(tmp_path):
    """Two plan folders push RES-0603-10k (B's push adopts A's component). A's approval alone made a new revision of
    the component B's boards use (B never signed it off), and B's later approval made a needless second one. The same
    rule as dblib-sync: A's change waits for B; B's approval makes the one revision; A then adopts it."""
    from tests.test_change_control import FakeA365
    _signed_off(tmp_path)
    nx = FakeA365()
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    assert _a365_push(a, nx)["RES-0603-10k"]["action"] == "release"
    r = _a365_push(b, nx)["RES-0603-10k"]
    assert r["action"] == "up_to_date" and r.get("adopted"), r
    _new_land(tmp_path / "A")
    r = _a365_push(a, nx)["RES-0603-10k"]
    assert r["action"] == "skip" and "(B)" in r["detail"] and "another plan folder" in r["detail"], r
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1
    _new_land(tmp_path / "B")
    assert _a365_push(b, nx)["RES-0603-10k"]["action"] == "new_revision"
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 2
    r = _a365_push(a, nx)["RES-0603-10k"]
    assert r["action"] in ("up_to_date", "update_parameters") and r.get("adopted"), r
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 2
    # and the next push of each is quiet
    assert _a365_push(a, nx)["RES-0603-10k"]["action"] == "up_to_date"
    assert _a365_push(b, nx)["RES-0603-10k"]["action"] == "up_to_date"


@lite
def test_a365_one_plan_still_makes_its_revision_and_a_gone_plan_can_be_forgotten(tmp_path):
    """No false hold: a part only this plan pushed gets its new revision at once. A plan folder that pushed it and is
    gone is named stale; --forget-missing-plans clears it."""
    from tests.test_change_control import FakeA365
    _signed_off(tmp_path)
    nx = FakeA365()
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    _a365_push(a, nx)
    _new_land(tmp_path / "A")
    assert _a365_push(a, nx)["RES-0603-10k"]["action"] == "new_revision"
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-10k", "RC0603FR-0710KL")], land=BIG)
    assert _a365_push(b, nx)["RES-0603-10k"].get("adopted")
    shutil.rmtree(tmp_path / "B")
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    AL.write_pcblib(tmp_path / "A/models/PcbLib/R_0603_1608Metric.PcbLib", [AL.footprint_from_spec(spec)[0]])
    r = _a365_push(a, nx)["RES-0603-10k"]
    assert r["action"] == "skip" and "not found from here" in r["detail"] and "--forget-missing-plans" in r["detail"], r
    out = _a365_push(a, nx, forget_missing=True)
    assert out["RES-0603-10k"]["action"] == "new_revision", out
    assert out["B"]["action"] == "forgotten"


# ---------------------------------------------------------------- R-FX15-4: one plan folder per output library
def _quiet(*a, **k):
    pass


@lite
def test_kicad_export_into_another_plans_library_is_refused_unless_taken_over(tmp_path):
    """Two plan folders exported into one -o under one --name: B's export replaced A's library (A's symbol removed,
    the shared footprint now B's land) with only a removed-part note. Refused, naming A; --take-over still can. The
    same plan again, the plan folder renamed, and another --name into the same folder are fine."""
    from ecadlib import kicad_export as X
    out = tmp_path / "kicad"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")], land=BIG)
    X.export(a, out, "lib", log=_quiet)
    sym = (out / "lib.kicad_sym").read_bytes()
    try:
        X.export(b, out, "lib", log=_quiet)
        raise AssertionError("B's export replaced A's library")
    except X.PlanError as e:
        assert "another plan folder (../A)" in str(e) and "--take-over" in str(e), e
    assert (out / "lib.kicad_sym").read_bytes() == sym
    X.export(b, out, "libB", log=_quiet)                # another library in the same folder: fine
    X.export(a, out, "lib", log=_quiet)                 # the same plan again: fine
    shutil.move(tmp_path / "A", tmp_path / "A2")
    X.export(tmp_path / "A2/plan.json", out, "lib", log=_quiet)    # renamed: still the same plan
    X.export(b, out, "lib", log=_quiet, take_over=True)
    assert b"RES-0603-1k" in (out / "lib.kicad_sym").read_bytes()


def _refused(fn):
    try:
        fn()
    except ValueError as e:
        assert "another plan folder" in str(e) and "--take-over" in str(e), e
        return
    raise AssertionError("another plan folder's output was overwritten")


@lite
def test_kicad_dblib_pcm_and_altium_pkg_refuse_another_plans_output(tmp_path):
    """kicad-dblib (same -o and --name), kicad-pcm (same --out and package id) and altium-pkg of a models folder
    (same --out) let plan B overwrite plan A's output with no warning."""
    import datetime
    from ecadlib import altium_pkg as AP, change_gate as CG, kicad_dblib as KD, kicad_pcm as PCM
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")], land=BIG)
    KD.build(a, tmp_path / "kd", "lib", log=_quiet)
    db = (tmp_path / "kd/lib.sqlite").read_bytes()
    _refused(lambda: KD.build(b, tmp_path / "kd", "lib", log=_quiet))
    assert (tmp_path / "kd/lib.sqlite").read_bytes() == db
    KD.build(b, tmp_path / "kd", "lib", log=_quiet, take_over=True)
    assert (tmp_path / "kd/lib.sqlite").read_bytes() != db
    day = datetime.date(2026, 10, 26)
    pcm = lambda p, name="lib": PCM.build(p, tmp_path / "pcm", name, offline=True, cache=str(tmp_path / "c"),
                                          log=_quiet, today=day)
    pcm(a)
    zips = {z.name: z.read_bytes() for z in (tmp_path / "pcm").glob("*.zip")}
    _refused(lambda: pcm(b))
    assert {z.name: z.read_bytes() for z in (tmp_path / "pcm").glob("*.zip")} == zips
    pcm(b, "libB")                                      # another package in the same folder: fine
    AP.build(tmp_path / "A/models", tmp_path / "pkg", "lib", plan=a, log=_quiet)
    land = (tmp_path / "pkg/PcbLib/R_0603_1608Metric.PcbLib").read_bytes()
    _refused(lambda: AP.build(tmp_path / "B/models", tmp_path / "pkg", "lib", plan=b, log=_quiet))
    assert (tmp_path / "pkg/PcbLib/R_0603_1608Metric.PcbLib").read_bytes() == land
    assert CG.OWNER in {p.name for p in (tmp_path / "pkg").iterdir()}


# ---------------------------------------------------------------- R-FX15-5: model item names never doubled
class _Taken:
    """A workspace where NAME, NAME-r3 and NAME-r3-2 .. -9 are taken (items this library has no record of, which do
    not read back as this drawing); every release answers duplicate. Records the item names each release tried."""
    def __init__(self, name, upto=9):
        self.taken = {name, f"{name}-r3"} | {f"{name}-r3-{k}" for k in range(2, upto + 1)}
        self.tried = []

    def find_model(self, ws, kind, name, folder):
        return f"{kind}:{name}" if name in self.taken else None

    def model_data(self, ws, kind, name, folder):
        return b"not a library"

    def release(self, inp):
        from ecadlib import altium365 as A
        self.tried.append(inp.get("footprintItemName"))
        raise A.NexarError(f"AddALU_ItemsDuplicate item HRID {inp.get('footprintItemName')}")

    def find(self, ws, name):
        return {"id": "C1", "revision": {"revisionId": "R1"}}

    def folder_id(self, ws, path):
        return f"folder:{path}"


@lite
def test_a365_place_model_never_names_a_taken_item_and_never_doubles_the_revision(tmp_path):
    """With NAME, NAME-r<rev> and NAME-r<rev>-2..9 taken, place_model returned NAME-r<rev> (taken), and the duplicate
    answer renamed it NAME-r<rev>-r<rev>. The first free name is chosen, the same on every run; a duplicate answer
    for a name place_model chose is an error, never a doubled name."""
    from ecadlib import altium365 as A
    nx = _Taken("R_0603_1608Metric")
    pick = lambda: A.place_model(nx, WS, None, A.ws_target(WS), {}, "footprints", "R_0603_1608Metric",
                                 "Components/Models/Footprints", "R_0603_1608Metric", "want", 3)
    assert pick() == ("send", "R_0603_1608Metric-r3-10") == pick()
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    it = json.loads(a.read_text())[0]
    for k in ("symbol", "footprint"):
        it[k]["file"] = str(tmp_path / "A" / it[k]["file"])
    try:                                               # (push() reports the duplicate answer as the part's error)
        res = A.push_revision(it, nx, WS, {}, lambda p: "file", "Components/Models", {"id": "C1"}, {"footprint"}, 3,
                              digests={"footprints": "want"}, target=A.ws_target(WS))
    except A.NexarError as e:
        res = {"action": "error", "detail": str(e)}
    assert res["action"] == "error" and nx.tried == ["R_0603_1608Metric-r3-10"], (res, nx.tried)
    full = _Taken("R_0603_1608Metric", upto=99)        # every name taken: nothing is sent
    got = A.place_model(full, WS, None, A.ws_target(WS), {}, "footprints", "R_0603_1608Metric",
                        "Components/Models/Footprints", "R_0603_1608Metric", "want", 3)
    assert got[0] == "full", got
