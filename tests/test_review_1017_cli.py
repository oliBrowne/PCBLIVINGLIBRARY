"""Regression tests from the 2026-10-17 review: CLI / terminal / KiCad plugin / experimental exporters.
Run:  python3 -m pytest -q tests/test_review_1017_cli.py"""
import io, json, subprocess, sys, tempfile, types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _tmp():
    return Path(tempfile.mkdtemp())


class CP1252Pipe(io.TextIOWrapper):
    """What sys.stdout is on Windows when the output is piped or redirected (no UTF-8 mode): cp1252, strict."""

    def __init__(self):
        self.raw_buf = io.BytesIO()
        super().__init__(self.raw_buf, encoding="cp1252", errors="strict", write_through=True)

    def isatty(self):
        return False


def test_term_write_survives_a_cp1252_pipe():
    from ecadlib import term as TM
    s = CP1252Pipe()
    t = TM.Term(s, styled=False, width=None, verbosity=0)
    t.write("R1  10k\u03a9  0402")                  # Ω is not in cp1252: this raised UnicodeEncodeError
    t.table([["C1", "100n\u03a9"]])
    out = s.raw_buf.getvalue().decode("cp1252")
    assert "R1  10k?  0402" in out and "100n?" in out, out


def test_doctor_render_has_no_glyph_the_console_cannot_print():
    from ecadlib import doctor as DR
    rep = {"ok": True, "checks": [{"check": "python", "level": "OK", "detail": "Python 3.12", "hint": ""}]}
    s = CP1252Pipe()
    with mock.patch.object(sys, "stdout", s):
        text = DR.render(rep, styled=True, width=80)
    text.encode("cp1252")                            # the "✓" glyph would not encode
    assert "ok" in text.lower()


def test_doctor_does_not_run_a_menu_command_it_found_by_itself():
    """`ecadlib doctor` in a downloaded project folder: an out/altium_menu/ecadlib_menu.ini there must not be run."""
    from ecadlib import altium_menu as AM, doctor as DR
    d = _tmp()
    (d / "out" / "altium_menu").mkdir(parents=True)
    ini = d / "out" / "altium_menu" / AM.INI
    ini.write_bytes(AM.ini_text(command=f'"{sys.executable}" -c "print(1)"').encode())
    ran = []
    real = DR.check_altium_menu

    def spy(menu, run=None, **kw):
        return real(menu, run=lambda argv, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "ecadlib 1", ""), **kw)
    with mock.patch.object(DR, "check_altium_menu", spy), mock.patch.object(DR, "find_menu_ini", lambda given=None: ini):
        rep = DR.run(str(d / "ws"), environ={})
        assert not ran, ran
        c = next(x for x in rep["checks"] if x["check"] == "altium menu")
        assert c["level"] == "OK" and "not run" in c["detail"], c
        DR.run(str(d / "ws"), environ={}, altium_menu=str(ini))     # named on the command line: it is tried
        assert ran


def _plugin_module():
    fake = types.ModuleType("pcbnew")
    fake.ActionPlugin = type("ActionPlugin", (), {"register": lambda self: None})
    with mock.patch.dict(sys.modules, {"pcbnew": fake}):
        src = ROOT / "ecadlib" / "assets" / "kicad_action_plugin.py"
        mod = types.ModuleType("ecadlib_kicad_action_plugin")
        mod.__file__ = str(src)
        exec(compile(src.read_text(), str(src), "exec"), mod.__dict__)
    return mod


def test_plugin_ecadlib_cmd_keeps_windows_backslashes():
    mod = _plugin_module()
    assert mod.split_command(r"C:\Tools\ecadlib.exe", nt=True) == [r"C:\Tools\ecadlib.exe"]
    assert mod.split_command(r'"C:\Program Files\Python\python.exe" -m ecadlib', nt=True) == \
        [r"C:\Program Files\Python\python.exe", "-m", "ecadlib"]
    assert mod.split_command("/usr/bin/python3 -m ecadlib", nt=False) == ["/usr/bin/python3", "-m", "ecadlib"]
    exe = _tmp() / "my ecadlib"
    exe.write_text("")
    assert mod.ecadlib_command({"ECADLIB_CMD": str(exe)}) == [str(exe)]     # a path with a space, unquoted


def test_launcher_waits_with_a_timeout():
    """`ecadlib start --no-window` on Windows: an untimed Event.wait() never sees Ctrl+C."""
    src = (ROOT / "ecadlib" / "launcher.py").read_text()
    assert "threading.Event().wait()" not in src
