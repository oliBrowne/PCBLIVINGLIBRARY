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


# ------------------------------------------------------------------ upkeep-04: upkeep missed an unsettled hand edit
def test_upkeep04_upkeep_reports_a_hand_edit_of_a_released_part_no_writer_has_settled():
    w, lib = _demo()
    args = ("upkeep", lib / "plan.json", "--checks", "changes", "--state", "st", "-o", "up")
    assert _run(w, *args).returncode == 0
    plan_before = (lib / "plan.json").read_bytes()
    k = lib / "models/KiCad/R_0603_1608Metric.kicad_mod"
    edited = k.read_text(encoding="utf-8").replace("(size 0.8 0.95)", "(size 1.8 0.95)", 1)
    k.write_text(edited, encoding="utf-8")
    _edit_side_files(lib)
    r = _run(w, *args)
    assert r.returncode == 1, r.stdout + r.stderr
    rep = json.loads((w / "up.json").read_text(encoding="utf-8"))
    got = {(c["kind"], c["item"]) for s in rep["checks"] for c in s["changes"]}
    assert ("edited, not settled", "RES-0603-10k") in got and ("edited, not settled", "RES-0603-4.7k") in got, got
    det = " ".join(c["detail"] for s in rep["checks"] for c in s["changes"])
    assert "0.8 x 0.95 -> 1.8 x 0.95" in det and "R_0603_1608Metric.PcbLib.ecadbuild" in det, det
    assert k.read_text(encoding="utf-8") == edited and (lib / "plan.json").read_bytes() == plan_before   # reports only
    assert _run(w, *args).returncode == 0                  # reported once


# ------------------------------------------------------------------ upkeep-05: PCM identifiers of non-ASCII libraries
def test_upkeep05_pcm_default_identifier_keeps_non_ascii_libraries_apart():
    from ecadlib import kicad_pcm as KP
    assert KP.default_identifier("my lib") == "local.ecadlib.my-lib"            # ASCII names: as before
    assert KP.default_identifier("ecadlib") == "local.ecadlib.ecadlib"
    assert KP.default_identifier("Bibliothèque") == "local.ecadlib.Bibliotheque"
    ids = [KP.default_identifier(n) for n in ("部品", "抵抗", "ünï", "n", "Ω-parts", "-parts", "電源 IC", "IC")]
    assert len(set(ids)) == len(ids), ids                 # one KiCad package each: the same id replaces the other
    for i in ids:
        assert KP.ID_RE.match(i), i


# ------------------------------------------------------------------ upkeep-06: kicad-pcm packed unchecked SPICE files
def test_upkeep06_pcm_from_an_export_folder_checks_the_sim_folder_too():
    w, lib = _demo()
    r = _run(w, "kicad-export", lib / "plan.json", "-o", "kx", "--name", "m")
    assert r.returncode == 0, r.stdout + r.stderr
    sim = w / "kx/m.sim"
    sim.mkdir()
    (sim / "AO3401A.lib").write_text(".model AO3401A PMOS (VTO=-9)\n", encoding="utf-8")   # put in by hand
    r = _run(w, "kicad-pcm", w / "kx", "--out", "pcm")
    assert r.returncode == 2 and "m.sim/AO3401A.lib" in r.stdout + r.stderr, r.stdout + r.stderr
    assert not list((w / "pcm").glob("*.zip")) if (w / "pcm").is_dir() else True


# ------------------------------------------------------------------ upkeep-03: a ledger write cut short
def _ledger_lib(d):
    from ecadlib import ledger as LG
    lib = Path(d)
    lg = LG.Ledger(lib)
    lg.append({"event": "release", "part": "A", "revision": 1, "digest": "a1"})
    lg.append({"event": "release", "part": "A", "revision": 2, "digest": "a2"})
    return lib, lib / LG.LEDGER


def test_upkeep03_a_torn_last_ledger_line_is_set_aside_not_a_dead_library():
    from ecadlib import ledger as LG
    with tempfile.TemporaryDirectory() as d:
        lib, p = _ledger_lib(d)
        with open(p, "a", encoding="utf-8") as f:            # killed while writing revision 3
            f.write('{"event": "release", "part": "A", "revisi')
        lg = LG.Ledger(lib)
        assert lg.head("A")["revision"] == 2                 # the cut line never counted
        lg.append({"event": "release", "part": "A", "revision": 3, "digest": "a3"})
        lines = p.read_text(encoding="utf-8").splitlines()
        assert [json.loads(x)["revision"] for x in lines] == [1, 2, 3], lines
        assert LG.Ledger(lib).head("A")["revision"] == 3
        torn = list((lib / LG.STORE).rglob("*torn*"))
        assert torn and b'"revisi' in torn[0].read_bytes(), torn       # its bytes are kept


def test_upkeep03_a_ledger_without_a_final_newline_gets_one_before_the_next_line():
    from ecadlib import ledger as LG
    with tempfile.TemporaryDirectory() as d:
        lib, p = _ledger_lib(d)
        p.write_bytes(p.read_bytes().rstrip(b"\n"))        # an editor that drops the final newline
        LG.Ledger(lib).append({"event": "release", "part": "A", "revision": 3, "digest": "a3"})
        assert [e["revision"] for e in LG.Ledger(lib).releases("A")] == [1, 2, 3]


def test_upkeep03_unicode_line_separators_in_a_reason_keep_the_ledger_readable():
    """ensure_ascii=False writes U+2028, U+0085 and \\x1c as they are; str.splitlines() split a line there."""
    from ecadlib import ledger as LG
    with tempfile.TemporaryDirectory() as d:
        lib, p = _ledger_lib(d)
        why = "pad 3 moved\x85to the datasheet's\x1cposition, Bibliothèque"
        LG.Ledger(lib).append({"event": "release", "part": "A", "revision": 3, "digest": "a3", "reason": why})
        assert LG.Ledger(lib).head("A")["reason"] == why


def test_upkeep03_a_torn_line_cut_inside_a_utf8_character():
    from ecadlib import ledger as LG
    with tempfile.TemporaryDirectory() as d:
        lib, p = _ledger_lib(d)
        with open(p, "ab") as f:
            f.write('{"event": "release", "part": "A", "by": "Ann Mü'.encode("utf-8")[:-1])
        assert LG.Ledger(lib).head("A")["revision"] == 2


def test_upkeep03_a_broken_line_in_the_middle_still_stops():
    from ecadlib import ledger as LG
    with tempfile.TemporaryDirectory() as d:
        lib, p = _ledger_lib(d)
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text(lines[0][:30] + "\n" + lines[1] + "\n", encoding="utf-8")
        try:
            LG.Ledger(lib).lines()
            raise AssertionError("a broken line inside the ledger must stop")
        except LG.LedgerError as e:
            assert ":1: a broken ledger line" in str(e)
        p.write_text("[1, 2]\n" + lines[1] + "\n", encoding="utf-8")   # JSON, but not a ledger line
        try:
            LG.Ledger(lib).lines()
            raise AssertionError("a line that is not an object must stop")
        except LG.LedgerError as e:
            assert ":1:" in str(e)
