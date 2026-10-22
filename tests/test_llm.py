"""Offline tests for ecadlib.llm: providers, capability profiles, guide rails.  python3 -m tests.test_llm

Nothing live: Claude answers come from fakes in the documented Messages API shape (as in test_datasheet_batch), and the
OpenAI-compatible side is a real local HTTP server (stdlib) that answers /v1/chat/completions and /v1/models in the
documented Chat Completions shapes. The extractions are test_datasheet's SYNTHETIC fixtures."""
import base64, copy, hashlib, io, json, os, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import requests

from ecadlib import crosscheck as CC, datasheet as D, ds_route as DR, llm as LL, rules as RU, visual_check as V
from ecadlib.llm import openai_compat as OC, profiles as PR, rails as RL, run as LR
from tests import test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR

KEY = TB.KEY
GOOD, BAD = TR.GOOD, TR.BAD
REAL_POST, REAL_GET = requests.post, requests.get


def h(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Request hashes captured from the code BEFORE ecadlib.llm existed (base 6894b8e, same scenarios as below), so a Claude
# request with the default settings is proven byte-identical to what was sent before.
# Updated once (review): the output schema's `tab` held a dangling "$ref": "#/$defs/dim" (no $defs in the request);
# it is now inlined. That is the only difference from the earlier hashes (checked by re-inserting the $ref).
# 2026-10-18: requests ask for wire_schema() (the API takes at most 16 union-typed fields)
# 2026-10-18 (#42): land gains ep_across / ep_along, the maker's thermal-pad land; nothing else changed.
# 2026-10-19 (#42): SYSTEM asks for a toleranced value (6.5±0.2) copied into nom; nothing else changed.
# 2026-10-21: land gains pin1_length / pin1_width, a pin 1 pad drawn another size; nothing else changed (checked with
# the earlier schema: the earlier hashes).
# 2026-10-22: land gains ep_mask_across / ep_mask_along, a solder-mask-defined thermal land (TI PowerPAD); nothing else
# changed (checked with the earlier schema: the earlier hashes).
GOLDEN = {"build_sync": "1137bd7b3e1a9f122117f3ab936a6b346f3b84b9f99d456e5c93e388053f77ef",
          "build_batch": "72f288a100fa6d1c89744915fa0d46b2ba1efe53f1ac92f90a0476ef5e3de1ff",
          "build_url_haiku": "f2c08c4829b0cc60ec24b55c12e90418b67130d8bcaf41d84c9d92378c2724e6",
          "reader_sync_calls": ["b37cc3db9f3941ba4b3396cd3817d7e0126fb738534f45c78fa06138eb541690",
                                "7d4783fd737357fbee90462479b2b4f7f1eee7bf28951607dedee5bdfcf5ffac"],
          "visual": "2617a48dcb947471108561ff7f8eabce190b1cb553e2d2c4cbf4ff3974a03181",
          "batch_posts": ["cf119f2291183d77100f9b67b5b5ef55c4f699633433f3beb772244b63373bf7",
                          "d7bb1e8fbb3a94ad1c897ef883f3dc43c61c01bde6b634a2149b243f66ceb969"]}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class LdoHTTP:
    offline = False

    def __init__(self, pdf):
        self.pdf = pdf

    def get_bytes(self, url):
        return 200, self.pdf, "application/pdf", url, None


def _claude_calls(rules):
    """The golden reader scenario: a 30-page two-package datasheet, single route, GOOD answers."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": {k: v for k, v in headers.items() if k != "x-api-key"}, "body": json})
        return TD.Resp(200, TD.message(TD.GOOD))
    pdf = TR.pdf_lines(TR.ldo_pages())
    with tempfile.TemporaryDirectory() as d, mock.patch.object(D.requests, "post", post):
        rd = D.Reader(Path(d) / "out", Path(d) / "cache", key="sk-test", rules=RU.load(rules, start=d), http=LdoHTTP(pdf), log=None)
        try:
            rd.read_now(rd.job("TESTLDO-H33TRG1", [("https://example.com/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"))
            raise AssertionError("the VQFN-16 answer for a SOT-223 part was accepted")
        except CC.Held as e:                          # [datasheet] cross_check = "auto": the standard SOT-223 has 3 pins;
            assert "pin count read 16, table 3" in str(e)   # an offline check, so no request is added
    return calls


def test_claude_requests_are_byte_identical_to_before():
    """Frontier Claude with no [ai] table, an empty one, or [ai] spelling out the defaults: the same bytes as before."""
    hd, b = D.build_request(("base64", TD.PDF), "TESTPART", "RGT", None, D.MODEL, "high", True, True)
    assert h([hd, b]) == GOLDEN["build_sync"]
    hd, b = D.build_request(("base64", TD.PDF), "TESTPART", "RGT", None, D.MODEL, "high", False, False)
    assert h([hd, b]) == GOLDEN["build_batch"]
    hd, b = D.build_request(("url", "https://example.com/x.pdf"), "TESTPART", None, (3, 4), "claude-haiku-4-5", "high", True, True)
    assert h([hd, b]) == GOLDEN["build_url_haiku"]
    for rules in (None, {"ai": {}}, {"ai": {"provider": "anthropic", "model": "claude-opus-5-5", "assist": "auto"}},
                  {"ai": {"assist": "off"}}):
        calls = _claude_calls(rules)
        assert [h(c) for c in calls] == GOLDEN["reader_sync_calls"], rules
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.png"
        p.write_bytes(PNG)
        hd, b = V.build_request([("Our footprint:", str(p))], [str(p)], [3], V.task_text("TESTPART"), D.MODEL, "high")
        assert h([hd, b]) == GOLDEN["visual"]
    api = TB.FakeAPI()
    pdf = TR.pdf_lines(TR.ldo_pages())
    with tempfile.TemporaryDirectory() as d, mock.patch.object(D.requests, "post", api.post), \
            mock.patch.object(D.requests, "get", api.get):
        rd = D.Reader(Path(d) / "out", Path(d) / "cache", key="sk-test", rules=RU.load(None, start=d), http=LdoHTTP(pdf),
                      log=None, batch=True, poll=0, sleep=lambda s: None)
        assert rd.batch                               # plain Claude keeps the Message Batch
        rd.submit(rd.job("TESTLDO-H33TRG1", [("https://example.com/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"))
        rd.flush()
    assert [h(c[3]) for c in api.calls if c[0] == "POST"] == GOLDEN["batch_posts"]


def test_claude_plan_is_support_only_and_assist_full_does_not_restrict_it():
    inf = LR.info("claude-opus-5-5", None)
    assert inf["plan"]["tier"] == "frontier" and not inf["plan"]["guided"] and LR.plain(inf)
    assert set(inf["plan"]["rails"]) == set(RL.SUPPORT)
    assert RL.signature(inf["plan"]) == ""            # cache keys unchanged
    full = RL.plan(PR.profile("claude-opus-5-5"), "full")
    assert "split_steps" in full["rails"] and full["guided"]   # assist = "full" is an explicit choice to guide even Claude
    off = RL.plan(PR.profile("tiny-3b", "openai-compatible"), "off")
    assert [k for k in off["rails"] if RL.RAILS[k][0] == "guidance"] == ["json_repair"]   # off: capability only
    assert "pdf_as_text" in off["rails"] and "schema_in_prompt" in off["rails"]


def test_claude_feedback_turn_is_append_only_and_counted():
    """A Claude answer that fails the checks where the route would stop gets ONE more turn: the same request, the
    answer, and the checks' exact errors. The first request is the one build_request makes."""
    api = TR.ModelAPI(lambda m, n: BAD if len(api.sent) <= 1 else GOOD)   # sent is logged before answering
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = TR.reader(d, {"https://a/ds.pdf": TD.PDF})
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        res = rd.read_now(j)
        posts = [c[3] for c in api.calls if c[0] == "POST"]
        assert len(posts) == 2 and h(posts[0]) == h(j["body"])          # the first request: exactly as built
        assert posts[1]["messages"][:1] == posts[0]["messages"] and posts[1]["system"] == posts[0]["system"]
        assert posts[1]["output_config"] == posts[0]["output_config"] and posts[1]["fallbacks"] == "default"
        asst, fb = posts[1]["messages"][1:]
        assert asst["role"] == "assistant" and asst["content"] == TD.message(BAD)["content"]   # thinking block unchanged
        assert fb["role"] == "user" and "self-check: name says pitch 0.5 mm" in fb["content"][0]["text"]
        assert "never change a value just to pass a check" in fb["content"][0]["text"]
        assert res["needs_review"] == [] and res["sidecar"]["ai"]["feedback"]["kept"] == "retry"
        led = TR.ledger(d)
        assert len(led) == 1 and led[0]["input_tokens"] == 2 * sum(TD.message(GOOD)["usage"][k] for k in
                                                                   ("input_tokens", "cache_creation_input_tokens"))
    api = TR.ModelAPI(lambda m, n: BAD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):                # feedback_retry = false: one request
        rd = TR.reader(d, {"https://a/ds.pdf": TD.PDF}, rules={"ai": {"feedback_retry": False}})
        rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        assert len(api.sent) == 1


# ---------------------------------------------------------------- config
def test_endpoint_resolution_and_keys():
    ep = LL.endpoint(None)
    assert (ep["provider"], ep["model"], ep["key_env"], ep["assist"]) == ("anthropic", "claude-opus-5-5", "ANTHROPIC_API_KEY", "auto")
    ep = LL.endpoint(RU.load({"datasheet": {"model": "claude-sonnet-5-5", "api_key_env": "MY_KEY"}}))
    assert (ep["model"], ep["key_env"]) == ("claude-sonnet-5-5", "MY_KEY")
    r = RU.load({"ai": {"provider": "ollama", "model": "qwen2.5:7b"}})
    ep = LL.endpoint(r)
    assert ep["base_url"] == "http://localhost:11434/v1" and ep["local"] and ep["model"] == "qwen2.5:7b"
    assert ep["key_env"] == "" and LL.key_for(ep, environ={}) == LL.NO_KEY
    assert LL.endpoint(r, model="claude-opus-5-5")["model"] == "claude-opus-5-5"
    r = RU.load({"ai": {"provider": "openrouter", "model": "openai/gpt-5"}})
    ep = LL.endpoint(r)
    assert ep["base_url"] == "https://openrouter.ai/api/v1" and ep["key_env"] == "OPENROUTER_API_KEY" and not ep["local"]
    assert LL.key_for(ep, environ={}) is None and LL.key_for(ep, environ={"OPENROUTER_API_KEY": "k"}) == "k"
    assert LL.key_for(ep, explicit=None, environ={"ANTHROPIC_API_KEY": "x"}) is None    # a Claude key never goes elsewhere
    assert "OPENROUTER_API_KEY is not set" in LL.needs_key_message(ep)
    r = RU.load({"ai": {"endpoints": {"local": {"provider": "lmstudio"}}},
                 "datasheet": {"route": "ladder", "ladder": ["local:qwen2.5-7b-instruct", "claude-opus-5-5"]}})
    ep = LL.endpoint(r, model="local:qwen2.5-7b-instruct")
    assert (ep["name"], ep["model"], ep["base_url"], ep["ref"]) == ("local", "qwen2.5-7b-instruct", "http://localhost:1234/v1",
                                                                    "local:qwen2.5-7b-instruct")
    assert LL.endpoint(r, model="claude-opus-5-5")["provider"] == "anthropic"
    # [agent.proof] falls back to [datasheet], which falls back to [ai]
    r = RU.load({"ai": {"provider": "openai", "model": "gpt-5"}, "agent": {"proof": {"model": "gpt-5-mini"}}})
    assert LL.endpoint(r, "proof")["model"] == "gpt-5-mini" and LL.endpoint(r)["model"] == "gpt-5"
    try:
        LL.endpoint(RU.load({"ai": {"provider": "openai"}}))
        raise AssertionError("no model for openai")
    except LL.AIConfigError as e:
        assert "set [ai] model" in str(e)


def test_rules_validation():
    bad = [({"ai": {"provider": "acme"}}, "ai.provider"), ({"ai": {"assist": "max"}}, "ai.assist"),
           ({"ai": {"api_key_env": "sk-live-123"}}, "NAME of an environment variable"),
           ({"ai": {"base_url": "http://api.example.com/v1"}}, "plain http://"),
           ({"ai": {"base_url": "ftp://x"}}, "http(s)://"),
           ({"ai": {"profiles": {"m": {"tier": "huge"}}}}, "frontier, mid or small"),
           ({"ai": {"profiles": {"m": {"context": "big"}}}}, "whole number"),
           ({"ai": {"endpoints": {"x": {"provider": "openai-compatible"}}}}, "base_url"),
           ({"ai": {"endpoints": {"x": {"colour": "red"}}}}, "not a known key"),
           ({"ai": {"feedback_retry": "yes"}}, "true or false"), ({"datasheet": {"assist": "lots"}}, "datasheet.assist"),
           ({"datasheet": {"ladder": ["bad model id!"]}}, "datasheet.ladder")]
    for raw, want in bad:
        try:
            RU.load(raw)
            raise AssertionError(f"accepted {raw}")
        except RU.RulesError as e:
            assert want in str(e), (raw, str(e))
    ok = RU.load({"ai": {"provider": "vllm", "model": "Qwen/Qwen2.5-72B-Instruct", "base_url": "http://10.0.0.5:8000/v1",
                         "profiles": {"Qwen/Qwen2.5-72B-Instruct": {"tier": "mid", "context": 32768}}},
                  "datasheet": {"route": "ladder", "ladder": ["Qwen/Qwen2.5-72B-Instruct", "claude-opus-5-5"]}})
    assert ok["ai"]["profiles"]["Qwen/Qwen2.5-72B-Instruct"]["tier"] == "mid"
    assert RU.load({})["ai"] == {**LL.DEFAULTS, "profiles": {}, "endpoints": {}}
    assert RU.load({})["datasheet"]["provider"] == "" and "[ai]" not in RU.dumps(RU.load({}))   # unchanged files stay


# ---------------------------------------------------------------- profiles
def test_profiles_table_override_and_detection():
    p = PR.profile("claude-opus-5-5")
    assert (p["tier"], p["pdf"], p["json_schema"]) == ("frontier", True, True)
    assert PR.profile("claude-haiku-4-5")["context"] == 200_000
    assert PR.profile("claude-some-future-model")["tier"] == "frontier"
    assert PR.profile("openai/gpt-5", "openai-compatible")["tier"] == "frontier"
    assert PR.profile("gpt-5-mini", "openai-compatible")["max_tokens_param"] == "max_completion_tokens"
    assert PR.profile("gpt-4o-mini", "openai-compatible")["tier"] == "small"
    assert PR.profile("gemini-2.5-flash", "openai-compatible")["tier"] == "mid"
    q = PR.profile("qwen2.5:7b", "openai-compatible")
    assert (q["tier"], q["vision"], q["pdf"]) == ("small", False, False)
    assert PR.profile("llama3.3:70b", "openai-compatible")["tier"] == "mid"
    assert PR.profile("qwen2.5vl:7b", "openai-compatible")["vision"] and PR.profile("llava:13b", "openai-compatible")["vision"]
    u = PR.profile("my-finetune", "openai-compatible")
    assert u == {**PR.CONSERVATIVE, "source": u["source"]} and "unknown" in u["source"]
    r = RU.load({"ai": {"profiles": {"my-finetune": {"tier": "mid", "vision": True, "context": 32768}}}})
    o = PR.profile("my-finetune", "openai-compatible", r)
    assert (o["tier"], o["vision"], o["context"]) == ("mid", True, 32768) and "ecadlib.toml" in o["source"]
    # what endpoints report: OpenRouter /models, vLLM /models, LM Studio /api/v0/models, Ollama /api/show
    orr = PR.from_models_entry({"id": "x/y", "context_length": 131072, "architecture": {"input_modalities": ["text", "image"]},
                                "supported_parameters": ["response_format", "structured_outputs"],
                                "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}})
    assert orr == {"context": 131072, "vision": True, "pdf": False, "json_schema": True, "price_in": 0.4, "price_out": 1.6}
    assert PR.from_models_entry({"id": "m", "max_model_len": 32768}) == {"context": 32768}
    assert PR.from_models_entry({"id": "m", "type": "vlm", "max_context_length": 8192}) == {"context": 8192, "vision": True}
    assert PR.from_ollama_show({"capabilities": ["completion", "vision"], "model_info": {"gemma3.context_length": 131072}}) == \
        {"vision": True, "context": 131072}
    d = PR.profile("qwen2.5:7b", "openai-compatible", r, {"context": 32768, "vision": True})
    assert d["context"] == 32768 and d["vision"] and "detected" in d["source"]


class FakeOpenAI:
    """A local /v1 server: answer(body) -> (status, payload); /v1/models lists `models`. Records every request."""

    def __init__(self, answer, models=None):
        self.answer, self.models, self.reqs = answer, models or [], []

    def __enter__(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, st, payload):
                data = json.dumps(payload).encode()
                self.send_response(st)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.reqs.append(("GET", self.path, dict(self.headers), None))
                if self.path == "/v1/models":
                    return self._send(200, {"object": "list", "data": fake.models})
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.reqs.append(("POST", self.path, dict(self.headers), body))
                if self.path != "/v1/chat/completions":
                    return self._send(404, {"error": {"message": "not found"}})
                st, payload = fake.answer(body)
                self._send(st, payload)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        self.env.start()
        return self

    def __exit__(self, *a):
        self.env.stop()
        self.srv.shutdown()

    def chats(self):
        return [r[3] for r in self.reqs if r[0] == "POST"]


def chat(text, finish="stop", refusal=None, model="m"):
    return 200, {"id": "chatcmpl-1", "object": "chat.completion", "model": model,
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": text, "refusal": refusal},
                              "finish_reason": finish}],
                 "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500}}


def test_openai_compatible_translation_and_fallback():
    seen = []

    def answer(body):
        seen.append(body)
        if body.get("response_format", {}).get("type") == "json_schema" and len(seen) == 1:
            return 400, {"error": {"message": "response_format json_schema is not supported", "type": "invalid_request_error"}}
        return chat('{"ok": true}', model="served-model")
    with FakeOpenAI(answer) as srv:
        cl = OC.OpenAICompatClient(srv.base, "sk-or-test", {"pdf": True, "json_schema": True,
                                                            "max_tokens_param": "max_completion_tokens"})
        body = {"model": "m", "max_tokens": 500, "system": [{"type": "text", "text": "SYS"}],
                "messages": [{"role": "user", "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "UERG"},
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aW1n"}},
                    {"type": "text", "text": "TASK"}]}],
                "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}},
                "fallbacks": "default"}
        msg = cl.messages(body)
        auth = [r[2].get("Authorization") or r[2].get("authorization") for r in srv.reqs]
    first, second = seen
    assert first["messages"][0] == {"role": "system", "content": "SYS"}
    parts = first["messages"][1]["content"]
    assert parts[0] == {"type": "file", "file": {"filename": "datasheet.pdf", "file_data": "data:application/pdf;base64,UERG"}}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}} and parts[2]["text"] == "TASK"
    assert first["max_completion_tokens"] == 500 and "max_tokens" not in first
    assert first["response_format"]["json_schema"]["strict"] is True and "fallbacks" not in first and "effort" not in json.dumps(first)
    assert second["response_format"] == {"type": "json_object"}              # the server said no to json_schema
    assert msg["content"] == [{"type": "text", "text": '{"ok": true}'}] and msg["stop_reason"] == "end_turn"
    assert msg["model"] == "served-model" and msg["usage"] == {"input_tokens": 1200, "cache_read_input_tokens": 0, "output_tokens": 300}
    assert auth == ["Bearer sk-or-test"] * 2
    m = OC.OpenAICompatClient.to_message({"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}, "m")
    assert m["stop_reason"] == "max_tokens"
    m = OC.OpenAICompatClient.to_message({"choices": [{"message": {"content": None, "refusal": "no"}, "finish_reason": "stop"}]}, "m")
    assert m["stop_reason"] == "refusal"
    try:                                               # a model without PDF input never gets the PDF silently dropped
        OC.OpenAICompatClient("http://127.0.0.1:9/v1", None, {"pdf": False}).to_chat(body)
        raise AssertionError("PDF sent to a text-only model")
    except D.DatasheetError as e:
        assert "does not take PDF input" in str(e)


def test_json_repair_and_coercion():
    schema = D.output_schema()
    text = "Here you go:\n```json\n" + json.dumps(GOOD).replace('"unit": "mm"', '"unit": "Millimeters"', 1) \
        .replace('"nom": 0.5,', '"nom": "0.50 mm",', 1)[:-1] + ",}\n```"
    val, fixes, err = RL.repair(text, schema)
    assert err is None and val["package"]["pitch"]["unit"] == "mm" and val["package"]["pitch"]["nom"] == 0.5
    assert "code fence removed" in fixes and "trailing commas" in fixes
    assert any("'Millimeters' -> 'mm'" in f for f in fixes) and any("'0.50 mm' -> 0.5" in f for f in fixes)
    pins = {"type": "array", "items": schema["properties"]["pins"]["items"]}
    v = RL.coerce([{"num": 1, "name": "GND", "type": "Ground", "side": "LEFT", "page": "3"}], pins, [])
    assert v == [{"num": "1", "name": "GND", "type": "ground", "side": "left", "page": 3}]
    assert RL.coerce({"num": "1"}, schema["properties"]["pins"]["items"], [])["num"] == "1"
    try:
        RL.extract_json("the pitch is 0.5 mm")
        raise AssertionError
    except ValueError:
        pass


# ---------------------------------------------------------------- a small model through the steps
PDF2 = None


def _pdf2():
    global PDF2
    if PDF2 is None:
        PDF2 = TR.pdf_lines([["TESTPART 3 A buck converter", "Pin Functions", "Pin No. Name Type", "1 SW O", "4 PG OD"],
                             ["Package outline RGT0016C VQFN", "Dim Min Nom Max", "D 2.90 3.00 3.10", "e 0.50 BSC"]])
    return PDF2


IDENT = {"package_type": "qfn", "name": GOOD["package"]["name"], "drawing": "RGT0016C", "pin_count": 16, "exposed_pad": True,
         "drawing_page": 2, "pin_table_page": 1, "description": "VQFN-16", "symbol_description": "Synthetic buck, VQFN-16"}
DIMS = {k: GOOD["package"][k] for k in ("body_length", "body_width", "height_max", "pitch", "terminal_length",
                                        "terminal_width", "pins_left_right", "pins_top_bottom", "exposed_pad")}
DIMS["unresolved"] = []
PINS = {"pin_table": GOOD["pin_table"], "pins": GOOD["pins"], "notes": [], "embedded_instructions": []}


def step_of(body):
    t = body["messages"][1]["content"][-1]["text"] if isinstance(body["messages"][1]["content"], list) else body["messages"][1]["content"]
    return "identify" if "Which package" in t else "dims" if "dimension table" in t else "pins" if "pin configuration" in t else "?"


def small_rules(base, **ai):
    return {"ai": {"provider": "openai-compatible", "base_url": base, "model": "tiny-3b", "detect": False, **ai}}


def test_small_model_runs_the_steps_repairs_and_retries():
    def answer(body):
        st = step_of(body)
        n = sum(step_of(b) == st for b in srv.chats())
        if st == "identify":                           # a code fence with a trailing comma: repaired
            return chat("Sure!\n```json\n" + json.dumps(IDENT)[:-1] + ",}\n```")
        if st == "dims" and n == 1:                    # not JSON at all: retried with the exact error
            return chat("I think the pitch is 0.5 mm.")
        if st == "dims":
            d = copy.deepcopy(DIMS)
            d["pitch"]["unit"], d["pitch"]["nom"] = "millimeters", "0.50"      # unit synonym and a number as text
            return chat(json.dumps(d))
        return chat(json.dumps(PINS))
    with FakeOpenAI(answer) as srv, tempfile.TemporaryDirectory() as d:
        rd = TR.reader(d, {"https://a/ds.pdf": _pdf2()}, rules=small_rules(srv.base), batch=True)
        assert not rd.batch and rd.key == LL.NO_KEY    # not a Message Batch; a local server needs no key
        res = rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")]))
        chats = srv.chats()
        heads = [r[2] for r in srv.reqs]
    assert [step_of(b) for b in chats] == ["identify", "dims", "dims", "pins"]
    assert not any("authorization" in {k.lower() for k in hh} for hh in heads)                # no key sent
    blob = json.dumps(chats)
    assert '"type": "file"' not in blob and "=== PDF page 2 ===" in blob                   # page text, not the PDF
    assert "Package outline RGT0016C" in json.dumps(chats[1]) and "Pin Functions" not in json.dumps(chats[1])  # only its page
    assert "no JSON object in the answer" in json.dumps(chats[2]["messages"][-1])           # the exact error, fed back
    assert all(b["response_format"] == {"type": "json_object"} for b in chats)              # no schema support assumed
    assert "JSON schema" in chats[0]["messages"][0]["content"] and "Worked example" in chats[0]["messages"][0]["content"]
    assert res["needs_review"] == [] and res["spec"]["pitch"] == 0.5
    ai = res["sidecar"]["ai"]
    assert ai["tier"] == "small" and "split_steps" in ai["rails"] and [s["step"] for s in ai["steps"]] == ["identify", "dims", "pins"]
    assert ai["steps"][0]["attempts"][0]["fixes"] and ai["steps"][1]["attempts"][0]["error"]


def test_small_model_holds_on_doubt_after_feeding_the_errors_back():
    def answer(body):
        st = step_of(body)
        if st == "identify":
            return chat(json.dumps(IDENT))
        if st == "dims":                               # leads wider than the pitch, every time
            d = copy.deepcopy(DIMS)
            d["terminal_width"] = TD.dim("b", 0.4, 0.5, 0.6, page=2)
            return chat(json.dumps(d))
        return chat(json.dumps(PINS))
    with FakeOpenAI(answer) as srv, tempfile.TemporaryDirectory() as d:
        rd = TR.reader(d, {"https://a/ds.pdf": _pdf2()}, rules=small_rules(srv.base))
        try:
            rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")]))
            raise AssertionError("a doubtful answer was accepted")
        except D.DatasheetError as e:
            assert str(e).startswith("held: the answer of tiny-3b (small tier) still fails") and "leads would touch" in str(e)
        steps = [step_of(b) for b in srv.chats()]
        redo = srv.chats()[-1]
        written = list((Path(d) / "out" / "datasheet").glob("*.json"))
    assert steps == ["identify", "dims", "pins", "dims"]                                    # the dims step, redone once
    assert "leads would touch" in json.dumps(redo["messages"][-1])
    assert not [f for f in written if f.name.startswith("TESTPART")]                      # nothing written: held


def test_mid_model_single_call_with_pdf_examples_repair_and_feedback():
    def answer(body):
        n = len(srv.chats())
        ext = BAD if n == 1 else GOOD
        return chat("```json\n" + json.dumps(D.to_wire(ext)) + "\n```")   # answers the schema it was sent
    with FakeOpenAI(answer) as srv, tempfile.TemporaryDirectory() as d:
        rd = TR.reader(d, {"https://a/ds.pdf": TD.PDF}, rules=small_rules(srv.base, model="gpt-4o"))
        res = rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        a, b = srv.chats()
    assert a["messages"][1]["content"][0]["type"] == "file"                                 # mid tier with PDF input: the PDF
    assert "Worked example" in a["messages"][0]["content"] and a["response_format"]["type"] == "json_schema"
    assert b["messages"][:2] == a["messages"][:2] and b["messages"][2]["role"] == "assistant"
    assert "self-check: name says pitch 0.5 mm" in b["messages"][3]["content"][0]["text"]
    assert res["needs_review"] == [] and res["sidecar"]["ai"]["tier"] == "mid"
    assert res["sidecar"]["ai"]["feedback"]["kept"] == "retry" and "json_repair" in res["sidecar"]["ai"]["rails"]


def test_mixed_ladder_local_first_then_claude():
    """[datasheet] ladder = local small model, then Claude: the local answer that can't be used escalates to Claude."""
    claude = []

    def post(url, headers=None, json=None, timeout=None):
        if url.startswith("http://127.0.0.1"):
            return REAL_POST(url, headers=headers, json=json, timeout=timeout)
        claude.append((url, headers, json))
        return TD.Resp(200, TD.message(GOOD))
    with FakeOpenAI(lambda body: chat("no idea")) as srv, tempfile.TemporaryDirectory() as d, \
            mock.patch.object(D.requests, "post", post):
        rules = {"ai": {"endpoints": {"local": {"provider": "openai-compatible", "base_url": srv.base}}, "detect": False},
                 "datasheet": {"route": "ladder", "ladder": ["local:tiny-3b", "claude-opus-5-5"]}}
        rd = TR.reader(d, {"https://a/ds.pdf": _pdf2()}, rules=rules, batch=True)
        assert not rd.batch                            # a ladder with a non-Claude rung reads synchronously
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        assert j["model"] == "local:tiny-3b"
        res = rd.read_now(j)
        led = TR.ledger(d)
    assert len(claude) == 1 and claude[0][0] == "https://api.anthropic.com/v1/messages"
    assert claude[0][2]["model"] == "claude-opus-5-5" and claude[0][2]["messages"][0]["content"][0]["type"] == "document"
    assert claude[0][1]["x-api-key"] == KEY
    assert all("x-api-key" not in r[2] and "X-Api-Key" not in r[2] for r in srv.reqs)
    assert res["sidecar"]["model"] == "claude-opus-5-5" and res["needs_review"] == []
    assert [(a["model"], a["verdict"]) for a in res["sidecar"]["route"]["attempts"]] == [("local:tiny-3b", "failed"),
                                                                                      ("claude-opus-5-5", "ok")]
    assert led[0]["model"] == "local:tiny-3b" and led[0]["usd"] == 0.0, led[0]
    assert led[0]["next"] == "claude-opus-5-5" and "identify step" in led[0]["error"]  # and led[0]["next"] == "claude-opus-5-5"
    assert "escalation" in res["sidecar"]["ai"]["support_ran"]


def test_detection_from_the_endpoint_and_local_price():
    models = [{"id": "big-local", "object": "model", "max_model_len": 65536}]
    with FakeOpenAI(lambda b: chat("{}"), models=models) as srv:
        PR._DETECTED.clear()
        r = RU.load({"ai": {"provider": "vllm", "base_url": srv.base, "model": "big-local"}})
        inf = LR.info("big-local", r)
        assert inf["prof"]["context"] == 65536 and "/models" in inf["prof"]["detect"]
        assert LL.price("big-local") == (0.0, 0.0) and D.price_of("big-local") == (0.0, 0.0)
    assert LL.price("claude-opus-5-5") == D.PRICES["claude-opus-5-5"]
    assert D.price_of("never-seen-model") is None


def test_visual_check_needs_a_vision_model_and_doctor_shows_the_setup():
    from ecadlib import doctor as DOC
    r = RU.load({"ai": {"provider": "ollama", "model": "qwen2.5:7b"}})
    inf = LR.info("qwen2.5:7b", r, "proof")
    assert not inf["prof"]["vision"]
    lines = DOC.check_ai(r, environ={})
    assert lines[0]["level"] == "OK" and "qwen2.5:7b via ollama" in lines[0]["detail"] and "small tier" in lines[0]["detail"]
    assert lines[1]["detail"].startswith("none needed")
    lines = DOC.check_ai(RU.load({"ai": {"provider": "openai", "model": "gpt-5"}}), environ={})
    assert lines[1]["level"] == "WARN" and "OPENAI_API_KEY not set" in lines[1]["detail"]
    assert DOC.check_ai(RU.load({}), environ={})[0]["detail"].startswith("claude-opus-5-5 via anthropic")
    assert [k for k, *_ in DOC.key_services(RU.load({"ai": {"provider": "ollama", "model": "q:7b"}}))][0] != "anthropic"
    fp = Path(__file__).parent / "fixtures" / "kicad" / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod"
    with tempfile.TemporaryDirectory() as d:
        ds = Path(d) / "ds.pdf"
        ds.write_bytes(TR.pdf_lines([["Package outline"]]))
        pr = V.prove({"part": "X", "footprint": str(fp), "datasheet": str(ds)}, workspace=d, rules=r, out_dir=Path(d) / "p")
    assert pr["verdict"] == "skipped" and "takes no images" in pr["reason"], pr


def test_ai_cli_and_prefer_standard():
    from ecadlib.llm import cli as AC
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ecadlib_rules.json"
        f.write_text(json.dumps({"ai": {"provider": "ollama", "model": "qwen2.5:7b"}}))
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            assert AC.main(["show", "--rules", str(f), "--json"]) == 0
        rep = json.loads(buf.getvalue())
    assert rep["plan"]["tier"] == "small" and "split_steps" in rep["layers"]["guidance"] and rep["endpoint"]["key"] == "none needed"
    assert LR.wants(RU.load({"ai": {"provider": "ollama", "model": "qwen2.5:7b"}}), "prefer_standard")
    assert not LR.wants(RU.load({}), "prefer_standard")


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_llm"]))


def test_eval_compares_providers_with_record_and_replay():
    """`ecadlib eval run --provider ... --model ...` records a non-Claude model's answers and replays them offline."""
    from ecadlib.eval import runner as ER
    from tests import test_eval as TE

    def answer(body):
        task = json.dumps(body["messages"][-1])
        return chat(json.dumps(D.to_wire(TD.INCH if "SOICPART" in task else TE.qfn_ext())), model="gpt-4o-2024-11-20")
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    with FakeOpenAI(answer) as srv, tempfile.TemporaryDirectory() as d:
        rep = ER.run(TE.mini_set(d), Path(d) / "live", cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None,
                     record=Path(d) / "rec", model="gpt-4o", provider="openai-compatible", base_url=srv.base)
        n = len(srv.chats())
        assert rep["meta"]["provider"] == "openai-compatible" and rep["meta"]["tier"] == "mid" and rep["meta"]["model"] == "gpt-4o"
        # 2 reads + QFNPART's cross-check read (its package is not in the standard table), replayed from the recording
        assert n == 3 and all(c["source"] == "datasheet" and c["pass"] for c in rep["cases"]), [c["errors"] for c in rep["cases"]]
        assert all(c["usd"] == 0.0 for c in rep["cases"])                     # a local server costs nothing
        rep2 = ER.run(TE.mini_set(d), Path(d) / "replayed", cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None,
                      replay=Path(d) / "rec", model="gpt-4o", provider="openai-compatible", base_url=srv.base)
        assert len(srv.chats()) == n and [c["pass"] for c in rep2["cases"]] == [c["pass"] for c in rep["cases"]]
        txt = ER.compare(Path(d) / "live", Path(d) / "replayed")["text"]
        assert "gpt-4o via openai-compatible" in txt
        from ecadlib.eval import cli as EC                 # `ecadlib eval compare A B` printed nothing but a crash before
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            assert EC.main(["compare", str(Path(d) / "live"), str(Path(d) / "replayed")]) == 0
        assert "gpt-4o via openai-compatible" in buf.getvalue()
