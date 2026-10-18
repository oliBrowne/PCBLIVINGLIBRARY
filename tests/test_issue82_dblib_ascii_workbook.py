"""#82: Altium installs 'Bibliothèque.DbLib' but can't open any of its tables while the workbook is called
'Bibliothèque.xlsx'. The workbook (and the .csv) take the ASCII name. Run:  python3 -m tests.test_issue82_dblib_ascii_workbook"""
import json

from ecadlib import altium_dblib_sync as S, altium_pkg as PKG
from tests.fixtures_lite import lite
from tests.test_review_1018_ad26_recheck import _item, _models


def _links(ini):
    return {k: v for k, _, v in (ln.partition("=") for ln in ini.splitlines())
            if k in ("ConnectionString", "LibraryDatabasePath", "DatabasePathRelative")}


def test_data_file_name_is_ascii():
    for name, want in (("ecadlib", "ecadlib"), ("Bibliothèque", "Bibliotheque"), ("Müller Parts", "Muller Parts"),
                       ("Straße", "Strasse"), ("Øresund", "Oresund"), ("a/b", "a_b"), ("日本", "ecadlib")):
        got = S.data_file_name(name)
        assert got == want and got.isascii(), (name, got)


@lite
def test_non_ascii_name_writes_an_ascii_workbook(tmp_path):
    _models(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([_item("R1", "Resistors")]))
    out = S.sync(plan, tmp_path / "lib", name="Bibliothèque")
    lib = tmp_path / "lib"
    assert (lib / "Bibliothèque.DbLib").exists(), "the DbLib keeps the name Altium shows"
    assert (lib / "Bibliotheque.xlsx").exists() and (lib / "Bibliotheque.csv").exists()
    assert not (lib / "Bibliothèque.xlsx").exists() and not (lib / "Bibliothèque.csv").exists()
    links = _links((lib / "Bibliothèque.DbLib").read_bytes().decode("utf-8-sig"))
    assert links["LibraryDatabasePath"] == "Bibliotheque.xlsx" and links["DatabasePathRelative"] == "1", links
    assert "Bibliotheque.xlsx" in links["ConnectionString"] and "è" not in links["ConnectionString"], links
    assert set(out["written"]) >= {"Bibliotheque.xlsx", "Bibliotheque.csv", "Bibliothèque.DbLib"}, out["written"]
    assert S.read_tables(lib / "Bibliotheque.xlsx")["Resistors"][1][0]["Part Number"] == "R1"


@lite
def test_old_non_ascii_workbook_moves_and_keeps_hand_edits(tmp_path):
    """A folder an older ecadlib wrote: rows and hand-added columns carry over, the old files go, a re-sync is quiet."""
    _models(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([_item("R1", "Resistors")]))
    lib = tmp_path / "lib"
    S.sync(plan, lib, name="Bibliothèque")
    tabs = S.read_tables(lib / "Bibliotheque.xlsx")
    hdr, rows = tabs["Resistors"]
    rows[0]["Stock Code"] = "SC-1"                                  # a column someone added in Excel
    S.write_xlsx(lib / "Bibliothèque.xlsx", {"Resistors": (hdr + ["Stock Code"], rows)})
    (lib / "Bibliotheque.csv").rename(lib / "Bibliothèque.csv")
    (lib / "Bibliotheque.xlsx").unlink()
    db = lib / "Bibliothèque.DbLib"
    db.write_bytes(db.read_bytes().replace("Bibliotheque.xlsx".encode(), "Bibliothèque.xlsx".encode()))

    dry = S.sync(plan, lib, name="Bibliothèque", dry_run=True)
    assert (lib / "Bibliothèque.xlsx").exists() and not (lib / "Bibliotheque.xlsx").exists(), "a dry run moves nothing"
    assert set(dry["removed"]) == {"Bibliothèque.xlsx", "Bibliothèque.csv"}, dry["removed"]

    out = S.sync(plan, lib, name="Bibliothèque")
    assert not (lib / "Bibliothèque.xlsx").exists() and not (lib / "Bibliothèque.csv").exists()
    row = S.read_tables(lib / "Bibliotheque.xlsx")["Resistors"][1][0]
    assert row["Part Number"] == "R1" and row["Stock Code"] == "SC-1", row
    assert {r["name"]: r["action"] for r in out["results"] if r.get("kind") == "file"} == \
        {"Bibliothèque.xlsx": "removed", "Bibliothèque.csv": "removed"}, out["results"]
    links = _links(db.read_bytes().decode("utf-8-sig"))
    assert links["LibraryDatabasePath"] == "Bibliotheque.xlsx" and "è" not in links["ConnectionString"], links

    again = S.sync(plan, lib, name="Bibliothèque")
    assert again["written"] == [] and again["removed"] == [], again


@lite
def test_package_build_with_non_ascii_name(tmp_path):
    _models(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([_item("R1", "Resistors")]))
    rep = PKG.build(plan, tmp_path / "pkg", "Bibliothèque", log=lambda *a: None)
    assert rep["problems"] == [] and rep["dblibs"] == ["Bibliothèque.DbLib"], rep
    assert (tmp_path / "pkg" / "Bibliotheque.xlsx").exists() and (tmp_path / "pkg" / "Bibliothèque.DbLib").exists()
