"""The Fusion / EAGLE and EasyEDA Pro exports were removed after 0.3.1 (#50). Run: python3 -m tests.run_all test_removed_exports"""
import contextlib, importlib.util, io

from ecadlib import cli, rules as RU


def test_removed_commands_say_where_the_last_version_is():
    for cmd in ("eagle-export", "easyeda-export"):
        assert cmd not in cli.COMMANDS
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert cli.main([cmd, "plan.json"]) == 2
        assert "removed after 0.3.1" in err.getvalue() and 'pip install "ecadlib==0.3.1"' in err.getvalue(), err.getvalue()


def test_the_modules_are_gone():
    for mod in ("eagle_export", "easyeda_pro_export", "cad_model", "hub_tools"):
        assert importlib.util.find_spec(f"ecadlib.{mod}") is None, mod


def test_an_old_ecadlib_toml_with_the_release_keys_still_loads():
    RU.validate({"release": {"parity": "warn", "eagle": "hold", "easyeda": "warn"}})
    assert "eagle" not in RU.DEFAULTS["release"] and "easyeda" not in RU.DEFAULTS["release"]
