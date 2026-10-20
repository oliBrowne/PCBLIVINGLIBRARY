"""One read of a datasheet by any model, with the rails its plan names (used by datasheet.Reader).

    info(ref, rules)         endpoint + profile + rail plan for a model reference (cached, registered for pricing)
    ask(job, info, send, judge, client)   -> a Messages-shaped answer with an "ecadlib" record of what ran

A frontier model with PDF input and JSON-schema output on Anthropic gets job["body"] exactly as datasheet.build_request
made it (the same bytes as before this module existed); the only support it may add is a second turn after an answer
that fails datasheet's checks (feedback_retry), sent as the same request with the answer and the errors appended."""
from . import _ai, endpoint, key_for, register, profiles as PR, rails as R, sampling as SM

_INFO = {}


def _rules_key(rules):
    """The cache key for a rules table: its content. Not id(rules): a freed table's id is reused by the next one, which
    then got the old table's plan (seen as an extra feedback turn with feedback_retry = false, Windows CI)."""
    import json
    try:
        return json.dumps(rules, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(rules)


def info(ref, rules=None, section="datasheet", key=None, get=None, post=None, probe=True):
    """probe=False (--offline): the profile from the table and [ai.profiles] only, the server is never asked."""
    k = (ref, section, _rules_key(rules), probe)
    if k not in _INFO:
        ep = endpoint(rules, section, ref)
        prof = PR.resolve(ep if probe else dict(ep, detect=False), rules,
                          key_for(ep, key) if ep["provider"] != "anthropic" and probe else None, get, post)
        plan = R.plan(prof, ep["assist"], ep["feedback_retry"])
        register(ep["ref"], ep, prof)
        _INFO[k] = {"ep": ep, "prof": prof, "plan": plan, "sampling": SM.params(ep, prof, _ai(rules).get("temperature"))}
    return _INFO[k]


def plain(inf):
    """Anthropic, and nothing beyond the support layer: the request goes out exactly as built."""
    return inf["ep"]["provider"] == "anthropic" and not inf["plan"]["guided"]


def _synth(ext, usage, model, served=None):
    import json
    return {"id": "ecadlib-steps", "type": "message", "role": "assistant", "model": served or model,
            "content": [{"type": "text", "text": json.dumps(ext)}], "stop_reason": "end_turn", "stop_details": None,
            "usage": usage}


def _better(k1, w1, k2, w2):
    return R.rank(k2) < R.rank(k1) or (R.rank(k2) == R.rank(k1) and len(w2) <= len(w1))


def ask(job, inf, send, judge, client=None, allow_feedback=True, guard=None):
    """send(body) -> answer for this endpoint (Messages shape; a parse failure is still returned as the answer).
    judge(msg) -> (ok | incomplete | failed | refused | truncated, reasons). guard(body) raises DatasheetError when
    the feedback turn would go over [datasheet] max_cost_usd: the first answer is then kept, nothing more is sent."""
    p, prof = inf["plan"], inf["prof"]
    schema = job["body"]["output_config"]["format"]["schema"]
    notes, rec = [], record(inf)
    fb = allow_feedback and "feedback_retry" in p["rails"]
    smp = inf.get("sampling") or {}
    if smp:                                           # temperature / seed where the model takes them (llm.sampling)
        send = (lambda b, _send=send: _send({**b, **smp}))
        rec["sampling"] = dict(smp)
    if "split_steps" in p["rails"]:
        ext, usage, steps, ctx = R.run_steps(client, job["body"], prof, p, job["part"], job["package"], notes)
        served = next((s.get("model_served") for s in steps if s.get("model_served")), None)
        msg = _synth(ext, usage, job["body"]["model"], served)
        kind, why = judge(msg)
        if kind == "failed" and fb:
            from ..datasheet import DatasheetError
            try:
                if guard:
                    guard(job["body"])
                ext2, u2 = R.redo_step(client, job["body"], prof, p, ctx, steps, ext, why, notes)
                msg2 = _synth(ext2, R.add_usage(usage, u2), job["body"]["model"], served)
                kind2, why2 = judge(msg2)
                rec["feedback"] = {"errors": why[:15], "before": kind, "after": kind2, "kept": "retry" if _better(kind, why, kind2, why2) else "first"}
                msg = msg2 if _better(kind, why, kind2, why2) else dict(msg, usage=msg2["usage"])
            except DatasheetError as e:
                rec["feedback"] = {"errors": why[:15], "before": kind, "after": f"error: {e}", "kept": "first"}
        rec["steps"] = [{k: v for k, v in s.items() if k != "model_served"} for s in steps]
    else:
        body = R.adapt_body(job["body"], prof, p, notes) if p["guided"] else job["body"]
        msg = R.repair_message(send(body), schema, p)
        if fb:
            kind, why = judge(msg)
            if kind == "failed":
                msg = feedback(body, msg, kind, why, inf, send, judge, rec, guard, schema)[0]
    if notes:
        rec["notes"] = notes
    if msg.get("ecadlib", {}).get("repair"):
        rec["repair"] = msg["ecadlib"]["repair"]
    if p["guided"] or rec.get("feedback") or smp:
        msg = dict(msg, ecadlib=rec)
    return msg


def feedback(body, msg, kind, why, inf, send, judge, rec, guard=None, schema=None):
    """The feedback_retry turn after `msg`, the answer to `body` that failed the checks (`why`): the same request with
    the answer and the errors appended. -> (the answer to keep, usage of both; the retry's own answer or None).
    rec["feedback"] says what happened; nothing is sent when guard(body) refuses it (the cost cap)."""
    p = inf["plan"]
    fbody = R.feedback_body(body, msg, why)
    if guard:
        from ..datasheet import DatasheetError
        try:
            guard(fbody)
        except DatasheetError as e:                   # over the cost cap: keep the first answer, send nothing more
            rec["feedback"] = {"errors": why[:15], "before": kind, "after": f"not sent: {e}"[:300], "kept": "first"}
            return msg, None
    msg2 = R.repair_message(send(fbody), schema or body["output_config"]["format"]["schema"], p)
    kind2, why2 = judge(msg2)
    keep = _better(kind, why, kind2, why2)
    rec["feedback"] = {"errors": why[:15], "before": kind, "after": kind2, "kept": "retry" if keep else "first"}
    return dict(msg2 if keep else msg, usage=R.add_usage(msg.get("usage"), msg2.get("usage"))), msg2


def record(inf):
    """What ran for a read: tier, rails, profile, endpoint (the start of the sidecar's "ai" record)."""
    p, prof = inf["plan"], inf["prof"]
    return {"tier": p["tier"], "assist": p["assist"], "profile": {k: prof.get(k) for k in
            ("tier", "pdf", "vision", "json_schema", "context", "source", "detect") if prof.get(k) is not None},
            "rails": list(p["rails"]), "endpoint": inf["ep"]["name"], "provider": inf["ep"]["provider"]}


def provenance(inf, msg=None, route=None, pages_picked=False):
    """The sidecar's "ai" record: which model, profile and rails produced this part, and which support rails ran."""
    rec = dict((msg or {}).get("ecadlib") or {})
    if not rec:
        p = inf["plan"]
        rec = {"tier": p["tier"], "assist": p["assist"], "rails": list(p["rails"]), "endpoint": inf["ep"]["name"],
               "provider": inf["ep"]["provider"],
               "profile": {k: inf["prof"].get(k) for k in ("tier", "pdf", "vision", "json_schema", "context", "source")}}
    ran = ["checks_gate", "units_in_code", "geometry_in_code", "provenance"]
    if pages_picked:
        ran.append("page_picking")
    if rec.get("feedback") and not str(rec["feedback"].get("after", "")).startswith("not sent"):
        ran.append("feedback_retry")
    if route and len(route.get("attempts") or []) > 1:
        ran.append("escalation")
    rec["support_ran"] = ran
    rec["model"] = inf["ep"]["ref"]
    return rec


def wants(rules, rail, section="datasheet", probe=True):
    """Does the configured model's plan include this rail? (False when the setup can't be resolved.)"""
    try:
        return rail in info(endpoint(rules, section)["ref"], rules, section, probe=probe)["plan"]["rails"]
    except Exception as e:                            # the small-model guide rails change what is sent: say so
        from .. import fallbacks as FB
        FB.note(f"[{section}] model setup did not resolve, so its guide rails are unknown", e, instead=f"no {rail!r} rail")
        return False
