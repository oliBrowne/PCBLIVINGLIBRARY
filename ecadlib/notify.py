"""Notifications: tell people when the library agent or upkeep has something for them (Slack, Microsoft Teams, any
JSON webhook). Off unless [notify] has targets.

    [notify]
    retries = 3            # more tries after a failed post (backoff_s, then 2x, 4x ...; 429 and 5xx only)
    timeout_s = 10         # per request
    backoff_s = 2.0

    [[notify.targets]]
    name = "library-channel"
    type = "slack"                       # slack (incoming webhook) | teams (Teams workflow webhook) | json (generic POST)
    url_env = "SLACK_WEBHOOK_URL"        # NAME of the environment variable holding the webhook URL
    events = ["batch_ready", "part_held"]   # default (or "*"): every event

    [[notify.targets]]
    name = "erp"
    type = "json"
    url_env = "ERP_HOOK_URL"
    token_env = "ERP_HOOK_TOKEN"         # optional, json only: sent as Authorization: Bearer <value>
    events = ["lifecycle"]

Webhook URLs carry their own secret (Slack, Teams), so the file names environment variables only; a URL or token
written into the file is refused by `ecadlib rules check`. Error messages name the target and host, never the URL.

Events:
    batch_ready      library agent: a review batch was published (pull / merge request opened or updated, or its folder)
    part_held        library agent: parts it could not build or that failed their proof (queue status failed)
    upkeep_changes   upkeep (`ecadlib upkeep`, or the agent's MAINTAIN step): new changes since the last run
    lifecycle        a part went EOL / obsolete, NRND or last time buy

Sending never raises and never stops the agent: a target whose variable is unset is skipped, a failed post is retried
with backoff inside a fixed time budget (max_seconds; once it is used up, the remaining posts are skipped), and every
outcome is returned (the agent puts them in its report as rep["notify"]). Tests pass sender=fake and sleep=fake.

    ecadlib notify test [--event batch_ready] [--target NAME]   send a test message to the configured targets
    ecadlib notify show                                         targets, events and whether their variables are set"""
import argparse, json, re, sys, time
from urllib.parse import urlsplit

EVENTS = ("batch_ready", "part_held", "upkeep_changes", "lifecycle")
TYPES = ("slack", "teams", "json")
LIFECYCLE_KINDS = {"eol", "obsolete", "nrnd", "last time buy", "ltb"}
TARGET_KEYS = ("name", "type", "url_env", "token_env", "events")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_SECONDS = 60.0                    # all retries of one send() together wait at most this long
MAX_LINES = 20


# ------------------------------------------------------------------ rules validation (called from rules.validate)
def check_targets(where, v, err):
    """[[notify.targets]]: a list of tables with the keys above. err(where, key, msg) raises."""
    if not isinstance(v, list):
        err(where, "notify.targets", "must be a list of { name, type, url_env, events } tables")
    names = set()
    for i, t in enumerate(v):
        k = f"notify.targets[{i}]"
        if not isinstance(t, dict):
            err(where, k, "must be a table such as { type = \"slack\", url_env = \"SLACK_WEBHOOK_URL\" }")
        bad = [x for x in t if x not in TARGET_KEYS]
        if bad:
            hint = " (put the URL in an environment variable and name it in url_env)" if bad[0] in ("url", "webhook", "token") else ""
            err(where, f"{k}.{bad[0]}", f"is not a known key; known: {', '.join(TARGET_KEYS)}{hint}")
        if t.get("type") not in TYPES:
            err(where, f"{k}.type", f"= {t.get('type')!r}: must be one of {', '.join(TYPES)}")
        for e in ("url_env", "token_env"):
            if e in t and (not isinstance(t[e], str) or (t[e] and not ENV_NAME.fullmatch(t[e]))):
                err(where, f"{k}.{e}", "must be the NAME of an environment variable (never the URL or token itself)")
        if not t.get("url_env"):
            err(where, f"{k}.url_env", "is required: the NAME of the environment variable holding the webhook URL")
        if t.get("token_env") and t.get("type") != "json":
            err(where, f"{k}.token_env", "is only used by type = \"json\" (Slack and Teams URLs carry their own secret)")
        ev = t.get("events", ["*"])
        ev = [ev] if isinstance(ev, str) else ev
        if not isinstance(ev, list) or any(x != "*" and x not in EVENTS for x in ev):
            err(where, f"{k}.events", f"= {t.get('events')!r}: events are {', '.join(EVENTS)} (or \"*\")")
        name = t.get("name", f"{t.get('type')}{i + 1}")
        if not isinstance(name, str) or name in names:
            err(where, f"{k}.name", f"= {name!r}: must be text, different for each target")
        names.add(name)


def settings(rules):
    from . import rules as RU
    n = dict(RU.DEFAULTS["notify"])
    n.update((rules or {}).get("notify") or {})
    out = []
    for i, t in enumerate(n.get("targets") or []):
        ev = t.get("events", ["*"])
        ev = [ev] if isinstance(ev, str) else list(ev)
        out.append({"name": t.get("name") or f"{t['type']}{i + 1}", "type": t["type"], "url_env": t["url_env"],
                    "token_env": t.get("token_env") or "", "events": set(EVENTS) if "*" in ev else set(ev)})
    n["targets"] = out
    return n


# ------------------------------------------------------------------ events
def event(name, title, text="", lines=(), url="", data=None):
    return {"event": name, "title": str(title), "text": str(text), "lines": [str(x) for x in lines][:MAX_LINES],
            "more": max(0, len(lines) - MAX_LINES), "url": str(url or ""), "data": data or {}}


def _is_lifecycle(c):
    return c.get("check", "lifecycle") == "lifecycle" and str(c.get("kind", "")).lower() in LIFECYCLE_KINDS


def _change_line(c):
    return f"{c.get('kind')}: {c.get('item')}" + (f" ({c.get('was')} -> {c.get('now')})" if c.get("was") or c.get("now") else "")


def upkeep_events(rep):
    """upkeep_changes (+ lifecycle) events for an upkeep.run report."""
    changes = [dict(c, check=s["check"]) for s in rep.get("checks", []) for c in s.get("changes", [])]
    evs = []
    if changes:
        url = ((rep.get("github") or rep.get("gitlab") or {}).get("url")) or ""
        evs.append(event("upkeep_changes", f"Library upkeep: {len(changes)} new change(s)",
                         ", ".join(f"{s['check']} {len(s['changes'])}" for s in rep.get("checks", []) if s.get("changes")),
                         [f"[{c['check']}] {_change_line(c)}" for c in changes], url,
                         {"count": len(changes), "changes": changes[:200]}))
    life = [c for c in changes if _is_lifecycle(c)]
    if life:
        evs.append(event("lifecycle", f"Lifecycle: {len(life)} part(s) EOL / NRND / last time buy", "",
                         [_change_line(c) for c in life], "", {"parts": life[:200]}))
    return evs


def _plain(reason):
    from . import messages as M
    return M.explain(reason)["text"] if reason else ""


def agent_events(rep, lookup=None):
    """batch_ready / part_held / upkeep_changes / lifecycle events for an agent.run_once report.
    lookup(item id) -> the queue item (for the part number of a failed item)."""
    evs = []
    for b in rep.get("batches") or []:
        n = len(b.get("items") or [])
        where = b.get("pr_url") or ""
        evs.append(event("batch_ready", f"Library agent: batch {b.get('id')} has {n} part(s) to review",
                         f"Next: compare the pictures with the datasheets, then approve or reject each part "
                         f"(pull request {b.get('pr_action') or 'ready'})." if where else
                         f"Next: review the parts in the review queue or in {b.get('folder')} "
                         f"(no pull request: {b.get('fallback') or 'not published'}).",
                         [], where, {"batch": b.get("id"), "items": b.get("items") or [], "pr_url": b.get("pr_url"),
                                     "folder": b.get("folder")}))
    failed = rep.get("failed") or []
    if failed:
        def part(f):
            it = (lookup(f["id"]) if lookup else None) or {}
            return it.get("mpn") or it.get("key") or f["id"]
        evs.append(event("part_held", f"Library agent: {len(failed)} part(s) held",
                         "They could not be built or failed the independent check. Next: open them in the review queue.",
                         [f"{part(f)}: {_plain(f.get('reason', ''))}"[:300] for f in failed], "",
                         {"items": [{"id": f["id"], "part": part(f), "reason": f.get("reason")} for f in failed]}))
    found = (rep.get("upkeep") or {}).get("items") or []
    if found:
        evs.append(event("upkeep_changes", f"Library agent upkeep: {len(found)} new finding(s)", "Next: they are in the review queue.",
                         [f"[{c.get('check')}] {_change_line(c)}" for c in found], "", {"count": len(found), "changes": found[:200]}))
        life = [c for c in found if _is_lifecycle(c)]
        if life:
            evs.append(event("lifecycle", f"Lifecycle: {len(life)} part(s) EOL / NRND / last time buy", "",
                             [_change_line(c) for c in life], "", {"parts": life[:200]}))
    return evs


# ------------------------------------------------------------------ payloads
def _slack_esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def payload(kind, ev):
    lines = ev["lines"] + ([f"... and {ev['more']} more"] if ev.get("more") else [])
    if kind == "slack":                    # incoming webhook: text (mrkdwn); untrusted text is escaped
        txt = f"*{_slack_esc(ev['title'])}*"
        if ev["text"]:
            txt += f"\n{_slack_esc(ev['text'])}"
        txt += "".join(f"\n• {_slack_esc(x)}" for x in lines)
        if ev["url"]:
            txt += f"\n<{_slack_esc(ev['url'])}|Open>"
        return {"text": txt}
    if kind == "teams":                    # Teams "workflow" webhook (Power Automate): an Adaptive Card message
        body = [{"type": "TextBlock", "text": ev["title"], "weight": "Bolder", "size": "Medium", "wrap": True}]
        if ev["text"]:
            body.append({"type": "TextBlock", "text": ev["text"], "wrap": True})
        if lines:
            body.append({"type": "TextBlock", "text": "\n".join(f"- {x}" for x in lines), "wrap": True})
        card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
                "body": body}
        if ev["url"]:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open", "url": ev["url"]}]
        return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                                    "contentUrl": None, "content": card}]}
    return {"source": "ecadlib", "event": ev["event"], "title": ev["title"], "text": ev["text"], "lines": ev["lines"],
            "more": ev.get("more", 0), "url": ev["url"], "data": ev["data"], "time": ev.get("time") or _now()}


def _now():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ sending
def http_sender(url, body, headers, timeout):
    """POST body (bytes) -> HTTP status. Network errors raise OSError. Redirects are not followed (urllib would resend the
    Authorization header to any host the 30x names, and turn the POST into a GET): a 3xx is returned as a failure."""
    import urllib.error, urllib.request
    from .forge import no_redirect_opener
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with no_redirect_opener().open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _url_ok(url):
    u = urlsplit(url)
    return u.scheme == "https" and bool(u.hostname) or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"))


def send(rules, events, env=None, sender=None, sleep=time.sleep, log=None, clock=time.monotonic, max_seconds=MAX_SECONDS):
    """Post each event to every target that wants it. Never raises. -> [{target, event, result, attempts}]."""
    import os
    log = log or (lambda *a: None)
    try:
        cfg = settings(rules)
    except Exception as e:                          # a broken [notify] must not stop the caller
        log(f"notify: settings unusable: {type(e).__name__}: {e}")
        return [{"target": "-", "event": "-", "result": f"error: {type(e).__name__}"}]
    env = os.environ if env is None else env
    sender = sender or http_sender
    out, start = [], clock()
    retries, timeout = int(cfg.get("retries", 3)), float(cfg.get("timeout_s", 10))
    backoff = float(cfg.get("backoff_s", 2.0))
    for ev in events or []:
        ev = dict(ev, time=ev.get("time") or _now())
        for t in cfg["targets"]:
            if ev["event"] not in t["events"]:
                continue
            rec = {"target": t["name"], "event": ev["event"], "attempts": 0}
            out.append(rec)
            url = (env.get(t["url_env"]) or "").strip()
            if not url:
                rec["result"] = f"skipped: ${t['url_env']} is not set"
                log(f"notify: {t['name']}: {rec['result']}")
                continue
            if not _url_ok(url):
                rec["result"] = f"skipped: ${t['url_env']} is not an https:// URL"
                log(f"notify: {t['name']}: {rec['result']}")
                continue
            host = urlsplit(url).hostname
            headers = {"Content-Type": "application/json", "User-Agent": "ecadlib-notify"}
            if t["token_env"]:
                tok = env.get(t["token_env"])
                if not tok:
                    rec["result"] = f"skipped: ${t['token_env']} is not set"
                    log(f"notify: {t['name']}: {rec['result']}")
                    continue
                headers["Authorization"] = f"Bearer {tok}"
            try:
                body = json.dumps(payload(t["type"], ev), ensure_ascii=False, default=str).encode()
            except Exception as e:
                rec["result"] = f"error: payload: {type(e).__name__}: {e}"
                continue
            if clock() - start > max_seconds:           # slow or hanging hooks: the agent waits no longer
                rec["result"] = f"skipped: the {max_seconds:g} s notify time budget is used up"
                log(f"notify: {t['name']}: {rec['result']}")
                continue
            for attempt in range(retries + 1):
                rec["attempts"] = attempt + 1
                try:
                    status = int(sender(url, body, headers, timeout))
                    err = None if 200 <= status < 300 else f"HTTP {status}"
                    transient = status == 429 or status >= 500
                except Exception as e:                    # network, DNS, TLS, timeout: try again
                    err, transient = f"{type(e).__name__}", True
                if err is None:
                    rec["result"] = "sent"
                    break
                rec["result"] = f"failed: {err} from {host}"
                wait = backoff * (2 ** attempt)
                if not transient or attempt == retries or clock() - start + wait > max_seconds:
                    log(f"notify: {t['name']}: {rec['result']} after {attempt + 1} attempt(s)")
                    break
                sleep(wait)
    return out


# ------------------------------------------------------------------ CLI
def main(argv=None):
    from . import rules as RU
    ap = argparse.ArgumentParser(prog="ecadlib notify", description="webhook notifications ([notify] in ecadlib.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, h in (("show", "targets, their events and whether their environment variables are set"),
                    ("test", "send a test message to the configured targets")):
        p = sub.add_parser(name, help=h)
        p.add_argument("--rules", help="ecadlib.toml (default: found from here)")
        if name == "test":
            p.add_argument("--event", choices=EVENTS, default="batch_ready")
            p.add_argument("--target", help="only this target (name)")
    a = ap.parse_args(argv)
    import os
    rules = RU.load(a.rules)
    cfg = settings(rules)
    if not cfg["targets"]:
        print(f"no [notify] targets in {rules['_file'] or 'the rules (no ecadlib.toml found)'}")
        return 0 if a.cmd == "show" else 1
    if a.cmd == "show":
        for t in cfg["targets"]:
            st = "set" if os.environ.get(t["url_env"]) else "NOT SET"
            print(f"{t['name']:<20} {t['type']:<6} ${t['url_env']} ({st})  events: {', '.join(sorted(t['events']))}")
        return 0
    if a.target:
        rules = dict(rules, notify=dict(rules["notify"], targets=[
            t for i, t in enumerate(rules["notify"]["targets"]) if (t.get("name") or f"{t['type']}{i + 1}") == a.target]))
        if not rules["notify"]["targets"]:
            print(f"no target named {a.target!r}", file=sys.stderr)
            return 1
    ev = event(a.event, f"ecadlib test notification ({a.event})", "If you can read this, the webhook works.",
               ["sent by `ecadlib notify test`"])
    res = send(rules, [ev], log=lambda m: print(m, file=sys.stderr))
    for r in res:
        print(f"{r['target']:<20} {r['result']}")
    return 0 if res and all(r["result"] == "sent" for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
