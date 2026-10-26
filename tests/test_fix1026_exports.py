"""Fixes 2026-10-26 (exports): the Altium 365 workspace guard on the UI/MCP push, the CLI `dblib` release gate, and
pulled Altium 365 parts through the release check on the KiCad / parts-table routes."""
from unittest import mock

from tests.fixtures_lite import lite


def _ws_with_rules(tmp_path, monkeypatch, workspace=None):
    from ecadlib.app import Workspace
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "w"
    root.mkdir()
    if workspace is not None:
        (root / "ecadlib.toml").write_text(f'[altium365]\nworkspace = "{workspace}"\n', encoding="utf-8")
    ws = Workspace(root)
    seen = []
    monkeypatch.setattr(Workspace, "_altium365_push",
                        lambda self, bom, url, dry_run, lib_plan: seen.append((url, dry_run)) or {"ok": True})
    return ws, seen


def _refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("not refused")


@lite
def test_real_push_to_another_workspace_than_the_rules_name_is_refused(tmp_path, monkeypatch):
    """The web UI / MCP push sent a real push to any workspace URL the token could see (e.g. the team workspace)."""
    ws, seen = _ws_with_rules(tmp_path, monkeypatch, "https://mine.365.altium.com")
    msg = _refused(lambda: ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=False))
    assert "https://team.365.altium.com" in msg and "https://mine.365.altium.com" in msg and "[altium365]" in msg, msg
    assert seen == []
    ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=True)         # a dry run sends nothing: allowed
    ws.altium365_push("b.csv", "https://Mine.365.Altium.com/", dry_run=False)       # same workspace, other spelling
    ws.altium365_push("b.csv", None, dry_run=False)                                 # no URL: the configured one
    assert seen == [("https://team.365.altium.com", True), ("https://Mine.365.Altium.com/", False),
                    ("https://mine.365.altium.com", False)], seen


@lite
def test_real_push_with_no_configured_workspace_is_refused(tmp_path, monkeypatch):
    ws, seen = _ws_with_rules(tmp_path, monkeypatch, None)
    msg = _refused(lambda: ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=False))
    assert "[altium365]" in msg and "workspace" in msg and "ecadlib.toml" in msg, msg
    ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=True)
    ws.altium365_push("b.csv", "", dry_run=True)
    assert seen == [("https://team.365.altium.com", True), ("", True)], seen


@lite
def test_mcp_real_push_goes_through_the_guard(tmp_path, monkeypatch):
    from ecadlib import mcp_server as M
    ws, seen = _ws_with_rules(tmp_path, monkeypatch, "https://mine.365.altium.com")
    ws.mcp_options = M.Options()
    from tests.test_bughunt_mcpcli import _call
    r = _call(ws, "altium365_push", {"bom": "b.csv", "workspace": "https://team.365.altium.com", "dry_run": False})
    assert r["isError"] and "[altium365]" in str(r), r
    assert seen == []
