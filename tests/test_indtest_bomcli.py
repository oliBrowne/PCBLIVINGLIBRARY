"""Regression tests for the 2026-10-20 independent testing pass, BOM and command-line group (one test per T-id)."""
import io, json, os, subprocess, sys, tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]

PADS = '''(footprint "{n}" (layer "F.Cu")
  (pad "1" smd roundrect (at -0.8 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
  (pad "2" smd roundrect (at 0.8 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)))'''


def cli(*args, cwd=None, home=None):
    """Run `python -m ecadlib` with this repo on the path -> CompletedProcess."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env.update(PYTHONPATH=str(ROOT), NO_COLOR="1", HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
    if home:
        env["ECADLIB_HOME"] = str(home)
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


# T016: same pads under two library footprint names (L_0603 / LED_0603) are two footprints, never one shared file
@lite
def test_fp_share_needs_the_same_library_footprint(tmp_path):
    from ecadlib import fp_share as FS
    led, ind = PADS.format(n="LED_0603_1608Metric"), PADS.format(n="L_0603_1608Metric")
    h, cache = FS.digest(led), {}
    assert FS.digest(ind) == h
    pcb = str(tmp_path / "models" / "PcbLib" / "0603.PcbLib")
    written = {pcb: {"source": "standard:LED_SMD:LED_0603_1608Metric", "file": "models/PcbLib/0603.PcbLib", "by": "D1",
                     "name": "0603", "hash": h}}
    name, path, same, _ = FS.pick(written, tmp_path / "models", "0603", ind, "standard:Inductor_SMD:L_0603_1608Metric",
                                  "L1", tmp_path, cache)
    assert same is None and name == "0603_L_0603_1608Metric", name
    name, path, same, _ = FS.pick(written, tmp_path / "models", "0603", led, "kicad:LED_SMD:LED_0603_1608Metric",
                                  "D2", tmp_path, cache)
    assert same is written[pcb] and name == "0603"           # the same footprint from another source still shares
