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
