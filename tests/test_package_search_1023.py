"""A datasheet with no drawing of the part's package: the package document found by web search is appended (live
benchmark 2026-10-23: 12 of the 17 blank-dimension parts had a PDF that does not print the dimension: Neutrik
product sheets with photos only, Altera / Lattice / Microchip family sheets whose BGA drawings are in a separate
package document, an onsemi sheet that dropped the DFN drawing). The PDFs are SYNTHETIC, not copies. The API is
faked; nothing is sent anywhere."""
import json
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_search as WS, outline_docs as OD
from tests.test_datasheet_batch import KEY, PdfHTTP
from tests.test_ds_route import pdf_lines, n_pages
from tests.test_ds_search import Client, _answer

DS = "https://maker.example/ds/nc3x.pdf"
DWG = "https://maker.example/drawings/NC3XYZ-1_drawing.pdf"
OTHER = "https://maker.example/drawings/press.pdf"


def product_sheet():
    # a product sheet: photos and spec tables, no drawing (Neutrik's product-page PDFs)
    return [["NC3XYZ-1 XLR receptacle, 3 pole female, vertical PCB mount",
             "Electrical: contact resistance 3 mOhm, insulation 2 GOhm, dielectric strength 1.5 kVdc",
             "Mechanical: lifetime 1000 cycles, insertion force 30 N, withdrawal force 20 N",
             "Material: housing PA 66 grey, contacts silver plated brass"],
            ["NC3XYZ-1 applications and related products: front panel accessories, coding rings, labels",
             "Ordering: NC3XYZ-1 standard, NC3XYZ-1-B black",
             "Standards: IEC 61076-2-103, UL 94 V-0, temperature range -30 to 80 C"]]


def drawing_sheet(name="NC3XYZ-1"):
    return [[f"{name} drawing", "PCB layout, top view, tolerance +-0.1",
             "DIM MIN NOM MAX mm A1 1.20 1.25 1.30 D1 26.0 E1 31.0 hole 1.3 pitch 5.08"]]


def _reader(d, cl, files, **ds):
    return D.Reader(Path(d) / "out", Path(d) / "cache", key=KEY, log=None, client=cl, http=PdfHTTP(files),
                    rules={"datasheet": {"cross_check": "off", "web_search_model": "claude-haiku-5-5", **ds}})


def _texts(pages):
    return [" \n".join(p) for p in pages]


def test_a_product_sheet_has_no_drawing():
    assert OD.drawing_missing(_texts(product_sheet()), "NC3XYZ-1", None)


def test_a_sheet_with_a_dimension_table_or_an_image_page_has_one():
    assert not OD.drawing_missing(_texts(product_sheet() + drawing_sheet()), "NC3XYZ-1", None)
    # an image-only page past the first: it may be the drawing (CY14U256LA-BA's and IR4301's are), so no search
    assert not OD.drawing_missing(_texts(product_sheet() + [[""]]), "NC3XYZ-1", None)
    assert not OD.drawing_missing([], "X", None) and not OD.drawing_missing(["", ""], "X", None)   # no text layer


def test_a_drawing_of_another_package_only_is_not_this_parts():
    # MC34063A/D Rev 27: PDIP-8 and SOIC-8 drawings, while the part is the DFN-8 (the revision dropped its drawing)
    pages = [["MC34063A 1.5 A step-up/down/inverting switching regulator", "Ordering information MC34063ADR2G SOIC-8"],
             ["PACKAGE DIMENSIONS PDIP-8 CASE 626", "DIM MIN MAX mm A1 0.38 D1 9.40 E1 6.10"],
             ["PACKAGE DIMENSIONS SOIC-8 CASE 751", "DIM MIN MAX mm A1 0.10 D1 4.80 E1 3.80"]]
    assert OD.drawing_missing(_texts(pages), "MC33063MNTXG", "DFN-8-1EP")
    assert not OD.drawing_missing(_texts(pages), "MC34063ADR2G", "SOIC-8")


def test_the_package_prompt_asks_for_the_drawing_and_names_the_documents():
    b = WS.request_body("NC3XYZ-1", "claude-haiku-5-5", maker="Neutrik", package="BGA-100", kind="package",
                        refs=["21-0664"])
    text = b["messages"][0]["content"]
    assert "package outline drawing" in text and "NC3XYZ-1" in text and "21-0664" in text and "Neutrik" in text
    assert "Find the datasheet PDF" not in text


def test_the_package_document_found_by_search_is_appended():
    cl = Client(_answer([OTHER, DWG]))
    files = {DS: pdf_lines(product_sheet()), DWG: pdf_lines(drawing_sheet()),
             OTHER: pdf_lines([["Press release: new connector colours available now for every series"] * 3])}
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, files, web_search=True)
        j = rd.job("NC3XYZ-1", [(DS, "BOM")], None, maker="Neutrik")
        assert len(cl.sent) == 1 and "package outline drawing" in cl.sent[0][0]["messages"][0]["content"]
        assert j["base_prov"]["outline"] == [{"code": "web search", "from": DWG, "first_page": 3}], j["base_prov"]
        assert j["n_pages"] == 3
        assert any(a["url"] == OTHER for a in j["base_prov"]["attempts"]), j["base_prov"]["attempts"]
        led = [json.loads(x) for x in open(Path(d) / "out" / "datasheet" / "cost.jsonl")]
        assert [x["via"] for x in led] == ["web search (package drawing)"], led
        cl2 = Client()                                # the link found is kept: no second search
        j2 = _reader(d, cl2, files, web_search=True).job("NC3XYZ-1", [(DS, "BOM")], None, maker="Neutrik")
        assert not cl2.sent and j2["base_prov"]["outline"][0]["from"] == DWG


def test_no_package_search_when_web_search_is_off_or_the_drawing_is_there():
    files = {DS: pdf_lines(product_sheet()), DWG: pdf_lines(drawing_sheet())}
    with tempfile.TemporaryDirectory() as d:
        cl = Client(_answer([DWG]))
        j = _reader(d, cl, files).job("NC3XYZ-1", [(DS, "BOM")], None)
        assert not cl.sent and "outline" not in j["base_prov"]
    files[DS] = pdf_lines(product_sheet() + drawing_sheet())
    with tempfile.TemporaryDirectory() as d:
        cl = Client(_answer([DWG]))
        j = _reader(d, cl, files, web_search=True).job("NC3XYZ-1", [(DS, "BOM")], None)
        assert not cl.sent and "outline" not in j["base_prov"]


def test_nothing_found_is_said_and_the_datasheet_read_alone():
    cl = Client(_answer([]))
    with tempfile.TemporaryDirectory() as d:
        j = _reader(d, cl, {DS: pdf_lines(product_sheet())}, web_search=True).job("NC3XYZ-1", [(DS, "BOM")], None)
        assert j["n_pages"] == 2 and "outline" not in j["base_prov"]
        assert any("package drawing" in m for m in j["base_prov"]["outline_missing"]), j["base_prov"]
