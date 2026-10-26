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
A plan's `ready` only says the files are there: push runs the release check (ecadlib.release_check) on each part
before it uploads anything, and a part that fails it is skipped as held (`ecadlib release-check PLAN` shows why).

Auth: NEXAR_TOKEN = a user access token with scopes openid, user.access, design.domain
(the token generated in the Nexar portal has them). Supply-only client-credential tokens
cannot see workspaces."""
import argparse, json, os, sys
from pathlib import Path

import pandas as pd

from .altium_export import RESERVED, to_rows
from .altium_lib import file_stem
from .altium_dblib_sync import readback_problems     # the read-back dblib-sync does, before any file goes up
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

# the component life cycle's transitions, for [change] a365_transition (a new revision moved to a state after release)
Q_TRANSITIONS = """query ($id: ID!) { desLifeCycleDefinitionById(id: $id) { stateTransitions { lifeCycleStateTransitionId name
  lifeCycleStateAfter { name } } } }"""

M_LIFECYCLE = """mutation ($input: DesUpdateComponentLifeCycleStateInput!) {
  desUpdateComponentLifeCycleState(input: $input) { errors { message } }
}"""

M_UPDATE_PARAMS = """mutation ($input: DesUpdateComponentRevisionParametersInput!) {
  desUpdateComponentRevisionParameters(input: $input) { componentId errors { message } }
}"""


REVISION = r"-([1-9]\d*|[A-Z]\.(0|[1-9]\d*))"     # the revision labels Altium's naming schemes give: 1, 2, 12 / A.1


def node_mpn(node, rules=None):
    """The MPN a workspace component node carries ("" when none): under ecadlib's names, the rules' house name
    ([parameters.names]) or a usual MPN parameter name (MfrPN, Mfr. Part #, Manufacturer Part No)."""
    import re
    from .rules import param_name
    par = {str(p.get("name") or "").strip().lower(): str(p.get("value") or "").strip()
           for p in ((node.get("details") or {}).get("parameters") or []) if isinstance(p, dict)}
    for k in ((param_name("Manufacturer Part Number", rules),) if rules else ()) + MPN_NAMES:
        if par.get(k.lower()):
            return par[k.lower()]
    return next((v for k, v in par.items() if v and re.fullmatch(
        r"(mfr|mfg|manufacturer|manuf)\.?[ _]*(part)?[ _]*(no\.?|number|num|#|pn|p/n)|mpn\d*", k)), "")


def pick_named(nodes, name, rules=None):
    """The node that is item `name` ("SS34" or its revision "SS34-1"), or None. Live 2026-10-18: the
    `name: {eq: "AMS1117-3.3"}` filter also returns Altium's sample part CMP-017-000002-1 (it matches more than
    the item name), so a hit is only a match when its own name is the item's. A revision is only a label Altium's
    naming schemes give ("-1", "-12", "-A.1"): "BAT54-04" is no revision. A node whose MPN (under ecadlib's, the
    rules' house or a usual MPN parameter name), or else any parameter, names the node itself and not `name` is
    another part (BAV99-7 with MfrPN = BAV99-7)."""
    import re
    if not name:
        return None
    nodes = nodes or []
    exact = next((n for n in nodes if (n.get("name") or "").lower() == name.lower()), None)
    rev = re.compile(re.escape(name) + REVISION, re.I)

    def other_part(n):
        own = (n.get("name") or "").lower()
        mpn = node_mpn(n, rules).lower()
        if mpn:
            return mpn == own and mpn != name.lower()
        vals = {str(p.get("value") or "").strip().lower()
                for p in ((n.get("details") or {}).get("parameters") or []) if isinstance(p, dict)}
        return own in vals and name.lower() not in vals
    return exact or next((n for n in nodes if rev.fullmatch(n.get("name") or "") and not other_part(n)), None)


def token_hint(token, now=None):
    """Why Nexar answered 401, in a line: an expired token says when it expired (a Nexar portal token is a JWT that
    lasts about a day); anything else is the scopes."""
    import base64, datetime, time
    try:
        body = token.split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("exp")
    except (AttributeError, IndexError, ValueError, TypeError):
        exp = None
    if isinstance(exp, (int, float)) and exp <= (now if now is not None else time.time()):
        when = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (f" (the token expired at {when}: run `ecadlib nexar signin` once to stay signed in, or make a new "
                "one on portal.nexar.com and store it with `ecadlib keys set NEXAR_TOKEN`)")
    return " (token rejected: check it has design.domain, user.access, openid)"


class NexarError(Exception):
    pass


class NexarUnreachable(ValueError):
    """Nexar refused the token or could not be reached: one plain line with what to do (no traceback in the UI)."""


def _http(token, send):
    """send() a Nexar request; refused / unreachable -> NexarUnreachable."""
    import requests
    from urllib.parse import urlsplit
    try:
        r = send()
        r.raise_for_status()
        return r
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", 0)
        if code in (401, 403):
            hint = token_hint(token)
            raise NexarUnreachable(f"Altium 365 (Nexar) refused the token ({code}){hint}" + (
                "" if "keys set" in hint else "; save a new one on the setup page or with `ecadlib keys set NEXAR_TOKEN`")
            ) from None
        raise NexarUnreachable(f"Altium 365 (Nexar) answered {code}: try again later") from None
    except requests.RequestException as e:
        host = urlsplit(getattr(e.request, "url", None) or API_URL).hostname
        raise NexarUnreachable(f"could not reach Altium 365 (Nexar, {host}; {type(e).__name__}): check the internet "
                               "connection or proxy, then try again") from None


class Nexar:
    def __init__(self, token, session=None):
        import requests
        self.token, self.s = token, session or requests.Session()

    def gql(self, query, variables=None):
        r = _http(self.token, lambda: self.s.post(API_URL, json={"query": query, "variables": variables or {}},
                                                  headers={"Authorization": f"Bearer {self.token}"}, timeout=60))
        body = r.json()
        if body.get("errors"):
            raise NexarError("; ".join(e.get("message", "?") for e in body["errors"]))
        return body["data"]

    def upload(self, path):
        with open(path, "rb") as f:
            r = _http(self.token, lambda: self.s.post(FILES_URL.rstrip("/") + "/File/Upload", files={"file": (Path(path).name, f)},
                                                      headers={"token": self.token}, timeout=120))
        return r.text.strip()              # opaque id; do not parse

    def workspaces(self):
        return self.gql(Q_WORKSPACES)["desWorkspaceInfos"]

    def release_defs(self, ws):
        return self.gql(Q_DEFS, {"ws": ws})

    def find(self, ws, name):
        conn = self.gql(Q_FIND, {"ws": ws, "name": name})["desLibrary"]["components"] or {}
        return pick_named(conn.get("nodes"), name, getattr(self, "rules", None))

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

    def transition(self, ws, defs, component_id, name, note):
        """Move a component revision through the life-cycle transition called `name` (or the one ending in state
        `name`). -> the transition's name; NexarError when the workspace has no such transition."""
        lc = (defs.get("componentLifeCycle") or {}).get("id")
        if not lc:
            raise NexarError("the workspace has no component life cycle definition")
        ts = (self.gql(Q_TRANSITIONS, {"id": lc})["desLifeCycleDefinitionById"] or {}).get("stateTransitions") or []
        low = name.strip().lower()
        t = next((t for t in ts if t and (t.get("name") or "").lower() == low), None) or \
            next((t for t in ts if t and ((t.get("lifeCycleStateAfter") or {}).get("name") or "").lower() == low), None)
        if not t:
            raise NexarError(f"no life-cycle transition {name!r} (have: {', '.join(sorted(x['name'] for x in ts if x))})")
        inp = {"componentId": component_id, "lifeCycleStateTransitionId": t["lifeCycleStateTransitionId"], "comment": note}
        errs = self.gql(M_LIFECYCLE, {"input": inp})["desUpdateComponentLifeCycleState"]["errors"]
        if errs:
            raise NexarError("; ".join(e["message"] for e in errs))
        return t["name"]

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


def plan(lines, triage=None, models=None, root=None, rules=None, prior=None):
    """One release item per unique Part Number, reusing the DbLib row builder for parameters.
    models: a folder or a list of folders, each with SchLib/<Library Ref>.SchLib and PcbLib/<Footprint Ref>.PcbLib;
    symbol and footprint are looked up independently, first folder wins. rules (ecadlib.rules, loaded): part and
    footprint names, folders and default parameters; None keeps ecadlib's own. root overrides the rules' folder root.
    prior: {name: MPN} of the plan so far (altium_export.prior_names): a released name stays with its part."""
    from . import rules as RU
    models = [models] if isinstance(models, (str, Path)) else list(models or [])
    items, names = [], set()
    defaults = RU.default_params(rules)
    for cat, df in to_rows(lines, triage, rules, prior=prior).items():
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
            if df.attrs.get("notes", {}).get(row["Part Number"]):    # BOM lines that disagree, renamed characters
                items[-1]["bom_notes"] = list(df.attrs["notes"][row["Part Number"]])
            if df.attrs.get("holds", {}).get(row["Part Number"]):    # lines that disagree on what is built: held
                items[-1]["bom_holds"] = list(df.attrs["holds"][row["Part Number"]])
            why = df.attrs.get("unstorable", {}).get(row["Part Number"])
            if why:                            # a name Altium can't store: this part is held, the others go on
                items[-1]["name_problems"] = [why]
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


MPN_NAMES = ("Manufacturer Part Number", "MPN", "Mfr Part Number")


def mpn_of(item_or_params, rules=None):
    """MPN of a plan item / component dict / {name: value}; falls back to the name."""
    from . import rules as RU
    x = item_or_params
    par = x if isinstance(x, dict) and "parameters" not in x and "name" not in x else (x.get("parameters") or {})
    if isinstance(par, list):
        par = {p["name"]: p["value"] for p in par}
    for k in ((RU.param_name("Manufacturer Part Number", rules),) if rules else ()) + MPN_NAMES:
        if str(par.get(k) or "").strip():
            return str(par[k]).strip()
    return (x.get("name") if isinstance(x, dict) and "name" in x else "") or ""


def merged_params(existing, item, rules=None):
    """Parameter list to send with replaceExisting=True, or None when the workspace already matches.
    Starts from the current revision's parameters so ones added by hand in Altium 365 survive; plan
    values win; fields the plan marks cleared are dropped, and so is a field under ecadlib's own name once the plan
    sends it under the rules' house name ([parameters.names]: an earlier push's "Manufacturer" when it sends "Mfr")."""
    from .rules import param_name
    cur = {p["name"]: p["value"] for p in ((existing.get("details") or {}).get("parameters") or [])}
    want = dict(cur)
    sent = {p["name"] for p in item["parameters"]}
    for k in item.get("cleared", []) + [k for k in cur if param_name(k, rules) != k and param_name(k, rules) in sent]:
        want.pop(k, None)
    want.update({p["name"]: p["value"] for p in item["parameters"]})
    return None if want == cur else [{"name": k, "value": v} for k, v in want.items()]


def _ecad_name(k, rules):
    """ecadlib's name for workspace parameter k (inverse of the rules' [parameters] names)."""
    inv = {v: c for c, v in (((rules or {}).get("parameters") or {}).get("names") or {}).items()}
    return inv.get(k, k)


def identity_change(existing, params, rules=None):
    """A parameter update that would make a released workspace component another part: [change text] for each
    identity field (MPN, manufacturer, the value as a number) the workspace has and `params` (the list a push would
    send) changes. Filling one the component does not have yet is no change of part. Both sides carry the house
    names of [parameters.names] ("MfrPN"): they are read back under ecadlib's names, which the fingerprint knows."""
    from . import change_diff as CD, fingerprint as FP
    cur = {_ecad_name(p["name"], rules): p["value"] for p in ((existing.get("details") or {}).get("parameters") or [])}
    new = {_ecad_name(p["name"], rules): p["value"] for p in params}
    a, b = FP.build("", params=cur), FP.build("", params=new)
    return [c["text"] for c in CD.diff(a, b) if c["class"] == "identity" and str(c.get("old") or "").strip()]


IDENTITY_REFUSED = ("not applied in place: a released workspace component keeps its MPN, manufacturer and value. "
                    "A different part needs its own component (another name in the plan); a correction goes through "
                    "change control (run the librarian on the BOM, then `ecadlib part approve`), which releases it as a "
                    "new component revision")


def ws_target(ws):
    """The ledger's publish target for workspace URL ws: one workspace is one target however its URL is typed (host
    case, a trailing slash, spaces), so a change published under one spelling is not taken as never published under
    another (which made an approved symbol change an in-place `up_to_date`, then recorded as published)."""
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit(str(ws or "").strip())
    return "altium365:" + urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), u.query, "")).rstrip("/")


def _published(lg, part, target):
    """The last publish line of part to workspace `target` (ws_target), also one written under another URL spelling."""
    norm = lambda t: ws_target(t[len("altium365:"):]) if str(t or "").startswith("altium365:") else t
    return next((e for e in reversed(lg.history(part))
                 if e.get("event") == "publish" and norm(e.get("target")) == norm(target)), None)


def change_mode(lg, it, target):
    """What a push of an existing component must do under change control: "revision" when the released revision
    (ledger head) changed the symbol, footprint or 3D model since the revision last published to `target` (a new
    component revision, never an edit of the released one), else "params". -> (mode, {"symbol", "footprint"} changed,
    head, published revision)"""
    from . import fingerprint as FP
    head = lg.head(it["name"]) if lg else None
    pub = _published(lg, it["name"], target) if lg else None
    old = lg.revision(it["name"], pub["revision"]) if head and pub and pub.get("revision") != head.get("revision") else None
    if not old or all(old.get(k) == head.get(k) for k in ("eid", "gid", "mid")):
        return "params", set(), head, pub
    a, b = lg.fingerprint(old) or {}, lg.fingerprint(head) or {}
    halves = {"symbol"} if FP.digest(a.get("symbol")) != FP.digest(b.get("symbol")) else set()
    if FP.digest([a.get("footprint"), a.get("model")]) != FP.digest([b.get("footprint"), b.get("model")]):
        halves.add("footprint")
    if FP.hashes(a)["eid"] != FP.hashes(b)["eid"] and not halves:   # recomputed: the two may be of different schemas
        halves = {"symbol", "footprint"}      # the pin-pad map changed: both go up
    if not halves:                             # stored hashes of two schemas differed, the files did not
        return "params", set(), head, pub
    return "revision", halves, head, pub


def _maybe_other(node, name, rules=None):
    """True when workspace node `node` was matched to item `name` only by a revision-looking name (SS34-13 for SS34)
    and carries no MPN to confirm it: it may be another part."""
    own = str(node.get("name") or "")
    return bool(own) and own.lower() != str(name).lower() and not node_mpn(node, rules)


def unpublished_halves(lg, target, sent_models, it, head):
    """The halves ({"symbol", "footprint"}) a new revision of a component this library never published to `target`
    must send: all but one whose workspace item the ledger knows holds the head's drawing (a shared symbol released
    with another part), which is linked."""
    out = set()
    for kind, half, name in zip(("symbols", "footprints"), ("symbol", "footprint"), model_item_names(it)):
        want = model_digest(lg, head, kind)
        have = name and (sent_models.get(f"{kind}:{name.lower()}") or released_model(lg, target, kind, name))
        if not (want and have and have[0] == want):
            out.add(half)
    return out


def _dup(e):
    return "duplicate" in str(e).lower()


class _Held(Exception):
    """A part push holds back before anything is sent; args[0]: the reasons."""


def model_digest(lg, entry, kind):
    """Fingerprint digest of a ledger revision's symbol ("symbols") or footprint + 3D model ("footprints"), or None."""
    from . import fingerprint as FP
    fp = lg.fingerprint(entry) if lg and entry else None
    if not fp:
        return None
    return FP.digest(fp.get("symbol")) if kind == "symbols" else FP.digest([fp.get("footprint"), fp.get("model")])


def released_model(lg, target, kind, name):
    """(digest, part, revision) of the drawing the workspace item `name` holds, from the ledger: a symbol / footprint
    item is released once (a second release of the name fails as duplicate), so it is the drawing of the first push to
    this workspace that sent it (`models` in its detail) or, for a ledger written before that was recorded, the first
    `release` whose part names that item. None: the ledger does not know (released outside ecadlib)."""
    from .ledger import RELEASES as LG_RELEASES
    if not lg:
        return None
    cache = lg.__dict__.setdefault("_a365_models", {})
    if target not in cache:                    # one pass over the ledger per push: "kind:name" -> first that sent it
        norm = lambda t: ws_target(t[len("altium365:"):]) if str(t or "").startswith("altium365:") else t
        revs = {(e.get("part"), e.get("revision")): e for e in lg.lines() if e.get("event") in LG_RELEASES}
        idx = cache[target] = {}
        for e in lg.lines():
            if e.get("event") != "publish" or norm(e.get("target")) != ws_target(target[len("altium365:"):]):
                continue
            d = e.get("detail") or {}
            for k, dg in (d.get("models") or {}).items():    # fingerprinted again from its revision when there:
                rv = revs.get((e.get("part"), e.get("revision")))      # a newer fingerprint schema compares like for like
                idx.setdefault(k, (rv or dg, e.get("part"), e.get("revision")))
            rev = revs.get((e.get("part"), e.get("revision"))) if d.get("action") == "release" and not d.get("models") else None
            item = (rev or {}).get("item")
            if isinstance(item, dict) and all(isinstance(item.get(h), dict) for h in ("symbol", "footprint")):
                for k, n in zip(("symbols", "footprints"), model_item_names(item)):
                    if n:
                        idx.setdefault(f"{k}:{n.lower()}", (rev, e.get("part"), e.get("revision")))
    hit = cache[target].get(f"{kind}:{name.lower()}")
    if hit and isinstance(hit[0], dict):       # the drawing of the revision it published
        hit = cache[target][f"{kind}:{name.lower()}"] = (model_digest(lg, hit[0], kind), hit[1], hit[2])
    return hit if hit and hit[0] else None


def matching_model(lg, target, sent_models, kind, name, digest):
    """(item name, (digest, part, revision)) of the workspace item NAME-r<n> that holds drawing `digest`, as
    push_revision names a changed shared symbol / footprint when NAME is taken: from this push (sent_models) or the
    ledger's publish records (released_model). The newest such item first; None when none holds this drawing."""
    import re
    pre = f"{kind}:{name.lower()}-r"
    keys = set(k for k in sent_models if k.startswith(pre))
    if lg:
        released_model(lg, target, kind, name)                  # builds the ledger's index for this target
        keys |= {k for k in (lg.__dict__.get("_a365_models") or {}).get(target, {}) if k.startswith(pre)}
    revs = sorted((int(k[len(pre):]) for k in keys if re.fullmatch(r"\d+", k[len(pre):])), reverse=True)
    for n in revs:
        alt = f"{name}-r{n}"
        hit = sent_models.get(f"{pre}{n}") or released_model(lg, target, kind, alt)
        if hit and hit[0] == digest:
            return alt, hit
    return None


def push_revision(it, client, ws, defs, upload, models_root, node, halves, rev, dry_run=False, note="ecadlib sync",
                  transition=None, digests=None):
    """A new revision of a component already in the workspace, for a change control approved (#105): the changed
    symbol / footprint are released as new models (under their item name, or NAME-r<rev> when the workspace refuses a
    second item of that name), the unchanged one is linked, and desReleaseComponent releases the component item
    again, which makes its next revision. The released revision is never edited. When the workspace makes no new
    revision, the result is an error and nothing else is sent. transition: an optional life-cycle transition (or
    target state) for the new revision ([change] a365_transition)."""
    names = model_item_names(it)
    before = _rev_id(node) if node else None
    found = []
    for kind, name, sub, half in zip(("symbols", "footprints"), names, ("Symbols", "Footprints"), ("symbol", "footprint")):
        if half in halves or not name:
            found.append(None)
        else:
            found.append(f"<id of {name}>" if dry_run else client.find_model(ws, kind, name, f"{models_root}/{sub}"))
    files = [] if found[0] else [it["symbol"]["file"]], [] if found[1] else [it["footprint"]["file"]]
    bad = readback_problems({k: it[k] for k, g in zip(("symbol", "footprint"), files) if g})
    if bad:                                    # the file to send holds another part's model: nothing goes up
        return {"action": "error", "detail": "; ".join(bad)}
    ids = tuple([(Path(p).name, upload(p)) for p in group] for group in files)
    parent, _ = split_folder(it["folder"])
    pid = (f"<id of {parent}>" if dry_run else client.folder_id(ws, parent)) if parent else None
    inp = release_input(it, ws, defs, ids, models_root, pid, tuple(found))
    detail = {"mutation": "desReleaseComponent", "new_revision_of": before or "<current revision>",
              "changed": sorted(halves), "ledger_revision": rev}
    if dry_run:
        return {"action": "new_revision", "detail": dict(detail, input=inp)}
    try:
        client.release(inp)
    except NexarError as e:
        if not _dup(e):
            raise
        renamed = dict(inp)                    # a model item of that name exists: the new model gets its own item
        for half, key in (("symbol", "symbolItemName"), ("footprint", "footprintItemName")):
            if half in halves and renamed.get(key):
                renamed[key] = f"{renamed[key]}-r{rev}"
        if renamed == inp:
            raise
        try:
            client.release(renamed)
        except NexarError as e2:
            return {"action": "error", "detail": f"Altium 365 made no new revision of {it['name']} ({e2}); the released "
                                                 f"revision {before} is unchanged in the workspace"}
        detail["model_item_names"] = [renamed.get("symbolItemName"), renamed.get("footprintItemName")]
        inp = renamed
    after = client.find(ws, it["name"]) or {}
    # the drawings this release put in the workspace, by item name (released_model: a later new part links them)
    models = {f"{kind}:{inp[key].lower()}": (digests or {}).get(kind)
              for kind, key, files in (("symbols", "symbolItemName", "symbolFiles"), ("footprints", "footprintItemName", "footprintFiles"))
              if inp.get(files) and inp.get(key) and (digests or {}).get(kind)}
    if not _rev_id(after) or _rev_id(after) == before:
        return {"action": "error", "detail": f"Altium 365 accepted the release but {it['name']} is still at revision "
                                             f"{before}: no new revision was made; nothing else was sent"}
    detail["revision"] = _rev_id(after)
    if transition:
        try:
            detail["transition"] = client.transition(ws, defs, after["id"], transition, note)
        except NexarError as e:
            detail["transition_error"] = str(e)
    return {"action": "new_revision", "detail": detail, "models": models}


def push(items, client, ws, dry_run=False, note="ecadlib sync", policy=None, rules=None, folder=None):
    """Release new parts, update parameters of parts already in the workspace.
    dry_run: no uploads or mutations; records the requests that would be sent.
    policy: the plan's approval policy (approvals.load_policy); None uses the one release_check stored per item.
    rules: parameter names go to the workspace in the rules' house names ([parameters] names).
    folder: the library folder (the plan's): change control (#105). Its ledger says which revision each part last
    published to this workspace; a released change to the symbol / footprint / 3D model then goes up as a new
    component revision (push_revision), a held change sends nothing new, and every push is recorded.
    Files go up only for parts that pass the release check run here, on the files as they are
    (release_check.publish_problems): the plan's `ready` is not taken on trust. A parameter update of a part
    already in the workspace sends no files and is not re-checked; a `held` part gets nothing."""
    from . import release_check as RC, rules as RU
    from .approvals import push_block
    from .rules import house_item
    from .rules import DEFAULTS
    # symbols and footprints go under the same root as the components ([folders] root), so a test root holds them all
    models_root = f"{(rules or DEFAULTS)['folders'].get('root') or 'Components'}/Models"
    # sign-off is checked on the item as it was approved (ecadlib's parameter names: the content hash covers them)
    blocks = [push_block(i, policy) for i in items]
    checked = RU.resolve(rules, folder or ".")
    rechecks = [RC.publish_problems(i, folder or ".", checked) for i in items]
    items = [house_item(i, rules) for i in items]
    if isinstance(client, Nexar):              # find reads the MPN under the house name too (pick_named)
        client.rules = rules
    defs = {} if dry_run else client.release_defs(ws)
    results, uploaded, released = [], {}, set()   # one upload per file per run; (kind, name) released this run
    sent_models = {}                           # "kind:name" -> (digest, part, revision) released this run
    from . import ledger as LG
    lg = LG.Ledger(folder) if folder and LG.Ledger(folder).exists() else None
    target = ws_target(ws)
    transition = ((rules or {}).get("change") or {}).get("a365_transition") or None
    def upload(p):
        if p not in uploaded:
            uploaded[p] = "<fileId>" if dry_run else client.upload(p)
        return uploaded[p]
    def recheck(res, why):                     # the release check, run again here, holds it back
        res.update(action="skip", detail="held: " + "; ".join(why), recheck=why)
    for it, block, why in zip(items, blocks, rechecks):
        res = {"name": it["name"], "action": "", "detail": ""}
        if it.get("status") == "held":         # release_check held it back: no release, no parameter update
            res["action"], res["detail"] = "skip", "held: " + "; ".join(it.get("hold_reasons") or ["release check"])
            results.append(res)
            continue
        if block:                              # sign-off policy not met, or a dedupe duplicate
            res["action"], res["detail"] = "skip", block
            results.append(res)
            continue
        held = (it.get("change") or {}).get("state") == "held"
        if held:                               # a change waits for approval: the workspace keeps the released one
            res["change"] = (f"a change is held for approval; revision r{it['change'].get('base')} stays "
                             "(ecadlib part show)")
        try:
            existing = None if dry_run else client.find(ws, it["name"])
            mode, halves, head, pub = change_mode(lg, it, target) if lg else ("params", set(), None, None)
            if existing and head and not pub:  # in the workspace, never published there from this library (made by
                mode = "revision"              # hand, an older ecadlib, a release whose answer was lost): what it
                halves = unpublished_halves(lg, target, sent_models, it, head)   # holds is unknown, so the head goes
                res["unpublished"] = (f"{it['name']} is in the workspace but this library has no record of publishing "
                                      f"it there: released again from ledger revision r{head['revision']}")
            if (existing or (dry_run and lg and _published(lg, it["name"], target))) and mode == "revision":
                if why:
                    recheck(res, why)
                else:
                    digests = {k: model_digest(lg, head, k) for k in ("symbols", "footprints")}
                    res.update(push_revision(it, client, ws, defs, upload, models_root, existing, halves,
                                             head["revision"], dry_run, note, transition, digests))
                    sent_models.update({k: (d, it["name"], head["revision"]) for k, d in (res.get("models") or {}).items()})
            elif existing and it.get("status", "ready") not in RC.PUBLISHABLE:   # draft, needs_review, a typo: no
                recheck(res, why or [f"status: {it.get('status')!r} is not ready"])   # parameter update (as the dry run)
            elif existing and not pub and _maybe_other(existing, it["name"], rules):
                res["action"], res["detail"] = "skip", (
                    f"the workspace has {existing.get('name')}, which may be a revision of {it['name']} or another "
                    "part (no MPN parameter says which), and this library never published it there: nothing was "
                    "changed. Add its MPN parameter in Altium 365, or rename one of them")
            elif existing:
                new = merged_params(existing, it, rules)
                res["a365_revision"] = _rev_id(existing)
                ident = identity_change(existing, new, rules) if new is not None else []
                if new is None:
                    res["action"] = "up_to_date"
                elif ident:                    # change control (#105): in-place edits never make it another part
                    res["action"], res["detail"] = "skip", f"identity change ({'; '.join(ident)}) {IDENTITY_REFUSED}"
                else:                          # full replace: a merge, so parameters added in Altium 365 are kept
                    res["action"] = "update_parameters"
                    res["detail"] = client.update_params(existing["id"], new, note, replace=True)
                    try:                       # the revision the update left (it takes a release note: a new one),
                        res["a365_revision"] = _rev_id(client.find(ws, it["name"])) or res["a365_revision"]
                    except Exception as e:     # for `ecadlib project check`; best effort, as after a release
                        from . import fallbacks as FB
                        FB.note(f"{it['name']}: parameters updated, but the Altium 365 revision id did not read back", e)
            elif it.get("status", "ready") != "ready":
                res["action"], res["detail"] = "skip", "needs_altium_models (no .SchLib/.PcbLib)"
            elif why:
                recheck(res, why)
            elif bad := readback_problems(it):     # a file named for this part holding another's model: not sent
                res["action"], res["detail"] = "error", "; ".join(bad)
            else:
                # a symbol / footprint shared by several parts (Capacitor, R_0603) is released with the first and
                # linked by the others: releasing it again fails with AddALU_ItemsDuplicate item HRID
                names = model_item_names(it)
                digests = {k: model_digest(lg, head, k) for k in ("symbols", "footprints")} if lg and head else {}
                found, known = [], {}
                for kind, name, sub in zip(("symbols", "footprints"), names, ("Symbols", "Footprints")):
                    key = (kind, (name or "").lower())
                    if name and lg:            # the drawing that item holds, when the ledger knows it
                        known[kind] = sent_models.get(f"{kind}:{name.lower()}") or released_model(lg, target, kind, name)
                    if not name:
                        found.append(None)
                    elif dry_run:
                        found.append(f"<id of {name}>" if key in released else None)
                    else:
                        found.append(client.find_model(ws, kind, name, f"{models_root}/{sub}"))
                # a shared symbol / footprint is linked by name: when the workspace's item holds another drawing than
                # this part's (it changed since it went up), the item that does hold it (NAME-r<n>, released by a new
                # revision of another part) is linked instead; with none, the part is held
                names = list(names)
                for i, (kind, sub) in enumerate((("symbols", "Symbols"), ("footprints", "Footprints"))):
                    if not ((found[i] or dry_run) and known.get(kind) and digests.get(kind) and known[kind][0] != digests[kind]):
                        continue
                    alt = matching_model(lg, target, sent_models, kind, names[i], digests[kind])
                    fid = alt and (f"<id of {alt[0]}>" if dry_run else client.find_model(ws, kind, alt[0], f"{models_root}/{sub}"))
                    if fid:
                        names[i], found[i], known[kind] = alt[0], fid, alt[1]
                stale = [f"the workspace's {kind[:-1]} {name!r} is another drawing than this part's (it went up with "
                         f"{known[kind][1]} r{known[kind][2]}): linking it would release this part with the old one"
                         for kind, name, f in zip(("symbols", "footprints"), names, found)
                         if (f or dry_run) and known.get(kind) and digests.get(kind) and known[kind][0] != digests[kind]]
                if stale:
                    raise _Held(stale)
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
                    try:                       # the revision it made, for `ecadlib project check` (best effort)
                        res["a365_revision"] = _rev_id(client.find(ws, it["name"]))
                    except Exception as e:     # `ecadlib project check` then can't tell this revision from a later one
                        from . import fallbacks as FB
                        FB.note(f"{it['name']}: released, but its new Altium 365 revision id did not read back", e)
                released.update((k, (n or "").lower()) for k, n in zip(("symbols", "footprints"), names) if n)
                res["models"] = {f"{k}:{n.lower()}": digests[k] for k, n, g in zip(("symbols", "footprints"), names, files)
                                 if n and g and digests.get(k)}       # the drawings it put there (released_model)
                sent_models.update({k: (d, it["name"], head["revision"]) for k, d in res["models"].items()})
        except _Held as e:                     # nothing was sent for this part
            recheck(res, e.args[0])
        except Exception as e:                 # keep going; report per part
            res["action"], res["detail"] = "error", f"{type(e).__name__}: {e}"
        sim = (it.get("sim_model") or {}).get("attached", {}).get("altium")
        if sim and res["action"] in ("release", "update_parameters", "up_to_date", "new_revision"):
            res["sim_model"] = (f"{sim['type']} link {sim['model_name']} travels inside the .SchLib; the model file "
                                f"{sim.get('file') or '(none: built-in)'} is not uploaded (Nexar has no simulation-model input)")
        results.append(res)
    if lg and not dry_run:                     # what went up, per part, at which ledger revision
        from . import change_gate as CG
        # an in-place result (up_to_date / update_parameters) proves nothing about the drawings: it is recorded only
        # over an earlier publish line of that part (never the first publish of a part already in the workspace)
        ok = {r["name"] for r in results if r["action"] in ("release", "new_revision") or (
            r["action"] in PUSHED and _published(lg, r["name"], target))}
        def detail(i):                         # the action, and the workspace revision it left (project check)
            r = next((r for r in results if r["name"] == i["name"]), {})
            d = r.get("detail")
            rid = r.get("a365_revision") or (d.get("revision") if isinstance(d, dict) else None)
            return {"action": r.get("action", ""), **({"a365_revision": rid} if rid else {}),
                    **({"models": r["models"]} if r.get("models") else {})}
        CG.record_publish(folder, items, target, ok=lambda i: i["name"] in ok, detail=detail)
    return results


PUSHED = ("release", "update_parameters", "up_to_date", "new_revision")


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
    u.add_argument("--workspace", help="workspace URL from `workspaces` (default: [altium365] workspace in ecadlib.toml)")
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
        from .approvals import cli_plan_lock    # a workspace plan: the web UI may be recording into it right now
        from .fsutil import write_text_atomic
        with cli_plan_lock(out):
            write_text_atomic(out, json.dumps(items, indent=2))
        ready = sum(i["status"] == "ready" for i in items)
        print(f"{len(items)} components planned, {ready} with models, {len(items) - ready} need Altium models -> {out}")
        if ready:                              # `ready` = the files are there; push checks them before it releases
            print(f"  push runs the release check first; to see it now: ecadlib release-check {out}")
        for i in items:
            print(f"  {i['status']:<20} {i['folder']:<24} {i['name']:<28} sym={i['symbol']['ref']} fp={i['footprint']['ref']}")
        return

    if a.cmd == "push" and not Path(a.plan).is_file():
        sys.exit(f"{a.plan}: no such plan file (write one with `ecadlib altium365 plan BOM -o {a.plan}`)")
    if a.cmd == "push" and not a.workspace:
        a.workspace = str((RU.load(a.rules, start=Path(a.plan).parent).get("altium365") or {}).get("workspace") or "").strip()
        if not a.workspace:
            sys.exit("ecadlib altium365 push: no workspace: pass --workspace URL or set [altium365] workspace in ecadlib.toml "
                     "(`ecadlib altium365 workspaces` lists them)")
    if a.cmd == "push":                        # the local files first: a damaged one is named, not "Nexar request failed"
        from .kicad_export import PlanError, check_plan
        try:
            items = json.loads(Path(a.plan).read_text(encoding="utf-8"))
            check_plan(items, a.plan)          # {} or a broken entry
        except (json.JSONDecodeError, PlanError) as e:
            why = (f"{a.plan}: not valid JSON ({e.msg} at line {e.lineno} column {e.colno})"
                   if isinstance(e, json.JSONDecodeError) else str(e))      # PlanError names the file itself
            print(f"ecadlib altium365: {why}\n  hint: is this the right file? `ecadlib altium365 plan` or `ecadlib "
                  "librarian` writes plan.json", file=sys.stderr)
            sys.exit(2)
        rules = RU.load(a.rules, start=Path(a.plan).parent)   # a bad ecadlib.toml: RulesError names it (exit 2)
    token = UC.secret("NEXAR_TOKEN")
    if not token and not (a.cmd == "push" and a.dry_run):
        from . import nexar_signin
        sys.exit("no Altium 365 token (NEXAR_TOKEN): " + nexar_signin.hint())
    client = Nexar(token) if token else None
    try:
        if a.cmd == "workspaces":
            for w in client.workspaces():
                print(f"{w['name']:<30} {w['url']}")
            return
        from .approvals import load_policy
        from . import change_gate as CG
        import time
        import contextlib                      # a real push holds the library lock from the settle step to the record
        with contextlib.nullcontext() if a.dry_run else CG.publishing(Path(a.plan).parent):
            if not a.dry_run:                  # change control: a hand edit since the last writer is settled first
                CG.publish_guard(Path(a.plan), items, "altium365", rules=rules)
            else:                              # a dry run writes nothing: an unsettled hand edit is shown as held
                CG.preview_unsettled(Path(a.plan).parent, items, rules)
            for i in items:
                for k in ("symbol", "footprint"):
                    if i[k]["file"]:
                        i[k]["file"] = str((Path(a.plan).parent / i[k]["file"]).resolve())
            t0 = time.time()
            results = push(items, client, a.workspace, dry_run=a.dry_run, policy=load_policy(Path(a.plan).parent),
                           rules=rules, folder=Path(a.plan).parent)
            if not a.dry_run:
                record_push(Path(a.plan).parent, t0, results)
    except Exception as e:                     # auth / network / GraphQL errors before any per-part work
        hint = token_hint(token) if "401" in str(e) and not isinstance(e, NexarUnreachable) else ""
        sys.exit(f"Nexar request failed: {type(e).__name__}: {e}{hint}")
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"  {r['action']:<18} {r['name']}")
    print(f"report -> {out}")
    if a.dry_run:                              # no network: what the workspace already holds was not looked up
        print("dry run: the workspace was not read, so a part shown as `release` may already be there (the real push "
              "then updates it, or adds a new revision for an approved change)")
    failed = sum(r["action"] == "error" for r in results)
    if failed:
        sys.exit(f"{failed} of {len(results)} parts failed (see {out})")


if __name__ == "__main__":
    main()
