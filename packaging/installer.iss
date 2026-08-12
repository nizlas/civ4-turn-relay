; Inno Setup 6 script — civ4-turn-relay per-user installer (64-bit).
;
; Build prerequisites:
;   1. Run packaging\build_windows.ps1 so dist\civ4-turn-relay-<version>-win64-portable\
;      contains the frozen application.
;   2. Compile this script with ISCC.exe (Inno Setup 6).
;
; Optional VC++ redistributable:
;   Place the official Microsoft vc_redist.x64.exe next to this script as
;   packaging\prereq\vc_redist.x64.exe (gitignored). Verify its provenance and
;   version before release. When absent, the installer skips that step and
;   continues; the operator must ensure a compatible runtime is already present.
;
; User data preservation:
;   Application data lives at %APPDATA%\civ4-turn-relay (see
;   civ4_turn_relay.ui.app.user_data_dir). This installer NEVER deletes that
;   directory, never installs into it, and never packs its contents. Upgrades
;   and uninstall leave .env, matches\, installation.json, logs, credentials,
;   and PBEM saves untouched.

#define MyAppName "Civ4 Turn Relay"
#define MyAppPublisher "Niclas Danielsson"
#define MyAppURL "https://github.com/nizlas/civ4-turn-relay"
#define MyAppExeName "civ4-turn-relay.exe"
#define MyAppId "{{A6F0C2E1-7B4D-4E8A-9C11-2D5F8B0A3E77}"

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#ifndef PortableDir
  #define PortableDir "..\dist\civ4-turn-relay-" + MyAppVersion + "-win64-portable"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\civ4-turn-relay
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=none
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=civ4-turn-relay-{#MyAppVersion}-win64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
; Never touch %APPDATA%\civ4-turn-relay
UsePreviousAppDir=yes
DisableDirPage=no
AllowNoIcons=yes
; Unsigned development builds: code signing is a future release decision.
; Do not configure a signing tool in this script.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Frozen portable tree produced by build_windows.ps1
Source: "{#PortableDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Optional redistributable (only if the operator placed it under packaging\prereq\)
#if FileExists("prereq\vc_redist.x64.exe")
Source: "prereq\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
#if FileExists("prereq\vc_redist.x64.exe")
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Installing Microsoft Visual C++ runtime (if needed)..."; \
  Flags: waituntilterminated; Check: VCRedistNeeded
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Intentionally empty of user-data paths. Never delete the Roaming app data tree.
; Only remove empty leftover folders under the install directory if Inno left any.
Type: dirifempty; Name: "{app}"

[Code]
function VCRedistNeeded: Boolean;
begin
  { Conservative: always offer/run when the binary was packaged. The quiet
    redistributable installer is itself a no-op when a suitable runtime exists. }
  Result := True;
end;

function InitializeUninstall: Boolean;
begin
  { Refuse any future attempt to wipe user data from this script. }
  Result := True;
end;
