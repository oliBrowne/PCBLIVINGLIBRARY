"""Regression tests for the bug-hunt fixes the owner decided (MCP-2, UX-15 / DS-62, UX-16, UX-32).
Nothing live: the Messages API is test_datasheet_batch's fake, part sources are offline or fail at a dead proxy.
    python3 -m tests.run_all test_bughunt_decided"""
import contextlib, io, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import approvals as AP, datasheet as D, mcp_server as M
from ecadlib.app import Workspace
from ecadlib.httpcache import CachedHTTP
from tests import test_ai_cost as TA, test_datasheet_batch as TB


@contextlib.contextmanager
def _quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


# ------------------------------------------------------------------ MCP-2: paid MCP tools need a server-side approval
def test_mcp2_datasheet_batch_needs_the_approved_amount():
    with TA._workspace() as (ws, bom, api), _quiet():
        ws.librarian_run(bom, ai=False)
        assert not api.of("/v1/messages")
        assert "approved_usd" in next(t for t in M.TOOLS if t["name"] == "datasheet_batch")["inputSchema"]["required"]
        for args in ({}, {"approved_usd": 0.0001}, {"approved_usd": float("nan")}):
            try:
                M.call_tool(ws, "datasheet_batch", dict(args, bom=bom, sync=True, over_limit_ok=True))
                raise AssertionError(f"paid reads without approval: {args}")
            except ValueError as e:
                assert "approve" in str(e) or "more than" in str(e), e
        assert not api.of("/v1/messages"), "a paid call before the person said yes"
        q = ws.datasheet_quote(bom, sync=True)
        r = M.call_tool(ws, "datasheet_batch", {"bom": bom, "sync": True, "approved_usd": q["approve_usd"],
                                                 "over_limit_ok": True})
        assert api.of("/v1/messages") and r["parts"], r
        est = M.call_tool(ws, "datasheet_estimate", {"bom": bom})
        assert "ai" in est                                              # the quote datasheet_batch's approval is of


def test_mcp2_visual_check_quotes_first_and_holds_the_call_to_the_approval():
    from ecadlib import visual_check as VC
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        fp = ws.root / "footprints" / "X.kicad_mod"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text("(footprint X)")
        seen = []

        def prove(item, root, rules, client=None, out_dir=None, render_only=False, yes=False):
            seen.append((render_only, rules["agent"]["proof"]["max_cost_usd"]))
            return {"verdict": "skipped" if render_only else "pass", "images": {}, "model": "claude-opus-5-5",
                    "estimate": {"input_tokens": 9000, "usd": 0.05, "usd_max": 0.3}}
        with mock.patch.object(VC, "prove", prove), mock.patch.object(VC, "write_proof", lambda pr, f: f):
            q = M.call_tool(ws, "visual_check", {"target": "footprints/X.kicad_mod", "render_only": True})["ai"]
            assert q["approve_usd"] == 0.3 and seen == [(True, 0.0)], (q, seen)
            for usd in (None, 0.1):
                try:
                    M.call_tool(ws, "visual_check", {"target": "footprints/X.kicad_mod", "approved_usd": usd})
                    raise AssertionError("a paid visual check without approval")
                except ValueError as e:
                    assert "approve" in str(e) or "more than" in str(e), e
            assert all(r for r, _ in seen), seen                        # nothing but render-only runs so far
            r = M.call_tool(ws, "visual_check", {"target": "footprints/X.kicad_mod", "approved_usd": 0.3})
            assert r["verdict"] == "pass" and seen[-1] == (False, 0.3), seen


def test_mcp2_apply_house_style_ai_is_a_quote_until_approved():
    from ecadlib import house_style as HS
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        calls = []
        q = {"step": "style-apply", "model": "m", "usd": 0.01, "usd_max": 0.02, "price_known": True, "over_limit": False,
             "key_set": True, "approve_usd": 0.02, "text": "quote"}
        plan_f = ws.root / "librarian" / "x" / "plan.json"
        plan_f.parent.mkdir(parents=True)
        plan_f.write_text("[]")
        with mock.patch.object(Workspace, "_style_or_raise", lambda self: {}), \
                mock.patch.object(Workspace, "_plan", lambda self, bom: (None, plan_f, [])), \
                mock.patch.object(Workspace, "restyle_quote", lambda self, *a: dict(q)), \
                mock.patch.object(HS, "adapt_items", lambda *a, **k: {}), \
                mock.patch.object(HS, "adapt_with_model", lambda *a, **k: calls.append(1) or ({}, [])):
            r = M.call_tool(ws, "apply_house_style", {"bom": "b.csv", "ai": True})
            assert r["ai"]["approve_usd"] == 0.02 and not calls and not r["written"], r
            try:
                M.call_tool(ws, "apply_house_style", {"bom": "b.csv", "ai": True, "approved_usd": 0.001})
                raise AssertionError("approved less than the quote")
            except ValueError:
                assert not calls
            M.call_tool(ws, "apply_house_style", {"bom": "b.csv", "ai": True, "approved_usd": 0.02})
            assert calls == [1]
    # the quote itself builds the requests and sends none
    with tempfile.TemporaryDirectory() as d, mock.patch.object(HS, "_sender", lambda inf: (_ for _ in ()).throw(
            AssertionError("restyle_quote sent a request"))):
        ws = Workspace(d)
        items = [{"name": "RES-0603-10k", "description": "res 10k", "comment": "", "parameters": [],
                  "footprint": {"ref": "R_0603"}, "symbol": {"ref": "R"}}]
        q = ws.restyle_quote(items, {"guide": {}, "settings": [], "direction": []})
        assert q["requests"] >= 1 and q["step"] == "style-apply" and "approve_usd" in q, q


# ------------------------------------------------------------------ UX-15 / DS-62: --offline calls no model
def test_ux15_offline_reader_has_no_key_and_refuses_to_call_the_model():
    with tempfile.TemporaryDirectory() as d:
        rd = D.Reader(d, d, key=TB.KEY, http=CachedHTTP(Path(d) / "c", offline=True), log=None)
        assert rd.offline and rd.key is None
        try:
            rd.client()
            raise AssertionError("an offline Reader built a client")
        except D.DatasheetError as e:
            assert str(e) == D.OFFLINE == "--offline: datasheet not read; run without --offline to read it"
        online = D.Reader(d, d, key=TB.KEY, http=CachedHTTP(Path(d) / "c"), log=None)
        assert not online.offline and online.key == TB.KEY


def test_ux15_offline_librarian_with_a_stored_key_sends_nothing_to_the_model():
    with TA._workspace() as (ws, bom, api), _quiet():
        ws.offline = True
        r = ws.librarian_run(bom, ai=True)                     # the command-line path: a Reader of its own
        assert not api.of("/v1/messages") and not api.of("/count_tokens"), api.calls if hasattr(api, "calls") else ""
        tried = " ".join(x.get("tried") or "" for x in r["review"])
        assert "--offline: datasheet not read" in tried, tried
        q = ws.librarian_run(bom, ai=False)["ai"]               # the web UI's quote says why, not "no API key"
        assert q is None or ("--offline" in q["text"] and "No API key" not in q["text"]), q
        for call in (lambda: ws.ai_read(bom, 100, True), lambda: ws.datasheet_batch(bom, approved_usd=100)):
            try:
                call()
                raise AssertionError("a paid read offline")
            except ValueError as e:
                assert str(e) == D.OFFLINE, e
        assert not api.of("/v1/messages")


def test_ux15_offline_blocker_names_offline_not_the_key():
    from ecadlib import firstrun as FR
    rows = [{"part": "P1", "status": "held", "tried": f"datasheet: {D.OFFLINE} (https://x/ds.pdf)"}]
    msgs = " ".join(m for m, _ in FR.blockers(rows, None, offline=True))
    assert "--offline sends nothing to the model" in msgs and "is not set" not in msgs, msgs


# ------------------------------------------------------------------ UX-16: a re-upload continues the plan
BOM = ("Designator,Quantity,Value,Footprint,Manufacturer,Manufacturer Part Number,Datasheet\n"
       "R1,1,10k,0603,Yageo,RC0603FR-0710KL,https://example.com/rc0603.pdf\n")


def test_ux16_reupload_with_the_same_name_continues_plan_ledger_and_signoffs():
    with tempfile.TemporaryDirectory() as d, _quiet():
        ws = Workspace(d, [], offline=True)
        b1 = ws.save_upload("bom.csv", BOM.encode())
        r = ws.librarian_run(b1, sources=["standard"], ai=False)
        assert r["ready"] == 1, r["review"]
        ws.approve_part(b1, "RES-0603-10k", "approve", "Ann <ann@x.com>")
        b2 = ws.save_upload("bom.csv", BOM.replace("Yageo", "Vishay").encode())
        assert b1 != b2 and ws._librarian_dir(ws._bom_path(b1)) == ws._librarian_dir(ws._bom_path(b2))
        assert ws._librarian_dir(ws._bom_path(ws.save_upload("other.csv", BOM.encode()))) != ws._librarian_dir(ws._bom_path(b1))
        ws.librarian_run(b2, sources=["standard"], ai=False)
        it = {i["name"]: i for i in json.loads((ws._librarian_dir(ws._bom_path(b2)) / "plan.json").read_text())}["RES-0603-10k"]
        assert (it.get("change") or {}).get("state") == "held", it.get("change")     # held as the CLI holds it
        assert [a["who"] for a in it.get("approvals") or []] == ["Ann <ann@x.com>"], it.get("approvals")
        assert len(list((ws.root / "librarian").iterdir())) == 1


def test_ux16_plans_made_per_upload_before_stay_readable():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [], offline=True)
        b = ws.save_upload("bom.csv", BOM.encode())
        p = ws._bom_path(b)
        old = ws.root / "librarian" / f"bom_{p.parent.name}"           # how a plan was keyed until now
        old.mkdir(parents=True)
        (old / "plan.json").write_text("[]")
        assert ws._librarian_dir(p) == old
        flat = ws.root / "uploads" / "bom.csv"                          # an older workspace's flat upload: its own key
        flat.write_text(BOM)
        assert ws._key(flat) == "bom" and ws._key(ws._bom_path(ws.save_upload("bom.csv", BOM.encode()))) == "bom@csv"


# ------------------------------------------------------------------ UX-32: a reject holds without a policy
def test_ux32_reject_holds_the_part_without_a_policy():
    with tempfile.TemporaryDirectory() as d, _quiet():
        ws = Workspace(d, [], offline=True)
        b = ws.save_upload("bom.csv", BOM.encode())
        assert ws.librarian_run(b, sources=["standard"], ai=False)["ready"] == 1
        r = ws.approve_part(b, "RES-0603-10k", "reject", "Ann <ann@x.com>", comment="pin 1 is ADJ")
        assert r["decision"]["status"] == "awaiting_approval" and not r["decision"]["enforced"], r["decision"]
        plan_f = ws._librarian_dir(ws._bom_path(b)) / "plan.json"
        it = json.loads(plan_f.read_text())[0]
        assert AP.load_policy(plan_f.parent) is None and "rejected" in (AP.push_block(it, None, plan_f.parent) or "")
        ws.release_check(b)                                             # a re-check keeps the hold
        it = json.loads(plan_f.read_text())[0]
        assert it["status"] == "awaiting_approval" and "rejected by" in " ".join(it["approval"]["missing"]), it
        from ecadlib import parts_export as PE
        assert not PE.rows(plan_f, "ecadlib", False)                    # not exported either
        ws.approve_part(b, "RES-0603-10k", "approve", "Ann <ann@x.com>")    # taken back by the same person
        it = json.loads(plan_f.read_text())[0]
        assert it["status"] == "ready" and AP.push_block(it, None, plan_f.parent) is None, it
        ws.release_check(b)
        assert json.loads(plan_f.read_text())[0]["status"] == "ready"


def test_ux32_cli_says_the_reject_holds():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [], offline=True)
        b = ws.save_upload("bom.csv", BOM.encode())
        with _quiet():
            ws.librarian_run(b, sources=["standard"], ai=False)
        plan_f = ws._librarian_dir(ws._bom_path(b)) / "plan.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            AP.main([str(plan_f), "reject", "--part", "RES-0603-10k", "--user", "Ann", "-m", "wrong pinout"])
        s = out.getvalue()
        assert "-> awaiting_approval" in s and "held: not pushed or exported" in s and "still pushed" not in s, s
        assert "a rejection still holds the part" in s, s
