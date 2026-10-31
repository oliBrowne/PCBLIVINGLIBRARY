"""ecadlib kicad-install: the library goes into KiCad's own per-user tables and path variables (offline; the real-KiCad
check that KiCad resolves them is test_kicad_real.test_installed_library_resolves_in_kicad)."""
import json
from pathlib import Path

from ecadlib import kicad_install as KI, sexpr
from tests.fixtures_lite import lite

# KiCad 10's template layout (spaces between the row's groups, tabs) and KiCad 9's (no spaces, two-space indent)
SYM10 = ('(sym_lib_table\n\t(version 7)\n\t(lib (name "Device") (type "KiCad") (uri "${KICAD10_SYMBOL_DIR}/Device.kicad_sym") '
         '(options "") (descr "Generic symbols"))\n)\n')
FP9 = ('(fp_lib_table\n  (version 7)\n  (lib (name "Resistor_SMD")(type "KiCad")(uri "${KICAD9_FOOTPRINT_DIR}/Resistor_SMD.pretty")'
       '(options "")(descr "x"))\n)\n')
MULTI = ('(sym_lib_table\n  (version 7)\n  (lib\n    (name "mylib")\n    (type "KiCad")\n    (uri "/old/place/mylib.kicad_sym")\n'
         '    (options "")\n    (descr "")\n  )\n  (lib (name "Device")(type "KiCad")(uri "${KICAD9_SYMBOL_DIR}/Device.kicad_sym")'
         '(options "")(descr ""))\n)\n')


def _lib(d, name="mylib", model="${ECADLIB_DIR_MYLIB}/mylib.3dshapes/R.step"):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.kicad_sym").write_text('(kicad_symbol_lib\n\t(version 20231120)\n\t(generator "ecadlib")\n'
                                         '\t(generator_version "8.0")\n)\n')
    (d / f"{name}.pretty").mkdir(exist_ok=True)
    (d / f"{name}.pretty" / "R.kicad_mod").write_text(f'(footprint "R"\n\t(version 20240108)\n\t(model "{model}")\n)\n')
    return d


def _cfg(base, series, sym=None, fp=None, common=None):
    c = Path(base) / series
    c.mkdir(parents=True)
    if sym is not None:
        (c / "sym-lib-table").write_text(sym)
    if fp is not None:
        (c / "fp-lib-table").write_text(fp)
    if common is not None:
        (c / "kicad_common.json").write_text(json.dumps(common))
    return c


def _names(p):
    return [(str(sexpr.first(l, "name")[1]), str(sexpr.first(l, "uri")[1])) for l in sexpr.find(sexpr.parse(Path(p).read_text()), "lib")]


def _env(base):
    return {"KICAD_CONFIG_HOME": str(base)}


@lite
def test_install_adds_rows_and_path_variable_and_keeps_the_rest(tmp_path):
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "10.0", SYM10, FP9.replace("KICAD9", "KICAD10"),
             {"appearance": {"icon_theme": 2}, "environment": {"vars": {"OTHER": "/x"}}})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["10.0"]["status"] == "installed", rep
    assert _names(c / "sym-lib-table") == [("Device", "${KICAD10_SYMBOL_DIR}/Device.kicad_sym"),
                                           ("mylib", "${ECADLIB_DIR_MYLIB}/mylib.kicad_sym")]
    assert _names(c / "fp-lib-table")[-1] == ("mylib", "${ECADLIB_DIR_MYLIB}/mylib.pretty")
    common = json.loads((c / "kicad_common.json").read_text())
    assert common["environment"]["vars"] == {"OTHER": "/x", "ECADLIB_DIR_MYLIB": lib.resolve().as_posix()}
    assert common["appearance"] == {"icon_theme": 2}
    assert (c / "sym-lib-table.ecadlib-bak").read_text() == SYM10           # the old table kept beside it
    assert not rep["warnings"], rep["warnings"]                             # models already use the variable


@lite
def test_install_twice_changes_nothing(tmp_path):
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", SYM10.replace("KICAD10", "KICAD9"), FP9, {})
    KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    before = {f.name: f.read_text() for f in c.iterdir()}
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["9.0"]["status"] == "already installed", rep
    assert {f.name: f.read_text() for f in c.iterdir()} == before


@lite
def test_a_multiline_row_of_the_same_name_is_replaced_not_duplicated(tmp_path):
    """KiCad's own Manage Libraries dialog writes rows over several lines; a second row of the same nickname makes
    KiCad refuse the whole table ("Duplicate library nickname")."""
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", MULTI, FP9, {})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    names = _names(c / "sym-lib-table")
    assert [n for n, _ in names] == ["mylib", "Device"], names              # same place, one row
    assert names[0][1] == "${ECADLIB_DIR_MYLIB}/mylib.kicad_sym"
    assert any("/old/place" in w for w in rep["warnings"]), rep["warnings"]


@lite
def test_no_global_table_and_no_template_is_left_alone(tmp_path):
    """Writing a table with only our row would hide KiCad's own libraries (KiCad offers its default table only when
    there is none)."""
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", None, None, {})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[tmp_path / "nowhere"], log=lambda *a: None)
    assert rep["versions"]["9.0"]["status"].startswith("not changed: KiCad 9.0 has no sym-lib-table"), rep
    assert sorted(f.name for f in c.iterdir()) == ["kicad_common.json"]


@lite
def test_no_global_table_is_set_up_from_that_kicads_template(tmp_path):
    lib = _lib(tmp_path / "out")
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "sym-lib-table").write_text(SYM10.replace("KICAD10", "KICAD9"))
    (tpl / "fp-lib-table").write_text(FP9)
    other = tmp_path / "tpl10"
    other.mkdir()
    (other / "sym-lib-table").write_text(SYM10)                            # KiCad 10's: not for KiCad 9
    c = _cfg(tmp_path / "cfg", "9.0", None, None, None)
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[other, tpl], log=lambda *a: None)
    assert rep["versions"]["9.0"]["status"] == "installed", rep
    assert [n for n, _ in _names(c / "sym-lib-table")] == ["Device", "mylib"]
    assert "${KICAD9_SYMBOL_DIR}" in (c / "sym-lib-table").read_text()
    assert json.loads((c / "kicad_common.json").read_text())["environment"]["vars"]["ECADLIB_DIR_MYLIB"]


@lite
def test_kicad7_row_points_at_a_kicad7_copy(tmp_path):
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "7.0", SYM10.replace("KICAD10", "KICAD7"), FP9.replace("KICAD9", "KICAD7"), {})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert ("mylib", "${ECADLIB_DIR_MYLIB}/mylib.kicad7.kicad_sym") in _names(c / "sym-lib-table"), rep
    assert "(version 20220914)" in (lib / "mylib.kicad7.kicad_sym").read_text()
    assert KI.library(lib)[1] == "mylib"                                    # the copy is not a second library


@lite
def test_a_kicad7_without_tables_is_not_reported_installed(tmp_path):
    """The KiCad 7 copy is written before the missing table stops that version: the result must not then say to
    restart KiCad to see the library."""
    lib = _lib(tmp_path / "out")
    _cfg(tmp_path / "cfg", "7.0", common={})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["7.0"]["status"].startswith("not changed"), rep
    said = "\n".join(KI.format_result(rep))
    assert "to see mylib" not in said and "start KiCad 7.0 once" in said, said


@lite
def test_every_version_found_is_installed_and_old_ones_skipped(tmp_path):
    lib = _lib(tmp_path / "out")
    for s in ("6.0", "8.0", "9.0", "10.0"):
        _cfg(tmp_path / "cfg", s, SYM10, FP9, {})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert list(rep["versions"]) == ["10.0", "9.0", "8.0"], rep["versions"]
    assert "mylib" not in (tmp_path / "cfg" / "6.0" / "sym-lib-table").read_text()


@lite
def test_a_database_row_of_that_name_is_not_replaced(tmp_path):
    lib = _lib(tmp_path / "out")
    db = SYM10.replace(')\n)', ')\n\t(lib (name "mylib")(type "Database")(uri "/db/mylib.kicad_dbl")(options "")(descr ""))\n)')
    c = _cfg(tmp_path / "cfg", "10.0", db, FP9, {})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["10.0"]["status"].startswith("not changed"), rep
    assert (c / "sym-lib-table").read_text() == db


@lite
def test_uninstall_takes_rows_and_variable_out(tmp_path):
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", MULTI, FP9, {"environment": {"vars": {"OTHER": "/x"}}})
    KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    rep = KI.install(lib, uninstall=True, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["9.0"]["status"] == "removed", rep
    assert [n for n, _ in _names(c / "sym-lib-table")] == ["Device"]
    assert [n for n, _ in _names(c / "fp-lib-table")] == ["Resistor_SMD"]
    assert json.loads((c / "kicad_common.json").read_text())["environment"]["vars"] == {"OTHER": "/x"}
    said = "\n".join(KI.format_result(rep))
    assert "removed" in said and "to see" not in said, said          # not "restart KiCad to see mylib" after removing it


@lite
def test_kiprjmod_models_are_warned_about(tmp_path):
    lib = _lib(tmp_path / "out", model="${KIPRJMOD}/mylib.3dshapes/R.step")
    _cfg(tmp_path / "cfg", "9.0", SYM10, FP9, {})
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert any("--models-var ECADLIB_DIR_MYLIB" in w for w in rep["warnings"]), rep["warnings"]


@lite
def test_dry_run_writes_nothing(tmp_path):
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", SYM10, FP9, {})
    rep = KI.install(lib, dry_run=True, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["9.0"]["changed"] and (c / "sym-lib-table").read_text() == SYM10
    assert not (c / "sym-lib-table.ecadlib-bak").exists()


@lite
def test_broken_settings_file_is_not_overwritten(tmp_path):
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", SYM10, FP9)
    (c / "kicad_common.json").write_text("{not json")
    rep = KI.install(lib, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert rep["versions"]["9.0"]["status"].startswith("not changed"), rep
    assert (c / "kicad_common.json").read_text() == "{not json" and (c / "sym-lib-table").read_text() == SYM10


def test_config_folders_per_platform():
    h = Path("/h")
    assert KI.config_bases({"APPDATA": r"C:\U\AppData\Roaming"}, h, "windows") == [Path(r"C:\U\AppData\Roaming") / "kicad"]
    assert KI.config_bases({}, h, "macos") == [h / "Library" / "Preferences" / "kicad"]
    assert KI.config_bases({}, h, "linux") == [h / ".config" / "kicad", h / ".var/app/org.kicad.KiCad/config/kicad"]
    assert KI.config_bases({"XDG_CONFIG_HOME": "/x"}, h, "linux")[0] == Path("/x/kicad")
    assert KI.config_bases({"KICAD_CONFIG_HOME": "/k"}, h, "linux") == [Path("/k")]


def test_var_names():
    assert KI.var_name("mylib") == "ECADLIB_DIR_MYLIB"
    assert KI.var_name("ecadlib") == "ECADLIB_DIR_ECADLIB"
    assert KI.var_name("my-lib 2") == "ECADLIB_DIR_MY_LIB_2"
    assert KI.var_name("lib") != "ECADLIB_LIB"                   # never one of ecadlib's own settings
    for bad in ("ECADLIB_LIB", "ECADLIB_HOME", "KIPRJMOD", "KICAD9_3DMODEL_DIR", "a b", ""):
        try:
            KI.check_var(bad)
            assert False, bad
        except ValueError:
            pass
    assert KI.check_var("MY_PARTS") == "MY_PARTS"


@lite
def test_cli_exit_codes(tmp_path, monkeypatch):
    lib = _lib(tmp_path / "out")
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "cfg"))
    _cfg(tmp_path / "cfg", "9.0", None, None, {})
    monkeypatch.setattr(KI, "template_dirs", lambda *a, **k: [])
    assert KI.main([str(lib)]) == 1                                         # nothing could be installed
    (tmp_path / "cfg" / "9.0" / "sym-lib-table").write_text(SYM10)
    (tmp_path / "cfg" / "9.0" / "fp-lib-table").write_text(FP9)
    assert KI.main([str(lib)]) == 0
    assert KI.main([str(tmp_path / "missing")]) == 2



@lite
def test_kicad_export_models_var_and_install(tmp_path, monkeypatch):
    """--models-var writes ${VAR}/<lib>.3dshapes model paths (kept by a later export without it); --install also puts
    the library into KiCad's user tables with that variable."""
    import re
    from ecadlib import kicad_export as X
    from tests import test_kicad_export as TX
    plan, _, http = TX._plan(tmp_path, api_key="sk-test")
    out = tmp_path / "k"
    X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None, models_var="ECADLIB_DIR_LIB")
    models = lambda: [m for f in (out / "lib.pretty").glob("*.kicad_mod")
                      for m in re.findall(r'\(model "([^"]+)"', f.read_text())]
    assert models() and all(m.startswith("${ECADLIB_DIR_LIB}/lib.3dshapes/") for m in models()), models()
    X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None)
    assert all(m.startswith("${ECADLIB_DIR_LIB}/lib.3dshapes/") for m in models()), models()     # remembered
    try:
        X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None, models_var="BAD VAR")
        assert False, "a bad variable name was taken"
    except X.PlanError:
        pass
    c = _cfg(tmp_path / "cfg", "9.0", SYM10, FP9, {})
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(X, "CachedHTTP", lambda *a, **k: http)
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert X.main([str(plan), "-o", str(out), "--name", "lib", "--include-held", "--install"]) == 0
    said = buf.getvalue()           # the summary names the variable the tables use, not ${KIPRJMOD}, and does not
    assert "${ECADLIB_DIR_LIB}" in said and "<your KiCad project folder>" not in said, said    # send you to a project
    assert ("lib", "${ECADLIB_DIR_LIB}/lib.pretty") in _names(c / "fp-lib-table")
    assert json.loads((c / "kicad_common.json").read_text())["environment"]["vars"]["ECADLIB_DIR_LIB"] == out.resolve().as_posix()

# ---------------------------------------------------------------- real KiCad (skips without kicad-cli + pcbnew)
def test_installed_library_resolves_in_kicad():
    """kicad-export --models-var + kicad-install into a scratch KiCad settings folder (KICAD_CONFIG_HOME): KiCad finds
    every footprint through its global fp-lib-table (no project table) and every 3D model through the path variable
    in kicad_common.json; the same board with nothing installed finds neither (so the check is live)."""
    import os, subprocess, tempfile
    from ecadlib import kicad_export as X
    from tests import test_kicad_export as TX, test_kicad_real as R
    if not (R.pcbnew_python() and R.kicad_cli()):
        return R._skip("kicad-cli / pcbnew (KiCad's Python module)")
    major = R.kicad_major() or 7
    if major < 8:                       # KiCad 7 has no `kicad-cli pcb drc`, and its pcbnew DRC ignores the user tables
        return R._skip("KiCad 8 or newer")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        plan, _, http = TX._plan(d, api_key="sk-test")
        out = d / "shared" / "lib"
        rep = X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None, models_var="ECADLIB_DIR_LIB")
        assert not rep["errors"], rep["errors"]
        cfg = d / "cfg"
        env = dict(os.environ, KICAD_CONFIG_HOME=str(cfg))
        subprocess.run([R.kicad_cli(), "version"], env=env, capture_output=True, timeout=120)  # KiCad sets up cfg/N.0
        series = f"{major}.0"
        if not (cfg / series).is_dir():
            (cfg / series).mkdir(parents=True)
        templated = all(KI._template(series, f, KI.template_dirs(series)) for f in ("sym-lib-table", "fp-lib-table"))
        if not templated:              # KiCad installed without its libraries (no template tables): the tables a
            for _, f, t in KI.TABLES:  # KiCad that has been started once has, with one row of its own
                (cfg / series / f).write_text(f'({t}\n  (version 7)\n  (lib (name "Own")(type "KiCad")'
                                              f'(uri "${{KICAD{major}_FOOTPRINT_DIR}}/Own.x")(options "")(descr ""))\n)\n')
        ins = KI.install(out, "lib", environ={"KICAD_CONFIG_HOME": str(cfg)}, log=lambda *a: None)
        assert ins["versions"][series]["status"] == "installed", ins
        assert "${KICAD" in (cfg / series / "fp-lib-table").read_text()   # KiCad's own rows kept / set up
        fps = [["lib", n, str(out / "lib.pretty")] for n in sorted(rep["footprints"])]
        assert rep["models"] and all(rep["models"].values()), rep["models"]

        def job(name, env):
            (d / name).mkdir()
            r = subprocess.run([R.pcbnew_python(), str(R.JOB), "pcmcheck", json.dumps({"dir": str(d / name), "fps": fps,
                                "bogus": True})], capture_output=True, text=True, timeout=600, env=env)
            assert r.returncode == 0, r.stderr[-2000:]
            return json.loads(r.stdout.strip().splitlines()[-1])
        got = job("b1", env)
        assert all(not got.get(f"lib:{n}") for _, n, _ in fps), got               # found in the global table, equal
        assert got.get("not_a_lib:X") and got.get("lib:NOT_IN_LIB"), got           # ... and the check is live
        paths = [m for ms in got["_models"].values() for m in ms]
        assert paths and all(m.startswith("${ECADLIB_DIR_LIB}/lib.3dshapes/") for m in paths), got["_models"]
        empty = d / "cfg_empty"
        (empty / series).mkdir(parents=True)
        ctl = job("b2", dict(os.environ, KICAD_CONFIG_HOME=str(empty)))
        assert all(ctl.get(f"lib:{n}") for _, n, _ in fps), ctl                     # not installed: none found

        def solids(env):
            r = subprocess.run([R.kicad_cli(), "pcb", "export", "step", got["_board"], "-o", str(d / "b.step"), "-f"],
                               capture_output=True, text=True, timeout=600, env=env)
            assert r.returncode == 0, r.stdout + r.stderr
            return (d / "b.step").read_text(errors="replace").count("MANIFOLD_SOLID_BREP(")
        want = sum(f.read_text().count("MANIFOLD_SOLID_BREP(") for f in (out / "lib.3dshapes").glob("*.step"))
        assert want and solids(env) == want + 1                                     # every model through ${ECADLIB_DIR_LIB}
        assert solids(dict(os.environ, KICAD_CONFIG_HOME=str(empty))) == 1           # control: the board only
