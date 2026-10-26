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
