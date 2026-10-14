"""Upstream change alerts: KiCad library parts we imported, and Altium 365 components that drift from the plan.

    python3 -m ecadlib.upstream check-kicad out/altium_models_kicad/manifest.json [-o out/upstream_kicad] [--update]
    NEXAR_TOKEN=... python3 -m ecadlib.upstream check-a365 out/altium365/plan.json --workspace URL \
        [--folder Components] [--state out/a365_state.json] [-o out/upstream_a365]

Provenance: kicad_import.import_part calls record() after writing a part, which adds an entry to
<out>/manifest.json per output file: KiCad ref, source URLs (footprint, STEP model, symbol and its `extends`
parents), sha256 and git blob id of each source, the last GitLab commit touching it, a small summary of the
source (pads and courtyard, pins and key properties, STEP extent), the output file and its sha256, and the
converter version. check-kicad asks the GitLab API (HEAD .../repository/files, no download) for each source's
blob id, fetches only the changed ones and diffs their summaries; a 404 is traced through the commit that removed
the path to tell `deleted` from `renamed`. --update reconverts parts whose sources changed (not deleted/renamed).
check-a365 lists the workspace library and compares it with a plan.json: missing components, parameter, comment,
description, folder, symbol and footprint names, and with --state the component revision and the symbol/footprint
GUIDs seen last time (Nexar exposes no symbol/footprint revision, so a GUID change is the closest signal).
Reports: <out>.md and <out>.csv. Exit codes: 0 no changes, 1 changes found, 2 error. GITLAB_TOKEN is optional."""
import argparse, csv, hashlib, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import __version__, kicad_import as K, sexpr
from .httpcache import CachedHTTP, Response

RAW = re.compile(r"https://gitlab\.com/kicad/libraries/([^/]+)/-/raw/([^/]+)/(.+)$")
API = "https://gitlab.com/api/v4/projects/kicad%2Flibraries%2F{repo}/repository"
RANK = {"unchanged": 0, "changed": 1, "renamed": 2, "deleted": 3, "error": 4}


def converter_version():
    return f"ecadlib {__version__} kicad_import {hashlib.sha256(Path(K.__file__).read_bytes()).hexdigest()[:12]}"


def digests(text):
    b = text.encode("utf-8")
    return hashlib.sha256(b).hexdigest(), hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()   # blob id = git's


# ---------------------------------------------------------------- source summaries (what a diff talks about)
def fp_summary(text):
    t = sexpr.parse(text)
    pads = []
    for p in sexpr.find(t, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        pads.append([p[1], *(round(float(v), 4) for v in (at[1], at[2], size[1], size[2])), p[3],
                     round(float(at[3]), 2) if len(at) > 3 else 0.0])
    cy = sexpr.courtyard_bbox(t)
    return {"pads": pads, "courtyard": [round(v, 4) for v in cy] if cy else None}


def _find_symbol(text, name):
    return next((s for s in sexpr.find(sexpr.parse(text), "symbol") if s[1] == name), None)


def sym_summary(text, name):
    s = _find_symbol(text, name)
    if not s:
        return {"missing": True}
    pins = {}
    for sub in sexpr.find(s, "symbol"):
        if sub[1].rsplit("_", 1)[-1] not in ("0", "1"):           # De Morgan body style
            continue
        for p in sexpr.find(sub, "pin"):
            pins[(sexpr.first(p, "number") or ["", ""])[1]] = [(sexpr.first(p, "name") or ["", ""])[1], p[1]]
    props = {p[1]: p[2] for p in sexpr.find(s, "property") if p[1] in ("Value", "Footprint", "Datasheet", "Description")}
    return {"pins": pins, "props": props, "extends": (sexpr.first(s, "extends") or [None, None])[1]}


def step_summary(text):
    ext = K.step_extent(text, {"scale": [1, 1, 1], "rotate": [0, 0, 0], "offset": [0, 0, 0]})
    return {"extent": [round(v, 3) for v in ext] if ext else None}


def summarize(src, text):
    return {"footprint": lambda: fp_summary(text), "step": lambda: step_summary(text),
            "symbol": lambda: sym_summary(text, src["symbol"])}[src["role"]]()


def _few(xs, n=6):
    xs = list(xs)
    return ", ".join(xs[:n]) + (f" +{len(xs) - n} more" if len(xs) > n else "")


def diff_fp(a, b):
    def keyed(pads):
        out, seen = {}, {}
        for p in pads:
            k = seen[p[0]] = seen.get(p[0], -1) + 1
            out[(p[0] or "(npth)") + (f"#{k + 1}" if k else "")] = p
        return out
    ka, kb = keyed(a["pads"]), keyed(b["pads"])
    out = [f"pads {len(ka)} -> {len(kb)}"] if len(ka) != len(kb) else []
    if set(kb) - set(ka):
        out.append("added " + _few(sorted(set(kb) - set(ka))))
    if set(ka) - set(kb):
        out.append("removed " + _few(sorted(set(ka) - set(kb))))
    common = [k for k in ka if k in kb]
    moved = [f"{k} ({ka[k][1]:g},{ka[k][2]:g})->({kb[k][1]:g},{kb[k][2]:g})" for k in common if ka[k][1:3] != kb[k][1:3]]
    sized = [f"{k} {ka[k][3]:g}x{ka[k][4]:g}->{kb[k][3]:g}x{kb[k][4]:g}" for k in common if ka[k][3:5] != kb[k][3:5]]
    shaped = [f"{k} {ka[k][5]}/{ka[k][6]:g}->{kb[k][5]}/{kb[k][6]:g}" for k in common if ka[k][5:] != kb[k][5:]]
    out += [f"{lbl} {_few(x, 4)}" for lbl, x in (("moved", moved), ("resized", sized), ("shape/rotation", shaped)) if x]
    if a["courtyard"] != b["courtyard"]:
        out.append(f"courtyard {a['courtyard']} -> {b['courtyard']}")
    return out


def diff_sym(a, b):
    if b.get("missing"):
        return ["symbol no longer in the file"]
    pa, pb = a.get("pins", {}), b.get("pins", {})
    out = []
    if set(pb) - set(pa):
        out.append("pins added " + _few(f"{n} {pb[n][0]}" for n in sorted(set(pb) - set(pa))))
    if set(pa) - set(pb):
        out.append("pins removed " + _few(f"{n} {pa[n][0]}" for n in sorted(set(pa) - set(pb))))
    common = sorted(set(pa) & set(pb))
    ren = [f"{n} {pa[n][0]}->{pb[n][0]}" for n in common if pa[n][0] != pb[n][0]]
    typ = [f"{n} {pa[n][1]}->{pb[n][1]}" for n in common if pa[n][1] != pb[n][1]]
    out += [f"{lbl} {_few(x, 4)}" for lbl, x in (("pins renamed", ren), ("pin types", typ)) if x]
    props = sorted(k for k in set(a.get("props", {})) | set(b.get("props", {})) if a["props"].get(k) != b["props"].get(k))
    if props:
        out.append("properties changed: " + ", ".join(props))
    if a.get("extends") != b.get("extends"):
        out.append(f"extends {a.get('extends')} -> {b.get('extends')}")
    return out


def diff_summary(role, a, b):
    if not a or not b:
        return []
    if role == "footprint":
        return diff_fp(a, b)
    if role == "symbol":
        return diff_sym(a, b)
    return [f"STEP extent {a['extent']} -> {b['extent']}"] if a.get("extent") != b.get("extent") else []


# ---------------------------------------------------------------- provenance (called by kicad_import)
def _remote(repo, path):
    return K.GITLAB.format(repo=repo, path=path)


def fp_sources(ref, http):
    """[(role, location, text, extra)] the converter read for a footprint: the .kicad_mod and its STEP model."""
    if Path(ref).suffix == ".kicad_mod":
        loc, text = str(ref), Path(ref).read_text()
    else:
        lib, name = ref.split(":", 1)
        path = f"{lib}.pretty/{name}.kicad_mod"
        loc, text = _remote("kicad-footprints", path), K.fetch(http, "kicad-footprints", path)
    out = [("footprint", loc, text, {})]
    mi = K.model_info(sexpr.parse(text))
    mp = K.model_url_path(mi["path"]) if mi else None
    if mp and http is not None:                # add_body only fetches the model when it has an http client
        st = K.fetch(http, "kicad-packages3D", mp)
        if st is not None:
            out.append(("step", _remote("kicad-packages3D", mp), st, {}))
    return out


def sym_sources(ref, http):
    """The symbol file(s) of a symbol and its `extends` parents, resolved as kicad_import.load_symbol does."""
    m = re.match(r"(.+\.kicad_sym)(?::(.+))?$", ref)
    if m:
        path = Path(m.group(1))
        def get(name):
            for f in (path, path.with_name(f"{name}.kicad_sym")):
                if f.exists() and _find_symbol(f.read_text(), name):
                    return str(f), f.read_text()
            raise FileNotFoundError(f"symbol {name} not in {path}")
        name = m.group(2) or [s[1] for s in sexpr.find(sexpr.parse(path.read_text()), "symbol")][0]
    else:
        lib, name = ref.split(":", 1)
        def get(name):
            for p in (f"{lib}.kicad_symdir/{name}.kicad_sym", f"{lib}.kicad_sym"):
                text = K.fetch(http, "kicad-symbols", p)
                if text and _find_symbol(text, name):
                    return _remote("kicad-symbols", p), text
            raise FileNotFoundError(f"KiCad symbol {lib}:{name} not found")
    out = []
    while name and len(out) < 8:
        loc, text = get(name)
        out.append(("symbol", loc, text, {"symbol": name}))
        name = (sexpr.first(_find_symbol(text, name), "extends") or [None, None])[1]
    return out


def last_commit(http, repo, path):
    if http is None:
        return None
    url = API.format(repo=repo) + f"/commits?path={quote(path)}&ref_name=master&per_page=1"
    try:
        r = http.get(url)
        c = r.json()[0] if r.ok else None
        return {"id": c["id"], "date": c["created_at"], "title": c["title"]} if c else None
    except Exception:
        return None


def source_record(role, loc, text, extra, http):
    sha, blob = digests(text)
    src = {"role": role, **extra, "sha256": sha, "blob_id": blob}
    m = RAW.match(loc)
    if m:
        src.update(url=loc, repo=m.group(1), ref=m.group(2), path=m.group(3), last_commit=last_commit(http, m.group(1), m.group(3)))
    else:
        src["file"] = loc
    src["summary"] = summarize(src, text)
    return src


def load_manifest(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {"version": 1, "entries": {}}


def save_manifest(path, man):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, indent=1))
    os.replace(tmp, p)


def record(out, spec, rep, http, step=False, gen_step=True):
    """Add/replace the manifest entries for a part kicad_import just wrote. Never raises: a failure is a note."""
    try:
        mpath, now = Path(out) / "manifest.json", datetime.now(timezone.utc).isoformat(timespec="seconds")
        man = load_manifest(mpath)
        for kind, key, ref, srcs in (("footprint", "pcblib", spec.get("footprint"), fp_sources),
                                     ("symbol", "schlib", spec.get("symbol"), sym_sources)):
            if not rep.get(key) or not ref:
                continue
            f = Path(rep[key])
            man["entries"][f"{kind}:{f.stem}"] = {
                "kind": kind, "kicad_ref": ref, "mpn": spec.get("mpn", ""),
                "output": os.path.relpath(f, Path(out)), "output_sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                "converter": converter_version(), "converted_at": now, "step": bool(step), "gen_step": bool(gen_step),
                "spec": {k: v for k, v in spec.items() if v is not None},
                "sources": [source_record(*s, http) for s in srcs(ref, http)]}
        save_manifest(mpath, man)
    except Exception as e:
        rep.setdefault("notes", []).append(f"provenance not recorded: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- GitLab
class GitLab:
    """GitLab REST v4 for gitlab.com/kicad/libraries/*: HEAD for blob ids, raw content, last commit, rename tracing."""
    def __init__(self, session=None, token=None, ref="master"):
        if session is None:
            import requests
            session = requests.Session()
        self.s, self.ref = session, ref
        self.h = {"PRIVATE-TOKEN": token} if token else {}

    def _req(self, method, url, params=None):
        r = self.s.request(method, url, params=params, headers=self.h, timeout=60)
        if r.status_code == 404:
            return None
        if not 200 <= r.status_code < 300:
            raise IOError(f"GitLab {method} {url}: HTTP {r.status_code}")
        return r

    def _file(self, repo, path):
        return API.format(repo=repo) + "/files/" + quote(path, safe="")

    def head(self, repo, path):
        r = self._req("HEAD", self._file(repo, path), {"ref": self.ref})
        return {"blob_id": r.headers.get("x-gitlab-blob-id"), "last_commit_id": r.headers.get("x-gitlab-last-commit-id")} if r else None

    def raw(self, repo, path):
        r = self._req("GET", self._file(repo, path) + "/raw", {"ref": self.ref})
        return r.content.decode("utf-8") if r else None

    def commit(self, repo, path):
        r = self._req("GET", API.format(repo=repo) + "/commits", {"path": path, "ref_name": self.ref, "per_page": 1})
        c = r.json()[0] if r and r.json() else None
        return {"id": c["id"], "date": c["created_at"], "title": c["title"]} if c else None

    def moved(self, repo, path):
        """A path that 404s: ('renamed', new_path, commit) or ('deleted', None, commit) from the commit that last touched it."""
        c = self.commit(repo, path)
        if not c:
            return "deleted", None, None
        for page in range(1, 51):
            r = self._req("GET", API.format(repo=repo) + f"/commits/{c['id']}/diff", {"per_page": 100, "page": page})
            files = r.json() if r else []
            for d in files:
                if d.get("old_path") == path:
                    return ("renamed", d["new_path"], c) if d.get("renamed_file") else ("deleted", None, c)
            if len(files) < 100:
                break
        return "deleted", None, c


# ---------------------------------------------------------------- check-kicad
def check_source(src, gl, memo):
    """-> dict(status, changes, new_path, commit, text)"""
    key = (src.get("repo"), src.get("path") or src.get("file"))
    if key not in memo:
        res = {"status": "unchanged", "changes": [], "new_path": None, "commit": None, "text": None}
        try:
            if src.get("repo"):
                info = gl.head(src["repo"], src["path"])
                if info is None:
                    res["status"], res["new_path"], res["commit"] = gl.moved(src["repo"], src["path"])
                    res["changes"] = [f"{res['status']} upstream" + (f" in \"{res['commit']['title']}\"" if res["commit"] else "")]
                elif info["blob_id"] != src["blob_id"]:
                    res.update(status="changed", text=gl.raw(src["repo"], src["path"]), commit=gl.commit(src["repo"], src["path"]))
            else:
                f = Path(src["file"])
                if not f.exists():
                    res["status"] = "deleted"
                elif digests(f.read_text())[0] != src["sha256"]:
                    res.update(status="changed", text=f.read_text())
        except Exception as e:
            res.update(status="error", changes=[f"{type(e).__name__}: {e}"])
        memo[key] = res
    res = dict(memo[key])
    if res["status"] == "changed":
        try:
            res["changes"] = diff_summary(src["role"], src.get("summary"), summarize(src, res["text"])) or \
                ["content changed (pads, courtyard, pins and properties the same)"]
        except Exception as e:
            res["changes"] = [f"changed; summary failed: {type(e).__name__}: {e}"]
    return res


def check_kicad(man, gl):
    """-> (rows, per-entry worst status)"""
    rows, worst, memo = [], {}, {}
    for key, e in man["entries"].items():
        for src in e["sources"]:
            r = check_source(src, gl, memo)
            worst[key] = max(worst.get(key, "unchanged"), r["status"], key=RANK.get)
            rows.append({"entry": key, "kind": e["kind"], "kicad_ref": e["kicad_ref"], "role": src["role"],
                         "source": src.get("path") or src.get("file"), "status": r["status"],
                         "summary": "; ".join(r["changes"]) + (f"; now {r['new_path']}" if r["new_path"] else ""), "spec": e["spec"],
                         "upstream_commit": (r["commit"] or {}).get("id", "")[:12], "upstream_date": (r["commit"] or {}).get("date", ""),
                         "_text": r["text"], "_src": src})
    return rows, worst


class Pinned:
    """http wrapper for kicad_import: URLs in `pins` answer with that text (what check-kicad just fetched)."""
    def __init__(self, http, pins):
        self.http, self.pins = http, pins

    def get(self, url, **kw):
        return Response(200, self.pins[url]) if url in self.pins else self.http.get(url, **kw)


def reconvert(entries, out, http):
    """Re-run kicad_import for the specs behind `entries` (one conversion per spec); record() refreshes the manifest."""
    done = {}
    for k in ("PcbLib", "SchLib"):
        (Path(out) / k).mkdir(parents=True, exist_ok=True)
    for e in entries:
        spec = e["spec"]
        k = json.dumps(spec, sort_keys=True)
        if k in done:
            continue
        if spec.get("footprint"):
            rep = K.import_part(spec, out, http, e.get("step", False), gen_step=e.get("gen_step", False))
        else:                                  # symbol-only conversion (kicad_import symbol ...)
            rep = {"notes": [], "ok": False}
            try:
                chain, _ = K.load_symbol(spec["symbol"], http)
                sym = K.convert_symbol(chain, spec.get("symbol_as"), spec.get("symbol_footprint"),
                                       [tuple(kv.split("=", 1)) for kv in spec.get("params", [])], None, rep["notes"])
                rep["schlib"] = str(K.AL.write_schlib(Path(out) / "SchLib" / f"{K.AL.file_stem(sym['name'])}.SchLib", [sym]))
                rep["ok"] = True
                record(out, spec, rep, http)
            except Exception as ex:
                rep["error"] = f"{type(ex).__name__}: {ex}"
        done[k] = rep
    return done


# ---------------------------------------------------------------- check-a365
Q_COMPONENTS = """query ($ws: String!, $after: String) {
  desLibrary(workspaceUrl: $ws) {
    components(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id name comment description isManaged folder { path }
              revision { id details { lifeCycleState { name } createdAt } }
              details { parameters { name value } symbols { name guid } footprints { name guid } } }
    }
  }
}"""


def workspace_components(client, ws):
    out, after = [], None
    while True:
        conn = client.gql(Q_COMPONENTS, {"ws": ws, "after": after})["desLibrary"]["components"] or {}
        out += conn.get("nodes") or []
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return out
        after = page["endCursor"]


def _folder(p):
    return "/".join(x for x in (p or "").replace("\\", "/").split("/") if x).lower()


def check_a365(items, nodes, folders=None, state=None, rules=None):
    """plan items vs workspace nodes -> (rows, new state). state: {name: {revision, symbols, footprints}} from last run.
    rules: the plan's parameters are compared under the rules' house names, as push sends them."""
    from .altium365 import merged_params
    from .rules import house_item
    items = [house_item(i, rules) for i in items]
    by = {}
    for n in nodes:
        by.setdefault(n["name"], n)
    watch = {_folder(f) for f in (folders or [i["folder"] for i in items])}
    rows, new_state = [], {}
    row = lambda name, status, field="", plan="", ws="", detail="": rows.append(
        {"name": name, "status": status, "field": field, "plan": plan, "workspace": ws, "detail": detail})
    for it in items:
        n = by.get(it["name"])
        if not n:
            row(it["name"], "missing", detail=f"plan status {it.get('status', '')}"); continue
        d = n.get("details") or {}
        cur = {p["name"]: p["value"] for p in d.get("parameters") or []}
        if merged_params(n, it) is not None:
            for p in it["parameters"]:
                if cur.get(p["name"]) != p["value"]:
                    row(it["name"], "parameter", p["name"], p["value"], cur.get(p["name"], "(absent)"))
            for k in it.get("cleared", []):
                if k in cur:
                    row(it["name"], "parameter", k, "(cleared)", cur[k])
        for f in ("comment", "description"):
            if f in n and (it.get(f) or "") != (n[f] or ""):
                row(it["name"], f, f, it.get(f) or "", n[f] or "")
        if n.get("folder") and _folder(n["folder"].get("path")) != _folder(it["folder"]):
            row(it["name"], "folder", "folder", it["folder"], n["folder"].get("path"))
        for kind, key in (("symbol", "symbols"), ("footprint", "footprints")):
            names = [x["name"] for x in d.get(key) or []]
            ref = (it.get(kind) or {}).get("ref")
            if ref and names and ref not in names:
                row(it["name"], kind, kind, ref, ", ".join(names))
        rev = n.get("revision") or {}
        snap = {"revision": rev.get("id"), "lifecycle": ((rev.get("details") or {}).get("lifeCycleState") or {}).get("name"),
                "symbols": sorted(x.get("guid") or "" for x in d.get("symbols") or []),
                "footprints": sorted(x.get("guid") or "" for x in d.get("footprints") or [])}
        new_state[it["name"]] = snap
        old = (state or {}).get(it["name"])
        if old:
            for k in ("revision", "symbols", "footprints"):
                if old.get(k) != snap[k]:
                    row(it["name"], "revision", k, str(old.get(k)), str(snap[k]), f"changed in the workspace since the last check"
                        + (f" (lifecycle {snap['lifecycle']})" if snap["lifecycle"] else ""))
    planned = {i["name"] for i in items}
    for n in nodes:
        f = _folder((n.get("folder") or {}).get("path"))
        if n["name"] not in planned and any(f == w or f.startswith(w + "/") for w in watch):
            row(n["name"], "not_in_plan", "folder", "", (n.get("folder") or {}).get("path"),
                "managed" if n.get("isManaged") else "unmanaged")
    return rows, new_state


# ---------------------------------------------------------------- reports & CLI
def write_report(rows, out, title, cols, meta=()):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out.with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    esc = lambda v: str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")
    md = [f"# {title}", "", *meta, ""]
    if rows:
        md += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(esc(r.get(c)) for c in cols) + " |" for r in rows]
    else:
        md.append("No changes.")
    out.with_suffix(".md").write_text("\n".join(md) + "\n")


KICAD_COLS = ["entry", "kind", "kicad_ref", "role", "source", "status", "summary", "upstream_commit", "upstream_date", "update"]
A365_COLS = ["name", "status", "field", "plan", "workspace", "detail"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Upstream change alerts for KiCad imports and Altium 365 workspaces")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("check-kicad", help="compare imported KiCad parts with gitlab.com/kicad/libraries")
    k.add_argument("manifest", nargs="?", default="out/altium_models_kicad/manifest.json")
    k.add_argument("--update", action="store_true", help="reconvert parts whose sources changed")
    k.add_argument("--all", action="store_true", help="list unchanged sources in the report too")
    k.add_argument("--cache", default="out/http_cache")
    k.add_argument("--offline", action="store_true", help="--update reads unchanged sources from the HTTP cache only")
    k.add_argument("-o", "--out", default="out/upstream_kicad", help="report path stem (.md and .csv)")
    a3 = sub.add_parser("check-a365", help="compare a plan.json with an Altium 365 workspace (NEXAR_TOKEN)")
    a3.add_argument("plan")
    a3.add_argument("--workspace", required=True)
    a3.add_argument("--folder", action="append", help="workspace folder to look for unplanned components (default: the plan's)")
    a3.add_argument("--state", help="JSON snapshot of revisions/GUIDs; compared, then rewritten")
    a3.add_argument("-o", "--out", default="out/upstream_a365", help="report path stem (.md and .csv)")
    a3.add_argument("--rules", help="library rules file: house parameter names (default: ecadlib.toml found from the plan)")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if a.cmd == "check-kicad":
        try:
            man = json.loads(Path(a.manifest).read_text())
            rows, worst = check_kicad(man, GitLab(token=os.environ.get("GITLAB_TOKEN")))
        except Exception as e:
            print(f"check-kicad: {type(e).__name__}: {e}", file=sys.stderr); return 2
        if a.update:
            ch = [r for r in rows if r["status"] == "changed"]
            todo = {r["entry"] for r in ch}
            if todo:
                pins = {r["_src"]["url"]: r["_text"] for r in ch if r["_src"].get("url") and r["_text"] is not None}
                done = reconvert([man["entries"][e] for e in sorted(todo)], Path(a.manifest).parent,
                                 Pinned(CachedHTTP(a.cache, ttl_s=0, offline=a.offline, timeout=60), pins))
                for r in rows:                 # every entry of a reconverted spec (footprint and its symbol) was rewritten
                    rep = done.get(json.dumps(r["spec"], sort_keys=True))
                    if rep:
                        r["update"] = "reconverted" if rep.get("ok") else f"failed: {rep.get('error')}"
        shown = rows if a.all else [r for r in rows if r["status"] != "unchanged"]
        counts = {s: sum(v == s for v in worst.values()) for s in RANK}
        write_report(shown, a.out, "KiCad upstream changes", KICAD_COLS,
                     [f"Manifest `{a.manifest}`, checked {now}.", "",
                      "Parts: " + ", ".join(f"{v} {s}" for s, v in counts.items() if v)])
        for r in shown:
            print(f"  {r['status']:<9} {r['entry']:<32} {r['source']:<56} {r['summary']}" + (f"  [{r['update']}]" if r.get("update") else ""))
        print("  ".join(f"{v} {s}" for s, v in counts.items() if v) + f"   -> {Path(a.out).with_suffix('.md')} and .csv")
        return 2 if counts["error"] else 1 if any(counts[s] for s in ("changed", "renamed", "deleted")) else 0

    token = os.environ.get("NEXAR_TOKEN")
    if not token:
        print("check-a365: set NEXAR_TOKEN (design.domain, user.access, openid)", file=sys.stderr); return 2
    try:
        from .altium365 import Nexar
        items = json.loads(Path(a.plan).read_text())
        state = json.loads(Path(a.state).read_text()) if a.state and Path(a.state).exists() else None
        from .rules import load
        rules = load(a.rules, start=Path(a.plan).parent)
        rows, new_state = check_a365(items, workspace_components(Nexar(token), a.workspace), a.folder, state, rules)
    except Exception as e:
        print(f"check-a365: {type(e).__name__}: {e}", file=sys.stderr); return 2
    if a.state:
        Path(a.state).parent.mkdir(parents=True, exist_ok=True)
        Path(a.state).write_text(json.dumps(new_state, indent=1))
    write_report(rows, a.out, "Altium 365 drift", A365_COLS, [f"Plan `{a.plan}` vs `{a.workspace}`, checked {now}."])
    for r in rows:
        print(f"  {r['status']:<11} {r['name']:<28} {r['field']:<24} plan={r['plan']!r} workspace={r['workspace']!r}")
    print(f"{len(rows)} differences   -> {Path(a.out).with_suffix('.md')} and .csv")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
