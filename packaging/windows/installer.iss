; Inno Setup script - Windows installer (same structure in both apps).
;
;   1. pyinstaller packaging\app.spec --noconfirm        -> dist\<AppName>\
;   2. iscc packaging\windows\installer.iss              -> dist\<File>-<version>-Setup.exe
;
; Per-user install (no administrator rights): an elevated app would not see
; mapped network / cloud drives such as Google Drive's H:, and neither would
; the weekly task it registers. Shortcuts are optional (checkboxes) and carry
; the app's AppUserModelID, so a pinned taskbar icon groups with the window.

#define AppName   "BiblioMancer"
#define Pkg       "bibliomancer"
#define FileName  "BiblioMancer"
#define IconFile  "bibliomancer_icon.ico"
#define AumId     "BiblioMancer.App"
#define AppGuid   "{{A7B9485F-E9C2-4441-A1D9-561A5A5B1242}"
#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "2.0.0"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Matteo Marchionni
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename={#FileName}-{#AppVersion}-Setup
SetupIconFile=..\..\src\{#Pkg}\assets\{#IconFile}
UninstallDisplayIcon={app}\{#Pkg}.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon";   Description: "Create a &desktop icon";      GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startmenuicon"; Description: "Add to the &Start menu";      GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#Pkg}.exe"; WorkingDir: "{app}"; AppUserModelID: "{#AumId}"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#Pkg}.exe"; WorkingDir: "{app}"; AppUserModelID: "{#AumId}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#Pkg}.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; remove the weekly / monthly task the app may have registered
Filename: "{app}\{#Pkg}-cli.exe"; Parameters: "unschedule"; Flags: runhidden; RunOnceId: "Unschedule"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent() then
    MsgBox('{#AppName} was removed. Your data (notes, database, reports) and settings ' +
           'were kept: the data folder you chose at setup (by default in Documents) and ' +
           ExpandConstant('{userappdata}') + '\{#AppName}. Delete them by hand if you ' +
           'no longer need them.', mbInformation, MB_OK);
end;
