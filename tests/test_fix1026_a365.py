"""Fixes 2026-10-26 (a365): Altium 365 push, MCP save_rules release gates, sign-off at every export.
Plain functions: python -m tests.run_all test_fix1026_a365. Altium 365 / Nexar is always a fake here."""
import json

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-AM-1: save_rules over MCP and the release gates
GATED = '''[altium365]
workspace = "https://mine.365.altium.com"
[release]
parity = "hold"
[release.approvals]
required = 2
[change]
identity = "hold"
electrical = "hold"
'''


def _mcp_ws(root):
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    (root / "librarian" / "b").mkdir(parents=True)
    ws = Workspace(str(root), [], None, True)
    ws.mcp_options = M.Options(confine=True)
    return ws


def _save(ws, text):
    from ecadlib import mcp_server as M
    r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "save_rules", "arguments": {"toml": text, "overwrite": True}}})
    return r["result"]["isError"], r["result"]["content"][0]["text"]


@lite
def test_save_rules_over_mcp_cannot_change_release_gates(tmp_path):
    from ecadlib import approvals as AP
    (tmp_path / "ecadlib.toml").write_text(GATED, encoding="utf-8")
    ws = _mcp_ws(tmp_path)
    for label, text in (
            ("workspace", GATED.replace("mine.365", "team.365")),
            ("approvals removed", GATED.replace("[release.approvals]\nrequired = 2\n", "")),
            ("change warn", GATED.replace('electrical = "hold"', 'electrical = "warn"')),
            ("change class added", GATED + 'geometry_major = "warn"\n'),
            ("parity off", GATED.replace('parity = "hold"', 'parity = "off"')),
            ("height", GATED.replace('parity = "hold"', 'parity = "hold"\nheight = "hold"')),
            ("required narrowed", GATED + '[parameters]\nrequired = ["Manufacturer"]\n'),
            ("cross-check off", GATED + '[datasheet]\ncross_check = "off"\n')):
        err, txt = _save(ws, text)
        assert err, (label, txt)
        assert "save_rules cannot change" in txt and "person" in txt, (label, txt)
    assert (tmp_path / "ecadlib.toml").read_text(encoding="utf-8") == GATED
    assert AP.load_policy(tmp_path / "librarian" / "b")["required"] == 2
    assert ws._a365_push_target("https://mine.365.altium.com", dry_run=False)


@lite
def test_save_rules_over_mcp_keeping_the_gates_still_saves(tmp_path):
    """The guard refuses a change of a gate, not every save: same gates written another way, a stricter required
    list, a gate left at its default, and other settings all save."""
    (tmp_path / "ecadlib.toml").write_text(GATED, encoding="utf-8")
    ws = _mcp_ws(tmp_path)
    text = (GATED.replace("required = 2", "required = 2.0").replace('parity = "hold"', 'parity = "hold"\nheight = "warn"')
            + 'geometry_minor = "warn"\n[naming]\ncase = "upper"\n'
            + '[parameters]\nrequired = ["Manufacturer", "Manufacturer Part Number", "Datasheet", "Description", "Value"]\n')
    err, txt = _save(ws, text)
    assert not err, txt
    assert ws.rules()["naming"]["case"] == "upper"


@lite
def test_save_rules_over_mcp_cannot_hide_the_gates_in_force_with_a_new_file(tmp_path, monkeypatch):
    """`ecadlib init` puts ecadlib.toml beside the workspace: a new workspace file would replace it, so it must keep
    its gates too."""
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    proj = tmp_path.resolve()
    (proj / "ecadlib.toml").write_text('[setup]\nworkspace = "ecadlib_workspace"\n' + GATED, encoding="utf-8")
    monkeypatch.chdir(proj)
    ws = Workspace("ecadlib_workspace")
    ws.mcp_options = M.Options(confine=True)
    err, txt = _save(ws, '[naming]\ncase = "upper"\n')
    assert err and "altium365.workspace" in txt and "release.approvals" in txt, txt
