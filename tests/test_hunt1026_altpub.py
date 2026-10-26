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


def test_dblib_sync_case_only_rename_updates_the_row_not_a_second_one():
    """Altium Part Numbers ignore case (ACE compares text without case): a part renamed SS34 -> ss34 in the plan is
    the same row. It was added as a second row while the old one stayed (not_in_plan, kept), so Altium had two rows
    for one Part Number and could place the stale one."""
    import tempfile
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _item, _models, _plan
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _models(d)
        S.sync(_plan(d, [_item("RES-0603-10K", "Resistors", "Resistor", "R_0603_1608Metric", Value="10k")]), d / "lib")
        out = S.sync(_plan(d, [_item("res-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", Value="10k")]),
                     d / "lib", prune=False)
        rows = [r for _, (_, rs) in S.read_tables(d / "lib" / "ecadlib.xlsx").items() for r in rs]
        assert [r["Part Number"] for r in rows] == ["res-0603-10k"], (rows, out["results"])
        assert {r["name"]: r["action"] for r in out["results"]} == {"res-0603-10k": "updated"}, out["results"]


class Workspace(FakeA365):
    """A closer Nexar mock: components found by name whatever the case (pick_named), symbol / footprint items that
    can be released once (a second release of the same item name fails as duplicate, as live), linked by name."""
    def __init__(self):
        super().__init__()
        self.models, self.fail = {}, set()

    def find(self, ws, name):
        from ecadlib import altium365 as A
        return A.pick_named(list(self.comps.values()), name)

    def find_model(self, ws, kind, name, folder):
        return self.models.get((kind, name.lower()), (None,))[0]

    def release(self, inp):
        from ecadlib import altium365 as A
        if inp["componentItemName"] in self.fail:
            raise A.NexarError("boom")
        halves = (("symbols", "symbolItemName", "symbolFiles"), ("footprints", "footprintItemName", "footprintFiles"))
        for kind, key, files in halves:
            if inp.get(files) and (kind, (inp.get(key) or "").lower()) in self.models:
                raise A.NexarError("AddALU_ItemsDuplicate: Duplicate ID is not allowed")
        super().release(inp)
        for kind, key, files in halves:
            if inp.get(files):
                self.models[(kind, inp[key].lower())] = (f"{kind}:{inp[key]}", [f["fileId"] for f in inp[files]])


def test_new_part_does_not_link_a_shared_model_older_than_its_own():
    """RES-0603-10k is in the workspace; the shared Resistor symbol then changes (approved for both resistors). 10k
    goes up as a new revision whose symbol is released as a new item (Resistor-r2: the item name is taken). The new
    RES-0603-4.7k must not be released linked to the workspace's old `Resistor` symbol while the ledger records its
    new revision as published."""
    from ecadlib import altium365 as A, rules as RU
    plan, lib = demo()
    nx, ws = Workspace(), "https://t.365.altium.com"
    rules = RU.load(None, start=lib)
    only = [i for i in items_of(plan) if i["name"] == "RES-0603-10k"]
    assert _push(A, plan, lib, nx, ws, rules, items=only)["RES-0603-10k"]["action"] == "release"
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "RES-0603-10k"), [("1", "A", "passive"), ("2", "B", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    CC.approve(plan, ["RES-0603-10k", "RES-0603-4.7k"], who=ANN)
    res = _push(A, plan, lib, nx, ws, rules)
    assert res["RES-0603-10k"]["action"] == "new_revision", res["RES-0603-10k"]
    r47 = res["RES-0603-4.7k"]
    sent = nx.comps.get("RES-0603-4.7k", {}).get("revisions") or [{}]
    assert not (r47["action"] == "release" and sent[-1].get("symbol") == {"id": "symbols:Resistor"}), (r47, sent[-1])
    pub = LG.Ledger(lib).published("RES-0603-4.7k", A.ws_target(ws))
    assert r47["action"] in A.PUSHED or not pub, (r47, pub)
    assert r47["action"] == "skip" and "'Resistor' is another drawing" in r47["detail"], r47


def test_shared_model_of_the_same_drawing_is_still_linked_and_idempotent():
    """No false hold: two resistors on one symbol / footprint, one pushed per run, the second links both models; a
    push again sends nothing; a ledger written before `models` was recorded still knows what each item holds."""
    from ecadlib import altium365 as A, rules as RU
    plan, lib = demo()
    nx, ws = Workspace(), "https://t.365.altium.com"
    rules = RU.load(None, start=lib)
    only = [i for i in items_of(plan) if i["name"] == "RES-0603-10k"]
    assert _push(A, plan, lib, nx, ws, rules, items=only)["RES-0603-10k"]["action"] == "release"
    led = lib / LG.LEDGER                      # as an older ecadlib wrote it: no `models` in the publish detail
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()]
    for e in lines:
        (e.get("detail") or {}).pop("models", None)
    led.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    nx.calls.clear()
    res = _push(A, plan, lib, nx, ws, rules)
    assert res["RES-0603-4.7k"]["action"] == "release", res["RES-0603-4.7k"]
    sent = nx.comps["RES-0603-4.7k"]["revisions"][-1]
    assert sent["symbol"] == {"id": "symbols:Resistor"} and sent["footprints"] == [{"id": "footprints:R_0603_1608Metric"}]
    assert [c for c in nx.calls if c[0] == "upload" and c[1] in ("Resistor.SchLib", "R_0603_1608Metric.PcbLib")] == []
    nx.calls.clear()
    again = _push(A, plan, lib, nx, ws, rules)
    assert {r["action"] for n, r in again.items() if n != "CH340C"} == {"up_to_date"}, again
    assert not [c for c in nx.calls if c[0] in ("release", "upload", "update_params")], nx.calls
    # and the stale case is still found from such a ledger
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "RES-0603-10k"), [("1", "A", "passive"), ("2", "B", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    CC.approve(plan, ["RES-0603-10k", "RES-0603-4.7k"], who=ANN)
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()]
    for e in lines:
        (e.get("detail") or {}).pop("models", None)
    led.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    nx.comps.pop("RES-0603-4.7k")              # as if 4.7k were new to this workspace
    for e in lines:
        if e.get("part") == "RES-0603-4.7k" and e.get("event") == "publish" and str(e.get("target")).startswith("altium365"):
            e["target"] = "altium365:https://other.365.altium.com"
    led.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    only = [i for i in items_of(plan) if i["name"] == "RES-0603-4.7k"]
    r47 = _push(A, plan, lib, nx, ws, rules, items=only)["RES-0603-4.7k"]
    assert r47["action"] == "skip" and "'Resistor' is another drawing" in r47["detail"], r47
