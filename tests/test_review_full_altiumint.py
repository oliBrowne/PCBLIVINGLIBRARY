"""Regression tests from the full review of the Altium integration (altium365, altium_pkg, altium_menu).
Run:  python3 -m tests.test_review_full_altiumint"""
import re

from ecadlib import altium_menu as M, altium_pkg as P


def _loops_over_installed(pas):
    """Every loop that indexes InstalledLibraryPath(i) must count InstalledLibraryCount (TR0136: AvailableLibraryCount
    also counts project and search-path libraries, so InstalledLibraryPath(i) runs past the installed list)."""
    assert "InstalledLibraryPath(i)" in pas
    assert "AvailableLibraryCount" not in pas, "AvailableLibraryCount paired with InstalledLibraryPath"
    loops = [m.group(1) for m in re.finditer(r"For i := ([^\r\n]+) Do", pas) if "IntegratedLibraryManager" in m.group(1)]
    assert loops and all("InstalledLibraryCount" in x for x in loops), loops


def test_menu_script_counts_installed_libraries():
    pas = M.pas_text()
    _loops_over_installed(pas)
    from ecadlib import altium_forms as AF                   # OpenDialog lives in the dialog unit
    from ecadlib import altium_native as AN                  # BuildLibraries lives in ecadlib_build.pas
    other = M.unit_names(AF.dialog_pas_text()) | M.unit_names(AN.pas_text())
    assert M.pas_problems(pas, known=other) == []            # InstalledLibraryCount is in the documented allow-list


def test_install_script_counts_installed_libraries():
    _loops_over_installed(P.install_script("ecadlib", ["ecadlib.DbLib"], "ecadlib.LibPkg", ["SchLib\\R.SchLib"]))


def test_install_script_quotes_library_name():
    """A library name with ' or } (altium-pkg --name, or name= in ecadlib_menu.ini) must not break the script:
    a lone ' ended the Pascal string literal, a } ended the header comment early."""
    for name in ("Bob's parts", "lib}x"):
        pas = P.install_script(name, [f"{name}.DbLib"], f"{name}.LibPkg", ["SchLib\\R.SchLib"])
        toks = M.pas_tokens(pas)                            # raised "unterminated string" for Bob's parts
        assert toks[0][:2] == ("word", "Var"), toks[:3]      # the comment ends where it should
        strs = [t[1] for t in toks if t[0] == "str"]
        assert f"{name}: database library" in strs and f"{name}: uninstalled" in strs
        assert any(f"open {name}.LibPkg, right-click" in s for s in strs)


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_full_altiumint"]))
