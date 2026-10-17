"""Library hub: your whole parts library across tools, every part once, with a column per tool.

    ecadlib hub [--workspace W] [--lib DIR ...] [--have altium --lack kicad] [--state different,behind] [--json]
    (or: the Library page of `ecadlib ui`, or the MCP tools library_hub / library_hub_part)

What it reads (nothing is written to your libraries; the hub keeps its own notes in <workspace>/hub/):
  ecadlib's own parts   every librarian plan in the workspace (<workspace>/librarian/*/plan.json): the Altium
                        .SchLib / .PcbLib each part was built into, the KiCad library kicad-dblib wrote for that BOM
                        (<workspace>/kicad/<bom>/), and the Altium 365 component it was pushed to or pulled from.
  your libraries        the --lib folders plus [hub] libraries in ecadlib.toml: .SchLib / .PcbLib, .DbLib / .SvnDbLib
                        (with their database), .kicad_sym, .pretty/*.kicad_mod, .kicad_dbl, and Altium 365 component
                        lists (a365 pull's workspace_plan.json, check-a365 --state files). Same readers as `ecadlib
                        init`, dedupe and where-used. Files inside the workspace are read through the plans instead.
  other tools           Fusion (Eagle .lbr) and EasyEDA Pro columns fill in once their reader is registered with
                        register_tool(); until then the column says "no reader yet".

One row per part: copies are grouped by normalised MPN (dedupe.mpn_key: packaging suffixes stripped), or by name
when a copy has no MPN. Each tool's cell is
  missing    no copy in that tool
  same       the copy's pads, pins and value match the newest copy that has them
  different  something differs from the newest copy (the detail says what: pad count, a pad's size, a pin name, the value)
  behind     different, and last changed [hub] stale_days (default 14) or more before the newest copy: "KiCad is
             3 weeks behind Altium"
  present    there, but nothing to compare it with (an Altium 365 component without downloaded files)

Last updated / by: the newest change of each copy: a git commit when the file is in a git repository and has no
uncommitted change (author and date), the Altium 365 revision date, else the file's own date ("by" is then whoever
ecadlib recorded doing it through the web app, or "not recorded"). Each scan is compared with the last one
(<workspace>/hub/state.json); added, changed and removed copies go into <workspace>/hub/history.jsonl together with
what people did through the web app (fill, pick, push, fix). A part's history also shows its sign-offs and Altium 365
syncs. Where-used reads the Altium projects under [hub] projects; lifecycle comes from upkeep's lifecycle checks and
Altium 365's lifecycle state.

Settings ([hub] in ecadlib.toml): libraries = ["D:/Altium/Libraries", ...], projects = ["D:/Projects"],
stale_days = 14."""
import argparse, contextlib, datetime, hashlib, json, os, re, sqlite3, subprocess, sys, threading
from pathlib import Path, PureWindowsPath

from .fsutil import write_text_atomic

TOOLS = [("altium", "Altium"), ("altium365", "Altium 365"), ("kicad", "KiCad"), ("fusion", "Fusion"), ("easyeda", "EasyEDA")]
LABEL = dict(TOOLS)
STATES = ("missing", "same", "present", "different", "behind")
RANK = {"missing": 0, "same": 1, "present": 1, "different": 2, "behind": 3}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "http_cache", ".venv", "venv", ".svn"}
MAX_FILES = 5000
HISTORY = "history.jsonl"
HISTORY_MAX = 4 * 1024 * 1024
STATE = "state.json"
BULK = 40                        # more added/removed copies than this in one file in one scan: one event for the file
NOT_PARAMS = {"comment", "description", "designator", "hiddennetname", "value"}
MPN_FIELDS = ("manufacturer part number", "mpn", "mfr part number", "manufacturer_part_number", "mfr_pn", "mpn1",
              "manufacturer part number 1")
MFR_FIELDS = ("manufacturer", "manufacturer 1", "mfr", "manufacturer name")

# ------------------------------------------------------------------ other tools (Fusion, EasyEDA, plugins)
_READERS = {}


def register_tool(tool, label, exts, read, workspace_dirs=(), fix=None):
    """A reader for another tool's library files, so the hub fills that tool's column.

    tool: "fusion", "easyeda" or a new id (a new id gets its own column). exts: file suffixes, e.g. (".lbr",).
    read(path) -> [{"name", "mpn", "symbol", "footprint", "prefix", "fields": {..},
                    "pads": [(number, x_mm, y_mm, w_mm, h_mm, rot_deg, drilled)], "pins": [(number, name)]}]
    with y pointing up (Altium's and dedupe's frame). workspace_dirs: folders inside the workspace where ecadlib's own
    exporter writes this tool's files (scanned as ecadlib's copies). fix(ws, plan_path, item) -> dict: optional
    "make this tool's copy" action for a part ecadlib built."""
    if tool not in LABEL:
        TOOLS.append((tool, label))
        LABEL[tool] = label
    _READERS[tool] = {"label": label, "exts": tuple(e.lower() for e in exts), "read": read,
                      "workspace_dirs": tuple(workspace_dirs), "fix": fix}


def _plugin_readers():
    """Readers shipped as plugins: entry point group ecadlib.hub_tools, each a callable that calls register_tool."""
    from . import plugins as PL
    for ep in PL._entry_points("ecadlib.hub_tools"):
        with contextlib.suppress(Exception):
            ep.load()()


def _builtin_readers():
    """Fusion / EasyEDA readers from their exporter modules, when this ecadlib has them."""
    for mod in ("fusion_lib", "eagle_lib", "easyeda_pro"):
        with contextlib.suppress(ImportError):
            m = __import__(f"{__package__}.{mod}", fromlist=["hub_register"])
            if hasattr(m, "hub_register"):
                m.hub_register(register_tool)


# ------------------------------------------------------------------ small helpers
_LOCK = threading.RLock()
_PARSED = {}                     # (path, mtime_ns, size, kind) -> parsed result
_GIT = {}                        # (path, mtime_ns) -> (who, when) or None
_BUILT = {}                      # workspace root -> (signature, result)
_DD_CFG = None


def _cfg():
    global _DD_CFG
    if _DD_CFG is None:
        from . import dedupe as DD
        _DD_CFG = DD.load_config()
    return _DD_CFG


def mpn_key(s):
    from . import dedupe as DD
    return DD.mpn_key(s, _cfg())


def part_key(mpn, name):
    k = mpn_key(mpn) if mpn else ""
    return k or "name:" + re.sub(r"\s+", " ", str(name or "")).strip().upper()


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def iso(ts):
    """A timestamp (epoch seconds or ISO text) -> ISO 8601 in UTC, or None."""
    if ts in (None, ""):
        return None
    try:
        if isinstance(ts, (int, float)):
            d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        else:
            d = datetime.datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=datetime.timezone.utc)
        return d.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _days(a, b):
    """Days from ISO time a to ISO time b (b later -> positive)."""
    try:
        return (datetime.datetime.fromisoformat(b) - datetime.datetime.fromisoformat(a)).total_seconds() / 86400
    except (TypeError, ValueError):
        return 0.0


def age_words(days):
    days = abs(days)
    if days < 1:
        return "less than a day"
    if days < 14:
        return f"{int(days)} day{'s' if int(days) != 1 else ''}"
    if days < 60:
        return f"{int(days // 7)} weeks"
    if days < 730:
        return f"{int(days // 30)} months"
    return f"{int(days // 365)} years"


def _stat(p):
    try:
        st = Path(p).stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _parsed(path, kind, fn):
    """fn(path), remembered while the file's date and size stay the same. Errors are cached too (as the exception)."""
    st = _stat(path)
    key = (str(path), st, kind)
    with _LOCK:
        if key in _PARSED:
            v = _PARSED[key]
            if isinstance(v, Exception):
                raise v
            return v
    try:
        v = fn(path)
    except Exception as e:                       # noqa: BLE001 - one unreadable library file is reported, not fatal
        v = e
    with _LOCK:
        if len(_PARSED) > 20000:
            _PARSED.clear()
        _PARSED[key] = v
    if isinstance(v, Exception):
        raise v
    return v


def _git_root(p):
    for d in Path(p).resolve().parents:
        if (d / ".git").exists():
            return d
    return None


def _git_last(path, budget):
    """(author, ISO date) of the last commit of a file that has no uncommitted change, else None. budget: a one-item
    list counting the git calls this build may still make (big libraries: the first files only, the rest next time)."""
    st = _stat(path)
    key = (str(path), st)
    with _LOCK:
        if key in _GIT:
            return _GIT[key]
    root = _git_root(path)
    if not root or budget[0] <= 0:
        return None
    budget[0] -= 1
    res = None
    try:
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", str(path)], capture_output=True,
                               text=True, timeout=5)
        if dirty.returncode == 0 and not dirty.stdout.strip():
            r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%an%x1f%ae%x1f%aI", "--", str(path)],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                name, email, when = r.stdout.strip().split("\x1f")
                res = (f"{name} <{email}>" if email else name, iso(when))
    except (OSError, subprocess.SubprocessError, ValueError):
        res = None
    with _LOCK:
        _GIT[key] = res
    return res


def _pin_name(s):
    s = re.sub(r"[\\~{}]", "", str(s or "")).strip().upper()
    return s


# ------------------------------------------------------------------ readers: Altium
def _schlib(path):
    """[{name, params, prefix, footprint, description, pins: [(num, name)]}] of a .SchLib."""
    from . import altium_lib as AL
    _, syms = AL.read_schlib(path)
    out = []
    for s in syms:
        params, prefix, fp, desc = {}, "", "", ""
        for r in s["records"]:
            rt = r.get("RECORD")
            if rt == "1":
                desc = r.get("COMPONENTDESCRIPTION", "")
            elif rt == "34":
                prefix = re.match(r"[A-Za-z]*", r.get("TEXT", "")).group(0).upper()
            elif rt == "41" and r.get("NAME") and "OWNERINDEX" not in r:
                params[r["NAME"]] = r.get("TEXT", "")
            elif rt == "45" and r.get("MODELTYPE", "").upper() == "PCBLIB" and not fp:
                fp = r.get("MODELNAME", "")
        pins = sorted({(str(r["num"]), _pin_name(r.get("name"))) for r in s["records"] if r.get("RECORD") == "2"})
        out.append({"name": s["name"], "params": params, "prefix": prefix, "footprint": fp, "description": desc, "pins": pins})
    return out


def _pcblib(path):
    """{footprint name: [pad tuples]} of a .PcbLib (dedupe's geometry, y up)."""
    from . import altium_lib as AL, dedupe as DD
    return {fp["name"]: DD.fp_geometry_pcblib(fp)["pads"] for fp in AL.read_pcblib(path)}


def _dblib(path):
    """Rows of a .DbLib / .SvnDbLib's database (dedupe.Collector): [{name, mpn, symbol, footprint, table}]."""
    from . import dedupe as DD
    col = DD.Collector(_cfg(), log=lambda *a, **k: None)
    if Path(path).suffix.lower() == ".svndblib":
        text = Path(path).read_text(errors="replace", encoding="utf-8")
        if not re.search(r"(?:Data Source|Database|DBQ)=", text, re.I):
            return {"rows": [], "errors": [f"{Path(path).name}: no database in ConnectionString"]}
    col.add_dblib(path)
    rows = [{"name": p["name"], "mpn": p["mpn"] if p["mpn"] != p["name"] else "", "symbol": p["symbol"],
             "footprint": p["footprint"], "table": p["where"].rsplit("[", 1)[-1].rstrip("]")} for p in col.parts]
    return {"rows": rows, "errors": col.errors}


# ------------------------------------------------------------------ readers: KiCad
def _kicad_pins(sym, by_name):
    pins = []
    for sub in [sym] + [c for c in sym if isinstance(c, list) and c and c[0] == "symbol"]:
        for p in (c for c in sub if isinstance(c, list) and c and c[0] == "pin"):
            name = next((c[1] for c in p if isinstance(c, list) and c and c[0] == "name" and len(c) > 1), "")
            num = next((c[1] for c in p if isinstance(c, list) and c and c[0] == "number" and len(c) > 1), "")
            pins.append((str(num), _pin_name(name)))
    ext = next((c[1] for c in sym if isinstance(c, list) and c and c[0] == "extends" and len(c) > 1), None)
    if not pins and ext and ext in by_name:
        return _kicad_pins(by_name[ext], by_name)
    return sorted(set(pins))


def _kicad_sym(path):
    """[{name, props, prefix, footprint, pins}] of a .kicad_sym."""
    from . import sexpr
    t = sexpr.parse(Path(path).read_text(errors="replace", encoding="utf-8"))
    syms = list(sexpr.find(t, "symbol"))
    by_name = {s[1]: s for s in syms}
    out = []
    for s in syms:
        props = {p[1]: p[2] for p in sexpr.find(s, "property") if len(p) > 2}
        ext = next((c[1] for c in s if isinstance(c, list) and c and c[0] == "extends" and len(c) > 1), None)
        if ext in by_name:
            props = {**{p[1]: p[2] for p in sexpr.find(by_name[ext], "property") if len(p) > 2}, **props}
        out.append({"name": s[1], "props": props, "prefix": re.match(r"[A-Za-z]*", props.get("Reference", "")).group(0).upper(),
                    "footprint": props.get("Footprint", ""), "pins": _kicad_pins(s, by_name)})
    return out


def _kicad_mod(path):
    from . import dedupe as DD
    return DD.fp_geometry_kicad(Path(path).read_text(errors="replace", encoding="utf-8"))["pads"]


def _kicad_dbl(path):
    """Rows of a KiCad database library (.kicad_dbl) whose ODBC source is an SQLite file next to it."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    cs = (d.get("source") or {}).get("connection_string") or ""
    m = re.search(r"Database=([^;]+)", cs, re.I)
    if not m:
        return {"rows": [], "errors": [f"{Path(path).name}: only SQLite database libraries are read (no Database= in its connection string)"]}
    db = Path(m.group(1).strip().replace("${CWD}", str(Path(path).parent)))
    if not db.is_file():
        db = Path(path).parent / PureWindowsPath(m.group(1).strip()).name
    if not db.is_file():
        return {"rows": [], "errors": [f"{Path(path).name}: database {db.name} not found next to it"]}
    rows = []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for lib in d.get("libraries") or []:
            table, key = lib.get("table"), lib.get("key") or "Part ID"
            fields = {f.get("name"): f.get("column") for f in lib.get("fields") or [] if f.get("column")}
            try:
                cur = con.execute(f'SELECT * FROM "{str(table).replace(chr(34), "")}"')
            except sqlite3.Error:
                continue
            cols = [c[0] for c in cur.description]
            for r in cur:
                row = dict(zip(cols, ("" if v is None else str(v) for v in r)))
                vals = {n: row.get(c, "") for n, c in fields.items()}
                rows.append({"name": row.get(key, ""), "symbol": row.get(lib.get("symbols") or "Symbol", ""),
                             "footprint": row.get(lib.get("footprints") or "Footprint", ""), "fields": vals,
                             "table": table})
    finally:
        con.close()
    return {"rows": rows, "errors": []}


# ------------------------------------------------------------------ copies
def _field(d, names):
    low = {str(k).strip().lower(): v for k, v in (d or {}).items()}
    return next((str(low[n]).strip() for n in names if str(low.get(n) or "").strip()), "")


def _copy(tool, name, origin, path, **kw):
    c = {"tool": tool, "name": str(name), "origin": origin, "path": str(path) if path else None, "mpn": "",
         "manufacturer": "", "value": "", "description": "", "symbol": "", "footprint": "", "prefix": "",
         "pins": None, "pads": None, "updated": None, "updated_by": None, "updated_how": None, "revision": None,
         "plan": None, "item": None, "status": None, "lifecycle": None, "where": ""}
    c.update(kw)
    return c


class Scan:
    """One pass over the libraries and the workspace -> copies, plus what could not be read."""

    def __init__(self, ws, libraries, max_files=MAX_FILES):
        self.ws, self.root = ws, Path(ws.root).resolve()
        from .hub_copy import library_dir
        self.hublib = library_dir(ws)            # parts the hub copied between tools: ecadlib's, read like a library
        self.libraries = [Path(p).expanduser() for p in libraries] + ([self.hublib] if self.hublib.is_dir() else [])
        self.max_files = max_files
        self.copies, self.errors, self.files, self.warnings = [], [], [], []
        self.sigs = []

    def _sig(self, p):
        st = _stat(p)
        if st:
            self.sigs.append((str(p), st))

    def _read(self, p, kind, fn):
        try:
            return _parsed(p, kind, fn)
        except Exception as e:                   # noqa: BLE001
            self.errors.append(f"{self._show(p)}: {type(e).__name__}: {str(e)[:200]}")
            return None

    def _show(self, p):
        p = Path(p)
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    # -- your libraries
    def library_files(self):
        exts = {".schlib", ".pcblib", ".dblib", ".svndblib", ".kicad_sym", ".kicad_mod", ".kicad_dbl", ".json"}
        exts |= {e for r in _READERS.values() for e in r["exts"]}
        files = []
        self.src = {}
        for base in self.libraries:
            if base.is_file():
                files.append(base)
                self.src[str(base)] = str(base)
                continue
            if not base.is_dir():
                self.warnings.append(f"{base}: not found")
                continue
            for f in sorted(base.rglob("*")):
                if len(files) >= self.max_files:
                    self.warnings.append(f"stopped after {self.max_files} files under {base}")
                    break
                if (f.suffix.lower() in exts and f.is_file() and not any(x in SKIP_DIRS for x in f.relative_to(base).parts[:-1])
                        and (self.root not in f.resolve().parents or self.hublib in f.resolve().parents)):
                    files.append(f)
                    self.src.setdefault(str(f), str(base))
        return list(dict.fromkeys(files))

    def libraries_scan(self):
        files = self.library_files()
        for f in files:
            self._sig(f)
        by_suf = {}
        for f in files:
            by_suf.setdefault(f.suffix.lower(), []).append(f)
        pcb = {}                                   # footprint name -> [(file, pads)]
        for f in by_suf.get(".pcblib", []):
            for name, pads in (self._read(f, "pcblib", _pcblib) or {}).items():
                pcb.setdefault(name.upper(), []).append((f, pads))
        kmod = {}                                  # "lib:name" and "name" -> [(file, pads)]
        for f in by_suf.get(".kicad_mod", []):
            pads = self._read(f, "kicad_mod", _kicad_mod)
            if pads is not None:
                kmod.setdefault(f"{f.parent.stem}:{f.stem}".upper(), []).append((f, pads))
                kmod.setdefault(f.stem.upper(), []).append((f, pads))
        schsym = {}                                # symbol name -> [(file, entry)]
        sch = {f: self._read(f, "schlib", _schlib) or [] for f in by_suf.get(".schlib", [])}
        for f, entries in sch.items():
            for e in entries:
                schsym.setdefault(e["name"].upper(), []).append((f, e))
        ksym = {f: self._read(f, "kicad_sym", _kicad_sym) or [] for f in by_suf.get(".kicad_sym", [])}
        kby = {}
        for f, entries in ksym.items():
            for e in entries:
                kby.setdefault(f"{f.stem}:{e['name']}".upper(), []).append((f, e))
                kby.setdefault(e["name"].upper(), []).append((f, e))

        def near(hits, f):
            """The hit in the same folder as f, else the first one."""
            return next((h for h in hits if Path(h[0]).parent == Path(f).parent), hits[0]) if hits else None

        used_sch, used_ksym = set(), set()
        # Altium database libraries: one part per row
        for f in by_suf.get(".dblib", []) + by_suf.get(".svndblib", []):
            got = self._read(f, "dblib", _dblib)
            if not got:
                continue
            self.errors += [e for e in got["errors"]]
            for r in got["rows"]:
                s = near(schsym.get(r["symbol"].upper(), []), f)
                p = near(pcb.get(r["footprint"].upper(), []), f)
                if s:
                    used_sch.add(r["symbol"].upper())
                self.copies.append(_copy("altium", r["name"], "library", f, mpn=r["mpn"], symbol=r["symbol"],
                                         footprint=r["footprint"], pins=s[1]["pins"] if s else None,
                                         prefix=s[1]["prefix"] if s else "", pads=p[1] if p else None,
                                         where=f"{f.name} [{r['table']}]", files=[x for x in (f, s and s[0], p and p[0]) if x]))
        # Altium symbols with their own parameters (integrated-library style): one part per symbol
        for f, entries in sch.items():
            for e in entries:
                if e["name"].upper() in used_sch:
                    continue
                p = near(pcb.get(e["footprint"].upper(), []), f) if e["footprint"] else None
                self.copies.append(_copy("altium", e["name"], "library", f, mpn=_field(e["params"], MPN_FIELDS),
                                         manufacturer=_field(e["params"], MFR_FIELDS), value=_field(e["params"], ("value",)),
                                         description=e["description"], symbol=e["name"], footprint=e["footprint"],
                                         pins=e["pins"], prefix=e["prefix"], pads=p[1] if p else None, where=f.name,
                                         files=[x for x in (f, p and p[0]) if x]))
        # KiCad database libraries: one part per row
        for f in by_suf.get(".kicad_dbl", []):
            got = self._read(f, "kicad_dbl", _kicad_dbl)
            if not got:
                continue
            self.errors += got["errors"]
            for r in got["rows"]:
                s = near(kby.get(r["symbol"].upper(), []), f)
                p = near(kmod.get(r["footprint"].upper(), []) or kmod.get(r["footprint"].split(":")[-1].upper(), []), f)
                if s:
                    used_ksym.add(r["symbol"].upper())
                self.copies.append(_copy("kicad", r["name"], "library", f, mpn=_field(r["fields"], MPN_FIELDS),
                                         manufacturer=_field(r["fields"], MFR_FIELDS), value=_field(r["fields"], ("value",)),
                                         description=_field(r["fields"], ("description",)), symbol=r["symbol"],
                                         footprint=r["footprint"], pins=s[1]["pins"] if s else None,
                                         prefix=s[1]["prefix"] if s else "", pads=p[1] if p else None,
                                         where=f"{f.name} [{r['table']}]", files=[x for x in (f, s and s[0], p and p[0]) if x]))
        # KiCad symbols: one part per symbol
        for f, entries in ksym.items():
            for e in entries:
                if f"{f.stem}:{e['name']}".upper() in used_ksym or e["name"].upper() in used_ksym:
                    continue
                fp = e["footprint"]
                p = near(kmod.get(fp.upper(), []) or kmod.get(fp.split(":")[-1].upper(), []), f) if fp else None
                pr = e["props"]
                self.copies.append(_copy("kicad", e["name"], "library", f, mpn=_field(pr, MPN_FIELDS),
                                         manufacturer=_field(pr, MFR_FIELDS),
                                         value=pr.get("Value", "") if str(pr.get("Value", "")) != e["name"] else "",
                                         description=pr.get("Description", "") or pr.get("ki_description", ""),
                                         symbol=f"{f.stem}:{e['name']}", footprint=fp, pins=e["pins"], prefix=e["prefix"],
                                         pads=p[1] if p else None, where=f.name, files=[x for x in (f, p and p[0]) if x]))
        for c in self.copies:
            if c["path"] and self.hublib in Path(c["path"]).resolve().parents:
                c["origin"], c["where"] = "ecadlib", f"hub library ({c['where']})"
        # Altium 365 component lists
        for f in by_suf.get(".json", []):
            self.a365_file(f, "library")
        # other tools
        for tool, r in _READERS.items():
            for f in [f for e in r["exts"] for f in by_suf.get(e, [])]:
                self.tool_file(tool, r, f, "library")

    def a365_file(self, f, origin):
        """A check-a365 --state file ({name: snapshot}) or a list of workspace component nodes; other JSON is skipped."""
        if not re.search(r"a365|altium.?365|components|workspace", f.name, re.I):
            return
        try:
            data = _parsed(f, "json", lambda p: json.loads(Path(p).read_text(encoding="utf-8")))
        except Exception:                        # noqa: BLE001 - not a component list
            return
        if isinstance(data, dict) and isinstance(data.get("components"), list):
            data = data["components"]
        if isinstance(data, dict) and data and all(isinstance(v, dict) and ("revision" in v or "symbols" in v) for v in data.values()):
            nodes = [{"name": k, **v} for k, v in data.items()]
        elif isinstance(data, list) and data and all(isinstance(x, dict) and "name" in x for x in data) and \
                any("parameters" in x or "details" in x or "revision" in x for x in data):
            nodes = data
        else:
            return
        for n in nodes:
            par = n.get("parameters")
            d = n.get("details") or {}
            if isinstance(par, list):
                par = {p.get("name"): p.get("value", "") for p in par if isinstance(p, dict)}
            elif not isinstance(par, dict):
                par = {p["name"]: p.get("value", "") for p in d.get("parameters") or [] if isinstance(p, dict) and p.get("name")}
            rev = n.get("revision")
            rev = (rev or {}).get("id") if isinstance(rev, dict) else rev
            self.copies.append(_copy("altium365", n["name"], origin, f, mpn=_field(par, MPN_FIELDS),
                                     manufacturer=_field(par, MFR_FIELDS), value=_field(par, ("value",)),
                                     description=str(n.get("description") or ""), symbol=str(n.get("symbol") or ""),
                                     footprint=str(n.get("footprint") or ""), revision=rev,
                                     lifecycle=n.get("lifecycle") if isinstance(n.get("lifecycle"), str) else None,
                                     updated=iso(n.get("modified_at") or n.get("modifiedAt")), updated_how="Altium 365 revision",
                                     where=f"Altium 365 ({f.name})", files=[f]))
        self._sig(f)

    def tool_file(self, tool, reader, f, origin):
        got = self._read(f, f"tool:{tool}", reader["read"])
        self._sig(f)
        for e in got or []:
            fields = e.get("fields") or {}
            self.copies.append(_copy(tool, e.get("name") or "", origin, f, mpn=e.get("mpn") or _field(fields, MPN_FIELDS),
                                     manufacturer=_field(fields, MFR_FIELDS), value=_field(fields, ("value",)),
                                     description=_field(fields, ("description",)), symbol=e.get("symbol") or "",
                                     footprint=e.get("footprint") or "", prefix=e.get("prefix") or "",
                                     pins=sorted({(str(a), _pin_name(b)) for a, b in e["pins"]}) if e.get("pins") is not None else None,
                                     pads=sorted(tuple(x) for x in e["pads"]) if e.get("pads") is not None else None,
                                     where=f.name, files=[f]))

    # -- ecadlib's own parts
    def plans(self):
        out = []
        for plan_f in sorted((self.root / "librarian").glob("*/plan.json")):
            self._sig(plan_f)
            try:
                items = _parsed(plan_f, "plan", lambda p: json.loads(Path(p).read_text(encoding="utf-8")))
            except Exception as e:               # noqa: BLE001
                self.errors.append(f"{self._show(plan_f)}: {type(e).__name__}: {e}")
                continue
            if not isinstance(items, list):
                continue
            out.append((plan_f, items))
        return out

    def plan_bom(self, plan_f):
        """The workspace path of the BOM a plan was built from (library.lock.json), or None."""
        lock = plan_f.parent / "library.lock.json"
        try:
            rel = json.loads(lock.read_text(encoding="utf-8"))["bom"]["file"]
            p = (plan_f.parent / rel).resolve()
            return p.relative_to(self.root).as_posix() if p.is_file() else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def plans_scan(self):
        from .a365_sync import is_a365
        for plan_f, items in self.plans():
            base, bom, key = plan_f.parent, self.plan_bom(plan_f), plan_f.parent.name
            kdir = self.root / "kicad" / key
            kex = {}
            for kx in [kdir / "kicad_export.json"] if (kdir / "kicad_export.json").is_file() else []:
                self._sig(kx)
                try:
                    d = _parsed(kx, "json", lambda p: json.loads(Path(p).read_text(encoding="utf-8")))
                    lib = d.get("library") or "ecadlib"
                    for p in d.get("parts") or []:
                        kex[p["part"]] = (lib, p)
                except Exception as e:           # noqa: BLE001
                    self.errors.append(f"{self._show(kx)}: {type(e).__name__}: {e}")
            for i, it in enumerate(items):
                if not isinstance(it, dict) or not it.get("name"):
                    continue
                par = {p.get("name"): p.get("value") for p in it.get("parameters") or [] if isinstance(p, dict)}
                common = dict(mpn=_field(par, MPN_FIELDS), manufacturer=_field(par, MFR_FIELDS), value=_field(par, ("value",)),
                              description=it.get("description") or "", plan=self._show(plan_f), bom=bom, item=i,
                              status=it.get("status"))
                pulled = is_a365(it)
                origin = "library" if pulled else "ecadlib"
                a = it.get("a365") or {}
                if a:
                    self.copies.append(_copy("altium365", it["name"], origin, plan_f, **common, revision=a.get("revision"),
                                             lifecycle=a.get("lifecycle"),
                                             updated=iso(a.get("modified_at") or a.get("synced_at")),
                                             updated_how="Altium 365 revision" if a.get("modified_at") else "synced",
                                             symbol=(it.get("symbol") or {}).get("ref") or "",
                                             footprint=(it.get("footprint") or {}).get("ref") or "",
                                             where="Altium 365" + (f" ({a.get('workspace')})" if a.get("workspace") else ""),
                                             files=[plan_f]))
                sym, fp = it.get("symbol") or {}, it.get("footprint") or {}
                sf = sym.get("file") and (Path(sym["file"]) if Path(sym["file"]).is_absolute() else base / sym["file"])
                ff = fp.get("file") and (Path(fp["file"]) if Path(fp["file"]).is_absolute() else base / fp["file"])
                files = [f for f in (sf, ff) if f and f.is_file()]
                if files and it.get("status") != "duplicate":
                    pins = pads = None
                    prefix = ""
                    if sf and sf.is_file() and sf.suffix.lower() == ".schlib":
                        es = self._read(sf, "schlib", _schlib) or []
                        e = next((x for x in es if x["name"] == sym.get("ref")), es[0] if len(es) == 1 else None)
                        if e:
                            pins, prefix = e["pins"], e["prefix"]
                    if ff and ff.is_file() and ff.suffix.lower() == ".pcblib":
                        fps = self._read(ff, "pcblib", _pcblib) or {}
                        pads = fps.get(fp.get("ref")) if fp.get("ref") in fps else (next(iter(fps.values())) if len(fps) == 1 else None)
                    for f in files:
                        self._sig(f)
                    self.copies.append(_copy("altium", it["name"], origin, files[0], **common, symbol=sym.get("ref") or "",
                                             footprint=fp.get("ref") or "", pins=pins, pads=pads, prefix=prefix,
                                             where="ecadlib .SchLib / .PcbLib", files=files))
                if it["name"] in kex:
                    lib, p = kex[it["name"]]
                    symf, modf = kdir / f"{lib}.kicad_sym", kdir / f"{lib}.pretty" / f"{p.get('footprint')}.kicad_mod"
                    files = [f for f in (symf, modf) if f.is_file()]
                    if files:
                        es = self._read(symf, "kicad_sym", _kicad_sym) if symf.is_file() else None
                        e = next((x for x in es or [] if x["name"] == p.get("symbol_name")), None)
                        pads = self._read(modf, "kicad_mod", _kicad_mod) if modf.is_file() else None
                        for f in files:
                            self._sig(f)
                        self.copies.append(_copy("kicad", it["name"], origin, files[0], **common,
                                                 symbol=f"{lib}:{p.get('symbol_name')}", footprint=f"{lib}:{p.get('footprint')}",
                                                 pins=e["pins"] if e else None, prefix=e["prefix"] if e else "", pads=pads,
                                                 where=f"ecadlib KiCad library ({lib})", files=files))
        # pulled whole Altium 365 workspace (ecadlib a365 pull without a BOM)
        wp = self.root / "altium365" / "workspace_plan.json"
        if wp.is_file():
            self._sig(wp)
            try:
                items = _parsed(wp, "plan", lambda p: json.loads(Path(p).read_text(encoding="utf-8")))
            except Exception as e:               # noqa: BLE001
                items = []
                self.errors.append(f"{self._show(wp)}: {type(e).__name__}: {e}")
            for it in items if isinstance(items, list) else []:
                a = it.get("a365") or {}
                par = {p.get("name"): p.get("value") for p in it.get("parameters") or [] if isinstance(p, dict)}
                self.copies.append(_copy("altium365", it.get("name") or "", "library", wp, mpn=_field(par, MPN_FIELDS),
                                         manufacturer=_field(par, MFR_FIELDS), value=_field(par, ("value",)),
                                         description=it.get("description") or "", revision=a.get("revision"),
                                         lifecycle=a.get("lifecycle"), updated=iso(a.get("modified_at") or a.get("synced_at")),
                                         updated_how="Altium 365 revision", symbol=(it.get("symbol") or {}).get("ref") or "",
                                         footprint=(it.get("footprint") or {}).get("ref") or "",
                                         where="Altium 365" + (f" ({a.get('workspace')})" if a.get("workspace") else ""),
                                         files=[wp]))
        # other tools' files ecadlib wrote into the workspace
        for tool, r in _READERS.items():
            for d in r["workspace_dirs"]:
                for f in sorted((self.root / d).rglob("*")) if (self.root / d).is_dir() else []:
                    if f.suffix.lower() in r["exts"] and f.is_file():
                        self.tool_file(tool, r, f, "ecadlib")


# ------------------------------------------------------------------ comparing copies
def _value(v):
    from .kicad_index import parse_value
    try:
        return parse_value(v, True)
    except Exception:                            # noqa: BLE001
        return None, None


def _fmt(v):
    return f"{float(v):.2f}".rstrip("0").rstrip(".")


def pad_diff(a, b):
    """What differs between two pad lists, in words; '' when they match."""
    if a == b:
        return ""
    if len(a) != len(b):
        return f"{len(a)} pads, the other has {len(b)}"
    by = {p[0]: p for p in b}
    for p in a:
        q = by.get(p[0])
        if q is None:
            return f"pad {p[0]} is not in the other copy"
        if (p[3], p[4]) != (q[3], q[4]):
            return f"pad {p[0]} is {_fmt(p[3])} x {_fmt(p[4])} mm, the other {_fmt(q[3])} x {_fmt(q[4])}"
        if (p[1], p[2]) != (q[1], q[2]):
            return f"pad {p[0]} is at ({_fmt(p[1])}, {_fmt(p[2])}), the other at ({_fmt(q[1])}, {_fmt(q[2])})"
        if p[6] != q[6]:
            return f"pad {p[0]} is {'through-hole' if p[6] else 'surface mount'} here, not in the other"
        if p != q:
            return f"pad {p[0]} is turned differently"
    return "pads differ"


def _plain(name, num):
    return "" if name in ("", num) else name


def pin_diff(a, b):
    """Pins compared by number; a name counts only when both copies name the pin (KiCad's '~' and numbers as names
    don't)."""
    na, nb = {p[0]: _plain(p[1], p[0]) for p in a}, {p[0]: _plain(p[1], p[0]) for p in b}
    if set(na) != set(nb):
        extra, gone = sorted(set(na) - set(nb)), sorted(set(nb) - set(na))
        bits = ([f"pin {', '.join(extra[:4])} only here"] if extra else []) + ([f"pin {', '.join(gone[:4])} only in the other"] if gone else [])
        return f"{len(na)} pins, the other has {len(nb)}" if len(na) != len(nb) else "; ".join(bits)
    for k in sorted(na):
        if na[k] and nb[k] and na[k] != nb[k]:
            return f"pin {k} is {na[k]} here, {nb[k]} in the other"
    return ""


def compare(copies, stale_days):
    """Set state / diffs on each copy against the newest copy that has the same kind of data; -> {tool: cell}."""
    def newest(pred):
        cs = [c for c in copies if pred(c)]
        return max(cs, key=lambda c: c["updated"] or "") if cs else None
    ref_pads = newest(lambda c: c["pads"])
    ref_pins = newest(lambda c: c["pins"])
    ref_val = newest(lambda c: _value(c["value"])[0] is not None)
    latest = max((c["updated"] for c in copies if c["updated"]), default=None)
    for c in copies:
        diffs, compared = [], False
        if c["pads"] and ref_pads:
            compared = True
            d = pad_diff(c["pads"], ref_pads["pads"]) if c is not ref_pads else ""
            if d:
                diffs.append({"what": "footprint", "text": d, "against": ref_pads["tool"]})
        if c["pins"] and ref_pins:
            compared = True
            d = pin_diff(c["pins"], ref_pins["pins"]) if c is not ref_pins else ""
            if d:
                diffs.append({"what": "symbol", "text": d, "against": ref_pins["tool"]})
        v, u = _value(c["value"])
        if v is not None and ref_val and c is not ref_val:
            rv, ru = _value(ref_val["value"])
            compared = True
            if abs(v - rv) > 1e-9 * max(abs(v), abs(rv), 1e-30) or (u and ru and u != ru):
                diffs.append({"what": "value", "text": f"value {c['value']}, the other {ref_val['value']}", "against": ref_val["tool"]})
        elif v is not None:
            compared = True
        c["diffs"] = diffs
        behind = _days(c["updated"], latest) if c["updated"] and latest else 0.0
        if diffs and behind >= stale_days:
            ahead = max((x for x in copies if x["updated"] == latest), key=lambda x: x["tool"] == diffs[0]["against"])
            c["state"] = "behind"
            c["behind"] = {"days": round(behind, 1), "words": age_words(behind), "tool": ahead["tool"]}
        else:
            c["state"] = "different" if diffs else ("same" if compared else "present")
    cells = {}
    for c in copies:
        cell = cells.setdefault(c["tool"], {"state": "same", "copies": 0, "updated": None, "updated_by": None, "note": ""})
        cell["copies"] += 1
        if RANK[c["state"]] > RANK[cell["state"]] or (cell["copies"] == 1):
            cell["state"] = c["state"]
            if c["state"] == "behind":
                cell["note"] = f"{age_words(c['behind']['days'])} behind {LABEL.get(c['behind']['tool'], c['behind']['tool'])}"
            elif c["diffs"]:
                cell["note"] = f"{c['diffs'][0]['what']}: {c['diffs'][0]['text']}"
            elif c["state"] == "present":
                cell["note"] = "nothing to compare it with"
        if (c["updated"] or "") > (cell["updated"] or ""):
            cell["updated"], cell["updated_by"] = c["updated"], c["updated_by"]
    for tool, cell in cells.items():
        origins = [c["origin"] for c in copies if c["tool"] == tool]
        cell["duplicate"] = origins.count("library") > 1 or origins.count("ecadlib") > 1
        if cell["duplicate"]:
            cell["note"] = (cell["note"] + "; " if cell["note"] else "") + f"{cell['copies']} copies in this tool"
        elif cell["copies"] > 1:
            cell["note"] = (cell["note"] + "; " if cell["note"] else "") + "ecadlib's copy and yours"
    return cells


# ------------------------------------------------------------------ history
def hub_dir(root):
    d = Path(root) / "hub"
    d.mkdir(parents=True, exist_ok=True)
    return d


def record(root, entries):
    """Append history entries ({when, who, what, key?, name?, tool?, bom?, file?, detail?})."""
    if not entries:
        return
    f = hub_dir(root) / HISTORY
    with _LOCK:
        with open(f, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps({k: v for k, v in e.items() if v not in (None, "")}, sort_keys=True) + "\n")
        if f.stat().st_size > HISTORY_MAX:
            lines = f.read_text(encoding="utf-8").splitlines()
            write_text_atomic(f, "\n".join(lines[len(lines) // 2:]) + "\n")


def read_history(root):
    f = Path(root) / "hub" / HISTORY
    out = []
    if f.is_file():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                e = json.loads(line)
                if isinstance(e, dict):
                    out.append(e)
    return out


ROUTE_WORDS = {
    "/api/librarian/run": "filled the library", "/api/resolve/choose": "picked a symbol or footprint",
    "/api/release-check": "ran the release check", "/api/altium365/push": "sent parts to Altium 365",
    "/api/export/dblib": "exported the Altium DbLib", "/api/altium/models": "built .PcbLib / .SchLib files",
    "/api/dedupe": "merged duplicates", "/api/agent/queue/approve": "approved a part in the review queue",
    "/api/agent/queue/rebuild": "asked for a rebuild", "/api/demo": "built the sample library",
}


def note_action(root, route, body, who, result=None):
    """A web-app action that changes library files, so the next scan can say who changed them."""
    what = ROUTE_WORDS.get(route)
    if not what or (route == "/api/altium365/push" and body.get("dry_run", True) is not False) or \
            (route == "/api/dedupe" and body.get("merge") is not True):
        return
    e = {"when": now(), "who": who, "what": what, "kind": "action", "bom": body.get("bom") if isinstance(body.get("bom"), str) else None,
         "name": body.get("part") if isinstance(body.get("part"), str) else None}
    if route == "/api/altium365/push" and isinstance(result, dict):
        e["detail"] = ", ".join(str(r.get("name")) for r in (result.get("results") or [])
                                if isinstance(r, dict) and str(r.get("status", "")).startswith(("released", "updated")))[:500]
    record(root, [e])


def _attribute(copy, actions):
    """Who changed a copy: the newest web-app action on its BOM or part within 20 minutes before its date."""
    if not copy["updated"]:
        return None
    best = None
    for a in actions:
        if not (a.get("bom") and a["bom"] == copy.get("bom")) and not (a.get("name") and a["name"] == copy["name"]):
            continue
        gap = _days(a["when"], copy["updated"]) * 86400
        if -120 <= gap <= 1200 and (best is None or a["when"] > best["when"]):
            best = a
    return best


def _copy_id(c):
    return f"{c['tool']}|{c.get('where') or ''}|{c['path']}|{c['name']}"


def _fingerprint(c):
    return hashlib.sha256(json.dumps([c["pads"], c["pins"], c["value"], c["revision"], c["symbol"], c["footprint"]],
                                     sort_keys=True, default=list).encode()).hexdigest()[:16]


def _h(x):
    return hashlib.sha256(json.dumps(x, default=list).encode()).hexdigest()[:12] if x is not None else None


def _what_changed(a, b):
    """In words, what changed between two scans of one copy."""
    out = []
    if a.get("pads") != b["pads"]:
        out.append("footprint pads" if a.get("pads") and b["pads"] else ("footprint added" if b["pads"] else "footprint gone"))
    if a.get("pins") != b["pins"]:
        out.append("symbol pins" if a.get("pins") and b["pins"] else ("symbol added" if b["pins"] else "symbol gone"))
    if a.get("value") != b["value"]:
        out.append(f"value {a.get('value') or '(none)'} -> {b['value'] or '(none)'}")
    if a.get("rev") != b["rev"]:
        out.append(f"revision {a.get('rev') or '(none)'} -> {b['rev'] or '(none)'}")
    if a.get("sym") != b["sym"] or a.get("fpname") != b["fpname"]:
        out.append("symbol or footprint link")
    return ", ".join(out) or None


def _diff_state(root, copies, show, sources):
    """Compare this scan with the last one: added / changed / removed copies become history entries. A copy from a
    library that was not read this time (another --lib) is not "removed": its entry is kept for when it is back."""
    f = hub_dir(root) / STATE
    old = {}
    with contextlib.suppress(OSError, ValueError):
        old = json.loads(f.read_text(encoding="utf-8")).get("copies") or {}
    first = not f.is_file()
    cur = {_copy_id(c): {"fp": _fingerprint(c), "key": c["key"], "name": c["name"], "tool": c["tool"], "file": show(c["path"]),
                         "updated": c["updated"], "who": c["updated_by"], "pads": _h(c["pads"]), "pins": _h(c["pins"]),
                         "value": c["value"], "rev": c["revision"], "sym": c["symbol"], "fpname": c["footprint"],
                         "src": c["src"]} for c in copies}
    for k, v in old.items():
        if k not in cur and v.get("src", "workspace") not in sources:
            cur[k] = v                                   # its library was not read this time
    events = []
    if not first:
        added = [(k, v) for k, v in cur.items() if k not in old]
        removed = [(k, v) for k, v in old.items() if k not in cur]
        changed = [(k, v) for k, v in cur.items() if k in old and old[k]["fp"] != v["fp"]]
        t = now()
        for kind, rows in (("added", added), ("removed", removed)):
            by_file = {}
            for k, v in rows:
                by_file.setdefault((v["tool"], v["file"]), []).append(v)
            for (tool, file), vs in by_file.items():
                if len(vs) > BULK:
                    events.append({"when": t, "what": f"{len(vs)} parts {kind}", "tool": tool, "file": file, "kind": "scan",
                                   "who": vs[0]["who"] if kind == "added" else None})
                    continue
                for v in vs:
                    events.append({"when": (v["updated"] or t) if kind == "added" else t, "what": kind, "key": v["key"],
                                   "name": v["name"], "tool": tool, "file": file, "who": v["who"] if kind == "added" else None,
                                   "kind": "scan"})
        for k, v in changed:
            events.append({"when": v["updated"] or t, "what": "changed", "key": v["key"], "name": v["name"], "tool": v["tool"],
                           "file": v["file"], "who": v["who"], "kind": "scan", "detail": _what_changed(old[k], v)})
    if first or events or set(cur) != set(old) or any(old[k]["fp"] != cur[k]["fp"] for k in cur if k in old):
        write_text_atomic(f, json.dumps({"scanned": now(), "copies": cur}, sort_keys=True))
    record(root, events)
    return events


# ------------------------------------------------------------------ where-used and lifecycle
_WU = {}


def _where_used(projects):
    """{mpn key or NAME: [{project, path, designators, qty}]} from Altium projects (altium_project.where_used)."""
    if not projects:
        return {}, []
    from . import altium_project as AP
    files = AP.find_projects([Path(p).expanduser() for p in projects])
    files += [f for d in projects for f in (sorted(Path(d).expanduser().rglob("*.kicad_sch")) if Path(d).expanduser().is_dir() else [])]
    sig = tuple((str(f), _stat(f)) for f in files)
    with _LOCK:
        if _WU.get("sig") == sig:
            return _WU["val"]
    res = AP.where_used([Path(p).expanduser() for p in projects])
    out = _kicad_where_used(projects)
    for e in res["parts"].values():
        uses = [{"project": u["project"], "path": str(Path(u["path"]).resolve()), "qty": u["qty"], "designators": u["designators"][:40]} for u in e["uses"]]
        keys = ({mpn_key(e["mpn"])} if e["mpn"] else set()) | {"name:" + str(x).upper() for x in e["design_item_ids"] | e["librefs"]}
        for k in keys - {""}:
            out.setdefault(k, []).extend(uses)
    val = (out, res["warnings"][:20])
    with _LOCK:
        _WU.update(sig=sig, val=val)
    return val


def _kicad_where_used(projects):
    """{mpn key or NAME: [uses]} from the schematics of KiCad projects (.kicad_pro) under the project folders."""
    from . import sexpr
    from .hub_copy import projects as find
    out = {}
    for prj in [p for p in find(projects) if p["tool"] == "kicad"]:
        parts = {}
        for sch in sorted(Path(prj["path"]).parent.glob("*.kicad_sch")):
            try:
                tree = _parsed(sch, "kicad_sch", lambda f: sexpr.parse(Path(f).read_text(encoding="utf-8", errors="replace")))
            except Exception:                    # noqa: BLE001 - a schematic that doesn't parse is skipped
                continue
            for sym in [n for n in tree[1:] if isinstance(n, list) and n and n[0] == "symbol"]:
                lib_id = (sexpr.first(sym, "lib_id") or [None, ""])[1]
                props = {str(p[1]): str(p[2]) for p in sexpr.find(sym, "property") if len(p) > 2}
                ref = props.get("Reference", "")
                if not lib_id or ref.startswith("#"):
                    continue
                mpn = _field(props, MPN_FIELDS)
                keys = ({mpn_key(mpn)} if mpn else set()) | {"name:" + lib_id.split(":")[-1].upper()}
                for k in keys - {""}:
                    u = parts.setdefault(k, {"project": prj["name"], "path": prj["path"], "qty": 0, "designators": []})
                    u["qty"] += 1
                    if len(u["designators"]) < 40:
                        u["designators"].append(ref)
        for k, u in parts.items():
            out.setdefault(k, []).append(u)
    return out


def _lifecycle(root):
    """{mpn key: {status, label, since, checked}} from upkeep's lifecycle state files (the worst status per part)."""
    from . import lifecycle as L
    out = {}
    for f in sorted(Path(root).glob("upkeep/*/state/lifecycle_state.json")):
        with contextlib.suppress(OSError, ValueError):
            for v in (json.loads(f.read_text(encoding="utf-8")).get("parts") or {}).values():
                k = mpn_key(v.get("mpn"))
                if k and L.SEVERITY.get(v.get("status"), -1) >= L.SEVERITY.get((out.get(k) or {}).get("status"), -1):
                    out[k] = {"status": v.get("status"), "label": L.LABEL.get(v.get("status"), v.get("status")),
                              "since": v.get("since"), "checked": v.get("checked"), "from": "upkeep lifecycle check"}
    return out


def _lifecycle_word(raw):
    from . import lifecycle as L
    st = L.normalise(raw) if raw else "unknown"
    return {"status": st, "label": L.LABEL.get(st, raw), "from": "Altium 365", "raw": raw}


# ------------------------------------------------------------------ the hub
def settings(ws):
    sec = dict((ws.rules().get("hub") or {}))
    rf = ws.rules_in_use()
    here = lambda p: str(Path(p).expanduser() if Path(p).expanduser().is_absolute() or not rf else Path(rf).parent / p)
    libs = [*ws.libs, *[here(p) for p in sec.get("libraries") or []]]
    return {"libraries": list(dict.fromkeys(libs)), "projects": [here(p) for p in sec.get("projects") or []],
            "stale_days": int(sec.get("stale_days") or 14)}


_BUILD_LOCK = threading.Lock()


def build(ws, force=False):
    """Scan everything -> {rows, tools, summary, errors, ...}; reuses the last result while no file changed. One build
    at a time, so two page loads can't both record the same change."""
    with _BUILD_LOCK:
        return _build(ws, force)


def _build(ws, force):
    _builtin_readers()
    _plugin_readers()
    cfg = settings(ws)
    root = Path(ws.root).resolve()
    sc = Scan(ws, cfg["libraries"])
    sc.libraries_scan()
    sc.plans_scan()
    wu, wu_warn = _where_used(cfg["projects"])    # cached on its own files; a changed schematic rebuilds the hub too
    with _LOCK:
        wu_sig = _WU.get("sig")
    sig = hashlib.sha256(json.dumps([sorted(sc.sigs), cfg, sorted(_READERS), wu_sig], default=str).encode()).hexdigest()
    with _LOCK:
        last = _BUILT.get(str(root))
    if last and last[0] == sig and not force:
        return last[1]
    actions = [e for e in read_history(root) if e.get("kind") == "action"]
    budget = [300]
    for c in sc.copies:
        c["key"] = part_key(c["mpn"], c["name"])
        files = [Path(f) for f in c.get("files") or ([c["path"]] if c["path"] else [])]
        c["files"] = [str(f) for f in files]
        if c["updated"] is None:                  # newest file of the copy: its git commit when clean, else its date
            dated = []
            for f in files:
                g = _git_last(f, budget)
                st = _stat(f)
                if g and g[1]:
                    dated.append((g[1], g[0], "git commit"))
                elif st:
                    dated.append((iso(st[0] / 1e9), None, "file saved"))
            if dated:
                c["updated"], c["updated_by"], c["updated_how"] = max(dated, key=lambda x: x[0])
        if not c["updated_by"]:
            a = _attribute(c, actions)
            if a and a.get("who"):
                c["updated_by"], c["updated_how"] = a["who"], f"{c['updated_how'] or 'changed'}: {a['what']} in the web app"
    for c in sc.copies:
        c["src"] = sc.src.get(str(c["path"]), "workspace") if c["origin"] == "library" and c["plan"] is None else "workspace"
    _diff_state(root, sc.copies, sc._show, {"workspace", *[str(p) for p in sc.libraries]})
    life = _lifecycle(root)
    rows = {}
    for c in sc.copies:
        rows.setdefault(c["key"], []).append(c)
    out = []
    names = {k: next((c["name"] for c in cs if c["origin"] == "ecadlib"), cs[0]["name"]) for k, cs in rows.items()}
    for k, cs in rows.items():
        cells = compare(cs, cfg["stale_days"])
        plan_c = next((c for c in cs if c["plan"] and c["status"]), None)
        lc = life.get(k) or next((_lifecycle_word(c["lifecycle"]) for c in cs if c["lifecycle"]), None)
        uses = wu.get(k, []) + [u for c in cs for u in wu.get("name:" + c["name"].upper(), [])]
        uses = list({(u["path"], tuple(u["designators"])): u for u in uses}.values())
        latest = max(cs, key=lambda c: c["updated"] or "")
        origin = {c["origin"] for c in cs}
        flags = sorted({c["state"] for c in cs if c["state"] in ("different", "behind")})
        if plan_c and plan_c["status"] in ("held", "awaiting_approval"):
            flags.append(plan_c["status"])
        if lc and lc["status"] in ("nrnd", "ltb", "obsolete"):
            flags.append("eol")
        if any(v["duplicate"] for v in cells.values()):
            flags.append("duplicate")
        places = {}
        for c in sorted(cs, key=lambda c: c["updated"] or "", reverse=True):
            lib = sc._show(c["path"]) if c["path"] else (c["where"] or LABEL.get(c["tool"], c["tool"]))
            places.setdefault(c["tool"], []).append({"library": lib, "origin": c["origin"], "symbol": c["symbol"],
                                                     "footprint": c["footprint"], "updated": c["updated"],
                                                     "updated_by": c["updated_by"]})
        out.append({"key": k, "name": names[k], "places": places, "mpn": next((c["mpn"] for c in cs if c["mpn"]), ""),
                    "manufacturer": next((c["manufacturer"] for c in cs if c["manufacturer"]), ""),
                    "description": next((c["description"] for c in cs if c["description"]), ""),
                    "origin": "both" if len(origin) > 1 else origin.pop(), "status": plan_c["status"] if plan_c else None,
                    "cells": cells, "updated": latest["updated"], "updated_by": latest["updated_by"],
                    "updated_tool": latest["tool"], "lifecycle": lc, "used_in": len({u["path"] for u in uses}),
                    "projects": sorted({u["path"] for u in uses}),
                    "flags": flags})
    out.sort(key=lambda r: (r["name"].upper(), r["key"]))
    tools = [{"tool": t, "label": lab, "reader": t in ("altium", "altium365", "kicad") or t in _READERS,
              "parts": sum(t in r["cells"] for r in out), "missing": sum(t not in r["cells"] for r in out),
              **_tool_summary([p for r in out for p in r["places"].get(t, [])]),
              "different": sum(r["cells"].get(t, {}).get("state") == "different" for r in out),
              "behind": sum(r["cells"].get(t, {}).get("state") == "behind" for r in out)} for t, lab in TOOLS]
    from .hub_copy import TARGETS, source_for
    missing_in = {r["key"]: r["cells"] for r in out}
    for tl in tools:                              # parts other tools have that the hub can convert into this one
        tl["can_copy"] = sum(1 for k, cs in rows.items() if tl["tool"] in TARGETS and tl["tool"] not in missing_in[k]
                             and source_for(cs, tl["tool"])) if tl["tool"] in TARGETS else 0
    summary = {"parts": len(out), "ecadlib": sum(r["origin"] in ("ecadlib", "both") for r in out),
               "library": sum(r["origin"] in ("library", "both") for r in out),
               "different": sum("different" in r["flags"] for r in out), "behind": sum("behind" in r["flags"] for r in out),
               "held": sum(r["status"] in ("held", "awaiting_approval") for r in out), "eol": sum("eol" in r["flags"] for r in out),
               "duplicates": sum("duplicate" in r["flags"] for r in out),
               "updated": max((r["updated"] for r in out if r["updated"]), default=None), "scanned": now()}
    res = {"rows": out, "tools": tools, "summary": summary, "errors": sc.errors[:50], "warnings": (sc.warnings + wu_warn)[:50],
           "libraries": [sc._show(p) for p in cfg["libraries"]], "projects": cfg["projects"], "stale_days": cfg["stale_days"],
           "project_list": _project_list(cfg["projects"], out, rows),
           "_copies": sc.copies, "_show": sc._show}
    with _LOCK:
        _BUILT[str(root)] = (sig, res)
    return res


def _project_list(dirs, rows, copies):
    """The projects under [hub] projects: how many library parts each uses, how many of those its tool lacks, and how
    many of those the hub can copy into it."""
    from .hub_copy import projects as find, source_for
    out = []
    for p in find(dirs):
        used = [r for r in rows if p["path"] in r["projects"]]
        lack = [r for r in used if p["tool"] not in r["cells"]]
        out.append({**p, "parts": len(used), "missing": len(lack),
                    "can_copy": sum(1 for r in lack if source_for(copies.get(r["key"], []), p["tool"]))})
    return sorted(out, key=lambda p: p["name"].upper())


def _tool_summary(places):
    """One tool's libraries (file or folder, part count, newest change and by whom) and its newest change overall."""
    libs = {}
    for p in places:
        lib = libs.setdefault(p["library"], {"library": p["library"], "parts": 0, "updated": None, "updated_by": None,
                                             "origin": p["origin"]})
        lib["parts"] += 1
        if p["origin"] != lib["origin"]:
            lib["origin"] = "both"
        if (p["updated"] or "") > (lib["updated"] or ""):
            lib["updated"], lib["updated_by"] = p["updated"], p["updated_by"]
    libs = sorted(libs.values(), key=lambda l: (-l["parts"], l["library"]))
    newest = max(libs, key=lambda l: l["updated"] or "", default=None)
    return {"libraries": libs, "updated": newest["updated"] if newest else None,
            "updated_by": newest["updated_by"] if newest else None}


def _matches(r, q, have, lack, states, origin, flags):
    if q:
        hay = " ".join(str(r.get(k) or "") for k in ("name", "mpn", "manufacturer", "description")).lower()
        if not all(w in hay for w in q.lower().split()):
            return False
    if any(t not in r["cells"] for t in have) or any(t in r["cells"] for t in lack):
        return False
    if states and not any(c["state"] in states for c in r["cells"].values()):
        return False
    if origin and r["origin"] not in (origin, "both"):
        return False
    return not flags or any(f in r["flags"] for f in flags)


def listing(ws, q="", have=(), lack=(), states=(), origin="", flags=(), sort="name", page=1, size=100, force=False,
            tool="", library="", project=""):
    """A page of hub rows, filtered: q (words in name / MPN / maker / description), have / lack (tools), states (cell
    states), origin (ecadlib / library), flags (different, behind, held, awaiting_approval, eol, duplicate).
    tool: one tool's library only (its copies, newest change in that tool first when sorted by update); library: one
    file or folder of that tool."""
    res = build(ws, force)
    have = (*have, tool) if tool else have
    rows = [r for r in res["rows"] if _matches(r, q, have, lack, states, origin, flags)
            and (not library or any(p["library"] == library for p in r["places"].get(tool, [])))
            and (not project or project in r["projects"])]
    if sort == "updated":
        rows.sort(key=lambda r: (r["cells"][tool]["updated"] if tool else r["updated"]) or "", reverse=True)
    elif sort == "attention":
        rows.sort(key=lambda r: (-max([RANK[c["state"]] for c in r["cells"].values()] + [0]) - 2 * ("held" in r["flags"]), r["name"].upper()))
    size = max(1, min(int(size or 100), 500))
    page = max(1, int(page or 1))
    return {"rows": rows[(page - 1) * size: page * size], "total": len(res["rows"]), "matched": len(rows), "page": page,
            "size": size, **{k: v for k, v in res.items() if k not in ("rows",) and not k.startswith("_")}}


# ------------------------------------------------------------------ one part
def _public(c, show):
    keep = ("tool", "name", "origin", "mpn", "manufacturer", "value", "description", "symbol", "footprint", "updated",
            "updated_by", "updated_how", "revision", "plan", "bom", "status", "state", "diffs", "behind", "where", "lifecycle")
    d = {k: c.get(k) for k in keep}
    d["file"] = show(c["path"]) if c["path"] else None
    d["tool_label"] = LABEL.get(c["tool"], c["tool"])
    d["pads"] = len(c["pads"]) if c["pads"] is not None else None
    d["pins"] = len(c["pins"]) if c["pins"] is not None else None
    return d


def footprint_picture(pads, scale=14):
    """Altium-style top view of a pad list (y up, as stored) via app.footprint_svg."""
    from .app import footprint_svg
    if not pads:
        return None
    ps = [{"num": p[0], "x": p[1], "y": -p[2], "w": p[3], "h": p[4]} for p in pads]
    xs = [p["x"] - p["w"] / 2 for p in ps] + [p["x"] + p["w"] / 2 for p in ps]
    ys = [p["y"] - p["h"] / 2 for p in ps] + [p["y"] + p["h"] / 2 for p in ps]
    return footprint_svg({"pads": ps, "courtyard_bbox": (min(xs) - .25, min(ys) - .25, max(xs) + .25, max(ys) + .25)}, scale)


def symbol_picture(pins, prefix="", name=""):
    """A plain box symbol: pins down both sides, number outside, name inside. A sketch of the pinout, not the drawing."""
    if not pins:
        return None
    import html as H
    pins = sorted(pins, key=lambda p: (len(p[0]), p[0]))
    half = (len(pins) + 1) // 2
    left, right = pins[:half], pins[half:][::-1]
    longest = max(len(p[1] or "") for p in pins) if pins else 0
    w = max(60, 12 + longest * 7 * 2 + 20)
    h = max(len(left), len(right), 1) * 20 + 20
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" class="sym" viewBox="-40 -24 {w + 80} {h + 36}" width="{w + 80}" height="{h + 36}">',
           f'<text class="ref" x="0" y="-8">{H.escape((prefix or "U") + "?")}</text>',
           f'<text class="ref" x="{w}" y="-8" text-anchor="end">{H.escape(name[:40])}</text>',
           f'<rect class="body" x="0" y="0" width="{w}" height="{h}"/>']
    for side, ps in (("l", left), ("r", right)):
        for i, (num, nm) in enumerate(ps):
            y = 20 + i * 20
            x0, x1 = (-30, 0) if side == "l" else (w, w + 30)
            out.append(f'<line class="pin" x1="{x0}" y1="{y}" x2="{x1}" y2="{y}"/>')
            out.append(f'<text class="num" x="{(x0 + x1) / 2}" y="{y - 3}" text-anchor="middle">{H.escape(num)}</text>')
            if nm and nm != num:
                tx, anchor = (6, "start") if side == "l" else (w - 6, "end")
                out.append(f'<text class="name" x="{tx}" y="{y + 4}" text-anchor="{anchor}">{H.escape(nm[:24])}</text>')
    return "".join(out) + "</svg>"


def part(ws, key):
    """Everything about one hub row: each tool's copy, pictures, sources, release check, sign-off, where-used,
    lifecycle, history and the fixes on offer."""
    res = build(ws)
    row = next((r for r in res["rows"] if r["key"] == key), None)
    if row is None:
        raise KeyError(f"no part {key!r} in the library hub")
    show, cfg = res["_show"], settings(ws)
    copies = [c for c in res["_copies"] if c["key"] == key]
    order = {t: i for i, (t, _) in enumerate(TOOLS)}
    copies.sort(key=lambda c: (order.get(c["tool"], 99), c["origin"] != "ecadlib", c["name"]))
    pictures, seen = [], set()
    for c in copies:
        if c["pads"] and tuple(map(tuple, c["pads"])) not in seen:
            seen.add(tuple(map(tuple, c["pads"])))
            pictures.append({"tool": LABEL.get(c["tool"], c["tool"]), "footprint": c["footprint"], "pads": len(c["pads"]),
                             "svg": footprint_picture(c["pads"])})
    symbol = next(({"tool": LABEL.get(c["tool"], c["tool"]), "symbol": c["symbol"], "pins": len(c["pins"]),
                    "svg": symbol_picture(c["pins"], c["prefix"], c["symbol"] or c["name"])} for c in copies if c["pins"]), None)
    # the plan item behind ecadlib's copy: sources, release check, sign-off
    plan_info = None
    pc = next((c for c in copies if c["plan"] and c["item"] is not None and c["origin"] == "ecadlib"), None) or \
        next((c for c in copies if c["plan"] and c["item"] is not None), None)
    history = []
    if pc:
        from . import partview as PV
        plan_f = ws.inside(pc["plan"])
        items = json.loads(plan_f.read_text(encoding="utf-8"))
        it = items[pc["item"]]
        card = PV.cards(plan_f.parent, [it], pictures=False)["parts"][0]
        appr = [{k: a.get(k) for k in ("who", "when", "role", "decision", "comment")} for a in it.get("approvals") or [] if isinstance(a, dict)]
        plan_info = {"plan": pc["plan"], "bom": pc["bom"], "status": it.get("status"), "status_label": card.get("status_label"),
                     "reasons": card.get("reasons"), "source": it.get("source") or {}, "field_sources": it.get("field_sources") or {},
                     "datasheet": PV.datasheet_url(it), "tried": ((it.get("librarian") or {}).get("tried") or [])[:6],
                     "notes": ((it.get("librarian") or {}).get("notes") or [])[:6], "approvals": appr,
                     "approval": card.get("approval"), "allowed": bool(it.get("allow"))}
        history += [{"when": a["when"], "who": a["who"], "what": "approved" if a["decision"] == "approve" else "rejected",
                     "detail": a.get("comment") or (f"as {a['role']}" if a.get("role") else ""), "kind": "sign-off"} for a in appr]
        a = it.get("a365") or {}
        if a.get("synced_at"):
            history.append({"when": iso(a["synced_at"]), "what": "synced with Altium 365", "tool": "altium365",
                            "detail": f"revision {a.get('revision')}" if a.get("revision") else "", "kind": "sync"})
    names = {c["name"] for c in copies}
    for e in read_history(ws.root):
        if e.get("key") == key or (e.get("kind") == "action" and (e.get("name") in names or (pc and e.get("bom") and e["bom"] == pc["bom"]))):
            history.append(e)
    history.sort(key=lambda e: e.get("when") or "", reverse=True)
    for e in history:
        if e.get("tool"):
            e["tool_label"] = LABEL.get(e["tool"], e["tool"])
    wu, _ = _where_used(cfg["projects"])
    uses = wu.get(key, []) + [u for n in names for u in wu.get("name:" + n.upper(), [])]
    uses = list({(u["path"], tuple(u["designators"])): u for u in uses}.values())
    return {**row, "copies": [_public(c, show) for c in copies], "pictures": pictures, "symbol": symbol, "plan": plan_info,
            "history": history[:200], "where_used": uses, "projects_set": bool(cfg["projects"]),
            "fixes": fixes(row, copies)}


def fixes(row, copies):
    """The buttons a part gets: [{action, tool, label, why}]."""
    out = []
    plan_c = next((c for c in copies if c["plan"] and c["origin"] == "ecadlib" and c["bom"]), None)
    def own_off(tool):
        """ecadlib's own copy for this tool is missing or differs (your library's copies are never rewritten)."""
        own = [c for c in copies if c["tool"] == tool and c["origin"] == "ecadlib"]
        return not own or any(c.get("state") in ("different", "behind") for c in own)
    if plan_c:
        st = plan_c["status"]
        if st in ("held", "awaiting_approval"):
            out.append({"action": "open_plan", "tool": "altium", "label": "See why it is held" if st == "held" else "Sign it off",
                        "why": "ecadlib holds a part back from every tool until its checks pass and it is signed off"})
        elif own_off("kicad"):
            out.append({"action": "kicad", "tool": "kicad", "label": "Make the KiCad version",
                        "why": "writes the KiCad symbol, footprint and database library for this part's BOM"})
        if st == "ready" and own_off("altium365") and not any(c["tool"] == "altium365" and c["origin"] == "library" for c in copies):
            out.append({"action": "a365_preview", "tool": "altium365", "label": "Send to Altium 365",
                        "why": "a dry run first: shows the request without sending anything"})
        for tool, r in _READERS.items():
            if r["fix"] and own_off(tool) and st == "ready":
                out.append({"action": f"tool:{tool}", "tool": tool, "label": f"Make the {r['label']} version", "why": ""})
    elif row["mpn"]:
        out.append({"action": "build", "tool": "", "label": "Build it with ecadlib",
                    "why": "ecadlib finds and checks its own copy from this MPN (it does not convert yours); the hub then "
                           "compares the two and can make the copies for the tools that lack it"})
    from .hub_copy import TARGETS, source_for
    offered = {f["tool"] for f in out}
    for tool in TARGETS:
        src = None if tool in row["cells"] or tool in offered else source_for(copies, tool)
        if src:
            out.append({"action": f"copy:{tool}", "tool": tool, "label": f"Copy to {LABEL[tool]}",
                        "why": f"converts the {LABEL[src['tool']]} copy ({src['where']}) into the hub's {LABEL[tool]} "
                               f"library; the original is left as it is"})
    if "duplicate" in row["flags"] and plan_c:
        out.append({"action": "dedupe", "tool": "", "label": "Find duplicates", "why": "the same part is in one tool more than once"})
    return out


def copy_parts(ws, keys, tool, project="", who=None):
    """Copy parts into the hub's library for tool (hub_copy), and, with a project from [hub] projects, import that
    library into the project. -> {tool, copied: [{key, name, from, files, notes}], skipped: [{key, name, why}], project}"""
    from . import hub_copy as HC
    if tool not in HC.TARGETS:
        raise ValueError(f"copying into {LABEL.get(tool, tool)} isn't supported yet: the hub copies between "
                         f"{' and '.join(LABEL[t] for t in HC.TARGETS)}")
    res = build(ws)
    if project and project not in {p["path"] for p in res["project_list"]}:
        raise ValueError(f"{project} is not one of the projects under [hub] projects")
    if project and next(p for p in res["project_list"] if p["path"] == project)["tool"] != tool:
        raise ValueError(f"{Path(project).name} is not a {LABEL[tool]} project")
    rows = {r["key"]: r for r in res["rows"]}
    copied, skipped, events = [], [], []
    for key in dict.fromkeys(keys):
        row = rows.get(key)
        if row is None:
            skipped.append({"key": key, "name": key, "why": "not in the library hub"})
            continue
        if tool in row["cells"]:
            skipped.append({"key": key, "name": row["name"], "why": f"already in {LABEL[tool]}"})
            continue
        src = HC.source_for([c for c in res["_copies"] if c["key"] == key], tool)
        if not src:
            skipped.append({"key": key, "name": row["name"], "why": f"no copy ecadlib can convert to {LABEL[tool]}: "
                                                                    f"it needs an Altium SchLib or a KiCad .kicad_sym"})
            continue
        try:
            files, fp, notes = (HC.to_kicad if tool == "kicad" else HC.to_altium)(ws, src, row["name"])
        except Exception as e:                    # noqa: BLE001 - one part's failure is reported, the rest go on
            skipped.append({"key": key, "name": row["name"], "why": f"{type(e).__name__}: {e}"})
            continue
        copied.append({"key": key, "name": row["name"], "from": f"{LABEL[src['tool']]} ({src['where']})",
                       "files": [Path(ws.root).resolve().joinpath(f).relative_to(Path(ws.root).resolve()).as_posix()
                                 if Path(ws.root).resolve() in Path(f).resolve().parents else str(f) for f in files],
                       "footprint": fp, "notes": notes[:10]})
        events.append({"when": now(), "who": who, "what": f"copied to {LABEL[tool]} from {LABEL[src['tool']]}",
                       "kind": "action", "key": key, "name": row["name"], "tool": tool})
    imported = HC.import_into_project(ws, project, tool) if project and copied else None
    if imported:
        events.append({"when": now(), "who": who, "what": imported, "kind": "action", "tool": tool, "file": project})
    record(ws.root, events)
    return {"tool": tool, "copied": copied, "skipped": skipped, "project": imported,
            "library": str(HC.library_dir(ws) / ("kicad" if tool == "kicad" else "altium"))}


def one_line_bom(row, copies):
    """A one-part BOM (CSV text) for "Build it with ecadlib"."""
    import csv, io
    c = next((c for c in copies if c["footprint"]), copies[0])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Designator", "Quantity", "Manufacturer", "Manufacturer Part Number", "Value", "Footprint", "Description"])
    w.writerow([f"{c['prefix'] or 'U'}1", 1, row["manufacturer"], row["mpn"], c["value"] or "",
                str(c["footprint"]).split(":")[-1], row["description"]])
    return buf.getvalue().encode("utf-8")


def copies_of(ws, key):
    res = build(ws)
    row = next((r for r in res["rows"] if r["key"] == key), None)
    if row is None:
        raise KeyError(f"no part {key!r} in the library hub")
    return row, [c for c in res["_copies"] if c["key"] == key]


# ------------------------------------------------------------------ command line
def _table(rows, tools):
    out = []
    head = ["Part", *[t["label"] for t in tools if t["reader"]], "Updated", "By"]
    out.append(" | ".join(head))
    mark = {"same": "ok", "present": "yes", "different": "DIFF", "behind": "BEHIND"}
    for r in rows:
        cells = [mark.get(r["cells"].get(t["tool"], {}).get("state"), "-") for t in tools if t["reader"]]
        out.append(" | ".join([r["name"][:40], *cells, (r["updated"] or "")[:10], (r["updated_by"] or "not recorded")[:30]]))
    return "\n".join(out)


def main(argv=None):
    from .app import Workspace
    ap = argparse.ArgumentParser(prog="ecadlib hub", description="your library across tools: every part once, a column per tool")
    ap.add_argument("--workspace", default=os.environ.get("ECADLIB_WORKSPACE") or "ecadlib_workspace")
    ap.add_argument("--lib", action="append", default=[], help="library folder or file to include (repeatable)")
    ap.add_argument("-q", "--search", default="", help="words to look for in name, MPN, maker, description")
    ap.add_argument("--have", default="", help="only parts in these tools (comma list: altium,altium365,kicad,fusion,easyeda)")
    ap.add_argument("--lack", default="", help="only parts missing from these tools")
    ap.add_argument("--state", default="", help="only parts with a cell in these states (different,behind,present,same)")
    ap.add_argument("--tool", default="", help="one tool's library only (altium, altium365, kicad, fusion, easyeda)")
    ap.add_argument("--project", default="", help="only parts this project uses (a .kicad_pro / .PrjPcb under [hub] projects)")
    ap.add_argument("--copy-to", default="", choices=["", "altium", "kicad"],
                    help="copy the listed parts that this tool lacks into the hub library (and into --project)")
    ap.add_argument("--part", default="", help="show one part (its key or name)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    a = ap.parse_args(argv)
    ws = Workspace(a.workspace, a.lib, offline=True)
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    if a.part:
        res = build(ws)
        r = next((r for r in res["rows"] if a.part in (r["key"], r["name"]) or mpn_key(a.part) == r["key"]), None)
        if not r:
            print(f"no part {a.part!r} in the library hub", file=sys.stderr)
            return 2
        p = part(ws, r["key"])
        if a.json:
            print(json.dumps({k: v for k, v in p.items() if k not in ("pictures", "symbol")}, indent=1, default=str))
        else:
            print(f"{p['name']}  {p['mpn']}  {p['manufacturer']}")
            for c in p["copies"]:
                print(f"  {c['tool_label']:<11} {c['state']:<9} {c['where']}  updated {c['updated'] or '?'} by {c['updated_by'] or 'not recorded'}")
                for d in c["diffs"] or []:
                    print(f"              {d['what']}: {d['text']}")
            for e in p["history"][:15]:
                print(f"  {e.get('when', '')[:19]}  {e.get('who') or '':<24} {e.get('what')} {e.get('tool_label') or ''} {e.get('detail') or ''}")
        return 0
    res = listing(ws, a.search, split(a.have), split(a.lack), split(a.state), size=500, tool=a.tool,
                  project=str(Path(a.project).resolve()) if a.project else "")
    if a.copy_to:
        out = copy_parts(ws, [r["key"] for r in res["rows"] if a.copy_to not in r["cells"]], a.copy_to,
                         str(Path(a.project).resolve()) if a.project else "")
        if a.json:
            print(json.dumps(out, indent=1, default=str))
        else:
            print(f"copied {len(out['copied'])} part(s) to {LABEL[a.copy_to]} in {out['library']}" +
                  (f"; {out['project']}" if out["project"] else ""))
            for x in out["skipped"]:
                print(f"  skipped {x['name']}: {x['why']}")
        return 0
    if a.json:
        print(json.dumps(res, indent=1, default=str))
    else:
        s = res["summary"]
        print(f"{s['parts']} parts ({s['ecadlib']} built by ecadlib, {s['library']} in your libraries); "
              f"{s['different']} different, {s['behind']} behind, {s['held']} held, {s['eol']} end of life")
        print(_table(res["rows"], res["tools"]))
        for e in res["errors"][:10]:
            print(f"couldn't read {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
