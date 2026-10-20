"""Debug sweep 2, group "checks": regression tests for project check / freeze / scan, parity, house style and messages.
Run:  python -m tests.run_all test_debugsweep_checks"""
import shutil
from pathlib import Path

from ecadlib import project_check as PJ
from tests.fixtures_lite import lite
from tests.test_project_check import make_project


@lite
def test_ds2_57_freeze_dotted_project_name_leaves_the_sibling_project_alone(tmp_path):
    """PSU_v1.1.kicad_pro next to an older PSU_v1 project: freeze must rewrite PSU_v1.1.kicad_sch, not PSU_v1's."""
    d = tmp_path.resolve()
    make_project(d)
    for ext in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
        shutil.copy(d / f"board{ext}", d / f"PSU_v1.1{ext}")
        shutil.copy(d / f"board{ext}", d / f"PSU_v1{ext}")
    sibling = {f: (d / f).read_bytes() for f in ("PSU_v1.kicad_sch", "PSU_v1.kicad_pcb")}
    PJ.freeze_kicad(d / "PSU_v1.1.kicad_pro")
    assert {f: (d / f).read_bytes() for f in sibling} == sibling, "freeze rewrote the PSU_v1 project"
    assert '"PSU_v1.1-frozen:QN"' in (d / "PSU_v1.1.kicad_sch").read_text()
    assert '"PSU_v1.1-frozen:SOT23"' in (d / "PSU_v1.1.kicad_pcb").read_text()


FIX_SCH = Path(__file__).parent / "fixtures" / "kicad_sch"


@lite
def test_ds2_58_freeze_follows_sub_sheets_whose_file_name_case_differs(tmp_path):
    """A project drawn on Windows / macOS: the Sheetfile says sub.kicad_sch, the file is Sub.kicad_sch. KiCad opens it,
    so freeze must point that sheet (and sub2/Leaf.kicad_sch below it) at the frozen library too."""
    d = tmp_path.resolve() / "caseproj"
    shutil.copytree(FIX_SCH, d)
    (d / "sub.kicad_sch").rename(d / "Sub.kicad_sch")
    (d / "sub2" / "leaf.kicad_sch").rename(d / "sub2" / "Leaf.kicad_sch")
    PJ.freeze_kicad(d / "board.kicad_pro")
    for sheet in ("Sub.kicad_sch", "sub2/Leaf.kicad_sch"):
        text = (d / sheet).read_text()
        assert '(lib_id "Device:' not in text and '(lib_id "board-frozen:' in text, f"{sheet} still uses the live library"


@lite
def test_ds2_59_freeze_backup_keeps_both_sheets_that_share_a_file_name(tmp_path):
    """sub.kicad_sch and sub2/sub.kicad_sch are both rewritten by freeze: the backup must hold both originals."""
    d = tmp_path.resolve() / "bkproj"
    shutil.copytree(FIX_SCH, d)
    (d / "sub2" / "leaf.kicad_sch").rename(d / "sub2" / "sub.kicad_sch")
    root = d / "board.kicad_sch"
    root.write_text(root.read_text().replace('"sub2/leaf.kicad_sch"', '"sub2/sub.kicad_sch"'))
    originals = {f: (d / f).read_bytes() for f in ("sub.kicad_sch", "sub2/sub.kicad_sch")}
    assert originals["sub.kicad_sch"] != originals["sub2/sub.kicad_sch"]
    res = PJ.freeze_kicad(d / "board.kicad_pro")
    assert all((d / f).read_bytes() != b for f, b in originals.items()), "freeze did not rewrite both sheets"
    kept = [p.read_bytes() for p in Path(res["backup"]).rglob("*") if p.is_file()]
    for f, b in originals.items():
        assert b in kept, f"the backup lost the original {f}"


@lite
def test_ds2_60_parity_with_a_library_name_kicad_export_normalises(tmp_path):
    """--name "My Lib": kicad-export writes My_Lib.kicad_sym / .pretty / .sqlite; parity given the same --name must
    read those files, so the identical libraries match and the database rows are compared."""
    from ecadlib import testpack as TP
    s = TP.build(tmp_path.resolve() / "tp", name="My Lib", log=lambda *a, **k: None)
    assert (tmp_path / "tp" / "kicad" / "My_Lib.kicad_sym").is_file()
    assert s["parity"]["match"] == 6 and s["parity"]["mismatch"] == 0, s["parity"]
    assert s["parity"]["database_rows"] == {"kicad": 6, "altium": 6}, s["parity"]
