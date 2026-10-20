"""Debug-sweep regressions (BOM reading, group bomread). Plain functions: python -m tests.run_all test_debugsweep_bomread"""
import contextlib, io

from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


@lite
def test_ds2_03_trailing_unnamed_notes_column_keeps_mpn(tmp_path):
    # Excel writes every row to the sheet's width: a notes column with no header gives 'Footprint,' in the header
    f = tmp_path / "notes_col.csv"
    f.write_bytes(b"Designator,Qty,Value,MPN,Footprint,\r\nR1,1,10k,RC0603FR-0710KL,0603,check stock\r\n"
                  b"C1,1,100n,GRM188R71H104KA93D,0603,\r\nU1,1,LDO,AMS1117-3.3,SOT-223,alt: LM1117\r\n"
                  b"R2,1,4,7k,RC0603FR-074K7L,0603,\r\n")
    lines, _ = T.read_bom(f)
    got = [(l["designator"], l["value"], l.get("mpn"), l.get("footprint"), l.get("bom_note")) for l in lines]
    assert got[:3] == [("R1", "10k", "RC0603FR-0710KL", "0603", None),
                       ("C1", "100n", "GRM188R71H104KA93D", "0603", None),
                       ("U1", "LDO", "AMS1117-3.3", "SOT-223", None)], got
    # a row one cell wider than the header row is still an unquoted decimal comma
    assert got[3][:4] == ("R2", "4,7k", "RC0603FR-074K7L", "0603") and "decimal comma" in got[3][4], got


@lite
def test_ds2_04_hidden_column_in_a_sparse_workbook_is_the_one_left_out(tmp_path):
    import openpyxl
    from openpyxl.styles import PatternFill

    def book(name, first_col, rows, hide):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "BOM"
        for r, row in enumerate(rows, 1):
            for c, v in enumerate(row, first_col):
                if v:
                    ws.cell(r, c, v)
        ws.column_dimensions[hide].hidden = True
        ws["Z100000"].fill = PatternFill("solid", fgColor="FFFF00")   # far, empty, styled: the sheet is read sparsely
        wb.save(tmp_path / name)
        return tmp_path / name

    b = book("from_b.xlsx", 2, [["Designator", "Value", "MPN (old)", "MPN"], ["R1", "10k", "OLD-OBSOLETE-1", "RC0603FR-0710KL"],
                                ["C1", "100n", "OLD-OBSOLETE-2", "GRM188R71H104KA93D"]], "D")
    a = book("spacer.xlsx", 1, [["Designator", "Value", "", "MPN"], ["R1", "10k", "", "RC0603FR-0710KL"],
                                ["C1", "100n", "", "GRM188R71H104KA93D"]], "C")    # an empty hidden spacer column
    for f in (b, a):
        lines, mapping = T.read_bom(f)
        assert mapping["mpn"] == "MPN" and [l.get("mpn") for l in lines] == ["RC0603FR-0710KL", "GRM188R71H104KA93D"], \
            (f.name, mapping, lines)


@lite
def test_ds2_05_triage_reports_of_dotted_bom_names_keep_the_whole_name(tmp_path, monkeypatch):
    from ecadlib.app import Workspace
    (tmp_path / "ecadlib.toml").write_text('[setup]\nworkspace = "ws"\n', encoding="utf-8")
    for rev, val in (("1.1", "10k"), ("1.2", "4k7")):
        (tmp_path / f"PowerBoard_rev{rev}_BOM.csv").write_text(f"Designator,Qty,Value,MPN\nR1,1,{val},RC0603FR-0710KL\n",
                                                               encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        for rev in ("1.1", "1.2"):                                       # CLI, default -o: <workspace>/reports/triage_<BOM>
            T.main([f"PowerBoard_rev{rev}_BOM.csv", "--providers", "", "--offline"])
        reports = tmp_path / "ws" / "reports"
        got = sorted(p.name for p in reports.iterdir())
        assert got == ["triage_PowerBoard_rev1.1_BOM.csv", "triage_PowerBoard_rev1.1_BOM.md",
                       "triage_PowerBoard_rev1.2_BOM.csv", "triage_PowerBoard_rev1.2_BOM.md"], got
        assert "rev1.1" in (reports / "triage_PowerBoard_rev1.1_BOM.md").read_text(encoding="utf-8").splitlines()[0]

        w = Workspace(tmp_path / "web", providers=[], offline=True)      # web / MCP: an upload keyed by its file name
        up = w.save_upload("PowerBoard_rev1.2_BOM.csv", b"Designator,Qty,Value,MPN\nR1,1,4k7,RC0603FR-074K7L\n")
        r = w.triage(up)
        assert r["reports"] == ["reports/triage_PowerBoard_rev1.2_BOM@csv.md",
                                "reports/triage_PowerBoard_rev1.2_BOM@csv.csv"], r["reports"]
        assert w.triage_result(r["id"])["name"] == "PowerBoard_rev1.2_BOM.csv"
        assert w.build_altium_models(up)["skipped"] == []              # reads the triage it just wrote
