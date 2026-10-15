"""Weekly upkeep: run the lifecycle, upstream, price/stock and Altium 365 drift checks in one go, report only what is
NEW since the last run, and open (or update) one GitHub issue or pull request listing it.

    python -m ecadlib upkeep PLAN|BOM [--checks lifecycle,upstream,sourcing,a365] [--state DIR] [-o REPORT]
        [--manifest models/manifest.json] [--bom BOM] [--workspace URL] [--apply] [--github issue|pr] [--repo OWNER/NAME]

Checks (each runs in-process; a check whose credentials are missing is skipped with a line saying so, never failed):
  lifecycle  lifecycle.py against Nexar / DigiKey / Mouser (needs at least one of NEXAR_CLIENT_ID+NEXAR_CLIENT_SECRET or
             NEXAR_SUPPLY_TOKEN, DIGIKEY_CLIENT_ID+DIGIKEY_CLIENT_SECRET, MOUSER_API_KEY). New: a part became NRND, last
             time buy or EOL/obsolete, stock fell to zero, a part recovered, or a newly listed part is already risky.
  upstream   upstream.check_kicad on the KiCad provenance manifest (kicad_import writes <out>/manifest.json; found next to
             the plan, in <plan dir>/models/, or given with --manifest). GITLAB_TOKEN optional. New: a KiCad source
             changed, was renamed or deleted since the last report. --apply reconverts parts whose sources *changed*
             (not renamed/deleted: those need a person) so a PR carries the real library diff.
  sourcing   sourcing.py (Nexar / DigiKey / Mouser with credentials, JLCPCB without). New: the best unit price for a line
             rose by more than the threshold (default 20 %, [upkeep] price_jump_pct), a line went out of stock at
             every distributor, or came back.
  a365       upstream.check_a365 against an Altium 365 workspace (NEXAR_TOKEN and --workspace, or [altium365] workspace
             in the rules). New: a drift row (missing, parameter, folder, symbol/footprint, revision) not reported before.

State (--state DIR, default out/upkeep_state): one JSON file per check; what each run saw, so the next run reports only
transitions. Report: <REPORT>.md and <REPORT>.json (default out/upkeep). Exit codes: 0 nothing new, 1 new changes,
2 error (a check failed, bad input, or the GitHub step failed; the report is still written, state is not saved, so the
next run reports the same changes again).

GitHub (--github issue|pr; only when something is new): GITHUB_TOKEN + GITHUB_REPOSITORY (or --repo) uses the REST API,
otherwise the `gh` CLI (`gh api`). issue: one rolling issue labelled `ecadlib-upkeep`; it is found by the label and its
body replaced (plus a short comment so watchers are notified), never duplicated. pr: needs --apply with real changes;
commits the changed library files to branch `ecadlib/upkeep-YYYY-MM-DD`, pushes it and opens (or updates) the PR; with
nothing to commit it falls back to the issue.

GitLab (--gitlab issue|pr): the same rolling issue or a merge request through GitLab REST v4 (ecadlib/forge.py); token
from $GITLAB_TOKEN (or [agent] gitlab_token_env), project from --gitlab-project / CI_PROJECT_PATH / [agent]
gitlab_project / the origin remote, API from --gitlab-api / CI_API_V4_URL / [agent] gitlab_api (self-hosted works).

Notifications: with [notify] targets in the rules, a run that found new changes (and saved its state) posts an
upkeep_changes event, and a lifecycle event for parts that went EOL / NRND / last time buy (ecadlib/notify.py).

Rules (ecadlib.toml, optional): [upkeep] checks = [...], schedule = "weekly", price_jump_pct = 20, build_qty = 1,
currency = "USD"; [altium365] workspace = "https://..."."""
import argparse, hashlib, json, os, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

CHECKS = ("lifecycle", "upstream", "sourcing", "a365")
LABEL = "ecadlib-upkeep"
DEFAULTS = {"schedule": "weekly", "checks": list(CHECKS), "price_jump_pct": 20.0, "build_qty": 1, "currency": "USD"}
LIFECYCLE_ENV = "NEXAR_CLIENT_ID+NEXAR_CLIENT_SECRET (or NEXAR_SUPPLY_TOKEN), DIGIKEY_CLIENT_ID+DIGIKEY_CLIENT_SECRET, MOUSER_API_KEY"
BODY_MAX = 60000                                    # GitHub issue/PR bodies stop at 65536 characters


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fp(*parts):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:20]


# ------------------------------------------------------------------ rules (ecadlib.rules when present)
def load_rules(path=None, start="."):
    try:
        from . import rules as RU
    except ImportError:
        RU = None
    if RU is not None:
        return RU.load(path, start=start)
    if not path:
        return {}
    p = Path(path)
    if p.suffix == ".toml":
        import tomllib
        return tomllib.loads(p.read_text(encoding="utf-8"))
    return json.loads(p.read_text(encoding="utf-8"))


def upkeep_rules(rules):
    u = dict(DEFAULTS)
    u.update((rules or {}).get("upkeep") or {})
    return u


# ------------------------------------------------------------------ inputs
class Source:
    """The input: a plan.json (lifecycle, sourcing, a365) or a BOM (lifecycle, sourcing); plus where the manifest is."""
    def __init__(self, path, manifest=None, bom=None, columns=None):
        self.path, self.columns = Path(path), columns   # columns: the rules' [bom] columns
        if not self.path.is_file():
            raise FileNotFoundError(f"input not found: {path}")
        self.kind = "plan" if self.path.suffix.lower() == ".json" else "bom"
        self.items = json.loads(self.path.read_text(encoding="utf-8")) if self.kind == "plan" else None
        if self.items is not None and not isinstance(self.items, list):
            raise ValueError(f"{path}: not an altium365 plan (expected a JSON list of items)")
        self.bom = Path(bom) if bom else (self.path if self.kind == "bom" else None)
        self.manifest = Path(manifest) if manifest else self._find_manifest()

    def _find_manifest(self):
        d = self.path.parent
        for c in (d / "manifest.json", d / "models" / "manifest.json", d / "altium_models_kicad" / "manifest.json"):
            if c.is_file():
                return c
        return None

    def lines(self):
        """BOM lines for sourcing: the BOM when there is one, else one line per plan item."""
        from .bom_triage import read_bom
        if self.bom:
            return read_bom(self.bom, self.columns)[0]
        out = []
        for it in self.items:
            if it.get("status") == "duplicate":
                continue
            prm = {p["name"]: p["value"] for p in it.get("parameters", [])}
            lcsc = prm.get("Supplier Part Number 1", "") if prm.get("Supplier 1", "").upper() in ("LCSC", "JLCPCB") else ""
            out.append({"designator": "", "qty": "1", "mpn": prm.get("Manufacturer Part Number", ""),
                        "manufacturer": prm.get("Manufacturer", ""), "lcsc": lcsc, "value": prm.get("Value", ""),
                        "description": it.get("description", ""), "footprint": (it.get("footprint") or {}).get("ref", "")})
        return out


class Ctx:
    """Clients the checks use; tests pass fakes. http: CachedHTTP-like; gitlab: upstream.GitLab; design: Nexar Design
    client with gql(); env: environment mapping (credential checks)."""
    def __init__(self, http=None, gitlab=None, design=None, env=None, cache="out/http_cache", offline=False):
        self._http, self._gitlab, self.design, self.env = http, gitlab, design, os.environ if env is None else env
        self.cache, self.offline = cache, offline

    @property
    def http(self):
        if self._http is None:
            from .httpcache import CachedHTTP
            self._http = CachedHTTP(self.cache, min_interval=1.0, ttl_s=12 * 3600, offline=self.offline)
        return self._http

    @property
    def gitlab(self):
        if self._gitlab is None:
            from .upstream import GitLab
            self._gitlab = GitLab(token=self.env.get("GITLAB_TOKEN"))
        return self._gitlab


def _section(name, status="ok", reason=""):
    return {"check": name, "status": status, "reason": reason, "changes": [], "resolved": [], "notes": [],
            "applied": [], "_state": None}


def _change(kind, item, was="", now="", detail=""):
    return {"kind": kind, "item": item, "was": was, "now": now, "detail": detail}


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


# ------------------------------------------------------------------ lifecycle
def check_lifecycle(src, state_dir, ctx, cfg, now):
    from . import lifecycle as L
    sec = _section("lifecycle")
    provs = [c(ctx.http) for c in L.PROVIDERS]
    with _environ(ctx.env):
        creds = [p.name for p in provs if p.name != "jlcpcb_search" and p.configured()]
        if not creds:
            sec.update(status="skipped", reason=f"no lifecycle credentials: set one of {LIFECYCLE_ENV}")
            return sec
        parts, skipped = L.merge(L.read_inputs([src.path] + ([src.bom] if src.bom and src.bom != src.path else []), src.columns))
        if not parts:
            sec.update(status="error", reason="no parts with an MPN or LCSC number in the input")
            return sec
        current, unconf = L.check_parts(parts, provs)
    state = L.load_state(state_dir / "lifecycle_state.json")
    rows = L.diff(state, current)
    if not any(r["answered"] for r in rows):
        sec.update(status="error", reason="no source answered for any part (quota, network or bad credentials)")
        return sec
    sev = lambda s: L.SEVERITY.get(s, -1)
    desc = lambda s, k: f"{L.LABEL.get(s, s or '-')}, stock {'-' if k is None else k}"
    for r in rows:
        item, was, cur = r["mpn"] or r["lcsc"], desc(r["prev_status"], r["prev_stock"]), desc(r["status"], r["stock"])
        if r["change"] == "worse":
            kind = ({"nrnd": "NRND", "ltb": "last time buy", "obsolete": "EOL"}[r["status"]]
                    if r["status"] != "unknown" and sev(r["status"]) > max(sev(r["prev_status"]), 0) else "stock-out")
            sec["changes"].append(_change(kind, item, was, cur, r["raw"]))
        elif r["change"] == "better":
            sec["changes"].append(_change("recovered", item, was, cur, r["raw"]))
        elif r["change"] == "new" and L.risky(r):
            sec["changes"].append(_change("new risky part", item, "(not watched)", cur, r["raw"]))
    if unconf:
        sec["notes"].append(f"not configured: {', '.join(unconf)}")
    if skipped:
        sec["notes"].append(f"skipped without MPN or LCSC: {', '.join(skipped)}")
    sec["notes"].append(f"{len(rows)} parts checked" + ("" if state.get("updated") else " (first run: baseline recorded)"))
    sec["_state"] = {"lifecycle_state.json": L.next_state(state, current, now)}
    return sec


class _environ:
    """Run provider code against ctx.env (tests pass a dict) without leaking it into os.environ afterwards."""
    def __init__(self, env):
        self.env = env

    def __enter__(self):
        if self.env is os.environ:
            return
        self.old = dict(os.environ)
        os.environ.clear()
        os.environ.update({k: v for k, v in self.env.items() if v is not None})

    def __exit__(self, *exc):
        if self.env is not os.environ:
            os.environ.clear()
            os.environ.update(self.old)


# ------------------------------------------------------------------ sourcing
def check_sourcing(src, state_dir, ctx, cfg, now):
    from . import sourcing as S
    from .kicad_index import norm_mpn
    sec = _section("sourcing")
    if src.kind == "plan" and not src.items:
        sec.update(status="skipped", reason="the plan has no items")
        return sec
    cur_ccy, qty, pct = str(cfg["currency"]).upper(), int(cfg["build_qty"]), float(cfg["price_jump_pct"])
    provs = [c(ctx.http) for c in S.PROVIDERS]
    for p in provs:
        p.currency = cur_ccy
    with _environ(ctx.env):
        creds = [p.name for p in provs if p.name != "jlcpcb_search" and p.configured()]
        rows, unconf = S.source_bom(src.lines(), provs, qty, cur_ccy, False)
    fitted = [r for r in rows if "dnp" not in r["flags"]]
    if fitted and not any(r["answered"] for r in fitted):
        if not creds:
            sec.update(status="skipped", reason=f"no distributor answered and no credentials set ({LIFECYCLE_ENV})")
        else:
            sec.update(status="error", reason="no source answered for any line (quota, network or bad credentials)")
        return sec
    old = _load(state_dir / "sourcing_state.json", {"version": 1, "updated": None, "lines": {}})
    first = old.get("updated") is None
    lines = dict(old["lines"])
    for r in fitted:
        key = norm_mpn(r["mpn"]) if r["mpn"] else f"LCSC:{r['lcsc']}" if r["lcsc"] else ""
        if not key or not r["answered"] or "not_found" in r["flags"]:
            continue
        c = r["chosen"] or {}
        cur = {"mpn": r["mpn"] or r["lcsc"], "unit": c.get("unit"), "currency": cur_ccy, "distributor": c.get("distributor"),
               "in_stock": r["distributors_in_stock"], "checked": now}
        prev = old["lines"].get(key)
        price = lambda d: "-" if d.get("unit") is None else f"{d['unit']:g} {d.get('currency', cur_ccy)} ({d.get('distributor') or '-'})"
        if prev is None:
            if cur["in_stock"] == 0 and not first:
                sec["changes"].append(_change("stock-out", cur["mpn"], "(new line)", "no distributor has stock"))
            elif cur["in_stock"] == 0:
                sec["changes"].append(_change("stock-out", cur["mpn"], "(first run)", "no distributor has stock"))
        else:
            if prev.get("in_stock", 0) > 0 and cur["in_stock"] == 0:
                sec["changes"].append(_change("stock-out", cur["mpn"], f"{prev['in_stock']} distributors", "none in stock"))
            elif prev.get("in_stock") == 0 and cur["in_stock"] > 0:
                sec["changes"].append(_change("back in stock", cur["mpn"], "none in stock", f"{cur['in_stock']} distributors"))
            pu, cu = prev.get("unit"), cur["unit"]
            if pu and cu is not None and prev.get("currency", cur_ccy) == cur_ccy and cu > pu * (1 + pct / 100):
                sec["changes"].append(_change("price jump", cur["mpn"], price(prev), price(cur),
                                              f"+{(cu / pu - 1) * 100:.0f}% (threshold {pct:g}%) at {r['need']} pcs"))
        if cur["unit"] is None and prev and prev.get("currency", cur_ccy) == cur_ccy:   # unpriced: keep the last price
            cur.update(unit=prev.get("unit"), distributor=prev.get("distributor"))
        lines[key] = cur
    if unconf:
        sec["notes"].append(f"not configured: {', '.join(unconf)}")
    sec["notes"].append(f"{len(fitted)} lines priced for {qty} board(s) in {cur_ccy}" + (" (first run: baseline recorded)" if first else ""))
    sec["_state"] = {"sourcing_state.json": {"version": 1, "updated": now, "lines": lines}}
    return sec


# ------------------------------------------------------------------ KiCad upstream
def check_upstream(src, state_dir, ctx, cfg, now, apply=False):
    from . import upstream as U
    sec = _section("upstream")
    if not src.manifest or not src.manifest.is_file():
        sec.update(status="skipped", reason="no KiCad provenance manifest (kicad_import writes OUT/manifest.json; "
                                            "pass --manifest)")
        return sec
    man = json.loads(src.manifest.read_text(encoding="utf-8"))
    rows, worst = U.check_kicad(man, ctx.gitlab)
    errs = [r for r in rows if r["status"] == "error"]
    for r in errs:
        sec["notes"].append(f"{r['entry']} {r['source']}: {r['summary']}")
    if errs and len(errs) == len(rows):
        sec.update(status="error", reason="every KiCad source failed: " + errs[0]["summary"])
        return sec
    old = _load(state_dir / "upstream_state.json", {"version": 1, "updated": None, "seen": {}})
    seen, applied = {}, {}
    if apply:
        ch = [r for r in rows if r["status"] == "changed"]
        if ch:
            pins = {r["_src"]["url"]: r["_text"] for r in ch if r["_src"].get("url") and r["_text"] is not None}
            todo = sorted({r["entry"] for r in ch})
            done = U.reconvert([man["entries"][e] for e in todo], src.manifest.parent, U.Pinned(ctx.http, pins))
            for r in rows:
                rep = done.get(json.dumps(r["spec"], sort_keys=True))
                if rep:
                    r["update"] = "reconverted" if rep.get("ok") else f"reconvert failed: {rep.get('error')}"
                    if rep.get("ok"):
                        applied[r["entry"]] = man["entries"][r["entry"]]["output"]
    for r in rows:
        if r["status"] in ("unchanged", "error"):
            continue
        key = _fp(r["entry"], r["source"], r["status"], hashlib.sha256((r["_text"] or r["summary"]).encode()).hexdigest())
        if r.get("update") == "reconverted":
            sec["changes"].append(_change("reconverted", r["entry"], r["source"], r["upstream_commit"] or "upstream", r["summary"]))
            continue                                       # the manifest now matches upstream: nothing left to remember
        seen[key] = {"entry": r["entry"], "source": r["source"], "status": r["status"], "first_seen": old["seen"].get(key, {}).get("first_seen", now)}
        if key not in old["seen"]:
            sec["changes"].append(_change(f"KiCad {r['status']}", r["entry"], r["source"],
                                          (r["upstream_commit"] + " " + r["upstream_date"]).strip() or r["status"],
                                          r["summary"] + (f" [{r['update']}]" if r.get("update") else "")))
    failed = {(r["entry"], r["source"]) for r in errs}
    for k, v in old["seen"].items():
        if k not in seen and (v["entry"], v["source"]) in failed:
            seen[k] = v                                    # could not check it this time: still outstanding
    for k, v in old["seen"].items():
        if k not in seen and not any(c["item"] == v["entry"] and c["kind"] == "reconverted" for c in sec["changes"]):
            sec["resolved"].append(_change("resolved", v["entry"], v["source"], "no longer reported", v["status"]))
    sec["applied"] = [str(src.manifest.parent / f) for f in sorted(set(applied.values()))]
    if sec["applied"]:
        sec["applied"].append(str(src.manifest))
    counts = {s: sum(v == s for v in worst.values()) for s in U.RANK}
    sec["notes"].append(f"manifest `{src.manifest}`: " + ", ".join(f"{v} {s}" for s, v in counts.items() if v))
    unsafe = [r["entry"] for r in rows if r["status"] in ("renamed", "deleted")]
    if apply and unsafe:
        sec["notes"].append(f"not applied (renamed/deleted upstream, needs a person): {', '.join(sorted(set(unsafe)))}")
    if errs:
        sec["status"], sec["reason"] = "error", f"{len(errs)} KiCad source(s) could not be checked"
    sec["_state"] = {"upstream_state.json": {"version": 1, "updated": now, "seen": seen}}
    return sec


# ------------------------------------------------------------------ Altium 365 drift
def check_a365(src, state_dir, ctx, cfg, now, workspace=None):
    from . import upstream as U
    sec = _section("a365")
    if src.kind != "plan":
        sec.update(status="skipped", reason="needs a plan.json (drift compares the plan with the workspace)")
        return sec
    if not workspace:
        sec.update(status="skipped", reason="no workspace: pass --workspace URL or set [altium365] workspace in ecadlib.toml")
        return sec
    client = ctx.design
    if client is None:
        token = ctx.env.get("NEXAR_TOKEN")
        if not token:
            sec.update(status="skipped", reason="NEXAR_TOKEN not set (Nexar user token: design.domain, user.access, openid)")
            return sec
        from .altium365 import Nexar
        client = Nexar(token)
    old = _load(state_dir / "a365_state.json", {"version": 1, "updated": None, "revisions": None, "seen": []})
    rows, revs = U.check_a365(src.items, U.workspace_components(client, workspace), None, old.get("revisions"))
    seen = []
    for r in rows:
        key = _fp(r["name"], r["status"], r["field"], r["plan"], r["workspace"])
        seen.append(key)
        if key not in old["seen"]:
            sec["changes"].append(_change(f"A365 {r['status']}", r["name"], r["plan"], r["workspace"],
                                          " ".join(x for x in (r["field"], r["detail"]) if x)))
    sec["resolved"] = [_change("resolved", "", "", "", f"{n} drift row(s) from earlier runs no longer seen")] \
        if (n := len(set(old["seen"]) - set(seen))) else []
    sec["notes"].append(f"{len(rows)} drift rows in `{workspace}`, {len(sec['changes'])} new")
    sec["_state"] = {"a365_state.json": {"version": 1, "updated": now, "revisions": revs, "seen": seen}}
    return sec


# ------------------------------------------------------------------ run, report, state
TITLES = {"lifecycle": "Lifecycle (NRND / EOL / stock)", "upstream": "KiCad upstream", "sourcing": "Price and stock",
          "a365": "Altium 365 drift"}


def run(input_path, checks=None, state_dir="out/upkeep_state", out="out/upkeep", manifest=None, bom=None,
        workspace=None, apply=False, rules=None, ctx=None, now=None, save=True):
    """Run the checks, write <out>.md and <out>.json, save the state (save=False leaves that to save_state)."""
    cfg = upkeep_rules(rules)
    checks = list(checks or cfg["checks"])
    bad = [c for c in checks if c not in CHECKS]
    if bad:
        raise ValueError(f"unknown check(s) {', '.join(bad)}; choose from {', '.join(CHECKS)}")
    workspace = workspace or ((rules or {}).get("altium365") or {}).get("workspace") or None
    ctx, now, state_dir = ctx or Ctx(), now or _now(), Path(state_dir)
    src = Source(input_path, manifest, bom, dict(((rules or {}).get("bom") or {}).get("columns") or {}) or None)
    prev = _load(state_dir / "upkeep_last.json", {}).get("date")
    sections = []
    for name in checks:
        try:
            if name == "lifecycle":
                sec = check_lifecycle(src, state_dir, ctx, cfg, now)
            elif name == "sourcing":
                sec = check_sourcing(src, state_dir, ctx, cfg, now)
            elif name == "upstream":
                sec = check_upstream(src, state_dir, ctx, cfg, now, apply)
            else:
                sec = check_a365(src, state_dir, ctx, cfg, now, workspace)
        except Exception as e:                       # one broken check never hides the others
            sec = _section(name, "error", f"{type(e).__name__}: {e}")
        sections.append(sec)
    new = sum(len(s["changes"]) for s in sections)
    error = any(s["status"] == "error" for s in sections)
    rep = {"version": 1, "date": now, "previous_run": prev, "input": str(input_path), "input_kind": src.kind,
           "schedule": cfg["schedule"], "checks": sections, "new_changes": new,
           "applied_paths": [p for s in sections for p in s["applied"]], "exit_code": 2 if error else 1 if new else 0,
           "state_dir": str(state_dir), "report": str(Path(out).with_suffix(".md"))}
    write_report(rep, out)
    if save:
        save_state(rep, state_dir)
    return rep


def save_state(rep, state_dir):
    """Write each check's new state (only checks that ran cleanly; an errored check keeps its old state)."""
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    for s in rep["checks"]:
        if s["status"] == "ok" and s.get("_state"):
            for f, data in s["_state"].items():
                tmp = d / (f + ".tmp")
                tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
                os.replace(tmp, d / f)
    (d / "upkeep_last.json").write_text(json.dumps({"date": rep["date"], "new_changes": rep["new_changes"]}, indent=1), encoding="utf-8")


def _esc(v):
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def markdown(rep, limit=None):
    day = rep["date"][:10]
    md = [f"# Library upkeep {day}", "",
          f"Input `{rep['input']}` ({rep['input_kind']}), run {rep['date']}; previous run: {rep['previous_run'] or 'none'}.", ""]
    md.append(f"**{rep['new_changes']} new change(s)** since the last run." if rep["new_changes"] else "Nothing new since the last run.")
    md += ["", "| Check | Result | New | Notes |", "|---|---|---|---|"]
    for s in rep["checks"]:
        res = s["status"] + (f": {s['reason']}" if s["reason"] else "")
        md.append(f"| {TITLES[s['check']]} | {_esc(res)} | {len(s['changes']) if s['status'] != 'skipped' else '-'} | "
                  f"{_esc('; '.join(s['notes'])[:300])} |")
    for s in rep["checks"]:
        if not (s["changes"] or s["resolved"]):
            continue
        md += ["", f"## {TITLES[s['check']]}", "", "| Change | Item | Was | Now | Detail |", "|---|---|---|---|---|"]
        rows = s["changes"] + s["resolved"]
        for c in rows[:limit] if limit else rows:
            md.append(f"| {_esc(c['kind'])} | {_esc(c['item'])} | {_esc(c['was'])} | {_esc(c['now'])} | {_esc(c['detail'])[:400]} |")
        if limit and len(rows) > limit:
            md.append(f"| ... | {len(rows) - limit} more in the JSON report | | | |")
    if rep["applied_paths"]:
        md += ["", "## Applied", "", "KiCad parts whose sources changed were reconverted; changed files:", ""]
        md += [f"- `{p}`" for p in rep["applied_paths"]]
    md += ["", f"<sub>ecadlib upkeep ({rep['schedule']}); state `{rep['state_dir']}`. Only changes not reported by an "
               "earlier run are listed.</sub>"]
    return "\n".join(md) + "\n"


def write_report(rep, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".md").write_text(markdown(rep), encoding="utf-8")
    clean = {**rep, "checks": [{k: v for k, v in s.items() if not k.startswith("_")} for s in rep["checks"]]}
    out.with_suffix(".json").write_text(json.dumps(clean, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ GitHub
class GitHubError(Exception):
    pass


def default_runner(cmd, input=None, cwd=None):
    """(returncode, stdout, stderr) of a command; tests pass a fake."""
    if shutil.which(cmd[0]) is None:
        return 127, "", f"{cmd[0]}: command not found"
    p = subprocess.run(cmd, input=input, capture_output=True, text=True, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


class GitHub:
    """GitHub REST, sent with requests + GITHUB_TOKEN (session=) or through `gh api` (runner=). Paths are relative to
    repos/<repo>; with gh and no repo, gh fills {owner}/{repo} from the checkout."""
    API = "https://api.github.com"

    def __init__(self, repo=None, token=None, session=None, runner=None):
        self.repo, self.token, self.session, self.runner = repo, token, session, runner or default_runner
        if token and not repo:
            raise GitHubError("GITHUB_TOKEN is set but the repository is unknown: pass --repo OWNER/NAME or set GITHUB_REPOSITORY")
        self.mode = "rest" if token else "gh"

    def api(self, method, path, body=None):
        path = f"repos/{self.repo or '{owner}/{repo}'}/{path}"
        if self.mode == "rest":
            if self.session is None:
                import requests
                self.session = requests.Session()
            r = self.session.request(method, f"{self.API}/{path}", json=body, timeout=60, headers={
                "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"})
            if not 200 <= r.status_code < 300:
                raise GitHubError(f"GitHub {method} /{path}: HTTP {r.status_code} {str(getattr(r, 'text', ''))[:200]}")
            return r.json() if r.status_code != 204 and getattr(r, "content", b"") else None
        code, out, err = self.runner(["gh", "api", "-X", method, path] + (["--input", "-"] if body is not None else []),
                                     input=json.dumps(body) if body is not None else None)
        if code:
            raise GitHubError(f"gh api {method} {path}: {(err or out).strip()[:300]}")
        return json.loads(out) if out.strip() else None

    def ensure_label(self):
        try:
            self.api("POST", "labels", {"name": LABEL, "color": "0e8a16", "description": "ecadlib weekly library upkeep"})
        except GitHubError:
            pass                                          # 422: it exists already

    def rolling_issue(self):
        issues = self.api("GET", f"issues?labels={LABEL}&state=open&per_page=100") or []
        issues = [i for i in issues if "pull_request" not in i]
        return min(issues, key=lambda i: i["number"]) if issues else None


def _title(rep):
    return f"Library upkeep: {rep['new_changes']} new change(s) ({rep['date'][:10]})"


def _body(rep):
    md = markdown(rep)
    return md if len(md) <= BODY_MAX else markdown(rep, limit=40)[:BODY_MAX]


def publish_issue(rep, gh):
    """Update the open issue labelled ecadlib-upkeep (body replaced, a comment added) or open it."""
    title, body = _title(rep), _body(rep)
    if getattr(gh, "kind", "") == "gitlab":           # forge.GitLab: the same rolling issue on GitLab
        from .forge import upkeep_issue
        return upkeep_issue(gh, LABEL, title, body, rep["new_changes"], rep["date"][:10])
    iss = gh.rolling_issue()
    if iss:
        gh.api("PATCH", f"issues/{iss['number']}", {"title": title, "body": body})
        gh.api("POST", f"issues/{iss['number']}/comments",
               {"body": f"{rep['new_changes']} new change(s) on {rep['date'][:10]}: the description now has this week's report."})
        return {"kind": "issue", "action": "updated", "number": iss["number"], "url": iss.get("html_url")}
    gh.ensure_label()
    new = gh.api("POST", "issues", {"title": title, "body": body, "labels": [LABEL]})
    return {"kind": "issue", "action": "opened", "number": new["number"], "url": new.get("html_url")}


def publish_pr(rep, gh, runner=None, base=None, cwd=None):
    """Commit the applied library files to ecadlib/upkeep-YYYY-MM-DD, push, open or update the PR. None when there is
    nothing to commit (the caller falls back to the issue)."""
    run = runner or default_runner
    paths = rep["applied_paths"]
    if not paths:
        return None

    def git(*args, ok=(0,)):
        code, out, err = run(["git", *args], cwd=cwd)
        if code not in ok:
            raise GitHubError(f"git {' '.join(args[:3])}: {(err or out).strip()[:300]}")
        return out.strip()
    if not git("status", "--porcelain", "--", *paths):
        return None
    base = base or git("rev-parse", "--abbrev-ref", "HEAD")
    branch = f"ecadlib/upkeep-{rep['date'][:10]}"
    ident = [] if git("config", "user.email", ok=(0, 1)) else ["-c", "user.name=ecadlib-upkeep",
                                                                 "-c", "user.email=ecadlib-upkeep@users.noreply.github.com"]
    # a same-day re-run replaces the branch: only ever overwrite what upkeep itself pushed there, never a person's
    # commits (a fix pushed to the PR), and only if the remote is still where we looked (lease)
    remote = git("ls-remote", "origin", f"refs/heads/{branch}")
    remote_sha = remote.split()[0] if remote else ""
    if remote_sha:
        git("fetch", "--no-tags", "origin", f"refs/heads/{branch}")
        theirs = [s for s in git("log", "--no-merges", "--format=%s", remote_sha, "--not", "HEAD").splitlines()
                  if s.strip() and not s.startswith("ecadlib upkeep ")]
        if theirs:
            raise GitHubError(f"origin/{branch} has commits not made by upkeep ({theirs[0][:80]!r}): not overwriting "
                              "them; merge or close that PR and delete the branch")
    git("switch", "-C", branch)
    try:
        git("add", "--", *paths)
        git(*ident, "commit", "-m", f"ecadlib upkeep {rep['date'][:10]}: reconvert KiCad parts changed upstream")
        git("push", f"--force-with-lease=refs/heads/{branch}:{remote_sha}", "-u", "origin", branch)
    finally:
        git("switch", base, ok=(0, 1, 128))
    title, body = _title(rep), _body(rep)
    if getattr(gh, "kind", "") == "gitlab":
        from .forge import upkeep_mr
        return upkeep_mr(gh, LABEL, branch, base, title, body)
    prs = [p for p in gh.api("GET", "pulls?state=open&per_page=100") or [] if (p.get("head") or {}).get("ref") == branch
           and ((p["head"].get("repo") or {}).get("full_name") or "") == (((p.get("base") or {}).get("repo") or {}).get("full_name") or "")]
    if prs:
        gh.api("PATCH", f"pulls/{prs[0]['number']}", {"title": title, "body": body})
        return {"kind": "pr", "action": "updated", "number": prs[0]["number"], "url": prs[0].get("html_url"), "branch": branch}
    pr = gh.api("POST", "pulls", {"title": title, "head": branch, "base": base, "body": body})
    gh.ensure_label()
    gh.api("POST", f"issues/{pr['number']}/labels", {"labels": [LABEL]})
    return {"kind": "pr", "action": "opened", "number": pr["number"], "url": pr.get("html_url"), "branch": branch}


def publish(rep, mode, gh, runner=None, base=None, cwd=None):
    if not rep["new_changes"] and not rep["applied_paths"]:
        return {"kind": mode, "action": "none", "detail": f"nothing new: {'GitLab' if getattr(gh, 'kind', '') == 'gitlab' else 'GitHub'} not touched"}
    if mode == "pr":
        res = publish_pr(rep, gh, runner, base, cwd)
        if res:
            return res
    return publish_issue(rep, gh)


# ------------------------------------------------------------------ CLI
def main(argv=None, ctx=None, gh=None, runner=None, notifier=None):
    ap = argparse.ArgumentParser(description="Weekly library upkeep: lifecycle, KiCad upstream, price/stock and "
                                             "Altium 365 drift; report what is new; one GitHub issue or PR")
    ap.add_argument("input", help="altium365 plan.json (all checks) or a BOM .csv/.xlsx (lifecycle, sourcing)")
    ap.add_argument("--checks", help=f"comma list from {','.join(CHECKS)} (default: [upkeep] checks, else all)")
    ap.add_argument("--state", default="out/upkeep_state", help="folder with the last run's state")
    ap.add_argument("-o", "--out", default="out/upkeep", help="report path stem (.md and .json)")
    ap.add_argument("--manifest", help="KiCad provenance manifest.json (default: next to the plan, or <plan dir>/models/)")
    ap.add_argument("--bom", help="BOM for the price/stock check when the input is a plan (quantities per board)")
    ap.add_argument("--workspace", help="Altium 365 workspace URL for the a365 check")
    ap.add_argument("--apply", action="store_true", help="reconvert KiCad parts whose sources changed (safe fixes only)")
    ap.add_argument("--github", choices=("issue", "pr"), help="open/update one rolling issue, or a PR with applied changes")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME (default GITHUB_REPOSITORY)")
    ap.add_argument("--base", help="PR base branch (default: the current branch)")
    ap.add_argument("--gitlab", choices=("issue", "pr"), help="the same on GitLab: one rolling issue, or a merge request "
                    "(token from [agent] gitlab_token_env, default GITLAB_TOKEN)")
    ap.add_argument("--gitlab-project", default=os.environ.get("CI_PROJECT_PATH"),
                    help="group/name or id (default CI_PROJECT_PATH, else [agent] gitlab_project, else the origin remote)")
    ap.add_argument("--gitlab-api", default=os.environ.get("CI_API_V4_URL"),
                    help="https://host/api/v4 (default CI_API_V4_URL, else [agent] gitlab_api)")
    ap.add_argument("--no-notify", action="store_true", help="do not send [notify] webhooks for this run")
    ap.add_argument("--rules", help="ecadlib.toml (default: searched from the input's folder)")
    ap.add_argument("--qty", type=int, help="boards per build for prices (default [upkeep] build_qty, else 1)")
    ap.add_argument("--currency", help="default [upkeep] currency, else USD")
    ap.add_argument("--price-jump", type=float, help="report a unit price rise above this percent (default 20)")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="HTTP cache only")
    ap.add_argument("--no-save", action="store_true", help="do not update the state (dry run)")
    a = ap.parse_args(argv)
    try:
        rules = load_rules(a.rules, start=str(Path(a.input).resolve().parent))
        up = dict((rules or {}).get("upkeep") or {})
        for k, v in (("build_qty", a.qty), ("currency", a.currency), ("price_jump_pct", a.price_jump)):
            if v is not None:
                up[k] = v
        rules = {**(rules or {}), "upkeep": up}
        checks = [c.strip() for c in a.checks.split(",") if c.strip()] if a.checks else None
        rep = run(a.input, checks, a.state, a.out, a.manifest, a.bom, a.workspace, a.apply, rules,
                  ctx or Ctx(cache=a.cache, offline=a.offline), save=False)
    except Exception as e:
        print(f"upkeep: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    for s in rep["checks"]:
        print(f"{s['check']:<10} {s['status']:<8} {len(s['changes'])} new" + (f"  {s['reason']}" if s["reason"] else ""))
        for c in s["changes"]:
            print(f"    {c['kind']:<16} {str(c['item'])[:32]:<32} {str(c['was'])[:28]} -> {str(c['now'])[:28]}")
    print(f"{rep['new_changes']} new change(s) -> {Path(a.out).with_suffix('.md')} and .json")
    code = rep["exit_code"]
    if a.gitlab:
        try:
            res = publish(rep, a.gitlab, gh or _gitlab_client(rules, a, runner), runner, a.base)
            rep["gitlab"] = res
            print(f"gitlab: {res['kind']} {res['action']}" + (f" !{res['number']}" if res.get("number") and res["kind"] == "pr"
                  else f" #{res['number']}" if res.get("number") else "") + (f" {res['url']}" if res.get("url") else ""))
        except Exception as e:
            print(f"gitlab: {type(e).__name__}: {e} (state not saved: next run reports these changes again)", file=sys.stderr)
            rep["gitlab"] = {"error": f"{type(e).__name__}: {e}"}
            write_report(rep, a.out)
            return 2
        write_report(rep, a.out)
    if a.github:
        try:
            token = os.environ.get("GITHUB_TOKEN")             # REST when token and repo are known, else `gh api`
            g = gh or GitHub(a.repo, token if token and a.repo else None, runner=runner)
            res = publish(rep, a.github, g, runner, a.base)
            rep["github"] = res
            print(f"github: {res['kind']} {res['action']}" + (f" #{res['number']}" if res.get("number") else "")
                  + (f" {res['url']}" if res.get("url") else ""))
        except Exception as e:
            print(f"github: {type(e).__name__}: {e} (state not saved: next run reports these changes again)", file=sys.stderr)
            rep["github"] = {"error": f"{type(e).__name__}: {e}"}
            write_report(rep, a.out)
            return 2
        write_report(rep, a.out)
    if not a.no_save:
        save_state(rep, a.state)
        if not a.no_notify and rep["new_changes"]:      # only once per change: a dry run (--no-save) repeats them
            from . import notify as N
            for r in N.send(rules, N.upkeep_events(rep), sender=notifier, log=lambda m: print(m, file=sys.stderr)):
                print(f"notify: {r['target']} {r['event']} {r['result']}")
    return code


def _gitlab_client(rules, a, runner=None):
    """forge.GitLab for --gitlab: project from --gitlab-project / [agent] gitlab_project / the origin remote."""
    from . import forge
    ag = dict((rules or {}).get("agent") or {})
    api = a.gitlab_api or ag.get("gitlab_api") or forge.GITLAB_API
    proj = a.gitlab_project or ag.get("gitlab_project")
    if not proj:
        code, out, _ = (runner or default_runner)(["git", "remote", "get-url", "origin"])
        proj = forge.project_from_remote(out.strip(), api) if code == 0 else None
        bad = forge.wrong_api_host(api, out.strip()) if proj else None
        if bad:
            raise forge.GitLabError(bad + "; or pass --gitlab-api")
    tok_env = ag.get("gitlab_token_env") or "GITLAB_TOKEN"
    token = os.environ.get(tok_env)
    if not token:
        raise forge.GitLabError(f"no GitLab token: set ${tok_env} (a project or personal access token with api scope)")
    return forge.GitLab(proj, token, api=api)


if __name__ == "__main__":
    sys.exit(main())
