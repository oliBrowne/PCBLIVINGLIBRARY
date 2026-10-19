"""Two-way Altium 365 sync: bring the components already in a workspace into a plan.json, keep them in step, and
let `altium365 push` update them instead of releasing duplicates.

    NEXAR_TOKEN=... ecadlib a365 pull      PLAN --workspace URL [--state F] [--folder F ...] [--no-models]
    NEXAR_TOKEN=... ecadlib a365 status    PLAN --workspace URL [--state F]
    NEXAR_TOKEN=... ecadlib a365 reconcile PLAN --workspace URL [--state F] [--prefer workspace|local]
    (or: python -m ecadlib.a365_sync ...)

pull       lists every workspace component (desLibrary.components, paged) with its folder, comment, description,
           revision, lifecycle, parameters and symbol / footprint names and GUIDs. Components the plan does not have
           are added as items with status `existing` and source {"component": "a365", ...}; their .SchLib/.PcbLib are
           downloaded from the symbol/footprint dataDownloadUrl into <plan dir>/a365_models/ and read back with
           altium_lib's readers (skip with --no-models). Items already pulled are refreshed: workspace edits come in,
           local edits stay. A field changed on both sides is a conflict: pull reports it and changes neither side.
status     the same comparison, read-only: in_sync, new_in_workspace, changed_in_workspace, changed_locally, merged
           (both changed, different fields), conflict, deleted_in_workspace, deleted_locally, new_local, linked.
reconcile  applies it and settles conflicts: the workspace value wins (the local value is kept in
           item.a365.overwritten), or with --prefer local the plan value is kept and `altium365 push` sends it.

Comparison is three-way, per field, against the snapshot taken at the last pull (item.a365.base, falling back to
the --state file): parameters one by one, comment, description, folder, symbol and footprint names, model GUIDs.
Only parameters can be written back through Nexar (desUpdateComponentRevisionParameters); a local change to the
comment, description, folder or models is reported as `not pushable` (change it in Altium).
Plan items that were not pulled but have the same name as a workspace component are `linked`: they get the
workspace snapshot as base, and push updates them (never re-releases them).

--state (default <plan dir>/a365_state.json) is the snapshot of every listed component, in the format
`upstream check-a365 --state` and `dedupe` read ({name: {revision, lifecycle, symbols, footprints, parameters, ...}}).
Report: <plan dir>/a365_sync.md and .csv (-o to change). Exit codes: 0 nothing to do, 1 differences (status) or
unresolved conflicts (pull), 2 error. Nexar needs NEXAR_TOKEN (scopes openid, user.access, design.domain)."""
import argparse, copy, datetime, io, json, os, re, sys, zipfile
from pathlib import Path
from . import userconfig as UC

try:
    from . import rules as RU
except ImportError:                     # rules.py is optional (built separately); defaults below
    RU = None

PAGE = 50
Q_COMPONENTS = """query ($ws: String!, $first: Int!, $after: String) {
  desLibrary(workspaceUrl: $ws) {
    components(first: $first, after: $after) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { ...A365Component }
    }
  }
}
fragment A365Component on DesComponent {
  id name comment description isManaged createdAt modifiedAt
  folder { path }
  revision { revisionId details { lifeCycleState { name } createdAt modifiedAt } }
  details {
    parameters { name value }
    symbols { name guid dataDownloadUrl folder { path } }
    footprints { name guid dataDownloadUrl folder { path } }
  }
}"""

PUSHABLE = ("parameters",)
MISSING = None
MPN_NAMES = ("Manufacturer Part Number", "MPN", "Mfr Part Number")


# ---------------------------------------------------------------- rules (optional)
def _param_name(canonical, rules):
    if not rules:
        return canonical
    if RU is not None and hasattr(RU, "param_name"):
        try:
            return RU.param_name(canonical, rules)
        except Exception:
            pass
    return ((rules.get("parameters") or {}).get("names") or {}).get(canonical, canonical)


def mpn_of(item_or_params, rules=None):
    """MPN of a plan item / component dict / {name: value}; falls back to the name."""
    x = item_or_params
    par = x if isinstance(x, dict) and "parameters" not in x and "name" not in x else (x.get("parameters") or {})
    if isinstance(par, list):
        par = {p["name"]: p["value"] for p in par}
    for k in (_param_name("Manufacturer Part Number", rules),) + MPN_NAMES:
        if str(par.get(k) or "").strip():
            return str(par[k]).strip()
    return (x.get("name") if isinstance(x, dict) and "name" in x else "") or ""


def _category(folder, rules):
    """Ref class / category whose rules folder this is (rules [folders] map), or the last folder name."""
    fm = ((rules or {}).get("folders") or {}).get("map") or {}
    f = _norm(folder).lower()
    hit = next((k for k, v in fm.items() if _norm(v).lower() == f), None)
    return hit or (_norm(folder).split("/")[-1] if folder else "")


# ---------------------------------------------------------------- workspace -> snapshots / components / items
def _norm(p):
    return "/".join(x for x in str(p or "").replace("\\", "/").split("/") if x)


def is_a365(item):
    """True for a plan item that mirrors a workspace component (pulled), not one that was only linked."""
    src = item.get("source")
    return src == "a365" or (isinstance(src, dict) and src.get("component") == "a365")


def fetch_components(client, ws, page=None):
    """Every component of the workspace library, following pageInfo.endCursor."""
    out, after, page = [], None, page or PAGE
    while True:
        lib = client.gql(Q_COMPONENTS, {"ws": ws, "first": page, "after": after})["desLibrary"]
        conn = (lib or {}).get("components") or {}
        out += conn.get("nodes") or []
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage") or not pi.get("endCursor") or pi.get("endCursor") == after:
            return out
        after = pi["endCursor"]


def snapshot(node):
    """What we remember of a workspace component: the base of the next three-way comparison. Its revision,
    lifecycle, symbols and footprints keys are the `upstream check-a365 --state` format."""
    d = node.get("details") or {}
    rev = node.get("revision") or {}
    rd = rev.get("details") or {}
    syms, fps = d.get("symbols") or [], d.get("footprints") or []
    return {"id": node.get("id"), "revision": rev.get("revisionId") or rev.get("id"),
            "lifecycle": (rd.get("lifeCycleState") or {}).get("name"),
            "modified_at": node.get("modifiedAt") or rd.get("modifiedAt"), "managed": node.get("isManaged"),
            "folder": _norm((node.get("folder") or {}).get("path")),
            "comment": node.get("comment") or "", "description": node.get("description") or "",
            "parameters": {p["name"]: p["value"] for p in d.get("parameters") or []},
            "symbol": syms[0]["name"] if syms else "", "footprint": fps[0]["name"] if fps else "",
            "symbols": sorted(x.get("guid") or "" for x in syms),
            "footprints": sorted(x.get("guid") or "" for x in fps)}


def component(node, rules=None):
    """A workspace component as the shared component dict (what rules inference consumes)."""
    s, d = snapshot(node), node.get("details") or {}
    model = lambda xs: ({"ref": xs[0]["name"], "guid": xs[0].get("guid"), "folder": _norm((xs[0].get("folder") or {}).get("path")),
                         "url": xs[0].get("dataDownloadUrl")} if xs else {"ref": "", "guid": None})
    return {"name": node["name"], "folder": s["folder"], "category": _category(s["folder"], rules),
            "description": s["description"], "comment": s["comment"], "parameters": dict(s["parameters"]),
            "symbol": model(d.get("symbols") or []), "footprint": model(d.get("footprints") or []),
            "revision": s["revision"], "guid": s["id"], "lifecycle": s["lifecycle"], "managed": s["managed"],
            "mpn": mpn_of(s["parameters"], rules) or node["name"]}


def components(items, rules=None):
    """Plan items that came from the workspace, as component dicts (for rules inference without a network call)."""
    out = []
    for it in items:
        a = it.get("a365") or {}
        if not a:
            continue
        par = {p["name"]: p["value"] for p in it.get("parameters") or []}
        out.append({"name": it["name"], "folder": it.get("folder") or "", "category": _category(it.get("folder"), rules),
                    "description": it.get("description") or "", "comment": it.get("comment") or "", "parameters": par,
                    "symbol": dict(it.get("symbol") or {}), "footprint": dict(it.get("footprint") or {}),
                    "revision": a.get("revision"), "guid": a.get("id"), "lifecycle": a.get("lifecycle"),
                    "managed": a.get("managed"), "mpn": mpn_of(par, rules) or it["name"], "pulled": is_a365(it)})
    return out


def _meta(snap, ws, now, mirror, old=None):
    m = dict(old or {})
    m.update({k: snap[k] for k in ("id", "revision", "lifecycle", "modified_at", "managed", "symbols", "footprints")})
    m.update({"workspace": ws, "synced_at": now, "mirror": mirror,
              "base": {k: copy.deepcopy(snap[k]) for k in ("parameters", "comment", "description", "folder", "symbol",
                                                            "footprint", "symbols", "footprints")}})
    m.pop("conflicts", None)
    return m


def item_from_node(node, ws, now, rules=None):
    s = snapshot(node)
    return {"name": node["name"], "folder": s["folder"], "description": s["description"], "comment": s["comment"],
            "parameters": [{"name": k, "value": v} for k, v in s["parameters"].items()], "cleared": [],
            "symbol": {"ref": s["symbol"], "file": None, "guid": (s["symbols"] or [None])[0]},
            "footprint": {"ref": s["footprint"], "file": None, "guid": (s["footprints"] or [None])[0]},
            "status": "existing", "category": _category(s["folder"], rules),
            "source": {"component": "a365", "symbol": f"a365:{s['symbol']}" if s["symbol"] else None,
                       "footprint": f"a365:{s['footprint']}" if s["footprint"] else None},
            "a365": _meta(s, ws, now, True)}


# ---------------------------------------------------------------- three-way comparison
def _hname(it, k, rules):
    """The name parameter k of item it has in the workspace: a pulled item already uses the workspace's names, a
    plan item uses ecadlib's (push renames them to the rules' house names)."""
    return k if is_a365(it) else _param_name(k, rules)


def _cname(k, rules):
    """ecadlib's name for workspace parameter k (inverse of the rules' [parameters] names)."""
    inv = {v: c for c, v in (((rules or {}).get("parameters") or {}).get("names") or {}).items()}
    return inv.get(k, k)


def local_fields(it, rules=None):
    """The plan item's side of the comparison, parameter names as push would send them."""
    return {"parameters": {_hname(it, p["name"], rules): p["value"] for p in it.get("parameters") or []},
            "comment": it.get("comment") or "", "description": it.get("description") or "", "folder": _norm(it.get("folder")),
            "symbol": (it.get("symbol") or {}).get("ref") or "", "footprint": (it.get("footprint") or {}).get("ref") or ""}


def _same(field, a, b):
    if field == "folder":
        return _norm(a).lower() == _norm(b).lower()
    return a == b


def three_way(base, ws, local, mirror=True, cleared=()):
    """[(field, key, side, base, workspace, local)] for every field that is not equal on all sides.
    side: 'workspace' (changed only there), 'local' (only in the plan), 'conflict' (both, differently), 'both' (same edit)."""
    out = []
    def one(field, key, b, w, l):
        if _same(field, w, l) and _same(field, b, w):
            return
        side = ("both" if _same(field, w, l) else "workspace" if _same(field, l, b) else
                "local" if _same(field, w, b) else "conflict")
        out.append((field, key, side, b, w, l))
    bp, wp, lp = base.get("parameters") or {}, ws["parameters"], local["parameters"]
    for k in list(dict.fromkeys([*wp, *bp, *lp])):
        l = lp.get(k, MISSING)
        if k not in lp and not mirror and k not in cleared:
            l = wp.get(k, MISSING)                     # a linked BOM item lists only what it cares about
        one("parameters", k, bp.get(k, MISSING), wp.get(k, MISSING), l)
    for f in ("comment", "description", "folder", "symbol", "footprint") if mirror else ():
        if f in base:
            one(f, "", base[f], ws[f], local[f])
    bm = [base.get("symbols"), base.get("footprints")]
    wm = [ws["symbols"], ws["footprints"]]
    if mirror and bm != [None, None] and bm != wm:     # a linked plan item keeps its own model files
        out.append(("models", "", "workspace", bm, wm, bm))
    return out


def _set_param(it, k, v, rules=None):
    """Set workspace parameter k (workspace name) on the item, under the name the item uses for it."""
    ps = it.setdefault("parameters", [])
    cur = next((p for p in ps if _hname(it, p["name"], rules) == k), None)
    if v is MISSING:
        if cur:
            ps.remove(cur)
        if not is_a365(it) and not any(_hname(it, c, rules) == k for c in it.setdefault("cleared", [])):
            it["cleared"].append(_cname(k, rules))     # linked item: make push drop it too
    elif cur:
        cur["value"] = v
    else:
        ps.append({"name": k if is_a365(it) else _cname(k, rules), "value": v})
        it["cleared"] = [c for c in it.get("cleared") or [] if _hname(it, c, rules) != k]


def _take(it, field, key, w, rules=None):
    if field == "parameters":
        _set_param(it, key, w, rules)
    elif field in ("symbol", "footprint"):
        it.setdefault(field, {})["ref"] = w
        it[field]["file"] = None                       # a different model now: fetch again
        if isinstance(it.get("source"), dict) and is_a365(it):
            it["source"][field] = f"a365:{w}" if w else None
    elif field == "models":
        for k in ("symbol", "footprint"):              # new symbol/footprint revision in the workspace: fetch again
            if isinstance(it.get(k), dict):
                it[k]["file"] = None
    else:
        it[field] = w


def _show(v):
    return "(absent)" if v is MISSING else v if isinstance(v, str) else json.dumps(v)


def sync(items, nodes, state=None, prefer=None, apply=False, import_new=True, folders=None, ws="", rules=None, now=None):
    """Compare plan items with workspace nodes. Returns (items, rows, new_state, fetch, {name: status}).
    prefer: None leaves conflicts as they are (pull, status), 'workspace' or 'local' settles them (reconcile).
    apply=False changes nothing (status). fetch: [(item, node)] whose models should be downloaded."""
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    items = copy.deepcopy(items) if apply else items
    state = state or {}
    by_name, by_id = {}, {}
    for n in nodes:
        by_name.setdefault(n["name"], n); by_id.setdefault(n.get("id"), n)
    watch = [_norm(f).lower() for f in folders or []]
    inside = lambda f: not watch or any(_norm(f).lower() == w or _norm(f).lower().startswith(w + "/") for w in watch)
    rows, fetch, seen, keep, new_state, status_of = [], [], set(), [], {}, {}
    def row(name, status, field="", plan="", wsv="", base="", detail=""):
        rows.append({"name": name, "status": status, "field": field, "plan": plan, "workspace": wsv, "base": base, "detail": detail})

    for it in items:
        link = it.get("a365")
        node = by_name.get(it["name"]) or (by_id.get(link.get("id")) if link else None)
        if not node:
            if link and not link.get("mirror", is_a365(it)):
                row(it["name"], "deleted_in_workspace", detail="linked BOM part no longer in the workspace"
                    + ("; link dropped, push releases it again" if apply else ""))
                status_of[it["name"]] = "deleted_in_workspace"
                if apply:
                    it.pop("a365")
            elif link:
                row(it["name"], "deleted_in_workspace", detail="was revision " + str(link.get("revision")))
                status_of[it["name"]] = "deleted_in_workspace"
                if apply and prefer == "workspace":
                    continue                           # reconcile: workspace wins, the item goes (reported above)
                if apply and it.get("status") != "deleted_in_workspace":
                    link["deleted"], link["prev_status"] = now, it.get("status")
                    it["status"] = "deleted_in_workspace"
            else:
                row(it["name"], "new_local", detail=f"plan status {it.get('status', '')}")
                status_of[it["name"]] = "new_local"
            keep.append(it)
            continue
        seen.add(node["name"])
        snap = snapshot(node)
        new_state[node["name"]] = {**snap, "in_plan": True}
        if not link:
            diffs = three_way({}, snap, local_fields(it, rules), mirror=False,
                               cleared=[_hname(it, c, rules) for c in it.get("cleared") or ()])
            row(it["name"], "linked", detail="in the workspace already: push updates it instead of releasing it"
                + (f"; {sum(d[0] == 'parameters' for d in diffs)} parameter(s) differ" if diffs else ""))
            for f, k, side, b, w, l in diffs:
                if f == "parameters" and w != l:
                    row(it["name"], "linked", f"parameter {k}", _show(l), _show(w), "", "push sends the plan value")
            status_of[it["name"]] = "linked"
            if apply:
                it["a365"] = _meta(snap, ws, now, False)
            keep.append(it)
            continue
        base = link.get("base") or {k: v for k, v in (state.get(it["name"]) or {}).items()
                                    if k in ("parameters", "comment", "description", "folder", "symbols", "footprints")}
        if "parameters" not in base:
            base = {**snap, **base}                    # no parameter snapshot: take the workspace as the base
        if node["name"] != it["name"]:
            row(it["name"], "renamed_in_workspace", "name", it["name"], node["name"], detail="found by id"
                + ("; renamed in the plan" if apply and is_a365(it) else ""))
            if apply and is_a365(it):
                it["name"] = node["name"]
        mirror = link.get("mirror", is_a365(it))
        diffs = three_way(base, snap, local_fields(it, rules), mirror, [_hname(it, c, rules) for c in it.get("cleared") or ()])
        sides = {d[2] for d in diffs}
        open_conf = []
        for f, k, side, b, w, l in diffs:
            label = f"parameter {k}" if f == "parameters" else f
            if side == "workspace":
                row(it["name"], "changed_in_workspace", label, _show(l), _show(w), _show(b), "taken from the workspace" if apply else "")
                if apply:
                    _take(it, f, k, w, rules)
            elif side == "local":
                why = "push sends it" if f in PUSHABLE else "not pushable: Nexar has no mutation for it, change it in Altium"
                row(it["name"], "changed_locally", label, _show(l), _show(w), _show(b), why)
            elif side == "conflict":
                if prefer == "workspace":
                    row(it["name"], "conflict", label, _show(l), _show(w), _show(b),
                        "workspace wins" + ("; plan value kept in a365.overwritten" if apply else ""))
                    if apply:
                        link.setdefault("overwritten", []).append({"field": label, "plan": l, "workspace": w, "at": now})
                        _take(it, f, k, w, rules)
                elif prefer == "local":
                    row(it["name"], "conflict", label, _show(l), _show(w), _show(b),
                        "plan wins: push sends it" if f in PUSHABLE else "plan kept; not pushable, change it in Altium")
                else:
                    open_conf.append({"field": label, "plan": l, "workspace": w, "base": b})
                    row(it["name"], "conflict", label, _show(l), _show(w), _show(b),
                        "edited in both; nothing changed: run `a365 reconcile` [--prefer local]")
        eff = sides - {"both"}
        status = ("in_sync" if not eff else "conflict" if "conflict" in eff else "changed_in_workspace" if eff == {"workspace"}
                  else "changed_locally" if eff == {"local"} else "merged")
        status_of[it["name"]] = status
        if status == "in_sync":
            row(it["name"], "in_sync")
        elif status == "merged":
            row(it["name"], "merged", detail="workspace and plan changed different fields: both kept")
        if apply:
            if open_conf:                              # pull: leave base and revision so push refuses until settled
                link["conflicts"] = open_conf
            else:
                it["a365"] = _meta(snap, ws, now, mirror, link)
                if it.get("status") == "deleted_in_workspace":
                    it["status"] = it["a365"].pop("prev_status", None) or ("existing" if is_a365(it) else "ready")
                    it["a365"].pop("deleted", None)
                elif is_a365(it) and it.get("status") != "duplicate":
                    it["status"] = "existing"
                if is_a365(it) and (any(d[0] in ("models", "symbol", "footprint") and d[2] in ("workspace", "conflict") for d in diffs)
                                    or not (it.get("symbol") or {}).get("file") or not (it.get("footprint") or {}).get("file")):
                    fetch.append((it, node))
        keep.append(it)

    planned = {it["name"] for it in keep}
    for n in nodes:
        if n["name"] in seen or n["name"] in planned:
            continue
        snap = snapshot(n)
        was = (state.get(n["name"]) or {}).get("in_plan")
        new_state[n["name"]] = {**snap, "in_plan": False}
        if not inside(snap["folder"]):
            continue
        status_of[n["name"]] = "deleted_locally" if was else "new_in_workspace"
        if was:
            row(n["name"], "deleted_locally", "folder", "", snap["folder"], detail="removed from the plan; still in the workspace"
                + ("; imported again (workspace wins)" if apply and prefer == "workspace" else ""))
            if not (apply and prefer == "workspace"):
                continue
        else:
            row(n["name"], "new_in_workspace", "folder", "", snap["folder"], detail="imported" if apply and import_new else "")
        if apply and (import_new or was):
            it = item_from_node(n, ws, now, rules)
            keep.append(it); fetch.append((it, n))
            new_state[n["name"]]["in_plan"] = True
    return (keep if apply else items), rows, new_state, fetch, status_of


# ---------------------------------------------------------------- model files
def _unpack(data, ext):
    """Raw download -> the library file's bytes (an OLE .SchLib/.PcbLib, or the first one inside a zip)."""
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return data
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(ext.lower())), None)
            if name:
                return z.read(name)
            raise ValueError(f"zip without a {ext} ({', '.join(z.namelist()[:5])})")
    raise ValueError(f"not an Altium library file (starts {data[:8]!r})")


def fetch_models(item, node, base, client, log=print):
    """Download the component's first symbol and footprint into <base>/a365_models/{SchLib,PcbLib}/ and read them
    back with altium_lib. Sets item.symbol/footprint.file (relative to base); errors go to item.a365.model_errors."""
    from . import altium_lib as AL
    d = node.get("details") or {}
    errs = []
    for kind, key, ext, reader in (("symbol", "symbols", ".SchLib", lambda p: [s["name"] for s in AL.read_schlib(p)[1]]),
                                   ("footprint", "footprints", ".PcbLib", lambda p: [f["name"] for f in AL.read_pcblib(p)])):
        ms = d.get(key) or []
        if not ms:
            continue
        m = ms[0]
        have = (item.get(kind) or {}).get("file")
        if have and (Path(base) / have).exists():
            continue
        if not m.get("dataDownloadUrl"):
            errs.append(f"{kind} {m['name']}: no dataDownloadUrl"); continue
        try:                                  # a name cp1252 can't hold (Altium 365 allows it) still gets a file name
            stem = AL.file_stem(m["name"])
        except ValueError:
            stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", m["name"]).rstrip(". ") or "_"
        f = Path(base) / "a365_models" / ext[1:] / f"{stem}{ext}"
        try:
            data = _unpack(client.download(m["dataDownloadUrl"]), ext)
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f.suffix + ".part")
            tmp.write_bytes(data)
            names = reader(tmp)
            if m["name"] not in names and len(names) != 1:
                raise ValueError(f"{ext} holds {', '.join(names[:5]) or 'nothing'}, not {m['name']}")
            os.replace(tmp, f)
            item[kind]["file"] = os.path.relpath(f, Path(base)).replace(os.sep, "/")
            item[kind]["entries"] = names
        except Exception as e:
            errs.append(f"{kind} {m['name']}: {type(e).__name__}: {e}")
            with_tmp = f.with_suffix(f.suffix + ".part")
            if with_tmp.exists():
                with_tmp.unlink()
    item.setdefault("a365", {})["model_errors"] = errs
    if not errs:
        item["a365"].pop("model_errors")
    for e in errs:
        log(f"  models  {item['name']}: {e}")
    return errs


# ---------------------------------------------------------------- run / write-back / CLI
def load_plan(path):
    p = Path(path)
    data = json.loads(p.read_text()) if p.exists() else []
    if not isinstance(data, list):
        raise ValueError(f"{p}: expected a list of plan items")
    return data


def default_state(plan):
    return Path(plan).parent / "a365_state.json"


COLS = ["name", "status", "field", "plan", "workspace", "base", "detail"]


def run(mode, plan, client, ws, state=None, prefer="workspace", folders=None, models=True, rules=None, out=None,
        nodes=None, log=print):
    """mode: pull | status | reconcile. Returns {rows, summary, items, components, report, plan, state}."""
    from .upstream import write_report
    if mode not in ("pull", "status", "reconcile"):
        raise ValueError(mode)
    plan = Path(plan)
    state_f = Path(state) if state else default_state(plan)
    items = load_plan(plan)
    st = json.loads(state_f.read_text()) if state_f.exists() else {}
    nodes = fetch_components(client, ws) if nodes is None else nodes
    apply = mode != "status"
    new, rows, new_state, fetch, status_of = sync(items, nodes, st, prefer=prefer if mode == "reconcile" else None, apply=apply,
                                       folders=folders, ws=ws, rules=rules)
    if apply:
        from . import change_gate as CG        # change control (#105): workspace revisions are recorded, not held
        gate = CG.Guard(plan.parent, "a365-pull", rules=rules, log=log)
        gate.begin(items)
        if models:
            for it, n in fetch:
                fetch_models(it, n, plan.parent, client, log)
        gate.settle(new)
        gate.baseline(new)
        plan.parent.mkdir(parents=True, exist_ok=True)
        if plan.exists() and new != items:
            bak = plan.with_name(plan.name + ".pre-a365")
            bak.write_text(plan.read_text())
        plan.write_text(json.dumps(new, indent=2))
        state_f.parent.mkdir(parents=True, exist_ok=True)
        state_f.write_text(json.dumps(new_state, indent=1))
    summary = {}
    for v in status_of.values():
        summary[v] = summary.get(v, 0) + 1
    stem = Path(out) if out else plan.parent / "a365_sync"
    meta = [f"`{mode}` of plan `{plan}` against `{ws}`, {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M} UTC.", "",
            "Components: " + (", ".join(f"{v} {k}" for k, v in sorted(summary.items())) or "none")]
    shown = [r for r in rows if r["status"] != "in_sync"]
    write_report(shown, stem, "Altium 365 sync", COLS, meta)
    return {"rows": rows, "summary": summary, "items": new if apply else items,
            "components": [component(n, rules) for n in nodes], "report": stem.with_suffix(".md"),
            "plan": plan, "state": state_f, "conflicts": sum(r["status"] == "conflict" for r in rows),
            "unresolved": sum(r["status"] == "conflict" and "nothing changed" in r["detail"] for r in rows)}


def save_meta(plan, items):
    """After a push: write each item's updated `a365` block (new revision / id / base) back into plan.json,
    leaving everything else in the file as it is."""
    plan = Path(plan)
    data = load_plan(plan)
    by = {i["name"]: i for i in items if i.get("a365")}
    changed = False
    for d in data:
        it = by.get(d["name"])
        if it and d.get("a365") != it["a365"]:
            d["a365"] = it["a365"]; changed = True
    if changed:
        plan.write_text(json.dumps(data, indent=2))
    return changed


def reuse_existing(items, old_items, rules=None):
    """Librarian hook: a BOM part that is already a workspace component (same name or MPN as a pulled item in the
    previous plan) is replaced by that item, so nothing is fetched for it. Pulled items the BOM does not use are
    kept at the end of the plan; a BOM part `a365 pull` linked keeps its link. Returns (items, {name of reused item})."""
    from .kicad_index import norm_mpn
    linked = {i["name"]: i["a365"] for i in old_items if i.get("a365") and not is_a365(i)}
    if linked:                                 # keep the link (and its snapshot): push then refuses to overwrite
        items = [dict(it, a365=copy.deepcopy(linked[it["name"]]))  # workspace edits made since the last pull
                 if it["name"] in linked and not it.get("a365") else it for it in items]
    pulled = [i for i in old_items if is_a365(i)]
    if not pulled:
        return items, set()
    by_name = {i["name"]: i for i in pulled}
    by_mpn = {}
    for i in pulled:
        by_mpn.setdefault(norm_mpn(mpn_of(i, rules)), i)
    out, used = [], set()
    for it in items:
        hit = by_name.get(it["name"]) or by_mpn.get(norm_mpn(mpn_of(it, rules)) or "\0")
        if hit and hit["name"] not in used:
            got = copy.deepcopy(hit)
            got["librarian"] = {"tried": [], "review": [], "notes": [f"in the Altium 365 workspace: reused for BOM part {it['name']}, not fetched"]}
            out.append(got); used.add(hit["name"])
        elif hit:
            continue                                   # second BOM part number for the same workspace component
        else:
            out.append(it)
    for i in pulled:
        if i["name"] not in used:
            got = copy.deepcopy(i)
            got.setdefault("librarian", {"tried": [], "review": [], "notes": ["in the Altium 365 workspace; not in this BOM"]})
            out.append(got)
    return out, used | {i["name"] for i in pulled}


def _load_rules(path, start="."):
    if RU is not None and hasattr(RU, "load"):
        try:
            return RU.load(path, start=start)          # found from the plan's folder, as `altium365 push` does
        except Exception as e:
            print(f"a365: rules not loaded ({type(e).__name__}: {e}); using defaults", file=sys.stderr)
    elif path:
        return json.loads(Path(path).read_text())
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib a365", description="Two-way sync between a plan.json and an Altium 365 workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, h in (("pull", "import workspace components into the plan; conflicts are reported, not settled"),
                   ("status", "compare plan and workspace; changes nothing"),
                   ("reconcile", "apply workspace changes and settle conflicts (--prefer)")):
        p = sub.add_parser(cmd, help=h)
        p.add_argument("plan")
        p.add_argument("--workspace", help="workspace URL (default: rules [altium365] workspace)")
        p.add_argument("--state", help="snapshot file (default <plan dir>/a365_state.json)")
        p.add_argument("--folder", action="append", help="import only components under this workspace folder (repeatable)")
        p.add_argument("--rules", help="ecadlib.toml / rules JSON")
        p.add_argument("-o", "--out", help="report stem (default <plan dir>/a365_sync)")
        if cmd != "status":
            p.add_argument("--no-models", action="store_true", help="do not download .SchLib/.PcbLib files")
        if cmd == "reconcile":
            p.add_argument("--prefer", choices=("workspace", "local"), default="workspace",
                           help="who wins a conflict (default: workspace)")
    a = ap.parse_args(argv)
    rules = _load_rules(a.rules, Path(a.plan).parent)
    ws = a.workspace or ((rules or {}).get("altium365") or {}).get("workspace")
    if not ws:
        print("a365: give --workspace URL (see `ecadlib altium365 workspaces`)", file=sys.stderr); return 2
    token = UC.secret("NEXAR_TOKEN")
    if not token:
        print("a365: set NEXAR_TOKEN (design.domain, user.access, openid)", file=sys.stderr); return 2
    from . import altium365 as A
    try:
        r = run(a.cmd, a.plan, A.Nexar(token), ws, a.state, getattr(a, "prefer", "workspace"), a.folder,
                not getattr(a, "no_models", False), rules, a.out)
    except Exception as e:
        hint = A.token_hint(token) if "401" in str(e) else ""
        print(f"a365 {a.cmd}: {type(e).__name__}: {e}{hint}", file=sys.stderr); return 2
    for x in r["rows"]:
        if x["status"] != "in_sync":
            print(f"  {x['status']:<21} {x['name']:<28} {x['field']:<26} plan={x['plan']!r} workspace={x['workspace']!r}"
                  + (f"  ({x['detail']})" if x["detail"] else ""))
    print(", ".join(f"{v} {k}" for k, v in sorted(r["summary"].items())) or "nothing in the workspace or the plan",
          f"-> {r['report']}" + (f"; plan {r['plan']}, state {r['state']}" if a.cmd != "status" else ""))
    if a.cmd == "status":
        return 1 if any(x["status"] != "in_sync" for x in r["rows"]) else 0
    return 1 if r["unresolved"] else 0


if __name__ == "__main__":
    sys.exit(main())
