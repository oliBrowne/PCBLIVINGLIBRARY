"""Regression tests from the full review of the Altium integration (altium365, a365_sync, altium_pkg, altium_menu).
Run:  python3 -m tests.test_review_full_altiumint"""
import json, re, tempfile
from pathlib import Path

from ecadlib import a365_sync as S, altium365 as A, altium_menu as M, altium_pkg as P, rules as RU
from tests.test_a365_sync import FakeA365, WS, node


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
    assert M.pas_problems(pas) == []                         # InstalledLibraryCount is in the documented allow-list


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


def test_push_keeps_pulled_parameter_names():
    """A pulled component whose parameter is named like an ecadlib name the rules rename ("Supplier 1" -> "Supplier"):
    pull then push with nothing edited renamed the parameter in the workspace."""
    rules = RU.load({"parameters": {"names": {"Supplier 1": "Supplier"}}})
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text("[]")
        ws = FakeA365([node("PART9", {"Supplier 1": "Digikey", "Value": "10k"})])
        S.run("pull", plan, ws, WS, models=False, rules=rules, log=lambda *a: None)
        items = json.loads(plan.read_text())
        assert A.push(items, ws, WS, rules=rules)[0]["action"] == "up_to_date"
        assert ws.nodes[0]["details"]["parameters"] == [{"name": "Supplier 1", "value": "Digikey"},
                                                        {"name": "Value", "value": "10k"}]
        items[0]["parameters"][1]["value"] = "10k 1%"          # a real local edit still goes out, names kept
        assert A.push(items, ws, WS, rules=rules)[0]["action"] == "update_parameters"
        assert ws.nodes[0]["details"]["parameters"] == [{"name": "Supplier 1", "value": "Digikey"},
                                                        {"name": "Value", "value": "10k 1%"}]


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_full_altiumint"]))
