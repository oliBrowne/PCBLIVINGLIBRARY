"""Regression tests for the 2026-10 debug sweep, MCP server / CLI / setup group (DS2-50 ... DS2-56).
Nothing live: no model, Altium 365 or network call is made. Run:
    python -m tests.run_all test_debugsweep_climcp"""
import contextlib, http.client, io, json

from ecadlib import mcp_server as M
from ecadlib.app import Workspace
from tests.fixtures_lite import lite


def _call(ws, name, args):
    with contextlib.redirect_stderr(io.StringIO()):
        return M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": args}})["result"]


def _txt(r):
    return r["content"][0]["text"]


# ------------------------------------------------------------------ DS2-50
@lite
def test_ds2_50_json_rules_workspace_save_rules_guard_reads_the_json(tmp_path):
    """In a workspace whose rules file is ecadlib_rules.json the guard parsed the agent's text as TOML only, saw
    nothing, and let it move [ai] base_url / provider, raise the cost limit and point [style] profile outside."""
    root = tmp_path / "ws"
    root.mkdir()
    f = root / "ecadlib_rules.json"
    f.write_text(json.dumps({"setup": {"tool": "kicad"}, "datasheet": {"max_cost_usd": 2.0}}), encoding="utf-8")
    before = f.read_text(encoding="utf-8")
    ws = Workspace(root)
    ws.mcp_options = M.Options(confine=True)
    for new in ({"setup": {"tool": "kicad"}, "datasheet": {"max_cost_usd": 2.0},
                 "ai": {"provider": "openai", "base_url": "https://attacker.example/v1",
                        "api_key_env": "ANTHROPIC_API_KEY"}},
                {"setup": {"tool": "kicad"}, "datasheet": {"max_cost_usd": 1000.0}}):
        r = _call(ws, "save_rules", {"toml": json.dumps(new), "overwrite": True})
        assert r["isError"] and "cannot change" in _txt(r), (new, r)
    outside = str((tmp_path / "elsewhere" / "house_style.json").resolve())
    r = _call(ws, "save_rules", {"toml": json.dumps({"setup": {"tool": "kicad"}, "datasheet": {"max_cost_usd": 2.0},
                                                     "style": {"profile": outside}}), "overwrite": True})
    assert r["isError"] and "outside the workspace" in _txt(r), r
    assert f.read_text(encoding="utf-8") == before
    r = _call(ws, "save_rules", {"toml": json.dumps({"setup": {"tool": "both"}, "datasheet": {"max_cost_usd": 2.0}}),
                                 "overwrite": True})                     # an ordinary JSON change still saves
    assert not r["isError"], r
    assert json.loads(f.read_text(encoding="utf-8"))["setup"]["tool"] == "both"


# ------------------------------------------------------------------ DS2-51
@lite
def test_ds2_51_save_rules_guard_compares_with_the_parent_rules_file_in_force(tmp_path, monkeypatch):
    """`ecadlib init` writes ./ecadlib.toml beside ./ecadlib_workspace. The guard read only <workspace>/ecadlib.toml
    (missing), so a save leaving the protected keys out, or setting the cap to its default, wrote a workspace file
    that hid the person's 0.5 USD cap and local model: the cap went back to 10.0 and requests to Anthropic."""
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    (proj / "ecadlib.toml").write_text('[setup]\nworkspace = "ecadlib_workspace"\n'
                                       '[datasheet]\nmax_cost_usd = 0.5\nask_above_usd = 0.1\n'
                                       '[ai]\nprovider = "openai"\nbase_url = "http://localhost:11434/v1"\n',
                                       encoding="utf-8")
    monkeypatch.chdir(proj)
    ws = Workspace("ecadlib_workspace")
    ws.mcp_options = M.Options(confine=True)
    for toml in ('[setup]\ntool = "kicad"\n', "[datasheet]\nmax_cost_usd = 10.0\n"):
        r = _call(ws, "save_rules", {"toml": toml, "overwrite": True})
        assert r["isError"] and "cannot change" in _txt(r), (toml, r)
    assert not (proj / "ecadlib_workspace" / "ecadlib.toml").exists()
    rules = ws.rules()
    assert (rules["datasheet"]["max_cost_usd"], rules["ai"]["base_url"]) == (0.5, "http://localhost:11434/v1")


# ------------------------------------------------------------------ DS2-52
def _sym_lib(f, name, description):
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"""(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "{name}" (property "Reference" "U") (property "Value" "{name}") (property "Footprint" "SOIC-8")
    (property "Description" "{description}") (property "MPN" "{name}-B")
    (symbol "{name}_1_1" (pin input line (at 0 0 0) (length 2.54) (name "~{{RESET}}" (effects)) (number "1" (effects))))))
""", encoding="utf-8")


@lite
def test_ds2_52_confined_propose_rules_and_learn_house_style_read_relative_paths_in_the_workspace(tmp_path, monkeypatch):
    """_check_paths approved 'private' as <workspace>/private, but propose_rules / learn_house_style scanned
    'private' from the server's start folder: a library outside the workspace went back to the remote client."""
    srv = tmp_path.resolve() / "srv"
    _sym_lib(srv / "private" / "lib" / "secret.kicad_sym", "SECRETASIC", "Confidential customer ASIC rev B")
    monkeypatch.chdir(srv)
    ws = Workspace("ecadlib_workspace")
    ws.mcp_options = M.Options(confine=True)
    _sym_lib(ws.root / "mine" / "own.kicad_sym", "OWNBUF", "Own workspace buffer")
    r = _call(ws, "propose_rules", {"paths": ["private"], "a365": ["private/lib/secret.kicad_sym"]})
    assert not r["isError"], r
    assert json.loads(_txt(r))["components"] == 0, _txt(r)[:300]
    r = _call(ws, "learn_house_style", {"paths": ["private"]})
    assert "Confidential customer ASIC" not in _txt(r), _txt(r)[:300]
    r = _call(ws, "propose_rules", {"paths": ["mine"]})                  # relative = inside the workspace
    assert json.loads(_txt(r))["components"] == 1, _txt(r)[:300]


# ------------------------------------------------------------------ DS2-53
@lite
def test_ds2_53_desktop_app_and_setup_start_with_the_library_folders_saved_on_the_setup_page(tmp_path, monkeypatch):
    """The setup page saved the person's library folders to [setup] libs, but the desktop app (launcher.App) and
    `ecadlib setup` started the web UI with no --lib: 0 symbols, so their own parts were never 'in your library'."""
    from ecadlib import easy_setup as ES, launcher as LA
    from ecadlib.ui import server as S
    home = tmp_path.resolve()
    monkeypatch.setenv("ECADLIB_HOME", home / "home")
    monkeypatch.chdir(home)
    mylib = home / "mylib"
    _sym_lib(mylib / "my.kicad_sym", "MYPART", "My own part")
    ws = home / "ws"
    assert ES.save(ws, {"workspace": str(ws), "tool": "kicad", "libs": [str(mylib)], "provider": "none"})["saved"]
    app = LA.App(ws, 0)
    app.start()
    try:
        with contextlib.redirect_stderr(io.StringIO()):                  # the server's request log
            c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
            c.request("GET", "/api/info", headers={"Host": f"127.0.0.1:{app.port}"})
            info = json.loads(c.getresponse().read())
    finally:
        app.stop()
    assert (info["libs"], info["symbols"]) == ([str(mylib)], 1), info
    seen = []
    monkeypatch.setattr(S, "main", lambda argv: seen.append(argv) or 0)   # `ecadlib setup`: the server it starts
    ES.main(["--workspace", str(ws), "--no-browser"])
    assert "--lib" in seen[0] and seen[0][seen[0].index("--lib") + 1] == str(mylib), seen


# ------------------------------------------------------------------ DS2-54
@lite
def test_ds2_54_setup_page_save_keeps_the_workspace_sources_order(tmp_path, monkeypatch):
    """Save on the setup page always wrote [sources] order = the defaults (online lookups first), even with nothing
    changed: a hand-set order such as standard first, without EasyEDA, was lost. Only the datasheet entry is the page's."""
    from ecadlib import easy_setup as ES, rules as RU
    monkeypatch.setenv("ECADLIB_HOME", tmp_path.resolve() / "home")
    ws = tmp_path.resolve() / "ws"
    ws.mkdir()
    (ws / "ecadlib.toml").write_text('[sources]\norder = ["standard", "kicad", "datasheet"]\n'
                                     '[ai]\nprovider = "anthropic"\nmodel = "claude-sonnet-5-5"\n', encoding="utf-8")

    def save(provider, model=""):
        ES.save(ws, {"workspace": str(ws), "tool": "both", "libs": [], "provider": provider, "model": model})
        return RU.load(ws / "ecadlib.toml")["sources"]["order"]
    assert save("anthropic", "claude-sonnet-5-5") == ["standard", "kicad", "datasheet"]   # nothing changed on the page
    assert save("none") == ["standard", "kicad"]                                          # No AI: no datasheet reader
    assert save("anthropic", "claude-sonnet-5-5") == ["standard", "kicad", "datasheet"]   # and back
