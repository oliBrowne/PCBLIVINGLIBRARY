"""Regression tests for the debug sweep's librarian-side reports (DS2-*): rebuild, upkeep, change_cli.
Plain functions (tests.run_all, no pytest)."""
import contextlib, hashlib, io, json
from pathlib import Path

from ecadlib import cli, librarian as L, rebuild as RB
from tests import upgrade_fixtures as UF
from tests.e2e_support import seed_cache
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _tree(root):
    """{relative path: sha256} of every file under root."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _library(d):
    """A real librarian output folder (offline, from the recorded cache) with its lock and change-control ledger."""
    d = Path(d).resolve()
    (d / "bom.csv").write_text(UF.BOM, encoding="utf-8")
    seed_cache(d / "cache")
    L.run(d / "bom.csv", out=d / "lib", offline=True, cache=d / "cache", rules={}, log=QUIET)
    return d / "lib"


@lite
def test_ds2_46_rebuild_check_keep_never_deletes_or_writes_into_the_library(tmp_path):
    lib = _library(tmp_path)
    before = _tree(lib)
    assert "plan.json" in before and "library.ledger.jsonl" in before and RB.LOCK in before
    # --keep the folder that holds the library (`--keep .` from its parent), and --keep the library itself
    for keep in (lib.parent, lib):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cli.main(["rebuild", str(lib), "--check", "--keep", str(keep)])
        assert _tree(lib) == before, f"--check --keep {keep} changed the library"
        assert rc == 2 and "internal error" not in err.getvalue(), (rc, err.getvalue())


@lite
def test_ds2_47_upkeep_a365_compares_under_the_house_parameter_names(tmp_path):
    from ecadlib import rules as RU, upkeep as UK
    from tests import test_upstream as TU
    items = TU.plan_items()[:1]                     # CH340C: Manufacturer=WCH, Manufacturer Part Number=CH340C
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(items), encoding="utf-8")
    rules = RU.load({"parameters": {"names": {"Manufacturer Part Number": "MfrPN", "Manufacturer": "Mfr"}}})
    # the workspace holds the part exactly as `altium365 push` sends it: in the house names
    ws = TU.node("CH340C", "Components/ICs", {"Mfr": "WCH", "MfrPN": "CH340C"}, comment="CH340C", description="USB UART")
    rep = UK.run(plan, ["a365"], tmp_path / "s", tmp_path / "r", workspace="https://ws", rules=rules,
                 ctx=UK.Ctx(design=TU.FakeWorkspace([ws]), env={}), now="2026-10-05T06:23:00+00:00")
    sec = next(s for s in rep["checks"] if s["check"] == "a365")
    assert sec["status"] == "ok", sec
    assert rep["new_changes"] == 0 and rep["exit_code"] == 0, sec["changes"]


@lite
def test_ds2_48_rebuild_check_finds_the_lib_files_of_a_moved_project(tmp_path):
    import shutil
    root = tmp_path.resolve()
    proj = root / "proj"
    proj.mkdir()
    (proj / "bom.csv").write_text("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description\n"
                                  "U5,1,CH340C,SOIC-16_3.9x9.9mm_P1.27mm,WCH,CH340C,C84681,USB to UART bridge\n", encoding="utf-8")
    shutil.copytree(Path(__file__).resolve().parent.parent / "examples" / "kicad_lib", proj / "kicad_lib")
    L.run(proj / "bom.csv", [proj / "kicad_lib"], proj / "lib", sources=["standard"], offline=True, cache=proj / "cache",
          rules={}, log=QUIET)
    assert RB.read_lock(proj / "lib")["parts"]["CH340C"]["local"], "CH340C comes from the --lib library"
    # a clone or a moved checkout: the same files in the same relative places, the original folder gone
    shutil.copytree(proj, root / "clone")
    shutil.move(str(proj), str(root / "moved_away"))
    rep = RB.check(root / "clone" / "lib", log=QUIET)
    assert rep["ok"] and rep["counts"]["DRIFT"] == 0, [d for d in rep["inputs"] if not d.get("info")]
    # and it is the clone's own --lib file that is checked
    sym = root / "clone" / "kicad_lib" / "ecadlib_demo.kicad_sym"
    sym.write_text(sym.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    rep = RB.check(root / "clone" / "lib", log=QUIET)
    assert any(d["part"] == "CH340C" and d["what"] == "input" for d in rep["inputs"]) and not rep["ok"], rep["inputs"]
