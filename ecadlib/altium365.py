"""Altium 365 workspace exporter, through the Nexar Design API.

Turns a BOM (plus the triage report) into a *release plan*: one managed workspace component
per part, with its parameters and the symbol (.SchLib) and footprint (.PcbLib) files to upload.
Then `push` releases new components and keeps parameters of existing ones up to date.

    python -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models out/altium365_models -o out/altium365/plan.json
    python -m ecadlib.altium365 workspaces                                   # needs NEXAR_TOKEN
    python -m ecadlib.altium365 push out/altium365/plan.json --workspace URL [--dry-run]

Flow per part (mirrors NexarDeveloper/nexar-release-component-demo-wpf, schema as of 2024-07):
  1. desLibrary.components(where: {name: {eq}})   exists? -> desUpdateComponentRevisionParameters
  2. POST {files}/File/Upload (header `token`)    -> plain-text fileId, valid 24 h
  3. desReleaseComponent(input)                   symbol + footprint + component in one call
Release needs real Altium binaries: one symbol per .SchLib, one footprint per .PcbLib, under
--models/SchLib/<Library Ref>.SchLib and --models/PcbLib/<Footprint Ref>.PcbLib. Parts without
them are planned as `needs_altium_models` (convert our KiCad output with Altium's KiCad importer).

Auth: NEXAR_TOKEN = a user access token with scopes openid, user.access, design.domain
(the token generated in the Nexar portal has them). Supply-only client-credential tokens
cannot see workspaces."""
import argparse, json, os, sys
from pathlib import Path

import pandas as pd

from .altium_export import RESERVED, to_rows
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
  desLibrary(workspaceUrl: $ws) { components(first: 1, where: {name: {eq: $name}}) { nodes { id name } } }
}"""

M_RELEASE = """mutation ($input: DesReleaseComponentInput!) { desReleaseComponent(input: $input) { errors { message } } }"""

M_UPDATE_PARAMS = """mutation ($input: DesUpdateComponentRevisionParametersInput!) {
  desUpdateComponentRevisionParameters(input: $input) { componentId }
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

    def find_component(self, ws, name):
        conn = self.gql(Q_FIND, {"ws": ws, "name": name})["desLibrary"]["components"] or {}
        nodes = conn.get("nodes") or []
        return nodes[0]["id"] if nodes else None

    def release(self, inp):
        errs = self.gql(M_RELEASE, {"input": inp})["desReleaseComponent"]["errors"]
        if errs:
            raise NexarError("; ".join(e["message"] for e in errs))

    def update_params(self, component_id, params, note):
        inp = {"componentId": component_id, "releaseNote": note, "replaceExisting": False, "parameters": params}
        return self.gql(M_UPDATE_PARAMS, {"input": inp})["desUpdateComponentRevisionParameters"]["componentId"]


# ------------------------------------------------------------------ plan
def plan(lines, triage=None, models=None, root="Components"):
    """One release item per unique Part Number, reusing the DbLib row builder for parameters."""
    models = Path(models) if models else None
    items = []
    for cat, df in to_rows(lines, triage).items():
        for row in df.to_dict("records"):
            sym = fp = None
            if models:
                s, f = models / "SchLib" / f"{row['Library Ref']}.SchLib", models / "PcbLib" / f"{row['Footprint Ref']}.PcbLib"
                sym, fp = (str(s) if s.exists() else None), (str(f) if f.exists() else None)
            params = [{"name": k, "value": str(v)} for k, v in row.items()
                      if k not in RESERVED and k not in ("Part Number", "Comment") and str(v)]
            items.append({
                "name": row["Part Number"], "folder": f"{root}/{cat}",
                "description": row["Description"], "comment": row["Comment"], "parameters": params,
                "symbol": {"ref": row["Library Ref"], "file": sym},
                "footprint": {"ref": row["Footprint Ref"], "file": fp},
                "status": "ready" if sym and fp else "needs_altium_models",
            })
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
        "symbolReleaseFolder": f"{models_root}/Symbols", "symbolItemName": None,
        "symbolFiles": [{"fileId": i, "relativePath": f"Released\\{n}"} for n, i in sym_ids],
        "symbolRevisionNamingSchemeNodeId": nid("symbolScheme"),
        "symbolLifeCycleDefinitionNodeId": nid("symbolLifeCycle"),
        "footprintReleaseFolder": f"{models_root}/Footprints", "footprintItemName": None,
        "footprintFiles": [{"fileId": i, "relativePath": f"Released\\{n}"} for n, i in fp_ids],
        "footprintRevisionNamingSchemeNodeId": nid("footprintScheme"),
        "footprintLifeCycleDefinitionNodeId": nid("footprintLifeCycle"),
    }


# ------------------------------------------------------------------ push
def push(items, client, ws, dry_run=False, note="ecadlib sync"):
    """Release new parts, update parameters of parts already in the workspace.
    dry_run: no uploads or mutations; records the requests that would be sent."""
    defs = {} if dry_run else client.release_defs(ws)
    results = []
    for it in items:
        res = {"name": it["name"], "action": "", "detail": ""}
        try:
            existing = None if dry_run else client.find_component(ws, it["name"])
            if existing:
                res["action"] = "update_parameters"
                res["detail"] = client.update_params(existing, it["parameters"], note)
            elif it["status"] != "ready":
                res["action"], res["detail"] = "skip", "needs_altium_models (no .SchLib/.PcbLib)"
            else:
                files = [it["symbol"]["file"]], [it["footprint"]["file"]]
                ids = tuple([(Path(p).name, "<fileId>" if dry_run else client.upload(p)) for p in group]
                            for group in files)
                inp = release_input(it, ws, defs, ids)
                res["action"] = "release"
                if dry_run:
                    res["detail"] = {"mutation": "desReleaseComponent", "input": inp}
                else:
                    client.release(inp)
        except Exception as e:                 # keep going; report per part
            res["action"], res["detail"] = "error", f"{type(e).__name__}: {e}"
        results.append(res)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Release BOM parts into an Altium 365 workspace via Nexar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="build a release plan from a BOM")
    p.add_argument("bom")
    p.add_argument("--triage", help="triage CSV from bom_triage")
    p.add_argument("--models", help="folder with SchLib/<ref>.SchLib and PcbLib/<ref>.PcbLib")
    p.add_argument("--root", default="Components", help="workspace folder to release components under")
    p.add_argument("-o", "--out", default="out/altium365/plan.json")
    sub.add_parser("workspaces", help="list workspaces visible to NEXAR_TOKEN")
    u = sub.add_parser("push", help="release / update parts from a plan")
    u.add_argument("plan")
    u.add_argument("--workspace", required=True, help="workspace URL from `workspaces`")
    u.add_argument("--dry-run", action="store_true", help="no network; write the requests that would be sent")
    u.add_argument("-o", "--out", default="out/altium365/push_report.json")
    a = ap.parse_args(argv)

    if a.cmd == "plan":
        lines, _ = read_bom(a.bom)
        tri = pd.read_csv(a.triage, dtype=str).fillna("") if a.triage else None
        items = plan(lines, tri, a.models, a.root)
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
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
    if a.cmd == "workspaces":
        for w in client.workspaces():
            print(f"{w['name']:<30} {w['url']}")
        return

    items = json.loads(Path(a.plan).read_text())
    results = push(items, client, a.workspace, dry_run=a.dry_run)
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"  {r['action']:<18} {r['name']}")
    print(f"report -> {out}")


if __name__ == "__main__":
    main()
