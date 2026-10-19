"""`ecadlib status`: one screen about a workspace - parts in the library plans, held, waiting for sign-off, the last
triage and the last upkeep run. Read-only: it never creates the workspace or any file in it,
and a damaged file is skipped (and named) instead of stopping the summary.

    ecadlib status [--workspace W] [--json]        (W defaults to $ECADLIB_WORKSPACE, then ./ecadlib_workspace)

--json prints one object (stable keys, see docs/command-line.md):
    {"workspace", "exists", "rules", "boms", "plans": [{"plan", "parts", "ready", "held", "awaiting_approval",
     "duplicates", "updated"}], "totals": {...same counts...}, "last_triage": {...} | null,
     "last_upkeep": {...} | null, "problems": [text]}
plus "datasheet": {"answers", "parts", "input_tokens", "output_tokens", "usd", "usd_estimated", "escalations",
"by_model"} once a Claude datasheet read has been paid for in the workspace.

Each plan row also has "timing" (its last BOM run, ecadlib.timing: {"started", "finished", "steps": {step: seconds},
"work_seconds", "total_seconds"} or null) and "review" (see review_stats); the top level has "last_run" (the newest
plan's timing, with "plan") and "review" (review_stats summed over the plans, top held reasons across them)."""
import argparse, datetime, json, os, sys
from pathlib import Path

COUNTS = ("ready", "held", "awaiting_approval", "duplicate")
BUILT = ("ready", "held", "awaiting_approval")         # what the librarian built (not duplicates, not workspace parts)
REVIEW_KEYS = ("built", "held", "passed_first_time", "approved", "rejected", "changed")


def review_stats(items, folder):
    """How parts fare in review (#45). Of the parts the librarian built:
    held: the release check holds them now; held_reasons: by rule (the text before the first ":"), most first;
    approved / rejected: someone signed them off / rejected them at least once (approvals);
    changed: a person changed the part: released it anyway (--allow), picked another symbol or footprint (resolve,
    review), or edited its footprint's pads (fp-edit);
    passed_first_time: not held, never rejected, never changed: the part ecadlib built went through as it was."""
    folder = Path(folder)
    chosen = set()
    try:
        state = json.loads((folder / "librarian_state.json").read_text(encoding="utf-8"))
        chosen = {n for n, st in state.items() if isinstance(st, dict)
                  and any((st.get(k) or {}).get("chosen") for k in ("symbol", "footprint"))}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        edited = set(json.loads((folder / "footprint_edits.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        edited = set()
    out = {k: 0 for k in REVIEW_KEYS}
    reasons = {}
    for it in items:
        if not isinstance(it, dict) or it.get("status") not in BUILT:
            continue
        out["built"] += 1
        decisions = [a.get("decision") for a in it.get("approvals") or [] if isinstance(a, dict)]
        rejected, changed = "reject" in decisions, bool(it.get("allow")) or it.get("name") in chosen | edited
        out["held"] += it["status"] == "held"
        out["approved"] += "approve" in decisions
        out["rejected"] += rejected
        out["changed"] += changed
        out["passed_first_time"] += it["status"] != "held" and not rejected and not changed
        if it["status"] == "held":
            for r in it.get("hold_reasons") or []:
                key = str(r).split(":", 1)[0].strip()[:40] or "?"
                reasons.setdefault(key, {"reason": key, "parts": 0, "example": str(r)[:160]})["parts"] += 1
    out["held_reasons"] = sorted(reasons.values(), key=lambda r: (-r["parts"], r["reason"]))
    return out


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(p, problems, root=None):
    """The file's JSON, or None with a problem line naming it relative to the workspace root (never the server path)."""
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        try:
            name = Path(p).resolve().relative_to(Path(root).resolve()).as_posix() if root else Path(p).as_posix()
        except (OSError, ValueError):
            name = Path(p).name
        problems.append(f"{name}: unreadable ({type(e).__name__})")
        return None


def _files(root, pattern):
    """Regular files (no links) matching pattern under root, newest first."""
    out = []
    for p in root.glob(pattern):
        try:
            if p.is_file() and not p.is_symlink():
                out.append((p.stat().st_mtime, p))
        except OSError:
            continue
    return [p for _, p in sorted(out, key=lambda x: x[0], reverse=True)]


def _plans(root):
    """Librarian plans: the UI's <root>/librarian/<bom>/plan.json, and a `librarian -o DIR` output given as the
    workspace itself or one folder below it (DIR/plan.json with the librarian's review.md next to it)."""
    seen, out = set(), []
    for pattern in ("librarian/*/plan.json", "plan.json", "*/plan.json"):
        for pf in _files(root, pattern):
            if pf in seen or (not pattern.startswith("librarian/") and not (pf.parent / "review.md").is_file()):
                continue
            seen.add(pf)
            out.append(pf)
    return out


def status(root):
    root = Path(root).expanduser().resolve()
    problems = []
    out = {"workspace": str(root), "exists": root.is_dir(), "rules": None, "boms": 0, "plans": [],
           "totals": {"parts": 0, **{("duplicates" if k == "duplicate" else k): 0 for k in COUNTS}},
           "last_triage": None, "last_upkeep": None, "last_run": None, "changes_held": [],
           "review": {**{k: 0 for k in REVIEW_KEYS}, "held_reasons": []},
           "problems": problems}
    if not out["exists"]:
        return out
    rel = lambda p: Path(p).relative_to(root).as_posix()
    rf = next((root / n for n in ("ecadlib.toml", "ecadlib_rules.json") if (root / n).is_file()), None)
    if rf is None:                             # the file in force for the workspace's plans: `init` puts it one folder up
        from . import rules as RU
        try:
            rf = RU.find(root)
        except RU.RulesError:
            rf = None
    out["rules"] = (rel(rf) if Path(rf).resolve().is_relative_to(root) else str(rf)) if rf else None
    up = root / "uploads"
    out["boms"] = len(_files(up, "*")) + len(_files(up, "*/*")) if up.is_dir() else 0

    from . import timing as TI
    rv, reasons = out["review"], {}
    for pf in _plans(root):
        items = _json(pf, problems, root)
        if not isinstance(items, list):
            if items is not None:
                problems.append(f"{rel(pf)}: not a plan (expected a list of parts)")
            continue
        st = [i.get("status") for i in items if isinstance(i, dict)]
        row = {"plan": rel(pf), "parts": len(st), **{("duplicates" if k == "duplicate" else k): st.count(k) for k in COUNTS},
               "updated": _iso(pf.stat().st_mtime), "timing": TI.last_run(pf.parent, problems),
               "review": review_stats(items, pf.parent)}
        out["plans"].append(row)
        ch = [i["name"] for i in items if isinstance(i, dict) and (i.get("change") or {}).get("state") == "held"]
        if ch:                                 # change control: a change to a released part waits for a person
            out["changes_held"].append({"plan": rel(pf), "parts": ch})
        for k in out["totals"]:
            out["totals"][k] += row[k]
        for k in REVIEW_KEYS:
            rv[k] += row["review"][k]
        for r in row["review"]["held_reasons"]:
            agg = reasons.setdefault(r["reason"], dict(r, parts=0))
            agg["parts"] += r["parts"]
    rv["held_reasons"] = sorted(reasons.values(), key=lambda r: (-r["parts"], r["reason"]))[:5]
    runs = [dict(p["timing"], plan=p["plan"]) for p in out["plans"] if p["timing"]]
    out["last_run"] = max(runs, key=lambda r: r["finished"]) if runs else None

    tri = _files(root, "reports/triage_*.json") or _files(root, "reports/triage_*.csv")
    if tri:
        f = tri[0]
        t = {"report": rel(f), "finished": _iso(f.stat().st_mtime)}
        if f.suffix == ".json":
            d = _json(f, problems, root) or {}
            if isinstance(d, dict):
                t.update(name=d.get("name"), finished=d.get("finished") or t["finished"], user=d.get("user"),
                         lines=len(d.get("rows") or []), counts=d.get("counts") or {})
        out["last_triage"] = t

    ups = _files(root, "upkeep/*/state/upkeep_last.json")
    best = None
    for f in ups:
        d = _json(f, problems, root)
        if isinstance(d, dict) and (best is None or str(d.get("date") or "") > str(best["date"] or "")):
            best = {"date": d.get("date"), "new_changes": d.get("new_changes"), "state": rel(f)}
    out["last_upkeep"] = best

    from . import ds_route                     # what the Claude datasheet reads cost (<out>/datasheet/cost.jsonl files)
    recs = [r for f in _files(root, "**/datasheet/cost.jsonl") for r in ds_route.read_ledger(f, problems)]
    if recs:
        d = ds_route.summarize(recs)
        out["datasheet"] = {k: d[k] for k in ("answers", "parts", "input_tokens", "output_tokens", "usd", "usd_estimated",
                                              "escalations")}
        out["datasheet"]["by_model"] = d["by_model"]
    return out


def render(s, styled=False, width=None, shown=None, verbosity=0):
    """The one-screen summary as text. styled: colour + glyphs (a terminal); shown: the workspace as the user typed it
    (the absolute path only with -v)."""
    import io
    from . import messages as M, term as TM
    buf = io.StringIO()
    t = TM.Term(buf, styled=styled, width=width, verbosity=verbosity)
    name = shown or s["workspace"]
    if not s["exists"]:
        t.write(t.bold(f"Workspace {name} does not exist yet."))
        t.write("  fix: `ecadlib demo -o <folder>` makes a sample one; `ecadlib ui --workspace ...` or "
                "`ecadlib librarian BOM -o <folder>/librarian/x` starts a real one")
        return buf.getvalue().rstrip("\n")
    tot = s["totals"]
    cnt = {"ready": tot["ready"], "held": tot["held"], "awaiting_approval": tot["awaiting_approval"], "duplicate": tot["duplicates"]}
    t.write(t.bold(f"Workspace {name}") + (t.dim(f"  ({s['workspace']})") if t.verbose and shown else ""))
    head = (f"{M.plural(tot['parts'], 'part')} in {M.plural(len(s['plans']), 'library plan')}: {M.counts(cnt)}"
            if s["plans"] else "no library yet (no BOM has been through the librarian)")
    t.write(f"  {head}")
    if t.quiet:
        return buf.getvalue().rstrip("\n")
    rows = []
    for p in s["plans"][:8]:
        cells = [p["plan"].removesuffix("/plan.json"), M.plural(p["parts"], "part")]
        cells.append("  ".join(t.status(k, f"{p[k]} {M.status_word(k)}") for k in ("ready", "held", "awaiting_approval") if p[k]))
        rows.append(cells)
    if rows:
        t.write("")
        t.write("library plans:")
        t.table(rows, right=(1,))
    if len(s["plans"]) > 8:
        t.write(f"  ... {len(s['plans']) - 8} more (--json lists all)")
    t.write("")
    lt, lu = s["last_triage"], s["last_upkeep"]
    kv = [["rules", s["rules"] or "defaults (no ecadlib.toml in the workspace or above it)"],
          ["BOMs", f"{s['boms']} uploaded"],
          ["last triage", (f"{lt.get('name') or lt['report']}, {lt['finished']}"
                           + (f", {M.plural(lt['lines'], 'line')}" if lt.get("lines") is not None else "")
                           + (f" ({', '.join(f'{k} {v}' for k, v in lt['counts'].items())})" if lt.get("counts") else "")
                           if lt else "never")],
          ["upkeep", f"{lu['date']}, {M.plural(lu['new_changes'] or 0, 'new change')}" if lu else "never"]]
    lr = s.get("last_run")
    if lr:
        from . import timing as TI
        names = {"triage": "triage", "build": "build", "release_check": "release check", "review": "review", "push": "push"}
        kv.append(["last run", f"{TI.human(lr['total_seconds'])} BOM to "
                   + ("plan" if "push" not in lr["steps"] else "released" if not lr.get("push_errors")
                      else f"push, {lr['push_errors']} failed") + f" ({TI.human(lr['work_seconds'])} ecadlib work): "
                   + ", ".join(f"{names.get(k, k)} {TI.human(v)}" for k, v in lr["steps"].items())])
    rv = s.get("review") or {}
    if rv.get("built"):
        kv.append(["review", f"{rv['built']} built: {rv['passed_first_time']} passed first time, {rv['held']} held, "
                   f"{rv['approved']} approved, {rv['rejected']} rejected, {rv['changed']} changed by a person"])
        if rv["held_reasons"]:
            kv.append(["held for", "; ".join(f"{r['reason']} {r['parts']}" for r in rv["held_reasons"][:3])])
    chg = s.get("changes_held") or []
    if chg:
        names = [n for c in chg for n in c["parts"]]
        kv.append(["changes held", f"{M.plural(len(names), 'change')} to released parts " + ("waits" if len(names) == 1 else "wait") + " for a person ("
                   + ", ".join(names[:5]) + (" ..." if len(names) > 5 else "") + "); the released versions are kept"])
    ds = s.get("datasheet")
    if ds:
        kv.append(["datasheet", f"{ds['answers']} Claude answers for {M.plural(ds['parts'], 'part')}, ${ds['usd']:.2f}"
                   + (f" (+ ${ds['usd_estimated']:.2f} estimated)" if ds.get("usd_estimated") else "")
                   + f", {ds['input_tokens']:,} tokens in: "
                   + ", ".join(f"{m} {v['answers']} (${v['usd']:.2f})" for m, v in sorted(ds["by_model"].items()))])
    t.table(kv, indent=0)
    for p in s["problems"]:
        t.note(f"WARN {M.clean(p)}", "the file was skipped; `ecadlib migrate --check` or `ecadlib doctor` says more")
    nxt = []
    ws = f" --workspace {shown}" if shown else ""
    if tot["held"]:
        nxt.append((f"ecadlib review{ws}", f"{M.plural(tot['held'], 'held part')}: why, and pick a fix (or `ecadlib resolve <plan.json> list`)"))
    if tot["awaiting_approval"]:
        nxt.append(("ecadlib approve <plan.json> status", "who still has to sign off"))
    for c in chg[:2]:
        nxt.append((f"ecadlib part status {Path(shown or s['workspace']) / c['plan']}", "approve or reject the held changes"))
    if not s["plans"]:
        nxt.append((f"ecadlib librarian BOM.csv -o {shown or '<workspace>'}/librarian/board", "fill a library from a BOM"))
    if nxt:
        t.write("")
        t.write("next:")
        t.table([[c, f"# {w}"] for c, w in nxt[:3]])
    return buf.getvalue().rstrip("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib status", description="one-screen summary of a workspace (read-only)")
    ap.add_argument("--workspace", default=os.environ.get("ECADLIB_WORKSPACE") or "ecadlib_workspace")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON (stable keys; see docs/command-line.md)")
    a = ap.parse_args(argv)
    s = status(a.workspace)
    if a.json:
        print(json.dumps(s, indent=2))
    else:
        from . import term as TM
        print(render(s, styled=TM.wants_style(sys.stdout), width=TM.term_width(sys.stdout), shown=a.workspace,
                     verbosity=TM.VERBOSITY))
    return 0 if s["exists"] else 1


if __name__ == "__main__":
    sys.exit(main())
