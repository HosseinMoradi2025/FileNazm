; FileNazm - Inno Setup script
; Version 1.0.0
; Admin required
; Persian display name: فایل‌نظم
; English install/start-menu folder: FileNazm
; Setup filename: FileNazm_Setup.exe
; No website URLs

#define MyAppNameEnglish "FileNazm"
#define MyAppDisplayName "فایل‌نظم"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Hossein Moradi"
#define MyAppExeName "FileNazm.exe"
#define MyAppIconSource "F:\MyFileOrganizer\FileNazm.ico"
#define MyStartMenuGroup "FileNazm"

[Setup]
AppId={{9A2D4F6C-1B8E-4C3A-9D5F-6E7A8B9C0D1E}}

AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}

DefaultDirName={autopf}\{#MyAppNameEnglish}
DefaultGroupName={#MyStartMenuGroup}

UsePreviousAppDir=no
UsePreviousGroup=no
AllowNoIcons=no

DisableWelcomePage=yes
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=no

OutputDir=F:\MyFileOrganizer\installer
OutputBaseFilename=FileNazm_Setup

SetupIconFile={#MyAppIconSource}

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=6.1sp1

PrivilegesRequired=admin

CloseApplications=yes
RestartApplications=no

UninstallDisplayName={#MyAppDisplayName}
UninstallDisplayIcon={app}\FileNazm.ico

VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FileNazm Setup

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
Name: "{commonprograms}\{#MyStartMenuGroup}"

[Files]
Source: "F:\MyFileOrganizer\FileNazm\*"; DestDir: "{app}"; Excludes: "FileNazm.ico"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppIconSource}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{commonprograms}\{#MyStartMenuGroup}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\FileNazm.ico"
Name: "{commonprograms}\{#MyStartMenuGroup}\{cm:UninstallProgram,{#MyAppDisplayName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\FileNazm.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppDisplayName}}"; Flags: nowait postinstall skipifsilent