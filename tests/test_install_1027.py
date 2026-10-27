"""New-user install audit (2026-10-27): the one-line install of every optional package, the help's starting
commands, and the docs on where keys are kept.  Run:  python3 -m tests.test_install_1027"""
import re
from pathlib import Path

from ecadlib import cli, doctor as DR
from ecadlib.rules import toml_module

ROOT = Path(__file__).resolve().parents[1]


def _optional(missing):
    pk = DR.check_packages(find_spec=lambda m: None if m in missing else object(), which=lambda t: f"/bin/{t}")
    return [c for c in pk if c["check"].startswith("optional ")]


def test_doctor_offers_the_all_extra_when_several_optional_packages_are_missing():
    warn = [c for c in _optional({"PIL", "pdfplumber", "curl_cffi"}) if c["level"] == "WARN"]
    assert [c["check"] for c in warn] == ["optional pillow", "optional pdfplumber", "optional curl_cffi"]
    assert 'pip install "ecadlib[all]"' in warn[0]["hint"] and warn[0]["hint"].startswith("pip install pillow")
    assert all("ecadlib[all]" not in c["hint"] for c in warn[1:])      # said once, not on every line
    assert [c["hint"] for c in warn[1:]] == ["pip install pdfplumber", "pip install curl_cffi"]


def test_doctor_keeps_the_single_package_hint_when_only_one_is_missing():
    warn = [c for c in _optional({"PIL"}) if c["level"] == "WARN"]
    assert [c["hint"] for c in warn] == ["pip install pillow"]


def test_the_all_extra_installs_every_optional_package_doctor_checks():
    tm = toml_module()
    if tm is None:
        print("skip (no TOML reader)")
        return
    extras = tm.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    names = {re.split(r"[<>=\[; ]", d, maxsplit=1)[0].lower() for d in extras["all"]}
    assert {pip for _, pip, _ in DR.OPTIONAL} <= names, sorted({pip for _, pip, _ in DR.OPTIONAL} - names)


def test_help_marks_where_to_start():
    doc = cli.__doc__
    start, rest = doc.index("Start here:"), doc.index("Every other command:")
    assert start < rest
    first = re.findall(r"^    ecadlib ([a-z0-9-]+)", doc[start:rest], re.M)
    assert first == ["setup", "start", "demo", "init", "keys", "nexar"], first
    listed = re.findall(r"^    ecadlib ([a-z0-9-]+)", doc, re.M)
    assert len(listed) == len(set(listed))                              # each command once


def test_key_docs_say_mac_and_linux_keys_are_not_encrypted():
    text = (ROOT / "docs" / "api-keys.md").read_text(encoding="utf-8")
    for system in ("macOS", "Linux"):
        row = next(ln for ln in text.splitlines() if ln.startswith(f"| {system} "))
        assert "not encrypted" in row, row


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
