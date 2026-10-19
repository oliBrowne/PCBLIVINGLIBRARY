"""The Altium DelphiScript units are real .pas files (ecadlib/altium_scripts), shipped as package data."""
import re

from ecadlib import __version__, altium_forms as AF, altium_menu as AM, altium_native as AN


def test_units_are_files_with_crlf_and_the_version():
    for name, text in ((AM.UNIT, AM.pas_text()), (AF.DIALOG_UNIT, AF.dialog_pas_text()), (AN.UNIT, AN.pas_text())):
        src = (AN.SCRIPTS / name).read_text(encoding="utf-8")
        assert "\r" not in src, f"{name}: keep the source LF (Altium gets CRLF from script_text)"
        assert "\r\n" in text and "\n" not in text.replace("\r\n", ""), name
        assert "@VERSION@" not in text, name
        assert text.replace("\r\n", "\n") == src.replace("@VERSION@", __version__), name
    assert __version__ in AM.pas_text() and __version__ in AF.dialog_pas_text()


def test_dialog_unit_matches_the_form():
    """The .dfm is generated from ROWS and the colour constants; the .pas file must use the same ones."""
    pas, dfm = AF.dialog_pas_text(), AF.dialog_dfm_text()
    rows = {int(n) for n in re.findall(r"EcadlibDialog\.Row(\d+)\.", pas)}
    assert rows == set(range(1, AF.ROWS + 1)), rows
    for i in range(1, AF.ROWS + 1):
        assert f"object Row{i}: " in dfm, i
    for c in (AF.TEXT, AF.GREEN, AF.RED, AF.DIM, AF.BLUE, AF.BLUE_OFF):
        assert c in pas, c
    assert str(AF.WIDTH - 40) in pas
