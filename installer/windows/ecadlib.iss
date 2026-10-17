; Inno Setup script for the Windows installer (built by .github/workflows/windows.yml after PyInstaller):
;   iscc /DAppVersion=0.3.0 installer\windows\ecadlib.iss      -> dist\ecadlib-setup-0.3.0.exe
; Installs for the current user by default (no administrator needed; %LOCALAPPDATA%\Programs\ecadlib), or for all
; users when run as administrator. Start menu: "ecadlib" (the app) and "ecadlib setup". Optional: a desktop icon,
; and the ecadlib command on PATH (scripts, the Altium menu's fallback). Uninstalling keeps your workspace, settings
; and stored keys (%APPDATA%\ecadlib).
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root "..\.."

[Setup]
AppId={{6F2A9B4C-3E1D-4C8A-9B57-0E1C2D3A4B5F}
AppName=ecadlib
AppVersion={#AppVersion}
AppVerName=ecadlib {#AppVersion}
AppPublisher=ecadlib (free, open source)
AppPublisherURL=https://github.com/oliBrowne/PCBLIVINGLIBRARY
AppSupportURL=https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues
DefaultDirName={autopf}\ecadlib
DefaultGroupName=ecadlib
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Root}\LICENSE
OutputDir={#Root}\dist
OutputBaseFilename=ecadlib-setup-{#AppVersion}
SetupIconFile={#Root}\build\ecadlib.ico
UninstallDisplayIcon={app}\ecadlib-app.exe
UninstallDisplayName=ecadlib {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addtopath"; Description: "Add the ecadlib command to PATH (for scripts; not needed for the app)"; GroupDescription: "Command line:"

[Files]
Source: "{#Root}\dist\ecadlib\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ecadlib"; Filename: "{app}\ecadlib-app.exe"; Comment: "Start ecadlib and open it in your browser"
Name: "{group}\ecadlib setup"; Filename: "{app}\ecadlib-app.exe"; Parameters: "--setup"; Comment: "AI model and key, Altium / KiCad, folders"
Name: "{group}\Uninstall ecadlib"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ecadlib"; Filename: "{app}\ecadlib-app.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "{code:EnvKey}"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\ecadlib-app.exe"; Description: "Start ecadlib now (opens the setup page in your browser)"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ecadlib-app.exe"; Flags: runhidden; RunOnceId: "StopEcadlib"

[Code]
function EnvKey(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

function EnvRoot: Integer;
begin
  if IsAdminInstallMode then Result := HKEY_LOCAL_MACHINE else Result := HKEY_CURRENT_USER;
end;

function NeedsAddPath(Dir: String): Boolean;
var
  Paths: String;
begin
  if not RegQueryStringValue(EnvRoot, EnvKey(''), 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Paths, Dir: String;
  P: Integer;
begin
  if CurUninstallStep <> usPostUninstall then exit;
  if not RegQueryStringValue(EnvRoot, EnvKey(''), 'Path', Paths) then exit;
  Dir := ExpandConstant('{app}');
  P := Pos(';' + Uppercase(Dir), Uppercase(Paths));
  if P > 0 then
  begin
    Delete(Paths, P, Length(Dir) + 1);
    RegWriteExpandStringValue(EnvRoot, EnvKey(''), 'Path', Paths);
  end;
end;
