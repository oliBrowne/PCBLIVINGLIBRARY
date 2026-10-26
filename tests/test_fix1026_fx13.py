"""Fixes 2026-10-26 (fx13): R-FX13-1..7 of the second PR #196 review (key names across providers, httpcache 404s,
outline package codes, viewer pages). Plain functions: python -m tests.run_all test_fix1026_fx13.
No network: nothing here talks to a model API or a web site."""
import os
from unittest import mock

from tests.fixtures_lite import lite

_ENV = {"OR_KEY": "sk-or-SECRET", "CLAUDE_KEY": "sk-ant-mine", "ECADLIB_NO_STORED_KEYS": "1"}
_CLEAR = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "MY_OPENAI")


def _env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in _CLEAR}
    env.update(_ENV)
    env.update(extra or {})
    return mock.patch.dict(os.environ, env, clear=True)


def _rules(user):
    import copy
    from ecadlib import rules as RU
    r = copy.deepcopy(RU.DEFAULTS)
    for s, v in user.items():
        if s == "agent":
            r["agent"]["proof"].update(v["proof"])
        else:
            r.setdefault(s, {})
            r[s].update(v)
    return r


# ---------------------------------------------------------------- R-FX13-1: eval --provider drops another provider's key
def test_eval_provider_anthropic_drops_the_datasheet_key_of_another_provider():
    from ecadlib import datasheet as D, llm as LL
    from ecadlib.eval.runner import ai_overrides
    r = _rules({"datasheet": {"provider": "openrouter", "model": "qwen/qwen3", "api_key_env": "OR_KEY"}})
    with _env():
        got = ai_overrides(r, provider="anthropic")
        ep = LL.endpoint(got, "datasheet")
        assert ep["provider"] == "anthropic" and ep["key_env"] != "OR_KEY", ep
        assert LL.key_for(ep) is None
        try:
            key = D.require_key(None, got)
        except D.DatasheetError:
            key = None
        assert key != "sk-or-SECRET", "the OpenRouter key went to Anthropic"
        # --api-key-env wins over [datasheet]'s key name
        got = ai_overrides(r, provider="anthropic", api_key_env="CLAUDE_KEY")
        assert D.require_key(None, got) == "sk-ant-mine"
        ep = LL.endpoint(got, "datasheet")
        assert ep["key_env"] == "CLAUDE_KEY" and LL.key_for(ep) == "sk-ant-mine", ep
        # --api-key-env with no --provider wins too
        got = ai_overrides(_rules({"datasheet": {"api_key_env": "OR_KEY"}}), api_key_env="CLAUDE_KEY")
        assert D.require_key(None, got) == "sk-ant-mine"
        # the same provider keeps the section's own key name
        got = ai_overrides(r, provider="openrouter", model="x/y")
        assert LL.endpoint(got, "datasheet")["key_env"] == "OR_KEY"
        mine = _rules({"datasheet": {"api_key_env": "CLAUDE_KEY"}})
        assert D.require_key(None, ai_overrides(mine, provider="anthropic")) == "sk-ant-mine"


# ---------------------------------------------------------------- R-FX13-2: the proof key comes from the proof endpoint
def test_visual_proof_on_anthropic_never_takes_the_datasheet_key_of_another_provider():
    from ecadlib import llm as LL, visual_check as V
    r = _rules({"datasheet": {"provider": "openrouter", "model": "qwen/qwen3", "api_key_env": "OR_KEY"},
                "agent": {"proof": {"provider": "anthropic", "model": "claude-opus-5-5"}}})
    with _env():
        ep = LL.endpoint(r, "proof")
        cfg = V._settings(r)
        assert ep["provider"] == "anthropic" and cfg["api_key_env"] == ep["key_env"] != "OR_KEY", cfg
    # the whole proof path: no Anthropic key -> skipped, nothing sent (the OpenRouter key never goes to Anthropic)
    from tests import test_visual_check as TV
    calls = []
    with TV.setup(key=None) as d, _env(), mock.patch.object(V.D.requests, "post", TV.fake_post(TV.message(TV.answer()), calls)):
        pr = V.prove(TV.item(d), d, r, out_dir=d / "out")
        assert pr["verdict"] == "skipped" and not calls, (pr["verdict"], pr["reason"],
                                                          [c["headers"].get("x-api-key") for c in calls])
        # with a Claude key, that key is the one sent
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real"
        V.prove(TV.item(d), d, r, out_dir=d / "out2")
        assert calls and all(c["headers"].get("x-api-key") == "sk-ant-real" for c in calls), calls[0]["headers"]
