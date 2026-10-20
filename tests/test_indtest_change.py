"""Regression tests for the 2026-10-20 independent testing pass, change-control area (T-ids from
/mnt/project-files/tests/2026-10-20_independent_test/REPORT.md). Nothing live.
    python3 -m tests.run_all test_indtest_change"""
import os, subprocess, sys, tempfile
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
