"""Library agent: the orchestrator that keeps the library right on its own.

    ecadlib agent run    [--workspace W] [--dry-run] [--upkeep] [--lib DIR]   one pass of the loop below
    ecadlib agent serve  [--workspace W] [--interval S]                       run it every [agent] interval_minutes
    ecadlib agent status [--workspace W] [--healthy SECONDS]                  what the agent did last, queue counts
    ecadlib agent approve ID [--user U] [-m NOTE]  |  reject ID -m NOTE        sign off a part in the review queue
    ecadlib agent push   [--dry-run]                                          push approved parts to Altium 365 now

One pass (run_once):
  WATCH    agent_watch.scan() finds new / changed / removed parts in the watched projects and BOMs
  QUEUE    each change is upserted into agent_queue (one open item per part key, never duplicated)
  BUILD    every detected item goes through the librarian (own libraries, KiCad, EasyEDA, then the datasheet reader;
           as one Message Batch when [agent] datasheet_batch and the rules' sources allow it). The agent keeps one
           cumulative BOM, <ws>/agent/library_bom.csv, and its librarian plan is the agent's library. Before and after
           pictures (visual_check renderers): before = the part's current version in that library (or in the library
           repo's git history), after = the new build.
  PROVE    visual_check.prove() compares our footprint/symbol with the datasheet drawing; "fail" -> failed with the
           reason, anything else -> review (doubts listed). A part the librarian could not build -> failed with why.
  REVIEW   review items are grouped into batches of [agent] batch_size; each batch is ONE pull request: a branch
           ecadlib/agent-<batch> in the library git repo with the changed library files and the pictures (under
           [agent] pictures_path), body with before/after pictures, proof findings and doubts. The commit is made with
           git plumbing (a temporary index), so the working tree and HEAD of the repo are never touched; pushes are
           never forced, and an open batch PR is updated, never duplicated. No git repo, no remote or no token: the PR
           body and pictures are written to <ws>/agent/batches/<id>/ only, and the report says why.
  PUSH     approved items go to Altium 365 through altium365.push (release check and approvals still gate it), when
           [agent] auto_push is true (before BUILD, in the same pass) or with `ecadlib agent push`. Only the build a
           person approved is sent: an item whose library files changed since, or that has nothing built (a removal,
           an upkeep finding), is not. A change seen after the approval becomes a new item once it is pushed.
           --dry-run sends nothing.
  MAINTAIN on the [agent] upkeep schedule, upkeep.run() over every librarian plan in the workspace; new findings
           (obsolete / NRND, stock-out, price jump, KiCad upstream change, Altium 365 drift) become queue items with
           origin "upkeep". A KiCad source that changed is rebuilt; the others are findings for a person.

State lives in <ws>/agent/: status.json (for the web UI), agent_state.json (last upkeep), run.lock (one run at a time),
plan.lock (held while the librarian rewrites the plan: approve/reject wait for it), library_bom.json/.csv,
pictures/<item>/, batches/<id>/, and watch_pending.json / upkeep_pending.json while what a scan saw is not queued yet.
A run that dies anywhere loses nothing: the next one rescans from before it, re-proves items left in `proving` and
rebuilds items left in `building` (an item that stopped the agent 3 times is failed instead). A dry run writes none of
it and restores the watch state."""
import argparse, contextlib, copy, csv, datetime as _dt, hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time
from pathlib import Path

from . import rules as RU

AGENT = "ecadlib-agent"
BOM_NAME = "library_bom.csv"
BOM_COLS = (("designator", "Designator"), ("qty", "Qty"), ("value", "Value"), ("footprint", "Footprint"),
            ("manufacturer", "Manufacturer"), ("mpn", "Mfr. Part #"), ("lcsc", "LCSC Part Number"),
            ("description", "Description"), ("datasheet", "Datasheet"))
SCHEDULES = {"off": None, "daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
SKIP_UPKEEP = ("recovered", "back in stock", "resolved", "reconverted")     # good news is not work
BODY_MAX = 60000


# ------------------------------------------------------------------ small helpers
def stamp(now=None):
    """UTC ISO-8601 with Z. now: None, a datetime, an epoch number or an ISO string."""
    if isinstance(now, str):
        return now
    if isinstance(now, (int, float)):
        now = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(s):
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def part_key(manufacturer, mpn):
    from .agent_queue import norm_manufacturer          # "ST" and "STMicroelectronics" are one maker
    return f"{norm_manufacturer(manufacturer)}|{(mpn or '').strip().upper()}"


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(data, indent=2, default=str))
        f.flush()
        os.fsync(f.fileno())                      # a power cut must not leave an empty file behind the rename
    os.replace(tmp, path)


def write_json_text(path, text):
    """write_json for text that already is JSON (a saved copy of a state file)."""
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return copy.deepcopy(default)


def settings(rules):
    a = dict(RU.DEFAULTS["agent"])
    a.update((rules or {}).get("agent") or {})
    return a


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("._")[:80] or "item"


def _par(item):
    return {p["name"]: p["value"] for p in item.get("parameters", [])}


class _NoQueue:
    """A dry run on a workspace without a queue yet: an empty queue that makes no folders."""
    def list(self, status=None, batch=None):
        return []


def _modules(watcher=None, queue=None, prover=None, workspace=None, dry_run=False):
    """The three sibling modules, imported only when the caller did not pass its own (tests pass fakes)."""
    if watcher is None:
        from . import agent_watch as watcher
    if queue is None:
        from . import agent_queue as AQ
        queue = _NoQueue() if dry_run and not (Path(workspace) / "agent" / "queue" / "items").is_dir() else AQ.Queue(workspace)
    if prover is None:
        from . import visual_check as prover
    return watcher, queue, prover


def _queue(ws):
    from . import agent_queue as AQ
    return AQ.Queue(ws)


@contextlib.contextmanager
def run_lock(ws, create=True):
    """One run at a time per workspace (serve, a CLI run and the web UI share it). Yields False when held.
    create=False (a dry run): no folder or file is made; without a lock file no run ever happened, so none runs."""
    d = Path(ws) / "agent"
    if not create and not (d / "run.lock").exists():
        yield True
        return
    if create:
        d.mkdir(parents=True, exist_ok=True)
    f = open(d / "run.lock", "a+" if create else "r")
    try:
        try:
            try:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:                     # Windows
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            got = False
        else:
            got = True
            if create:
                f.seek(0); f.truncate(); f.write(f"{os.getpid()} {stamp()}\n"); f.flush()
        yield got
    finally:
        f.close()


def _librarian_out(ws, bom):
    """The same folder Workspace uses for this BOM, so the review queue's sign-off finds the agent's plan."""
    from .app import Workspace
    w = Workspace.__new__(Workspace)              # no __init__: it makes the workspace folders (a dry run must not)
    w.root = Path(ws).resolve()
    return w._librarian_dir(Path(bom))


# ------------------------------------------------------------------ status file
def status_path(ws):
    return Path(ws) / "agent" / "status.json"


def write_status(ws, **fields):
    st = read_json(status_path(ws), {})
    st.update(fields, heartbeat=stamp(), pid=os.getpid())
    write_json(status_path(ws), st)
    return st


def status(workspace, queue=None):
    """status.json plus the queue's counts per state (what `ecadlib agent status` and the web UI show)."""
    ws = Path(workspace).resolve()
    st = read_json(status_path(ws), {"state": "never run"})
    try:
        q = queue if queue is not None else _queue(ws)
        counts = {}
        for it in q.list():
            counts[it["status"]] = counts.get(it["status"], 0) + 1
        st["queue"] = counts
    except Exception as e:                       # queue module not installed / unreadable: status still answers
        st["queue_error"] = f"{type(e).__name__}: {e}"
    st["batches"] = [read_json(b, {}) for b in sorted((ws / "agent" / "batches").glob("*/batch.json"))[-20:]]
    return st


# ------------------------------------------------------------------ git
class GitError(Exception):
    pass


def git(repo, *args, env=None, input=None, ok=(0,)):
    e = dict(os.environ, **(env or {}))
    e.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=e, input=input)
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    if p.returncode not in ok:
        raise GitError(f"git {' '.join(args[:3])}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout.strip()


def repo_root(path):
    try:
        return Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    except (GitError, OSError):
        return None


def github_slug(url):
    m = re.search(r"github[^/:]*[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def commit_files(repo, branch, base, files, message, remove=()):
    """Commit {repo path: local file} on top of `branch` (or `base` when the branch does not exist) WITHOUT touching
    the working tree, the index or HEAD: a temporary index, write-tree, commit-tree, update-ref. `remove`: repo paths
    taken out of the tree. -> (sha, changed)."""
    parent = git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", ok=(0, 1)) or git(repo, "rev-parse", base)
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
        git(repo, "read-tree", parent, env=env)
        for path, src in sorted(files.items()):
            blob = git(repo, "hash-object", "-w", "--", str(src))
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        gone = sorted(set(remove) - set(files))
        if gone:
            git(repo, "update-index", "--force-remove", "--", *gone, env=env)
        tree = git(repo, "write-tree", env=env)
    if tree == git(repo, "rev-parse", f"{parent}^{{tree}}"):
        if not git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", ok=(0, 1)):
            git(repo, "update-ref", f"refs/heads/{branch}", parent, "")
        return parent, False
    ident = {}
    if not git(repo, "config", "user.email", ok=(0, 1)):
        ident = {"GIT_AUTHOR_NAME": AGENT, "GIT_AUTHOR_EMAIL": f"{AGENT}@users.noreply.github.com",
                 "GIT_COMMITTER_NAME": AGENT, "GIT_COMMITTER_EMAIL": f"{AGENT}@users.noreply.github.com"}
    sha = git(repo, "commit-tree", tree, "-p", parent, "-F", "-", env=ident, input=message)
    old = git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", ok=(0, 1))
    git(repo, "update-ref", f"refs/heads/{branch}", sha, old or "")      # refuses if someone moved it meanwhile
    return sha, True


# ------------------------------------------------------------------ the agent's BOM and builder
def _line_of(item, plan_items=None):
    """BOM line for a queue item: the watched line, else (upkeep) built from the library plan entry."""
    ch = item.get("change") or {}
    ln = dict(ch.get("line") or {})
    if not ln and plan_items:
        pi = _find_plan_item(plan_items, item)
        if pi:
            p = _par(pi)
            ln = {"mpn": p.get("Manufacturer Part Number", ""), "manufacturer": p.get("Manufacturer", ""),
                  "value": p.get("Value", ""), "footprint": (pi.get("footprint") or {}).get("ref", ""),
                  "description": pi.get("description", ""), "datasheet": p.get("ComponentLink2URL", ""),
                  "lcsc": p.get("Supplier Part Number 1", "") if p.get("Supplier 1", "").upper() in ("LCSC", "JLCPCB") else ""}
    ln.setdefault("mpn", item.get("mpn", ""))
    ln.setdefault("manufacturer", item.get("manufacturer", ""))
    ln.setdefault("qty", "1")
    if not ln.get("designator"):
        ln["designator"] = "U1"
    return ln


def _find_plan_item(plan_items, item):
    name = (item.get("build") or {}).get("part")
    from .agent_queue import norm_manufacturer
    mpn, mfr = (item.get("mpn") or "").strip().upper(), norm_manufacturer(item.get("manufacturer"))
    hits = []
    for pi in plan_items:
        if name and pi.get("name") == name:
            return pi
        p = _par(pi)
        if mpn and (p.get("Manufacturer Part Number", "").strip().upper() == mpn or pi.get("name", "").upper() == mpn):
            hits.append(pi)
    ln = (item.get("change") or {}).get("line") or {}
    if not hits and not ln.get("mpn") and ln.get("value"):       # a bare passive: matched by value like the librarian
        same = [pi for pi in plan_items if not _par(pi).get("Manufacturer Part Number") and _par(pi).get("Value") == ln["value"]]
        hits = [pi for pi in same if (pi.get("footprint") or {}).get("ref") == ln.get("footprint")] or same
    return next((h for h in hits if norm_manufacturer(_par(h).get("Manufacturer")) == mfr), hits[0] if hits else None)


def write_bom(ws, lines):
    """<ws>/agent/library_bom.csv: one line per part the agent ever built (removed parts stay in the library)."""
    f = Path(ws) / "agent" / BOM_NAME
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([h for _, h in BOM_COLS])
        for key in sorted(lines):
            w.writerow([str(lines[key].get(k) or "") for k, _ in BOM_COLS])
    os.replace(tmp, f)
    return f


def librarian_builder(ws, bom, out, refresh, rules, log=None, libs=(), http=None, offline=False):
    """Default builder: the existing librarian on the agent's BOM. refresh: plan names to redo. The datasheet reader is
    used when the rules' sources include it; with [agent] datasheet_batch it is one Message Batch that is submitted
    without waiting (a later run collects it; parts waiting for it stay `building`)."""
    from . import librarian as L
    cfg, reader = settings(rules), None
    cache = Path(ws) / "http_cache"
    if http is None:
        from .httpcache import CachedHTTP
        http = CachedHTTP(cache, min_interval=0.5, offline=offline, timeout=60)
    if cfg["datasheet_batch"] and "datasheet" in rules["sources"]["order"]:
        from . import datasheet as D
        reader = D.Reader(out, cache, None, rules, http, batch=True, wait=False, log=log or (lambda *a: None))
    libs = [str(l) for l in libs] + [str(l) for l in cfg["libs"]] + [str(Path(ws) / "footprints")]
    return L.run(str(bom), libs, out, None, list(refresh) if refresh else None, False, [], offline, cache, 0.5,
                 "jlcpcb_search,easyeda", http=http, log=log or (lambda *a: None), rules=rules,
                 datasheet_batch=bool(reader), reader=reader)


# ------------------------------------------------------------------ pictures
def _render(prover, kind, src, png, notes):
    try:
        fn = prover.render_footprint if kind == "footprint" else prover.render_symbol
        return str(fn(str(src), str(png)))
    except Exception as e:                        # a picture is a help for the reviewer, never a reason to fail
        notes.append(f"{kind} picture not drawn: {type(e).__name__}: {e}")
        return None


def _model_files(pi, out):
    """{'footprint': kicad_mod or PcbLib, 'symbol': SchLib} that exist on disk for a plan item."""
    from .release_check import _path
    got = {}
    fp = pi.get("footprint") or {}
    for kind, cands in (("footprint", [fp.get("kicad_mod"), fp.get("file")]), ("symbol", [(pi.get("symbol") or {}).get("file")])):
        for c in cands:
            if c and _path(out, c).is_file():
                got[kind] = _path(out, c)
                break
    return got


def _git_before(repo, out, pi, tmpdir):
    """The plan item's files as committed in the library repo's HEAD, when they are no longer on disk."""
    if not repo:
        return {}
    got = {}
    fp = pi.get("footprint") or {}
    for kind, rel in (("footprint", fp.get("kicad_mod") or fp.get("file")), ("symbol", (pi.get("symbol") or {}).get("file"))):
        if not rel:
            continue
        try:
            p = (Path(out) / rel).resolve().relative_to(repo).as_posix()
            data = subprocess.run(["git", "show", f"HEAD:{p}"], cwd=repo, capture_output=True).stdout
        except (ValueError, OSError):
            continue
        if data:
            f = Path(tmpdir) / f"before_{kind}{Path(rel).suffix}"
            f.write_bytes(data)
            got[kind] = f
    return got


def pictures_for(ws, prover, item_id, files, which, notes):
    d = Path(ws) / "agent" / "pictures" / _safe(item_id)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for kind in ("footprint", "symbol"):
        if kind in files:
            p = _render(prover, kind, files[kind], d / f"{which}_{kind}.png", notes)
            if p and Path(p).is_file():
                out.append(Path(p).resolve().relative_to(Path(ws).resolve()).as_posix()
                           if Path(ws).resolve() in Path(p).resolve().parents else str(p))
    return out


def _published(it):
    """What a batch PR shows of an item: its files and when it was built (a rebuild brings a new proof)."""
    b = it.get("build") or {}
    return f"{b.get('hash')}@{b.get('built')}"


def _hash_files(files):
    h = hashlib.sha256()
    for k in sorted(files):
        h.update(k.encode()); h.update(Path(files[k]).read_bytes())
    return h.hexdigest()[:16]


# ------------------------------------------------------------------ upkeep -> findings
def upkeep_findings(ws, rules, now=None, log=None):
    """Default MAINTAIN step: upkeep.run on every librarian plan in the workspace; -> list of finding dicts."""
    from . import upkeep as UK
    ws = Path(ws)
    found = []
    for plan in sorted((ws / "librarian").glob("*/plan.json")):
        base = ws / "agent" / "upkeep" / plan.parent.name
        rep = UK.run(str(plan), None, base / "state", base / "upkeep", rules=rules,
                     ctx=UK.Ctx(cache=ws / "http_cache"), now=stamp(now))
        items = read_json(plan, [])
        for sec in rep["checks"]:
            for c in sec["changes"]:
                found.append(dict(c, check=sec["check"], plan=plan.relative_to(ws).as_posix(), _items=items))
    return found


def finding_item(f, now):
    """A queue item (fields form) for one upkeep finding; the key is the plan part's manufacturer|MPN when known."""
    items, name = f.get("_items") or [], str(f.get("item") or "")
    pi = next((i for i in items if i.get("name") == name or _par(i).get("Manufacturer Part Number", "").upper() == name.upper()
               or (i.get("footprint") or {}).get("ref") == name), None)
    mpn, mfr = (_par(pi).get("Manufacturer Part Number", "") or pi.get("name", ""), _par(pi).get("Manufacturer", "")) if pi else (name, "")
    change = {k: v for k, v in f.items() if not k.startswith("_")}
    change.update(detected=stamp(now), part=pi.get("name") if pi else None)
    return {"key": part_key(mfr, mpn), "mpn": mpn, "manufacturer": mfr, "origin": "upkeep", "change": change}


def upkeep_due(ws, cfg, now=None):
    period = SCHEDULES.get(cfg["upkeep_schedule"])
    if not period:
        return False
    last, t = _epoch(read_json(Path(ws) / "agent" / "agent_state.json", {}).get("last_upkeep")), _epoch(stamp(now))
    # last in the future: the clock was ahead when it was stamped (then corrected); waiting for it would stop upkeep
    return last is None or t is None or t - last >= period or last > t


# ------------------------------------------------------------------ PR body
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()!|~])")


def _md(v):
    """Untrusted text (MPNs, BOM fields, notes, model answers) as one inert line of GitHub markdown: no HTML, links,
    images, tables breaks, @mentions or #issue references (so no "fixes #12" closing an issue on merge)."""
    s = " ".join(str(v if v is not None else "").split())
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = _MD_SPECIAL.sub(r"\\\1", s)
    return s.replace("@", "@​").replace("#", "#​")


_esc = _md


def _url(v):
    """A datasheet link for <...>: http(s) only, with nothing that could close the autolink."""
    from urllib.parse import quote
    v = str(v or "").strip()
    return quote(v, safe=":/?#[]@!$&'()*+,;=%~.-_") if re.match(r"https?://[^\s]+$", v) else None


def pr_body(batch_id, items, link, t, removed=()):
    md = [f"# Library agent batch {_md(batch_id)}", "",
          f"{len(items)} part(s) built by `ecadlib agent` ({_md(t)}). Check the pictures against the datasheet, then approve "
          "or reject each part in the web UI's **Review queue** (or `ecadlib agent approve ID`). Approved parts are pushed "
          "to Altium 365 by the agent; merging this PR only keeps the library files in git.", ""]
    for it in items:
        ch, b, pr = it.get("change") or {}, it.get("build") or {}, it.get("proof") or {}
        src = ch.get("source") or {}
        what = ch.get("kind") or ch.get("check") or ""
        md += [f"## {_md(it.get('mpn') or it['key'])}" + (f" ({_md(it['manufacturer'])})" if it.get("manufacturer") else ""), "",
               f"Queue item `{_safe(it['id'])}`, {_md(it.get('origin', 'watch'))}: **{_md(what)}**"
               + (f" in {_md(src.get('type'))} {_md(src.get('name') or src.get('ref'))}" if src else "")
               + (f": {_md(ch.get('detail'))}" if it.get("origin") == "upkeep" and ch.get("detail") else ""), ""]
        if b.get("part"):
            md.append(f"Plan part {_md(b['part'])}: release check **{_md(b.get('status', '?'))}**; symbol from "
                      f"{_md((b.get('source') or {}).get('symbol') or '-')}, footprint from {_md((b.get('source') or {}).get('footprint') or '-')}.")
            md.append("")
        elif b.get("action") == "none":
            md += [f"Nothing rebuilt: {_md(b.get('reason', ''))}", ""]
        ds = _url((ch.get("line") or {}).get("datasheet"))
        if ds:
            md += [f"Datasheet: <{ds}>", ""]
        pics = it.get("pictures") or {}
        before, after = pics.get("before") or [], pics.get("after") or []
        if before or after:
            md += ["| Before | After |", "|---|---|"]
            for i in range(max(len(before), len(after))):
                cell = lambda lst: f"![{_safe(Path(lst[i]).stem)}]({link(it, lst[i])})" if i < len(lst) else ("(new part)" if not lst else "")
                md.append(f"| {cell(before)} | {cell(after)} |")
            md.append("")
        if pr:
            md.append(f"**Proof:** {_md(pr.get('verdict', '?'))}" + (f" (score {pr['score']:.2f})" if isinstance(pr.get("score"), (int, float)) else "")
                      + (f": {_md(pr.get('reason'))}" if pr.get("reason") else "")
                      + (f" ({_md(pr['model'])})" if pr.get("model") and pr.get("verdict") != "skipped" else ""))
            if pr.get("findings"):
                md += ["", "| Severity | What | Expected | Found | Page |", "|---|---|---|---|---|"]
                md += [f"| {_md(f.get('severity'))} | {_md(f.get('what'))} | {_md(f.get('expected'))} | {_md(f.get('found'))} | "
                       f"{_md(f.get('page'))} |" for f in pr["findings"]]
            md.append("")
        doubts = list(b.get("hold_reasons") or []) + list(b.get("verify") or []) + list(b.get("notes") or [])
        doubts += [f"proof: {f.get('what')}" for f in pr.get("findings") or [] if f.get("severity") in ("error", "warning")]
        if pr.get("verdict") in ("doubt", "skipped"):
            doubts.insert(0, f"proof {pr['verdict']}: {pr.get('reason', '')}")
        if doubts:
            md += ["**Doubts:**", ""] + [f"- {_md(d)}" for d in dict.fromkeys(map(str, doubts))] + [""]
    if removed:
        md += ["## Taken out of this PR", "", "These parts were rejected, failed or moved to another batch; their files "
               "and pictures were removed from this branch:", ""]
        md += [f"- {_md(r.get('mpn') or r.get('id'))}: {_md(r.get('status'))}" for r in removed] + [""]
    md.append("<sub>Opened by ecadlib agent. It never force-pushes; re-runs update this PR.</sub>")
    text = "\n".join(md) + "\n"
    return text if len(text) <= BODY_MAX else text[:BODY_MAX - 80] + "\n\n(cut: the full text is in the batch folder)\n"


# ------------------------------------------------------------------ batch PR
def _departed(it, batch_id):
    """An item whose files must leave a batch branch: gone, rejected, failed, or now in another batch."""
    return it is None or it.get("batch") != batch_id or it.get("status") in ("rejected", "failed")


def publish_batch(ws, rules, batch_id, items, out, now=None, env=None, session=None, log=None, lookup=None):
    """Write <ws>/agent/batches/<id>/ (PR.md, pictures, batch.json) and, with a git repo + remote + token, commit the
    library files and pictures to ecadlib/agent-<id> and open or update its PR. Files of items that left the batch
    (lookup(id) -> the item now, or None: rejected, failed, moved) are removed from the branch and the PR body says so.
    -> the batch record."""
    ws, cfg, env = Path(ws), settings(rules), os.environ if env is None else env
    log = log or (lambda *a: None)
    t = stamp(now)
    bdir = ws / "agent" / "batches" / _safe(batch_id)
    rec = read_json(bdir / "batch.json", {"id": batch_id, "branch": f"ecadlib/agent-{_safe(batch_id)}", "pr_url": None,
                                          "pr_number": None, "published": {}})
    rec.update(items=[it["id"] for it in items], updated=t)
    by_id = {it["id"]: it for it in items}
    lookup = lookup or by_id.get
    rec.setdefault("paths", {})
    departed = [i for i in rec["paths"] if i not in by_id and _departed(lookup(i), batch_id)]
    removed = list(rec.get("removed") or []) + [
        {"id": i, "mpn": (lookup(i) or {}).get("mpn"), "status": (lookup(i) or {}).get("status") or "gone"}
        for i in departed]
    bdir.mkdir(parents=True, exist_ok=True)
    pic_dir = bdir / "pictures"
    for it in items:                            # pictures next to the PR text, for the folder fallback and the web UI
        for p in (it.get("pictures") or {}).get("before", []) + (it.get("pictures") or {}).get("after", []):
            src = ws / p
            if src.is_file():
                (pic_dir / _safe(it["id"])).mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, pic_dir / _safe(it["id"]) / src.name)
    local_link = lambda it, p: f"pictures/{_safe(it['id'])}/{Path(p).name}"
    (bdir / "PR.md").write_text(pr_body(batch_id, items, local_link, t, removed))

    repo_cfg = cfg["library_repo"]
    repo = repo_root(Path(repo_cfg) if repo_cfg else ws)
    reason = None
    if repo is None:
        reason = "no git repository (set [agent] library_repo or make the workspace a git repo)"
    files = {}
    if repo is not None:
        out = Path(out).resolve()
        inside = repo in out.parents
        libdir = cfg["library_path"].strip("/") or "library"
        rel = lambda p: (Path(p).resolve().relative_to(repo).as_posix() if inside
                         else f"{libdir}/{Path(p).resolve().relative_to(out).as_posix()}")
        from .release_check import _path
        for it in items:
            b = it.get("build") or {}
            for f in b.get("files", {}).values():
                if f and _path(out, f).is_file():
                    files[rel(_path(out, f))] = _path(out, f)
        own = {it["id"]: set() for it in items}
        for it in items:
            for f in ((it.get("build") or {}).get("files") or {}).values():
                if f and _path(out, f).is_file():
                    own[it["id"]].add(rel(_path(out, f)))
        if (out / "plan.json").is_file():
            files[rel(out / "plan.json")] = out / "plan.json"
        for it in items:
            for p in (it.get("pictures") or {}).get("before", []) + (it.get("pictures") or {}).get("after", []):
                if (ws / p).is_file():
                    key = f"{cfg['pictures_path'].strip('/')}/{_safe(batch_id)}/{_safe(it['id'])}/{Path(p).name}"
                    files[key] = ws / p
                    own[it["id"]].add(key)
        keep = set(files) | {x for i, ps in rec["paths"].items() if i not in departed for x in ps}
        drop = {x for i in departed for x in rec["paths"][i]} - keep
        try:
            base = cfg["base_branch"] or git(repo, "symbolic-ref", "--short", "HEAD")
            remote = git(repo, "remote", "get-url", "origin", ok=(0, 2, 128))
            slug = cfg["github_repo"] or github_slug(remote)
            token = env.get(cfg["github_token_env"]) if cfg["github_token_env"] else None
            start = base
            if remote:
                # start from the remote's base, never the local one: local commits nobody pushed (work in progress,
                # private files) must not reach origin inside the agent's branch. FETCH_HEAD only: no ref, index or
                # working tree of the repo changes.
                git(repo, "fetch", "--no-tags", "origin", f"refs/heads/{base}")
                start = git(repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
                # build on what is on the remote branch (a person may have pushed a fix)
                rs = git(repo, "ls-remote", "origin", f"refs/heads/{rec['branch']}")
                if rs:
                    git(repo, "fetch", "--no-tags", "origin", f"+refs/heads/{rec['branch']}:refs/heads/{rec['branch']}")
            msg = f"ecadlib agent batch {batch_id}: {len(items)} part(s)\n\n" + "\n".join(
                f"- {it.get('mpn') or it['key']} ({(it.get('change') or {}).get('kind') or (it.get('change') or {}).get('check') or ''})" for it in items)
            if departed:
                msg += "\n\nTaken out: " + ", ".join(str(r["mpn"] or r["id"]) for r in removed[-len(departed):])
            sha, changed = commit_files(repo, rec["branch"], start, files, msg, remove=drop)
            rec.update(repo=str(repo), base=base, commit=sha)
            for i in departed:                   # committed: their files are out of the branch now
                rec["paths"].pop(i, None)
            rec["removed"] = removed
            rec["paths"].update({i: sorted(ps) for i, ps in own.items()})
            if not remote:
                reason = f"branch {rec['branch']} committed in {repo}, but the repo has no origin remote: no PR"
            elif not token:
                reason = (f"branch {rec['branch']} committed locally; no GitHub token in ${cfg['github_token_env']}: "
                          "not pushed, no PR")
            elif not slug:
                reason = "origin is not a GitHub remote and [agent] github_repo is empty: not pushed, no PR"
            else:
                git(repo, "push", "origin", f"refs/heads/{rec['branch']}:refs/heads/{rec['branch']}")   # never --force
                from .upkeep import GitHub
                gh = GitHub(slug, token, session)
                gh.API = cfg["github_api"].rstrip("/")
                web = "https://github.com" if gh.API == "https://api.github.com" else re.sub(r"/api/v3$", "", gh.API)
                link = lambda it, p: (f"{web}/{slug}/blob/{sha}/{cfg['pictures_path'].strip('/')}/{_safe(batch_id)}/"
                                      f"{_safe(it['id'])}/{Path(p).name}?raw=true")
                body, title = pr_body(batch_id, items, link, t, removed), f"Library agent: {len(items)} part(s) to review (batch {batch_id})"
                num = rec.get("pr_number")
                if num:                          # merged or closed meanwhile: a closed PR is not a review; open a new one
                    cur = gh.api("GET", f"pulls/{num}")
                    if isinstance(cur, dict) and cur.get("state") == "closed":
                        rec.setdefault("closed_prs", []).append(rec.get("pr_url"))
                        num, rec["pr_number"], rec["pr_url"] = None, None, None
                if not num:
                    owner = slug.split("/")[0]
                    open_ = gh.api("GET", f"pulls?state=open&head={owner}:{rec['branch']}&per_page=10") or []
                    num = open_[0]["number"] if open_ else None
                    rec["pr_url"] = open_[0].get("html_url") if open_ else rec.get("pr_url")
                if num:
                    r = gh.api("PATCH", f"pulls/{num}", {"title": title, "body": body})
                    rec.update(pr_action="updated")
                else:
                    r = gh.api("POST", "pulls", {"title": title, "head": rec["branch"], "base": base, "body": body})
                    rec.update(pr_action="opened")
                rec.update(pr_number=(r or {}).get("number") or num, pr_url=(r or {}).get("html_url") or rec.get("pr_url"))
                log(f"agent: PR {rec['pr_action']} {rec['pr_url']}")
        except Exception as e:                   # git or GitHub failed: the folder is still there
            reason = f"{type(e).__name__}: {e}"
    rec["fallback"] = reason
    rec["folder"] = bdir.relative_to(ws).as_posix()
    rec["published"] = {it["id"]: _published(it) for it in items}
    write_json(bdir / "batch.json", rec)
    if reason:
        log(f"agent: batch {batch_id} written to {bdir} ({reason})")
    return rec


def _github(ws, cfg, env, session):
    """(GitHub client, None) for the library repo's PRs, or (None, why not)."""
    repo = repo_root(Path(cfg["library_repo"]) if cfg["library_repo"] else ws)
    if repo is None:
        return None, "no git repository"
    remote = git(repo, "remote", "get-url", "origin", ok=(0, 2, 128))
    slug = cfg["github_repo"] or github_slug(remote)
    token = env.get(cfg["github_token_env"]) if cfg["github_token_env"] else None
    if not (remote and slug and token):
        return None, "no GitHub remote or token"
    from .upkeep import GitHub
    gh = GitHub(slug, token, session)
    gh.API = cfg["github_api"].rstrip("/")
    return gh, None


def close_batch(ws, rules, batch_id, members, now=None, env=None, session=None, log=None):
    """Every item of the batch is decided (pushed, rejected or failed) and none waits for its push: comment the
    outcome on its PR and close it, so PRs never pile up. The branch stays (pushed parts' files are on it). -> rec."""
    ws, cfg, env = Path(ws), settings(rules), os.environ if env is None else env
    bdir = ws / "agent" / "batches" / _safe(batch_id)
    rec = read_json(bdir / "batch.json", {"id": batch_id})
    if rec.get("closed"):
        return rec
    lines = [f"- {_md(m.get('mpn') or m['key'])}: {_md(m['status'])}" for m in members]
    text = ("Every part of this batch is decided, so the library agent closes this PR:\n\n" + "\n".join(lines or ["- (no parts left)"])
            + "\n\nPushed parts are in Altium 365; their library files stay on this branch (reopen the PR to merge them).")
    if rec.get("pr_number") and rec.get("pr_state") != "closed":
        try:
            gh, why = _github(ws, cfg, env, session)
            if gh is None:
                raise RuntimeError(f"PR left open: {why}")
            gh.api("POST", f"issues/{rec['pr_number']}/comments", {"body": text})
            gh.api("PATCH", f"pulls/{rec['pr_number']}", {"state": "closed"})
            rec["pr_state"] = "closed"
        except Exception as e:                      # not closed: tried again next pass
            rec["close_note"] = f"{type(e).__name__}: {e}"
            if bdir.is_dir():
                write_json(bdir / "batch.json", rec)
            return rec
    rec.update(closed=stamp(now), outcome={m["id"]: m["status"] for m in members})
    rec.pop("close_note", None)
    bdir.mkdir(parents=True, exist_ok=True)
    write_json(bdir / "batch.json", rec)
    (log or (lambda *a: None))(f"agent: batch {batch_id} closed")
    return rec


# ------------------------------------------------------------------ push
def push_approved(workspace, rules=None, dry_run=False, queue=None, a365=None, env=None, now=None):
    """Approved queue items -> Altium 365 through altium365.push (release check and approvals still gate each part).
    -> {"results": [...], "pushed": [ids], "skipped": reason or None}."""
    from . import altium365 as A
    from .approvals import load_policy
    ws, env = Path(workspace).resolve(), os.environ if env is None else env
    rules = RU.resolve(rules, ws)
    q = queue if queue is not None else _queue(ws)
    approved = q.list(status="approved")
    out = _librarian_out(ws, ws / "agent" / BOM_NAME)
    plan = read_json(out / "plan.json", [])
    rep = {"results": [], "pushed": [], "skipped": None, "dry_run": dry_run, "approved": [i["id"] for i in approved]}
    if not approved:
        return rep
    url = (rules.get("altium365") or {}).get("workspace") or ""
    if not dry_run and not url:
        rep["skipped"] = "no Altium 365 workspace: set [altium365] workspace in ecadlib.toml"
        return rep
    client = a365
    if client is None and not dry_run:
        tok = env.get("NEXAR_TOKEN")
        if not tok:
            rep["skipped"] = "NEXAR_TOKEN is not set: approved parts wait for the push"
            return rep
        client = A.Nexar(tok)
    pairs = []
    for it in approved:
        if (it.get("build") or {}).get("action") == "none":
            # a removal or an upkeep finding: what was approved is "nothing to build", not the library files (which
            # may never have been approved): nothing is sent; the item is closed
            rep["results"].append({"id": it["id"], "action": "none", "detail": "nothing was built: nothing sent"})
            if not dry_run:
                try:
                    q.transition(it["id"], "pushed", user=AGENT, note="nothing was built: nothing sent to Altium 365")
                    _followup(q, it)
                except (ValueError, KeyError) as e:
                    rep["results"][-1]["queue"] = f"{type(e).__name__}: {e}"
            continue
        pi = _find_plan_item(plan, it)
        if pi is None:
            rep["results"].append({"id": it["id"], "action": "skip", "detail": "not in the agent's library plan"})
            continue
        why = _not_as_approved(it, pi, out)
        if why:
            rep["results"].append({"id": it["id"], "action": "skip", "detail": why})
            continue
        pi = copy.deepcopy(pi)
        for k in ("symbol", "footprint"):
            if pi[k].get("file"):
                pi[k]["file"] = str((out / pi[k]["file"]).resolve())
        pairs.append((it, pi))
    if not dry_run:                              # a person may have rejected one while the plan was read
        live = []
        for it, pi in pairs:
            try:
                cur = q.get(it["id"])
            except (KeyError, ValueError):
                continue
            if cur.get("status") == "approved" and _published(cur) == _published(it):
                live.append((cur, pi))
            else:
                rep["results"].append({"id": it["id"], "action": "skip", "detail": f"now {cur.get('status')}: not pushed"})
        pairs = live
    results = A.push([p for _, p in pairs], client, url or "https://<workspace>", dry_run=dry_run,
                     policy=load_policy(out), rules=rules) if pairs else []
    for (it, _), res in zip(pairs, results):
        res = dict(res, id=it["id"])
        rep["results"].append(res)
        if dry_run:
            continue
        try:
            q.update(it["id"], push=dict(res, at=stamp(now)))
            if res["action"] in ("release", "update_parameters", "up_to_date"):
                q.transition(it["id"], "pushed", user=AGENT, note=f"Altium 365: {res['action']}")
                rep["pushed"].append(it["id"])
                _followup(q, it)
        except (ValueError, KeyError) as e:      # changed by a person meanwhile: the push result is still in the report
            res["queue"] = f"{type(e).__name__}: {e}"
    return rep


def _followup(q, it):
    """Its source changed after the approval: that change is new work (a new item, now that this one is closed)."""
    nxt = it.get("followup")
    if nxt:
        q.upsert({"key": it["key"], "mpn": it.get("mpn") or nxt.get("mpn", ""), "manufacturer": it.get("manufacturer", ""),
                  "origin": it.get("origin") or "watch", "change": nxt})


def _not_as_approved(it, pi, out):
    """Why an approved item must not be pushed: its library files are no longer the build that was approved (rebuilt
    since, or the approval was given on another build). None when they are."""
    b = it.get("build") or {}
    ap = it.get("approval") or {}
    if ap.get("build") and ap["build"] != _published(it):
        return "approved on another build than the current one: review it again"
    if b.get("hash"):
        files = _model_files(pi, out)
        try:
            now_hash = _hash_files(files) if files else None
        except OSError:
            now_hash = None
        if now_hash != b["hash"]:
            return "its library files changed since the approved build: reject and retry it to review the new build"
    return None


# ------------------------------------------------------------------ one pass
def run_once(workspace, rules=None, dry_run=False, client=None, now=None, *, watcher=None, queue=None, prover=None,
             builder=None, upkeep=None, do_upkeep=None, github_session=None, a365=None, env=None, libs=(), log=None):
    """One pass: scan -> queue -> build -> pictures -> prove -> review/failed -> batch PR -> push (auto_push) ->
    upkeep (when due). client goes to the watcher and the prover (their API clients). Returns a report dict.
    Injectable for tests: watcher/queue/prover (the sibling modules), builder(ws, bom, out, refresh, rules, log),
    upkeep(ws, rules, now) -> findings, github_session (requests-like), a365 (Nexar-like client), env."""
    ws = Path(workspace).resolve()
    rules = RU.resolve(rules, ws)
    cfg, t, log = settings(rules), stamp(now), log or (lambda *a: None)
    env = os.environ if env is None else env
    rep = {"started": t, "dry_run": dry_run, "detected": [], "built": [], "review": [], "failed": [], "waiting": [],
           "batches": [], "push": None, "upkeep": None, "notes": []}
    with run_lock(ws, create=not dry_run) as got:
        if not got:
            rep["skipped"] = "another agent run is in progress"
            return rep
        watcher, q, prover = _modules(watcher, queue, prover, ws, dry_run)
        if not dry_run and hasattr(q, "migrate_keys"):
            q.migrate_keys()                          # once: items keyed before manufacturer names were normalised
        if not dry_run:
            write_status(ws, state="running", run_started=t, dry_run=False)
        try:
            _pass(ws, rules, cfg, t, dry_run, client, now, watcher, q, prover, builder, upkeep, do_upkeep,
                  github_session, a365, env, libs, log, rep)
        except Exception as e:
            rep["error"] = f"{type(e).__name__}: {e}"
            if not dry_run:
                write_status(ws, state="error", last_error=rep["error"], last_run=_summary(rep))
            raise
        rep["finished"] = stamp() if now is None else t
        if not dry_run:
            write_status(ws, state="idle", last_run=_summary(rep), enabled=cfg["enabled"])
    return rep


def _summary(rep):
    return {k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()
            if k in ("started", "finished", "detected", "built", "review", "failed", "waiting", "error", "skipped")} | {
        "batches": [{k: b.get(k) for k in ("id", "pr_url", "fallback", "folder")} for b in rep["batches"]],
        "pushed": len((rep.get("push") or {}).get("pushed", [])), "upkeep": (rep.get("upkeep") or {}).get("findings")}


def _pass(ws, rules, cfg, t, dry_run, client, now, watcher, q, prover, builder, upkeep, do_upkeep, session, a365, env,
          libs, log, rep):
    # WATCH (a dry run puts the watch state back, so the next real run still sees these changes). A real run keeps
    # the state from before the scan in watch_pending.json until every change is in the queue: a run that dies in
    # between (killed, power cut) gets the old state back next time and sees the same changes again, never loses them.
    wfile, pend = ws / "agent" / "watch_state.json", ws / "agent" / "watch_pending.json"
    saved = wfile.read_bytes() if dry_run and wfile.exists() else None
    if not dry_run:
        old = read_json(pend, None)
        if isinstance(old, dict) and "state" in old:
            if old["state"] is None:
                wfile.unlink(missing_ok=True)
            else:
                write_json_text(wfile, old["state"])
            rep["notes"].append("the last run stopped before queueing what it saw: scanning again from before it")
        else:
            write_json(pend, {"state": wfile.read_text() if wfile.exists() else None, "at": t})
    try:
        import inspect
        kw = {"dry_run": True} if dry_run and "dry_run" in inspect.signature(watcher.scan).parameters else {}
        changes = watcher.scan(ws, rules=rules, client=client, now=None if now is None else t, **kw) or []   # t: ISO
    finally:
        if dry_run:
            if saved is not None:
                wfile.write_bytes(saved)
            elif wfile.exists():
                wfile.unlink()
    rep["detected"] = [{k: c.get(k) for k in ("key", "mpn", "kind")} for c in changes]

    # MAINTAIN: upkeep findings become queue items (on its schedule, or when asked)
    due = upkeep_due(ws, cfg, now) if do_upkeep is None else do_upkeep
    upend = ws / "agent" / "upkeep_pending.json"
    findings = [] if dry_run else read_json(upend, [])      # findings a dead run computed but did not queue
    findings = findings if isinstance(findings, list) else []
    if due and not dry_run:
        try:
            raw = (upkeep or upkeep_findings)(ws, rules, now)
            new = [finding_item(f, now) for f in raw if str(f.get("kind", "")).lower() not in SKIP_UPKEEP]
            findings += new
            write_json(upend, findings)            # upkeep remembers them as seen: keep them until they are queued
            rep["upkeep"] = {"ran": t, "findings": len(new)}
            st = read_json(ws / "agent" / "agent_state.json", {})
            write_json(ws / "agent" / "agent_state.json", dict(st, last_upkeep=t))
        except Exception as e:
            rep["upkeep"] = {"ran": t, "error": f"{type(e).__name__}: {e}"}
    elif due:
        rep["upkeep"] = {"due": True, "note": "dry run: upkeep not run"}

    if dry_run:
        rep["notes"].append("dry run: nothing queued, built, committed or pushed")
        rep["would_build"] = [i["id"] for i in q.list(status="detected")] + [c.get("key") for c in changes]
        rep["would_batch"] = [i["id"] for i in q.list(status="review") if not i.get("batch")]
        if cfg["auto_push"]:
            rep["push"] = push_approved(ws, rules, True, q, a365, env, now)
        return

    with getattr(q, "lock", None) or contextlib.nullcontext():     # one lock hold: the folder is read once, not per part
        for c in changes:
            try:
                it = q.upsert({"key": c.get("key") or part_key(c.get("manufacturer"), c.get("mpn")), "mpn": c.get("mpn", ""),
                               "manufacturer": c.get("manufacturer", ""), "origin": "watch", "change": c})
                if it.get("status") == "review" and c.get("kind") in ("new", "changed"):
                    # the source changed while the part waits for review: rebuild it (its batch PR is then updated)
                    q.update(it["id"], build=dict(it.get("build") or {}, requested_at=t, request_note=f"source {c.get('kind')}"))
                    q.transition(it["id"], "building", user=AGENT, note=f"rebuild: {c.get('kind')} in a watched source")
                elif it.get("status") == "approved" and c.get("kind") == "changed":
                    # the approved build is still what a person signed: it is pushed as it is, and this change becomes
                    # a new item once it is (else it would be lost: the watch never reports it again)
                    q.update(it["id"], followup=c)
                    rep["notes"].append(f"{it.get('mpn')}: changed in a watched source after approval; rebuilt after "
                                        "its push (or reject and retry it to rebuild now)")
            except Exception as e:                 # one bad change must not lose the others
                rep["notes"].append(f"{c.get('key') or c.get('mpn')}: not queued: {type(e).__name__}: {e}")
        for f in findings:
            try:
                q.upsert(f)
            except Exception as e:
                rep["notes"].append(f"upkeep finding {f.get('key')}: not queued: {type(e).__name__}: {e}")
    pend.unlink(missing_ok=True)
    upend.unlink(missing_ok=True)

    # PUSH (before the build: what was approved is sent before the librarian runs again, and a change that came in
    # after an approval becomes a new item that this same run builds)
    if cfg["auto_push"]:
        rep["push"] = push_approved(ws, rules, False, q, a365, env, now)

    # BUILD
    _build(ws, rules, cfg, t, client, q, prover, builder, libs, log, rep)

    # REVIEW: batches and their PRs
    out = _librarian_out(ws, ws / "agent" / BOM_NAME)
    review = q.list(status="review")
    fresh = [i for i in review if not i.get("batch")]
    size = max(1, int(cfg["batch_size"]))
    touched = {}
    for n in range(0, len(fresh), size):
        b = q.batch([i["id"] for i in fresh[n:n + size]])
        touched[b["id"]] = True
    for bid in {i["batch"] for i in review if i.get("batch")}:      # a rebuilt item: update its batch's PR
        rec = read_json(ws / "agent" / "batches" / _safe(bid) / "batch.json", {})
        if any(rec.get("published", {}).get(i["id"]) != _published(i)
               for i in review if i.get("batch") == bid):
            touched[bid] = True
    lookup = lambda i: _get_or_none(q, i)
    open_batches = [b["id"] for b in q.batches() if b.get("status", "open") == "open"] if callable(getattr(q, "batches", None)) else \
        sorted({i["batch"] for i in q.list() if i.get("batch")})
    for bid in open_batches:
        members = q.list(batch=bid)
        rec = read_json(ws / "agent" / "batches" / _safe(bid) / "batch.json", {})
        if members and all(m["status"] in ("pushed", "rejected", "failed") for m in members) or (not members and rec):
            try:                                        # all decided, nothing waits for a push: close its PR
                rec = close_batch(ws, rules, bid, members, now, env, session, log)
                if rec.get("closed") and hasattr(q, "update_batch"):
                    q.update_batch(bid, status="closed")
            except (KeyError, ValueError, OSError) as e:
                rep["notes"].append(f"batch {bid}: not closed: {type(e).__name__}: {e}")
            touched.pop(bid, None)
        elif any(_departed(lookup(i), bid) for i in rec.get("paths", {})):
            touched[bid] = True                         # a rejected / failed / moved part's files leave its branch
    for bid in touched:
        items = [i for i in q.list(batch=bid) if i["status"] in ("review", "approved")]
        if not items:
            continue
        rec = publish_batch(ws, rules, bid, items, out, now, env, session, log, lookup=lookup)
        try:
            if rec.get("pr_url") and hasattr(q, "update_batch"):   # the queue's batch record (web UI batch view) gets the PR too
                q.update_batch(bid, pr_url=rec["pr_url"], branch=rec.get("branch"))
            for i in items:
                if rec.get("pr_url") and i.get("pr_url") != rec["pr_url"]:
                    q.update(i["id"], pr_url=rec["pr_url"])
        except (KeyError, ValueError) as e:
            rep["notes"].append(f"batch {bid}: PR link not stored: {type(e).__name__}: {e}")
        rep["batches"].append(rec)

    if not cfg["auto_push"]:
        n = len(q.list(status="approved"))
        if n:
            rep["notes"].append(f"{n} approved part(s) wait for `ecadlib agent push` ([agent] auto_push is off)")


MAX_ATTEMPTS = 3          # runs that died while building / proving one item before that item is failed


def _get_or_none(q, i):
    try:
        return q.get(i)
    except (KeyError, ValueError):
        return None


def _wants_build(it):
    """Items to build now: detected and building ones. Runs never overlap (run_lock), so an item found `building` at
    the start of a run is one a person asked to rebuild, one waiting for a datasheet batch, or one a run died on: all
    are (re)built. No time comparison: a clock stepped back must not strand a rebuild request."""
    return it["status"] in ("detected", "building")


def _rebuildable(it):
    ch = it.get("change") or {}
    if it.get("origin") == "upkeep":
        return ch.get("check") == "upstream" and str(ch.get("kind", "")).lower() == "kicad changed"
    return ch.get("kind") != "removed"


def _skipped_proof(reason):
    return {"verdict": "skipped", "score": None, "reason": reason, "findings": [], "images": {"ours": [], "datasheet": []},
            "model": "", "cost_usd": 0.0}


def _prove(q, item_id, ws, rules, client, prover, rep, note=None):
    """proving -> review / failed for one item (the PROVE step; also resumes an item a dead run left in proving)."""
    it = q.get(item_id)
    b = dict(it.get("build") or {})
    b["proof_attempts"] = int(b.get("proof_attempts") or 0) + 1
    if b["proof_attempts"] > MAX_ATTEMPTS:
        q.update(item_id, build=b)
        q.transition(item_id, "failed", user=AGENT, note=f"the proof stopped the agent {MAX_ATTEMPTS} times: look at it")
        rep["failed"].append({"id": item_id, "reason": "proof interrupted repeatedly"})
        return
    it = q.update(item_id, build=b)
    if b.get("action") == "none":
        proof = _skipped_proof("nothing was built")
    else:
        try:
            proof = prover.prove(it, workspace=ws, rules=rules, client=client)
        except Exception as e:
            proof = _skipped_proof(f"proof crashed: {type(e).__name__}: {e}")
    q.update(item_id, proof=proof)
    if proof.get("verdict") == "fail":
        q.transition(item_id, "failed", user=AGENT, note=f"proof failed: {proof.get('reason', '')}")
        rep["failed"].append({"id": item_id, "reason": f"proof failed: {proof.get('reason', '')}"})
    else:
        q.transition(item_id, "review", user=AGENT, note=note or f"proof {proof.get('verdict')}: {proof.get('reason', '')}"[:300])
        rep["review"].append(item_id)


def _changed_meanwhile(rep, it, e):
    """A person decided on the item while the agent worked on it (reject, ...): their decision stands."""
    rep["notes"].append(f"{it.get('mpn') or it['id']}: left as it is, changed meanwhile ({type(e).__name__}: {e})")


def _read_lines(ws):
    f = ws / "agent" / "library_bom.json"
    if not f.exists():
        return {}
    try:
        lines = json.loads(f.read_text())
    except ValueError:
        lines = None
    if not isinstance(lines, dict):           # starting from {} would drop every part built so far from the plan
        raise RuntimeError(f"{f} is damaged; restore it (its lines are also in {BOM_NAME}) before the agent builds again")
    from .agent_queue import renorm_key
    out = {}
    for k, ln in lines.items():                # keys from before manufacturer names were normalised
        out.setdefault(renorm_key(k), ln)
    for k in [k for k in out if k.startswith("|")]:   # a blank maker line once that MPN has exactly one known maker
        if len([x for x in out if x.endswith(k) and x != k]) == 1:
            del out[k]
    return out


def _build(ws, rules, cfg, t, client, q, prover, builder, libs, log, rep):
    for it in q.list(status="proving"):           # a run died between the build and its proof: prove it now
        try:
            _prove(q, it["id"], ws, rules, client, prover, rep)
        except (ValueError, KeyError) as e:
            _changed_meanwhile(rep, it, e)
    todo = [i for i in q.list() if _wants_build(i)]
    if not todo:
        return
    bom = ws / "agent" / BOM_NAME
    out = _librarian_out(ws, bom)
    plan_before = read_json(out / "plan.json", [])
    repo = repo_root(Path(cfg["library_repo"]) if cfg["library_repo"] else ws)
    lines = _read_lines(ws)
    build_these, refresh = [], []
    for it in todo:
        try:
            if it["status"] == "detected":
                # a new build cycle (retry / re-opened): it gets a new batch and PR, and a follow-up change is in it
                fresh = {k: None for k in ("batch", "pr_url", "followup") if it.get(k)}
                if fresh:
                    q.update(it["id"], **fresh)
                it = q.transition(it["id"], "building", user=AGENT, note="agent run")
            bb = dict(it.get("build") or {})
            bb["attempts"] = int(bb.get("attempts") or 0) + 1
            if bb["attempts"] > MAX_ATTEMPTS:
                _fail(q, it, rep, f"the build stopped the agent {MAX_ATTEMPTS} times: look at its BOM line", t, bb)
                continue
            it = q.update(it["id"], build=bb)
            notes = []
            old = _find_plan_item(plan_before, it)
            before = []
            if old:                                      # BEFORE pictures: the current library version
                files = _model_files(old, out)
                with tempfile.TemporaryDirectory() as td:
                    files = {**_git_before(repo, out, old, td), **files} if len(files) < 2 else files
                    before = pictures_for(ws, prover, it["id"], files, "before", notes) if files else []
            if not _rebuildable(it):
                ch = it.get("change") or {}
                why = ("removed from " + str((ch.get("source") or {}).get("name") or "a watched source") if ch.get("kind") == "removed"
                       else f"{ch.get('check')} finding: {ch.get('kind')} ({ch.get('was')} -> {ch.get('now')})")
                q.update(it["id"], build={"action": "none", "reason": why + "; a person decides", "built": t, "user": AGENT,
                                          "part": old.get("name") if old else None, "hash": None,
                                          "bom": bom.relative_to(ws).as_posix()},
                         pictures={"before": before, "after": []})
                q.transition(it["id"], "proving", user=AGENT, note="nothing to build")
                _prove(q, it["id"], ws, rules, client, prover, rep, note=why)
                continue
        except (ValueError, KeyError) as e:
            _changed_meanwhile(rep, it, e)
            continue
        ln = _line_of(it, plan_before)
        lines[it["key"]] = {k: ln.get(k, "") for k, _ in BOM_COLS}
        if old and ((it.get("build") or {}).get("requested_at") or it.get("origin") == "upkeep"):
            refresh.append(old["name"])
        build_these.append((it, before, notes))
    if not build_these:
        return
    write_json(ws / "agent" / "library_bom.json", lines)
    write_bom(ws, lines)
    from .agent_queue import plan_lock
    try:
        with plan_lock(ws):           # a person's approve/reject waits: the librarian rewrites plan.json from its copy
            plan = (builder or (lambda *a, **k: librarian_builder(*a, libs=libs, **k)))(ws, bom, out, refresh, rules, log)
    except Exception as e:
        for it, _, _ in build_these:
            try:
                q.update(it["id"], build={"error": f"{type(e).__name__}: {e}", "built": t, "user": AGENT})
                q.transition(it["id"], "failed", user=AGENT, note=f"librarian failed: {type(e).__name__}: {e}")
                rep["failed"].append({"id": it["id"], "reason": f"librarian failed: {e}"})
            except (ValueError, KeyError) as e2:
                _changed_meanwhile(rep, it, e2)
        return
    from . import librarian as L
    rows = {r["part"]: r for r in L.review_rows(plan)} if plan else {}
    for it, before, notes in build_these:
        try:
            pi = _find_plan_item(plan, it)
            if pi is None:
                _fail(q, it, rep, "the librarian made no plan entry for this part (check its BOM line)", t)
                continue
            files = _model_files(pi, out)
            tried = (pi.get("librarian") or {}).get("tried", [])
            b = {"part": pi["name"], "bom": bom.relative_to(ws).as_posix(), "user": AGENT, "built": t, "status": pi.get("status"),
                 "hold_reasons": pi.get("hold_reasons", []), "verify": pi.get("verify", []), "source": pi.get("source"),
                 "tried": tried, "notes": notes, "files": {"symbol": (pi.get("symbol") or {}).get("file"),
                                                             "footprint": (pi.get("footprint") or {}).get("file"),
                                                             "kicad_mod": (pi.get("footprint") or {}).get("kicad_mod")},
                 "plan": (out / "plan.json").relative_to(ws).as_posix()}
            prev = q.get(it["id"]).get("build") or {}    # who asked for this rebuild: agent_queue refuses their approval
            b.update({k: prev[k] for k in ("requested_by", "requested_at", "request_note") if prev.get(k)})
            if not (pi["symbol"].get("file") and pi["footprint"].get("file")):
                if any("run again to collect" in x for x in tried):
                    b["pending"] = "datasheet batch submitted; the next run collects it"
                    q.update(it["id"], build=b)
                    rep["waiting"].append(it["id"])
                    continue
                _fail(q, it, rep, "build incomplete: " + (rows.get(pi["name"], {}).get("why") or "no symbol or footprint"), t, b)
                continue
            if q.get(it["id"])["status"] != "building":
                raise ValueError(f"it is {q.get(it['id'])['status']} now")
            b["hash"] = _hash_files({k: v for k, v in files.items()})
            after = pictures_for(ws, prover, it["id"], files, "after", notes)
            q.update(it["id"], build=b, pictures={"before": before, "after": after})
            q.transition(it["id"], "proving", user=AGENT, note=f"built {pi['name']} ({pi.get('status')})")
            rep["built"].append(it["id"])
            _prove(q, it["id"], ws, rules, client, prover, rep)
        except (ValueError, KeyError, OSError) as e:
            _changed_meanwhile(rep, it, e)


def _fail(q, it, rep, reason, t, build=None):
    q.update(it["id"], build=dict(build or {}, error=reason, built=t, user=AGENT))
    q.transition(it["id"], "failed", user=AGENT, note=reason[:500])
    rep["failed"].append({"id": it["id"], "reason": reason})


# ------------------------------------------------------------------ serve
def serve(workspace, interval_s=None, rules=None, stop=None, max_runs=None, run=None, install_signals=True, log=print,
          sleep_s=None, **run_kw):
    """Run run_once every interval (default [agent] interval_minutes) until `stop` (a threading.Event) is set, SIGTERM
    or SIGINT arrives, or max_runs passes ran. While [agent] enabled is false it only keeps the status file fresh.
    Rules are re-read every pass (unless given), so switching the agent on needs no restart."""
    ws = Path(workspace).resolve()
    stop = stop or threading.Event()
    run = run or run_once
    if install_signals and threading.current_thread() is threading.main_thread():
        for s in (signal.SIGTERM, signal.SIGINT):
            signal.signal(s, lambda *_: stop.set())
    runs = 0
    write_status(ws, state="starting", serving=True)
    wait = interval_s if interval_s is not None else 60
    while not stop.is_set():
        try:
            r = RU.resolve(rules, ws) if rules is not None else RU.load(None, start=ws)
            cfg = settings(r)
        except Exception as e:                     # a typo saved in ecadlib.toml: say so and try again, don't stop
            log(f"agent: rules not loaded, no run: {type(e).__name__}: {e}")
            with contextlib.suppress(OSError):
                write_status(ws, state="error", serving=True, last_error=f"rules: {type(e).__name__}: {e}")
            stop.wait(sleep_s if sleep_s is not None else wait)
            continue
        wait = interval_s if interval_s is not None else cfg["interval_minutes"] * 60
        if cfg["enabled"]:
            try:
                rep = run(ws, r, **run_kw)
                log(f"agent: {json.dumps(_summary(rep), default=str)}")
            except Exception as e:                 # one bad pass never ends the service
                log(f"agent: run failed: {type(e).__name__}: {e}")
            runs += 1
        else:
            log("agent: [agent] enabled = false in ecadlib.toml; idle")
        write_status(ws, state="idle" if cfg["enabled"] else "disabled", serving=True, enabled=cfg["enabled"],
                     interval_s=wait, next_run=stamp(time.time() + wait))
        if max_runs is not None and runs >= max_runs:
            break
        stop.wait(sleep_s if sleep_s is not None else wait)
    write_status(ws, state="stopped", serving=False)
    return runs


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib agent", description="library agent: watch, build, prove, review PRs, push, upkeep")
    ap.add_argument("--workspace", default=os.environ.get("ECADLIB_WORKSPACE", "ecadlib_workspace"))
    ap.add_argument("--rules", help="rules file (default: <workspace>/ecadlib.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one pass now (works even while [agent] enabled is false)")
    r.add_argument("--dry-run", action="store_true", help="show what would happen; change nothing")
    r.add_argument("--upkeep", action="store_true", help="run the upkeep checks now, whatever the schedule")
    r.add_argument("--lib", action="append", default=[], help="local KiCad library folder (repeatable)")
    s = sub.add_parser("serve", help="run every [agent] interval_minutes until stopped")
    s.add_argument("--interval", type=float, help="seconds between runs (default: the rules)")
    s.add_argument("--lib", action="append", default=[])
    st = sub.add_parser("status", help="last run, queue counts, batches")
    st.add_argument("--healthy", type=float, metavar="SECONDS", help="exit 1 unless the status was written within SECONDS")
    for name in ("approve", "reject"):
        p = sub.add_parser(name, help=f"{name} a review-queue item")
        p.add_argument("id")
        p.add_argument("--user", help="who signs (default: $ECADLIB_USER, then git config)")
        p.add_argument("--role")
        p.add_argument("-m", "--note", default="")
    pu = sub.add_parser("push", help="push approved parts to Altium 365 now")
    pu.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ws = Path(a.workspace).resolve()
    if not (a.cmd == "run" and a.dry_run):            # a dry run writes nothing, not even the workspace folder
        ws.mkdir(parents=True, exist_ok=True)
    rules = RU.load(a.rules, start=ws)
    if a.cmd == "run":
        rep = run_once(ws, rules, dry_run=a.dry_run, do_upkeep=True if a.upkeep else None, libs=a.lib, log=print)
        print(json.dumps(rep, indent=2, default=str))
        return 1 if rep.get("error") or rep.get("skipped") else 0
    if a.cmd == "serve":
        serve(ws, a.interval, rules if a.rules else None, libs=a.lib)
        return 0
    if a.cmd == "status":
        stt = status(ws)
        print(json.dumps(stt, indent=2, default=str))
        if a.healthy is not None:
            hb = _epoch(stt.get("heartbeat"))
            return 0 if hb and time.time() - hb <= a.healthy else 1
        return 0
    if a.cmd in ("approve", "reject"):
        from . import agent_queue as AQ
        from .approvals import identity
        if a.cmd == "reject" and not a.note.strip():
            print("a reject needs -m saying what is wrong", file=sys.stderr)
            return 2
        from .app import Workspace
        try:                        # decide() records through Workspace.approve_part, so it needs a Workspace, not a path
            r = AQ.decide(Workspace(ws), a.id, a.cmd, a.user or identity(cwd=ws), role=a.role, note=a.note, via="ecadlib agent")
        except (ValueError, PermissionError, KeyError) as e:
            print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
            return 2
        ap_ = r.get("approval")
        print(f"{a.id}: {r['item']['status']}" + (f"; approvals.py: {'recorded' if ap_['recorded'] else ap_['reason']}" if ap_ else ""))
        return 0
    rep = push_approved(ws, rules, a.dry_run)
    print(json.dumps(rep, indent=2, default=str))
    return 1 if rep.get("skipped") or any(x.get("action") == "error" for x in rep["results"]) else 0


if __name__ == "__main__":
    sys.exit(main())
