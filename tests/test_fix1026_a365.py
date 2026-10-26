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


# ---------------------------------------------------------------- R-AM-3: pick_named and parts named like a revision
def _node(name, params):
    return {"id": "ID-" + name, "name": name, "revision": {"revisionId": "R1"},
            "details": {"parameters": [{"name": k, "value": v} for k, v in params.items()]}}


def test_pick_named_only_takes_the_revision_form_and_reads_house_mpn_names():
    from ecadlib import altium365 as A, rules as RU
    house = RU.load({"parameters": {"names": {"Manufacturer Part Number": "MfrPN"}}})
    # "-04" (leading zero) is no revision Altium names: another part
    assert A.pick_named([_node("BAT54-04", {"Description": "dual series Schottky"})], "BAT54") is None
    # its MPN under a house name says it is another part, with the rules or without them
    assert A.pick_named([_node("BAV99-7", {"MfrPN": "BAV99-7"})], "BAV99", rules=house) is None
    assert A.pick_named([_node("BAV99-7", {"MfrPN": "BAV99-7"})], "BAV99") is None
    assert A.pick_named([_node("BAV99-7", {"Mfr. Part #": "BAV99-7"})], "BAV99") is None
    # our own revisions are still found: numeric, letter.number, MPN under ecadlib's or a house name
    assert A.pick_named([_node("BAT54-1", {"Manufacturer Part Number": "BAT54"})], "BAT54")["name"] == "BAT54-1"
    assert A.pick_named([_node("BAT54-12", {"MfrPN": "BAT54"})], "BAT54", rules=house)["name"] == "BAT54-12"
    assert A.pick_named([_node("BAT54-A.1", {})], "BAT54")["name"] == "BAT54-A.1"
    assert A.pick_named([_node("BAT54", {})], "bat54")["name"] == "BAT54"


def test_push_does_not_write_parameters_onto_a_revision_looking_component_with_no_mpn():
    """SS34-13 with no MPN parameter (Altium keeps it in Part Choices) may be revision 13 of SS34 or another part:
    a push without a publish record of SS34 must not write MPN=SS34 onto it."""
    import tempfile
    from ecadlib import altium365 as A

    class Fake:
        def __init__(self):
            self.calls = []

        def release_defs(self, ws):
            return {}

        def find(self, ws, name):
            return A.pick_named([_node("SS34-13", {"Description": "Schottky 40V 3A"})], name)

        def update_params(self, cid, params, note, replace=False):
            self.calls.append(("update_params", cid, params))
            return cid

        def release(self, inp):
            self.calls.append(("release", inp))
    it = {"name": "SS34", "folder": "Components/Diodes", "description": "Schottky 40V 3A SMA", "comment": "SS34",
          "parameters": [{"name": "Manufacturer Part Number", "value": "SS34"}, {"name": "Manufacturer", "value": "onsemi"}],
          "symbol": {"ref": "D_Schottky", "file": None}, "footprint": {"ref": "SMA", "file": None}, "status": "ready"}
    c = Fake()
    r = A.push([it], c, "https://x.365.altium.com", folder=tempfile.mkdtemp())[0]
    assert not [x for x in c.calls if x[0] == "update_params"], c.calls
    assert r["action"] == "skip" and "SS34-13" in r["detail"], r


# ---------------------------------------------------------------- R-AM-4: parameter updates only for publishable parts
def test_push_sends_no_parameter_update_for_a_part_that_is_not_publishable():
    """draft / needs_review / a typo'd "Ready" / needs_altium_models: the dry run says skipped (it never reads the
    workspace); the real push must not rewrite the workspace component's parameters either."""
    import tempfile
    from ecadlib import altium365 as A

    class Fake:
        def __init__(self):
            self.calls = []

        def release_defs(self, ws):
            return {}

        def find(self, ws, name):
            return {"id": "C1", "name": name, "revision": {"revisionId": "R1"},
                    "details": {"parameters": [{"name": "Manufacturer Part Number", "value": name},
                                               {"name": "Manufacturer", "value": "TI"},
                                               {"name": "Notes", "value": "old note"}]}}

        def update_params(self, cid, params, note, replace=False):
            self.calls.append(("update_params", cid, params))
            return cid

        def release(self, inp):
            self.calls.append(("release", inp))
    d = tempfile.mkdtemp()
    for st in ("draft", "needs_review", "Ready", "needs_altium_models", "awaiting_approval", "ready"):
        it = {"name": "LM358DR", "folder": "Components/ICs", "description": "dual op amp", "comment": "LM358",
              "parameters": [{"name": "Manufacturer Part Number", "value": "LM358DR"},
                             {"name": "Manufacturer", "value": "TI"}, {"name": "Notes", "value": "new note"}],
              "symbol": {"ref": "LM358", "file": None}, "footprint": {"ref": "SOIC-8", "file": None}, "status": st}
        c = Fake()
        real = A.push([dict(it)], c, "https://x.365.altium.com", folder=d)[0]
        dry = A.push([dict(it)], None, "https://x.365.altium.com", dry_run=True, folder=d)[0]
        if st == "ready":                      # a ready part's parameters are kept up to date, as before
            assert real["action"] == "update_parameters", (st, real)
            continue
        assert not c.calls, (st, c.calls)
        assert real["action"] == "skip" == dry["action"], (st, real, dry)
        assert st.replace("_", " ") in real["detail"].replace("_", " "), (st, real)


# ---------------------------------------------------------------- R-AM-5: sign-off re-checked by every export route
def _signed_off(d):
    """A part released, signed off by one person under a 1-approval policy: (item, policy)."""
    from ecadlib import approvals as AP, release_check as RC
    from tests.test_release_check import make
    it = make(d)
    (d / "approvals.json").write_text(json.dumps({"required": 1}))
    RC.apply([it], d)
    pol = AP.load_policy(d)
    AP.decide(it, "approve", "Ann Lee <ann@example.com>", base=d, policy=pol)
    assert AP.refresh_status(it, pol, d) == "ready"
    return it, pol


@lite
def test_part_edited_after_sign_off_is_not_shipped_by_kicad_export(tmp_path):
    """Signed off, then the footprint edited (pads 0.58 -> 0.45 in the .kicad_mod and the .PcbLib): the Altium 365
    push skipped it, kicad-export shipped it. The release check's publish_problems (every export route) now holds it."""
    import contextlib, io
    from ecadlib import altium_lib as AL, kicad_export as KE, kicad_import as K, release_check as RC
    from tests.test_release_check import SOIC
    d = tmp_path
    it, pol = _signed_off(d)
    assert RC.publish_problems(it, d, writes_files=True) == []
    (d / "plan.json").write_text(json.dumps([it], indent=2))
    edited = SOIC.replace("(size 1.965 0.58)", "(size 1.965 0.45)")
    assert edited != SOIC
    (d / it["footprint"]["kicad_mod"]).write_text(edited)
    fp, t = K.convert_footprint(edited, it["footprint"]["ref"])
    K.add_body(fp, t, None, None)
    AL.write_pcblib(d / it["footprint"]["file"], [fp])
    why = RC.publish_problems(it, d, writes_files=True)
    assert any("sign-off" in w and "changed since the release check" in w for w in why), why
    with contextlib.redirect_stdout(io.StringIO()):
        rep = KE.export(str(d / "plan.json"), str(d / "kicad"), "lib", log=lambda *a, **k: None)
    assert not any("0.45" in m.read_text() for m in (d / "kicad").rglob("*.kicad_mod")), rep
    assert rep["skipped"], rep


# ---------------------------------------------------------------- R-AM-6: the sign-off covers the .kicad_mod
@lite
def test_sign_off_goes_stale_when_only_the_kicad_mod_changes(tmp_path):
    """kicad-export, kicad-dblib and kicad-pcm ship the .kicad_mod: an edit of it alone (pads 0.58 -> 0.30), with
    [release] parity = warn, kept the approval valid also after release-check ran again."""
    from ecadlib import approvals as AP, release_check as RC, rules as RU
    from tests.test_release_check import SOIC
    d = tmp_path
    it, pol = _signed_off(d)
    h0 = AP.content_hash(it, d)
    (d / it["footprint"]["kicad_mod"]).write_text(SOIC.replace("(size 1.965 0.58)", "(size 1.965 0.30)"))
    assert AP.content_hash(it, d) != h0
    RC.apply([it], d, rules=RU.load({"release": {"parity": "warn"}}))
    ev = AP.evaluate(it, pol, d)
    assert not ev["met"] and ev["stale"], ev
    assert it["status"] == "awaiting_approval", it["status"]


@lite
def test_sign_off_recorded_under_the_old_hash_stays_valid(tmp_path):
    """Approvals and release-check snapshots written before the .kicad_mod was hashed are compared like for like
    (the old hash): upgrading ecadlib does not send every signed-off part back for approval."""
    from ecadlib import approvals as AP, release_check as RC
    d = tmp_path
    it, pol = _signed_off(d)
    old = AP.content_hash(it, d, version=1)
    assert len(old) == 64 and old != AP.content_hash(it, d)
    for r in it["approvals"]:
        r["hash"] = old
    it["approval"]["checked_hash"] = old
    assert AP.evaluate(it, pol, d)["met"]
    assert AP.push_block(it, pol, d) is None
    assert AP.refresh_status(it, pol, d) == "ready"
    assert RC.publish_problems(it, d) == []
    RC.apply([it], d)                          # release-check again: still signed off
    assert it["status"] == "ready" and AP.evaluate(it, pol, d)["met"], it.get("approval")
    # an edit of the files the old hash covers still makes the old approval stale
    f = d / it["symbol"]["file"]
    f.write_bytes(f.read_bytes() + b"\0")
    assert not AP.evaluate(it, pol, d)["met"]
