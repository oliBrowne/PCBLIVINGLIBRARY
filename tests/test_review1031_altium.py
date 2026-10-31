"""Review 2026-10-31 (altium): fixes from a full review of the Altium code (menu scripts, DbLib sync, footprint and
symbol files, Altium 365 push)."""
import json
from pathlib import Path

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- the CLI push goes only to the configured workspace
def _cli_push(tmp_path, monkeypatch, workspace, toml=None, dry_run=False):
    """Run `ecadlib altium365 push` on a one-part plan with push() mocked; returns (exit, workspaces push was given)."""
    from ecadlib import altium365 as A
    if toml is not None:
        (tmp_path / "ecadlib.toml").write_text(toml, encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"name": "X", "folder": "ICs", "description": "", "comment": "", "parameters": [],
                                 "symbol": {"ref": "X", "file": None}, "footprint": {"ref": "X", "file": None},
                                 "status": "needs_altium_models"}]), encoding="utf-8")
    seen = []
    monkeypatch.setattr(A, "push", lambda items, client, ws, **k: seen.append(ws) or [])
    monkeypatch.setattr(A, "Nexar", lambda token: object())
    monkeypatch.setattr(A, "record_push", lambda *a, **k: None)
    monkeypatch.setenv("NEXAR_TOKEN", "t")
    args = ["push", str(plan), "-o", str(tmp_path / "r.json")] + (["--workspace", workspace] if workspace else [])
    try:
        A.main(args + (["--dry-run"] if dry_run else []))
        code = 0
    except SystemExit as e:
        code = e.code
    return code, seen


MINE = '[altium365]\nworkspace = "https://mine.365.altium.com/"\n'


@lite
def test_cli_push_to_another_workspace_is_refused(tmp_path, monkeypatch):
    """`altium365 push --workspace URL` released into any URL it was given: only the web UI and the menu checked it
    against [altium365] workspace. A pasted URL of another team's workspace now pushes nothing."""
    code, seen = _cli_push(tmp_path, monkeypatch, "https://other-team.365.altium.com", MINE)
    assert seen == [] and code and "not the Altium 365 workspace" in str(code), (code, seen)


@lite
def test_cli_push_with_no_configured_workspace_is_refused(tmp_path, monkeypatch):
    code, seen = _cli_push(tmp_path, monkeypatch, "https://any.365.altium.com", None)
    assert seen == [] and code and "no Altium 365 workspace is configured" in str(code), (code, seen)


@lite
def test_cli_push_to_the_configured_workspace_in_another_spelling_goes_ahead(tmp_path, monkeypatch):
    code, seen = _cli_push(tmp_path, monkeypatch, "https://MINE.365.altium.com", MINE)
    assert seen == ["https://MINE.365.altium.com"], (code, seen)


@lite
def test_cli_dry_run_may_name_any_workspace(tmp_path, monkeypatch):
    code, seen = _cli_push(tmp_path, monkeypatch, "https://other-team.365.altium.com", MINE, dry_run=True)
    assert seen == ["https://other-team.365.altium.com"], (code, seen)


@lite
def test_lifecycle_apply_to_another_workspace_is_refused(tmp_path, monkeypatch):
    """`python -m ecadlib.lifecycle --update-a365 --apply` rewrote the Lifecycle parameter in any workspace URL."""
    from ecadlib import lifecycle as LC
    (tmp_path / "ecadlib.toml").write_text(MINE, encoding="utf-8")
    bom = tmp_path / "bom.csv"
    bom.write_text("MPN,Manufacturer\nNE555P,TI\n", encoding="utf-8")
    calls = []
    monkeypatch.setenv("NEXAR_TOKEN", "t")
    monkeypatch.setattr(LC, "DesignClient", lambda token: calls.append("client") or object())
    for flag in ("--workspace", "--a365"):
        code = LC.main([str(bom), "--update-a365", "--apply", flag, "https://other-team.365.altium.com",
                        "-o", str(tmp_path / "rep"), "--offline"])
        assert code == 2 and calls == [], (flag, code, calls)
        assert not (tmp_path / "rep_a365.json").exists()


# ---------------------------------------------------------------- request.ini keeps non-ANSI text
def _start_job_text():
    from ecadlib import altium_menu as AM
    pas = AM.pas_text()
    return pas[pas.index("Function StartJob"):pas.index("End;", pas.index("Function StartJob") + 1200)]


def test_start_job_copies_the_utf16_template_before_the_first_ask():
    """TIniFile.WriteString on a new request.ini made an ANSI file: a manufacturer in kanji, or a Release anyway reason
    with any non-ANSI letter, reached ecadlib as '?'. StartJob now starts the file from request_template.ini."""
    body = _start_job_text()
    copy, ask = body.index("request_template.ini"), body.index("Ask(Result")
    assert copy < ask and "LoadFromFile" in body[:ask] and r"SaveToFile(Result + '\request.ini')" in body[:ask], body


@lite
def test_menu_build_writes_the_request_template_with_a_bom(tmp_path):
    from ecadlib import altium_menu as AM
    rep = AM.build(tmp_path / "m", workspace=r"C:\ws", library=r"C:\lib", command="python -m ecadlib")
    data = (tmp_path / "m" / AM.REQUEST_TEMPLATE).read_bytes()
    assert data.startswith(b"\xff\xfe") and data[2:].decode("utf-16-le") == "[request]\r\n", data
    assert AM.REQUEST_TEMPLATE in rep["written"]
    assert AM.REQUEST_TEMPLATE not in AM.build(tmp_path / "m", command="python -m ecadlib")["written"]


@lite
def test_profile_api_writes_unicode_into_the_template_and_read_ini_reads_it(tmp_path):
    """What TIniFile.WriteString does on Windows (WritePrivateProfileStringW) to a copy of the template."""
    import os
    if os.name != "nt":
        return
    import ctypes
    from ecadlib import altium_menu as AM
    p = tmp_path / "request.ini"
    p.write_bytes(AM.REQUEST_TEMPLATE_BYTES)
    for k, v in (("action", "add-part"), ("manufacturer", "Müller 村田"), ("reason", "geprüft – ok")):
        assert ctypes.windll.kernel32.WritePrivateProfileStringW("request", k, v, str(p))
    assert p.read_bytes().startswith(b"\xff\xfe")
    r = AM.read_ini(p)["request"]
    assert r["manufacturer"] == "Müller 村田" and r["reason"] == "geprüft – ok", dict(r)


# ---------------------------------------------------------------- DbLib columns as Excel's ACE provider names them
def _dblib_maps(path):
    """[(FieldNameOnly, ParameterName)] of a .DbLib's field maps."""
    out = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.startswith("Options=FieldName="):
            o = dict(kv.split("=", 1) for kv in ln[len("Options="):].split("|") if "=" in kv)
            out.append((o["FieldNameOnly"], o["ParameterName"]))
    return out


def _sync_odd_names(d, first_params, second_params=None):
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _item, _models, _plan
    _models(d)
    items = [_item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", **first_params)]
    if second_params is not None:
        items.append(_item("RES-0603-1k", "Resistors", "Resistor", "R_0603_1608Metric", **second_params))
    return S.sync(_plan(d, items), d / "lib")


def test_dblib_field_maps_name_the_columns_ace_exposes():
    """A parameter 'Temp. Coeff' or 'Tol [%]' was mapped as written, but Excel's ACE provider calls those workbook
    columns 'Temp# Coeff' and 'Tol (%)': the field never reached Altium. The Altium parameter keeps its own name."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        out = _sync_odd_names(d, {"Temp. Coeff": "100ppm", "Tol [%]": "1", "Value": "10k"})
        assert all(r["action"] == "added" for r in out["results"]), out
        maps = dict(_dblib_maps(d / "lib" / "ecadlib.DbLib"))
        assert maps.get("Temp# Coeff") == "Temp. Coeff" and maps.get("Tol (%)") == "Tol [%]", maps
        assert "Temp. Coeff" not in maps and "Tol [%]" not in maps, maps


def test_dblib_update_repairs_a_map_an_older_version_wrote():
    from ecadlib import altium_dblib_sync as S
    from ecadlib.altium_export import fieldmap_section
    old = S.dblib_update("", {"Resistors": ["Part Number", "Temp. Coeff"]}, {"conn_str": "x"})
    broken = old.replace("Temp# Coeff", "Temp. Coeff")       # what the older version wrote
    assert "FieldNameOnly=Temp. Coeff" in broken
    again = S.dblib_update(broken, {"Resistors": ["Part Number", "Temp. Coeff"]}, {"conn_str": "x"})
    assert "FieldNameOnly=Temp. Coeff" not in again and again.count("FieldNameOnly=Temp# Coeff") == 1, again
    assert fieldmap_section(1, "T$", "A.b")[1] == fieldmap_section(1, "T$", "A.b", excel=False)[1]   # sqlite: as is


def test_dblib_columns_differing_only_in_case_are_one_column():
    """Parameters 'value' and 'part number' became columns next to 'Value' and 'Part Number' (ACE: 'value1'), and a
    second part's 'tol' a column next to the first one's 'Tol'."""
    import tempfile, openpyxl
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        out = _sync_odd_names(d, {"value": "10k", "part number": "EVIL", "Tol": "1%"}, {"Value": "1k", "tol": "5%"})
        assert all(r["action"] == "added" for r in out["results"]), out
        ws = openpyxl.load_workbook(d / "lib" / "ecadlib.xlsx")["Resistors"]
        rows = list(ws.iter_rows(values_only=True))
        hdr = [h for h in rows[0] if h]
        assert len({h.lower() for h in hdr}) == len(hdr), hdr
        got = {r[hdr.index("Part Number")]: (r[hdr.index("Value")], r[hdr.index("Tol")]) for r in rows[1:]}
        assert got == {"RES-0603-10k": ("10k", "1%"), "RES-0603-1k": ("1k", "5%")}, got
        maps = [f.lower() for f, _ in _dblib_maps(d / "lib" / "ecadlib.DbLib")]
        assert len(maps) == len(set(maps)), maps


def test_dblib_columns_read_through_excels_ace_provider():
    """On a Windows PC with the ACE provider (Altium's Excel database library uses it), every field the DbLib maps
    is a column ACE can select from the synced workbook."""
    import os, subprocess, tempfile
    if os.name != "nt":
        return
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _sync_odd_names(d, {"Temp. Coeff": "100ppm", "Tol [%]": "1", "a!b": "x", "value": "10k"})
        fields = sorted({f for f, _ in _dblib_maps(d / "lib" / "ecadlib.DbLib")})
        sel = ",".join(f"[{f}]" for f in fields)
        ps = (f"$c=New-Object System.Data.OleDb.OleDbConnection 'Provider=Microsoft.ACE.OLEDB.12.0;Data Source="
              f"{d / 'lib' / 'ecadlib.xlsx'};Extended Properties=\"Excel 12.0 Xml;HDR=YES;IMEX=1\"';"
              "try{$c.Open()}catch{'NOACE';exit 0};$m=$c.CreateCommand();"
              f"$m.CommandText='SELECT {sel} FROM [Resistors$]';$r=$m.ExecuteReader();$null=$r.Read();'OK '+$r[0]")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
        if "NOACE" in r.stdout:
            return
        assert r.stdout.startswith("OK "), (r.stdout, r.stderr, fields)


# ---------------------------------------------------------------- DbLib sync: workbook open in Excel, damaged state
def test_dblib_sync_with_the_workbook_open_in_excel_writes_nothing():
    """With the workbook open in Excel the sync wrote the new models, then crashed (PermissionError) on the workbook,
    leaving a .tmp and a state that didn't know the models. It now refuses before writing anything."""
    import ctypes, os, tempfile
    if os.name != "nt":
        return
    from ecadlib import altium_dblib_sync as S
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _sync_odd_names(d, {"Value": "10k"})
        lib = d / "lib"
        before = {p.name: p.stat().st_mtime_ns for p in lib.rglob("*") if p.is_file()}
        from tests.test_dblib_sync import _item, _plan
        items = [_item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", Value="22k"),
                 _item("CAP-0402-100nF", "Capacitors", "Capacitor", "C_0402_1005Metric", Value="100nF")]
        k = ctypes.windll.kernel32
        k.CreateFileW.restype = ctypes.c_void_p
        h = k.CreateFileW(str(lib / "ecadlib.xlsx"), 0x80000000, 0, None, 3, 0x80, None)   # read, no sharing: Excel
        try:
            try:
                S.sync(_plan(d, items), lib)
                raise AssertionError("sync with the workbook locked went ahead")
            except ValueError as e:
                assert "is open in Excel" in str(e), e
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
        after = {p.name: p.stat().st_mtime_ns for p in lib.rglob("*") if p.is_file()}
        assert after == before, (before, after)


def test_dblib_sync_names_a_damaged_state_file():
    import tempfile
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _item, _plan
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _sync_odd_names(d, {"Value": "10k"})
        (d / "lib" / S.STATE).write_text('{"rows": ["RES', encoding="utf-8")       # cut off mid-write
        try:
            S.sync(_plan(d, [_item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric")]), d / "lib")
            raise AssertionError("a damaged state file was not reported")
        except ValueError as e:
            assert S.STATE in str(e) and "damaged" in str(e), e


def test_dblib_sync_writes_its_text_files_atomically():
    """The .DbLib, .csv and state file were written in place: a sync killed mid-write left a cut-off DbLib."""
    import inspect
    from ecadlib import altium_dblib_sync as S
    src = inspect.getsource(S._sync)
    i = src.index("def put(")
    put = src[i:src.index("written.append(rel)", i)]
    assert "write_bytes_atomic" in put and ".write_bytes(" not in put, put


# ---------------------------------------------------------------- bottom-side KiCad footprints
BOTTOM_MOD = """(footprint "BOT" (layer "B.Cu")
  (fp_text reference "REF**" (at 0 -2) (layer "B.SilkS") (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))
  (fp_text value "BOT" (at 0 2) (layer "B.Fab") (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))
  (fp_line (start -1 -1) (end 1 -1) (stroke (width 0.12) (type solid)) (layer "B.SilkS"))
  (fp_line (start -1 1) (end 1 1) (stroke (width 0.1) (type solid)) (layer "B.Fab"))
  (pad "1" smd rect (at -0.5 0) (size 0.6 0.6) (layers "B.Cu" "B.Paste" "B.Mask"))
  (pad "2" smd rect (at 0.5 0) (size 0.6 0.6) (layers "B.Cu" "B.Paste" "B.Mask"))
)"""


def test_bottom_side_reference_and_silk_land_on_bottom_overlay():
    """A reference on B.SilkS went to Top Overlay and every B.* line vanished without a note, so silk and reference
    ended up on opposite sides (and the import note said Top Overlay)."""
    from ecadlib import altium_lib as AL
    notes = []
    fp = AL.footprint_from_kicad(BOTTOM_MOD, notes=notes)
    des = [t for t in fp["texts"] if t[0] == ".Designator"]
    assert des and des[0][6] == AL.BOTTOM_OVERLAY, fp["texts"]
    assert [tr[5] for tr in fp["tracks"]] == [AL.BOTTOM_OVERLAY], fp["tracks"]
    assert any("B.Fab x1" in n for n in notes), notes
    assert not [t for t in fp["texts"] if t[6] == AL.TOP_OVERLAY], fp["texts"]


# ---------------------------------------------------------------- a padded footprint name keeps its link
@lite
def test_footprint_name_with_stray_spaces_links_from_the_symbol(tmp_path):
    """The SchLib footprint link was written cleaned ('SOT-23') but the PcbLib entry as given ('SOT-23 '): the IntLib
    compile and ecadlib's own compare could not find the footprint, and relink_schlib changed nothing."""
    from ecadlib import altium_lib as AL, altium_native as AN
    for k, name in enumerate(("SOT-23 ", " SOT-23", "SOT  23")):
        d = tmp_path / str(k)
        d.mkdir()
        fp = {"name": name, "description": "", "pads": [{"designator": "1", "x": 0, "y": 0, "w": 1, "h": 1,
                                                          "shape": "rect"}], "tracks": [], "fills": [], "regions": []}
        AL.write_pcblib(d / "a.PcbLib", [fp])
        sym = AL.ic_symbol("U", [{"num": "1", "name": "A", "type": "input", "side": "left"}], footprint=name,
                           layout=False)
        AL.write_schlib(d / "a.SchLib", [sym])
        pcb = [c["name"] for c in AL.read_pcblib(d / "a.PcbLib")]
        _, comps = AL.read_schlib(d / "a.SchLib")
        links = [r.get("MODELNAME") for r in comps[0]["records"] if r.get("RECORD") == "45"]
        assert pcb == links == [AL.clean(name)], (name, pcb, links)
        assert AN.compare_one(d / "a.SchLib", d / "a.SchLib.ecadbuild") == [], name
        assert AL.relink_schlib(d / "a.SchLib", name, "X"), name


# ---------------------------------------------------------------- package names that can't be Windows file names
@lite
def test_package_name_that_is_not_a_windows_file_name_writes_nothing(tmp_path):
    """--name 'my:lib' wrote the package into an NTFS alternate data stream of a file 'my' (no .LibPkg to be seen) and
    passed its own check; 'lib?' failed after the recipes and libraries were copied."""
    from ecadlib import altium_pkg as P
    out = tmp_path / "out"
    for name in ("a:b", "a?b", "a/b", "a<b", "a|b", "lib.", " ", "a\x01b"):
        try:
            P.build(tmp_path / "src", out, name=name)
            raise AssertionError(f"{name!r} was accepted")
        except ValueError as e:
            assert "Windows file name" in str(e), (name, e)
        assert not out.exists() or not any(out.iterdir()), name
    P.check_name("ecadlib")
    P.check_name("Bibliothèque v2")


# ---------------------------------------------------------------- run-job with no settings file
@lite
def test_run_job_with_no_settings_file_says_so_instead_of_reporting_a_bug(tmp_path):
    """A request.ini with no config= in a folder with no ecadlib_menu.ini two levels up: load_config(None) raised a
    TypeError, which the menu showed as a bug with `ecadlib report-bug`."""
    from ecadlib import altium_menu as AM
    job = tmp_path / "jobs" / "1"
    job.mkdir(parents=True)
    (job / "request.ini").write_bytes(AM.REQUEST_TEMPLATE_BYTES + "action=add-part\r\n".encode("utf-16-le"))
    res = AM.run_job(job)
    assert res["status"] == "error" and AM.INI in res["message"], res
    assert "report-bug" not in " ".join(str(v) for v in res.values()), res


# ---------------------------------------------------------------- stop waiting: the job still gets installed
def test_stopping_the_wait_hands_the_job_to_keep_working():
    """Answering No to "Keep waiting?" left a job ecadlib went on to finish that Altium never installed: no
    background.txt, not on waiting.txt. Both waits now hand it over as Keep working does."""
    from ecadlib import altium_forms as AF, altium_menu as AM
    for text in (AM.pas_text(), AF.dialog_pas_text()):
        i = text.index("Stopped waiting.")
        before = text[text.rindex("Keep waiting?", 0, i):i]
        assert "MarkJob(Job, 'background.txt')" in before and "AddWaiting(Job)" in before, before
    assert all(ord(c) < 128 for c in AM.pas_text() + AF.dialog_pas_text())     # Altium reads the units as ANSI
