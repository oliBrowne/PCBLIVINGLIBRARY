"""A part's route keeps its best answer, not just its last (live benchmark 2026-10-19)."""
import copy, tempfile

from tests import test_datasheet_batch as TB
from tests.test_ds_route import BAD, GOOD, INCOMPLETE, ModelAPI, OPUS, ldo_pages, pdf_lines, reader


def test_a_later_worse_answer_does_not_replace_an_earlier_better_one():
    """CY14U256LA-BA: the 5-page read had the ball grid and pins but cited a page it wasn't sent; the whole-PDF read
    came back with other_json "x" and no pins. The route kept the last one and nothing was built."""
    data = pdf_lines(ldo_pages())
    worse = copy.deepcopy(INCOMPLETE)                                    # more left blank than the first answer
    worse["package"]["body_width"] = None
    worse["package"]["unresolved"] = ["body_length", "body_width"]
    for first, last, kept in ((INCOMPLETE, BAD, "incomplete"), (INCOMPLETE, worse, "incomplete"), (BAD, GOOD, "ok")):
        api = ModelAPI(lambda m, n: last if n == 30 else first)
        with tempfile.TemporaryDirectory() as d, TB.patched(api):
            rd = reader(d, {"https://a/ldo.pdf": data})
            res = rd.read_now(rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"))
            assert [m for m, _ in api.sent][:2] == [OPUS, OPUS] and api.sent[1][1] == 30, api.sent
            spec = res["spec"]
            if kept == "incomplete":
                assert spec["unresolved"] == ["body_length"] and spec.get("body_width"), spec
                assert "later one was worse" in res["sidecar"]["route"]["kept"]
            else:
                assert res["needs_review"] == [] and "kept" not in res["sidecar"]["route"]
