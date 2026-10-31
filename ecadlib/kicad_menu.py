"""The ecadlib window inside KiCad: what the PCB editor plugin runs (add part, check the project BOM, fill the library,
review queue), the same flow as the ecadlib menu in Altium (altium_menu), with a KiCad library as the target.

    ecadlib kicad-menu run-job JOB --action add-part --project BOARD.kicad_pcb --mpn LM358DR [--manufacturer TI]
    ecadlib kicad-menu run-job JOB --action check-bom|fill --project BOARD.kicad_pcb|SCHEMATIC.kicad_sch
    ecadlib kicad-menu run-job JOB --action queue|open --project BOARD.kicad_pcb [--path REPORT]
    ecadlib kicad-menu check-bom "%I"      # the schematic editor's Tools > Generate BOM hook (a netlist or .kicad_sch)

The plugin (ecadlib/assets/kicad_action_plugin.py) runs in KiCad's own Python, starts `run-job` in the background and
reads JOB/progress.ini (live steps) and JOB/result.ini (the answer: big, row1..rowN "label|value|ok", message, fix,
footprint to place) - the same files, written the same way, as the Altium dialog reads.

Per project, next to the board: ecadlib-work/ is the ecadlib workspace (one parts list, uploads/altium-menu/parts.csv,
for every part added or filled, so one plan makes the whole library) and ecadlib/ the KiCad library (ecadlib.kicad_sym,
ecadlib.pretty, ecadlib.3dshapes, kicad_export.json). The job itself lists the library in the project's library tables
(${KIPRJMOD}/ecadlib/... rows), so a window closed while it works loses nothing, and for a KiCad 7 window (--kicad 7)
writes the library in KiCad 7's file formats; the plugin places the footprint. Nothing here needs KiCad."""
import argparse, csv, json, re, sys
from pathlib import Path

from . import altium_menu as AM

WORK, LIB = "ecadlib-work", "ecadlib"
ACTIONS = ("add-part", "check-bom", "fill", "queue", "open", "ai-read")


def project_dir(project):
    p = Path(project)
    return p if p.is_dir() else p.parent


def config(project, port=8765, offline=False):
    """The menu settings for a KiCad project: workspace ecadlib-work/, library ecadlib/ next to the board."""
    d = project_dir(project).resolve()
    return {"workspace": d / WORK, "library": d / LIB, "name": LIB, "port": port, "offline": offline, "tool": "kicad"}


def schematic_of(project):
    """The root schematic of the board's project: <stem>.kicad_sch next to the .kicad_pcb / .kicad_pro, or the only
    .kicad_pro's. A .kicad_sch or a netlist is itself."""
    p = Path(project)
    if p.is_file() and p.suffix.lower() in (".kicad_sch", ".xml", ".net"):
        return p
    d = project_dir(p)
    if p.suffix.lower() in (".kicad_pcb", ".kicad_pro") and p.with_suffix(".kicad_sch").is_file():
        return p.with_suffix(".kicad_sch")
    pros = sorted(d.glob("*.kicad_pro"))
    if len(pros) == 1 and pros[0].with_suffix(".kicad_sch").is_file():
        return pros[0].with_suffix(".kicad_sch")
    raise ValueError(f"no schematic for {p.name}: save the schematic next to the board (same name, .kicad_sch)")


def adopt_old_list(cfg):
    """Parts the first plugin (ecadlib-work/parts.csv) added go into the menu's list once."""
    old = Path(cfg["workspace"]) / "parts.csv"
    if not old.is_file() or (Path(cfg["workspace"]) / AM.MENU_BOM).is_file():
        return 0
    n = 0
    for r in csv.DictReader(old.open(encoding="utf-8", newline="")):
        if (r.get("Mfr. Part #") or "").strip():
            AM.add_to_parts(cfg["workspace"], r["Mfr. Part #"], r.get("Manufacturer", ""))
            n += 1
    return n


TABLES = (("sym", "sym_lib_table", f"{LIB}/{LIB}.kicad_sym", "Symbol"), ("fp", "fp_lib_table", f"{LIB}/{LIB}.pretty", "Footprint"))


def project_tables(cfg):
    """List the built library in the project's sym-lib-table / fp-lib-table (created when missing) as "ecadlib",
    ${KIPRJMOD}/ecadlib/...; rows are found whatever their layout (one line or pretty-printed), so an existing row is
    never doubled (KiCad refuses a whole table with a duplicate nickname). An "ecadlib" row of another library is left
    alone. -> {"tables": "added" | "listed" | "problem" | "", "tables_note": the problem}; "" when nothing is built."""
    from . import kicad_dblib as KD, sexpr
    lib = Path(cfg["library"])
    if not (lib / f"{LIB}.kicad_sym").is_file():
        return {"tables": ""}
    d, added = lib.parent, False
    for kind, table, rel, what in TABLES:
        path, uri = d / f"{kind}-lib-table", f"${{KIPRJMOD}}/{rel}"
        rows = []
        if path.is_file():
            text = path.read_text(encoding="utf-8").lstrip("﻿")
            for s, e in KD._row_spans(text):
                node = sexpr.parse(text[s:e])
                if isinstance(node, list) and node[:1] == ["lib"] and str((sexpr.first(node, "name") or [0, ""])[1]) == LIB:
                    rows.append(str((sexpr.first(node, "uri") or [0, ""])[1]))
        other = [u for u in rows if u.replace("\\", "/").rstrip("/") != uri]
        if other:
            return {"tables": "problem", "tables_note": (
                f"the project's {kind}-lib-table already has a library named {LIB} at {other[0]}, not {uri} where "
                f"ecadlib builds the parts: rename or remove that row (Preferences > Manage {what} Libraries > Project)")}
        if len(rows) != 1:                       # none yet, or the same row twice (add_lib_row keeps one)
            try:
                KD.add_lib_row(path, LIB, uri, "ecadlib parts", typ="KiCad", table=table)
            except (OSError, ValueError) as e:
                return {"tables": "problem", "tables_note": str(e)}
            added = True
    return {"tables": "added" if added else "listed"}


def kicad7_library(cfg):
    """A KiCad 7 window: the library kicad_export wrote (KiCad 8 formats; KiCad 7 refuses a 20231120 symbol library)
    in KiCad 7's formats (kicad_pcm's converters), and kicad_export.json's record of the files updated to match, so the
    next export does not take the converted files for hand edits. -> the files converted."""
    import hashlib
    from . import fsutil, kicad_pcm as KP
    lib = Path(cfg["library"])
    files = [(lib / f"{LIB}.kicad_sym", KP.symbol_kicad7)] + [
        (f, KP.footprint_kicad7) for f in sorted((lib / f"{LIB}.pretty").glob("*.kicad_mod"))]
    rep_f = lib / "kicad_export.json"
    try:
        rep = json.loads(rep_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rep = None
    done = []
    for f, conv in files:
        if not f.is_file():
            continue
        old = f.read_text(encoding="utf-8")
        new = conv(old)
        if new == old:
            continue
        fsutil.write_text_atomic(f, new)
        done.append(f)
        if isinstance(rep, dict):
            rel, h = f.relative_to(lib).as_posix(), hashlib.sha256(new.encode("utf-8")).hexdigest()
            for rec in [rep] + [v for v in (rep.get("libraries") or {}).values() if isinstance(v, dict)]:
                if isinstance(rec.get("written"), dict) and rel in rec["written"]:
                    rec["written"][rel] = h
    if done and isinstance(rep, dict):
        fsutil.write_text_atomic(rep_f, json.dumps(rep, indent=2) + "\n")
    return done


def symbol_fields(cfg, names):
    """(reference prefix, value) of the exported symbol of one of the parts `names`, e.g. ("R", "10k"): what the placed
    footprint gets instead of KiCad's REF** and the footprint name. ("", "") when the part is not exported."""
    from . import sexpr
    lib = Path(cfg["library"])
    try:
        parts = json.loads((lib / "kicad_export.json").read_text(encoding="utf-8")).get("parts", [])
        tree = sexpr.parse((lib / f"{LIB}.kicad_sym").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    syms = {str(s[1]): s for s in sexpr.find(tree, "symbol") if len(s) > 1}
    for p in parts:
        if p.get("part") in names and str(p.get("symbol_name") or "") in syms:
            props = {str(q[1]): str(q[2]) for q in sexpr.find(syms[str(p["symbol_name"])], "property") if len(q) > 2}
            ref = re.sub(r"[^A-Za-z_#]+$", "", props.get("Reference", "")) or "U"
            return ref, props.get("Value") or p.get("part") or ""
    return "", ""


def _exported_footprint(cfg, names):
    rep = Path(cfg["library"]) / "kicad_export.json"
    try:
        parts = json.loads(rep.read_text(encoding="utf-8")).get("parts", [])
    except (OSError, ValueError):
        return ""
    return next((p.get("footprint") or "" for p in parts if p.get("part") in names), "")


def add_part(cfg, mpn, manufacturer="", progress=None):
    adopt_old_list(cfg)
    res = AM.add_part(cfg, mpn, manufacturer, progress=progress)
    if not res.get("footprint") and res.get("status") == "duplicate":     # already in the library: place that one
        res["footprint"] = _exported_footprint(cfg, {res.get("part"), *res.get("message", "").rstrip(".").split()[-1:]})
    return res


def fill(cfg, project, progress=None):
    """The schematic's fitted lines with a part number go into the menu's parts list; the librarian builds the list
    and the whole list is exported (one plan = the whole KiCad library)."""
    from .bom_triage import read_bom
    adopt_old_list(cfg)
    lines, _ = read_bom(schematic_of(project))
    skipped = 0
    for ln in lines:
        if ln.get("dnp"):
            continue
        if not str(ln.get("mpn") or "").strip() or not AM.MPN_RE.match(str(ln["mpn"]).strip()):
            skipped += 1
            continue
        AM.add_to_parts(cfg["workspace"], ln["mpn"], ln.get("manufacturer", ""), value=ln.get("value", ""),
                        footprint=ln.get("footprint", ""), designator=ln.get("designator", ""), lcsc=ln.get("lcsc", ""),
                        datasheet=ln.get("datasheet", ""))
    res = AM.fill(cfg, Path(cfg["workspace"]) / AM.MENU_BOM, progress=progress)
    if skipped:
        from . import messages as M
        res["message"] += f" {M.plural(skipped, 'schematic line')} without a part number left out."
    return res


def run_action(cfg, action, project, mpn="", manufacturer="", path="", opener=None, progress=None, ai=None):
    if action == "ai-read":                    # only after the window showed the cost and the person said yes
        return AM.ai_read(cfg, dict(ai or {}, mpn=mpn), progress=progress)
    if action == "add-part":
        return add_part(cfg, mpn, manufacturer, progress=progress)
    if action == "check-bom":
        return AM.check_bom(cfg, schematic_of(project), opener=opener, progress=progress)
    if action == "fill":
        return fill(cfg, project, progress=progress)
    if action == "queue":
        return AM.open_queue(cfg, opener=opener)
    if action == "open":
        return AM.open_file(cfg, path, opener=opener)
    raise ValueError(f"unknown action {action!r} (one of {', '.join(ACTIONS)})")


def run_job(job, action, project, mpn="", manufacturer="", path="", opener=None, offline=False, ai=None, kicad=None):
    """What the plugin runs: JOB/progress.ini while working, JOB/result.ini at the end. Returns the result dict.
    kicad: the major version of the KiCad window that started it (7: the library in KiCad 7's formats). A job that
    builds the library also lists it in the project's library tables ("tables" in the result), so it holds when the
    window was closed meanwhile; a footprint to place comes with the reference prefix and value it gets."""
    job = Path(job)
    job.mkdir(parents=True, exist_ok=True)
    try:
        cfg = config(project, offline=offline)
        res = run_action(cfg, action, project, mpn, manufacturer, path,
                         opener=False if action == "check-bom" else opener, progress=AM.Progress(job), ai=ai)
        if action in ("add-part", "fill", "ai-read"):
            if kicad and int(kicad) < 8:
                kicad7_library(cfg)
            res.update(project_tables(cfg))
            if res.get("footprint"):
                ref, value = symbol_fields(cfg, {res.get("part"), *res.get("message", "").rstrip(".").split()[-1:]})
                if ref:
                    res.update(reference=ref, value=value)
    except Exception as e:                                   # every failure becomes one dialog line with a fix
        res = AM._error(e)
    AM.write_result(job / "result.ini", res)
    if job.parent.name.lower() == "jobs":                    # the plugin's jobs/<id>: never prune a folder chosen by hand
        AM.prune_jobs(job.parent, keep=job)
    (job / "exit.txt").write_text("0\n", encoding="utf-8")
    return res


# ------------------------------------------------------------------ the toolbar icon
def icon_png(size=24):
    """The ecadlib toolbar icon (the Sprout Trace mark on a dark tile, see ecadlib/brand.py) as PNG bytes."""
    from .brand import mark_png
    return mark_png(size, tile=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib kicad-menu",
                                 description="what the ecadlib window in KiCad's PCB editor runs (add part, check BOM, "
                                             "fill the library, review queue)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run-job", help="what the KiCad plugin runs: JOB/progress.ini while working, JOB/result.ini")
    r.add_argument("job")
    r.add_argument("--action", required=True, choices=ACTIONS)
    r.add_argument("--project", required=True, help="the board (.kicad_pcb), its project or schematic")
    r.add_argument("--mpn", default="")
    r.add_argument("--manufacturer", default="")
    r.add_argument("--path", default="", help="open: the report to open")
    r.add_argument("--offline", action="store_true", help="cached downloads and standard parts only")
    r.add_argument("--kicad", type=int, default=None, help="the KiCad window's major version (7: KiCad 7 file formats)")
    r.add_argument("--bom", default="", help="ai-read: the workspace BOM the AI step's cost was shown for")
    r.add_argument("--part", default="", help="ai-read: only this part")
    r.add_argument("--approved-usd", default="", help="ai-read: the amount the person approved (the reads are held to it)")
    r.add_argument("--over-limit-ok", action="store_true", help="ai-read: the person said yes to a cost over the limit")
    r.add_argument("--price-unknown-ok", action="store_true", help="ai-read: the person said yes without a known price")
    c = sub.add_parser("check-bom", help='check a schematic against the library and open the report (Generate BOM: "%%I")')
    c.add_argument("project", help="netlist (.xml) KiCad's Generate BOM passes as %%I, a .kicad_sch or a board")
    c.add_argument("output", nargs="?", help="KiCad's %%O (ignored)")
    c.add_argument("--no-open", action="store_true")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run-job":
        ai = {"bom": a.bom, "part": a.part, "approved_usd": a.approved_usd,
              "over_limit_ok": "1" if a.over_limit_ok else "", "price_unknown_ok": "1" if a.price_unknown_ok else ""}
        res = run_job(a.job, a.action, a.project, a.mpn, a.manufacturer, a.path, offline=a.offline, ai=ai, kicad=a.kicad)
        return 0 if res.get("status") != "error" else 1
    try:
        res = AM.check_bom(config(a.project), schematic_of(a.project), opener=False if a.no_open else None)
    except (OSError, ValueError) as e:
        print(f"ecadlib kicad-menu: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if a.json else res["message"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
