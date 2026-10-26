"""The no-terminal setup: stored keys (ecadlib.userconfig), finding Altium / KiCad (ecadlib.detect), the setup page's
logic and routes (ecadlib.easy_setup, ui/server.py), the desktop app (ecadlib.launcher) and the quieter doctor.
Offline: key checks use a fake HTTP get. Run:  python3 -m tests.test_easy_setup"""
import contextlib, http.client, importlib.util, io, json, os, stat, sys, threading, time, types
from pathlib import Path
from unittest import mock

from ecadlib import detect as DT, doctor as DR, easy_setup as ES, launcher as LA, userconfig as UC
from ecadlib.app import Workspace
from ecadlib.ui import server as S
from tests.fixtures_lite import clean_all, scratch


def _tmp():
    return scratch("ecadlib-setup-")


@contextlib.contextmanager
def _home():
    """A fresh per-user folder (keys.json, settings.json) and an environment without AI keys."""
    d = _tmp()
    keep = {k: os.environ.get(k) for k in ("ECADLIB_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NEXAR_TOKEN")}
    os.environ["ECADLIB_HOME"] = str(d / "home")
    for k in keep:
        if k != "ECADLIB_HOME":
            os.environ.pop(k, None)
    try:
        yield d
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ------------------------------------------------------------------ userconfig
def test_keys_are_stored_privately_and_never_listed_by_value():
    with _home() as d:
        UC.set_key("ANTHROPIC_API_KEY", "  sk-ant-secret-1  ")
        f = Path(os.environ["ECADLIB_HOME"]) / UC.KEYS
        assert "sk-ant-secret-1" not in f.read_text()           # encoded (DPAPI on Windows), never the plain text
        if os.name != "nt":
            assert stat.S_IMODE(f.stat().st_mode) == 0o600
        assert UC.stored_names() == ["ANTHROPIC_API_KEY"] and UC.get_key("ANTHROPIC_API_KEY") == "sk-ant-secret-1"
        for bad in ("", "a\nb", "x" * 5000):
            try:
                UC.set_key("ANTHROPIC_API_KEY", bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        for bad in ("1ABC", "A-B", "", "A B"):
            try:
                UC.set_key(bad, "v")
                raise AssertionError(bad)
            except ValueError:
                pass
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert UC.main(["list"], out=print) == 0
        assert "ANTHROPIC_API_KEY" in out.getvalue() and "sk-ant" not in out.getvalue()
        assert UC.remove_key("ANTHROPIC_API_KEY") and not UC.remove_key("ANTHROPIC_API_KEY")


def test_stored_keys_are_read_but_never_put_into_the_environment():
    with _home():
        UC.set_key("ANTHROPIC_API_KEY", "stored")
        UC.set_key("NEXAR_TOKEN", "stored-nexar")
        env = {"NEXAR_TOKEN": "from-env", "PATH": ""}
        assert UC.apply_env(env, config_environ=os.environ) == []
        assert "ANTHROPIC_API_KEY" not in env and env["NEXAR_TOKEN"] == "from-env"            # nothing exported
        assert "ANTHROPIC_API_KEY" not in os.environ
        assert UC.secret("ANTHROPIC_API_KEY", env) == "stored"                               # read from the store
        assert UC.secret("NEXAR_TOKEN", env) == "stored-nexar"                               # the store comes first
        assert UC.secret("OPENAI_API_KEY", {"OPENAI_API_KEY": "env-only"}) == "env-only"      # else the environment
        assert UC.key_source("ANTHROPIC_API_KEY", env) == "stored"
        assert UC.key_source("OPENAI_API_KEY", {"OPENAI_API_KEY": "x"}) == "environment"
        from ecadlib import cli
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--version"])
        assert "ANTHROPIC_API_KEY" not in os.environ                                         # entry points export nothing
        with mock.patch.dict(os.environ, {UC.NO_STORED: "1"}):                                # the eval gate: store ignored
            assert UC.secret("ANTHROPIC_API_KEY", {}) is None
        rep = DR.check_key_safety({}, {"ANTHROPIC_API_KEY": "sk-env"})
        assert not rep                                                                        # stored one is used: fine
        UC.remove_key("ANTHROPIC_API_KEY")
        rep = DR.check_key_safety({}, {"ANTHROPIC_API_KEY": "sk-env"})
        assert rep and rep[0]["level"] == "WARN" and "sk-env" not in json.dumps(rep), rep
        Path(os.environ["ECADLIB_HOME"], UC.KEYS).write_text("{broken")
        assert UC.apply_env({"PATH": ""}, config_environ=os.environ) == []                      # never raises
        assert UC.secret("NEXAR_TOKEN", {}) is None


def test_keys_cli_set_from_stdin_and_the_cli_uses_it():
    with _home():
        with mock.patch.object(sys, "stdin", io.StringIO("sk-from-stdin\n")), contextlib.redirect_stdout(io.StringIO()):
            assert UC.main(["set", "OPENAI_API_KEY", "--stdin"]) == 0
        assert UC.get_key("OPENAI_API_KEY") == "sk-from-stdin"
        from ecadlib import cli
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--version"])
            cli.main(["keys", "list"])
        assert os.environ.get("OPENAI_API_KEY") is None                    # never exported to the environment
        from ecadlib import llm as LL
        ep = LL.endpoint({"ai": {"provider": "openai", "model": "gpt-5"}})
        assert LL.key_for(ep) == "sk-from-stdin"                          # but every ecadlib command reads it


def test_self_command_source_and_frozen():
    assert UC.self_command() == [sys.executable, "-m", "ecadlib"]
    d = _tmp()
    app = d / ("ecadlib-app.exe" if os.name == "nt" else "ecadlib-app")
    cli = d / ("ecadlib.exe" if os.name == "nt" else "ecadlib")
    app.write_text("")
    with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(app)):
        assert UC.self_command() == [str(app)]                   # no console sibling: itself
        cli.write_text("")
        assert UC.self_command() == [str(cli)]                   # the console ecadlib.exe next to the app


# ------------------------------------------------------------------ detect
def test_detect_altium_and_kicad_in_a_fake_program_files():
    pf = _tmp()
    for ver in ("AD23", "AD24"):
        (pf / "Altium" / ver).mkdir(parents=True)
        (pf / "Altium" / ver / "X2.EXE").write_text("")
    (pf / "Altium" / "Other").mkdir()
    for ver in ("8.0", "9.0"):
        b = pf / "KiCad" / ver / "bin"
        b.mkdir(parents=True)
        (b / "kicad.exe").write_text("")
        (b / "kicad-cli.exe").write_text("")
    al = DT.altium(program_files=[pf], registry=[])
    assert [a["version"] for a in al] == ["24", "23"] and al[0]["name"] == "Altium Designer 24"
    reg = [{"ProgramsInstallPath": str(pf / "Altium" / "AD24"), "Version": "24.9.1", "ProductName": "Altium Designer"}]
    assert DT.altium(program_files=[pf], registry=reg)[0]["version"] == "24.9.1"
    ki = DT.kicad(program_files=[pf], applications=[], which=lambda x: None)
    assert [k["series"] for k in ki] == ["9.0", "8.0"] and ki[0]["cli"].endswith("kicad-cli.exe")
    assert DT.kicad(program_files=[], applications=[], which=lambda x: None) == []
    assert DT.altium(program_files=[_tmp()], registry=[]) == []


def test_kicad_user_folders_per_platform():
    home = _tmp()
    assert DT.kicad_plugin_dir("8.0", home=home, environ={}, platform="linux") == home / ".local/share/kicad/8.0/scripting/plugins"
    assert DT.kicad_plugin_dir("8.0", home=home, environ={"XDG_DATA_HOME": "/x"}, platform="linux") == Path("/x/kicad/8.0/scripting/plugins")
    assert DT.kicad_plugin_dir("9.0", home=home, environ={}, platform="macos") == home / "Documents/KiCad/9.0/scripting/plugins"
    assert DT.kicad_plugin_dir("9.0", home=home, environ={}, platform="windows") == home / "Documents/KiCad/9.0/scripting/plugins"


# ------------------------------------------------------------------ the setup page's logic
def test_save_writes_the_workspace_rules_and_settings():
    with _home() as d:
        lib = d / "mylib"
        lib.mkdir()
        ws = d / "ws"
        r = ES.save(d / "running", {"provider": "openai", "model": "gpt-5-mini", "tool": "kicad", "workspace": str(ws),
                                     "libs": [str(lib)]})
        assert r["saved"] and r["restart"] and not r["can_restart"] and r["workspace"] == str(ws.resolve())
        from ecadlib import rules as RU
        rules = RU.load(ws / "ecadlib.toml")
        assert rules["ai"]["provider"] == "openai" and rules["ai"]["model"] == "gpt-5-mini"
        # the folder is stored resolved: a Windows temp path is an 8.3 short name (RUNNER~1)
        assert rules["setup"]["tool"] == "kicad" and rules["setup"]["libs"] == [str(lib.resolve())]
        assert "sk-" not in (ws / "ecadlib.toml").read_text()
        st = ES.state(ws)
        assert st["done"] and st["ai"] == {"provider": "openai", "model": "gpt-5-mini"} and st["tool"] == "kicad"
        ES.save(ws, {"provider": "none", "tool": "altium", "workspace": str(ws)})
        rules = RU.load(ws / "ecadlib.toml")
        assert "datasheet" not in rules["sources"]["order"] and ES.state(ws)["ai"]["provider"] == "none"
        ES.save(ws, {"provider": "anthropic", "tool": "both", "workspace": str(ws)})
        assert ES.state(ws)["ai"] == {"provider": "anthropic", "model": "claude-opus-5-5"}
        assert "datasheet" in RU.load(ws / "ecadlib.toml")["sources"]["order"]
        for bad in ({"tool": "eagle"}, {"libs": [str(d / "missing")]}, {"workspace": "relative/ws"},
                    {"provider": "nope"}, {"provider": "openai", "model": "bad model!"}, {"libs": "C:/x"}):
            try:
                ES.save(ws, dict({"workspace": str(ws)}, **bad))
                raise AssertionError(bad)
            except ValueError:
                pass


def test_state_never_contains_a_key_value():
    with _home() as d:
        ES.store_key({"name": "ANTHROPIC_API_KEY", "value": "sk-ant-zzz-secret"})
        st = ES.state(d)
        assert "sk-ant-zzz-secret" not in json.dumps(st)
        row = next(k for k in st["keys"] if k["name"] == "ANTHROPIC_API_KEY")
        assert row["stored"] and row["set"]
        try:
            ES.store_key({"name": "PATH", "value": "x"})          # only the page's own keys
            raise AssertionError
        except ValueError:
            pass
        assert ES.forget_key({"name": "ANTHROPIC_API_KEY"}) == {"removed": True, "still_in_env": False}
        assert "ANTHROPIC_API_KEY" not in os.environ        # the running server stops using a forgotten key
        os.environ.pop("ANTHROPIC_API_KEY", None)


class _R:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


def test_check_key_reads_the_providers_answer():
    with _home():
        assert not ES.check_key({"provider": "anthropic"})["ok"]                    # no key yet: nothing is sent
        os.environ["ANTHROPIC_API_KEY"] = "sk-x"
        seen = {}

        def get(url, headers, timeout):
            seen.update(url=url, headers=headers)
            return _R(401)
        r = ES.check_key({"provider": "anthropic"}, get=get)
        assert not r["ok"] and "refused" in r["message"] and seen["url"] == "https://api.anthropic.com/v1/models"
        assert seen["headers"]["x-api-key"] == "sk-x" and "sk-x" not in r["message"]
        assert ES.check_key({"provider": "anthropic"}, get=lambda *a, **k: _R(200, {"data": []}))["ok"]

        def boom(*a, **k):
            raise ConnectionError("down")
        assert "could not reach" in ES.check_key({"provider": "anthropic"}, get=boom)["message"]
        r = ES.check_key({"provider": "ollama", "model": "qwen2.5vl:7b"}, get=lambda *a, **k: _R(200, {"data": [{"id": "llama3"}]}))
        assert not r["ok"] and "ollama pull qwen2.5vl:7b" in r["message"]
        assert ES.check_key({"provider": "none"})["ok"]
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_add_to_kicad_copies_the_plugin_with_this_ecadlib_command():
    with _home() as d:
        kd = d / "kicad-user"
        with mock.patch.object(DT, "kicad", lambda *a, **k: [{"series": "8.0"}]), \
                mock.patch.object(DT, "kicad_plugin_dir", lambda s, **k: kd / s / "scripting" / "plugins"):
            r = ES.install_kicad_plugin()
        p = kd / "8.0" / "scripting" / "plugins" / "ecadlib_add_part"
        assert r["installed"] == [str(p)] and (p / "__init__.py").is_file()
        assert (p / "ecadlib.png").read_bytes().startswith(b"\x89PNG")          # the toolbar button's icon
        cmd = json.loads((p / "ecadlib_cmd.json").read_text())["command"]
        assert cmd == UC.self_command()
        from tests.test_tool_packages import _plugin_module
        mod, _ = _plugin_module()
        assert mod.ecadlib_command({}, which=lambda x: None, cmd_file=str(p / "ecadlib_cmd.json")) == cmd
        (p / "ecadlib_cmd.json").write_text(json.dumps({"command": [str(d / "gone.exe")]}))
        assert mod.ecadlib_command({}, which=lambda x: None, cmd_file=str(p / "ecadlib_cmd.json")) is None
        with mock.patch.object(DT, "kicad", lambda *a, **k: []):
            try:
                ES.install_kicad_plugin()
                raise AssertionError
            except ValueError as e:
                assert "no KiCad" in str(e)
        assert UC.load_settings()["installed"]["kicad_plugin"] == [str(p)]


def test_add_to_altium_writes_the_menu_set_to_this_ecadlib():
    with _home() as d:
        r = ES.install_altium_menu(d / "ws")
        ini = (Path(r["folder"]) / "ecadlib_menu.ini").read_text()
        assert Path(r["project"]).is_file() and len(r["steps"]) == 3 and not r["problems"]
        assert "command=" + " ".join(UC.self_command()) in ini.replace('"', "")
        ES.install_altium_menu(d / "ws")                         # again: the kept .ini still points at this ecadlib
        assert ini == (Path(r["folder"]) / "ecadlib_menu.ini").read_text()


# ------------------------------------------------------------------ routes
def _call(port, method, path, body=None, origin=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    if origin:
        h["Origin"] = origin
    c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


@contextlib.contextmanager
def _server(root):
    srv = S.make_server(Workspace(root), "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def test_setup_routes_local_mode():
    with _home() as d, _server(d / "ws") as port:
        st, page = _call(port, "GET", "/setup")
        assert st == 200 and b"ecadlib setup" in page
        st, j = _call(port, "GET", "/api/setup")
        assert st == 200 and j["workspace"] == str((d / "ws").resolve()) and not j["done"]
        st, j = _call(port, "POST", "/api/setup/key", {"name": "NEXAR_TOKEN", "value": "tok-1"}, origin="http://evil.example")
        assert st == 403 and "NEXAR_TOKEN" not in UC.stored_names()                 # another site cannot store a key
        st, j = _call(port, "POST", "/api/setup/key", {"name": "NEXAR_TOKEN", "value": "tok-1"}, origin=f"http://127.0.0.1:{port}")
        assert st == 200 and UC.stored_names() == ["NEXAR_TOKEN"]
        st, j = _call(port, "POST", "/api/setup", {"provider": "anthropic", "tool": "both", "workspace": str(d / "ws")})
        assert st == 200 and j["saved"] and not j["restart"]
        st, j = _call(port, "POST", "/api/setup/restart", {})
        assert st == 400 and "ecadlib ui --workspace" in j["error"]
        st, j = _call(port, "POST", "/api/setup", {"tool": "eagle"})
        assert st == 400 and "tool" in j["error"]
        os.environ.pop("NEXAR_TOKEN", None)


# ------------------------------------------------------------------ the desktop app
def test_launcher_serves_restarts_in_a_new_workspace_and_is_single_instance():
    with _home() as d:
        port, running = LA.free_port(18765)
        assert running is None
        app = LA.App(d / "one", port)
        app.start()
        try:
            assert LA.free_port(port) == (port, str((d / "one").resolve()))         # second start: reuse it
            ES.RESTART = app.restart
            st, j = _call(port, "POST", "/api/setup", {"provider": "none", "tool": "kicad", "workspace": str(d / "two")})
            assert st == 200 and j["restart"] and j["can_restart"]
            st, j = _call(port, "POST", "/api/setup/restart", {"workspace": j["workspace"]})
            assert st == 200 and j["restarting"]
            for _ in range(50):
                time.sleep(0.1)
                try:
                    if _call(port, "GET", "/api/setup")[1]["workspace"] == str((d / "two").resolve()):
                        break
                except OSError:
                    pass
            else:
                raise AssertionError("did not restart in the new workspace")
            assert app.workspace == str((d / "two").resolve()) and app.port == port
        finally:
            ES.RESTART = None
            app.stop()


# ------------------------------------------------------------------ doctor on a fresh install
def test_doctor_on_a_fresh_install_warns_only_about_what_matters():
    d = _tmp()
    rep = DR.run(str(d / "ecadlib_workspace"), [], None, {"PATH": ""})
    warn = sorted(c["check"] for c in rep["checks"] if c["level"] != "OK")
    # an optional extra this Python really lacks (curl_cffi / playwright without .[dev]) is a fair warning, not noise
    lacking = {f"optional {pip}" for mod, pip, _ in DR.OPTIONAL if importlib.util.find_spec(mod) is None}
    assert set(warn) <= {"key anthropic", "tool kicad-cli"} | lacking, warn
    hints = " ".join(c["hint"] for c in rep["checks"])
    assert "export " not in hints and "ecadlib keys set ANTHROPIC_API_KEY" in hints
    rules = {"agent": {"github_repo": "me/lib"}, "altium365": {"workspace": "https://x.365.altium.com"}, "setup": {"tool": "altium"}}
    by = {c["check"]: c for c in DR.check_keys(rules, {})}
    assert by["key altium365"]["level"] == "WARN" and by["key github"]["level"] == "WARN"
    pk = {c["check"]: c for c in DR.check_packages(which=lambda t: None, rules=rules)}
    assert pk["tool git"]["level"] == "WARN" and pk["tool kicad-cli"]["level"] == "OK"     # Altium only: no kicad-cli


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_easy_setup"]))


clean_all(globals())        # R-G2-8: the scratch folders a test makes go when it ends
