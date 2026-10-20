; Inno Setup script for Nexus Remote.
;
; Build the application bundle first:
;     .\packaging\build.ps1
;
; Then compile this script with Inno Setup 6:
;     ISCC.exe packaging\installer.iss
;
; Output: dist\NexusRemoteSetup-<version>.exe
;
; The installer is per-user (no elevation), installs under
; %LOCALAPPDATA%\Programs\Nexus Remote and leaves user data in
; %LOCALAPPDATA%\NexusRemote untouched on uninstall.

#define AppName "Nexus Remote"
#define AppVersion "0.3.0"
#define AppPublisher "NexusRemote"
#define AppExeName "NexusRemote.exe"
#define AppId "{{6F1F0F2C-6E31-4A70-9A5B-6B9A1C2D4E01}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Nexus Remote
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=NexusRemoteSetup-{#AppVersion}
SetupIconFile=..\src\nexus_remote\resources\nexus.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\NexusRemote\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only generated files inside the install directory; user data lives elsewhere.
Type: filesandordirs; Name: "{app}\_internal"
