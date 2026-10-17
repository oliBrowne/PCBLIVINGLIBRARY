"""An "ecadlib" menu inside Altium Designer: a DelphiScript script project that calls ecadlib on your machine.

    ecadlib altium-menu build --out D:/ecadlib-menu [--workspace D:/ecadlib-ws] [--to D:/libs/ecadlib]
                              [--command "C:\\Python311\\python.exe -m ecadlib"] [--win-dir D:\\ecadlib-menu]
    ecadlib altium-menu add-part MPN [--manufacturer M] [--workspace W] [--to DIR] [--result-file F] [--json]
    ecadlib altium-menu check-bom BOARD.PrjPcb [--workspace W] [--to DIR] [--no-open] [--result-file F]
    ecadlib altium-menu fill BOARD.PrjPcb|BOM.csv [--workspace W] [--to DIR] [--result-file F]
    ecadlib altium-menu queue [--workspace W] [--port 8765] [--result-file F]
    ecadlib altium-menu run-job JOBFOLDER          what the menu runs: reads JOBFOLDER/request.ini, writes result.ini

`build` writes into --out:
  ecadlib_menu.PrjScr + ecadlib_menu.pas   the script project; three procedures without parameters (so Altium's
                                    Customize dialog and File > Run Script list them):
        AddPartFromMPN     asks for an MPN (+ optional manufacturer), builds the part, reinstalls the library
        CheckProjectBOM    checks the focused project's BOM, opens the report, offers to fill the library
        OpenReviewQueue    opens the ecadlib web UI at the review queue (starts `ecadlib ui` when it is not running)
  ecadlib_menu.ini                  settings: workspace, library folder, command, hidden, timeout_s, port (kept when
                                    it exists, so your edits survive a rebuild). On Windows, command= is the Python that
                                    ran `build` (a venv's python.exe too): "<sys.executable>" -m ecadlib, unless --command
                                    is given; a rebuild fills an empty command= the same way and changes nothing else
  ecadlib_run.cmd + ecadlib_run.vbs how the script runs ecadlib: the .vbs starts the .cmd without a console window
                                    (WScript.Shell.Run, window style 0) and the .cmd finds ecadlib (command= in the
                                    .ini, else `ecadlib` on PATH, else `py -3 -m ecadlib`, else `python -m ecadlib`;
                                    a venv install is found only through command=)
  ecadlib_menu.DXPPrf               optional: a preferences file that adds the "ecadlib" menu to the schematic, PCB,
                                    SchLib and PcbLib editors (DXP > Preferences > Load, "System > Customizations"
                                    only). Written only when the Windows folder is known (--win-dir, or running on
                                    Windows): Altium's menu entries need the script's absolute path.
  ALTIUM_MENU_README.txt            the install steps

How a menu entry talks to ecadlib (no stdout parsing, no quoting of user text on a command line):
  1. the script makes jobs\\<id>\\, writes request.ini there ([request] action=add-part, mpn=..., config=...);
  2. RunApplication('wscript.exe //B //NoLogo "<dir>\\ecadlib_run.vbs" "<dir>\\ecadlib_run.cmd" "<job>"')
     (or `cmd.exe /s /c ""<dir>\\ecadlib_run.cmd" "<job>""` with hidden=0), which runs
     `ecadlib altium-menu run-job "<job>"` > log.txt and then writes exit.txt;
  3. the script waits for exit.txt (Sleep + FileExists, as Altium-Designer-addons' XIA_Utils.pas waits for its
     svn runs) and reads result.ini ([result] status, message, fix, install, offer_fill, offer, report, url) with
     TIniFile. result.ini is UTF-16 with a BOM, which Windows' profile API (behind TIniFile) reads in any code page.
  4. install= lists the DbLib(s) to reinstall: the script uninstalls/installs each with IntegratedLibraryManager and
     runs IntegratedLibrary:RefreshInstalledLibraries (AllLibraries=True).

Where parts go: the workspace is an ordinary ecadlib workspace (`ecadlib ui --workspace` shows it); parts added from
the menu collect in <workspace>/uploads/altium-menu/parts.csv and the librarian runs on that list (finished parts are
kept). Ready parts are synced into the library folder (`ecadlib altium-pkg`: DbLib + SchLib/PcbLib + LibPkg), or,
when the rules name an Altium 365 workspace ([altium365] workspace), released there (`altium365 push`; NEXAR_TOKEN in
the environment): the Components panel then shows them after a refresh. Held parts are never published.

Never run inside Altium here: the script is written from Altium's scripting references (TR0120 DelphiScript,
TR0124 Server Process, TR0135 System, TR0136 Integrated Library, TR0140 Workspace Manager; 2008 PDFs kept in
github.com/Altium-Designer-addons/scripting-reference) and public scripts; see ALTIUM_API for each call it uses."""
import argparse, configparser, csv, datetime, html, io, json, os, re, subprocess, sys, time, zipfile
from pathlib import Path, PureWindowsPath

from . import __version__

PROJECT, UNIT, INI = "ecadlib_menu.PrjScr", "ecadlib_menu.pas", "ecadlib_menu.ini"
RUN_CMD, RUN_VBS, PREFS, README = "ecadlib_run.cmd", "ecadlib_run.vbs", "ecadlib_menu.DXPPrf", "ALTIUM_MENU_README.txt"
PROCS = [("AddPartFromMPN", "&Add part from MPN...", "Build a part from its manufacturer part number with ecadlib"),
         ("CheckProjectBOM", "&Check this project BOM", "Check the focused project's BOM against the library; offer to fill it"),
         ("OpenReviewQueue", "Open &review queue", "Open the ecadlib web UI at the review queue")]
MENU_BOM = "uploads/altium-menu/parts.csv"
HEADER = ["Designator", "Qty", "Value", "Footprint", "Manufacturer", "Mfr. Part #", "LCSC Part Number", "Description", "Datasheet"]
ACTIONS = ("add-part", "check-bom", "fill", "queue")
QUEUE_ANCHOR = "#queuecard"

# Every identifier the .pas calls from Altium / DelphiScript, and where it is documented. TRxxxx = Altium's scripting
# reference PDFs (page numbers of github.com/Altium-Designer-addons/scripting-reference copies); "used in" = public
# Altium scripts that call it (github.com/Altium-Designer-addons/scripts-libraries, BrettLMiller/Altium-DelphiScripts),
# for the few calls the 2008 references do not list. tests/test_altium_menu.py checks the .pas uses nothing else.
ALTIUM_API = {
    "RunApplication": "TR0135 System Reference p150 (ClientAPIReg: Function RunApplication(CommandLine) : Integer); TR0120 p18",
    "ShowMessage": "TR0120 DelphiScript Reference p3",
    "ShowError": "TR0120 p18 (Useful Dialogs)",
    "ConfirmNoYes": "TR0120 p18 (Useful Dialogs); TR0135 p136",
    "InputBox": "not in the 2008 references; used in BrettLMiller/Altium-DelphiScripts PCB/PolygonReFitBO.pas (InputBox(caption, prompt, default))",
    "GetRunningScriptProjectName": "not in the 2008 references; used in Altium-Designer-addons scripts (4 files) and ecadlib altium-pkg's install script",
    "GetWorkspace": "TR0140 Workspace Manager API p2",
    "DM_FocusedProject": "TR0140 p57 (IWorkspace.DM_FocusedProject : IProject)",
    "DM_ProjectFullPath": "TR0140 p28 (IProject.DM_ProjectFullPath)",
    "DM_ProjectFileName": "TR0140 p28; 'Free Documents' check as in Altium-Designer-addons IncrementDesignators.pas",
    "IntegratedLibraryManager": "TR0136 Integrated Library API p2",
    "InstallLibrary": "TR0136 p2 (IIntegratedLibraryManager.InstallLibrary)",
    "UninstallLibrary": "TR0136 p4",
    "InstalledLibraryPath": "TR0136 p4",
    "InstalledLibraryCount": "TR0136 p19 (IIntegratedLibraryManager.InstalledLibraryCount: the Installed tab only; AvailableLibraryCount also counts project and search-path libraries, which InstalledLibraryPath cannot index)",
    "ResetParameters": "TR0120 p18 (Extension Routines); TR0135 p143",
    "AddStringParameter": "TR0135 p143",
    "RunProcess": "TR0120 p18; TR0135 p143; process IntegratedLibrary:RefreshInstalledLibraries (AllLibraries=True) in TR0124 Server Process Reference p19",
    "TIniFile": "TR0135 p191 (Helper classes: Create, ReadString, ReadInteger, WriteString)",
    "ReadString": "TR0135 p191 (TIniFile)",
    "ReadInteger": "TR0135 p191 (TIniFile)",
    "WriteString": "TR0135 p191 (TIniFile)",
    "TStringList": "TR0135 p191 (Helper classes); TR0120 p10",
    "SaveToFile": "TR0135 p196 (TStringList)",
    "Add": "TStringList.Add, TR0120 p10",
    "Create": "constructor of TIniFile / TStringList, TR0135 p191",
    "Free": "destructor, TR0120 p10",
    "FileExists": "TR0135 p143",
    "DirectoryExists": "TR0135 p145",
    "MkDir": "TR0120 p16 (File IO Routines)",
    "ExtractFilePath": "TR0136 p29",
    "Now": "TR0120 p8",
    "FormatDateTime": "not in the 2008 references; used in Altium-Designer-addons XIA_Generate_Sorted_Multiwire_Netlist.pas (FormatDateTime('yyyy-mm-dd_hh_nn_ss', Now()))",
    "Random": "TR0120 p17 (Math Routines)",
    "Randomize": "TR0120 p17 (Math Routines)",
    "IntToStr": "TR0120 p6",
    "Trim": "TR0120 p17 (String Routines)",
    "Pos": "TR0120 p17 (String Routines)",
    "Copy": "TR0120 p17 (String Routines)",
    "Length": "TR0120 p17 (String Routines)",
    "UpperCase": "TR0120 p17 (String Routines)",
    "Sleep": "not in the 2008 references; used in Altium-Designer-addons XIA_Utils.pas (waits for an external run's result file) and QuickSilk.pas",
    "Application": "Application.ProcessMessages: not in the 2008 references; used in Altium-Designer-addons AutoPlaceSilkscreen.pas and AssemblyTextPrep.pas",
    "ProcessMessages": "see Application",
}


# ------------------------------------------------------------------ the script project
def pas_text():
    """The DelphiScript unit (CRLF). Static: everything that differs per install is in ecadlib_menu.ini."""
    return r"""{ ecadlib menu for Altium Designer (written by `ecadlib altium-menu build`, ecadlib @VERSION@).

  Three entries, each a procedure without parameters (Customize > [Scripts] and File > Run Script list them):
    AddPartFromMPN    asks for a manufacturer part number, builds it with ecadlib into your library folder (or
                      Altium 365 when ecadlib.toml names a workspace) and reinstalls the database library
    CheckProjectBOM   checks the BOM of the focused project, opens the report and offers to fill the library
    OpenReviewQueue   opens the ecadlib web UI at the review queue (starts it when it is not running)

  How: each entry writes jobs\<id>\request.ini next to this script, runs ecadlib_run.cmd (without a console window
  through ecadlib_run.vbs unless hidden=0 in ecadlib_menu.ini), waits for jobs\<id>\exit.txt and reads
  jobs\<id>\result.ini, which `ecadlib altium-menu run-job` writes. Settings: ecadlib_menu.ini. No keys or passwords
  are in these files: API keys stay in your environment variables. }

Function Here : String;
Begin
    Result := ExtractFilePath(GetRunningScriptProjectName);
End;

Function Setting(Key : String; Fallback : String) : String;
Var
    Ini : TIniFile;
Begin
    Ini := TIniFile.Create(Here + 'ecadlib_menu.ini');
    Result := Ini.ReadString('ecadlib', Key, Fallback);
    Ini.Free;
End;

Function SettingNumber(Key : String; Fallback : Integer) : Integer;
Var
    Ini : TIniFile;
Begin
    Ini := TIniFile.Create(Here + 'ecadlib_menu.ini');
    Result := Ini.ReadInteger('ecadlib', Key, Fallback);
    Ini.Free;
End;

Procedure Ask(Job : String; Key : String; Value : String);
Var
    Ini : TIniFile;
Begin
    Ini := TIniFile.Create(Job + '\request.ini');
    Ini.WriteString('request', Key, Value);
    Ini.Free;
End;

Function Answer(Job : String; Key : String) : String;
Var
    Ini : TIniFile;
Begin
    Ini := TIniFile.Create(Job + '\result.ini');
    Result := Ini.ReadString('result', Key, '');
    Ini.Free;
End;

Function StartJob(Action : String) : String;
Var
    Cmd : String;
    L : TStringList;
Begin
    If Not DirectoryExists(Here + 'jobs') Then
        MkDir(Here + 'jobs');
    Randomize;
    Result := Here + 'jobs\' + FormatDateTime('yyyymmdd-hhnnss', Now) + '-' + IntToStr(Random(100000));
    MkDir(Result);
    Ask(Result, 'action', Action);
    Ask(Result, 'config', Here + 'ecadlib_menu.ini');
    Cmd := Trim(Setting('command', ''));
    If Cmd <> '' Then
    Begin
        L := TStringList.Create;
        L.Add(Cmd);
        L.SaveToFile(Result + '\command.txt');
        L.Free;
    End;
End;

Function CommandLine(Job : String) : String;
Begin
    If Setting('hidden', '1') = '0' Then
        Result := 'cmd.exe /s /c ""' + Here + 'ecadlib_run.cmd" "' + Job + '""'
    Else
        Result := 'wscript.exe //B //NoLogo "' + Here + 'ecadlib_run.vbs" "' + Here + 'ecadlib_run.cmd" "' + Job + '"';
End;

Function RunJob(Job : String; Title : String) : String;
{ Runs the job and waits for it. Returns '' when ecadlib wrote result.ini, else the problem in one line. }
Var
    Code : Integer;
    Waited : Integer;
    Limit : Integer;
    Cmd : String;
Begin
    Result := '';
    Limit := SettingNumber('timeout_s', 1800) * 1000;
    Cmd := CommandLine(Job);
    Code := RunApplication(Cmd);
    If Code <> 0 Then
    Begin
        Result := 'Windows could not start ecadlib (error ' + IntToStr(Code) + ').' + #13#10 + 'Next: set hidden=0 in ' + Here + 'ecadlib_menu.ini and try again.';
        Exit;
    End;
    Waited := 0;
    While Not FileExists(Job + '\exit.txt') Do
    Begin
        Sleep(200);
        Application.ProcessMessages;
        Waited := Waited + 200;
        If Waited >= Limit Then
        Begin
            If ConfirmNoYes(Title + ': ecadlib is still working. Keep waiting?') Then
                Waited := 0
            Else
            Begin
                Result := 'Stopped waiting. ecadlib may still finish in the background.' + #13#10 + 'Next: look at its log, ' + Job + '\log.txt';
                Exit;
            End;
        End;
    End;
    Sleep(300);
    If FileExists(Job + '\result.ini') Then
        Exit;
    If FileExists(Job + '\notfound.txt') Then
        Result := 'ecadlib is not installed on this PC (not on PATH, and no Python can import it).' + #13#10 + 'Next: pip install ecadlib, or set command= in ' + Here + 'ecadlib_menu.ini'
    Else
        Result := 'ecadlib stopped without a result.' + #13#10 + 'Next: look at its log, ' + Job + '\log.txt';
End;

Function IsInstalled(Path : String) : Boolean;
Var
    i : Integer;
Begin
    Result := False;
    For i := 0 To IntegratedLibraryManager.InstalledLibraryCount - 1 Do
        If UpperCase(IntegratedLibraryManager.InstalledLibraryPath(i)) = UpperCase(Path) Then
            Result := True;
End;

Function Reinstall(Job : String) : String;
{ Uninstalls and installs again every library result.ini names in install= (';' between paths), then refreshes. }
Var
    List : String;
    P : String;
    i : Integer;
Begin
    Result := '';
    List := Answer(Job, 'install');
    While List <> '' Do
    Begin
        i := Pos(';', List);
        If i = 0 Then
        Begin
            P := List;
            List := '';
        End
        Else
        Begin
            P := Copy(List, 1, i - 1);
            List := Copy(List, i + 1, Length(List));
        End;
        If P <> '' Then
        Begin
            If FileExists(P) Then
            Begin
                If IsInstalled(P) Then
                    IntegratedLibraryManager.UninstallLibrary(P);
                IntegratedLibraryManager.InstallLibrary(P);
                Result := Result + #13#10 + 'Installed again: ' + P;
            End
            Else
                Result := Result + #13#10 + 'Missing: ' + P;
        End;
    End;
    ResetParameters;
    AddStringParameter('AllLibraries', 'True');
    RunProcess('IntegratedLibrary:RefreshInstalledLibraries');
End;

Function MessageText(Job : String; Extra : String) : String;
Begin
    Result := Answer(Job, 'message');
    If Answer(Job, 'fix') <> '' Then
        Result := Result + #13#10 + #13#10 + 'Next: ' + Answer(Job, 'fix');
    If Extra <> '' Then
        Result := Result + #13#10 + Extra;
End;

Procedure ShowResult(Title : String; Job : String; Extra : String);
Begin
    If Answer(Job, 'status') = 'error' Then
        ShowError(Title + #13#10 + #13#10 + MessageText(Job, Extra))
    Else
        ShowMessage(Title + #13#10 + #13#10 + MessageText(Job, Extra));
End;

Function Publish(Job : String) : String;
Begin
    Result := '';
    If Answer(Job, 'install') <> '' Then
        Result := Reinstall(Job);
End;

Procedure AddPartFromMPN;
Var
    MPN : String;
    Maker : String;
    Job : String;
    Problem : String;
    Title : String;
Begin
    MPN := Trim(InputBox('ecadlib: add part', 'Manufacturer part number:', ''));
    If MPN = '' Then
        Exit;
    Maker := Trim(InputBox('ecadlib: add part', 'Manufacturer of ' + MPN + ' (optional, leave empty if unsure):', ''));
    Title := 'ecadlib: add ' + MPN;
    Job := StartJob('add-part');
    Ask(Job, 'mpn', MPN);
    Ask(Job, 'manufacturer', Maker);
    Problem := RunJob(Job, Title);
    If Problem <> '' Then
    Begin
        ShowError(Title + #13#10 + #13#10 + Problem);
        Exit;
    End;
    ShowResult(Title, Job, Publish(Job));
End;

Procedure CheckProjectBOM;
Var
    Prj : IProject;
    PrjPath : String;
    Job : String;
    Problem : String;
    Title : String;
Begin
    Title := 'ecadlib: check this project BOM';
    Prj := GetWorkspace.DM_FocusedProject;
    If Prj = Nil Then
    Begin
        ShowError(Title + #13#10 + #13#10 + 'Open a PCB project first (click it in the Projects panel).');
        Exit;
    End;
    If Prj.DM_ProjectFileName = 'Free Documents' Then
    Begin
        ShowError(Title + #13#10 + #13#10 + 'Open a PCB project first (click it in the Projects panel).');
        Exit;
    End;
    PrjPath := Prj.DM_ProjectFullPath;
    Job := StartJob('check-bom');
    Ask(Job, 'project', PrjPath);
    Problem := RunJob(Job, Title);
    If Problem <> '' Then
    Begin
        ShowError(Title + #13#10 + #13#10 + Problem);
        Exit;
    End;
    If Answer(Job, 'offer_fill') <> '1' Then
    Begin
        ShowResult(Title, Job, '');
        Exit;
    End;
    If Not ConfirmNoYes(MessageText(Job, '') + #13#10 + #13#10 + Answer(Job, 'offer')) Then
        Exit;
    Title := 'ecadlib: fill the library';
    Job := StartJob('fill');
    Ask(Job, 'project', PrjPath);
    Problem := RunJob(Job, Title);
    If Problem <> '' Then
    Begin
        ShowError(Title + #13#10 + #13#10 + Problem);
        Exit;
    End;
    ShowResult(Title, Job, Publish(Job));
End;

Procedure OpenReviewQueue;
Var
    Job : String;
    Problem : String;
    Title : String;
Begin
    Title := 'ecadlib: review queue';
    Job := StartJob('queue');
    Problem := RunJob(Job, Title);
    If Problem <> '' Then
    Begin
        ShowError(Title + #13#10 + #13#10 + Problem);
        Exit;
    End;
    If Answer(Job, 'status') <> 'ok' Then
        ShowResult(Title, Job, '');
End;
""".replace("@VERSION@", __version__).replace("\n", "\r\n")


def run_cmd_text():
    """ecadlib_run.cmd JOBFOLDER: find ecadlib, run the job, write exit.txt (and notfound.txt when there is none)."""
    return r"""@echo off
rem ecadlib menu runner (written by `ecadlib altium-menu build`). Usage: ecadlib_run.cmd JOBFOLDER
rem Finds ecadlib: command.txt in the job (command= in ecadlib_menu.ini), else ecadlib on PATH, else py -3 / python
rem that can import it. Runs `ecadlib altium-menu run-job JOBFOLDER` (which writes result.ini), then exit.txt.
setlocal
set "JOB=%~1"
set "ECADLIB="
if exist "%JOB%\command.txt" set /p ECADLIB=<"%JOB%\command.txt"
if defined ECADLIB goto run
where ecadlib >nul 2>nul && set "ECADLIB=ecadlib"
if defined ECADLIB goto run
py -3 -c "import ecadlib" >nul 2>nul && set "ECADLIB=py -3 -m ecadlib"
if defined ECADLIB goto run
python -c "import ecadlib" >nul 2>nul && set "ECADLIB=python -m ecadlib"
if defined ECADLIB goto run
> "%JOB%\log.txt" echo ecadlib was not found: not on PATH and no Python that can import it.
> "%JOB%\notfound.txt" echo 9009
> "%JOB%\exit.txt" echo 9009
exit /b 9009
:run
%ECADLIB% altium-menu run-job "%JOB%" > "%JOB%\log.txt" 2>&1
> "%JOB%\exit.txt" echo %ERRORLEVEL%
exit /b 0
""".replace("\n", "\r\n")


def run_vbs_text():
    """wscript ecadlib_run.vbs RUNNER.cmd JOBFOLDER: run the .cmd with no window (style 0) and wait for it."""
    return """' ecadlib menu: runs ecadlib_run.cmd without a console window and waits (written by `ecadlib altium-menu build`).
' Usage: wscript.exe //B //NoLogo ecadlib_run.vbs RUNNER.cmd JOBFOLDER
q = Chr(34)
line = "cmd.exe /s /c " & q & q & WScript.Arguments(0) & q & " " & q & WScript.Arguments(1) & q & q
CreateObject("WScript.Shell").Run line, 0, True
""".replace("\n", "\r\n")


def vbs_line(runner, job):
    """The command line ecadlib_run.vbs hands to WScript.Shell.Run (Python mirror of its one expression, for tests)."""
    q = '"'
    return "cmd.exe /s /c " + q + q + runner + q + " " + q + job + q + q


def script_command_line(here, job, hidden=True):
    """The command line the .pas passes to RunApplication (Python mirror of CommandLine(), for tests). `here` ends
    with a backslash (ExtractFilePath); `job` does not."""
    if not hidden:
        return f'cmd.exe /s /c ""{here}{RUN_CMD}" "{job}""'
    return f'wscript.exe //B //NoLogo "{here}{RUN_VBS}" "{here}{RUN_CMD}" "{job}"'


def ini_text(workspace="workspace", library="library", name="ecadlib", command="", hidden=True, timeout_s=1800,
             port=8765, offline=False):
    return f"""; ecadlib menu settings (read by ecadlib_menu.pas and `ecadlib altium-menu run-job`).
; Relative paths start at this folder. Edit freely: `ecadlib altium-menu build` keeps this file.
[ecadlib]
; the ecadlib workspace: parts added from the menu, reports, the review queue (`ecadlib ui --workspace` it)
workspace={workspace}
; the Altium library folder ready parts are synced into (DbLib + SchLib\\ + PcbLib\\ + .LibPkg); not used when
; the workspace's ecadlib.toml names an Altium 365 workspace ([altium365] workspace)
library={library}
; library name (the .DbLib / .LibPkg file name)
name={name}
; how to run ecadlib. `ecadlib altium-menu build` on Windows writes the Python it ran with (a venv's too).
; empty = ecadlib on PATH, else py -3 -m ecadlib, else python -m ecadlib (a venv install is not found that way)
; example: command="C:\\Program Files\\Python311\\python.exe" -m ecadlib
command={command}
; 1 = no console window (wscript + ecadlib_run.vbs), 0 = run ecadlib_run.cmd in a console window
hidden={1 if hidden else 0}
; seconds to wait before asking whether to keep waiting
timeout_s={int(timeout_s)}
; port of the local web UI (Open review queue)
port={int(port)}
; 1 = use only cached downloads and built-in standard parts (no network)
offline={1 if offline else 0}
""".replace("\n", "\r\n")


# ------------------------------------------------------------------ the optional menu (DXP.RCS in a .DXPPrf)
# Menus to extend and the item to insert before (their Window menu), as Altium-Designer-addons' Menu_SVN DXP.RCS does.
MENUS = [("SchDoc", "MNSchematicMenu", "MNDesignMenu_Window1_3", "SchCustom"),
         ("PcbDoc", "MNPCBMenu", "MNDesignMenu_Window1_1", "PCBCustom"),
         ("SchLib", "MNSchLibMenu", "MNDesignMenu_Window1_4", "SchLibCustom"),
         ("PcbLib", "MNPCBLibMenu", "MNDesignMenu_Window1_2", "PCBLibCustom")]


def check_win_dir(win_dir):
    w = str(win_dir).rstrip("\\/")
    if not PureWindowsPath(w).is_absolute() or not re.match(r"^([A-Za-z]:\\|\\\\)", w.replace("/", "\\")):
        raise ValueError(f"--win-dir {win_dir!r}: give the absolute Windows folder the menu files will be in (D:\\ecadlib-menu)")
    if any(c in w for c in "'|\"\r\n"):
        raise ValueError(f"--win-dir {win_dir!r}: a menu entry cannot hold ' | or \" in its path; use another folder")
    return w.replace("/", "\\")


def rcs_text(win_dir):
    """DXP.RCS that adds an "ecadlib" menu (format of Altium-Designer-addons UI customizations/Menu_SVN/DXP.RCS)."""
    w = check_win_dir(win_dir)
    L = []
    for proc, caption, desc in PROCS:
        L.append(f"PL ecadlib_{proc} Command='ScriptingSystem:RunScript' Params='ProjectName={w}\\{PROJECT}|ProcName="
                 f"{w}\\{UNIT}>{proc}' Caption='{caption}' Description='{desc.replace(chr(39), '')}' DefaultChecked=0  End")
    L.append("")
    for kind, target, ref, _ in MENUS:
        L += [f"Insertion {kind}_ecadlib_Menu TargetID='{target}' RefID0='{ref}' ",
              f"    Tree {kind}_ecadlib_Tree Caption='&ecadlib' Popup='N' "]
        L += [f"        Link {kind}_ecadlib_{proc} PLID='ecadlib_{proc}'          End" for proc, _, _ in PROCS]
        L += ["    End", "End"]
    L.append("")
    for kind, _, _, custom in MENUS:
        L.append(f"Tree {custom} Caption='[Custom]' TopLevel='True' ")
        L += [f"    Link {custom}_ecadlib_{proc} PLID='ecadlib_{proc}'          End" for proc, _, _ in PROCS]
        L.append("End")
    return "\r\n".join(L) + "\r\n"


def dxpprf_bytes(win_dir):
    """A preferences file (zip of DXP.RCS + IncludedFiles.ini, as Menu_SVN.DXPPrf). Fixed timestamps: same bytes each run."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data, comp in (("DXP.RCS", rcs_text(win_dir).encode("ascii"), zipfile.ZIP_DEFLATED),
                                 ("IncludedFiles.ini", b"[WorkspaceManager:Customizations]\r\nImportMode=0\r\nFileCount=1\r\n"
                                                       b"FileName0=DXP.RCS\r\n", zipfile.ZIP_STORED)):
            zi = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            zi.compress_type = comp
            z.writestr(zi, data)
    return buf.getvalue()


def readme_text(win_dir, prefs):
    w = win_dir or "<this folder>"
    menu = (f"""   Or load the ready-made menu: DXP (or the gear icon) > Preferences > Load... (bottom left) > {PREFS}; tick only
   "System > Customizations", OK, restart Altium. CAUTION: this loads a customisation file, which may replace
   menus you customised yourself (back them up first with Preferences > Save...). It names {w} in each entry: build
   it again with --win-dir if you move this folder.""" if prefs else
            f"""   (No {PREFS} here: the Windows folder was not known. Run `ecadlib altium-menu build --win-dir {w}` on
   this PC to get the ready-made menu file.)""")
    return f"""ecadlib menu for Altium Designer (written by `ecadlib altium-menu build`, ecadlib {__version__})

What it adds: an "ecadlib" menu (or toolbar buttons) with three entries:
  Add part from MPN        asks for a manufacturer part number, builds the part, reinstalls the library
  Check this project BOM   checks the focused project's BOM, opens the report, offers to fill the library
  Open review queue        opens the ecadlib web UI at the review queue in your browser

Needs: ecadlib installed on this PC (pip install ecadlib; `ecadlib doctor --altium-menu {INI}` checks it). Settings
are in {INI} (workspace, library folder, how to run ecadlib). Your API keys stay in environment variables; no file
here holds one.

How the menu runs ecadlib: command= in {INI}. `ecadlib altium-menu build` run on Windows writes the Python it ran
with there ("C:\\...\\python.exe" -m ecadlib), so an install in a virtual environment (venv) works. When command=
is empty, the runner tries `ecadlib` on PATH, then `py -3 -m ecadlib`, then `python -m ecadlib`; a venv is none of
those, and the menu then says "ecadlib was not found". Fix: run the build again from the venv (it fills an empty
command= and keeps your other settings), or set command= yourself.

Without an AI key (ANTHROPIC_API_KEY, or the model set in ecadlib.toml [ai]), ecadlib cannot read datasheets, so
parts no free source fully describes come back held with the reason ("fields: Description is empty", "footprint:
..."). Held parts never go into the library: open the review queue (ecadlib > Open review queue) to see why and
pick or fill what is missing, or set the key (Windows: setx ANTHROPIC_API_KEY "<your key>" in a terminal, then
restart Altium so it sees the new variable) and add the part again.

Install (once):
1. Altium: DXP (AD17) or the gear icon (AD18+) > Preferences > Scripting System > Global Projects > Install from
   file... > {w}\\{PROJECT}. The three procedures now appear in File > Run Script... under {PROJECT}.
2. Menu: right-click any menu or toolbar > Customize... > Categories: [Scripts] (or [Custom]) > drag
   {UNIT}>AddPartFromMPN, >CheckProjectBOM and >OpenReviewQueue onto a menu or toolbar; rename them in the entry's
   properties. Repeat for each editor you want them in (schematic, PCB, library editors).
{menu}
3. Try it: open a PCB project and pick ecadlib > Check this project BOM.

What happens on a click: the script writes jobs\\<id>\\request.ini next to it, runs {RUN_CMD} without a console
window (through {RUN_VBS}; set hidden=0 in {INI} to see the console), waits, and reads jobs\\<id>\\result.ini.
If something fails, the message names the fix and jobs\\<id>\\log.txt has ecadlib's output.

This script was written from Altium's scripting reference and public scripts and was never run inside Altium when it
was made: if a step fails, please report the message.
""".replace("\n", "\r\n")


def _short_path(p):
    """Windows 8.3 short form of an existing path (ASCII even when the long one is not), else p."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, len(buf))
        return buf.value if 0 < n < len(buf) else p
    except (AttributeError, OSError, ValueError):
        return p


def default_command(executable=None, nt=None):
    """command= for this PC: the Python running ecadlib now (a venv's python.exe too) with -m ecadlib, the path quoted
    for cmd.exe ("C:\\venv\\Scripts\\python.exe" -m ecadlib). '' when not on Windows (the menu runs on the Altium PC,
    where this Python's path means nothing), or when the path cannot be written in plain ASCII or holds a quote."""
    nt = os.name == "nt" if nt is None else nt
    exe = str(sys.executable if executable is None else executable or "")
    if not nt or not exe:
        return ""
    if not exe.isascii():                        # the .ini is read in the ANSI code page: use the 8.3 name
        exe = _short_path(exe)
    if not exe.isascii() or '"' in exe:
        return ""
    return f'"{exe}" -m ecadlib'


def _fill_command(ini, command):
    """Set command= in an existing ecadlib_menu.ini when it is empty (nothing else changes). True when it did."""
    data = ini.read_bytes()
    new = re.sub(rb"(?m)^command=[ \t]*(?=\r?$)", lambda m: b"command=" + command.encode("utf-8"), data, count=1)
    if new == data:
        return False
    ini.write_bytes(new)
    return True


def build(out, workspace=None, library=None, name="ecadlib", command=None, win_dir=None, port=8765, hidden=True,
          timeout_s=1800, offline=False):
    """Write the script project into `out`. Returns a report dict (also altium_menu.json). command None or '': on
    Windows, the Python running this (default_command()); elsewhere empty (the runner then searches PATH / py / python)."""
    out = Path(out)
    command = command or default_command()
    out.mkdir(parents=True, exist_ok=True)
    if win_dir is None and os.name == "nt":
        win_dir = str(out.resolve())
    if win_dir:
        win_dir = check_win_dir(win_dir)
    if name and not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$", name):
        raise ValueError(f"--name {name!r}: letters, digits, _ . - only")
    from .altium_pkg import prjscr_text
    files = {PROJECT: prjscr_text(UNIT), UNIT: pas_text(), RUN_CMD: run_cmd_text(), RUN_VBS: run_vbs_text(),
             README: readme_text(win_dir, bool(win_dir))}
    written, kept = [], []
    for fname, text in files.items():
        data = text.encode("utf-8")
        p = out / fname
        if not p.is_file() or p.read_bytes() != data:
            p.write_bytes(data)
            written.append(fname)
    ini = out / INI
    filled = False
    if ini.is_file():
        kept.append(INI)
        filled = bool(command) and _fill_command(ini, command)     # an empty command= (older builds) gets this PC's
    else:
        ini.write_bytes(ini_text(workspace or "workspace", library or "library", name, command, hidden, timeout_s,
                                 port, offline).encode("utf-8"))
        written.append(INI)
    if win_dir:
        data = dxpprf_bytes(win_dir)
        if not (out / PREFS).is_file() or (out / PREFS).read_bytes() != data:
            (out / PREFS).write_bytes(data)
            written.append(PREFS)
    problems = pas_problems(files[UNIT])
    rep = {"folder": str(out), "project": PROJECT, "procedures": [p for p, _, _ in PROCS], "written": written,
           "kept": kept, "win_dir": win_dir, "menu_file": PREFS if win_dir else None, "problems": problems,
           "command": load_command(ini), "command_filled": filled}
    (out / "altium_menu.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    return rep


# ------------------------------------------------------------------ a small Pascal check (also used by the tests)
PAS_KEYWORDS = {"begin", "end", "var", "const", "function", "procedure", "if", "then", "else", "while", "do", "for",
                "to", "downto", "repeat", "until", "not", "and", "or", "div", "mod", "result", "exit", "break", "true",
                "false", "nil", "try", "finally", "except", "string", "integer", "boolean", "case", "of"}
PAS_TYPES = {"tinifile", "tstringlist", "iproject"}


def pas_tokens(text):
    """[(kind, value, line)] for DelphiScript: comments { } (* *) //, strings '..'' ..', #13 char codes, words,
    numbers, symbols. Raises ValueError on an unterminated comment or string."""
    toks, i, line, n = [], 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "{":
            j = text.find("}", i)
            if j < 0:
                raise ValueError(f"line {line}: unterminated {{ comment")
            line += text.count("\n", i, j)
            i = j + 1
        elif text.startswith("(*", i):
            j = text.find("*)", i)
            if j < 0:
                raise ValueError(f"line {line}: unterminated (* comment")
            line += text.count("\n", i, j)
            i = j + 2
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "'":
            j, s = i + 1, ""
            while True:
                k = text.find("'", j)
                if k < 0 or "\n" in text[j:k]:
                    raise ValueError(f"line {line}: unterminated string")
                s += text[j:k]
                if text.startswith("''", k):
                    s += "'"
                    j = k + 2
                    continue
                i = k + 1
                break
            toks.append(("str", s, line))
        elif c == "#":
            m = re.match(r"#\d+", text[i:])
            if not m:
                raise ValueError(f"line {line}: bad character code")
            toks.append(("str", chr(int(m.group(0)[1:])), line))
            i += len(m.group(0))
        elif c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i:])
            toks.append(("word", m.group(0), line))
            i += len(m.group(0))
        elif c.isdigit():
            m = re.match(r"\d+(\.\d+)?", text[i:])
            toks.append(("num", m.group(0), line))
            i += len(m.group(0))
        else:
            two = text[i:i + 2]
            if two in (":=", "<>", "<=", ">="):
                toks.append(("sym", two, line))
                i += 2
            else:
                toks.append(("sym", c, line))
                i += 1
    return toks


def pas_routines(toks):
    """{name: {"params", "vars", "body": [tokens], "kind"}} for each top-level Function / Procedure."""
    out, i = {}, 0
    low = lambda t: t[1].lower() if t[0] == "word" else None
    while i < len(toks):
        if low(toks[i]) in ("function", "procedure"):
            kind, name = low(toks[i]), toks[i + 1][1]
            j, params = i + 2, set()
            if toks[j][1] == "(":
                k = j
                while toks[k][1] != ")":
                    if toks[k][0] == "word" and toks[k + 1][1] in (":", ",") and toks[k - 1][1] in ("(", ";", ","):
                        params.add(toks[k][1].lower())
                    k += 1
                j = k + 1
            while toks[j][1] != ";":
                j += 1
            j += 1
            vars_ = set()
            if low(toks[j]) == "var":
                j += 1
                while low(toks[j]) != "begin":
                    names = []
                    while toks[j][1] != ":":
                        if toks[j][0] == "word":
                            names.append(toks[j][1].lower())
                        j += 1
                    vars_.update(names)
                    while toks[j][1] != ";":
                        j += 1
                    j += 1
            if low(toks[j]) != "begin":
                raise ValueError(f"line {toks[j][2]}: {name}: expected Begin")
            depth, k = 0, j
            while True:
                w = low(toks[k])
                if w in ("begin", "try", "case"):
                    depth += 1
                elif w == "end":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
                if k >= len(toks):
                    raise ValueError(f"{name}: Begin without End")
            if toks[k + 1][1] != ";":
                raise ValueError(f"line {toks[k][2]}: {name}: End without ;")
            out[name] = {"kind": kind, "params": params, "vars": vars_, "body": toks[j:k + 1]}
            i = k + 2
        else:
            raise ValueError(f"line {toks[i][2]}: {toks[i][1]!r} outside a Function / Procedure")
    return out


def pas_problems(text, api=None):
    """What a reader of the .pas would object to: unbalanced Begin/End, an identifier that is neither declared here
    (routine, parameter, local variable) nor in the documented allow-list (ALTIUM_API), a Function without a Result,
    a procedure the menu needs that is missing or takes parameters. [] = fine."""
    api = {k.lower() for k in (api or ALTIUM_API)}
    try:
        routines = pas_routines(pas_tokens(text))
    except (ValueError, IndexError) as e:
        return [f"does not parse: {e}"]
    probs = []
    known = {n.lower() for n in routines}
    for name, r in routines.items():
        local = r["params"] | r["vars"]
        for idx, (kind, val, line) in enumerate(r["body"]):
            if kind != "word":
                continue
            w = val.lower()
            if w in PAS_KEYWORDS or w in PAS_TYPES or w in known or w in local or w in api:
                continue
            probs.append(f"line {line}: {name}: {val} is not declared and not in the API allow-list")
        if r["kind"] == "function" and not any(t[0] == "word" and t[1].lower() == "result" for t in r["body"]):
            probs.append(f"{name}: a Function that never sets Result")
    for proc, _, _ in PROCS:
        if proc not in routines:
            probs.append(f"{proc}: missing")
        elif routines[proc]["params"] or routines[proc]["kind"] != "procedure":
            probs.append(f"{proc}: must be a Procedure without parameters (Altium's menus cannot pass any)")
    return probs


# ------------------------------------------------------------------ files the script and ecadlib exchange
def read_ini(path):
    """configparser of an INI the script wrote with TIniFile (Windows' profile API: UTF-16 with a BOM, else the ANSI
    code page) or by hand (UTF-8)."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        for enc in ("utf-8-sig", "mbcs", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read_string(text)
    return cp


def _one_line(v):
    return re.sub(r"\s*[\r\n]+\s*", " ", str(v if v is not None else "")).strip()


def result_text(res):
    keys = ["status", "message", "fix"] + [k for k in res if k not in ("status", "message", "fix")]
    return "[result]\r\n" + "".join(f"{k}={_one_line(res.get(k, ''))}\r\n" for k in keys)


def write_result(path, res):
    """result.ini as UTF-16 LE with a BOM (what TIniFile / GetPrivateProfileStringW reads whatever the code page),
    written to a temp file and moved into place, so the script never reads half a file."""
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(b"\xff\xfe" + result_text(res).encode("utf-16-le"))
    os.replace(tmp, path)


def load_command(ini_path):
    """command= of an ecadlib_menu.ini ('' when empty or missing)."""
    try:
        return (read_ini(ini_path)["ecadlib"].get("command") or "").strip()
    except (OSError, KeyError, configparser.Error):
        return ""


def load_config(ini_path):
    """Settings from ecadlib_menu.ini with paths resolved against its folder."""
    ini_path = Path(ini_path)
    base = ini_path.parent
    sec = read_ini(ini_path)["ecadlib"] if ini_path.is_file() else {}
    get = lambda k, d="": (sec.get(k, d) or d).strip()
    def path(v, rel_to):
        if Path(v).is_absolute() or PureWindowsPath(v).is_absolute():
            return Path(v)
        return rel_to / Path(*PureWindowsPath(v).parts)       # ..\\altium_library works on any OS
    ws = path(get("workspace", "workspace"), base)
    lib = path(get("library"), base) if get("library") else ws / "altium_library"
    try:
        port = int(get("port", "8765"))
    except ValueError:
        raise ValueError(f"{ini_path}: port= must be a number")
    return {"workspace": ws, "library": lib, "name": get("name", "ecadlib") or "ecadlib", "port": port,
            "offline": get("offline", "0") == "1"}


# ------------------------------------------------------------------ the actions
MPN_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9_.+/#,() ]{0,79}$")


def add_to_parts(ws_root, mpn, manufacturer="", **hints):
    """Add (or update) one line in <workspace>/uploads/altium-menu/parts.csv. Returns the file."""
    mpn = (mpn or "").strip()
    if not MPN_RE.match(mpn):
        raise ValueError(f"not a manufacturer part number: {mpn!r} (letters, digits and - _ . + / # , ( ) only)")
    bom = Path(ws_root) / MENU_BOM
    bom.parent.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(bom.open(encoding="utf-8", newline=""))) if bom.is_file() else []
    new = {"Mfr. Part #": mpn, "Manufacturer": (manufacturer or "").strip(), "Value": hints.get("value") or "",
           "Footprint": hints.get("footprint") or "", "LCSC Part Number": hints.get("lcsc") or "",
           "Description": hints.get("description") or "", "Datasheet": hints.get("datasheet") or "",
           "Designator": hints.get("designator") or ""}
    row = next((r for r in rows if r.get("Mfr. Part #", "").strip().upper() == mpn.upper()), None)
    if row is None:
        row = {"Qty": "1"}
        rows.append(row)
    row.update({k: v for k, v in new.items() if v or k not in row})
    for i, r in enumerate(rows, 1):
        if not r.get("Designator"):
            r["Designator"] = f"U{i}"
        if not r.get("Value"):
            r["Value"] = r.get("Mfr. Part #", "")
    text = io.StringIO()
    w = csv.DictWriter(text, HEADER, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in HEADER})
    if not bom.is_file() or bom.read_text(encoding="utf-8") != text.getvalue():
        bom.write_text(text.getvalue(), encoding="utf-8", newline="")
    return bom


def _mpn_of(item):
    par = {p["name"]: str(p.get("value", "")) for p in item.get("parameters", [])}
    return par.get("Manufacturer Part Number", "")


def _item_for(items, mpn):
    m = mpn.strip().upper()
    return (next((i for i in items if _mpn_of(i).strip().upper() == m), None)
            or next((i for i in items if str(i.get("name", "")).strip().upper() == m), None))


def _a365_url(ws):
    return str((ws.rules().get("altium365") or {}).get("workspace") or "").strip()


def publish(ws, bom_rel, library, name="ecadlib"):
    """Ready parts of the BOM's plan -> the library folder (altium-pkg / dblib-sync) or Altium 365 (push).
    Returns {"target", "install": [DbLib paths], "problems", "note"}."""
    from . import altium_pkg as AP
    url = _a365_url(ws)
    if url:
        try:
            res = ws.altium365_push(bom_rel, url, dry_run=False)
        except ValueError as e:
            return {"target": "a365", "install": [], "problems": [str(e)], "workspace": url,
                    "fix": "set NEXAR_TOKEN in your environment, then restart Altium" if "NEXAR_TOKEN" in str(e) else ""}
        acts = [r.get("action") for r in res["results"]]
        return {"target": "a365", "install": [], "problems": [f"{r['name']}: {r.get('detail')}" for r in res["results"]
                                                              if r.get("action") == "error"],
                "workspace": url, "released": sum(a in ("release", "released", "create", "created", "update", "updated")
                                                 for a in acts), "report": str(ws.root / res["report"])}
    bom_path, plan_f, items = ws._plan(bom_rel)
    if not any(i.get("status") == "ready" for i in items) and not any(Path(library).glob("*.DbLib")):
        return {"target": "folder", "install": [], "problems": [], "library": str(library)}
    try:
        rep = AP.build(plan_f, library, name, rules=ws.rules_in_use(), log=lambda *a: None)
    except ValueError as e:
        return {"target": "folder", "install": [], "problems": [str(e)], "library": str(library)}
    return {"target": "folder", "install": [str((Path(library) / d).resolve()) for d in rep["dblibs"]],
            "problems": rep["problems"], "library": str(library)}


def _workspace(cfg):
    from .app import Workspace
    Path(cfg["workspace"]).mkdir(parents=True, exist_ok=True)
    return Workspace(cfg["workspace"], offline=cfg.get("offline", False))


def add_part(cfg, mpn, manufacturer="", **hints):
    """Build one part from its MPN and publish it. Returns the result dict."""
    ws = _workspace(cfg)
    bom = add_to_parts(ws.root, mpn, manufacturer, **hints)
    rel = ws.rel(bom)
    ws.librarian_run(rel)
    _, _, items = ws._plan(rel)
    it = _item_for(items, mpn)
    if it is None:
        return {"status": "error", "message": f"ecadlib could not add {mpn} to the library.",
                "fix": f"Check the part number. What was tried is in {ws.root / ws._librarian_dir(bom).relative_to(ws.root) / 'review.html'}"}
    pub = publish(ws, rel, cfg["library"], cfg["name"]) if it["status"] == "ready" else {"install": [], "problems": []}
    st, nm = it["status"], it["name"]
    res = {"status": st, "part": nm, "mpn": mpn, "install": ";".join(pub.get("install") or []),
           "refresh": pub.get("target", ""), "review": str(ws._librarian_dir(bom) / "review.md")}
    from . import messages as M
    whys = M.explain_all(it.get("hold_reasons") or [])
    if st == "ready":
        where = (f"released to Altium 365 ({pub['workspace']}): refresh the Components panel to see it"
                 if pub.get("target") == "a365" else
                 f"in the {cfg['name']} database library ({pub.get('library')})")
        res["message"] = f"{mpn} is ready as {nm}: {where}."
        if pub.get("problems"):
            res.update(status="error", message=f"{mpn} was built ({nm}) but not published: {pub['problems'][0]}",
                       fix=pub.get("fix") or "run `ecadlib altium-pkg` or `ecadlib altium365 push` by hand to see more")
    elif st == "awaiting_approval":
        res.update(message=f"{mpn} was built as {nm} and is waiting for sign-off before it goes into the library.",
                   fix="Approve it in the review queue (ecadlib > Open review queue), then add it again.")
    elif st == "duplicate":
        res.update(message=f"{mpn} is already in the library as {it.get('duplicate_of') or nm}.")
    else:
        res.update(status="held", message=f"{mpn} is held ({nm}): " + (" ".join(w["text"] for w in whys[:3]) or "see the review."),
                   fix=(whys[0]["fix"] + " " if whys else "") + "Open the review queue (ecadlib > Open review queue) "
                       "to pick a symbol or footprint, or release it anyway.")
    return res


def _library_mpns(cfg, ws):
    """MPNs already in the library: the DbLib parts table (any 'part number' column) and every ready / pulled plan item."""
    have = set()
    for f in sorted(Path(cfg["library"]).glob("*.csv")):
        try:
            for row in csv.DictReader(f.open(encoding="utf-8", newline="")):
                have |= {str(v).strip().upper() for k, v in row.items() if k and "part number" in k.lower() and v}
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
    for plan in sorted((ws.root / "librarian").glob("*/plan.json")) + [ws.root / "altium365" / "workspace_plan.json"]:
        try:
            items = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        have |= {_mpn_of(i).strip().upper() for i in items if i.get("status") in ("ready", "existing") and _mpn_of(i)}
    return have


def _bom_of(ws, project):
    """(workspace-relative BOM csv, project summary or None) for an Altium project / SchDoc, or a BOM file."""
    p = Path(project)
    if not p.is_file():
        raise ValueError(f"{project}: no such file (save the project first)")
    if p.suffix.lower() in (".prjpcb", ".schdoc"):
        pb = ws.project_bom(str(p.resolve()))
        return pb["csv"], pb
    return str(p.resolve()), None


def report_html(title, rows, counts, md_link):
    e = html.escape
    head = ["#", "Designators", "Qty", "MPN", "Manufacturer", "In your library", "Lookup", "Notes"]
    body = "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in (
        r["line"], r["designator"], r["qty"], r["mpn"], r["manufacturer"], "yes" if r["in_library"] else "NO",
        r["status"], r["notes"])) + "</tr>\n" for r in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(title)}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#222}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:3px 8px;text-align:left;vertical-align:top}}th{{background:#f2f2f2}}
.n{{color:#b00;font-weight:600}}</style></head><body><h1>{e(title)}</h1>
<p>{counts['lines']} BOM lines: <b>{counts['in_library']}</b> in your library, <span class="n">{counts['missing']}</span>
not yet, {counts['dnp']} not fitted. Full lookup report: <a href="{e(md_link)}">{e(Path(md_link).name)}</a>.</p>
<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>
{body}</table><p><small>written by ecadlib {__version__} (ecadlib altium-menu check-bom)</small></p></body></html>
"""


def check_bom(cfg, project, opener=None):
    """BOM of an Altium project -> triage + which lines the library already has -> HTML report (opened)."""
    ws = _workspace(cfg)
    bom, pb = _bom_of(ws, project)
    tri = ws.triage(bom)
    have = _library_mpns(cfg, ws)
    rows, counts = [], {"lines": 0, "in_library": 0, "missing": 0, "dnp": 0}
    for r in tri["rows"]:
        dnp = str(r.get("status", "")).startswith("dnp")
        inlib = bool(r.get("mpn")) and r["mpn"].strip().upper() in have
        rows.append({**r, "in_library": inlib})
        counts["lines"] += 1
        counts["dnp" if dnp else "in_library" if inlib else "missing"] += 1
    stem = Path(tri["reports"][0]).with_suffix("").name
    out = ws.root / "reports" / f"{stem}_altium.html"
    name = pb["project"] if pb else Path(project).name
    out.write_text(report_html(f"BOM check: {name}", rows, counts, (ws.root / tri["reports"][0]).as_uri()),
                   encoding="utf-8")
    opened = _open(out, opener)
    res = {"status": "ok", "message": f"{name}: {counts['lines']} BOM lines, {counts['in_library']} in your library, "
                                      f"{counts['missing']} not yet" + (f", {counts['dnp']} not fitted" if counts["dnp"] else "")
                                      + (". The report is open in your browser." if opened else f". Report: {out}"),
           "report": str(out), "bom": str(ws.root / bom) if not Path(bom).is_absolute() else bom,
           "lines": counts["lines"], "missing": counts["missing"], "offer_fill": "1" if counts["missing"] else "0"}
    if pb and pb.get("warnings"):
        res["warnings"] = "; ".join(pb["warnings"])[:400]
    if counts["missing"]:
        res["offer"] = (f"Fill the library now? ecadlib looks up the {counts['missing']} missing part(s), builds what it "
                        "can and puts the ready ones into the library (this can take a few minutes).")
    return res


def fill(cfg, project):
    """Librarian on the project's BOM, then publish the ready parts."""
    ws = _workspace(cfg)
    bom, pb = _bom_of(ws, project)
    s = ws.librarian_run(bom)
    pub = publish(ws, bom, cfg["library"], cfg["name"]) if s["ready"] else {"install": [], "problems": []}
    where = ("released to Altium 365: refresh the Components panel" if pub.get("target") == "a365"
             else f"in the {cfg['name']} database library")
    msg = f"{s['parts']} parts: {s['ready']} ready" + (f" ({where})" if s["ready"] else "")
    msg += "".join(f", {n} {label}" for n, label in ((s["held"], "held"), (s["awaiting_approval"], "waiting for sign-off"),
                                                       (s["duplicates"], "duplicates")) if n)
    res = {"status": "ok" if not pub.get("problems") else "error", "message": msg + ".",
           "install": ";".join(pub.get("install") or []), "refresh": pub.get("target", ""),
           "ready": s["ready"], "held": s["held"], "review": str(ws._librarian_dir(ws._bom_path(bom)) / "review.md")}
    if pub.get("problems"):
        res["message"] += f" Not published: {pub['problems'][0]}"
        res["fix"] = pub.get("fix") or "run `ecadlib altium-pkg` / `ecadlib altium365 push` by hand to see more"
    elif s["held"] or s["awaiting_approval"]:
        res["fix"] = "Open the review queue (ecadlib > Open review queue): it says why each part is held and what to pick."
    return res


def _open(path_or_url, opener=None):
    """Open a file or URL in the default viewer/browser (opener False: don't). Returns True when it was handed over."""
    target = str(path_or_url)
    if opener is False:
        return False
    try:
        if opener is not None:
            opener(target)
        elif os.name == "nt" and not target.startswith("http"):
            os.startfile(target)                                     # the default program for .html
        else:
            import webbrowser
            webbrowser.open(target if target.startswith("http") else Path(target).resolve().as_uri())
        return True
    except Exception:
        return False


def probe_ui(port, host="127.0.0.1", timeout=1.5):
    """What answers on the port: ("ecadlib", workspace) | ("ecadlib-team", None) | ("other", None) | (None, None)."""
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/info", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return ("ecadlib", data.get("workspace")) if isinstance(data, dict) and "workspace" in data else ("other", None)
    except urllib.error.HTTPError as e:
        return ("ecadlib-team", None) if e.code in (401, 403) else ("other", None)
    except (OSError, ValueError):
        return (None, None)


def start_ui(ws_root, port):
    """`python -m ecadlib ui --workspace W --port P --no-browser`, detached, no window; output in altium_menu_ui.log."""
    cmd = [sys.executable, "-m", "ecadlib", "ui", "--workspace", str(ws_root), "--port", str(port), "--no-browser"]
    log = open(Path(ws_root) / "altium_menu_ui.log", "ab")
    env = dict(os.environ)                        # this ecadlib, also when it runs from a checkout (not installed)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parent.parent)] +
                                        ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    kw = {"env": env, "cwd": str(ws_root), "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                               | getattr(subprocess, "CREATE_NO_WINDOW", 0x8000000))
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


def open_queue(cfg, opener=None, probe=probe_ui, launcher=start_ui, wait_s=30.0, sleep=time.sleep):
    """Open the web UI at the review queue, starting `ecadlib ui` for the workspace when nothing answers."""
    port, ws = cfg["port"], Path(cfg["workspace"])
    url = f"http://127.0.0.1:{port}/{QUEUE_ANCHOR}"
    kind, served = probe(port)
    started = False
    if kind == "other":
        return {"status": "error", "message": f"port {port} is used by another program",
                "fix": f"set port= to a free port in {INI}"}
    if kind == "ecadlib" and served and Path(served).resolve() != ws.resolve():
        return {"status": "error", "message": f"port {port} already serves another ecadlib workspace ({served})",
                "fix": f"stop that `ecadlib ui`, or set port= to another port in {INI}"}
    if kind is None:
        ws.mkdir(parents=True, exist_ok=True)
        launcher(ws, port)
        started, t0 = True, time.monotonic()
        while probe(port)[0] is None:
            if time.monotonic() - t0 > wait_s:
                return {"status": "error", "message": f"the ecadlib web UI did not start on port {port} within {int(wait_s)} s",
                        "fix": f"see {ws / 'altium_menu_ui.log'}, or run `ecadlib ui --workspace \"{ws}\"` by hand"}
            sleep(0.3)
    if opener is not False and not _open(url, opener):
        return {"status": "error", "message": f"could not open a browser at {url}", "fix": "open that address yourself",
                "url": url}
    return {"status": "ok", "message": f"review queue: {url}" + (" (web UI started)" if started else ""), "url": url,
            "started": "1" if started else "0"}


def run_action(cfg, action, req, opener=None):
    if action == "add-part":
        hints = {k: req.get(k, "") for k in ("value", "footprint", "lcsc", "description", "datasheet", "designator")}
        return add_part(cfg, req.get("mpn", ""), req.get("manufacturer", ""), **hints)
    if action == "check-bom":
        return check_bom(cfg, req.get("project", ""), opener=opener)
    if action == "fill":
        return fill(cfg, req.get("project", ""))
    if action == "queue":
        return open_queue(cfg, opener=opener)
    raise ValueError(f"unknown action {action!r} (one of {', '.join(ACTIONS)})")


def _error(e):
    msg = str(e) or type(e).__name__
    fix = ""
    if isinstance(e, FileNotFoundError) or "no such file" in msg:
        fix = "save the project, then try again"
    elif "NEXAR_TOKEN" in msg:
        fix = "set NEXAR_TOKEN in your environment, then restart Altium"
    elif "ecadlib.toml" in msg or "rules" in msg.lower():
        fix = "`ecadlib rules check` shows the problem in ecadlib.toml"
    return {"status": "error", "message": msg if isinstance(e, (ValueError, OSError)) else f"{type(e).__name__}: {msg}",
            "fix": fix or "see log.txt in the job folder; `ecadlib doctor` checks the setup"}


def run_job(job, opener=None):
    """What the menu runs: read <job>/request.ini, do it, write <job>/result.ini. Returns the result dict."""
    job = Path(job)
    try:
        req = dict(read_ini(job / "request.ini")["request"])
        cfg = load_config(req.get("config") or job.parent.parent / INI)
        res = run_action(cfg, req.get("action", ""), req, opener=opener)
    except Exception as e:                                   # every failure becomes one dialog line with a fix
        res = _error(e)
    write_result(job / "result.ini", res)
    prune_jobs(job.parent, keep=job)
    return res


def prune_jobs(jobs_dir, days=14, keep=None, now=None):
    """Remove finished job folders (exit.txt present) older than `days`; returns how many."""
    import shutil
    now, n = now or time.time(), 0
    for d in sorted(Path(jobs_dir).iterdir()) if Path(jobs_dir).is_dir() else []:
        if d.is_dir() and d != keep and (d / "exit.txt").is_file() and now - (d / "exit.txt").stat().st_mtime > days * 86400:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


# ------------------------------------------------------------------ CLI
def _cfg_from_args(a):
    if getattr(a, "config", None):
        cfg = load_config(a.config)
    else:
        ws = Path(a.workspace or "ecadlib_workspace")
        cfg = {"workspace": ws, "library": Path(a.library) if a.library else ws / "altium_library", "name": a.name,
               "port": a.port, "offline": a.offline}
    if a.workspace and getattr(a, "config", None):
        cfg["workspace"] = Path(a.workspace)
    if a.library and getattr(a, "config", None):
        cfg["library"] = Path(a.library)
    if a.offline:
        cfg["offline"] = True
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib altium-menu",
                                 description="An 'ecadlib' menu in Altium Designer (DelphiScript script project) and the "
                                             "actions it runs",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="examples:\n  ecadlib altium-menu build --out D:/ecadlib-menu --workspace D:/ecadlib-ws "
                                        "--to D:/libs/ecadlib\n  ecadlib altium-menu add-part LM358DR --manufacturer TI "
                                        "--workspace D:/ecadlib-ws --result-file r.ini\n"
                                        "then in Altium: Preferences > Scripting System > Global Projects > ecadlib_menu.PrjScr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the script project, settings, runner and (with a Windows folder) the menu file")
    b.add_argument("-o", "--out", default="out/altium_menu", help="folder for the script project (default %(default)s)")
    b.add_argument("--workspace", help="ecadlib workspace the menu uses (default: 'workspace' next to the script)")
    b.add_argument("--to", dest="library", metavar="DIR",
                   help="Altium library folder for ready parts, as in dblib-sync --to (default: <workspace>/altium_library)")
    b.add_argument("--name", default="ecadlib", help="library name (default %(default)s)")
    b.add_argument("--command", default="", help='how to run ecadlib on that PC (default on Windows: "<this Python>" -m '
                                                 "ecadlib, so a venv install works; elsewhere: find it: PATH, py -3, python)")
    b.add_argument("--win-dir", help="the Windows folder --out will be in on the Altium PC (for the ready-made menu file)")
    b.add_argument("--port", type=int, default=8765, help="web UI port for Open review queue (default %(default)s)")
    b.add_argument("--console", action="store_true", help="show ecadlib's console window (hidden=0)")
    b.add_argument("--timeout", type=int, default=1800, help="seconds before the script asks whether to keep waiting")
    b.add_argument("--offline", action="store_true", help="offline=1: cached downloads and standard parts only")
    b.add_argument("--json", action="store_true")
    for name, hlp in (("add-part", "build one part from its MPN and put it into the library"),
                      ("check-bom", "check an Altium project's BOM against the library; HTML report"),
                      ("fill", "librarian on a project's (or BOM's) parts, ready ones into the library"),
                      ("queue", "open the web UI at the review queue (start it if needed)")):
        s = sub.add_parser(name, help=hlp)
        if name == "add-part":
            s.add_argument("mpn")
            s.add_argument("--manufacturer", default="")
            for h in ("value", "footprint", "lcsc", "description", "datasheet", "designator"):
                s.add_argument(f"--{h}", default="", help=f"optional {h} hint for the BOM line")
        elif name in ("check-bom", "fill"):
            s.add_argument("project", help=".PrjPcb / .SchDoc (or a BOM file for fill)")
        if name == "check-bom":
            s.add_argument("--no-open", action="store_true", help="do not open the report")
        if name == "queue":
            s.add_argument("--no-open", action="store_true", help="do not open a browser (print the address)")
        s.add_argument("--config", help=f"read workspace / library / name / port from this {INI}")
        s.add_argument("--workspace", help="ecadlib workspace (default ecadlib_workspace)")
        s.add_argument("--to", dest="library", metavar="DIR",
                       help="Altium library folder, as in dblib-sync --to (default <workspace>/altium_library)")
        s.add_argument("--name", default="ecadlib")
        s.add_argument("--port", type=int, default=8765)
        s.add_argument("--offline", action="store_true")
        s.add_argument("--result-file", help="also write the result as the INI file the Altium script reads")
        s.add_argument("--json", action="store_true", help="print the result as JSON")
    r = sub.add_parser("run-job", help="what the Altium script runs: JOB/request.ini -> JOB/result.ini")
    r.add_argument("job")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        try:
            rep = build(a.out, a.workspace, a.library, a.name, a.command, a.win_dir, a.port, not a.console, a.timeout,
                        a.offline)
        except (OSError, ValueError) as e:
            print(f"ecadlib altium-menu: {e}", file=sys.stderr)
            return 2
        if a.json:
            print(json.dumps(rep, indent=2))
        else:
            for p in rep["problems"]:
                print(f"  PROBLEM {p}")
            print(f"wrote {', '.join(rep['written']) or 'nothing new'} in {a.out}" +
                  (f" (kept your {', '.join(rep['kept'])})" if rep["kept"] else ""))
            if rep["command_filled"]:
                print(f"  {INI}: command= was empty; set it to {rep['command']}")
            print(f"  runs ecadlib with: {rep['command'] or 'ecadlib on PATH, else py -3 / python (set command= in ' + INI + ' for a venv)'}")
            print(f"  in Altium: Preferences > Scripting System > Global Projects > Install from file > {PROJECT}; "
                  f"then Customize to put {', '.join(rep['procedures'])} on a menu (see {README})")
            if not rep["menu_file"]:
                print(f"  no {PREFS}: give --win-dir (the folder on the Altium PC) for the ready-made menu file")
        return 1 if rep["problems"] else 0
    if a.cmd == "run-job":
        res = run_job(a.job)
        print(json.dumps(res, indent=2))
        return 0
    cfg = _cfg_from_args(a)
    opener = False if getattr(a, "no_open", False) else None
    req = {"mpn": getattr(a, "mpn", ""), "manufacturer": getattr(a, "manufacturer", ""),
           "project": str(Path(a.project).resolve()) if getattr(a, "project", None) else ""}
    for h in ("value", "footprint", "lcsc", "description", "datasheet", "designator"):
        req[h] = getattr(a, h, "")
    try:
        res = run_action(cfg, a.cmd, req, opener=opener)
    except Exception as e:
        if os.environ.get("ECADLIB_DEBUG"):
            raise
        res = _error(e)
    if a.result_file:
        write_result(a.result_file, res)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"{res['status']}: {res.get('message', '')}")
        if res.get("fix"):
            print(f"  fix: {res['fix']}")
    return 0 if res["status"] in ("ok", "ready") else 1


if __name__ == "__main__":
    sys.exit(main())
