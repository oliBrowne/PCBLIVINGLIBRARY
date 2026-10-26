"""Second review of PR #196: MCP altium_menu command / win_dir and ledger file names (R-ST-1, R-ST-2, R-ST-4, R-ST-5)."""

from tests.fixtures_lite import lite


def _menu(tmp_path):
    from ecadlib.app import Workspace
    from ecadlib import mcp_server as M, altium_menu as AM
    ws = Workspace(str(tmp_path / "ws"), offline=True)
    ws.mcp_options = M.Options(wait=30, confine=True)
    ini = ws.root / "altium_menu" / AM.INI

    def call(**args):
        r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "altium_menu", "arguments": args}})
        return r["result"]["isError"], r["result"]["content"][0]["text"]
    return ws, ini, call


@lite
def test_an_unquoted_menu_path_cannot_hide_a_program_behind_a_cmd_delimiter(tmp_path):
    # cmd.exe splits an unquoted word at , ; = ( so 'C:\...\calc.exe,\python.exe -m ecadlib' ran calc.exe
    _, ini, call = _menu(tmp_path)
    for sep in ",;=()":
        for tail in ("\\python.exe -m ecadlib", "\\ecadlib.exe"):
            bad = f"C:\\Windows\\System32\\calc.exe{sep}{tail}"
            err, text = call(command=bad)
            assert err and "command" in text, (bad, text)
            assert not ini.exists(), bad
    for bad in ("C:\\x^\\python.exe -m ecadlib", "C:\\a b\\python.exe -m ecadlib", "ecadlib -m ecadlib",
                '"\\\\srv\\share\\python.exe" -m ecadlib', "\\\\srv\\share\\ecadlib.exe"):
        err, text = call(command=bad)
        assert err, bad


@lite
def test_menu_command_takes_the_usual_ways_to_run_ecadlib(tmp_path):
    from ecadlib import altium_menu as AM
    _, ini, call = _menu(tmp_path)
    for good in ("python3.11 -m ecadlib", "C:\\Users\\a\\AppData\\Local\\Microsoft\\WindowsApps\\python3.11.exe -m ecadlib",
                 '"C:\\Users\\R&D\\venv\\Scripts\\python.exe" -m ecadlib', '"C:/Python311/python.exe" -m ecadlib',
                 "C:\\Python311\\pythonw.exe -m ecadlib", "ecadlib.exe"):
        err, text = call(command=good)
        assert not err, (good, text)
        assert AM.load_command(ini) == good
        ini.unlink()


@lite
def test_a_program_uploaded_into_the_workspace_is_refused_on_every_os(tmp_path):
    # the check ran only on Windows; a Linux MCP server sharing its workspace with the Altium PC took it
    _, ini, call = _menu(tmp_path)
    err, text = call(command='"Z:\\ws\\uploads\\20261026-0400\\ecadlib.exe"')
    assert err and not ini.exists(), text


@lite
def test_mcp_win_dir_must_be_a_local_drive_folder(tmp_path):
    # the ready-made .DXPPrf runs a DelphiScript project from win_dir: a network share would run someone else's script
    ws, _, call = _menu(tmp_path)
    for bad in ("\\\\attacker.example\\share\\m", "//attacker.example/share/m", "C:\\x\\..\\..\\\\srv\\m", "menu"):
        err, text = call(win_dir=bad)
        assert err and "win_dir" in text, (bad, text)
    err, text = call(win_dir="D:\\ecadlib-menu")
    assert not err, text


def test_ledger_proposed_file_is_never_a_windows_device_name():
    from ecadlib import ledger as LG
    for part in ("AUX", "con", "NUL.x", "COM1", "lpt9"):
        stem = LG.safe_name(part).split(".")[0]
        assert stem.upper() not in ("AUX", "CON", "NUL", "COM1", "LPT9"), (part, LG.safe_name(part))
    assert LG.safe_name("AUX") != LG.safe_name("AUX_")
    assert LG.safe_name("R1") == "R1"


@lite
def test_an_upload_cannot_be_a_program(tmp_path):
    # R-ST-6: "Z:\ws\UPLOAD~1\...\ecadlib.exe" or "uploads.\" got past a string check on a non-Windows server; the
    # root fix is that no program is ever stored in the workspace's uploads
    from ecadlib.app import Workspace
    ws = Workspace(str(tmp_path / "ws"), offline=True)
    for name in ("ecadlib.exe", "python.EXE", "run.bat", "x.cmd", "a.ps1", "setup.msi", "evil.exe.", "evil.exe "):
        try:
            ws.save_upload(name, b"MZ")
            raise AssertionError(name)
        except ValueError as e:
            assert "not a BOM" in str(e), (name, e)
    assert not list((ws.root / "uploads").rglob("*.*")) if (ws.root / "uploads").exists() else True
    assert ws.save_upload("bom.csv", b"Ref,MPN\nR1,X\n").endswith("bom.csv")


def _aged_release(lib, schema):
    """A part released, then its stored fingerprint rewritten as an older ecadlib computed it: pads read back at
    89.9996 deg kept their w x h the other way round (fingerprint SCHEMA 4)."""
    import json
    from ecadlib import change_gate as CG, ledger as LG, release_check as RC
    from tests.test_release_check import make
    it = make(lib, name="PART1", fp_name="SOIC-8_X", keep_kmod=False)
    it["footprint"].pop("kicad_mod", None)
    RC.apply([it], lib)
    g = CG.Guard(lib, "librarian"); g.begin([it]); g.settle([it]); RC.apply([it], lib); g.baseline([it])
    lg = LG.Ledger(lib)
    lines = [json.loads(x) for x in lg.path.read_text(encoding="utf-8").splitlines()]
    for e in lines:
        if e.get("fingerprint"):
            fp = lg.get_json(e["fingerprint"])
            fp["schema"] = schema
            for p in fp["footprint"]["pads"]:
                p["w"], p["h"], p["rot"] = p["h"], p["w"], 90.0
            fp["hashes"] = {k: "0" * 64 for k in fp["hashes"]}       # what that canonical form hashed to
            e.update(fp["hashes"])
            e["fingerprint"] = lg.put_json(fp)
    lg.path.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    return it


@lite
def test_a_part_released_by_the_previous_fingerprint_schema_shows_no_change(tmp_path):
    # R-CL-1: the canonical form changed (89.9996 deg pads, stacked pad order, pin table instance) with SCHEMA kept at
    # 4, so every such released part came back held as "pad size changed" with nothing changed on disk
    from ecadlib import change_gate as CG, fingerprint as FP
    assert FP.SCHEMA >= 5
    lib = tmp_path / "lib"; lib.mkdir()
    it = _aged_release(lib, 4)
    g = CG.Guard(lib, "librarian"); g.begin([it])
    assert g.settle([it]) == []
