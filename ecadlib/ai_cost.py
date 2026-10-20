"""The cost of an AI step, shown before it starts and next to what it really cost afterwards (issue #49).

The Altium dialog, the KiCad window and the web UI never start a paid model call by themselves:

  1. the free part runs first (Fill the library / Add part: every free source; the house style scan). Parts only a
     datasheet read can finish are priced, not sent: `quote()` -> the model, about how much, at most how much;
  2. the person says yes (a second, explicit yes when the most it can cost is above the limit: [datasheet]
     max_cost_usd when you set one, else [datasheet] ask_above_usd, default $1.00, or when the model's price is unknown);
  3. the reads run held to what was approved (`approve()`: the Reader's max_cost_usd becomes the approved amount,
     so a retry or a bigger answer can never spend more than the person saw), and `after()` puts what the cost
     ledger (<plan folder>/datasheet/cost.jsonl) recorded for this run next to the estimate.

Estimates come from datasheet.Reader.estimate (exact input token counts with a key, pages x PAGE_TOKENS without)
plus the cross-check's second read where [datasheet] cross_check may make one. "at most" is the worst case: every
answer at max_tokens and every step of the part's route. The command line keeps `ecadlib datasheet estimate`; it
refuses a run over [datasheet] max_cost_usd (default $10 per run, --max-cost-usd changes it, --yes does not lift it);
the library agent keeps its own [agent.proof] max_cost_usd."""
import json
import math
from pathlib import Path

CHECK_TYPICAL_OUT = 1000           # a cross-check answer: the critical numbers and pin names only
TOLERANCE = 0.005                  # an estimate may come back half a cent higher than the one the person approved


def limit(rules=None):
    """(USD, setting name) above which the dialogs ask a second time."""
    from . import datasheet as D
    s = D.settings(rules)
    cap = float(s.get("max_cost_usd") or 0)
    if cap > 0 and (cap != D.DEFAULT_MAX_COST_USD or D.cap_chosen(rules)):   # a cap you set (10 too); the default
        return cap, "max_cost_usd"                                           # one is for unattended runs
    return float(s.get("ask_above_usd") or 0), "ask_above_usd"


def _over(worst, lim, which):
    """Ask a second time? Price unknown, or above the limit. max_cost_usd 0 = no cap; ask_above_usd 0 = ask twice for
    any paid request (the cautious setting). One rule for every quote, so they cannot drift apart."""
    return worst is None or ((lim > 0 or which == "ask_above_usd") and worst > lim)


def up(v):
    """v rounded up to what money() shows (cents from $0.095, else tenths of a cent): the "at most" a dialog prints
    and the amount it approves are then the same number, never the approval a cent above the text."""
    if not v:
        return v
    step = 100 if v >= 0.095 else 1000
    return math.ceil(round(v * step, 6)) / step


def money(v):
    if v is None:
        return "price unknown"
    if v == 0:
        return "$0 (free)"
    if v < 0.001:
        return "under $0.001"
    return f"${v:.2f}" if v >= 0.095 else f"${v:.3f}" if v >= 0.001 else "under $0.001"


def _check_cost(rows, cfg, batch, rules=None):
    """(typical, worst) USD of the cross-check's second reads, or (0, 0) when cross_check is off; None if unpriced."""
    from . import crosscheck as CC, datasheet as D, ds_route as DR
    from .llm import run as LR, AIConfigError
    if str(cfg.get("cross_check") or "auto") == "off":
        return 0.0, 0.0
    cw, mult = D.CACHE_WRITE, 1.0                     # the second read is always a synchronous request, batch run or not
    typ = worst = 0.0
    for r in rows:
        model = cfg.get("cross_check_model") or r["model"]
        p = DR.prices(model, r["input_tokens"])
        if not p:                                     # "local:qwen2.5:7b" no request has used yet: resolve it as the
            try:                                      # Reader will before the second read (its endpoint and profile)
                LR.info(model, rules)
            except AIConfigError:
                return None
            p = DR.prices(model, r["input_tokens"])
        if not p:
            return None
        typ += (r["input_tokens"] * cw * p[0] + CHECK_TYPICAL_OUT * p[1]) / 1e6 * mult
        worst += D.worst_usd(model, r["input_tokens"], CC.CHECK_MAX_TOKENS, cw, mult,
                             fallback=model in D.FALLBACK_MODELS)   # as the Reader's cap counts it
    return typ, worst


def quote(est, rules=None, key_set=True):
    """What the dialogs show before the datasheet reads: None when no part needs one. est: Reader.estimate /
    datasheet.estimate_plan."""
    from . import datasheet as D
    rows = (est or {}).get("rows") or []
    if not rows:
        return None
    cfg = D.settings(rules)
    batch = str(est.get("mode", "")).startswith("batch")
    usd = est.get("usd")
    worst = est.get("usd_max_route") if est.get("usd_max_route") is not None else est.get("usd_max")
    check = _check_cost(rows, cfg, batch, rules)
    if check is None or usd is None or worst is None:
        usd = worst = None
    else:
        usd, worst = round(usd + check[0], 4), round(worst + check[1], 4)
    lim, which = limit(rules)
    models = list(est.get("models") or {}) or [est.get("model") or cfg["model"]]
    q = {"step": "datasheet", "parts": len(rows), "names": [r["part"] for r in rows], "model": ", ".join(models),
         "usd": usd, "usd_max": worst, "limit_usd": lim, "limit_name": which, "price_known": worst is not None,
         "over_limit": _over(worst, lim, which), "exact": bool(est.get("exact_input")),
         "cross_check": str(cfg.get("cross_check") or "auto") != "off", "key_set": bool(key_set),
         "skipped": [s["part"] for s in est.get("skipped") or []], "cached": list(est.get("cached") or [])}
    q["approve_usd"] = up(worst) if worst is not None else None
    q["text"] = text(q)
    return q


def text(q):
    """One plain paragraph for a dialog: how many reads, which model, about / at most, and the limit."""
    from . import messages as M
    what = f"{M.plural(q['parts'], 'part')} need{'s' if q['parts'] == 1 else ''} the datasheet read by AI ({q['model']})"
    if not q["key_set"]:
        return what + ". No API key is stored for it yet: add one on the setup page or with `ecadlib keys set`."
    if not q["price_known"]:
        return what + ". ecadlib does not know this model's price, so it cannot say what the reads cost."
    s = f"{what}: about {money(q['usd'])}, at most {money(up(q['usd_max']))}"
    s += " (with the cross-check's second read)." if q["cross_check"] else "."
    if q["over_limit"]:
        s += f" That is more than your limit of {money(q['limit_usd'])} ([datasheet] {q['limit_name']})."
    return s


def approve(q, approved_usd=None, over_limit_ok=False, price_unknown_ok=False):
    """The cap to hold the reads to, or ValueError when the person did not approve this much. Returns (cap, yes):
    cap goes into the Reader's max_cost_usd; yes=True only for a model whose price is unknown, approved as such."""
    if not q["price_known"]:
        if not price_unknown_ok:
            raise ValueError(f"the price of {q['model']} is unknown: confirm reading without a cost estimate first")
        return 0.0, True
    if q["over_limit"] and not over_limit_ok:
        raise ValueError(f"at most {money(up(q['usd_max']))} is over your limit of {money(q['limit_usd'])}: "
                         "confirm the higher cost first")
    try:
        approved = float(approved_usd)
        if isinstance(approved_usd, bool) or not math.isfinite(approved) or approved < 0:
            raise ValueError(approved_usd)   # NaN / inf / -1 would compare as "within the approval" and lift the cap
    except (TypeError, ValueError):
        raise ValueError(f"approve the cost first: about {money(q['usd'])}, at most {money(up(q['usd_max']))}")
    if q["usd_max"] > approved + TOLERANCE:
        raise ValueError(f"the reads now cost up to {money(up(q['usd_max']))}, more than the {money(approved)} you approved: "
                         "check the new estimate")
    return max(approved, q["usd_max"]), False


def ledger(folder):
    return Path(folder) / "datasheet" / "cost.jsonl"


def mark(folder):
    """Where the cost ledger ends now (bytes), so after() counts only this run's answers."""
    try:
        return ledger(folder).stat().st_size
    except OSError:
        return 0


def spent(folder, since=0):
    """{usd, reads, models, unpriced} of the ledger lines written after `since`."""
    out = {"usd": 0.0, "reads": 0, "models": [], "unpriced": 0}
    try:
        with open(ledger(folder), "rb") as f:
            f.seek(since)
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return out
    for line in data.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        out["reads"] += 1
        if rec.get("usd") is None:
            out["unpriced"] += 1
        else:
            out["usd"] += float(rec["usd"])
        m = rec.get("model_served") or rec.get("model")
        if m and m not in out["models"]:
            out["models"].append(m)
    out["usd"] = round(out["usd"], 4)
    return out


def after(q, got):
    """What the dialogs show afterwards: the estimate and the real cost side by side."""
    from . import messages as M
    est = q or {}
    r = {"estimate_usd": est.get("usd"), "estimate_max_usd": est.get("usd_max"), "actual_usd": got["usd"],
         "reads": got["reads"], "models": got["models"], "unpriced": got["unpriced"]}
    s = f"Cost: {money(got['usd'])} for {M.plural(got['reads'], 'AI answer')}"
    if est:
        s += f" (estimated about {money(est.get('usd'))}, at most {money(est.get('usd_max'))})"
    if got["unpriced"]:
        s += f"; {got['unpriced']} without a known price"
    r["text"] = s + "."
    return r


def style_quote(system, user, schema, model, rules=None, max_tokens=8000, retry=True, key_set=True):
    """The house style guide request priced the same way (one request; a second one only when the answer does not
    fit). -> a quote dict like quote()'s, step "style"."""
    from . import ds_route as DR
    tin = (len(system) + len(user) + len(json.dumps(schema))) // 3 + 50
    p = DR.prices(model, tin)
    lim, which = limit(rules)
    usd = round((tin * p[0] + 2000 * p[1]) / 1e6, 4) if p else None
    worst = round((tin * p[0] + max_tokens * p[1]) / 1e6 * (2.2 if retry else 1), 4) if p else None
    q = {"step": "style", "parts": 1, "names": [], "model": model, "usd": usd, "usd_max": worst, "limit_usd": lim,
         "limit_name": which, "price_known": worst is not None, "over_limit": _over(worst, lim, which),
         "exact": False, "cross_check": False, "key_set": bool(key_set), "skipped": [], "cached": []}
    q["approve_usd"] = up(worst) if worst is not None else None
    if worst is None:
        q["text"] = f"Writing the style guide asks {model} once; ecadlib does not know its price."
    else:
        q["text"] = (f"Writing the style guide asks {model} once: about {money(usd)}, at most {money(up(worst))}."
                     + (f" That is more than your limit of {money(lim)} ([datasheet] {which})." if q["over_limit"] else ""))
    if not key_set:
        q["text"] += " No API key is stored for it yet: add one on the setup page or with `ecadlib keys set`."
    return q


def request_quote(step, model, usd, worst, rules=None, key_set=True, what="This asks the model once"):
    """A quote dict like quote()'s for model requests priced elsewhere (a visual check, the house style rewording):
    `usd` typical, `worst` at most (None = price unknown)."""
    lim, which = limit(rules)
    q = {"step": step, "parts": 1, "names": [], "model": model, "usd": usd, "usd_max": worst, "limit_usd": lim,
         "limit_name": which, "price_known": worst is not None,
         "over_limit": _over(worst, lim, which),
         "exact": False, "cross_check": False, "key_set": bool(key_set), "skipped": [], "cached": []}
    q["approve_usd"] = up(worst) if worst is not None else None
    if worst is None:
        q["text"] = f"{what} ({model}); ecadlib does not know its price."
    else:
        q["text"] = (f"{what} ({model}): about {money(usd)}, at most {money(up(worst))}."
                     + (f" That is more than your limit of {money(lim)} ([datasheet] {which})." if q["over_limit"] else ""))
    if not key_set:
        q["text"] += " No API key is stored for it yet: add one on the setup page or with `ecadlib keys set`."
    return q
