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
