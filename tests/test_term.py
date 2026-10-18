"""Offline tests for the terminal output layer (ecadlib.term) and the commands that print through it, plus
`ecadlib review`. Run:  python3 -m tests.test_term

Golden text: the exact output of a fixed run (the demo workspace, built offline) in plain mode (piped) and in
terminal mode (colour + glyphs), at 60 columns, with NO_COLOR, -q and -v; --json output must not change with any of
the styling switches. Set ECADLIB_UPDATE_GOLDEN=1 to rewrite tests/fixtures/term/*.txt (then read the diff)."""
import contextlib, io, json, os, re, shutil, sys, tempfile, threading, time
from pathlib import Path
from unittest import mock

from ecadlib import cli, term as TM

GOLD = Path(__file__).parent / "fixtures" / "term"
STYLE_ENV = ("NO_COLOR", "ECADLIB_COLOR", "COLUMNS", "TERM", "ECADLIB_LIB", "ECADLIB_WORKSPACE", "ECADLIB_CONFIG",
             "ECADLIB_DEBUG") + TM.CI_VARS


class TTY(io.StringIO):
    """A StringIO that says it is a terminal (utf-8)."""
    encoding = "utf-8"

    def isatty(self):
        return True


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in set(STYLE_ENV) | set(kv)}
    try:
        for k in STYLE_ENV:
            os.environ.pop(k, None)
        for k, v in kv.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run(argv, tty=False, **kv):
    """(exit code, stdout) of cli.main(argv) with stdout a pipe or a terminal; stderr dropped."""
    out = TTY() if tty else io.StringIO()
    err = io.StringIO()
    old = sys.argv
    with env(**kv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as x:
            rc = x.code if isinstance(x.code, int) else 1
        finally:
            sys.argv = old
            TM.VERBOSITY, TM.JSON = 0, False
    return rc or 0, out.getvalue()


_DEMO = {}


def demo():
    """A demo workspace built once per test session, in a temporary folder; returns its path (cwd-relative names
    in the goldens: every test runs from its parent folder)."""
    if "d" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-term-"))
        cwd = os.getcwd()
        try:
            os.chdir(d)
            with env():
                rc, _ = run(["demo", "-o", "ws", "--tool", "both"])
            assert rc == 0
        finally:
            os.chdir(cwd)
        _DEMO["d"] = d
    return _DEMO["d"]


@contextlib.contextmanager
def in_demo(copy=False):
    d = demo()
    cwd = os.getcwd()
    tmp = None
    if copy:
        tmp = Path(tempfile.mkdtemp(prefix="ecadlib-term-copy-"))
        shutil.copytree(d / "ws", tmp / "ws")
        d = tmp
    try:
        os.chdir(d)
        yield d
    finally:
        os.chdir(cwd)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def golden(name, text):
    f = GOLD / f"{name}.txt"
    text = re.sub(r"in \d+\.\d s", "in X.X s", text).replace("\\", "/")     # Windows prints its own separators
    text = re.sub(r"\b\d+(?:h \d{2}m|m \d{2}s|s)\b", "Ns", text)          # step times in `status` (ecadlib.timing.human)
    if os.environ.get("ECADLIB_UPDATE_GOLDEN") or not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        if not os.environ.get("ECADLIB_UPDATE_GOLDEN"):
            raise AssertionError(f"golden {f} was missing; written - check it and run again")
    want = f.read_text(encoding="utf-8")
    assert text == want, f"{name}: output changed\n--- want\n{want}\n--- got\n{text}"


PLAN = "ws/librarian/demo_bom_demo/plan.json"


# ------------------------------------------------------------------ style detection
def test_style_only_on_a_terminal_and_never_with_no_color_ci_or_json():
    with env():
        assert TM.wants_style(TTY()) and not TM.wants_style(io.StringIO())
    for kv in ({"NO_COLOR": "1"}, {"CI": "true"}, {"GITHUB_ACTIONS": "true"}, {"TERM": "dumb"}, {"ECADLIB_COLOR": "never"}):
        with env(**kv):
            assert not TM.wants_style(TTY()), kv
    with env(ECADLIB_COLOR="always"):
        assert TM.wants_style(io.StringIO())
    with env(NO_COLOR=""):                     # NO_COLOR set but empty: no effect (no-color.org)
        assert TM.wants_style(TTY())
    try:
        TM.set_from_argv(["x", "--json"])
        with env(ECADLIB_COLOR="always"):
            assert not TM.wants_style(TTY())
    finally:
        TM.JSON = False


def test_quiet_verbose_flags_are_stripped_everywhere_but_after_double_dash():
    try:
        assert TM.set_from_argv(["plan.json", "-q", "--allow", "X"]) == ["plan.json", "--allow", "X"] and TM.VERBOSITY == -1
        assert TM.set_from_argv(["-v", "plan.json", "--verbose"]) == ["plan.json"] and TM.VERBOSITY == 2
        assert TM.set_from_argv(["a", "--", "-q"]) == ["a", "--", "-q"] and TM.VERBOSITY == 0
        assert TM.set_from_argv(["a", "--json"]) == ["a", "--json"] and TM.JSON
        assert TM.set_from_argv(["a"]) == ["a"] and not TM.JSON
    finally:
        TM.VERBOSITY, TM.JSON = 0, False


def test_status_words_and_glyphs():
    plain, tty = TM.Term(io.StringIO(), styled=False), TM.Term(TTY(), styled=True)
    assert plain.status("ready") == "ready" and plain.status("held") == "held" and plain.status("awaiting_approval") == "waiting for sign-off"
    assert tty.status("ready") == "\x1b[32m✓ ready\x1b[0m" and tty.status("held") == "\x1b[33m! held\x1b[0m"
    assert TM.visible_len(tty.status("signoff")) == len("○ waiting for sign-off")
    ascii_tty = TM.Term(type("A", (TTY,), {"encoding": "ascii"})(), styled=True)
    assert ascii_tty.status("ready") == "\x1b[32m+ ready\x1b[0m"           # a terminal that cannot show ✓


def test_table_fits_the_width_and_wraps_the_last_column():
    rows = [["held", "CH340C", "There is no symbol for this part yet. Pick a symbol from the candidates, or let the datasheet reader draw one."],
            ["ready", "AMS1117-3.3", ""]]
    t = TM.Term(io.StringIO(), styled=False, width=60)
    lines = t.table_lines(rows)
    assert all(len(l) <= 60 for l in lines), lines
    assert lines[0].startswith("  held   CH340C       There is no symbol") and lines[1].startswith(" " * 22) and lines[1][22] != " "
    piped = TM.Term(io.StringIO(), styled=False, width=None).table_lines(rows)
    assert len(piped) == 2                                                   # piped: no limit, nothing wrapped
    narrow = TM.Term(io.StringIO(), styled=False, width=30).table_lines([["x" * 20, "y" * 10, "some long text here"]])
    assert narrow[0].rstrip() == "  " + "x" * 20 + "  " + "y" * 10 and narrow[1].startswith("      some")


def test_progress_live_on_a_terminal_plain_lines_when_piped():
    tty = TTY()
    t = TM.Term(tty, styled=True, width=80)
    with t.progress(3) as p:
        p.start("SOT-23")
        p.done("SOT-23", "ready")
        p.done("CH340C", "held", "no symbol yet")
        t.write("a line in between")
    s = tty.getvalue()
    assert "\r\x1b[2K" in s and "[2/3] CH340C  \x1b[33m! held\x1b[0m  no symbol yet" in s and "a line in between\n" in s
    assert s.endswith("\r\x1b[2K")                                         # the live line is gone at the end
    pipe = io.StringIO()
    t = TM.Term(pipe, styled=False)
    with t.progress(14) as p:
        p.start("x")
        p.done("SOT-23", "ready")
    assert pipe.getvalue() == "[ 1/14] SOT-23  ready\n"
    quiet = io.StringIO()
    with TM.Term(quiet, styled=False, verbosity=-1).progress(2) as p:
        p.done("a", "ready")
    assert quiet.getvalue() == ""


def test_waiting_says_what_and_how_long():
    pipe = io.StringIO()
    with mock.patch.object(TM, "HEARTBEAT", (1, 2)):
        with TM.waiting("DigiKey", TM.Term(pipe, styled=False)):
            time.sleep(2.6)
    assert "still waiting on DigiKey (1.0 s)" in pipe.getvalue() or "still waiting on DigiKey (1." in pipe.getvalue(), pipe.getvalue()
    tty = TTY()
    with TM.waiting("Claude (model call)", TM.Term(tty, styled=True)):
        time.sleep(1.3)
    assert "waiting on\x1b[0m Claude (model call)  1." in tty.getvalue() and tty.getvalue().endswith("\r\x1b[2K"), repr(tty.getvalue())
    q = io.StringIO()
    with TM.waiting("x", TM.Term(q, styled=False, verbosity=-1)):
        pass
    assert q.getvalue() == "" and TM.source_name("gitlab.com") == "the KiCad library (gitlab.com)"


# ------------------------------------------------------------------ golden output of real commands
def test_golden_release_check_plain_tty_narrow_quiet_verbose():
    with in_demo(copy=True):
        rc, plain = run(["release-check", PLAN])
        assert rc == 2
        golden("release_check_plain", plain)
        rc, tty = run(["release-check", PLAN], tty=True, COLUMNS="100")
        golden("release_check_tty", tty)
        assert "\x1b[" in tty and "\x1b[" not in plain
        assert run(["release-check", PLAN], tty=True, NO_COLOR="1")[1] == plain            # NO_COLOR: plain text
        assert run(["release-check", PLAN], tty=True, CI="true")[1] == plain               # CI logs: plain text
        rc, narrow = run(["release-check", PLAN], tty=True, NO_COLOR="1", COLUMNS="60")
        golden("release_check_60cols", narrow)
        assert all(len(l) <= 60 for l in narrow.splitlines()), narrow
        golden("release_check_quiet", run(["release-check", PLAN, "-q"])[1])
        golden("release_check_verbose", run(["release-check", "-v", PLAN])[1])


def test_golden_status_kicad_export_parity_migrate():
    with in_demo(copy=True):
        golden("status_plain", run(["status", "--workspace", "ws"])[1])
        golden("status_tty", run(["status", "--workspace", "ws"], tty=True, COLUMNS="100")[1])
        golden("kicad_export_plain", run(["kicad-export", PLAN, "-o", "kx", "--name", "mylib", "--offline"])[1])
        golden("parity_plain", run(["parity", PLAN])[1])
        golden("migrate_plain", run(["migrate", "ws", "--check"])[1])
        golden("dblib_sync_plain", run(["dblib-sync", PLAN, "--to", "dbs", "--dry-run"])[1])


def test_json_output_does_not_change_with_styling():
    """--json is for scripts: colour, width, -q / -v and a terminal must not change one byte."""
    with in_demo(copy=True):
        for argv in (["release-check", PLAN, "--json"], ["status", "--workspace", "ws", "--json"],
                     ["migrate", "ws", "--check", "--json"], ["agent", "--workspace", "ws", "status", "--json"]):
            base = run(argv)[1]
            json.loads(base)
            for kv, extra in (({"ECADLIB_COLOR": "always", "COLUMNS": "40"}, []), ({}, ["-v"]), ({}, ["-q"])):
                got = run(argv + extra, tty=True, **kv)[1]
                assert got == base, (argv, kv, extra)
            assert "\x1b" not in base


def test_errors_are_one_line_and_a_fix():
    from ecadlib import review_cli as RV
    err = io.StringIO()
    with in_demo(), env(), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = RV.main(["--workspace", "nowhere"])
    lines = err.getvalue().splitlines()
    assert rc == 2 and len(lines) == 2, lines
    assert lines[0] == "ecadlib review: There is no library plan in nowhere yet." and lines[1].startswith("  fix: Fill a library")
    assert "Traceback" not in err.getvalue() and "/" not in lines[0]


def test_queue_list_and_agent_status_in_plain_words():
    from tests import test_qol as Q
    with tempfile.TemporaryDirectory() as d:
        ws = Q._ws(d)
        for i, (st, k) in enumerate((("review", "new"), ("review", "changed"), ("pushed", "new"))):
            (ws / f"agent/queue/items/{i}.json").write_text(json.dumps({"id": f"q{i}", "key": f"TI|LM358DR{i}", "status": st,
                                                                          "change": {"kind": k}, "proof": {"verdict": "pass"}}))
        rc, out = run(["queue", "--workspace", str(ws), "list"])
        assert rc == 0 and "3 items in the review queue: 1 released, 2 waiting for review" in out or \
            "3 items in the review queue: 2 waiting for review, 1 released" in out, out
        assert "  q1  waiting for review  TI|LM358DR1  changed  pass" in out and "ecadlib queue approve ID" in out, out
        js = json.loads(run(["queue", "--workspace", str(ws), "list", "--json"])[1])
        assert [i["id"] for i in js] == ["q2", "q1", "q0"] and js[1]["status"] == "review"
        rc, out = run(["agent", "--workspace", str(ws), "status"])
        assert out.startswith("Library agent: idle  (last heartbeat 2026-10-06T00:00:00Z)") and "2 waiting for review" in out, out
        assert json.loads(run(["agent", "--workspace", str(ws), "status", "--json"])[1])["state"] == "idle"


# ------------------------------------------------------------------ ecadlib review
def test_review_non_interactive_never_prompts_and_prints_the_fix():
    with in_demo(copy=True):
        asked = []
        out = io.StringIO()
        from ecadlib import review_cli as RV
        with env(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = RV.main(["--workspace", "ws", "--yes", "--offline"], inp=lambda p: asked.append(p) or "a")
        text = out.getvalue()
        assert rc == 2 and not asked
        assert "CH340C" in text and "There is no symbol for this part yet." in text and "fix: Pick a symbol" in text
        assert "datasheet: https://www.lcsc.com/datasheet/C84681.pdf" in text and "1 part held" in text
        assert "files:" not in text and "plan.json:" not in text.split("\n", 1)[1]      # machine reasons stay in the plan


def test_review_interactive_pick_a_candidate_and_release_anyway():
    from tests import test_resolve as TR
    from ecadlib import resolve as R, review_cli as RV
    with tempfile.TemporaryDirectory() as d:
        plan, src = TR.make_plan(d)
        answers = iter(["d", "1", "s", "a", "q"])
        prompts = []

        def inp(p):
            prompts.append(p)
            return next(answers)
        out = io.StringIO()
        with env(), mock.patch.object(R, "Sources", lambda *a, **k: src), mock.patch("webbrowser.open", lambda u: True) as wb, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = RV.main([str(plan)], inp=inp)
        text = out.getvalue()
        items = {i["name"]: i for i in json.loads(plan.read_text())}
        # part 1 (USBC-FAKE): d opens the datasheet, 1 picks the best symbol -> ready
        assert "opened https://example.com/ds.pdf" in text, text
        assert "symbol is now kicad:Connector:USB_C_Receptacle_Fake_4P_NoShield" in text and items["USBC-FAKE"]["status"] == "ready"
        # part 2: skipped; part 3: "a" on a part whose symbol file is missing cannot be released anyway
        assert items["FTSH-105-TEST"]["status"] == "held"
        assert "This one cannot be released anyway." in text
        assert rc == 2 and "3 held parts reviewed: 1 fixed, 2 still held" in text, text
        assert all("use a candidate" in p or "release anyway" in p for p in prompts)


def test_review_release_anyway_records_the_override():
    from ecadlib import review_cli as RV, release_check as RC
    with in_demo(copy=True) as d:
        plan = Path(PLAN)
        items = json.loads(plan.read_text())
        it = next(i for i in items if i["name"] == "AO3401A")
        it["parameters"] = [p for p in it["parameters"] if p["name"] != "Manufacturer"]      # a field missing: held
        RC.apply(items, plan.parent)
        plan.write_text(json.dumps(items))
        out = io.StringIO()
        with env(ECADLIB_USER="tester"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = RV.main([PLAN, "--part", "AO3401A", "--limit", "0"], inp=lambda p: "a")
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "AO3401A")
        assert it["status"] in ("ready", "awaiting_approval") and it["allow"]["user"] == "tester", it
        assert "released anyway" in out.getvalue() and rc == 0, out.getvalue()


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_term"]))
