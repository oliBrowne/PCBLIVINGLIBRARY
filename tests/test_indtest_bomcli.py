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
    monkeypatch.setattr(AP, "load_project", lambda p, rules=None: prj)
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


# T073: `stdparts resolve --kicad-mod newdir/x.kicad_mod` creates the folder and writes the footprint
@lite
def test_stdparts_kicad_mod_into_a_new_folder(tmp_path):
    from ecadlib import stdparts as SP
    f = tmp_path / "newdir" / "soic8.kicad_mod"
    with redirect_stdout(io.StringIO()):
        assert SP.main(["resolve", "SOIC-8 3.9x4.9mm", "--kicad-mod", str(f)]) == 0
    assert '(footprint "SOIC-8_3.9x4.9mm_P1.27mm"' in f.read_text()


# T074: stdparts coverage files an FB-designator ferrite bead as an inductor, as the librarian does
@lite
def test_stdparts_coverage_ferrite_bead(tmp_path):
    from ecadlib import stdparts as SP
    b = tmp_path / "bom.csv"
    b.write_text("Designator,Qty,Value,Footprint,Mfr. Part #,Description\nFB1,1,600R,0603,BLM18PG601SN1D,Ferrite bead 600R 0603\n")
    row = SP.coverage([str(b)])[0]
    assert row["kicad_ref"] == "Inductor_SMD:L_0603_1608Metric", row


# T075: stdparts coverage on an empty or unrecognised BOM: "cannot read BOM" + hint, exit 2
@lite
def test_stdparts_coverage_unreadable_bom(tmp_path):
    from ecadlib import stdparts as SP
    for name, text in (("empty.csv", ""), ("house.csv", "Pos,Bauteilwert,Gehaeuse\nR1,10k,0603\n")):
        (tmp_path / name).write_text(text)
        with redirect_stderr(io.StringIO()) as e:
            assert SP.main(["coverage", str(tmp_path / name)]) == 2
        assert "cannot read BOM" in e.getvalue() and "hint:" in e.getvalue()


# T077: the "ambiguous" hint names what tells the fitting packages apart (body / exposed pad), not a missing pitch
def test_stdparts_ambiguous_hint_names_the_difference():
    from ecadlib import stdparts as SP
    assert SP.ic_package("SOIC-8 208mil")[1].endswith("give the body)")
    assert SP.ic_package("WQFN-16 3x3mm EP1.7x1.7mm")[1].endswith("give the epsize)")


# T131: `stdparts -h` describes the command in a whole sentence
def test_stdparts_help_is_a_whole_sentence():
    from ecadlib import stdparts as SP
    with redirect_stdout(io.StringIO()) as o:
        try:
            SP.main(["-h"])
        except SystemExit:
            pass
    assert "the symbol) offline and deterministically" in " ".join(o.getvalue().split())


# T076: a KiCad 5 .sch and a DOCTYPE netlist reach their own messages through read_bom (triage, librarian)
@lite
def test_read_bom_kicad5_sch_and_doctype_netlist(tmp_path):
    from ecadlib import bom_triage as T
    cases = (("old.sch", "EESchema Schematic File Version 4\nEELAYER 30 0\nEELAYER END\n$EndSCHEMATC\n", "KiCad 5"),
             ("evil.xml", '<?xml version="1.0"?>\n<!DOCTYPE export [ <!ENTITY x SYSTEM "file:///etc/hostname"> ]>\n'
                          '<export version="E"><components><comp ref="R1"><value>&x;</value></comp></components></export>\n',
              "DOCTYPE"))
    for name, text, want in cases:
        (tmp_path / name).write_text(text)
        try:
            T.read_bom(tmp_path / name)
            raise AssertionError(f"{name} read")
        except ValueError as e:
            assert want in str(e), e


# T078: an unreadable BOM leaves no -o folder and no HTTP cache folder behind (librarian, triage)
@lite
def test_unreadable_bom_writes_nothing(tmp_path, monkeypatch):
    from ecadlib import bom_triage as T, librarian as L
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    (tmp_path / "hdr.csv").write_text("Designator,Qty,Value\n")
    try:
        L.run("hdr.csv", out="o/x", cache="o/cache", offline=True, log=lambda *a, **k: None)
        raise AssertionError("empty BOM accepted")
    except ValueError:
        pass
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        assert T.main(["hdr.csv", "--offline", "-o", "o/t", "--cache", "o/cache"]) == 2
    assert not (tmp_path / "o").exists()


# T122: release-check refuses a plan.json that is {} / null / has a nameless entry (exit 2), and does not rewrite it
@lite
def test_release_check_malformed_plan(tmp_path, monkeypatch):
    from ecadlib import release_check as RC
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    plan = tmp_path / "plan.json"
    for content in ("{}", "null", '[{"symbol": {}, "footprint": {}}]'):
        plan.write_text(content)
        with redirect_stderr(io.StringIO()) as e, redirect_stdout(io.StringIO()):
            assert RC.main([str(plan)]) == 2
        assert "not a" in e.getvalue() and "hint:" in e.getvalue() and plan.read_text() == content


# T124: release-check / approve on a broken ecadlib.toml: one line + hint, exit 2 (the RulesError reaches cli.py)
@lite
def test_release_check_and_approve_broken_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    (tmp_path / "ecadlib.toml").write_text("[naming\n")
    (tmp_path / "plan.json").write_text("[]")
    for args in (["release-check", "plan.json"], ["approve", "plan.json", "status"]):
        p = cli(*args, cwd=tmp_path, home=tmp_path / "home")
        assert p.returncode == 2 and "not valid TOML" in p.stderr and "hint:" in p.stderr, (args, p.returncode, p.stderr)


# T125: status names a plan.json holding null / [1, 2, 3] and an ecadlib.toml that does not load
@lite
def test_status_names_damaged_files(tmp_path):
    from ecadlib import ws_status as WS
    pf = tmp_path / "librarian" / "b" / "plan.json"
    pf.parent.mkdir(parents=True)
    for content in ("null", "[1, 2, 3]"):
        pf.write_text(content)
        assert any("librarian/b/plan.json: not a plan" in p for p in WS.status(tmp_path)["problems"]), content
    pf.write_text("[]")
    assert WS.status(tmp_path)["problems"] == []
    (tmp_path / "ecadlib.toml").write_text("[naming\n")
    assert any(p.startswith("ecadlib.toml: not valid TOML") for p in WS.status(tmp_path)["problems"])


# T126: a library.lock.json of the wrong shape is exit 2 with a plain message, never 1 (= --check "drift")
@lite
def test_rebuild_malformed_lock_is_exit_2(tmp_path):
    from ecadlib import rebuild as RB
    good = {"schema_version": 1, "bom": {"file": "bom.csv", "sha256": ""}, "settings": {}, "parts": {}}
    for bad in ([], {**good, "parts": 5}, {k: v for k, v in good.items() if k != "bom"}):
        (tmp_path / RB.LOCK).write_text(json.dumps(bad))
        with redirect_stderr(io.StringIO()) as e, redirect_stdout(io.StringIO()):
            assert RB.main([str(tmp_path), "--check"]) == 2, bad
        assert "not a library lock" in e.getvalue() and "hint:" in e.getvalue()


# T127: dedupe, upkeep, upstream check-kicad and dblib-sync use the shared one line + hint, never a raw class name
@lite
def test_own_error_handlers_use_the_shared_wording(tmp_path, monkeypatch):
    from ecadlib import altium_dblib_sync as DS, dedupe as DD, upkeep as UK, upstream as UP
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    (tmp_path / "trunc.json").write_text('{"a": ')
    runs = [(DD.main, ["nope.json"]), (UK.main, ["nope.json"]), (DD.main, ["trunc.json"]), (UK.main, ["trunc.json"]),
            (UP.main, ["check-kicad", "trunc.json"]), (DS.main, ["trunc.json", "--to", "DS"])]
    for fn, argv in runs:
        with redirect_stderr(io.StringIO()) as e, redirect_stdout(io.StringIO()):
            try:
                code = fn(argv)
            except SystemExit as x:
                code = x.code
        assert code == 2 and "hint:" in e.getvalue() and "Error:" not in e.getvalue(), (argv, code, e.getvalue())


# T128: every option of init, doctor, review, keys set, setup and start is named on docs/command-line.md
@lite
def test_command_line_md_names_every_option(tmp_path):
    import re
    doc = (ROOT / "docs" / "command-line.md").read_text(encoding="utf-8")
    for cmd in (["init"], ["doctor"], ["review"], ["keys", "set"], ["setup"], ["start"]):
        p = cli(*cmd, "-h", cwd=tmp_path, home=tmp_path / "home")
        assert p.returncode == 0, p.stderr
        opts = set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", p.stdout.split("\n\n")[0])) - {"--help"}
        assert not [o for o in opts if o not in doc], (cmd, sorted(o for o in opts if o not in doc))


# T130: where-used -h names only commands that exist (`ecadlib lifecycle` is not one)
@lite
def test_where_used_help_names_real_commands(tmp_path):
    import re
    from ecadlib import cli as C
    p = cli("where-used", "-h", cwd=tmp_path, home=tmp_path / "home")
    named = set(re.findall(r"`ecadlib ([a-z][a-z0-9-]*)", " ".join(p.stdout.split())))
    assert named and not named - set(C.COMMANDS), named
