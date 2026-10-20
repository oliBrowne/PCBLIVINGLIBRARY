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


# T019: dedupe never groups an N-channel with a P-channel MOSFET (or NPN with PNP) symbol of the same pins
def test_dedupe_symbol_polarity():
    from ecadlib import dedupe as DD

    def sym(name, desc, pins=(("1", "G"), ("2", "S"), ("3", "D"))):
        return {"records": [{"RECORD": "1", "LIBREFERENCE": name, "COMPONENTDESCRIPTION": desc},
                            {"RECORD": "34", "TEXT": "Q?"}] + [{"RECORD": "2", "num": n, "name": p} for n, p in pins]}
    h = {n: DD.sym_hash(DD.sym_pinout(sym(n, d))) for n, d in
         (("BSS138", "N-channel MOSFET"), ("2N7002", "MOSFET N-CH 60V"), ("AO3401A", "P-channel MOSFET"),
          ("Q_PMOS_GSD", ""), ("GENERIC", "MOSFET"))}
    assert h["BSS138"] == h["2N7002"] != h["AO3401A"] == h["Q_PMOS_GSD"] != h["GENERIC"] != h["BSS138"]
    bjt = [DD.sym_hash(DD.sym_pinout(sym(n, d, (("1", "B"), ("2", "E"), ("3", "C")))))
           for n, d in (("MMBT3904", "NPN transistor"), ("MMBT3906", "PNP transistor"))]
    assert bjt[0] != bjt[1]


# T021: dedupe --bom leaves a cell whose keeper the merge did not adopt (map row with no file) as it is
@lite
def test_dedupe_remap_bom_skips_keepers_not_adopted(tmp_path):
    from ecadlib import dedupe as DD
    (tmp_path / "bom.csv").write_text("Reference,Qty,Value,Footprint,LibRef\nQ1,1,X,Pkg:SOT-23,OLDSYM\n")
    mapping = [{"kind": "footprint", "from": "SOT-23", "to": "MySOT23", "file": "", "parts": ""},
               {"kind": "symbol", "from": "OLDSYM", "to": "NEWSYM", "file": "models/SchLib/NEWSYM.SchLib", "parts": "Q1"}]
    out, n = DD.remap_bom(tmp_path / "bom.csv", mapping, tmp_path / "o.csv")
    assert n == 1 and "Q1,1,X,Pkg:SOT-23,NEWSYM" in out.read_text(encoding="utf-8-sig")


# T022: dedupe --config naming a file that does not exist is an error, not the default rules
@lite
def test_dedupe_missing_config_is_an_error(tmp_path):
    from ecadlib import dedupe as DD
    assert DD.load_config(None, tmp_path)["keep"] == []      # no default dedupe.json: fine
    try:
        DD.load_config(tmp_path / "typo.json", tmp_path)
        raise AssertionError("missing --config accepted")
    except FileNotFoundError as e:
        assert "typo.json" in str(e)


# T061: `project check --json -o STEM` prints one JSON object on stdout; "-> STEM.md" goes to stderr
@lite
def test_project_check_json_out_stdout_is_only_json(tmp_path, monkeypatch):
    from ecadlib import project_check as PC
    monkeypatch.setattr(PC, "check_kicad", lambda *a, **k: {"tool": "kicad", "held": []})
    monkeypatch.setattr(PC, "write_report", lambda res, out: Path(out).with_suffix(".md"))
    o, e = io.StringIO(), io.StringIO()
    with redirect_stdout(o), redirect_stderr(e):
        assert PC.main(["check", str(tmp_path / "b.kicad_pro"), "--json", "-o", str(tmp_path / "drift")]) == 0
    assert json.loads(o.getvalue())["tool"] == "kicad" and "drift.md" in e.getvalue()


# T063: `style learn --from TYPO` exits 2 and leaves the learned house style as it was
@lite
def test_style_learn_missing_from_keeps_the_style(tmp_path, monkeypatch):
    from ecadlib import house_style as HS
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    pf = tmp_path / "house_style.json"
    with redirect_stderr(io.StringIO()) as e, redirect_stdout(io.StringIO()):
        assert HS.main(["--profile", str(pf), "learn", "--from", str(tmp_path / "typo")]) == 2
    assert "not found" in e.getvalue() and "hint:" in e.getvalue() and not pf.exists()


# T064: `init --lib TYPO` exits 2 with a hint and writes no ecadlib.toml
@lite
def test_init_missing_lib_writes_nothing(tmp_path, monkeypatch):
    from ecadlib import rules_infer as RI
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    with redirect_stderr(io.StringIO()) as e, redirect_stdout(io.StringIO()):
        assert RI.main(["--lib", "my_lbi", "--yes", "--no-doctor"], inp=lambda *a: "") == 2
    assert "hint:" in e.getvalue() and not (tmp_path / "ecadlib.toml").exists()


# T065: a file from a newer ecadlib is a plain one-line refusal with an upgrade hint, exit 2, not an "internal error"
def test_schema_too_new_is_a_plain_refusal():
    from ecadlib import cliutil as CU, migrate as MG
    out = io.StringIO()
    assert CU.report("librarian", MG.too_new("plan", 99, "9.9.9", "lib/plan.json"), out) == 2
    assert "upgrade ecadlib" in out.getvalue() and "pip install -U ecadlib" in out.getvalue()
    assert "internal error" not in out.getvalue() and "please report" not in out.getvalue()
