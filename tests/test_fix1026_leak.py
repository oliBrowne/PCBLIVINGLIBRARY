"""R-G2-8: the test suite left its temp folders behind (~29 GB in one container's /tmp after repeated runs: ecadlib-cc-,
tmp*, "ecadlib menu ", ecadlib-test-home-, ecadlib-pkg-, ...). A run now keeps every temp folder under one root of
its own (tests/__init__.py) that goes when the run ends, and the worst offenders remove theirs when each test ends
(tests.fixtures_lite.scratch / cleans)."""
import os, subprocess, sys, tempfile
from pathlib import Path

from tests import fixtures_lite as FL

ROOT = Path(__file__).resolve().parent.parent

# a representative set of the worst offenders, by the prefix they leaked under
GUARD = [
    ("test_change_control", "test_kicad_commit_that_only_added_sim_pins_passes"),     # ecadlib-cc-demo-, ecadlib-cc-
    ("test_change_control", "test_same_part_from_kicad_and_altium_has_the_same_identity"),  # ecadlib-cc-x-
    ("test_altium_menu", "test_build_writes_well_formed_files"),                      # "ecadlib menu "
    ("test_tool_packages", "test_pcm_metadata_checks"),                               # ecadlib-pkg-
    ("test_easy_setup", "*"),                                                         # ecadlib-setup-
    ("test_change_control_round6", "*"),                                              # ecadlib-r6-, ecadlib-r5-
    ("test_kicad_menu", "*"),                                                         # ecadlib-kmenu-
    ("test_review_1018_kicad", "test_kicad10_footprint_points_are_not_written_for_kicad8_or_7"),  # ecadlib-1018-kicad-
]

RUNNER = r"""
import importlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
import tests                                   # the run's temp root and ECADLIB_HOME
tempfile.mkdtemp()                             # a bare mkdtemp (the tmp* folders) goes with the root
bad = []
for spec in sys.argv[2:]:
    m, name = spec.split(":")
    mod = importlib.import_module("tests." + m)
    fns = [f for n, f in vars(mod).items() if n.startswith("test_") and callable(f)] if name == "*" else [getattr(mod, name)]
    for fn in fns:
        try:
            fn()
        except Exception as e:
            bad.append(f"{m}.{fn.__name__}: {e!r}")
print("\n".join(bad) or "all passed")
sys.exit(1 if bad else 0)
"""


def test_a_test_run_leaves_nothing_in_the_temp_folder():
    with tempfile.TemporaryDirectory(prefix="ecadlib-leak-guard-") as d:
        env = {**os.environ, "TMPDIR": d, "TEMP": d, "TMP": d}
        env.pop("ECADLIB_HOME", None)
        r = subprocess.run([sys.executable, "-c", RUNNER, str(ROOT), *[f"{m}:{n}" for m, n in GUARD]], env=env,
                           cwd=ROOT, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        left = sorted(os.listdir(d))
        assert left == [], f"the run left {len(left)} entries in its temp folder: {left[:20]}"


def test_the_run_keeps_its_temp_folders_under_one_root_of_its_own():
    import tests
    root = Path(tests.TEMP_ROOT)
    assert root.name.startswith("ecadlib-tests-") and root.is_dir()
    assert Path(tempfile.gettempdir()) == root
    assert Path(os.environ["ECADLIB_HOME"]).parent == root
    for k in ("TMPDIR", "TEMP", "TMP"):                 # child `python -m ecadlib` processes write under it too
        assert os.environ[k] == str(root)


def test_scratch_folders_go_when_the_test_ends_and_kept_ones_stay_for_the_run():
    made = {}

    def test_one():
        made["own"] = FL.scratch("ecadlib-leak-")
        made["kept"] = FL.scratch("ecadlib-leak-kept-", keep=True)
        (made["own"] / "f.txt").write_text("x")
        os.chmod(made["own"] / "f.txt", 0o444)          # read-only (Windows refuses to delete it as is)
        assert made["own"].is_dir()

    wrapped = FL.cleans(test_one)
    assert wrapped.__name__ == "test_one"
    wrapped()
    assert not made["own"].exists() and made["kept"].is_dir()
    FL.rmtree(made["kept"])
    assert not made["kept"].exists()

    def test_fails():
        made["fail"] = FL.scratch()
        raise RuntimeError("boom")
    try:
        FL.cleans(test_fails)()
    except RuntimeError:
        pass
    else:
        raise AssertionError("cleans() swallowed the failure")
    assert not made["fail"].exists()                    # removed when the test fails too


def test_lite_tmp_path_also_removes_scratch_folders():
    made = {}

    @FL.lite
    def test_x(tmp_path):
        made["d"] = FL.scratch()
    test_x()
    assert not made["d"].exists()


def test_clean_all_wraps_only_the_modules_own_plain_test_functions():
    def test_a():
        pass

    def test_b(x):
        pass

    def helper():
        pass
    ns = {"__name__": test_a.__module__, "test_a": test_a, "test_b": test_b, "helper": helper, "test_x": len}
    FL.clean_all(ns)
    assert ns["test_a"] is not test_a and ns["test_a"].__name__ == "test_a"
    assert ns["test_b"] is test_b and ns["helper"] is helper and ns["test_x"] is len
    once = ns["test_a"]
    FL.clean_all(ns)
    assert ns["test_a"] is once                          # not wrapped twice
