"""Regression tests for the 2026-10-20 independent testing pass, KiCad outputs and install / first run. Offline; no API keys."""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]
DEAD = {k: "http://127.0.0.1:9" for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}


def _run(argv, cwd=None, env=None):
    """cli.main(argv) in cwd -> (exit code, stdout, stderr)."""
    from ecadlib import cli
    out, err = io.StringIO(), io.StringIO()
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    try:
        if cwd:
            os.chdir(cwd)
        for k in ("ECADLIB_LIB", "ECADLIB_WORKSPACE", "ECADLIB_CONFIG", "ECADLIB_DEBUG"):
            os.environ.pop(k, None)
        os.environ.update(DEAD)
        os.environ.update(env or {})
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = e.code
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    return rc, out.getvalue(), err.getvalue()


def _sub(args, cwd, env=None):
    """python -m ecadlib args in cwd, this checkout first on the path -> CompletedProcess."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_"))}
    e.update(DEAD, PYTHONPATH=str(ROOT), NO_COLOR="1", ECADLIB_HOME=str(Path(cwd) / "home"))
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=str(cwd), env=e, capture_output=True,
                          text=True, timeout=300)


_DEMO = {}


def _demo():
    """One offline `ecadlib demo -o ws` for the tests that only read it -> the workspace folder (do not change it)."""
    if "ws" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="indtest-kicad-"))
        rc, _, err = _run(["demo", "-o", "ws"], cwd=d, env={"ECADLIB_HOME": str(d / "home")})
        assert rc == 0, err
        _DEMO["ws"] = d / "ws"
    return _DEMO["ws"]


# T030
def test_standard_regulator_output_is_power_out_in_kicad():
    from ecadlib import kicad_export as KX, sexpr
    ws = _demo()
    plan_dir = ws / "librarian" / "demo_bom_demo"
    item = next(i for i in json.loads((plan_dir / "plan.json").read_text()) if i["name"] == "AMS1117-3.3")
    sym, how = KX.load_symbol(item, plan_dir, None, [], "AMS1117-3.3", [])
    got = {str(sexpr.first(p, "number")[1]).strip('"'): p[1] for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")}
    assert got == {"1": "power_in", "2": "power_out", "3": "power_in"}, (got, how)


# T033
@lite
def test_kicad_pcm_refuses_a_license_kicads_pcm_refuses(tmp_path):
    from ecadlib import kicad_pcm as PCM
    m = PCM.metadata("mylib", "local.ecadlib.mylib", "1.0.0", 8, "Proprietary-X", "me")
    assert any("license" in p for p in PCM.check_metadata(m, schema_path=False)[0])
    assert not PCM.check_metadata(dict(m, license="CC-BY-SA-4.0"), schema_path=False)[0]
    rc, _, err = _run(["kicad-pcm", str(tmp_path / "nope" / "plan.json"), "--out", str(tmp_path / "pcm"), "--license", "mit"])
    assert rc == 2 and "did you mean MIT" in err and not (tmp_path / "pcm").exists(), err


# T034
@lite
def test_kicad_check_bom_library_is_the_kicad_sym_not_the_workspace_plan(tmp_path):
    import types
    from ecadlib import altium_menu as AM, kicad_menu as KM
    cfg = KM.config(tmp_path)
    (tmp_path / "ecadlib").mkdir()
    (tmp_path / "ecadlib" / "ecadlib.kicad_sym").write_text(
        '(kicad_symbol_lib (symbol "R1" (property "MPN" "0603WAF1002T5E" (at 0 0 0)) (property "Mfr PN" "lm358dr" (at 0 0 0))'
        ' (property "Value" "10k" (at 0 0 0))))\n', encoding="utf-8")
    plan = tmp_path / "ecadlib-work" / "librarian" / "b" / "plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps([{"name": "C1", "status": "ready", "mpn": "CL05B104KO5NNNC"}]))
    assert AM._library_mpns(cfg, types.SimpleNamespace(root=tmp_path / "ecadlib-work")) == {"0603WAF1002T5E", "LM358DR"}


# T035
@lite
def test_kicad_review_queue_of_a_second_project_gets_its_own_port(tmp_path):
    from ecadlib import altium_menu as AM, kicad_menu as KM
    a, b = KM.config(tmp_path / "A" / "board.kicad_pcb"), KM.config(tmp_path / "B" / "board.kicad_pcb")
    up = {8765: str(a["workspace"])}                    # project A's UI already runs on the default port
    probe = lambda p: ("ecadlib", up[p]) if p in up else (None, None)
    launched = []
    r = AM.open_queue(b, opener=False, probe=probe, launcher=lambda ws, p: (launched.append(p), up.__setitem__(p, str(ws))),
                      sleep=lambda s: None)
    assert r["status"] == "ok" and launched == [8766] and ":8766/" in r["url"], r
    r = AM.open_queue(b, opener=False, probe=probe, launcher=None)          # again: B's own UI is found, not restarted
    assert r["status"] == "ok" and r["started"] == "0" and ":8766/" in r["url"], r
    up = {p: "/elsewhere" for p in range(8765, 8800)}
    r = AM.open_queue(b, opener=False, probe=probe, launcher=None)
    assert r["status"] == "error" and "ecadlib_menu.ini" not in r["fix"], r


def _plugin():
    import types
    from unittest import mock
    fake = types.ModuleType("pcbnew")
    fake.ActionPlugin = type("ActionPlugin", (), {"register": lambda self: None})
    with mock.patch.dict(sys.modules, {"pcbnew": fake}):
        src = ROOT / "ecadlib" / "assets" / "kicad_action_plugin.py"
        mod = types.ModuleType("ecadlib_kicad_action_plugin")
        mod.__file__ = str(src)
        exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), mod.__dict__)
    return mod


# T036
@lite
def test_plugin_fill_adds_the_library_to_the_project_tables(tmp_path):
    kap = _plugin()
    assert kap.tables_after_fill(str(tmp_path)) == ""                   # nothing built: tables left alone
    assert not (tmp_path / "sym-lib-table").exists()
    (tmp_path / "ecadlib").mkdir()
    (tmp_path / "ecadlib" / "ecadlib.kicad_sym").write_text("(kicad_symbol_lib)\n")
    assert "reopen the project" in kap.tables_after_fill(str(tmp_path))
    assert '(uri "${KIPRJMOD}/ecadlib/ecadlib.kicad_sym")' in (tmp_path / "sym-lib-table").read_text()
    assert '(uri "${KIPRJMOD}/ecadlib/ecadlib.pretty")' in (tmp_path / "fp-lib-table").read_text()
    assert kap.tables_after_fill(str(tmp_path)) == ""                   # again: already listed


# T037
@lite
def test_plugin_names_an_ecadlib_row_of_another_library(tmp_path):
    kap = _plugin()
    (tmp_path / "fp-lib-table").write_text('(fp_lib_table\n  (version 7)\n  (lib (name "ecadlib")(type "KiCad")'
                                           '(uri "${KIPRJMOD}/ecadlib.pretty")(options "")(descr ""))\n)\n')
    try:
        kap.ensure_tables(str(tmp_path))
        raise AssertionError("kept another library's ecadlib row")
    except ValueError as e:
        assert "${KIPRJMOD}/ecadlib.pretty" in str(e) and "rename or remove" in str(e), e
    (tmp_path / "ecadlib").mkdir()
    (tmp_path / "ecadlib" / "ecadlib.kicad_sym").write_text("(kicad_symbol_lib)\n")
    assert "${KIPRJMOD}/ecadlib.pretty" in kap.tables_after_fill(str(tmp_path))


# T084
@lite
def test_kicad_export_house_name_replaces_the_mpn_field(tmp_path):
    from ecadlib import sexpr
    shutil.copytree(_demo(), tmp_path / "ws")
    (tmp_path / "house.toml").write_text('[parameters]\nnames = { "Manufacturer Part Number" = "Mfr PN", '
                                         '"Manufacturer" = "Mfr Name" }\n', encoding="utf-8")
    rc, _, err = _run(["kicad-export", "ws/librarian/demo_bom_demo/plan.json", "-o", "k", "--name", "mylib", "--rules",
                       "house.toml"], cwd=tmp_path)
    assert rc == 0, err
    t = sexpr.parse((tmp_path / "k" / "mylib.kicad_sym").read_text(encoding="utf-8"))
    for s in sexpr.find(t, "symbol"):
        props = [str(p[1]).strip('"') for p in sexpr.find(s, "property")]
        assert "Mfr PN" in props and "MPN" not in props and "Manufacturer" not in props, (s[1], props)


# T085
def test_kicad_docs_say_generated_footprints_get_the_pcblib_3d_model():
    rep = json.loads((_demo() / "kicad" / "demo_bom_demo" / "kicad_export.json").read_text(encoding="utf-8"))
    assert rep["models"] and all("from the .PcbLib" in v for v in rep["models"].values()), rep["models"]
    doc = " ".join((ROOT / "docs" / "kicad.md").read_text(encoding="utf-8").split())
    assert "ecadlib-generated footprints have none" not in doc and "cannot be downloaded offline stay missing" not in doc
    assert "gets its `.PcbLib`'s 3D model" in doc


# T086
@lite
def test_quiet_kicad_dblib_and_kicad_pcm_print_only_the_outcome(tmp_path):
    shutil.copytree(_demo(), tmp_path / "ws")
    plan = "ws/librarian/demo_bom_demo/plan.json"
    assert _run(["kicad-export", plan, "-o", "k", "--name", "mylib"], cwd=tmp_path)[0] == 0
    rc, db, err = _run(["-q", "kicad-dblib", plan, "-o", "k", "--name", "mylib"], cwd=tmp_path)
    assert rc == 0 and "wrote k/mylib.kicad_dbl" in db and " parts" not in db, (db, err)
    rc, pcm, err = _run(["-q", "kicad-pcm", plan, "-o", "p", "--name", "mylib"], cwd=tmp_path)
    assert rc == 0 and "wrote p/" in pcm and "Plugin and Content Manager" not in pcm and "symbol library" not in pcm, (pcm, err)
    rc, pcm, _ = _run(["kicad-pcm", plan, "-o", "p", "--name", "mylib"], cwd=tmp_path)
    assert "Plugin and Content Manager" in pcm, pcm


# T087
def test_kicad_menu_check_bom_help_shows_kicads_placeholder():
    rc, out, _ = _run(["kicad-menu", "check-bom", "-h"])
    assert rc == 0 and '"%I"' in out and '"%%I"' not in out, out


# T088
@lite
def test_parity_next_hint_keeps_the_export_folder(tmp_path):
    shutil.copytree(_demo(), tmp_path / "ws")
    plan = "ws/librarian/demo_bom_demo/plan.json"
    assert _run(["kicad-export", plan, "-o", "my k", "--name", "mylib"], cwd=tmp_path)[0] == 0
    fp = tmp_path / "my k" / "mylib.pretty" / "R_0603_1608Metric.kicad_mod"
    fp.write_text(fp.read_text(encoding="utf-8").replace("(size 0.8 0.95)", "(size 0.9 0.95)", 1), encoding="utf-8")
    rc, out, _ = _run(["parity", plan, "--kicad", "my k", "--name", "mylib"], cwd=tmp_path)
    assert rc == 1 and "2 differ" in out, out
    from ecadlib.cliutil import shell_word
    assert f"ecadlib parity {plan} --kicad {shell_word('my k')} --name mylib -v" in out, out


# T089
@lite
def test_kicad_bom_check_report_names_kicad_not_altium(tmp_path):
    (tmp_path / "P").mkdir()
    (tmp_path / "P" / "board.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<export version="E"><design><source>board.kicad_sch</source></design>'
        '<components><comp ref="R1"><value>10k</value><footprint>Resistor_SMD:R_0603_1608Metric</footprint><fields>'
        '<field name="MPN">0603WAF1002T5E</field></fields></comp></components></export>\n', encoding="utf-8")
    rc, out, err = _run(["kicad-menu", "check-bom", str(tmp_path / "P" / "board.xml"), "--no-open", "--json"],
                        env={"ECADLIB_HOME": str(tmp_path / "home")})
    assert rc == 0, err
    report = Path(json.loads(out)["report"])
    assert report.name.endswith("_kicad.html") and "(ecadlib kicad-menu check-bom)" in report.read_text(encoding="utf-8"), report


# T012
def test_rerun_init_yes_turns_online_lookups_and_the_reader_back_on():
    from ecadlib import firstrun as FR

    def ask(order, answers):
        it = iter(answers)
        return FR.ask_setup({"sources": {"order": order}}, given={"tool": "kicad", "libs": ["x"], "workspace": "ws"},
                            inp=lambda q: next(it), out=lambda *a: None, environ={})["sources"]
    assert ask(["standard"], ["y", "n", "y"]) == ["kicad", "standard", "datasheet"]
    assert ask(["standard"], ["y", "y", "n"]) == ["kicad", "easyeda", "standard"]
    assert ask(["kicad", "standard"], ["y", "n", "n"]) is None                  # unchanged: nothing written
    assert ask(["standard", "kicad"], ["n", "n"]) == ["standard"]


# T013
@lite
def test_ui_serves_the_page_to_a_browser_that_waits_for_it(tmp_path, monkeypatch):
    import threading, urllib.request
    from ecadlib.ui import server as S
    made, seen = [], []
    real = S.make_server
    monkeypatch.setattr(S, "make_server", lambda *a, **k: made.append(real(*a, **k)) or made[-1])

    def console_browser(url, *a, **k):          # like w3m: fetches the page and only then returns
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with direct.open(url, timeout=3) as r:
                seen.append(r.status)
        except OSError as e:
            seen.append(type(e).__name__)
        threading.Thread(target=made[0].shutdown, daemon=True).start()
        return True
    monkeypatch.setattr(S.webbrowser, "open", console_browser)
    with contextlib.redirect_stdout(io.StringIO()):
        S.main(["--workspace", str(tmp_path / "ws"), "--port", "0", "--setup"])
    made[0].server_close()
    assert seen == [200], seen


# T014
@lite
def test_first_start_prints_the_setup_page_and_the_main_page_links_to_it(tmp_path, monkeypatch):
    import re, urllib.request
    from ecadlib import launcher as L
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    got = {}

    def window(app, open_page):                 # the 'ecadlib is running' window: fetch what was printed, then close
        url = re.search(r"(http://\S+)", got["out"].getvalue()).group(1)
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        got["printed"] = direct.open(url, timeout=10).read().decode()
        got["main"] = direct.open(app.url, timeout=10).read().decode()
        return True
    monkeypatch.setattr(L, "window", window)
    got["out"] = io.StringIO()
    with contextlib.redirect_stdout(got["out"]):
        assert L.main(["--no-browser", "--port", "0", "--workspace", str(tmp_path / "ws")]) == 0
    assert "<title>ecadlib setup</title>" in got["printed"], got["out"].getvalue()
    assert re.search(r'href="setup"', got["main"])
