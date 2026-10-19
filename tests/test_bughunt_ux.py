"""Regression tests for the new-user experience bugs (bug hunt group "ux"). Run: python3 -m tests.run_all test_bughunt_ux"""
import contextlib, io, json, os, sys
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(*a, **k)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def _demo(root):
    """The bundled demo library (7 parts, CH340C held) in workspace `root`: -> (Workspace, plan.json path)."""
    from ecadlib import firstrun as FR
    from ecadlib.app import Workspace
    ws = Workspace(root, offline=True)
    res = FR.build_demo(ws, "both", log=lambda *a: None)
    return ws, Path(root) / res["plan"]


def _summary(fn):
    from ecadlib import term as TM
    buf = io.StringIO()
    fn(TM.Term(buf, styled=False, width=200, verbosity=0))
    return buf.getvalue()


def test_ux1_offline_cache_miss_gets_a_note():
    from ecadlib.firstrun import blockers
    rows = [{"part": "X", "tried": "kicad: library index unavailable (kicad-symbols tree page 1: offline: not in cache); "
                                   "easyeda C1: offline: not in cache"}]
    got = blockers(rows, None, True)
    assert any("kicad: not in the download cache" in m for m, _ in got), got
    assert all("without --offline" in f for _, f in got)


def test_ux2_ux24_ux19_next_steps_give_each_plan_its_own_kicad_library():
    from ecadlib import firstrun as FR, librarian as L
    items = [{"name": "R1", "status": "ready", "source": {}}]
    out = Path("ws/librarian/sample_bom")
    text = _summary(lambda t: L.print_result(items, out, (), t))
    assert f"-o {Path('ws/kicad/sample_bom')} --name sample_bom" in text, text
    assert "--name mylib" not in text and "altium-pkg" in text
    assert "altium-pkg" not in _summary(lambda t: L.print_result(items, out, (), t, tool="kicad"))
    assert "kicad-export" not in _summary(lambda t: L.print_result(items, out, (), t, tool="altium"))
    steps = " ".join(c for c, _ in FR.next_steps({"workspace": "ws", "tool": "both"}))
    assert "-o ws/kicad/YOUR_BOM --name YOUR_BOM" in steps, steps        # the same name as the librarian suggests
    assert "ecadlib review ws/librarian/YOUR_BOM/plan.json" in " ".join(w for _, w in FR.next_steps({"workspace": "ws", "tool": "altium"}))


@lite
def test_ux2_ux4_kicad_export_says_which_parts_it_removed(tmp_path):
    from ecadlib import kicad_export as KX
    ws, plan = _demo(tmp_path / "ws")
    out = tmp_path / "kicad"
    rep = KX.export(plan, out, "mylib", log=lambda *a: None)
    assert not any("removed from mylib" in w for w in rep["warnings"]), rep["warnings"]
    items = json.loads(plan.read_text(encoding="utf-8"))
    gone = next(r["symbol_name"] for r in rep["parts"] if r.get("symbol"))
    plan.write_text(json.dumps([i for i in items if KX.kname(i["name"]) != gone], indent=2), encoding="utf-8")
    rep = KX.export(plan, out, "mylib", log=lambda *a: None)
    assert any("removed from mylib" in w and gone in w for w in rep["warnings"]), rep["warnings"]


def test_ux5_lcsc_field_learned_by_init_is_filled_by_the_supplier_number():
    from ecadlib import release_check as RC, rules as RU
    par = {"Supplier 1": "LCSC", "Supplier Part Number 1": "C25804"}
    assert RC._supplier_number(par, "LCSC", RU.DEFAULTS)
    assert not RC._supplier_number({"Supplier 1": "DigiKey", "Supplier Part Number 1": "x"}, "LCSC", RU.DEFAULTS)
    assert not RC._supplier_number(par, "Voltage", RU.DEFAULTS)


@lite
def test_ux7_status_names_the_rules_file_init_wrote_above_the_workspace(tmp_path):
    from ecadlib import ws_status as WS
    (tmp_path / "ecadlib.toml").write_text('[setup]\nworkspace = "ws"\n', encoding="utf-8")
    (tmp_path / "ws").mkdir()
    s = WS.status(tmp_path / "ws")
    assert s["rules"] and Path(s["rules"]).resolve() == (tmp_path / "ecadlib.toml").resolve(), s["rules"]


@lite
def test_ux8_ux27_default_output_follows_the_init_workspace(tmp_path, monkeypatch):
    from ecadlib import bom_triage as BT, firstrun as FR, librarian as L
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    assert FR.default_out("librarian", "a/sample_bom.csv") == "out/librarian"
    (tmp_path / "ecadlib.toml").write_text('[setup]\nworkspace = "myws"\n', encoding="utf-8")
    assert Path(FR.default_out("librarian", "a/sample_bom.csv")) == Path("myws/librarian/sample_bom")
    assert Path(FR.default_out("triage", "sample bom.csv")) == Path("myws/reports/triage_sample_bom")
    assert BT._lib_out("x/board.csv") == str(Path("myws/librarian/board"))
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "library.lock.json").write_text(json.dumps({"bom": {"file": "../sample_bom.csv"}}), encoding="utf-8")
    assert L._plan_bom(tmp_path / "lib") == "sample_bom.csv"


def test_ux9_lifecycle_workflow_installs_ecadlib():
    text = (ROOT / "examples" / "lifecycle-watch.yml").read_text(encoding="utf-8")
    assert "ecadlib @ git+https://github.com/oliBrowne/PCBLIVINGLIBRARY" in text and "examples/sample_bom.csv" not in text


@lite
def test_ux10_review_files_follow_the_plan_after_a_release_check(tmp_path):
    from ecadlib import release_check as RC
    ws, plan = _demo(tmp_path / "ws")
    for f in ("review.md", "review.csv"):
        (plan.parent / f).write_text("stale", encoding="utf-8")
    _quiet(RC.main, [str(plan)])
    md = (plan.parent / "review.md").read_text(encoding="utf-8")
    assert "7 parts: 6 ready, 1 held" in md and "### CH340C (held)" in md, md[:300]
    assert "CH340C,held" in (plan.parent / "review.csv").read_text(encoding="utf-8-sig")


def test_ux13_quiet_before_the_command():
    from ecadlib import cli
    rc, out, err = _quiet(cli.main, ["-q", "--version"])
    assert rc == 0 and out.startswith("ecadlib "), (rc, out, err)


@lite
def test_ux14_push_takes_the_workspace_from_the_rules(tmp_path, monkeypatch):
    from ecadlib import altium365 as A
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXAR_TOKEN", "")
    (tmp_path / "ecadlib.toml").write_text('[altium365]\nworkspace = "https://acme.365.altium.com/"\n', encoding="utf-8")
    (tmp_path / "plan.json").write_text("[]", encoding="utf-8")
    rc, out, err = _quiet(A.main, ["push", "plan.json", "--dry-run", "-o", "rep.json"])
    assert rc in (None, 0) and "report ->" in out, (rc, out, err)
    (tmp_path / "ecadlib.toml").write_text("", encoding="utf-8")
    rc, out, err = _quiet(A.main, ["push", "plan.json", "--dry-run", "-o", "rep.json"])
    assert rc and "[altium365] workspace" in str(rc), (rc, out, err)


def test_ux18_one_missing_field_on_many_parts_is_named_once():
    from ecadlib.firstrun import blockers
    rows = [{"part": f"R{i}", "status": "held", "why": "fields: no datasheet link", "tried": ""} for i in range(4)]
    got = blockers(rows)
    assert len(got) == 1 and "4 parts are held only because of 'no datasheet link'" in got[0][0], got
    assert "Datasheet column" in got[0][1]


@lite
def test_ux20_house_style_found_without_a_rules_file(tmp_path, monkeypatch):
    from ecadlib import house_style as HS
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    (tmp_path / "out" / "librarian").mkdir(parents=True)
    assert HS.profile_path({}, "out/librarian") == Path("out/librarian/house_style.json")   # nothing yet: next to it
    (tmp_path / "house_style.json").write_text("{}", encoding="utf-8")                       # `style learn` from here
    assert HS.profile_path({}, "out/librarian").resolve() == (tmp_path / "house_style.json").resolve()


def test_ux21_init_rerun_with_only_setup_answers_changed():
    from ecadlib import rules_infer as RI
    old = {"setup": {"libs": []}, "naming": {"case": "upper"}}
    assert RI._setup_only(old, {"setup": {"libs": ["mylibs"]}, "naming": {"case": "upper"}})
    assert not RI._setup_only(old, {"setup": {"libs": ["mylibs"]}, "naming": {"case": "lower"}})


@lite
def test_ux22_demo_with_your_bom_does_not_call_holds_expected(tmp_path, monkeypatch):
    from ecadlib import firstrun as FR
    monkeypatch.chdir(tmp_path)
    rc, out, err = _quiet(FR.demo_main, ["-o", "d", "--tool", "kicad", "--bom", str(ROOT / "examples" / "sensor_node_bom.csv")])
    assert rc == 0, err
    assert "that is expected" not in out and "Altium files" not in out, out
    head = next(l for l in out.splitlines() if "held in" in l)
    assert head.count(",") < 8, head                       # the list is cut at 5 names


def test_ux23_advanced_package_specs_are_candidates():
    from ecadlib import resolve as R, librarian as L
    names = {f.stem for f in R.spec_files(L.SPECS)}
    assert "PinHeader_2x05_P1.27mm_Vertical_SMD" in names and "USB_C_Receptacle_HRO_TYPE-C-31-M-12" in names
    assert R.spec_file(L.SPECS, "PinHeader_2x05_P1.27mm_Vertical_SMD").is_file()


@lite
def test_ux26_held_change_shows_in_status_and_review(tmp_path):
    from ecadlib import reports, ws_status as WS
    (tmp_path / "librarian" / "b").mkdir(parents=True)
    items = [{"name": "AO3401A", "status": "ready", "change": {"state": "held", "base": 1, "summary": "manufacturer"}}]
    (tmp_path / "librarian" / "b" / "plan.json").write_text(json.dumps(items), encoding="utf-8")
    s = WS.status(tmp_path)
    assert s["changes_held"] == [{"plan": "librarian/b/plan.json", "parts": ["AO3401A"]}], s["changes_held"]
    assert "changes held" in WS.render(s) and "ecadlib part status" in WS.render(s)
    md = reports.to_md(reports.review_doc(items))
    assert "1 change to released parts waits for a person (AO3401A)" in md, md


@lite
def test_ux28_compare_before_altium_built_anything_is_not_a_pass(tmp_path, monkeypatch):
    from ecadlib import altium_lib as AL, altium_native as AN
    from tests.test_altium_native import _soic8
    (tmp_path / "part").mkdir()
    AL.write_pcblib(tmp_path / "part" / "fp.PcbLib", [_soic8()])
    (tmp_path / "lib" / "PcbLib").mkdir(parents=True)
    (tmp_path / "lib" / "PcbLib" / "SOIC8.PcbLib").write_bytes((tmp_path / "part" / "fp.PcbLib").read_bytes())
    AN.copy_recipe(tmp_path / "part" / "fp.PcbLib", tmp_path / "lib" / "PcbLib" / "SOIC8.PcbLib")
    assert AN.compare(tmp_path / "lib")["not_built"] == [str(Path("PcbLib/SOIC8.PcbLib"))]
    rc, out, _ = _quiet(AN.main, ["compare", str(tmp_path / "lib")])
    assert rc == 2 and "not built by Altium yet" in out, (rc, out)


def test_ux30_kicad_export_next_step_keeps_the_project_tables():
    from ecadlib import kicad_export as KX
    rep = {"parts": [], "skipped": [], "footprints": {}, "models": {}, "errors": [], "warnings": [], "library": "mylib",
           "symbols": 0}
    text = _summary(lambda t: KX.print_result(rep, Path("kicad"), "plan.json", t))
    assert "cp -r" not in text and "-o <your KiCad project folder> --name mylib" in text, text


@lite
def test_ux32_reject_without_a_policy_says_it_is_still_pushed(tmp_path):
    from ecadlib import approvals as AP
    ws, plan = _demo(tmp_path / "ws")
    rc, out, err = _quiet(AP.main, [str(plan), "reject", "--part", "RES-0603-10k", "-m", "wrong", "--user", "Ann <a@x.com>"])
    assert "not enforced" in out and "is pushed" in out, out


@lite
def test_ux34_init_force_replaces_a_broken_rules_file(tmp_path, monkeypatch):
    from ecadlib import rules_infer as RI
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    (tmp_path / "ecadlib.toml").write_text('[setup]\nlibs = []\n[setup]\nlibs = ["x"]\n', encoding="utf-8")
    rc, out, err = _quiet(RI.main, ["--yes", "--force", "--no-doctor"], environ={})
    assert rc == 0, (rc, out, err)
    assert (tmp_path / "ecadlib.toml.bak").is_file() and "[setup]" in (tmp_path / "ecadlib.toml").read_text(encoding="utf-8")


@lite
def test_ux3_ui_dblib_export_uses_the_plan(tmp_path):
    ws, plan = _demo(tmp_path / "ws")
    r = ws.export_dblib("uploads/demo/demo_bom.csv")
    assert r["from_plan"] and r["held"] == ["CH340C"] and r["written"] == 6, r
    assert any(f.endswith(".DbLib") for f in r["files"]), r
