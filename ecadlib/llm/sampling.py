"""Repeatable answers: sampling settings where a provider takes them ([ai] temperature).

    [ai]
    temperature = "auto"     # auto | off | a number from 0 to 2

auto: temperature 0 (and a fixed seed on OpenAI-compatible servers, which most of them honour) for models that accept
a temperature; nothing for models that reject it. Rejected (claude-api skill, Thinking & Effort table, 2026-10): every
Claude model from Opus 4.7 / Sonnet 5 / Haiku 5.5 on (temperature removed or only the default accepted: a 400
otherwise), OpenAI's reasoning models (gpt-5*, o1/o3/o4) and DeepSeek's reasoner. So a request to the default model
(Claude Opus 5.5) is byte-identical with and without this setting; older Claude models (Opus/Sonnet 4.5-4.6,
Haiku 4.5) get temperature 0. [ai.profiles."<model>"] sampling = true | false overrides the table for one model.
off: never send a sampling parameter. A number: that temperature, where the model takes one.

Repeat runs give the same answer mainly through the result cache (PDF hash + model + pages + prompt/schema version +
rails): a second run replays the cached answer and pays nothing. The sampling settings are not part of the cache key,
so changing them never throws away answers already paid for."""
import re

SEED = 7351
# a dated snapshot id (claude-haiku-4-5-20251001) is its alias
REJECTS = re.compile(r"claude-(?!((opus|sonnet)-4-[56]|haiku-4-5)(-\d{8})?$).*|gpt-5.*|o[134](-.*)?|.*reasoner.*")


def takes_sampling(model, prof=None):
    if prof and prof.get("sampling") is not None:
        return bool(prof["sampling"])
    from .profiles import bare
    return not REJECTS.fullmatch(bare(model))


def params(ep, prof, setting="auto"):
    """-> {} or {"temperature": t[, "seed": SEED]} for one endpoint + profile."""
    if setting in (None, "", "off") or isinstance(setting, bool):
        return {}
    if not takes_sampling(ep["model"], prof):
        return {}
    t = 0.0 if setting == "auto" else float(setting)
    out = {"temperature": t}
    if ep["provider"] != "anthropic":
        out["seed"] = SEED
    return out


def check(v):
    """Validation message for [ai] temperature, or None."""
    if v in ("auto", "off"):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 2:
        return None
    return "must be \"auto\", \"off\" or a number from 0 to 2"
