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
