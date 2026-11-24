"""v0.6.0 "Easy to adopt" (2026-11-24): the places a brand-new user got stuck on a first install and first run."""
import io, sys
from contextlib import redirect_stderr, redirect_stdout

from tests.fixtures_lite import lite


def _run(argv):
    from ecadlib import cli
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


# ------------------------------------------------------------------ a BOM given where plan.json belongs
@lite
def test_a_bom_given_to_a_plan_command_says_to_run_the_librarian_first(tmp_path):
    from ecadlib import firstrun as FR
    bom = tmp_path / "my_board.csv"
    bom.write_text("Designator,MPN\nR1,RC0402FR-0710KL\n")
    for cmd, argv in [("kicad-export", [str(bom), "-o", str(tmp_path / "k"), "--name", "x"]),
                      ("altium-pkg", [str(bom), "--out", str(tmp_path / "a")]),
                      ("review", [str(bom)]), ("release-check", [str(bom)]), ("resolve", [str(bom), "list"]),
                      ("kicad-dblib", [str(bom), "-o", str(tmp_path / "d"), "--name", "x"]),
                      ("export-parts", [str(bom), "--format", "csv"]), ("parity", [str(bom)])]:
        bad = FR.preflight(cmd, argv)
        assert bad, cmd
        assert "is a BOM" in bad[0] and "plan.json" in bad[0], (cmd, bad)
        assert f"ecadlib librarian {bom}" in bad[1] and f"ecadlib {cmd}" in bad[1], (cmd, bad)


@lite
def test_plan_and_bom_commands_that_take_a_bom_are_left_alone(tmp_path):
    from ecadlib import firstrun as FR
    bom = tmp_path / "b.xlsx"
    bom.write_bytes(b"PK\x03\x04")
    plan = tmp_path / "plan.json"
    plan.write_text("[]")
    assert FR.preflight("kicad-export", [str(plan), "-o", str(tmp_path / "k"), "--name", "x"]) is None
    for cmd in ("librarian", "triage", "dblib", "upkeep", "datasheet", "altium365", "stdparts"):
        assert FR.preflight(cmd, [str(bom)]) is None, cmd
    assert FR.preflight("review", [str(tmp_path / "nope.csv")]) is None      # not there: the command says so itself
    assert FR.preflight("kicad-export", ["-h", str(bom)]) is None
    assert FR.preflight("dedupe", [str(plan), "--bom", str(bom)]) is None              # the BOM as an option's value
    assert FR.preflight("dedupe", ["--bom", str(bom), str(plan)]) is None
    assert FR.preflight("dedupe", ["--bom", str(bom), str(bom)])                       # the plan slot is the BOM


@lite
def test_the_cli_stops_before_the_command_with_the_bom_hint(tmp_path):
    bom = tmp_path / "b.csv"
    bom.write_text("Designator,MPN\nR1,X\n")
    code, _, err = _run(["review", str(bom)])
    assert code == 2 and "is a BOM" in err and "Expecting value" not in err, err


# ------------------------------------------------------------------ a mistyped command
def test_a_mistyped_command_suggests_the_closest_one():
    code, _, err = _run(["lbrarian", "x.csv"])
    assert code == 2 and "did you mean `ecadlib librarian`" in err, err
    code, _, err = _run(["kicad-exprot"])
    assert "did you mean `ecadlib kicad-export`" in err, err
    code, _, err = _run(["zzzzzz"])
    assert code == 2 and "did you mean" not in err and "ecadlib -h" in err, err


# ------------------------------------------------------------------ no browser could be opened
def test_when_no_browser_opens_a_person_at_a_terminal_gets_the_link():
    from ecadlib.ui import server as S
    said = []
    assert S.open_or_say("http://127.0.0.1:8765/setup?key=T1", opener=lambda u: False, tty=True, say=said.append) is False
    assert said and "http://127.0.0.1:8765/setup?key=T1" in said[0] and "browser" in said[0], said
    said.clear()
    S.open_or_say("http://127.0.0.1:8765/?key=T2", opener=lambda u: False, tty=False, say=said.append)
    assert said and "T2" not in said[0], said                 # a log file never gets the key
    said.clear()
    assert S.open_or_say("http://x/?key=T3", opener=lambda u: True, tty=True, say=said.append) is True
    assert not said
    said.clear()

    def boom(u):
        raise RuntimeError("no display")
    S.open_or_say("http://x/?key=T4", opener=boom, tty=True, say=said.append)
    assert said and "T4" in said[0], said


# ------------------------------------------------------------------ keys set without a terminal
@lite
def test_keys_set_with_nothing_on_stdin_says_how_to_give_the_key(tmp_path, monkeypatch):
    from ecadlib import userconfig as UC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        code = UC.main(["set", "ANTHROPIC_API_KEY"])
    assert code == 2 and not (tmp_path / UC.KEYS).exists(), code
    assert "--stdin" in err.getvalue() and "terminal" in err.getvalue(), err.getvalue()


# ------------------------------------------------------------------ KiCad: the suggested next step works as given
def test_init_next_steps_for_kicad_name_the_install_option():
    from ecadlib import firstrun as FR
    steps = FR.next_steps({"tool": "kicad", "workspace": "ws"})
    kicad = [c for c, _ in steps if c.startswith("ecadlib kicad-export")]
    assert kicad and any("--install" in w for c, w in steps if c.startswith("ecadlib kicad-export")), steps


@lite
def test_kicad_export_suggests_install_through_the_export_not_a_dead_end(tmp_path):
    """`kicad-install` on a plain export warns that the 3D models point at ${KIPRJMOD} and says to re-export with
    --install: the hint after an export names that command straight away."""
    import json
    from ecadlib import kicad_export as KE
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([]))
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        try:
            KE.main([str(plan), "-o", str(tmp_path / "k"), "--name", "mylib"])
        except SystemExit:
            pass
    text = out.getvalue()
    assert "ecadlib kicad-install" not in text, text
    assert "--install" in text, text


@lite
def test_keys_set_reads_a_piped_key_and_still_shows_a_prompt(tmp_path, monkeypatch):
    from ecadlib import userconfig as UC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-test-123\n"))
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        code = UC.main(["set", "MY_TEST_KEY"])
    assert code == 0 and UC.get_key("MY_TEST_KEY") == "sk-test-123", code
    assert "MY_TEST_KEY" in err.getvalue() and "echo" not in err.getvalue(), err.getvalue()


@lite
def test_keys_set_with_no_stdin_at_all_says_no_input(tmp_path, monkeypatch):
    from ecadlib import userconfig as UC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", None)
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        code = UC.main(["set", "MY_TEST_KEY"])
    assert code == 2 and "--stdin" in err.getvalue(), err.getvalue()


def test_a_link_without_a_key_is_never_offered_as_the_way_in():
    from ecadlib.ui import server as S
    said = []
    S.open_or_say("http://127.0.0.1:8765/setup", opener=lambda u: False, tty=True, say=said.append)
    assert said and "keep it to yourself" not in said[0] and "ecadlib start" in said[0], said


@lite
def test_option_values_are_not_taken_for_the_plan(tmp_path):
    from ecadlib import firstrun as FR
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    plan = tmp_path / "plan.json"
    plan.write_text("[]")
    assert FR.preflight("approve", ["-m", str(notes), str(plan), "approve"]) is None
