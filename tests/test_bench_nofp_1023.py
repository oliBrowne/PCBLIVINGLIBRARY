"""Live benchmark 2026-10-23 (Opus zoom + web search, main 47328c9): parts that got no footprint at all.
Each test is a general rule, shown on the part that exposed it."""
from ecadlib import datasheet as D


def test_an_asymmetric_tolerance_with_an_unsigned_zero_side_gives_min_and_max():
    # S28B-PHDSS: JST prints the board hole as "0.7 +0.1/0" (0.7 to 0.8 mm); the reader rejected it as not a number
    assert D._toleranced("0.7 +0.1/0") == {"min": 0.7, "nom": 0.7, "max": 0.8}
    assert D._toleranced("0.7+0.1/-0") == {"min": 0.7, "nom": 0.7, "max": 0.8}
    assert D._toleranced("0.7 +0.1 0") == {"min": 0.7, "nom": 0.7, "max": 0.8}
    assert D._toleranced("0.7 +0.1/0.0 mm") == {"min": 0.7, "nom": 0.7, "max": 0.8}
    assert D._toleranced("1.2 +0/-0.1") == {"min": 1.1, "nom": 1.2, "max": 1.2}
    assert D._toleranced("1.2 0/-0.1") == {"min": 1.1, "nom": 1.2, "max": 1.2}


def test_an_asymmetric_tolerance_printed_minus_first_reads_the_same():
    assert D._toleranced("3.56 -0.25/+0.15") == {"min": 3.31, "nom": 3.56, "max": 3.71}
    assert D._toleranced("0.7 -0/+0.1") == {"min": 0.7, "nom": 0.7, "max": 0.8}


def test_an_unsigned_non_zero_side_or_two_same_signs_stays_unread():
    # "0.7 +0.1/0.05": is 0.05 below or above? A guess could build the part wrong, so the value is left for the hold
    for v in ("0.7 +0.1/0.05", "0.7 +0.1/+0.05", "0.7 -0.1/-0.05", "0.7 0.1/0", "0.7 +0.1", "0.7/0.8"):
        assert D._toleranced(v) is None, v


def test_a_drill_row_printed_with_an_asymmetric_tolerance_is_read_not_rejected():
    row = {"name": "drill", "label": "hole", "min": "", "nom": "0.7 +0.1/0", "max": "", "unit": "mm", "page": 2,
           "figure": "PC board layout"}
    assert D._wire_dim(row, "drill") == {"min": 0.7, "nom": 0.7, "max": 0.8}


def test_zoom_drawings_with_no_pdf_renderer_says_so_instead_of_reading_without_close_ups():
    # 10-23 run: the bench venv had no pypdfium2 or pdftoppm, so zoom_drawings sent no close-ups and nothing said so
    from unittest import mock
    import tempfile
    from ecadlib import pdf_render
    from tests.test_ds_route import reader
    from tests.test_datasheet_reading_1019 import MCU_MARKS, bookmarked, mcu_pages
    data = bookmarked(mcu_pages(), MCU_MARKS)
    said = []
    with tempfile.TemporaryDirectory() as d, mock.patch.object(pdf_render, "renderer", lambda: None):
        rd = reader(d, {"https://a/mcu.pdf": data})
        rd.cfg, rd.say = dict(rd.cfg, zoom_drawings=True), said.append
        job = rd.job("TESTMCU100", [("https://a/mcu.pdf", "BOM")], "LQFP-100")
        rd.job("TESTMCU100", [("https://a/mcu.pdf", "BOM")], "LQFP-100")
    assert not any(b["type"] == "image" for b in job["body"]["messages"][0]["content"])
    assert "no PDF renderer" in job["prov"]["zoom_skipped"]
    assert sum("zoom_drawings" in s and "pypdfium2" in s for s in said) == 1, said     # said once, not per part


def test_an_eval_run_with_zoom_drawings_and_no_pdf_renderer_stops_before_it_reads():
    from unittest import mock
    import tempfile
    from ecadlib import pdf_render
    from ecadlib.eval import runner as R
    with tempfile.TemporaryDirectory() as d, mock.patch.object(pdf_render, "renderer", lambda: None):
        try:
            R.run(limit=1, out=d, offline=True, cache=d + "/c", rules={"datasheet": {"zoom_drawings": True}})
            raise AssertionError("ran without close-ups")
        except ValueError as e:
            assert "zoom_drawings" in str(e) and "pypdfium2" in str(e), e


def _answer_with_drill(nom):
    import copy
    from tests import test_datasheet as TD
    g = copy.deepcopy(TD.GOOD)
    g["package"]["terminal_length"] = TD.dim("L", None, nom, None)
    return TD.message(D.to_wire(g))


def test_an_answer_the_parser_rejected_is_kept_so_a_later_parser_fix_re_scores_it_free():
    # S28B-PHDSS (10-23): the batch answer was rejected and only its error string was kept, so the parser fix could
    # not be checked without paying for the read again
    import tempfile
    from tests.test_ds_route import reader
    bad = _answer_with_drill("0.4 +0.1/0")
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {})
        job = {"key": "abc-claude-opus-5-5-v1-123", "part": "TESTPART", "url": "https://a/x.pdf", "offset": 0}
        real = D._toleranced_unit
        try:                                          # the parser before the fix
            D._toleranced_unit = lambda v: (None, None)
            try:
                rd._store(job, bad, "batch msgbatch_1")
                raise AssertionError("parsed")
            except D.DatasheetError:
                pass
            assert rd.results.get(job["key"]) is None or "message" not in rd.results.get(job["key"])
        finally:
            D._toleranced_unit = real
        rec = rd.results.get(job["key"])              # this parser reads it: the saved answer, no new read
        assert rec and rec["message"] == bad and rec.get("reparsed")
        assert D.parse_message(rec["message"])["package"]["terminal_length"]["max"] == 0.5


def test_a_kept_rejected_answer_that_still_does_not_parse_is_no_answer():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        c = D.ResultCache(d)
        c.put_rejected("k1", {"key": "k1", "message": _answer_with_drill("about 0.4"), "error": "x"})
        assert c.get("k1") is None
        c.put("k1", {"key": "k1", "error": "x"})
        assert "message" not in c.get("k1")
