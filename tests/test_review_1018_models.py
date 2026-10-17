"""Review 2026-10-18: bring-your-own-model path and the datasheet reader.  python -m tests.run_all review_1018_models

Offline only: Claude answers come from tests.test_ds_route's fake Messages API, other providers from a stubbed post;
no request leaves this machine and no real key is read."""
import json, os, tempfile
from pathlib import Path
from unittest import mock

import requests

from ecadlib import datasheet as D, doctor as DOC, firstrun as FR, visual_check as V
from ecadlib.llm import openai_compat as OC, rails as RL
from tests import test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR, test_visual_check as TV
from tests.fixtures_lite import lite

SECRET = "sk-test-NOT-A-REAL-KEY-0123456789"


# ---------------------------------------------------------------- the feedback turn and [datasheet] max_cost_usd
def _one_request_worst():
    api = TR.ModelAPI(lambda m, n: TR.BAD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = TR.reader(d, {"https://a/ds.pdf": TD.PDF})
        return rd.estimate([rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")])["usd_max"]


def test_feedback_turn_is_held_to_max_cost_usd():
    """A failing answer at the end of the route gets one more (paid) turn. With a cap that covers one request but not
    two, that turn must not be sent: before, only the first request was counted against max_cost_usd."""
    cap = round(_one_request_worst() * 1.5, 4)
    api = TR.ModelAPI(lambda m, n: TR.BAD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = TR.reader(d, {"https://a/ds.pdf": TD.PDF}, rules={"datasheet": {"max_cost_usd": cap}})
        res = rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        assert len(api.sent) == 1, api.sent
        fb = res["sidecar"]["ai"]["feedback"]
        assert fb["kept"] == "first" and fb["after"].startswith("not sent") and "max_cost_usd" in fb["after"]
        assert "feedback_retry" not in res["sidecar"]["ai"]["support_ran"]
    api = TR.ModelAPI(lambda m, n: TR.BAD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):  # a cap with room for both: the turn goes out as before
        rd = TR.reader(d, {"https://a/ds.pdf": TD.PDF}, rules={"datasheet": {"max_cost_usd": cap * 4}})
        rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        assert len(api.sent) == 2


# ---------------------------------------------------------------- the API key never reaches an error or the log
@lite
def test_key_with_a_stray_newline_is_not_echoed_in_errors_or_the_log(tmp_path, monkeypatch):
    """A key variable with a trailing CR/LF or a leading space (a Windows .env file, `set KEY=... `) makes requests
    reject the header, and its exception quotes the whole header value: the key went into APIError and api_log.jsonl."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    log = tmp_path / "api_log.jsonl"
    cl = OC.OpenAICompatClient("http://127.0.0.1:9/v1", SECRET + "\r\n", {"pdf": True}, retries=0, log_file=log,
                               say=lambda *a: None)
    try:
        cl.chat({"model": "m", "messages": []})
        raise AssertionError("no error")
    except D.APIError as e:
        assert SECRET not in str(e) and "<api key>" in str(e), str(e)
    c = D.Client(" " + SECRET, base="http://127.0.0.1:9", retries=0, log_file=log, say=lambda *a: None)
    try:
        c.messages({"model": "m", "max_tokens": 1, "messages": []})
        raise AssertionError("no error")
    except D.APIError as e:
        assert SECRET not in str(e), str(e)
    assert log.exists() and SECRET not in log.read_text(encoding="utf-8")

    class R:                                          # a gateway that echoes the key it refused
        status_code, headers, text = 401, {}, ""

        def json(self):
            return {"error": {"message": f"invalid api key: {SECRET}", "type": "authentication_error"}}
    cl = OC.OpenAICompatClient("http://127.0.0.1:9/v1", SECRET, {}, retries=0, log_file=log, post=lambda *a, **k: R())
    try:
        cl.chat({"model": "m", "messages": []})
        raise AssertionError("no error")
    except D.APIError as e:
        assert SECRET not in str(e) and "HTTP 401" in str(e)
    monkeypatch.setattr(D.requests, "post", lambda *a, **k: R())
    try:
        D.Client(SECRET, base="http://127.0.0.1:9", retries=0, log_file=log).messages({"model": "m", "messages": []})
        raise AssertionError("no error")
    except D.APIError as e:
        assert SECRET not in str(e)
    assert SECRET not in log.read_text(encoding="utf-8")


# ---------------------------------------------------------------- json_repair: a value in another unit is not "repaired"
def test_json_repair_does_not_drop_a_unit_that_disagrees_with_the_row():
    """'0.0197 in' in a row whose unit is mm became 0.0197 mm (the unit text stripped, the value silently 25x off);
    '0.30-0.50' became 0.30. Both now stay text, so the shape check rejects them and the feedback turn asks again."""
    schema = D.output_schema()
    good = json.dumps(TR.GOOD)
    pitch = TR.GOOD["package"]["pitch"]
    assert pitch["unit"] == "mm" and pitch["nom"] is not None
    bad = json.loads(good)
    bad["package"]["pitch"]["nom"] = "0.0197 in"
    val, fixes, err = RL.repair(json.dumps(bad), schema)
    assert err is not None and val["package"]["pitch"]["nom"] == "0.0197 in", (err, fixes)
    assert any("not in the row's unit" in f for f in fixes)
    rng = json.loads(good)
    rng["package"]["pitch"]["nom"] = "0.45-0.55"
    val, fixes, err = RL.repair(json.dumps(rng), schema)
    assert err is not None and val["package"]["pitch"]["nom"] == "0.45-0.55"
    ok = json.loads(good)                             # the same unit, or a drawing qualifier, is still only form
    ok["package"]["pitch"]["nom"] = f"{pitch['nom']} mm BSC"
    val, fixes, err = RL.repair(json.dumps(ok), schema)
    assert err is None and val["package"]["pitch"]["nom"] == pitch["nom"]
    inch = json.loads(good)
    inch["package"]["pitch"].update(unit="inches", nom="0.0197 in")
    val, fixes, err = RL.repair(json.dumps(inch), schema)
    assert err is None and val["package"]["pitch"]["unit"] == "in" and val["package"]["pitch"]["nom"] == 0.0197


# ---------------------------------------------------------------- doctor / init / visual proof: the configured key name
def test_doctor_and_init_read_the_key_the_reader_uses():
    """[ai] api_key_env names the Claude key (the reader and endpoint() use it); the doctor checked ANTHROPIC_API_KEY
    instead and warned that the datasheet reader would be skipped. With [ai] provider = "ollama", init said the reader
    needs ANTHROPIC_API_KEY and defaulted to leaving datasheet out of [sources]."""
    rules = {"ai": {"api_key_env": "MY_CLAUDE_KEY"}, "datasheet": {"api_key_env": "ANTHROPIC_API_KEY"}}
    env = {"MY_CLAUDE_KEY": "x"}
    line = next(c for c in DOC.check_keys(rules, env) if c["check"] == "key anthropic")
    assert line["level"] == "OK" and "MY_CLAUDE_KEY" in line["detail"], line
    ollama = {"ai": {"provider": "ollama", "model": "qwen2.5:7b"}}
    asked = []

    def inp(q):
        asked.append(q)
        return ""
    ans = FR.ask_setup({}, {"tool": "kicad", "libs": ["lib"], "workspace": "ws"}, inp=inp, out=lambda *a: None,
                       rules=ollama, environ={})
    q = next(x for x in asked if "datasheet" in x)
    assert "ANTHROPIC_API_KEY" not in q and "no key needed" in q and "[Y/n]" in q, q
    assert ans["sources"] is None                     # datasheet stays in [sources] order
    msg, _ = FR.blockers([{"part": "U1", "tried": "datasheet: no API key"}],
                         rules={"ai": {"provider": "openai", "model": "gpt-5"}})[0]
    assert "OPENAI_API_KEY" in msg and "ANTHROPIC_API_KEY" not in msg, msg


def test_visual_proof_reads_the_ai_key_variable():
    calls = []
    with TV.setup(key=None) as d, mock.patch.dict(os.environ, {"MY_CLAUDE_KEY": SECRET}), \
            mock.patch.object(D.requests, "post", TV.fake_post(TV.message(TV.answer()), calls)):
        pr = V.prove(TV.item(d), d, {"ai": {"api_key_env": "MY_CLAUDE_KEY"}}, out_dir=d / "out")
    assert pr["verdict"] != "skipped", pr.get("reason")
    assert len(calls) == 1 and calls[0]["headers"]["x-api-key"] == SECRET


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["review_1018_models"]))
