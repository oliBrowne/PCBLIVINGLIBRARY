"""Offline tests for the command-line quality-of-life features: `ecadlib doctor`, friendly errors (--debug /
ECADLIB_DEBUG), ECADLIB_LIB / ECADLIB_WORKSPACE / ECADLIB_CONFIG defaults, --json for triage / release-check /
approve status, `ecadlib status` and `ecadlib completion`.  Run:  python3 -m tests.test_qol"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import cli, cliutil as CU, completion as CO, doctor as DR, rules as RU, ws_status as WS
from tests.test_cli_smoke import BOM, KICAD_SYM
from tests.test_release_check import make


@contextlib.contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _cwd(d):
    old = os.getcwd()
    os.chdir(d)
    try:
        yield
    finally:
        os.chdir(old)


def _run(argv):
    """(exit code, stdout, stderr) of cli.main(argv)."""
    o, e, old = io.StringIO(), io.StringIO(), sys.argv
    try:
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            try:
                rc = cli.main(argv)
            except SystemExit as x:
                rc = x.code if isinstance(x.code, int) else 1
    finally:
        sys.argv = old
    return rc or 0, o.getvalue(), e.getvalue()


CLEAN = dict(ECADLIB_LIB=None, ECADLIB_WORKSPACE=None, ECADLIB_CONFIG=None, ECADLIB_DEBUG=None)


# ------------------------------------------------------------------ environment defaults
def test_env_defaults_fill_missing_flags_only():
    env = {"ECADLIB_LIB": os.pathsep.join(["/a", "/b"]), "ECADLIB_WORKSPACE": "/ws"}
    a, notes = CU.env_defaults("triage", ["bom.csv"], env)
    assert a == ["--lib", "/a", "--lib", "/b", "bom.csv"] and notes == ["--lib from $ECADLIB_LIB"]
    assert CU.env_defaults("triage", ["bom.csv", "--lib", "/mine"], env)[0] == ["bom.csv", "--lib", "/mine"]   # flag wins
    assert CU.env_defaults("triage", ["bom.csv", "--lib=/mine"], env)[0] == ["bom.csv", "--lib=/mine"]
    assert CU.env_defaults("queue", ["list"], env)[0] == ["--workspace", "/ws", "list"]      # before the subcommand
    assert CU.env_defaults("agent", ["run", "--dry-run"], env)[0] == ["run", "--dry-run", "--lib", "/a", "--lib", "/b"]
    assert CU.env_defaults("agent", ["status"], env)[0] == ["status"]                      # status takes no --lib
    assert CU.env_defaults("a365", ["status", "plan.json"], env)[0] == ["status", "plan.json"]   # --workspace is a URL there
    assert CU.env_defaults("triage", ["-h"], env)[0] == ["-h"]
    assert CU.env_defaults("triage", ["x"], {})[0] == ["x"]


def test_env_flags_exist_in_each_commands_parser():
    """Every (command, flag) pair in ENV_FLAGS is a real option of that command (or of the named subcommands), so an
    environment default can never turn into 'unrecognized arguments'."""
    for var, (flag, cmds) in CU.ENV_FLAGS.items():
        for cmd, subs in cmds.items():
            d = CO.describe(CO._capture(cli.COMMANDS[cmd][0]))
            if subs is None:
                assert flag in d["options"], (var, cmd)
            else:
                for s in subs:
                    assert flag in d["subcommands"][s]["options"], (var, cmd, s)


def test_ecadlib_lib_reaches_triage_and_json_shape():
    """ECADLIB_LIB stands in for --lib: the AMS1117 line is found in the local library only through it."""
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN), _cwd(d):
        lib = Path(d, "lib")
        lib.mkdir()
        (lib / "demo.kicad_sym").write_text(KICAD_SYM)
        Path(d, "bom.csv").write_text(BOM)
        args = ["triage", "bom.csv", "-o", "out/t", "--offline", "--cache", "out/cache", "--json"]
        rc, out, _ = _run(args)
        rows = {r["mpn"]: r for r in json.loads(out)["rows"]}
        assert rc == 0 and rows["AMS1117-3.3"]["local_symbol"] == ""          # no library: not found locally
        os.environ["ECADLIB_LIB"] = str(lib)
        rc, out, _ = _run(args)
        res = json.loads(out)                                                  # stdout is pure JSON
        assert rc == 0 and set(res) == {"bom", "lines", "mapping", "counts", "rows", "reports"}
        assert res["lines"] == len(res["rows"]) == 3 and sum(res["counts"].values()) == 3
        assert {r["mpn"]: r for r in res["rows"]}["AMS1117-3.3"]["local_symbol"].startswith("demo:AMS1117-3.3")
        assert res["reports"] == ["out/t.md", "out/t.csv"] and Path(d, "out", "t.csv").is_file()


def test_ecadlib_config_env_and_flag_wins():
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN), _cwd(d):
        Path(d, "sub").mkdir()
        Path(d, "ecadlib.toml").write_text('[footprint]\ndensity = "M"\n')
        other = Path(d, "elsewhere.toml")
        other.write_text('[footprint]\ndensity = "L"\n')
        with _cwd(Path(d, "sub")):
            assert RU.load()["footprint"]["density"] == "M"                  # walked up from sub/
            os.environ["ECADLIB_CONFIG"] = str(other)
            assert RU.load()["footprint"]["density"] == "L"                  # env beats the search
            assert RU.load(str(Path(d, "ecadlib.toml")))["footprint"]["density"] == "M"   # --rules beats env
            rc, out, _ = _run(["rules", "check"])
            assert rc == 0 and out.startswith(f"# {other.resolve()}")
            os.environ["ECADLIB_CONFIG"] = str(Path(d, "missing.toml"))
            rc, _, err = _run(["rules", "check"])
            assert rc == 2 and "ECADLIB_CONFIG" in err and "missing.toml" in err


def test_ecadlib_workspace_env_for_status():
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN), _cwd(d):
        ws = Path(d, "myws")
        (ws / "librarian" / "b").mkdir(parents=True)
        (ws / "librarian" / "b" / "plan.json").write_text(json.dumps([{"name": "P", "status": "held"}]))
        os.environ["ECADLIB_WORKSPACE"] = str(ws)
        rc, out, _ = _run(["status", "--json"])
        assert rc == 0 and json.loads(out)["totals"]["held"] == 1
        rc, out, _ = _run(["status", "--json", "--workspace", str(Path(d, "none"))])   # flag wins
        assert rc == 1 and json.loads(out)["exists"] is False and not Path(d, "none").exists()


# ------------------------------------------------------------------ friendly errors
def test_expected_errors_are_one_line_with_hint():
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN), _cwd(d):
        rc, out, err = _run(["release-check", "missing.json"])
        assert rc == 2 and "Traceback" not in err and "file not found: missing.json" in err and "hint:" in err
        Path(d, "bad.json").write_text("{x")
        rc, _, err = _run(["release-check", "bad.json"])
        assert rc == 2 and "not valid JSON" in err and "line 1 column 2" in err and "Traceback" not in err
        Path(d, "ecadlib.toml").write_text('[footprint]\ndensity = "Q"\n')
        rc, _, err = _run(["approve", "bad.json"])                  # bad JSON still first
        assert rc == 2
        rc, _, err = _run(["triage", "nope.csv", "--offline"])
        assert rc == 2 and "footprint.density" in err and "ecadlib doctor" in err


def test_debug_flag_and_env_show_the_traceback():
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN), _cwd(d):
        for argv, env in ((["--debug", "release-check", "missing.json"], None),
                          (["release-check", "missing.json", "--debug"], None),
                          (["release-check", "missing.json"], "1")):
            os.environ.pop("ECADLIB_DEBUG", None)
            if env:
                os.environ["ECADLIB_DEBUG"] = env
            try:
                _run(argv)
                raise AssertionError(f"{argv}: no exception with debug on")
            except FileNotFoundError:
                pass
    assert CU.debug_wanted(["x", "--", "--debug"], {}) == (False, ["x", "--", "--debug"])
    assert CU.debug_wanted(["x"], {"ECADLIB_DEBUG": "0"}) == (False, ["x"])


def test_unexpected_error_and_interrupt():
    def boom(argv):
        raise RuntimeError("kaboom")

    def stop(argv):
        raise KeyboardInterrupt
    mod = type(sys)("ecadlib_qol_fake")
    mod.boom, mod.stop = boom, stop
    sys.modules["ecadlib_qol_fake"] = mod
    cli.COMMANDS["qol-boom"] = ("ecadlib_qol_fake:boom", "")
    cli.COMMANDS["qol-stop"] = ("ecadlib_qol_fake:stop", "")
    try:
        with _env(**CLEAN):
            rc, _, err = _run(["qol-boom"])
            assert rc == 1 and "internal error (RuntimeError: kaboom)" in err and "--debug" in err and "Traceback" not in err
            rc, _, err = _run(["qol-stop"])
            assert rc == 130 and "interrupted" in err
    finally:
        del cli.COMMANDS["qol-boom"], cli.COMMANDS["qol-stop"], sys.modules["ecadlib_qol_fake"]


def test_friendly_mapping():
    import pandas as pd
    assert CU.friendly(PermissionError(13, "Permission denied", "/x"))[0] == "permission denied: /x"
    assert CU.friendly(IsADirectoryError(21, "Is a directory", "/d"))[0].startswith("expected a file")
    assert "pip install pillow" in CU.friendly(ModuleNotFoundError("No module named 'PIL'", name="PIL"))[1]
    assert CU.friendly(pd.errors.EmptyDataError("No columns to parse from file"))[0].startswith("cannot read the table")
    assert CU.friendly(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"))[0].startswith("file is not readable text")
    assert CU.friendly(ValueError("x")) is None and CU.friendly(KeyError("x")) is None


# ------------------------------------------------------------------ doctor
def test_doctor_rules_errors_point_at_the_line():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d, "ecadlib.toml")
        f.write_text('[naming]\ncase = "keep"\n\n[footprint]\npin1 = ["1"]\ndensity = "Q"\n')
        c, r = DR.check_rules(str(f))
        assert c["level"] == "FAIL" and r is None and c["detail"].startswith(f"{f}:6: footprint.density")
        f.write_text('[naming]\ncase = "keep"\n[bogus]\nx = 1\n')
        assert DR.check_rules(str(f))[0]["detail"].startswith(f"{f}:3: [bogus]")
        f.write_text('[naming]\ncase = "keep"\nfoo = = 2\n')                 # TOML syntax error: tomllib's line
        if sys.version_info >= (3, 11):
            assert DR.check_rules(str(f))[0]["detail"].startswith(f"{f}:3: ")
        f.write_text('[naming.by_category]\nWidgets = "{mpn}"\n')
        assert DR.check_rules(str(f))[0]["detail"].startswith(f"{f}:2: naming.by_category.Widgets")
        f.write_text('[footprint]\ndensity = "L"\n')
        c, r = DR.check_rules(str(f))
        assert c["level"] == "OK" and r["footprint"]["density"] == "L"
        assert DR.check_rules(str(Path(d, "nope.toml")))[0]["level"] == "FAIL"


def test_doctor_checks_levels():
    assert DR.check_python((3, 9, 1))["level"] == "FAIL"
    assert DR.check_python((3, 10, 4))["level"] == "WARN"
    assert DR.check_python((3, 13, 0))["level"] == "OK"
    pk = DR.check_packages(find_spec=lambda m: None if m in ("olefile", "PIL") else object(),
                           which=lambda t: None if t == "kicad-cli" else f"/bin/{t}")
    by = {c["check"]: c for c in pk}
    assert by["packages"]["level"] == "FAIL" and by["packages"]["hint"] == "pip install olefile"
    assert by["optional pillow"]["level"] == "WARN" and by["optional pypdf"]["level"] == "OK"
    assert by["tool kicad-cli"]["level"] == "WARN" and by["tool git"]["level"] == "OK"


def test_doctor_keys_names_only():
    secret = "sk-very-secret-value-123"
    keys = DR.check_keys({"datasheet": {"api_key_env": "MY_CLAUDE_KEY"}},
                         {"MY_CLAUDE_KEY": secret, "NEXAR_CLIENT_ID": "id-zzz", "NEXAR_CLIENT_SECRET": "s3cr3t"})
    by = {c["check"]: c for c in keys}
    assert by["key anthropic"]["level"] == "OK" and "MY_CLAUDE_KEY" in by["key anthropic"]["detail"]
    assert by["key altium365"]["level"] == "OK" and by["key part lookups"]["level"] == "OK"
    assert "mouser (MOUSER_API_KEY)" in by["key part lookups"]["detail"]             # optional extras: one line
    text = json.dumps(keys)
    assert secret not in text and "s3cr3t" not in text and "id-zzz" not in text



def test_doctor_key_hint_is_the_same_on_every_os():
    keys = {c["check"]: c for c in DR.check_keys({}, {})}        # keys: `ecadlib keys set`, the same on every OS
    assert "ecadlib keys set ANTHROPIC_API_KEY" in keys["key anthropic"]["hint"], keys["key anthropic"]


def test_doctor_end_to_end_exit_codes_and_writes_nothing():
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN), _cwd(d):
        ws, lib = Path(d, "ws"), Path(d, "lib")
        ws.mkdir()
        lib.mkdir()
        (lib / "demo.kicad_sym").write_text(KICAD_SYM)
        before = sorted(p.relative_to(d) for p in Path(d).rglob("*"))
        rc, out, _ = _run(["doctor", "--workspace", str(ws), "--lib", str(lib), "--json"])
        rep = json.loads(out)
        by = {c["check"]: c for c in rep["checks"]}
        assert by["workspace"]["level"] == "OK" and by["lib"]["level"] == "OK" and "1 .kicad_sym" in by["lib"]["detail"]
        assert rc == (0 if rep["ok"] else 1) and all(c["level"] in DR.LEVELS for c in rep["checks"])
        assert sorted(p.relative_to(d) for p in Path(d).rglob("*")) == before        # the probe file is gone
        os.environ["ECADLIB_LIB"] = str(Path(d, "gone"))
        rc, out, _ = _run(["doctor", "--workspace", str(ws)])
        assert rc == 1 and "FAIL  lib" in out and "gone: not found" in out
        Path(ws, "ecadlib.toml").write_text("[agent]\nlibs = [\"nolib\"]\n[[agent.watch.sources]]\ntype = \"bom_file\"\nref = \"x.csv\"\n")
        rep = DR.run(str(ws), [str(lib)])
        by = {c["check"]: c for c in rep["checks"]}
        assert not rep["ok"] and by["agent.libs"]["level"] == "FAIL" and by["watch bom_file"]["level"] == "WARN"
        assert by["rules"]["detail"].startswith(str(ws / "ecadlib.toml"))
        f = Path(d, "afile")
        f.write_text("")
        assert DR.check_workspace(f)["level"] == "FAIL" and DR.check_workspace(Path(d, "new"))["level"] == "OK"


# ------------------------------------------------------------------ --json on release-check and approve status
def test_release_check_and_approve_status_json():
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN):
        it = make(d)
        plan = Path(d, "plan.json")
        plan.write_text(json.dumps([it]))
        rc, out, _ = _run(["release-check", str(plan), "--json"])
        res = json.loads(out)
        assert rc == 0 and set(res) == {"plan", "report", "counts", "rows"}
        assert res["counts"] == {"ready": 1, "held": 0, "awaiting_approval": 0, "duplicate": 0}
        assert res["rows"][0]["name"] == "PART1" and res["rows"][0]["status"] == "ready"
        rc, out, _ = _run(["approve", str(plan), "status", "--json"])
        res = json.loads(out)
        assert rc == 0 and set(res) == {"plan", "policy_file", "enforced", "awaiting_approval", "rows"}
        assert res["enforced"] is False and res["rows"][0]["name"] == "PART1"


# ------------------------------------------------------------------ status
def _ws(d):
    ws = Path(d, "ws")
    for sub in ("librarian/a", "librarian/b", "librarian/broken", "reports", "upkeep/k/state", "agent/upkeep/p/state",
                "agent/queue/items", "uploads/20260101-x"):
        (ws / sub).mkdir(parents=True)
    (ws / "librarian/a/plan.json").write_text(json.dumps(
        [{"name": "A1", "status": "ready"}, {"name": "A2", "status": "held"}, {"name": "A3", "status": "awaiting_approval"}]))
    (ws / "librarian/b/plan.json").write_text(json.dumps([{"name": "B1", "status": "held"}, {"name": "B2", "status": "duplicate"}]))
    (ws / "librarian/broken/plan.json").write_text("{half")                 # a crash mid-write must not hide the rest
    (ws / "reports/triage_bom.json").write_text(json.dumps(
        {"name": "bom.csv", "finished": "2026-10-01T10:00:00Z", "rows": [{}, {}], "counts": {"in_library": 2}}))
    (ws / "upkeep/k/state/upkeep_last.json").write_text(json.dumps({"date": "2026-10-02T00:00:00Z", "new_changes": 1}))
    (ws / "agent/upkeep/p/state/upkeep_last.json").write_text(json.dumps({"date": "2026-10-05T00:00:00Z", "new_changes": 4}))
    (ws / "agent/status.json").write_text(json.dumps({"state": "idle", "heartbeat": "2026-10-06T00:00:00Z"}))
    for i, st in enumerate(("review", "review", "pushed")):
        (ws / f"agent/queue/items/{i}.json").write_text(json.dumps({"id": str(i), "status": st}))
    (ws / "uploads/20260101-x/bom.csv").write_text(BOM)
    return ws


def test_status_summary_read_only():
    with tempfile.TemporaryDirectory() as d:
        ws = _ws(d)
        before = {p: p.stat().st_mtime_ns for p in ws.rglob("*")}
        s = WS.status(ws)
        assert s["totals"] == {"parts": 5, "ready": 1, "held": 2, "awaiting_approval": 1, "duplicates": 1}
        assert len(s["plans"]) == 2 and s["boms"] == 1
        assert any("librarian/broken/plan.json" in p for p in s["problems"])
        assert s["last_triage"]["name"] == "bom.csv" and s["last_triage"]["lines"] == 2
        assert s["last_upkeep"] == {"date": "2026-10-05T00:00:00Z", "new_changes": 4, "state": "agent/upkeep/p/state/upkeep_last.json"}
        assert s["agent"]["state"] == "idle" and s["agent"]["queue"] == {"review": 2, "pushed": 1} and s["agent"]["waiting_review"] == 2
        text = WS.render(s)
        assert "5 parts in 2 library plans: 1 ready, 2 held, 1 waiting for sign-off, 1 duplicate" in text
        assert "2 waiting for review" in text and "ecadlib resolve" in text and "WARN" in text
        assert {p: p.stat().st_mtime_ns for p in ws.rglob("*")} == before
        empty = WS.status(Path(d, "nothing"))
        assert empty["exists"] is False and not Path(d, "nothing").exists() and "does not exist" in WS.render(empty)


def test_workspace_and_mcp_status_and_doctor():
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as d, _env(**CLEAN):
        ws = Workspace(_ws(d), offline=True)
        names = {t["name"] for t in M.TOOLS}
        assert {"workspace_status", "doctor"} <= names
        s = M.call_tool(ws, "workspace_status", {})
        assert s["totals"]["held"] == 2 and s["workspace"] == str(ws.root)
        rep = M.call_tool(ws, "doctor", {})
        by = {c["check"]: c for c in rep["checks"]}
        assert by["workspace"]["level"] == "OK" and by["lib"]["level"] == "OK"
        json.dumps(rep)


# ------------------------------------------------------------------ completion
def test_top_level_help_lists_every_command():
    listed = set(re.findall(r"^    ecadlib ([a-z0-9-]+)", cli.__doc__, re.M))
    assert listed == set(cli.COMMANDS), (set(cli.COMMANDS) - listed, listed - set(cli.COMMANDS))


def test_completion_tree_and_scripts():
    t = CO.tree()
    assert set(t) == set(cli.COMMANDS)
    assert "--lib" in t["triage"]["options"] and "--json" in t["triage"]["options"]
    assert set(t["queue"]["subcommands"]) >= {"list", "approve"} and t["footprint"]["subcommands"]["gen"]["choices"]["--density"]
    assert t["completion"]["positional_choices"] == ["bash", "zsh", "fish"]
    sh = CO.bash(t)
    assert "complete -o filenames -o bashdefault -F _ecadlib ecadlib" in sh
    assert CO.zsh(t).startswith("#compdef ecadlib") and "bashcompinit" in CO.zsh(t)
    fi = CO.fish(t)
    assert "complete -c ecadlib -n __fish_use_subcommand -a triage" in fi
    assert "complete -c ecadlib -n '__fish_seen_subcommand_from triage' -l offline\n" in fi          # a flag: no value
    assert "complete -c ecadlib -n '__fish_seen_subcommand_from triage' -l lib -r -F\n" in fi
    rc, out, _ = _run(["completion", "fish"])
    assert rc == 0 and out == fi


def test_bash_completion_really_completes():
    bash = shutil.which("bash")
    if not bash:
        print("skip: bash not installed")
        return
    with tempfile.TemporaryDirectory() as d:
        f = Path(d, "c.bash")
        f.write_text(CO.bash(CO.tree()))
        probe = (f'source "{f}"\n'
                 't() { COMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]}-1)); COMPREPLY=(); _ecadlib; echo "${COMPREPLY[*]}"; }\n'
                 't ecadlib tri\n'
                 't ecadlib queue --workspace W ""\n'
                 't ecadlib footprint gen x.json --density ""\n'
                 't ecadlib --debug triage --of\n'
                 't ecadlib completion z\n')
        r = subprocess.run([bash, "--norc", "-c", probe], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.splitlines() == ["triage", "approve list rebuild reject retry show", "A B C", "--offline", "zsh"], r.stdout


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_qol"]))


def test_env_lib_only_for_the_real_subcommand():
    from ecadlib import cliutil
    env = {"ECADLIB_LIB": "/libs/a"}
    assert cliutil.env_defaults("agent", ["approve", "run"], env)[0] == ["approve", "run"]     # an item named "run"
    assert cliutil.env_defaults("agent", ["--workspace", "run", "status"], env)[0] == ["--workspace", "run", "status"]
    assert cliutil.env_defaults("agent", ["--workspace", "W", "run"], env)[0] == ["--workspace", "W", "run", "--lib", "/libs/a"]
    assert cliutil.env_defaults("agent", ["serve"], env)[0] == ["serve", "--lib", "/libs/a"]
