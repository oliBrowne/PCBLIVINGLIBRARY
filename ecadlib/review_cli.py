"""`ecadlib review`: go through the held parts one at a time, in the terminal.

    ecadlib review [PLAN] [--workspace W] [--part P] [--yes] [--offline] [--lib DIR ...] [--limit N]

For each held part: the reason in plain words and its fix (ecadlib.messages), the datasheet link, and - when a
symbol or footprint is missing or the pins and pads disagree - the best candidates (ecadlib.resolve). Then one
question:

    1-N  use that candidate (converted, written into the plan, the part re-checked)
    a    release anyway (like `ecadlib release-check --allow PART`; recorded with your name)
    d    open the datasheet in the browser
    s    skip to the next part         q  stop here

Only when stdin is a terminal. With --yes, or piped, nothing is asked and nothing changes: each held part is printed
with its reason and the commands that fix it (so it also works in a log or a script). Without PLAN, every library
plan in the workspace with held parts is reviewed (--workspace, else $ECADLIB_WORKSPACE, else ./ecadlib_workspace).
Exit code: 0 when nothing is held at the end, 2 otherwise."""
import argparse, json, os, re, sys, webbrowser
from pathlib import Path

from . import messages as M, term as TM

CHOICES = "1-{n} use a candidate   a release anyway   d open the datasheet   s skip   q quit"


def datasheet_url(item):
    """The part's datasheet link from its parameters (ComponentLinkNURL next to a "Datasheet" description, or a
    Datasheet / Datasheet URL parameter), or None."""
    pars = {p.get("name"): p.get("value") for p in item.get("parameters") or [] if isinstance(p, dict)}
    for k, v in pars.items():
        m = re.fullmatch(r"ComponentLink(\d+)Description", k or "")
        if m and str(v).strip().lower() == "datasheet" and pars.get(f"ComponentLink{m.group(1)}URL"):
            return pars[f"ComponentLink{m.group(1)}URL"]
    for k, v in sorted(pars.items()):           # a link without a description that is plainly a PDF
        if re.fullmatch(r"ComponentLink\d+URL", k or "") and str(v).lower().split("?")[0].endswith(".pdf") \
                and not pars.get(k.replace("URL", "Description")):
            return v
    for k in ("Datasheet", "Datasheet URL", "datasheet"):
        if str(pars.get(k) or "").startswith(("http://", "https://")):
            return pars[k]
    return None


def datasheet_page(reasons):
    """A page number named in the reasons ("page 34"), for #page=N."""
    for r in reasons:
        m = re.search(r"\bpage (\d+)", str(r))
        if m:
            return int(m.group(1))
    return None


def find_plans(workspace):
    ws = Path(workspace)
    return sorted(ws.glob("librarian/*/plan.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _cache_for(plan, given):
    if given:
        return given
    for c in (Path(plan).parent.parent.parent / "http_cache", Path("out/http_cache")):
        if c.is_dir():
            return str(c)
    return "out/http_cache"


def wants_candidates(item, why):
    from . import resolve as R
    return bool(R.missing(item)) or any(e["code"].startswith(("pins.", "files.")) for e in why)


def candidates(item, plan, src, limit, t):
    """{kind: [candidate]} for the missing half (or both when pins and pads disagree); errors become a note."""
    from . import resolve as R
    kinds = R.missing(item) or list(R.KINDS)
    out = {}
    for k in kinds:
        try:
            with TM.waiting(f"{k} candidates for {item['name']} (KiCad library index)", t.err):
                out[k] = R.candidates(item, k, Path(plan).parent, src, limit)
        except Exception as e:                      # network down, no cache: say so, keep going
            text, fix = M.friendly_error(e)
            t.note(f"No {k} candidates right now: {text}", fix + " (--offline uses what is cached)")
            out[k] = []
    return out


def show_part(t, item, plan, cands, src=None):
    """The card for one held part; returns the numbered candidate list [(kind, candidate)]."""
    why = M.explain_all(item.get("hold_reasons") or [])
    desc = item.get("description") or ""
    t.write(f"{t.status('held')}  {t.bold(item['name'])}" + (f"  {t.dim(desc)}" if desc else ""))
    for e in why:
        t.wrap(e["text"] + (f" {e['note']}" if e.get("note") else ""), 4)
        t.wrap(f"fix: {e['fix']}", 4)
    if t.verbose:
        for e in why:
            t.write(t.dim(f"    ({e['code']}: {e['raw']})"))
    url = datasheet_url(item)
    if url:
        t.write(f"    datasheet: {url}")
    numbered = []
    for kind, cs in cands.items():
        if not cs:
            t.write(f"    No {kind} candidates found." + " Add your KiCad libraries with --lib" + (", or drop --offline." if a_offline(src) else "."))
            continue
        t.write(f"    {kind} candidates:")
        rows = []
        for c in cs:
            numbered.append((kind, c))
            unit = "pins" if kind == "symbol" else "pads"
            fit = (t.status("ready", "fits") if c["match"] else t.status("skipped", "not compared") if c["match"] is None
                   else t.status("held", "pins and pads differ"))
            rows.append([f"{len(numbered)}", c["id"], f"{c['count'] if c['count'] is not None else '?'} {unit}", fit
                         + ("  (current)" if c.get("current") else "")])
        t.table(rows, indent=6, right=(0,))
    return numbered


def a_offline(src):
    return bool(getattr(getattr(src, "http", None), "offline", False))


def release_anyway(plan, name, user=None, reason=""):
    """release-check --allow for one part; returns the item after the re-check. reason: why the person released it
    (the Altium dialog asks for one), kept with who did it in the item's allow record."""
    from . import release_check as RC
    from .approvals import cli_plan_lock, identity, post_check, stamp_allow
    from .fsutil import write_text_atomic
    plan = Path(plan)
    with cli_plan_lock(plan):
        items = json.loads(plan.read_text(encoding="utf-8"))
        it = next(i for i in items if i["name"] == name)
        rows = RC.apply([it], plan.parent, [name])
        who = identity(user, cwd=plan.parent)
        stamp_allow([it], [name], who)
        if reason and it.get("allow"):
            it["allow"]["reason"] = reason
        post_check([it], plan.parent, rows)
        RC.record_releases([it], plan.parent, [name], who)    # change control: revision 1 of what was released
        write_text_atomic(plan, json.dumps(items, indent=2))
        from .librarian import refresh_review
        refresh_review(plan, items)
    from . import timing
    timing.mark_review(plan.parent, "allow")
    return it


def fix_commands(plan, item, numbered):
    cmds = []
    if numbered:
        kind, c = numbered[0]
        cmds.append(f"ecadlib resolve {plan} --part {item['name']} choose {kind} {c['id']}")
    codes = [e["code"] for e in M.explain_all(item.get("hold_reasons") or [])]
    if any(not c.startswith("files.") for c in codes):
        cmds.append(f"ecadlib release-check {plan} --allow {item['name']}")
    return cmds


def review_plan(plan, a, t, inp, interactive):
    """Review one plan's held parts. Returns (held at the start, still held, stopped?)."""
    from . import resolve as R
    held = [h["name"] for h in R.held(plan)]
    if a.part:
        held = [n for n in held if n == a.part]
    if not held:
        return 0, 0, False
    t.write(t.bold(f"{plan}: {M.plural(len(held), 'held part')}"))
    src = None
    still, stopped = 0, False
    for k, name in enumerate(held, 1):
        items = json.loads(Path(plan).read_text(encoding="utf-8"))
        item = next((i for i in items if i["name"] == name), None)
        if not item or item.get("status") != "held":
            continue
        t.write("")
        t.write(t.dim(f"[{k}/{len(held)}]"))
        why = M.explain_all(item.get("hold_reasons") or [])
        cands = {}
        if wants_candidates(item, why) and a.limit > 0:
            src = src or R.Sources(_cache_for(plan, a.cache), a.lib, a.offline)
            cands = candidates(item, plan, src, a.limit, t)
        numbered = show_part(t, item, plan, cands, src)
        if not interactive:
            for c in fix_commands(plan, item, numbered):
                t.write(f"    to fix: {c}")
            still += 1
            continue
        status = "held"
        while True:
            fixed_only = all(e["code"].startswith("files.") for e in M.explain_all(item.get("hold_reasons") or []))
            ask = CHOICES.format(n=len(numbered)) if numbered else CHOICES.split("   ", 1)[1]
            if fixed_only:                  # a missing / broken file cannot be released anyway: do not offer it
                ask = ask.replace("   a release anyway", "").replace("a release anyway   ", "")
            try:
                ans = inp(f"    {ask}\n    > ").strip().lower()
            except EOFError:
                ans = "q"
            if ans in ("", "s", "skip", "n"):
                break
            if ans in ("q", "quit", "exit"):
                stopped = True
                break
            if ans in ("d", "datasheet"):
                url = datasheet_url(item)
                if not url:
                    t.note("This part has no datasheet link.", "Add one to the BOM (a Datasheet column) and fill the library again.")
                    continue
                page = datasheet_page(item.get("hold_reasons") or [])
                full = url + (f"#page={page}" if page and "#" not in url else "")
                opened = False
                try:
                    opened = webbrowser.open(full)
                except Exception:
                    pass
                t.write(f"    {'opened' if opened else 'open this in your browser:'} {full}")
                continue
            if ans in ("a", "release", "allow"):
                it = release_anyway(plan, name, a.user)
                status = it.get("status")
                if status == "held":
                    left = M.explain_all(it.get("hold_reasons") or [])
                    t.write(f"    {t.status('held')}: {left[0]['text']} {left[0].get('note') or ''}".rstrip())
                    t.write(f"    fix: {left[0]['fix']}")
                    continue
                t.write(f"    {t.status(status, M.status_word(status))}: released anyway, recorded in the plan")
                break
            if ans.isdigit() and 1 <= int(ans) <= len(numbered):
                kind, c = numbered[int(ans) - 1]
                from .approvals import cli_plan_lock
                try:
                    with cli_plan_lock(plan), TM.waiting(f"{c['id']} (fetch and convert)", t.err):
                        r = R.choose(plan, name, kind, c["id"], src=src, pin_map=c.get("pin_map") or None, user=a.user)
                except Exception as e:
                    text, fix = M.friendly_error(e)
                    t.note(text, fix)
                    continue
                status = r["status"]
                t.write(f"    {kind} is now {c['id']}: {t.status(status, M.status_word(status))}")
                for e in M.explain_all(r["hold_reasons"]):
                    t.write(f"    {e['text']}  fix: {e['fix']}")
                for n in r["notes"]:
                    t.detail(f"    note: {M.clean(n)}")
                if status != "held":
                    break
                items = json.loads(Path(plan).read_text(encoding="utf-8"))
                item = next(i for i in items if i["name"] == name)
                continue
            t.write(f"    Not one of the choices: {ans!r}.")
        if status == "held":
            still += 1
        if stopped:
            still += sum(1 for _ in held[k:])
            break
    return len(held), still, stopped


def main(argv=None, inp=input):
    ap = argparse.ArgumentParser(prog="ecadlib review", description="go through held parts one at a time: why it is "
                                 "held in plain words, pick a candidate, release anyway, open the datasheet")
    ap.add_argument("plan", nargs="?", help="a library plan (plan.json); default: every plan in --workspace with held parts")
    ap.add_argument("--workspace", default=os.environ.get("ECADLIB_WORKSPACE") or "ecadlib_workspace")
    ap.add_argument("--part", help="only this part")
    ap.add_argument("--yes", action="store_true", help="never ask: print each held part, its reason and the fix commands")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir for candidates (repeatable)")
    ap.add_argument("--cache", help="HTTP cache (default: the workspace's http_cache)")
    ap.add_argument("--offline", action="store_true", help="candidates from the cache only")
    ap.add_argument("--limit", type=int, default=5, help="candidates shown per missing half (0: none)")
    ap.add_argument("--user", help="who releases anyway (default: $ECADLIB_USER, then git config)")
    a = ap.parse_args(argv)
    t = TM.Term()
    plans = [Path(a.plan)] if a.plan else find_plans(a.workspace)
    if not plans:
        t.err.error("review", f"There is no library plan in {a.workspace} yet.",
                "Fill a library from a BOM first: ecadlib librarian BOM.csv -o <workspace>/librarian/board")
        return 2
    tty = inp is not input or (sys.stdin is not None and sys.stdin.isatty())   # tests pass their own inp
    interactive = tty and not a.yes
    total = still = 0
    for plan in plans:
        n, s, stopped = review_plan(plan, a, t, inp, interactive)
        total, still = total + n, still + s
        if stopped:
            break
    if not total:
        t.summary(f"Nothing is held{' for ' + a.part if a.part else ''}: every part is ready or waiting for sign-off.",
                  next=[(f"ecadlib status --workspace {a.workspace}" if not a.plan else f"ecadlib release-check {plans[0]}", "")])
        return 0
    done = total - still
    head = (f"{M.plural(total, 'held part')} reviewed: {done} fixed, {still} still held" if interactive
            else f"{M.plural(still, 'part')} held")
    nxt = [] if interactive else [("ecadlib review" + (f" {a.plan}" if a.plan else f" --workspace {a.workspace}"),
                                   "in a terminal: pick a fix for each part")]
    if done:
        nxt.append((f"ecadlib release-check {plans[0]}", "check the whole library again"))
    t.summary(head, next=nxt)
    return 2 if still else 0


if __name__ == "__main__":
    sys.exit(main())
