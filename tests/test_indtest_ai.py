"""Regression tests for the AI-path bugs of the 2026-10-20 independent testing pass (one comment line per T-id).
Nothing live: the OpenAI-compatible side is test_llm's local FakeOpenAI server, Claude answers are fakes."""
import contextlib, copy, io, json, os
from unittest import mock

from ecadlib import datasheet as D
from tests import test_llm as TL
from tests.fixtures_lite import lite


def _small_single(tmp_path, dims):
    """`ecadlib datasheet PDF --part TESTPART` with a small model on a local server whose dims step answers `dims`."""
    def answer(body):
        st = TL.step_of(body)
        return TL.chat(json.dumps(TL.IDENT if st == "identify" else dims if st == "dims" else TL.PINS))
    with TL.FakeOpenAI(answer) as srv:
        (tmp_path / "t.pdf").write_bytes(TL._pdf2())
        (tmp_path / "r.json").write_text(json.dumps(TL.small_rules(srv.base)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                rc = D.main([str(tmp_path / "t.pdf"), "--part", "TESTPART", "-o", str(tmp_path / "out"),
                             "--rules", str(tmp_path / "r.json")])
            except SystemExit as e:
                rc = e.code
    return rc, out.getvalue()


# T057: the single read holds a guided model's failing answer (hold_on_doubt) and records "ai" in the sidecar
@lite
def test_t057_single_read_holds_a_failing_small_model_answer(tmp_path):
    d = copy.deepcopy(TL.DIMS)
    d["terminal_width"] = TL.TD.dim("b", 0.4, 0.5, 0.6, page=2)     # leads wider than the pitch, every time
    rc, _ = _small_single(tmp_path, d)
    assert isinstance(rc, str) and "held: the answer of tiny-3b (small tier)" in rc, rc
    assert not [f for f in (tmp_path / "out").glob("*.json") if not f.name.startswith("TESTPART.response")]


@lite
def test_t057_single_read_sidecar_has_the_ai_record(tmp_path):
    rc, out = _small_single(tmp_path, TL.DIMS)
    assert rc == 0, (rc, out)
    ai = json.loads((tmp_path / "out" / "TESTPART.extraction.json").read_text())["ai"]
    assert ai["model"] == "tiny-3b" and ai["tier"] == "small" and "hold_on_doubt" in ai["rails"], ai


# T055: a local $0 model is priced $0 (not "no price known") under a [datasheet] max_cost_usd cap the person set
def test_t055_local_zero_price_model_passes_a_set_cap():
    import tempfile
    rules = {"ai": {"provider": "ollama", "base_url": "http://127.0.0.1:9/v1", "model": "local-m", "detect": False,
                    "profiles": {"local-m": {"tier": "frontier", "pdf": True, "json_schema": True}}},
             "datasheet": {"max_cost_usd": 5}}
    with tempfile.TemporaryDirectory() as d:
        rd = TL.TR.reader(d, {"https://a/ds.pdf": TL._pdf2()}, rules=rules)
        job = dict(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")]), sync=True)
        rd._guard_cost([job])                           # $0 is under $5: no "no price known"
        est = rd.estimate([job])
    assert est["usd"] == 0 and est["usd_max"] == 0, est


# T056: a read refused before its request went out (an endpoint that can't fetch a PDF by URL) costs $0 in cost.jsonl
@lite
def test_t056_ledger_has_no_cost_for_a_request_never_sent(tmp_path, monkeypatch):
    from ecadlib import ds_route as DR
    sent = []
    monkeypatch.setattr(D.requests, "post", lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError("sent")))
    monkeypatch.setenv("REMOTE_KEY", "sk-remote-test")
    rules = {"ai": {"provider": "openai-compatible", "base_url": "https://llm.example.invalid/v1", "api_key_env": "REMOTE_KEY",
                    "model": "priced-model", "detect": False,
                    "profiles": {"priced-model": {"tier": "frontier", "pdf": True, "json_schema": True,
                                                  "price_in": 2.0, "price_out": 8.0}}}}
    rd = TL.TR.reader(tmp_path, {}, rules=rules, yes=True)
    try:                                                # the link can't be downloaded: the PDF would go as a URL
        rd.read_now(rd.job("TESTPART", [("https://datasheets.example.com/unreachable.pdf", "BOM")]))
        raise AssertionError("read")
    except D.DatasheetError as e:
        assert "can't fetch a PDF by URL" in str(e), e
    rows = DR.read_ledger(tmp_path / "out" / "datasheet" / "cost.jsonl")
    assert sent == [] and rows and sum(r["usd"] or 0 for r in rows) == 0, rows
    assert all(r["how"] == "not sent" and r["input_tokens"] == 0 for r in rows), rows
    assert rd.committed_usd == 0, rd.committed_usd      # the cap's hold is given back too


# T058: a Message Batch answer that fails the checks where the route stops gets the feedback turn (synchronously)
def test_t058_failing_batch_answer_gets_the_feedback_turn():
    import tempfile
    from tests import test_datasheet_batch as TB, test_ds_route as TR
    api = TR.ModelAPI(lambda m, n: TR.BAD if len(api.sent) == 1 else TR.GOOD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = TR.reader(d, {"https://a/ds.pdf": TL.TD.PDF}, batch=True)
        job = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        rd.submit(job)
        res = rd.flush()[job["key"]]
        sync = [c for c in api.calls if c[0] == "POST" and c[1].endswith("/v1/messages")]
        rows = TR.ledger(d)
        n = len(api.calls)
        rd2 = TR.reader(d, {"https://a/ds.pdf": TL.TD.PDF}, batch=True)   # a re-run replays it, sends nothing
        rd2.submit(rd2.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        rd2.flush()
        assert len(api.calls) == n
    assert len(api.of("/v1/messages/batches")) == 1 and len(sync) == 1, api.calls
    assert len(sync[0][3]["messages"]) == 3 and "pitch" in json.dumps(sync[0][3]["messages"][-1])   # answer + errors
    assert not isinstance(res, Exception) and res["needs_review"] == [], res
    ai = res["sidecar"]["ai"]
    assert ai["feedback"]["kept"] == "retry" and "feedback_retry" in ai["support_ran"], ai
    assert [r["via"] for r in rows] == ["batch " + rows[0]["via"].split()[-1], "sync"], rows


# T059: `ecadlib costs --model gemini-...` (no [ai] table) prices Gemini without Claude's cache write and batch price
@lite
def test_t059_costs_model_gemini_is_not_priced_as_claude(tmp_path, monkeypatch):
    from ecadlib import costs as C
    monkeypatch.chdir(tmp_path)                         # no ecadlib.toml found from here
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert C.main(["--model", "gemini-3.8-flash", "--json"]) == 0
    row = json.loads(buf.getvalue())[0]
    rin, rout = C.read_tokens()
    assert row["read_batch"] is None and abs(row["read"] - (rin * 0.75 + rout * 3.75) / 1e6) < 1e-9, row
    assert row == dict(C.jobs("gemini-3.8-flash", "gemini"), model="gemini-3.8-flash")
