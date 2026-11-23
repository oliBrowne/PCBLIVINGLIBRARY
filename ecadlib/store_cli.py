"""`ecadlib store`: the shared store of checked parts from your side (docs/shared-store.md).

    ecadlib store status [PLAN] [--offline]          the [store] settings, whether the store answers, and (with a
                                                     plan) how many parts came from it and which it has withdrawn
    ecadlib store get MAKER MPN [-o FILE]            one part's record: level, status, evidence; -o saves it
    ecadlib store share PLAN [-o DIR] [--by NAME] [PART ...]
                                                     write the record files of the ready parts that may be shared
                                                     (store_record.shareable) to DIR/parts/<shard>/<key_id>.json, show
                                                     what each holds, and the steps of a pull request. Sends nothing
    ecadlib store report PART|MAKER MPN [--plan PLAN] [--reason TEXT] [--print]
                                                     a pre-filled GitHub issue on the store's repository; nothing is
                                                     sent until you press Submit there (as `ecadlib report-bug`)
    ecadlib store-repo ...                           the store repository's own tools (ecadlib/store_repo.py)

ecadlib never uploads a record and never calls the GitHub API: share writes files, report builds a link."""
import argparse, json, re, sys, webbrowser
from pathlib import Path
from urllib.parse import quote, urlencode

RAW = re.compile(r"https://raw\.githubusercontent\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/([^/?#]+))?", re.I)


def github_repo(url):
    """(https://github.com/OWNER/REPO, branch) of a store kept on GitHub (a raw URL or a github.com URL), else None."""
    u = str(url or "").strip().rstrip("/")
    m = RAW.match(u)
    if m:
        return f"https://github.com/{m[1]}/{m[2]}", m[3] or "main"
    m = re.match(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/tree/([^/?#]+))?$", u, re.I)
    return (f"https://github.com/{m[1]}/{m[2]}", m[3] or "main") if m else None


def _rules(path, start="."):
    from . import rules as RU
    return RU.load(path, start=start)


def _store(rules, offline=False, cache=None):
    from . import store_client as SC
    from .firstrun import default_cache
    return SC.from_rules(rules, cache_dir=cache or default_cache(), offline=offline)


def _plan(path):
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"{path}: not a librarian plan (a JSON list of parts)")
    return [i for i in items if isinstance(i, dict) and i.get("name")]


def _say(out, *lines):
    """Print lines; text from the store (or anywhere) never carries terminal control characters (store_client.safe)."""
    from .store_client import safe
    for ln in lines:
        print(safe(ln), file=out)


# ------------------------------------------------------------------ status
def status(a, out):
    from . import release_check as RC, store_client as SC
    rules = _rules(a.rules, str(Path(a.plan).resolve().parent) if a.plan else ".")
    s = rules.get("store") or {}
    st = _store(rules, a.offline, a.cache)
    _say(out, f"store url : {s.get('url') or '(none: the shared store is off)'}",
         f"accept    : {s.get('accept')}", f"mode      : {s.get('mode')}",
         f"librarian : {'asks it first, for every part' if st.enabled else 'does not ask it'}")
    if not st.enabled:
        reach = "-"
    elif a.offline:
        reach = "not asked (--offline)"
    else:
        reach = "yes" if st.usable() else "no"
    _say(out, f"reachable : {reach}")
    rep = {"url": s.get("url") or "", "accept": s.get("accept"), "mode": s.get("mode"), "enabled": st.enabled,
           "reachable": reach}
    if a.plan:
        items = _plan(a.plan)
        mine = [i for i in items if SC.depends(i)]
        bad = {i["name"]: RC.store_problems(i, st) for i in mine} if st.enabled and not a.offline else {}
        bad = {k: v for k, v in bad.items() if v}
        checked = [i for i in items if (i.get("store_check") or {}).get("result")]
        _say(out, f"plan      : {a.plan}: {len(items)} part(s), {len(mine)} owing the shared store (built from it or "
                  f"released by its agreement), {len(checked)} compared with a store record, {len(bad)} no longer "
                  "standing (withdrawn, disputed, gone)")
        for n, why in bad.items():
            _say(out, f"  {n}: {'; '.join(w.split(': ', 1)[-1] for w in why)}")
        rep.update(parts=len(items), from_store=len(mine), compared=len(checked), withdrawn_or_disputed=bad)
    for t in st.trouble:
        _say(out, f"note: {t}")
    if a.json:
        print(json.dumps(rep, indent=1), file=out)
    return 0


# ------------------------------------------------------------------ get
def get(a, out):
    from . import store_record as SR
    rules = _rules(a.rules)
    st = _store(rules, a.offline, a.cache)
    if not st.enabled:
        _say(out, "The shared store is off ([store] url is empty, or accept = \"off\"): nothing to look up.")
        return 2
    hit = st.lookup(a.maker, a.mpn)
    for t in st.trouble:
        _say(out, f"note: {t}")
    if hit is None:
        _say(out, f"{a.maker} {a.mpn}: not in the shared store" + (" (or the store could not be read)" if st.trouble else ""))
        return 1
    rec = hit.record
    from .store_client import decide
    _say(out, f"{hit.key}  r{hit.rev}  {hit.level}  {hit.status}  ({hit.key_id})" + (f"\n  {hit.note}" if hit.note else ""),
         f"  this computer: {decide(hit, st.accept)} ([store] accept = \"{st.accept}\")",
         f"  package {(rec.get('part') or {}).get('package') or '?'}; footprint from "
         f"{(rec.get('footprint') or {}).get('source')}, symbol from {(rec.get('symbol') or {}).get('source')}; "
         f"{len((rec.get('symbol') or {}).get('pins') or [])} pins")
    ds = (rec.get("part") or {}).get("datasheet") or {}
    if ds.get("url"):
        _say(out, f"  datasheet {ds['url']}")
    for e in rec.get("evidence") or []:
        r = e.get("reader") or {}
        _say(out, f"  evidence: {e.get('kind')} by {e.get('by') or '?'} at {e.get('at')}, ecadlib {e.get('ecadlib')}"
                  + (f", read by {r.get('provider')} {r.get('model')}" if r.get("model") else ""))
    problems = SR.validate(rec)
    if problems:
        _say(out, f"  record checks: {len(problems)} problem(s): {problems[0]}")
    _say(out, "  What the store can say: the part passed the release check and the builds above agree. It cannot "
              "say the drawing is the datasheet's: compare before you order boards.")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        _say(out, f"record saved: {a.out}")
    return 0


# ------------------------------------------------------------------ share
def _fields(rec):
    """What a record holds, one short line per part (the drawings by size, the rest as they are)."""
    k, p, s, f, b = (rec.get(x) or {} for x in ("key", "part", "symbol", "footprint", "body"))
    ds = p.get("datasheet") or {}
    ev = (rec.get("evidence") or [{}])[0]
    rd = ev.get("reader") or {}
    return [f"key: maker {k.get('maker')!r}, part number {k.get('mpn')!r}",
            f"part: description {p.get('description')!r}, package {p.get('package')!r}"
            + (f", datasheet link {ds.get('url')}" + (" and its PDF's hash" if ds.get("sha256") else "") if ds else
               ", no datasheet link"),
            f"symbol: KiCad symbol text ({len(str(s.get('kicad_sym') or '').splitlines())} lines), "
            f"{len(s.get('pins') or [])} pins (number, name, type), from {s.get('source')}, licence {s.get('license')}",
            f"footprint: KiCad footprint text ({len(str(f.get('kicad_mod') or '').splitlines())} lines, no 3D model), "
            f"pad hash, from {f.get('source')}, licence {f.get('license')}"
            + (", the datasheet's package dimensions" if f.get("spec") else ""),
            f"body: height {b.get('height_mm')} mm" + (f", 3D model link {(b.get('model') or {}).get('url')}"
                                                         if b.get("model") else ""),
            f"pin map: {len(rec.get('pin_map') or {})} pins to pads",
            f"evidence: built by {ev.get('by')!r}" + (" (filled from your pull request)" if not ev.get("by") else "")
            + f" at {ev.get('at')}, ecadlib {ev.get('ecadlib')}"
            + (f", read by {rd.get('provider')} {rd.get('model')}" if rd.get("model") else "")
            + f", checks passed: {', '.join(ev.get('checks') or [])}"]


def share(a, out):
    from . import store_keys as SK, store_record as SR
    plan = Path(a.plan)
    base = plan.parent
    rules = _rules(a.rules, str(base.resolve()))
    items = _plan(plan)
    if a.part:
        names = {n.casefold() for n in a.part}
        missing = sorted(names - {i["name"].casefold() for i in items})
        if missing:
            _say(sys.stderr, f"ecadlib store share: not in {plan.name}: {', '.join(missing)}")
            return 2
        items = [i for i in items if i["name"].casefold() in names]
    dest = Path(a.out)
    wrote, refused = [], []
    for it in items:
        try:
            rec = SR.from_item(it, base, rules, by=a.by or "")
        except SR.NotShareable as e:
            refused.append((it["name"], e.reasons))
            continue
        except Exception as e:                       # one part that cannot be read never stops the rest
            refused.append((it["name"], [f"the record could not be made ({type(e).__name__}: {e})"]))
            continue
        bad = SR.validate(rec)
        if bad:
            refused.append((it["name"], [f"the record fails its own checks: {bad[0]}"]))
            continue
        k = rec["key"]["key"]
        f = dest / "parts" / SK.shard(k) / f"{SK.key_id(k)}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        wrote.append((it["name"], f, rec))
    for n, f, rec in wrote:
        _say(out, f"shareable: {n} -> {f.relative_to(dest).as_posix()}", *(f"    {x}" for x in _fields(rec)))
    for n, why in refused:
        _say(out, f"not shared: {n}: {why[0]}" + (f" (+{len(why) - 1} more)" if len(why) > 1 else ""))
    _say(out, "", f"{len(wrote)} record(s) written to {dest}, {len(refused)} part(s) not shareable.",
         "Nothing has been sent. A record never holds your BOM, project or file names, your name or e-mail, internal "
         "or supplier part numbers, stock or price fields, or a private datasheet link.")
    if not wrote:
        return 1 if refused else 0
    gh = github_repo((rules.get("store") or {}).get("url"))
    if gh:
        repo, branch = gh
        _say(out, "", "To offer them to the store (your own GitHub account; your GitHub name is public on the pull "
                      "request):",
             f"  1. open {repo} and press Fork (once)",
             f"  2. in your fork, Add file > Upload files, and drop the parts/ folder from {dest}",
             "  3. commit to a new branch, then open a pull request to the store; its checks rebuild every record "
             "and run the release check on it",
             f"  (pull requests: {repo}/pulls)")
    else:
        _say(out, "", "No GitHub store is set ([store] url): give the parts/ folder to whoever keeps your store "
                      "(`ecadlib store-repo add` adds it there).")
    return 0


# ------------------------------------------------------------------ report
def report_url(repo, title, body):
    return f"{repo}/issues/new?" + urlencode({"title": title, "body": body}, quote_via=quote)


def report(a, out, inp=None):
    from . import __version__, store_client as SC, store_keys as SK
    rules = _rules(a.rules, str(Path(a.plan).resolve().parent) if a.plan else ".")
    what = list(a.what)
    src = kid = rev = None
    if len(what) == 1:                               # a part of the plan
        if not a.plan:
            _say(sys.stderr, "ecadlib store report: give MAKER MPN, or a PART with --plan PLAN")
            return 2
        it = next((i for i in _plan(a.plan) if i["name"].casefold() == what[0].casefold()), None)
        if it is None:
            _say(sys.stderr, f"ecadlib store report: {what[0]} is not a part of {a.plan}")
            return 2
        par = {p.get("name"): p.get("value") for p in it.get("parameters") or [] if isinstance(p, dict)}
        maker, mpn = str(par.get("Manufacturer") or ""), str(par.get("Manufacturer Part Number") or "")
        src = next((s for s in (it.get("source") or {}).values() if SC.parse_source(s)), None)
        if src:
            kid, rev = SC.parse_source(src)
        key = ((it.get("store") or {}).get("keys") or {}).get(kid) or SK.key(maker, mpn)
    elif len(what) == 2:
        maker, mpn = what
        key = SK.key(maker, mpn)
    else:
        _say(sys.stderr, "ecadlib store report: give one PART (with --plan) or MAKER MPN")
        return 2
    if not key:
        _say(sys.stderr, "ecadlib store report: no part number to report")
        return 2
    kid = kid or SK.key_id(key)
    title = f"Report: {key}" + (f" r{rev}" if rev else "")
    body = "\n".join([f"Part: {key}", f"Record: parts/{SK.shard(key)}/{kid}.json" + (f" (revision {rev})" if rev else ""),
                      "", "What is wrong (a pad size, a pin, the package; the datasheet page that shows it):",
                      a.reason or "", "", f"ecadlib {__version__}"])
    gh = github_repo((rules.get("store") or {}).get("url"))
    _say(out, title, body, "")
    if not gh:
        _say(out, "No GitHub store is set ([store] url): send this to whoever keeps your store.")
        return 0
    url = report_url(gh[0], title, body)
    _say(out, "Nothing has been sent yet. Opening the link passes this text to GitHub so it can show it to you; it "
              "becomes a public issue only when you press Submit (a free GitHub account is needed).", f"Link: {url}")
    if a.print:
        return 0
    if not a.yes:
        if inp is None:
            if not sys.stdin or not sys.stdin.isatty():
                return 0
            inp = input
        if inp("Open GitHub in your browser now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
            return 0
    _open(url)
    return 0


def _open(url):
    webbrowser.open(url)


# ------------------------------------------------------------------ main
def main(argv=None, out=None):
    out = out or sys.stdout
    from .firstrun import default_cache
    ap = argparse.ArgumentParser(prog="ecadlib store", description="The shared store of checked parts: settings, one "
                                 "part's record, sharing your parts as files, reporting a bad part. Sends nothing.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--rules", help="ecadlib.toml (default: searched from the plan's folder, else here)")
    p = sub.add_parser("status", parents=[common], help="the [store] settings, whether the store answers, and the "
                       "plan's parts from it", description="The [store] settings, whether the store answers, and "
                       "(with a plan) how many parts came from it and which it has withdrawn or disputes.")
    p.add_argument("plan", nargs="?", help="a librarian plan.json (optional)")
    p.add_argument("--offline", action="store_true", help="ask the store nothing (its cache only)")
    p.add_argument("--cache", default=default_cache())
    p.add_argument("--json", action="store_true", help="also print the result as JSON")
    p = sub.add_parser("get", parents=[common], help="one part's record: level, status, evidence",
                       description="One part's record from the store: its level, status and evidence; -o saves it.")
    p.add_argument("maker")
    p.add_argument("mpn")
    p.add_argument("-o", "--out", help="save the record (JSON) here")
    p.add_argument("--offline", action="store_true", help="the store's cache only")
    p.add_argument("--cache", default=default_cache())
    p = sub.add_parser("share", parents=[common], help="write the shareable parts' record files; sends nothing",
                       description="Write the record files of the plan's ready parts that may be shared to "
                                   "DIR/parts/<shard>/<key_id>.json and show what each holds. Sends nothing.")
    p.add_argument("plan")
    p.add_argument("part", nargs="*", help="only these parts (default: every part of the plan)")
    p.add_argument("-o", "--out", default="store_share", help="folder for the record files (default store_share)")
    p.add_argument("--by", default="", help="your GitHub name, as the record's contributor (one contributor counts once)")
    p = sub.add_parser("report", parents=[common], help="a pre-filled GitHub issue about a store part; you press Submit",
                       description="A pre-filled GitHub issue on the store's repository about a wrong part; nothing "
                                   "is sent until you press Submit on GitHub.")
    p.add_argument("what", nargs="+", metavar="PART|MAKER MPN", help="a part of --plan, or the maker and part number")
    p.add_argument("--plan", help="the plan that holds PART")
    p.add_argument("--reason", default="", help="what is wrong (a pad size, a pin, the package)")
    p.add_argument("--print", action="store_true", help="only print the text and the link; open nothing")
    p.add_argument("--yes", "-y", action="store_true", help="open GitHub without asking")
    a = ap.parse_args(argv)
    try:
        return {"status": status, "get": get, "share": share, "report": report}[a.cmd](a, out)
    except (OSError, ValueError) as e:
        _say(sys.stderr, f"ecadlib store {a.cmd}: {e}")
        return 2


def store_repo_main(argv=None):
    """`ecadlib store-repo ...`: the store repository's maintainer and CI tools (ecadlib/store_repo.py), loaded only
    when asked for."""
    try:
        from . import store_repo
    except ImportError as e:
        ap = argparse.ArgumentParser(prog="ecadlib store-repo", description="The shared store repository's own tools "
                                     "(init, add, index, verify, withdraw, dispute, snapshot): not in this ecadlib.")
        ap.add_argument("cmd", nargs="?")
        ap.add_argument("rest", nargs=argparse.REMAINDER)
        ap.parse_args(argv)
        _say(sys.stderr, f"ecadlib store-repo: not available in this ecadlib ({e})")
        return 2
    return store_repo.main(argv)


if __name__ == "__main__":
    sys.exit(main())
