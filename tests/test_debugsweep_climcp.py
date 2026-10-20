"""Regression tests for the 2026-10 debug sweep, MCP server / CLI / setup group (DS2-50 ... DS2-56).
Nothing live: no model, Altium 365 or network call is made. Run:
    python -m tests.run_all test_debugsweep_climcp"""
import contextlib, io, json

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
