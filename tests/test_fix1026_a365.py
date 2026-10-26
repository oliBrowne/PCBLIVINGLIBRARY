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


# ---------------------------------------------------------------- R-AM-2: a component in the workspace with no publish line
def _push_lib(plan, lib, nx, ws, rules, dry=False):
    from ecadlib import altium365 as A
    from tests.test_change_control import items_of
    items = items_of(plan)
    for i in items:
        for k in ("symbol", "footprint"):
            if i[k].get("file"):
                i[k]["file"] = str(lib / i[k]["file"])
    return {r["name"]: r for r in A.push(items, nx, ws, dry_run=dry, rules=rules, folder=lib)}


def test_lost_first_release_then_approved_change_goes_up_as_a_new_revision():
    """The first release reached the workspace but its answer was lost (no publish line). After an approved symbol
    change the next push must send the approved revision (it was `up_to_date`, sent nothing and recorded r2 as
    published, so the workspace kept r1's drawing for good)."""
    from ecadlib import altium365 as A, change_cli as CC, change_gate as CG, ledger as LG, rules as RU
    from tests.test_change_control import ANN, FakeA365, demo, gate, item, items_of, write_symbol
    plan, lib = demo()
    ws = "https://test.365.altium.com"
    nx = FakeA365()
    rules = RU.load(None, start=lib)
    real = nx.release

    def flaky(inp):                            # the server makes the component, the answer is lost
        real(inp)
        if inp["componentItemName"] == "AO3401A":
            raise A.NexarUnreachable("could not reach Altium 365 (Nexar, api.nexar.com; ReadTimeout)")
    nx.release = flaky
    assert _push_lib(plan, lib, nx, ws, rules)["AO3401A"]["action"] == "error"
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}") is None
    nx.release = real
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    CC.approve(plan, ["AO3401A"], who=ANN)
    assert LG.Ledger(lib).head("AO3401A")["revision"] == 2
    r2 = _push_lib(plan, lib, nx, ws, rules)["AO3401A"]
    assert r2["action"] == "new_revision", r2
    assert len(nx.comps["AO3401A"]["revisions"]) == 2
    assert nx.comps["AO3401A"]["revisions"][-1]["symbolFiles"], "the approved symbol must go up"
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}")["revision"] == 2
    assert _push_lib(plan, lib, nx, ws, rules)["AO3401A"]["action"] == "up_to_date"


def test_component_made_by_hand_in_the_workspace_is_released_from_the_head_not_recorded_as_published():
    """A component made by hand (or by an older ecadlib, or a release whose answer was lost) and no publish line: the
    push does not take it as up to date / a parameter update and record a publish it never made; it releases the
    head revision's files as a new revision of that component."""
    from ecadlib import ledger as LG, rules as RU
    from tests.test_change_control import FakeA365, demo, item, items_of
    plan, lib = demo()
    ws = "https://test.365.altium.com"
    nx = FakeA365()
    rules = RU.load(None, start=lib)
    it = item(items_of(plan), "AO3401A")
    nx.comps["AO3401A"] = {"id": "HAND", "name": "AO3401A", "revisions": [{"by": "hand"}],
                           "revision": {"revisionId": "R1"},
                           "details": {"parameters": [{"name": p["name"], "value": p["value"]}
                                                      for p in it["parameters"]]}}
    res = _push_lib(plan, lib, nx, ws, rules)["AO3401A"]
    assert res["action"] == "new_revision", res
    assert [c for c in nx.calls if c[0] == "release" and c[1] == "AO3401A"], nx.calls
    assert len(nx.comps["AO3401A"]["revisions"]) == 2
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}")["revision"] == 1
