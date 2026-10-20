"""Regression tests for the footprint/symbol bugs from the 2026-10-20 independent testing pass."""
import io, json, os, re, subprocess, sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecadlib import footprint_check as FC, footprint_gen as FG

BGA = {"name": "BGA-25_5.0x5.0mm_Layout5x5_P0.8mm", "package_type": "bga", "pin_count": 25, "rows": 5, "cols": 5,
       "pitch": 0.8, "body_length": {"nom": 5.0}, "body_width": {"nom": 5.0}, "ball_diameter": {"nom": 0.4}}
BALLPAD = re.compile(r'(\(pad "[A-Z]{1,2}\d+" smd \w+ \(at )(-?[\d.]+) (-?[\d.]+)\)')


def _cli(args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env.update(PYTHONPATH=str(ROOT), ECADLIB_HOME=str(Path(cwd) / "home"), HTTPS_PROXY="http://127.0.0.1:9",
               HTTP_PROXY="http://127.0.0.1:9")
    return subprocess.run([sys.executable, "-m", "ecadlib", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=300)


def _flip(text, sx, sy):
    return BALLPAD.sub(lambda m: f"{m.group(1)}{float(m.group(2)) * sx + 0.0:g} {float(m.group(3)) * sy + 0.0:g})", text)


def _errors(text):
    return [m for l, _, m in FC.check(text) if l == "error"]


# T001: a mirrored ball map (rows or columns reversed) is an error; a 180-degree rotation stays a warning
def test_t001_mirrored_bga_is_an_error():
    txt, _ = FG.build(dict(BGA))
    assert not _errors(txt)
    assert not _errors(_flip(txt, -1, -1))
    for sx, sy in ((1, -1), (-1, 1)):
        assert any("mirrored" in m for m in _errors(_flip(txt, sx, sy))), (sx, sy)
