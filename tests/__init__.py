import atexit, os, tempfile

# Every temp folder of a run lives under one root of its own, removed when the run ends (R-G2-8: the suite left ~29 GB
# of folders in /tmp). tempfile and the TMPDIR / TEMP / TMP of child processes (`python -m ecadlib ...`) point at it, so
# a bare mkdtemp() in a test, or one in ecadlib a test does not clean up, goes too. Each process that imports tests
# (python -m tests.run_all, a pytest-xdist worker) has its own. ECADLIB_KEEP_TEST_TMP=1 keeps it, to look for leaks.
TEMP_ROOT = tempfile.mkdtemp(prefix="ecadlib-tests-")
# The librarian's KiCad index starts from the copy shipped with ecadlib (assets/kicad_library_index.json.gz); the tests
# give it a fake gitlab.com instead, so it is built from that (and child processes inherit this too).
# tests/test_kicad_index_snapshot.py turns it back on where it tests the shipped copy.
os.environ["ECADLIB_KICAD_SNAPSHOT"] = "0"
_OWNER = os.getpid()


def _remove_root():
    if os.getpid() != _OWNER or os.environ.get("ECADLIB_KEEP_TEST_TMP"):      # a forked child must not remove it
        return
    try:
        if os.path.abspath(os.getcwd()).startswith(os.path.abspath(TEMP_ROOT)):   # Windows cannot remove the cwd
            os.chdir(os.path.dirname(TEMP_ROOT))
    except OSError:
        pass
    from tests.fixtures_lite import rmtree
    rmtree(TEMP_ROOT)


atexit.register(_remove_root)
tempfile.tempdir = TEMP_ROOT
for _k in ("TMPDIR", "TEMP", "TMP"):
    os.environ[_k] = TEMP_ROOT

# Keys and settings stored by `ecadlib keys` / the setup page live in a per-user folder (ecadlib.userconfig); the tests
# use an empty one of their own, so a developer's stored keys never change a result.
os.environ["ECADLIB_HOME"] = tempfile.mkdtemp(prefix="ecadlib-test-home-")

# The undocumented EasyEDA / JLCPCB endpoints ([providers] unofficial) are on by default again since 2026-10-20 (they
# were opt-in from 2026-10-19). Most tests were written against fixtures from those sources, so they run with them on
# whatever ships; tests/test_unofficial_default.py checks the shipped default and its effect.
from ecadlib import rules as _RU                       # noqa: E402

SHIPPED_UNOFFICIAL = _RU.DEFAULTS["providers"]["unofficial"]
_RU.DEFAULTS["providers"]["unofficial"] = True

# A datasheet link a test's fake server refuses must stay refused: the suite never tries the real browser ways
# ([datasheet] download, ecadlib.pdf_fetch) or opens a browser. tests/test_pdf_fetch.py turns them on where it tests them.
os.environ["ECADLIB_DOWNLOAD"] = "plain"
os.environ["ECADLIB_NO_HANDOFF"] = "1"
