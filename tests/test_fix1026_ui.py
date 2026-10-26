"""2026-10-26 fixes for review round 4, web UI (R-UI-1, R-UI-2). Offline.
Run:  python3 -m pytest -q tests/test_fix1026_ui.py   (or python -m tests.run_all)"""
import contextlib, io, json, re
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


def _item(plan):
    return next(i for i in json.loads(plan.read_text()) if i["name"] == "USBC-FAKE")


def _refused_no_name(plan, msg):
    assert "name" in msg and "user is required" in msg, msg
    it = _item(plan)
    assert not it.get("allow") and it["status"] == "held", it      # nothing recorded


@lite
def test_fix_ui_01_release_anyway_without_a_name_is_refused(monkeypatch):
    """R-UI-1 (fixes6 M2): Workspace.release_check (web UI, MCP) with allow and no user stamped nobody (no_self_approval
    never fired); stamping this computer's identity instead named the wrong person on a shared computer. A release
    anyway now needs the person's typed name, as an approval does; the CLI keeps stamping approvals.identity()."""
    for user in (None, "", "   ", 5):
        with _held_with_policy(monkeypatch) as (ws, bom, plan):
            try:
                ws.release_check(bom, allow=["USBC-FAKE"], user=user)
            except ValueError as e:
                _refused_no_name(plan, str(e))
            else:
                raise AssertionError(f"release anyway with user {user!r} was accepted")
            ws.release_check(bom, unallow=["USBC-FAKE"])               # a re-check / hold again needs no name
            ws.release_check(bom)


@lite
def test_fix_ui_02_web_ui_release_anyway_with_an_empty_name(monkeypatch):
    from tests.test_ui import serve
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        srv, call = serve(ws)
        try:
            for user in (None, "  "):
                st, j = call("POST", "/api/release-check", {"bom": bom, "allow": ["USBC-FAKE"], "user": user})
                assert st == 400, (st, j)
                _refused_no_name(plan, j["error"])
            st, j = call("POST", "/api/release-check", {"bom": bom, "allow": ["USBC-FAKE"], "user": BOB})
            assert st == 200, j
            st, j = call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve", "user": BOB})
            assert st == 400 and "no_self_approval" in j["error"], (st, j)
        finally:
            srv.shutdown()
            srv.server_close()


@lite
def test_fix_ui_03_mcp_release_check_without_a_user(monkeypatch):
    from ecadlib import mcp_server as M
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        call = lambda i, name, args: M.handle(ws, {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                                   "params": {"name": name, "arguments": args}})
        r = call(1, "release_check", {"bom": bom, "allow": ["USBC-FAKE"]})
        assert r["result"].get("isError"), r
        _refused_no_name(plan, json.dumps(r))
        assert not call(2, "release_check", {"bom": bom, "allow": ["USBC-FAKE"], "user": BOB})["result"].get("isError")
        r = call(3, "approve_part", {"bom": bom, "part": "USBC-FAKE", "decision": "approve", "user": BOB})
        assert r["result"].get("isError") and "no_self_approval" in json.dumps(r), r


@lite
def test_fix_ui_03b_cli_still_stamps_identity(monkeypatch):
    """The CLI has a person at the keyboard: release-check --allow without --user keeps stamping approvals.identity()."""
    from ecadlib import release_check as RC
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                RC.main([str(plan), "--allow", "USBC-FAKE", "-o", str(plan.parent / "rc")])
            except SystemExit:
                pass
        assert _item(plan)["allow"]["user"] == BOB, _item(plan).get("allow")


@lite
def test_fix_ui_04_named_release_keeps_the_typed_name(monkeypatch):
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        ws.release_check(bom, allow=["USBC-FAKE"], user="  Ann Lee  ")
        assert _item(plan)["allow"]["user"] == "Ann Lee", _item(plan)["allow"]


def test_fix_ui_05_release_anyway_toast_follows_the_real_status():
    """R-UI-2: the toast after Release anyway said "goes to Altium with the next push" for a part awaiting sign-off."""
    page = (ROOT / "ecadlib" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    body = page[page.index("async function release(part, allow)"):]
    body = body[:body.index("\n}\n")]
    assert re.search(r'=== "awaiting_approval"', body), "no awaiting_approval branch in release()"
    i_aw, i_push = body.index('"awaiting_approval"'), body.index("goes to Altium with the next push")
    assert i_aw < i_push, "the awaiting_approval branch must come before the ready text"
    branch = body[i_aw:i_push]
    assert "sign-off" in branch and "missing" in branch, branch
    assert 'st === "ready"' in body[:i_push], "the next-push text is only for a part that is ready"


def test_fix_ui_06_page_asks_for_a_name_before_release_anyway():
    """fixes6 M2: the page sent user null for Release anyway with an empty name box; now it asks for the name first,
    as Approve / Reject do (signer()), and never sends a release without one."""
    page = (ROOT / "ecadlib" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    body = page[page.index("async function release(part, allow)"):]
    body = body[:body.index("\n}\n")]
    head = body[:body.index('api("/api/release-check"')]
    assert "signer(" in head and "return" in head, head
    assert "|| null" not in body, body


@lite
def test_fix_ui_07_release_anyway_in_chromium(monkeypatch):
    """fixes6 L4: behaviour, not page text. In real Chromium (skipped without node + Playwright): Release anyway with
    an empty name box asks for the name and sends nothing; with a name it sends it, and the toast of a part that then
    awaits sign-off says so instead of "goes to Altium with the next push"."""
    import os, subprocess, threading
    from ecadlib.ui import server as S
    from tests.test_ui_browser import _browser, _skip
    b = _browser()
    if not b:
        return _skip("node + Playwright + Chromium")
    node, mods, exe = b
    with _held_with_policy(monkeypatch) as (ws, bom, plan):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ws.triage(bom, offline=True)                               # the page opens a BOM from its history
        srv = S.make_server(ws, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = {"url": f"http://127.0.0.1:{srv.server_address[1]}/", "part": "USBC-FAKE", "executable": exe}
            p = subprocess.run([node, str(ROOT / "tests" / "fix1026_ui_browser.js"), json.dumps(cfg)], capture_output=True,
                               text=True, env={**os.environ, "NODE_PATH": mods}, timeout=300)
        finally:
            srv.shutdown()
            srv.server_close()
        assert p.returncode == 0, p.stderr[-3000:]
        checks = json.loads(p.stdout.strip().splitlines()[-1])["checks"]
        assert len(checks) == 4 and all(c[1] for c in checks), checks
        it = _item(plan)
        assert it["allow"]["user"] == "Ann Lee" and it["status"] == "awaiting_approval", it
