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
from . import userconfig as UC

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
  desLibrary(workspaceUrl: $ws) { components(first: 20, where: {name: {eq: $name}}) { nodes { id name revision { revisionId } details { parameters { name value } } } } }
}"""

Q_BY_ID = """query ($id: ID!) { desComponentById(id: $id) { id name revision { revisionId } details { parameters { name value } } } }"""

M_RELEASE = """mutation ($input: DesReleaseComponentInput!) { desReleaseComponent(input: $input) { errors { message } } }"""

# an existing symbol / footprint, by item name: `name` on these is the revision name ("Capacitor-1"), so the lookup is
# a prefix and the revision is picked here (live schema 2026-10-18)
Q_MODELS = {k: """query ($ws: String!, $prefix: String!) {
  desLibrary(workspaceUrl: $ws) { %s(first: 50, where: {name: {startsWith: $prefix}}) { nodes { id name releasedAt folder { path } } } }
}""" % k for k in ("symbols", "footprints")}

# folders by parent: componentReleaseFolder is one child folder name (a "/" in it fails with AddALU_FoldersInvalid
# folder HRID), so a nested component folder is released as componentParentFolderId + leaf name
# live schema 2026-10: a folder has a node id (base64) and a folderId (GUID); desCreateFolder takes the parent's
# folderId as parentFolderId and returns only the new folderId
Q_FOLDERS = """query ($ws: String!) { desLibrary(workspaceUrl: $ws) { folders { id folderId name parent { id } } } }"""

M_CREATE_FOLDER = """mutation ($input: DesCreateFolderInput!) { desCreateFolder(input: $input) { folderId errors { message } } }"""

M_UPDATE_PARAMS = """mutation ($input: DesUpdateComponentRevisionParametersInput!) {
  desUpdateComponentRevisionParameters(input: $input) { componentId errors { message } }
}"""


def pick_named(nodes, name):
    """The node that is item `name` ("SS34" or its revision "SS34-1"), or None. Live 2026-10-18: the
    `name: {eq: "AMS1117-3.3"}` filter also returns Altium's sample part CMP-017-000002-1 (it matches more than
    the item name), so a hit is only a match when its own name is the item's."""
    import re
    if not name:
        return None
    nodes = nodes or []
    exact = next((n for n in nodes if (n.get("name") or "").lower() == name.lower()), None)
    rev = re.compile(re.escape(name) + r"-(\d+|[A-Z]\.\d+)", re.I)     # a revision ("-1", "-A.1"), not "LM358-N"
    return exact or next((n for n in nodes if rev.fullmatch(n.get("name") or "")), None)


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
        return pick_named(conn.get("nodes"), name)

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

    def find_model(self, ws, kind, item_name, folder):
        """Node id of the newest revision of symbol / footprint item_name in folder ("a/b"), or None.
        kind: "symbols" or "footprints". A same-named item in another folder is an error: item names are unique in
        the workspace, so releasing it again would fail, and linking someone else's model is not ours to decide."""
        import re
        rev = re.compile(re.escape(item_name) + r"-[^-]+$", re.I)          # "Capacitor-1", not "Capacitor-SYM-1"
        conn = self.gql(Q_MODELS[kind], {"ws": ws, "prefix": item_name + "-"})["desLibrary"][kind] or {}
        nodes = [n for n in conn.get("nodes") or [] if rev.match(n.get("name") or "")]
        if not nodes:
            return None
        norm = lambda p: "/".join(x for x in (p or "").replace("\\", "/").split("/") if x).lower()
        here = [n for n in nodes if norm((n.get("folder") or {}).get("path")) == norm(folder)]
        if not here:
            where = sorted({(n.get("folder") or {}).get("path") or "?" for n in nodes})
            raise NexarError(f"{kind[:-1]} {item_name!r} already exists in {', '.join(where)}, not in {folder}: "
                             "rename it in the plan or release under that folder")
        return max(here, key=lambda n: n.get("releasedAt") or "")["id"]

    def _read_folders(self, ws):
        self._folders = [{"id": f["id"], "guid": f.get("folderId"), "name": f["name"], "parent": (f.get("parent") or {}).get("id")}
                         for f in self.gql(Q_FOLDERS, {"ws": ws})["desLibrary"]["folders"]]
        self._folders_ws = ws

    def folder(self, ws, path):
        """The workspace folder at path ("a/b") as {"id", "guid", ...}, creating any missing level as a generic folder.
        The folder list is read once per client, and again after a create (the create returns only the GUID)."""
        if getattr(self, "_folders_ws", None) != ws:
            self._read_folders(ws)
        hit = None
        for name in [p for p in path.split("/") if p]:
            parent = hit["id"] if hit else None
            find = lambda: next((f for f in self._folders if f["parent"] == parent and f["name"].lower() == name.lower()), None)
            nxt = find()
            if not nxt:
                inp = {"workspaceUrl": ws, "name": name, "folderType": "GENERIC_FOLDER", "parentFolderId": hit["guid"] if hit else None}
                res = self.gql(M_CREATE_FOLDER, {"input": inp})["desCreateFolder"]
                if res.get("errors") or not res.get("folderId"):
                    raise NexarError(f"creating folder {name!r}: " + "; ".join(e["message"] for e in res.get("errors") or []))
                self._read_folders(ws)
                nxt = find() or next((f for f in self._folders if f["guid"] == res["folderId"]), None)
                if not nxt:
                    raise NexarError(f"folder {name!r} was created ({res['folderId']}) but is not in the folder list")
            hit = nxt
        return hit

    def folder_id(self, ws, path):
        return self.folder(ws, path)["id"]

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


def _rev_id(node):
    """A component's revision id: Nexar renamed DesRevision.id to revisionId (live schema, 2026-10)."""
    r = (node or {}).get("revision") or {}
    return r.get("revisionId") or r.get("id")


def split_folder(path):
    """("parent/path", "leaf") of a component folder; parent is "" for a top-level folder."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return "/".join(parts[:-1]), parts[-1] if parts else ""


def model_item_names(item):
    """Workspace item names for the part's symbol and footprint. Altium 365 item names share one namespace, so a
    symbol named like its component ("SS34" for SS34) failed live with "Duplicate ID is not allowed": such a name
    gets a -SYM / -FP suffix. The library ref inside the .SchLib/.PcbLib is not changed."""
    comp = (item.get("name") or "").lower()
    sym, fp = item["symbol"]["ref"] or None, item["footprint"]["ref"] or None
    return (f"{sym}-SYM" if sym and sym.lower() == comp else sym), (f"{fp}-FP" if fp and fp.lower() == comp else fp)


def release_input(item, ws, defs, file_ids, models_root="Components/Models", parent_id=None, existing=(None, None)):
    """parent_id: workspace id of the component folder's parent (split_folder); None for a top-level folder.
    existing: (symbol id, footprint id) already in the workspace; such a model is linked, not released again."""
    sym_ids, fp_ids = file_ids
    nid = lambda k: (defs.get(k) or {}).get("id")
    _, leaf = split_folder(item["folder"])
    sym_name, fp_name = model_item_names(item)
    inp = {
        "workspaceUrl": ws,
        **({"componentParentFolderId": parent_id} if parent_id else {}),
        "componentReleaseFolder": leaf, "useExistingComponentReleaseFolder": True,
        "componentItemName": item["name"], "componentComment": item["comment"],
        "componentDescription": item["description"], "parameters": item["parameters"],
        "componentRevisionNamingSchemeNodeId": nid("componentScheme"),
        "componentLifeCycleDefinitionNodeId": nid("componentLifeCycle"),
        "symbolReleaseFolder": f"{models_root}/Symbols", "symbolItemName": sym_name,
        "symbolFiles": [{"fileId": i, "relativePath": f"Released\\{n}"} for n, i in sym_ids],
        "symbolRevisionNamingSchemeNodeId": nid("symbolScheme"),
        "symbolLifeCycleDefinitionNodeId": nid("symbolLifeCycle"),
        "footprintReleaseFolder": f"{models_root}/Footprints", "footprintItemName": fp_name,
        "footprintFiles": [{"fileId": i, "relativePath": f"Released\\{n}"} for n, i in fp_ids],
        "footprintRevisionNamingSchemeNodeId": nid("footprintScheme"),
        "footprintLifeCycleDefinitionNodeId": nid("footprintLifeCycle"),
    }
    sym_id, fp_id = existing
    if sym_id:                                 # a shared Capacitor symbol: released once, then linked
        inp.update(symbol={"id": sym_id}, symbolFiles=[], symbolReleaseFolder=None, symbolItemName=None)
    if fp_id:
        inp.update(footprints=[{"id": fp_id}], footprintFiles=[], footprintReleaseFolder=None, footprintItemName=None)
    return inp


# ------------------------------------------------------------------ push
def record_push(folder, started, results):
    """The push step of the plan's BOM run (timing.jsonl, `ecadlib status`)."""
    from . import timing
    acts = [r.get("action") for r in results]
    timing.record(folder, "push", started, released=acts.count("release"), updated=acts.count("update_parameters"),
                  errors=acts.count("error"))


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
        rev = _rev_id(node)
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
    a["revision"] = _rev_id(after) or a.get("revision")
    a.setdefault("base", {})["parameters"] = {p["name"]: p["value"] for p in new}
    return {"action": "update_parameters", "detail": cid, "a365_updated": True}


def push(items, client, ws, dry_run=False, note="ecadlib sync", policy=None, rules=None):
    """Release new parts, update parameters of parts already in the workspace.
    dry_run: no uploads or mutations; records the requests that would be sent.
    policy: the plan's approval policy (approvals.load_policy); None uses the one release_check stored per item.
    rules: parameter names go to the workspace in the rules' house names ([parameters] names)."""
    from .approvals import push_block
    from .rules import house_item
    from .a365_sync import is_a365
    from .rules import DEFAULTS
    # symbols and footprints go under the same root as the components ([folders] root), so a test root holds them all
    models_root = f"{(rules or DEFAULTS)['folders'].get('root') or 'Components'}/Models"
    # sign-off is checked on the item as it was approved (ecadlib's parameter names: the content hash covers them)
    blocks = [push_block(i, policy) for i in items]
    # a pulled item already carries the workspace's own parameter names: renaming it would rename them there
    items = [i if is_a365(i) else house_item(i, rules) for i in items]
    defs = {} if dry_run else client.release_defs(ws)
    results, uploaded, released = [], {}, set()   # one upload per file per run; (kind, name) released this run
    def upload(p):
        if p not in uploaded:
            uploaded[p] = "<fileId>" if dry_run else client.upload(p)
        return uploaded[p]
    for it, block in zip(items, blocks):
        res = {"name": it["name"], "action": "", "detail": ""}
        if it.get("status") == "held":         # release_check held it back: no release, no parameter update
            res["action"], res["detail"] = "skip", "held: " + "; ".join(it.get("hold_reasons") or ["release check"])
            results.append(res)
            continue
        if block:                              # sign-off policy not met, or a dedupe duplicate
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
                # a symbol / footprint shared by several parts (Capacitor, R_0603) is released with the first and
                # linked by the others: releasing it again fails with AddALU_ItemsDuplicate item HRID
                names = model_item_names(it)
                found = []
                for kind, name, sub in zip(("symbols", "footprints"), names, ("Symbols", "Footprints")):
                    key = (kind, (name or "").lower())
                    if not name:
                        found.append(None)
                    elif dry_run:
                        found.append(f"<id of {name}>" if key in released else None)
                    else:
                        found.append(client.find_model(ws, kind, name, f"{models_root}/{sub}"))
                files = [] if found[0] else [it["symbol"]["file"]], [] if found[1] else [it["footprint"]["file"]]
                ids = tuple([(Path(p).name, upload(p)) for p in group] for group in files)
                parent, _ = split_folder(it["folder"])
                pid = (f"<id of {parent}>" if dry_run else client.folder_id(ws, parent)) if parent else None
                inp = release_input(it, ws, defs, ids, models_root, pid, tuple(found))
                res["action"] = "release"
                if dry_run:
                    released.update((k, (n or "").lower()) for k, n in zip(("symbols", "footprints"), names) if n)
                    res["detail"] = {"mutation": "desReleaseComponent", "input": inp,
                                     **({"parent_folder": parent} if parent else {})}
                else:
                    try:
                        client.release(inp)
                    except NexarError as e:    # the schema does not say which folder id the parent takes: try the GUID
                        guid = (client.folder(ws, parent) or {}).get("guid") if parent and hasattr(client, "folder") else None
                        if not (guid and "folder" in str(e).lower()):
                            raise
                        client.release({**inp, "componentParentFolderId": guid})
                released.update((k, (n or "").lower()) for k, n in zip(("symbols", "footprints"), names) if n)
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
        if Path(a.out).is_dir() or a.out.endswith(("/", "\\")) or not Path(a.out).suffix:   # -o out/a365: the plan file inside that folder
            a.out = str(Path(a.out) / "plan.json")
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

    if a.cmd == "push" and not Path(a.plan).is_file():
        sys.exit(f"{a.plan}: no such plan file (write one with `ecadlib altium365 plan BOM -o {a.plan}`)")
    token = UC.secret("NEXAR_TOKEN")
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
        import time
        t0 = time.time()
        results = push(items, client, a.workspace, dry_run=a.dry_run, policy=load_policy(Path(a.plan).parent),
                       rules=RU.load(a.rules, start=Path(a.plan).parent))
        if not a.dry_run:
            record_push(Path(a.plan).parent, t0, results)
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
