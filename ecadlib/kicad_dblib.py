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

from . import fsutil, sexpr

FIXED = ["Part ID", "Symbol", "Footprint"]
CHOOSER = {"Value", "Description", "Manufacturer", "MPN"}             # shown as chooser columns
DEFAULT_DRIVER = "SQLite3 ODBC Driver"


def _ident(s):
    return '"' + str(s).replace('"', '""') + '"'


def table_name(item):
    folder = str(item.get("folder") or "").rstrip("/")
    name = folder.split("/")[-1] if folder else "Parts"
    t = re.sub(r"[^\w .+-]+", "_", name).strip() or "Parts"
    return "_" + t if t.casefold().startswith("sqlite_") else t      # SQLite reserves sqlite_* names


def part_rows(plan, lib, include_held=False, rules=None):
    """{table: [row dict]} plus the parts left out, from plan.json, using kicad_export's field names."""
    from . import kicad_export as X
    from .parts_export import kicad_ids
    from .rules import resolve
    from . import change_gate as CG, release_check as RC
    plan = Path(plan)
    rules = resolve(rules, plan.parent)
    X.check_plan(json.loads(plan.read_text(encoding="utf-8")), plan)   # {} or a broken entry: nothing written
    # change control (#105): a hand edit of a released part's fields is held, and its released fields go out
    with CG.publishing(plan.parent):
        items = CG.settled_plan(plan, "kicad-dblib", rules=rules)
    tables, skipped, seen, spelled = {}, [], {}, {}
    for it in sorted(items, key=lambda i: str(i.get("name", ""))):
        if not isinstance(it, dict) or not it.get("name"):
            continue
        status = it.get("status", "ready")
        if status not in ("ready", "existing") and not (include_held and status == "held"):
            skipped.append({"part": it["name"], "why": f"{status}: " + "; ".join(it.get("hold_reasons", []))})
            continue
        why = [] if status == "held" else RC.publish_problems(it, plan.parent, rules, writes_files=True)   # checked again, as kicad-export
        if why and not include_held:                                                  # does: `ready` is not enough
            skipped.append({"part": it["name"], "why": "held: " + "; ".join(why)})
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
        t = spelled.setdefault(table_name(it).casefold(), table_name(it))   # SQLite names ignore case: 'Resistors' and
        seen[key] = t                                                        # 'resistors' are one table
        row = {"Part ID": key, "Symbol": sym, "Footprint": fp}
        low = {k.casefold() for k in row}
        for k, v in fields:                   # 'Tolerance' and 'tolerance' would be a duplicate SQLite column: first wins
            if k.casefold() not in low:
                low.add(k.casefold())
                row[k] = v
        tables.setdefault(t, []).append(row)
    return tables, skipped


def columns(rows):
    """FIXED, then every other field in first-seen order; one column per name ignoring case (SQLite's rule), so a
    row's 'MPN' and another row's 'Mpn' share the first spelling's column."""
    cols, low = list(FIXED), {c.casefold() for c in FIXED}
    for r in rows:
        for k in r:
            if k.casefold() not in low:
                low.add(k.casefold())
                cols.append(k)
    return cols


def _cell(r, c):
    v = r.get(c)
    if v in (None, ""):
        v = next((x for k, x in r.items() if k.casefold() == c.casefold() and x not in (None, "")), "")
    return str(v)


def write_sqlite(path, tables):
    path = Path(path)
    tmp = path.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(tmp)) as con, con:
            for t, rows in sorted(tables.items()):
                cols = columns(rows)
                con.execute(f"CREATE TABLE {_ident(t)} (" + ", ".join(
                    f"{_ident(c)} TEXT" + (" PRIMARY KEY NOT NULL" if c == "Part ID" else "") for c in cols) + ")")
                con.executemany(f"INSERT INTO {_ident(t)} VALUES ({', '.join('?' * len(cols))})",
                                [[_cell(r, c) for c in cols] for r in rows])
    except BaseException:
        tmp.unlink(missing_ok=True)           # no half-built database left behind
        raise
    try:
        fsutil.replace(tmp, path)           # replace it whole, never half-written
    except PermissionError:                 # Windows: KiCad (ODBC) has the old database open, so it can't be renamed over
        if not path.is_file():
            raise
        _copy_into(tmp, path)               # one SQLite transaction instead: KiCad sees the old or the new tables
        tmp.unlink(missing_ok=True)


def _old_rows(path, parts):
    """{Part ID: (table, row)} of `parts` in the database the last build wrote ({} when there is none to read)."""
    out = {}
    try:
        with closing(sqlite3.connect(str(path))) as con:       # only read (the caller checked it is there)
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
                cur = con.execute(f"SELECT * FROM {_ident(t)}")
                cols = [c[0] for c in cur.description]
                for vals in cur.fetchall():
                    row = {c: v for c, v in zip(cols, vals) if v not in (None, "") or c in ("Symbol", "Footprint")}
                    row.update({c: row.get(c) or "" for c in ("Symbol", "Footprint") if c in row})   # a symbol-only
                    # part's empty Footprint stays a column of its row (the rebuild reads both)
                    if row.get("Part ID") in parts:
                        out[row["Part ID"]] = (t, row)
    except sqlite3.Error:
        return {}
    return out


def _copy_into(src, dst):
    """Replace every table of the database dst by those of src, in one transaction (works while dst is open)."""
    with closing(sqlite3.connect(dst, isolation_level=None, timeout=30)) as con:
        con.execute("ATTACH DATABASE ? AS src", (str(src),))
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                for (t,) in con.execute("SELECT name FROM main.sqlite_master WHERE type = 'table' "
                                        "AND name NOT LIKE 'sqlite_%'").fetchall():
                    con.execute(f"DROP TABLE main.{_ident(t)}")
                for t, sql in con.execute("SELECT name, sql FROM src.sqlite_master WHERE type = 'table'").fetchall():
                    con.execute(sql)
                    con.execute(f"INSERT INTO main.{_ident(t)} SELECT * FROM src.{_ident(t)}")
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.execute("DETACH DATABASE src")


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


def _row_spans(text):
    """(start, end) of each row of a lib table text: the second-level ( ... ) groups, strings and nesting respected,
    whatever the layout (one line or pretty-printed over several)."""
    spans, depth, i, start, n = [], 0, 0, None, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
            if depth == 2:
                start = i
        elif c == ")":
            if depth == 2 and start is not None:
                spans.append((start, i + 1))
                start = None
            depth -= 1
        i += 1
    return spans


def add_lib_row(table_file, nick, uri, descr, typ="Database", table="sym_lib_table", warnings=None):
    """Add (or replace, in place) one row of a sym-lib-table / fp-lib-table, keeping every other row, their order and
    layout. kicad-export writes its KiCad rows with this too, so a project's own libraries (or another export) stay.
    A row of that name of another type (a KiCad library where the database row goes) is not replaced: ValueError.
    One that points elsewhere is replaced and said in `warnings`."""
    row = (f'(lib (name {sexpr.quote(nick)})(type {sexpr.quote(typ)})(uri {sexpr.quote(uri)})(options "")'
           f'(descr {sexpr.quote(descr)}))')
    p = Path(table_file)
    what = table.replace("_", "-")
    if p.is_file():
        text = p.read_text(encoding="utf-8").lstrip("﻿")
        tree = sexpr.parse(text)
        if not tree or tree[0] != table:
            raise ValueError(f"{p}: not a {what}")
        mine = []
        for s, e in _row_spans(text):
            node = sexpr.parse(text[s:e])
            name = sexpr.first(node, "name") if isinstance(node, list) and node and node[0] == "lib" else None
            if name and len(name) > 1 and str(name[1]) == nick:
                mine.append((s, e, node))
        if mine:
            s, e, node = mine[0]
            val = lambda k: str(sexpr.first(node, k)[1]) if len(sexpr.first(node, k) or []) > 1 else ""
            old_typ, old_uri = val("type"), val("uri")
            if old_typ and old_typ.casefold() != typ.casefold():
                raise ValueError(f"{p} already has a library {nick!r} of type {old_typ} ({old_uri}); it is kept: "
                                 f"pass another --name")
            if old_uri and old_uri != uri and warnings is not None:
                warnings.append(f"{p.name}: the {nick!r} row pointed at {old_uri}; it now points at {uri}")
            for s2, e2, _ in reversed(mine[1:]):   # a duplicate row of the same name: removed with its line
                while s2 > 0 and text[s2 - 1] in " \t":
                    s2 -= 1
                if text[e2:e2 + 1] == "\n":
                    e2 += 1
                text = text[:s2] + text[e2:]
            text = text[:s] + row + text[e:]
        else:
            text = text.rstrip()
            if not text.endswith(")"):
                raise ValueError(f"{p}: unexpected {what} layout; add this row by hand:\n{row}")
            text = text[:-1].rstrip() + "\n\t" + row + "\n)\n"
    else:
        text = f"({table}\n\t(version 7)\n\t" + row + "\n)\n"
    t = sexpr.parse(text)
    if [str(sexpr.first(l, "name")[1]) for l in sexpr.find(t, "lib") if sexpr.first(l, "name")].count(nick) != 1:
        raise ValueError(f"{p}: could not set the {nick} row (unexpected layout); add this row by hand:\n{row}")
    if not p.is_file() or p.read_text(encoding="utf-8") != text:
        fsutil.write_text_atomic(p, text)
    return text


def build(plan, out, name="ecadlib", include_held=False, rules=None, driver=DEFAULT_DRIVER, dsn="", export=False,
          http=None, log=print):
    from .kicad_export import libname
    out, lib = Path(out), libname(name)
    out.mkdir(parents=True, exist_ok=True)
    rep = {"plan": str(plan), "library": lib, "dblib": f"{lib}.kicad_dbl", "database": f"{lib}.sqlite",
           "nickname": f"{lib}-db", "tables": {}, "skipped": [], "warnings": []}
    if (out / rep["database"]).is_file() and json.loads(Path(plan).read_text(encoding="utf-8")) == []:
        raise ValueError(f"{plan} has no parts: building it would empty the database library {lib} in {out}; "
                         f"nothing written (delete {lib}.sqlite by hand if that is what you want)")
    if export:
        from . import kicad_export as X
        xr = X.export(plan, out, name, include_held, http=http, log=log, rules=rules)
        rep["kicad_export"] = {"symbols": xr.get("symbols"), "errors": xr.get("errors")}
    tables, skipped = part_rows(plan, lib, include_held, rules)
    rep["skipped"] = skipped
    exp = out / "kicad_export.json"
    xr = None
    if exp.is_file():                                        # this library's export (one report, several libraries)
        try:
            full = json.loads(exp.read_text(encoding="utf-8"))
        except ValueError:
            full = {}
        full = full if isinstance(full, dict) else {}
        xr = ((full.get("libraries") or {}).get(lib) if isinstance(full.get("libraries"), dict) else None) \
            or (full if full.get("library", lib) == lib and full else None)
    if xr is not None:                                       # only point at symbols / footprints that were written
        kept = [p for p in xr.get("kept") or [] if isinstance(p, dict) and p.get("part")]
        db = out / rep["database"]                         # a held part kicad-export kept as exported before keeps the
        for pid, (t, row) in sorted((_old_rows(db, {p["part"] for p in kept}) if kept and db.is_file() else {}).items()):
            s = next((s for s in skipped if s["part"] == pid and str(s["why"]).startswith(("held", "awaiting"))), None)
            if s is None or any(r["Part ID"] == pid for rs in tables.values() for r in rs):     # row it had too: never
                continue                                                                          # the held fields
            skipped.remove(s)
            tables.setdefault(next((k for k in tables if k.casefold() == t.casefold()), t), []).append(row)
            rep["warnings"].append(f"{pid}: {s['why'].replace(': ', ' (', 1)}); the row exported before stays in "
                                   f"{rep['database']} until it passes the check again")
        parts = list(xr.get("parts", [])) + kept
        syms = {p["symbol_name"] for p in parts if p.get("symbol")}
        owner = {p["symbol_name"]: p["part"] for p in parts if p.get("symbol")}
        own_fp = {p["part"]: p.get("footprint") for p in parts}
        fps = set((xr.get("footprints") or {}))
        for t, rows in list(tables.items()):
            for r in list(rows):
                sname = (r.get("Symbol") or ":").split(":", 1)[-1]
                if owner.get(sname, r["Part ID"]) != r["Part ID"]:   # two part names, one KiCad name: the symbol is
                    rows.remove(r)                                    # the other part's (kicad-export reported it)
                    skipped.append({"part": r["Part ID"], "why": f"symbol {r['Symbol']} is part {owner[sname]}'s "
                                                                 "(same KiCad name); rename one of them"})
                    continue
                if sname not in syms:
                    rep["warnings"].append(f"{r['Part ID']}: symbol {r['Symbol']} is not in {lib}.kicad_sym")
                fpn = r["Footprint"].split(":", 1)[1] if r.get("Footprint") else None
                if fpn and r["Part ID"] in own_fp and own_fp[r["Part ID"]] != fpn and fpn in fps:
                    rep["warnings"].append(f"{r['Part ID']}: footprint {r['Footprint']} is another part's (same KiCad "
                                           "name); left empty")
                    r["Footprint"] = ""
                elif fpn and fpn not in fps:
                    rep["warnings"].append(f"{r['Part ID']}: footprint {r['Footprint']} is not in {lib}.pretty")
            if not rows:
                del tables[t]
    else:
        rep["warnings"].append(f"no kicad_export.json for library {lib} in {out}: run `ecadlib kicad-export --name {name}` "
                               "into this folder (or pass --export) so the Symbol / Footprint columns resolve")
    write_sqlite(out / rep["database"], tables)
    doc = dbl(f"{lib} (ecadlib database)", rep["database"], tables, driver, dsn, f"ecadlib parts from {Path(plan).name}")
    doc = keep_settings(out / rep["dblib"], doc, keep_source=(driver == DEFAULT_DRIVER and not dsn))
    fsutil.write_text_atomic(out / rep["dblib"], json.dumps(doc, indent=2) + "\n")
    add_lib_row(out / "sym-lib-table", rep["nickname"], f"${{KIPRJMOD}}/{rep['dblib']}", "ecadlib database library",
                warnings=rep["warnings"])
    rep["tables"] = {t: len(r) for t, r in sorted(tables.items())}
    fsutil.write_text_atomic(out / "kicad_dblib.json", json.dumps(rep, indent=2) + "\n")
    return rep


def keep_settings(path, doc, keep_source=True):
    """The new .kicad_dbl doc with the settings a user changed in the existing one kept: the ODBC timeout (and the
    whole connection when no --driver / --dsn was given this time), the cache, each field's visibility flags for
    columns that are still there. The libraries / columns themselves come from the plan."""
    try:
        old = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else None
    except ValueError:
        old = None
    if not isinstance(old, dict):
        return doc
    src = old.get("source")
    if isinstance(src, dict):
        if keep_source:
            doc["source"] = {**doc["source"], **{k: v for k, v in src.items() if k in doc["source"]}}
        elif "timeout_seconds" in src:
            doc["source"]["timeout_seconds"] = src["timeout_seconds"]
    if isinstance(old.get("cache"), dict):
        doc["cache"] = {**doc["cache"], **{k: v for k, v in old["cache"].items() if k in doc["cache"]}}
    flags = ("visible_on_add", "visible_in_chooser", "show_name", "inherit_properties")
    was = {(str(l.get("table")), str(f.get("column"))): f for l in old.get("libraries") or [] if isinstance(l, dict)
           for f in l.get("fields") or [] if isinstance(f, dict)}
    for l in doc["libraries"]:
        for f in l["fields"]:
            o = was.get((l["table"], f["column"]))
            if o:
                f.update({k: o[k] for k in flags if isinstance(o.get(k), bool)})
                if isinstance(o.get("name"), str) and o["name"].strip():
                    f["name"] = o["name"]
    return doc


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
    from . import term as TM
    say = (lambda *x, **k: None) if TM.VERBOSITY < 0 else print      # -q: only the outcome and the warnings
    for t, n in rep["tables"].items():
        say(f"  {t}: {n} parts")
    for s in rep["skipped"]:
        say(f"  skipped {s['part']}: {s['why']}")
    for w in rep["warnings"]:
        print(f"  warning: {w}")
    print(f"wrote {Path(a.out) / rep['dblib']}, {rep['database']} and the {rep['nickname']} row in sym-lib-table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
