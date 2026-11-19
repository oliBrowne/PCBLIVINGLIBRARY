"""A free check (no model) uses the part's own PDF that a person or their assistant saved (2026-11-19, re-measuring the
1000-part run's holds with 124 datasheets downloaded by hand): the reader read <MPN>.pdf from the [datasheet] folder
and from datasheet/assistant/pdfs (the folder its own request names), but the free pin-table / diode / case checks
only tried the part's links, so a part whose maker site refuses scripts (Nexperia, analog.com) stayed held for a PDF
that was already on disk. Plain functions: python3 -m tests.run_all test_free_check_own_pdf_1119"""
from ecadlib import librarian as L
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_datasheet_reading_1019 import _placed_pdf


class _Store:
    def __init__(self):
        self.asked = []

    def fetch(self, url, where, **kw):
        self.asked.append(url)
        raise RuntimeError("offline and not downloaded before")


ITEM = {"parameters": [{"name": "Manufacturer Part Number", "value": "BAS16,215"}]}
ROW = {"src": "digikey", "src_datasheet": "https://assets.nexperia.com/documents/data-sheet/BAS16.pdf"}


def _run(out, rules=None):
    run = L.Run.__new__(L.Run)
    run._pdfs, run.http, run.reader, run.rules, run.out = _Store(), None, None, rules, out
    return run


@lite
def test_the_assistants_saved_pdf_is_used_when_no_link_gives_one(tmp_path):
    pdfs = tmp_path / "datasheet" / "assistant" / "pdfs"
    pdfs.mkdir(parents=True)
    pdf = _placed_pdf([(72, 700, "BAS16 High-speed switching diode")])
    (pdfs / "BAS16,215.pdf").write_bytes(pdf)
    run = _run(tmp_path, {"datasheet": {"reader": "assistant"}})
    data, where = run.datasheet_pdf(ITEM, {"mpn": "BAS16,215"}, ROW, {"found": {}}, "BAS16,215")
    assert data == pdf, (where, run._pdfs.asked)
    assert "BAS16,215.pdf" in where and str(tmp_path) not in where      # the file's name, never a path


@lite
def test_the_datasheet_folder_file_is_used_too(tmp_path):
    folder = tmp_path / "sheets"
    folder.mkdir()
    pdf = _placed_pdf([(72, 700, "BAS16 High-speed switching diode")])
    (folder / "BAS16,215.pdf").write_bytes(pdf)
    run = _run(tmp_path / "out", {"datasheet": {"folder": str(folder)}})
    data, where = run.datasheet_pdf(ITEM, {"mpn": "BAS16,215"}, ROW, {"found": {}}, "BAS16,215")
    assert data == pdf and where == "BAS16,215.pdf in the datasheet folder", where


@lite
def test_a_saved_file_that_does_not_name_the_part_or_is_not_a_pdf_is_not_used(tmp_path):
    pdfs = tmp_path / "datasheet" / "assistant" / "pdfs"
    pdfs.mkdir(parents=True)
    other = [(72, 700 - 12 * i, "BAV99 double switching diode in a SOT23 small plastic package, high switching speed")
             for i in range(12)]                     # (a page of text: a few words can't tell, mentions_part None)
    (pdfs / "BAS16,215.pdf").write_bytes(_placed_pdf(other))
    run = _run(tmp_path, {"datasheet": {"reader": "assistant"}})
    assert run.datasheet_pdf(ITEM, {"mpn": "BAS16,215"}, ROW, {"found": {}}, "BAS16,215") == (None, None)
    (pdfs / "BAS16,215.pdf").write_bytes(b"<html>Access denied</html>")
    assert run.datasheet_pdf(ITEM, {"mpn": "BAS16,215"}, ROW, {"found": {}}, "BAS16,215") == (None, None)


@lite
def test_another_parts_file_that_starts_with_the_mpn_is_not_this_parts(tmp_path):
    """BAT54-04.pdf (a series pair) for BAT54 (one diode): local_datasheet calls it "exact" (MPN, separator, more) and its
    text names BAT54 too, so a free check would read another pinout. Only <MPN>.pdf itself is the part's own file here."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    page = [(72, 700 - 12 * i, "BAT54-04 Schottky barrier double diode, series pair, BAT54 family, SOT23 package")
            for i in range(12)]
    (folder / "BAT54-04.pdf").write_bytes(_placed_pdf(page))
    run = _run(tmp_path / "out", {"datasheet": {"folder": str(folder)}})
    assert run.own_pdf("BAT54") == (None, None)
    (folder / "BAT54.pdf").write_bytes(_placed_pdf([(72, 700, "BAT54 Schottky barrier diode")]))
    assert run.own_pdf("BAT54")[1] == "BAT54.pdf in the datasheet folder"


@lite
def test_the_saved_file_comes_before_a_link_that_works(tmp_path):
    """As the reader does (Reader.job: the part's own file beats every link): the person saved it for this part."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    pdf = _placed_pdf([(72, 700, "BAS16 High-speed switching diode")])
    (folder / "BAS16,215.pdf").write_bytes(pdf)
    run = _run(tmp_path / "out", {"datasheet": {"folder": str(folder)}})
    run._pdfs.fetch = lambda url, where, **kw: (b"%PDF-1.4 link", {})
    assert run.datasheet_pdf(ITEM, {"mpn": "BAS16,215"}, ROW, {"found": {}}, "BAS16,215") == (
        pdf, "BAS16,215.pdf in the datasheet folder")


@lite
def test_the_assistants_folder_is_not_read_outside_assistant_mode(tmp_path):
    pdfs = tmp_path / "datasheet" / "assistant" / "pdfs"
    pdfs.mkdir(parents=True)
    (pdfs / "BAS16,215.pdf").write_bytes(_placed_pdf([(72, 700, "BAS16 High-speed switching diode")]))
    run = _run(tmp_path, None)
    assert run.datasheet_pdf(ITEM, {"mpn": "BAS16,215"}, ROW, {"found": {}}, "BAS16,215") == (None, None)


def test_a_cut_off_pdf_saved_as_the_part_s_file_is_said_and_never_read():
    """11-19: 15 of 124 hand-downloaded datasheets had stopped early (no end-of-file marker, no page tree). The folder
    took them as PDFs and sent a read with no pages (NX3225GA, SL1002A090SM); a link's PDF that is cut off is refused
    (PdfStore._fetch). Now the folder file is refused the same way, and the person is told to download it again."""
    import tempfile
    from ecadlib import datasheet as D, messages as M
    from tests import test_crosscheck as TC
    from tests.test_datasheet_folder import folder, refused, refused_run, LINK
    whole = TC.pdf3()
    assert D.pdf_complete(whole) and not D.pdf_complete(whole[:len(whole) // 2])
    with tempfile.TemporaryDirectory() as d:
        f = folder(d)
        (f / "DSPART1.pdf").write_bytes(whole[:len(whole) // 2])
        it, _ = refused_run(d, {"datasheet": {"folder": str(f)}})
        bad = "DSPART1.pdf in your [datasheet] folder is cut off (the download stopped early): replace it with the whole PDF"
        tried = [t for t in it["librarian"]["tried"] if t.startswith("datasheet")]
        assert tried[0] == f"datasheet: {bad}", tried
        line, e = refused(it)
        assert e["code"] == "files.datasheet_not_pdf", e
        assert e["text"].startswith("DSPART1.pdf in your datasheet folder is cut off (the download stopped early), and "
                                    "example.com would not let ecadlib download"), e["text"]
        assert e["fix"].startswith(f"Replace DSPART1.pdf with the PDF: download it in a browser ({LINK})"), e["fix"]
        run = _run(Path(d) / "out", {"datasheet": {"folder": str(f)}})     # nor a free check
        assert run.own_pdf("DSPART1") == (None, None)


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
