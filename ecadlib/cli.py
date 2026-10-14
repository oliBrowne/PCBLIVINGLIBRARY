"""One command for everything:  ecadlib <command> [options]   (or: python -m ecadlib <command>)

    ecadlib ui        --lib <kicad libs>          local web UI in your browser
    ecadlib mcp       --lib <kicad libs>          MCP server (stdio) for Claude and other agents
    ecadlib triage    BOM --lib ... -o out/x      BOM triage report (.md + .csv)
    ecadlib footprint gen|compare ...             IPC-7351B footprint generator
    ecadlib dblib     BOM -o out/altium           Altium DbLib (SQLite + XLSX + .DbLib)
    ecadlib altium-lib pcblib|schlib|dump ...     write/read Altium .PcbLib/.SchLib files
    ecadlib altium365 plan|workspaces|push ...    Altium 365 release via Nexar
    ecadlib librarian BOM --lib ... -o out/lib    BOM -> missing parts filled -> Altium models + plan + review
    ecadlib release-check plan.json [--allow P]   hold back plan items that fail the pre-release checks

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
    "librarian": ("ecadlib.librarian", "BOM to library: fill missing parts, plan, release check"),
    "release-check": ("ecadlib.release_check", "pre-release checks on an altium365 plan"),
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
    return importlib.import_module(cmd[0]).main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
