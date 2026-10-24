"""Maxim / ADI datasheets that print only the package's outline and land pattern numbers (MAX22201 10-23: page 3
"Package Information / TDFN12 3x3mm / Package Code TD1233+1C / Outline Number 21-0664 / Land Pattern Number
90-0397", no drawing in the PDF). The pages are SYNTHETIC, laid out like that block, not copies."""
import tempfile

from ecadlib import ds_route as DR
from ecadlib import outline_docs as OD
from tests.test_ds_route import pdf_lines, reader

INFO = ["Package Information", "TDFN12 3x3mm", "Package Code TD1233+1C", "Outline Number 21-0664",
        "Land Pattern Number 90-0397", "THERMAL RESISTANCE, SINGLE-LAYER BOARD", "Junction-to-Ambient 63 C/W"]


def max_pages(info=INFO, n=12):
    pages = [[f"MAX22201 brushed DC motor driver page {i + 1}", "Electrical characteristics VM min max"] for i in range(n)]
    pages[2] = ["Absolute Maximum Ratings", *info]
    pages[7] = ["Pin Configuration", "TDFN 3mm x 3mm", "Pin Description", "1 ILIM", "2 ROFF", "13 EP Exposed pad"]
    return [" \n".join(p) for p in pages]


def test_tdfn_and_tqfn_are_package_families():
    assert DR.package_tokens("TDFN12 3x3mm") == {"SON12"}
    assert DR.package_tokens("TDFN-12-1EP_3x3mm") == {"SON12"}
    assert DR.package_tokens("TQFN-20_4x4mm") == {"QFN20"}


def test_the_outline_and_land_pattern_numbers_under_the_package_name():
    assert OD.refs(max_pages(), "MAX22201", "TDFN-12-1EP_3x3mm") == [("21-0664", "maxim", 3), ("90-0397", "maxim", 3)]


def test_each_package_block_owns_its_own_numbers():
    two = ["Package Information", "TQFN20 4x4mm", "Package Code T2044+1", "Outline Number 21-0139",
           "Land Pattern Number 90-0409", *INFO[1:5]]
    assert OD.refs(max_pages(two), "MAX22201", "TDFN-12-1EP_3x3mm") == [("21-0664", "maxim", 3), ("90-0397", "maxim", 3)]
    assert OD.refs(max_pages(two), "MAX22201", "TQFN-20") == [("21-0139", "maxim", 3), ("90-0409", "maxim", 3)]


def test_a_number_not_called_an_outline_or_land_pattern_is_no_document():
    other = ["Package Information", "TDFN12 3x3mm", "Ordering code 21-0664 rev 90-0397"]
    assert OD.refs(max_pages(other), "MAX22201", "TDFN-12-1EP_3x3mm") == []


def test_a_maxim_sheet_with_its_own_drawing_needs_no_document():
    pages = max_pages()
    pages[10] = "Package outline TDFN 12 \nDIM MIN NOM MAX mm A 0.70 0.80 A1 0.00 0.05 D2 2.30 2.40 2.50 E2 1.60 1.70 1.80"
    assert OD.refs(pages, "MAX22201", "TDFN-12-1EP_3x3mm") == []


def test_maxim_links_and_the_folder_hint():
    assert OD.links("21-0664", "maxim") == ["https://pdfserv.maximintegrated.com/package_dwgs/21-0664.PDF"]
    assert OD.links("90-0397", "maxim") == ["https://pdfserv.maximintegrated.com/land_patterns/90-0397.PDF"]
    data = pdf_lines([p.split(" \n") for p in max_pages()])
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/max22201.pdf": data})            # Maxim's links are refused here
        j = rd.job("MAX22201", [("https://a/max22201.pdf", "BOM")], "TDFN-12-1EP_3x3mm")
        missing = j["prov"]["outline_missing"]
        assert missing[0].startswith("21-0664 (named on page 3") and missing[1].startswith("90-0397"), missing
