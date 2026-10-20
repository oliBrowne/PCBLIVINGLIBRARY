{ ecadlib menu for Altium Designer (written by `ecadlib altium-menu build`, ecadlib @VERSION@).

  Three entries, each a procedure without parameters (Customize > [Scripts] and File > Run Script list them):
    AddPartFromMPN    asks for a manufacturer part number, builds it with ecadlib into your library folder (or
                      Altium 365 when ecadlib.toml names a workspace) and reinstalls the database library
    CheckProjectBOM   checks the BOM of the focused project, opens the report and offers to fill the library
    OpenReviewQueue   opens the ecadlib web UI at the review queue (starts it when it is not running)
  ecadlib_build.pas in this project adds BuildLibrariesInAltium (File > Run Script) and BuildLibraries(Folder),
  which Reinstall calls so Altium writes the library files itself.

  How: each entry writes jobs\<id>\request.ini next to this script, runs ecadlib_run.cmd (without a console window
  through ecadlib_run.vbs unless hidden=0 in ecadlib_menu.ini), waits for jobs\<id>\exit.txt and reads
  jobs\<id>\result.ini, which `ecadlib altium-menu run-job` writes. Settings: ecadlib_menu.ini. No keys or passwords
  are in these files: API keys stay in ecadlib's own key store (ecadlib keys set). }

Var
    WasInstalled : String;      { the libraries BuildInAltium uninstalled, ';' between: installed again when it fails }

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

Function SamePath(Path : String; Listed : String) : Boolean;
{ Listed is a path from Altium's installed list, which may start with an environment variable (%UserProfile%\...,
  as Altium saves it): then the rest of it must end Path. }
Var
    Rest : String;
Begin
    Result := UpperCase(Listed) = UpperCase(Path);
    If (Not Result) And (Copy(Listed, 1, 1) = '%') Then
    Begin
        Rest := Copy(Listed, 2, Length(Listed));
        If Pos('%', Rest) > 0 Then
        Begin
            Rest := UpperCase(Copy(Rest, Pos('%', Rest) + 1, Length(Rest)));
            Result := (Rest <> '') And (UpperCase(Copy(Path, Length(Path) - Length(Rest) + 1, Length(Rest))) = Rest);
        End;
    End;
End;

Function IsInstalled(Path : String) : Boolean;
Var
    i : Integer;
Begin
    Result := False;
    For i := 0 To IntegratedLibraryManager.InstalledLibraryCount - 1 Do
        If SamePath(Path, IntegratedLibraryManager.InstalledLibraryPath(i)) Then
            Result := True;
End;

Function FirstPath(List : String) : String;
{ The first path of a ';' list (install=). }
Begin
    If Pos(';', List) = 0 Then
        Result := List
    Else
        Result := Copy(List, 1, Pos(';', List) - 1);
End;

Function OtherPaths(List : String) : String;
{ The rest of a ';' list after its first path. }
Begin
    If Pos(';', List) = 0 Then
        Result := ''
    Else
        Result := Copy(List, Pos(';', List) + 1, Length(List));
End;

Function BuildReport(Folder : String) : String;
{ What Altium said about building the libraries (ecadlib_build_report.txt, one OK / FAILED line each): '' when all are OK. }
Var
    R : TStringList;
    i : Integer;
    Bad : Integer;
Begin
    Result := '';
    If Not FileExists(Folder + 'ecadlib_build_report.txt') Then
    Begin
        Result := #13#10 + 'Altium build: no report in ' + Folder + ', so the library files are the ones ecadlib wrote.';
        Exit;
    End;
    R := TStringList.Create;
    R.LoadFromFile(Folder + 'ecadlib_build_report.txt');
    Bad := 0;
    For i := 0 To R.Count - 1 Do
        If Copy(R.Strings[i], 1, 6) = 'FAILED' Then
            Bad := Bad + 1;
    If Bad > 0 Then
        Result := #13#10 + 'Altium build: FAILED for ' + IntToStr(Bad) + ' of ' + IntToStr(R.Count) + ' libraries. Next: read ' + Folder + 'ecadlib_build_report.txt';
    R.Free;
End;

Function MissingLibraries(Folder : String) : String;
{ The libraries ecadlib_build.txt lists that are not on disk after the build ('' when all are), '; ' between. }
Var
    R : TStringList;
    i : Integer;
    P : String;
Begin
    Result := '';
    R := TStringList.Create;
    R.LoadFromFile(Folder + 'ecadlib_build.txt');
    For i := 0 To R.Count - 1 Do
        If (Copy(R.Strings[i], 1, 7) = 'PCBLIB' + #9) Or (Copy(R.Strings[i], 1, 7) = 'SCHLIB' + #9) Then
        Begin
            P := Copy(R.Strings[i], 8, Length(R.Strings[i]));
            If Pos(#9, P) > 0 Then
                P := Copy(P, 1, Pos(#9, P) - 1);
            If Not FileExists(Folder + P) Then
                Result := Result + Folder + P + '; ';
        End;
    R.Free;
End;

Function PutBackOldLibraries(Folder : String) : Integer;
{ After a build that Altium stopped half way (a script error ends the script, so no report is written): a library
  ecadlib_build.txt lists that is now only NAME.ecadlib_old is renamed back, so the old file stays usable. How many. }
Var
    R : TStringList;
    i : Integer;
    P : String;
Begin
    Result := 0;
    R := TStringList.Create;
    R.LoadFromFile(Folder + 'ecadlib_build.txt');
    For i := 0 To R.Count - 1 Do
        If (Copy(R.Strings[i], 1, 7) = 'PCBLIB' + #9) Or (Copy(R.Strings[i], 1, 7) = 'SCHLIB' + #9) Then
        Begin
            P := Copy(R.Strings[i], 8, Length(R.Strings[i]));
            If Pos(#9, P) > 0 Then
                P := Copy(P, 1, Pos(#9, P) - 1);
            If (Not FileExists(Folder + P)) And FileExists(Folder + P + '.ecadlib_old') Then
                If RenameFile(Folder + P + '.ecadlib_old', Folder + P) Then
                    Result := Result + 1;
        End;
    R.Free;
End;

Function BuildFailed(Extra : String) : Boolean;
{ What BuildInAltium returned says a library was not built: nothing is installed then, and nothing says ready. }
Begin
    Result := Pos('Altium build: FAILED', Extra) > 0;
End;

Function BuildInAltium(Job : String) : String;
{ build= in result.ini is the library folder ecadlib wrote ecadlib_build.txt into: Altium writes every .SchLib /
  .PcbLib there again with its own API (BuildLibraries, ecadlib_build.pas) before the libraries are installed, so
  the files Altium loads are ones Altium wrote. The listed libraries are uninstalled first so none is in use.
  native_build=0 in ecadlib_menu.ini skips this. }
Var
    Folder : String;
    List : String;
    P : String;
    Missing : String;
Begin
    Result := '';
    Folder := Answer(Job, 'build');
    If (Folder = '') Or (Setting('native_build', '1') = '0') Then
        Exit;
    If Copy(Folder, Length(Folder), 1) <> '\' Then
        Folder := Folder + '\';
    If Not FileExists(Folder + 'ecadlib_build.txt') Then
        Exit;
    List := Answer(Job, 'install');
    WasInstalled := '';
    While List <> '' Do
    Begin
        P := FirstPath(List);
        List := OtherPaths(List);
        If (P <> '') And IsInstalled(P) Then
        Begin
            IntegratedLibraryManager.UninstallLibrary(P);
            WasInstalled := WasInstalled + P + ';';
        End;
    End;
    If FileExists(Folder + 'ecadlib_build_report.txt') Then
        DeleteFile(Folder + 'ecadlib_build_report.txt');
    BuildLibraries(Folder);
    If FileExists(Folder + 'ecadlib_build_report.txt') Then
        Result := BuildReport(Folder)
    Else
    Begin
        Result := #13#10 + 'Altium build: FAILED, it wrote no ecadlib_build_report.txt in ' + Folder;
        If PutBackOldLibraries(Folder) > 0 Then
            Result := Result + ' (the build stopped part way; the previous library files were put back)';
    End;
    Missing := MissingLibraries(Folder);
    If Missing <> '' Then
        Result := Result + #13#10 + 'Altium build: FAILED, these library files are not there: ' + Missing;
End;

Function Reinstall(Job : String) : String;
{ Lets Altium build the library files (BuildInAltium), then uninstalls and installs again every library result.ini
  names in install= (';' between paths), then refreshes. }
Var
    List : String;
    P : String;
Begin
    WasInstalled := '';
    Result := BuildInAltium(Job);
    If BuildFailed(Result) Then
    Begin
        List := WasInstalled;
        While List <> '' Do
        Begin
            P := FirstPath(List);
            List := OtherPaths(List);
            If (P <> '') And FileExists(P) Then
                IntegratedLibraryManager.InstallLibrary(P);
        End;
        If WasInstalled <> '' Then
            Result := Result + #13#10 + 'The library you had is installed again; what this run added is not built yet.'
        Else
            Result := Result + #13#10 + 'Nothing was installed: the library is not ready.';
        Result := Result + ' Next: read the report, fix it, run this again.';
        Exit;
    End;
    List := Answer(Job, 'install');
    While List <> '' Do
    Begin
        P := FirstPath(List);
        List := OtherPaths(List);
        If P <> '' Then
        Begin
            If FileExists(P) Then
            Begin
                If IsInstalled(P) Then
                    IntegratedLibraryManager.UninstallLibrary(P);
                IntegratedLibraryManager.InstallLibrary(P);
                { Altium can take the call and install nothing: say so, so "Done" never hides a library it can't see }
                If IsInstalled(P) Then
                    Result := Result + #13#10 + 'Installed in Altium: ' + P
                Else
                    Result := Result + #13#10 + 'Not installed: ' + P + ' (Altium did not install it). Next: in the Components panel, '
                        + 'click the menu button > File-based Libraries Preferences > Installed > Install, and pick that file.';
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
    If BuildFailed(Extra) Then
        ShowError(Title + #13#10 + #13#10 + 'Altium could not build the library files.' + Extra)
    Else If (Answer(Job, 'status') = 'error') Or (Pos('Not installed:', Extra) > 0) Then
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

Function PlainDialogs : Boolean;
{ dialogs=0 in ecadlib_menu.ini: Altium's plain message boxes instead of the ecadlib dialog. }
Begin
    Result := Setting('dialogs', '1') = '0';
End;

Procedure OfferAi(Job : String);
{ A part only an AI datasheet read can finish: show the model and the cost, read only on a yes (a second yes when it
  can cost more than the limit), then show the real cost next to the estimate. }
Var
    Ai : String;
    Problem : String;
Begin
    If Answer(Job, 'offer_ai') <> '1' Then
        Exit;
    If Not ConfirmNoYes('ecadlib: AI step' + #13#10 + #13#10 + Answer(Job, 'ai_text') + #13#10 + #13#10 + 'Read the datasheets with AI now? Nothing is sent unless you say yes.') Then
        Exit;
    If Answer(Job, 'ai_over') = '1' Then
        If Not ConfirmNoYes(Answer(Job, 'ai_confirm')) Then
            Exit;
    Ai := StartJob('ai-read');
    Ask(Ai, 'bom', Answer(Job, 'ai_bom'));
    Ask(Ai, 'mpn', Answer(Job, 'ai_mpn'));
    Ask(Ai, 'part', Answer(Job, 'ai_part'));
    Ask(Ai, 'approved_usd', Answer(Job, 'ai_approve'));
    Ask(Ai, 'over_limit_ok', Answer(Job, 'ai_over'));
    Ask(Ai, 'price_unknown_ok', Answer(Job, 'ai_unknown'));
    Problem := RunJob(Ai, 'ecadlib: read with AI');
    If Problem <> '' Then
    Begin
        ShowError('ecadlib: read with AI' + #13#10 + #13#10 + Problem);
        Exit;
    End;
    ShowResult('ecadlib: read with AI', Ai, Publish(Ai));
End;

Procedure AddPartFromMPN;
Var
    MPN : String;
    Maker : String;
    Job : String;
    Problem : String;
    Title : String;
Begin
    If Not PlainDialogs Then
    Begin
        OpenDialog('add', '', '');
        Exit;
    End;
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
    OfferAi(Job);
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
    If Not PlainDialogs Then
    Begin
        OpenDialog('bom', PrjPath, '');
        Exit;
    End;
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
    OfferAi(Job);
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
