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
