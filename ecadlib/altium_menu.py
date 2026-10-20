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
  ecadlib_build.pas                 Altium builds the library files itself (ecadlib.altium_native):
        BuildLibrariesInAltium   (File > Run Script) builds every library in ecadlib_build.txt next to the project
        BuildLibraries(Folder)   the same for Folder; the menu calls it before it reinstalls the library
                                    (PROCS + BUILD_PROCS below; `build` prints them all)
  ecadlib_dialog.pas + .dfm         the ecadlib dialog (ecadlib.altium_forms): dark, like Altium's own, with
                                    live progress read from the job's progress.ini; dialogs=0 in the .ini turns it off
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
     runs IntegratedLibrary:RefreshInstalledLibraries (AllLibraries=True). Before that, when build= names the library
     folder (altium-pkg wrote ecadlib_build.txt there, see ecadlib.altium_native), Altium writes every .SchLib /
     .PcbLib again with its own API (BuildLibraries in ecadlib_build.pas, part of this script project) and the dialog
     says if any library FAILED (ecadlib_build_report.txt). native_build=0 in the .ini skips that.

Where parts go: the workspace is an ordinary ecadlib workspace (`ecadlib ui --workspace` shows it); parts added from
the menu collect in <workspace>/uploads/altium-menu/parts.csv and the librarian runs on that list (finished parts are
kept). Ready parts are synced into the library folder (`ecadlib altium-pkg`: DbLib + SchLib/PcbLib + LibPkg), or,
when the rules name an Altium 365 workspace ([altium365] workspace), released there (`altium365 push`; the Nexar token from
ecadlib's key store): the Components panel then shows them after a refresh. Held parts are never published.

Never run inside Altium here: the script is written from Altium's scripting references (TR0120 DelphiScript,
TR0124 Server Process, TR0135 System, TR0136 Integrated Library, TR0140 Workspace Manager; 2008 PDFs kept in
github.com/Altium-Designer-addons/scripting-reference) and public scripts; see ALTIUM_API for each call it uses."""
import argparse, configparser, csv, datetime, html, io, json, os, re, subprocess, sys, time, zipfile
from pathlib import Path, PureWindowsPath

from . import __version__, altium_native as AN

PROJECT, UNIT, INI = "ecadlib_menu.PrjScr", "ecadlib_menu.pas", "ecadlib_menu.ini"
RUN_CMD, RUN_VBS, PREFS, README = "ecadlib_run.cmd", "ecadlib_run.vbs", "ecadlib_menu.DXPPrf", "ALTIUM_MENU_README.txt"
PROCS = [("AddPartFromMPN", "&Add part from MPN...", "Build a part from its manufacturer part number with ecadlib"),
         ("CheckProjectBOM", "&Check this project BOM", "Check the focused project's BOM against the library; offer to fill it"),
         ("OpenReviewQueue", "Open &review queue", "Open the ecadlib web UI at the review queue")]
# the other procedures the script project offers (ecadlib_build.pas, ecadlib.altium_native): not menu entries
BUILD_PROCS = [("BuildLibrariesInAltium", "ecadlib_build.pas", "Altium writes the libraries in ecadlib_build.txt itself"),
               ("BuildLibraries(Folder)", "ecadlib_build.pas", "the same for Folder; the menu calls it before reinstalling")]


def procedures():
    """Every procedure the script project offers: the menu entries (PROCS), then BUILD_PROCS."""
    return [p for p, _, _ in PROCS] + [p for p, _, _ in BUILD_PROCS]
MENU_BOM = "uploads/altium-menu/parts.csv"
HEADER = ["Designator", "Qty", "Value", "Footprint", "Manufacturer", "Mfr. Part #", "LCSC Part Number", "Description", "Datasheet"]
ACTIONS = ("add-part", "check-bom", "fill", "queue", "open", "ai-read")
QUEUE_ANCHOR = "#libcard"

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
    "LoadFromFile": "TStringList.LoadFromFile, TR0120 p10 (as ecadlib_build.pas reads ecadlib_build.txt)",
    "Strings": "TStringList.Strings, TR0120 p10",
    "Count": "TStringList.Count, TR0120 p10",
    "DeleteFile": "TR0120 p16 (File IO Routines)",
    "RenameFile": "TR0120 File IO Routines (ecadlib_build.pas uses it too; checked in AD26 26.10)",
}


# ------------------------------------------------------------------ the script project
def pas_text():
    """The DelphiScript unit (CRLF): altium_scripts/ecadlib_menu.pas. Static: everything that differs per install is
    in ecadlib_menu.ini."""
    return AN.script_text(UNIT)


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
; 1 = the ecadlib dialog (dark, live progress, like Altium's own); 0 = Altium's plain message boxes
dialogs=1
; 1 = Altium writes the .SchLib / .PcbLib files again with its own API before the library is installed
; (ecadlib_build.pas); 0 = install the files ecadlib wrote as they are
native_build=1
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
        L += [f"Insertion {kind}_ecadlib_Menu TargetID='{target}' RefID0='{ref}' InsertType='Before' ",
              f"    Tree {kind}_ecadlib_Tree Caption='&ecadlib' Popup='N' "]
        L += [f"        Link {kind}_ecadlib_{proc} PLID='ecadlib_{proc}'          End" for proc, _, _ in PROCS]
        L += ["    End", "End"]
    L.append("")
    for kind, _, _, custom in MENUS:
        L.append(f"Tree {custom} Caption='[Custom]' TopLevel='True' ")
        L += [f"    Link {custom}_ecadlib_{proc} PLID='ecadlib_{proc}'          End" for proc, _, _ in PROCS]
        L.append("End")
    return "\r\n".join(L) + "\r\n"


# ------------------------------------------------------------------ the menu straight into Altium's DXP.RCS
def rcs_blocks(text):
    """DXP.RCS -> [lines of each top-level block]. A line ending in End closes itself; Insertion / Tree / other lines
    without one open a block that an End line closes (the grammar of Altium's .rcs resources)."""
    blocks, cur, depth = [], [], 0
    for ln in text.replace("\r\n", "\n").split("\n"):
        if not ln.strip():
            if depth == 0:
                continue
            cur.append(ln)
            continue
        cur.append(ln)
        st = ln.strip()
        if st == "End":
            depth -= 1
        elif not re.search(r"\bEnd\s*$", st):
            depth += 1
        if depth < 0:
            raise ValueError(f"DXP.RCS: an End without its block near {st[:60]!r}")
        if depth == 0:
            blocks.append(cur)
            cur = []
    if depth or cur:
        raise ValueError("DXP.RCS: a block is not closed (End missing)")
    return blocks


def _block_id(block):
    m = re.match(r"^\s*\w+\s+(\w+)", block[0])
    return m.group(1) if m else ""


def merge_rcs(current, win_dir, remove=False):
    """Altium's DXP.RCS with the ecadlib menu in it (or, remove=True, without it): ecadlib's commands (PL) and the
    four menu Insertions. Earlier ecadlib entries are replaced, our links inside a [Custom] tree (from loading the
    .DXPPrf) are taken out, and everything else is kept as it was. The [Custom] trees themselves are left alone."""
    ours = [] if remove else [b for b in rcs_blocks(rcs_text(win_dir)) if not _block_id(b).endswith("Custom")]
    mine = lambda i: i.startswith("ecadlib_") or "_ecadlib_" in i
    out = []
    for b in rcs_blocks(current or ""):
        if mine(_block_id(b)):
            continue
        out.append([ln for ln in b if not mine(_block_id([ln]))])
    out += ours
    return "\r\n".join(ln for b in out for ln in b) + ("\r\n" if out else "")


def altium_user_folders(appdata=None):
    """%APPDATA%\\Altium\\Altium Designer {GUID} folders (one per installed Altium), newest first."""
    base = Path(appdata or os.environ.get("APPDATA", "")) / "Altium"
    if not base.is_dir():
        return []
    found = [d for d in base.glob("Altium Designer {*}") if d.is_dir()]
    return sorted(found, key=lambda d: (d / "DXP.RCS").stat().st_mtime if (d / "DXP.RCS").exists() else d.stat().st_mtime,
                  reverse=True)


def altium_running():
    """True when an Altium Designer (X2.EXE) runs on this PC (it writes DXP.RCS when it closes)."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/NH"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "x2.exe" in out.lower()


def install_menu_bar(menu_dir, folders=None, remove=False, running=altium_running, now=None, settle_s=5.0,
                     sleep=time.sleep):
    """Put the "ecadlib" menu into Altium's top menu bar (schematic, PCB, SchLib and PcbLib editors, before Window)
    by merging it into each Altium's DXP.RCS, after a backup next to it. Altium must be closed: it writes that
    file when it closes. remove=True takes it out again. -> {"installed"|"removed": [DXP.RCS], "backups": [...]}."""
    if os.name == "nt":
        win = check_win_dir(str(Path(menu_dir).resolve()))
    else:                                # WSL / Linux writing to the Windows side: the folder `build --win-dir` recorded
        try:
            rec = json.loads((Path(menu_dir) / "altium_menu.json").read_text(encoding="utf-8")).get("win_dir")
        except (OSError, ValueError, AttributeError):
            rec = None
        try:
            win = check_win_dir(rec or str(menu_dir))
        except ValueError:
            raise ValueError(f"{menu_dir}: not a Windows folder, and no Windows folder recorded for this menu: run "
                             f"`ecadlib altium-menu build --out {menu_dir} --win-dir D:\\ecadlib-menu` first (the "
                             "folder Altium will read it from), or give --out as that Windows folder") from None
    if running():
        raise ValueError("Altium Designer is running: close it first (it rewrites its menu file when it closes), "
                         "then run this again")
    folders = [Path(f) for f in folders] if folders else altium_user_folders()
    if not folders:
        raise ValueError("no Altium Designer settings folder found (%APPDATA%\\Altium\\Altium Designer {...}): start "
                         "Altium once, close it, then run this again")
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    done, backups = [], []
    for f in folders:
        rcs = f / "DXP.RCS"
        cur = rcs.read_text(encoding="latin-1") if rcs.is_file() else ""
        new = merge_rcs(cur, win, remove=remove)
        if new.replace("\r\n", "\n").strip() == cur.replace("\r\n", "\n").strip():
            continue
        if rcs.is_file():
            bak = f / f"DXP.RCS.before-ecadlib-{stamp}"
            bak.write_bytes(rcs.read_bytes())
            backups.append(str(bak))
        rcs.write_bytes(new.encode("latin-1"))
        done.append(str(rcs))
    if done and settle_s:                # an Altium still closing writes its old menus over ours: look again
        sleep(settle_s)
        lost = [r for r in done if Path(r).read_bytes() != merge_rcs(Path(r).read_text(encoding="latin-1"), win,
                                                                       remove=remove).encode("latin-1")]
        if lost or running():
            raise ValueError("an Altium Designer that was still closing wrote its old menus back over the ecadlib menu"
                             f" ({', '.join(lost) or 'Altium is running again'}): wait until no X2.EXE is left in Task "
                             "Manager, then run this again")
    return {"removed" if remove else "installed": done, "backups": backups, "folders": [str(f) for f in folders]}


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
    run_script = ", ".join(p for p in procedures() if "(" not in p)            # parameterless: Run Script lists them
    build_procs = "\n".join(f"     {p:<24} {u}: {d}" for p, u, d in BUILD_PROCS)
    entries = ", ".join(f"{UNIT}>{p}" for p, _, _ in PROCS)
    return f"""ecadlib menu for Altium Designer (written by `ecadlib altium-menu build`, ecadlib {__version__})

What it adds: an "ecadlib" menu (or toolbar buttons) with three entries:
  Add part from MPN        asks for a manufacturer part number, builds the part, reinstalls the library
  Check this project BOM   checks the focused project's BOM: counts per kind, then Later / Fill the library now
  Open review queue        opens the ecadlib web UI at the review queue in your browser
The entries work in the ecadlib dialog (dark, like Altium's own, with live progress); you stay in Altium. Only the
review queue (a held part needs a person) and the full BOM report (on request) open in your browser. dialogs=0 in
{INI} brings back Altium's plain message boxes.

Needs: ecadlib installed on this PC (pip install ecadlib; `ecadlib doctor --altium-menu {INI}` checks it). Settings
are in {INI} (workspace, library folder, how to run ecadlib). Your API keys stay in ecadlib's own key store (`ecadlib keys
set NAME` or the setup page), never in an environment variable; no file here holds one.

How the menu runs ecadlib: command= in {INI}. `ecadlib altium-menu build` run on Windows writes the Python it ran
with there ("C:\\...\\python.exe" -m ecadlib), so an install in a virtual environment (venv) works. When command=
is empty, the runner tries `ecadlib` on PATH, then `py -3 -m ecadlib`, then `python -m ecadlib`; a venv is none of
those, and the menu then says "ecadlib was not found". Fix: run the build again from the venv (it fills an empty
command= and keeps your other settings), or set command= yourself.

Without an AI key (ANTHROPIC_API_KEY, or the model set in ecadlib.toml [ai]), ecadlib cannot read datasheets, so
parts no free source fully describes come back held with the reason ("fields: Description is empty", "footprint:
..."). Held parts never go into the library: open the review queue (ecadlib > Open review queue) to see why and
pick or fill what is missing, or store the key for ecadlib only (`ecadlib keys set ANTHROPIC_API_KEY` in a terminal,
or the setup page) and add the part again. Do not use setx or another environment variable: every program on the PC
can read those, and some (coding assistants) bill an ANTHROPIC_API_KEY they find. Before ecadlib reads a datasheet
the dialog shows the model and the estimated cost and asks you; afterwards it shows what the reads really cost.

Install (once), the quick way: close Altium, run `ecadlib altium-menu install-menu --out "{w}"` and start Altium
again: the "ecadlib" menu is in the menu bar of the schematic, PCB and library editors, before Window. It is merged
into Altium's own menu file (%APPDATA%\\Altium\\Altium Designer {{...}}\\DXP.RCS, backed up next to it first);
`--remove` takes it out again. The step-by-step way, without closing Altium:
1. Altium: DXP (AD17) or the gear icon (AD18+) > Preferences > Scripting System > Global Projects > Install from
   file... > {w}\\{PROJECT}. File > Run Script... under {PROJECT} now lists {run_script}:
{build_procs}
2. Menu: right-click any menu or toolbar > Customize... > Categories: [Scripts] (or [Custom]) > drag
   {entries} onto a menu or toolbar; rename them in the entry's
   properties. Repeat for each editor you want them in (schematic, PCB, library editors).
{menu}
3. Try it: open a PCB project and pick ecadlib > Check this project BOM.

What happens on a click: the script writes jobs\\<id>\\request.ini next to it, runs {RUN_CMD} without a console
window (through {RUN_VBS}; set hidden=0 in {INI} to see the console); the dialog follows jobs\\<id>\\progress.ini
and reads jobs\\<id>\\result.ini at the end.
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


def default_command(executable=None, nt=None, frozen=None):
    """command= for this PC: the Python running ecadlib now (a venv's python.exe too) with -m ecadlib, the path quoted
    for cmd.exe ("C:\\venv\\Scripts\\python.exe" -m ecadlib); in the Windows app (frozen), its ecadlib.exe, which takes
    no -m. '' when not on Windows (the menu runs on the Altium PC, where this Python's path means nothing), or when
    the path cannot be written in plain ASCII or holds a quote."""
    nt = os.name == "nt" if nt is None else nt
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    exe = str(sys.executable if executable is None else executable or "")
    if not nt or not exe:
        return ""
    suffix = " -m ecadlib"
    if frozen:                                   # ecadlib-app.exe / ecadlib.exe: no Python to give -m to
        cli = Path(exe).with_name("ecadlib.exe")
        exe, suffix = (str(cli) if cli.is_file() else exe), ""
    if not exe.isascii():                        # the .ini is read in the ANSI code page: use the 8.3 name
        exe = _short_path(exe)
    if not exe.isascii() or '"' in exe:
        return ""
    return f'"{exe}"{suffix}'


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
    derived = win_dir is None and os.name == "nt"
    if derived:
        win_dir = str(out.resolve())
    if win_dir:
        try:
            win_dir = check_win_dir(win_dir)
        except ValueError:
            if not derived:
                raise
            win_dir = None            # this folder (C:\\Users\\O'Brien\\...) cannot go into a menu entry: no menu file
    if name and not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$", name):
        raise ValueError(f"--name {name!r}: letters, digits, _ . - only")
    from . import altium_forms as AF, altium_native as AN
    files = {PROJECT: prjscr_text(UNIT, AF.DIALOG_UNIT, AN.UNIT), UNIT: pas_text(),
             AF.DIALOG_UNIT: AF.dialog_pas_text(), AF.DIALOG_FORM: AF.dialog_dfm_text(), AN.UNIT: AN.pas_text(),
             RUN_CMD: run_cmd_text(), RUN_VBS: run_vbs_text(), README: readme_text(win_dir, bool(win_dir))}
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
    problems = project_problems(files)
    rep = {"folder": str(out), "project": PROJECT, "procedures": procedures(), "menu_entries": [p for p, _, _ in PROCS], "written": written,
           "kept": kept, "win_dir": win_dir, "menu_file": PREFS if win_dir else None, "problems": problems,
           "command": load_command(ini), "command_filled": filled}
    (out / "altium_menu.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    return rep


def prjscr_text(*units):
    """Script project as Altium saves one (Altium-Designer-addons/scripts-libraries QuickSilk.PrjScr, OpenLib.PrjScr for
    several units). A form unit's .dfm is found next to its .pas."""
    lines = ["[Design]", "Version=1.0", ""]
    for i, u in enumerate(units, 1):
        lines += [f"[Document{i}]", f"DocumentPath={u}", ""]
    return "\r\n".join(lines)


# ------------------------------------------------------------------ a small Pascal check (also used by the tests)
PAS_KEYWORDS = {"begin", "end", "var", "const", "function", "procedure", "if", "then", "else", "while", "do", "for",
                "to", "downto", "repeat", "until", "not", "and", "or", "div", "mod", "result", "exit", "break", "true",
                "false", "nil", "try", "finally", "except", "string", "integer", "boolean", "case", "of"}
PAS_TYPES = {"tinifile", "tstringlist", "iproject"}
GLOBALS = " globals"                       # pas_routines' key for a unit's Var block (no routine has a space)


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
        elif c == "$" and re.match(r"\$[0-9A-Fa-f]+", text[i:]):          # $00D9772F: a hex number (TColor)
            m = re.match(r"\$[0-9A-Fa-f]+", text[i:])
            toks.append(("num", m.group(0), line))
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
        if low(toks[i]) == "var" and not out:             # unit globals, before the first routine
            j, names = i + 1, set()
            while low(toks[j]) not in ("function", "procedure"):
                while toks[j][1] != ":":
                    if toks[j][0] == "word":
                        names.add(toks[j][1].lower())
                    j += 1
                while toks[j][1] != ";":
                    j += 1
                j += 1
            out[GLOBALS] = {"kind": "var", "params": set(), "vars": names, "body": []}
            i = j
        elif low(toks[i]) in ("function", "procedure"):
            kind, name = low(toks[i]), toks[i + 1][1]
            j, params = i + 2, set()
            if toks[j][1] == "." and toks[j + 1][0] == "word":     # Procedure TForm1.Button1Click(Sender: TObject)
                name, j = f"{name}.{toks[j + 1][1]}", j + 2
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


def pas_problems(text, api=None, known=(), procs=None):
    """What a reader of the .pas would object to: unbalanced Begin/End, an identifier that is neither declared here
    (routine, unit global, parameter, local variable), nor in `known` (the other unit's routines and globals, the
    form's components), nor in the documented allow-list (ALTIUM_API), a Function without a Result, a procedure the
    menu needs (procs, default PROCS) that is missing or takes parameters. [] = fine."""
    api = {k.lower() for k in (api or ALTIUM_API)}
    try:
        routines = pas_routines(pas_tokens(text))
    except (ValueError, IndexError) as e:
        return [f"does not parse: {e}"]
    probs = []
    glob = routines.pop(GLOBALS, {"vars": set()})["vars"]
    known = {n.lower() for n in routines} | {n.split(".")[-1].lower() for n in routines} | glob | {k.lower() for k in known}
    order = {n.lower(): i for i, n in enumerate(routines) if "." not in n}      # plain routines, in file order
    for here, (name, r) in enumerate(routines.items()):
        local = r["params"] | r["vars"]
        for idx, (kind, val, line) in enumerate(r["body"]):
            if kind != "word":
                continue
            w = val.lower()
            if order.get(w, -1) > here and w not in local:   # DelphiScript has no forward declarations (AD 26.10:
                probs.append(f"line {line}: {name}: {val} is used before it is declared")  # "Undeclared identifier")
            if w in PAS_KEYWORDS or w in PAS_TYPES or w in known or w in local or w in api:
                continue
            probs.append(f"line {line}: {name}: {val} is not declared and not in the API allow-list")
        if r["kind"] == "function" and not any(t[0] == "word" and t[1].lower() == "result" for t in r["body"]):
            probs.append(f"{name}: a Function that never sets Result")
    for proc, _, _ in (PROCS if procs is None else procs):
        if proc not in routines:
            probs.append(f"{proc}: missing")
        elif routines[proc]["params"] or routines[proc]["kind"] != "procedure":
            probs.append(f"{proc}: must be a Procedure without parameters (Altium's menus cannot pass any)")
    return probs


def unit_names(text):
    """Routines and unit globals a .pas declares (what another unit of the script project may call)."""
    try:
        r = pas_routines(pas_tokens(text))
    except (ValueError, IndexError):
        return set()
    return {n for n in r if n != GLOBALS and "." not in n} | set(r.get(GLOBALS, {}).get("vars", ()))


def project_problems(files):
    """pas_problems of every unit of the script project, each knowing the other units' routines and globals; a form
    unit also knows its .dfm's components and the form API (altium_forms.FORM_API). Every handler a .dfm
    names (OnClick = X) must be a method of its form in the unit. Problems are prefixed with the file name."""
    from . import altium_forms as AF, altium_native as AN
    units = {UNIT: None, AF.DIALOG_UNIT: AF.DIALOG_FORM}
    names = {u: unit_names(files[u]) for u in units}
    if AN.UNIT in files:                 # ecadlib_build.pas is checked by tests/test_altium_native.py; here it only
        names[AN.UNIT] = unit_names(files[AN.UNIT])          # declares what the menu calls (BuildLibraries)
    api = {**ALTIUM_API, **AF.FORM_API}
    probs = []
    for u, form_file in units.items():
        others = set().union(*(n for o, n in names.items() if o != u))
        if form_file is None:
            probs += [f"{u}: {p}" for p in pas_problems(files[u], known=others)]
            continue
        comps = AF.dfm_names(files[form_file])
        probs += [f"{u}: {p}" for p in pas_problems(files[u], api, known=others | set(comps), procs=[])]
        form = comps[0] if comps else ""
        methods = {n.split(".")[1] for n in pas_routines(pas_tokens(files[u])) if n.startswith(f"T{form}.")}
        handlers = set(re.findall(r"(?m)^\s*On\w+ = (\w+)", files[form_file]))
        probs += [f"{form_file}: {h} is named in the form but {u} has no T{form}.{h}" for h in sorted(handlers - methods)]
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


def result_text(res, section="result"):
    first = [k for k in ("status", "message", "fix") if section == "result" or k in res]
    keys = first + [k for k in res if k not in first]
    return f"[{section}]\r\n" + "".join(f"{k}={_one_line(res.get(k, ''))}\r\n" for k in keys)


def write_result(path, res, section="result"):
    """result.ini as UTF-16 LE with a BOM (what TIniFile / GetPrivateProfileStringW reads whatever the code page),
    written to a temp file and moved into place, so the script never reads half a file."""
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(b"\xff\xfe" + result_text(res, section).encode("utf-16-le"))
    os.replace(tmp, path)


class Progress:
    """<job>/progress.ini while the job runs, for the dialog's live view ([progress] done, total, text, rows, row1..).
    Rows are "label|value|ok" lines (ok = 1 shows the value in green). Written like result.ini (UTF-16, moved into
    place); a write the script's read collides with on Windows is skipped, the next one carries the same state."""

    def __init__(self, job=None, every_s=0.25, clock=time.monotonic):
        self.path = Path(job) / "progress.ini" if job else None
        self.done, self.total, self.text, self.rows = 0, 0, "", []
        self.every_s, self.clock, self.last = every_s, clock, None

    def step(self, done, total, text):
        self.done, self.total, self.text = done, total, step_text(text)
        self.flush(force=bool(total) and done >= total)          # the last step always shows

    def row(self, label, value="", ok=False):
        self.rows.append(row_text(label, value, ok))
        self.flush()

    def __call__(self, done, total, text):            # Workspace.triage / librarian_run progress(done, total, label)
        self.step(done, total, text)

    def flush(self, force=True):
        if self.path is None or (not force and self.last is not None and self.clock() - self.last < self.every_s):
            return
        self.last = self.clock()
        res = {"done": self.done, "total": self.total, "text": self.text, "rows": len(self.rows)}
        res.update({f"row{i}": r for i, r in enumerate(self.rows, 1)})
        try:
            write_result(self.path, res, section="progress")
        except OSError:
            pass


def clip(text, n):
    """One line of at most n characters, cut at a word with "..." when longer (the dialog's columns do not wrap)."""
    t = _one_line(text)
    if len(t) <= n:
        return t
    cut = t[:n - 3]
    if t[n - 3] != " " and " " in cut:                      # a word cut in two: drop it
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:(") + "..."


def row_text(label, value="", ok=False):
    """One dialog row: "label|value|1" (the script splits on |, so neither part may hold one). The label column fits
    about 26 characters, the value column about 28 (altium_forms.LABEL_W, WIDTH; Consolas is about 7 px a character, and
    a longer value would lose its start, the column being right-aligned)."""
    clean = lambda v, n: clip(str(v).replace("|", "/"), n)
    return f"{clean(label, 26)}|{clean(value, 28)}|{1 if ok else 0}"


def step_text(msg):
    """A librarian log line -> what the dialog's status line says (it fits about 55 characters)."""
    t = _one_line(msg)
    m = re.match(r"^(done|open|skip)\s+(\S+)", t)
    if m:
        return clip(f"{m.group(2)}: {'checked' if m.group(1) != 'open' else 'still needs a person'}", 55)
    t = re.sub(r"\s*\(sources: [^)]*\)?", "", t)
    return clip(t, 55)


def rows_result(rows, big=""):
    """{"big", "rows", "row1".."rowN"} for result.ini: what the dialog shows when the job is done."""
    out = {"big": big, "rows": len(rows)}
    out.update({f"row{i}": r for i, r in enumerate(rows, 1)})
    return out


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
    from . import sheetsafe as SS
    rows = [{k: SS.unquote(v) for k, v in r.items()} for r in csv.DictReader(bom.open(encoding="utf-8", newline=""))] \
        if bom.is_file() else []
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
    w = SS.DictWriter(text, HEADER, extrasaction="ignore", lineterminator="\n")   # '=HYPERLINK(..)' is not run
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


def publish(ws, bom_rel, library, name="ecadlib", tool="altium"):
    """Ready parts of the BOM's plan -> the library folder (altium-pkg / dblib-sync) or Altium 365 (push); with
    tool="kicad", the KiCad library folder (kicad-export). Returns {"target", "install": [DbLib paths], "problems", "note",
    "held": {part: why}}: "held" = parts the publisher's own release check held back (files changed since the check)."""
    if tool == "kicad":
        return publish_kicad(ws, bom_rel, library, name)
    from . import altium_pkg as AP
    url = _a365_url(ws)
    if url:
        try:
            res = ws.altium365_push(bom_rel, url, dry_run=False)
        except ValueError as e:
            return {"target": "a365", "install": [], "problems": [str(e)], "workspace": url,
                    "fix": "store your Nexar token on the setup page (Start menu > ecadlib setup) or with `ecadlib keys set NEXAR_TOKEN`, then try again" if "NEXAR_TOKEN" in str(e) else ""}
        acts = [r.get("action") for r in res["results"]]
        return {"target": "a365", "install": [], "problems": [f"{r['name']}: {r.get('detail')}" for r in res["results"]
                                                              if r.get("action") == "error"],
                "held": {r["name"]: r.get("detail") for r in res["results"] if r.get("recheck")},
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
            "problems": rep["problems"], "library": str(library), "held": rep.get("recheck") or {},
            "build": str(Path(library).resolve()) if rep.get("native_build") else ""}


def publish_kicad(ws, bom_rel, library, name="ecadlib"):
    """The plan's parts -> <library>/<name>.kicad_sym, .pretty, .3dshapes (kicad_export). One plan makes the whole
    library, so the KiCad menu keeps every part in one list (MENU_BOM). "footprints" maps part -> footprint name."""
    from . import kicad_export as KX
    from .httpcache import CachedHTTP
    _, plan_f, _ = ws._plan(bom_rel)
    rep = KX.export(plan_f, library, name, http=CachedHTTP(ws.root / "http_cache", offline=ws.offline, timeout=60),
                    rules=ws.rules_in_use(), log=lambda *a, **k: None)
    return {"target": "kicad", "install": [], "problems": rep["errors"], "library": str(library),
            "held": {s["part"]: s["why"] for s in rep["skipped"] if s.get("recheck")},
            "footprints": {p["part"]: p.get("footprint") or "" for p in rep["parts"]}}


def _where(cfg, pub):
    if pub.get("target") == "a365":
        return f"released to Altium 365 ({pub['workspace']}): refresh the Components panel to see it"
    if pub.get("target") == "kicad":
        return f"in the {cfg['name']} KiCad library ({pub.get('library')})"
    return f"in the {cfg['name']} database library ({pub.get('library')})"


def _queue_hint(cfg):
    return "the Open review queue button in the ecadlib window" if cfg.get("tool") == "kicad" else "ecadlib > Open review queue"


def _workspace(cfg):
    from .app import Workspace
    Path(cfg["workspace"]).mkdir(parents=True, exist_ok=True)
    return Workspace(cfg["workspace"], offline=cfg.get("offline", False))


def part_rows(it, limit=7):
    """What the dialog lists for one built part: where ecadlib looked, what it took, and the release check."""
    from . import messages as M
    rows, src = [], it.get("source") or {}
    for t in (it.get("librarian") or {}).get("tried", [])[:4]:
        kind, _, rest = str(t).partition(":")
        if rest and kind.strip() in M.SOURCES:
            rows.append(row_text(M.SOURCES[kind.strip()], rest.strip()))
    for k in ("symbol", "footprint"):
        rows.append(row_text(k, M.source_word(src.get(k)) if src.get(k) else "none yet", bool(src.get(k))))
    st = it.get("status", "")
    rows.append(row_text("release check", M.status_word(st), st in ("ready", "duplicate")))
    return rows[-limit:] if len(rows) > limit else rows


def add_part(cfg, mpn, manufacturer="", progress=None, **hints):
    """Build one part from its MPN and publish it. Returns the result dict."""
    progress = progress or Progress()
    ws = _workspace(cfg)
    bom = add_to_parts(ws.root, mpn, manufacturer, **hints)
    rel = ws.rel(bom)
    progress.step(0, 0, f"looking up {mpn}")
    s = ws.librarian_run(rel, progress=lambda d, t, text: progress.step(0, 0, text), ai=False)   # AI only after an OK
    return _part_result(cfg, ws, bom, rel, mpn, progress, s.get("ai"))


def _part_result(cfg, ws, bom, rel, mpn, progress, ai=None, ai_cost=None):
    """add_part / ai-read for one part: publish it when ready, else say why it is held (and offer the AI read)."""
    _, _, items = ws._plan(rel)
    it = _item_for(items, mpn)
    if it is None:
        return {"status": "error", "message": f"ecadlib could not add {mpn} to the library.",
                "fix": f"Check the part number. What was tried is in {ws.root / ws._librarian_dir(bom).relative_to(ws.root) / 'review.html'}"}
    if it["status"] == "ready":
        progress.step(0, 0, "putting it into the library")
    pub = (publish(ws, rel, cfg["library"], cfg["name"], cfg.get("tool", "altium")) if it["status"] == "ready"
           else {"install": [], "problems": []})
    st, nm = it["status"], it["name"]
    res = {"status": st, "part": nm, "mpn": mpn, "install": ";".join(pub.get("install") or []),
           "build": pub.get("build") or "", "refresh": pub.get("target", ""), "review": str(ws._librarian_dir(bom) / "review.md")}
    from . import messages as M
    whys = M.explain_all(it.get("hold_reasons") or [])
    if st == "ready":
        res["message"] = f"{mpn} is ready as {nm}: {_where(cfg, pub)}."
        if pub.get("footprints", {}).get(nm):
            res["footprint"] = pub["footprints"][nm]
        if pub.get("problems") or nm in (pub.get("held") or {}):
            why = f"{nm}: {pub['held'][nm]}" if nm in (pub.get("held") or {}) else pub["problems"][0]
            res.update(status="error", message=f"{mpn} was built ({nm}) but not published: {why}",
                       fix=pub.get("fix") or "run `ecadlib altium-pkg` or `ecadlib altium365 push` by hand to see more")
    elif st == "awaiting_approval":
        res.update(message=f"{mpn} was built as {nm} and is waiting for sign-off before it goes into the library.",
                   fix=f"Approve it in the review queue ({_queue_hint(cfg)}), then add it again.")
    elif st == "duplicate":
        res.update(message=f"{mpn} is already in the library as {it.get('duplicate_of') or nm}.")
    elif not_found(ws._librarian_dir(bom) / "triage.csv", mpn, it):
        res.update(status="error", reason="not_found", message=f"{mpn} was not found: no part source knows this part "
                   "number, so ecadlib has no symbol, footprint or data for it.",
                   fix="Check the part number (a typo, or a suffix missing), then add it again.")
    else:
        res.update(status="held", message=f"{mpn} is held ({nm}): " + (" ".join(w["text"] for w in whys[:3]) or "see the review."),
                   fix=(whys[0]["fix"] + " " if whys else "") + f"Open the review queue ({_queue_hint(cfg)}) "
                       "to pick a symbol or footprint, or release it anyway.")
    res.update(rows_result(cost_rows(ai_cost) + part_rows(it), {"ready": "Ready", "duplicate": "Already in your library",
                                                                "awaiting_approval": "Waiting for sign-off"}.get(st, "Held")))
    if res["status"] == "error":
        res["big"] = "Not found" if res.get("reason") == "not_found" else "Not published"
    if res["status"] == "held" and ai and nm in ai["names"]:
        q = ws.ai_quote(rel, parts=[nm]) if ai["parts"] > 1 else ai      # the menu's list may hold other parts too
        if q:
            offer_ai(res, q, rel, mpn, nm)
    if ai_cost:
        res["message"] = (res.get("message") or "") + " " + ai_cost["text"]
        res["ai_cost_text"] = ai_cost["text"]
    return res


def offer_ai(res, q, bom, mpn="", part=""):
    """result.ini keys for the dialog's AI step: the model and cost (ai_text), and, with a key, the offer to read
    (offer_ai, the button, the amount to approve, whether a second yes is needed). Nothing has been sent."""
    from . import ai_cost as AC
    res["ai_text"] = q["text"]
    if not q["key_set"]:
        res["fix"] = (res.get("fix") or "") + " " + q["text"]
        return res
    res.update(offer_ai="1", ai_bom=bom, ai_mpn=mpn, ai_part=part, ai_model=q["model"], ai_over="1" if q["over_limit"] else "0",
               ai_unknown="0" if q["price_known"] else "1",
               ai_approve="" if q["approve_usd"] is None else f"{q['approve_usd']:.3f}",
               ai_button=f"Read with AI (about {AC.money(q['usd'])})" if q["price_known"] else "Read with AI (price unknown)",
               ai_confirm=(f"Reading the datasheets can cost up to {AC.money(AC.up(q['usd_max']))} with {q['model']}, more than your "
                           f"limit of {AC.money(q['limit_usd'])} ([datasheet] {q['limit_name']}). Go ahead anyway?")
               if q["price_known"] else f"ecadlib does not know what {q['model']} costs. Read the datasheets anyway?")
    return res


def cost_rows(c):
    """The dialog rows after an AI step: the estimate and what it really cost."""
    if not c:
        return []
    from . import ai_cost as AC
    return [row_text("AI estimate", f"about {AC.money(c['estimate_usd'])}"),
            row_text("AI cost", f"{AC.money(c['actual_usd'])} ({c['reads']} read{'s' if c['reads'] != 1 else ''})", True)]


def ai_read(cfg, req, progress=None):
    """The dialog's "Read with AI": the datasheet reads of the BOM ai_bom names, held to the amount the person
    approved, then the same publish and result as add-part (ai_mpn given) or fill, with the real cost."""
    progress = progress or Progress()
    ws = _workspace(cfg)
    bom = req.get("bom", "")
    approved = req.get("approved_usd") or None
    s = ws.ai_read(bom, float(approved) if approved else None, req.get("over_limit_ok") == "1",
                   req.get("price_unknown_ok") == "1", parts=[req["part"]] if req.get("part") else None, progress=progress)
    if req.get("mpn"):
        return _part_result(cfg, ws, ws._bom_path(bom), bom, req["mpn"], progress, None, s.get("ai_cost"))
    return _fill_result(cfg, ws, bom, s, progress, s.get("ai_cost"))


def not_found(triage_csv, mpn, item):
    """True when every part source that was asked answered "no such part" (none failed or was offline), nothing local
    matched, and the librarian made neither a symbol nor a footprint: a bad MPN, not a part to review."""
    if item.get("status") != "held" or any((item.get(k) or {}).get("file") for k in ("symbol", "footprint")):
        return False
    try:
        rows = list(csv.DictReader(Path(triage_csv).open(encoding="utf-8-sig", newline="")))
    except OSError:
        return False
    row = next((r for r in rows if (r.get("mpn") or "").strip().upper() == mpn.strip().upper()), None)
    if not row or row.get("status") != "needs_footprint_generation":      # unreachable / offline / found somewhere
        return False
    if row.get("local_symbol") or row.get("local_footprint") or row.get("src_mpn") or row.get("src_description"):
        return False
    return bool(row.get("src") and row.get("src") != "easyeda") or "jlcpcb: no exact MPN match" in (row.get("notes") or "")   # a source answered


def _library_mpns(cfg, ws):
    """MPNs already in the library: the DbLib parts table (any 'part number' column) and every ready / pulled plan item.
    KiCad (cfg tool kicad): the part number properties of the library's .kicad_sym files, which travel with the
    project (the workspace may not, and a deleted library has nothing in it)."""
    have = set()
    if cfg.get("tool") == "kicad":
        for f in sorted(Path(cfg["library"]).glob("*.kicad_sym")):
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            have |= {v.replace('\\"', '"').strip().upper() for k, v in re.findall(r'\(property "([^"]*)" "((?:[^"\\]|\\.)*)"', text)
                     if v.strip() and re.search(r"\bmpn\b|part ?(number|no\b|#)|\bmfr\.? ?pn\b", k, re.I)}
        return have
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


def report_html(title, rows, counts, md_link, tool="altium"):
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
{body}</table><p><small>written by ecadlib {__version__} (ecadlib {tool}-menu check-bom)</small></p></body></html>
"""


def check_bom(cfg, project, opener=None, progress=None):
    """BOM of an Altium project -> triage + which lines the library already has -> HTML report (opened unless
    opener is False: the dialog shows the counts and offers the report)."""
    progress = progress or Progress()
    ws = _workspace(cfg)
    progress.step(0, 0, "reading the project's BOM")
    bom, pb = _bom_of(ws, project)
    tri = ws.triage(bom, progress=lambda d, t, text: progress.step(d, t, f"line {min(d + 1, t)} of {t}: {text}"
                                                                    if d < t else text))
    have = _library_mpns(cfg, ws)
    rows, counts = [], {"lines": 0, "in_library": 0, "missing": 0, "dnp": 0}
    kinds = {"source": 0, "footprint": 0, "unverified": 0, "no_pn": 0}
    for r in tri["rows"]:
        dnp = str(r.get("status", "")).startswith("dnp")
        inlib = bool(r.get("mpn")) and r["mpn"].strip().upper() in have
        rows.append({**r, "in_library": inlib})
        counts["lines"] += 1
        counts["dnp" if dnp else "in_library" if inlib else "missing"] += 1
        if not dnp and not inlib:
            st = str(r.get("status", ""))
            kinds["no_pn" if not (r.get("mpn") or r.get("lcsc"))       # nothing to look up: not a source problem
                  else "footprint" if st.startswith("needs_footprint") else "unverified" if st.startswith("unverified")
                  else "source"] += 1
    stem = Path(tri["reports"][0]).with_suffix("").name
    tool = "kicad" if cfg.get("tool") == "kicad" else "altium"
    out = ws.root / "reports" / f"{stem}_{tool}.html"
    name = pb["project"] if pb else Path(project).name
    out.write_text(report_html(f"BOM check: {name}", rows, counts, (ws.root / tri["reports"][0]).as_uri(), tool),
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
    from . import messages as M
    shown = [row_text("already in your library", counts["in_library"], True),
             row_text("a source can supply", kinds["source"]) if kinds["source"] else None,
             row_text("needs a footprint", kinds["footprint"]) if kinds["footprint"] else None,
             row_text("no part number", kinds["no_pn"]) if kinds["no_pn"] else None,
             row_text("could not check (offline)" if ws.offline else "source refused/unreachable",
                      kinds["unverified"]) if kinds["unverified"] else None,
             row_text("not fitted", counts["dnp"]) if counts["dnp"] else None]
    res.update(rows_result([r for r in shown if r], M.plural(counts["lines"], "line")))
    return res


def fill(cfg, project, progress=None):
    """Librarian on the project's BOM, then publish the ready parts."""
    progress = progress or Progress()
    ws = _workspace(cfg)
    bom, pb = _bom_of(ws, project)
    s = ws.librarian_run(bom, progress=progress, ai=False)          # paid AI reads only after the person's OK
    return _fill_result(cfg, ws, bom, s, progress)


def _fill_result(cfg, ws, bom, s, progress, ai_cost=None):
    if s["ready"]:
        progress.step(progress.total, progress.total, "putting the ready parts into the library")
    pub = publish(ws, bom, cfg["library"], cfg["name"], cfg.get("tool", "altium")) if s["ready"] else {"install": [], "problems": []}
    where = ("released to Altium 365: refresh the Components panel" if pub.get("target") == "a365"
             else f"in the {cfg['name']} KiCad library" if pub.get("target") == "kicad"
             else f"in the {cfg['name']} database library")
    msg = f"{s['parts']} parts: {s['ready']} ready" + (f" ({where})" if s["ready"] else "")
    msg += "".join(f", {n} {label}" for n, label in ((s["held"], "held"), (s["awaiting_approval"], "waiting for sign-off"),
                                                       (s["duplicates"], "duplicates")) if n)
    problems = (pub.get("problems") or []) + [f"{n}: {w}" for n, w in (pub.get("held") or {}).items()]
    res = {"status": "ok" if not problems else "error", "message": msg + ".",
           "install": ";".join(pub.get("install") or []), "build": pub.get("build") or "",
           "refresh": pub.get("target", ""), "ready": s["ready"], "held": s["held"], "review": str(ws._librarian_dir(ws._bom_path(bom)) / "review.md")}
    if problems:
        res["message"] += f" Not published: {problems[0]}"
        res["fix"] = pub.get("fix") or "run `ecadlib altium-pkg` / `ecadlib altium365 push` by hand to see more"
    elif s["held"] or s["awaiting_approval"]:
        res["fix"] = f"Open the review queue ({_queue_hint(cfg)}): it says why each part is held and what to pick."
    shown = [row_text("ready, in your library" if pub.get("target") != "a365" else "released to Altium 365", s["ready"], s["ready"] > 0)]
    shown += [row_text(label, n) for n, label in ((s["held"], "held for a person"), (s["awaiting_approval"], "waiting for sign-off"),
                                                   (s["duplicates"], "already in the library")) if n]
    held = [r for r in s.get("review", []) if r.get("status") == "held"]
    shown += [row_text(r["part"], (r.get("why") or "").split(";")[0]) for r in held[:3]]
    if len(held) > 3:
        shown.append(row_text(f"and {len(held) - 3} more held", "in the review queue"))
    res.update(rows_result(cost_rows(ai_cost) + shown, f"{s['ready']} of {s['parts']} ready"))
    res["offer_queue"] = "1" if s["held"] or s["awaiting_approval"] else "0"
    if s.get("ai"):
        offer_ai(res, s["ai"], bom)
    if ai_cost:
        res["message"] += " " + ai_cost["text"]
        res["ai_cost_text"] = ai_cost["text"]
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


def open_file(cfg, path, opener=None):
    """Open a report the dialog offers (in the browser / its default program). Only files under the workspace or the
    library folder: a request.ini is plain text anyone can write, so it never names an arbitrary program to start."""
    p = Path(path)
    roots = [Path(cfg["workspace"]).resolve(), Path(cfg["library"]).resolve()]
    try:
        rp = p.resolve()
    except OSError:
        rp = p
    if not p.is_file() or not any(rp == r or r in rp.parents for r in roots):
        return {"status": "error", "message": f"{path}: not a report in the ecadlib workspace", "fix": "check the job's log.txt"}
    if rp.suffix.lower() not in (".html", ".htm", ".md", ".csv", ".txt"):
        return {"status": "error", "message": f"{rp.name}: ecadlib opens only reports (.html, .md, .csv, .txt)", "fix": ""}
    if not _open(rp, opener):
        return {"status": "error", "message": f"could not open {rp}", "fix": "open that file yourself"}
    return {"status": "ok", "message": f"opened {rp.name}"}


def probe_ui(port, host="127.0.0.1", timeout=1.5):
    """What answers on the port: ("ecadlib", workspace) | ("ecadlib-team", None) | ("other", None) | (None, None)."""
    import urllib.request, urllib.error
    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a system proxy
        with direct.open(f"http://{host}:{port}/api/info", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return ("ecadlib", data.get("workspace")) if isinstance(data, dict) and "workspace" in data else ("other", None)
    except urllib.error.HTTPError as e:
        return ("ecadlib-team", None) if e.code in (401, 403) else ("other", None)
    except (OSError, ValueError):
        return (None, None)


def start_ui(ws_root, port):
    """`ecadlib ui --workspace W --port P --no-browser`, detached, no window; output in altium_menu_ui.log."""
    from .userconfig import self_command                  # this Python, or the Windows app's bundled ecadlib.exe
    cmd = self_command() + ["ui", "--workspace", str(ws_root), "--port", str(port), "--no-browser"]
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


def queue_anchor(ws):
    """Where "Open review queue" lands: the menu's own parts (held ones first, each with why it is held) once a part
    was added from the menu; they never reach the library agent's queue (#queuecard), which listed "Nothing in the
    queue" while parts were held."""
    from urllib.parse import quote
    return f"#review={quote(MENU_BOM)}" if (Path(ws) / MENU_BOM).is_file() else QUEUE_ANCHOR


def open_queue(cfg, opener=None, probe=probe_ui, launcher=start_ui, wait_s=30.0, sleep=time.sleep):
    """Open the web UI at the review queue, starting `ecadlib ui` for the workspace when nothing answers."""
    port, ws = cfg["port"], Path(cfg["workspace"])
    kicad = cfg.get("tool") == "kicad"
    for p in range(port, port + 20) if kicad else ():   # KiCad reads no settings file: one UI per project, on the first
        k, served = probe(p)                            # port that serves this workspace or is free
        if k is None or (k == "ecadlib" and served and Path(served).resolve() == ws.resolve()):
            port = p
            break
    url = f"http://127.0.0.1:{port}/{queue_anchor(ws)}"
    kind, served = probe(port)
    started = False
    if kind == "other":
        return {"status": "error", "message": f"port {port} is used by another program",
                "fix": "close the program on that port" if kicad else f"set port= to a free port in {INI}"}
    if kind == "ecadlib" and served and Path(served).resolve() != ws.resolve():
        return {"status": "error", "message": f"port {port} already serves another ecadlib workspace ({served})",
                "fix": "stop that `ecadlib ui`" + ("" if kicad else f", or set port= to another port in {INI}")}
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


def run_action(cfg, action, req, opener=None, progress=None):
    if action == "add-part":
        hints = {k: req.get(k, "") for k in ("value", "footprint", "lcsc", "description", "datasheet", "designator")}
        return add_part(cfg, req.get("mpn", ""), req.get("manufacturer", ""), progress=progress, **hints)
    if action == "check-bom":                  # the dialog (dialog=1) shows the counts and opens the report on request
        return check_bom(cfg, req.get("project", ""), opener=False if req.get("dialog") == "1" else opener,
                         progress=progress)
    if action == "fill":
        return fill(cfg, req.get("project", ""), progress=progress)
    if action == "ai-read":                    # only after the dialog showed the cost and the person said yes
        return ai_read(cfg, req, progress=progress)
    if action == "open":                       # a report or review the dialog offers: only files in the workspace
        return open_file(cfg, req.get("path", ""), opener=opener)
    if action == "queue":
        return open_queue(cfg, opener=opener)
    raise ValueError(f"unknown action {action!r} (one of {', '.join(ACTIONS)})")


def _error(e):
    msg = str(e) or type(e).__name__
    fix = ""
    if isinstance(e, FileNotFoundError) or "no such file" in msg:
        fix = "save the project, then try again"
    elif "NEXAR_TOKEN" in msg:
        fix = "store your Nexar token on the setup page (Start menu > ecadlib setup) or with `ecadlib keys set NEXAR_TOKEN`, then try again"
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
        res = run_action(cfg, req.get("action", ""), req, opener=opener, progress=Progress(job))
    except Exception as e:                                   # every failure becomes one dialog line with a fix
        res = _error(e)
    write_result(job / "result.ini", res)
    if job.parent.name.lower() == "jobs":                    # the script's jobs\<id>: never prune a folder chosen by hand
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
    m = sub.add_parser("install-menu", help="put the ecadlib menu into Altium's top menu bar (Altium closed)")
    m.add_argument("--out", default="out/altium_menu", help="the folder `build` wrote (default %(default)s)")
    m.add_argument("--altium-folder", action="append", help="an Altium settings folder (default: every "
                   "%%APPDATA%%\\Altium\\Altium Designer {...})")
    m.add_argument("--remove", action="store_true", help="take the ecadlib menu out again")
    a = ap.parse_args(argv)
    if a.cmd == "install-menu":
        try:
            rep = install_menu_bar(a.out, a.altium_folder, remove=a.remove)
        except (OSError, ValueError) as e:
            print(f"ecadlib altium-menu: {e}", file=sys.stderr)
            return 2
        what = rep.get("removed") if a.remove else rep.get("installed")
        print(("removed the ecadlib menu from " if a.remove else "added the ecadlib menu to ") + (", ".join(what) or
              "nothing (already as wanted)") + "".join(f"\n  backup: {b}" for b in rep["backups"]))
        if not a.remove:
            print("  start Altium: the ecadlib menu is in the schematic, PCB and library editors, before Window")
        return 0
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
                  f"then Customize to put {', '.join(rep['menu_entries'])} on a menu (see {README})")
            for p, u, d in BUILD_PROCS:
                print(f"  also in the project: {p} ({u}): {d}")
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
