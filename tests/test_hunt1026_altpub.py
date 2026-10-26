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


def test_menu_job_whose_config_path_lost_characters_uses_the_menu_folders_ini():
    """TIniFile writes request.ini in the ANSI code page: a menu folder named with characters that code page lacks
    comes back with '?' in config=. The job then ran on built-in defaults (a `workspace` folder next to a path that
    does not exist) instead of the ecadlib_menu.ini two folders up, which is the one the script used."""
    from ecadlib import altium_menu as AM
    from tests.test_altium_menu import _job, _setup
    menu, cfg = _setup()
    job = _job(menu, {"action": "nothing"})
    req = (job / "request.ini").read_text(encoding="cp1252").replace(str(menu), str(menu.parent / "men?"))
    (job / "request.ini").write_text(req, encoding="cp1252")
    seen = {}
    real = AM.run_action
    AM.run_action = lambda c, *a, **k: seen.setdefault("cfg", c) and {"status": "ok"}
    try:
        AM.run_job(job)
    finally:
        AM.run_action = real
    assert seen["cfg"]["workspace"] == cfg["workspace"], seen


def test_menu_config_that_does_not_exist_is_an_error_not_defaults():
    """`altium-menu add-part --config typo.ini` put parts into <typo folder>/workspace without a word."""
    from ecadlib import altium_menu as AM
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        try:
            AM.load_config(Path(t) / "nope" / AM.INI)
        except ValueError as e:
            assert "nope" in str(e)
        else:
            raise AssertionError("a missing ecadlib_menu.ini gave defaults")


def test_menu_build_refuses_a_line_break_that_would_add_ini_keys():
    """build(workspace="D:/ws\\r\\nlibrary=D:/other") wrote a second library= line: Python's reader takes the last
    one, Windows' profile API (the script) the first, so the two sides disagreed on the library folder."""
    import tempfile
    from ecadlib import altium_menu as AM
    with tempfile.TemporaryDirectory() as t:
        for kw in ({"workspace": "D:/ws\r\nlibrary=D:/other"}, {"library": "L\nport=1"}, {"command": "py -m ecadlib\nhidden=0"}):
            try:
                AM.build(Path(t) / "menu", **kw)
            except ValueError as e:
                assert "line break" in str(e)
            else:
                raise AssertionError(kw)
            assert not (Path(t) / "menu" / AM.INI).exists()


class _MenuWS:
    """What altium_menu.publish / _part_result use of app.Workspace, with an Altium 365 push that answers `results`."""
    def __init__(self, root, items, results):
        self.root, self.items, self.results = Path(root), items, results

    def rules(self):
        return {"altium365": {"workspace": "https://t.365.altium.com"}}

    def _plan(self, rel):
        return self.root / "bom.csv", self.root / "plan.json", self.items

    def _librarian_dir(self, bom):
        return self.root / "lib"

    def altium365_push(self, bom, url, dry_run=True):
        (self.root / "r.json").write_text("[]")
        return {"results": self.results, "report": "r.json"}


def _menu_part(results, name="PART-A"):
    import tempfile
    from ecadlib import altium_menu as AM
    item_ = {"name": name, "status": "ready", "hold_reasons": [], "source": {}, "parameters": []}
    with tempfile.TemporaryDirectory() as t:
        ws = _MenuWS(t, [item_], results)
        return AM._part_result({"library": Path(t) / "lib", "name": "ecadlib"}, ws, "bom.csv", "bom.csv", name,
                               AM.Progress(None))


def test_menu_does_not_say_released_for_a_part_the_push_skipped():
    """A ready part the Altium 365 push skipped (a sign-off the policy still wants, an identity change it refuses)
    was reported "ready ... released to Altium 365" in the menu dialog: only `error` and release-check holds counted."""
    for detail in ("sign-off: needs 2 approvals (has 1)", f"identity change (Manufacturer Part Number: A -> B) not applied"):
        res = _menu_part([{"name": "PART-A", "action": "skip", "detail": detail}])
        assert res["status"] == "error" and "not published" in res["message"] and detail in res["message"], res


def test_menu_part_released_is_not_failed_by_another_parts_error():
    """parts.csv holds every part added from the menu: another part's failed push made this one's dialog say
    "built but not published" with the other part's error."""
    res = _menu_part([{"name": "OTHER", "action": "error", "detail": "NexarError: boom"},
                      {"name": "PART-A", "action": "release", "detail": ""}])
    assert res["status"] == "ready" and "released to Altium 365" in res["message"], res


def test_pick_named_does_not_take_a_part_named_like_a_revision():
    """BAT54-04 (a dual diode of its own) looks like revision 04 of BAT54: when Nexar's name filter returns it for
    BAT54, its MPN says it is another part, so BAT54 is released, not BAT54-04's parameters updated with BAT54's."""
    from ecadlib import altium365 as A
    mk = lambda n, mpn=None: {"id": n, "name": n, "details": {"parameters": [{"name": "Manufacturer Part Number", "value": mpn}] if mpn else []}}
    assert A.pick_named([mk("BAT54-04", "BAT54-04")], "BAT54") is None
    assert A.pick_named([mk("BAT54-1", "BAT54")], "BAT54")["id"] == "BAT54-1"          # our revision 1
    assert A.pick_named([mk("BAT54-1")], "BAT54")["id"] == "BAT54-1"                   # no MPN: as before
    assert A.pick_named([mk("bat54", "BAT54")], "BAT54")["id"] == "bat54"


def test_kicad_menu_part_exported_is_not_failed_by_another_parts_error():
    """The KiCad window's add-part: one plan is the whole library, so another part's export error ("OTHER: symbol
    name ... already used") made the part just exported say "built but not published"."""
    import tempfile
    from unittest import mock
    from ecadlib import altium_menu as AM
    items = [{"name": n, "status": "ready", "hold_reasons": [], "source": {}, "parameters": []} for n in ("PART-A", "OTHER")]
    with tempfile.TemporaryDirectory() as t:
        ws = _MenuWS(t, items, [])
        pub = {"target": "kicad", "install": [], "problems": ["OTHER: symbol name X already used by another part"],
               "library": t, "held": {}, "footprints": {"PART-A": "ecadlib:SOT-23"}}
        with mock.patch.object(AM, "publish", lambda *a, **k: pub):
            res = AM._part_result({"library": Path(t), "name": "ecadlib", "tool": "kicad"}, ws, "bom.csv", "bom.csv",
                                  "PART-A", AM.Progress(None))
        assert res["status"] == "ready" and res["footprint"] == "ecadlib:SOT-23", res
        pub["problems"] = ["library: ecadlib.pretty could not be written"]          # not another part's: still counts
        with mock.patch.object(AM, "publish", lambda *a, **k: pub):
            res = AM._part_result({"library": Path(t), "name": "ecadlib", "tool": "kicad"}, ws, "bom.csv", "bom.csv",
                                  "PART-A", AM.Progress(None))
        assert res["status"] == "error", res


class Workspace(FakeA365):
    """A closer Nexar mock: components found by name whatever the case (pick_named), symbol / footprint items that
    can be released once (a second release of the same item name fails as duplicate, as live), linked by name."""
    def __init__(self):
        super().__init__()
        self.wmodels, self.fail = {}, set()

    def find(self, ws, name):
        from ecadlib import altium365 as A
        return A.pick_named(list(self.comps.values()), name)

    def find_model(self, ws, kind, name, folder):
        return self.wmodels.get((kind, name.lower()), (None,))[0]

    def model_data(self, ws, kind, name, folder):
        hit = self.wmodels.get((kind, name.lower()))
        return self.blobs.get(hit[1][0]) if hit else None

    def release(self, inp):
        from ecadlib import altium365 as A
        if inp["componentItemName"] in self.fail:
            raise A.NexarError("boom")
        halves = (("symbols", "symbolItemName", "symbolFiles"), ("footprints", "footprintItemName", "footprintFiles"))
        for kind, key, files in halves:
            if inp.get(files) and (kind, (inp.get(key) or "").lower()) in self.wmodels:
                raise A.NexarError("AddALU_ItemsDuplicate: Duplicate ID is not allowed")
        super().release(inp)
        self.comps[inp["componentItemName"]]["details"] = {"parameters": [dict(p) for p in inp["parameters"]]}
        for kind, key, files in halves:
            if inp.get(files):
                self.wmodels[(kind, inp[key].lower())] = (f"{kind}:{inp[key]}", [f["fileId"] for f in inp[files]])

    def update_params(self, cid, params, note, replace=False):     # a new revision, as live (it takes a release note)
        super().update_params(cid, params, note, replace)
        c = next(c for c in self.comps.values() if c["id"] == cid)
        c["revisions"].append({"parameters": params})
        c["revision"] = {"revisionId": f"R{len(c['revisions'])}"}
        c["details"] = {"parameters": [dict(p) for p in params]}
        return cid


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
    # review of #196 (R-PUB-3): it links Resistor-r2, the item 10k's new revision put its drawing in, not held forever
    assert r47["action"] == "release" and sent[-1].get("symbol") == {"id": "symbols:Resistor-r2"}, (r47, sent[-1])


def test_parameter_update_reports_the_revision_it_left():
    """desUpdateComponentRevisionParameters takes a release note and makes a new revision: the push reported (and
    the ledger kept, for `project check`) the revision from before the update."""
    from ecadlib import altium365 as A, rules as RU
    plan, lib = demo()
    nx, ws = Workspace(), "https://t.365.altium.com"
    rules = RU.load(None, start=lib)
    only = lambda: [i for i in items_of(plan) if i["name"] == "AO3401A"]
    assert _push(A, plan, lib, nx, ws, rules, items=only())["AO3401A"]["a365_revision"] == "R1"
    pars = nx.comps["AO3401A"]["details"]["parameters"]       # one dropped in the workspace: the push puts it back
    drop = next(p for p in pars if not any(w in p["name"] for w in ("Manufacturer", "MPN", "Part Number", "Value")))
    nx.comps["AO3401A"]["details"]["parameters"] = [p for p in pars if p is not drop]
    res = _push(A, plan, lib, nx, ws, rules, items=only())["AO3401A"]
    assert res["action"] == "update_parameters" and res["a365_revision"] == "R2", res


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
