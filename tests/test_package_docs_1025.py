"""The package search found notices instead of drawings (live benchmark 2026-10-23: a product change notice for
M2GL090T-FG484 and for IR4301, each naming the part and its package): a notice found for the package drawing is
skipped and the next link is tried. An application note or a user guide can hold the drawing (IR4301's land is in
AN-1170), so only notices are skipped here. The PDFs are SYNTHETIC; the API is faked and nothing is sent."""
import tempfile

from ecadlib import datasheet as D, ds_search as WS
from tests.test_ds_route import pdf_lines
from tests.test_ds_search import Client, _answer
from tests import test_package_search_1023 as P

PCN = "https://maker.example/pcn/PCN-2023-118.pdf"


def pcn_sheet():
    return [["Product Change Notification PCN-2023-118", "Affected parts: NC3XYZ-1 and the whole series",
             "Change: assembly site transfer. Package outline and dimensions unchanged."]]


def test_a_notice_is_told_from_a_package_document():
    assert D.a_notice([" \n".join(pcn_sheet()[0])])
    assert D.a_notice(["Product Discontinuation Notice\nLast time buy 2027-03-31"])
    # the documents a drawing can be in are not notices
    for first in ("Application Note AN-1170\nDirectFET board mounting", "User's Guide\nPackage outline and land",
                  "Packaging Specification\nFBGA-484 package outline", "NC3XYZ-1 drawing\nPCB layout, top view"):
        assert D.a_notice([first]) is None, first
    assert D.a_notice([]) is None


def test_a_notice_found_for_the_drawing_is_skipped_for_the_next_link():
    from tests import test_datasheet_batch as TB
    cl, api = Client(_answer([PCN, P.DWG])), P._reads()
    files = {P.DS: pdf_lines(P.product_sheet()), PCN: pdf_lines(pcn_sheet()), P.DWG: pdf_lines(P.drawing_sheet())}
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        res = P._read(P._reader(d, cl, files, web_search=True))
        assert res["sidecar"]["route"]["package_doc"] == P.DWG
        miss = [a for a in res["sidecar"]["pdf"]["attempts"] if a["url"] == PCN]
        assert miss and "a notice, not a package drawing" in miss[0]["error"], miss


def test_only_notices_found_leaves_the_part_without_a_drawing():
    from tests import test_datasheet_batch as TB
    cl, api = Client(_answer([PCN])), P._reads()
    files = {P.DS: pdf_lines(P.product_sheet()), PCN: pdf_lines(pcn_sheet())}
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        res = P._read(P._reader(d, cl, files, web_search=True))
        assert [n for _, n in api.sent] == [2]                        # the notice was never appended to the read
        assert any(r.startswith("no package drawing: ") for r in res["needs_review"]), res["needs_review"]


def test_the_package_prompt_says_a_notice_is_not_wanted():
    text = WS.request_body("NC3XYZ-1", "claude-haiku-5-5", kind="package")["messages"][0]["content"]
    assert "change notice" in text


# ---------------------------------------------------------------- the 8 wrong documents of the 2026-10-24 run
def test_a_prefix_of_the_part_number_does_not_name_it_in_a_package_document():
    from ecadlib import outline_docs as OD
    # JST's XH catalog has S13B-XH-A-1, not S13B-EH; Vishay's tray table has "KBU4, 6, 8", not KBU4A
    assert not OD.names_it(["XH CONNECTOR 2.5 mm pitch, crimp style. S13B-XH-A-1 side entry, 13 circuits"] * 2, "S13B-EH")
    assert not OD.names_it(["Packaging information. KBU4, 6, 8 anti-static PVC tray 12.2 x 6.1, 20 pcs per tray"] * 2,
                           "KBU4A")
    assert OD.names_it(["EH CONNECTOR 2.5 mm pitch. S13B-EH side entry type, 13 circuits, PCB layout"] * 2, "S13B-EH")
    # the datasheet check itself still takes a prefix that keeps the numeric core (LTC4060 of LTC4060EDHC)
    from ecadlib import datasheet as D
    assert D.mentions_part(["LTC4060 standalone linear NiMH/NiCd fast battery charger, full text of a page here"],
                           "LTC4060EDHC")
    assert not D.mentions_part(["LTC4060 standalone linear NiMH/NiCd fast battery charger, full text of a page here"],
                               "LTC4060EDHC", whole=True)


def test_the_package_code_in_the_part_number_must_be_printed():
    from ecadlib import outline_docs as OD
    fineline = ["256-Pin FineLine BGA package outline, DIM MIN MAX, ball pitch 1.00 mm, BGA-256"]
    assert not OD.names_it(fineline, "EPM1270M256", own={"BGA", "BGA256"})
    assert OD.names_it(["M256 Micro FineLine BGA-256 package outline, 0.5 mm pitch, DIM MIN MAX"], "EPM1270M256",
                       own={"BGA", "BGA256"})
    assert not OD.names_it(["PCB layout recommendations for BGA packages: 64, 81, 100, 121, 144 ball BGA-121"],
                           "ICE40HX8K-BG121", own={"BGA121"})
    assert OD.mpn_package_code("M2GL090T-FG484", {"BGA484"}) == "FG484"
    assert OD.mpn_package_code("GBU4A", {"DIP275"}) is None and OD.mpn_package_code("LM358DR", {"SOIC8"}) is None
    # a part number with no package code keeps the family and count match (GBU4A took Vishay's GBU case outline)
    assert OD.names_it(["GBU25H08 GBU case outline, DIM MIN MAX, DIP-275"], "GBU4A", own={"DIP275"})


def test_a_long_document_with_no_drawing_of_the_package_is_skipped():
    from tests import test_datasheet_batch as TB
    LONG = "https://maker.example/docs/pin-descriptions.pdf"
    pins = [[f"NC3XYZ-1 pin descriptions, part {i}", "User I/O banks, supply pins and configuration pins",
             "Each I/O can be configured as input, output or bidirectional"] for i in range(5)]
    cl, api = Client(_answer([LONG, P.DWG])), P._reads()
    files = {P.DS: pdf_lines(P.product_sheet()), LONG: pdf_lines(pins), P.DWG: pdf_lines(P.drawing_sheet())}
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        res = P._read(P._reader(d, cl, files, web_search=True))
        assert res["sidecar"]["route"]["package_doc"] == P.DWG
        miss = [a for a in res["sidecar"]["pdf"]["attempts"] if a["url"] == LONG]
        assert miss and "no drawing of the package" in miss[0]["error"], miss


def test_a_long_application_note_with_the_land_pattern_is_kept():
    # Greptile on #186: a long note that shows the recommended land pattern and no package dimension table
    # (IR4301's land is in AN-1170) is a document worth appending
    from tests import test_datasheet_batch as TB
    AN = "https://maker.example/docs/an-land.pdf"
    note = [["Application Note: board mounting of the NC3XYZ-1", "Handling, reflow profile and cleaning"]] + \
           [[f"Section {i}: solder joint reliability and inspection", "Thermal cycling results for the series"]
            for i in range(3)] + [["NC3XYZ-1 recommended land pattern", "Pad 1.30 x 0.80, pitch 5.08, hole 1.3"]]
    cl, api = Client(_answer([AN])), P._reads(blank_on=2)
    files = {P.DS: pdf_lines(P.product_sheet()), AN: pdf_lines(note)}
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        res = P._read(P._reader(d, cl, files, web_search=True))
        assert res["sidecar"]["route"].get("package_doc") == AN, res["sidecar"]["pdf"]["attempts"]
