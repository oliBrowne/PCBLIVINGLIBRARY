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


GOLDEN = ROOT / "tests" / "fixtures" / "kicad_golden"


# T007: footprint compare lists every differing pad, names one-sided pads, exits non-zero on any difference
@lite
def test_t007_compare_reports_every_difference(tmp_path):
    t = (GOLDEN / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").read_text()
    old = '(pad "6" smd roundrect\n\t\t(at 2.475 0.635)'
    (tmp_path / "mine.kicad_mod").write_text(t)
    ref = t.replace(old, '(pad "6" smd roundrect\n\t\t(at 3.475 0.635)')
    rows, _, _ = FG.compare(tmp_path / "mine.kicad_mod", ref)
    assert [r["pad"] for r in rows if FG.differs(r)] == ["6"]
    assert [r for r in rows if r["pad"] == "6"][0]["d_pos"] == (-1.0, 0.0)
    q = (GOLDEN / "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.kicad_mod").read_text()
    (tmp_path / "qfn.kicad_mod").write_text(q)
    rows, _, _ = FG.compare(tmp_path / "qfn.kicad_mod", re.sub(r'\t\(pad "33" smd rect\n.*?\n\t\)\n', "", q, flags=re.S))
    assert [(r["pad"], r["only"]) for r in rows if "only" in r] == [("33", "mine")]
    (tmp_path / "ref.kicad_mod").write_text(ref)
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            FG.main(["compare", str(tmp_path / "mine.kicad_mod"), str(tmp_path / "ref.kicad_mod")])
        code = 0
    except SystemExit as e:
        code = e.code
    assert code and "6" in str(code), code
    assert re.search(r"^\s*6\s.*\(-1\.0, 0\.0\)", out.getvalue(), re.M), out.getvalue()
    with redirect_stdout(io.StringIO()):
        FG.main(["compare", str(tmp_path / "mine.kicad_mod"), str(tmp_path / "mine.kicad_mod")])   # same: no exit
