"""One command for everything:  ecadlib <command> [options]   (or: python -m ecadlib <command>)

    ecadlib setup                                 the setup page in your browser: AI model + key, Altium / KiCad, folders
    ecadlib start                                 the desktop app (what the Windows Start menu entry runs)
    ecadlib keys      list|set NAME|remove NAME   API keys stored for you (encrypted on Windows), instead of env variables
    ecadlib init      [--yes] [--tool T] [--lib DIR] [--from DIR ...]   set up: tool, libraries, workspace, sources -> ecadlib.toml, doctor
    ecadlib demo      [-o DIR]                    a small library from a bundled BOM, offline, no keys (start here)
    ecadlib rules     check|show                  validate the rules file / print the defaults
    ecadlib style     learn|show|direct|check|apply|export-dataset   learn your house style from your library + your direction
    ecadlib ui        --lib <kicad libs>          local web UI in your browser (--team: shared server with sign-in)
    ecadlib team      add-user|set-role|reset-password|remove|list|setup   users and roles for `ecadlib ui --team`; setup: Docker + HTTPS
    ecadlib mcp       --lib <kicad libs>          MCP server for Claude, Codex, Grok and other agents (stdio, or --http)
    ecadlib triage    BOM --lib ... -o out/x      BOM triage report (.md + .csv)
    ecadlib sources-check [--save DIR] [--json]   check the unofficial EasyEDA / JLCPCB endpoints against the live sites
    ecadlib footprint gen|compare ...             IPC-7351B footprint generator (also BGA, pin headers, connectors, pad tables)
    ecadlib check     x.kicad_mod|DIR.pretty [--summary]   footprints against the rule book (KLC / IPC-7351)
    ecadlib fp-edit   PLAN --part P show|rename OLD=NEW|same-net A,B|undo   fix a held part's pad table by hand
    ecadlib footprint-bench [--offline]           compare generated footprints with KiCad's official ones, pad by pad
    ecadlib eval      run|compare|sets|show ...   accuracy test set: run the pipeline on known parts, pass rate and cost
    ecadlib dblib     BOM -o out/altium           Altium DbLib (SQLite + XLSX + .DbLib)
    ecadlib altium-lib pcblib|schlib|dump ...     write/read Altium .PcbLib/.SchLib files
    ecadlib altium-native write|compare DIR       let Altium build the .PcbLib/.SchLib files itself; compare the result
    ecadlib altium365 plan|workspaces|push ...    Altium 365 release via Nexar
    ecadlib dblib-sync plan.json --to DIR [--svn] Altium without 365: sync a plan into a .DbLib/.SVNDbLib folder
    ecadlib librarian BOM --lib ... -o out/lib    BOM -> missing parts filled -> Altium models + plan + review
    ecadlib datasheet PDF --part MPN | batch PLAN|BOM | estimate PLAN|BOM   Claude reads datasheets -> symbols + footprints
    ecadlib release-check plan.json [--allow P]   hold back plan items that fail the pre-release checks
    ecadlib kicad-export plan.json -o DIR --name LIB   the same library as KiCad files (.kicad_sym, .pretty)
    ecadlib resolve   plan.json [--part P] list|show|choose ...   fix held parts: ranked candidates, pick one
    ecadlib review    [plan.json] [--part P] [--yes]   go through held parts one by one: why, pick a candidate, release anyway
    ecadlib upstream  check-kicad|check-a365 ...  upstream change alerts (KiCad sources, Altium 365 drift)
    ecadlib a365      pull|status|reconcile PLAN --workspace URL [--state F]   two-way Altium 365 sync
    ecadlib queue     list|show|approve|reject|rebuild|retry [ID]   the library agent's review queue
    ecadlib approve   plan.json approve|reject|status [--part P]   sign-off before altium365 push
    ecadlib dedupe    plan.json [--lib ...] [--merge]  duplicate parts, footprints and symbols
    ecadlib sourcing  BOM [--qty N] [--alternates] price, stock, second sources and alternates per BOM line
    ecadlib assembly  check|export BOARD.kicad_pcb|PNP.csv [-o DIR] | part FP --lcsc C123   assembler BOM + CPL, pads, rotations
    ecadlib project   BOARD.PrjPcb [-o bom.csv] [--variant V]   BOM straight from an Altium project (.PrjPcb/.SchDoc)
    ecadlib hub       [-q WORDS] [--have T] [--lack T] [--part P]   your library across tools: every part once, a column per tool
    ecadlib where-used --projects DIR... [--part X] [--plan plan.json]   which projects use which library part
    ecadlib part      status|show|diff|history|approve|reject|revert [LIBRARY]   change control for released parts
    ecadlib step-gen  SPEC.json|FOOTPRINT -o x.step   package-shaped 3D model (STEP); --check FILE.step validates
    ecadlib upkeep    PLAN|BOM [--checks ...] [--apply] [--github issue|pr]   weekly checks, only what is new, one issue/PR
    ecadlib sim-models plan.json [--models DIR] [--attach]   find, check and attach SPICE / IBIS simulation models
    ecadlib agent-watch [--dry-run] [--source TYPE:REF]   library agent: new / changed / removed parts in watched projects and BOMs
    ecadlib agent     run [--dry-run]|serve|status|approve ID|reject ID|push   library agent: watch, build, prove, PR, push
    ecadlib symbol    PINS.csv|PART -o x.SchLib [--preview x.svg]   big-chip symbol with pins grouped by function
    ecadlib visual-check PART|x.kicad_mod --datasheet DS.pdf [--pages 3,7-8]   Claude compares our drawings with the datasheet
    ecadlib doctor    [--workspace W] [--lib DIR] [--json]   check Python, packages, API keys, ecadlib.toml, folders
    ecadlib status    [--workspace W] [--json]    one-screen summary of a workspace: parts, held, sign-off, last runs
    ecadlib completion bash|zsh|fish              shell completion script (e.g. ecadlib completion bash > ~/.ecadlib.bash)
    ecadlib plugins   list|export NAME PLAN -o DIR   installed part-source / exporter plugins (Python entry points)
    ecadlib notify    show|test [--event E]       [notify] webhooks (Slack, Teams, JSON): targets, send a test message
    ecadlib kicad-dblib plan.json -o DIR --name LIB [--export]   KiCad database library (.kicad_dbl + SQLite)
    ecadlib export-parts plan.json --format csv|json|xlsx [-o F]   parts table with stable columns (PLM / ERP)
    ecadlib stdparts resolve "SOIC-8 3.9x4.9mm" | coverage BOM | bench   standard packages built offline (librarian source)
    ecadlib kicad-pcm plan.json|KICAD_DIR --out DIR [--kicad 7]   KiCad Plugin and Content Manager zip (--plugin: action plugin)
    ecadlib altium-pkg plan.json|MODELS_DIR --out DIR   Altium .LibPkg (-> IntLib), install script, relative DbLib
    ecadlib altium-menu build --out DIR [--win-dir W]   an "ecadlib" menu inside Altium (script project): add part, check BOM, queue
    ecadlib kicad-menu check-bom NETLIST|SCH     what the ecadlib window in KiCad runs; Generate BOM hook for the schematic
    ecadlib costs     [--model M] [--all] [--md F] [--xlsx F]   estimated AI cost per job, for your model or every model
    ecadlib ai        show [--model M] [--detect] | rails   which model / provider / guide rails the AI features use
    ecadlib migrate   [PATH] [--check] [--dry-run]   check / upgrade workspace files to this version (backups first)
    ecadlib rebuild   LIB_DIR [--check]           rebuild a library from library.lock.json offline; report drift
    ecadlib testpack  --out DIR                   small Altium + KiCad test pack (6 parts) + CHECKLIST.md for a person to open
    ecadlib parity    plan.json [--kicad DIR] [--altium DIR] | pair A B | survey   KiCad and Altium files of every part match

`ecadlib <command> -h` shows that command's options. -q / --quiet prints only the outcome, -v / --verbose every
detail; colour only on a terminal (NO_COLOR=1 or ECADLIB_COLOR=never turns it off). --debug (or ECADLIB_DEBUG=1) shows tracebacks instead of a
one-line error. ECADLIB_LIB, ECADLIB_WORKSPACE and ECADLIB_CONFIG are defaults for --lib, --workspace and the rules file."""
import importlib, sys

COMMANDS = {
    "setup": ("ecadlib.easy_setup", "open the setup page: AI model and key, Altium / KiCad, folders, add ecadlib to Altium and KiCad"),
    "start": ("ecadlib.launcher", "the desktop app: start ecadlib in the background and open it in the browser (setup page on first run)"),
    "keys": ("ecadlib.userconfig", "API keys stored for this user instead of environment variables: list, set, remove"),
    "init": ("ecadlib.rules_infer", "set up ecadlib: tool, library folders, workspace, sources; learn your conventions -> ecadlib.toml"),
    "demo": ("ecadlib.firstrun:demo_main", "build a small library from a bundled sample BOM, offline, no API keys"),
    "rules": ("ecadlib.rules", "check or show the library rules"),
    "style": ("ecadlib.house_style", "learn your house style from your library and your written direction; new parts follow it"),
    "ui": ("ecadlib.ui.server", "local web UI"),
    "team": ("ecadlib.team", "users and roles for the team web server (ecadlib ui --team)"),
    "mcp": ("ecadlib.mcp_server", "MCP server for AI agents (stdio, or --http)"),
    "triage": ("ecadlib.bom_triage", "BOM triage report"),
    "sources-check": ("ecadlib.sources_check", "check the unofficial EasyEDA / JLCPCB endpoints against the live sites"),
    "footprint": ("ecadlib.footprint_gen", "IPC-7351B footprint generator"),
    "check": ("ecadlib.footprint_rules", "check .kicad_mod files against the footprint rule book (KLC / IPC-7351)"),
    "fp-edit": ("ecadlib.fp_edit", "edit a held part's datasheet footprint: rename pads (pad 1), join pads on one net"),
    "footprint-bench": ("ecadlib.footprint_bench", "compare generated footprints (BGA, connectors, ...) with KiCad's official ones"),
    "eval": ("ecadlib.eval.cli", "accuracy test set: run the pipeline on parts with a known answer, score, compare runs"),
    "dblib": ("ecadlib.altium_export", "Altium DbLib export"),
    "altium-lib": ("ecadlib.altium_lib", "Altium .PcbLib/.SchLib writer"),
    "altium-native": ("ecadlib.altium_native", "let Altium build the .PcbLib/.SchLib files with its own API; compare the result"),
    "altium365": ("ecadlib.altium365", "Altium 365 release via Nexar"),
    "dblib-sync": ("ecadlib.altium_dblib_sync", "sync a plan into an Altium .DbLib / .SVNDbLib library folder"),
    "librarian": ("ecadlib.librarian", "BOM to library: fill missing parts, plan, release check"),
    "costs": ("ecadlib.costs", "estimated AI cost per job (datasheet read, BOM, library) for your model or every model"),
    "datasheet": ("ecadlib.datasheet", "Claude datasheet reader: one PDF, or a whole plan/BOM as one Message Batch"),
    "release-check": ("ecadlib.release_check", "pre-release checks on an altium365 plan"),
    "kicad-export": ("ecadlib.kicad_export", "librarian plan -> KiCad .kicad_sym / .pretty / .3dshapes"),
    "resolve": ("ecadlib.resolve", "fix held parts: candidates for a missing symbol/footprint, choose one"),
    "review": ("ecadlib.review_cli", "go through held parts one at a time: the reason in plain words, pick a candidate, release anyway, open the datasheet"),
    "upstream": ("ecadlib.upstream", "KiCad upstream changes and Altium 365 drift"),
    "a365": ("ecadlib.a365_sync", "two-way Altium 365 sync: pull workspace components into the plan, status, reconcile"),
    "queue": ("ecadlib.agent_queue", "the library agent's review queue: list, show, approve, reject, rebuild"),
    "approve": ("ecadlib.approvals", "approve / reject plan items before release"),
    "dedupe": ("ecadlib.dedupe", "find and merge duplicate parts, footprints and symbols"),
    "sourcing": ("ecadlib.sourcing", "price, stock, second sources and alternates for a BOM"),
    "step-gen": ("ecadlib.step_gen", "package-shaped STEP 3D models from a package spec or footprint name"),
    "upkeep": ("ecadlib.upkeep", "weekly lifecycle / upstream / price / A365 checks; new changes as one issue or PR"),
    "assembly": ("ecadlib.assembly_cli", "assembly-ready parts: pads against JLCPCB's footprints, CPL rotations, the assembler's BOM + CPL"),
    "project": ("ecadlib.altium_project", "BOM straight from an Altium project (.PrjPcb / .SchDoc / ActiveBOM)"),
    "hub": ("ecadlib.hub", "your library across tools: every part once, a column per tool, last changed and by whom"),
    "where-used": ("ecadlib.altium_project:where_used_main", "which projects use which library part, and flags"),
    "part": ("ecadlib.change_cli", "change control: held changes to released parts, diff, history, approve / reject / revert"),
    "sim-models": ("ecadlib.sim_models", "find, check and attach SPICE / IBIS simulation models for a plan"),
    "agent-watch": ("ecadlib.agent_watch", "library agent watch step: new / changed / removed parts in watched projects and BOMs"),
    "agent": ("ecadlib.agent", "library agent: watch projects, build + prove parts, batch PRs, push approved parts, upkeep"),
    "visual-check": ("ecadlib.visual_check", "render footprint/symbol + datasheet pages; Claude checks they match"),
    "symbol": ("ecadlib.symbol_layout", "IC symbol with pins grouped by function (power, ports, debug ...), SVG preview"),
    "doctor": ("ecadlib.doctor", "check the setup: Python, packages, API keys (names only), ecadlib.toml, folders"),
    "status": ("ecadlib.ws_status", "one-screen summary of a workspace: parts, held, waiting for sign-off, last runs"),
    "completion": ("ecadlib.completion", "shell completion script for bash, zsh or fish"),
    "plugins": ("ecadlib.plugins", "installed part-source and exporter plugins; run an exporter on a plan"),
    "notify": ("ecadlib.notify", "webhook notifications ([notify]): show targets, send a test message"),
    "kicad-dblib": ("ecadlib.kicad_dblib", "KiCad database library: .kicad_dbl (ODBC) + SQLite parts table from a plan"),
    "export-parts": ("ecadlib.parts_export", "parts table with stable columns as CSV / JSON / XLSX for PLM / ERP imports"),
    "kicad-pcm": ("ecadlib.kicad_pcm", "package the library for KiCad's Plugin and Content Manager (zip); --plugin: the add-part action plugin"),
    "altium-pkg": ("ecadlib.altium_pkg", "Altium hand-off folder: .LibPkg to compile an IntLib, install script project, relative DbLib"),
    "altium-menu": ("ecadlib.altium_menu", "an 'ecadlib' menu inside Altium: DelphiScript project (add part from MPN, check the project BOM, review queue)"),
    "kicad-menu": ("ecadlib.kicad_menu", "what the ecadlib window in KiCad's PCB editor runs (add part, check BOM, fill, queue)"),
    "ai": ("ecadlib.llm.cli", "bring your own model: provider, model, key variable, capability profile, guide rails"),
    "testpack": ("ecadlib.testpack", "write a 6-part Altium + KiCad test pack with a CHECKLIST.md of what to open and what to see"),
    "migrate": ("ecadlib.migrate", "check or upgrade a workspace's files to this ecadlib's formats (backups first)"),
    "rebuild": ("ecadlib.rebuild", "rebuild a library from its library.lock.json offline; --check reports any drift"),
    "parity": ("ecadlib.parity", "read back the KiCad and the Altium library and compare every part (pads, drills, 3D, pins, fields)"),
    "stdparts": ("ecadlib.stdparts", "standard packages and pinouts without Claude: resolve, list, bench vs KiCad, BOM coverage"),
}


# Commands that were removed, and where to find the last version that had them.
REMOVED = {
    "eagle-export": "the Fusion Electronics / EAGLE export was removed after 0.3.1 (see CHANGELOG.md). "
                    "The last version with it is 0.3.1: pip install \"ecadlib==0.3.1\"",
    "easyeda-export": "the EasyEDA Pro export was removed after 0.3.1 (see CHANGELOG.md). "
                      "The last version with it is 0.3.1: pip install \"ecadlib==0.3.1\"",
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    from .cliutil import debug_wanted, env_defaults, report
    debug, argv = debug_wanted(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] in ("-V", "--version"):
        from . import __version__
        print(f"ecadlib {__version__}")
        return 0
    if argv[0] in REMOVED:
        print(f"ecadlib {argv[0]}: {REMOVED[argv[0]]}", file=sys.stderr)
        return 2
    cmd = COMMANDS.get(argv[0])
    if not cmd:
        print(f"ecadlib: unknown command {argv[0]!r}. Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    from . import term, userconfig
    userconfig.apply_env()                                   # kicad-cli on PATH; keys are read from the store, never exported
    rest = term.set_from_argv(argv[1:])                      # -q / -v for every command; --json: never styled
    args, notes = env_defaults(argv[0], rest)                # ECADLIB_LIB / ECADLIB_WORKSPACE when the flag is not given
    from . import firstrun as FR
    args, more = FR.config_defaults(argv[0], args)          # then ecadlib.toml [setup] libs / workspace
    notes += more
    bad = FR.preflight(argv[0], args)                        # a --lib that does not exist, an unwritable -o
    if bad:
        print(f"ecadlib {argv[0]}: {bad[0]}\n  hint: {bad[1]}", file=sys.stderr)
        return 2
    if debug and notes:
        print(f"ecadlib {argv[0]}: " + ", ".join(notes), file=sys.stderr)
    sys.argv = [f"ecadlib {argv[0]}"] + args                 # so argparse usage lines show the right command
    mod, _, fn = cmd[0].partition(":")
    from .rules import RulesError
    try:
        from .clihelp import installed                       # descriptions + examples in every -h
        with installed(argv[0], cmd[1]):
            return getattr(importlib.import_module(mod), fn or "main")(args)
    except RulesError as e:                                  # a bad ecadlib.toml: say which key, no traceback
        if debug:
            raise
        print(f"ecadlib {argv[0]}: {e}\n  hint: `ecadlib doctor` shows the file and line", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if debug:
            raise
        print(f"ecadlib {argv[0]}: interrupted", file=sys.stderr)
        return 130
    except Exception as e:                                   # one line + hint; --debug / ECADLIB_DEBUG=1: the traceback
        if debug:
            raise
        return report(argv[0], e, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
