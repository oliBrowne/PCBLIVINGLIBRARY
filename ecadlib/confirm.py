"""What an assistant asks for and only the person can grant (security review 2026-11-23, 2.3).

    ecadlib confirm [--workspace DIR]            what is waiting for your answer
    ecadlib confirm ID [--workspace DIR] [--user "Ann Lee <ann@example.com>"]

Over MCP the calling model used to pass the person's name, "release anyway" and the approved dollar amount as plain
arguments: it could write its own approval. Now such a call only asks:

  release anyway (release_check allow)   the request is written down; `ecadlib confirm ID` shows why each part is held,
                                         asks for your reason and releases it as you (review_cli.release_anyway: who /
                                         when / why go into the library's ledger). The tool call then succeeds on retry.
  sign-off (approve_part)                the same: `ecadlib confirm ID` records your approve / reject as you.
  a paid run (ai_read, datasheet_batch,  runs at once only within a cap you set yourself ([datasheet] max_cost_usd in
  house_style_guide, visual_check,       ecadlib.toml); otherwise `ecadlib confirm ID` shows the quote and grants that
  apply_house_style ai)                  amount once, for a day. The model's own approved_usd is never the approval.

A request is a file in <workspace>/confirm/ (what was asked: not proof of anything). A grant is a file in this user's
settings folder (userconfig.config_dir()/confirmed/), outside every workspace and --lib folder, so no MCP file
argument reaches it. `ecadlib confirm ID` answers only at a terminal, where a person types; the local page can show the
same requests and grant them with pending() and grant() from a request that carries its token."""
import argparse, contextvars, datetime, hashlib, json, os, secrets, sys
from pathlib import Path

REQUESTS = "confirm"                           # <workspace>/confirm/<id>.json
GRANTS = "confirmed"                           # <user settings folder>/confirmed/<id>.json
KINDS = ("release_anyway", "approve_part", "spend")
VALID_S = 24 * 3600                            # a request, and a granted amount, lapse after a day
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read out and typed by a person
_CALLER = contextvars.ContextVar("ecadlib_confirm_caller", default=None)   # {"tool": name} while an MCP tool call runs


class NeedsPerson(ValueError):
    """The call was not carried out: it needs the person's own answer (`ecadlib confirm ID`). The text tells the
    assistant what to ask the person to do; .request is what was written down."""

    def __init__(self, msg, request=None):
        super().__init__(msg)
        self.request = request


def over_mcp(tool):
    """Context manager: the calls made inside come from an MCP client (mcp_server.call_tool)."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        tok = _CALLER.set({"tool": str(tool)})
        try:
            yield
        finally:
            _CALLER.reset(tok)
    return cm()


def caller():
    """{"tool": name} when this call was asked for over MCP, else None (the local page, the command line, a menu)."""
    return _CALLER.get()


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _fresh(stamp):
    try:
        return (_now() - datetime.datetime.fromisoformat(str(stamp))).total_seconds() < VALID_S
    except (TypeError, ValueError):
        return False


def _digest(kind, key):
    return hashlib.sha256(json.dumps([kind, key], sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _read(f):
    try:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write(f, data):
    from .fsutil import write_text_atomic
    Path(f).parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(Path(f), json.dumps(data, indent=2, sort_keys=True, default=str))


def _valid_id(rid):
    rid = str(rid or "").strip().upper()
    return rid if rid and len(rid) <= 12 and all(c in _ALPHABET for c in rid) else None


def grants_dir():
    from .userconfig import config_dir
    return config_dir() / GRANTS


# ---------------------------------------------------------------- requests (written for an MCP call; not proof)
def pending(ws_root):
    """The requests of this workspace still waiting for the person, oldest first."""
    d = Path(ws_root) / REQUESTS
    out = []
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        files = []
    for f in files:
        r = _read(f)
        if r and r.get("kind") in KINDS and _valid_id(r.get("id")) == f.stem and _fresh(r.get("asked_at")):
            out.append(r)
    return sorted(out, key=lambda r: str(r.get("asked_at")))


MAX_TEXT = 500                                 # a comment, a role, a name: longer is not a request, it is a page


def _odd(ch):
    """A character that can change what a terminal shows rather than be shown: a control or escape code, a line
    break, a direction mark, an unassigned or private-use code point."""
    import unicodedata
    return unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def plain(text):
    """Text as it may be printed for the person: every such character becomes "?" (second review 2026-11-23, C2:
    an escape code in an approve_part comment redrew the line, and "yes" recorded a sign-off other than the one
    on screen). A request file is not trusted, so this is applied to all of it, whatever wrote it."""
    return "".join("?" if _odd(c) else c for c in str(text))


def _refuse_odd(value, where="request"):
    """ValueError when what an MCP call passed (a part, role, comment, target ...) holds such a character or is
    over-long: it is not written down, so nobody is asked to answer it."""
    if isinstance(value, dict):
        for k, v in value.items():
            _refuse_odd(v, str(k))
    elif isinstance(value, (list, tuple)):
        for v in value:
            _refuse_odd(v, where)
    elif isinstance(value, str):
        if any(_odd(c) for c in value):
            raise ValueError(f"not asked: the {where} holds a control character or a line break (the person is "
                             "shown it at a terminal, so it must be plain text on one line)")
        if len(value) > MAX_TEXT and where not in ("plan", "bom"):
            raise ValueError(f"not asked: the {where} is longer than {MAX_TEXT} characters")


def request(ws_root, kind, key, show, tool=None):
    """Write down what an MCP call asked for (one request per same question: asked again, it keeps its id, unless
    what the person would be shown changed, an amount above all: then the old request is dropped and a new id
    given, so an id they were told never comes to mean something else) -> the request. key: what a grant is bound
    to; show: what the person is shown."""
    _refuse_odd(key)
    ws_root = Path(ws_root).resolve()
    dg = _digest(kind, key)
    old = next((r for r in pending(ws_root) if r.get("digest") == dg), None)
    if old and old.get("show") != json.loads(json.dumps(show, default=str)):
        _drop(ws_root, old["id"])
        old = None
    rid = old["id"] if old else "".join(secrets.choice(_ALPHABET) for _ in range(6))
    r = {"id": rid, "kind": kind, "digest": dg, "key": key, "show": show, "workspace": str(ws_root),
         "tool": tool or (caller() or {}).get("tool") or "", "asked_at": _now().isoformat(timespec="seconds")}
    _write(ws_root / REQUESTS / f"{rid}.json", r)
    return r


def how(ws_root, r):
    """The command the person runs, as the refusal gives it to the assistant."""
    from .cliutil import shell_word
    return f"ecadlib confirm {r['id']} --workspace {shell_word(str(Path(ws_root).resolve()))}"


def _drop(ws_root, rid):
    try:
        (Path(ws_root) / REQUESTS / f"{rid}.json").unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- grants (written only where the person answers)
def _grant_spend(r, who, usd):
    g = {"id": r["id"], "kind": "spend", "digest": r["digest"], "workspace": r["workspace"], "by": who,
         "at": _now().isoformat(timespec="seconds"), "approved_usd": usd,
         "over_limit_ok": bool(r["show"].get("over_limit")), "price_unknown_ok": not r["show"].get("price_known", True),
         "used_at": None}
    _write(grants_dir() / f"{r['id']}.json", g)
    return g


def _spend_grant(ws_root, digest):
    """The person's unused grant, not older than a day, for this workspace and question, or None."""
    try:
        files = sorted(grants_dir().glob("*.json"))
    except OSError:
        return None
    for f in files:
        g = _read(f)
        if g and g.get("kind") == "spend" and g.get("digest") == digest and not g.get("used_at") \
                and g.get("workspace") == str(Path(ws_root).resolve()) and _fresh(g.get("at")):
            return f, g
    return None


def spend(ws_root, q, asked_usd, what):
    """A paid step asked for over MCP: -> (approved_usd, over_limit_ok, price_unknown_ok) for ai_cost.approve, taken
    from the person and never from the call: their grant of this quote (`ecadlib confirm`, used once), else the cap
    they set themselves in the rules file ([datasheet] max_cost_usd: the model's amount counts only up to it, and
    never for a higher cost or an unknown price). Otherwise the request is written down and NeedsPerson raised."""
    from . import ai_cost as AC
    tool = (caller() or {}).get("tool") or "a paid step"
    if asked_usd is None and q["price_known"]:   # no amount at all: the quote first, in ai_cost.approve's words
        raise ValueError(f"approve the cost first: about {AC.money(q['usd'])}, at most {AC.money(AC.up(q['usd_max']))}")
    key = {"tool": tool, "what": str(what)}
    hit = _spend_grant(ws_root, _digest("spend", key))
    if hit:
        f, g = hit
        fits = g.get("price_unknown_ok") if not q["price_known"] else \
            isinstance(g.get("approved_usd"), (int, float)) and q["usd_max"] <= g["approved_usd"] + AC.TOLERANCE
        if fits:
            g["used_at"] = _now().isoformat(timespec="seconds")       # one run per grant
            _write(f, g)
            return g.get("approved_usd"), bool(g.get("over_limit_ok")), bool(g.get("price_unknown_ok"))
    if q["price_known"] and not q["over_limit"] and q.get("limit_name") == "max_cost_usd" and q.get("limit_usd"):
        try:                                   # the person's own cap, set outside MCP: the run may start under it
            asked = AC.cap_usd(asked_usd, "approved_usd") if asked_usd is not None else None
        except ValueError:
            asked = None
        return (min(asked, float(q["limit_usd"])) if asked is not None else None), False, False
    r = request(ws_root, "spend", key, {"text": q.get("text") or "", "model": q.get("model"), "parts": q.get("parts"),
                                        "usd_max": q.get("usd_max"), "approve_usd": q.get("approve_usd"),
                                        "price_known": bool(q["price_known"]), "over_limit": bool(q["over_limit"]),
                                        "what": str(what)}, tool)
    cost = f"at most {AC.money(q['approve_usd'])}" if q["price_known"] else "a cost ecadlib cannot estimate"
    raise NeedsPerson(f"Not started: {tool} costs money ({cost}) and the amount an assistant passes is not the "
                      f"person's yes. Show them the quote and ask them to approve it themselves: `{how(ws_root, r)}` "
                      "in a terminal (it shows the quote and asks for their yes), or their own cap, [datasheet] "
                      f"max_cost_usd, in ecadlib.toml. Then call {tool} again.", r)


def ask_release(ws_root, plan, bom, parts):
    """release_check allow over MCP: the request for the person, raised as NeedsPerson."""
    r = request(ws_root, "release_anyway", {"plan": str(plan), "parts": sorted(parts)},
                {"bom": str(bom), "plan": str(plan), "parts": sorted(parts)})
    raise NeedsPerson(f"Not released: {', '.join(sorted(parts))} {'is' if len(parts) == 1 else 'are'} held, and "
                      "releasing a held part anyway is the person's decision: an assistant cannot make it or name "
                      f"who made it. Ask them to run `{how(ws_root, r)}` in a terminal (it shows why each part is held "
                      "and asks for their reason), or to release it on the ecadlib page or with `ecadlib review`. "
                      "Then call release_check again.", r)


def ask_decision(ws_root, plan, bom, part, decision, role="", comment=""):
    """approve_part over MCP: the request for the person, raised as NeedsPerson."""
    key = {"plan": str(plan), "part": part, "decision": decision, "role": role or "", "comment": comment or ""}
    r = request(ws_root, "approve_part", key, dict(key, bom=str(bom)))
    raise NeedsPerson(f"Not recorded: a sign-off ({decision} {part}) is the person's own decision under their own "
                      "name: an assistant cannot give it or name who gave it. Ask them to run "
                      f"`{how(ws_root, r)}` in a terminal, or to decide on the ecadlib page or with `ecadlib approve`. "
                      "Then call approval_status to see it.", r)


# ---------------------------------------------------------------- the person's answer
def describe(r):
    """Plain lines saying what a request asks, with what is true now (the hold reasons as the plan has them)."""
    from . import messages as M
    s, lines = r.get("show") or {}, []
    if r["kind"] == "release_anyway":
        lines.append(f"Release anyway, asked by your assistant ({r.get('tool') or 'MCP'}): {', '.join(s.get('parts') or [])}")
        try:
            items = {i["name"]: i for i in json.loads(Path(s["plan"]).read_text(encoding="utf-8"))}
        except (OSError, ValueError, KeyError, TypeError):
            items = {}
        for n in s.get("parts") or []:
            it = items.get(n)
            if not it:
                lines.append(f"  {n}: no longer in the plan")
            elif it.get("status") != "held":
                lines.append(f"  {n}: not held any more ({it.get('status')})")
            else:
                lines.append(f"  {n} is held:")
                for e in M.explain_all(it.get("hold_reasons") or []):
                    lines.append(f"    - {e['text']} {e.get('note') or ''}".rstrip())
        lines.append(f"  plan: {s.get('plan')}")
    elif r["kind"] == "approve_part":
        lines.append(f"Sign-off, asked by your assistant ({r.get('tool') or 'MCP'}): {s.get('decision')} {s.get('part')}"
                     + (f" as {s['role']}" if s.get("role") else "") + (f", comment: {s['comment']}" if s.get("comment") else ""))
        lines.append(f"  plan: {s.get('plan')}")
    else:
        lines.append(f"A paid step, asked by your assistant ({r.get('tool') or 'MCP'}) for {s.get('what')}:")
        lines += [f"  {t}" for t in str(s.get("text") or "").splitlines() or [""]]
    return [plain(x) for line in lines for x in str(line).split("\n")]


def asks(r, who):
    """What "yes" does, in the question itself and built from the request's own fields (never from free text), so
    the person answers what is recorded whatever the lines above it show."""
    s = r.get("show") or {}
    if r["kind"] == "approve_part":
        d = str(s.get("decision"))
        d = {"approve": "APPROVE", "reject": "REJECT"}.get(d) or f"the decision {plain(d)!r}"
        return f"record {d} of {plain(s.get('part'))}" + (f" (role {plain(s['role'])})" if s.get("role") else "") + f" as {plain(who)}"
    tool = plain(r.get("tool") or "the paid step")
    if s.get("price_known"):
        return f"allow {tool} to spend up to {_money(s.get('approve_usd'))}, once"
    return f"allow {tool} to run once without a cost estimate"


def grant(ws_root, rid, user=None, reason="", log=print):
    """Carry out one request as the person: the release anyway (their reason is required), the sign-off, or the grant
    of the quoted amount. Called only where a person answered: `ecadlib confirm` at a terminal, the local page's own
    request. -> {"id", "kind", "result"}; ValueError when it cannot be done."""
    from . import approvals as AP
    rid = _valid_id(rid)
    r = next((x for x in pending(ws_root) if x["id"] == rid), None) if rid else None
    if not r:
        raise ValueError(f"no request {rid or ''} is waiting in {ws_root} (it was answered, or is older than a day): "
                         "`ecadlib confirm` lists the ones that are")
    s = r["show"]
    if r["kind"] != "spend":                   # a request file is not trusted: its plan must be this workspace's
        root, plan = Path(ws_root).resolve(), Path(str(s.get("plan") or "")).resolve()
        if root not in plan.parents or not plan.is_file():
            raise ValueError(f"request {r['id']} names a plan that is not in {ws_root}: not carried out")
    if r["kind"] == "release_anyway":
        from . import review_cli as RV
        reason = " ".join(str(reason or "").split())
        if not reason:
            raise ValueError("not released: a release anyway needs your reason")
        plan, result = Path(s["plan"]), {}
        names = {i["name"]: i.get("status") for i in json.loads(plan.read_text(encoding="utf-8"))}
        for n in s["parts"]:
            if names.get(n) != "held":
                result[n] = names.get(n) or "not in the plan"
                continue
            it = RV.release_anyway(plan, n, user, reason, via="ecadlib confirm (asked over MCP)")
            result[n] = it.get("status")
            log(f"  {n}: {'released anyway' if it.get('status') != 'held' else 'still held: ' + '; '.join(it.get('hold_reasons') or [])}")
    elif r["kind"] == "approve_part":
        argv = [s["plan"], s["decision"], "--part", s["part"], "-m", s.get("comment") or ""]
        argv += ["--user", user] if user else []
        argv += ["--role", s["role"]] if s.get("role") else []
        result = {"exit": AP.main(argv)}
    else:
        usd = s.get("approve_usd")
        g = _grant_spend(r, AP.identity(user, cwd=ws_root), usd)
        result = {"approved_usd": usd, "valid_until": (_now() + datetime.timedelta(seconds=VALID_S)).isoformat(timespec="seconds"),
                  "by": g["by"]}
        log(f"  granted once, for a day: ask your assistant to call {r.get('tool') or 'the tool'} again")
    _drop(ws_root, r["id"])
    return {"id": r["id"], "kind": r["kind"], "result": result}


def main(argv=None, inp=input):
    ap = argparse.ArgumentParser(prog="ecadlib confirm", description="answer what your assistant asked for over MCP and "
                                 "only you can grant: a release anyway, a sign-off, a paid run")
    ap.add_argument("id", nargs="?", help="the request (the assistant names it); none: list what is waiting")
    ap.add_argument("--workspace", default=os.environ.get("ECADLIB_WORKSPACE") or "ecadlib_workspace")
    ap.add_argument("--user", help="who you are (default: $ECADLIB_USER, then git config user.name/email)")
    a = ap.parse_args(argv)
    ws = Path(a.workspace)
    waiting = pending(ws)
    if not a.id:
        if not waiting:
            print(f"Nothing is waiting for your answer in {ws}.")
            return 0
        for r in waiting:
            print(f"{r['id']}  (asked {r['asked_at']})")
            for line in describe(r):
                print(f"  {line}")
            print(f"  to answer: {how(ws, r)}")
        return 0
    rid = _valid_id(a.id)
    r = next((x for x in waiting if x["id"] == rid), None)
    if not r:
        print(f"ecadlib confirm: no request {a.id} is waiting in {ws} (it was answered, or is older than a day); "
              "`ecadlib confirm` lists the ones that are", file=sys.stderr)
        return 2
    if inp is input and (sys.stdin is None or not sys.stdin.isatty()):      # tests pass their own inp
        print("ecadlib confirm: this asks you, at a terminal; it does not answer for a script or an assistant. Run it "
              "yourself in a terminal window.", file=sys.stderr)
        return 2
    from .approvals import identity
    who = identity(a.user, cwd=ws if ws.is_dir() else None)
    for line in describe(r):
        print(line)
    try:
        if r["kind"] == "release_anyway":
            names = plain(", ".join(str(n) for n in r["show"].get("parts") or []))
            reason = " ".join(inp(f"Why may {names} go out anyway? (recorded as {plain(who)}; empty: not released)\n> ").split())
            if not reason:
                print("Not released.")
                return 2
        else:
            reason = ""
            what = asks(r, who)
            if inp(f"Type yes to {what} (anything else: no)\n> ").strip().lower() != "yes":
                print("Not granted.")
                return 2
    except (EOFError, KeyboardInterrupt):
        print("\nNot granted.")
        return 2
    try:
        res = grant(ws, r["id"], a.user, reason)
    except ValueError as e:
        print(f"ecadlib confirm: {e}", file=sys.stderr)
        return 2
    if r["kind"] == "approve_part":
        return int(res["result"].get("exit") or 0)
    if r["kind"] == "release_anyway" and any(v == "held" for v in res["result"].values()):
        return 2
    return 0


def _money(v):
    from .ai_cost import money
    return money(v) if isinstance(v, (int, float)) else "?"


if __name__ == "__main__":
    sys.exit(main())
