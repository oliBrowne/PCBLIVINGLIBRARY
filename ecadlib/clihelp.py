"""`ecadlib <command> -h` polish, applied by ecadlib/cli.py to whatever parser the command builds:

- every command and subcommand gets a one-line description (a subcommand without one takes its `help=` text, a
  command without one takes its line in cli.COMMANDS);
- every command's help ends with a few examples (EXAMPLES below), so a new user can copy a working line.

Done from the outside (a wrapper around ArgumentParser.parse_known_args while the command runs), so the commands'
own modules stay as they are and `python -m ecadlib.<module>` still works. tests/test_firstrun.py walks the whole
argparse tree and checks every command has a description, examples and the common flag spellings."""
import argparse, types

EXAMPLES = {
    "init": ["ecadlib init                                   # questions: tool, library folders, workspace, sources",
             "ecadlib init --yes --tool kicad --lib ~/kicad/libs --workspace ws",
             "ecadlib init --from ~/altium/libs --yes         # also learn naming / parameters from existing libraries"],
    "demo": ["ecadlib demo", "ecadlib demo -o /tmp/try --tool kicad"],
    "kicad-pcm": ["ecadlib kicad-pcm out/librarian/plan.json -o out/pcm --name mylib",
                  "ecadlib kicad-pcm out/kicad -o out/pcm --kicad 7", "ecadlib kicad-pcm --plugin -o out/pcm"],
    "altium-menu": ["ecadlib altium-menu build --out out/altium_menu --workspace D:/ecadlib-ws --win-dir D:\\ecadlib-menu",
                    "ecadlib altium-menu add-part LM358DR --manufacturer TI --workspace D:/ecadlib-ws --result-file r.ini"],
    "altium-pkg": ["ecadlib altium-pkg out/librarian/plan.json --out out/altium_pkg --name mylib",
                   "ecadlib altium-pkg out/altium_models --out out/altium_pkg"],
    "testpack": ["ecadlib testpack --out altium_kicad_testpack"],
    "ai": ["ecadlib ai show", "ecadlib ai show --model qwen2.5:7b --detect", "ecadlib ai rails"],
    "rules": ["ecadlib rules check", "ecadlib rules check ecadlib.toml", "ecadlib rules show"],
    "ui": ["ecadlib ui", "ecadlib ui --lib ~/kicad/libs --workspace ws", "ecadlib ui --team --host 0.0.0.0"],
    "team": ["ecadlib team add-user alice --role librarian", "ecadlib team list", "ecadlib team setup --domain lib.example.com"],
    "mcp": ["claude mcp add ecadlib -- ecadlib mcp --lib ~/kicad/libs --workspace ws"],
    "triage": ["ecadlib triage my_bom.csv -o out/triage", "ecadlib triage my_bom.csv --lib ~/kicad/libs --offline --json"],
    "footprint": ["ecadlib footprint gen ecadlib/package_specs/soic8.json -o out/fp",
                  "ecadlib footprint compare out/fp/SOIC-8.kicad_mod ref/SOIC-8.kicad_mod"],
    "check": ["ecadlib check out/fp/*.kicad_mod"],
    "footprint-bench": ["ecadlib footprint-bench --offline", "ecadlib footprint-bench --json bench.json"],
    "eval": ["ecadlib eval sets", "ecadlib eval run --set kicad-v1 --limit 5 --offline", "ecadlib eval compare runA.json runB.json",
             "ecadlib eval gate", "ecadlib eval golden --update"],
    "dblib": ["ecadlib dblib my_bom.csv -o out/altium"],
    "altium-lib": ["ecadlib altium-lib pcblib out/fp/SOIC-8.kicad_mod -o out/altium", "ecadlib altium-lib dump x.PcbLib"],
    "altium365": ["ecadlib altium365 plan my_bom.csv -o out/a365", "ecadlib altium365 push out/a365/plan.json --workspace https://acme.365.altium.com --dry-run"],
    "dblib-sync": ["ecadlib dblib-sync out/library/plan.json --to //server/libs/Components --dry-run"],
    "librarian": ["ecadlib librarian my_bom.csv -o ws/library",
                  "ecadlib librarian my_bom.csv --lib ~/kicad/libs -o ws/library --json",
                  "ecadlib librarian my_bom.csv --offline --sources standard   # no network, no API key"],
    "datasheet": ["ecadlib datasheet TPS62133.pdf --part TPS62133RGTR -o out/ds",
                  "ecadlib datasheet estimate ws/library/plan.json"],
    "release-check": ["ecadlib release-check ws/library/plan.json", "ecadlib release-check ws/library/plan.json --allow CH340C"],
    "kicad-export": ["ecadlib kicad-export ws/library/plan.json -o ws/kicad --name mylib"],
    "review": ["ecadlib review                                 # every held part in the workspace, one at a time",
               "ecadlib review ws/library/plan.json --part CH340C", "ecadlib review --workspace ws --yes   # print only, never ask"],
    "resolve": ["ecadlib resolve ws/library/plan.json list", "ecadlib resolve ws/library/plan.json --part CH340C show"],
    "upstream": ["ecadlib upstream check-kicad --offline"],
    "a365": ["ecadlib a365 status ws/library/plan.json --workspace https://acme.365.altium.com"],
    "queue": ["ecadlib queue list", "ecadlib queue approve 12 --user alice"],
    "approve": ["ecadlib approve ws/library/plan.json status", "ecadlib approve ws/library/plan.json approve --part CH340C --user alice"],
    "hub": ["ecadlib hub --workspace ws --lib ~/altium/libs", "ecadlib hub --workspace ws --lack kicad --json",
            "ecadlib hub --workspace ws --part AO3401A", "ecadlib hub --workspace ws --tool kicad",
            "ecadlib hub --workspace ws --project boards/sensor/sensor.kicad_pro --copy-to kicad"],
    "dedupe": ["ecadlib dedupe ws/library/plan.json", "ecadlib dedupe ws/library/plan.json --merge"],
    "sourcing": ["ecadlib sourcing my_bom.csv --qty 100 --alternates"],
    "step-gen": ["ecadlib step-gen SOIC-8_3.9x4.9mm_P1.27mm -o out/soic8.step", "ecadlib step-gen --check out/soic8.step"],
    "upkeep": ["ecadlib upkeep ws/library/plan.json", "ecadlib upkeep ws/library/plan.json --github issue"],
    "project": ["ecadlib project board.PrjPcb -o bom.csv", "ecadlib project board.PrjPcb --json"],
    "where-used": ["ecadlib where-used --projects ~/projects --part CH340C"],
    "sim-models": ["ecadlib sim-models ws/library/plan.json --no-net", "ecadlib sim-models ws/library/plan.json --attach"],
    "agent-watch": ["ecadlib agent-watch --dry-run", "ecadlib agent-watch --source bom_file:boards/main_bom.csv --json"],
    "agent": ["ecadlib agent status", "ecadlib agent run --dry-run", "ecadlib agent approve 3 --user alice"],
    "visual-check": ["ecadlib visual-check out/fp/SOIC-8.kicad_mod --datasheet ds.pdf --pages 3 --render-only"],
    "symbol": ["ecadlib symbol pins.csv -o out/MCU.SchLib --preview out/MCU.svg"],
    "doctor": ["ecadlib doctor", "ecadlib doctor --workspace ws --lib ~/kicad/libs --json"],
    "status": ["ecadlib status", "ecadlib status --workspace ws --json"],
    "completion": ["ecadlib completion bash > ~/.local/share/bash-completion/completions/ecadlib",
                   "source <(ecadlib completion zsh)"],
    "plugins": ["ecadlib plugins list", "ecadlib plugins export myexporter ws/library/plan.json -o out/x"],
    "notify": ["ecadlib notify show", "ecadlib notify test --event batch_ready"],
    "kicad-dblib": ["ecadlib kicad-dblib ws/library/plan.json -o ws/kicad --name mylib --export"],
    "export-parts": ["ecadlib export-parts ws/library/plan.json --format xlsx -o parts.xlsx"],
    "parity": ["ecadlib parity ws/library/plan.json", "ecadlib parity ws/library/plan.json --kicad ws/kicad --altium ws/altium",
               "ecadlib parity survey --quick"],
    "stdparts": ["ecadlib stdparts resolve \"SOIC-8\"", "ecadlib stdparts coverage my_bom.csv"],
    "migrate": ["ecadlib migrate ws --check             # exit 1 when files need migrating or are from a newer ecadlib",
                "ecadlib migrate ws --dry-run", "ecadlib migrate ws                     # backups in .ecadlib_backup/"],
    "rebuild": ["ecadlib rebuild ws/library --check     # offline; exit 1 on drift",
                "ecadlib rebuild ws/library --check --keep /tmp/rb --json", "ecadlib rebuild ws/library"],
}


def _fill(parser, fallback=""):
    if getattr(parser, "_ecadlib_polished", False):
        return
    parser._ecadlib_polished = True
    if not (parser.description or "").strip() and fallback:
        parser.description = fallback
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            helps = {ca.dest: ca.help for ca in act._choices_actions}
            for name, sub in act.choices.items():
                _fill(sub, helps.get(name) or "")


def polish(parser, cmd, summary=""):
    """Descriptions everywhere under `parser`, and the examples for `cmd` at the end of its help."""
    _fill(parser, summary)
    ex = EXAMPLES.get(cmd)
    if ex and not getattr(parser, "_ecadlib_examples", False):
        parser._ecadlib_examples = True
        orig = parser.format_help
        text = "\nexamples:\n" + "".join(f"  {e}\n" for e in ex)

        def format_help(self):
            h = orig()
            return h if "\nexamples:" in h else h.rstrip("\n") + "\n" + text
        parser.format_help = types.MethodType(format_help, parser)
    return parser


class installed:
    """with installed(cmd, summary): the first parser the command parses with gets polish(); restored on exit."""
    def __init__(self, cmd, summary=""):
        self.cmd, self.summary, self.done = cmd, summary, False

    def __enter__(self):
        self.orig = argparse.ArgumentParser.parse_known_args
        me = self

        def parse_known_args(parser, *a, **k):
            if not me.done:
                me.done = True
                polish(parser, me.cmd, me.summary)
            return me.orig(parser, *a, **k)
        argparse.ArgumentParser.parse_known_args = parse_known_args
        return self

    def __exit__(self, *exc):
        argparse.ArgumentParser.parse_known_args = self.orig
        return False
