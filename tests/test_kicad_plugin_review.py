"""The KiCad PCB editor plugin (ecadlib/assets/kicad_action_plugin.py) and what its jobs do (kicad_menu.run_job):
library tables written by the job whatever their layout, a finished job placed after the window was closed, failures
of the review queue shown, the python probe outside the project folder, the placed footprint's reference and value,
KiCad 7 library files, the PCM zip's command file and one registration for two installed copies. Offline."""
import json, os, re, shutil, subprocess, sys, tempfile, types, zipfile
from pathlib import Path
from unittest import mock

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "ecadlib" / "assets" / "kicad_action_plugin.py"

ML = ('(sym_lib_table\n\t(version 7)\n\t(lib\n\t\t(name "ecadlib")\n\t\t(type "KiCad")\n'
      '\t\t(uri "${KIPRJMOD}/ecadlib/ecadlib.kicad_sym")\n\t\t(options "")\n\t\t(descr "")\n\t)\n'
      '\t(lib\n\t\t(name "Other")\n\t\t(type "KiCad")\n\t\t(uri "x.kicad_sym")\n\t\t(options "")\n\t\t(descr "")\n\t)\n)\n')


def _fake_pcbnew(**kw):
    fake = types.ModuleType("pcbnew")
    fake.registered = []
    fake.ActionPlugin = type("ActionPlugin", (), {"register": lambda self: fake.registered.append(self)})
    fake.__dict__.update(kw)
    return fake


def _plugin(fake=None, src=SRC):
    fake = fake or _fake_pcbnew()
    with mock.patch.dict(sys.modules, {"pcbnew": fake}):
        mod = types.ModuleType("ecadlib_kicad_action_plugin")
        mod.__file__ = str(src)
        exec(compile(Path(src).read_text(encoding="utf-8"), str(src), "exec"), mod.__dict__)
    return mod


def _rows(text, name="ecadlib"):
    from ecadlib import sexpr
    return [l for l in sexpr.find(sexpr.parse(text), "lib") if str(sexpr.first(l, "name")[1]) == name]


def _built(d):
    (d / "ecadlib" / "ecadlib.pretty").mkdir(parents=True)
    (d / "ecadlib" / "ecadlib.kicad_sym").write_text("(kicad_symbol_lib (version 20231120))\n")


# 1: a pretty-printed ecadlib row is found (no duplicate nickname, which makes KiCad refuse the whole table)
@lite
def test_plugin_tables_find_a_multi_line_row(tmp_path):
    kap = _plugin()
    (tmp_path / "sym-lib-table").write_text(ML)
    (tmp_path / "fp-lib-table").write_text(ML.replace("sym_lib", "fp_lib").replace("ecadlib.kicad_sym", "ecadlib.pretty")
                                           .replace("x.kicad_sym", "x.pretty"))
    assert kap.ensure_tables(str(tmp_path)) == []
    assert len(_rows((tmp_path / "sym-lib-table").read_text())) == 1
    (tmp_path / "sym-lib-table").write_text(ML.replace("${KIPRJMOD}/ecadlib/ecadlib.kicad_sym", "C:/libs/other.kicad_sym"))
    try:
        kap.ensure_tables(str(tmp_path))
        raise AssertionError("kept another library's multi-line ecadlib row")
    except ValueError as e:
        assert "C:/libs/other.kicad_sym" in str(e), e


@lite
def test_job_lists_the_library_in_the_project_tables(tmp_path):
    from ecadlib import kicad_menu as KM
    cfg = KM.config(tmp_path / "board.kicad_pcb")
    assert KM.project_tables(cfg) == {"tables": ""}                  # nothing built: tables left alone
    _built(tmp_path)
    (tmp_path / "sym-lib-table").write_text(ML)
    assert KM.project_tables(cfg) == {"tables": "added"}             # fp-lib-table made, sym-lib-table kept as is
    assert (tmp_path / "sym-lib-table").read_text() == ML
    fp = (tmp_path / "fp-lib-table").read_text()
    assert len(_rows(fp)) == 1 and '"${KIPRJMOD}/ecadlib/ecadlib.pretty"' in fp
    assert KM.project_tables(cfg) == {"tables": "listed"}
    assert len(_rows((tmp_path / "fp-lib-table").read_text())) == 1
    (tmp_path / "fp-lib-table").write_text('(fp_lib_table\n  (version 7)\n  (lib (name "ecadlib")(type "KiCad")'
                                           '(uri "${KIPRJMOD}/elsewhere.pretty")(options "")(descr ""))\n)\n')
    r = KM.project_tables(cfg)
    assert r["tables"] == "problem" and "${KIPRJMOD}/elsewhere.pretty" in r["tables_note"], r
    assert "elsewhere.pretty" in (tmp_path / "fp-lib-table").read_text()          # left alone


# 2: the job writes the tables (window closed mid-job loses nothing); a finished, unplaced job is offered again
@lite
def test_run_job_writes_tables_and_kicad7_files(tmp_path):
    from ecadlib import kicad_menu as KM, altium_menu as AM
    from tests import test_kicad_menu as TK
    d = TK._project()
    job = d / KM.WORK / "jobs" / "fill"
    res = KM.run_job(job, "fill", str(d / "board.kicad_pcb"), offline=True, kicad=7)
    assert res["status"] == "ok" and res["tables"] == "added", res
    assert len(_rows((d / "sym-lib-table").read_text())) == 1 and len(_rows((d / "fp-lib-table").read_text())) == 1
    sym = (d / "ecadlib" / "ecadlib.kicad_sym").read_text()
    assert "(version 20220914)" in sym and "generator_version" not in sym, sym[:200]
    for f in (d / "ecadlib" / "ecadlib.pretty").glob("*.kicad_mod"):
        v = int(re.search(r"\(version (\d+)\)", f.read_text()).group(1))
        assert v <= 20221018, (f.name, v)
    res = KM.run_job(d / KM.WORK / "jobs" / "add", "add-part", str(d / "board.kicad_pcb"), mpn="RC0603FR-0710KL",
                     manufacturer="Yageo", offline=True, kicad=7)
    assert res["status"] == "ready" and res["footprint"] == "R_0603_1608Metric" and res["tables"] == "listed", res
    assert res["reference"] == "R" and res["value"] and res["value"] != res["footprint"], res
    rep = json.loads((d / "ecadlib" / "kicad_export.json").read_text())
    assert not any("edited since the last export" in w for w in rep.get("warnings", [])), rep.get("warnings")
    assert AM.read_ini(job / "result.ini")["result"]["tables"] == "added"
    # the KiCad 8 window keeps KiCad 8 files
    res = KM.run_job(d / KM.WORK / "jobs" / "fill8", "fill", str(d / "board.kicad_pcb"), offline=True, kicad=8)
    assert "(version 20231120)" in (d / "ecadlib" / "ecadlib.kicad_sym").read_text()
    assert KM.main(["run-job", str(d / KM.WORK / "jobs" / "cli"), "--action=open", "--project=" + str(d), "--kicad=7",
                    "--path=" + str(d / "nothing.html")]) == 1        # --kicad is accepted


@lite
def test_window_finds_a_finished_job_it_did_not_place(tmp_path):
    from ecadlib import altium_menu as AM
    kap = _plugin()
    job = kap.new_job(str(tmp_path), "add-part")
    kap.mark_pending(job)
    assert kap.unplaced_jobs(str(tmp_path)) == []                     # still working: nothing to place yet
    AM.write_result(Path(job) / "result.ini", {"status": "ready", "footprint": "R_0603_1608Metric", "mpn": "RC0603",
                                               "reference": "R", "value": "10k"})
    (Path(job) / "exit.txt").write_text("0\n")
    got = kap.unplaced_jobs(str(tmp_path))
    assert [g[0] for g in got] == [job] and got[0][1]["footprint"] == "R_0603_1608Metric", got
    kap.mark_placed(job)
    assert kap.unplaced_jobs(str(tmp_path)) == []
    held = kap.new_job(str(tmp_path), "add-part")
    kap.mark_pending(held)
    AM.write_result(Path(held) / "result.ini", {"status": "held", "mpn": "X"})
    (Path(held) / "exit.txt").write_text("0\n")
    assert kap.unplaced_jobs(str(tmp_path)) == []
    src = SRC.read_text()
    assert "ecadlib finishes in the background" not in src


# 3: a job that fails is shown, not dropped (no result, or an error result)
@lite
def test_job_failure_text(tmp_path):
    from ecadlib import altium_menu as AM
    kap = _plugin()
    job = tmp_path / "j"
    job.mkdir()
    (job / "log.txt").write_text("Traceback: boom\n")
    (job / "exit.txt").write_text("1\n")
    msg = kap.job_failure(str(job))
    assert "stopped without a result" in msg and "boom" in msg, msg
    AM.write_result(job / "result.ini", {"status": "error", "message": "port 8765 is used by another program",
                                         "fix": "close the program on that port"})
    msg = kap.job_failure(str(job))
    assert "port 8765" in msg and "Next: close the program" in msg, msg
    AM.write_result(job / "result.ini", {"status": "ok", "message": "review queue: http://x"})
    assert kap.job_failure(str(job)) == ""
    src = SRC.read_text()
    assert "stdout=subprocess.DEVNULL" not in src                     # every job's output goes to its log


# 4: the python probe does not run in the project folder (its ecadlib/ library imports as a namespace package)
@lite
def test_python_probe_runs_outside_the_project(tmp_path, monkeypatch):
    kap = _plugin()
    (tmp_path / "ecadlib").mkdir()                                    # a project folder after a fill
    (tmp_path / "ecadlib" / "ecadlib.kicad_sym").write_text("")
    monkeypatch.chdir(tmp_path)
    seen = []

    def run(c, **kw):
        seen.append((c, kw.get("cwd")))
        return subprocess.run(c, **kw)
    bare = tmp_path / "bin"
    bare.mkdir()
    py = shutil.which("python3") or sys.executable
    # a python that has no ecadlib package: PYTHONPATH cleared, site-packages off (-S via a wrapper script)
    wrapper = bare / "python3"
    wrapper.write_text(f'#!/bin/sh\nPYTHONPATH= exec "{py}" -S "$@"\n')
    wrapper.chmod(0o755)
    if os.name == "nt":
        return
    got = kap.ecadlib_command({}, which=lambda n: str(wrapper) if n == "python3" else None, run=run, cmd_file="/nonexistent")
    assert got is None, got
    assert seen and all(cwd and Path(cwd).resolve() != tmp_path.resolve() for _, cwd in seen), seen


# 5: the placed footprint gets the next free reference and the part's value
def test_next_reference():
    kap = _plugin()
    assert kap.next_reference(["R1", "R2", "U1", "R10", "RN3", "REF**"], "R") == "R11"
    assert kap.next_reference([], "U") == "U1"
    assert kap.next_reference(["C1"], "") == "U1"


FP = """(footprint "R_0603_1608Metric"
\t(version 20221018)
\t(generator "ecadlib_footprint_gen")
\t(layer "F.Cu")
\t(attr smd)
\t(fp_text reference "REF**" (at 0 -1.43) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
\t(fp_text value "R_0603_1608Metric" (at 0 1.43) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
\t(pad "1" smd roundrect (at -0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
\t(pad "2" smd roundrect (at 0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
)
"""


def test_placed_footprint_in_real_pcbnew():
    from tests import test_kicad_real as R
    py = R.pcbnew_python()
    if not py:
        return R._skip("pcbnew (KiCad's Python module)")
    d = Path(tempfile.mkdtemp(prefix="ecadlib-place-"))
    try:
        pretty = d / "ecadlib" / "ecadlib.pretty"
        pretty.mkdir(parents=True)
        (pretty / "R_0603_1608Metric.kicad_mod").write_text(FP)
        code = ("import json, sys, types, pcbnew\n"
                f"src = {str(SRC)!r}\n"
                "mod = types.ModuleType('kplug'); mod.__file__ = src\n"
                "pcbnew.ActionPlugin.register = lambda self: None\n"
                "pcbnew.Refresh = lambda: None\n"
                "exec(compile(open(src).read(), src, 'exec'), mod.__dict__)\n"
                "b = pcbnew.BOARD()\n"
                f"for i in range(2): mod.place(b, {str(d)!r}, 'R_0603_1608Metric', 'R', '10k')\n"
                "print(json.dumps(sorted([f.GetReference(), f.GetValue()] for f in b.GetFootprints())))\n")
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=300, cwd=tempfile.gettempdir())
        assert r.returncode == 0, r.stderr[-2000:]
        assert json.loads(r.stdout.strip().splitlines()[-1]) == [["R1", "10k"], ["R2", "10k"]], r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


# 6: the PCM zip carries the command file; two installed copies register once
def test_pcm_plugin_zip_has_the_command_file():
    from ecadlib import kicad_pcm as KP
    d = Path(tempfile.mkdtemp(prefix="ecadlib-pcmplug-"))
    try:
        rep = KP.plugin_build(d)
        z = zipfile.ZipFile(rep["zip"])
        assert "plugins/ecadlib_cmd.json" in z.namelist(), z.namelist()
        cmd = json.loads(z.read("plugins/ecadlib_cmd.json"))["command"]
        assert isinstance(cmd, list) and cmd and os.path.isfile(cmd[0]), cmd
    finally:
        shutil.rmtree(d, ignore_errors=True)


@lite
def test_two_installed_copies_register_once(tmp_path):
    a, b = tmp_path / "scripting" / "ecadlib_add_part", tmp_path / "3rdparty" / "local_ecadlib_add-part"
    for x in (a, b):
        x.mkdir(parents=True)
        shutil.copyfile(SRC, x / "__init__.py")
    fake = _fake_pcbnew(PLUGIN_DIRECTORIES_SEARCH="pass 1")
    _plugin(fake, a / "__init__.py")
    _plugin(fake, b / "__init__.py")
    assert len(fake.registered) == 4, len(fake.registered)             # the first copy's four entries only
    fake.registered.clear()
    fake.PLUGIN_DIRECTORIES_SEARCH = "".join(["pass ", "2"])            # Refresh Plugins: a new loading pass
    _plugin(fake, a / "__init__.py")
    _plugin(fake, b / "__init__.py")
    assert len(fake.registered) == 4, len(fake.registered)


# 7: the KiCad version goes to the job
def test_job_command_passes_the_kicad_version():
    kap = _plugin(_fake_pcbnew(Version=lambda: "7.0.11"))
    assert kap.kicad_major() == 7
    assert "--kicad=7" in kap.job_command(["e"], "J", "fill", "b.kicad_pcb", kicad=7)
    assert not any(a.startswith("--kicad") for a in kap.job_command(["e"], "J", "fill", "b.kicad_pcb"))
    assert _plugin(_fake_pcbnew(Version=lambda: "(9.0.1-rc2)")).kicad_major() == 9
    assert _plugin(_fake_pcbnew()).kicad_major() is None


@lite
def test_child_env_drops_kicads_python_settings(tmp_path):
    """ecadlib starts without the PYTHONHOME / PYTHONPATH KiCad sets for its own Python (on Linux the system's older
    numpy / pandas came first and ecadlib stopped at start); the PYTHONPATH saved when it was added is used."""
    kap = _plugin()
    kicad = {"PATH": "/usr/bin", "PYTHONPATH": "/repo:/usr/bin/../lib/python3/dist-packages", "PYTHONHOME": "/k"}
    env = kap.child_env(kicad, cmd_file=str(tmp_path / "none.json"))
    assert env == {"PATH": "/usr/bin"}, env
    (tmp_path / "c.json").write_text(json.dumps({"command": ["x"], "pythonpath": "/repo"}))
    assert kap.child_env(kicad, cmd_file=str(tmp_path / "c.json")) == {"PATH": "/usr/bin", "PYTHONPATH": "/repo"}
