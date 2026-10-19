"""Work out a library's rules from what the user already has, and write ecadlib.toml with minimal setup.

    ecadlib init [--from DIR_OR_FILE ...] [--a365 FILE] [--yes] [--force] [-o ecadlib.toml] [--json]

Scans (recursively for folders):
  .SchLib            component names, parameters actually used and how often, designators, footprint links
  .PcbLib            footprint names, which mechanical layers hold courtyard / assembly / 3D bodies, pad sizes
  .DbLib/.SVNDbLib   field mappings (= parameter names) and the table (sheet, SQLite) rows when the data file is
                     reachable next to it; tables are categories
  parts tables       .xlsx/.csv/.sqlite with a Library Ref / Part Number column (a DbLib's data on its own)
  Altium 365 lists   a JSON list of components {name, folder, parameters{...}, symbol, footprint} (a365_sync), the
                     Nexar node shape, or an `upstream check-a365 --state` file (names only)
  .kicad_sym/.pretty KiCad symbol names and properties, footprint names and pads
  BOMs               .csv/.xlsx column headers (what the BOM reader would map, and where it had to guess)

and proposes each setting with its evidence and a confidence. Names are pattern-mined: each name is split into
tokens that are classified as package (0603, C0402), value (10K, 4K7, 100N, 100nF), tolerance (1%), voltage
(16V), dielectric (X7R), power, the part's own MPN or manufacturer, a category word (RES/CAP) or a literal;
the commonest template per category wins (a template that is the same with fewer fields counts for it).
Footprints: IPC-7351 names (RESC1608X55N; the last letter is the density), KiCad names, or a mined template.
Density comes from IPC names, ecadlib's own tags and, for chip parts 0603 and up, the toe-to-toe pad length
compared with footprint_gen at L/N/M. Where the evidence is split, `init` asks one short question (with --yes it
takes the best guess). An existing ecadlib.toml is never replaced without confirmation: the diff is shown."""
import argparse, contextlib, json, re, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path

from . import rules as RU

IMPERIAL = ("01005", "0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2512")
METRIC = ("0402", "0603", "1005", "1608", "2012", "3216", "3225", "4532", "5025", "6332")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "http_cache", ".venv", "venv", ".svn"}
MAX_FILES = 3000
PARAM_SYN = {
    "Manufacturer Part Number": ("mpn", "manufacturerpartnumber", "mfrpartnumber", "mfrpn", "mfgpartnumber", "mfrpartno",
                                 "manufacturerpn", "mfrpart", "manufacturerpart", "mfgpn", "mfrno", "manufacturerpartno",
                                 "partnumbermfr", "mfrpartnum", "manufacturerpartnum"),
    "Manufacturer": ("manufacturer", "mfr", "mfg", "manufacturername", "mfrname", "maker", "mfgname"),
    "Supplier 1": ("supplier1", "supplier", "vendor", "vendor1", "distributor", "distributor1"),
    "Supplier Part Number 1": ("supplierpartnumber1", "supplierpartnumber", "supplierpn", "supplierpn1", "spn", "vendorpn",
                               "vendorpartnumber", "distributorpn", "distributorpartnumber", "supplierpartno", "vendorpartno"),
    "Supplier 2": ("supplier2", "vendor2", "distributor2"),
    "Supplier Part Number 2": ("supplierpartnumber2", "supplierpn2", "vendorpn2", "vendorpartnumber2", "distributorpn2"),
    "ComponentLink2URL": ("componentlink2url", "datasheet", "datasheeturl", "datasheetlink", "helpurl", "datasheetlocation"),
    "Value": ("value", "val"),
}
NOT_PARAMS = {"comment", "description", "designator", "libraryref", "librarypath", "footprintref", "footprintpath",
              "partnumber", "footprint", "reference", "kidescription", "kikeywords", "kifpfilters", "componentlink1description",
              "componentlink2description", "hiddennetname", "pcb3dref", "pcb3dpath"}
FOLDER_WORDS = [("Resistors", r"resist|^res$|^r$"), ("Capacitors", r"capac|^caps?$|^c$"), ("Inductors", r"induct|^ind$|^l$|ferrite|bead"),
                ("Diodes", r"diode|^led|zener|tvs"), ("Transistors", r"transist|mosfet|fet$|bjt|^q$"),
                ("Crystals", r"crystal|oscill|xtal|resonat"), ("Connectors", r"connect|header|^conn|^j$"),
                ("ICs", r"^ics?$|integrated|semi|mcu|micro|regulat|interface|logic|analog|power|memory|^u$|amplif|sensor")]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def canonical_of(house):
    n = _norm(house)
    return next((c for c, syn in PARAM_SYN.items() if n in syn), None)


def category_of(*hints):
    """Category from designator ('R?', 'R'), a folder or table name, a name prefix (RES_...)."""
    for h in hints:
        h = str(h or "").strip()
        if not h:
            continue
        m = re.match(r"^([A-Z]{1,3})\??\d*$", h)
        if m and m.group(1) in RU.REF_CLASS:
            return RU.REF_CLASS[m.group(1)]
        for seg in reversed([x for x in re.split(r"[/\\]", h) if x]):
            s = re.sub(r"\$$", "", seg).strip().lower()
            for cat, rx in FOLDER_WORDS:
                if re.search(rx, s):
                    return cat
    return None


def name_category(name):
    m = re.match(r"^(RES|CAP|IND|R|C|L)(?=[_\-\s]|\d{4}[_\-])", str(name), re.I)
    return {"res": "Resistors", "r": "Resistors", "cap": "Capacitors", "c": "Capacitors", "ind": "Inductors",
            "l": "Inductors"}[m.group(1).lower()] if m else None


class Library:
    """Everything the scanners found, in one shape."""
    def __init__(self):
        self.components, self.footprints, self.boms, self.files, self.warnings = [], [], [], Counter(), []
        self.sym_pins = []                        # per SchLib symbol: (name, [(x, y, length) of each pin], DXP units)
        self.pin_names = []                       # every pin name of every symbol (house_style: active-low notation)

    def add_component(self, name, origin, params=None, category=None, folder="", symbol="", footprint="", designator="",
                      description=""):
        params = {str(k): str(v) for k, v in (params or {}).items() if str(k).strip()}
        cat = (category or category_of(designator) or category_of(folder) or name_category(name)
               or category_of(params.get("Category", ""), params.get("Type", "")) or "Misc")
        self.components.append({"name": str(name), "category": cat, "folder": folder, "params": params, "symbol": symbol,
                                "footprint": footprint, "designator": designator, "description": description, "origin": origin})

    def kinds(self):
        return dict(self.files)


# ------------------------------------------------------------------ scanners
def scan(paths, a365=None):
    """Scan files and folders -> Library. a365: extra Altium 365 component lists (paths, or lists of dicts)."""
    lib = Library()
    files = []
    for p in paths or []:
        p = Path(p).expanduser()
        if p.is_dir():
            found = []
            for f in sorted(p.rglob("*")):
                if len(found) >= MAX_FILES:
                    lib.warnings.append(f"stopped after {MAX_FILES} files under {p}")
                    break
                if f.is_file() and not any(part in SKIP_DIRS for part in f.relative_to(p).parts[:-1]):
                    found.append(f)
            own = _own_output_dirs(p, found)
            for d in sorted(own):
                lib.warnings.append(f"skipped {d.as_posix()}: ecadlib's own output (it would teach ecadlib its own defaults)")
            files += [(f, False) for f in found if not any(d in f.parents for d in own)]
        elif p.is_file():
            files.append((p, True))
        else:
            lib.warnings.append(f"{p}: not found")
    for x in a365 or []:
        if isinstance(x, (list, dict)):
            scan_a365_data(lib, x, "Altium 365 list")
        else:
            files.append((Path(x), True))
    for f, explicit in files:
        try:
            scan_file(lib, f, explicit)
        except Exception as e:                    # one unreadable file must not stop the scan
            lib.warnings.append(f"{f}: {type(e).__name__}: {e}")
    return lib


OWN_OUTPUTS = {"librarian_state.json", "kicad_export.json", "kicad_dblib.json", "release_check.csv"}


def _own_output_dirs(root, files):
    """Folders under root (not root itself) that ecadlib wrote: a librarian / kicad-export output folder, or a whole
    workspace (uploads/ next to altium_models/, e.g. `ecadlib demo`). Scanning them would learn ecadlib's own defaults
    (and its report columns as BOM headers)."""
    dirs = set()
    for f in files:
        if f.name in OWN_OUTPUTS:
            dirs.add(f.parent)
        for d in f.parents:
            if d == root or root not in d.parents:
                break
            if (d / "uploads").is_dir() and (d / "altium_models").is_dir():
                dirs.add(d)
    return {d for d in dirs if d != root and not any(o in d.parents for o in dirs)}


def scan_file(lib, f, explicit=False):
    suf = f.suffix.lower()
    if suf == ".schlib":
        scan_schlib(lib, f)
    elif suf == ".pcblib":
        scan_pcblib(lib, f)
    elif suf in (".dblib", ".svndblib"):
        scan_dblib(lib, f)
    elif suf == ".kicad_sym":
        scan_kicad_sym(lib, f)
    elif suf == ".kicad_mod":
        scan_kicad_mod(lib, f)
    elif suf == ".json" and (explicit or re.search(r"a365|altium.?365|components|workspace", f.name, re.I)) \
            and not re.search(r"plan|report|manifest|package", f.name, re.I):
        scan_a365_data(lib, json.loads(f.read_text(encoding="utf-8")), str(f), explicit)
    elif suf in (".csv", ".xlsx", ".xlsm", ".tsv") or (suf in (".sqlite", ".db") and explicit):
        scan_table(lib, f, explicit)


def scan_schlib(lib, f):
    from . import altium_lib as AL
    _, syms = AL.read_schlib(f)
    lib.files["SchLib"] += 1
    for s in syms:
        recs, params, desig, fp, desc = s["records"], {}, "", "", ""
        for r in recs:
            rt = r.get("RECORD")
            if rt == "1":
                desc = r.get("COMPONENTDESCRIPTION", "")
            elif rt == "34":
                desig = r.get("TEXT", "")
            elif rt == "41" and r.get("NAME") and r.get("NAME") not in ("Comment", "HiddenNetName") and "OWNERINDEX" not in r:
                params[r["NAME"]] = r.get("TEXT", "")
            elif rt == "45" and r.get("MODELTYPE", "").upper() == "PCBLIB" and not fp:
                fp = r.get("MODELNAME", "")
        lib.add_component(s["name"], f"SchLib {f.name}", params, None, f.stem if category_of(f.stem) else "", s["name"], fp,
                          desig, desc)
        pins = [(r["x"], r["y"], r["length"]) for r in recs if r.get("RECORD") == "2" and not r.get("mode")]
        lib.pin_names += [r["name"] for r in recs if r.get("RECORD") == "2" and not r.get("mode") and r.get("name")]
        if pins:
            lib.sym_pins.append((s["name"], pins))


def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def scan_pcblib(lib, f):
    from . import altium_lib as AL
    lib.files["PcbLib"] += 1
    for fp in AL.read_pcblib(f):
        mm = AL.to_mm
        pads, graphics, texts, bodies = [], defaultdict(list), [], []
        for r in fp["records"]:
            t = r["type"]
            if t == "pad":
                w, h = mm(r["w"]), mm(r["h"])
                if round(r["rot"]) % 180 == 90:
                    w, h = h, w
                pads.append((r["designator"], mm(r["x"]), mm(r["y"]), w, h))
            elif t == "track":
                graphics[r["layer"]] += [(mm(r["x1"]), mm(r["y1"])), (mm(r["x2"]), mm(r["y2"]))]
            elif t == "arc":
                c, rr = (mm(r["cx"]), mm(r["cy"])), mm(r["r"])
                graphics[r["layer"]] += [(c[0] - rr, c[1] - rr), (c[0] + rr, c[1] + rr)]
            elif t == "fill":
                graphics[r["layer"]] += [(mm(r["x1"]), mm(r["y1"])), (mm(r["x2"]), mm(r["y2"]))]
            elif t == "region":
                graphics[r["layer"]] += [(mm(x), mm(y)) for x, y in r["pts"]]
            elif t == "body":
                bp = AL.parse_params(r.get("params", ""))
                v7 = bp.get("V7_LAYER", "")
                m = re.match(r"MECHANICAL(\d+)", v7)
                bodies.append(56 + int(m.group(1)) if m and int(m.group(1)) <= 16 else r["layer"])   # 17..32: reader's id
            elif t == "text":
                texts.append((r["text"], r["layer"]))
        lib.footprints.append({"name": fp["name"], "origin": f"PcbLib {f.name}", "kind": "altium", "pads": pads,
                               "graphics": {k: _bbox(v) for k, v in graphics.items()}, "texts": texts, "bodies": bodies,
                               "description": fp["params"].get("DESCRIPTION", "")})


def scan_kicad_sym(lib, f):
    from . import sexpr
    t = sexpr.parse(f.read_text(errors="replace", encoding="utf-8"))
    lib.files["kicad_sym"] += 1
    for s in sexpr.find(t, "symbol"):
        props = {p[1]: p[2] for p in sexpr.find(s, "property") if len(p) > 2}
        params = {k: v for k, v in props.items() if k not in ("Reference", "Value", "Footprint", "Datasheet", "Description")
                  and not k.startswith("ki_")}
        if props.get("Value") and str(props.get("Value")) != str(s[1]):
            params["Value"] = props["Value"]
        lib.add_component(s[1], f"KiCad {f.name}", params, None, "", s[1], props.get("Footprint", "").split(":")[-1],
                          props.get("Reference", ""), props.get("Description", ""))
        lib.pin_names += [str(nm[1]) for pin in _walk(s, "pin") for nm in sexpr.find(pin, "name") if len(nm) > 1 and nm[1] != "~"]


def _walk(node, head):
    """Every list under node (any depth) whose first element == head: a KiCad symbol's pins sit in its unit symbols."""
    for c in node:
        if isinstance(c, list) and c:
            if c[0] == head:
                yield c
            else:
                yield from _walk(c, head)


def scan_kicad_mod(lib, f):
    from . import sexpr
    text = f.read_text(errors="replace", encoding="utf-8")
    t = sexpr.parse(text)
    lib.files["kicad_mod"] += 1
    pads = [(p["number"], p["x"], -p["y"], p["w"], p["h"]) for p in sexpr.pads(t) if "F.Cu" in p["layers"] or "*.Cu" in p["layers"]]
    tags = " ".join(str(x) for x in (sexpr.first(t, "tags") or [])[1:]) + " " + " ".join(str(x) for x in (sexpr.first(t, "descr") or [])[1:])
    lib.footprints.append({"name": str(t[1]).split(":")[-1], "origin": f"KiCad {f.parent.name}/{f.name}", "kind": "kicad",
                           "pads": pads, "graphics": {}, "texts": [], "bodies": [], "description": tags})


def scan_a365_data(lib, data, origin, explicit=True):
    """A list of component dicts, Nexar component nodes, or a check-a365 --state file ({name: snapshot})."""
    if isinstance(data, dict) and isinstance(data.get("components"), list):
        data = data["components"]
    if isinstance(data, dict):
        if not all(isinstance(v, dict) and ("revision" in v or "symbols" in v) for v in data.values()):
            return
        lib.files["Altium 365 (state, names only)"] += 1
        for name in data:
            lib.add_component(name, f"A365 {Path(origin).name}")
        return
    if not isinstance(data, list) or not data or not all(isinstance(x, dict) and "name" in x for x in data):
        return
    if not explicit and not any(("parameters" in x or "folder" in x or "details" in x) for x in data):
        return
    lib.files["Altium 365 list"] += 1
    for x in data:
        params = x.get("parameters")
        d = x.get("details") or {}
        if isinstance(params, list):
            params = {p.get("name"): p.get("value", "") for p in params if isinstance(p, dict)}
        elif params is None:
            params = {p["name"]: p.get("value", "") for p in d.get("parameters") or [] if isinstance(p, dict) and p.get("name")}
        folder = x.get("folder") or ""
        if isinstance(folder, dict):
            folder = folder.get("path") or folder.get("name") or ""
        sym = x.get("symbol") or ((d.get("symbols") or [{}])[0] or {}).get("name", "")
        fp = x.get("footprint") or ((d.get("footprints") or [{}])[0] or {}).get("name", "")
        lib.add_component(x["name"], f"A365 {Path(str(origin)).name}", params, None, folder.replace("\\", "/"),
                          sym if isinstance(sym, str) else "", fp if isinstance(fp, str) else "", x.get("designator", ""),
                          x.get("description") or x.get("comment") or "")


def _key_col(cols):
    return next((c for c in cols if _norm(c) in ("partnumber", "partid", "partno", "internalpartnumber", "ipn")), None)


def add_table_rows(lib, table, rows, origin):
    """Rows of a DbLib table (dicts) -> components."""
    if not rows:
        return
    cols = list(rows[0])
    key = _key_col(cols) or next((c for c in cols if _norm(c) == "libraryref"), cols[0])
    for r in rows:
        name = str(r.get(key) or "").strip()
        if not name or name.lower() == "nan":
            continue
        params = {c: ("" if str(v) == "nan" else v) for c, v in r.items() if c != key and _norm(c) not in NOT_PARAMS}
        lib.add_component(name, origin, params, category_of(table) or category_of(r.get("Library Ref", "")), str(table).rstrip("$"),
                          str(r.get("Library Ref") or ""), str(r.get("Footprint Ref") or ""), "", str(r.get("Description") or ""))


def read_table_file(f):
    """{table: [row dicts]} from .xlsx / .csv / .sqlite / .db."""
    import pandas as pd
    suf = f.suffix.lower()
    if suf in (".sqlite", ".db", ".sqlite3"):
        out = {}
        with contextlib.closing(sqlite3.connect(Path(f).resolve().as_uri() + "?mode=ro", uri=True)) as con:   # closed: Windows locks open files
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                cur = con.execute(f'SELECT * FROM "{t}"')
                cols = [c[0] for c in cur.description]
                out[t] = [dict(zip(cols, (str(v) if v is not None else "" for v in row))) for row in cur.fetchall()]
        return out
    if suf in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(f, sheet_name=None, dtype=str)
        return {k: v.fillna("").to_dict("records") for k, v in sheets.items()}
    df = pd.read_csv(f, dtype=str, sep=None, engine="python").fillna("")
    return {f.stem: df.to_dict("records")}


def scan_table(lib, f, explicit=False):
    """A parts table (has a Library Ref or Part Number + Footprint Ref column) or a BOM (headers only)."""
    from . import bom_triage as T
    if f.suffix.lower() in (".sqlite", ".db"):
        for t, rows in read_table_file(f).items():
            add_table_rows(lib, t, rows, f"table {f.name}[{t}]")
        lib.files["parts table"] += 1
        return
    try:
        if f.suffix.lower() in (".xlsx", ".xlsm"):
            import pandas as pd
            heads = {k: [str(c) for c in v.columns] for k, v in pd.read_excel(f, sheet_name=None, nrows=1, dtype=str).items()}
        else:
            heads = {f.stem: [str(c) for c in T._read_csv(f).iloc[0]]}
    except Exception:
        return
    if any({"srcsymbol", "srcfootprint", "status"} <= {_norm(c) for c in hs} for hs in heads.values()):
        lib.warnings.append(f"skipped {f.name}: an ecadlib triage report (it would teach ecadlib its own columns)")
        return
    if any({"libraryref", "footprintref"} & {_norm(c) for c in hs} for hs in heads.values()):
        for t, rows in read_table_file(f).items():
            add_table_rows(lib, t, rows, f"table {f.name}[{t}]")
        lib.files["parts table"] += 1
        return
    try:
        lines, mapping = T.read_bom(f)
    except Exception:
        return
    raw = T._read_csv(f) if f.suffix.lower() not in (".xlsx", ".xlsm") else None
    headers = list(mapping.values())
    if raw is not None:
        row = max(range(min(10, len(raw))), key=lambda i: len(T.map_headers(list(raw.iloc[i]))))
        headers = [str(h).strip() for h in raw.iloc[row]]
    lib.boms.append({"file": str(f), "headers": headers, "mapping": mapping, "lines": len(lines)})
    lib.files["BOM"] += 1


def _ini_sections(text):
    out, cur = [], None
    for ln in text.splitlines():
        ln = ln.strip()
        m = re.fullmatch(r"\[(.+)\]", ln)
        if m:
            cur = (m.group(1), {})
            out.append(cur)
        elif cur and "=" in ln:
            k, v = ln.split("=", 1)
            cur[1][k.strip()] = v.strip()
    return out


def scan_dblib(lib, f):
    secs = _ini_sections(f.read_text(encoding="utf-8", errors="replace"))
    links = next((d for n, d in secs if n == "DatabaseLinks"), {})
    tables = [d.get("TableName", "") for n, d in secs if n.startswith("Table") and d.get("Enabled", "True") != "False"]
    fields = defaultdict(list)
    for n, d in secs:
        if n.startswith("FieldMap"):
            opt = dict(kv.split("=", 1) for kv in d.get("Options", "").split("|") if "=" in kv)
            pname = opt.get("ParameterName", opt.get("FieldNameOnly", "")).strip("[]")
            if pname:
                fields[opt.get("TableNameOnly", "")].append(pname)
    lib.files["DbLib"] += 1
    # the data file: LibraryDatabasePath (relative when DatabasePathRelative=1), else the connection string
    cands = [links.get("LibraryDatabasePath", "")]
    cs = links.get("ConnectionString", "")
    cands += re.findall(r"(?:Data Source|Database|DBQ)=([^;\"]+)", cs, re.I)
    data = None
    for c in filter(None, cands):
        for p in (f.parent / c, Path(c), f.parent / Path(c.replace("\\", "/")).name):
            try:
                if p.is_file():
                    data = p
                    break
            except OSError:
                continue
        if data:
            break
    rows_read = False
    if data and data.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".sqlite", ".db", ".csv"):
        try:
            for t, rows in read_table_file(data).items():
                add_table_rows(lib, t, rows, f"DbLib {f.name} [{t}]")
            rows_read = True
        except Exception as e:
            lib.warnings.append(f"{f.name}: could not read {data.name} ({type(e).__name__}: {e}); used the field mappings only")
    elif data:
        lib.warnings.append(f"{f.name}: {data.suffix} data ({data.name}) can't be read here; used the field mappings only")
    if not rows_read:
        lib.warnings.append(f"{f.name}: data file not found or not readable; parameter names come from its field mappings")
        for t in tables or list(fields):
            params = {p: "" for p in fields.get(t, []) + fields.get(t.rstrip("$"), []) if _norm(p) not in NOT_PARAMS}
            lib.components.append({"name": "", "category": category_of(t) or "Misc", "folder": t.rstrip("$"), "params": params,
                                   "symbol": "", "footprint": "", "designator": "", "description": "", "origin": f"DbLib {f.name} [{t}] (field map)",
                                   "fieldmap_only": True})


# ------------------------------------------------------------------ name mining
def parse_token_value(tok, cat):
    """'10K' '4K7' '100N' '4u7' '100nF' '0R1' -> value in base units, for a category; None if it is not a value."""
    from .kicad_index import parse_value
    if not re.match(r"^\d", tok) or cat not in RU.UNIT:
        return None
    s = re.sub(r"(?i)(ohms?|Ω)$", "", tok)
    if cat == "Resistors":
        s = s.replace("K", "k")
        if re.fullmatch(r"\d+", s) is None and not re.fullmatch(r"\d+(\.\d+)?[kMGR]\d*|\d+R\d*", s):
            return None
    else:
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return None                            # a bare number is not a capacitance
        s = re.sub(r"[PNU]", lambda m: m.group(0).lower(), s)
        if cat == "Inductors":
            s = re.sub(r"M(?=\d|H|$)", "m", s)
        if not re.fullmatch(r"\d+(\.\d+)?[pnumµ]\d*[FfHh]?|\d+[pnumµ]\d+[FfHh]?", s):
            return None
    return parse_value(s)


def value_styles(tok, cat):
    v = parse_token_value(tok, cat)
    if v is None:
        return None
    return [s for s in RU.VALUE_STYLES if s != "keep" and RU.fmt_number(v, cat, s) == tok]


def _params_ci(params, *names):
    low = {_norm(k): v for k, v in params.items()}
    for n in names:
        for syn in PARAM_SYN.get(n, (n,)):
            if low.get(_norm(syn)):
                return str(low[_norm(syn)])
    return ""


def classify(tok, comp, styles_seen):
    """One name token -> placeholder or literal."""
    cat, params = comp["category"], comp["params"]
    mpn, mfr = _params_ci(params, "Manufacturer Part Number"), _params_ci(params, "Manufacturer")
    up = tok.upper()
    if mpn and up == mpn.upper():
        return "{mpn}"
    if mfr and up == mfr.upper():
        return "{manufacturer}"
    if tok in IMPERIAL and cat in RU.PASSIVES + ("Diodes",):
        return "{package}"
    if tok in METRIC and tok not in IMPERIAL and cat in RU.PASSIVES + ("Diodes",):
        return "{metric}"
    m = re.fullmatch(r"([A-Za-z]{1,4})(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)", tok)
    if m and cat in RU.PASSIVES + ("Diodes",):
        lit = m.group(1)
        lit = "{cat3}" if lit.upper() == RU.CAT3.get(cat) else "{ref}" if lit.upper() == RU.CAT_REF.get(cat) else lit
        return lit + "{package}"
    if re.fullmatch(r"\d+(\.\d+)?%", tok):
        return "{tolerance}"
    if re.fullmatch(r"\d+(\.\d+)?V", tok, re.I) and cat in RU.PASSIVES + ("Diodes",):
        return "{voltage}"
    if re.fullmatch(r"X[5-8][RSTPV]|C0G|NP0|Y5V|Z5U", up):
        return "{dielectric}"
    if re.fullmatch(r"(\d+/\d+|\d+(\.\d+)?)W", tok, re.I):
        return "{power}"
    st = value_styles(tok, cat) if cat in RU.PASSIVES else None
    if st is not None:
        styles_seen.append(tuple(st) or ("keep",))
        return "{value}"
    if up == RU.CAT3.get(cat) and cat in RU.PASSIVES:
        return "{cat3}"
    if up == RU.CAT_REF.get(cat) and cat in RU.PASSIVES:
        return "{ref}"
    return None                                    # literal


def looks_like_mpn(name):
    n = re.sub(r"[^A-Za-z0-9]", "", name)
    return len(n) >= 5 and re.search(r"\d", n) and re.search(r"[A-Za-z]", n) and not re.search(r"(?<!\d)(0402|0603|0805|1206)(?!\d)", name)


def name_template(comp, styles_seen):
    """Component name -> template string, or None when it is only literals (a free-form name)."""
    name = comp["name"]
    mpn = _params_ci(comp["params"], "Manufacturer Part Number")
    if mpn and name.upper() == mpn.upper():
        return "{mpn}"
    parts = re.split(r"([_\- ]+)", name)
    out, fields = [], 0
    for i, p in enumerate(parts):
        if i % 2:
            out.append(p)
            continue
        c = classify(p, comp, styles_seen) if p else ""
        if c:
            fields += 1
        out.append(c if c is not None else p)
    if not fields:
        return "{mpn}?" if looks_like_mpn(name) and comp["category"] not in RU.PASSIVES else None
    return "".join(out)


def _tokens(t):
    return [x for x in re.split(r"(\{\w+\})", t) if x]


def reduces_to(small, big):
    """True when `small` is `big` with some fields (and one neighbouring separator each) left out, i.e. what
    rules.render() makes of `big` when those fields are empty."""
    if small == big:
        return True
    names = RU._template_fields(big)
    if not names or len(names) > 8:
        return False
    import itertools
    for k in range(1, len(names)):
        for drop in itertools.combinations(range(len(names)), k):
            f = {n: ("" if i in drop else f"\x00{n}\x00") for i, n in enumerate(names)}
            if len(set(names)) != len(names):
                break
            r = re.sub(r"\x00(\w+)\x00", r"{\1}", RU.render(big, f))
            if r == small:
                return True
    return False


def best_template(templates):
    """Counter of templates -> [(template, votes incl. reductions, own count)] best first."""
    cands = []
    for t, n in templates.items():
        votes = sum(m for s, m in templates.items() if reduces_to(s, t))
        cands.append((t, votes, n))
    cands.sort(key=lambda x: (-x[1], -len(RU._template_fields(x[0])), -x[2], x[0]))
    return cands


class Setting(dict):
    """{key, value, confidence, evidence [...], source, alternatives [{value, share}], question?}"""


def _label(c):
    return "high" if c >= 0.8 else "medium" if c >= 0.5 else "low"


MIN_SAMPLES = 3          # fewer parts / footprints / symbols than this behind a setting: at most "low"
MIN_COVERAGE = 0.5       # a feature on fewer than half of the footprints scales the confidence down
LOW_CAP = 0.45


def support(share, n=None, have=None, population=None):
    """Confidence of a learned value from the share of samples that agree on it (share), how many samples there were
    (n) and, for a feature every footprint could carry, how many carry it (have) out of how many were read
    (population). 1 of 1 agreeing is not "high" when 47 other footprints say nothing."""
    c = share
    if population and have is not None and have < population * MIN_COVERAGE:
        c *= have / (population * MIN_COVERAGE)
    if n is not None and n < MIN_SAMPLES:
        c = min(c, LOW_CAP)
    return c


def mk(key, value, confidence, evidence, source, alternatives=(), ask=None, n=None, have=None, population=None):
    """A Setting. n / have / population (see support()) scale the confidence for thin evidence."""
    evidence = list(evidence)
    raw, confidence = confidence, support(confidence, n, have, population)
    if confidence < raw:
        why = [f"too little evidence: only {n} sample(s)"] if n is not None and n < MIN_SAMPLES else []
        if population and have is not None and have < population * MIN_COVERAGE:
            why.append(f"seen in only {have} of {population}")
        evidence.insert(1, "; ".join(why) or "too little evidence")
    s = Setting(key=key, value=value, confidence=round(confidence, 2), level=_label(confidence),
                evidence=evidence[:6], source=source, alternatives=list(alternatives))
    if ask:
        s["question"] = ask
    return s


def _origins(comps, n=3):
    c = Counter(x["origin"] for x in comps)
    more = f" and {len(c) - n} more" if len(c) > n else ""
    return ", ".join(f"{k} ({v})" for k, v in c.most_common(n)) + more


def infer_naming(lib):
    """naming.component / passive / by_category / value_style / footprint."""
    out = []
    comps = [c for c in lib.components if c.get("name") and not c.get("fieldmap_only")]
    by_cat = defaultdict(list)
    for c in comps:
        by_cat[c["category"]].append(c)
    styles_seen, per_cat = [], {}
    for cat, cs in by_cat.items():
        temps, examples = Counter(), defaultdict(list)
        for c in cs:
            t = name_template(c, styles_seen if cat in RU.PASSIVES else [])
            if t is None:
                continue
            t = "{mpn}" if t == "{mpn}?" else t
            temps[t] += 1
            examples[t].append(c["name"])
        if temps:
            per_cat[cat] = (best_template(temps), temps, examples, cs)
    other = [c for c in per_cat if c not in RU.PASSIVES]
    pooled = Counter()
    for c in other:
        pooled.update(per_cat[c][1])
    pooled_ex = defaultdict(list)
    for c in other:
        for t, ex in per_cat[c][2].items():
            pooled_ex[t] += ex
    if pooled:
        ranked = best_template(pooled)
        t, votes, _ = ranked[0]
        total = sum(pooled.values())
        conf = votes / total
        unmatched = sum(len(per_cat[c][3]) for c in other) - total
        if unmatched:
            conf *= total / (total + unmatched)
        alts = [{"value": a, "share": round(v / total, 2)} for a, v, _ in ranked[1:3]]
        ev = [f"{ex}" for ex in pooled_ex[t][:3]] + [f"{votes} of {total + unmatched} non-passive names fit"]
        if not any(_params_ci(c["params"], "Manufacturer Part Number") for c in comps) and t == "{mpn}":
            ev.append("names look like manufacturer part numbers (no MPN parameter found to confirm)")
            conf *= 0.8
        ask = _question(f"Non-passive part names: which pattern do you use?", t, ranked, total)
        out.append(mk("naming.component", t, conf, ev, _origins([c for k in other for c in per_cat[k][3]]), alts, ask,
                      n=total + unmatched))
    passive_best = {}
    for cat in RU.PASSIVES:
        if cat not in per_cat:
            continue
        ranked, temps, examples, cs = per_cat[cat]
        t, votes, _ = ranked[0]
        total = len(cs)
        alts = [{"value": a, "share": round(v / total, 2)} for a, v, _ in ranked[1:3]]
        ev = examples[t][:3] + [f"{votes} of {total} {cat.lower()} names fit"]
        passive_best[cat] = mk(f"naming.by_category.{cat}", t, votes / total, ev, _origins(cs), alts,
                               _question(f"{cat} names: which pattern do you use?", t, ranked, total), n=total)
    vals = {s["value"] for s in passive_best.values()}
    if len(vals) == 1 and len(passive_best) >= 2:
        s = min(passive_best.values(), key=lambda x: x["confidence"])
        merged = mk("naming.passive", s["value"], s["confidence"],
                    [e for x in passive_best.values() for e in x["evidence"][:2]], "; ".join(x["source"] for x in passive_best.values()),
                    s["alternatives"], s.get("question"))
        out.append(merged)
    else:
        out += passive_best.values()
    # value style
    if styles_seen:
        votes = Counter()
        for st in styles_seen:
            for s in st:
                votes[s] += 1
        order = ["rkm", "rkm_lower", "si_unit", "si", "si_upper", "keep"]
        ranked = sorted(votes.items(), key=lambda kv: (-kv[1], order.index(kv[0])))
        best, n = ranked[0]
        total = len(styles_seen)
        ex = {"rkm": "4K7, 100N", "rkm_lower": "4k7, 100n", "si": "4.7k, 100n", "si_upper": "4.7K, 100N", "si_unit": "100nF", "keep": "as the BOM writes it"}
        ask = None
        if len(ranked) > 1 and ranked[1][1] == n and {best, ranked[1][0]} not in ({"rkm", "si_upper"}, {"rkm_lower", "si"}):
            ask = {"text": "Values in names: which style?", "choices": [r[0] for r in ranked[:3]], "default": best}
        out.append(mk("naming.value_style", best, n / total, [f"{n} of {total} value tokens are written {best} ({ex[best]})"],
                      "component names", [{"value": k, "share": round(v / total, 2)} for k, v in ranked[1:3]], ask, n=total))
    out += infer_footprint_naming(lib)
    return out


def _question(text, best, ranked, total):
    if len(ranked) < 2:
        return None
    share, second = ranked[0][1] / total, ranked[1][1] / total
    if share < 0.6 and second >= 0.2 and not reduces_to(ranked[1][0], best):
        return {"text": text, "choices": [r[0] for r in ranked[:3]], "default": best,
                "counts": {r[0]: r[2] for r in ranked[:3]}}
    return None


FP_KICAD = re.compile(r"_\d{4}Metric|_P\d+(\.\d+)?mm|\d+(\.\d+)?x\d+(\.\d+)?mm|^(SOT|SOD|SOIC|TSSOP|MSOP|QFN|LQFP|DFN)-\d")


def infer_footprint_naming(lib):
    names = [(f["name"], f["origin"]) for f in lib.footprints if f["kind"] == "altium"] or \
            [(f["name"], f["origin"]) for f in lib.footprints]
    names += [(c["footprint"], c["origin"]) for c in lib.components if c.get("footprint")]
    names = list(dict.fromkeys(names))
    if not names:
        return []
    kinds, ex = Counter(), defaultdict(list)
    chip_t = Counter()
    for n, o in names:
        k = "ipc7351" if RU.IPC_NAME.match(n) else "kicad" if FP_KICAD.search(n) else "other"
        kinds[k] += 1
        ex[k].append(n)
        if k == "other":
            m = re.fullmatch(r"([A-Za-z]{0,4})([_\-]?)(0201|0402|0603|0805|1206|1210|1812|2010|2512)([_\-]?)([A-Za-z]*)", n)
            if m:
                pre = m.group(1).upper()
                pre_t = "{ref}" if pre in ("R", "C", "L", "D") else "{cat3}" if pre in ("RES", "CAP", "IND") else m.group(1)
                chip_t[f"{pre_t}{m.group(2)}{{package}}{m.group(4)}{m.group(5)}"] += 1
    total = sum(kinds.values())
    best, n = kinds.most_common(1)[0]
    if best == "other":
        if chip_t:
            t, m = chip_t.most_common(1)[0]
            return [mk("naming.footprint", t, m / total * 0.9, ex["other"][:3] + [f"{m} of {total} footprint names fit (chip packages; others keep their names)"],
                       "footprint names", [{"value": k, "share": round(v / total, 2)} for k, v in kinds.most_common() if k != "other"],
                       n=total)]
        return [mk("naming.footprint", "kicad", 0.3, ex["other"][:3] + ["footprint names follow no pattern ecadlib knows; KiCad names kept"],
                   "footprint names")]
    alts = [{"value": k, "share": round(v / total, 2)} for k, v in kinds.most_common() if k != best and k != "other"]
    ask = None
    if len(kinds) > 1 and n / total < 0.6 and alts and alts[0]["share"] >= 0.25:
        ask = {"text": "Footprint names: KiCad style (R_0603_1608Metric) or IPC-7351 (RESC1608X55N)?", "choices": [best] + [a["value"] for a in alts], "default": best}
    return [mk("naming.footprint", best, n / total, ex[best][:3] + [f"{n} of {total} footprint names are {best} style"],
               "footprint names", alts, ask, n=total)]


# ------------------------------------------------------------------ parameters, folders
def infer_parameters(lib):
    out = []
    comps = [c for c in lib.components if not c.get("fieldmap_only")]
    altium = [c for c in comps if not c["origin"].startswith("KiCad")]
    pool = altium or comps
    fieldmaps = [c for c in lib.components if c.get("fieldmap_only")]
    if not pool and not fieldmaps:
        return out
    presence, values = Counter(), defaultdict(Counter)
    for c in pool:
        for k, v in c["params"].items():
            if str(v).strip():
                presence[k] += 1
                values[k][str(v).strip()] += 1
    names_seen = Counter(k for c in pool + fieldmaps for k in c["params"])
    # house names for ecadlib's parameters
    names, ev_names = {}, []
    for canon in RU.CANONICAL_PARAMS:
        hits = [(k, names_seen[k]) for k in names_seen if canonical_of(k) == canon]
        if canon == "ComponentLink2URL" and not altium:
            hits = []                                  # KiCad's Datasheet field is KiCad's own
        if not hits:
            continue
        house, n = max(hits, key=lambda kv: (kv[1], kv[0] == canon))
        if house != canon and not (canon == "ComponentLink2URL" and _norm(house) == "componentlink2url"):
            names[canon] = house
            ev_names.append(f"{house} = {canon} ({n} parts)")
    src = _origins(pool or fieldmaps)
    if names:
        out.append(mk("parameters.names", names, 0.9 if pool else 0.7, ev_names, src,
                      n=None if fieldmaps else len(pool)))      # a DbLib field map is one deliberate statement
    n = len(pool)
    if n >= 3:
        req = {}
        for k, m in presence.most_common():
            if m / n >= 0.9 and _norm(k) not in NOT_PARAMS:
                canon = canonical_of(k)
                if canon == "ComponentLink2URL":
                    req.setdefault("Datasheet", m)
                elif canon not in ("Supplier 1", "Supplier Part Number 1", "Supplier 2", "Supplier Part Number 2", "Value"):
                    req.setdefault(k, m)
        nd = sum(bool(str(c.get("description") or "").strip()) for c in pool)
        if nd / n >= 0.9:
            req["Description"] = nd
        if req:
            out.append(mk("parameters.required", list(req), min(1.0, 0.6 + 0.04 * n),
                          [f"{k}: filled on {m} of {n} parts" for k, m in req.items()], src))
        defaults = {}
        for k, cnt in values.items():
            if canonical_of(k) or _norm(k) in NOT_PARAMS or re.search(r"url|link|date|number|pn$|part|lcsc|supplier|vendor|"
                                                                      r"digi|mouser|sku|order|code|id$", k, re.I):
                continue
            v, m = cnt.most_common(1)[0]
            if len(cnt) == 1 and m >= max(5, 0.95 * n) and len(v) <= 40:
                defaults[k] = v
        if defaults:
            out.append(mk("parameters.defaults", defaults, 0.7, [f"{k} = {v} on every part ({presence[k]})" for k, v in defaults.items()], src))
    slots = [int(m.group(1)) for k in names_seen for m in [re.search(r"(?:supplier|vendor|distributor)\D*(\d+)$", k, re.I)] if m]
    if slots and max(slots) != 2:
        out.append(mk("parameters.supplier_slots", max(slots), 0.8, [f"supplier columns up to {max(slots)}"], src))
    return out


def infer_folders(lib):
    comps = [c for c in lib.components if c.get("folder")]
    a365 = [c for c in comps if c["origin"].startswith("A365")]
    use = a365 or [c for c in comps if c["origin"].startswith(("DbLib", "table"))]
    if not use:
        return []
    out = []
    by = defaultdict(Counter)
    for c in use:
        cat = category_of(c["folder"]) or c["category"]
        by[cat][c["folder"]] += 1
    folders = [f for cnt in by.values() for f in cnt]
    if a365:
        segs = [f.split("/") for f in folders]
        common = []
        for parts in zip(*segs):
            if len(set(parts)) == 1:
                common.append(parts[0])
            else:
                break
        root = "/".join(common) if len(common) < min(len(s) for s in segs) else "/".join(common[:-1])
    else:
        root = RU.DEFAULTS["folders"]["root"]
    mapping, ev = {}, []
    for cat, cnt in sorted(by.items()):
        f, m = cnt.most_common(1)[0]
        full = f if a365 else f"{root}/{f}"
        if full != f"{root}/{cat}":
            mapping[cat] = full
        ev.append(f"{cat}: {full} ({m} parts)")
    total = sum(sum(c.values()) for c in by.values())
    hits = sum(c.most_common(1)[0][1] for c in by.values())
    if root and root != RU.DEFAULTS["folders"]["root"]:
        out.append(mk("folders.root", root, 0.8, [f"every folder starts with {root}/"], _origins(use), n=len(use)))
    if mapping:
        out.append(mk("folders.map", mapping, hits / total, ev, _origins(use), n=total))
    return out


# ------------------------------------------------------------------ layers, density
def infer_layers(lib):
    """layers.* from the PcbLibs. Every footprint could carry each role, so a role seen in few of them is low
    confidence (support()). Two sanity rules: the courtyard is never a layer that holds the library's 3D bodies, and a
    .Designator on a non-overlay layer is not proposed when the library has silkscreen on the overlay (the new
    footprints would have no reference on silk)."""
    fps = [f for f in lib.footprints if f["kind"] == "altium" and f["pads"]]
    if not fps:
        return []
    roles = ("courtyard", "assembly", "body3d", "designator", "silkscreen")
    votes = {k: Counter() for k in roles}
    have = {k: 0 for k in roles}                  # footprints that carry the role at all
    for f in fps:
        for l in {l for l in f["bodies"] if 57 <= l <= 72}:   # the writer's (and rules') mechanical layers: 1..16
            votes["body3d"][l] += 1
        have["body3d"] += any(57 <= l <= 72 for l in f["bodies"])
    body = votes["body3d"].most_common(1)[0][0] if votes["body3d"] else None
    overlay_silk = 0
    for f in fps:
        px = [(x - w / 2, y - h / 2, x + w / 2, y + h / 2) for _, x, y, w, h in f["pads"]]
        pb = (min(b[0] for b in px), min(b[1] for b in px), max(b[2] for b in px), max(b[3] for b in px))
        mech = {l: b for l, b in f["graphics"].items() if b and 57 <= l <= 72}
        enclosing = [l for l, b in mech.items() if b[0] <= pb[0] + 0.01 and b[1] <= pb[1] + 0.01
                     and b[2] >= pb[2] - 0.01 and b[3] >= pb[3] - 0.01]
        area = lambda l: (mech[l][2] - mech[l][0]) * (mech[l][3] - mech[l][1])
        outer = max(enclosing, key=area) if enclosing else None
        not_crt = set(f["bodies"]) | {body}       # a layer that holds 3D bodies is never the courtyard
        crt = max([l for l in enclosing if l not in not_crt], key=area, default=None)
        if crt:
            votes["courtyard"][crt] += 1
            have["courtyard"] += 1
        asm = set()
        for t, l in f["texts"]:
            if t == ".Comment" and 57 <= l <= 72:
                votes["assembly"][l] += 1
                asm.add(l)
        des = {l for t, l in f["texts"] if t == ".Designator"}
        if len(des) > 1:                          # silk designator + the assembly drawing's one: the silk one is the role
            des = {l for l in des if not 57 <= l <= 72} or des
        for l in des:
            votes["designator"][l] += 1
        have["designator"] += bool(des)
        for l in mech:
            if l not in (crt, outer) and not any(t == ".Comment" for t, _ in f["texts"]):
                votes["assembly"][l] += 1
                asm.add(l)
        have["assembly"] += bool(asm)
        ov = [l for l in f["graphics"] if l in (33, 34)]
        if ov:
            votes["silkscreen"][33 if 33 in ov else ov[0]] += 1
            have["silkscreen"] += 1
        overlay_silk += bool(ov or any(l in (33, 34) for _, l in f["texts"]))
    out = []
    for role, cnt in votes.items():
        if not cnt:
            continue
        l, n = cnt.most_common(1)[0]
        total = sum(cnt.values())
        alts = [{"value": RU.layer_name(k), "share": round(v / total, 2)} for k, v in cnt.most_common()[1:3]]
        ask = None
        if alts and n / total < 0.6:
            ask = {"text": f"Which layer holds the {role}?", "choices": [RU.layer_name(l)] + [a["value"] for a in alts], "default": RU.layer_name(l)}
        how = {"courtyard": "the outline that encloses all pads", "assembly": "the .Comment text / body outline",
               "body3d": "3D bodies", "designator": ".Designator text", "silkscreen": "silkscreen lines"}[role]
        ev = [f"{RU.layer_name(l)} holds {how} in {n} of {total} footprints"]
        conf = n / total
        if role == "designator" and l not in (33, 34) and overlay_silk:
            conf = min(conf, 0.3)
            ev.append(f"not proposed: {overlay_silk} footprint(s) have silkscreen on the overlay, and a designator on "
                      f"{RU.layer_name(l)} would leave new footprints with no reference on silk")
        out.append(mk(f"layers.{role}", RU.layer_name(l), conf, ev + [f["name"] for f in fps[:2]],
                      _origins_fp(fps), alts, ask, n=have[role], have=have[role], population=len(fps)))
    return out


def _origins_fp(fps):
    c = Counter(f["origin"] for f in fps)
    return ", ".join(f"{k} ({v})" for k, v in c.most_common(3)) + (f" and {len(c) - 3} more" if len(c) > 3 else "")


_Z_CACHE = {}


def _chip_z(imp, kind):
    """Toe-to-toe length Z of footprint_gen's chip land pattern for an imperial size at densities A/B/C."""
    from . import footprint_gen as F
    key = (imp, kind)
    if key not in _Z_CACHE:
        specs = Path(F.__file__).with_name("package_specs")
        met = {"0603": "1608", "0805": "2012", "1206": "3216", "1210": "3225"}.get(imp)
        f = next((p for p in (specs / f"{kind}_{imp}_{met}Metric.json", specs / f"R_{imp}_{met}Metric.json") if p.exists()), None)
        if not f:
            _Z_CACHE[key] = None
        else:
            spec = json.loads(f.read_text(encoding="utf-8"))
            zs = {}
            for d in "ABC":
                _, rep = F.build(spec, d)
                ps = rep["pads"]
                zs[d] = max(abs(p["x"]) + p["w"] / 2 for p in ps) * 2
            _Z_CACHE[key] = zs
    return _Z_CACHE[key]


def infer_density(lib):
    votes, ev, voters = Counter(), [], 0
    for f in lib.footprints:
        m = RU.IPC_NAME.match(f["name"])
        if m:
            d = m.groups()[-1].upper()
            votes[d] += 2
            voters += 1
            if len(ev) < 2:
                ev.append(f"{f['name']}: IPC name ends in {d}")
            continue
        m = re.search(r"density[- ]?([ABC])\b", f.get("description", ""))
        if m:
            votes[{"A": "M", "B": "N", "C": "L"}[m.group(1)]] += 1
            voters += 1
            continue
        if len(f["pads"]) == 2:
            from .kicad_index import package_of
            imp = package_of(f["name"])
            km = re.match(r"^(R|C|L)[_\d]", f["name"])
            kind = km.group(1) if km else "R"
            zs = _chip_z(imp, kind) if imp in ("0603", "0805", "1206", "1210") else None
            if not zs:
                continue
            (_, x1, y1, w1, h1), (_, x2, y2, w2, h2) = f["pads"]
            horiz = abs(x1 - x2) >= abs(y1 - y2)
            z = (abs(x1 - x2) + (w1 + w2) / 2) if horiz else (abs(y1 - y2) + (h1 + h2) / 2)
            d, dz = min(((d, abs(z - zz)) for d, zz in zs.items()), key=lambda t: t[1])
            if dz <= 0.3:
                votes[{"A": "M", "B": "N", "C": "L"}[d]] += 1
                voters += 1
                if len(ev) < 4:
                    ev.append(f"{f['name']}: pads {z:.2f} mm toe to toe, IPC {imp} {'MNL'['ABC'.index(d)]} = {zs[d]:.2f}")
    if not votes:
        return []
    best, n = votes.most_common(1)[0]
    total = sum(votes.values())
    alts = [{"value": k, "share": round(v / total, 2)} for k, v in votes.most_common()[1:3]]
    ask = {"text": "IPC-7351 density for new footprints?", "choices": [best] + [a["value"] for a in alts], "default": best} \
        if alts and n / total < 0.6 else None
    return [mk("footprint.density", best, n / total, ev + [f"{n} of {total} votes for {best}"], _origins_fp(lib.footprints), alts, ask,
               n=voters)]


def infer_symbols(lib):
    """[symbols] pin_length and grid from the IC symbols in scanned SchLibs (8+ pins; passives say little), when they
    differ from ecadlib's (300 mil pins, 100 mil grid)."""
    syms = [(n, ps) for n, ps in lib.sym_pins if len(ps) >= 8]
    if not syms:
        return []
    from math import gcd
    lengths, grids = Counter(), Counter()
    for _, ps in syms:
        lengths.update(L * 10 for _, _, L in ps if L > 0)
        g = 0
        for x, y, _ in ps:
            g = gcd(gcd(g, x), y)
        grids[100 if g and g % 10 == 0 else 50 if g and g % 5 == 0 else 0] += 1
    out = []
    if lengths:
        L, n = lengths.most_common(1)[0]
        total = sum(lengths.values())
        g = 50 if grids.most_common(1)[0][0] == 50 else 100
        if L % 50 == 0 and L % g == 0 and L != RU.DEFAULTS["symbols"]["pin_length"]:     # only what differs from ecadlib's
            out.append(mk("symbols.pin_length", L, n / total, [f"{n} of {total} pins are {L} mil long"] + [n_ for n_, _ in syms[:2]],
                          f"{len(syms)} SchLib symbols", n=len(syms)))
    g, n = grids.most_common(1)[0]
    if g and g != RU.DEFAULTS["symbols"]["grid"]:
        out.append(mk("symbols.grid", g, n / len(syms), [f"pins of {n} of {len(syms)} symbols sit on a {g} mil grid"], f"{len(syms)} SchLib symbols",
                      n=len(syms)))
    return out


def infer_bom(lib):
    from . import bom_triage as T
    cols, ev = {}, []
    for b in lib.boms:
        for f, h in b["mapping"].items():
            if _norm(h) and T._norm(h) not in T.FIELDS.get(f, []):
                cols.setdefault(f, Counter())[h] += 1
    out = {f: c.most_common(1)[0][0] for f, c in cols.items()}
    if not out:
        return []
    ev = [f"{h} -> {f} (a fuzzy match today; written down so it never changes)" for f, h in out.items()]
    return [mk("bom.columns", out, 0.75, ev, ", ".join(Path(b["file"]).name for b in lib.boms[:3]))]


# ------------------------------------------------------------------ proposal
def infer(lib):
    settings = []
    for fn in (infer_naming, infer_parameters, infer_folders, infer_layers, infer_density, infer_bom, infer_symbols):
        try:
            settings += fn(lib)
        except Exception as e:                     # one analysis failing must not lose the others
            lib.warnings.append(f"{fn.__name__}: {type(e).__name__}: {e}")
    return settings


def _set(rules, key, value):
    parts = key.split(".")
    d = rules
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def written(s, choices=None):
    """A setting goes into the rules file when its confidence is medium or high, or the person chose its value."""
    return s["level"] != "low" or s["key"] in (choices or {})


def to_rules(settings, choices=None):
    """Settings (+ the person's choices {key: value}) -> a raw rules dict (only the inferred keys). Low-confidence
    settings are left out (ecadlib's default stays); propose() lists them as comments."""
    raw = {}
    for s in settings:
        if not written(s, choices):
            continue
        v = (choices or {}).get(s["key"], s["value"])
        _set(raw, s["key"], v)
    return raw


def skipped_lines(settings, choices=None):
    """Comment lines for the low-confidence settings to_rules() leaves out: what was seen, and why it is not used."""
    out = []
    for s in settings:
        if written(s, choices):
            continue
        v = json.dumps(s["value"], ensure_ascii=False)
        why = next((e for e in s["evidence"][1:] if e.startswith(("too little evidence", "not proposed"))),
                   s["evidence"][-1] if s["evidence"] else "")
        out.append(f"  {s['key']} = {v}   (confidence low, {int(s['confidence'] * 100)}%: {why})")
    if out:
        out.insert(0, "Not written (too little evidence in the library; ecadlib's default stays). If one is your "
                      "convention, set it yourself:")
    return out


def notes_for(settings, choices=None):
    notes = {}
    for s in settings:
        k = s["key"]
        chosen = k in (choices or {}) and (choices or {})[k] != s["value"]
        line = [f"from {s['source']}" if s.get("source") else "inferred",
                f"confidence {s['level']} ({int(s['confidence'] * 100)}%)" + ("; you chose this value" if chosen else "")]
        line += [f"  e.g. {e}" for e in s["evidence"][:3]]
        if s.get("alternatives"):
            line.append("  also seen: " + ", ".join(f"{a['value']} ({int(a['share'] * 100)}%)" for a in s["alternatives"]))
        notes[k] = line
    return notes


def propose(paths=(), a365=None, choices=None, existing=None, extra=None):
    """Scan, infer and render. Returns {settings, questions, rules (inferred keys), toml, scanned, warnings, diff?}.
    choices: {setting key: value} answers that replace the best guesses. existing: path of a current rules file,
    whose explicit keys are kept unless a setting replaces them; a diff against it is included. extra: sections
    to write as well (the `init` setup answers: [setup], [sources]); they replace the same keys of the file."""
    lib = scan(paths, a365)
    settings = infer(lib)
    choices = dict(choices or {})
    raw = to_rules(settings, choices)
    if extra:
        raw = RU.merge(raw, extra)
    old_text, old_raw = "", {}
    if existing and Path(existing).is_file():
        old_text = Path(existing).read_text(encoding="utf-8")
        old_raw = RU.parse_text(old_text, Path(existing).name)
    merged_raw = RU.merge(old_raw, raw)
    errors = []
    try:
        RU.validate(merged_raw, "proposal")
    except RU.RulesError as e:
        errors.append(str(e))
    keys = {f"{sec}.{k}" for sec, v in merged_raw.items() if isinstance(v, dict) for k in v}
    full = RU.merge(RU.DEFAULTS, merged_raw)
    header = ["# ecadlib library rules: every ecadlib tool follows this file (see `ecadlib rules -h`).",
              "# Written by `ecadlib init` from: " + (", ".join(f"{v} {k}" for k, v in lib.kinds().items()) or "nothing found") + ".",
              "# Anything not listed keeps ecadlib's default (`ecadlib rules show` prints them all)."]
    header += [f"# {h}" for h in skipped_lines(settings, choices)]
    text = RU.dumps(full, notes_for([s for s in settings if written(s, choices)], choices), only=keys, header=header)
    out = {"settings": [dict(s) for s in settings], "questions": [dict(s["question"], key=s["key"]) for s in settings if s.get("question")],
           "rules": merged_raw, "toml": text, "scanned": lib.kinds(), "components": len(lib.components),
           "footprints": len(lib.footprints), "boms": len(lib.boms), "warnings": lib.warnings, "errors": errors}
    if old_text:
        out["diff"] = RU.diff(old_text, text)
        out["existing"] = str(existing)
    return out


def save(text, path, overwrite=False):
    """Validate and write a rules file. An existing different file is replaced only with overwrite=True (it is kept as
    <name>.bak); otherwise returns {saved: False, diff} so the caller can ask. Returns {saved, path, diff}."""
    path = Path(path)
    raw = RU.parse_text(text, path.name)
    RU.validate(raw, path.name)
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old == text:
            return {"saved": False, "path": str(path), "diff": "", "unchanged": True}
        d = RU.diff(old, text, path.name)
        if not overwrite:
            return {"saved": False, "path": str(path), "diff": d, "needs_confirm": True}
        path.with_name(path.name + ".bak").write_text(old, encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        return {"saved": True, "path": str(path), "diff": d, "backup": str(path.with_name(path.name + ".bak"))}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"saved": True, "path": str(path), "diff": RU.diff("", text, path.name)}


# ------------------------------------------------------------------ CLI
def _ask(q, inp=input, out=print):
    out(f"\n{q['text']}")
    for i, c in enumerate(q["choices"], 1):
        cnt = (q.get("counts") or {}).get(c)
        out(f"  {i}) {c}" + (f"   ({cnt} seen)" if cnt else "") + ("   [best guess]" if c == q["default"] else ""))
    a = inp(f"Pick 1-{len(q['choices'])}, press Enter for the best guess, or type your own: ").strip()
    if not a:
        return q["default"]
    if a.isdigit() and 1 <= int(a) <= len(q["choices"]):
        return q["choices"][int(a) - 1]
    return a


def main(argv=None, inp=input, environ=None):
    from . import firstrun as FR
    ap = argparse.ArgumentParser(prog="ecadlib init", description="set up ecadlib here: which tool, your library folders, "
                                 "the workspace and part sources (asked, or --yes for the defaults), plus your naming / "
                                 "parameter / layer conventions learned from existing libraries; writes ecadlib.toml, "
                                 "then runs the doctor and prints what to run next")
    ap.add_argument("--from", dest="paths", action="append", default=[], metavar="PATH",
                    help="folder or file to scan (repeatable; default: the --lib folders, else the current folder): "
                         ".SchLib/.PcbLib/.DbLib, parts tables, .kicad_sym/.pretty, BOMs, Altium 365 JSON")
    ap.add_argument("--a365", action="append", default=[], metavar="FILE", help="Altium 365 component list (JSON) or check-a365 --state file")
    ap.add_argument("-o", "--out", default="ecadlib.toml", help="rules file to write (default %(default)s)")
    ap.add_argument("--yes", "-y", action="store_true", help="no questions: take the defaults and the best guesses")
    ap.add_argument("--force", action="store_true", help="replace an existing rules file without asking (a .bak is kept)")
    ap.add_argument("--json", action="store_true", help="print the proposal as JSON and write nothing")
    ap.add_argument("--tool", choices=FR.TOOLS, help="the tool you make libraries for (default: asked, else both)")
    ap.add_argument("--lib", action="append", default=[], metavar="DIR",
                    help="your library folder (repeatable): the default --lib of every command, and scanned for conventions")
    ap.add_argument("--workspace", help="workspace folder for uploads, plans and outputs (default: asked, else ecadlib_workspace)")
    ap.add_argument("--unofficial", action=argparse.BooleanOptionalAction, default=None,
                    help="use (or not) the unofficial EasyEDA/LCSC and JLCPCB search endpoints ([providers] unofficial; "
                         "default: asked, else off)")
    ap.add_argument("--sources", help="part sources in order, comma-separated (kicad,easyeda,standard,datasheet; default: asked, else all)")
    ap.add_argument("--no-doctor", action="store_true", help="do not run `ecadlib doctor` at the end")
    a = ap.parse_args(argv)
    out = Path(a.out)
    interactive = inp is not input or sys.stdin.isatty()
    sources = [x.strip() for x in a.sources.split(",") if x.strip()] if a.sources else None
    for x in sources or []:
        if x not in RU.SOURCES:
            print(f"ecadlib init: unknown source {x!r}\n  hint: sources are {', '.join(RU.SOURCES)}", file=sys.stderr)
            return 2
    broken = None                                        # an existing file that does not parse: --force replaces it
    try:
        old_raw = RU.parse_text(out.read_text(encoding="utf-8"), out.name) if out.is_file() else {}
    except (RU.RulesError, OSError) as e:
        old_raw, broken = {}, e
    ans = FR.ask_setup(old_raw, {"tool": a.tool, "libs": a.lib, "workspace": a.workspace, "sources": sources,
                                "unofficial": a.unofficial}, inp, print,
                       interactive and not a.yes and not a.json, environ=environ)
    base = out.resolve().parent                          # [setup] paths are relative to the rules file's folder
    ans["libs"] = [l if Path(l).expanduser().is_absolute() or base == Path.cwd().resolve() else str(Path(l).resolve())
                   for l in ans["libs"]]
    if base != Path.cwd().resolve() and not Path(ans["workspace"]).is_absolute() and ans["workspace"] != (old_raw.get("setup") or {}).get("workspace"):
        ans["workspace"] = str(Path(ans["workspace"]).resolve())
    paths = a.paths or [str(_abs(l, base)) for l in ans["libs"] if _abs(l, base).exists()] or ["."]
    extra = FR.setup_raw(ans)
    prop = propose(paths, a.a365, existing=None if broken else out, extra=extra)
    if a.json:
        print(json.dumps({k: v for k, v in prop.items() if k != "toml"} | {"toml": prop["toml"]}, indent=1, default=str))
        return 0
    from . import term as TM
    say = (lambda *x, **k: None) if TM.VERBOSITY < 0 else print      # -q: only the outcome line
    say(f"\nscanned {', '.join(paths)}: " + (", ".join(f"{v} {k}" for k, v in prop["scanned"].items()) or "nothing ecadlib can read")
        + f"; {prop['components']} parts, {prop['footprints']} footprints, {prop['boms']} BOMs")
    for w in prop["warnings"][:10]:
        say(f"  note: {w}")
    if not prop["settings"]:
        say("  no conventions to learn (fine for a new library; ecadlib's defaults apply). To learn yours, point "
            "--from at your Altium/KiCad library folders, a DbLib, an Altium 365 export or a BOM")
    for s in prop["settings"]:
        v = json.dumps(s["value"]) if isinstance(s["value"], (dict, list)) else str(s["value"])   # pin_length is a number
        say(f"  {s['key']:<34} {v[:60]:<60} {s['level']:<6} {s['evidence'][0][:70] if s['evidence'] else ''}")
    choices = {}
    if prop["questions"] and not a.yes:
        if not interactive:
            say("(not a terminal: taking the best guesses; run with --yes to say so)")
        else:
            for q in prop["questions"]:
                choices[q["key"]] = _ask(q, inp)
            prop = propose(paths, a.a365, choices, existing=None if broken else out, extra=extra)
    if prop["errors"]:
        print("error: " + "; ".join(prop["errors"]), file=sys.stderr)
        return 2
    done = lambda: FR.finish_init(ans, out, say, environ=environ, run_doctor=not a.no_doctor) and None
    if out.exists():
        res = save(prop["toml"], out, overwrite=False)
        if res.get("unchanged"):
            print(f"{out} already says this; nothing written")
            done()
            return 0
        print(f"\n{out} exists and cannot be read ({broken}); a new one is proposed" if broken else
              f"\n{out} exists; changes:\n{res['diff']}")
        ok = a.force or (not broken and _setup_only(old_raw, prop["rules"]))   # only the [setup] answers changed
        if not ok and interactive and not a.yes:
            ok = inp(f"replace {out}? (a copy is kept as {out.name}.bak) [y/N] ").strip().lower() in ("y", "yes")
        if not ok:
            prop_f = out.with_name(out.name + ".proposed")
            prop_f.write_text(prop["toml"], encoding="utf-8")
            print(f"kept {out}; the proposal is in {prop_f} (use --force to replace)")
            return 1
        res = save(prop["toml"], out, overwrite=True)
        print(f"wrote {out} (old file kept as {res['backup']})")
        done()
        return 0
    save(prop["toml"], out)
    print(f"\nwrote {out}. Every ecadlib command run from this folder (or below it) now follows it; "
          f"`ecadlib rules check` shows the effective rules.")
    done()
    return 0


SETUP_SECTIONS = ("setup", "sources", "providers")


def _setup_only(old_raw, new_raw):
    """True when the proposal differs from the existing file only in the init answers ([setup], [sources],
    [providers]): re-running `init --lib X` then updates them in place (a .bak is kept) instead of asking."""
    strip = lambda r: {k: v for k, v in (r or {}).items() if k not in SETUP_SECTIONS and not k.startswith("_")}
    return strip(old_raw) == strip(new_raw)


def _abs(p, base):
    p = Path(str(p)).expanduser()
    return p if p.is_absolute() else base / p


if __name__ == "__main__":
    sys.exit(main())
