"""A datasheet with no drawing of the part's package: the package document found by web search is appended (live
benchmark 2026-10-23: 12 of the 17 blank-dimension parts had a PDF that does not print the dimension: Neutrik
product sheets with photos only, Altera / Lattice / Microchip family sheets whose BGA drawings are in a separate
package document, an onsemi sheet that dropped the DFN drawing). The PDFs are SYNTHETIC, not copies. The API is
faked; nothing is sent anywhere."""
import json
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_search as WS, outline_docs as OD
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
    from tests.test_ds_route import reader
    return reader(d, files, rules={"datasheet": {"web_search_model": "claude-haiku-5-5", **ds}}, client=cl)


def _reads(blank_on=2):
    """Reads answered by page count: the datasheet alone (blank_on pages) leaves the body length blank; with the
    drawing appended, the body is read."""
    from tests.test_ds_route import ModelAPI, GOOD, INCOMPLETE
    return ModelAPI(lambda m, n: INCOMPLETE if n == blank_on else GOOD)


def _texts(pages):
    return [" \n".join(p) for p in pages]


def _read(rd, part="NC3XYZ-1"):
    return rd.read_now(rd.job(part, [(DS, "BOM")], None, maker="Neutrik"))


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


def test_a_read_that_left_a_dimension_blank_gets_the_package_drawing():
    from tests import test_datasheet_batch as TB
    cl, api = Client(_answer([OTHER, DWG])), _reads()
    files = {DS: pdf_lines(product_sheet()), DWG: pdf_lines(drawing_sheet()),
             OTHER: pdf_lines([["Press release: new connector colours available now for every series"] * 3])}
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = _reader(d, cl, files, web_search=True)
        res = _read(rd)
        assert [n for _, n in api.sent] == [2, 3], api.sent           # the datasheet, then with the drawing appended
        assert len(cl.sent) == 1 and "package outline drawing" in cl.sent[0][0]["messages"][0]["content"]
        assert res["spec"]["body_length"], res["spec"]
        assert any(f"the package drawing came from web search ({DWG}), appended from page 3" in r
                   for r in res["needs_review"]), res["needs_review"]
        assert res["sidecar"]["route"]["package_doc"] == DWG
        assert any(a["url"] == OTHER for a in res["sidecar"]["pdf"]["attempts"]), res["sidecar"]["pdf"]["attempts"]
        led = [json.loads(x) for x in open(Path(d) / "out" / "datasheet" / "cost.jsonl")]
        assert [x["via"] for x in led if "web search" in x["via"]] == ["web search (package drawing)"], led
        cl2 = Client()                                # replayed: the link and both answers are kept, nothing is sent
        res2 = _read(_reader(d, cl2, files, web_search=True))
        assert not cl2.sent and len(api.sent) == 2 and res2["spec"]["body_length"]


def test_a_read_that_came_back_whole_never_searches():
    # benchmark 2026-10-23: ten parts read right had vector drawings the text check alone took for no drawing
    from tests import test_datasheet_batch as TB
    cl = Client(_answer([DWG]))
    with tempfile.TemporaryDirectory() as d, TB.patched(_reads(blank_on=-1)):
        res = _read(_reader(d, cl, {DS: pdf_lines(product_sheet()), DWG: pdf_lines(drawing_sheet())}, web_search=True))
        assert not cl.sent and not any("package drawing" in r for r in res["needs_review"])


def test_no_package_search_when_web_search_is_off_or_the_drawing_is_there():
    from tests import test_datasheet_batch as TB
    files = {DS: pdf_lines(product_sheet()), DWG: pdf_lines(drawing_sheet())}
    with tempfile.TemporaryDirectory() as d, TB.patched(_reads()):
        cl = Client(_answer([DWG]))
        _read(_reader(d, cl, files))
        assert not cl.sent
    files[DS] = pdf_lines(product_sheet() + drawing_sheet())
    with tempfile.TemporaryDirectory() as d, TB.patched(_reads(blank_on=3)):
        cl = Client(_answer([DWG]))
        _read(_reader(d, cl, files, web_search=True))
        assert not cl.sent


def test_nothing_found_is_said_and_the_read_stands():
    from tests import test_datasheet_batch as TB
    cl, api = Client(_answer([])), _reads()
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        res = _read(_reader(d, cl, {DS: pdf_lines(product_sheet())}, web_search=True))
        assert [n for _, n in api.sent] == [2] and len(cl.sent) == 1
        assert any(r.startswith("no package drawing: ") for r in res["needs_review"]), res["needs_review"]


# ---------------------------------------------------------------- Greptile on #175
def test_a_drawing_of_another_size_of_the_family_is_not_taken():
    assert OD.names_it(["FBGA-484 package outline, DIM MIN MAX"], "M2GL090T-FG484", own={"BGA484"})
    assert not OD.names_it(["BGA package outline: FBGA-256, DIM MIN MAX"], "M2GL090T-FG484", own={"BGA484"})
    assert not OD.names_it(["Ball grid array packages: general handling notes"], "M2GL090T-FG484", own={"BGA484"})


def test_a_page_that_only_points_to_the_packaging_specification_is_not_a_drawing():
    pages = [["PIC16F18446 28/40-pin microcontroller", "Ordering: PIC16F18446-I/SS SSOP-20"],
             ["Package information: see the Microchip Packaging Specification for the package drawing of every "
              "package listed here, with its dimensions, land pattern and marking. "
              "The specification is updated as packages are added, so always use the current revision. " * 2,
              "Electrical characteristics follow on the next pages: supply voltage, current, timing and temperature."]]
    assert OD.drawing_missing(_texts(pages), "PIC16F18446-I/SS", "SSOP-20")


def test_a_search_that_found_nothing_is_not_paid_for_again():
    import datetime
    from tests import test_datasheet_batch as TB
    files = {DS: pdf_lines(product_sheet())}
    with tempfile.TemporaryDirectory() as d, TB.patched(_reads()):
        cl = Client(_answer([]))
        _read(_reader(d, cl, files, web_search=True))
        assert len(cl.sent) == 1
        cl2 = Client()
        res = _read(_reader(d, cl2, files, web_search=True))
        assert not cl2.sent and any("found no PDF of it" in r for r in res["needs_review"]), res["needs_review"]
        entry = WS.Found(Path(d) / "out" / "datasheet").get("NC3XYZ-1 package drawing")
        later = datetime.date.fromisoformat(entry["at"]) + datetime.timedelta(days=WS.NONE_DAYS)
        assert WS.Found.none_for(entry, entry["none"]) and not WS.Found.none_for(entry, entry["none"], later)
        assert not WS.Found.none_for(entry, "another datasheet")


def test_a_failed_search_is_not_remembered_as_found_nothing():
    from tests import test_datasheet_batch as TB
    from tests.test_ds_search import Failing
    with tempfile.TemporaryDirectory() as d, TB.patched(_reads()):
        _read(_reader(d, Failing(), {DS: pdf_lines(product_sheet())}, web_search=True))
        assert WS.Found(Path(d) / "out" / "datasheet").get("NC3XYZ-1 package drawing") is None


def test_the_batch_route_goes_on_with_the_package_drawing():
    from tests import test_datasheet_batch as TB
    from tests.test_ds_route import ModelAPI, GOOD, INCOMPLETE, reader

    class API(ModelAPI):                              # one client for the batch and the search, as in a real run
        def _msg(self, body):
            return _answer([DWG]) if body.get("tools") else super()._msg(body)
    api = API(lambda m, n: INCOMPLETE if n == 2 else GOOD, polls=0)
    files = {DS: pdf_lines(product_sheet()), DWG: pdf_lines(drawing_sheet())}
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, files, rules={"datasheet": {"web_search": True, "web_search_model": "claude-haiku-5-5"}},
                    batch=True)
        j = rd.job("NC3XYZ-1", [(DS, "BOM")], None)
        rd.submit(j)
        res = rd.flush()[j["key"]]
        assert isinstance(res, dict) and res["spec"]["body_length"], res
        assert [n for _, n in api.sent] == [2, 3] and len(api.of("/v1/messages/batches")) == 2
