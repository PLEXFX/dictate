; Inno Setup script for Dictate.
;
; dictate.spec's PyInstaller build no longer bundles any CUDA DLLs -- that
; was most of the installer's ~1GB size for a component most machines never
; use. This script installs one Core-only file set and, when Setup detects
; an NVIDIA card (NvidiaCardDetected below), offers a "gpuaccel" task that
; just seeds a preference in a fresh settings.json. The actual CUDA files
; are fetched the same way either way: gpu_runtime.py's on-demand PyPI
; download, the first time the app actually needs them, with its own
; progress UI in Settings. No card, no task shown, no download ever
; attempted -- resolve_device() (engine.py) falls back to CPU regardless.
;
; Build with (from the installer\ folder):
;   ISCC.exe dictate.iss
; after first producing dist\dictate\ via: uv run pyinstaller ..\dictate.spec
;
; Override the version at build time without editing this file:
;   ISCC.exe dictate.iss /DMyAppVersion=0.1.0-beta.3

#ifndef MyAppVersion
#define MyAppVersion "1.0.0-beta.1"
#endif
#define MyAppName "Dictate"
#define MyAppPublisher "PLEXFX"
#define MyAppURL "https://github.com/PLEXFX/dictate"
#define MyAppExeName "dictate.exe"
#define SourceDir "..\dist\dictate"
#define UpdaterSourceDir "..\dist\dictate-updater"

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

[Tasks]
Name: "autoupdate"; Description: "Automatically check GitHub for new versions of Dictate"
Name: "gpuaccel"; Description: "Enable GPU acceleration (NVIDIA card detected) - downloads automatically the first time it's needed"; Check: NvidiaCardDetected

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; The update-progress splash (update_splash.py) -- its own small, separately
; built onedir tree, kept in its own subfolder rather than mixed into
; dictate.exe's _internal above so two independently built PyInstaller
; trees never risk colliding on a same-named file.
Source: "{#UpdaterSourceDir}\dictate-updater.exe"; DestDir: "{app}\updater"; Flags: ignoreversion
Source: "{#UpdaterSourceDir}\_internal\*"; DestDir: "{app}\updater\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

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
Type: filesandordirs; Name: "{app}\updater"
; settings.json, update-notice.json -- everything config.py/updater.py ever
; write under %APPDATA%\dictate. Removing it on uninstall means a later
; reinstall starts genuinely fresh: onboarding_complete is gone, so the
; first-run welcome dialog shows again exactly as it would for someone who
; had never installed Dictate, rather than silently inheriting a stranger's
; old settings from a previous install this machine forgot about.
Type: filesandordirs; Name: "{userappdata}\dictate"

[Code]
// NVIDIA driver installs (any of them) create this key -- reading it needs
// no admin rights and works whether or not the card is currently in use by
// another app. This is detection only; it never triggers a download itself.
function NvidiaCardDetected(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\NVIDIA Corporation');
end;

var
  GpuInfoLabel: TNewStaticText;

// The Select Tasks page has no built-in room for plain explanatory text, so
// this adds one line under the task list itself: which branch a person is
// in (GPU found vs CPU-only) should not be a guess just because the task
// checkbox above either appears or doesn't.
procedure InitializeWizard();
begin
  GpuInfoLabel := TNewStaticText.Create(WizardForm);
  GpuInfoLabel.Parent := WizardForm.TasksList.Parent;
  GpuInfoLabel.Left := WizardForm.TasksList.Left;
  GpuInfoLabel.Top := WizardForm.TasksList.Top + WizardForm.TasksList.Height + 12;
  GpuInfoLabel.Width := WizardForm.TasksList.Width;
  GpuInfoLabel.AutoSize := False;
  GpuInfoLabel.WordWrap := True;
  if NvidiaCardDetected() then
    GpuInfoLabel.Caption :=
      'NVIDIA graphics card detected. Check the box above to enable GPU ' +
      'acceleration -- the files download automatically the first time ' +
      'Dictate actually uses it.'
  else
    GpuInfoLabel.Caption := 'No NVIDIA graphics card detected -- Dictate will run on CPU.';
end;

// "Start with Windows" deliberately has no installer checkbox -- see the
// [Run] comment above -- but auto-update and GPU acceleration are one-time
// choices worth surfacing during setup, so they seed settings.json instead.
// This only ever writes a genuinely fresh settings.json; config.py already
// tolerates a partial file and fills in every other default. An existing
// settings.json (reinstall, upgrade, or repair) is never touched, so a
// user's own later choice in Settings always wins.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigDir, ConfigFile, Json: string;
  Parts: TStringList;
  i: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  ConfigDir := ExpandConstant('{userappdata}\dictate');
  ConfigFile := ConfigDir + '\settings.json';
  if FileExists(ConfigFile) then
    Exit;

  Parts := TStringList.Create;
  try
    if not WizardIsTaskSelected('autoupdate') then
      Parts.Add('"auto_update_enabled": false');
    // "auto" -- not a hard "cuda" -- so resolve_device() (engine.py) still
    // falls back to CPU silently if detection was ever wrong for this PC.
    if WizardIsTaskSelected('gpuaccel') then
      Parts.Add('"device": "auto"');

    if Parts.Count > 0 then
    begin
      Json := '{';
      for i := 0 to Parts.Count - 1 do
      begin
        if i > 0 then
          Json := Json + ', ';
        Json := Json + Parts[i];
      end;
      Json := Json + '}';
      ForceDirectories(ConfigDir);
      SaveStringToFile(ConfigFile, Json, False);
    end;
  finally
    Parts.Free;
  end;
end;
