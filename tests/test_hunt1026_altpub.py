"""Hunt 2026-10-26 (altpub): Altium 365 push, DbLib sync, Altium menu jobs."""
import json
from pathlib import Path

from ecadlib import change_cli as CC, change_gate as CG, ledger as LG
from tests.test_change_control import ANN, FakeA365, demo, gate, item, items_of, write_symbol


def _push(A, plan, lib, nx, ws, rules, items=None, dry=False):
    items = items if items is not None else items_of(plan)
    for i in items:
        for k in ("symbol", "footprint"):
            if i[k].get("file"):
                i[k]["file"] = str(lib / i[k]["file"])
    return {r["name"]: r for r in A.push(items, nx, ws, dry_run=dry, rules=rules, folder=lib)}


def test_workspace_url_spelling_does_not_lose_an_approved_revision():
    """The same workspace typed with another host case or a trailing slash is the same publish target: an approved
    symbol change still goes up as a new revision (it was an in-place `up_to_date`, and the ledger then recorded the
    changed revision as published, so it never went up)."""
    from ecadlib import altium365 as A, rules as RU
    plan, lib = demo()
    nx = FakeA365()
    rules = RU.load(None, start=lib)
    first = _push(A, plan, lib, nx, "https://test.365.altium.com", rules)
    assert first["AO3401A"]["action"] == "release"
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    CC.approve(plan, ["AO3401A"], who=ANN)
    res = _push(A, plan, lib, nx, "https://Test.365.Altium.com/", rules)["AO3401A"]
    assert res["action"] == "new_revision", res
    assert len(nx.comps["AO3401A"]["revisions"]) == 2
