"""One command for everything:  ecadlib <command> [options]   (or: python -m ecadlib <command>)

    ecadlib ui        --lib <kicad libs>          local web UI in your browser
    ecadlib mcp       --lib <kicad libs>          MCP server (stdio) for Claude and other agents
    ecadlib triage    BOM --lib ... -o out/x      BOM triage report (.md + .csv)
    ecadlib footprint gen|compare ...             IPC-7351B footprint generator
    ecadlib dblib     BOM -o out/altium           Altium DbLib (SQLite + XLSX + .DbLib)
    ecadlib altium-lib pcblib|schlib|dump ...     write/read Altium .PcbLib/.SchLib files
    ecadlib altium365 plan|workspaces|push ...    Altium 365 release via Nexar
    ecadlib dblib-sync plan.json --to DIR [--svn] Altium without 365: sync a plan into a .DbLib/.SVNDbLib folder
    ecadlib librarian BOM --lib ... -o out/lib    BOM -> missing parts filled -> Altium models + plan + review
    ecadlib release-check plan.json [--allow P]   hold back plan items that fail the pre-release checks
    ecadlib kicad-export plan.json -o DIR --name LIB   the same library as KiCad files (.kicad_sym, .pretty)
    ecadlib resolve   plan.json [--part P] list|show|choose ...   fix held parts: ranked candidates, pick one
    ecadlib upstream  check-kicad|check-a365 ...  upstream change alerts (KiCad sources, Altium 365 drift)
    ecadlib approve   plan.json approve|reject|status [--part P]   sign-off before altium365 push
    ecadlib dedupe    plan.json [--lib ...] [--merge]  duplicate parts, footprints and symbols
    ecadlib sourcing  BOM [--qty N] [--alternates] price, stock, second sources and alternates per BOM line
    ecadlib project   BOARD.PrjPcb [-o bom.csv] [--variant V]   BOM straight from an Altium project (.PrjPcb/.SchDoc)
    ecadlib where-used --projects DIR... [--part X] [--plan plan.json]   which projects use which library part
    ecadlib step-gen  SPEC.json|FOOTPRINT -o x.step   package-shaped 3D model (STEP); --check FILE.step validates
    ecadlib upkeep    PLAN|BOM [--checks ...] [--apply] [--github issue|pr]   weekly checks, only what is new, one issue/PR

`ecadlib <command> -h` shows that command's options."""
import importlib, sys

COMMANDS = {
    "ui": ("ecadlib.ui.server", "local web UI"),
    "mcp": ("ecadlib.mcp_server", "MCP server over stdio"),
    "triage": ("ecadlib.bom_triage", "BOM triage report"),
    "footprint": ("ecadlib.footprint_gen", "IPC-7351B footprint generator"),
    "check": ("ecadlib.footprint_check", "check .kicad_mod files against KLC/IPC rules"),
    "dblib": ("ecadlib.altium_export", "Altium DbLib export"),
    "altium-lib": ("ecadlib.altium_lib", "Altium .PcbLib/.SchLib writer"),
    "altium365": ("ecadlib.altium365", "Altium 365 release via Nexar"),
    "dblib-sync": ("ecadlib.altium_dblib_sync", "sync a plan into an Altium .DbLib / .SVNDbLib library folder"),
    "librarian": ("ecadlib.librarian", "BOM to library: fill missing parts, plan, release check"),
    "release-check": ("ecadlib.release_check", "pre-release checks on an altium365 plan"),
    "kicad-export": ("ecadlib.kicad_export", "librarian plan -> KiCad .kicad_sym / .pretty / .3dshapes"),
    "resolve": ("ecadlib.resolve", "fix held parts: candidates for a missing symbol/footprint, choose one"),
    "upstream": ("ecadlib.upstream", "KiCad upstream changes and Altium 365 drift"),
    "approve": ("ecadlib.approvals", "approve / reject plan items before release"),
    "dedupe": ("ecadlib.dedupe", "find and merge duplicate parts, footprints and symbols"),
    "sourcing": ("ecadlib.sourcing", "price, stock, second sources and alternates for a BOM"),
    "step-gen": ("ecadlib.step_gen", "package-shaped STEP 3D models from a package spec or footprint name"),
    "upkeep": ("ecadlib.upkeep", "weekly lifecycle / upstream / price / A365 checks; new changes as one issue or PR"),
    "project": ("ecadlib.altium_project", "BOM straight from an Altium project (.PrjPcb / .SchDoc / ActiveBOM)"),
    "where-used": ("ecadlib.altium_project:where_used_main", "which projects use which library part, and flags"),
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] in ("-V", "--version"):
        from . import __version__
        print(f"ecadlib {__version__}")
        return 0
    cmd = COMMANDS.get(argv[0])
    if not cmd:
        print(f"ecadlib: unknown command {argv[0]!r}. Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    sys.argv = [f"ecadlib {argv[0]}"] + argv[1:]           # so argparse usage lines show the right command
    mod, _, fn = cmd[0].partition(":")
    return getattr(importlib.import_module(mod), fn or "main")(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
