"""CLI smoke test: every registered `ecadlib <command>` answers -h, and a minimal real (offline) invocation of each
exits with an expected code and writes nothing outside its output folder (inputs, the current folder and $HOME
stay as they were). Runs in-process in a temporary folder.  Run:  python3 -m tests.test_cli_smoke

Also usable from outside a checkout (the fresh-install check):  python -m tests.test_cli_smoke --subprocess
runs each command as `ecadlib ...` (the installed console script) instead of in-process."""
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

from ecadlib import cli

HERE = Path(__file__).resolve().parent
PKG = Path(cli.__file__).resolve().parent                 # the installed package (package data comes from here)

BOM = ('Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description\n'
       'U1,1,AMS1117-3.3,SOT-223-3_TabPin2,Advanced Monolithic Systems,AMS1117-3.3,C6186,LDO 3.3V 1A\n'
       '"R1,R2",2,10k,R_0603_1608Metric,UNI-ROYAL,0603WAF1002T5E,C25804,Resistor 10k 1% 0603\n'
       'C1,1,100nF,C_0402_1005Metric,Samsung,CL05B104KO5NNNC,C1525,Capacitor 100nF 0402\n'
       ',,,,,,,\n'
       'Total,4,,,,,,\n')
KICAD_SYM = """(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "AMS1117-3.3" (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 0 0)) (property "Value" "AMS1117-3.3" (at 0 0 0))
    (property "Footprint" "Package_TO_SOT_SMD:SOT-223-3_TabPin2" (at 0 0 0))
    (symbol "AMS1117-3.3_0_1" (rectangle (start -5 3) (end 5 -3) (stroke (width 0.25)) (fill (type background))))
    (symbol "AMS1117-3.3_1_1"
      (pin power_in line (at -7.62 0 0) (length 2.54) (name "VI" (effects (font (size 1 1)))) (number "3" (effects (font (size 1 1)))))
      (pin power_out line (at 7.62 0 180) (length 2.54) (name "VO" (effects (font (size 1 1)))) (number "2" (effects (font (size 1 1)))))
      (pin power_in line (at 0 -5.08 90) (length 2.54) (name "GND" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1))))))))
"""
PINS = "number,name,type\n1,VDD,power_in\n2,PA0,bidirectional\n3,PA1,bidirectional\n4,GND,power_in\n5,NRST,input\n6,SWDIO,bidirectional\n"

# (argv, allowed exit codes). {in} = the inputs folder, {out} = the output folder (the only place writes may go).
# Exit codes: 0 ok; 1 findings / incomplete; 2 held parts or a usage-level refusal; 3 no source answered (offline).
CASES = [
    (["rules", "check", "{in}/ecadlib.toml"], (0,)),
    (["rules", "show"], (0,)),
    (["init", "--from", "{in}/lib", "-o", "{out}/ecadlib.toml", "--yes"], (0,)),
    (["team", "--workspace", "{out}/teamws", "list"], (0,)),
    (["triage", "{in}/bom.csv", "--lib", "{in}/lib", "-o", "{out}/triage", "--offline", "--cache", "{out}/cache"], (0,)),
    (["footprint", "gen", "{pkg}/package_specs/R_0603_1608Metric.json", "-o", "{out}/fp.pretty"], (0,)),
    (["check", "{out}/fp.pretty/R_0603_1608Metric.kicad_mod"], (0, 1)),
    (["footprint-bench", "--offline", "--fixture", "{tests}/fixtures/kicad_bench.json", "--cache", "{out}/cache",
      "--json", "{out}/bench.json"], (0, 1)),
    (["dblib", "{in}/bom.csv", "-o", "{out}/dblib"], (0,)),
    (["altium-lib", "pcblib", "{pkg}/package_specs/R_0603_1608Metric.json", "-o", "{out}/al"], (0,)),
    (["altium-lib", "dump", "{out}/al/R_0603_1608Metric.PcbLib"], (0,)),
    (["altium365", "plan", "{in}/bom.csv", "-o", "{out}/a365plan.json"], (0,)),
    (["librarian", "{in}/bom.csv", "--lib", "{in}/lib", "-o", "{out}/lib", "--offline", "--cache", "{out}/cache",
      "--sources", "kicad"], (0, 2)),
    (["release-check", "{out}/lib/plan.json", "-o", "{out}/rc"], (0, 2)),
    (["kicad-export", "{out}/lib/plan.json", "-o", "{out}/kicad", "--offline", "--cache", "{out}/cache",
      "--include-held"], (0,)),
    (["dblib-sync", "{out}/lib/plan.json", "--to", "{out}/dbsync", "-o", "{out}/dbsync.json"], (0,)),
    (["resolve", "{out}/lib/plan.json", "--offline", "--cache", "{out}/cache", "list"], (0,)),
    (["approve", "{out}/lib/plan.json", "status"], (0,)),
    (["dedupe", "{out}/lib/plan.json", "-o", "{out}/dedupe"], (0,)),
    (["datasheet", "estimate", "{out}/lib/plan.json"], (0, 1, 2)),
    (["sourcing", "{in}/bom.csv", "--offline", "--cache", "{out}/cache", "-o", "{out}/src"], (0, 1, 3)),
    (["step-gen", "{pkg}/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json", "-o", "{out}/soic.step"], (0,)),
    (["step-gen", "--check", "{out}/soic.step"], (0,)),
    (["upkeep", "{in}/bom.csv", "--offline", "--cache", "{out}/cache", "-o", "{out}/upk", "--state", "{out}/upkstate",
      "--checks", "sourcing"], (0, 1)),
    (["upstream", "check-kicad", "{out}/missing_manifest.json", "--offline", "--cache", "{out}/cache",
      "-o", "{out}/upstream"], (0, 1, 2)),
    (["sim-models", "{out}/lib/plan.json", "--no-net", "--offline", "--cache", "{out}/cache"], (0, 1)),
    (["symbol", "{in}/pins.csv", "-o", "{out}/big.SchLib", "--preview", "{out}/big.svg"], (0,)),
    (["agent-watch", "--workspace", "{out}/ws", "--source", "bom_file:{in}/bom.csv", "--dry-run"], (0,)),
    (["agent", "--workspace", "{out}/ws", "status"], (0, 1)),
    (["queue", "--workspace", "{out}/ws", "list"], (0,)),
    (["where-used", "--projects", "{in}", "-o", "{out}/where"], (0, 1)),
    (["visual-check", "{out}/fp.pretty/R_0603_1608Metric.kicad_mod", "--render-only", "-o", "{out}/vc",
      "--workspace", "{out}/ws"], (0, 1, 2)),
    (["doctor", "--workspace", "{out}/ws", "--lib", "{in}/lib"], (0, 1)),
    (["status", "--workspace", "{out}/ws"], (0,)),
    (["completion", "bash"], (0,)),
    (["plugins", "list"], (0,)),
    (["plugins", "export", "nosuch", "{out}/lib/plan.json", "-o", "{out}/px"], (2,)),
    (["notify", "show", "--rules", "{in}/ecadlib.toml"], (0,)),
    (["kicad-dblib", "{out}/lib/plan.json", "-o", "{out}/kicad", "--name", "ecadlib"], (0,)),
    (["export-parts", "{out}/lib/plan.json", "--format", "xlsx", "-o", "{out}/parts.xlsx"], (0,)),
]
HELP_ONLY = {"ui", "mcp", "a365", "project"}       # need a server / network / an Altium project: -h (and mcp below)


def _snapshot(root, skip):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and skip not in p.parents:
            out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def _workspace(d):
    i = d / "in"
    (i / "lib").mkdir(parents=True)
    (i / "bom.csv").write_text(BOM)
    (i / "pins.csv").write_text(PINS)
    (i / "lib" / "demo.kicad_sym").write_text(KICAD_SYM)
    (i / "ecadlib.toml").write_text('[footprint]\ndensity = "N"\n')
    (d / "home").mkdir()
    return i, d / "out"


def _run(argv, sub):
    if sub:
        r = subprocess.run(["ecadlib", *argv], capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr
    buf = io.StringIO()
    old_argv = sys.argv
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old_argv
    return (rc or 0), buf.getvalue()


def smoke(sub=False, verbose=False):
    """Returns a list of problems (empty = all good)."""
    problems = []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td).resolve()
        inp, out = _workspace(d)
        out.mkdir()
        old_cwd, old_env = os.getcwd(), dict(os.environ)
        os.chdir(d)
        os.environ.update(HOME=str(d / "home"), XDG_CACHE_HOME=str(d / "home" / ".cache"), ECADLIB_OFFLINE="1")
        for k in ("ANTHROPIC_API_KEY", "NEXAR_TOKEN", "NEXAR_CLIENT_ID", "DIGIKEY_CLIENT_ID", "MOUSER_API_KEY", "GITHUB_TOKEN"):
            os.environ.pop(k, None)
        try:
            for name in cli.COMMANDS:
                rc, text = _run([name, "-h"], sub)
                if rc != 0 or "usage" not in text.lower():
                    problems.append(f"{name} -h: exit {rc}: {text[-300:]}")
            before = _snapshot(d, out)
            fmt = dict(out=str(out), pkg=str(PKG), tests=str(HERE))
            fmt["in"] = str(inp)
            covered = set(HELP_ONLY)
            for argv, ok in CASES:
                argv = [a.format(**fmt) for a in argv]
                covered.add(argv[0])
                t0 = time.time()
                rc, text = _run(argv, sub)
                if verbose:
                    print(f"  {rc} {time.time() - t0:5.1f}s ecadlib {' '.join(argv)}")
                if rc not in ok or "Traceback (most recent call last)" in text:
                    problems.append(f"ecadlib {' '.join(argv)}: exit {rc} (want {ok}):\n{text[-1500:]}")
                after = _snapshot(d, out)
                if after != before:
                    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
                    problems.append(f"ecadlib {' '.join(argv)} wrote outside its output folder: {changed[:8]}")
                    before = after
            missing = set(cli.COMMANDS) - covered
            if missing:
                problems.append(f"commands without a smoke invocation: {sorted(missing)}")
            if not sub:                                      # mcp: initialize + tools/list over stdio
                from ecadlib import mcp_server as M
                from ecadlib.app import Workspace
                req = b"".join(json.dumps(m).encode() + b"\n" for m in (
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
                o = io.BytesIO()
                M.serve(Workspace(out / "mcpws", [str(inp / "lib")], offline=True), io.BytesIO(req), o)
                answers = [json.loads(x) for x in o.getvalue().splitlines() if x.strip()]
                if len(answers) != 2 or not answers[1]["result"]["tools"]:
                    problems.append(f"mcp: unexpected answers {answers!r:.300}")
                if _snapshot(d, out) != before:
                    problems.append("mcp wrote outside its workspace")
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
    return problems


def test_every_command_help_and_minimal_run():
    problems = smoke()
    assert not problems, "\n\n".join(problems)


OPTIONAL = {"PIL": "pillow", "pypdf": "pypdf"}           # extras: every use handles ImportError with a message


def test_third_party_imports_are_declared():
    """A fresh `pip install ecadlib` gets only the declared dependencies: olefile was an extra, so the release check
    held every part ('symbol file does not read back: No module named olefile') and kicad-export wrote nothing."""
    import ast, re
    pyproject = (HERE.parent / "pyproject.toml").read_text()
    deps = re.search(r"^dependencies = \[(.*?)\]", pyproject, re.M | re.S).group(1)
    declared = {re.split(r"[<>=!~ \[]", d.strip().strip('"'))[0].lower() for d in deps.split(",") if d.strip()}
    used = {}
    for f in PKG.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else [])
            for n in names:
                top = n.split(".")[0]
                if top != "ecadlib" and top not in sys.stdlib_module_names:
                    used.setdefault(top, f"{f.name}:{node.lineno}")
    undeclared = {m: where for m, where in used.items() if m.lower() not in declared and m not in OPTIONAL}
    assert not undeclared, f"imported but not in [project] dependencies: {undeclared}"
    assert "olefile" in declared


if __name__ == "__main__":
    probs = smoke(sub="--subprocess" in sys.argv, verbose=True)
    print("\n\n".join(probs) or "all commands ok")
    sys.exit(1 if probs else 0)
