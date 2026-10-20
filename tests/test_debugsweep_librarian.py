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


def _library(d, out=None):
    """A real librarian output folder (offline, from the recorded cache) with its lock and change-control ledger.
    The BOM and the cache are in d; the library is d/lib unless out says where."""
    d = Path(d).resolve()
    d.mkdir(parents=True, exist_ok=True)
    out = Path(out).resolve() if out else d / "lib"
    (d / "bom.csv").write_text(UF.BOM, encoding="utf-8")
    seed_cache(d / "cache")
    L.run(d / "bom.csv", out=out, offline=True, cache=d / "cache", rules={}, log=QUIET,
          allow=["EEPART1"], user="Test Reviewer <reviewer@example.com>")   # its EasyEDA conversion checked: released,
    return out                                                                # so the library has a ledger


def _check_keep(lib, keep):
    """`ecadlib rebuild LIB --check --keep KEEP` -> (exit code, stderr)."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = cli.main(["rebuild", str(lib), "--check", "--keep", str(keep)])
    return rc, err.getvalue()


@lite
def test_ds2_46_rebuild_check_keep_never_deletes_or_writes_into_the_library(tmp_path):
    lib = _library(tmp_path)
    before = _tree(lib)
    assert "plan.json" in before and "library.ledger.jsonl" in before and RB.LOCK in before
    # --keep the folder that holds the library (`--keep .` from its parent), and --keep the library itself
    for keep in (lib.parent, lib):
        rc, err = _check_keep(lib, keep)
        assert _tree(lib) == before, f"--check --keep {keep} changed the library"
        assert rc == 2 and "internal error" not in err, (rc, err)


@lite
def test_ds2_46_rebuild_module_entry_point_refuses_an_overlapping_keep_in_one_line(tmp_path):
    # `python -m ecadlib.rebuild LIB --check --keep ..` (rebuild.main, not the ecadlib CLI)
    lib = _library(tmp_path)
    before = _tree(lib)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = RB.main([str(lib), "--check", "--keep", str(lib.parent)])
    assert _tree(lib) == before
    assert rc == 2 and err.getvalue().startswith("ecadlib rebuild: --keep "), (rc, err.getvalue())


@lite
def test_ds2_46_rebuild_check_keep_never_replaces_a_folder_around_the_library(tmp_path):
    # a library nested in a folder of its own name (X/lib/lib): --keep X would put the copy on X/lib, around it
    root = tmp_path.resolve()
    lib = _library(root / "lib")
    assert lib == root / "lib" / "lib"
    before = _tree(root)
    rc, err = _check_keep(lib, root)
    assert _tree(root) == before, "--check --keep changed the folder that holds the library"
    assert rc == 2 and "internal error" not in err, (rc, err)


@lite
def test_ds2_46_rebuild_check_keep_never_replaces_the_lock_inputs_or_a_folder_it_did_not_make(tmp_path):
    root = tmp_path.resolve()
    # the project folder T/board holds the BOM and the cache; the librarian named its library after the BOM folder
    # (<workspace>/librarian/board), so --keep T would put the copy on T/board, the lock's own inputs
    proj = root / "board"
    proj.mkdir()
    (proj / "board.kicad_pro").write_text("{}\n", encoding="utf-8")
    lib = _library(proj, root / "ws" / "librarian" / "board")
    before = _tree(proj)
    rc, err = _check_keep(lib, root)
    assert _tree(proj) == before, "--check --keep replaced the folder that holds the lock's BOM and cache"
    assert rc == 2 and "internal error" not in err, (rc, err)
    # nor a copy written into an input: --keep inside the HTTP cache
    rc, err = _check_keep(lib, proj / "cache")
    assert _tree(proj) == before, "--check --keep wrote the rebuilt copy into the lock's HTTP cache"
    assert rc == 2 and "internal error" not in err, (rc, err)
    # an existing DIR/<library name> that `rebuild --check` did not make (another project's library, say)
    other = root / "other" / "board"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("not a rebuild copy\n", encoding="utf-8")
    rc, err = _check_keep(lib, root / "other")
    assert sorted(_tree(other)) == ["notes.txt"], "--check --keep replaced a folder it did not make"
    assert rc == 2 and "internal error" not in err, (rc, err)
    # a copy an earlier --check --keep made is replaced: the same --keep works again
    for _ in range(2):
        rc, err = _check_keep(lib, root / "rb")
        assert rc == 0, (rc, err)
    assert (root / "rb" / "board" / RB.LOCK).is_file()


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
def test_ds2_48_rebuild_check_finds_the_lib_files_of_a_moved_project(tmp_path, monkeypatch):
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
    # an in-place rebuild finds them too, from any working folder (the lock's paths start at the library)
    monkeypatch.chdir(root)
    _, drift = RB.rebuild(root / "clone" / "lib", log=QUIET)
    assert not [d for d in drift if d["part"] == "CH340C" and not d.get("info")], drift
    # and it is the clone's own --lib file that is checked
    sym = root / "clone" / "kicad_lib" / "ecadlib_demo.kicad_sym"
    sym.write_text(sym.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    rep = RB.check(root / "clone" / "lib", log=QUIET)
    assert any(d["part"] == "CH340C" and d["what"] == "input" for d in rep["inputs"]) and not rep["ok"], rep["inputs"]


@lite
def test_ds2_49_part_version_specs_take_drive_letter_library_paths(tmp_path, monkeypatch):
    import os, shutil
    from ecadlib import change_cli as CC
    here = Path(__file__).resolve().parent
    files = {"demo.kicad_sym": here.parent / "examples" / "kicad_lib" / "ecadlib_demo.kicad_sym",
             "hdr.kicad_mod": here / "fixtures" / "kicad_golden" / "PinHeader_1x04_P2.54mm_Vertical.kicad_mod"}
    if os.name == "nt":                 # the real thing: tmp_path is on a drive (C:\...), spelled with \ and with /
        lib = tmp_path.resolve() / "lib"
        lib.mkdir()
        for name, src in files.items():
            shutil.copy(src, lib / name)
        bases = [(str(lib), "\\"), (lib.as_posix(), "/")]
    else:                               # stand-ins from the cwd: a folder named "C:", and files named "C:\lib\..."
        monkeypatch.chdir(tmp_path)
        (tmp_path / "C:" / "lib").mkdir(parents=True)
        for name, src in files.items():
            shutil.copy(src, tmp_path / "C:" / "lib" / name)
            shutil.copy(src, tmp_path / ("C:\\lib\\" + name))
        bases = [("C:\\lib", "\\"), ("C:/lib", "/")]
    for base, sep in bases:
        assert CC.version(f"{base}{sep}hdr.kicad_mod")[1]["part"] == "PinHeader_1x04_P2.54mm_Vertical"   # whole file
        assert CC.version(f"{base}{sep}demo.kicad_sym:CH340C")[1]["part"] == "CH340C"                     # FILE:NAME
