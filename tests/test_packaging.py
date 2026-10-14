"""Packaging checks (offline, fast): the version is single-sourced and every runtime data file
inside ecadlib/ is shipped by [tool.setuptools.package-data]. Run:  python3 -m tests.test_packaging"""
import contextlib, io, re
from pathlib import Path

import ecadlib
from ecadlib import cli

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "ecadlib"


def _pyproject():
    try:
        import tomllib
    except ImportError:                                  # Python 3.10
        try:
            import tomli as tomllib
        except ImportError:
            return None
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _data_files():
    """Every file under ecadlib/ that is not Python source or bytecode."""
    return {p.relative_to(PKG).as_posix() for p in PKG.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".py", ".pyc", ".pyo")}


def test_version_single_sourced():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?", ecadlib.__version__), ecadlib.__version__
    cfg = _pyproject()
    if cfg is None:
        assert re.search(r'(?m)^dynamic\s*=\s*\["version"\]', (ROOT / "pyproject.toml").read_text())
        print("skip  tomllib/tomli missing: only the text check ran")
        return
    assert "version" in cfg["project"].get("dynamic", []) and "version" not in cfg["project"], cfg["project"]
    assert cfg["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "ecadlib.__version__"}


def test_cli_version():
    for flag in ("--version", "-V"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cli.main([flag]) == 0
        assert buf.getvalue().strip() == f"ecadlib {ecadlib.__version__}", buf.getvalue()


def test_changelog_has_current_version():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "## [Unreleased]" in text
    assert re.search(rf"(?m)^## \[{re.escape(ecadlib.__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text), \
        f"CHANGELOG.md has no '## [{ecadlib.__version__}] - YYYY-MM-DD' section"


def test_package_data_covers_runtime_files():
    cfg = _pyproject()
    if cfg is None:
        print("skip  tomllib/tomli missing")
        return
    patterns = cfg["tool"]["setuptools"]["package-data"]["ecadlib"]
    shipped, unused = set(), []
    for pat in patterns:
        hits = {p.relative_to(PKG).as_posix() for p in PKG.glob(pat) if p.is_file()}
        if not hits:
            unused.append(pat)
        shipped |= hits
    missing = sorted(_data_files() - shipped)
    assert not missing, f"data files not matched by [tool.setuptools.package-data] (they won't be in the wheel): {missing}"
    assert not unused, f"package-data patterns that match nothing: {unused}"


def test_known_runtime_files_present():
    """Files the code opens via Path(__file__) at runtime."""
    for rel in ("package_schema.json", "assets/pcblib_template.json.gz", "ui/static/index.html"):
        assert (PKG / rel).is_file(), rel
    assert any((PKG / "package_specs").glob("*.json"))


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
