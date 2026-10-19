"""What a model can do, and how much help it gets: capability profiles.

A profile is {tier, pdf, vision, json_schema, context, max_tokens_param, price_in, price_out, source}:
  tier         frontier | mid | small - picks the guide rails with [ai] assist = "auto" (ecadlib.llm.rails)
  pdf          the endpoint takes a PDF as input (Claude document blocks; OpenAI file parts)
  vision       it takes images (page pictures when the PDF is sent as text; visual-check needs it)
  json_schema  it honours a JSON-schema response format (else: schema in the prompt + JSON repair)
  context      context window in tokens (sizes how much page text is sent)
  max_tokens_param  max_tokens | max_completion_tokens (OpenAI's newer models want the second)
  price_in / price_out  $ per million tokens when known (Claude: datasheet.PRICES; a local server is $0)
  sampling     it takes a temperature (unset: ecadlib.llm.sampling's table of models that reject one)

Where it comes from, later wins: CONSERVATIVE (an unknown model: small, text only, no schema, 4K context) ->
the built-in table below (best knowledge as of 2026-10; names matched without a vendor prefix such as
"openai/" or "anthropic/") -> what the endpoint reports (detect(): /models on OpenRouter, vLLM, LM Studio;
/api/show on Ollama) -> [ai.profiles."<model>"] in ecadlib.toml (always wins)."""
import json, re

TIERS = ("frontier", "mid", "small")
FIELDS = {"tier": str, "pdf": bool, "vision": bool, "json_schema": bool, "context": int, "max_tokens_param": str,
          "price_in": float, "price_out": float, "sampling": bool}
CONSERVATIVE = {"tier": "small", "pdf": False, "vision": False, "json_schema": False, "context": 4096,
                "max_tokens_param": "max_tokens", "price_in": None, "price_out": None}
_C = dict(tier="frontier", pdf=True, vision=True, json_schema=True, context=1_000_000)
# (regex on the bare model name, profile fields). First match wins. Tiers are judgement calls for THIS task (reading a
# package drawing and a pin table exactly); override any of them with [ai.profiles."<model>"].
BUILTIN = [
    (r"claude-(haiku|sonnet|opus)-4-5", dict(_C, context=200_000)),
    (r"claude-.*", _C),
    # OpenAI's 2026 names (context and PDF input assumed as for gpt-5; correct with [ai.profiles] if wrong)
    (r"gpt-[56](\.\d+)?-luna.*", dict(tier="small", pdf=True, vision=True, json_schema=True, context=400_000,
                                     max_tokens_param="max_completion_tokens")),
    (r"gpt-[56](\.\d+)?-terra.*", dict(tier="mid", pdf=True, vision=True, json_schema=True, context=400_000,
                                      max_tokens_param="max_completion_tokens")),
    (r"gpt-6.*|gpt-5(\.\d+)?-sol.*", dict(tier="frontier", pdf=True, vision=True, json_schema=True, context=400_000,
                                         max_tokens_param="max_completion_tokens")),
    (r"gpt-5(\.\d+)?-nano.*", dict(tier="small", pdf=True, vision=True, json_schema=True, context=400_000,
                                   max_tokens_param="max_completion_tokens")),
    (r"gpt-5(\.\d+)?-mini.*", dict(tier="mid", pdf=True, vision=True, json_schema=True, context=400_000,
                                   max_tokens_param="max_completion_tokens")),
    (r"gpt-5.*", dict(tier="frontier", pdf=True, vision=True, json_schema=True, context=400_000,
                      max_tokens_param="max_completion_tokens")),
    (r"o[34](-pro)?(-\d.*)?", dict(tier="frontier", pdf=True, vision=True, json_schema=True, context=200_000,
                                   max_tokens_param="max_completion_tokens")),
    (r"o4-mini.*", dict(tier="mid", pdf=True, vision=True, json_schema=True, context=200_000,
                        max_tokens_param="max_completion_tokens")),
    (r"gpt-4\.1-nano.*|gpt-4o-mini.*", dict(tier="small", pdf=True, vision=True, json_schema=True, context=128_000)),
    (r"gpt-4\.1.*|gpt-4o.*", dict(tier="mid", pdf=True, vision=True, json_schema=True, context=128_000)),
    (r"gemini-[\d.]+-(pro|argon).*", dict(tier="frontier", pdf=False, vision=True, json_schema=True, context=1_000_000)),
    (r"gemini-[\d.]+-flash-lite.*", dict(tier="small", pdf=False, vision=True, json_schema=True, context=1_000_000)),
    (r"gemini-.*flash.*", dict(tier="mid", pdf=False, vision=True, json_schema=True, context=1_000_000)),
    (r"deepseek-(chat|reasoner|v3.*|r1)", dict(tier="mid", pdf=False, vision=False, json_schema=False, context=64_000)),
    (r"deepseek-flash.*", dict(tier="mid", pdf=False, vision=True, json_schema=False, context=64_000)),
    (r"deepseek-v4.*", dict(tier="mid", pdf=False, vision=False, json_schema=False, context=64_000)),
]
# List prices, $ per million tokens (input, output), standard tier, prompts up to 200K, for models called directly
# (not through OpenRouter, whose /models reports its own). Checked 2026-10-16 against benchlm.ai and promptunit.ai
# (third-party price trackers); vendors change these, so [ai.profiles."<model>"] price_in / price_out wins.
# Claude's prices are datasheet.PRICES. Known changes: gemini-4-argon $4/$20 from 2026-12-01, gemini-3.8-flash
# $1.50/$7.50 from 2027-01-01; DeepSeek bills half these rates off-peak.
PRICES = {
    "gpt-6-astra": (10.0, 50.0), "gpt-6.1-sol": (2.0, 10.0), "gpt-6-sol": (2.0, 10.0), "gpt-6-luna": (0.10, 0.50),
    "gpt-5.6-terra": (2.0, 12.0), "gpt-5.6-luna": (0.20, 1.20), "gpt-5.5": (5.0, 30.0), "gpt-5.4": (2.50, 15.0),
    "gpt-5.4-mini": (0.75, 4.50), "gpt-5.4-nano": (0.20, 1.25), "gpt-5.2": (1.75, 14.0), "gpt-5.1": (1.25, 10.0),
    "gpt-5": (1.25, 10.0), "gpt-5-mini": (0.25, 2.0), "gpt-5-nano": (0.05, 0.40),
    "gemini-4-argon": (2.0, 10.0), "gemini-3.1-pro-preview": (2.0, 12.0), "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.0), "gemini-3-flash-preview": (0.50, 3.0), "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40),
    "deepseek-v4-pro": (1.32, 3.96), "deepseek-flash": (0.30, 1.20)}
VISION_NAME = re.compile(r"vl\b|vl[-:]|vision|llava|pixtral|minicpm-v|gemma-?3|qwen2\.5-?vl|llama-?4|moondream|-v\b", re.I)
LOCAL_FAMILY = re.compile(r"llama|llava|pixtral|minicpm|moondream|qwen|mistral|mixtral|gemma|phi|deepseek|granite|command-r|olmo|smollm|nemotron|yi|glm", re.I)
SIZE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", re.I)


def bare(model):
    """'openai/gpt-5' -> 'gpt-5', 'anthropic/claude-opus-5-5' -> 'claude-opus-5-5', 'local:qwen2.5:7b' handled by the
    caller (ecadlib.llm.split_ref); 'hf.co/x/y:Q4' -> 'y:q4'. Lower case."""
    return str(model or "").rsplit("/", 1)[-1].lower()


def builtin(model):
    """-> (fields, source) from the table, or a guess for local open-weight families by size, or ({}, None)."""
    m = bare(model)
    for rx, prof in BUILTIN:
        if re.fullmatch(rx, m):
            got = dict(prof)
            if m in PRICES:
                got["price_in"], got["price_out"] = PRICES[m]
            return got, f"built-in ({rx})"
    if LOCAL_FAMILY.search(m):
        sz = SIZE.search(m.replace(":", " ").replace("-", " "))
        b = float(sz.group(1)) if sz else None
        tier = "mid" if b and b >= 60 else "small"
        return ({"tier": tier, "vision": bool(VISION_NAME.search(m)), "json_schema": True, "pdf": False,
                 "context": 8192}, f"built-in (open-weight family, {('%gB' % b) if b else 'size unknown'} -> {tier})")
    return {}, None


def profile(model, provider="anthropic", rules=None, detected=None):
    """The profile for `model` (a bare model id, without an endpoint prefix)."""
    p, src = dict(CONSERVATIVE), ["default (unknown model: conservative)"]
    got, s = builtin(model)
    if provider == "anthropic" and not got:               # any Claude id the table does not know yet
        got, s = dict(_C), "built-in (anthropic)"
    if got:
        p.update(got)
        src = [s]
    if detected:
        p.update({k: v for k, v in detected.items() if k in FIELDS and v is not None})
        src.append("detected: " + ", ".join(sorted(k for k in detected if k in FIELDS and detected[k] is not None)))
    over = (((rules or {}).get("ai") or {}).get("profiles") or {}) if isinstance(rules, dict) else {}
    o = over.get(model) or over.get(bare(model)) or {}
    if o:
        p.update({k: v for k, v in o.items() if k in FIELDS})
        src.append("ecadlib.toml [ai.profiles] (" + ", ".join(sorted(o)) + ")")
    p["source"] = "; ".join(src)
    return p


def check_field(k, v):
    """Validation message for one [ai.profiles] key, or None."""
    if k not in FIELDS:
        return f"is not a known key; known: {', '.join(FIELDS)}"
    if k == "tier" and v not in TIERS:
        return f"= {v!r}: must be frontier, mid or small"
    if k == "max_tokens_param" and v not in ("max_tokens", "max_completion_tokens"):
        return "must be max_tokens or max_completion_tokens"
    t = FIELDS[k]
    if t is bool and not isinstance(v, bool):
        return "must be true or false"
    if t is int and (not isinstance(v, int) or isinstance(v, bool) or not 512 <= v <= 10_000_000):
        return "must be a whole number of tokens from 512 to 10000000"
    if t is float and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v < float("inf")):  # nan too
        return "must be a number of 0 or more ($ per million tokens)"
    return None


# ---------------------------------------------------------------- cheap auto-detection (openai-compatible servers)
_DETECTED = {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def from_models_entry(e):
    """One entry of GET /models (OpenRouter, vLLM, LM Studio's /api/v0/models, ...) -> profile fields it states."""
    out = {}
    ctx = e.get("context_length") or e.get("max_model_len") or e.get("max_context_length") or \
        (e.get("top_provider") or {}).get("context_length")
    if isinstance(ctx, (int, float)) and ctx > 0:
        out["context"] = int(ctx)
    mods = (e.get("architecture") or {}).get("input_modalities")
    if isinstance(mods, list):
        out["vision"] = "image" in mods
        out["pdf"] = "file" in mods
    if e.get("type") in ("vlm", "llm"):                   # LM Studio
        out["vision"] = e["type"] == "vlm"
    sp = e.get("supported_parameters")
    if isinstance(sp, list):
        out["json_schema"] = "structured_outputs" in sp or "response_format" in sp
    pr = e.get("pricing") or {}
    pin, pout = _num(pr.get("prompt")), _num(pr.get("completion"))
    if pin is not None and pout is not None and pin >= 0 and pout >= 0:
        out["price_in"], out["price_out"] = round(pin * 1e6, 6), round(pout * 1e6, 6)
    return out


def from_ollama_show(j):
    """POST /api/show -> profile fields: capabilities ['completion', 'vision', ...], model_info *.context_length."""
    out = {}
    caps = j.get("capabilities")
    if isinstance(caps, list):
        out["vision"] = "vision" in caps
    for k, v in (j.get("model_info") or {}).items():
        if k.endswith(".context_length") and isinstance(v, int) and v > 0:
            out["context"] = v
    return out


def detect(ep, key=None, get=None, post=None, timeout=5):
    """Ask the endpoint about the model (cached per base_url + model). Never raises: -> (fields, note)."""
    ck = (ep.get("base_url"), ep.get("model"))
    if ck in _DETECTED:
        return _DETECTED[ck]
    import requests
    get, post = get or requests.get, post or requests.post
    h = {"Authorization": f"Bearer {key}"} if key and key != "no-key-needed" else {}
    fields, notes = {}, []
    base = ep["base_url"]
    try:
        r = get(base + "/models", headers=h, timeout=timeout)
        data = r.json() if getattr(r, "status_code", 0) == 200 else None
        rows = (data or {}).get("data") if isinstance(data, dict) else data
        hit = next((e for e in rows or [] if isinstance(e, dict) and str(e.get("id")) in (ep["model"], bare(ep["model"]))), None)
        if hit:
            fields.update(from_models_entry(hit))
            notes.append("/models")
        elif rows is not None:
            notes.append(f"/models does not list {ep['model']}")
    except Exception as e:                                # unreachable, not JSON, ...: the table decides
        notes.append(f"/models: {type(e).__name__}")
    root = re.sub(r"/v1/?$", "", base)
    if ep.get("preset") == "ollama" or ":11434" in base:
        try:
            r = post(root + "/api/show", json={"model": ep["model"]}, headers=h, timeout=timeout)
            if getattr(r, "status_code", 0) == 200:
                fields.update(from_ollama_show(r.json()))
                notes.append("/api/show")
        except Exception as e:
            notes.append(f"/api/show: {type(e).__name__}")
    if ep.get("preset") == "lmstudio" or ":1234" in base:
        try:
            r = get(root + "/api/v0/models/" + ep["model"], headers=h, timeout=timeout)
            if getattr(r, "status_code", 0) == 200:
                fields.update(from_models_entry(r.json()))
                notes.append("/api/v0/models")
        except Exception as e:
            notes.append(f"/api/v0/models: {type(e).__name__}")
    _DETECTED[ck] = (fields, "; ".join(notes))
    return _DETECTED[ck]


def resolve(ep, rules=None, key=None, get=None, post=None):
    """Profile for an endpoint dict (ecadlib.llm.endpoint), with detection for openai-compatible endpoints when
    [ai] detect is on. Anthropic endpoints are never probed (their table entry is enough; nothing extra is sent)."""
    det, note = (None, "")
    if ep["provider"] == "openai-compatible" and ep.get("detect", True):
        det, note = detect(ep, key, get, post)
    p = profile(ep["model"], ep["provider"], rules, det)
    if note:
        p["detect"] = note
    return p


def describe(p):
    caps = [c for c in ("pdf", "vision", "json_schema") if p.get(c)]
    return f"{p['tier']} tier; {', '.join(caps) or 'text only'}; context {p['context']:,} tokens ({p['source']})"


def dumps(p):
    return json.dumps(p, sort_keys=True)
