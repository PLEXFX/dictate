; Inno Setup script for Dictate.
;
; Ships one installer with an optional "GPU acceleration" component instead
; of separate CPU/GPU downloads. dictate.spec's PyInstaller build always
; bundles the CUDA DLLs under _internal\nvidia\ in their own isolated tree
; (engine.py's own CUDA DLL search already expects exactly that layout), so
; this script simply chooses whether that one folder gets copied to disk.
; Skipping it does not remove CPU support -- that lives in Core and always
; installs -- it only removes the optional GPU path, which falls back to CPU
; automatically at runtime either way if it's ever missing or fails to load.
;
; Build with (from the installer\ folder):
;   ISCC.exe dictate.iss
; after first producing dist\dictate\ via: uv run pyinstaller ..\dictate.spec
;
; Override the version at build time without editing this file:
;   ISCC.exe dictate.iss /DMyAppVersion=0.1.0-beta.3

#ifndef MyAppVersion
  #define MyAppVersion "0.2.2-beta.1"
#endif
#define MyAppName "Dictate"
#define MyAppPublisher "PLEXFX"
#define MyAppURL "https://github.com/PLEXFX/dictate"
#define MyAppExeName "dictate.exe"
#define SourceDir "..\dist\dictate"

[Setup]
AppId={{65B8AB9F-FF1B-4148-8ED5-76F30681FD64}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Per-user install, no admin prompt -- {autopf} resolves to the per-user
; Program-Files-equivalent automatically once PrivilegesRequired is lowest,
; the same pattern Inno Setup itself installed under on this machine.
PrivilegesRequired=lowest
DefaultDirName={autopf}\Dictate
DefaultGroupName=Dictate
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=Dictate-Setup-{#MyAppVersion}
SetupIconFile={#SourceDir}\_internal\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Components]
Name: "core"; Description: "Dictate (required)"; Types: full compact custom; Flags: fixed
Name: "gpu"; Description: "GPU acceleration (NVIDIA CUDA) - adds faster transcription on supported NVIDIA graphics cards. Not required - Dictate works on CPU without this."; Types: full

[Tasks]
Name: "autoupdate"; Description: "Automatically check GitHub for new versions of Dictate"

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Components: core; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "nvidia\*"
Source: "{#SourceDir}\_internal\nvidia\*"; DestDir: "{app}\_internal\nvidia"; Components: gpu; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Dictate"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Dictate"; Filename: "{uninstallexe}"

[Run]
; Start with Windows is left to Dictate's own Settings toggle rather than a
; second install-time checkbox -- startup.py already registers correctly
; against the installed exe path once running frozen, so a separate
; installer-side mechanism would only duplicate it.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Dictate"; Flags: nowait postinstall skipifsilent
; A verified in-app update runs Setup silently.  Inno Setup skips the normal
; post-install checkbox in that mode, so launch the new version explicitly
; and mark it as updated so Dictate can show the release's What's New window.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--updated"; Flags: nowait skipifnotsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// "Start with Windows" deliberately has no installer checkbox -- see the
// [Run] comment above -- but the auto-update check is a one-time network
// call at every launch rather than a per-session opt-in like startup, so
// it gets an install-time choice too. This only ever seeds settings.json
// with a single override key on a genuinely fresh install; config.py
// already tolerates a partial settings file and fills in every other
// default. An existing settings.json (reinstall, upgrade, or repair) is
// never touched, so a user's own later choice in Settings always wins.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigDir, ConfigFile: string;
begin
  if (CurStep = ssPostInstall) and (not WizardIsTaskSelected('autoupdate')) then
  begin
    ConfigDir := ExpandConstant('{userappdata}\dictate');
    ConfigFile := ConfigDir + '\settings.json';
    if not FileExists(ConfigFile) then
    begin
      ForceDirectories(ConfigDir);
      SaveStringToFile(ConfigFile, '{"auto_update_enabled": false}', False);
    end;
  end;
end;
