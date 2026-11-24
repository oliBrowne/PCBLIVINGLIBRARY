"""Security review 2026-11-23, group S-B: approvals a caller cannot write for itself.
2.4  a release anyway needs who / when / why in the library's ledger; an `allow` block in plan.json alone is not proof
2.3  over MCP a release anyway, a sign-off and a paid run are asked of the person (ecadlib confirm); save_rules takes
     only harmless settings"""
import contextlib, io, json, sys
from pathlib import Path

from ecadlib import ledger as LG, release_check as RC
from tests.fixtures_lite import lite
from tests.test_release_check import make

ANN = "Ann Lee <ann@example.com>"


def _plan(d, verify=("check me",)):
    it = make(d)
    it["verify"] = list(verify)
    plan = Path(d) / "plan.json"
    plan.write_text(json.dumps([it]))
    return plan


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
        rc = fn(*a, **k)
    return rc, err.getvalue()


def _events(d, part="PART1"):
    return [e for e in LG.Ledger(Path(d)).lines() if e.get("part") == part and e.get("event", "").startswith("release_anyway")]


# ------------------------------------------------------------------ 2.4
@lite
def test_hand_written_allow_block_releases_nothing(tmp_path):
    """a5 old_allow_edit: "allow": {"failures": [...]} typed into plan.json, no release-check run, no person."""
    plan = _plan(tmp_path)
    items = json.loads(plan.read_text())
    items[0]["status"] = "ready"
    items[0]["allow"] = {"by": "--allow", "date": "2026-11-23", "failures": ["verify: check me"], "user": ANN}
    probs = RC.publish_problems(items[0], tmp_path)
    assert any(p.startswith("verify: check me") for p in probs), probs            # a publisher does not send it
    assert any("no record of it" in p for p in probs), probs
    RC.apply(items, tmp_path)
    it = items[0]
    assert it["status"] == "held" and "allow" not in it, it
    assert it["hold_reasons"][0] == "verify: check me" and "no record of it" in it["hold_reasons"][-1], it["hold_reasons"]
    assert not _events(tmp_path)


@lite
def test_cli_allow_needs_a_reason_and_writes_who_when_why(tmp_path):
    plan = _plan(tmp_path)
    rc, err = _quiet(RC.main, [str(plan), "--allow", "PART1", "--user", ANN])
    assert rc == 2 and "--reason" in err, (rc, err)                                # no reason: nothing released
    assert "allow" not in json.loads(plan.read_text())[0] and not _events(tmp_path)
    rc, _ = _quiet(RC.main, [str(plan), "--allow", "PART1", "--user", ANN, "--reason", "pins 1-3 checked  against the sheet"])
    assert rc == 0
    it = json.loads(plan.read_text())[0]
    assert it["status"] == "ready" and it["allow"]["user"] == ANN and it["allow"]["reason"] == "pins 1-3 checked against the sheet"
    (e,) = _events(tmp_path)
    assert (e["event"], e["by"], e["reason"], e["failures"]) == ("release_anyway", ANN, "pins 1-3 checked against the sheet",
                                                                 ["verify: check me"]) and e["at"], e
    assert RC.publish_problems(it, tmp_path) == []                                 # the recorded release counts
    rc, _ = _quiet(RC.main, [str(plan)])                                           # and stays on the next check
    assert rc == 0 and json.loads(plan.read_text())[0]["status"] == "ready"


@lite
def test_recorded_allow_does_not_cover_what_a_hand_edit_adds(tmp_path):
    plan = _plan(tmp_path)
    assert _quiet(RC.main, [str(plan), "--allow", "PART1", "--user", ANN, "--reason", "looked"])[0] == 0
    items = json.loads(plan.read_text())
    items[0]["verify"].append("a second doubt")
    items[0]["allow"]["failures"].append("verify: a second doubt")                 # widened by hand
    assert RC.publish_problems(items[0], tmp_path)
    RC.apply(items, tmp_path)
    assert items[0]["status"] == "held", items[0]
    items = json.loads(plan.read_text())                                           # another name on the block
    items[0]["allow"]["user"] = "Bob Ray <bob@example.com>"
    RC.apply(items, tmp_path)
    assert items[0]["status"] == "held"


@lite
def test_unallow_is_recorded_so_the_block_cannot_be_put_back(tmp_path):
    plan = _plan(tmp_path)
    assert _quiet(RC.main, [str(plan), "--allow", "PART1", "--user", ANN, "--reason", "looked"])[0] == 0
    block = json.loads(plan.read_text())[0]["allow"]
    assert _quiet(RC.main, [str(plan), "--unallow", "PART1", "--user", ANN])[0] == 2
    assert [e["event"] for e in _events(tmp_path)] == ["release_anyway", "release_anyway_withdrawn"]
    items = json.loads(plan.read_text())
    items[0]["allow"] = block                                                      # the old block typed back in
    RC.apply(items, tmp_path)
    assert items[0]["status"] == "held"


@lite
def test_review_release_anyway_asks_for_the_reason(tmp_path):
    from ecadlib import review_cli as RV
    plan = _plan(tmp_path)
    assert _quiet(RC.main, [str(plan)])[0] == 2
    for answers, status in ((["a", "", "q"], "held"), (["a", "checked against the datasheet"], "ready")):
        said = iter(answers)
        rc, _ = _quiet(RV.main, [str(plan), "--user", ANN, "--limit", "0"], inp=lambda prompt="": next(said))
        assert json.loads(plan.read_text())[0]["status"] == status, (answers, rc)
    (e,) = _events(tmp_path)
    assert (e["by"], e["reason"], e["via"]) == (ANN, "checked against the datasheet", "ecadlib review")


# ------------------------------------------------------------------ 2.3 over MCP
def _demo(tmp_path):
    from ecadlib import firstrun as FR, mcp_server as M
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", offline=True)
    res = FR.build_demo(ws, "altium", log=lambda *a: None)
    ws.mcp_options = M.Options(confine=True)
    return ws, res["bom"], res["held_parts"][0]


def _call(ws, name, **args):
    from ecadlib import mcp_server as M
    r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]
    t = r["content"][0]["text"]
    try:
        t = json.loads(t)
    except ValueError:
        pass
    return r["isError"], t


def _plan_of(ws, bom):
    return ws._plan(bom)[1]


def _item(ws, bom, part):
    return next(i for i in json.loads(_plan_of(ws, bom).read_text()) if i["name"] == part)


def _confirm(ws, rid, answer, user=ANN):
    from ecadlib import confirm as CF
    return _quiet(CF.main, [rid, "--workspace", str(ws.root), "--user", user], inp=lambda prompt="": answer)[0]


@lite
def test_mcp_release_anyway_is_asked_of_the_person(tmp_path, monkeypatch):
    """a9 old_r4: release_check allow under the invented name "Ann Lee" released a held demo part."""
    from ecadlib import confirm as CF
    ws, bom, part = _demo(tmp_path)
    err, text = _call(ws, "release_check", bom=bom, allow=[part], user="Ann Lee <ann@example.com>")
    assert err and "ecadlib confirm" in text and part in text, text
    it = _item(ws, bom, part)
    assert it["status"] == "held" and "allow" not in it
    assert not _events(_plan_of(ws, bom).parent, part)
    (req,) = CF.pending(ws.root)
    assert req["kind"] == "release_anyway" and req["show"]["parts"] == [part]
    err2, text2 = _call(ws, "release_check", bom=bom, allow=[part])                # asked again: the same request
    assert err2 and req["id"] in text2 and len(CF.pending(ws.root)) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))                                # no terminal: nobody answered
    assert _quiet(CF.main, [req["id"], "--workspace", str(ws.root)])[0] == 2 and _item(ws, bom, part)["status"] == "held"
    assert _confirm(ws, req["id"], "") == 2 and _item(ws, bom, part)["status"] == "held"   # no reason: not released
    assert _confirm(ws, req["id"], "pins 1-3 checked against the datasheet") == 0
    (e,) = _events(_plan_of(ws, bom).parent, part)
    assert e["by"] == ANN and e["reason"] == "pins 1-3 checked against the datasheet" and "MCP" in e["via"], e
    assert not CF.pending(ws.root)
    err, out = _call(ws, "release_check", bom=bom, allow=[part])                   # the retry succeeds
    assert not err and {r["part"]: r["status"] for r in out["review"]}[part] == "ready", out


@lite
def test_mcp_sign_off_is_asked_of_the_person(tmp_path):
    from ecadlib import confirm as CF
    ws, bom, _ = _demo(tmp_path)
    part = next(i["name"] for i in json.loads(_plan_of(ws, bom).read_text()) if i["status"] == "ready")
    err, text = _call(ws, "approve_part", bom=bom, part=part, decision="approve", user="Bob Ray <bob@example.com>")
    assert err and "ecadlib confirm" in text, text
    assert not _item(ws, bom, part).get("approvals")
    (req,) = CF.pending(ws.root)
    assert _confirm(ws, req["id"], "no") == 2 and not _item(ws, bom, part).get("approvals")
    assert _confirm(ws, req["id"], "yes") == 0
    (rec,) = _item(ws, bom, part)["approvals"]
    assert rec["who"] == ANN and rec["decision"] == "approve"
    err, out = _call(ws, "approve_part", bom=bom, part=part, decision="approve", user="Bob Ray <bob@example.com>")
    assert not err and out["decision"]["who"] == ANN, out


@lite
def test_mcp_choice_is_never_an_approval(tmp_path, monkeypatch):
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    ws, got = Workspace(tmp_path / "ws", offline=True), {}
    monkeypatch.setattr(ws, "choose_part", lambda *a, **k: got.update(a=a, k=k) or {})
    M.call_tool(ws, "choose_part", {"bom": "b.csv", "part": "P", "kind": "symbol", "candidate_id": "kicad:Device:R",
                                    "user": "Bob Ray <bob@example.com>"})
    assert got["a"][-1] is None and got["k"] == {"approve": False}, got


def _quote(**over):
    q = {"step": "datasheet", "parts": 2, "names": ["A", "B"], "model": "claude-opus-5-5", "usd": 0.4, "usd_max": 0.9,
         "limit_usd": 1.0, "limit_name": "ask_above_usd", "price_known": True, "over_limit": False, "key_set": True,
         "approve_usd": 0.9, "text": "2 parts need the datasheet read by AI: about $0.40, at most $0.90."}
    q.update(over)
    return q


def _refused(ws, q, what, why):
    from ecadlib import confirm as CF
    try:
        ws._approve_cost(q, 1e9, True, True, what)
    except CF.NeedsPerson as e:
        return e.request
    raise AssertionError(why)


@lite
def test_mcp_paid_run_does_not_start_on_the_models_amount(tmp_path, monkeypatch):
    from ecadlib import confirm as CF
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws")
    monkeypatch.setattr(ws, "ai_quote", lambda bom, parts=None: _quote())
    (ws.root / "uploads" / "b.csv").write_text("Designator,MPN\nR1,X\n")
    err, text = _call(ws, "ai_read", bom="uploads/b.csv", approved_usd=0.9, over_limit_ok=True, price_unknown_ok=True)
    assert err and "ecadlib confirm" in text and "$0.90" in text, text
    (req,) = CF.pending(ws.root)
    assert req["kind"] == "spend" and req["tool"] == "ai_read"
    assert ws._approve_cost(_quote(), 0.9, False, False, "x") == (0.9, False)      # the page / command line: as before
    what = req["key"]["what"]
    with CF.over_mcp("ai_read"):
        for q in (_quote(), _quote(over_limit=True), _quote(price_known=False, usd_max=None)):
            _refused(ws, q, what, "a paid run started on the model's own approval")
        (other,) = CF.pending(ws.root)                 # a different quote is a different request: a new id
        assert other["id"] != req["id"] and not other["show"]["price_known"]
        assert _confirm(ws, req["id"], "yes") == 2     # the id the person was told for $0.90 no longer grants anything
        _refused(ws, _quote(), what, "an old id granted a new quote")
        (req,) = CF.pending(ws.root)
        assert _confirm(ws, req["id"], "nope") == 2
        _refused(ws, _quote(), what, "not granted, yet started")
        assert _confirm(ws, req["id"], "yes") == 0
        _refused(ws, _quote(usd_max=2.0, approve_usd=2.0), what, "a higher cost than the person granted started")
        assert ws._approve_cost(_quote(), 1e9, True, True, what) == (0.9, False)   # granted: runs, held to $0.90
        _refused(ws, _quote(), what, "one grant paid for two runs")


@lite
def test_mcp_paid_run_within_the_persons_own_cap(tmp_path):
    from ecadlib import confirm as CF
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws")
    mine = _quote(limit_usd=5.0, limit_name="max_cost_usd")                        # [datasheet] max_cost_usd = 5 in the file
    with CF.over_mcp("ai_read"):
        assert ws._approve_cost(mine, 1e9, True, True, "x") == (5.0, False)        # runs, never above their cap
        for q in (dict(mine, over_limit=True, usd_max=7.0), dict(mine, price_known=False, usd_max=None)):
            _refused(ws, q, "x", "over the person's cap, or no price: started on the model's word")
    assert not CF.grants_dir().exists()


@lite
def test_grant_file_in_the_workspace_is_not_a_grant(tmp_path):
    """What an MCP client can write (the workspace) is never the person's answer."""
    from ecadlib import confirm as CF
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws")
    with CF.over_mcp("ai_read"):
        req = _refused(ws, _quote(), "x", "started")
        forged = {"id": req["id"], "kind": "spend", "digest": req["digest"], "workspace": req["workspace"], "by": ANN,
                  "at": req["asked_at"], "approved_usd": 100, "over_limit_ok": True, "price_unknown_ok": True, "used_at": None}
        for d in ("confirm", "confirmed"):
            (ws.root / d).mkdir(exist_ok=True)
            (ws.root / d / f"{req['id']}.json").write_text(json.dumps(dict(req, **forged)))
        _refused(ws, _quote(), "x", "a file in the workspace counted as the person's grant")


BASE = '[ai]\nprovider = "openai"\nmodel = "gpt-5"\n'
REFUSED = {"model price table (a9 m1, old 4.4)": '\n[ai.profiles."gpt-5"]\nprice_in = 0.0001\nprice_out = 0.0001\n',
           "standard_land off": '\n[datasheet]\nstandard_land = "off"\n',
           "pin_drawing_check off": '\n[datasheet]\npin_drawing_check = "off"\n',
           "cross_check off": '\n[datasheet]\ncross_check = "off"\n',
           "cross_check always": '\n[datasheet]\ncross_check = "always"\n',
           "second-read model": '\n[datasheet]\ncross_check_model = "gpt-5-nano"\n',
           "datasheet folder": '\n[datasheet]\nfolder = "uploads"\n',
           "web search uses": '\n[datasheet]\nweb_search_max_uses = 9\n',
           "spending limit": '\n[datasheet]\nmax_cost_usd = 9999\n',
           "spending cap written at its default (it would count as the person's own)": '\n[datasheet]\nmax_cost_usd = 10.0\n',
           "model tier": '\n[ai.profiles."gpt-5-nano"]\ntier = "large"\npdf = true\n',
           "sources order": '\n[sources]\norder = ["datasheet"]\n',
           "store url": '\n[store]\nurl = "https://example.invalid/store"\n',
           "providers": '\n[providers]\norder = ["easyeda"]\n',
           "ai reviewer": '\n[review]\nai_reviewer = "assistant"\n',
           "release parity": '\n[release]\nparity = "warn"\n',
           "missing 3D allowed": '\n[release]\nallow_missing_3d = true\n',
           "a setting nobody listed": '\n[brand_new]\nswitch = true\n',
           "default for a required parameter": '\n[parameters.defaults]\nManufacturer = "ACME"\n',
           "default datasheet link": '\n[parameters.defaults]\nComponentLink1URL = "https://example.invalid/x.pdf"\n',
           "house style file": '\n[style]\nprofile = "uploads/style.json"\n'}
SAVED = {"naming": '\n[naming]\ncomponent = "{mpn}"\ncase = "upper"\n',
         "folders": '\n[folders]\nroot = "Parts"\n[folders.map]\nResistors = "Passives/R"\n',
         "bom columns": '\n[bom.columns]\nmpn = "Part No"\n',
         "symbol style": '\n[symbols]\nresistor = "iec"\n',
         "parameter names and a default": '\n[parameters]\nsupplier_slots = 3\n[parameters.names]\n"Manufacturer Part Number" = "MPN"\n'
                                          '[parameters.defaults]\n"Supplier 1" = "LCSC"\n',
         "layers and density": '\n[layers]\nassembly = "Mechanical7"\n[footprint]\ndensity = "L"\n',
         "a stricter gate": '\n[release]\nheight = "hold"\n'}


@lite
def test_save_rules_over_mcp_is_an_allow_list(tmp_path):
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws")
    ws.mcp_options = M.Options(confine=True)
    f = ws.root / "ecadlib.toml"
    for label, extra in REFUSED.items():
        f.write_text(BASE)
        err, text = _call(ws, "save_rules", toml=BASE + extra, overwrite=True)
        assert err and "save_rules cannot change" in text, (label, text)
        assert f.read_text() == BASE, label
    for label, extra in SAVED.items():
        f.write_text(BASE)
        err, text = _call(ws, "save_rules", toml=BASE + extra, overwrite=True)
        assert not err and f.read_text() == BASE + extra, (label, text)


# ------------------------------------------------------------------ second review (C2): what the person is shown
def _shown(ws, rid, answer):
    """(exit code, every character `ecadlib confirm` wrote for the person, prompts included)."""
    from ecadlib import confirm as CF
    asked = []

    def inp(prompt=""):
        asked.append(prompt)
        return answer
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = CF.main([rid, "--workspace", str(ws.root), "--user", ANN], inp=inp)
    return rc, out.getvalue() + "".join(asked), asked


@lite
def test_mcp_sign_off_cannot_redraw_what_the_person_is_shown(tmp_path):
    """The comment "x<erase line><carriage return>Sign-off ...: reject OTHER" made the terminal show another part
    and decision than the one "yes" then recorded. Such text is refused when asked, never printed raw even from a
    request file written by hand, and the question itself names what yes does."""
    from ecadlib import confirm as CF
    ws, bom, _ = _demo(tmp_path)
    part = next(i["name"] for i in json.loads(_plan_of(ws, bom).read_text()) if i["status"] == "ready")
    fake = "Sign-off, asked by your assistant (approve_part): reject CAP-0402-100nF, comment: duplicate"
    for field, bad in (("comment", "x\x1b[2K\r" + fake), ("role", "QA\x1b[2K\r" + fake), ("comment", "x\r" + fake),
                       ("comment", "x\n" + fake), ("comment", "x‮" + fake), ("comment", "x\x9b2K" + fake),
                       ("comment", "y" * 2000)):
        err, text = _call(ws, "approve_part", bom=bom, part=part, decision="approve", **{field: bad})
        assert err and "ecadlib confirm" not in text and not CF.pending(ws.root), (field, bad[:12], text)
    err, text = _call(ws, "approve_part", bom=bom, part=part, decision="approve", comment="looks right, Ann said so")
    assert err and "ecadlib confirm" in text, text
    (req,) = CF.pending(ws.root)
    f = Path(ws.root) / CF.REQUESTS / f"{req['id']}.json"            # and a request file edited by hand
    raw = json.loads(f.read_text())
    raw["show"]["comment"] = raw["key"]["comment"] = "x\x1b[2K\r" + fake
    f.write_text(json.dumps(raw))
    rc, seen, asked = _shown(ws, req["id"], "no")
    assert rc == 2 and "\x1b" not in seen and "\r" not in seen, repr(seen)
    assert f"APPROVE of {part}" in asked[-1] and ANN in asked[-1], asked       # the question says what yes records
    assert not _item(ws, bom, part).get("approvals")


@lite
def test_mcp_paid_request_gets_a_new_id_when_the_amount_changes(tmp_path):
    from ecadlib import confirm as CF
    q = {"price_known": True, "over_limit": False, "usd": 0.4, "usd_max": 0.5, "approve_usd": 0.5, "text": "about $0.40"}
    ids = []
    for q2 in (q, q, dict(q, usd=30.0, usd_max=40.0, approve_usd=40.0, text="about $30")):
        with CF.over_mcp("ai_read"):
            try:
                CF.spend(tmp_path, q2, 100000, "bom.csv")
                raise AssertionError("started")
            except CF.NeedsPerson as e:
                ids.append(e.request["id"])
    assert ids[0] == ids[1] != ids[2], ids
    assert [r["show"]["approve_usd"] for r in CF.pending(tmp_path)] == [40.0], CF.pending(tmp_path)   # the old one is gone
    with CF.over_mcp("visual_check"):
        try:
            CF.spend(tmp_path, q, 1, "visual check of U1\x1b[2K\rsomething else")
            raise AssertionError("asked")
        except CF.NeedsPerson:
            raise AssertionError("a request with an escape code was written down")
        except ValueError as e:
            assert "\x1b" not in str(e), repr(str(e))
