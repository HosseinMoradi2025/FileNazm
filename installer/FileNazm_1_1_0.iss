; FileNazm - Inno Setup script
; Version 1.1.0
; Installs over an existing 1.0.0 installation as an in-place upgrade.
; NOTE: this file MUST be saved as UTF-8 with BOM (Persian text).

#define MyAppNameEnglish "FileNazm"
#define MyAppDisplayName "فایل‌نظم"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Hossein Moradi"
#define MyAppExeName "FileNazm.exe"
#define MyAppIconSource "..\FileNazm.ico"
#define MyStartMenuGroup "FileNazm"

[Setup]
; MUST be byte-identical to version 1.0.0, including the two closing braces.
; Inno Setup only treats '{{' as an escape, so '}}' really ends up as '}}' in the
; AppId (and in the registry key). Do NOT "fix" it to a single '}', or Windows
; will no longer recognise 1.0.0 and will install side by side.
AppId={{9A2D4F6C-1B8E-4C3A-9D5F-6E7A8B9C0D1E}}

AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}

DefaultDirName={autopf}\{#MyAppNameEnglish}
DefaultGroupName={#MyStartMenuGroup}

UsePreviousAppDir=yes
UsePreviousGroup=yes
AllowNoIcons=no
UsePreviousTasks=yes

DisableWelcomePage=yes
; auto = shown on a fresh install, skipped on upgrade (previous folder is reused and locked)
DisableDirPage=auto
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=no

; Use the same 64-bit installation mode as version 1.0.0
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=6.1sp1

OutputDir=..\installer_output
OutputBaseFilename=FileNazm_1_1_0

SetupIconFile={#MyAppIconSource}

; License page shown in the wizard (RTF = correct right-to-left Persian rendering)
LicenseFile=..\LICENSE.rtf

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no

UninstallDisplayName={#MyAppDisplayName}
UninstallDisplayIcon={app}\FileNazm.ico

VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FileNazm Setup
VersionInfoProductName={#MyAppNameEnglish}
VersionInfoProductVersion={#MyAppVersion}

[Tasks]
Name: "desktopicon"; Description: "ساخت میانبر روی دسکتاپ"; GroupDescription: "میانبرها:"; Flags: unchecked

[Dirs]
Name: "{commonprograms}\{#MyStartMenuGroup}"

[InstallDelete]
Type: files; Name: "{commonprograms}\{#MyStartMenuGroup}\{#MyAppDisplayName}.lnk"

[Files]
Source: "..\output\*"; DestDir: "{app}"; Excludes: "FileNazm.ico"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppIconSource}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.rtf"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\fonts\OFL.txt"; DestDir: "{app}\licenses"; DestName: "Vazirmatn-OFL.txt"; Flags: ignoreversion

[Icons]
Name: "{commonprograms}\{#MyStartMenuGroup}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\FileNazm.ico"
Name: "{commonprograms}\{#MyStartMenuGroup}\{cm:UninstallProgram,{#MyAppDisplayName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\FileNazm.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "اجرای فایل‌نظم"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillFileNazm"
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ""FileNazm"" /f"; Flags: runhidden

[Code]
{ Makes sure FileNazm.exe (including a tray / background instance) is not running
  before files are replaced. First a polite close, then a forced one. }
procedure CloseFileNazm;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/T /IM {#MyAppExeName}', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExeName}', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = wpReady then
    CloseFileNazm;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseFileNazm;
  Result := '';
end;
