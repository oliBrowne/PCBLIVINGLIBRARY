"""First run for a new user: `ecadlib demo`, the `ecadlib init` setup questions, [setup] defaults, the checks the CLI
runs before a command, the librarian's blocker notes, and the help of every command (descriptions, examples that parse,
the same flag spellings everywhere). Offline; no API keys."""
import argparse, contextlib, io, json, os, shlex, socket, tempfile, time
from pathlib import Path

from ecadlib import cli, clihelp, firstrun as FR, rules as RU, rules_infer as RI
from ecadlib.completion import _capture

ROOT = Path(__file__).resolve().parents[1]


def _run(argv, cwd=None, env=None):
    """cli.main(argv) in cwd -> (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    try:
        if cwd:
            os.chdir(cwd)
        for k in ("ECADLIB_LIB", "ECADLIB_WORKSPACE", "ECADLIB_CONFIG", "ECADLIB_DEBUG"):
            os.environ.pop(k, None)
        os.environ.update(env or {})
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = e.code
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    return rc, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def _no_network():
    def refuse(*a, **k):
        raise AssertionError("the demo tried to open a network connection")
    orig = socket.socket.connect, socket.create_connection
    socket.socket.connect, socket.create_connection = refuse, refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection = orig


# ------------------------------------------------------------------ ecadlib demo
def test_demo_builds_offline_and_reruns_without_churn():
    with tempfile.TemporaryDirectory() as d:
        t0 = time.time()
        with _no_network():
            rc, out, err = _run(["demo", "-o", "demo", "--json"], cwd=d)
        assert rc == 0, err
        assert time.time() - t0 < 60
        res = json.loads(out)
        assert (res["parts"], res["ready"], res["held"], res["held_parts"]) == (7, 6, 1, ["CH340C"])
        ws = Path(d) / "demo"
        assert len(res["altium"]) >= 10 and all((ws / f).read_bytes()[:8] == bytes.fromhex("d0cf11e0a1b11ae1") for f in res["altium"])
        kd = ws / res["kicad"]["folder"]
        for f in ("ecadlib_demo.kicad_sym", "fp-lib-table", "sym-lib-table", "ecadlib_demo.kicad_dbl", "ecadlib_demo.sqlite"):
            assert (kd / f).is_file(), f
        assert len(list((kd / "ecadlib_demo.pretty").glob("*.kicad_mod"))) == 5
        plan = (ws / res["plan"]).read_bytes()
        with _no_network():
            rc, out2, _ = _run(["demo", "-o", "demo"], cwd=d)           # again: same plan, nothing new
        assert rc == 0 and "6 ready, 1 held" in out2 and "next:" in out2 and "ecadlib ui --workspace demo" in out2
        assert (ws / res["plan"]).read_bytes() == plan
        assert len(list((ws / "uploads").rglob("*.csv"))) == 1
        rc, out3, _ = _run(["status", "--workspace", "demo"], cwd=d)
        assert rc == 0 and "6 ready, 1 held" in out3


def test_demo_refuses_a_workspace_with_its_own_rules():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "ws").mkdir()
        (Path(d) / "ws" / "ecadlib.toml").write_text('[sources]\norder = ["kicad"]\n')
        rc, _, err = _run(["demo", "-o", "ws"], cwd=d)
        assert rc == 2 and "has its own ecadlib.toml" in err
        assert (Path(d) / "ws" / "ecadlib.toml").read_text() == '[sources]\norder = ["kicad"]\n'


# ------------------------------------------------------------------ ecadlib init setup
def test_init_yes_writes_setup_runs_doctor_and_prints_next_steps():
    with tempfile.TemporaryDirectory() as d:
        lib = ROOT / "examples" / "kicad_lib"
        rc, out, err = _run(["init", "--yes", "--tool", "kicad", "--lib", str(lib), "--workspace", "ws"], cwd=d)
        assert rc == 0, err
        raw = RU.parse_text((Path(d) / "ecadlib.toml").read_text())
        assert raw["setup"] == {"tool": "kicad", "libs": [str(lib)], "workspace": "ws"}
        assert "sources" not in raw                        # defaults kept: nothing written for them
        assert (Path(d) / "ws").is_dir()
        assert "Checking the setup (ecadlib doctor)" in out and "failure(s)" in out
        assert "API keys (names only" in out
        nxt = out[out.index("Next:"):]
        assert "ecadlib demo" in nxt and "ecadlib librarian YOUR_BOM.csv -o ws/librarian/YOUR_BOM" in nxt and "ecadlib kicad-export" in nxt
        assert "ecadlib ui" not in nxt                     # kicad only: no Altium hand-off step
        assert "naming.component" in out                   # the conventions were learned from the --lib folder
        rc, out2, _ = _run(["init", "--yes"], cwd=d)       # again: the answers are kept, nothing changes
        assert rc == 0 and "already says this" in out2 and "Next:" in out2


def test_init_after_demo_does_not_learn_from_ecadlibs_own_output():
    # the Quick start runs `demo` then `init` in one folder; init used to read the demo's review.csv as a BOM and
    # write [bom.columns] designator = "part", footprint = "footprint_from" (every real BOM then lost its columns)
    with tempfile.TemporaryDirectory() as d:
        assert _run(["demo"], cwd=d)[0] == 0
        lib = Path(d) / "out" / "librarian"
        assert _run(["librarian", str(Path(d) / "ecadlib-demo" / "uploads" / "demo" / "demo_bom.csv"), "-o", str(lib),
                     "--offline", "--sources", "standard", "--cache", str(Path(d) / "c")], cwd=d)[0] == 2
        rc, out, err = _run(["init", "--yes", "--no-doctor"], cwd=d)
        assert rc == 0, err
        raw = RU.parse_text((Path(d) / "ecadlib.toml").read_text())
        assert "bom" not in raw and "naming" not in raw, raw
        assert "skipped ecadlib-demo: ecadlib's own output" in out and "skipped out/librarian" in out


def test_init_questions_validate_answers_and_set_sources():
    with tempfile.TemporaryDirectory() as d:
        answers = iter(["kicadd", "altium", "libs1, /nope", "myws", "n", ""])
        seen = []

        def inp(q):
            seen.append(q)
            return next(answers)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = RI.main(["--no-doctor"], inp=inp, environ={})
        finally:
            os.chdir(cwd)
        assert rc == 0
        text = out.getvalue()
        assert "please type altium, kicad or both" in text and "libs1 does not exist" in text
        assert any("ANTHROPIC_API_KEY is NOT set" in q for q in seen)      # names only, and its state
        raw = RU.parse_text((Path(d) / "ecadlib.toml").read_text())
        assert raw["setup"] == {"tool": "altium", "libs": ["libs1", "/nope"], "workspace": "myws"}
        assert raw["sources"]["order"] == ["standard"]                     # no online lookups, no datasheet reader
        assert "ecadlib ui" in text and "kicad-export" not in text


def test_init_rejects_an_unknown_source():
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run(["init", "--yes", "--sources", "kicad,lcsc"], cwd=d)
        assert rc == 2 and "unknown source 'lcsc'" in err and not (Path(d) / "ecadlib.toml").exists()


def test_setup_section_is_validated():
    RU.validate({"setup": {"tool": "both", "libs": [], "workspace": ""}})
    try:
        RU.validate({"setup": {"tool": "eagle"}})
    except RU.RulesError as e:
        assert "setup.tool" in str(e)
    else:
        raise AssertionError("setup.tool = eagle accepted")


# ------------------------------------------------------------------ [setup] defaults and preflight
def test_setup_libs_and_workspace_become_defaults():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "libs").mkdir()
        (Path(d) / "ecadlib.toml").write_text('[setup]\ntool = "kicad"\nlibs = ["libs"]\nworkspace = "ws"\n')
        sub = Path(d) / "boards"
        sub.mkdir()
        args, notes = FR.config_defaults("ui", [], environ={}, start=sub)
        pairs = dict(zip(args[::2], args[1::2]))
        assert pairs == {"--lib": str(Path(d).resolve() / "libs"), "--workspace": str(Path(d).resolve() / "ws")} and len(notes) == 2
        args, _ = FR.config_defaults("ui", ["--lib", "x"], environ={"ECADLIB_WORKSPACE": "w"}, start=sub)
        assert args == ["--lib", "x"]                     # the command line and the environment win
        assert FR.config_defaults("agent", ["status"], environ={}, start=sub)[0] == ["status"]
        assert FR.config_defaults("agent", ["run"], environ={}, start=sub)[0][:2] == ["run", "--lib"]
        assert FR.config_defaults("triage", ["-h"], environ={}, start=sub)[0] == ["-h"]
        rc, out, _ = _run(["doctor"], cwd=sub)            # the doctor sees the [setup] folders
        assert str(Path(d).resolve() / "libs") in out


def test_missing_lib_folder_is_one_line_with_a_fix():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "b.csv"
        bom.write_text("Designator,Qty,Value,Footprint\nR1,1,10k,R_0603_1608Metric\n")
        rc, out, err = _run(["librarian", str(bom), "--lib", str(Path(d) / "nope"), "-o", str(Path(d) / "o"), "--offline"])
        assert rc == 2 and "library folder not found" in err and "hint:" in err and "Traceback" not in err
        assert not (Path(d) / "o").exists()                  # nothing was written
        rc, _, err = _run(["triage", str(bom), "--offline", "-o", str(Path(d) / "t")], env={"ECADLIB_LIB": str(Path(d) / "gone")})
        assert rc == 2 and "gone" in err


def test_unwritable_output_is_one_line_with_a_fix():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "b.csv"
        bom.write_text("Designator,Qty,Value,Footprint\nR1,1,10k,R_0603_1608Metric\n")
        rc, _, err = _run(["librarian", str(bom), "-o", str(bom / "out"), "--offline"])
        assert rc == 2 and "is a file, not a folder" in err and "pick another -o" in err
        assert FR.writable_problem(Path(d) / "new" / "deeper") is None
        assert FR.writable_problem(bom) is None               # an existing file that will be replaced
        if os.name == "posix" and Path("/proc/self").exists():
            assert FR.writable_problem("/proc/ecadlib-test/x")


def test_bom_without_columns_or_rows_gives_a_fix():
    with tempfile.TemporaryDirectory() as d:
        nocols, hdr = Path(d) / "a.csv", Path(d) / "b.csv"
        nocols.write_text("foo,bar\n1,2\n")
        hdr.write_text("Designator,Qty,MPN\n")
        rc, _, err = _run(["librarian", str(nocols), "-o", str(Path(d) / "o"), "--offline"])
        assert rc == 2 and "[bom.columns]" in err and "hint:" in err
        rc, out, err = _run(["librarian", str(hdr), "-o", str(Path(d) / "o"), "--offline"])
        assert rc == 2 and "header but no part rows" in err    # was: "0 parts: 0 ready" and exit 0
        rc, out, err = _run(["triage", str(hdr), "-o", str(Path(d) / "t"), "--offline"])
        assert rc == 2 and "header but no part rows" in err


def test_librarian_notes_unreachable_sources_and_missing_key():
    rows = [{"part": "A", "tried": "kicad: library index unavailable (kicad-symbols tree page 3: HTTP 429); easyeda C1: "
                                   "ProxyError: proxy CONNECT refused (403 Forbidden); standard: footprint: no standard package named; "
                                   "needs datasheet reader (no API key): https://x/y.pdf"},
            {"part": "B", "tried": "kicad: library index unavailable (x: HTTP 429); easyeda: no LCSC number; standard: footprint X"},
            {"part": "C", "tried": "standard: footprint R_0603"}]
    got = FR.blockers(rows, {"datasheet": {"api_key_env": "MY_KEY"}})
    text = "\n".join(m + " | " + f for m, f in got)
    assert "easyeda could not be reached (or refused) for 1 part(s)" in text and "kicad could not be reached (or refused) for 2" in text
    assert "1 part(s) need the datasheet reader, but MY_KEY is not set" in text and "export MY_KEY=" in text
    assert "--offline --sources standard" in text
    assert FR.blockers(rows[2:]) == []
    assert "--offline" in FR.blockers(rows[:1], offline=True)[0][0]


def test_librarian_json_and_progress():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "b.csv"
        bom.write_text("Designator,Qty,Value,Footprint,Manufacturer,MPN,LCSC,Description\n"
                       "R1,1,10k,R_0603_1608Metric,UNI-ROYAL,0603WAF1002T5E,C25804,Resistor 10k 0603\n")
        rc, out, _ = _run(["librarian", str(bom), "-o", str(Path(d) / "o"), "--offline", "--sources", "standard",
                           "--cache", str(Path(d) / "c"), "--json"])
        res = json.loads(out)
        assert rc == 0 and res["ready"] == 1 and res["plan"].endswith("plan.json")
        rc, out, _ = _run(["librarian", str(bom), "-o", str(Path(d) / "o2"), "--offline", "--sources", "standard",
                           "--cache", str(Path(d) / "c")])
        assert "BOM: 1 lines" in out and "finding symbols and footprints for 1 part(s)" in out
        assert out.strip().splitlines()[-1].startswith("1 parts: 1 ready, 0 held ->")


# ------------------------------------------------------------------ the CLI tree: descriptions, examples, flag names
def _tree():
    for name, (target, summary) in cli.COMMANDS.items():
        p = _capture(target)
        yield name, summary, (clihelp.polish(p, name, summary) if p is not None else None)


def _walk(path, p):
    yield path, p
    for act in p._actions:
        if isinstance(act, argparse._SubParsersAction):
            for n, sub in act.choices.items():
                yield from _walk(path + " " + n, sub)


def test_every_command_has_a_description_and_examples():
    missing = []
    for name, summary, p in _tree():
        assert summary.strip(), name
        if p is None:
            continue
        assert name in clihelp.EXAMPLES, f"no examples for {name}"
        assert "\nexamples:\n" in p.format_help(), name
        for path, sub in _walk(name, p):
            if not (sub.description or "").strip():
                missing.append(path)
    assert not missing, missing
    assert set(clihelp.EXAMPLES) <= set(cli.COMMANDS)


class _Parsed(Exception):
    pass


def _parse_like_cli(target, args):
    """Run the command's main(args) until its first parse_args, then parse `args` with that parser (so commands that
    dispatch by hand, such as `datasheet estimate` or `team setup`, are checked too). Returns an error text or None."""
    import importlib
    mod, _, fn = target.partition(":")
    orig = argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args
    got = {}

    def stop(self, a=None, *x, **k):
        got["p"], got["a"] = self, a
        raise _Parsed()
    argparse.ArgumentParser.parse_args = argparse.ArgumentParser.parse_known_args = stop
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            getattr(importlib.import_module(mod), fn or "main")(list(args))
    except _Parsed:
        pass
    finally:
        argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args = orig
    if "p" not in got:
        return "main() returned before parsing its arguments"
    try:
        with contextlib.redirect_stderr(io.StringIO()) as e:
            got["p"].parse_args(got["a"])
    except SystemExit:
        return e.getvalue().strip().splitlines()[-1]
    return None


def test_every_example_parses():
    bad = []
    for name, (target, _) in cli.COMMANDS.items():
        for ex in clihelp.EXAMPLES.get(name, []):
            line = ex.split("  #")[0].strip()
            if not line.startswith("ecadlib ") or ">" in line or "<(" in line:
                continue
            words = shlex.split(line)
            assert words[1] == name, ex
            why = _parse_like_cli(target, words[2:])
            if why:
                bad.append(f"{ex}: {why}")
    assert not bad, bad


def test_flag_spellings_are_the_same_everywhere():
    # one spelling per meaning; an old spelling may stay only next to the common one (an alias)
    odd = {"--output": "-o/--out", "--outdir": "-o/--out", "--out-dir": "-o/--out", "--dryrun": "--dry-run",
           "--libs": "--lib", "--library": "--lib", "--ws": "--workspace", "--no-net": "--offline", "--js": "--json"}
    found = []
    for name, _, p in _tree():
        if p is None:
            continue
        for path, sub in _walk(name, p):
            opts = {o for a in sub._actions for o in a.option_strings}
            for o in opts & set(odd):
                if not any(c in opts for c in odd[o].split("/")):
                    found.append(f"{path}: {o} (use {odd[o]})")
            for a in sub._actions:
                if "-o" in a.option_strings and "--out" not in a.option_strings:
                    found.append(f"{path}: -o without --out")
    assert not found, found


def test_help_shows_examples_through_the_cli():
    rc, out, _ = _run(["librarian", "-h"])
    assert rc == 0 and "examples:" in out and "--offline --sources standard" in out
    rc, out, _ = _run(["queue", "approve", "-h"])
    assert rc == 0 and "approve an item" in out          # subcommand description from its help text
    rc, out, _ = _run(["-h"])
    assert "ecadlib demo" in out
