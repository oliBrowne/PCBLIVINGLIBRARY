"""Library agent, WATCH step: find parts that are new, changed or removed in the projects and BOMs you watch.

    ecadlib agent-watch [--workspace DIR] [--lib DIR ...] [--source TYPE:REF ...] [--dry-run] [--json]

Sources (rules [agent.watch] sources, or --source / the `sources` argument):
    {type = "bom_file",       ref = "boms/main.csv"}             one CSV / TSV / XLSX BOM (bom_triage reads it)
    {type = "folder",         ref = "uploads"}                   every BOM (and .PrjPcb) under a folder; the web UI's
                                                                 uploads/<time>-<random>/<name> layout counts the newest
                                                                 upload of each file name as that BOM's current version
    {type = "altium_project", ref = "boards/Main/Main.PrjPcb"}   BOM straight from the schematics (altium_project.py)
    {type = "a365_project",   ref = "Main board", workspace = "https://acme.365.altium.com", variant = ""}
                                                                 an Altium 365 project's BOM through Nexar (NEXAR_TOKEN)
Optional per source: name, include / exclude (globs, folder only), variant (altium_project / a365_project).
Relative refs are relative to the workspace. include / exclude in [agent.watch] apply to every folder.

A scan reads every source, compares its parts (key = "manufacturer|MPN") with what the last scan saw
(<workspace>/agent/watch_state.json) and returns Change records:
    new      a part that appeared and is NOT already in the workspace library (the KiCad index the triage uses, the
             librarian plans' ready parts, Altium 365 components pulled into a plan or an a365_state.json)
    changed  a part whose footprint, value, datasheet or LCSC number changed in the BOM
    removed  a part no longer in the source
Scans are idempotent (a second scan with nothing changed returns []), files whose size and time are unchanged are
not read again (huge BOMs), and a source that fails is recorded in the state with its error while its last good parts
are kept, so nothing is reported as removed because a file was briefly unreadable or Nexar was down. A source whose
ref changed (file renamed, rules edited) takes over the old state when the content is the same (same file hash, same
folder files, same Altium 365 project id), so a rename is not reported as every part removed and added.

Altium 365 (UNVERIFIED LIVE: api.nexar.com is blocked from the sandbox this was built in): the queries below are
validated against tests/nexar/schema.graphql (Nexar schema as of 2024-07) and follow the shapes of Nexar's own demos
(desProjects(workspaceUrl, first, after) paging; design.variants -> bom -> items -> bomItemInstances). Assumptions:
DesProject.updatedAt (plus latestRevision.revisionId) moves when the design is saved, so an unchanged project is not
re-read; a project without variants named in the source uses its first variant; DesComponent.details is allowed inside a
BOM item (the schema notes it is heavier than desComponentById)."""
import argparse, contextlib, datetime, fnmatch, hashlib, json, os, re, sys
from pathlib import Path

TYPES = ("bom_file", "folder", "altium_project", "a365_project")
WATCH_FIELDS = ("footprint", "value", "datasheet", "lcsc")          # a difference here makes a part "changed"
LINE_FIELDS = ("mpn", "manufacturer", "value", "footprint", "description", "designator", "qty", "datasheet", "lcsc",
               "libref")
BOM_EXT = (".csv", ".tsv", ".xlsx", ".xlsm", ".xls")
PROJECT_EXT = (".prjpcb",)
UPLOAD_DIR = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")                  # app.Workspace.save_upload's folder names
PLAN_LIBRARY_STATUS = ("ready", "existing")                         # plan items that are library parts
STATE_VERSION = 1
PAGE = 100

# ------------------------------------------------------------------ Nexar (Altium 365); UNVERIFIED LIVE, see module doc
Q_PROJECTS = """query ($ws: String!, $first: Int!, $after: String) {
  desProjects(workspaceUrl: $ws, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id projectId name url updatedAt }
  }
}"""

Q_PROJECT = """query ($id: ID!) {
  desProjectById(id: $id) {
    id projectId name url updatedAt
    latestRevision { revisionId }
    design { variants { name } }
  }
}"""

Q_BOM = """query ($id: ID!, $variant: String!, $first: Int!, $after: String) {
  desProjectById(id: $id) {
    design {
      variants(where: {name: {eq: $variant}}) {
        name
        bom {
          items(first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              quantity
              bomItemInstances { designator isFitted }
              component {
                id name comment description
                manufacturerParts { companyName partNumber priority }
                details { parameters { name value } footprints { name } }
              }
            }
          }
        }
      }
    }
  }
}"""


def now_iso(now=None):
    t = now or datetime.datetime.now(datetime.timezone.utc)
    if isinstance(t, str):
        return t
    return t.astimezone(datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def part_key(manufacturer, mpn):
    return f"{str(manufacturer or '').strip().lower()}|{str(mpn or '').strip().upper()}"


# ------------------------------------------------------------------ BOM lines -> parts
def line_key(ln):
    """(key, mpn) of a BOM line; None for a line with nothing to identify it. A line without an MPN uses the part
    number the triage would use (an IC's Comment, a non-generic LibRef); a bare passive is keyed by value and footprint."""
    from .bom_triage import part_numbers
    pns = part_numbers(ln)
    mpn = (ln.get("mpn") or "").strip() or (pns[0] if pns else "")
    if mpn:
        return part_key(ln.get("manufacturer"), mpn), mpn
    v, fp = (ln.get("value") or "").strip(), (ln.get("footprint") or "").strip()
    if v or fp:
        return part_key(ln.get("manufacturer"), f"{v} {fp}".strip()), ""
    return None, ""


def _qty(v):
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


def parts_from_lines(lines, extra=None):
    """{key: line} with the same part on several lines merged (designators joined, quantities added)."""
    out = {}
    for ln in lines:
        key, mpn = line_key(ln)
        if not key:
            continue
        rec = {f: str(ln.get(f) or "").strip() for f in LINE_FIELDS if str(ln.get(f) or "").strip()}
        rec["mpn"] = mpn
        if ln.get("dnp") is True:
            rec["dnp"] = True
        rec.update(extra or {})
        prev = out.get(key)
        if prev is None:
            out[key] = rec
            continue
        if rec.get("designator"):
            prev["designator"] = ", ".join(x for x in (prev.get("designator"), rec["designator"]) if x)
        if prev.get("qty") or rec.get("qty"):
            prev["qty"] = str(_qty(prev.get("qty")) + _qty(rec.get("qty")))
        for f in LINE_FIELDS:
            if rec.get(f) and not prev.get(f):
                prev[f] = rec[f]
        if prev.get("dnp") and not rec.get("dnp"):
            prev.pop("dnp")
    return out


def read_bom_parts(path, rules):
    from . import bom_triage as T, rules as RU
    lines, _ = T.read_bom(path, RU.bom_columns(rules), rules)
    return parts_from_lines(lines)


def _project_parts(path, rules, variant=None):
    from .altium_project import bom_lines
    with contextlib.redirect_stderr(open(os.devnull, "w")):       # project warnings go to the scan's own report
        lines, _ = bom_lines(path, variant=variant or None, rules=rules)
    return parts_from_lines(lines)


# ------------------------------------------------------------------ is it already in the workspace library?
class Library:
    """What the workspace already has, built on first use: the KiCad index triage uses (--lib folders + approved
    footprints), the librarian plans' ready / existing parts and the Altium 365 components pulled or snapshotted."""

    def __init__(self, root, libs=(), rules=None):
        self.root, self.libs, self.rules = Path(root), list(libs), rules
        self._index = self._keys = self._idx = None

    def _plan_keys(self):
        if self._keys is None:
            from .a365_sync import mpn_of
            from .dedupe import mpn_key
            keys = set()
            plans = list(self.root.glob("librarian/*/plan.json")) + list(self.root.glob("altium365/*plan*.json"))
            for f in plans:
                try:
                    items = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                items = items.get("items", []) if isinstance(items, dict) else items
                for it in items if isinstance(items, list) else []:
                    if isinstance(it, dict) and it.get("status") in PLAN_LIBRARY_STATUS:
                        keys |= {mpn_key(x) for x in (mpn_of(it, self.rules), it.get("name")) if x}
            for f in self.root.glob("**/a365_state.json"):
                try:
                    data = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                for name, snap in (data.items() if isinstance(data, dict) else []):
                    par = (snap or {}).get("parameters") if isinstance(snap, dict) else None
                    keys |= {mpn_key(x) for x in (name, mpn_of(par or {}, self.rules) if par else "") if x}
            keys.discard("")
            self._keys = keys
        return self._keys

    def index(self):
        if self._index is None:
            from .kicad_index import KiCadIndex, norm_mpn
            self._index = KiCadIndex(self.libs + [str(self.root / "footprints")])
            mpns = {norm_mpn(x) for s in self._index.symbols for x in (s.name, s.mpn) if x}
            self._idx = (mpns, {s.lcsc.upper() for s in self._index.symbols if s.lcsc})
        return self._index

    def has(self, line):
        from . import bom_triage as T
        from .dedupe import mpn_key
        from .kicad_index import norm_mpn, parse_value
        pns = T.part_numbers(line)
        keys = self._plan_keys()
        if any(mpn_key(p) in keys for p in pns):
            return True
        index = self.index()
        mpns, lcscs = self._idx
        # index.match finds a symbol by MPN, LCSC or (passives) value + package; only those can be in_library, so the
        # triage (one pass over every symbol) runs only for lines that can match: fast on huge BOMs.
        if not (any(norm_mpn(p) in mpns for p in pns) or (line.get("lcsc") or "").upper() in lcscs - {""}
                or parse_value(line.get("value") or "") is not None):
            return False
        ln = {k: v for k, v in line.items() if k != "dnp"}
        row = T.triage([ln], index, [], None)[0]
        return row["status"].startswith("in_library")


# ------------------------------------------------------------------ sources
def norm_source(src, root):
    """A source dict (or a bare path) -> {type, ref, name, ...} with the path resolved; raises ValueError."""
    if isinstance(src, str):
        ref = src.strip()
        p = _path(ref, root)
        typ = ("folder" if p.is_dir() else "altium_project" if p.suffix.lower() in PROJECT_EXT + (".schdoc", ".bomdoc")
               else "bom_file")
        src = {"type": typ, "ref": ref}
    if not isinstance(src, dict):
        raise ValueError(f"watch source {src!r}: expected {{type, ref}}")
    typ, ref = src.get("type"), str(src.get("ref") or "").strip()
    if typ not in TYPES:
        raise ValueError(f"watch source {src!r}: type must be one of {', '.join(TYPES)}")
    if not ref:
        raise ValueError(f"watch source {src!r}: ref is empty")
    out = dict(src, type=typ, ref=ref)
    out.setdefault("name", ref if typ == "a365_project" else Path(ref).name or ref)
    return out


def source_id(src):
    if src["type"] == "a365_project":
        return f"a365_project:{(src.get('workspace') or '').rstrip('/')}#{src['ref']}"
    return f"{src['type']}:{src['ref']}"


def _path(ref, root):
    p = Path(ref).expanduser()
    return p if p.is_absolute() else Path(root) / p


def _file_sig(p, old=None):
    """{size, mtime_ns, sha1}; sha1 is only recomputed when size or time moved."""
    st = p.stat()
    sig = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if old and old.get("size") == sig["size"] and old.get("mtime_ns") == sig["mtime_ns"] and old.get("sha1"):
        return dict(sig, sha1=old["sha1"])
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return dict(sig, sha1=h.hexdigest())


def _same_file(a, b):
    return bool(a and b) and a.get("sha1") == b.get("sha1") and a.get("size") == b.get("size")


def _globs(src, cfg):
    inc = list(src.get("include") or []) + list(cfg.get("include") or [])
    exc = list(src.get("exclude") or []) + list(cfg.get("exclude") or [])
    return inc, exc


def _match(rel, pats):
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in pats)


def folder_files(folder, include=(), exclude=()):
    """{identity: path} of the BOMs / projects under folder. In an uploads/<stamp>-<rand>/<name> layout the identity
    is the file name and only the newest upload of each name counts (a re-upload is a new version of that BOM)."""
    folder = Path(folder)
    found = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.is_symlink() or p.suffix.lower() not in BOM_EXT + PROJECT_EXT:
            continue
        rel = p.relative_to(folder).as_posix()
        if any(part.startswith(".") for part in rel.split("/")) or p.name.startswith("~$"):   # hidden, Excel lock files
            continue
        if include and not _match(rel, include):
            continue
        if exclude and _match(rel, exclude):
            continue
        up = UPLOAD_DIR.fullmatch(p.parent.name)
        ident = p.name if up else rel
        if ident in found and up and found[ident][0] > p.parent.name:
            continue
        found[ident] = (p.parent.name if up else "", p)
    return {k: v[1] for k, v in sorted(found.items())}


def _project_sig(p):
    """Size and time of the .PrjPcb and every document it names: any saved sheet re-reads the project."""
    from .altium_project import read_prjpcb
    files = [p]
    if p.suffix.lower() in PROJECT_EXT:
        try:
            prj = read_prjpcb(p)
            files += [Path(x) for x in list(prj["schdocs"]) + list(prj["bomdocs"])]
        except (OSError, ValueError):
            pass
    sig = []
    for f in files:
        try:
            st = f.stat()
            sig.append([f.name, st.st_size, st.st_mtime_ns])
        except OSError:
            sig.append([f.name, None, None])
    return {"docs": sig}


def scan_files(src, root, old_files, rules, cfg):
    """(files {ident: {path, sig, parts}}, file errors [str]) for bom_file / folder / altium_project. A file that can't
    be read keeps its old entry (its parts are not reported as removed) and adds an error."""
    p = _path(src["ref"], root)
    typ = src["type"]
    if typ == "folder":
        if not p.is_dir():
            raise FileNotFoundError(f"{src['ref']}: folder not found")
        inc, exc = _globs(src, cfg)
        todo = folder_files(p, inc, exc)
    else:
        if not p.is_file():
            raise FileNotFoundError(f"{src['ref']}: file not found")
        todo = {"": p}
    files, errors = {}, []
    for ident, f in todo.items():
        old = (old_files or {}).get(ident) or {}
        project = typ == "altium_project" or f.suffix.lower() in PROJECT_EXT
        try:
            sig = _project_sig(f) if project else _file_sig(f, old.get("sig"))
            if old.get("sig") == sig or (not project and _same_file(old.get("sig"), sig)):
                files[ident] = dict(old, path=str(f), sig=sig)              # unchanged: not parsed again
                continue
            parts = _project_parts(f, rules, src.get("variant")) if project else read_bom_parts(f, rules)
            files[ident] = {"path": str(f), "sig": sig, "parts": parts}
        except Exception as e:
            errors.append(f"{ident or f.name}: {type(e).__name__}: {e}")
            if old:
                files[ident] = old
    return files, errors


# ------------------------------------------------------------------ Altium 365 through Nexar
def _paged(client, query, variables, conn_of):
    after, out = None, []
    while True:
        conn = conn_of(client.gql(query, dict(variables, after=after))) or {}
        out += conn.get("nodes") or []
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage") or not pi.get("endCursor") or pi.get("endCursor") == after:
            return out
        after = pi["endCursor"]


def a365_projects(client, ws):
    return _paged(client, Q_PROJECTS, {"ws": ws, "first": PAGE}, lambda d: d.get("desProjects"))


def find_project(client, ws, ref, known_id=None):
    """The project node for a source: by the id the state remembers (survives a rename), else by node id, projectId,
    name (case-insensitive) or URL."""
    projects = a365_projects(client, ws)
    if known_id:
        hit = next((p for p in projects if p.get("id") == known_id), None)
        if hit:
            return hit
    r = ref.strip()
    for test in (lambda p: r in (p.get("id"), p.get("projectId"), p.get("url")),
                 lambda p: (p.get("name") or "").strip().lower() == r.lower()):
        hits = [p for p in projects if test(p)]
        if len(hits) > 1:
            raise ValueError(f"{len(hits)} Altium 365 projects match {ref!r}; use its id or URL as ref")
        if hits:
            return hits[0]
    raise LookupError(f"Altium 365 project {ref!r} not found in {ws}")


def _params(comp):
    return {p.get("name"): p.get("value") or "" for p in ((comp.get("details") or {}).get("parameters") or [])}


def bom_item_line(item, rules=None):
    """A Nexar DesBomItem -> a BOM line (the fields read_bom gives for a CSV)."""
    from .a365_sync import MPN_NAMES, mpn_of
    from .altium_project import _datasheet
    comp = item.get("component") or {}
    par = _params(comp)
    mps = sorted((m for m in comp.get("manufacturerParts") or [] if (m.get("partNumber") or "").strip()),
                 key=lambda m: m.get("priority") if isinstance(m.get("priority"), int) else 1 << 30)
    mp = mps[0] if mps else {}
    named = next((par[k] for k in MPN_NAMES if (par.get(k) or "").strip()), "") or (mpn_of(par, rules) if par else "")
    mpn = (mp.get("partNumber") or "").strip() or named or comp.get("name") or ""
    inst = item.get("bomItemInstances") or []
    fitted = [i for i in inst if i.get("isFitted", True)]
    fps = (comp.get("details") or {}).get("footprints") or []
    line = {"mpn": mpn, "manufacturer": (mp.get("companyName") or par.get("Manufacturer") or "").strip(),
            "value": par.get("Value") or comp.get("comment") or "", "footprint": fps[0].get("name", "") if fps else "",
            "description": comp.get("description") or "", "datasheet": _datasheet(par),
            "designator": ", ".join(i.get("designator", "") for i in inst if i.get("designator")),
            "qty": str(item.get("quantity") if item.get("quantity") is not None else len(fitted)),
            "lcsc": next((v for k, v in par.items() if k and k.strip().lower() in ("lcsc", "lcsc part", "lcsc part number")), ""),
            "a365_component": comp.get("name") or ""}
    if line["value"].startswith("="):                    # Altium's "=Value" comment: the parameter, not text
        line["value"] = par.get(line["value"][1:], "")
    if inst and not fitted:
        line["dnp"] = True
    return line


def scan_a365(src, ws, client, old, rules):
    """(files, errors, meta) for one Altium 365 project. Its BOM is only fetched when updatedAt / the latest revision
    moved since the last scan."""
    if not ws:
        raise ValueError("no Altium 365 workspace: set workspace on the source or [altium365] workspace in the rules")
    if client is None:
        tok = os.environ.get("NEXAR_TOKEN")
        if not tok:
            raise ValueError("NEXAR_TOKEN is not set (a Nexar token with the design.domain, user.access, openid scopes)")
        from .altium365 import Nexar
        client = Nexar(tok)
    meta_old = (old or {}).get("a365") or {}
    proj = find_project(client, ws, src["ref"], meta_old.get("id"))
    full = client.gql(Q_PROJECT, {"id": proj["id"]})["desProjectById"] or {}
    variants = [v.get("name") for v in ((full.get("design") or {}).get("variants") or []) if v.get("name") is not None]
    variant = src.get("variant") or (variants[0] if variants else "")
    if src.get("variant") and src["variant"] not in variants:
        raise LookupError(f"{proj.get('name')}: no variant {src['variant']!r} (has: {', '.join(map(str, variants)) or 'none'})")
    sig = {"updatedAt": full.get("updatedAt") or proj.get("updatedAt"),
           "revision": (full.get("latestRevision") or {}).get("revisionId"), "variant": variant}
    meta = {"id": proj["id"], "projectId": proj.get("projectId"), "name": proj.get("name") or src["ref"],
            "url": proj.get("url"), "variant": variant}
    old_f = ((old or {}).get("files") or {}).get(proj["id"])
    if old_f and old_f.get("sig") == sig:
        return {proj["id"]: old_f}, [], meta

    def conn_of(d):
        vs = ((d.get("desProjectById") or {}).get("design") or {}).get("variants") or []
        return ((vs[0].get("bom") or {}).get("items") if vs else None) or {}
    items = _paged(client, Q_BOM, {"id": proj["id"], "variant": variant, "first": PAGE}, conn_of)
    parts = parts_from_lines([bom_item_line(i, rules) for i in items])
    return {proj["id"]: {"path": proj.get("url") or "", "sig": sig, "parts": parts}}, [], meta


# ------------------------------------------------------------------ state
def state_path(root):
    return Path(root) / "agent" / "watch_state.json"


def load_state(root):
    f = state_path(root)
    if not f.exists():
        return {"version": STATE_VERSION, "sources": {}}
    data = json.loads(f.read_text(encoding="utf-8"))
    data.setdefault("sources", {})
    return data


def save_state(root, state):
    f = state_path(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, f)


@contextlib.contextmanager
def _lock(root):
    f = Path(root) / "agent" / "watch_state.lock"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as h:
        try:
            import fcntl
        except ImportError:                              # Windows: no advisory lock, single-writer use
            yield
            return
        fcntl.flock(h, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(h, fcntl.LOCK_UN)


def merged_parts(files):
    """One {key: line} over a source's files (sorted by identity, first file wins; line.file names it)."""
    out = {}
    for ident in sorted(files or {}):
        for k, ln in ((files[ident] or {}).get("parts") or {}).items():
            if k not in out:
                out[k] = dict(ln, file=ident) if ident else ln
    return out


def _adopt(state, sid, src, new_files, meta, active):
    """An old state entry for the same content under another ref (renamed file or folder, edited rules, renamed
    Altium 365 project): its id, or None."""
    for osid, ent in state["sources"].items():
        if osid in active or ent.get("type") != src["type"]:
            continue
        of = ent.get("files") or {}
        if src["type"] == "a365_project":
            if meta and (ent.get("a365") or {}).get("id") == meta.get("id"):
                return osid
        elif src["type"] == "folder":
            if of and set(of) == set(new_files) and all(
                    _same_file((of[i] or {}).get("sig"), (new_files[i] or {}).get("sig")) for i in of):
                return osid
        elif "" in of and "" in new_files and (
                _same_file(of[""].get("sig"), new_files[""].get("sig")) or of[""].get("sig") == new_files[""].get("sig")):
            return osid
    return None


def _change(kind, key, line, was, src, when):
    ln = line if kind != "removed" else was
    # a line without an MPN (a bare passive) is keyed "|VALUE FOOTPRINT": the Change's mpn says the same, so the key
    # stays manufacturer|MPN (agent_queue refuses an item without an MPN); line.mpn stays empty for the builder
    mpn = ln.get("mpn") or " ".join(x for x in (str(ln.get("value") or "").strip(), str(ln.get("footprint") or "").strip()) if x)
    return {"key": key, "mpn": mpn, "manufacturer": ln.get("manufacturer", ""), "kind": kind,
            "source": {"type": src["type"], "ref": src["ref"], "name": src.get("name") or src["ref"]},
            "line": dict(line or {}), "was": dict(was or {}), "detected": when}


def diff(old, new, src, when, library=None):
    """Change records between two {key: line} maps."""
    out = []
    for k, ln in new.items():
        prev = old.get(k)
        if prev is None:
            if library is None or not library.has(ln):
                out.append(_change("new", k, ln, {}, src, when))
        elif any((ln.get(f) or "") != (prev.get(f) or "") for f in WATCH_FIELDS):
            out.append(_change("changed", k, ln, prev, src, when))
    out += [_change("removed", k, {}, ln, src, when) for k, ln in old.items() if k not in new]
    return out


# ------------------------------------------------------------------ scan
def watch_rules(rules):
    return dict(((rules or {}).get("agent") or {}).get("watch") or {})


def _workspace_parts(workspace, rules):
    if not isinstance(workspace, (str, os.PathLike)) and hasattr(workspace, "rules"):     # a Workspace
        r = rules if rules is not None else workspace.rules()
        return Path(workspace.root), list(getattr(workspace, "libs", []) or []), r
    from . import rules as RU
    root = Path(workspace).resolve()
    return root, [], RU.resolve(rules, root)


def scan_report(workspace, rules=None, sources=None, client=None, now=None, dry_run=False, libs=None):
    """scan() plus what happened per source: {changes, errors: [{source, error}], sources: [{id, type, ref, name,
    status, parts, error}], state}. dry_run computes the changes without writing the state."""
    root, wlibs, rules = _workspace_parts(workspace, rules)
    cfg = watch_rules(rules)
    when = now_iso(now)
    srcs = [norm_source(s, root) for s in (sources if sources is not None else cfg.get("sources") or [])]
    library = Library(root, list(libs if libs is not None else wlibs), rules)
    changes, errors, summary = [], [], []
    with _lock(root):
        state = load_state(root)
        active = {source_id(s) for s in srcs}
        for src in srcs:
            if src["type"] == "a365_project":
                src = dict(src, workspace=src.get("workspace") or (rules.get("altium365") or {}).get("workspace") or "")
            sid = source_id(src)
            ent = state["sources"].get(sid)
            meta, ferrs = None, []
            try:
                if src["type"] == "a365_project":
                    files, ferrs, meta = scan_a365(src, src["workspace"], client, ent, rules)
                    src = dict(src, name=src.get("name") if src.get("name") != src["ref"] else meta["name"])
                else:
                    files, ferrs = scan_files(src, root, (ent or {}).get("files"), rules, cfg)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                errors.append({"source": sid, "error": msg})
                ent = state["sources"].setdefault(sid, {"type": src["type"], "ref": src["ref"], "name": src.get("name"),
                                                        "files": {}, "first_seen": when})
                ent.update(last_scan=when, error=msg, error_since=ent.get("error_since") or when,
                           errors=int(ent.get("errors") or 0) + 1)
                summary.append({"id": sid, "type": src["type"], "ref": src["ref"], "name": src.get("name"),
                                "status": "error", "error": msg, "parts": len(merged_parts(ent.get("files")))})
                continue
            adopted = None
            if ent is None:
                adopted = _adopt(state, sid, src, files, meta, active)
                if adopted:
                    ent = state["sources"].pop(adopted)
                    ent["renamed_from"] = ent.get("ref") or adopted
            old_parts = merged_parts((ent or {}).get("files"))
            new_parts = merged_parts(files)
            found = diff(old_parts, new_parts, src, when, library)
            changes += found
            for m in ferrs:
                errors.append({"source": sid, "error": m})
            ent = dict(ent or {"first_seen": when}, type=src["type"], ref=src["ref"], name=src.get("name"), files=files,
                       last_scan=when, last_ok=when, error=("; ".join(ferrs) or None), errors=len(ferrs))
            ent.pop("error_since", None)
            if ferrs:
                ent["error_since"] = when
            if meta:
                ent["a365"] = meta
            state["sources"][sid] = ent
            summary.append({"id": sid, "type": src["type"], "ref": src["ref"], "name": src.get("name"),
                            "status": "partial" if ferrs else "ok", "error": ent["error"], "parts": len(new_parts),
                            "files": len(files), "changes": len(found), **({"renamed_from": adopted} if adopted else {})})
        state["version"], state["last_scan"] = STATE_VERSION, when
        if not dry_run:
            save_state(root, state)
    return {"changes": changes, "errors": errors, "sources": summary, "dry_run": bool(dry_run),
            "state": str(state_path(root))}


def scan(workspace, rules=None, sources=None, client=None, now=None, dry_run=False):
    """Change records for every watched source (see module doc). workspace: a Workspace or its folder."""
    return scan_report(workspace, rules, sources, client, now, dry_run)["changes"]


# ------------------------------------------------------------------ CLI
def _parse_source(s):
    typ, sep, ref = s.partition(":")
    if sep and typ in TYPES:
        return {"type": typ, "ref": ref}
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib agent-watch", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default="ecadlib_workspace", help="workspace folder (state in <workspace>/agent/)")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library folder(s) the library check uses")
    ap.add_argument("--source", action="append", default=[],
                    help="TYPE:REF (bom_file, folder, altium_project, a365_project) or a path; replaces the rules' sources")
    ap.add_argument("--rules", help="rules file (default: <workspace>/ecadlib.toml, then the usual search)")
    ap.add_argument("--dry-run", action="store_true", help="report changes without saving the watch state")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    a = ap.parse_args(argv)
    from . import rules as RU
    root = Path(a.workspace).resolve()
    rules = RU.load(a.rules) if a.rules else RU.load(None, root)
    rep = scan_report(root, rules, [_parse_source(s) for s in a.source] or None, dry_run=a.dry_run,
                      libs=[str(Path(x).resolve()) for x in a.lib])
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        if not rep["sources"]:
            print("no watch sources: add [agent.watch] sources to ecadlib.toml or pass --source")
        for s in rep["sources"]:
            print(f"{s['status']:8} {s['type']:15} {s['name']}: {s['parts']} parts"
                  + (f", {s.get('changes', 0)} changes" if s["status"] != "error" else f" (kept) - {s['error']}"))
        for c in rep["changes"]:
            diff = ", ".join("%s: %r -> %r" % (f, c["was"].get(f, ""), c["line"].get(f, ""))
                             for f in WATCH_FIELDS if c["was"].get(f, "") != c["line"].get(f, ""))
            print(f"  {c['kind']:8} {c['mpn'] or c['key']:30} {c['source']['name']}"
                  + (f"  ({diff})" if c["kind"] == "changed" else ""))
        print(f"{len(rep['changes'])} change(s)" + (" (dry run: state not saved)" if a.dry_run else f"; state {rep['state']}"))
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
