"""Regression tests for the 2026-10-20 independent testing pass, change-control area (T-ids from
/mnt/project-files/tests/2026-10-20_independent_test/REPORT.md). Nothing live.
    python3 -m tests.run_all test_indtest_change"""
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

from ecadlib import release_check as RC

REPO = Path(__file__).resolve().parents[1]


def _run(cwd, home, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_")) and k not in ("NEXAR_TOKEN",)}
    env.update(ECADLIB_HOME=str(home), NO_COLOR="1", ECADLIB_USER="Tester <tester@example.com>",
               PYTHONPATH=os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]))
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


# ------------------------------------------------------------------ T009: a reject without a policy crashed the report
def test_t009_report_takes_columns_from_every_row():
    with tempfile.TemporaryDirectory() as d:
        row = dict.fromkeys(("symbol_source", "footprint_source", "height_mm", "failures", "warnings", "allowed"), "")
        rows = [dict(row, name="A", status="ready", result="ready"),
                dict(row, name="B", status="awaiting_approval", result="ready", approval="rejected by Bob")]
        RC.write_report(rows, Path(d) / "release_check")
        csv = (Path(d) / "release_check.csv").read_text(encoding="utf-8-sig")
        assert "approval" in csv.splitlines()[0] and "rejected by Bob" in csv, csv


def test_t009_release_check_after_reject_without_policy():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        home = d / "home"
        assert _run(d, home, "demo", "-o", "ws").returncode == 0
        plan = "ws/librarian/demo_bom_demo/plan.json"
        r = _run(d, home, "approve", plan, "reject", "--part", "AO3401A", "-m", "pin 3 is the drain", "--user", "Bob <bob@x.com>")
        assert r.returncode == 0, r.stdout + r.stderr
        rc = _run(d, home, "release-check", plan)
        out = rc.stdout + rc.stderr
        assert "internal error" not in out and "not in fieldnames" not in out, out
        assert rc.returncode == 2, (rc.returncode, out)                  # CH340C has no symbol: held
        assert "AO3401A" in (d / "ws/librarian/demo_bom_demo/release_check.csv").read_text(encoding="utf-8-sig")


def _ledger(lib):
    return [json.loads(x) for x in (lib / "library.ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def _demo(d):
    d = Path(d)
    assert _run(d, d / "home", "demo", "-o", "ws").returncode == 0
    return d / "ws/librarian/demo_bom_demo"


# ------------------------------------------------------------------ T002: --refresh / rebuild overwrote a hand edit unrecorded
def test_t002_refresh_and_rebuild_keep_a_hand_edit_of_a_released_part():
    with tempfile.TemporaryDirectory() as d:
        lib = _demo(d)
        kmod = lib / "models/KiCad/R_0603_1608Metric.kicad_mod"
        released = kmod.read_text(encoding="utf-8")
        assert "(size 0.8 0.95)" in released
        for size, how in (("0.9", ("librarian", "ws/uploads/demo/demo_bom.csv", "-o", lib, "--offline", "--sources",
                                   "standard", "--refresh", "RES-0603-10k")), ("1.1", ("rebuild", lib))):
            kmod.write_text(released.replace("(size 0.8 0.95)", f"(size {size} 0.95)", 1), encoding="utf-8")
            sha = hashlib.sha256(kmod.read_bytes()).hexdigest()
            n = len(_ledger(lib))
            r = _run(d, Path(d) / "home", *how)
            assert r.returncode in (0, 2), r.stdout + r.stderr
            assert "change hold  RES-0603-10k" in r.stdout, r.stdout
            assert (lib / ".ecadlib/blobs" / sha[:2] / sha).is_file(), how      # the edited bytes are kept
            assert any(e.get("event") == "hold" and e.get("part") == "RES-0603-10k" and
                       any(f"0.8 x 0.95 -> {size} x 0.95 mm" in c["text"] for c in e.get("changes") or [])
                       for e in _ledger(lib)[n:]), how
            assert "(size 0.8 0.95)" in kmod.read_text(encoding="utf-8")       # the released revision stays on disk


# ------------------------------------------------------------------ T011: resolve choose rewrote a file another released part uses
def test_t011_chosen_footprint_gets_its_own_file():
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "D1,1,RED,LED_0603_1608Metric,Hubei KENTO Elec,KT-0603R,C2286,LED red 0603,https://example.com/r.pdf\n"
           "D2,1,GREEN,LED_0603_1608Metric,Hubei KENTO Elec,KT-0603G,C2287,LED green 0603,https://example.com/g.pdf\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "bom.csv").write_text(bom, encoding="utf-8")
        assert _run(d, d / "home", "librarian", "bom.csv", "-o", "lib", "--offline", "--sources", "standard").returncode == 0
        lib = d / "lib"
        shared = lib / "models/KiCad/LED_0603_1608Metric.kicad_mod"
        before = shared.read_bytes()
        r = _run(d, d / "home", "resolve", "lib/plan.json", "--part", "KT-0603R", "--offline", "choose", "footprint",
                 "spec:LED_0805_2012Metric")
        assert r.returncode == 0 and "is another part's, with other pads" in r.stdout, r.stdout + r.stderr
        items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        red, green = items["KT-0603R"]["footprint"], items["KT-0603G"]["footprint"]
        assert red["ref"] == "LED_0603_1608Metric_LED_0805_2012Metric" and red["bom_ref"] == "LED_0603_1608Metric", red
        assert red["file"] != green["file"] and green["kicad_mod"] == "models/KiCad/LED_0603_1608Metric.kicad_mod"
        assert shared.read_bytes() == before                                  # KT-0603G keeps its released pads
        assert [e["revision"] for e in _ledger(lib) if e.get("part") == "KT-0603G" and e["event"] == "release"] == [1]
        assert [e["revision"] for e in _ledger(lib) if e.get("part") == "KT-0603R" and e["event"] == "release"] == [1, 2]
        assert _run(d, d / "home", "librarian", "bom.csv", "-o", "lib", "--offline", "--sources", "standard").returncode == 0
        again = {i["name"]: i["footprint"] for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        assert again["KT-0603R"]["file"] == red["file"] and again["KT-0603G"]["file"] == green["file"]   # a re-run keeps both


# ------------------------------------------------------------------ T042: part approve released an unreadable .kicad_mod
def test_t042_unreadable_kicad_mod_fails_the_release_check():
    with tempfile.TemporaryDirectory() as d:
        lib = _demo(d)
        items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        kmod = lib / "models/KiCad/C_0402_1005Metric.kicad_mod"
        released = kmod.read_bytes()
        assert not RC.check_item(items["CAP-0402-100nF"], lib)[0]
        kmod.write_bytes(released[:400])                                       # cut short
        fails = RC.check_item(items["CAP-0402-100nF"], lib)[0]
        assert any(r == "files" and "KiCad footprint file does not read back" in m for r, m in fails), fails
        _run(d, Path(d) / "home", "kicad-export", lib / "plan.json", "-o", "kx", "--name", "m")    # held as a change
        held = {h["part"]: h for h in json.loads(_run(d, Path(d) / "home", "part", "status", lib, "--json").stdout)["held"]}
        ap = _run(d, Path(d) / "home", "part", "approve", lib, "--part", "CAP-0402-100nF", "--digest",
                  held["CAP-0402-100nF"]["digest"][:12], "-m", "ok", "--user", "Ann <ann@x.com>")
        assert ap.returncode == 2 and "fails the release check" in ap.stderr, (ap.returncode, ap.stdout, ap.stderr)
        assert kmod.read_bytes() == released
