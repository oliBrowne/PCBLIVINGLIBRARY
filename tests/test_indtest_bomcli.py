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


# T017: `project --json` prints only the JSON on stdout; "wrote <csv>" goes to stderr
@lite
def test_project_json_stdout_is_only_json(tmp_path, monkeypatch):
    from ecadlib import altium_project as AP
    prj = {"name": "B", "sheets": [], "components": [], "variants": [], "warnings": []}
    monkeypatch.setattr(AP, "load_project", lambda p: prj)
    monkeypatch.setattr(AP, "bom_groups", lambda p, v: [{"qty": 1}])
    monkeypatch.setattr(AP, "write_bom_csv", lambda g, out, v: out)
    o, e = io.StringIO(), io.StringIO()
    with redirect_stdout(o), redirect_stderr(e):
        assert AP.main([str(tmp_path / "B.PrjPcb"), "--json"]) == 0
    assert json.loads(o.getvalue()) == [{"qty": 1}] and "wrote" in e.getvalue()


# T018: where-used refuses a --lib or --lifecycle that does not exist (exit 2), instead of flagging every part
@lite
def test_where_used_missing_lib_or_lifecycle(tmp_path):
    from ecadlib import altium_project as AP, firstrun as FR
    assert FR.preflight("where-used", ["--projects", ".", "--lib", str(tmp_path / "nope")])
    for args in (["--lib", str(tmp_path / "nope")], ["--lifecycle", str(tmp_path / "nope.json")]):
        with redirect_stderr(io.StringIO()) as e:
            assert AP.where_used_main(["--projects", str(tmp_path), *args, "-o", str(tmp_path / "wu")]) == 2
        assert "not found" in e.getvalue()
    assert AP.load_lifecycle(None) == {}


# T020: dedupe / where-used --lib take a library file (.DbLib, .kicad_mod), as the docs show
@lite
def test_dedupe_lib_file_passes_preflight(tmp_path):
    from ecadlib import firstrun as FR
    f = tmp_path / "parts.DbLib"
    f.write_text("[OutputDatabaseLinkFile]\n")
    assert FR.preflight("dedupe", ["plan.json", "--lib", str(f)]) is None
    assert FR.preflight("where-used", ["--projects", ".", "--lib", str(f)]) is None
    assert "not a file" in FR.preflight("librarian", ["bom.csv", "--lib", str(f)])[0]
