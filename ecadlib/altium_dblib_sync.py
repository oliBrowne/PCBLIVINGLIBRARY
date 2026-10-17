"""Altium without 365: sync a librarian / altium365 plan.json into a Database Library (.DbLib) or an
SVN Database Library (.SVNDbLib) folder.

    python -m ecadlib.altium_dblib_sync out/librarian/plan.json --to D:/libs/ecadlib [--svn] [--prune]
        [--name ecadlib] [--win-dir C:\\libs\\ecadlib] [--svn-url URL] [--commit [-m MSG]] [--dry-run] [-o report.json]
    ecadlib dblib-sync ...                                              (same options)

Folder layout (one entry per model file, the altium_lib / altium365 convention):
    <to>/<name>.DbLib | <name>.SVNDbLib     INI file Altium opens (Excel connection, field maps, tables)
    <to>/<name>.xlsx                        parts table: one sheet per category (Capacitors, ICs, ...), all text
    <to>/<name>.csv                         the same rows flat (Table column first), for diffs and review only
    <to>/SchLib/<Library Ref>.SchLib        symbol per file;  [Library Path]   = SchLib\\<Library Ref>.SchLib
    <to>/PcbLib/<Footprint Ref>.PcbLib      footprint per file; [Footprint Path] = PcbLib\\<Footprint Ref>.PcbLib
    <to>/ecadlib_sync.json                  which rows and files this tool manages (only those are --prune-able)

Per plan item, like `altium365 push`: `held` items (release_check) are left exactly as they are; items without
both models are `skipped`; otherwise the row is `added`, `updated` (row fields or a model file changed) or
`up_to_date`. Row updates merge: columns added by hand in the sheet survive, plan values win, the plan's
`cleared` fields are blanked. Rows and model files no longer in the plan are reported (`not_in_plan`) and only
deleted with --prune, and only if this tool wrote them. Unchanged files are not rewritten. Model files written
are read back with altium_lib's readers and must contain the Library Ref / Footprint Ref the row names.

An existing .DbLib/.SVNDbLib is updated, not replaced: settings made in Altium's DbLib editor are kept, missing
tables and field maps are appended, and ConnectionString / SVNRepositoryURL are only rewritten when --win-dir /
--svn-url are given (or the URL is read from `svn info`). Altium opens the workbook through LibraryDatabasePath +
DatabasePathRelative=1 (the .xlsx next to the DbLib), so the library folder can be moved or copied to another PC. The
connection string's Data Source is only what Altium's DbLib editor shows (and uses if the relative option is switched
off): the --win-dir folder, else the fixed C:\\ecadlib\\library placeholder, never this machine's absolute path.

SVN (--svn): <to> is meant to be an SVN working copy; the .SVNDbLib gets SVNRepositoryURL and a LibrarySearchPath
of <url>/SchLib;<url>/PcbLib. With --commit, and `svn` on PATH and <to> a working copy, it runs svn add / delete /
commit for exactly the files it touched; otherwise it prints those commands."""
import argparse, csv, io, json, os, re, shutil, subprocess, sys
from pathlib import Path, PureWindowsPath

from . import altium_lib as AL
from . import altium_native as AN
from . import sheetsafe as SS
from .hub_copy import _read_prj as read_ini_text      # (text, encoding) of an INI Altium saved
from .altium_export import (COLUMNS, dblib_links, excel_connection, fieldmap_section, table_section)

STATE = "ecadlib_sync.json"
EXT = {"dblib": ".DbLib", "svndblib": ".SVNDbLib"}
KINDS = (("symbol", "SchLib", "Library"), ("footprint", "PcbLib", "Footprint"))


# ------------------------------------------------------------------ plan -> rows
def load_plan(path):
    """plan.json with model paths resolved against the plan's folder (as altium365 push does)."""
    items = json.loads(Path(path).read_text())
    for i in items:
        for k, _, _ in KINDS:
            if (i.get(k) or {}).get("file"):
                i[k]["file"] = str((Path(path).parent / i[k]["file"]).resolve())
    return items


def table_of(item):
    """Sheet name from the workspace folder (Components/Capacitors -> Capacitors); Excel's sheet name rules."""
    t = re.sub(r"[\[\]:*?/\\]", "_", (item.get("folder") or "Misc").rstrip("/").rsplit("/", 1)[-1]).strip("'")
    return (t or "Misc")[:31]


def plan_row(item, rules=None):
    """The DbLib row for one plan item, columns in altium_export order, then any extra plan parameters; parameter
    columns in the rules' house names ([parameters] names)."""
    row = {c: "" for c in COLUMNS}
    row.update({"Part Number": item["name"], "Description": item.get("description", ""), "Comment": item.get("comment", "")})
    for k, d, w in KINDS:
        ref = (item.get(k) or {}).get("ref") or ""
        row[f"{w} Ref"] = ref
        row[f"{w} Path"] = f"{d}\\{AL.file_stem(ref)}.{d}" if ref else ""
    row.update({p["name"]: str(p["value"]) for p in item.get("parameters", [])})
    from .rules import house_row
    return house_row(row, rules)


# ------------------------------------------------------------------ parts table (xlsx + csv)
def read_tables(xlsx):
    """{sheet: (header, [row dicts])} from an existing parts workbook; {} if there is none."""
    if not Path(xlsx).exists():
        return {}
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    out = {}
    try:
        for ws in wb.worksheets:
            rows = [["" if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)]
            if not rows:
                continue
            cols = [(i, h) for i, h in enumerate(rows[0]) if h]       # by position: a blank header column must not
            hdr = [h for _, h in cols]                                  # shift the hand columns right of it
            out[ws.title] = (hdr, [{h: r[i] if i < len(r) else "" for i, h in cols} for r in rows[1:] if any(r)])
    finally:
        wb.close()
    return out


def write_xlsx(path, tables):
    import openpyxl
    from openpyxl.cell.cell import TYPE_STRING
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, (hdr, rows) in tables.items():
        ws = wb.create_sheet(name)
        ws.append(hdr)
        for r in rows:
            ws.append([r.get(c, "") for c in hdr])
        for row in ws.iter_rows(min_row=2):  # all text: "=Value" is a parameter, not a formula; '0402' stays '0402'
            for c in row:
                if isinstance(c.value, str):
                    c.data_type = TYPE_STRING
        ws.freeze_panes = "B2"
    tmp = Path(str(path) + ".tmp")
    wb.save(tmp)
    os.replace(tmp, path)


def csv_text(tables):
    cols = ["Table"]
    for hdr, _ in tables.values():
        cols += [c for c in hdr if c not in cols]
    buf = io.StringIO()
    w = SS.DictWriter(buf, cols, lineterminator="\n", param_refs=True)    # =Value stays; =HYPERLINK(...) does not
    w.writeheader()
    for name, (_, rows) in tables.items():
        for r in rows:
            w.writerow({"Table": name, **r})
    return buf.getvalue()


# ------------------------------------------------------------------ .DbLib / .SVNDbLib INI
def _sections(text):
    secs = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        if re.fullmatch(r"\[[^\]=]+\]", ln.strip()):
            secs.append([ln.strip()[1:-1], []])
        elif secs and ln.strip():
            secs[-1][1].append(ln)
    return secs


PLACEHOLDER_DIR = r"C:\ecadlib\library"      # the Data Source folder unless --win-dir names one


def win_folder(win_dir=None):
    """The Windows folder the connection string names: --win-dir, else PLACEHOLDER_DIR on every OS. Never the library's
    own absolute path: Altium reads the workbook through LibraryDatabasePath + DatabasePathRelative=1 (next to the
    DbLib), and a machine path would stop the folder from being portable."""
    return PureWindowsPath(win_dir or PLACEHOLDER_DIR)


def _kv(lines):
    return dict(ln.split("=", 1) for ln in lines if "=" in ln)


def dblib_update(existing, tables, links, override=()):
    """New or merged INI text. tables: {sheet: columns} (sheet names without '$'); links: kwargs for
    altium_export.dblib_links; override: [DatabaseLinks] keys whose template value replaces the existing one."""
    names = {t + "$": cols for t, cols in tables.items()}
    tmpl = dblib_links(last_table=next(iter(names), ""), **links)
    if not existing:
        L = list(tmpl)
        for i, tn in enumerate(names, 1):
            L += table_section(i, tn)
        fm = 0
        for tn, cols in names.items():
            for c in cols:
                fm += 1
                L += fieldmap_section(fm, tn, c)
        return "\r\n".join(L) + "\r\n"
    secs = _sections(existing)
    link_tmpl = _kv(tmpl[3:])
    db = next((s for s in secs if s[0] == "DatabaseLinks"), None)
    if db is None:
        db = ["DatabaseLinks", []]
        secs.insert(1 if secs and secs[0][0] == "OutputDatabaseLinkFile" else 0, db)
    have = _kv(db[1])
    for k, v in link_tmpl.items():
        if k not in have:
            db[1].append(f"{k}={v}")
        elif k in override and have[k] != v:
            db[1] = [f"{k}={v}" if ln.split("=", 1)[0] == k else ln for ln in db[1]]
    num = lambda s, p: int(s[0][len(p):]) if s[0].startswith(p) and s[0][len(p):].isdigit() else None
    tabs = {_kv(s[1]).get("TableName") for s in secs if num(s, "Table") is not None}
    fmaps = {_kv(s[1]).get("Options", "").split("|", 1)[0] for s in secs if num(s, "FieldMap") is not None}
    ti = max([num(s, "Table") or 0 for s in secs] + [0])
    fi = max([num(s, "FieldMap") or 0 for s in secs] + [0])
    last_t = max([k for k, s in enumerate(secs) if num(s, "Table") is not None] + [secs.index(db)])
    new_t, new_f = [], []
    for tn, cols in names.items():
        if tn not in tabs:
            ti += 1
            sec = table_section(ti, tn)
            new_t.append([sec[0][1:-1], sec[1:]])
        for c in cols:
            if f"FieldName={tn}.{c}" not in fmaps:
                fi += 1
                sec = fieldmap_section(fi, tn, c)
                new_f.append([sec[0][1:-1], sec[1:]])
    secs = secs[:last_t + 1] + new_t + secs[last_t + 1:] + new_f
    return "".join(f"[{n}]\r\n" + "".join(ln + "\r\n" for ln in body) for n, body in secs)


# ------------------------------------------------------------------ svn
def run_cmd(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def svn_info(target, runner=run_cmd, which=shutil.which):
    """Working-copy URL of target, or None (no svn on PATH, or not a working copy)."""
    if not which("svn"):
        return None
    r = runner(["svn", "info", "--show-item", "url", str(target)])
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def svn_commands(written, removed, message):
    """svn add/delete/commit for exactly the paths (relative to the working copy root) this run touched."""
    written, removed = sorted(set(written)), sorted(set(removed))
    dirs = sorted({p.split("/")[0] for p in written + removed if "/" in p})
    cmds = []
    if written:
        cmds.append(["svn", "add", "--force", "--parents", *written])
    if removed:
        cmds.append(["svn", "delete", "--force", *removed])
    if written or removed:
        cmds.append(["svn", "commit", "--depth", "empty", "-m", message, *dirs, *written, *removed])
    return cmds


def svn_step(target, written, removed, commit=False, message="ecadlib dblib-sync", runner=run_cmd, which=shutil.which):
    """Run (commit=True, svn available, target is a working copy) or just return the svn commands."""
    cmds = svn_commands(written, removed, message)
    res = {"commands": [" ".join(_q(c) for c in cmd) for cmd in cmds], "ran": False, "output": []}
    if not cmds:
        res["note"] = "nothing to commit"
    elif not commit:
        res["note"] = "run these in the working copy (or pass --commit)"
    elif not which("svn"):
        res["note"] = "svn is not on PATH: run these by hand"
    elif runner(["svn", "info", str(target)]).returncode != 0:
        res["note"] = f"{target} is not an svn working copy: check it out first, then run these"
    else:
        for cmd in cmds:
            r = runner(cmd, cwd=str(target))
            res["output"].append((r.stdout or "") + (r.stderr or ""))
            if r.returncode != 0:
                res["error"] = f"{cmd[1]} failed (exit {r.returncode})"
                break
        else:
            res["ran"] = True
    return res


def _q(s):
    return f'"{s}"' if re.search(r"[\s()&|;<>]", s) else s


# ------------------------------------------------------------------ sync
def _readback(path, kind, ref):
    """None if the written model reads back with altium_lib and holds `ref`, else the problem."""
    try:
        names = ([c["name"] for c in AL.read_schlib(path)[1]] if kind == "SchLib"
                 else [f["name"] for f in AL.read_pcblib(path)])
    except Exception as e:
        return f"{Path(path).name} does not read back: {type(e).__name__}: {e}"
    return None if ref in names else f"{Path(path).name} holds {names}, not {ref!r}"


def sync(plan, target_dir, mode="dblib", name="ecadlib", prune=False, win_dir=None, svn_url=None,
         dry_run=False, base=None, runner=run_cmd, which=shutil.which, rules=None):
    """Bring <target_dir> in line with the plan. plan: a plan.json path, or its items (model paths absolute or
    relative to `base`). Returns {"results": [...], "written": [...], "removed": [...], "dblib": path, ...};
    results are {"name", "action", "detail", "table"} with action added/updated/up_to_date/held/skipped/error,
    and not_in_plan/removed for rows (kind "row") and model files (kind "file") the plan no longer has."""
    if mode not in EXT:
        raise ValueError(f"mode must be one of {sorted(EXT)}")
    items = load_plan(plan) if isinstance(plan, (str, Path)) else plan
    base = Path(base or (Path(plan).parent if isinstance(plan, (str, Path)) else "."))
    to = Path(target_dir)
    from .rules import param_name, resolve
    rules = resolve(rules, base)                            # house parameter names: ecadlib.toml found from the plan
    xlsx, csvf, lib, statef = to / f"{name}.xlsx", to / f"{name}.csv", to / f"{name}{EXT[mode]}", to / STATE
    state = json.loads(statef.read_text()) if statef.exists() else {}
    managed_rows, managed_files = set(state.get("rows", [])), set(state.get("files", []))
    old = read_tables(xlsx)
    tables = {t: (list(h), [dict(r) for r in rows]) for t, (h, rows) in old.items()}
    where = {r.get("Part Number"): t for t, (_, rows) in tables.items() for r in rows}
    results, written, removed, copied, failed = [], [], [], {}, {}

    def find(pn, t):
        rows = tables.get(t, ([], []))[1]
        return next((r for r in rows if r.get("Part Number") == pn), None)

    for it in items:
        t0 = table_of(it)                                   # Excel sheet names ignore case: Passives = passives
        res = {"name": it["name"], "action": "", "detail": "", "table": next((k for k in tables if k.lower() == t0.lower()), t0)}
        results.append(res)
        if it.get("status") == "held":
            res["action"], res["detail"] = "held", "; ".join(it.get("hold_reasons") or ["release check"])
            if it["name"] in where:
                res["table"] = where[it["name"]]
            continue
        if it.get("status") != "ready":
            res["action"], res["detail"] = "skipped", it.get("status") or "not ready"
            continue
        notes, problems = [], []
        for k, d, w in KINDS:                               # model files first: a bad file must not reach the table
            src, ref = it[k]["file"], it[k]["ref"]
            src = Path(src) if Path(src).is_absolute() else base / src
            rel = f"{d}/{AL.file_stem(ref)}.{d}"
            dst = to / rel
            if not src.exists():
                problems.append(f"{k} file missing: {src}")
                continue
            data = src.read_bytes()
            if rel in copied:
                if copied[rel] != data:
                    problems.append(f"{rel}: another plan item already wrote different content this run")
                elif rel in failed:                         # same bad file as an earlier item: same error
                    problems.append(failed[rel])
                elif rel in written:
                    notes.append(f"{rel} updated")
                continue
            copied[rel] = data
            if dst.exists() and dst.read_bytes() == data:
                if not dry_run:
                    AN.copy_recipe(src, dst)
                continue
            notes.append(f"{rel} {'updated' if dst.exists() else 'added'}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(dst.name + ".tmp")
                tmp.write_bytes(data)
                err = _readback(tmp, d, ref)
                if err:
                    tmp.unlink()
                    problems.append(err)
                    failed[rel] = err
                    notes.pop()
                    continue
                os.replace(tmp, dst)
                AN.copy_recipe(src, dst)            # what Altium rebuilds the file from (ecadlib.altium_native)
            else:
                err = _readback(src, d, ref)
                if err:
                    problems.append(err)
                    failed[rel] = err
                    continue
            written.append(rel)
        if problems:
            res["action"], res["detail"] = "error", "; ".join(problems + notes)
            continue
        want, t = plan_row(it, rules), res["table"]
        old_t = where.get(it["name"])
        cur = find(it["name"], old_t) if old_t else None
        if cur is None:
            hdr, rows = tables.setdefault(t, (list(want), []))
            hdr += [c for c in want if c not in hdr]
            rows.append(want)
            where[it["name"]] = t
            res["action"], res["detail"] = "added", "; ".join(notes)
            managed_rows.add(it["name"])
            continue
        new = dict(cur)
        for c in (param_name(c, rules) for c in it.get("cleared", [])):
            if c in new:
                new[c] = ""
        new.update(want)
        changed = [c for c in new if str(new.get(c, "")) != str(cur.get(c, ""))]
        if old_t != t:                                      # category changed: move the row
            tables[old_t][1].remove(cur)
            hdr, rows = tables.setdefault(t, (list(want), []))
            hdr += [c for c in new if c not in hdr]
            rows.append(new)
            where[it["name"]] = t
            notes.insert(0, f"moved from {old_t}")
        elif changed:
            hdr, rows = tables[t]
            hdr += [c for c in new if c not in hdr]
            rows[rows.index(cur)] = new
        if changed:
            notes.insert(0, "row: " + ", ".join(changed))
        res["action"] = "updated" if notes else "up_to_date"
        res["detail"] = "; ".join(notes)
        managed_rows.add(it["name"])

    # rows and model files the plan no longer has
    plan_names = {it["name"] for it in items}
    for t, (_, rows) in tables.items():
        for r in list(rows):
            pn = r.get("Part Number", "")
            if pn in plan_names:
                continue
            res = {"name": pn, "action": "not_in_plan", "detail": "", "table": t, "kind": "row"}
            if pn not in managed_rows:
                res["detail"] = "not written by ecadlib: kept"
            elif prune:
                rows.remove(r)
                managed_rows.discard(pn)
                res["action"] = "removed"
            else:
                res["detail"] = "kept (--prune deletes it)"
            results.append(res)
    used = {f"{d}/{AL.file_stem(r.get(f'{w} Ref', ''))}.{d}" for _, (_, rows) in tables.items() for r in rows
            for _, d, w in KINDS if r.get(f"{w} Ref")}
    for _, d, _ in KINDS:
        for f in sorted((to / d).glob(f"*.{d}")) if (to / d).is_dir() else []:
            rel = f"{d}/{f.name}"
            if rel in used or rel in copied:
                continue
            res = {"name": rel, "action": "not_in_plan", "detail": "", "table": "", "kind": "file"}
            if rel not in managed_files:
                res["detail"] = "not written by ecadlib: kept"
            elif prune:
                res["action"] = "removed"
                removed.append(rel)
                managed_files.discard(rel)
                if not dry_run:
                    f.unlink()
                    recipe = f.with_name(f.name + AN.EXT)      # else Altium's rebuild would make a file put here later
                    if recipe.is_file():                       # from the recipe of the one pruned
                        recipe.unlink()
            else:
                res["detail"] = "no row uses it; kept (--prune deletes it)"
            results.append(res)
    managed_files |= set(written)
    tables = {t: v for t, v in tables.items() if v[1] or t in old}  # never create an empty sheet

    # parts table, DbLib file, state: only rewritten when their content changes
    out = {"results": results, "written": written, "removed": removed, "dblib": str(lib), "mode": mode,
           "dry_run": dry_run}
    def put(rel, text=None, writer=None, data=None):
        if not dry_run:
            to.mkdir(parents=True, exist_ok=True)
            if writer:
                writer()
            else:
                (to / rel).write_bytes(data if data is not None else text.encode("utf-8"))
        written.append(rel)
    if tables and tables != old:
        put(xlsx.name, writer=lambda: write_xlsx(xlsx, tables))
    if tables:
        ct = csv_text(tables)
        if not csvf.exists() or csvf.read_bytes().decode("utf-8", "replace") != ct:
            put(csvf.name, ct)

    url = svn_url or (svn_info(to, runner, which) if mode == "svndblib" else None)
    win = win_folder(win_dir)
    extra = (("SVNRepositoryURL", url or ""), ("FileRepositoryIsRelative", "0")) if mode == "svndblib" else ()
    links = dict(conn_str=excel_connection(win / xlsx.name), db_type="Microsoft Excel", db_path=xlsx.name,
                 relative=True, search_path=f"{url}/SchLib;{url}/PcbLib" if url else "", extra=extra)
    override = {"LibraryDatabaseType", "LibraryDatabasePath", "DatabasePathRelative"}
    if win_dir:                                     # else keep a connection set in Altium's DbLib editor
        override.add("ConnectionString")
    if url:
        override |= {"SVNRepositoryURL", "LibrarySearchPath"}
    # keep the \r\n, and the encoding a DbLib saved by Altium's editor has (ANSI, or UTF-8 / UTF-16 with a BOM)
    cur_text, db_enc = read_ini_text(lib) if lib.exists() else ("", "utf-8")
    if tables:
        text = dblib_update(cur_text, {t: h for t, (h, _) in tables.items()}, links, override)
        if text != cur_text:
            try:
                data = text.encode(db_enc)
            except UnicodeEncodeError:                # a new table / column name the ANSI code page can't hold
                data = text.encode("utf-8-sig")
            put(lib.name, data=data)
        cur_text = text
    db = next((s for s in _sections(cur_text) if s[0] == "DatabaseLinks"), ["", []])
    url = url or _kv(db[1]).get("SVNRepositoryURL") or None      # the one already in the file
    if mode == "svndblib" and not url:
        out["warning"] = "no SVN repository URL (pass --svn-url, or sync into an svn working copy)"
    st = {"rows": sorted(managed_rows), "files": sorted(managed_files)}
    if st != {"rows": sorted(state.get("rows", [])), "files": sorted(state.get("files", []))}:
        put(STATE, json.dumps(st, indent=1) + "\n")
    out["svn_url"] = url
    return out


def summary(out):
    n = {}
    for r in out["results"]:
        n[r["action"]] = n.get(r["action"], 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(n.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync a plan.json into an Altium .DbLib / .SVNDbLib library folder")
    ap.add_argument("plan", help="plan.json from `ecadlib librarian` or `ecadlib altium365 plan`")
    ap.add_argument("--to", required=True, help="library folder (for --svn: an svn working copy)")
    ap.add_argument("--svn", action="store_true", help="write an .SVNDbLib (SVN Database Library) instead of a .DbLib")
    ap.add_argument("--name", default="ecadlib", help="base name of the .DbLib/.SVNDbLib, .xlsx and .csv")
    ap.add_argument("--prune", action="store_true", help="delete rows and model files (written by ecadlib) no longer in the plan")
    ap.add_argument("--win-dir", help=r"Windows folder the library lives in, for the Excel connection string (default C:\ecadlib\library; Altium finds the workbook relative to the DbLib either way)")
    ap.add_argument("--svn-url", help="repository URL of --to (default: from `svn info`)")
    ap.add_argument("--commit", action="store_true", help="svn add/commit the changed files (needs svn on PATH and a working copy)")
    ap.add_argument("-m", "--message", default="ecadlib dblib-sync", help="svn commit message")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("-o", "--out", help="write the JSON report here")
    ap.add_argument("--rules", help="library rules file: house parameter names (default: ecadlib.toml found from the plan)")
    a = ap.parse_args(argv)

    out = sync(a.plan, a.to, "svndblib" if a.svn else "dblib", a.name, a.prune, a.win_dir, a.svn_url, a.dry_run, rules=a.rules)
    from . import messages as M, term as TM
    t = TM.Term()
    rows = []
    for r in out["results"]:
        st = {"added": "ready", "updated": "ready", "unchanged": "skipped", "held": "held", "error": "failed",
              "removed": "info"}.get(r["action"], "info")
        detail = M.explain(r["detail"].removeprefix("held: "))["text"] if r["action"] == "held" else M.clean(r["detail"])
        if r["action"] in ("added", "updated", "unchanged") and not t.verbose:
            detail = ""
        rows.append([t.status(st, r["action"]), r["table"], r["name"], detail])
    t.table(rows)
    if out.get("warning"):
        t.note(M.clean(out["warning"]), None)
    if a.dry_run and not t.quiet:
        t.wrap("would write " + (", ".join(out["written"]) or "nothing"), 0)
    if a.svn and not a.dry_run:
        s = svn_step(a.to, out["written"], out["removed"], a.commit, a.message)
        out["svn"] = s
        t.write(f"svn: {s.get('note') or ('committed' if s['ran'] else s.get('error', ''))}")
        if not s["ran"]:
            for c in s["commands"]:
                t.write(f"  {c}")
    written = [] if a.dry_run else [(out["dblib"], "open this in Altium (it lists the parts from the .xlsx next to it)")]
    if not a.dry_run and t.verbose:
        written += [(w, "") for w in out["written"]]
    t.summary(f"{summary(out)}" + (" (dry run: nothing written; " + M.plural(len(out["written"]), "file") + " would be)" if a.dry_run else "")
              + (f"; removed {', '.join(out['removed'])}" if out["removed"] else ""),
              written=written,
              next=[(f"ecadlib dblib-sync {a.plan} --to {a.to}", "write it for real")] if a.dry_run else
              ([(f"ecadlib review {a.plan}", "the held parts")] if any(r["action"] == "held" for r in out["results"]) else []))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=2))
    errors = sum(r["action"] == "error" for r in out["results"])
    if errors or (out.get("svn") or {}).get("error"):
        sys.exit(f"{errors} part(s) failed" + (f"; svn: {out['svn']['error']}" if (out.get("svn") or {}).get("error") else ""))


if __name__ == "__main__":
    main()
