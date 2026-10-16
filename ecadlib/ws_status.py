"""`ecadlib status`: one screen about a workspace - parts in the library plans, held, waiting for sign-off, the last
triage, the last upkeep run and the library agent's queue. Read-only: it never creates the workspace or any file in it,
and a damaged file is skipped (and named) instead of stopping the summary.

    ecadlib status [--workspace W] [--json]        (W defaults to $ECADLIB_WORKSPACE, then ./ecadlib_workspace)

--json prints one object (stable keys, see README "ecadlib status"):
    {"workspace", "exists", "rules", "boms", "plans": [{"plan", "parts", "ready", "held", "awaiting_approval",
     "duplicates", "updated"}], "totals": {...same counts...}, "last_triage": {...} | null,
     "last_upkeep": {...} | null, "agent": {"state", "heartbeat", "last_run", "queue": {state: n}, "waiting_review"},
     "problems": [text]}
plus "datasheet": {"answers", "parts", "input_tokens", "output_tokens", "usd", "usd_estimated", "escalations",
"by_model"} once a Claude datasheet read has been paid for in the workspace."""
import argparse, datetime, json, os, sys
from pathlib import Path

COUNTS = ("ready", "held", "awaiting_approval", "duplicate")


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(p, problems):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append(f"{p}: unreadable ({type(e).__name__})")
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


def status(root):
    root = Path(root).expanduser().resolve()
    problems = []
    out = {"workspace": str(root), "exists": root.is_dir(), "rules": None, "boms": 0, "plans": [],
           "totals": {"parts": 0, **{("duplicates" if k == "duplicate" else k): 0 for k in COUNTS}},
           "last_triage": None, "last_upkeep": None,
           "agent": {"state": "never run", "heartbeat": None, "last_run": None, "queue": {}, "waiting_review": 0},
           "problems": problems}
    if not out["exists"]:
        return out
    rel = lambda p: Path(p).relative_to(root).as_posix()
    rf = next((root / n for n in ("ecadlib.toml", "ecadlib_rules.json") if (root / n).is_file()), None)
    out["rules"] = rel(rf) if rf else None
    up = root / "uploads"
    out["boms"] = len(_files(up, "*")) + len(_files(up, "*/*")) if up.is_dir() else 0

    for pf in _files(root, "librarian/*/plan.json"):
        items = _json(pf, problems)
        if not isinstance(items, list):
            if items is not None:
                problems.append(f"{rel(pf)}: not a plan (expected a list of parts)")
            continue
        st = [i.get("status") for i in items if isinstance(i, dict)]
        row = {"plan": rel(pf), "parts": len(st), **{("duplicates" if k == "duplicate" else k): st.count(k) for k in COUNTS},
               "updated": _iso(pf.stat().st_mtime)}
        out["plans"].append(row)
        for k in out["totals"]:
            out["totals"][k] += row[k]

    tri = _files(root, "reports/triage_*.json") or _files(root, "reports/triage_*.csv")
    if tri:
        f = tri[0]
        t = {"report": rel(f), "finished": _iso(f.stat().st_mtime)}
        if f.suffix == ".json":
            d = _json(f, problems) or {}
            if isinstance(d, dict):
                t.update(name=d.get("name"), finished=d.get("finished") or t["finished"], user=d.get("user"),
                         lines=len(d.get("rows") or []), counts=d.get("counts") or {})
        out["last_triage"] = t

    ups = _files(root, "upkeep/*/state/upkeep_last.json") + _files(root, "agent/upkeep/*/state/upkeep_last.json")
    best = None
    for f in ups:
        d = _json(f, problems)
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
    ag = out["agent"]
    sf = root / "agent" / "status.json"
    if sf.is_file():
        d = _json(sf, problems)
        if isinstance(d, dict):
            ag.update(state=d.get("state") or "unknown", heartbeat=d.get("heartbeat"), last_run=d.get("last_run"))
            if d.get("last_error"):
                ag["last_error"] = d["last_error"]
    for f in _files(root, "agent/queue/items/*.json"):
        d = _json(f, problems)
        if isinstance(d, dict) and d.get("status"):
            ag["queue"][d["status"]] = ag["queue"].get(d["status"], 0) + 1
    ag["waiting_review"] = ag["queue"].get("review", 0)
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
    lt, lu, ag = s["last_triage"], s["last_upkeep"], s["agent"]
    kv = [["rules", s["rules"] or "defaults (no ecadlib.toml in the workspace)"],
          ["BOMs", f"{s['boms']} uploaded"],
          ["last triage", (f"{lt.get('name') or lt['report']}, {lt['finished']}"
                           + (f", {M.plural(lt['lines'], 'line')}" if lt.get("lines") is not None else "")
                           + (f" ({', '.join(f'{k} {v}' for k, v in lt['counts'].items())})" if lt.get("counts") else "")
                           if lt else "never")],
          ["upkeep", f"{lu['date']}, {M.plural(lu['new_changes'] or 0, 'new change')}" if lu else "never"]]
    ds = s.get("datasheet")
    if ds:
        kv.append(["datasheet", f"{ds['answers']} Claude answers for {M.plural(ds['parts'], 'part')}, ${ds['usd']:.2f}"
                   + (f" (+ ${ds['usd_estimated']:.2f} estimated)" if ds.get("usd_estimated") else "")
                   + f", {ds['input_tokens']:,} tokens in: "
                   + ", ".join(f"{m} {v['answers']} (${v['usd']:.2f})" for m, v in sorted(ds["by_model"].items()))])
    q = ", ".join(f"{v} {M.status_word(k)}" for k, v in sorted(ag["queue"].items())) or "empty"
    kv.append(["agent", f"{ag['state']}" + (f", last heartbeat {ag['heartbeat']}" if ag.get("heartbeat") else "")
               + f"; review queue: {q}" + (f"; {ag['waiting_review']} waiting for review" if ag["waiting_review"] else "")])
    if ag.get("last_error"):
        kv.append(["", t.color("last error: ", "red") + M.clean(str(ag["last_error"]))[:160]])
    t.table(kv, indent=0)
    for p in s["problems"]:
        t.note(f"WARN {M.clean(p)}", "the file was skipped; `ecadlib migrate --check` or `ecadlib doctor` says more")
    nxt = []
    ws = f" --workspace {shown}" if shown else ""
    if tot["held"]:
        nxt.append((f"ecadlib review{ws}", f"{M.plural(tot['held'], 'held part')}: why, and pick a fix (or `ecadlib resolve <plan.json> list`)"))
    if tot["awaiting_approval"]:
        nxt.append(("ecadlib approve <plan.json> status", "who still has to sign off"))
    if ag["waiting_review"]:
        nxt.append((f"ecadlib queue list{ws}", "the agent's review queue"))
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
    ap.add_argument("--json", action="store_true", help="print the summary as JSON (stable keys; see README)")
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
