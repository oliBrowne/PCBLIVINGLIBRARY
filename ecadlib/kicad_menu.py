"""The ecadlib window inside KiCad: what the PCB editor plugin runs (add part, check the project BOM, fill the library,
review queue), the same flow as the ecadlib menu in Altium (altium_menu), with a KiCad library as the target.

    ecadlib kicad-menu run-job JOB --action add-part --project BOARD.kicad_pcb --mpn LM358DR [--manufacturer TI]
    ecadlib kicad-menu run-job JOB --action check-bom|fill --project BOARD.kicad_pcb|SCHEMATIC.kicad_sch
    ecadlib kicad-menu run-job JOB --action queue|open --project BOARD.kicad_pcb [--path REPORT]
    ecadlib kicad-menu run-job JOB --action assembly --project BOARD.kicad_pcb   JLCPCB BOM + CPL, pads, rotations
    ecadlib kicad-menu check-bom "%I"      # the schematic editor's Tools > Generate BOM hook (a netlist or .kicad_sch)

The plugin (ecadlib/assets/kicad_action_plugin.py) runs in KiCad's own Python, starts `run-job` in the background and
reads JOB/progress.ini (live steps) and JOB/result.ini (the answer: big, row1..rowN "label|value|ok", message, fix,
footprint to place) - the same files, written the same way, as the Altium dialog reads.

Per project, next to the board: ecadlib-work/ is the ecadlib workspace (one parts list, uploads/altium-menu/parts.csv,
for every part added or filled, so one plan makes the whole library) and ecadlib/ the KiCad library (ecadlib.kicad_sym,
ecadlib.pretty, ecadlib.3dshapes, kicad_export.json). The plugin adds ${KIPRJMOD}/ecadlib/... rows to the project's
library tables and places the footprint; nothing here needs KiCad."""
import argparse, csv, json, sys
from pathlib import Path

from . import altium_menu as AM

WORK, LIB = "ecadlib-work", "ecadlib"
ACTIONS = ("add-part", "check-bom", "fill", "queue", "open", "ai-read", "assembly")


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


def board_of(project):
    """The .kicad_pcb of a project: itself, <stem>.kicad_pcb next to a .kicad_pro / .kicad_sch, or the only board."""
    p = Path(project)
    if p.suffix.lower() == ".kicad_pcb" and p.is_file():
        return p
    if p.with_suffix(".kicad_pcb").is_file():
        return p.with_suffix(".kicad_pcb")
    boards = sorted(project_dir(p).glob("*.kicad_pcb"))
    if len(boards) == 1:
        return boards[0]
    raise ValueError(f"no board for {p.name}: save the board (.kicad_pcb) next to the project")


def assembly(cfg, project, opener=None):
    """The Assembly check: the board's parts against JLCPCB's footprints, the BOM + CPL in
    ecadlib-work/assembly/<board>/ and the report (ecadlib.assembly_io.menu_check)."""
    from . import assembly_io as IO
    from .httpcache import CachedHTTP
    board = board_of(project)
    ws = Path(cfg["workspace"])
    return IO.menu_check(board, ws / "assembly" / board.stem, None, CachedHTTP(ws / "http_cache", offline=cfg.get("offline")),
                         opener=opener)


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
    if action == "assembly":
        return assembly(cfg, project, opener=opener)
    raise ValueError(f"unknown action {action!r} (one of {', '.join(ACTIONS)})")


def run_job(job, action, project, mpn="", manufacturer="", path="", opener=None, offline=False, ai=None):
    """What the plugin runs: JOB/progress.ini while working, JOB/result.ini at the end. Returns the result dict."""
    job = Path(job)
    job.mkdir(parents=True, exist_ok=True)
    try:
        cfg = config(project, offline=offline)
        res = run_action(cfg, action, project, mpn, manufacturer, path,
                         opener=False if action in ("check-bom", "assembly") else opener, progress=AM.Progress(job), ai=ai)
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
        res = run_job(a.job, a.action, a.project, a.mpn, a.manufacturer, a.path, offline=a.offline, ai=ai)
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
