"""Fixes 2026-10-26 (llmeval): R-LE-1..6 of the PR #196 review (llm endpoint keys, eval gate, lifecycle, cached-only).
Plain functions: python -m tests.run_all test_fix1026_llmeval. No network: nothing here talks to a model API."""
import os
from unittest import mock

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-LE-1: a key never goes to another provider
_ENV = {"OPENAI_API_KEY": "sk-openai-SECRET", "OPENROUTER_API_KEY": "sk-or-OTHER", "GEMINI_API_KEY": "g-key",
        "MY_CLAUDE_KEY": "mine", "ANTHROPIC_API_KEY": "ant"}


def _ep(rules, section="datasheet"):
    from ecadlib import llm as L
    with mock.patch.dict(os.environ, {"ECADLIB_NO_STORED_KEYS": "1"}):
        ep = L.endpoint(rules, section)
        return ep, L.key_for(ep, environ=_ENV)


def test_section_provider_does_not_inherit_the_ai_key_env():
    ai = {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"}
    ep, key = _ep({"ai": ai, "datasheet": {"provider": "openrouter", "model": "qwen/qwen3"}})
    assert ep["base_url"] == "https://openrouter.ai/api/v1"
    assert ep["key_env"] == "OPENROUTER_API_KEY" and key == "sk-or-OTHER", ep
    ep, key = _ep({"ai": ai, "agent": {"proof": {"provider": "gemini", "model": "gemini-2.5-pro"}}}, "proof")
    assert "googleapis" in ep["base_url"] and ep["key_env"] == "GEMINI_API_KEY" and key == "g-key", ep
    # [agent.proof] with its own provider does not take [datasheet]'s key name or server either
    ep, key = _ep({"datasheet": {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"},
                   "agent": {"proof": {"provider": "gemini", "model": "gemini-2.5-pro"}}}, "proof")
    assert "googleapis" in ep["base_url"] and ep["key_env"] == "GEMINI_API_KEY", ep
    # a Claude key named in [ai] does not go to OpenRouter either
    ep, key = _ep({"ai": {"api_key_env": "MY_CLAUDE_KEY"}, "datasheet": {"provider": "openrouter", "model": "x/y"}})
    assert ep["key_env"] == "OPENROUTER_API_KEY" and key == "sk-or-OTHER", ep
    # an openai-compatible section on another server than [ai]'s: no inherited key (none sent, never [ai]'s)
    ep, key = _ep({"ai": {"provider": "openai-compatible", "base_url": "https://a.example/v1", "model": "m",
                          "api_key_env": "OPENAI_API_KEY"},
                   "datasheet": {"provider": "openai-compatible", "base_url": "https://b.example/v1"}})
    assert ep["key_env"] != "OPENAI_API_KEY" and key != "sk-openai-SECRET", ep


def test_missing_key_of_the_other_provider_is_refused_not_replaced():
    from ecadlib import llm as L
    rules = {"ai": {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"},
             "datasheet": {"provider": "openrouter", "model": "qwen/qwen3"}}
    with mock.patch.dict(os.environ, {"ECADLIB_NO_STORED_KEYS": "1"}):
        ep = L.endpoint(rules)
        assert L.key_for(ep, environ={"OPENAI_API_KEY": "sk-openai-SECRET"}) is None
    assert "OPENROUTER_API_KEY" in L.needs_key_message(ep)


def test_same_provider_still_inherits_the_ai_key_env():
    ai = {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY"}
    ep, key = _ep({"ai": ai, "datasheet": {"provider": "openai", "model": "gpt-5-mini"}})
    assert ep["key_env"] == "OPENAI_API_KEY" and key == "sk-openai-SECRET"
    ep, key = _ep({"ai": ai, "datasheet": {"model": "gpt-5-mini"}})            # no own provider: [ai]'s
    assert ep["key_env"] == "OPENAI_API_KEY" and key == "sk-openai-SECRET"
    ep, key = _ep({"ai": {"api_key_env": "MY_CLAUDE_KEY"}, "datasheet": {"provider": "anthropic"}})
    assert ep["key_env"] == "MY_CLAUDE_KEY" and key == "mine"
    # a section's own api_key_env always wins
    ep, key = _ep({"ai": ai, "datasheet": {"provider": "openrouter", "model": "x/y", "api_key_env": "OPENAI_API_KEY"}})
    assert ep["key_env"] == "OPENAI_API_KEY"


def test_eval_provider_switch_drops_the_ai_key_env_and_base_url():
    from ecadlib.eval import runner as R
    rules = {"ai": {"provider": "openai", "model": "gpt-5", "api_key_env": "OPENAI_API_KEY",
                    "base_url": "https://api.openai.com/v1"}, "datasheet": {"api_key_env": "ANTHROPIC_API_KEY"}}
    got = R.ai_overrides(rules, provider="openrouter", model="x/y")
    ep, key = _ep(got)
    assert ep["base_url"] == "https://openrouter.ai/api/v1" and ep["key_env"] == "OPENROUTER_API_KEY", ep
    assert key == "sk-or-OTHER"
    got = R.ai_overrides(rules, provider="openrouter", model="x/y", api_key_env="MY_ROUTER")
    assert _ep(got)[0]["key_env"] == "MY_ROUTER"
    got = R.ai_overrides(rules, provider="openai", model="gpt-5-mini")        # same provider: keeps its key
    assert _ep(got)[0]["key_env"] == "OPENAI_API_KEY"


# ---------------------------------------------------------------- R-LE-2: the gate fails on any built-wrong case
def _row(i, fam, ok, source):
    return {"id": i, "family": fam, "pass": ok, "source": source, "fields": {}, "usd": 0, "tokens_in": 0,
            "tokens_out": 0, "errors": [] if ok else ["pads: 6 of 28 pads off"], "tried": ["datasheet"]}


def _rep(rows):
    from ecadlib.eval import runner as R
    return {"meta": {"set": "t", "set_version": 1, "sources": [], "wall_s": 0}, "cases": rows,
            "summary": R.summarize(rows)}


def test_gate_fails_when_a_held_failing_case_becomes_built_wrong():
    from ecadlib.eval import gate as G
    held = _rep([_row("a", "qfn", True, "standard"), _row("b", "qfn", False, "none")])
    base = G.baseline(held)
    assert G.check(held, base)["ok"]                                       # held: the safe outcome, passes
    wrong = _rep([_row("a", "qfn", True, "standard"), _row("b", "qfn", False, "datasheet")])
    assert wrong["summary"]["outcome"]["built_wrong"] == ["b"]
    res = G.check(wrong, base)
    assert not res["ok"] and any("built wrong" in p and "b" in p for p in res["problems"]), res
    assert not G.check(wrong, base, subset=True)["ok"]


# ---------------------------------------------------------------- R-LE-3: lifecycle write-back never weakens A365
class _A365:
    def __init__(self, value, maker="Texas Instruments"):
        self.value, self.maker, self.sent = value, maker, []

    def gql(self, q, v):
        return {"desLibrary": {"components": {"nodes": [{"id": "cid1", "name": v["name"], "isManaged": True,
                "details": {"parameters": [{"name": "Lifecycle", "value": self.value},
                                           {"name": "Manufacturer", "value": self.maker}]}}]}}}

    def update_params(self, cid, params, note, replace):
        self.sent.append(params)
        return "ok"


def _mouser_rows(status, maker, a365_value):
    from ecadlib import lifecycle as LC
    part = LC._part("MC1458P", "Texas Instruments", name="MC1458P", source="a365", component_id="cid1",
                    a365_value=a365_value)
    data = {"Errors": [], "SearchResults": {"Parts": [
        {"ManufacturerPartNumber": "MC1458P", "Manufacturer": maker, "LifecycleStatus": status,
         "AvailabilityInStock": "1200", "ProductDetailUrl": "u"}]}}
    r = LC.parse_mouser(data, part, LC.LifecycleResult("mouser", mpn="MC1458P"))
    return LC.diff({"parts": {}}, {"MC1458P": LC.combine(part, [r])})


def test_a365_writeback_does_not_take_a_blank_status_from_another_maker():
    from ecadlib import lifecycle as LC
    c = _A365("Obsolete")
    up = LC.a365_updates(_mouser_rows("", "Shenzhen Foo Semi", "Obsolete"), c, "ws", apply=True)
    assert c.sent == [] and up[0]["action"] == "skip", up
    c = _A365("")                                       # nothing there yet: still no status made up from a blank
    up = LC.a365_updates(_mouser_rows("", "Texas Instruments", ""), c, "ws", apply=True)
    assert c.sent == [] and up[0]["action"] == "skip", up
    c = _A365("")                                       # another maker's stated status is not this part's either
    up = LC.a365_updates(_mouser_rows("Obsolete", "Shenzhen Foo Semi", ""), c, "ws", apply=True)
    assert c.sent == [] and up[0]["action"] == "skip", up


def test_a365_writeback_never_lowers_severity():
    from ecadlib import lifecycle as LC
    c = _A365("Obsolete")
    up = LC.a365_updates(_mouser_rows("Active", "Texas Instruments", "Obsolete"), c, "ws", apply=True)
    assert c.sent == [] and up[0]["action"] == "skip" and "lower" in up[0]["detail"], up
    up = LC.a365_updates(_mouser_rows("Active", "Texas Instruments", "Obsolete"), None, "ws")   # dry run too
    assert up[0]["action"] == "skip", up
    c = _A365("Active")                                 # a firm, stricter status still goes up
    up = LC.a365_updates(_mouser_rows("Obsolete", "Texas Instruments", "Active"), c, "ws", apply=True)
    assert up[0]["action"] == "updated" and {"name": "Lifecycle", "value": "Obsolete"} in c.sent[0], (up, c.sent)
    c = _A365("")                                       # an empty value is filled from a firm status
    up = LC.a365_updates(_mouser_rows("Active", "Texas Instruments", ""), c, "ws", apply=True)
    assert up[0]["action"] == "updated", up


# ---------------------------------------------------------------- R-LE-4: --cached-only scores the asked model's answers
@lite
def test_cached_only_takes_only_the_runs_own_models_answers(tmp_path):
    import types
    from ecadlib import datasheet as D
    from ecadlib.eval import runner as R
    rc = D.ResultCache(tmp_path)
    src = "ab" * 32
    k_haiku = D.ResultCache.key(src, "claude-haiku-5-5", "X1", None, [0, None], "high")
    k_opus = D.ResultCache.key(src, "claude-opus-5-5", "X1", None, [0, None], "high")
    rc.put(k_haiku + "OLD", {"key": k_haiku, "part": "X1", "model": "claude-haiku-5-5", "message": {"m": "haiku"},
                             "at": "2026-10-20"})
    rc.put(k_opus, {"key": k_opus, "part": "X1", "model": "claude-opus-5-5", "message": {"m": "opus"},
                    "at": "2026-10-19"})
    judge = lambda job, rec: ("ok" if rec["message"]["m"] == "opus" else "failed", [], None)
    job = {"key": "ab" * 8 + "-claude-haiku-5-5-NEWSCHEMA-123", "part": "X1", "offset": 0, "rung": 0,
           "model": "claude-haiku-5-5"}
    fake = types.SimpleNamespace(results=rc, _judge=judge, model="claude-haiku-5-5",
                                 router=types.SimpleNamespace(ladder=["claude-haiku-5-5"]))
    got = R.EvalReader._saved_answer(fake, job)
    assert got and got["model"] == "claude-haiku-5-5", got          # not the better-scoring opus answer
    # the rescore kit: an opus run reads the opus answers saved under an older schema key
    job_o = dict(job, key="ab" * 8 + "-claude-opus-5-5-NEWSCHEMA-123", model="claude-opus-5-5")
    fake_o = types.SimpleNamespace(results=rc, _judge=judge, model="claude-opus-5-5",
                                   router=types.SimpleNamespace(ladder=["claude-opus-5-5"]))
    assert R.EvalReader._saved_answer(fake_o, job_o)["model"] == "claude-opus-5-5"
    # a ladder run may score any of its own rungs' answers, still never another model's
    fake_l = types.SimpleNamespace(results=rc, _judge=judge, model="claude-haiku-5-5",
                                   router=types.SimpleNamespace(ladder=["claude-haiku-5-5", "claude-opus-5-5"]))
    assert R.EvalReader._saved_answer(fake_l, job)["model"] == "claude-opus-5-5"
    fake_s = types.SimpleNamespace(results=rc, _judge=judge, model="claude-sonnet-5-5",
                                   router=types.SimpleNamespace(ladder=["claude-sonnet-5-5"]))
    assert R.EvalReader._saved_answer(fake_s, dict(job, model="claude-sonnet-5-5")) is None
