"""Regression tests for the full review of the bring-your-own-model layer (ecadlib.llm, datasheet, visual_check).
Run:  python3 -m tests.run_all test_review_full_llm   (HTTP is mocked; no key needed)"""
import copy, json, os, tempfile
from unittest import mock

from ecadlib import datasheet as D, ds_route as DR, llm as LL, rules as RU, visual_check as V
from ecadlib.llm import openai_compat as OC, rails as RL
from tests import test_datasheet as TD


def test_output_schema_has_no_dangling_ref_and_a_tab_answer_parses():
    """package_schema's tab.width / tab.length were "$ref": "#/$defs/dim" in a request schema without $defs: the API
    gets an unresolvable schema and jsonschema raised a non-DatasheetError on any answer that had a tab."""
    assert "$ref" not in json.dumps(D.output_schema())
    e = copy.deepcopy(TD.GOOD)
    e["package"]["tab"] = {"number": "4", "side": "R", "offset": None, "length": None,
                           "width": {"min": 3.0, "nom": None, "max": 3.2, "tol": None}}
    ext = D.parse_message(TD.message(e))
    assert ext["package"]["tab"]["width"]["max"] == 3.2
    res = D.process(ext, "TESTPART", "ds.pdf")
    assert res["spec"]["tab"]["width"] == {"min": 3.0, "max": 3.2}


class _R:
    def __init__(self, st, p):
        self.status_code, self.p, self.headers, self.text = st, p, {}, ""

    def json(self):
        return self.p


def _body(schema):
    return {"model": "m", "max_tokens": 100, "system": [{"type": "text", "text": "SYS"}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}


def test_rejected_json_schema_format_moves_the_schema_into_the_prompt():
    """A server that 400s on json_schema was asked again with json_object and NO schema anywhere (the plan relied on
    the response format), so the model could not know the field names; a fenced answer was then rejected as not JSON."""
    sent = []

    def post(url, headers, json, timeout):
        sent.append(json)
        if json.get("response_format", {}).get("type") == "json_schema":
            return _R(400, {"error": {"message": "response_format json_schema not supported"}})
        return _R(200, {"choices": [{"message": {"content": "```json\n{\"a\": 1}\n```"}, "finish_reason": "stop"}]})
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
    cl = OC.OpenAICompatClient("http://localhost:1234/v1", None, {"json_schema": True}, post=post, sleep=lambda s: None)
    msg = cl.messages(_body(schema))
    assert [s.get("response_format", {}).get("type") for s in sent] == ["json_schema", "json_object"]
    assert '"a"' not in sent[0]["messages"][0]["content"] and '"a"' in sent[1]["messages"][0]["content"]
    assert "JSON" in sent[1]["messages"][0]["content"]          # OpenAI's json_object needs the word in the prompt
    assert msg["content"] == [{"type": "text", "text": '{"a": 1}'}]


def test_extract_json_leaves_strings_of_a_fenced_valid_object_alone():
    t = '```json\n{"notes": ["None of the pins, ] are NC"], "name": "True"}\n```'
    val, fixes = RL.extract_json(t)
    assert val == {"notes": ["None of the pins, ] are NC"], "name": "True"}, val
    assert fixes == ["code fence removed"]
    val, fixes = RL.extract_json('{"a": None, "b": [1, 2,],}')          # a real Python-ism is still repaired
    assert val == {"a": None, "b": [1, 2]} and "None -> null" in fixes


def test_worst_case_uses_haiku_long_prompt_price():
    """The max_cost_usd guard priced Claude Haiku 5.5 at the short-prompt rate above 100K tokens (5x under the ledger)."""
    worst = D.worst_usd("claude-haiku-5-5", 150_000, 16_000, D.CACHE_WRITE)
    billed = DR.usage_usd("claude-haiku-5-5", {"cache_creation_input_tokens": 150_000, "output_tokens": 16_000})
    assert abs(worst - billed) < 1e-9, (worst, billed)


def test_cached_small_model_pass_is_still_held():
    """visual_check: a small model's "pass" was downgraded to doubt, cached, and then replayed as a real pass."""
    from tests import test_visual_check as TV
    n = []

    class Cl:
        def messages(self, body, headers=None):
            n.append(1)
            return TV.message(TV.answer())
    r = RU.load({"ai": {"provider": "ollama", "model": "llava:7b", "detect": False}})
    with TV.setup() as d:
        a = V.prove(TV.item(d), d, r, client=Cl(), out_dir=d / "out")
        b = V.prove(TV.item(d), d, r, client=Cl(), out_dir=d / "out")
    assert len(n) == 1 and b["cached"]
    assert a["verdict"] == b["verdict"] == "doubt", (a["verdict"], b["verdict"])


def test_ai_api_key_env_for_claude_and_no_foreign_key():
    env = {"MY_CLAUDE_KEY": "mine", "ANTHROPIC_API_KEY": "other", "OPENAI_API_KEY": "sk-openai"}
    with mock.patch.dict(os.environ, env, clear=True):
        r = RU.load({"ai": {"api_key_env": "MY_CLAUDE_KEY"}})
        assert LL.endpoint(r)["key_env"] == "MY_CLAUDE_KEY" and LL.key_for(LL.endpoint(r)) == "mine"
        assert D.require_key(None, r) == "mine"
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai"}, clear=True):
        r = RU.load({"ai": {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"}})
        try:
            k = D.require_key(None, r)
        except D.DatasheetError:
            k = None
        assert k is None, "an OpenAI key would go to the Claude API"


def test_named_endpoint_sends_the_servers_model_id():
    """[datasheet] ladder = ["local:tiny-3b", ...]: the local server was asked for model "local:tiny-3b"."""
    from tests import test_llm as TL
    from tests import test_ds_route as TR

    def post(url, headers=None, json=None, timeout=None):
        if url.startswith("http://127.0.0.1"):
            return TL.REAL_POST(url, headers=headers, json=json, timeout=timeout)
        return TD.Resp(200, TD.message(TD.GOOD))
    with TL.FakeOpenAI(lambda body: TL.chat("no idea")) as srv, tempfile.TemporaryDirectory() as d, \
            mock.patch.object(D.requests, "post", post):
        rules = {"ai": {"endpoints": {"local": {"provider": "openai-compatible", "base_url": srv.base}}, "detect": False},
                 "datasheet": {"route": "ladder", "ladder": ["local:tiny-3b", "claude-opus-5-5"]}}
        rd = TR.reader(d, {"https://a/ds.pdf": TL._pdf2()}, rules=rules)
        rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
    sent = {r[3]["model"] for r in srv.reqs if r[0] == "POST"}
    assert sent == {"tiny-3b"}, sent
    cl = OC.OpenAICompatClient("http://localhost:11434/v1", None, {}, model="qwen2.5:7b")
    assert cl.to_chat({"model": "local:qwen2.5:7b", "messages": []})["model"] == "qwen2.5:7b"


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_review_full_llm"]))
