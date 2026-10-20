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


# T023: a pin_header custom drill gets its own land (ring >= 0.15 mm) or is refused (mils typed as mm)
def test_t023_header_custom_drill():
    spec = lambda d: {"name": "H", "package_type": "pin_header", "pin_count": 3, "cols": 1, "rows": 3, "pitch": 2.54,
                      "drill": {"nom": d}}
    txt, _ = FG.build(spec(1.6))
    assert "(size 2.04 2.04) (drill 1.6" in txt, txt
    assert "(size 1.7 1.7) (drill 1" in FG.build(spec(1.0))[0]          # the table drill keeps the table land
    for d in (2.6, 40):
        try:
            FG.build(spec(d))
            assert False, d
        except ValueError as e:
            assert "ring" in str(e), e


# T024: a schema-invalid spec is a plain one-line refusal and the rest of the batch is still built
@lite
def test_t024_schema_error_does_not_stop_the_batch(tmp_path):
    good = {"name": "R_0603_1608Metric", "package_type": "chip", "pin_count": 2, "body_length": {"min": 1.5, "max": 1.7},
            "body_width": {"min": 0.7, "max": 0.95}, "terminal_length": {"min": 0.15, "max": 0.4}}
    (tmp_path / "bad.json").write_text(json.dumps(dict(good, name="TYPO", pitchh=1.27)))
    (tmp_path / "good.json").write_text(json.dumps(good))
    err = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            FG.main(["gen", str(tmp_path / "bad.json"), str(tmp_path / "good.json"), "-o", str(tmp_path / "o.pretty")])
        assert False, "no failure exit"
    except SystemExit as e:
        assert "1 footprint spec" in str(e.code), e.code
    assert err.getvalue().startswith("TYPO: not written:") and "pitchh" in err.getvalue(), err.getvalue()
    assert len(err.getvalue().splitlines()) == 1, err.getvalue()
    assert (tmp_path / "o.pretty" / "R_0603_1608Metric.kicad_mod").exists()


# T025: pad 1 in the middle of a single-row connector is flagged by the table checks and by `check`
def test_t025_pin1_in_the_middle_of_a_single_row():
    pads = [{"number": n, "x": x, "y": 0} for n, x in ((2, -2.54), (1, 0), (3, 2.54))]
    spec = {"name": "C", "package_type": "connector", "pin_count": 3, "pitch": 2.54,
            "pad_table": {"unit": "mm", "y_axis": "down", "origin": "center", "default_pad": {"w": 1.0, "h": 1.8},
                          "pads": pads, "body": {"x1": -3.5, "y1": -1.5, "x2": 3.5, "y2": 1.5}}}
    txt, rep = FG.build(spec)
    mid = lambda msgs: any("middle of its row" in m for m in msgs)
    assert mid(c["message"] for c in rep.get("table_checks", []) if c["level"] == "warning"), rep.get("table_checks")
    assert mid(m for l, _, m in FC.check(txt) if l == "warning")
    pads[0]["number"], pads[1]["number"] = 1, 2                          # 1 2 3: pin 1 at the end, nothing to say
    txt, rep = FG.build(spec)
    assert not mid(c["message"] for c in rep.get("table_checks", [])) and not mid(m for _, _, m in FC.check(txt))


# T026: the pin-header STEP body is the spec's body_length x body_width, centred on the pin field
def test_t026_header_step_body_from_spec():
    from ecadlib import step_gen as SG
    spec = {"rows": 5, "cols": 2, "pitch": 1.27, "body_length": {"nom": 3.41}, "body_width": {"nom": 6.35}}
    bb = lambda s: [f(v[i] for v in s.verts) for i in range(2) for f in (min, max)]
    body, = [s for s in SG.pin_header(spec) if s.name == "body"]
    x0, x1, y0, y1 = bb(body)
    assert abs(x1 - x0 - 3.41) < 1e-6 and abs(y1 - y0 - 6.35) < 1e-6 and abs((x0 + x1) / 2 - 0.635) < 1e-6, bb(body)
    del spec["body_length"], spec["body_width"]                          # no body size: cols x pitch by rows x pitch
    body, = [s for s in SG.pin_header(spec) if s.name == "body"]
    x0, x1, y0, y1 = bb(body)
    assert abs(x1 - x0 - 2.54) < 1e-6 and abs(y1 - y0 - 6.35) < 1e-6, bb(body)
