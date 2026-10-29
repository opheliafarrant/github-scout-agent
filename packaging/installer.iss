; Inno Setup script for GitHub Scout Agent.
; Build (after the PyInstaller one-folder build exists in dist\GitHubScoutAgent):
;   ISCC.exe packaging\installer.iss
; Output: dist\installer\GitHubScoutAgent-1.2.0-setup.exe
;
; Per-user install (PrivilegesRequired=lowest) so no administrator prompt is
; needed; {autopf} resolves to %LOCALAPPDATA%\Programs. User data lives in
; %APPDATA%\GitHubScoutAgent and is deliberately left in place on uninstall.

#define MyAppName "GitHub Scout Agent"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "Ophelia Farrant"
#define MyAppURL "https://github.com/opheliafarrant/github-scout-agent"
#define MyAppExeName "GitHubScoutAgent.exe"
#define MyBuildDir "..\dist\GitHubScoutAgent"

[Setup]
AppId={{7C4A1E93-5B62-4F0D-9A71-2D6E8B3C5F14}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
SetupIconFile=..\packaging\github_scout_agent.ico
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=GitHubScoutAgent-{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Local read-only GitHub research UI"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; %APPDATA%\GitHubScoutAgent (settings, history, logs) is intentionally not
; touched here: reinstalling must not erase the user's research history.
