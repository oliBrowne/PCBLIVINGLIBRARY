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


# ---------------------------------------------------------------- #31 names that differ only in case
def _named_items(d, extra_name):
    import copy
    from tests.test_dblib_sync import _base_items, _models
    _models(d)
    items = _base_items()[:2]
    dup = copy.deepcopy(items[1])
    dup["name"] = extra_name
    items.append(dup)
    for k, it in enumerate(items):
        it["parameters"] += [{"name": "Manufacturer Part Number", "value": f"MPN-{k}"}, {"name": "Manufacturer", "value": "ACME"}]
    return items


@lite
def test_release_check_holds_a_second_part_whose_name_differs_only_in_case(tmp_path):
    from ecadlib import release_check as RC
    for other in ("res-0603-10K", "RES-0603-10k"):          # Altium Part Numbers ignore case; dblib-sync refuses it
        items = _named_items(tmp_path / other, other)
        rows = RC.apply(items, tmp_path / other)
        assert [r["status"] for r in rows] == ["ready", "ready", "held"], rows
        assert any("RES-0603-10k" in h and "case" in h for h in items[2]["hold_reasons"]), items[2]["hold_reasons"]
        RC.apply(items, tmp_path / other, allow=[other])            # a name clash cannot be released anyway
        assert items[2]["status"] == "held" and items[0]["status"] == "ready", items
