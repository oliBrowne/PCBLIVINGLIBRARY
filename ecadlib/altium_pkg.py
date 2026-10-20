"""Hand the library to Altium Designer: an integrated-library package (.LibPkg) and an install script project.

    ecadlib altium-pkg out/librarian/plan.json --out D:/libs/ecadlib [--name ecadlib] [--win-dir D:\\libs\\ecadlib]
    ecadlib altium-pkg out/altium_models --out out/altium_pkg      # any folder of .SchLib / .PcbLib (+ .DbLib)

Writes into --out (a plan first goes through `ecadlib dblib-sync`, so the folder also holds the DbLib and its
parts workbook):
  SchLib/<ref>.SchLib, PcbLib/<ref>.PcbLib   the models (copied in when they come from elsewhere)
  <name>.LibPkg                 integrated-library package listing every .SchLib and .PcbLib by relative path
                                (SchLib\\x.SchLib): open it in Altium and compile it into <name>.IntLib
  <name>_Install.PrjScr + .pas  DelphiScript project: run InstallDatabaseLibrary, BuildIntegratedLibrary,
                                InstallFileLibraries or UninstallAll from File > Run Script. When the folder has
                                build recipes (ecadlib_build.txt, see ecadlib.altium_native), each install first lets
                                Altium rebuild every .SchLib / .PcbLib with its own API (RebuildWithAltium), so the
                                files Altium installs are ones Altium wrote; --no-native-build leaves that out
  ALTIUM_README.txt             what to click, in order
  altium_pkg.json               what was written and the checks

Every path in these files is relative to the folder (the script works out the folder from its own location with
GetRunningScriptProjectName), and uses Windows separators, so the folder can be moved, zipped or put on a share.

Checked here (no Altium): the files follow real Altium-authored ones (LibPkg: particle-iot/hardware-libraries,
blueintegral/Level with SchLib in sub folders; PrjScr: Altium-Designer-addons/scripts-libraries QuickSilk); every
document the LibPkg lists exists and reads back with ecadlib's reader; every footprint a symbol links to is in one
of the package's PcbLibs (what the IntLib compiler looks for); every [Library Path] / [Footprint Path] in the DbLib's
table resolves from the DbLib's folder. Not checked: that Altium compiles the package or runs the script (both
written from Altium's documented scripting API and the examples above, never run)."""
import argparse, contextlib, json, os, shutil, sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import altium_lib as AL

MODEL_EXT = {".schlib": "SchLib", ".pcblib": "PcbLib"}
COPY_EXT = {".schlib", ".pcblib", ".dblib", ".svndblib", ".xlsx", ".xls", ".csv", ".intlib", ".ecadbuild", ".step", ".stp"}

# [Design] and [DocumentN] keys as Altium writes them in a .LibPkg (particle-iot/hardware-libraries Particle-Devices.LibPkg)
DESIGN = ["Version=1.0", "HierarchyMode=0", "ChannelRoomNamingStyle=0", "ReleasesFolder=",
          "ChannelDesignatorFormatString=$Component_$RoomName", "ChannelRoomLevelSeperator=_", "OpenOutputs=1",
          "ArchiveProject=0", "TimestampOutput=0", "SeparateFolders=0", "TemplateLocationPath=", "PinSwapBy_Netlabel=1",
          "PinSwapBy_Pin=1", "AllowPortNetNames=0", "AllowSheetEntryNetNames=1", "AppendSheetNumberToLocalNets=0",
          "NetlistSinglePinNets=0", "DefaultConfiguration=Default - All Constraints", "UserID=0xFFFFFFFF",
          "DefaultPcbProtel=1", "DefaultPcbPcad=0", "ReorderDocumentsOnCompile=1", "NameNetsHierarchically=0",
          "PowerPortNamesTakePriority=0", "PushECOToAnnotationFile=1", "DItemRevisionGUID=",
          "ReportSuppressedErrorsInMessages=0", "FSMCodingStyle=eFMSDropDownList_OneProcess",
          "FSMEncodingStyle=eFMSDropDownList_OneHot", "OutputPath=", "LogFolderPath=", "ManagedProjectGUID=",
          "LinkedManagedProjectGUID=", "IncludeDesignInRelease=0"]
DOCUMENT = ["AnnotationEnabled=1", "AnnotateStartValue=1", "AnnotationIndexControlEnabled=0", "AnnotateSuffix=",
            "AnnotateScope=All", "AnnotateOrder=-1", "DoLibraryUpdate=1", "DoDatabaseUpdate=1", "ClassGenCCAutoEnabled=1",
            "ClassGenCCAutoRoomEnabled=1", "ClassGenNCAutoScope=None", "DItemRevisionGUID=", "GenerateClassCluster=0",
            "DocumentUniqueId="]


def win_rel(path, base):
    """`path` relative to `base`, with Windows separators (SchLib\\x.SchLib)."""
    return str(PureWindowsPath(*PurePosixPath(Path(os.path.relpath(path, base)).as_posix()).parts))


def libpkg_text(docs):
    """A .LibPkg (INI, CRLF) listing `docs` (relative Windows paths), SchLibs first as Altium orders them."""
    L = ["[Design]", *DESIGN, "", "[Preferences]", "PrefsVaultGUID=", "PrefsRevisionGUID=", ""]
    for i, d in enumerate(docs, 1):
        L += [f"[Document{i}]", f"DocumentPath={d}", *DOCUMENT, ""]
    return "\r\n".join(L)


def prjscr_text(pas, *more):
    """Script project as Altium saves one (Altium-Designer-addons/scripts-libraries QuickSilk.PrjScr)."""
    L = ["[Design]", "Version=1.0", ""]
    for i, d in enumerate((pas, *more), 1):
        L += [f"[Document{i}]", f"DocumentPath={d}", ""]
    return "\r\n".join(L)


def _pas_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def install_script(name, dblibs, libpkg, files, native=False):
    """DelphiScript with the four procedures; every path is relative to the script project's folder. native: the
    project also holds ecadlib_build.pas, and every install first lets Altium rebuild the libraries (RebuildWithAltium)."""
    add = lambda var, xs: "\n".join(f"    {var}.Add({_pas_str(x)});" for x in xs) or f"    {{ none }}"
    intlib = f"Project Outputs for {Path(libpkg).stem}\\{Path(libpkg).stem}.IntLib"
    # user-chosen names go into a { } comment (a '}' would end it) and into '...' literals (a lone ' would end those)
    c = lambda v: str(v).replace("}", ")")
    sname, spkg = _pas_str(name)[1:-1], _pas_str(libpkg)[1:-1]
    native_doc = ("Altium writes every .SchLib / .PcbLib of this folder again with its own API, from ecadlib_build.txt"
                  if native else "(does nothing here: this package was written without build recipes)")
    native_body = "    { no build recipes in this package }" if not native else "\n".join([
        "    If Not FileExists(Folder + 'ecadlib_build.txt') Then",
        "        Exit;",
        "    BuildLibraries(Folder);",
        "    R := TStringList.Create;",
        "    If FileExists(Folder + 'ecadlib_build_report.txt') Then",
        "        R.LoadFromFile(Folder + 'ecadlib_build_report.txt');",
        "    For i := 0 To R.Count - 1 Do",
        "        If Pos('FAILED', R.Strings[i]) = 1 Then",
        "            Report.Add('Altium could not rebuild: ' + R.Strings[i]);",
        "    Report.Add('rebuilt by Altium: ' + IntToStr(R.Count) + ' libraries (ecadlib_build_report.txt)');",
        "    R.Free;"])
    return f"""{{ Install the {c(name)} library (written by ecadlib altium-pkg; paths are relative to this folder).

  In Altium Designer: File > Open... > {c(name)}_Install.PrjScr, then File > Run Script... and pick one:
    InstallDatabaseLibrary   adds the database library ({c(', '.join(dblibs) or 'none in this folder')}) to the installed
                             libraries; the Components panel then lists every part with its parameters
    BuildIntegratedLibrary   opens {c(libpkg)}, compiles it into an .IntLib and installs that
    InstallFileLibraries     adds every .SchLib and .PcbLib of this folder (one per part)
    UninstallAll             removes every library of this folder from the installed list
    RebuildWithAltium        {native_doc}
  Re-running is safe: a library already installed is removed and added again, so Altium re-reads it.
  The folder can be moved or copied: paths are worked out from where this script project is. }}

Var
    Report : TStringList;

Function Folder : String;
Begin
    Result := ExtractFilePath(GetRunningScriptProjectName);
End;

Function DbLibs : TStringList;
Begin
    Result := TStringList.Create;
{add("Result", dblibs)}
End;

Function ModelFiles : TStringList;
Begin
    Result := TStringList.Create;
{add("Result", files)}
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

Procedure InstallOne(Path : String);
Begin
    If Not FileExists(Path) Then
    Begin
        Report.Add('missing: ' + Path);
        Exit;
    End;
    If IsInstalled(Path) Then
        IntegratedLibraryManager.UninstallLibrary(Path);
    IntegratedLibraryManager.InstallLibrary(Path);
    Report.Add('installed: ' + Path);
End;

Procedure Finish(Title : String);
Begin
    ResetParameters;
    RunProcess('IntegratedLibrary:RefreshInstalledLibraries');
    ShowMessage(Title + #13#10 + #13#10 + Report.Text);
    Report.Free;
End;

Procedure NativeBuild;
Var
    R : TStringList;
    i : Integer;
Begin
{native_body}
End;

Procedure RebuildWithAltium;
Begin
    Report := TStringList.Create;
    NativeBuild;
    Finish('{sname}: libraries rebuilt by Altium');
End;

Procedure InstallDatabaseLibrary;
Var
    L : TStringList;
    i : Integer;
Begin
    Report := TStringList.Create;
    NativeBuild;
    L := DbLibs;
    If L.Count = 0 Then
        Report.Add('No .DbLib in this folder: run `ecadlib altium-pkg plan.json` or `ecadlib dblib-sync` first.');
    For i := 0 To L.Count - 1 Do
        InstallOne(Folder + L.Strings[i]);
    L.Free;
    Finish('{sname}: database library');
End;

Procedure InstallFileLibraries;
Var
    L : TStringList;
    i : Integer;
Begin
    Report := TStringList.Create;
    NativeBuild;
    L := ModelFiles;
    For i := 0 To L.Count - 1 Do
        InstallOne(Folder + L.Strings[i]);
    L.Free;
    Finish('{sname}: symbol and footprint libraries');
End;

Procedure BuildIntegratedLibrary;
Var
    WS : IWorkspace;
    Prj : IProject;
    IntLib : String;
Begin
    Report := TStringList.Create;
    NativeBuild;
    WS := GetWorkspace;
    Prj := WS.DM_OpenProject(Folder + {_pas_str(libpkg)}, True);
    If Prj = Nil Then
        Report.Add('Could not open ' + Folder + {_pas_str(libpkg)})
    Else
    Begin
        Prj.DM_Compile;
        IntLib := Folder + {_pas_str(intlib)};
        If Not FileExists(IntLib) Then
            IntLib := Folder + {_pas_str(Path(libpkg).stem + '.IntLib')};
        If FileExists(IntLib) Then
            InstallOne(IntLib)
        Else
            Report.Add('No .IntLib after compiling: open {spkg}, right-click it in the Projects panel > ' +
                       'Compile Integrated Library, and read the Messages panel.');
    End;
    Finish('{sname}: integrated library');
End;

Procedure UninstallAll;
Var
    i : Integer;
    P : String;
Begin
    Report := TStringList.Create;
    For i := IntegratedLibraryManager.InstalledLibraryCount - 1 DownTo 0 Do
    Begin
        P := IntegratedLibraryManager.InstalledLibraryPath(i);
        If Pos(UpperCase(Folder), UpperCase(P)) = 1 Then
        Begin
            IntegratedLibraryManager.UninstallLibrary(P);
            Report.Add('removed: ' + P);
        End;
    End;
    Finish('{sname}: uninstalled');
End;
""".replace("\n", "\r\n")


def readme_text(name, libpkg, dblibs, n_sch, n_pcb):
    return f"""{name}: Altium Designer hand-off (written by `ecadlib altium-pkg`)

This folder can be moved or copied anywhere (a network share, another PC): every path in it is relative.

1. Database library (parts with parameters, one row per part)
   {'Open ' + dblibs[0] + ' in Altium and check the connection, or run InstallDatabaseLibrary (step 3).' if dblibs else 'No .DbLib here: build from a plan.json to get one.'}
   The DbLib reads its parts table from the workbook next to it (DatabasePathRelative=1) and finds symbols and
   footprints by the relative [Library Path] / [Footprint Path] in that table (SchLib\\x.SchLib, PcbLib\\x.PcbLib).
   Excel connections need the Microsoft Access Database Engine (ACE) with the same bitness as Altium (64-bit).

2. Integrated library ({n_sch} symbol and {n_pcb} footprint libraries)
   File > Open... > {libpkg}, then right-click it in the Projects panel > Compile Integrated Library.
   Altium writes "Project Outputs for {Path(libpkg).stem}\\{Path(libpkg).stem}.IntLib"; install it from the Components panel
   (... > File-based Libraries Preferences > Install).

3. Or let the script do 1 and 2: File > Open... > {name}_Install.PrjScr, then File > Run Script... and pick
   InstallDatabaseLibrary, BuildIntegratedLibrary, InstallFileLibraries or UninstallAll.

What to expect: the Components panel lists the library; placing a part shows its symbol with the footprint linked.
None of this was opened in Altium when it was written: if a step fails, the Messages panel says why; please report it.
""".replace("\n", "\r\n")


# ---------------------------------------------------------------- build
def collect(src, out, kept=None, overwrite_edits=False):
    """Copy library files under `src` into `out` (same relative layout) unless they are the same folder.
    Returns the list of paths in `out`. A library someone changed in Altium since ecadlib's copy was built there is
    left as it is, with its recipe, unless overwrite_edits; kept (a list) gets a note for each (altium_native)."""
    from . import altium_native as AN
    src, out = Path(src).resolve(), Path(out).resolve()
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in COPY_EXT
             and not (out != src and p.is_relative_to(out))]
    if src == out:
        return files
    got, leave = [], set()
    for p in files:
        dst = out / p.relative_to(src)
        recipe = p.with_name(p.name + AN.EXT)
        if p.suffix.lower() == AN.EXT and p.with_name(p.name[:-len(AN.EXT)]) in leave:
            got.append(dst)                                   # the kept library's recipe stays with it
            continue
        if not dst.is_file() or dst.read_bytes() != p.read_bytes():
            act = (AN.replace_action(p.read_bytes(), dst, overwrite_edits)
                   if p.suffix.lower() in MODEL_EXT and recipe.is_file() else "copy")
            if act != "copy":
                leave.add(p)
                if act == "edited" and kept is not None:
                    kept.append(f"{dst.relative_to(out).as_posix()} {AN.EDITED_NOTE}")
                got.append(dst)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
        if p.suffix.lower() in MODEL_EXT:
            if recipe.is_file():
                AN.record(dst, AN._sha(p.read_bytes()))      # ecadlib's bytes: Altium builds them next
            else:
                for ext in (AN.EXT, AN.STATE_EXT):            # a recipe from an earlier copy: Altium would rebuild
                    stale = dst.with_name(dst.name + ext)     # this library from it (altium_native)
                    if stale.is_file():
                        stale.unlink()
        got.append(dst)
    return got


def library_of(src):
    """The plan.json of the ecadlib library a folder of Altium files comes from, or None: the folder itself or one of
    its two parents (a library's models/ or models/SchLib/), else the plan an earlier altium-pkg named as its source
    (altium_pkg.json in the folder)."""
    src = Path(src).resolve()
    for d in (src, src.parent, src.parent.parent):
        if (d / "plan.json").is_file():
            return d / "plan.json"
    try:
        s = json.loads((src / "altium_pkg.json").read_text(encoding="utf-8")).get("source")
    except (OSError, ValueError, AttributeError):
        s = None
    for p in ([Path(s), src / s] if s else []):
        if p.suffix.lower() == ".json" and p.is_file():
            return p.resolve()
        if p.is_dir() and p.resolve() != src:
            return next((d / "plan.json" for d in (p.resolve(), p.resolve().parent, p.resolve().parent.parent)
                         if (d / "plan.json").is_file()), None)
    return None


def check_released(src, plan, rules=None):
    """Change control (#105) for a folder of Altium files: the library is settled first (a hand edit of a released
    part is held and its released files put back, as every publisher does), then every .SchLib / .PcbLib in the folder
    that is a copy of a released part's file must still be that part: same bytes, or a fingerprint with nothing more
    than cosmetic changes (Altium's own rebuild writes other bytes). A file that differs is refused with the part and
    what changed. Files of no released part (new parts, your own libraries) are packaged as before. Raises ValueError."""
    from . import change_diff as CD, change_gate as CG, fingerprint as FP, ledger as LG
    src, plan = Path(src).resolve(), Path(plan)
    lib = plan.parent.resolve()
    items = CG.settled_plan(plan, "altium-pkg", rules=rules, log=lambda *a, **k: None)
    lg = LG.Ledger(lib)
    by_name = {}
    for it in items:
        head = lg.head(it["name"])
        for half in ("symbol", "footprint") if head else ():
            f = (it.get(half) or {}).get("file")
            if f:
                by_name.setdefault(Path(f).name.lower(), []).append((it, half, head))
    bad = []
    for f in sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in MODEL_EXT):
        for it, half, head in by_name.get(f.name.lower(), []):
            mine = Path(it[half]["file"]) if Path(it[half]["file"]).is_absolute() else lib / it[half]["file"]
            if mine.resolve() == f or (mine.is_file() and mine.read_bytes() == f.read_bytes()):
                continue
            probe = json.loads(json.dumps(it, default=str))
            probe[half] = dict(probe[half], file=str(f))
            kmod = FP.kicad_mod_path(it, lib)  # the library's .kicad_mod, not one looked for beside the copy
            if half == "footprint" and kmod:
                probe["footprint"]["kicad_mod"] = str(kmod.resolve())
            try:
                changes = [c for c in CD.diff(lg.fingerprint(head), FP.of_item(probe, lib)) if c["class"] != "cosmetic"]
            except Exception as e:             # unreadable: not the released file
                changes = [{"class": "electrical", "text": f"unreadable ({type(e).__name__})"}]
            if changes:
                bad.append(f"{f.relative_to(src).as_posix()}: {it['name']} r{head['revision']} {CD.summary(changes)}")
    if bad:
        raise ValueError(f"{src}: not packaged: files differ from the released revisions of {plan}: "
                         + "; ".join(bad[:8]) + (f" and {len(bad) - 8} more" if len(bad) > 8 else "")
                         + ". A change goes through the library (ecadlib part status / approve); package the plan.json "
                           "to get the released files")


def check_folder(out, libpkg, dblibs):
    """Problems a user would hit in Altium that can be seen without it. Returns (problems, facts)."""
    out = Path(out)
    probs, facts = [], {"symbols": 0, "footprints": 0, "links": 0, "dblib_rows": 0}
    docs = [ln.split("=", 1)[1] for ln in (out / libpkg).read_text(encoding="utf-8").splitlines() if ln.startswith("DocumentPath=")]
    names = {"SchLib": set(), "PcbLib": set()}
    links = []
    for d in docs:
        p = out / Path(*PureWindowsPath(d).parts)
        if not p.is_file():
            probs.append(f"{libpkg}: {d} is not in the folder")
            continue
        kind = MODEL_EXT.get(p.suffix.lower())
        try:
            if kind == "SchLib":
                for c in AL.read_schlib(p)[1]:
                    names["SchLib"].add(c["name"])
                    links += [(c["name"], r.get("MODELNAME")) for r in c.get("records") or []
                              if r.get("RECORD") == "45" and r.get("MODELTYPE") == "PCBLIB"]
            elif kind == "PcbLib":
                names["PcbLib"] |= {f["name"] for f in AL.read_pcblib(p)}
        except Exception as e:
            probs.append(f"{d} does not read back: {type(e).__name__}: {e}")
    facts["symbols"], facts["footprints"], facts["links"] = len(names["SchLib"]), len(names["PcbLib"]), len(links)
    for sym, fp in links:
        if fp and fp not in names["PcbLib"]:
            probs.append(f"symbol {sym} links footprint {fp}, which no PcbLib in the package holds")
    from .altium_dblib_sync import dblib_table_paths
    for db in dblibs:
        csvf = dblib_table_paths(out / db)[1]            # the table the DbLib names ('Bibliothèque' -> 'Bibliotheque.csv')
        if not csvf.is_file():
            continue
        import csv
        for row in csv.DictReader(csvf.open(encoding="utf-8", newline="")):
            facts["dblib_rows"] += 1
            for col, refc, kind in (("Library Path", "Library Ref", "SchLib"), ("Footprint Path", "Footprint Ref", "PcbLib")):
                rel = row.get(col) or ""
                if not rel:
                    continue
                if PureWindowsPath(rel).is_absolute() or rel.startswith(("\\\\", "/")):
                    probs.append(f"{db}: {row.get('Part Number')} {col} {rel} is absolute (breaks when the folder moves)")
                    continue
                p = (out / db).parent / Path(*PureWindowsPath(rel).parts)
                if not p.is_file():
                    probs.append(f"{db}: {row.get('Part Number')} {col} {rel} does not exist")
                    continue
                ref = row.get(refc)
                have = ({c["name"] for c in AL.read_schlib(p)[1]} if kind == "SchLib" else {f["name"] for f in AL.read_pcblib(p)})
                if ref and ref not in have:
                    probs.append(f"{db}: {row.get('Part Number')} {refc} {ref} is not in {rel}")
        from .fsutil import read_ini_text              # ANSI or UTF-8/16 with a BOM, as Altium may save it
        text = read_ini_text(out / db)[0]
        if "DatabasePathRelative=1" not in text:
            probs.append(f"{db}: the database path is absolute (DatabasePathRelative=0): breaks when the folder moves")
    return probs, facts


def check_name(name):
    """The package name goes into file names the install scripts open: it must fit cp1252 (see script_bytes)."""
    try:
        str(name).encode("cp1252")
    except UnicodeEncodeError as e:
        raise ValueError(f"package name {name!r} has characters the Altium install script can't hold "
                         f"({str(name)[e.start:e.end]!r} is not in Windows-1252); pick another --name") from None


def script_bytes(fname, text):
    """<name>_Install.PrjScr / .pas in cp1252: Altium reads both as ANSI, so in UTF-8 'Bibliothèque' became
    'BibliothÃ¨que' (script not found, DbLib not found)."""
    try:
        return text.encode("cp1252")
    except UnicodeEncodeError as e:
        line = text[:e.start].count("\n") + 1
        raise ValueError(f"{fname} line {line}: {text[e.start:e.end]!r} is not in Windows-1252, which Altium reads "
                         f"this file in; rename the file or library that brings it in") from None


def ecadlib_made(src):
    """Files in a folder that say ecadlib wrote its libraries (a DbLib sync record, build recipes, edit records)."""
    src = Path(src)
    marks = [src / "ecadlib_sync.json"] if (src / "ecadlib_sync.json").is_file() else []
    for pat in ("*.ecadbuild", "*.ecadstate"):
        marks += sorted(src.rglob(pat))[:3]
    return marks


def build(src, out, name="ecadlib", win_dir=None, rules=None, log=print, native=True, plan=None, unchecked=False):
    """Write the Altium hand-off folder; returns the report dict (also altium_pkg.json). native: write the build
    recipes as ecadlib_build.txt + ecadlib_build.pas so the install script lets Altium write the libraries itself.
    plan: for a folder, the plan.json of the ecadlib library its files come from (else found beside the folder or in
    an earlier altium_pkg.json). unchecked: package a folder of ecadlib-made files whose library is not known without
    checking them against the released revisions (the report and the output say so)."""
    src, out = Path(src), Path(out)
    check_name(name)
    out.mkdir(parents=True, exist_ok=True)
    rep = {"source": str(src), "folder": str(out), "name": name, "sync": None}
    if src.is_file():
        from . import altium_dblib_sync as DS
        res = DS.sync(src, out, "dblib", name, win_dir=win_dir, rules=rules)
        rep["sync"] = DS.summary(res)
        bad = [r for r in res["results"] if r["action"] == "error"]
        rep["sync_errors"] = [f"{r['name']}: {r['detail']}" for r in bad]
        rep["recheck"] = {r["name"]: "held: " + r["detail"] for r in res["results"] if r.get("recheck")}   # held by the check again
        rep["kept_edits"] = [f"{r['name']}: {n}" for r in res["results"] for n in (r.get("detail") or "").split("; ")
                             if "changed in Altium" in n]
        files = sorted(p for p in out.rglob("*") if p.is_file())
    elif src.is_dir():
        kept = []
        from . import change_gate as CG
        if plan and not Path(plan).is_file():
            raise FileNotFoundError(f"{plan}: no such plan.json")
        lib = Path(plan).resolve() if plan else library_of(src)
        with CG.publishing(lib.parent) if lib else contextlib.nullcontext():
            if lib:                            # change control (#105): only the released revisions leave the library
                check_released(src, lib, rules)
            elif ecadlib_made(src) and not unchecked:
                marks = ", ".join(m.relative_to(src).as_posix() for m in ecadlib_made(src)[:3])
                raise ValueError(f"{src}: these are ecadlib-made libraries ({marks}) but their library is not known (no "
                                 f"plan.json beside them, no altium_pkg.json naming it), so they cannot be checked "
                                 f"against the released revisions: name it with --plan PLAN, package the plan.json "
                                 f"itself, or pass --unchecked to package them as they are")
            files = collect(src, out, kept)
        rep["kept_edits"] = kept
        rep["checked"] = str(lib) if lib else None
        if not lib and unchecked:
            rep["unchecked"] = True
    else:
        raise FileNotFoundError(f"{src}: no such plan.json or folder")
    models = sorted((p for p in files if p.suffix.lower() in MODEL_EXT and p.resolve().is_relative_to(out.resolve())),
                    key=lambda p: (MODEL_EXT[p.suffix.lower()] != "SchLib", win_rel(p, out).lower()))
    if not models:
        raise ValueError(f"no .SchLib or .PcbLib under {src}: run the librarian (or `ecadlib altium-lib`) first")
    docs = [win_rel(p, out) for p in models]
    dblibs = sorted(win_rel(p, out) for p in files if p.suffix.lower() in (".dblib", ".svndblib")
                    and p.resolve().is_relative_to(out.resolve()))
    libpkg = f"{name}.LibPkg"
    written = []
    from . import altium_native as AN
    native = bool(native and AN.recipes(out))
    if native:
        nat = AN.write(out)
        written += nat["written"]
        rep.update(native_up_to_date=nat["up_to_date"], native_edited=nat["edited"])
    more = (AN.UNIT,) if native else ()
    outs = [(libpkg, libpkg_text(docs).encode("utf-8")),        # all encoded before any is written: a script cp1252
            (f"{name}_Install.PrjScr", script_bytes(f"{name}_Install.PrjScr", prjscr_text(f"{name}_Install.pas", *more))),
            (f"{name}_Install.pas", script_bytes(f"{name}_Install.pas", install_script(name, dblibs, libpkg, docs, native))),
            ("ALTIUM_README.txt", readme_text(name, libpkg, dblibs, sum(1 for d in docs if d.lower().endswith(".schlib")),
                                              sum(1 for d in docs if d.lower().endswith(".pcblib"))).encode("utf-8")
                                  + (b"\nNOT CHECKED: packaged with --unchecked. These libraries were not compared with "
                                     b"any released revision of an ecadlib library.\n" if rep.get("unchecked") else b""))]
    for fname, data in outs:                                    # can't hold leaves no half-written package
        p = out / fname
        if not p.is_file() or p.read_bytes() != data:
            p.write_bytes(data)
            written.append(fname)
    probs, facts = check_folder(out, libpkg, dblibs)
    rep.update(libpkg=libpkg, script=f"{name}_Install.PrjScr", dblibs=dblibs, documents=docs, written=written, native_build=native,
               problems=probs + rep.get("sync_errors", []), **facts)
    (out / "altium_pkg.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ecadlib altium-pkg",
        description="Altium hand-off folder: .LibPkg (compile to .IntLib), install script project, DbLib (from a plan)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  ecadlib altium-pkg out/librarian/plan.json --out out/altium_pkg --name mylib\n"
               "  ecadlib altium-pkg out/altium_models --out out/altium_pkg\n"
               "then in Altium: open <name>_Install.PrjScr and run InstallDatabaseLibrary, or open <name>.LibPkg and compile it")
    ap.add_argument("src", help="librarian plan.json, or a folder with .SchLib / .PcbLib files (and a .DbLib)")
    ap.add_argument("-o", "--out", default="out/altium_pkg", help="output folder (default %(default)s)")
    ap.add_argument("--name", default="ecadlib", help="library name (default %(default)s)")
    ap.add_argument("--win-dir", help="with a plan: Windows folder for the DbLib's connection string (Altium rewrites it "
                                      "from the relative workbook path anyway)")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan)")
    ap.add_argument("--no-native-build", action="store_true", help="leave out the build recipes: Altium installs the "
                    ".SchLib / .PcbLib files ecadlib wrote instead of rebuilding them with its own API first")
    ap.add_argument("--plan", help="with a folder: the plan.json of the ecadlib library its files come from (they are "
                                   "checked against its released revisions)")
    ap.add_argument("--unchecked", action="store_true", help="with a folder of ecadlib-made files whose library is "
                    "not known: package them without checking them against released revisions")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    try:
        rep = build(a.src, a.out, a.name, a.win_dir, a.rules, native=not a.no_native_build, plan=a.plan,
                    unchecked=a.unchecked)
    except (OSError, ValueError) as e:
        print(f"ecadlib altium-pkg: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, indent=2))
        return 1 if rep["problems"] else 0
    from . import messages as M, term as TM
    t = TM.Term()
    for p in rep["problems"]:
        t.note(M.clean(p), "fix the part in the plan (ecadlib review), then run altium-pkg again")
    if rep.get("unchecked"):
        t.note("--unchecked: these libraries were packaged as they are, not checked against any released revision",
               "package the library's plan.json (or pass --plan) for a checked package")
    if rep["sync"]:
        t.detail(f"  database library: {rep['sync']}")
    t.summary(f"{len(rep['documents'])} Altium libraries packed ({M.plural(rep['symbols'], 'symbol')}, "
              f"{M.plural(rep['footprints'], 'footprint')}, {rep['links']} symbol-footprint links checked)"
              + (f"; {rep['sync']}" if rep["sync"] else ""),
              written=[(f"{Path(a.out) / rep['libpkg']}", "compile this in Altium to get an .IntLib"),
                       (f"{Path(a.out) / rep['script']}", "or open this script project and run InstallDatabaseLibrary")]
              + [(f"{Path(a.out) / d}", "database library") for d in rep["dblibs"]]
              + [(f"{Path(a.out) / 'ALTIUM_README.txt'}", "the steps to take in Altium")],
              next=[("ecadlib altium-menu build --out altium_menu", "an 'ecadlib' menu inside Altium (add part, check BOM)")])
    return 1 if rep["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
