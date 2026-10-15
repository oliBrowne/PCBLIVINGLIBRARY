"""KiCad database library (KiCad 7+): the Altium-DbLib experience for KiCad users.

    ecadlib kicad-dblib plan.json -o out/kicad --name mylib [--export] [--driver "SQLite3 ODBC Driver"]

Writes into the output folder
    mylib.sqlite        one table per category (Resistors, ICs, ...): Part ID (key), Symbol, Footprint, then the same
                        fields kicad-export puts on each symbol (Value, Datasheet, Description, Manufacturer, MPN,
                        supplier numbers, other plan parameters; house names from the rules' [parameters] names)
    mylib.kicad_dbl     the JSON file KiCad opens: ODBC connection to mylib.sqlite (${CWD} = this folder), one
                        library per table, every column mapped to a symbol field
    sym-lib-table       gains a row `mylib-db` of type "Database" (the existing rows, e.g. `mylib` from kicad-export,
                        are kept); the Symbol / Footprint columns point at `mylib:<part>`, so the KiCad files from
                        `ecadlib kicad-export --name mylib` must be in the same project (--export writes them first)
    kicad_dblib.json    what was written, and parts left out (held, or no symbol)

KiCad reads the database through ODBC, so the machine needs an SQLite ODBC driver (Windows: "SQLite3 ODBC Driver"
from http://www.ch-werner.de/sqliteodbc/; Linux: libsqliteodbc / unixODBC, driver name usually "SQLite3"; macOS:
sqliteodbc from Homebrew). Pick the driver name with --driver, or use a DSN you set up with --dsn."""
import argparse, json, re, sqlite3, sys
from contextlib import closing
from pathlib import Path

from . import sexpr

FIXED = ["Part ID", "Symbol", "Footprint"]
CHOOSER = {"Value", "Description", "Manufacturer", "MPN"}             # shown as chooser columns
DEFAULT_DRIVER = "SQLite3 ODBC Driver"


def _ident(s):
    return '"' + str(s).replace('"', '""') + '"'


def table_name(item):
    folder = str(item.get("folder") or "").rstrip("/")
    name = folder.split("/")[-1] if folder else "Parts"
    return re.sub(r"[^\w .+-]+", "_", name).strip() or "Parts"


def part_rows(plan, lib, include_held=False, rules=None):
    """{table: [row dict]} plus the parts left out, from plan.json, using kicad_export's field names."""
    from . import kicad_export as X
    from .parts_export import kicad_ids
    from .rules import resolve
    plan = Path(plan)
    rules = resolve(rules, plan.parent)
    items = json.loads(plan.read_text())
    if not isinstance(items, list):
        raise ValueError(f"{plan}: not a librarian plan (expected a list of parts)")
    tables, skipped, seen = {}, [], {}
    for it in sorted(items, key=lambda i: str(i.get("name", ""))):
        if not isinstance(it, dict) or not it.get("name"):
            continue
        status = it.get("status", "ready")
        if status not in ("ready", "existing") and not (include_held and status == "held"):
            skipped.append({"part": it["name"], "why": f"{status}: " + "; ".join(it.get("hold_reasons", []))})
            continue
        sym, fp = kicad_ids(it, X.kname(lib))
        if not sym:
            skipped.append({"part": it["name"], "why": "no symbol in the plan"})
            continue
        fp_name = fp.split(":", 1)[1] if fp else None
        fields = [(k, v) for k, v in X.plan_fields(it, X.kname(lib), fp_name, {}, rules) if k not in ("Reference", "Footprint")]
        key = str(it["name"])
        if key in seen:
            skipped.append({"part": key, "why": f"duplicate Part ID (also in table {seen[key]})"})
            continue
        t = table_name(it)
        seen[key] = t
        tables.setdefault(t, []).append({"Part ID": key, "Symbol": sym, "Footprint": fp, **dict(fields)})
    return tables, skipped


def columns(rows):
    cols = list(FIXED)
    for r in rows:
        cols += [k for k in r if k not in cols]
    return cols


def write_sqlite(path, tables):
    path = Path(path)
    tmp = path.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    with closing(sqlite3.connect(tmp)) as con, con:
        for t, rows in sorted(tables.items()):
            cols = columns(rows)
            con.execute(f"CREATE TABLE {_ident(t)} (" + ", ".join(
                f"{_ident(c)} TEXT" + (" PRIMARY KEY NOT NULL" if c == "Part ID" else "") for c in cols) + ")")
            con.executemany(f"INSERT INTO {_ident(t)} VALUES ({', '.join('?' * len(cols))})",
                            [[str(r.get(c, "") or "") for c in cols] for r in rows])
    tmp.replace(path)                       # KiCad may hold the old file open: replace it whole, never half-written


def dbl(name, db_file, tables, driver=DEFAULT_DRIVER, dsn="", description=""):
    """The .kicad_dbl JSON (KiCad 7+ DATABASE_LIB_SETTINGS: meta, name, description, source, cache, libraries)."""
    libs = []
    for t, rows in sorted(tables.items()):
        cols = columns(rows)
        fields = [{"column": c, "name": c, "visible_on_add": c == "Value", "visible_in_chooser": c in CHOOSER,
                   "show_name": False, "inherit_properties": c in ("Value", "Datasheet")}
                  for c in cols if c not in FIXED and c != "Description"]
        props = {"description": "Description"} if "Description" in cols else {}
        libs.append({"name": t, "table": t, "key": "Part ID", "symbols": "Symbol", "footprints": "Footprint",
                     "fields": fields, "properties": props})
    source = {"type": "odbc", "dsn": dsn, "username": "", "password": "", "timeout_seconds": 2,
              "connection_string": "" if dsn else f"Driver={{{driver}}};Database=${{CWD}}/{db_file}"}
    return {"meta": {"version": 0}, "name": name, "description": description, "source": source,
            "cache": {"max_size": 256, "max_age": 10}, "libraries": libs}


def add_lib_row(table_file, nick, uri, descr):
    """Add (or replace) the Database row in a sym-lib-table, keeping every other row."""
    row = (f'\t(lib (name {sexpr.quote(nick)})(type "Database")(uri {sexpr.quote(uri)})(options "")'
           f'(descr {sexpr.quote(descr)}))\n')
    p = Path(table_file)
    if p.is_file():
        text = p.read_text()
        tree = sexpr.parse(text)
        if not tree or tree[0] != "sym_lib_table":
            raise ValueError(f"{p}: not a sym-lib-table")
        keep = [ln for ln in text.splitlines(keepends=True)
                if not re.match(rf'\s*\(lib \(name "?{re.escape(nick)}"?\)', ln)]
        text = "".join(keep).rstrip()
        if not text.endswith(")"):
            raise ValueError(f"{p}: unexpected sym-lib-table layout; add this row by hand:\n{row}")
        text = text[:-1].rstrip() + "\n" + row + ")\n"
    else:
        text = "(sym_lib_table\n\t(version 7)\n" + row + ")\n"
    sexpr.parse(text)
    p.write_text(text)
    return text


def build(plan, out, name="ecadlib", include_held=False, rules=None, driver=DEFAULT_DRIVER, dsn="", export=False,
          http=None, log=print):
    from .kicad_export import kname
    out, lib = Path(out), kname(name)
    out.mkdir(parents=True, exist_ok=True)
    rep = {"plan": str(plan), "library": lib, "dblib": f"{lib}.kicad_dbl", "database": f"{lib}.sqlite",
           "nickname": f"{lib}-db", "tables": {}, "skipped": [], "warnings": []}
    if export:
        from . import kicad_export as X
        xr = X.export(plan, out, name, include_held, http=http, log=log, rules=rules)
        rep["kicad_export"] = {"symbols": xr.get("symbols"), "errors": xr.get("errors")}
    tables, skipped = part_rows(plan, lib, include_held, rules)
    rep["skipped"] = skipped
    exp = out / "kicad_export.json"
    if exp.is_file():                                        # only point at symbols / footprints that were written
        xr = json.loads(exp.read_text())
        syms = {p["symbol_name"] for p in xr.get("parts", []) if p.get("symbol")}
        fps = set((xr.get("footprints") or {}))
        for t, rows in tables.items():
            for r in rows:
                if r["Symbol"].split(":", 1)[1] not in syms:
                    rep["warnings"].append(f"{r['Part ID']}: symbol {r['Symbol']} is not in {lib}.kicad_sym")
                if r["Footprint"] and r["Footprint"].split(":", 1)[1] not in fps:
                    rep["warnings"].append(f"{r['Part ID']}: footprint {r['Footprint']} is not in {lib}.pretty")
    else:
        rep["warnings"].append(f"no kicad_export.json in {out}: run `ecadlib kicad-export --name {name}` into this folder "
                               "(or pass --export) so the Symbol / Footprint columns resolve")
    write_sqlite(out / rep["database"], tables)
    doc = dbl(f"{lib} (ecadlib database)", rep["database"], tables, driver, dsn, f"ecadlib parts from {Path(plan).name}")
    (out / rep["dblib"]).write_text(json.dumps(doc, indent=2) + "\n")
    add_lib_row(out / "sym-lib-table", rep["nickname"], f"${{KIPRJMOD}}/{rep['dblib']}", "ecadlib database library")
    rep["tables"] = {t: len(r) for t, r in sorted(tables.items())}
    (out / "kicad_dblib.json").write_text(json.dumps(rep, indent=2) + "\n")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="KiCad database library (.kicad_dbl + SQLite) from a librarian plan")
    ap.add_argument("plan", help="librarian plan.json")
    ap.add_argument("-o", "--out", default="out/kicad", help="folder (the same one kicad-export writes to)")
    ap.add_argument("--name", default="ecadlib", help="KiCad library name, as given to kicad-export (default %(default)s)")
    ap.add_argument("--export", action="store_true", help="run kicad-export into the folder first")
    ap.add_argument("--include-held", action="store_true", help="also list parts the release check held")
    ap.add_argument("--driver", default=DEFAULT_DRIVER, help="ODBC driver name (Linux unixODBC: usually SQLite3)")
    ap.add_argument("--dsn", default="", help="use this ODBC data source name instead of a driver connection string")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="with --export: use the HTTP cache only")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan)")
    a = ap.parse_args(argv)
    http = None
    if a.export:
        from .httpcache import CachedHTTP
        http = CachedHTTP(a.cache, offline=a.offline, timeout=60)
    try:
        rep = build(a.plan, a.out, a.name, a.include_held, a.rules, a.driver, a.dsn, a.export, http)
    except (OSError, ValueError) as e:
        print(f"cannot build the database library: {e}", file=sys.stderr)
        return 2
    for t, n in rep["tables"].items():
        print(f"  {t}: {n} parts")
    for s in rep["skipped"]:
        print(f"  skipped {s['part']}: {s['why']}")
    for w in rep["warnings"]:
        print(f"  warning: {w}")
    print(f"wrote {Path(a.out) / rep['dblib']}, {rep['database']} and the {rep['nickname']} row in sym-lib-table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
