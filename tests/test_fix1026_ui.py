"""2026-10-26 fixes for review round 4, web UI (R-UI-1, R-UI-2). Offline.
Run:  python3 -m pytest -q tests/test_fix1026_ui.py   (or python -m tests.run_all)"""
import contextlib, json
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]
BOB = "Bob Roe <bob@example.com>"
HDR = "kicad:Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical"
SHIELD = "kicad:Connector:USB_C_Receptacle_Fake_4P"


@contextlib.contextmanager
def _held_with_policy(monkeypatch):
    """resolve's test plan with approvals.json {"required": 1} and USBC-FAKE held on its pins; this machine is BOB."""
    from tests.test_resolve_ui import workspace
    monkeypatch.setenv("ECADLIB_USER", BOB)
    with workspace() as (ws, bom, plan):
        (plan.parent / "approvals.json").write_text('{"required": 1}')
        ws.choose_part(bom, "FTSH-105-TEST", "footprint", HDR)
        assert ws.choose_part(bom, "USBC-FAKE", "symbol", SHIELD)["chosen"]["status"] == "held"
        yield ws, bom, plan


def _self_approval_refused(ws, bom, plan):
    it = next(i for i in json.loads(plan.read_text()) if i["name"] == "USBC-FAKE")
    assert it["allow"]["user"] == BOB, it["allow"]                 # the release is attributed to this machine's person
    assert it["status"] == "awaiting_approval", it["status"]
    try:
        ws.approve_part(bom, "USBC-FAKE", "approve", BOB)
    except ValueError as e:
        assert "no_self_approval" in str(e), e
    else:
        raise AssertionError("the person who released it anyway approved it themselves")


@lite
def test_fix_ui_01_release_anyway_without_a_name_is_attributed(monkeypatch):
    """R-UI-1: Workspace.release_check (web UI, MCP) with allow and no user stamped nobody, so no_self_approval never
    fired; now it stamps approvals.identity() as the CLI does."""
    for user in (None, "", "   "):
        with _held_with_policy(monkeypatch) as (ws, bom, plan):
            ws.release_check(bom, allow=["USBC-FAKE"], user=user)
            _self_approval_refused(ws, bom, plan)


@lite
def test_fix_ui_02_web_ui_release_anyway_with_an_empty_name(monkeypatch):
    from tests.test_ui import serve
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        srv, call = serve(ws)
        try:
            st, j = call("POST", "/api/release-check", {"bom": bom, "allow": ["USBC-FAKE"], "user": None})
            assert st == 200, j
            _self_approval_refused(ws, bom, plan)
            st, j = call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve", "user": BOB})
            assert st == 400 and "no_self_approval" in j["error"], (st, j)
        finally:
            srv.shutdown()
            srv.server_close()


@lite
def test_fix_ui_03_mcp_release_check_without_a_user(monkeypatch):
    from ecadlib import mcp_server as M
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "release_check", "arguments": {"bom": bom, "allow": ["USBC-FAKE"]}}})
        assert not r["result"].get("isError"), r
        _self_approval_refused(ws, bom, plan)
        r = M.handle(ws, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "approve_part", "arguments": {"bom": bom, "part": "USBC-FAKE",
                                                                           "decision": "approve", "user": BOB}}})
        assert r["result"].get("isError") and "no_self_approval" in json.dumps(r), r


@lite
def test_fix_ui_04_named_release_keeps_the_typed_name(monkeypatch):
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        ws.release_check(bom, allow=["USBC-FAKE"], user="  Ann Lee  ")
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "USBC-FAKE")
        assert it["allow"]["user"] == "Ann Lee", it["allow"]

