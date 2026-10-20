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
