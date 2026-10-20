"""The menu checks that Altium really installed the library it just built (real AD26, 2026-10-20): a part added from
the menu said "Done", but the Components panel could not find it until ecadlib.DbLib was installed by hand, and
nothing recorded whether the install had happened. The DelphiScript runs in the small interpreter of
test_review_1018_build_fail with Altium's library manager faked. Run:
    python3 -m tests.run_all test_menu_install_check"""
from ecadlib import altium_forms as AF
from tests.fixtures_lite import lite
from tests.test_review_1018_build_fail import Manager, _library, _menu


class Ignores(Manager):
    """Altium takes the install call and installs nothing."""

    def installlibrary(self, p):
        self.calls.append(("install", p))


class EnvPaths(Manager):
    """Altium lists an installed library under %UserProfile% (as its registry entry does)."""

    def __init__(self, home):
        super().__init__()
        self.home = home

    def installedlibrarypath(self, i):
        p = self.installed[i]
        return "%UserProfile%" + p[len(self.home):] if p.upper().startswith(self.home.upper()) else p


@lite
def test_an_install_altium_ignores_is_reported_with_the_manual_step(tmp_path):
    lib = _library(tmp_path)
    mgr, shown = Ignores(), []
    pas, dblib = _menu(lib, "ok", mgr, shown)
    extra = pas.call("Reinstall", "job")
    assert ("install", dblib) in mgr.calls, mgr.calls
    assert "Not installed: " + dblib in extra and "Installed again" not in extra, extra
    assert "File-based Libraries Preferences" in extra, extra
    pas.call("ShowResult", "ecadlib: add NE555DR", "job", extra)
    assert [k for k, _ in shown] == ["error"] and dblib in shown[0][1], shown


@lite
def test_a_good_install_says_so(tmp_path):
    lib = _library(tmp_path)
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "ok", mgr, shown)
    extra = pas.call("Reinstall", "job")
    assert "Installed in Altium: " + dblib in extra and "Not installed" not in extra, extra
    pas.call("ShowResult", "ecadlib: add NE555DR", "job", extra)
    assert [k for k, _ in shown] == ["message"], shown


@lite
def test_a_library_altium_lists_under_userprofile_counts_as_installed(tmp_path):
    lib = _library(tmp_path)
    home = str(tmp_path).replace("/", "\\")
    mgr, shown = EnvPaths(home), []
    pas, dblib = _menu(lib, "ok", mgr, shown)
    mgr.installed.append(dblib)                          # installed by an earlier run
    extra = pas.call("Reinstall", "job")
    assert mgr.calls[:2] == [("uninstall", dblib), ("install", dblib)], mgr.calls
    assert "Installed in Altium: " + dblib in extra and "Not installed" not in extra, extra


def test_the_dialog_shows_a_failed_install():
    dialog = AF.dialog_pas_text()
    assert "Pos('Not installed:', Extra) > 0" in dialog
