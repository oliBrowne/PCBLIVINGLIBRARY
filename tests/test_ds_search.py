"""[datasheet] web_search: a datasheet link found by web search when no link of the part gave a PDF (ecadlib.ds_search).
Plain functions (tests.run_all, no pytest). The API is faked; nothing is sent anywhere."""
import json
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_search as WS
from tests.test_datasheet_batch import KEY, PDF, PdfHTTP

DEAD, FOUND = "https://maker.example/old/ds.pdf", "https://maker.example/new/ds.pdf"


def _answer(urls, results=(), searches=1, stop="end_turn"):
    content = [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "TESTPART datasheet"}},
               {"type": "web_search_tool_result", "tool_use_id": "s1",
                "content": [{"type": "web_search_result", "url": u, "title": "t"} for u in results]},
               {"type": "text", "text": "Found it.\n" + json.dumps({"urls": list(urls)})}]
    return {"model": "claude-opus-5-5", "stop_reason": stop, "content": content,
            "usage": {"input_tokens": 9000, "output_tokens": 800, "server_tool_use": {"web_search_requests": searches}}}


class Client:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def messages(self, body, headers=None):
        self.sent.append((body, headers))
        return self.answers.pop(0)


def _reader(d, client, **ds):
    return D.Reader(Path(d) / "out", Path(d) / "cache", key=KEY, log=None, client=client,
                    http=PdfHTTP({FOUND: PDF, DEAD: 404}),
                    rules={"datasheet": {"cross_check": "off", **ds}})


def test_links_take_the_answers_order_then_pdf_search_results():
    msg = _answer(["https://a.example/x.pdf", "https://a.example/x.pdf"],
                  results=["https://b.example/y.pdf", "https://b.example/page.html", "https://c.example/datasheet?id=3"])
    assert WS.links_in(msg["content"]) == ["https://a.example/x.pdf", "https://b.example/y.pdf",
                                           "https://c.example/datasheet?id=3"]


def test_request_uses_the_dynamic_search_tool_on_opus_and_the_basic_one_on_haiku():
    b = WS.request_body("TESTPART", "claude-opus-5-5", maker="ACME", package="SOIC-8", max_uses=2)
    assert b["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}]
    assert "ACME" in b["messages"][0]["content"] and "SOIC-8" in b["messages"][0]["content"]
    assert WS.request_body("X", "claude-haiku-5-5")["tools"][0]["type"] == "web_search_20250305"


def test_a_dead_link_is_found_by_search_kept_and_billed():
    cl = Client(_answer([FOUND]))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True)
        job = rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
        assert job["url"] == FOUND and job["base_prov"]["found_by"] == "web search", job["url"]
        assert cl.sent[0][1] == {"anthropic-beta": D.FALLBACK_BETA} and cl.sent[0][0]["fallbacks"] == "default"
        led = [json.loads(x) for x in open(Path(d) / "out" / "datasheet" / "cost.jsonl")]
        ws = [x for x in led if x["via"] == "web search"]
        assert len(ws) == 1 and ws[0]["web_searches"] == 1
        assert abs(ws[0]["usd"] - ((9000 * 4 + 800 * 20) / 1e6 + WS.SEARCH_USD)) < 1e-9, ws[0]["usd"]
        assert abs(rd.committed_usd - ws[0]["usd"]) < 1e-9
        job2 = _reader(d, Client(), web_search=True).job("TESTPART", [(DEAD, "BOM")], "SOIC-8")   # no second search
        assert job2["url"] == FOUND


def test_a_part_with_no_link_is_searched_with_its_maker():
    cl = Client(_answer([FOUND]))
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, cl, web_search=True).job("TESTPART", [], "SOIC-8", maker="ACME Corp")
        assert job["url"] == FOUND
        assert "made by ACME Corp" in cl.sent[0][0]["messages"][0]["content"]


def test_pause_turn_is_continued():
    cl = Client(_answer([], stop="pause_turn"), _answer([FOUND]))
    links, usage, turns, err = WS.search(cl, "TESTPART", "claude-opus-5-5")
    assert links == [FOUND] and err is None and len(turns) == 2 and usage["web_search_requests"] == 2 and len(cl.sent) == 2
    assert cl.sent[1][0]["messages"][-1]["role"] == "assistant"


def _fails(rd):
    try:
        rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
    except D.DatasheetError as e:
        return str(e)
    raise AssertionError("a PDF was found")


def test_off_by_default_and_held_by_the_cost_cap():
    with tempfile.TemporaryDirectory() as d:
        cl = Client(_answer([FOUND]))
        assert "HTTP 404" in _fails(_reader(d, cl)) and not cl.sent
        assert "not searched" in _fails(_reader(d, cl, web_search=True, max_cost_usd=0.001)) and not cl.sent


def test_a_search_link_that_is_not_a_pdf_of_the_part_is_skipped():
    cl = Client(_answer([DEAD, FOUND]))
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, cl, web_search=True).job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
        assert job["url"] == FOUND
        assert any(a["where"] == "web search" and a["url"] == DEAD for a in job["base_prov"]["attempts"])


class Failing(Client):
    def messages(self, body, headers=None):
        if not self.answers:
            self.sent.append((body, headers))
            raise D.DatasheetError("timed out")
        return super().messages(body, headers)


def test_a_failed_continuation_keeps_the_paid_turn_and_holds_the_failed_one():
    cl = Failing(_answer([], stop="pause_turn"))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True)
        assert "search failed: timed out" in _fails(rd)
        first = WS.usd("claude-opus-5-5", [_answer([])])
        est = WS.estimate("claude-opus-5-5", WS.MAX_USES, fallback=True)
        assert abs(rd.committed_usd - (first + est)) < 1e-6, rd.committed_usd
        led = [json.loads(x) for x in open(Path(d) / "out" / "datasheet" / "cost.jsonl")]
        assert [x["how"] for x in led if x["via"] == "web search"] == ["estimate"]


def test_a_continuation_that_could_pass_the_cap_is_not_sent():
    cl = Client(_answer([], stop="pause_turn"), _answer([FOUND]))
    est = WS.estimate("claude-opus-5-5", WS.MAX_USES, fallback=True)
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True, max_cost_usd=est + 0.01)   # room for one turn's worst case, not two
        assert "the search found no PDF link" in _fails(rd)
        assert len(cl.sent) == 1 and rd.committed_usd <= est + 0.01


def test_a_fallback_answer_is_priced_at_the_model_that_served_it():
    msg = dict(_answer([FOUND]), model="claude-opus-4-8")
    want = WS.usd("claude-opus-4-8", [dict(msg)])
    assert WS.usd("claude-opus-5-5", [msg]) == want
    assert WS.estimate("claude-opus-5-5", fallback=True) >= WS.estimate("claude-opus-5-5")


def test_models_that_reject_effort_are_not_sent_it():
    assert "output_config" not in WS.request_body("X", "claude-haiku-4-5")
    assert "output_config" not in WS.request_body("X", "claude-sonnet-4-5-20250929")
    assert WS.request_body("X", "claude-opus-5-5")["output_config"] == {"effort": "low"}
    assert WS.request_body("X", "claude-sonnet-4-5")["tools"][0]["type"] == "web_search_20250305"


def test_a_part_with_no_link_says_why_the_search_did_not_run():
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, Client(), web_search=True, max_cost_usd=0.001)
        try:
            rd.job("TESTPART", [], "SOIC-8")
        except D.DatasheetError as e:
            assert "no datasheet URL known; web search: not searched" in str(e), str(e)
        else:
            raise AssertionError("a PDF was found")
