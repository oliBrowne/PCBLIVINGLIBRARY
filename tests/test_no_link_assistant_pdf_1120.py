"""A part with no datasheet link at all is read from the PDF saved for it in datasheet/assistant/pdfs (2026-11-20,
re-measuring the 12 re-downloaded datasheets: Molex 22-23-2021 has no link in the BOM or from DigiKey/Mouser, and its
whole PDF saved as datasheet/assistant/pdfs/22-23-2021.pdf was never read: "no datasheet URL known"). The reader takes
that folder for a part with links (Reader.job), and the free checks read it for any part (Run.own_pdf); the librarian
stopped before the reader when there was no link, unless the [datasheet] folder had the file.
Plain functions: python3 -m tests.run_all test_no_link_assistant_pdf_1120"""
import json
import tempfile
from pathlib import Path

from ecadlib import ds_assistant as DA
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_assistant as TA

ASSIST = {"datasheet": {"reader": "assistant"}}
NO_LINK = TB.BOM2.replace("Datasheet-only part,https://example.com/ds.pdf", "Datasheet-only part,")


def _run(d, rules=ASSIST):
    """TB.run2 with DSPART1's link taken out of the BOM -> (DSPART1's plan item, the reader)."""
    import ecadlib.datasheet as D
    from tests import test_librarian as TL
    assert NO_LINK != TB.BOM2
    http = TB.PdfHTTP({"https://www.lcsc.com/datasheet/C9999.pdf": TB.PDF})
    rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=http, batch=False, poll=0,
                  sleep=lambda s: None, log=lambda *a: None, rules=rules)
    with TB.patched(TA._api()):
        items, _ = TL.run(d, http=http, bom=NO_LINK, reader=rd, rules=rules)
    return items["DSPART1"], rd


def _named():
    from tests.test_datasheet_reading_1019 import _placed_pdf
    return _placed_pdf([(72, 700, "DSPART1 buck converter"), (72, 680, "Pin Functions")])


def test_no_link_and_no_saved_pdf_says_so_as_before():
    with tempfile.TemporaryDirectory() as d:
        it, _ = _run(d)
        assert "datasheet: no datasheet URL known" in it["librarian"]["tried"], it["librarian"]["tried"]


def test_no_link_but_a_pdf_saved_for_the_assistant_is_asked_of_it_then_built():
    with tempfile.TemporaryDirectory() as d:
        pdfs = DA.pdf_folder(DA.root(Path(d) / "out"))
        pdfs.mkdir(parents=True)
        (pdfs / "DSPART1.pdf").write_bytes(_named())
        it, rd = _run(d)
        tried = " ".join(it["librarian"]["tried"])
        assert "no datasheet URL known" not in tried and "waiting for your assistant" in tried, tried
        (f,) = [p for p in rd.assistant.iterdir() if p.is_dir() and p.name.startswith("DSPART1")]
        (f / "answer.json").write_text(json.dumps(TD.GOOD), encoding="utf-8")
        it, _ = _run(d)
        tried = it["librarian"]["tried"]
        assert any(t.startswith("datasheet reader (") and "saved by your assistant" in t for t in tried), tried
        assert str(it["source"]["symbol"] or "").startswith("datasheet:") and it["source"]["footprint"], it["source"]
        assert not [t for t in tried if str(d) in t], tried      # a file's name, never a path on this machine


def test_another_parts_file_in_the_assistants_folder_is_not_this_parts():
    with tempfile.TemporaryDirectory() as d:
        pdfs = DA.pdf_folder(DA.root(Path(d) / "out"))
        pdfs.mkdir(parents=True)
        (pdfs / "DSPART12.pdf").write_bytes(TC.pdf3())
        it, _ = _run(d)
        assert "datasheet: no datasheet URL known" in it["librarian"]["tried"], it["librarian"]["tried"]


def test_a_cut_off_or_wrong_file_saved_for_it_is_said_and_never_read():
    """As the free checks (Run.own_pdf) and the [datasheet] folder do: a cut-off download, a web page saved as .pdf, or
    another part's datasheet saved under this part's name is said, with what to do, and the part waits as before."""
    from tests.test_datasheet_reading_1019 import _placed_pdf
    whole = TC.pdf3()
    other = _placed_pdf([(72, 700 - 12 * i, "BAV99 double switching diode in a SOT23 small plastic package, high speed")
                         for i in range(12)])
    for data, said in ((whole[:len(whole) // 2], "is cut off (the download stopped early): replace it with the whole PDF"),
                       (b"<html>Access denied</html>", "is not a PDF (a web page saved as .pdf?): replace it with the PDF"),
                       (other, "does not name DSPART1 or is not a datasheet: replace it with the part's own datasheet")):
        with tempfile.TemporaryDirectory() as d:
            pdfs = DA.pdf_folder(DA.root(Path(d) / "out"))
            pdfs.mkdir(parents=True)
            (pdfs / "DSPART1.pdf").write_bytes(data)
            it, rd = _run(d)
            tried = it["librarian"]["tried"]
            assert f"datasheet: DSPART1.pdf in your assistant's pdfs folder {said}" in tried, tried
            assert "datasheet: no datasheet URL known" in tried, tried
            assert not [p for p in rd.assistant.iterdir() if p.is_dir() and p.name.startswith("DSPART1")]
            assert not [t for t in tried if str(d) in t], tried


def test_the_assistants_folder_is_not_read_outside_assistant_mode():
    with tempfile.TemporaryDirectory() as d:
        pdfs = DA.pdf_folder(DA.root(Path(d) / "out"))
        pdfs.mkdir(parents=True)
        (pdfs / "DSPART1.pdf").write_bytes(_named())
        it, _ = _run(d, rules={})
        assert "datasheet: no datasheet URL known" in it["librarian"]["tried"], it["librarian"]["tried"]


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
