"""The "ecadlib" menu for Altium (ecadlib.altium_menu): the script project's files are well-formed, the .pas uses only
documented calls, the command lines survive spaces and Windows separators, and every action the menu runs works end to
end offline through the same files the script exchanges (request.ini -> run-job -> result.ini).
python3 -m tests.test_altium_menu.   Nothing here runs Altium, cmd.exe or wscript (none exist on this machine)."""
import configparser, contextlib, csv, io, json, os, re, socket, subprocess, sys, tempfile, zipfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_forms as AF, altium_menu as AM, altium_native as AN

ROOT = Path(__file__).parent.parent
RULES = ('[sources]\norder = ["standard"]\n\n[parameters]\n'
         'required = ["Manufacturer", "Manufacturer Part Number", "Description"]\n')
CAP = {"mpn": "CL05B104KO5NNNC", "manufacturer": "Samsung Electro-Mechanics", "value": "100nF",
       "footprint": "C_0402_1005Metric", "description": "Capacitor 100nF 16V X7R 0402", "designator": "C1"}


def _tmp(name="ecadlib menu "):
    return Path(tempfile.mkdtemp(prefix=name))                  # a space in every path


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
        r = fn(*a, **k)
    return r, out.getvalue()


def _setup(d=None, **ini):
    """A built menu folder + a workspace with standard-parts-only rules. Returns (menu dir, config dict)."""
    d = d or _tmp()
    ws = d / "my workspace"
    ws.mkdir(parents=True)
    (ws / "ecadlib.toml").write_text(RULES + ini.pop("extra_rules", ""), encoding="utf-8")
    AM.build(d / "menu (Altium)", str(ws), None, offline=True, **ini)
    return d / "menu (Altium)", AM.load_config(d / "menu (Altium)" / AM.INI)


def _job(menu, req, encoding="cp1252"):
    """A job folder as the script makes it: request.ini written the way TIniFile (WritePrivateProfileString) writes a
    new file, in the ANSI code page, CRLF, no BOM."""
    job = menu / "jobs" / f"20261016-120000-{len(list((menu / 'jobs').glob('*'))) if (menu / 'jobs').exists() else 0}"
    job.mkdir(parents=True)
    lines = ["[request]", f"config={menu / AM.INI}"] + [f"{k}={v}" for k, v in req.items()]
    (job / "request.ini").write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return job


def _result(job):
    data = (Path(job) / "result.ini").read_bytes()
    assert data[:2] == b"\xff\xfe", "result.ini must be UTF-16 LE with a BOM"
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read_string(data.decode("utf-16"))
    return dict(cp["result"])


# ---------------------------------------------------------------- Windows command-line rules (for the quoting tests)
def argv_w(cmdline):
    """CommandLineToArgvW / MSVC runtime: 2n backslashes + quote -> n backslashes, toggles quoting; 2n+1 -> n + a quote."""
    args, cur, inq, i, n, have = [], "", False, 0, len(cmdline), False
    while i < n:
        c = cmdline[i]
        if c == "\\":
            j = i
            while j < n and cmdline[j] == "\\":
                j += 1
            k = j - i
            if j < n and cmdline[j] == '"':
                cur += "\\" * (k // 2)
                if k % 2:
                    cur += '"'
                else:
                    inq = not inq
                i, have = j + 1, True
            else:
                cur += "\\" * k
                i, have = j, True
        elif c == '"':
            if inq and i + 1 < n and cmdline[i + 1] == '"':       # "" inside quotes: a literal quote (MSVC 2008+)
                cur += '"'
                i += 2
            else:
                inq = not inq
                i += 1
            have = True
        elif c in " \t" and not inq:
            if have:
                args.append(cur)
            cur, have = "", False
            i += 1
        else:
            cur += c
            i, have = i + 1, True
    if have:
        args.append(cur)
    return args


def cmd_s_c(cmdline):
    """What `cmd.exe /s /c <rest>` executes: the rest with its first and last quote removed (cmd /? rule with /s)."""
    m = re.match(r'(?i)^cmd\.exe /s /c (.*)$', cmdline)
    assert m, cmdline
    rest = m.group(1)
    assert rest.startswith('"') and rest.endswith('"'), rest
    return rest[1:-1]


# ---------------------------------------------------------------- the files
def test_build_names_every_procedure():
    """`altium-menu build`, its README and the docs name every procedure the project offers (PROCS + BUILD_PROCS),
    and each one exists in the unit the table names (ecadlib_build.pas came with the native build)."""
    assert AM.procedures() == ["AddPartFromMPN", "CheckProjectBOM", "OpenReviewQueue", "BuildLibrariesInAltium", "BuildLibraries(Folder)"]
    units = {AM.UNIT: AM.pas_text(), AN.UNIT: AN.pas_text()}
    for p, unit, _ in [(p, AM.UNIT, d) for p, _, d in AM.PROCS] + AM.BUILD_PROCS:
        name, end = p.split("(")[0], r"\(" if "(" in p else ";"
        assert re.search(rf"(?m)^Procedure {name}{end}", units[unit]), (p, unit)
    d = _tmp()
    rc, out = _quiet(AM.main, ["build", "--out", str(d / "m")])
    readme = (d / "m" / AM.README).read_text()
    for p in AM.procedures():
        assert p in out and p in readme and p in AM.__doc__ and p in AM.pas_text(), (p, out)
    assert "now lists AddPartFromMPN, CheckProjectBOM, OpenReviewQueue, BuildLibrariesInAltium:" in readme, readme


def test_build_writes_well_formed_files():
    d = _tmp()
    out = d / "menu dir"
    rep = AM.build(out, "D:\\ecadlib ws", None, win_dir="D:\\Altium Scripts\\ecadlib (menu)")
    assert rep["problems"] == [] and rep["menu_file"] == AM.PREFS, rep
    units = (AF.DIALOG_UNIT, AF.DIALOG_FORM, AN.UNIT)
    assert set(rep["written"]) == {AM.PROJECT, AM.UNIT, *units, AM.INI, AM.RUN_CMD, AM.RUN_VBS, AM.README, AM.PREFS}
    for f in (AM.PROJECT, AM.UNIT, *units, AM.INI, AM.RUN_CMD, AM.RUN_VBS, AM.README):
        raw = (out / f).read_bytes()
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{f}: every line ends with CRLF (Windows tools)"
        raw.decode("ascii")                                       # plain ASCII: any code page reads it
        assert not re.search(rb"(?i)sk-ant|api[_-]?key\s*=|NEXAR_TOKEN\s*=|password\s*=", raw), f"{f}: no secrets"
    prj = configparser.ConfigParser()
    prj.read_string((out / AM.PROJECT).read_text())
    assert prj.sections() == ["Design", "Document1", "Document2", "Document3"]
    assert [prj[f"Document{i}"]["DocumentPath"] for i in (1, 2, 3)] == [AM.UNIT, AF.DIALOG_UNIT, AN.UNIT]
    assert (out / AN.UNIT).read_bytes() == AN.pas_text().encode(), "the menu calls BuildLibraries (ecadlib_build.pas)"
    ini = configparser.ConfigParser(interpolation=None)
    ini.read_string((out / AM.INI).read_text())
    assert {k for k in ini["ecadlib"]} == {"workspace", "library", "name", "command", "hidden", "timeout_s", "port", "offline",
                                    "dialogs", "native_build"}
    assert ini["ecadlib"]["workspace"] == "D:\\ecadlib ws" and ini["ecadlib"]["hidden"] == "1"
    assert not any(";" in v for v in ini["ecadlib"].values()), "TIniFile does not strip inline comments"
    # again: nothing rewritten; the user's settings survive
    (out / AM.INI).write_bytes((out / AM.INI).read_bytes().replace(b"port=8765", b"port=9001"))
    again = AM.build(out, "elsewhere", None, win_dir="D:\\Altium Scripts\\ecadlib (menu)")
    assert again["written"] == [] and again["kept"] == [AM.INI] and b"port=9001" in (out / AM.INI).read_bytes()
    assert AM.load_config(out / AM.INI)["port"] == 9001
    # no Windows folder known (not on Windows, no --win-dir): no menu file, and the README says how to get one
    if os.name != "nt":
        r2 = AM.build(d / "plain", None, None)
        assert r2["menu_file"] is None and not (d / "plain" / AM.PREFS).exists()
        assert "--win-dir" in (d / "plain" / AM.README).read_text()
    for bad in ("relative\\dir", "C:\\O'Brien\\menu", "C:\\a|b"):
        try:
            AM.build(d / "x", None, None, win_dir=bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert "--win-dir" in str(e)


def test_menu_file_follows_altiums_format():
    """The .DXPPrf: a zip of DXP.RCS + IncludedFiles.ini as Altium-Designer-addons' Menu_SVN.DXPPrf (Altium-saved),
    whose DXP.RCS grammar is: PL <id> Command='..' Params='..' Caption='..' ... End; Insertion <id> TargetID RefID0 /
    Tree <id> Caption Popup / Link <id> PLID / End blocks; Tree <X>Custom Caption='[Custom]' TopLevel='True' blocks."""
    win = "D:\\Altium Scripts\\ecadlib menu"
    data = AM.dxpprf_bytes(win)
    assert data == AM.dxpprf_bytes(win), "same bytes every time (fixed zip timestamps)"
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.namelist() == ["DXP.RCS", "IncludedFiles.ini"]
    # IncludedFiles.ini byte for byte as in Menu_SVN.DXPPrf
    assert z.read("IncludedFiles.ini") == b"[WorkspaceManager:Customizations]\r\nImportMode=0\r\nFileCount=1\r\nFileName0=DXP.RCS\r\n"
    rcs = z.read("DXP.RCS").decode("ascii")
    assert rcs.endswith("\r\n") and "\n" not in rcs.replace("\r\n", "")
    pl, links, depth, targets = {}, [], 0, []
    for ln in rcs.split("\r\n"):
        if not ln.strip():
            assert depth == 0
            continue
        m = re.match(r"^PL (\w+) Command='([^']*)' Params='([^']*)' Caption='([^']*)' Description='([^']*)' DefaultChecked=0  End$", ln)
        if m:
            pl[m.group(1)] = m.groups()
            continue
        m = re.match(r"^Insertion (\w+) TargetID='(\w+)' RefID0='(\w+)' InsertType='Before' $", ln)   # as AdvPcb.rcs
        if m:
            targets.append((m.group(2), m.group(3)))
            depth += 1
            continue
        if re.match(r"^ *Tree \w+ Caption='[^']*'( Popup='N'| TopLevel='True') $", ln):
            depth += 1
            continue
        m = re.match(r"^ *Link \w+ PLID='(\w+)' +End$", ln)
        if m:
            links.append(m.group(1))
            continue
        assert re.match(r"^ *End$", ln), f"not in the RCS grammar: {ln!r}"
        depth -= 1
        assert depth >= 0
    assert depth == 0
    # the menus and the item they go before, as Menu_SVN's DXP.RCS names them
    assert targets == [("MNSchematicMenu", "MNDesignMenu_Window1_3"), ("MNPCBMenu", "MNDesignMenu_Window1_1"),
                       ("MNSchLibMenu", "MNDesignMenu_Window1_4"), ("MNPCBLibMenu", "MNDesignMenu_Window1_2")]
    assert set(links) == set(pl) and len(links) == len(AM.PROCS) * 8
    procs = {p for p, _, _ in AM.PROCS}
    for pid, (_, command, params, caption, _) in pl.items():
        assert command == "ScriptingSystem:RunScript"            # TR0124: ProjectName | ProcName = file>procedure
        kv = dict(x.split("=", 1) for x in params.split("|"))
        assert kv["ProjectName"] == f"{win}\\{AM.PROJECT}"
        f, proc = kv["ProcName"].split(">")
        assert f == f"{win}\\{AM.UNIT}" and proc in procs and pid == f"ecadlib_{proc}"


def test_menu_goes_straight_into_altiums_menu_file():
    """install-menu merges the ecadlib menu into Altium's own DXP.RCS (Altium closed), after a backup: other
    customisations stay ([Custom] trees untouched), a second run changes nothing, and remove
    gives back the file as it was. The user file is the one seen on a real AD 26.10 (one line)."""
    d = _tmp()
    alt = d / "Roaming" / "Altium" / "Altium Designer {EDA20636-A941-48A8-9173-5E8D6A734E05}"
    alt.mkdir(parents=True)
    theirs = ("Tree PCBCustom Caption='[Custom]' TopLevel='True'  End\r\n"
              "PL Mine_Run Command='ScriptingSystem:RunScript' Params='ProjectName=C:\\x.PrjScr' Caption='Mine' End\r\n"
              "Insertion Mine_Menu TargetID='MNPCBMenu' RefID0='MNDesignMenu_Window1_1' \r\n"
              "    Tree Mine_Tree Caption='Mine' Popup='N' \r\n        Link Mine_L PLID='Mine_Run'  End\r\n    End\r\nEnd\r\n")
    (alt / "DXP.RCS").write_bytes(theirs.encode("latin-1"))
    assert AM.altium_user_folders(d / "Roaming") == [alt]
    try:
        AM.install_menu_bar("D:\\ecadlib menu", running=lambda: True)
        raise AssertionError("must refuse while Altium runs")
    except ValueError as e:
        assert "close it first" in str(e)
    win = "D:\\ecadlib menu" if os.name != "nt" else str(d / "menu")
    with mock.patch.object(AM, "check_win_dir", lambda w: "D:\\ecadlib menu"):
        # an Altium still closing writes its old file back: reported, not taken for done
        try:
            AM.install_menu_bar(win, [alt], running=lambda: False,
                                sleep=lambda s: (alt / "DXP.RCS").write_bytes(theirs.encode("latin-1")))
            raise AssertionError("must notice the overwrite")
        except ValueError as e:
            assert "still closing" in str(e)
        r = AM.install_menu_bar(win, [alt], running=lambda: False, now=__import__("datetime").datetime(2026, 10, 17, 4, 0),
                                sleep=lambda s: None)
        text = (alt / "DXP.RCS").read_bytes().decode("latin-1")
        assert r["installed"] == [str(alt / "DXP.RCS")] and Path(r["backups"][0]).read_bytes() == theirs.encode("latin-1")
        blocks = AM.rcs_blocks(text)
        ids = [AM._block_id(b) for b in blocks]
        assert ids.count("PCBCustom") == 1 and "Mine_Menu" in ids and "Mine_Run" in ids
        assert {"SchDoc_ecadlib_Menu", "PcbDoc_ecadlib_Menu", "SchLib_ecadlib_Menu", "PcbLib_ecadlib_Menu"} <= set(ids)
        assert blocks[ids.index("PCBCustom")] == ["Tree PCBCustom Caption='[Custom]' TopLevel='True'  End"]
        assert "ProcName=D:\\ecadlib menu\\ecadlib_menu.pas>CheckProjectBOM" in text
        again = AM.install_menu_bar(win, [alt], running=lambda: False, sleep=lambda s: None)
        assert again["installed"] == [] and again["backups"] == []
        AM.install_menu_bar(win, [alt], remove=True, running=lambda: False, sleep=lambda s: None)
        back = (alt / "DXP.RCS").read_bytes().decode("latin-1")
        assert "ecadlib" not in back and [AM._block_id(b) for b in AM.rcs_blocks(back)] == ["PCBCustom", "Mine_Run", "Mine_Menu"]
    for bad in ("Tree X Caption='a' \r\n", "End\r\n"):
        try:
            AM.rcs_blocks(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_pas_uses_only_documented_calls_and_is_balanced():
    text = AM.pas_text()
    assert any("OpenDialog is not declared" in p for p in AM.pas_problems(text)), "the dialog unit is what declares it"
    assert AM.pas_problems(text, known=AM.unit_names(AF.dialog_pas_text()) | AM.unit_names(AN.pas_text())) == []
    toks = AM.pas_tokens(text)
    routines = AM.pas_routines(toks)
    for proc, _, _ in AM.PROCS:
        assert routines[proc]["kind"] == "procedure" and not routines[proc]["params"]
    used = {t[1].lower() for r in routines.values() for t in r["body"] if t[0] == "word"}
    api = {k.lower() for k in AM.ALTIUM_API}
    dlg = {t[1].lower() for r in AM.pas_routines(AM.pas_tokens(AF.dialog_pas_text())).values() for t in r["body"]
           if t[0] == "word"}
    assert api <= used | dlg | {"processmessages", "application"} | {"create", "free"}, sorted(api - used - dlg)
    for name, where in AM.ALTIUM_API.items():
        assert re.search(r"TR01\d\d|used in|see ", where), f"{name}: no reference"
    # every Begin / Try has its End, counted across the whole unit
    words = [t[1].lower() for t in toks if t[0] == "word"]
    assert words.count("begin") + words.count("try") + words.count("case") == words.count("end")
    # the .pas builds exactly the command lines script_command_line() describes (checked for quoting below)
    assert """'cmd.exe /s /c ""' + Here + 'ecadlib_run.cmd" "' + Job + '""'""" in text
    assert """'wscript.exe //B //NoLogo "' + Here + 'ecadlib_run.vbs" "' + Here + 'ecadlib_run.cmd" "' + Job + '"'""" in text
    # the job folder never ends with a backslash (a \\" would escape the closing quote)
    assert "Result := Here + 'jobs\\' + FormatDateTime('yyyymmdd-hhnnss', Now) + '-' + IntToStr(Random(100000));" in text
    # the result keys the script reads are the ones run-job writes
    assert set(re.findall(r"Answer\(Job, '(\w+)'\)", text)) == {"install", "build", "message", "fix", "status", "offer_fill", "offer",
        "offer_ai", "ai_text", "ai_over", "ai_confirm", "ai_bom", "ai_mpn", "ai_part", "ai_approve", "ai_unknown"}


def test_menu_lets_altium_build_the_library_before_reinstalling_it():
    """build= in result.ini: the menu uninstalls the listed DbLibs, calls BuildLibraries (ecadlib_build.pas) with a
    folder ending in a backslash, reports FAILED libraries, then installs again; never a message box mid-run."""
    text = AM.pas_text()
    r = AM.pas_routines(AM.pas_tokens(text))
    assert "Result := BuildInAltium(Job);" in text and list(r).index("BuildInAltium") < list(r).index("Reinstall")
    body = text[text.index("Function BuildInAltium"):text.index("Function Reinstall")]
    assert "Folder := Folder + '\\';" in body and body.index("UninstallLibrary") < body.index("BuildLibraries(Folder);")
    assert "Setting('native_build', '1') = '0'" in body and "ShowMessage" not in body and "ConfirmNoYes" not in body
    assert "'FAILED'" in text[text.index("Function BuildReport"):text.index("Function BuildInAltium")]
    assert "Pos('Altium build:', Extra)" in AF.dialog_pas_text(), "the dialog shows a failed build"


def test_pas_checker_catches_mistakes():
    ok = "".join(f"Procedure {p};\nBegin\nEnd;\n" for p, _, _ in AM.PROCS)
    assert AM.pas_problems(ok) == []
    cases = {
        "not in the API allow-list": ok + "Procedure X;\nBegin\n    ShellExecute(0, 'open', 'x', '', '', 1);\nEnd;\n",
        "not declared": ok + "Procedure X;\nBegin\n    Y := 1;\nEnd;\n",
        "never sets Result": ok + "Function F : String;\nBegin\nEnd;\n",
        "Begin without End": ok + "Procedure X;\nBegin\n    If True Then\n    Begin\n        ShowMessage('a');\nEnd;\n",
        "unterminated string": ok + "Procedure X;\nBegin\n    ShowMessage('a);\nEnd;\n",
        "without parameters": ok.replace("Procedure OpenReviewQueue;", "Procedure OpenReviewQueue(X : String);"),
        "OpenReviewQueue: missing": ok.replace("OpenReviewQueue", "Other"),
    }
    for want, text in cases.items():
        probs = AM.pas_problems(text)
        assert any(want in p for p in probs), (want, probs)
    # strings and comments do not count as calls; '' is a quote inside a string; #13#10 is a character code
    toks = AM.pas_tokens("{ ShellExecute } (* RunApplicationAndWait *) // CreateProcess\n'it''s' + #13#10")
    assert toks == [("str", "it's", 2), ("sym", "+", 2), ("str", "\r", 2), ("str", "\n", 2)]


def test_dialog_form_matches_its_unit_and_the_results():
    """The ecadlib dialog (ecadlib_dialog.pas + .dfm): every component the unit names is in the form, every handler
    the form names is in the unit, the .dfm nests object / end, and the keys the dialog reads are ones run-job writes."""
    files = {AM.UNIT: AM.pas_text(), AF.DIALOG_UNIT: AF.dialog_pas_text(), AF.DIALOG_FORM: AF.dialog_dfm_text(),
             AN.UNIT: AN.pas_text()}
    assert AM.project_problems(files) == []
    assert "OnClose = EcadlibDialogClose" in files[AF.DIALOG_FORM], "a closed form's timer must stop (AD 26.10)"
    dfm = files[AF.DIALOG_FORM]
    names = AF.dfm_names(dfm)
    assert names[0] == "EcadlibDialog" and len(names) == len(set(names))
    assert dfm.startswith("object EcadlibDialog: TEcadlibDialog\r\n") and dfm.rstrip().endswith("end")
    depth = 0
    for ln in dfm.split("\r\n"):
        if re.match(r"^\s*object \w+: T\w+$", ln):
            depth += 1
        elif re.match(r"^\s*end$", ln):
            depth -= 1
            assert depth >= 0
    assert depth == 0
    assert {f"Row{i}{x}" for i in range(1, AF.ROWS + 1) for x in ("", "A", "B")} <= set(names)
    assert "FormKind = fkModal" in dfm and "BorderStyle = bsNone" in dfm and "OnTimer = TickTimer" in dfm
    assert "'ecadlib '#183' Check project BOM'" in dfm, "non-ASCII characters as #codes"
    # what the checker catches in the dialog: a handler the form names but the unit lacks, a component not in the form
    broken = dict(files, **{AF.DIALOG_UNIT: files[AF.DIALOG_UNIT].replace("Procedure TEcadlibDialog.ReportClick", "Procedure ReportGone")})
    assert any("ReportClick" in p for p in AM.project_problems(broken))
    broken = dict(files, **{AF.DIALOG_UNIT: files[AF.DIALOG_UNIT].replace("EcadlibDialog.Report.Visible := False;", "EcadlibDialog.Gone.Visible := False;", 1)})
    assert any("Gone is not declared" in p for p in AM.project_problems(broken))
    read = set(re.findall(r"Answer\(Job, '(\w+)'\)", files[AF.DIALOG_UNIT]))
    assert read == {"status", "install", "build", "big", "report", "warnings", "offer_fill", "fix", "offer_queue",
                    "offer_ai", "ai_text", "ai_button", "ai_cost_text", "offer_release", "release_part"}, read
    asked = set(re.findall(r"Answer\(Prev, '(\w+)'\)", files[AF.DIALOG_UNIT]))       # the AI step's next job
    assert asked == {"ai_over", "ai_confirm", "ai_bom", "ai_mpn", "ai_part", "ai_approve", "ai_unknown",
                     "release_part", "release_bom", "release_mpn"}, asked                   # and Release anyway's
    assert "ReadString('progress', 'text'" in files[AF.DIALOG_UNIT] and "'row' + IntToStr(i)" in files[AF.DIALOG_UNIT]
    # the menu entries open the dialog, unless dialogs=0 asks for Altium's plain message boxes
    assert "OpenDialog('add', '', '')" in files[AM.UNIT] and "OpenDialog('bom', PrjPath, '')" in files[AM.UNIT]
    assert "Setting('dialogs', '1') = '0'" in files[AM.UNIT]


def test_progress_file_for_the_live_view():
    d = _tmp()
    t = [0.0]
    pr = AM.Progress(d, every_s=0.25, clock=lambda: t[0])
    pr.step(0, 6, "line 1 of 6: LM358DR")
    pr.step(1, 6, "line 2 of 6: skipped (too soon)")              # within 0.25 s: not written
    got = lambda: dict(AM.read_ini(d / "progress.ini")["progress"])
    assert got() == {"done": "0", "total": "6", "text": "line 1 of 6: LM358DR", "rows": "0"}
    t[0] = 1.0
    pr(6, 6, "writing report\nnow")                                # the Workspace progress(done, total, label) form
    assert got()["done"] == "6" and got()["text"] == "writing report now"
    pr.row("KiCad library", "no symbol | named like X")            # rows are always written
    t[0] = 2.0
    pr.step(0, 0, "  done NE555DR      sym=easyeda:C7593 fp=easyeda:C7593")   # librarian log lines, in plain words
    assert got()["text"] == "NE555DR: checked"
    pr.row("symbol", "EasyEDA", True)
    g = got()
    assert g["rows"] == "2" and g["row1"] == "KiCad library|no symbol / named like X|0" and g["row2"] == "symbol|EasyEDA|1"
    assert (d / "progress.ini").read_bytes()[:2] == b"\xff\xfe" and not list(d.glob("*.tmp"))
    AM.Progress().step(1, 2, "no job: nothing written")
    # the columns do not wrap: values are cut at a word to what fits (seen clipped on a real AD 26.10 dialog)
    r = AM.row_text("KiCad library", "library index unavailable (kicad-symbols tree page 3 timed out)")
    assert r == "KiCad library|library index unavailable...|0" and all(len(x) <= 28 for x in r.split("|")[1:])
    assert AM.rows_result(["a|1|1"], "1 line") == {"big": "1 line", "rows": 1, "row1": "a|1|1"}


def test_command_lines_survive_spaces_and_windows_separators():
    here = "C:\\Users\\Ann Lee\\Documents\\Altium (scripts) & co\\ecadlib menu\\"
    job = here + "jobs\\20261016-120000-42"
    for hidden in (True, False):
        line = AM.script_command_line(here, job, hidden)
        if hidden:
            a = argv_w(line)
            assert a == ["wscript.exe", "//B", "//NoLogo", here + AM.RUN_VBS, here + AM.RUN_CMD, job], a
            line = AM.vbs_line(a[4], a[5])                    # what ecadlib_run.vbs hands to WScript.Shell.Run
        assert argv_w(cmd_s_c(line)) == [here + AM.RUN_CMD, job]
    # ecadlib_run.vbs builds exactly vbs_line(): q = Chr(34), the same concatenation
    vbs = AM.run_vbs_text()
    assert 'line = "cmd.exe /s /c " & q & q & WScript.Arguments(0) & q & " " & q & WScript.Arguments(1) & q & q' in vbs
    assert 'CreateObject("WScript.Shell").Run line, 0, True' in vbs        # window style 0 (hidden), wait = True
    # why the job folder must not end with a backslash: \" escapes the closing quote
    assert argv_w(f'x "{job}\\" y') != ["x", job + "\\", "y"]
    # the runner quotes the job folder everywhere and runs exactly `altium-menu run-job "<job>"`
    cmd = AM.run_cmd_text()
    assert 'set "JOB=%~1"' in cmd
    assert re.findall(r"%JOB%[^\"]*\"", cmd) and not re.search(r'[^"]%JOB%', cmd.replace('set "JOB=%~1"', ""))
    run = [ln for ln in cmd.split("\r\n") if "run-job" in ln and not ln.startswith("rem")]
    assert run == ['%ECADLIB% altium-menu run-job "%JOB%" > "%JOB%\\log.txt" 2>&1']
    assert cmd.index("run-job \"%JOB%\" >") < cmd.index('> "%JOB%\\exit.txt" echo %ERRORLEVEL%')
    assert '> "%JOB%\\notfound.txt" echo 9009' in cmd and 'py -3 -m ecadlib' in cmd and 'python -m ecadlib' in cmd
    # the command= setting reaches the runner through command.txt (set /p), never through a command line
    assert 'set /p ECADLIB=<"%JOB%\\command.txt"' in cmd and "L.SaveToFile(Result + '\\command.txt');" in AM.pas_text()


def test_settings_paths_resolve_relative_and_windows_style():
    d = _tmp()
    (d / AM.INI).write_text("[ecadlib]\nworkspace=..\\shared ws\nlibrary=libs\\altium\nport=9100\noffline=1\n", encoding="utf-8")
    cfg = AM.load_config(d / AM.INI)
    assert cfg["workspace"] == d / ".." / "shared ws" and cfg["library"] == d / "libs" / "altium"
    assert cfg["port"] == 9100 and cfg["offline"] is True and cfg["name"] == "ecadlib"
    (d / AM.INI).write_text("[ecadlib]\nworkspace=D:\\ws\nport=x\n", encoding="utf-8")
    try:
        AM.load_config(d / AM.INI)
        raise AssertionError("bad port accepted")
    except ValueError as e:
        assert "port" in str(e)
    (d / AM.INI).write_text("[ecadlib]\nworkspace=D:\\ws\n", encoding="utf-8")
    cfg = AM.load_config(d / AM.INI)
    assert str(cfg["workspace"]) == "D:\\ws" and str(cfg["library"]).startswith("D:\\ws")


def test_result_file_is_utf16_one_line_values():
    d = _tmp()
    AM.write_result(d / "result.ini", {"status": "held", "message": "Müller's part\nis held:\r\n  no symbol",
                                       "install": "C:\\Lib Ü\\a.DbLib;C:\\b.DbLib"})
    r = _result(d)
    assert r == {"status": "held", "message": "Müller's part is held: no symbol", "fix": "",
                 "install": "C:\\Lib Ü\\a.DbLib;C:\\b.DbLib"}
    assert not list(d.glob("*.tmp"))
    # request.ini as TIniFile writes it: ANSI (cp1252) here, or UTF-16 when the file had a BOM
    for enc, bom in (("cp1252", b""), ("utf-16-le", b"\xff\xfe"), ("utf-8", b"")):
        (d / "request.ini").write_bytes(bom + "[request]\r\nmanufacturer=Société Générale\r\n".encode(enc))
        assert AM.read_ini(d / "request.ini")["request"]["manufacturer"] == "Société Générale", enc


# ---------------------------------------------------------------- the actions, end to end offline
def test_add_part_ready_through_run_job_exactly_as_the_script_runs_it():
    menu, cfg = _setup()
    job = _job(menu, {"action": "add-part", **CAP})
    # the command line ecadlib_run.cmd runs (with ECADLIB = python -m ecadlib): altium-menu run-job "<job>"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.run([sys.executable, "-m", "ecadlib", "altium-menu", "run-job", str(job)], capture_output=True,
                       text=True, env=env, cwd=str(job), timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    r = _result(job)
    assert r["status"] == "ready" and r["part"] == "CAP-0402-100nF" and r["refresh"] == "folder", r
    assert r["fix"] == "" and "CL05B104KO5NNNC is ready as CAP-0402-100nF" in r["message"]
    dblib = Path(r["install"])
    assert dblib.is_file() and dblib.suffix == ".DbLib" and dblib.parent == cfg["library"].resolve()
    rows = list(csv.DictReader((dblib.with_suffix(".csv")).open(encoding="utf-8")))
    assert [row["Manufacturer Part Number"] for row in rows] == ["CL05B104KO5NNNC"]
    assert (dblib.parent / "SchLib").is_dir() and (dblib.parent / "PcbLib").is_dir() and (dblib.parent / "ecadlib.LibPkg").is_file()
    # the recipes Altium builds the libraries from (the menu calls BuildLibraries on build= before it reinstalls)
    assert r["build"] == str(cfg["library"].resolve()), r
    built = (dblib.parent / AN.BUILD).read_text(encoding="utf-8-sig")
    libs = {ln.split("\t")[1] for ln in built.splitlines() if ln.startswith(("PCBLIB\t", "SCHLIB\t"))}
    on_disk = {str(f.relative_to(dblib.parent)).replace("/", "\\") for f in dblib.parent.glob("*/*.*Lib")}
    assert libs == on_disk and len(libs) == 2, (libs, on_disk)
    # the part list in the workspace; a second add of the same MPN does not duplicate it and keeps the library
    bom = cfg["workspace"] / AM.MENU_BOM
    job2 = _job(menu, {"action": "add-part", "mpn": "cl05b104ko5nnnc", "manufacturer": ""})
    r2 = AM.run_job(job2)
    assert r2["status"] == "ready" and len(list(csv.DictReader(bom.open(encoding="utf-8")))) == 1


def test_add_part_held_says_why_and_publishes_nothing():
    menu, cfg = _setup()
    r = AM.run_job(_job(menu, {"action": "add-part", "mpn": "NE555DR", "manufacturer": "Texas Instruments"}))
    assert r["status"] == "held" and r["install"] == "" and r["build"] == "", r
    assert r["message"].startswith("NE555DR is held") and "There is no symbol for this part yet." in r["message"], r
    assert "review queue" in r["fix"] and "release it anyway" in r["fix"] and "plan.json" not in r["message"] + r["fix"]
    assert not cfg["library"].exists() or not list(cfg["library"].glob("*.DbLib"))
    for bad in ("", "  ", "x;del *", "A" * 90):
        r = AM.run_job(_job(menu, {"action": "add-part", "mpn": bad}))
        assert r["status"] == "error" and "not a manufacturer part number" in r["message"], (bad, r)


def test_check_bom_report_offer_and_fill():
    from tests.test_altium_project import board
    d = _tmp()
    menu, cfg = _setup(d)
    prj = board(d / "Board files (rev A)")
    lib = cfg["library"]
    lib.mkdir(parents=True)
    with open(lib / "ecadlib.csv", "w", newline="", encoding="utf-8") as f:   # one part already in the DbLib's table
        w = csv.writer(f)
        w.writerow(["Table", "Part Number", "Manufacturer Part Number"])
        w.writerow(["Capacitors", "CAP-0603-100nF", "GRM188R71C104KA01D"])
    opened = []
    job = _job(menu, {"action": "check-bom", "project": str(prj)})
    r = AM.run_job(job, opener=opened.append)
    assert {"fix": "", **{k: str(v) for k, v in r.items()}} == _result(job)
    r = _result(job)                                             # what the script sees: strings
    assert r["status"] == "ok" and r["offer_fill"] == "1" and r["lines"] == "6" and r["missing"] == "5", r
    assert "1 in your library, 5 not yet" in r["message"] and "open in your browser" in r["message"]
    assert "Fill the library now?" in r["offer"] and "Missing.SchDoc" in r["warnings"]
    assert opened == [r["report"]] and r["report"].endswith(".html")
    page = Path(r["report"]).read_text(encoding="utf-8")
    assert "GRM188R71C104KA01D" in page and "LM358DR" in page and page.count("<tr>") == 7
    assert Path(r["bom"]).is_file()
    # the user says yes: fill (offline, standard parts only: the two passives are ready with a description ecadlib
    # wrote, the rest held, and a fix that says where to look)
    job = _job(menu, {"action": "fill", "project": str(prj)})
    AM.run_job(job)
    r = _result(job)
    assert r["status"] == "ok" and r["held"] == "4" and r["ready"] == "2" and r["install"].endswith("ecadlib.DbLib"), r
    assert r["build"] and (Path(r["build"]) / AN.BUILD).is_file(), r
    assert r["message"].startswith("6 parts: 2 ready") and "review queue" in r["fix"] and Path(r["review"]).is_file()
    # a project that is not saved / not there: one line and a fix, never a traceback
    r = AM.run_job(_job(menu, {"action": "check-bom", "project": str(d / "Unsaved.PrjPcb")}))
    assert r["status"] == "error" and "no such file" in r["message"] and r["fix"].startswith("save the project")


def test_check_bom_says_why_lines_were_not_checked():
    """Lines with no part number are counted as such, and an unverified line is 'offline' only when the run was
    offline: online it is a source that refused or could not be reached (it once read 'offline: 26' online)."""
    from ecadlib.app import Workspace
    d = _tmp()
    _, cfg = _setup(d)
    bom = d / "bom.csv"
    bom.write_text("Designator,Qty,MPN\nR1,1,\n", encoding="utf-8")
    un = "unverified (source unreachable)"
    rows = [{"line": 1, "mpn": "", "lcsc": "", "status": un}, {"line": 2, "mpn": "", "lcsc": "", "status": un},
            {"line": 3, "mpn": "TPS62162DSGR", "lcsc": "", "status": un},
            {"line": 4, "mpn": "", "lcsc": "C25804", "status": "available_easyeda"},
            {"line": 5, "mpn": "X1", "lcsc": "", "status": "dnp (not fitted)"}]

    def fake(self, bom, progress=None, **kw):
        (self.root / "reports").mkdir(parents=True, exist_ok=True)
        (self.root / "reports" / "triage_bom.md").write_text("x", encoding="utf-8")
        return {"rows": [{"designator": "", "qty": 1, "manufacturer": "", "notes": "", **r} for r in rows],
                "reports": ["reports/triage_bom.md"]}
    for offline, label in ((False, "source refused/unreachable"), (True, "could not check (offline)")):
        with mock.patch.object(Workspace, "triage", fake):
            r = AM.check_bom({**cfg, "offline": offline}, str(bom), opener=False)
        shown = [r[f"row{i}"] for i in range(1, int(r["rows"]) + 1)]
        assert shown == ["already in your library|0|1", "a source can supply|1|0", "no part number|2|0",
                         f"{label}|1|0", "not fitted|1|0"], (offline, shown)


def test_dialog_results_rows_and_reports():
    """With dialog=1 (the ecadlib dialog) a BOM check opens nothing: the dialog shows the counts and opens the report
    on request (action=open, files in the workspace only). Each result carries the big line and the rows."""
    from tests.test_altium_project import board
    d = _tmp()
    menu, cfg = _setup(d)
    prj = board(d / "Board files (rev A)")
    opened = []
    job = _job(menu, {"action": "check-bom", "project": str(prj), "dialog": "1"})
    AM.run_job(job, opener=opened.append)
    r = _result(job)
    assert opened == [] and "Report:" in r["message"] and r["big"] == "6 lines", r
    rows = [r[f"row{i}"] for i in range(1, int(r["rows"]) + 1)]
    assert rows[0] == "already in your library|0|1" and all(x.count("|") == 2 for x in rows), rows
    assert sum(int(x.split("|")[1]) for x in rows) == 6, rows          # every line counted once
    pg = dict(AM.read_ini(job / "progress.ini")["progress"])
    assert pg["done"] == pg["total"] == "6", pg
    # the dialog's "Open the full report": only a report inside the workspace or the library folder
    r2 = AM.run_job(_job(menu, {"action": "open", "path": r["report"]}), opener=opened.append)
    assert r2["status"] == "ok" and opened == [str(Path(r["report"]).resolve())], r2
    for bad in (str(menu / AM.INI), str(d / "elsewhere.html"), sys.executable):
        assert AM.run_job(_job(menu, {"action": "open", "path": bad}), opener=opened.append)["status"] == "error", bad
    assert len(opened) == 1
    # fill: big line, ready / held rows, and the review queue offered for the held parts
    job = _job(menu, {"action": "fill", "project": str(prj), "dialog": "1"})
    AM.run_job(job)
    r = _result(job)
    assert r["big"] == "2 of 6 ready" and r["offer_queue"] == "1" and r["row1"] == "ready, in your library|2|1", r
    assert r["row2"] == "held for a person|4|0", r
    # one part: where ecadlib looked and what it took, then the release check
    job = _job(menu, {"action": "add-part", "dialog": "1", **CAP})
    AM.run_job(job)
    r = _result(job)
    rows = [r[f"row{i}"] for i in range(1, int(r["rows"]) + 1)]
    assert r["big"] == "Ready" and rows[-1] == "release check|ready|1", rows
    assert any(x.startswith("symbol|standard parts") and x.endswith("|1") for x in rows), rows


def test_altium_365_target():
    """[altium365] workspace in the rules: ready parts go to Altium 365 (push), nothing to reinstall; without a token
    the dialog says what to set."""
    menu, cfg = _setup(extra_rules='\n[altium365]\nworkspace = "https://acme.365.altium.com"\n')
    with mock.patch.dict(os.environ, {"NEXAR_TOKEN": ""}):
        r = AM.run_job(_job(menu, {"action": "add-part", **CAP}))
    assert r["status"] == "error" and "not published" in r["message"] and "NEXAR_TOKEN" in r["fix"], r
    from ecadlib.app import Workspace
    calls = []

    def push(self, bom, url, dry_run=True):
        calls.append((bom, url, dry_run))
        return {"dry_run": dry_run, "report": "altium365/push_x.json",
                "results": [{"name": "CAP-0402-100nF", "action": "released", "detail": ""}]}
    with mock.patch.object(Workspace, "altium365_push", push):
        r = AM.run_job(_job(menu, {"action": "add-part", **CAP}))
    assert calls == [("uploads/altium-menu/parts.csv", "https://acme.365.altium.com", False)]
    assert r["status"] == "ready" and r["install"] == "" and r["build"] == "" and r["refresh"] == "a365", r
    assert "refresh the Components panel" in r["message"]


def test_publish_reports_a_part_the_publisher_held_again():
    """Every target runs the release check again before it writes a part; one it holds is "not published", not ready."""
    menu, cfg = _setup(extra_rules='\n[altium365]\nworkspace = "https://acme.365.altium.com"\n')
    from ecadlib.app import Workspace
    held = {"name": "CAP-0402-100nF", "action": "skip", "detail": "held: fields: Description is empty",
            "recheck": ["fields: Description is empty"]}
    with mock.patch.object(Workspace, "altium365_push", lambda self, bom, url, dry_run=True: {
            "dry_run": dry_run, "report": "altium365/push_x.json", "results": [held]}):
        r = AM.run_job(_job(menu, {"action": "add-part", **CAP}))
    assert r["status"] == "error" and r["big"] == "Not published", r
    assert "not published: CAP-0402-100nF: held: fields: Description is empty" in r["message"], r
    menu, cfg = _setup()
    assert AM.run_job(_job(menu, {"action": "add-part", **CAP}))["status"] == "ready"
    ws, rel = AM._workspace(cfg), "uploads/altium-menu/parts.csv"
    _, plan_f, items = ws._plan(rel)
    for it in items:
        it["description"] = ""                                   # after the check
    plan_f.write_text(json.dumps(items, indent=2), encoding="utf-8")
    for tool, lib in (("altium", cfg["library"]), ("kicad", str(Path(cfg["library"]).parent / "kicad lib"))):
        pub = AM.publish(ws, rel, lib, cfg["name"], tool=tool)
        assert pub["held"] == {"CAP-0402-100nF": "held: fields: Description is empty"}, (tool, pub)
        with mock.patch.object(AM, "publish", lambda *a, **k: pub):          # fill: the same hold, in its message
            r = AM._fill_result(cfg, ws, rel, {"parts": 1, "ready": 1, "held": 0, "awaiting_approval": 0,
                                               "duplicates": 0}, AM.Progress())
        assert r["status"] == "error" and "Not published: CAP-0402-100nF: held: fields: Description is empty" in \
            r["message"], (tool, r)


def test_open_queue_starts_reuses_and_refuses():
    menu, cfg = _setup()
    state, launched, opened = {"kind": None}, [], []
    probe = lambda port: (state["kind"], str(cfg["workspace"]) if state["kind"] == "ecadlib" else None)

    def launcher(ws, port):
        launched.append((Path(ws), port))
        state["kind"] = "ecadlib"
    r = AM.open_queue(cfg, opener=opened.append, probe=probe, launcher=launcher, sleep=lambda s: None)
    assert r["status"] == "ok" and r["started"] == "1" and launched == [(cfg["workspace"], 8765)]
    assert opened == ["http://127.0.0.1:8765/#libcard"]
    r = AM.open_queue(cfg, opener=opened.append, probe=probe, launcher=launcher)
    assert r["started"] == "0" and len(launched) == 1
    state["kind"] = "other"
    r = AM.open_queue(cfg, opener=opened.append, probe=probe, launcher=launcher)
    assert r["status"] == "error" and "another program" in r["message"] and "port=" in r["fix"]
    r = AM.open_queue(cfg, opener=opened.append, probe=lambda p: ("ecadlib", "/somewhere/else"), launcher=launcher)
    assert r["status"] == "error" and "another ecadlib workspace" in r["message"]
    r = AM.open_queue(cfg, opener=opened.append, probe=lambda p: (None, None), launcher=lambda w, p: None,
                      wait_s=0.0, sleep=lambda s: None)
    assert r["status"] == "error" and "did not start" in r["message"] and "altium_menu_ui.log" in r["fix"]
    assert AM.open_queue(cfg, opener=opened.append, probe=lambda p: ("ecadlib-team", None))["status"] == "ok"


def test_open_queue_starts_the_real_web_ui():
    """start_ui really starts `python -m ecadlib ui` (detached, no browser) and the page has the review queue."""
    import urllib.request
    menu, cfg = _setup()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        cfg["port"] = s.getsockname()[1]
    procs, opened = [], []
    try:
        r = AM.open_queue(cfg, opener=opened.append, launcher=lambda ws, port: procs.append(AM.start_ui(ws, port)))
        assert r["status"] == "ok" and r["started"] == "1", r
        page = urllib.request.urlopen(opened[0].split("#")[0], timeout=10).read().decode("utf-8")
        assert 'id="libcard"' in page and opened[0].endswith("#libcard")
        assert AM.probe_ui(cfg["port"]) == ("ecadlib", str(cfg["workspace"].resolve()))
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)


def test_errors_are_one_line_with_a_fix():
    menu, cfg = _setup()
    r = AM.run_job(_job(menu, {"action": "fly"}))
    assert r["status"] == "error" and "unknown action" in r["message"] and r["fix"]
    job = menu / "jobs" / "broken"
    job.mkdir(parents=True)
    r = AM.run_job(job)                                          # no request.ini at all
    assert r["status"] == "error" and "\n" not in r["message"] and _result(job)["status"] == "error"
    job = _job(menu, {"action": "add-part", "mpn": "X1"})
    with mock.patch.object(AM, "add_part", side_effect=RuntimeError("boom\nsecond line")):
        r = AM.run_job(job)
    assert r["message"] == "RuntimeError: boom\nsecond line" and _result(job)["message"] == "RuntimeError: boom second line"


def test_old_finished_jobs_are_pruned():
    d = _tmp()
    for name, done, age in (("old", True, 20), ("old-running", False, 20), ("new", True, 1)):
        (d / name).mkdir()
        if done:
            (d / name / "exit.txt").write_text("0")
            os.utime(d / name / "exit.txt", (0, 1_000_000 - age * 86400))
        else:
            (d / name / "request.ini").write_text("[request]\n")
    assert AM.prune_jobs(d, now=1_000_000) == 1
    assert sorted(p.name for p in d.iterdir()) == ["new", "old-running"]


# ---------------------------------------------------------------- front ends
def test_cli_workspace_and_mcp():
    from ecadlib import cli, mcp_server as M
    from ecadlib.app import Workspace
    assert "altium-menu" in cli.COMMANDS
    d = _tmp()
    menu, cfg = _setup(d)
    rf = d / "r esult.ini"
    rc, out = _quiet(cli.main, ["altium-menu", "add-part", CAP["mpn"], "--config", str(menu / AM.INI),
                                "--manufacturer", CAP["manufacturer"], "--value", "100nF", "--footprint", CAP["footprint"],
                                "--description", CAP["description"], "--designator", "C1", "--result-file", str(rf), "--json"])
    assert rc == 0 and json.loads(out)["status"] == "ready", out
    data = rf.read_bytes()
    assert data[:2] == b"\xff\xfe" and "status=ready" in data.decode("utf-16")
    rc, out = _quiet(cli.main, ["altium-menu", "build", "--out", str(d / "b"), "--json"])
    assert rc == 0 and json.loads(out)["problems"] == []
    ws = Workspace(d / "ws2", offline=True)
    rep = M.call_tool(ws, "altium_menu", {"win_dir": "D:\\ws2\\altium_menu"})
    assert rep["problems"] == [] and rep["menu_file"] == AM.PREFS and rep["folder"] == "altium_menu"
    assert rep["procedures"] == AM.procedures() and rep["menu_entries"] == ["AddPartFromMPN", "CheckProjectBOM", "OpenReviewQueue"]
    c = AM.load_config(ws.root / "altium_menu" / AM.INI)
    assert c["workspace"].resolve() == ws.root and c["library"].resolve() == ws.root / "altium_library"
    assert "altium_menu" in {t["name"] for t in M.TOOLS}



def test_build_writes_this_pythons_command_on_windows_and_fills_an_empty_one():
    exe = "C:\\Users\\Oliver\\ecadlib venv\\Scripts\\python.exe"
    assert AM.default_command(exe, nt=True) == '"C:\\Users\\Oliver\\ecadlib venv\\Scripts\\python.exe" -m ecadlib'
    assert AM.default_command(exe, nt=False) == ""               # built elsewhere: that Python is not on the Altium PC
    assert AM.default_command('C:\\a"b\\python.exe', nt=True) == ""
    with mock.patch.object(AM, "_short_path", lambda p: "C:\\Users\\OLIVER~1\\venv\\Scripts\\python.exe"):
        assert AM.default_command("C:\\Users\\Olivér\\venv\\Scripts\\python.exe", nt=True) == \
            '"C:\\Users\\OLIVER~1\\venv\\Scripts\\python.exe" -m ecadlib'           # the .ini stays ASCII
    cmd = AM.default_command(exe, nt=True)
    d = _tmp()
    with mock.patch.object(AM, "default_command", lambda *a, **k: cmd):
        rep = AM.build(d / "new", "ws", None)
        assert rep["command"] == cmd and not rep["command_filled"], rep
        raw = (d / "new" / AM.INI).read_bytes()
        assert b'\r\ncommand="C:\\Users\\Oliver\\ecadlib venv\\Scripts\\python.exe" -m ecadlib\r\n' in raw
        assert AM.load_command(d / "new" / AM.INI) == cmd                  # TIniFile keeps a value that only starts with "
        # what the runner does with it: set /p reads the line, %ECADLIB% altium-menu run-job "<job>" runs it
        line = f'{cmd} altium-menu run-job "C:\\menu\\jobs\\1"'
        assert argv_w(line) == [exe, "-m", "ecadlib", "altium-menu", "run-job", "C:\\menu\\jobs\\1"]
        # an older build left command= empty: a rebuild fills only that line; an edited command= is kept
        old = AM.ini_text("ws", "lib", command="").replace("port=8765", "port=9001").encode()
        (d / "old").mkdir()
        (d / "old" / AM.INI).write_bytes(old)
        rep = AM.build(d / "old", "elsewhere", None)
        assert rep["command_filled"] and rep["kept"] == [AM.INI], rep
        assert (d / "old" / AM.INI).read_bytes() == old.replace(b"\r\ncommand=\r\n", b"\r\ncommand=" + cmd.encode() + b"\r\n")
        mine = old.replace(b"\r\ncommand=\r\n", b"\r\ncommand=ecadlib\r\n")
        (d / "old" / AM.INI).write_bytes(mine)
        assert not AM.build(d / "old", "x", None)["command_filled"] and (d / "old" / AM.INI).read_bytes() == mine
        rep = AM.build(d / "given", "ws", None, command="py -3.13 -m ecadlib")   # --command wins
        assert rep["command"] == "py -3.13 -m ecadlib"
    readme = (d / "new" / AM.README).read_text()
    assert "venv" in readme and "come back held" in readme and "ecadlib keys set ANTHROPIC_API_KEY" in readme \
        and "setx ANTHROPIC_API_KEY" not in readme


def test_installed_libraries_are_counted_with_the_installed_count():
    from ecadlib import altium_pkg as AP
    for text in (AM.pas_text(), AP.install_script("lib", ["lib.DbLib"], "lib.LibPkg", ["SchLib\\a.SchLib"])):
        assert "AvailableLibraryCount" not in text
        loops = re.findall(r"For i := .*LibraryCount.*Do", text)
        assert loops and all("InstalledLibraryCount" in l for l in loops), loops
        assert "InstalledLibraryPath(i)" in text
    src = Path(AP.__file__).read_text()
    assert "AvailableLibraryCount" not in src and src.count("InstalledLibraryCount") == 2


def test_doctor_checks_the_menus_command():
    from ecadlib import doctor as DR
    d = _tmp()
    ini = d / AM.INI
    ini.write_bytes(AM.ini_text(command="").encode())
    c = DR.check_altium_menu(ini)
    assert c["level"] == "WARN" and "command= is empty" in c["detail"] and "venv" in c["detail"], c
    ini.write_bytes(AM.ini_text(command=f'"{d / "nope" / "python.exe"}" -m ecadlib').encode())
    assert DR.check_altium_menu(ini)["level"] == "FAIL" and "does not exist" in DR.check_altium_menu(ini)["detail"]
    ini.write_bytes(AM.ini_text(command=f'"{sys.executable}" -m ecadlib').encode())
    seen = []
    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ecadlib 9.9\n", "")
    c = DR.check_altium_menu(ini, run=run)
    assert c["level"] == "OK" and "ecadlib 9.9" in c["detail"] and seen == [[sys.executable, "-m", "ecadlib", "--version"]], (c, seen)
    c = DR.check_altium_menu(ini, run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "No module named ecadlib"))
    assert c["level"] == "FAIL" and "No module named ecadlib" in c["detail"], c
    assert DR.split_command('"C:\\Program Files\\Python\\python.exe" -m ecadlib') == ["C:\\Program Files\\Python\\python.exe", "-m", "ecadlib"]
    assert DR.split_command("py -3 -m ecadlib") == ["py", "-3", "-m", "ecadlib"]
    assert DR.find_menu_ini(d) == ini and DR.find_menu_ini(None, cwd=d) == ini and DR.find_menu_ini(None, cwd=d / "x") is None
    # really running it (this Python, from the checkout): the same check the user gets
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    with mock.patch.dict(os.environ, env):
        c = DR.check_altium_menu(ini)
    assert c["level"] == "OK", c
    rep = DR.run(str(d / "ws"), altium_menu=str(d))
    assert any(x["check"] == "altium menu" for x in rep["checks"])


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)


def test_pas_checker_catches_a_routine_used_before_it_is_declared():
    """DelphiScript has no forward declarations: AD 26.10 stopped on "Undeclared identifier: ClipboardMpn" when
    OpenDialog called a procedure written below it."""
    text = "Procedure A;\nBegin\n    B;\nEnd;\n\nProcedure B;\nBegin\nEnd;\n"
    assert any("B is used before it is declared" in p for p in AM.pas_problems(text, procs=[]))
    assert not AM.pas_problems(text.replace("    B;\n", ""), procs=[])


def test_dialog_moves_by_its_header():
    """The dialog has no Windows title bar (bsNone): its header and title drag it around the screen."""
    from ecadlib import altium_forms as AF
    dfm, pas = AF.dialog_dfm_text(), AF.dialog_pas_text()
    head = dfm[dfm.index("object Head:"):dfm.index("object HeadClose:")]
    assert head.count("OnMouseDown = HeadMouseDown") == 2 and head.count("OnMouseMove = HeadMouseMove") == 2
    for h in ("HeadMouseDown", "HeadMouseMove", "HeadMouseUp"):
        assert f"Procedure TEcadlibDialog.{h}(" in pas


def test_ai_offer_keeps_the_review_queue():
    """On AD26 the AI offer replaced the "Next: open the review queue" hint and button for a fill with held parts
    (2026-10-18): the hint stays under the offer, and Not now goes back to Done / Open review queue."""
    from ecadlib import altium_forms as AF
    pas = AF.dialog_pas_text()
    offer = pas[pas.index("If Answer(Job, 'offer_ai') = '1' Then"):pas.index("Else If (St = 'held')")]
    assert "Second := 'after_ai'" in offer and "NextHint" in offer and "Buttons(Answer(Job, 'ai_button'), 'Not now'" in offer
    back = pas[pas.index("Else If Second = 'after_ai' Then"):pas.index("Else If Second = 'log' Then")]
    assert "Second := 'queue'" in back and "Buttons('Done', 'Open review queue', False)" in back and "NextHint" in back
