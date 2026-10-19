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


# ------------------------------------------------------------------ MCP-4
@lite
def test_mcp4_confined_server_fetches_only_public_https_links(tmp_path):
    """--confine (HTTP mode) skipped every http(s) value, so visual_check datasheet=http://127.0.0.1/... reached a
    service only this computer can see."""
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options(confine=True)
    called = []
    with mock.patch.object(Workspace, "visual_check", lambda self, *a, **k: called.append(a) or {}):
        for url in ("http://127.0.0.1:8080/admin/secret", "https://127.0.0.1/x.pdf", "https://localhost/x.pdf",
                    "https://10.0.0.5/x.pdf", "https://169.254.169.254/latest/meta-data", "https://[::1]/x.pdf",
                    "HTTP://127.0.0.1/x.pdf"):
            r = _call(ws, "visual_check", {"target": "X", "datasheet": url})
            assert r["isError"] and "datasheet" in _txt(r), (url, r)
    assert not called
    ws.mcp_options = M.Options(confine=False)                  # stdio without --confine: unchanged
    with mock.patch.object(Workspace, "visual_check", lambda self, *a, **k: called.append(a) or {}):
        assert not _call(ws, "visual_check", {"target": "X", "datasheet": "http://127.0.0.1/x.pdf"})["isError"]


# ------------------------------------------------------------------ MCP-9
@lite
def test_mcp9_visual_check_offline_downloads_nothing_and_calls_no_model(tmp_path):
    from ecadlib import visual_check as VC
    ws = Workspace(tmp_path / "w", offline=True)
    fp = ws.root / "footprints" / "X.kicad_mod"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("(footprint X)")
    seen = []
    with mock.patch.object(VC, "prove", lambda item, *a, **k: seen.append((item, k)) or
                           {"images": {}, "verdict": "skipped"}), mock.patch.object(VC, "write_proof", lambda pr, f: f):
        try:
            ws.visual_check("footprints/X.kicad_mod", datasheet="https://example.com/ds.pdf")
            raise AssertionError("an uncached datasheet was accepted offline")
        except ValueError as e:
            assert "offline" in str(e), e
        ws.visual_check("footprints/X.kicad_mod")
    assert seen and seen[-1][1]["render_only"] is True, seen


# ------------------------------------------------------------------ MCP-12
def test_mcp12_job_status_is_offered_with_any_tool_list():
    ws = Workspace.__new__(Workspace)
    ws.mcp_options = M.Options(tools=M.tool_names("librarian_run,held_parts"))
    assert {t["name"] for t in M._offered(ws)} == {"librarian_run", "held_parts", "job_status"}


# ------------------------------------------------------------------ MCP-13
def test_mcp13_bad_wait_max_chars_and_port_are_usage_errors():
    for argv, env in ((["--wait", "-5"], {}), (["--max-chars", "-1"], {}), (["--port", "99999"], {}),
                      ([], {"ECADLIB_MCP_WAIT": "abc"}), ([], {"ECADLIB_MCP_MAX_CHARS": "1e5"}),
                      (["--wait", "nan"], {})):
        err = io.StringIO()
        with mock.patch.dict("os.environ", env), contextlib.redirect_stderr(err), \
                mock.patch.object(M, "Workspace", side_effect=AssertionError("started")):
            try:
                M.main(argv)
                raise AssertionError(f"{argv} {env} accepted")
            except SystemExit as e:
                assert e.code == 2, (argv, env, e.code)
        assert "internal error" not in err.getvalue() and "Traceback" not in err.getvalue(), err.getvalue()


# ------------------------------------------------------------------ MCP-14
@lite
def test_mcp14_choose_part_without_a_person_is_no_approval(tmp_path):
    """choose_part over MCP passed no user, so change control took the server's git/login name as the approver of a
    geometry-major change to a released part."""
    import inspect
    from ecadlib import resolve as R
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options()
    seen = []
    with mock.patch.object(Workspace, "choose_part", lambda self, *a, **k: seen.append((a, k)) or {}):
        args = {"bom": "b.csv", "part": "C1", "kind": "footprint", "candidate_id": "spec:C_0805_2012Metric"}
        assert not _call(ws, "choose_part", args)["isError"]
        assert not _call(ws, "choose_part", dict(args, user="Ann Lee <ann@x.com>"))["isError"]
    assert seen[0][1]["approve"] is False and seen[0][0][5] is None, seen[0]
    assert seen[1][1]["approve"] is True and seen[1][0][5] == "Ann Lee <ann@x.com>", seen[1]
    assert "approve" in inspect.signature(R.choose).parameters


# ------------------------------------------------------------------ MCP-16
@lite
def test_mcp16_misspelt_arguments_are_an_error_naming_the_right_one(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options()
    r = _call(ws, "altium365_push", {"bom": "b.csv", "dryRun": True})
    assert r["isError"] and "dryRun (did you mean dry_run?)" in _txt(r), r
    r = _call(ws, "triage_bom", {"bom": "b.csv", "summary": True})
    assert r["isError"] and "did you mean summary_only?" in _txt(r), r
    r = _call(ws, "triage_bom", {"bom_path": "b.csv"})
    assert r["isError"] and "missing argument(s): bom" in _txt(r) and "bom_path" in _txt(r), r


# ------------------------------------------------------------------ MCP-17
@lite
def test_mcp17_overlong_path_is_a_plain_error_not_a_bug_report(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options()
    r = _call(ws, "held_parts", {"bom": "x" * 300 + ".csv"})
    assert r["isError"] and "report" not in _txt(r).lower() and "Something went wrong" not in _txt(r), r


# ------------------------------------------------------------------ MCP-18
@lite
def test_mcp18_jsonrpc_ids_responses_and_byte_order_mark(tmp_path):
    ws = Workspace(tmp_path / "w")
    for bad in ([1], {"a": 1}, True, 2.5, None):
        r = M.handle(ws, {"jsonrpc": "2.0", "id": bad, "method": "ping"})
        assert r and r["error"]["code"] == -32600 and r["id"] is None, (bad, r)
    assert M.handle(ws, {"jsonrpc": "2.0", "id": 18, "result": {}}) is None        # a client's response: no answer
    assert M.handle(ws, {"jsonrpc": "2.0", "id": 7, "method": "ping"})["result"] == {}
    out = io.BytesIO()
    M.serve(ws, io.BytesIO('﻿{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n'.encode("utf-8")), out)
    assert json.loads(out.getvalue())["result"] == {}, out.getvalue()


# ------------------------------------------------------------------ MCP-19
@lite
def test_mcp19_job_list_shows_elapsed_time_of_running_jobs(tmp_path):
    import time
    ws = Workspace(tmp_path / "w")
    jobs = M._jobs(ws)
    with jobs.lock:
        jobs.jobs["j1"] = {"id": "j1", "tool": "librarian_run", "state": "running", "started": time.time() - 30}
    r = json.loads(_txt(M._job_status(ws, {})))
    assert r["jobs"][0]["elapsed_s"] >= 29, r
