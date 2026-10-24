; Inno Setup script for the standalone AI Video Flow Windows installer.
; Built by packaging\build_windows_app.bat (or CI) after PyInstaller has produced build\dist\AI Video Flow\

#define AppName "AI Video Flow"
#define AppVersion "1.1.0"
#define AppExe "AI Video Flow.exe"

[Setup]
AppId={{825B4728-2F44-4DDF-BCC1-A2527464A5B1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AI Video Flow
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=AI-Video-Flow-Windows-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\build\dist\AI Video Flow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
