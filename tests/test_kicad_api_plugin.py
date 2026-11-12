"""The ecadlib button through KiCad's plugin system (KiCad 9+): kicad_install.api_plugin writes plugins/ecadlib/
(plugin.json KiCad accepts, the starter, the window, the command, icons, requirements.txt) into each KiCad 9+ documents
folder and turns on Enable KiCad API; the window (kicad_action_plugin.py) started that way runs without pcbnew, finds
the project KiCad has open from KIPRJMOD and its KiCad version from KiCad's KICAD<N>_*_DIR variables. Offline."""
import json, os, re, sys, types
from pathlib import Path
from unittest import mock

from tests.fixtures_lite import lite
from ecadlib import kicad_install as KI

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "ecadlib" / "assets"
CMD = [sys.executable, "-m", "ecadlib"]

# KiCad's api.v1 plugin schema (kicad-python 0.8.0 kipy/packaging/schemas/api.v1.schema.json, the file KiCad 10.0.8
# ships as share/kicad/schemas/api.v1.schema.json): the parts a plugin.json must meet
ID_RE = re.compile(r"^[a-zA-Z][-_a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$")
ACTION_ID_RE = re.compile(r"^[a-zA-Z][-_a-zA-Z0-9.]{0,48}[a-zA-Z0-9]$")
SCOPES = {"pcb", "schematic", "footprint", "symbol", "project_manager", "footprint_wizard"}


def _env(tmp_path, **more):
    conf, docs = tmp_path / "conf", tmp_path / "docs"
    return {"KICAD_CONFIG_HOME": str(conf), "KICAD_DOCUMENTS_HOME": str(docs), **more}, conf, docs


def _plugin_dir(docs, series, platform=None):
    platform = platform or ("windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux")
    return docs / ("kicad" if platform == "linux" else "KiCad") / series / "plugins" / "ecadlib"


def _check_plugin_json(d):
    """plugin.json as KiCad reads it: required keys, identifiers, runtime, scopes, entry point and icons there."""
    pj = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
    for k in ("identifier", "name", "description", "runtime", "actions"):
        assert k in pj, k
    assert ID_RE.match(pj["identifier"]) and len(pj["name"]) <= 200 and len(pj["description"]) <= 500
    assert pj["runtime"]["type"] in ("python", "exec")
    if pj["runtime"]["type"] == "exec":                     # KiCad starts only an executable file
        assert all(os.access(d / a["entrypoint"], os.X_OK) for a in pj["actions"])
    for a in pj["actions"]:
        for k in ("identifier", "name", "description", "entrypoint"):
            assert k in a, k
        assert ACTION_ID_RE.match(a["identifier"]) and len(a["description"]) <= 500
        assert set(a["scopes"]) <= SCOPES and a["scopes"]
        assert not os.path.isabs(a["entrypoint"]) and (d / a["entrypoint"]).is_file()
        for icon in a["icons-light"] + a["icons-dark"]:
            assert icon.endswith(".png") and (d / icon).read_bytes().startswith(b"\x89PNG"), icon
    # KiCad's Python plugins need requirements.txt (pip installs it into the plugin's environment)
    assert (d / "requirements.txt").is_file()
    assert not [l for l in (d / "requirements.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    return pj


@lite
def test_the_button_goes_into_every_kicad_9_and_newer(tmp_path):
    env, conf, docs = _env(tmp_path)
    for s in ("7.0", "8.0", "9.0", "10.0", "11.0"):
        (conf / s).mkdir(parents=True)
    (conf / "10.0" / "kicad_common.json").write_text(json.dumps({"environment": {"vars": {"X": "1"}},
                                                                 "api": {"interpreter_path": "/usr/bin/python3"}}))
    got = KI.api_plugin(CMD, environ=env, platform="linux", log=lambda *a, **k: None)
    assert set(got) == {"9.0", "10.0", "11.0"}, got                  # KiCad 7 / 8 have no plugin system
    assert all(v == "added (turned on Enable KiCad API)" for v in got.values()), got
    for s, scopes in (("9.0", ["schematic"]), ("10.0", ["schematic"]), ("11.0", ["schematic", "pcb"])):
        d = _plugin_dir(docs, s, "linux")
        pj = _check_plugin_json(d)
        # Linux: a launcher, no virtual environment (Debian / Ubuntu leave out the pip one needs)
        assert pj["runtime"]["type"] == "exec" and pj["actions"][0]["entrypoint"] == "ecadlib_kicad.sh"
        sh = (d / "ecadlib_kicad.sh").read_text()
        assert sh.startswith("#!/bin/sh") and "ecadlib_kicad.py" in sh, sh
        # one ecadlib button in the PCB editor: the older plugin has it up to KiCad 10 (and places footprints)
        assert pj["actions"][0]["scopes"] == scopes, (s, pj)
        assert json.loads((d / "ecadlib_cmd.json").read_text())["command"] == CMD
        assert (d / "ecadlib_window.py").read_text() == (ASSETS / "kicad_action_plugin.py").read_text()
        assert (d / "ecadlib_kicad.py").read_text() == (ASSETS / "kicad_api_plugin.py").read_text()
        common = json.loads((conf / s / "kicad_common.json").read_text())
        assert common["api"]["enable_server"] is True
    common = json.loads((conf / "10.0" / "kicad_common.json").read_text())   # the rest of the settings stay
    assert common["environment"] == {"vars": {"X": "1"}} and common["api"]["interpreter_path"] == "/usr/bin/python3"
    assert not (docs / "kicad" / "8.0").exists()
    assert "exec /usr/bin/python3 " in (_plugin_dir(docs, "10.0", "linux") / "ecadlib_kicad.sh").read_text() \
        or not os.path.isfile("/usr/bin/python3")             # the Python KiCad's settings name, when it is there
    again = KI.api_plugin(CMD, environ=env, platform="linux", log=lambda *a, **k: None)
    assert set(again.values()) == {"already there"}, again


@lite
def test_windows_and_macos_use_kicads_python(tmp_path):
    env, conf, docs = _env(tmp_path)
    (conf / "9.0").mkdir(parents=True)
    for plat in ("windows", "macos"):
        assert KI.api_plugin(CMD, environ=env, platform=plat, log=lambda *a, **k: None)["9.0"].startswith(("added", "already"))
        d = _plugin_dir(docs, "9.0", plat)
        pj = _check_plugin_json(d)
        assert pj["runtime"]["type"] == "python" and pj["actions"][0]["entrypoint"] == "ecadlib_kicad.py"
        assert not (d / "ecadlib_kicad.sh").exists()


@lite
def test_one_version_and_a_kicad_never_started(tmp_path):
    env, conf, docs = _env(tmp_path)
    (conf / "9.0").mkdir(parents=True)
    assert KI.api_plugin(CMD, "10.0", environ=env, log=lambda *a, **k: None) == {
        "10.0": "not changed: KiCad 10.0 has no settings yet (start it once, then do this again)"}
    assert not (docs / "kicad" / "10.0").exists()
    assert set(KI.api_plugin(CMD, "9.0", environ=env, log=lambda *a, **k: None)) == {"9.0"}
    assert KI.api_plugin(CMD, "8.0", environ=env, log=lambda *a, **k: None) == {}
    (conf / "9.0" / "kicad_common.json").write_text("{ not json")
    (conf / "10.0").mkdir()
    (conf / "10.0" / "kicad_common.json").write_text("{ not json")
    got = KI.api_plugin(CMD, "10.0", environ=env, log=lambda *a, **k: None)
    assert got["10.0"].startswith("not changed:") and "not valid JSON" in got["10.0"], got
    assert (conf / "10.0" / "kicad_common.json").read_text() == "{ not json"     # never overwritten


@lite
def test_remove_takes_out_only_our_files_and_leaves_the_api_on(tmp_path):
    env, conf, docs = _env(tmp_path)
    (conf / "10.0").mkdir(parents=True)
    KI.api_plugin(CMD, environ=env, log=lambda *a, **k: None)
    d = _plugin_dir(docs, "10.0")
    (d / "notes.txt").write_text("mine")
    assert KI.api_plugin(CMD, remove=True, environ=env, log=lambda *a, **k: None) == {"10.0": "removed"}
    assert sorted(p.name for p in d.iterdir()) == ["notes.txt"]
    (d / "notes.txt").unlink()
    KI.api_plugin(CMD, environ=env, log=lambda *a, **k: None)
    KI.api_plugin(CMD, remove=True, environ=env, log=lambda *a, **k: None)
    assert not d.exists()
    assert KI.api_plugin(CMD, remove=True, environ=env, log=lambda *a, **k: None) == {"10.0": "not there"}
    assert json.loads((conf / "10.0" / "kicad_common.json").read_text())["api"]["enable_server"] is True


def test_documents_folder_per_platform_and_flatpak():
    home = Path("/home/u")
    assert KI.documents_dir("10.0", environ={}, home=home, platform="linux") == home / ".local/share/kicad/10.0"
    assert KI.documents_dir("10.0", environ={"XDG_DATA_HOME": "/x"}, home=home, platform="linux") == Path("/x/kicad/10.0")
    assert KI.documents_dir("9.0", environ={}, home=home, platform="macos") == home / "Documents/KiCad/9.0"
    flat = home / ".var/app/org.kicad.KiCad/config/kicad/9.0"
    assert KI.documents_dir("9.0", flat, environ={}, home=home, platform="linux") == \
        home / ".var/app/org.kicad.KiCad/data/kicad/9.0"
    assert KI.documents_dir("9.0", environ={"KICAD_DOCUMENTS_HOME": "/d"}, platform="linux") == Path("/d/kicad/9.0")
    assert KI.documents_dir("9.0", environ={"KICAD_DOCUMENTS_HOME": "/d"}, platform="windows") == Path("/d/KiCad/9.0")


# ------------------------------------------------------------------ the window started by KiCad's plugin system
def _window_module(env=None):
    """kicad_action_plugin.py loaded as KiCad's plugin system starts it: ECADLIB_KICAD_API_PLUGIN set, no pcbnew (an
    import of it fails, as in KiCad 11)."""
    src = ASSETS / "kicad_action_plugin.py"

    class NoPcbnew:
        def find_module(self, name, path=None):
            return None

        def find_spec(self, name, path=None, target=None):
            if name == "pcbnew":
                raise ImportError("no pcbnew here")
            return None

    blocker = NoPcbnew()
    sys.meta_path.insert(0, blocker)
    try:
        with mock.patch.dict(os.environ, dict(env or {}, ECADLIB_KICAD_API_PLUGIN="1")), \
                mock.patch.dict(sys.modules, {k: v for k, v in sys.modules.items() if k != "pcbnew"}, clear=True):
            sys.modules.pop("pcbnew", None)
            mod = types.ModuleType("ecadlib_window")
            mod.__file__ = str(src)
            exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), mod.__dict__)
    finally:
        sys.meta_path.remove(blocker)
    return mod


def test_window_without_pcbnew_registers_nothing_and_reads_kicads_version():
    mod = _window_module()
    assert mod.pcbnew is None and mod.API
    assert mod._ActionPlugin is object                          # the classes still load, nothing registers
    assert mod.kicad_major({"KICAD10_SYMBOL_DIR": "/s", "KICAD10_3DMODEL_DIR": "/m", "KICAD9_FOOTPRINT_DIR": "/f",
                            "KICAD_API_SOCKET": "ipc://x"}) == 10
    assert mod.kicad_major({"KICAD_API_SOCKET": "ipc://x"}) is None
    assert mod.kicad_major({"KICAD11_SYMBOL_DIR": "/s"}) == 11
    line = mod.place_yourself({"part": "LM358DR", "footprint": "SOIC-8_3.9x4.9mm_P1.27mm"})
    assert "ecadlib:LM358DR" in line and "ecadlib:SOIC-8" in line and "Add Symbol" in line, line


@lite
def test_window_finds_the_project_kicad_has_open(tmp_path):
    mod = _window_module()
    d = tmp_path / "amp"
    d.mkdir()
    assert mod.api_project({}) == (None, None, None)
    assert mod.api_project({"KIPRJMOD": str(tmp_path / "gone")}) == (None, None, None)
    assert mod.api_project({"KIPRJMOD": str(d)}) == (None, None, None)       # a schematic opened outside a project
    (d / "amp.kicad_pro").write_text("{}")
    assert mod.api_project({"KIPRJMOD": str(d)}) == (None, str(d), str(d / "amp.kicad_pro"))
    (d / "amp_v2.kicad_pro").write_text("{}")
    assert mod.api_project({"KIPRJMOD": str(d)})[2] == str(d / "amp.kicad_pro")       # the one named like the folder
    (d / "amp.kicad_pro").unlink()
    (d / "other.kicad_pro").write_text("{}")
    try:
        mod.api_project({"KIPRJMOD": str(d)})
        raise AssertionError("picked one of two projects")
    except ValueError as e:
        assert "several KiCad projects" in str(e) and "Next:" in str(e), e
    (d / "other.kicad_pro").unlink()
    with mock.patch.dict(os.environ, {"KIPRJMOD": str(d)}):
        assert mod.board_project() == (None, str(d), str(d / "amp_v2.kicad_pro"))
    # the job's --project is what kicad_menu reads: the schematic next to that project file
    from ecadlib import kicad_menu
    (d / "amp_v2.kicad_sch").write_text("(kicad_sch)")
    assert kicad_menu.schematic_of(str(d / "amp_v2.kicad_pro")) == d / "amp_v2.kicad_sch"
    assert kicad_menu.global_nick(str(d / "amp_v2.kicad_pro")) == "ecadlib_amp_v2"


def test_pcb_editor_copy_still_registers_with_pcbnew():
    from tests.test_kicad_plugin_review import _fake_pcbnew, _plugin
    fake = _fake_pcbnew(PLUGIN_DIRECTORIES_SEARCH="pass 1")
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ECADLIB_KICAD_API_PLUGIN", None)
        mod = _plugin(fake)
    assert mod.pcbnew is fake and not mod.API and len(fake.registered) == 4


def test_starter_opens_the_window_for_kicads_project():
    """ecadlib_kicad.py: sets the plugin mode, opens the window from the module next to it, runs wx's loop only when
    a window opened, and shows (not loses) a failure."""
    src = ASSETS / "kicad_api_plugin.py"
    calls = []

    class App:
        def __init__(self, redirect):
            calls.append(("app", redirect))

        def MainLoop(self):
            calls.append(("loop",))

    fake_wx = types.SimpleNamespace(App=App, MessageBox=lambda text, title: calls.append(("box", text)))
    for opened in (object(), None):
        calls.clear()
        win = types.SimpleNamespace(open_window=lambda mode: calls.append(("open", mode, os.environ.get(
            "ECADLIB_KICAD_API_PLUGIN"))) or opened)
        with mock.patch.dict(sys.modules, {"wx": fake_wx, "ecadlib_window": win}), \
                mock.patch.dict(os.environ, {}), mock.patch.object(sys, "argv", ["ecadlib_kicad.py"]):
            mod = types.ModuleType("ecadlib_kicad")
            mod.__file__ = str(src)
            exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), mod.__dict__)
            assert mod.main() == 0
        assert ("open", "home", "1") in calls and (("loop",) in calls) == (opened is not None), calls

    def boom(mode):
        raise RuntimeError("broken")
    calls.clear()
    with mock.patch.dict(sys.modules, {"wx": fake_wx, "ecadlib_window": types.SimpleNamespace(open_window=boom)}), \
            mock.patch.dict(os.environ, {}), mock.patch.object(sys, "argv", ["ecadlib_kicad.py"]):
        mod = types.ModuleType("ecadlib_kicad")
        mod.__file__ = str(src)
        exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), mod.__dict__)
        assert mod.main() == 1
    assert any(c[0] == "box" and "broken" in c[1] for c in calls), calls


@lite
def test_add_to_schematic_command_adds_the_button_and_the_bom_entries(tmp_path):
    from ecadlib import kicad_menu
    env, conf, docs = _env(tmp_path)
    (conf / "10.0").mkdir(parents=True)
    with mock.patch.dict(os.environ, env):
        assert kicad_menu.main(["add-to-schematic"]) == 0
        assert (_plugin_dir(docs, "10.0") / "plugin.json").is_file()
        assert (conf / "10.0" / "eeschema.json").is_file()
        assert kicad_menu.main(["add-to-schematic", "--remove"]) == 0
    assert not _plugin_dir(docs, "10.0").exists()


@lite
def test_setup_page_add_to_kicad_reports_the_button(tmp_path):
    from ecadlib import detect as DT, easy_setup as ES, userconfig as UC
    env, conf, docs = _env(tmp_path)
    (conf / "10.0").mkdir(parents=True)
    with mock.patch.object(DT, "kicad", lambda *a, **k: [{"series": "10.0"}]), \
            mock.patch.object(DT, "kicad_plugin_dir", lambda s, **k: tmp_path / "swig" / s), \
            mock.patch.object(ES, "_record_install", lambda *a, **k: None), \
            mock.patch.dict(os.environ, env):
        r = ES.install_kicad_plugin()
    assert r["api_plugin"] == {"10.0": "added (turned on Enable KiCad API)"}, r
    assert any("ecadlib button at the right end" in s for s in r["steps"]), r["steps"]
    assert any("Enable KiCad API" in s for s in r["steps"]), r["steps"]
    assert json.loads((_plugin_dir(docs, "10.0") / "ecadlib_cmd.json").read_text())["command"] == UC.self_command()



def _kicad_schema():
    """KiCad's own api.v1.schema.json (share/kicad/schemas), when a KiCad 9+ is installed here; else None."""
    roots = [Path("/usr/share/kicad/schemas"), Path("/usr/local/share/kicad/schemas"),
             Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/schemas")]
    pf = os.environ.get("ProgramFiles")
    if pf:
        roots += sorted(Path(pf, "KiCad").glob("*/share/kicad/schemas"), reverse=True)
    return next((r / "api.v1.schema.json" for r in roots if (r / "api.v1.schema.json").is_file()), None)


@lite
def test_plugin_json_passes_the_schema_kicad_ships(tmp_path):
    """Checked against the schema file the installed KiCad validates plugins with (the CI kicad job: KiCad 9 and 10)."""
    schema = _kicad_schema()
    if schema is None:
        print("SKIP: no KiCad 9+ (share/kicad/schemas/api.v1.schema.json) here")
        return
    import jsonschema
    js = json.loads(schema.read_text(encoding="utf-8"))
    for plat in ("linux", "windows"):
        for series in ("9.0", "10.0", "11.0"):
            jsonschema.validate(KI.api_plugin_json(series, plat), js)


@lite
def test_launcher_python_flatpak_and_a_launcher_made_executable_again(tmp_path):
    conf = tmp_path / "c"
    conf.mkdir()
    fallback = "/usr/bin/python3" if os.path.isfile("/usr/bin/python3") else "python3"
    assert KI.kicad_python(conf) == fallback                              # no settings file
    (conf / "kicad_common.json").write_text("{ bad")
    assert KI.kicad_python(conf) == fallback
    (conf / "kicad_common.json").write_text(json.dumps({"api": {"interpreter_path": str(tmp_path / "gone")}}))
    assert KI.kicad_python(conf) == fallback
    (conf / "kicad_common.json").write_text(json.dumps({"api": {"interpreter_path": sys.executable}}))
    assert KI.kicad_python(conf) == sys.executable
    env, confs, docs = _env(tmp_path)
    (confs / "10.0").mkdir(parents=True)
    KI.api_plugin(CMD, environ=env, platform="linux", log=lambda *a, **k: None)
    sh = _plugin_dir(docs, "10.0", "linux") / "ecadlib_kicad.sh"
    if os.name != "nt":
        sh.chmod(0o644)
        assert KI.api_plugin(CMD, environ=env, platform="linux", log=lambda *a, **k: None) == {"10.0": "added"}
        assert os.access(sh, os.X_OK)
    flat = tmp_path / "home" / ".var" / "app" / "org.kicad.KiCad" / "config" / "kicad"
    (flat / "9.0").mkdir(parents=True)
    got = KI.api_plugin(CMD, environ={"KICAD_CONFIG_HOME": str(flat)}, home=tmp_path / "home", platform="linux",
                        log=lambda *a, **k: None)
    assert got["9.0"].startswith("not changed: the Flatpak"), got
    assert not (tmp_path / "home" / ".var" / "app" / "org.kicad.KiCad" / "data").exists()


def test_window_version_from_its_plugins_folder():
    mod = _window_module()
    mod.HERE = os.path.join("/home/u/.local/share/kicad", "9.0", "plugins", "ecadlib")
    with mock.patch.dict(os.environ, {"KICAD10_SYMBOL_DIR": "/s"}):
        assert mod.kicad_major() == 9                       # the folder it was installed for, not a stray variable
    mod.HERE = "/somewhere/else"
    with mock.patch.dict(os.environ, {"KICAD10_SYMBOL_DIR": "/s"}):
        assert mod.kicad_major() == 10


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main([Path(__file__).stem]))
