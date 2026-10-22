"""Package drawings kept in a separate document (ecadlib.outline_docs, 2026-10-22): a family datasheet that prints
only the drawing's document number gets that document appended. The PDFs are SYNTHETIC, laid out like an NXP
Kinetis sheet (a list of every package's document number, no drawing), not copies."""
import tempfile
from pathlib import Path

from ecadlib import outline_docs as OD
from tests.test_ds_route import pdf_lines, reader, n_pages


def kl_pages(n=30):
    pages = [[f"KL24 sub-family data sheet page {i + 1}", "Electrical characteristics VDD min max"] for i in range(n)]
    pages[1] = ["Packages", "32-pin QFN 98ASA00473D", "64-pin LQFP 98ASS23234W", "80-pin LQFP 98ASS23174W"]
    pages[20] = ["Dimensions", "Obtaining package dimensions",
                 "To find a package drawing, search for the drawing's document number:",
                 "If you want the drawing for this package Then use this document number",
                 "32-pin QFN 98ASA00473D", "64-pin LQFP 98ASS23234W", "80-pin LQFP 98ASS23174W"]
    pages[22] = ["Pinout", "80 LQFP 64 LQFP 32 QFN Pin Name", "1 1 - PTE0", "2 2 - PTE1"]
    pages[26] = ["Part identification", "PP Package identifier FM = 32 QFN (5 mm x 5 mm) LH = 64 LQFP (10 mm x 10 mm)",
                 "LK = 80 LQFP (12 mm x 12 mm)", "Example MKL24Z32VFM4 MKL24Z64VLH4"]
    return pages


OUTLINE80 = pdf_lines([["98ASS23174W LQFP 80 12 X 12 X 1.4 PKG, 0.5 PITCH", "CASE OUTLINE"],
                       ["DIM MIN NOM MAX mm", "A1 0.05 0.15 D1 12.00 BSC E1 12.00 BSC e 0.50 BSC"]])


def test_the_document_number_of_this_package_only():
    texts = [" \n".join(p) for p in kl_pages()]
    assert OD.refs(texts, "MKL24Z32VLK4", "LQFP-80") == [("98ASS23174W", "nxp", 2)]
    assert OD.refs(texts, "MKL24Z32VLK4", None) == [("98ASS23174W", "nxp", 2)]      # the ordering key names it
    assert OD.refs(texts, "MKL24Z32VFM4", "QFN-32")[0][0] == "98ASA00473D"
    assert OD.links("98ASS23174W", "nxp") == ["https://www.nxp.com/docs/en/package-information/98ASS23174W.pdf"]


def test_a_datasheet_with_its_own_drawing_needs_no_document():
    pages = kl_pages()
    pages[21] = ["Package outline 80-pin LQFP", "DIM MIN NOM MAX mm A1 0.05 0.15 D1 12.00 E1 12.00"]
    assert OD.refs([" \n".join(p) for p in pages], "MKL24Z32VLK4", "LQFP-80") == []
    assert OD.refs(["Packages 80-pin LQFP 98ASS23174W"], "X", None) == []           # no package known: nothing


def test_the_reader_appends_the_outline_document():
    data = pdf_lines(kl_pages())
    url = "https://www.nxp.com/docs/en/package-information/98ASS23174W.pdf"
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/kl24.pdf": data, url: OUTLINE80})
        j = rd.job("MKL24Z32VLK4", [("https://a/kl24.pdf", "BOM")], "LQFP-80_12x12mm_P0.5mm")
        assert j["prov"]["outline"] == [{"code": "98ASS23174W", "from": url, "first_page": 31}], j["prov"]
        assert isinstance(j["offset"], list) and {31, 32} & set(j["offset"]), j["offset"]
        assert n_pages(j["body"]) == len(j["offset"])


def test_a_document_that_cannot_be_got_is_named_for_the_review():
    data = pdf_lines(kl_pages())
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/kl24.pdf": data})                 # the NXP link is refused here
        j = rd.job("MKL24Z32VLK4", [("https://a/kl24.pdf", "BOM")], "LQFP-80_12x12mm_P0.5mm")
        assert "outline" not in j["prov"] and "98ASS23174W (named on page 2" in j["prov"]["outline_missing"][0]


def test_the_folder_copy_of_the_document_comes_first():
    data = pdf_lines(kl_pages())
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "pdfs"
        folder.mkdir()
        (folder / "98ASS23174W.pdf").write_bytes(OUTLINE80)
        rd = reader(d, {"https://a/kl24.pdf": data}, rules={"datasheet": {"folder": str(folder)}})
        j = rd.job("MKL24Z32VLK4", [("https://a/kl24.pdf", "BOM")], "LQFP-80_12x12mm_P0.5mm")
        assert j["prov"]["outline"][0]["from"] == "98ASS23174W.pdf in your [datasheet] folder", j["prov"]


def test_the_review_names_the_document():
    from tests import test_datasheet_batch as TB
    from tests.test_ds_route import ModelAPI, GOOD
    data = pdf_lines(kl_pages())
    url = "https://www.nxp.com/docs/en/package-information/98ASS23174W.pdf"
    with tempfile.TemporaryDirectory() as d, TB.patched(ModelAPI(lambda m, n: GOOD)):
        rd = reader(d, {"https://a/kl24.pdf": data, url: OUTLINE80})
        res = rd.read_now(rd.job("MKL24Z32VLK4", [("https://a/kl24.pdf", "BOM")], "LQFP-80_12x12mm_P0.5mm"))
        assert any("the package drawing came from 98ASS23174W" in r for r in res["needs_review"]), res["needs_review"]
