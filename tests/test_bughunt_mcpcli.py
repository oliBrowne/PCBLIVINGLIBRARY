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
    (ws.root / "librarian").mkdir(exist_ok=True)              # a workspace that has run the librarian before
    r = _call(ws, "held_parts", {"bom": "a" * 300})
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


# ------------------------------------------------------------------ CLI
def _quiet_run(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(*a, **k)
    return code, out.getvalue(), err.getvalue()


@lite
def test_cli3_rules_file_with_a_utf8_byte_order_mark_loads(tmp_path):
    from ecadlib import rules as RU
    f = tmp_path / "ecadlib.toml"
    f.write_bytes(b'\xef\xbb\xbf[naming]\r\ncase = "upper"\r\n')
    assert RU.load(f)["naming"]["case"] == "upper"
    assert RU.parse_text("﻿[naming]\n", "ecadlib.toml") == {"naming": {}}
    # an agent cannot slip a protected setting past save_rules behind a byte-order mark
    ws = Workspace(tmp_path / "w")
    ws.mcp_options = M.Options()
    r = _call(ws, "save_rules", {"toml": "﻿[agent]\ngithub_api = 'https://evil.example'\n", "overwrite": True})
    assert r["isError"] and "cannot change" in _txt(r), r


def test_cli4_a_plugin_source_hit_is_not_called_easyeda():
    import argparse
    from ecadlib import bom_triage as T, term as TM
    buf = io.StringIO()
    row = {"line": 1, "status": "available_easyeda", "src": "stockroom", "mpn": "RC0603FR-0710KL", "lcsc": "", "notes": ""}
    T.print_result([row], {"available_easyeda": 1}, argparse.Namespace(offline=False, out="out/t", bom="b.csv"),
                   TM.Term(buf, styled=False))
    assert "found online (stockroom)" in buf.getvalue() and "EasyEDA" not in buf.getvalue().split("\n")[0], buf.getvalue()
    buf = io.StringIO()
    T.print_result([dict(row, src="easyeda")], {"available_easyeda": 1}, argparse.Namespace(offline=False, out="out/t", bom="b.csv"),
                   TM.Term(buf, styled=False))
    assert "found online (EasyEDA)" in buf.getvalue()


@lite
def test_cli5_cli6_keys_mistakes_are_user_errors_and_a_damaged_file_is_kept(tmp_path, monkeypatch):
    from ecadlib import userconfig as UC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "cfg"))
    code, _, err = _quiet_run(UC.main, ["set", "FOO"], inp=lambda prompt: "")
    assert code == 2 and "the key is empty" in err and "internal error" not in err, err
    code, _, err = _quiet_run(UC.main, ["set", "bad name"], inp=lambda prompt: "x")
    assert code == 2 and "not an environment variable name" in err, err

    def eof(prompt):
        raise EOFError()
    code, _, err = _quiet_run(UC.main, ["set", "FOO"], inp=eof)
    assert code == 2 and "no input" in err, err
    UC.set_key("ANTHROPIC_API_KEY", "sk-one")
    UC.set_key("NEXAR_TOKEN", "tok-two")
    f = tmp_path / "cfg" / UC.KEYS
    f.write_text(f.read_text().rstrip().rstrip("}") + ",}\n")         # one-character damage: a trailing comma
    lines = []
    assert UC.main(["list"], out=lines.append) == 1 and "not valid JSON" in "\n".join(lines), lines
    code, _, err = _quiet_run(UC.set_key, "OTHER", "x")
    kept = list(f.parent.glob(UC.KEYS + ".damaged-*"))
    assert len(kept) == 1 and "ANTHROPIC_API_KEY" in kept[0].read_text() and "NEXAR_TOKEN" in kept[0].read_text(), kept
    assert "warning" in err and kept[0].name in err, err


@lite
def test_cli7_cli12_review_and_the_setup_page_workspace_are_defaults(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()                                       # Windows: the long form, not RUNNER~1
    from ecadlib import cliutil as CU, firstrun as FR
    assert "review" in CU.ENV_FLAGS["ECADLIB_WORKSPACE"][1]
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    (proj / "ecadlib.toml").write_text('[setup]\nworkspace = "myws"\n')
    argv, _ = FR.config_defaults("review", ["--yes"], environ={"ECADLIB_HOME": str(tmp_path / "none")}, start=str(proj))
    assert argv[:2] == ["--workspace", str(proj / "myws")], argv
    home, docs = tmp_path / "cfg", tmp_path / "Documents" / "ecadlib"
    docs.mkdir(parents=True)
    env = {"ECADLIB_HOME": str(home)}
    from ecadlib import userconfig as UC
    UC.save_settings({"workspace": str(docs)}, env)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    argv, notes = FR.config_defaults("status", [], environ=env, start=str(elsewhere))
    assert argv == ["--workspace", str(docs)] and "settings.json" in notes[0], (argv, notes)
    (elsewhere / "ecadlib_workspace").mkdir()                          # a local ./ecadlib_workspace still wins
    assert FR.config_defaults("status", [], environ=env, start=str(elsewhere))[0] == []


@lite
def test_cli8_a_workspaces_own_rules_beat_ecadlib_config(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()                                       # Windows: the long form, not RUNNER~1
    from ecadlib import rules as RU
    env_f, ws = tmp_path / "env.toml", tmp_path / "ws"
    (ws / "librarian" / "b").mkdir(parents=True)
    env_f.write_text("[naming]\n")
    (ws / "ecadlib.toml").write_text("[naming]\n")
    monkeypatch.setenv("ECADLIB_CONFIG", str(env_f))
    monkeypatch.chdir(tmp_path)
    assert RU.find(ws / "librarian" / "b") == ws / "ecadlib.toml"
    assert RU.find(".") == env_f.resolve()                             # elsewhere the variable still wins
    monkeypatch.chdir(ws)
    assert RU.find(".") == (ws / "ecadlib.toml").resolve()             # inside the workspace


def test_cli9_a_closed_pipe_is_a_silent_exit():
    from ecadlib import cli

    class Closed(io.StringIO):
        def write(self, s):
            raise BrokenPipeError(32, "Broken pipe")
    err = io.StringIO()
    with mock.patch("sys.stdout", Closed()), contextlib.redirect_stderr(err):
        code = cli.main(["--help"])
        assert cli.main(["completion", "bash"]) == code
    assert code in (141, 1) and err.getvalue() == "", err.getvalue()


@lite
def test_cli10_demo_with_a_missing_or_empty_bom_writes_nothing(tmp_path):
    from ecadlib import firstrun as FR
    (tmp_path / "empty.csv").write_text("  \n")
    for bom in (tmp_path / "nope.csv", tmp_path / "empty.csv"):
        code, _, err = _quiet_run(FR.demo_main, ["-o", str(tmp_path / "d"), "--bom", str(bom)])
        assert code == 2 and "hint" in err, err
        assert not (tmp_path / "d").exists()


@lite
def test_cli11_web3_a_damaged_plan_is_named_by_migrate_doctor_and_status(tmp_path):
    from ecadlib import doctor as DR, migrate as MG, ws_status as WS
    d = tmp_path / "librarian" / "b"
    d.mkdir(parents=True)
    (d / "plan.json").write_text("[]")
    MG.write_stamp(d, ["plan"])
    (d / "plan.json").write_text('[{"name": "R1",\x01')
    rows = [r for r in MG.scan(tmp_path) if r["kind"] == "plan"]
    assert rows and rows[0]["state"] == "error" and "not valid JSON" in rows[0]["error"], rows
    c = DR.check_formats(tmp_path)
    assert c["level"] == "FAIL" and "librarian/b/plan.json" in c["detail"] and "upgrade" not in c["hint"], c
    code, _, _ = _quiet_run(MG.main, [str(tmp_path), "--check"])
    assert code == 1
    s = WS.status(tmp_path)
    assert s["problems"] == ["librarian/b/plan.json: unreadable (JSONDecodeError)"], s["problems"]


@lite
def test_cli13_check_refuses_non_footprints_and_empty_folders(tmp_path):
    from ecadlib import footprint_rules as FRU
    (tmp_path / "plan.json").write_text("[]")
    (tmp_path / "Typo.pretty").mkdir()
    code, out, _ = _quiet_run(FRU.main, [str(tmp_path / "plan.json")])
    assert code == 1 and "not a KiCad footprint" in out and "pin1" not in out, out
    code, _, err = _quiet_run(FRU.main, [str(tmp_path / "Typo.pretty")])
    assert code == 2 and "no .kicad_mod files" in err, err


@lite
def test_cli14_lib_naming_a_file_is_refused(tmp_path):
    from ecadlib import doctor as DR, firstrun as FR
    f = tmp_path / "x.kicad_sym"
    f.write_text("(kicad_symbol_lib)")
    bad = FR.preflight("triage", ["bom.csv", "--lib", str(f)])
    assert bad and "not a file" in bad[0] and str(tmp_path) in bad[1], bad
    assert FR.preflight("triage", ["bom.csv", "--lib", str(tmp_path)]) is None
    c = DR.check_paths([str(f)])[0]
    assert c["level"] == "FAIL" and "not a library folder" in c["detail"], c


@lite
def test_cli15_init_does_not_learn_from_a_triage_report(tmp_path):
    from ecadlib import rules_infer as RI
    f = tmp_path / "triage_report.csv"
    f.write_text("line,designator,qty,mpn,manufacturer,lcsc,value,package,status,local_symbol,local_footprint,src,"
                 "src_symbol,src_footprint,src_3d,src_mpn,src_description,src_description_from,src_datasheet,notes\n"
                 "1,R1,1,RC0603FR-0710KL,Yageo,,10k,0603,in_library,,,,,,,,,,,\n")
    lib = RI.Library()
    RI.scan_table(lib, f)
    assert not lib.boms and any("triage report" in w for w in lib.warnings), (lib.boms, lib.warnings)


def test_cli16_doctor_quiet_has_no_empty_headings():
    from ecadlib import doctor as DR
    rep = {"checks": [DR._c("a", "WARN", "x", "fix x"), DR._c("b", "OK", "y"), DR._c("c", "FAIL", "z", "fix z")]}
    q = DR.render(rep, verbosity=-1)
    assert "Optional" not in q and "Fine:" not in q and "Fix these:" in q and "fix z" in q, q


def test_cli17_part_history_lists_changes_with_the_global_verbose_flag():
    import argparse
    from ecadlib import change_cli as CC, ledger as LG, term as TM
    lines = [{"event": "release", "revision": 2, "at": "t", "writer": "resolve", "by": "Ann",
              "classes": {"electrical": 1}, "changes": [{"class": "electrical", "text": "pin 1 was G now S"}]}]

    class Lg:
        def __init__(self, *a):
            pass

        def history(self, part):
            return lines

        def trouble(self, part):                          # a whole history (R-G2-3)
            return None
    old = TM.VERBOSITY
    try:
        TM.VERBOSITY = 1
        with mock.patch.object(CC, "_load", lambda p: (Path("lib/plan.json"), [])), mock.patch.object(LG, "Ledger", Lg):
            code, out, _ = _quiet_run(CC.cmd_history, argparse.Namespace(plan="p", part="AO3401A", json=False, verbose=False))
    finally:
        TM.VERBOSITY = old
    assert code == 0 and "pin 1 was G now S" in out, out


def test_cli19_a_failing_exporter_plugin_is_named():
    from ecadlib import plugins as PL

    def boom(items, out_dir, rules):
        raise RuntimeError("exporter exploded")
    with mock.patch.object(PL, "exporters", lambda eps=None: ({"exp-raise": (boom, "")}, [])), \
            mock.patch("ecadlib.rules.resolve", lambda r, start=".": {}):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            try:
                PL.run_exporter("exp-raise", [], d)
                raise AssertionError("no error")
            except PL.PluginError as e:
                assert "exp-raise" in str(e) and "RuntimeError: exporter exploded" in str(e), e
                assert isinstance(e, ValueError)
