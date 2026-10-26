"""Fixes 2026-10-26 (fx16): R-FX16-1..5 of the PR #196 re-review (a section's key name never goes to another server,
embedded PDFs never beat the datasheet link, package codes at the end of the first segment, easyeda module 404s).
Plain functions: python -m tests.run_all test_fix1026_fx16. No network: nothing here talks to a model API or a web site."""
import os
from unittest import mock

from tests.fixtures_lite import lite

_KEYS = {"GW1_KEY": "sk-gw1-secret", "GW2_KEY": "sk-gw2", "PROXY_KEY": "sk-proxy-secret", "OPENAI_API_KEY": "sk-openai",
         "OR_KEY": "sk-or-secret", "ECADLIB_NO_STORED_KEYS": "1"}
_CLEAR = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY")
GW0, GW1, GW2 = "https://gw0.example.org/v1", "https://gw1.example.com/v1", "https://gw2.example.net/v1"
PROXY = "https://openai-proxy.corp.example/v1"


def _env():
    env = {k: v for k, v in os.environ.items() if k not in _CLEAR}
    env.update(_KEYS)
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


# ---------------------------------------------------------------- R-FX16-1: eval overrides keep a key only for its server
def _eval_key(rules, **kw):
    from ecadlib import llm as LL
    from ecadlib.eval.runner import ai_overrides
    ep = LL.endpoint(ai_overrides(rules, **kw), "datasheet")
    return ep["base_url"], LL.key_for(ep)


def test_eval_override_never_sends_the_datasheet_key_to_another_server():
    ds_gw1 = {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}
    with _env():
        # --base-url gw2: GW1's key never goes to gw2
        base, key = _eval_key(_rules({"datasheet": ds_gw1}), provider="openai-compatible", base_url=GW2, model="m")
        assert base == GW2.rstrip("/") and key != "sk-gw1-secret", (base, key)
        # [ai] on gw0: GW1's key never goes to gw0
        r = _rules({"ai": {"provider": "openai-compatible", "base_url": GW0, "model": "m"}, "datasheet": ds_gw1})
        base, key = _eval_key(r, provider="openai-compatible")
        assert base == GW0 and key != "sk-gw1-secret", (base, key)
        # a corporate proxy key never goes to api.openai.com
        r = _rules({"datasheet": {"provider": "openai", "base_url": PROXY, "model": "gpt-5", "api_key_env": "PROXY_KEY"}})
        base, key = _eval_key(r, provider="openai", model="gpt-5")
        assert base == "https://api.openai.com/v1" and key == "sk-openai", (base, key)


def test_eval_override_keeps_the_datasheet_key_on_its_own_server():
    with _env():
        # the same gateway again (written out with a trailing slash): its key stays
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}})
        assert _eval_key(r, provider="openai-compatible", base_url=GW1 + "/", model="m")[1] == "sk-gw1-secret"
        # [ai] already on gw1, [datasheet] only names the key: the same server
        r = _rules({"ai": {"provider": "openai-compatible", "base_url": GW1, "model": "m"},
                    "datasheet": {"api_key_env": "GW1_KEY"}})
        assert _eval_key(r, provider="openai-compatible") == (GW1, "sk-gw1-secret")
        # a preset to the same preset (default server both times)
        r = _rules({"datasheet": {"provider": "openrouter", "model": "q/q", "api_key_env": "OR_KEY"}})
        assert _eval_key(r, provider="openrouter", model="x/y")[1] == "sk-or-secret"
        # --api-key-env always wins
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}})
        assert _eval_key(r, provider="openai-compatible", base_url=GW2, model="m", api_key_env="GW2_KEY")[1] == "sk-gw2"
