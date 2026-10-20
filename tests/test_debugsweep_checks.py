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


@lite
def test_ds2_61_kicad_database_in_a_folder_with_hash_sign(tmp_path):
    """'Board #2/lib.sqlite' cut the SQLite URI at '#': parity saw no rows and created an empty 'Board ' file; the eval
    golden reader opened the same unquoted URI."""
    import os, sqlite3
    from ecadlib import parity as PY
    from ecadlib.eval import golden as G
    base = tmp_path.resolve()
    d = base / ("Board #2" if os.name == "nt" else "Board #2?")      # Windows forbids '?' in names
    d.mkdir()
    (d / "lib.kicad_dbl").write_text("{}")
    con = sqlite3.connect(d / "lib.sqlite")
    con.execute('create table T ("Part ID" text, MPN text)')
    con.execute("insert into T values ('P1', 'M1')")
    con.commit()
    con.close()
    rows = PY.kicad_db_rows(d, "lib")
    assert sorted(rows) == ["P1"] and rows["P1"]["MPN"] == "M1", rows
    assert 'row T: ["P1", "M1"]' in G.table_rows(d / "lib.sqlite")
    assert sorted(p.name for p in base.iterdir()) == [d.name], "a stray file was created next to the folder"


@lite
def test_ds2_62_check_compares_every_instance_of_a_reused_sheet(tmp_path):
    """sub.kicad_sch is used twice (R10 and R20 are one symbol with two instance paths). R20 sits on the board with
    another footprint than the library part's: check must name R20, not only R10."""
    from ecadlib import sexpr
    d = tmp_path.resolve() / "proj"
    shutil.copytree(FIX_SCH, d)
    cached = next(s for s in sexpr.find(sexpr.first(sexpr.parse((d / "board.kicad_sch").read_text(), keep_quotes=True),
                                                    "lib_symbols"), "symbol") if str(s[1]) == "Device:R")
    cached[1] = sexpr.Q("R")
    for p in sexpr.find(cached, "property"):
        if str(p[1]) == "Footprint":
            p[2] = sexpr.Q("Resistor_SMD:R_0402_1005Metric")
    (d / "lib").mkdir()
    (d / "lib" / "Device.kicad_sym").write_text(f'(kicad_symbol_lib (version 20241209) (generator "test") {sexpr.dumps(cached)})\n')
    fp = lambda fpid, ref, x: (f'(footprint "{fpid}" (layer "F.Cu") (at {x} 10) (property "Reference" "{ref}" (at 0 -1 0) '
                               f'(layer "F.SilkS")) (pad "1" smd rect (at -0.5 0) (size 0.5 0.5) (layers "F.Cu")) '
                               f'(pad "2" smd rect (at 0.5 0) (size 0.5 0.5) (layers "F.Cu")))')
    (d / "board.kicad_pcb").write_text('(kicad_pcb (version 20241229) (generator "test") '
                                       + fp("Resistor_SMD:R_0402_1005Metric", "R10", 10)
                                       + fp("Resistor_SMD:R_0805_2012Metric", "R20", 20) + ')\n')
    res = PJ.check_kicad(d / "board.kicad_pro", libs=[d / "lib"], use_global=False)
    row = next(r for r in res["rows"] if r["kind"] == "symbol" and r["id"] == "Device:R")
    assert "R20" in row["refs"], row["refs"]
    board = [c["text"] for c in row["changes"] if "(the board)" in c["text"]]
    assert any(t.startswith("R20 places") for t in board), board
