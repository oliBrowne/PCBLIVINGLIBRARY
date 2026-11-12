"""Compartmental testing round 2, group 3 (2026-11-12): what happens after a part is built - change control,
approvals, the ledger, library writing (librarian, KiCad import) and the Altium files. Each component is called
directly with small hand-made inputs."""
import json
from pathlib import Path

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- #30 parity reads the workbook the DbLib names
@lite
def test_parity_reads_the_workbook_altium_reads_not_the_csv_mirror(tmp_path):
    import openpyxl
    from ecadlib import altium_dblib_sync as S, parity as P
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    lib = tmp_path / "lib"
    S.sync(_plan(tmp_path, _base_items()), lib)
    wb = openpyxl.load_workbook(lib / "ecadlib.xlsx")
    ws = wb["Resistors"]
    h = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[h.index("Part Number")].value == "RES-0603-10k":
            row[h.index("Value")].value = "4.7k"                         # edited in the workbook Altium reads
    wb.save(lib / "ecadlib.xlsx")
    assert "10k" in (lib / "ecadlib.csv").read_text(encoding="utf-8-sig")  # the mirror is stale
    assert P.altium_db_rows(lib)["RES-0603-10k"]["Value"] == "4.7k"
