"""Altium 365 workspace exporter, through the Nexar Design API.

Turns a BOM (plus the triage report) into a *release plan*: one managed workspace component
per part, with its parameters and the symbol (.SchLib) and footprint (.PcbLib) files to upload.
Then `push` releases new components and keeps parameters of existing ones up to date.

    python -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models out/altium_models --models out/altium_models_kicad -o out/altium365/plan.json
    python -m ecadlib.altium365 workspaces                                   # needs NEXAR_TOKEN
    python -m ecadlib.altium365 push out/altium365/plan.json --workspace URL [--dry-run]

Flow per part (mirrors NexarDeveloper/nexar-release-component-demo-wpf, schema as of 2024-07):
  1. desLibrary.components(where: {name: {eq}})   exists? -> desUpdateComponentRevisionParameters
  2. POST {files}/File/Upload (header `token`)    -> plain-text fileId, valid 24 h
  3. desReleaseComponent(input)                   symbol + footprint + component in one call
Release needs real Altium binaries: one symbol per .SchLib, one footprint per .PcbLib, under
--models/SchLib/<Library Ref>.SchLib and --models/PcbLib/<Footprint Ref>.PcbLib. Parts without
them are planned as `needs_altium_models` (altium_lib writes ours; kicad_import converts KiCad library parts).

Auth: NEXAR_TOKEN = a user access token with scopes openid, user.access, design.domain
(the token generated in the Nexar portal has them). Supply-only client-credential tokens
cannot see workspaces."""
import argparse, json, os, sys
from pathlib import Path

import pandas as pd

from .altium_export import RESERVED, to_rows
from .altium_lib import file_stem
from .bom_triage import read_bom

API_URL = os.environ.get("NEXAR_API_URL", "https://api.nexar.com/graphql")
FILES_URL = os.environ.get("NEXAR_FILES_URL", "https://files.nexar.com")

Q_WORKSPACES = "query { desWorkspaceInfos { name url } }"

# content-type specific defaults, as the demo's ReleaseDefinitions query
Q_DEFS = """query ($ws: String!) {
  componentScheme: desRevisionNamingSchemeByContentTypeKind(workspaceUrl: $ws, kind: COMPONENT) { id name }
  symbolScheme: desRevisionNamingSchemeByContentTypeKind(workspaceUrl: $ws, kind: SYMBOL) { id name }
  footprintScheme: desRevisionNamingSchemeByContentTypeKind(workspaceUrl: $ws, kind: PCB_COMPONENT) { id name }
  componentLifeCycle: desLifeCycleDefinitionByContentTypeKind(workspaceUrl: $ws, kind: COMPONENT) { id name }
  symbolLifeCycle: desLifeCycleDefinitionByContentTypeKind(workspaceUrl: $ws, kind: SYMBOL) { id name }
  footprintLifeCycle: desLifeCycleDefinitionByContentTypeKind(workspaceUrl: $ws, kind: PCB_COMPONENT) { id name }
}"""

Q_FIND = """query ($ws: String!, $name: String!) {
  desLibrary(workspaceUrl: $ws) { components(first: 1, where: {name: {eq: $name}}) { nodes { id name revision { id } details { parameters { name value } } } } }
}"""

Q_BY_ID = """query ($id: ID!) { desComponentById(id: $id) { id name revision { id } details { parameters { name value } } } }"""

M_RELEASE = """mutation ($input: DesReleaseComponentInput!) { desReleaseComponent(input: $input) { errors { message } } }"""

M_UPDATE_PARAMS = """mutation ($input: DesUpdateComponentRevisionParametersInput!) {
  desUpdateComponentRevisionParameters(input: $input) { componentId errors { message } }
}"""


class NexarError(Exception):
    pass


class Nexar:
    def __init__(self, token, session=None):
        import requests
        self.token, self.s = token, session or requests.Session()

    def gql(self, query, variables=None):
        r = self.s.post(API_URL, json={"query": query, "variables": variables or {}},
                        headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise NexarError("; ".join(e.get("message", "?") for e in body["errors"]))
        return body["data"]

    def upload(self, path):
        with open(path, "rb") as f:
            r = self.s.post(FILES_URL.rstrip("/") + "/File/Upload", files={"file": (Path(path).name, f)},
                            headers={"token": self.token}, timeout=120)
        r.raise_for_status()
        return r.text.strip()              # opaque id; do not parse

    def workspaces(self):
        return self.gql(Q_WORKSPACES)["desWorkspaceInfos"]

    def release_defs(self, ws):
        return self.gql(Q_DEFS, {"ws": ws})

    def find(self, ws, name):
        conn = self.gql(Q_FIND, {"ws": ws, "name": name})["desLibrary"]["components"] or {}
        nodes = conn.get("nodes") or []
        return nodes[0] if nodes else None

    def component(self, cid):
        return self.gql(Q_BY_ID, {"id": cid})["desComponentById"]

    def download(self, url):
        """A symbol/footprint dataDownloadUrl. Tried without credentials first (pre-signed URLs reject extra auth),
        then with the bearer token."""
        r = self.s.get(url, timeout=120)
        if r.status_code in (401, 403):
            r = self.s.get(url, headers={"Authorization": f"Bearer {self.token}"}, timeout=120)
        r.raise_for_status()
        return r.content

    def find_component(self, ws, name):
        n = self.find(ws, name)
        return n["id"] if n else None

    def release(self, inp):
        errs = self.gql(M_RELEASE, {"input": inp})["desReleaseComponent"]["errors"]
        if errs:
            raise NexarError("; ".join(e["message"] for e in errs))

    def update_params(self, component_id, params, note, replace=False):
        inp = {"componentId": component_id, "releaseNote": note, "replaceExisting": replace, "parameters": params}
        res = self.gql(M_UPDATE_PARAMS, {"input": inp})["desUpdateComponentRevisionParameters"]
        if res.get("errors"):                  # payload errors come back with HTTP 200 and no top-level `errors`
            raise NexarError("; ".join(e["message"] for e in res["errors"]))
        return res["componentId"]


# ------------------------------------------------------------------ plan
def find_model(models, kind, ref):
    """First <dir>/<kind>/<ref>.<kind> over the --models dirs, in the order given."""
    return next((str(f) for f in (Path(m) / kind / f"{file_stem(ref)}.{kind}" for m in models) if f.exists()), None)


def plan(lines, triage=None, models=None, root=None, rules=None):
    """One release item per unique Part Number, reusing the DbLib row builder for parameters.
    models: a folder or a list of folders, each with SchLib/<Library Ref>.SchLib and PcbLib/<Footprint Ref>.PcbLib;
    symbol and footprint are looked up independently, first folder wins. rules (ecadlib.rules, loaded): part and
    footprint names, folders and default parameters; None keeps ecadlib's own. root overrides the rules' folder root."""
    from . import rules as RU
    models = [models] if isinstance(models, (str, Path)) else list(models or [])
    items, names = [], set()
    defaults = RU.default_params(rules)
    for cat, df in to_rows(lines, triage, rules).items():
        for row in df.to_dict("records"):
            if row["Part Number"] in names:    # same MPN under two designator classes: one workspace item
                continue
            names.add(row["Part Number"])
            sym = fp = None
            if models:
                sym, fp = find_model(models, "SchLib", row["Library Ref"]), find_model(models, "PcbLib", row["Footprint Ref"])
            managed = [k for k in row if k not in RESERVED and k not in ("Part Number", "Comment")]
            params = [{"name": k, "value": str(row[k])} for k in managed if str(row[k])]
            have = {p["name"] for p in params}
            params += [{"name": k, "value": v} for k, v in defaults if k not in have and v]
            items.append({
                "name": row["Part Number"],
                "folder": RU.folder_for(cat, rules, root) if rules is not None else f"{root or 'Components'}/{cat}",
                "description": row["Description"], "comment": row["Comment"], "parameters": params,
                "cleared": [k for k in managed if not str(row[k])],
                "symbol": {"ref": row["Library Ref"], "file": sym},
                "footprint": {"ref": row["Footprint Ref"], "file": fp},
                "status": "ready" if sym and fp else "needs_altium_models",
            })
            kfp = df.attrs.get("kicad_fp", {}).get(row["Part Number"])
            if kfp:                            # renamed by the rules: keep the library name for lookups and checks
                items[-1]["footprint"]["kicad"] = kfp
    return items


def release_input(item, ws, defs, file_ids, models_root="Components/Models"):
    sym_ids, fp_ids = file_ids
    nid = lambda k: (defs.get(k) or {}).get("id")
    return {
        "workspaceUrl": ws,
        "componentReleaseFolder": item["folder"], "useExistingComponentReleaseFolder": True,
        "componentItemName": item["name"], "componentComment": item["comment"],
        "componentDescription": item["description"], "parameters": item["parameters"],
        "componentRevisionNamingSchemeNodeId": nid("componentScheme"),
        "componentLifeCycleDefinitionNodeId": nid("componentLifeCycle"),
        "symbolReleaseFolder": f"{models_root}/Symbols", "symbolItemName": item["symbol"]["ref"] or None,
        "symbolFiles": [{"fileId": i, "relativePath": f"Released\\{n}"} for n, i in sym_ids],
        "symbolRevisionNamingSchemeNodeId": nid("symbolScheme"),
        "symbolLifeCycleDefinitionNodeId": nid("symbolLifeCycle"),
        "footprintReleaseFolder": f"{models_root}/Footprints", "footprintItemName": item["footprint"]["ref"] or None,
        "footprintFiles": [{"fileId": i, "relativePath": f"Released\\{n}"} for n, i in fp_ids],
        "footprintRevisionNamingSchemeNodeId": nid("footprintScheme"),
        "footprintLifeCycleDefinitionNodeId": nid("footprintLifeCycle"),
    }


# ------------------------------------------------------------------ push
def merged_params(existing, item):
    """Parameter list to send with replaceExisting=True, or None when the workspace already matches.
    Starts from the current revision's parameters so ones added by hand in Altium 365 survive; plan
    values win; fields the plan marks cleared are dropped."""
    cur = {p["name"]: p["value"] for p in ((existing.get("details") or {}).get("parameters") or [])}
    want = dict(cur)
    for k in item.get("cleared", []):
        want.pop(k, None)
    want.update({p["name"]: p["value"] for p in item["parameters"]})
    return None if want == cur else [{"name": k, "value": v} for k, v in want.items()]


def push_existing(it, client, ws, dry_run=False, note="ecadlib sync"):
    """Parameter update for an item that `ecadlib a365 pull` imported or linked (item.a365 = revision, id and the
    snapshot of the last pull). Refuses when the component was edited in the workspace since that snapshot, so a
    person's edit there is never overwritten: run `ecadlib a365 reconcile` first. On success the item's a365 block is
    moved to the new revision (callers write it back with a365_sync.save_meta)."""
    a = it["a365"]
    base = (a.get("base") or {}).get("parameters")
    local = {p["name"] for p in it["parameters"]}
    cleared = list(it.get("cleared") or [])
    if a.get("mirror") and base is not None:   # a pulled item lists every parameter: one removed locally is cleared
        cleared += [k for k in base if k not in local and k not in cleared]
    want = {**it, "cleared": cleared}
    if a.get("conflicts"):
        return {"action": "skip", "detail": "conflict with workspace edits: run `ecadlib a365 reconcile` first"}
    if dry_run:
        node = {"details": {"parameters": [{"name": k, "value": v} for k, v in (base or {}).items()]}}
    else:
        node = client.find(ws, it["name"])
        if not node and a.get("id") and hasattr(client, "component"):
            node = client.component(a["id"])
        if not node:
            return {"action": "skip", "detail": "not in the workspace (deleted or renamed there): run `ecadlib a365 reconcile`"}
        rev = (node.get("revision") or {}).get("id")
        cur = {p["name"]: p["value"] for p in ((node.get("details") or {}).get("parameters") or [])}
        if rev and a.get("revision") and rev != a["revision"] and base is not None and cur != base:
            return {"action": "skip", "detail": f"edited in the workspace since the last pull (revision {a['revision']} -> {rev}): "
                                                "run `ecadlib a365 reconcile` first; nothing sent"}
    new = merged_params(node, want)
    if new is None:
        return {"action": "up_to_date", "detail": ""}
    if dry_run:
        return {"action": "update_parameters", "detail": {"mutation": "desUpdateComponentRevisionParameters",
                                                          "replaceExisting": True, "parameters": new}}
    cid = client.update_params(node["id"], new, note, replace=True)
    after = client.find(ws, it["name"]) or (client.component(cid) if cid and hasattr(client, "component") else None) or {}
    a["id"] = after.get("id") or cid
    a["revision"] = (after.get("revision") or {}).get("id") or a.get("revision")
    a.setdefault("base", {})["parameters"] = {p["name"]: p["value"] for p in new}
    return {"action": "update_parameters", "detail": cid, "a365_updated": True}


def push(items, client, ws, dry_run=False, note="ecadlib sync", policy=None, rules=None):
    """Release new parts, update parameters of parts already in the workspace.
    dry_run: no uploads or mutations; records the requests that would be sent.
    policy: the plan's approval policy (approvals.load_policy); None uses the one release_check stored per item.
    rules: parameter names go to the workspace in the rules' house names ([parameters] names)."""
    from .approvals import push_block
    from .rules import house_item
    items = [house_item(i, rules) for i in items]
    defs = {} if dry_run else client.release_defs(ws)
    results, uploaded = [], {}                  # one upload per file per run (shared Resistor.SchLib etc.)
    def upload(p):
        if p not in uploaded:
            uploaded[p] = "<fileId>" if dry_run else client.upload(p)
        return uploaded[p]
    for it in items:
        res = {"name": it["name"], "action": "", "detail": ""}
        if it.get("status") == "held":         # release_check held it back: no release, no parameter update
            res["action"], res["detail"] = "skip", "held: " + "; ".join(it.get("hold_reasons") or ["release check"])
            results.append(res)
            continue
        block = push_block(it, policy)         # sign-off policy not met, or a dedupe duplicate
        if block:
            res["action"], res["detail"] = "skip", block
            results.append(res)
            continue
        if it.get("a365"):                     # pulled from / linked to the workspace: update it, never release it again
            try:
                res.update(push_existing(it, client, ws, dry_run, note))
            except Exception as e:
                res["action"], res["detail"] = "error", f"{type(e).__name__}: {e}"
            results.append(res)
            continue
        try:
            existing = None if dry_run else client.find(ws, it["name"])
            if existing:
                new = merged_params(existing, it)
                if new is None:
                    res["action"] = "up_to_date"
                else:                          # full replace: a merge, so parameters added in Altium 365 are kept
                    res["action"] = "update_parameters"
                    res["detail"] = client.update_params(existing["id"], new, note, replace=True)
            elif it["status"] != "ready":
                res["action"], res["detail"] = "skip", "needs_altium_models (no .SchLib/.PcbLib)"
            else:
                files = [it["symbol"]["file"]], [it["footprint"]["file"]]
                ids = tuple([(Path(p).name, upload(p)) for p in group] for group in files)
                inp = release_input(it, ws, defs, ids)
                res["action"] = "release"
                if dry_run:
                    res["detail"] = {"mutation": "desReleaseComponent", "input": inp}
                else:
                    client.release(inp)
        except Exception as e:                 # keep going; report per part
            res["action"], res["detail"] = "error", f"{type(e).__name__}: {e}"
        sim = (it.get("sim_model") or {}).get("attached", {}).get("altium")
        if sim and res["action"] in ("release", "update_parameters", "up_to_date"):
            res["sim_model"] = (f"{sim['type']} link {sim['model_name']} travels inside the .SchLib; the model file "
                                f"{sim.get('file') or '(none: built-in)'} is not uploaded (Nexar has no simulation-model input)")
        results.append(res)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Release BOM parts into an Altium 365 workspace via Nexar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="build a release plan from a BOM")
    p.add_argument("bom")
    p.add_argument("--triage", help="triage CSV from bom_triage")
    p.add_argument("--models", action="append", default=[],
                   help="folder with SchLib/<ref>.SchLib and PcbLib/<ref>.PcbLib; repeat to search several, first wins")
    p.add_argument("--root", help="workspace folder to release components under (default: the rules' [folders] root, Components)")
    p.add_argument("-o", "--out", default="out/altium365/plan.json")
    p.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the output folder or here)")
    sub.add_parser("workspaces", help="list workspaces visible to NEXAR_TOKEN")
    u = sub.add_parser("push", help="release / update parts from a plan")
    u.add_argument("plan")
    u.add_argument("--workspace", required=True, help="workspace URL from `workspaces`")
    u.add_argument("--dry-run", action="store_true", help="no network; write the requests that would be sent")
    u.add_argument("-o", "--out", default="out/altium365/push_report.json")
    u.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan's folder or here)")
    a = ap.parse_args(argv)
    from . import rules as RU

    if a.cmd == "plan":
        rules = RU.load(a.rules, start=Path(a.out).parent)
        lines, _ = read_bom(a.bom, RU.bom_columns(rules))
        tri = pd.read_csv(a.triage, dtype=str).fillna("") if a.triage else None
        items = plan(lines, tri, a.models, a.root, rules)
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
        for i in items:                        # model paths relative to the plan file, so push works from any cwd
            for k in ("symbol", "footprint"):
                if i[k]["file"]:
                    try:
                        i[k]["file"] = os.path.relpath(i[k]["file"], out.parent.resolve()).replace(os.sep, "/")
                    except ValueError:         # another drive on Windows: keep it absolute
                        i[k]["file"] = Path(i[k]["file"]).resolve().as_posix()
        out.write_text(json.dumps(items, indent=2))
        ready = sum(i["status"] == "ready" for i in items)
        print(f"{len(items)} components planned, {ready} ready, {len(items) - ready} need Altium models -> {out}")
        for i in items:
            print(f"  {i['status']:<20} {i['folder']:<24} {i['name']:<28} sym={i['symbol']['ref']} fp={i['footprint']['ref']}")
        return

    token = os.environ.get("NEXAR_TOKEN")
    if not token and not (a.cmd == "push" and a.dry_run):
        sys.exit("set NEXAR_TOKEN (Nexar portal token with design.domain, user.access, openid scopes)")
    client = Nexar(token) if token else None
    try:
        if a.cmd == "workspaces":
            for w in client.workspaces():
                print(f"{w['name']:<30} {w['url']}")
            return
        items = json.loads(Path(a.plan).read_text())
        for i in items:
            for k in ("symbol", "footprint"):
                if i[k]["file"]:
                    i[k]["file"] = str((Path(a.plan).parent / i[k]["file"]).resolve())
        from .approvals import load_policy
        results = push(items, client, a.workspace, dry_run=a.dry_run, policy=load_policy(Path(a.plan).parent),
                       rules=RU.load(a.rules, start=Path(a.plan).parent))
    except Exception as e:                     # auth / network / GraphQL errors before any per-part work
        hint = " (token rejected: check it has design.domain, user.access, openid)" if "401" in str(e) else ""
        sys.exit(f"Nexar request failed: {type(e).__name__}: {e}{hint}")
    if any(r.get("a365_updated") for r in results):
        from .a365_sync import save_meta            # new revisions, so the next push is not mistaken for a conflict
        save_meta(a.plan, items)
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"  {r['action']:<18} {r['name']}")
    print(f"report -> {out}")
    failed = sum(r["action"] == "error" for r in results)
    if failed:
        sys.exit(f"{failed} of {len(results)} parts failed (see {out})")


if __name__ == "__main__":
    main()
