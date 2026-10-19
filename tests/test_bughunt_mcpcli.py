"""Regression tests for the 2026-10 bug hunt, MCP server / app.Workspace / CLI and config group (MCP-*, CLI-*, WEB-*).
Nothing live: no model, Altium 365 or network call is made. Run:
    python3 -m tests.run_all test_bughunt_mcpcli"""
import contextlib, io, json, math
from pathlib import Path
from unittest import mock

from ecadlib import mcp_server as M
from ecadlib.app import Workspace
from tests.fixtures_lite import lite


def _tool(name):
    return next(t for t in M.TOOLS if t["name"] == name)


def _call(ws, name, args):
    with contextlib.redirect_stderr(io.StringIO()):
        return M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": args}})["result"]


def _txt(r):
    return r["content"][0]["text"]


# ------------------------------------------------------------------ MCP-8
@lite
def test_mcp8_dry_run_empty_zero_or_no_is_never_a_real_push(tmp_path):
    """dry_run "" / 0 / "no" became False and altium365_push released for real; only JSON false (or "false") may."""
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options()
    seen = []
    with mock.patch.object(Workspace, "altium365_push", lambda self, bom, w=None, dry_run=True: seen.append(dry_run) or {}):
        for v in ("", "  "):
            r = _call(ws, "altium365_push", {"bom": "b.csv", "dry_run": v})
            assert not r["isError"], r
        for v in (0, 1, "no", "0", "off"):
            r = _call(ws, "altium365_push", {"bom": "b.csv", "dry_run": v})
            assert r["isError"] and "dry_run" in _txt(r), (v, r)
        assert seen == [True, True], seen                     # "" = not given = the safe default
        _call(ws, "altium365_push", {"bom": "b.csv", "dry_run": False})
        _call(ws, "altium365_push", {"bom": "b.csv", "dry_run": "false"})
    assert seen == [True, True, False, False], seen
    assert M._normalize(_tool("find_duplicates"), {"bom": "b", "merge": "1"})["merge"] == "1"   # schema refuses it
    assert M._normalize(_tool("triage_bom"), {"bom": "b", "summary_only": "yes"})["summary_only"] is True   # harmless


# ------------------------------------------------------------------ MCP-3
@lite
def test_mcp3_save_rules_cannot_redirect_tokens_or_raise_money_limits(tmp_path):
    """save_rules guarded only [ai]-style base_url / provider / api_key_env: github_api + github_token_env =
    ANTHROPIC_API_KEY (sent by a later `ecadlib upkeep --github`), [notify] targets and the cost limits went through."""
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options()
    for toml in ("[agent]\ngithub_api = 'https://evil.example'\n",
                 "[agent]\ngithub_token_env = 'ANTHROPIC_API_KEY'\n",
                 "[agent]\ngitlab_api = 'https://evil.example/api/v4'\n",
                 "[agent]\ngithub_repo = 'evil/repo'\n",
                 "[[notify.targets]]\ntype = 'json'\nurl_env = 'X_URL'\ntoken_env = 'ANTHROPIC_API_KEY'\n",
                 "[datasheet]\nask_above_usd = 100000.0\n",
                 "[datasheet]\nmax_cost_usd = 5000.0\n"):
        r = _call(ws, "save_rules", {"toml": toml, "overwrite": True})
        assert r["isError"] and "cannot change" in _txt(r), (toml, r)
    assert not (ws.root / "ecadlib.toml").exists()
    # a setting written at its default value is no change, and other settings still save
    r = _call(ws, "save_rules", {"toml": "[datasheet]\nask_above_usd = 1\n[naming]\n", "overwrite": True})
    assert not r["isError"], r


# ------------------------------------------------------------------ MCP-7
@lite
def test_mcp7_flat_and_folder_uploads_never_share_a_librarian_folder(tmp_path):
    """uploads/demo/demo_bom.csv and uploads/demo_bom_demo.csv both had the key demo_bom_demo: one BOM's librarian run
    replaced the other's plan and sign-offs."""
    ws = Workspace(tmp_path / "w")
    up = ws.root / "uploads"
    (up / "demo").mkdir(parents=True)
    a, b = up / "demo" / "demo_bom.csv", up / "demo_bom_demo.csv"
    for f in (a, b):
        f.write_text("Designator,Qty,MPN\nR1,1,X\n")
    assert ws._key(a) == "demo_bom_demo"                       # the folder upload keeps its key (existing plans)
    assert ws._key(b) != ws._key(a) and ws._key(b).startswith("demo_bom_demo_")
    assert ws._librarian_dir(b) != ws._librarian_dir(a)
    c = up / "other_x.csv"
    c.write_text("Designator,Qty,MPN\nR1,1,X\n")
    assert ws._key(c) == "other_x"                             # an ordinary flat upload keeps its plain name


# ------------------------------------------------------------------ MCP-1 / CLI-18
def test_mcp1_a_nan_or_infinite_approval_is_refused():
    from ecadlib import ai_cost as AC
    q = {"price_known": True, "over_limit": False, "usd": 1.0, "usd_max": 3.2, "limit_usd": 1.0, "model": "m"}
    for bad in ("NaN", float("nan"), float("inf"), "inf", "1e309", -1, True):
        try:
            AC.approve(dict(q, over_limit=False), bad, over_limit_ok=True)
            raise AssertionError(f"{bad!r} approved")
        except ValueError as e:
            assert "approve the cost first" in str(e), e
    assert AC.approve(q, 3.2)[0] == 3.2
    a = M._normalize(_tool("ai_read"), {"bom": "b", "approved_usd": "NaN"})
    assert a["approved_usd"] == "NaN"                          # left a string: the schema refuses it
    assert "approved_usd" in M._arg_errors(_tool("ai_read"), {"bom": "b", "approved_usd": "inf"})


@lite
def test_cli18_nan_and_inf_money_settings_are_rejected(tmp_path):
    from ecadlib import rules as RU
    for v in ("nan", "inf", "+inf"):
        f = tmp_path / "ecadlib.toml"
        f.write_text(f"[datasheet]\nmax_cost_usd = {v}\nask_above_usd = {v}\n")
        try:
            RU.load(f)
            raise AssertionError(f"{v} accepted")
        except ValueError as e:
            assert "max_cost_usd" in str(e) and "nan or inf" in str(e), e
    f.write_text("[datasheet]\nmax_cost_usd = 2.5\n")
    assert RU.load(f)["datasheet"]["max_cost_usd"] == 2.5
