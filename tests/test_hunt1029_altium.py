"""Hunt 2026-10-29 (altium): the Altium menu's files as Altium's script engine reads them (no Altium here: the
Windows profile API and cmd.exe rules are modelled from their documentation)."""
import tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_menu as AM, doctor as D


def profile_read(ini_text, key, section="ecadlib"):
    """What GetPrivateProfileString (TIniFile.ReadString) returns for key: the value with leading/trailing blanks
    removed and, when it is enclosed in a pair of single or double quotation marks, without them (documented: "If the
    string associated with lpKeyName is enclosed in single or double quotation marks, the marks are discarded")."""
    cur = None
    for ln in ini_text.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
        elif cur == section and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            if k.strip() == key:
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                return v
    return ""


def test_windows_app_command_survives_altium_reading_the_ini():
    """The Windows app (frozen) writes command="C:\\Program Files\\ecadlib\\ecadlib.exe": the whole value is in quotes,
    so Altium's TIniFile drops them, the menu puts C:\\Program Files\\ecadlib\\ecadlib.exe unquoted into command.txt and
    cmd.exe runs "C:\\Program" (not recognized): no menu entry works. What Altium reads must still be the quoted path,
    and `ecadlib doctor` (load_command) must see the same value Altium does."""
    with tempfile.TemporaryDirectory() as d:
        app = Path(d) / "Program Files" / "ecadlib"
        app.mkdir(parents=True)
        (app / "ecadlib.exe").write_bytes(b"MZ")
        cmd = AM.default_command(str(app / "ecadlib-app.exe"), nt=True, frozen=True)
        for text in (AM.ini_text(command=cmd),):
            seen = profile_read(text, "command")
            assert D.split_command(seen)[0] == str(app / "ecadlib.exe"), (cmd, seen)
        # an older menu with an empty command= gets it filled the same way
        ini = Path(d) / "ecadlib_menu.ini"
        ini.write_bytes(AM.ini_text(command="").encode("utf-8"))
        assert AM._fill_command(ini, cmd)
        seen = profile_read(ini.read_text(encoding="utf-8"), "command")
        assert D.split_command(seen)[0] == str(app / "ecadlib.exe"), seen
        assert AM.load_command(ini) == seen


def test_doctor_sees_the_command_altium_sees():
    """A hand-edited command="C:\\Program Files\\ecadlib\\ecadlib.exe" (the .ini's own example quotes the program) is run
    by Altium without its quotes; doctor must not call it OK."""
    with tempfile.TemporaryDirectory() as d:
        ini = Path(d) / "ecadlib_menu.ini"
        ini.write_bytes(AM.ini_text(command='"C:\\Program Files\\ecadlib\\ecadlib.exe"').encode("utf-8"))
        assert AM.load_command(ini) == profile_read(ini.read_text(encoding="utf-8"), "command")


def test_menu_settings_paths_in_quotes_are_the_paths():
    """workspace="D:\\My Parts\\ws" (quoted, as people write paths with spaces) is that folder, not a folder named with
    quotes next to the menu."""
    with tempfile.TemporaryDirectory() as d:
        ini = Path(d) / "ecadlib_menu.ini"
        ini.write_bytes(AM.ini_text(workspace='"D:\\My Parts\\ws"', library="'D:\\My Parts\\lib'").encode("utf-8"))
        cfg = AM.load_config(ini)
        assert str(cfg["workspace"]).replace("/", "\\").endswith("D:\\My Parts\\ws") and '"' not in str(cfg["workspace"])
        assert "'" not in str(cfg["library"])


def test_install_menu_in_a_folder_outside_latin1_says_what_to_do():
    """install-menu merges the menu into DXP.RCS, written in Latin-1: a menu folder named in Cyrillic (C:\\Users\\Ольга)
    stopped with Python's "'latin-1' codec can't encode characters in position 91-95", after making a backup."""
    import json
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "m").mkdir()
        (d / "a").mkdir()
        (d / "a" / "DXP.RCS").write_bytes(b"")
        (d / "m" / "altium_menu.json").write_text(json.dumps({"win_dir": "C:\\Users\\\u041e\u043b\u044c\u0433\u0430\\menu"}))
        try:
            AM.install_menu_bar(d / "m", folders=[d / "a"], running=lambda: False, settle_s=0)
            raise AssertionError("a folder DXP.RCS cannot name must be refused")
        except UnicodeError as e:
            raise AssertionError(f"raw codec error: {e}")
        except ValueError as e:
            assert "codec" not in str(e) and "--out" in str(e), e
        assert sorted(p.name for p in (d / "a").iterdir()) == ["DXP.RCS"], "nothing written, no backup"


def test_menu_folder_outside_latin1_goes_by_its_short_name():
    """On Windows the folder's 8.3 short name (ASCII) names it in DXP.RCS, as wake_command does for RunScript."""
    short = lambda p: "C:\\Users\\OLGA~1\\menu" if "\u041e" in str(p) else p
    assert AM.rcs_folder("C:\\Users\\\u041e\u043b\u044c\u0433\u0430\\menu", short=short) == "C:\\Users\\OLGA~1\\menu"
    assert AM.rcs_folder("C:\\Users\\Jos\u00e9\\menu", short=short) == "C:\\Users\\Jos\u00e9\\menu"   # Latin-1 as it was


def test_project_path_the_script_wrote_with_question_marks_is_found():
    """The menu writes request.ini with TIniFile, in the ANSI code page: a project folder named in characters that
    code page lacks (Cyrillic on a Western Windows) arrives as C:\\Users\\?????\\board, and the job said
    "no such file (save the project first)" for a saved project. '?' cannot be in a Windows file name, so the one file
    that matches is the project."""
    with tempfile.TemporaryDirectory() as d:
        real = Path(d) / "\u041e\u043b\u044c\u0433\u0430" / "board"
        real.mkdir(parents=True)
        (real / "bom.csv").write_text("Designator,MPN\nR1,RC0603FR-0710KL\n", encoding="utf-8")
        asked = str(Path(d) / "?????" / "board" / "bom.csv")
        bom, pb = AM._bom_of(None, asked)
        assert Path(bom) == (real / "bom.csv").resolve() and pb is None
        try:                                            # two matches: say so, never guess
            (Path(d) / "\u041e\u043b\u044c\u0433\u0431" / "board").mkdir(parents=True)
            (Path(d) / "\u041e\u043b\u044c\u0433\u0431" / "board" / "bom.csv").write_text("x", encoding="utf-8")
            AM._bom_of(None, asked)
            raise AssertionError("an ambiguous path must not be guessed")
        except ValueError as e:
            assert "save the project" not in str(e), e


def test_release_anyway_for_a_bom_named_outside_the_ansi_code_page():
    """A project named in Cyrillic has its BOM at reports/project_<name>_<hash>.csv; result.ini (UTF-16) hands that
    to the dialog, but the dialog's Release anyway / Read with AI writes it back into request.ini with TIniFile in the
    ANSI code page, as reports/project_?????_<hash>.csv. The release then said the part "is not in the library plan
    any more" for a part held right there."""
    import json
    with tempfile.TemporaryDirectory() as d:
        cfg = {"workspace": Path(d) / "ws", "library": Path(d) / "lib", "name": "ecadlib", "port": 8765, "offline": True}
        ws = AM._workspace(cfg)
        real = "reports/project_Плата_0123abcd.csv"
        (ws.root / real).parent.mkdir(parents=True, exist_ok=True)
        (ws.root / real).write_text("Designator,MPN\nU1,XYZ123\n", encoding="utf-8")
        plan = ws._librarian_dir(ws._bom_path(real)) / "plan.json"
        plan.parent.mkdir(parents=True)
        plan.write_text(json.dumps([{"name": "XYZ123", "status": "held", "hold_reasons": ["files.symbol missing"]}]),
                        encoding="utf-8")
        res = AM.release_part(cfg, {"bom": "reports/project_?????_0123abcd.csv", "part": "XYZ123", "mpn": "XYZ123",
                                    "reason": "checked", "release_key": "x"})
        assert "not in the library plan" not in res["message"], res


def test_open_report_whose_path_the_dialog_wrote_with_question_marks():
    """The BOM check's "Open the full report" sends the report path back through request.ini (ANSI code page): the
    report of a project named in Cyrillic arrived as reports/triage_project_?????_<hash>_altium.html and the job said
    it is "not a report in the ecadlib workspace"."""
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d) / "ws"
        rep = ws / "reports" / "triage_project_Плата_0123abcd_altium.html"
        rep.parent.mkdir(parents=True)
        rep.write_text("<html></html>", encoding="utf-8")
        cfg = {"workspace": ws, "library": Path(d) / "lib"}
        opened = []
        res = AM.open_file(cfg, str(ws / "reports" / "triage_project_?????_0123abcd_altium.html"),
                           opener=lambda p: opened.append(p) or True)
        assert res["status"] == "ok" and opened, res
        # still only under the workspace or the library folder
        outside = Path(d) / "xЖ.html"
        outside.write_text("x", encoding="utf-8")
        assert AM.open_file(cfg, str(Path(d) / "x?.html"), opener=lambda p: opened.append(p) or True)["status"] == "error"
