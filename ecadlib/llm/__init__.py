"""Bring your own model: which provider, model, endpoint and key every AI feature uses.

Two providers:
  anthropic          Claude through the Messages API (datasheet.Client, unchanged: PDF document blocks, structured
                     outputs, Message Batches, prompt caching, server-side refusal fallback). The default.
  openai-compatible  any /v1/chat/completions server: OpenAI, OpenRouter, Google's OpenAI-compatible Gemini endpoint,
                     and local servers (Ollama, LM Studio, vLLM, llama.cpp). base_url + the NAME of the env var holding
                     the key; a local server needs no key. ecadlib.llm.openai_compat translates our Messages-shaped
                     requests and answers, so everything after the call (checks, cache, cost ledger) is shared.

Settings (ecadlib.toml), all optional - with no [ai] table everything is exactly as before (Claude Opus 5.5):

    [ai]
    provider = "anthropic"        # anthropic | openai-compatible | a preset: openai, openrouter, gemini, ollama,
                                  #   lmstudio, vllm, llamacpp (preset = openai-compatible + its usual base_url / key var)
    model = ""                    # "" = [datasheet] model (claude-opus-5-5)
    base_url = ""                 # openai-compatible only; "" = the preset's
    api_key_env = ""              # NAME of the env var with the key ("" = the preset's; none for local servers)
    assist = "auto"               # guide rails: auto (by the model's tier) | off | full   (ecadlib.llm.rails)
    feedback_retry = true         # one retry that feeds datasheet's exact check errors back (every model, Claude too)
    detect = true                 # ask an openai-compatible server what the model can do (context, vision, price)
    temperature = "auto"          # auto | off | 0..2: temperature 0 + seed where the model takes them (llm.sampling)
    [ai.profiles."qwen2.5:7b"]    # override what ecadlib assumes about a model (ecadlib.llm.profiles)
    tier = "small"                # frontier | mid | small
    [ai.endpoints.local]          # named endpoints for a mixed ladder: [datasheet] ladder = ["local:qwen2.5:7b",
    provider = "ollama"           #   "claude-opus-5-5"] tries the local model first and escalates to Claude

[datasheet] and [agent.proof] may set provider / base_url / api_key_env / model / assist of their own; empty = [ai].
Keys are read from ecadlib's own key store (`ecadlib keys set`, the setup page), else an environment variable of the
name given; ecadlib never puts a key into the environment or writes it anywhere else. Nothing here phones home: no telemetry,
no hosted service of this project; requests go only to the endpoint you configure."""
import ipaddress, os, re
from urllib.parse import urlparse
from .. import userconfig as UC

PROVIDERS = ("anthropic", "openai-compatible")
# preset name -> (base_url, key env var name); every preset is an openai-compatible endpoint
PRESETS = {"openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
           "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
           "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
           "ollama": ("http://localhost:11434/v1", ""),
           "lmstudio": ("http://localhost:1234/v1", ""),
           "vllm": ("http://localhost:8000/v1", ""),
           "llamacpp": ("http://localhost:8080/v1", "")}
ASSIST = ("auto", "off", "full")
ANTHROPIC_MODEL = "claude-opus-5-5"
ANTHROPIC_KEY = "ANTHROPIC_API_KEY"
DEFAULTS = {"provider": "anthropic", "model": "", "base_url": "", "api_key_env": "", "assist": "auto",
            "feedback_retry": True, "detect": True, "profiles": {}, "endpoints": {}, "temperature": "auto"}
SECTION_KEYS = ("provider", "base_url", "assist")       # added to [datasheet] and [agent.proof] (empty = [ai])
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}")
ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ENDPOINT_KEYS = ("provider", "base_url", "api_key_env", "assist")
NO_KEY = "no-key-needed"                                # a local endpoint: there is no key to send


class AIConfigError(ValueError):
    pass


def is_local(url):
    """localhost, loopback, private and link-local addresses, *.local / *.lan hosts: a server on your own network."""
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith((".local", ".lan", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _ai(rules):
    got = (rules or {}).get("ai") if isinstance(rules, dict) else None
    return {**DEFAULTS, **{k: v for k, v in (got or {}).items() if k in DEFAULTS}}


def _section(rules, section):
    r = rules if isinstance(rules, dict) else {}
    if section == "proof":
        return ((r.get("agent") or {}).get("proof") or {})
    return r.get(section) or {}


def _norm_provider(p):
    """-> (provider, preset or None)."""
    p = (p or "anthropic").strip().lower()
    if p in PRESETS:
        return "openai-compatible", p
    if p in ("openai-compatible", "openai_compatible", "openai-compat"):
        return "openai-compatible", None
    if p == "anthropic":
        return "anthropic", None
    raise AIConfigError(f"provider {p!r}: use anthropic, openai-compatible or one of {', '.join(PRESETS)}")


def split_ref(ref, rules=None):
    """'local:qwen2.5:7b' -> ('local', 'qwen2.5:7b') when [ai.endpoints.local] exists, else (None, ref)."""
    eps = _ai(rules).get("endpoints") or {}
    head, sep, rest = str(ref or "").partition(":")
    if sep and head in eps and rest:
        return head, rest
    return None, ref


def same_server(a, b):
    """True when two resolved endpoints ({provider, base_url}, as endpoint() returns) are the same server: the same
    provider and the same base_url (trailing "/" and case aside). A key name set for one is sent only to the same
    one (R-FX16-1, R-FX16-3); None (an endpoint that does not resolve) is never the same."""
    if not a or not b:
        return False
    return a["provider"] == b["provider"] and \
        (a.get("base_url") or "").rstrip("/").lower() == (b.get("base_url") or "").rstrip("/").lower()


def _same_endpoint(ai, provider, base):
    """True when a section resolved to (provider, base) talks to [ai]'s own provider, so [ai] api_key_env is its key:
    the same provider and, for openai-compatible, the same server."""
    try:
        ai_provider, ai_preset = _norm_provider(ai["provider"])
    except AIConfigError:
        return False
    if provider != ai_provider:
        return False
    if provider == "anthropic":
        return True
    ai_base = ai["base_url"] or (PRESETS[ai_preset][0] if ai_preset else "")
    return same_server({"provider": provider, "base_url": ai_base}, {"provider": provider, "base_url": base})


def server_of(rules, section="datasheet"):
    """The resolved {provider, base_url} of a section (whatever its model), or None when it does not resolve."""
    try:
        return endpoint(rules, section, model="-")
    except AIConfigError:
        return None


def endpoint(rules=None, section="datasheet", model=None):
    """Where a request for `section` goes: {provider, preset, base_url, key_env, model, ref, name, local, assist,
    feedback_retry, detect}. `model` (an explicit --model or a ladder rung) may name an endpoint: 'local:qwen2.5:7b'.
    With no [ai] table and no section overrides this is Anthropic + [datasheet] model + [datasheet] api_key_env."""
    ai, sec = _ai(rules), _section(rules, section)
    ds = _section(rules, "datasheet")
    if section == "proof":                                # [agent.proof] empty = [datasheet] (as before)
        # [datasheet]'s provider as resolved: its own, else [ai]'s (default anthropic), not "" as written (R-FX13-7)
        if sec.get("provider") and str(sec["provider"]).strip().lower() != \
                str(ds.get("provider") or ai["provider"] or "anthropic").strip().lower():
            # its own provider: [datasheet]'s server and key name belong to another provider (R-LE-1)
            ds = {k: v for k, v in ds.items() if k not in ("base_url", "api_key_env")}
        sec = {k: sec.get(k) or ds.get(k) for k in set(sec) | set(ds) | {"provider", "base_url", "assist", "model",
                                                                         "api_key_env"}}
    name, mref = split_ref(model, rules) if model else (None, None)
    if name:
        ep_cfg = ai["endpoints"][name] or {}
        provider, preset = _norm_provider(ep_cfg.get("provider") or "openai-compatible")
        base = ep_cfg.get("base_url") or (PRESETS[preset][0] if preset else "")
        key_env = ep_cfg.get("api_key_env") or (PRESETS[preset][1] if preset else "")
        assist = ep_cfg.get("assist") or sec.get("assist") or ai["assist"]
    else:
        provider, preset = _norm_provider(sec.get("provider") or ai["provider"])
        own_provider = bool(sec.get("provider"))
        base = sec.get("base_url") or ("" if own_provider and sec.get("provider") != ai["provider"] else ai["base_url"]) \
            or (PRESETS[preset][0] if preset else "")
        sk = sec.get("api_key_env") or ""
        if sk == ANTHROPIC_KEY and (provider != "anthropic" or ai["api_key_env"]):
            sk = ""                                       # [datasheet]'s default key name: [ai] api_key_env / the endpoint's decide
        # [ai] api_key_env names the key of [ai]'s provider: a section on another provider (or, openai-compatible,
        # another server) never inherits it, or e.g. the OpenAI key went to openrouter.ai as a Bearer header (R-LE-1).
        # It takes its own preset's key variable instead; key_for() then refuses when that one is not set.
        ai_key = ai["api_key_env"] if _same_endpoint(ai, provider, base) else ""
        key_env = sk or ai_key or (PRESETS[preset][1] if preset else "") or \
            (ANTHROPIC_KEY if provider == "anthropic" else "")
        assist = sec.get("assist") or ai["assist"]
        mref = model
    if not mref:
        sm = sec.get("model") or ""
        if section in ("datasheet", "proof") and sm == ANTHROPIC_MODEL and (ai["model"] or provider != "anthropic"):
            sm = ""                                       # [datasheet] model at its default: [ai] model decides
        mref = sm or ai["model"] or (ANTHROPIC_MODEL if provider == "anthropic" else "")
    if not mref:
        raise AIConfigError(f"no model set for provider {preset or provider}: set [ai] model (e.g. \"qwen2.5:7b\" for "
                            "Ollama, \"gpt-5\" for OpenAI)")
    if provider == "openai-compatible" and not base:
        raise AIConfigError("provider openai-compatible needs [ai] base_url (e.g. http://localhost:11434/v1)")
    if assist not in ASSIST:
        raise AIConfigError(f"assist = {assist!r}: use auto, off or full")
    ref = f"{name}:{mref}" if name else mref
    return {"provider": provider, "preset": preset, "base_url": base.rstrip("/") if base else "", "key_env": key_env,
            "model": mref, "ref": ref, "name": name or (preset or provider), "local": is_local(base),
            "assist": assist, "feedback_retry": bool(ai["feedback_retry"]), "detect": bool(ai["detect"])}


def key_for(ep, explicit=None, environ=None):
    """The key for an endpoint (userconfig.secret: stored for this user, else the environment). Anthropic: explicit,
    the named key, then ANTHROPIC_API_KEY (as datasheet.require_key always did). A local endpoint needs none (NO_KEY). Otherwise None."""
    env = os.environ if environ is None else environ
    if explicit:
        return explicit
    k = UC.secret(ep["key_env"], env) if ep.get("key_env") else None
    if ep["provider"] == "anthropic":
        return k or UC.secret(ANTHROPIC_KEY, env)
    if k:
        return k
    return NO_KEY if ep["local"] or not ep.get("key_env") else None   # no key named: the server takes none


def needs_key_message(ep):
    if ep["provider"] == "anthropic":
        return (f"{ep['key_env']} is not set: add your Claude API key on the setup page (`ecadlib setup`) or with "
                f"`ecadlib keys set {ep['key_env']}`")
    name = ep['key_env'] or '[ai] api_key_env'
    return (f"{name} is not set: add the key for {ep['base_url']} on the setup page (`ecadlib setup`) or with "
            f"`ecadlib keys set {name}` (or point [ai] base_url at a local server, which needs none)")


# ---------------------------------------------------------------- model registry (prices / profiles of models in use)
_REG = {}


def register(ref, ep, prof):
    _REG[ref] = {"endpoint": ep, "profile": prof}


def registered(ref):
    return _REG.get(ref)


def price(ref):
    """(input, output) $ per MTok for a model in use, or None when unknown. Claude: datasheet.PRICES. A local
    endpoint: (0, 0), unless [ai.profiles] sets price_in and price_out (a paid gateway on your network). Others: what
    the profile says (rules override, or detected from OpenRouter's /models)."""
    from .. import datasheet as D
    if ref in D.PRICES:
        return D.PRICES[ref]
    r = _REG.get(ref)
    if not r:
        return None
    p = r["profile"]
    if r["endpoint"]["local"] and not p.get("price_set"):   # local = on your network, not free: [ai.profiles] wins
        return (0.0, 0.0)
    if p.get("price_in") is not None and p.get("price_out") is not None:
        try:
            got = (float(p["price_in"]), float(p["price_out"]))
        except (TypeError, ValueError):
            return None
        # nan / inf / negative (a detected or hand-written price): unknown, so a cost cap refuses instead of passing
        return got if all(0 <= x < float("inf") for x in got) else None
    return None


def is_anthropic(ref, rules=None):
    r = _REG.get(ref)
    if r:
        return r["endpoint"]["provider"] == "anthropic"
    try:
        return endpoint(rules, model=ref)["provider"] == "anthropic"
    except AIConfigError:
        return False


# ---------------------------------------------------------------- rules validation (called from rules.validate)
def validate(where, raw_ai, sections, err):
    """[ai], [ai.profiles.*], [ai.endpoints.*] and the provider keys of [datasheet] / [agent.proof]."""
    from . import profiles as PR
    ai = raw_ai or {}
    if not isinstance(ai, dict):
        err(where, "[ai]", "must be a table")
    for k, v in ai.items():
        if k not in DEFAULTS:
            err(where, f"ai.{k}", f"is not a known key; known in [ai]: {', '.join(DEFAULTS)}")
        if k == "temperature":
            from .sampling import check
            if check(v):
                err(where, "ai.temperature", check(v))
            continue
        d = DEFAULTS[k]
        if isinstance(d, bool):
            if not isinstance(v, bool):
                err(where, f"ai.{k}", "must be true or false")
        elif isinstance(d, dict):
            if not isinstance(v, dict) or not all(isinstance(x, dict) for x in v.values()):
                err(where, f"ai.{k}", f"must be tables: [ai.{k}.\"name\"] key = value")
        elif not isinstance(v, str):
            err(where, f"ai.{k}", "must be text")
    for label, d in [("ai", ai)] + list(sections):
        if not isinstance(d, dict):
            continue
        if d.get("provider"):
            try:
                _norm_provider(d["provider"])
            except AIConfigError as e:
                err(where, f"{label}.provider", f"= {d['provider']!r}: {e}")
        if d.get("assist") and d["assist"] not in ASSIST:
            err(where, f"{label}.assist", f"= {d['assist']!r}: must be auto (by the model's tier), off or full")
        if d.get("base_url") and not re.fullmatch(r"https?://[^\s]+", str(d["base_url"])):
            err(where, f"{label}.base_url", "must be an http(s):// URL such as http://localhost:11434/v1")
        if d.get("base_url") and str(d["base_url"]).startswith("http://") and not is_local(d["base_url"]):
            err(where, f"{label}.base_url", "plain http:// is only allowed for a server on this machine or your own "
                                            "network (the key would travel unencrypted); use https://")
        if d.get("api_key_env") and not ENV_RE.fullmatch(str(d["api_key_env"])):
            err(where, f"{label}.api_key_env", "must be the NAME of an environment variable (never the key itself)")
        if d.get("model") and not MODEL_RE.fullmatch(str(d["model"])):
            err(where, f"{label}.model", f"= {d['model']!r}: must be a model id such as claude-opus-5-5 or qwen2.5:7b")
    for n, ep in (ai.get("endpoints") or {}).items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", n):
            err(where, f"ai.endpoints.{n}", "endpoint names are letters, digits, _ and - (used as name:model in a ladder)")
        for k, v in ep.items():
            if k not in ENDPOINT_KEYS:
                err(where, f"ai.endpoints.{n}.{k}", f"is not a known key; known: {', '.join(ENDPOINT_KEYS)}")
            if not isinstance(v, str):
                err(where, f"ai.endpoints.{n}.{k}", "must be text")
        validate(where, None, [(f"ai.endpoints.{n}", ep)], err) if ep else None
        try:
            p, preset = _norm_provider(ep.get("provider") or "openai-compatible")
        except AIConfigError:
            continue
        if p == "openai-compatible" and not ep.get("base_url") and not preset:
            err(where, f"ai.endpoints.{n}.base_url", "is needed for an openai-compatible endpoint without a preset")
    for m, prof in (ai.get("profiles") or {}).items():
        for k, v in prof.items():
            msg = PR.check_field(k, v)
            if msg:
                err(where, f"ai.profiles.{m}.{k}", msg)
