"""Regression tests for the 2026-10-26 upkeep bug hunt (rebuild, ledger, change gate side files, packagers).
Nothing live: the offline demo library, built once per run.
    python3 -m tests.run_all test_hunt1026_upkeep"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_DEMO = {}


def _run(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_")) and k not in ("NEXAR_TOKEN",)}
    env.update(ECADLIB_HOME=str(Path(cwd) / "home"), NO_COLOR="1", ECADLIB_USER="Tester <tester@example.com>",
               PYTHONPATH=os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]))
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


def _demo():
    """A fresh copy of the offline demo workspace -> (work dir, library folder)."""
    if "dir" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-hunt-demo-"))
        r = _run(d, "demo", "-o", "ws")
        assert r.returncode == 0, r.stdout + r.stderr
        _DEMO["dir"] = d
    w = Path(tempfile.mkdtemp(prefix="ecadlib-hunt-"))
    shutil.copytree(_DEMO["dir"] / "ws", w / "ws")
    return w, w / "ws/librarian/demo_bom_demo"


def _ledger(lib):
    return [json.loads(x) for x in (lib / "library.ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


R_RECIPE = "models/PcbLib/R_0603_1608Metric.PcbLib.ecadbuild"
R_STEP = "models/PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603_1608Metric.step"
R_PAD1 = "PAD\t1\t-324803\t0\t314961\t374016"


def _edit_side_files(lib):
    """Hand edits of a released part's Altium build recipe (pad 1 moved and widened) and of its 3D model, the two files
    Altium's own rebuild (altium-pkg's RebuildWithAltium) makes the shipped .PcbLib from. -> released bytes."""
    rp, st = lib / R_RECIPE, lib / R_STEP
    released = {R_RECIPE: rp.read_bytes(), R_STEP: st.read_bytes()}
    t = rp.read_text(encoding="utf-8")
    assert R_PAD1 in t
    rp.write_text(t.replace(R_PAD1, "PAD\t1\t-424803\t0\t514961\t374016", 1), encoding="utf-8")
    s = st.read_text(encoding="utf-8")
    assert "(-0.8,-0.4125,0.45)" in s
    st.write_text(s.replace("(-0.8,-0.4125,0.45)", "(-0.8,-0.4125,9.45)"), encoding="utf-8")
    return released


# ------------------------------------------------------------------ upkeep-01: a released part's recipe / STEP edited
def test_upkeep01_altium_pkg_never_packs_an_edited_recipe_or_step_of_a_released_part():
    w, lib = _demo()
    released = _edit_side_files(lib)
    edited = {k: (lib / k).read_bytes() for k in released}
    r = _run(w, "altium-pkg", lib / "plan.json", "--out", "apkg")
    assert r.returncode == 0, r.stdout + r.stderr
    pkg = w / "apkg"
    assert (pkg / "PcbLib/R_0603_1608Metric.PcbLib.ecadbuild").read_bytes() == released[R_RECIPE]
    assert (pkg / "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603_1608Metric.step").read_bytes() == released[R_STEP]
    assert "-424803" not in (pkg / "ecadlib_build.txt").read_text(encoding="utf-8")
    for k, data in released.items():                       # the library has the released bytes back
        assert (lib / k).read_bytes() == data, k
    for k, data in edited.items():                         # and the edit is kept, with a ledger line
        sha = hashlib.sha256(data).hexdigest()
        assert (lib / ".ecadlib/blobs" / sha[:2] / sha).is_file(), k
    lines = [e for e in _ledger(lib) if e.get("event") == "overwritten" and e.get("part") == "RES-0603-10k"]
    assert lines, [(e["event"], e.get("part")) for e in _ledger(lib)][-8:]
    assert "R_0603_1608Metric.PcbLib.ecadbuild" in r.stdout + r.stderr


def test_upkeep01_altium_pkg_from_a_folder_refuses_an_edited_recipe_beside_a_released_file():
    w, lib = _demo()
    out = w / "copy"
    shutil.copytree(lib / "models", out)
    rp = out / "PcbLib/R_0603_1608Metric.PcbLib.ecadbuild"
    rp.write_text(rp.read_text(encoding="utf-8").replace(R_PAD1, "PAD\t1\t-424803\t0\t514961\t374016", 1), encoding="utf-8")
    r = _run(w, "altium-pkg", out, "--out", "apkg2", "--plan", lib / "plan.json")
    assert r.returncode == 2 and "R_0603_1608Metric.PcbLib.ecadbuild: RES-0603-10k" in r.stdout + r.stderr, r.stdout + r.stderr
    assert not (w / "apkg2/ecadlib_build.txt").is_file() or "-424803" not in (w / "apkg2/ecadlib_build.txt").read_text()


# ------------------------------------------------------------------ upkeep-02: rebuild --check said OK over hand edits
def test_upkeep02_rebuild_check_reports_files_changed_on_disk_since_the_build():
    w, lib = _demo()
    assert _run(w, "rebuild", lib, "--check").returncode == 0
    k = lib / "models/KiCad/R_0603_1608Metric.kicad_mod"
    k.write_text(k.read_text(encoding="utf-8").replace("(size 0.8 0.95)", "(size 1.8 0.95)", 1), encoding="utf-8")
    r = _run(w, "rebuild", lib, "--check", "--json")
    rep = json.loads(r.stdout)
    assert r.returncode == 1 and not rep["ok"], r.stdout
    assert any(x["result"] == "DRIFT" and x["file"] == "models/KiCad/R_0603_1608Metric.kicad_mod" for x in rep["rows"]), rep["rows"]


def test_upkeep02_rebuild_check_covers_recipes_and_3d_models():
    w, lib = _demo()
    _edit_side_files(lib)
    r = _run(w, "rebuild", lib, "--check", "--json")
    rep = json.loads(r.stdout)
    assert r.returncode == 1, r.stdout + r.stderr
    drifted = {x["file"] for x in rep["rows"] if x["result"] == "DRIFT"}
    assert {R_RECIPE, R_STEP} <= drifted, drifted
