; Inno Setup script for Dictate.
;
; dictate.spec's PyInstaller build no longer bundles any CUDA DLLs -- that
; was most of the installer's ~1GB size for a component most machines never
; use. This script installs one Core-only file set and, when Setup detects
; an NVIDIA card (NvidiaCardDetected below), offers a "gpuaccel" task. If
; checked, Setup itself fetches the CUDA compute DLLs from PyPI during
; install (CurStepChanged's ssPostInstall below, via download-gpu-runtime.ps1
; -- a Pascal-Script-callable mirror of gpu_runtime.py's own PyPI-fetch
; logic) and lays them into {app}\_internal\nvidia -- inside the installed
; program folder itself, the exact place gpu_runtime.runtime_dir() already
; looks for a frozen build, not some separate app-data location. GPU
; acceleration is then ready the moment Dictate first opens.
;
; This is a head start, not the only path: if it fails for any reason
; (offline install, a PyPI hiccup, Setup running with scripts blocked) it
; fails silently, and GPU acceleration is still reachable afterward from
; Settings' own "Download now" button (Engine.start_gpu_download(),
; settings_window.py) -- deliberately the only way to trigger it once
; Dictate is actually running; nothing here or in the app auto-retries on
; its own. A dictation never waits on this download either way: were it to
; run (whether from here or that button), engine.py's ensure_loaded()
; always runs it on its own background thread and transcribes on CPU in
; the meantime.
;
; Build with (from the installer\ folder):
;   ISCC.exe dictate.iss
; after first producing dist\dictate\ via: uv run pyinstaller ..\dictate.spec
;
; Override the version at build time without editing this file:
;   ISCC.exe dictate.iss /DMyAppVersion=0.1.0-beta.3

#ifndef MyAppVersion
#define MyAppVersion "1.3.0-beta.1"
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
Name: "gpuaccel"; Description: "Enable GPU acceleration (NVIDIA card detected) - files download during setup so it's ready right away"; Check: NvidiaCardDetected

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; The update-progress splash (update_splash.py) -- its own small, separately
; built onedir tree, kept in its own subfolder rather than mixed into
; dictate.exe's _internal above so two independently built PyInstaller
; trees never risk colliding on a same-named file.
Source: "{#UpdaterSourceDir}\dictate-updater.exe"; DestDir: "{app}\updater"; Flags: ignoreversion
Source: "{#UpdaterSourceDir}\_internal\*"; DestDir: "{app}\updater\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Setup-time only -- never installed alongside the app itself. "dontcopy"
; keeps it out of {app} and the uninstall list entirely; CurStepChanged
; below extracts it to {tmp} (Setup's own scratch folder, auto-cleaned)
; only when the gpuaccel task actually needs it.
Source: "download-gpu-runtime.ps1"; DestDir: "{tmp}"; Flags: dontcopy

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
; The updater helper is pure app binary, same as everything else Inno
; already tracks per-file from [Files] -- always safe to remove outright,
; no prompt needed.
Type: filesandordirs; Name: "{app}\updater"
; {app}\_internal (Dictate's own runtime files, individually tracked and
; removed by Inno automatically -- no entry needed for those) and
; {userappdata}\dictate (settings.json, downloaded speech models, GPU
; acceleration files) are deliberately NOT unconditionally deleted here
; anymore. InitializeUninstall/CurUninstallStepChanged below ask first --
; see their own comments for why an unconditional wipe was the wrong
; default once GPU files and speech models can each be a genuine multi-
; hundred-MB-to-multi-GB download someone would rather not repeat.

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
      'acceleration -- Setup downloads the files now, during install, so ' +
      'it works right away. If that fails for any reason (no internet ' +
      'right now, for example), a "Download now" button in Settings ' +
      'fetches them later -- Dictate works on CPU in the meantime either way.'
  else
    GpuInfoLabel.Caption := 'No NVIDIA graphics card detected -- Dictate will run on CPU.';
end;

// True once all three CUDA compute-DLL subfolders are actually on disk --
// mirrors gpu_runtime.py's own is_installed() check (all three bin/
// folders present), so a re-run (repair install, or a silent self-update
// that still has the task's own Check: passing) never re-downloads files
// that already made it into place.
function GpuRuntimeInstalled(): Boolean;
var
  Base: string;
begin
  Base := ExpandConstant('{app}\_internal\nvidia');
  Result :=
    DirExists(Base + '\cublas\bin') and
    DirExists(Base + '\cudnn\bin') and
    DirExists(Base + '\cuda_nvrtc\bin');
end;

// Best-effort head start for GpuInfoLabel's promise: run
// download-gpu-runtime.ps1 (a Pascal-Script-callable mirror of
// gpu_runtime.py's own PyPI-fetch logic) so the GPU files are already in
// place before Dictate ever runs, instead of only starting at first
// launch. Failure here is silent and non-fatal on purpose -- GPU
// acceleration is then available from Settings' own "Download now" button
// instead (Engine.start_gpu_download(), settings_window.py) -- this is
// just a head start when it works, not the only path.
//
// Launched with ewNoWait rather than a blocking Exec: Pascal Script is
// single-threaded, so a blocking call would freeze the wizard's own UI
// (no repaint, no progress bar movement) for the download's entire
// length. Real percentage instead comes from polling a small progress
// file the script itself writes to on a throttle (see its own
// ProgressFile parameter doc) -- the same file's terminal "SUCCESS" or
// "FAILED: ..." line doubles as this loop's own "the child is done"
// signal, since Pascal Script has no simple built-in way to wait on an
// external process handle without dropping into raw Win32 API imports.
const
  GpuPollIntervalMs = 250;
  // A real download rarely needs more than a minute; this is a ceiling
  // against a genuinely stalled connection, not a normal-case wait.
  GpuMaxWaitMs = 180000;

procedure DownloadGpuRuntime();
var
  ScriptPath, DestDir, ProgressFile, Arguments: string;
  ProgressText: AnsiString;
  ResultCode, ElapsedMs, Percent: Integer;
begin
  ExtractTemporaryFile('download-gpu-runtime.ps1');
  ScriptPath := ExpandConstant('{tmp}\download-gpu-runtime.ps1');
  DestDir := ExpandConstant('{app}\_internal\nvidia');
  ProgressFile := ExpandConstant('{tmp}\dictate-gpu-progress.txt');
  if FileExists(ProgressFile) then
    DeleteFile(ProgressFile);

  WizardForm.StatusLabel.Caption := 'Downloading GPU acceleration files...';
  WizardForm.ProgressGauge.Style := npbstNormal;
  WizardForm.ProgressGauge.Min := 0;
  WizardForm.ProgressGauge.Max := 100;
  WizardForm.ProgressGauge.Position := 0;
  WizardForm.Refresh;

  Arguments :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath +
    '" -DestDir "' + DestDir + '" -ProgressFile "' + ProgressFile + '"';
  // ewNoWait: Result/ResultCode here only report whether powershell.exe
  // itself launched, not the script's own outcome -- that only ever comes
  // from ProgressFile's terminal line, read below.
  Exec('powershell.exe', Arguments, '', SW_HIDE, ewNoWait, ResultCode);

  ElapsedMs := 0;
  while ElapsedMs < GpuMaxWaitMs do
  begin
    Sleep(GpuPollIntervalMs);
    ElapsedMs := ElapsedMs + GpuPollIntervalMs;
    if not FileExists(ProgressFile) then
      Continue;
    if not LoadStringFromFile(ProgressFile, ProgressText) then
      Continue;
    ProgressText := Trim(ProgressText);
    if (ProgressText = 'SUCCESS') or (Copy(ProgressText, 1, 7) = 'FAILED:') then
    begin
      if ProgressText = 'SUCCESS' then
      begin
        WizardForm.ProgressGauge.Position := 100;
        WizardForm.StatusLabel.Caption := 'GPU acceleration files downloaded.';
        WizardForm.Refresh;
      end;
      // A FAILED: line is deliberately not surfaced as an error dialog --
      // see this procedure's own header comment on why that's fine.
      Break;
    end;
    Percent := StrToIntDef(ProgressText, -1);
    if Percent >= 0 then
    begin
      WizardForm.ProgressGauge.Position := Percent;
      WizardForm.StatusLabel.Caption :=
        'Downloading GPU acceleration files... ' + IntToStr(Percent) + '%';
      WizardForm.Refresh;
    end;
  end;
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
  IsFreshInstall: Boolean;
begin
  if CurStep <> ssPostInstall then
    Exit;
  ConfigDir := ExpandConstant('{userappdata}\dictate');
  ConfigFile := ConfigDir + '\settings.json';
  IsFreshInstall := not FileExists(ConfigFile);

  if IsFreshInstall then
  begin
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

  // Gated on IsFreshInstall the same way the settings seed above is: a
  // silent self-update re-runs Setup with the gpuaccel task defaulting
  // back to "selected" on any NVIDIA machine (no /TASKS= is ever passed --
  // see updater.py's start_update()), which would otherwise attempt this
  // download on every single update regardless of what the user actually
  // chose or later changed in Settings. GpuRuntimeInstalled() is also
  // checked so a repair/reinstall never repeats a download that already
  // succeeded.
  if IsFreshInstall and WizardIsTaskSelected('gpuaccel') and not GpuRuntimeInstalled() then
    DownloadGpuRuntime();
end;

// --- Uninstall: ask before throwing away downloads ---
//
// A speech model can be ~600 MB-1.5 GB and the GPU runtime is ~1.3 GB --
// both real downloads someone would rather not repeat after a plain
// uninstall/reinstall (troubleshooting a stuck install, moving to a fresh
// build, whatever). The previous version of this script deleted
// {userappdata}\dictate (settings.json AND every downloaded model) and
// {app}\_internal (which now also holds the GPU runtime) unconditionally on
// every uninstall, no way to opt out. This asks once, up front, and only
// removes them if the answer is yes.
var
  KeepDownloads: Boolean;

// Inno Setup's Pascal Script has no BoolToStr -- that's a Delphi SysUtils
// function this dialect doesn't include.
function YesNo(B: Boolean): string;
begin
  if B then
    Result := 'True'
  else
    Result := 'False';
end;

// Inno Setup has no built-in "is this switch present" check -- ParamStr/
// ParamCount are the raw primitives it does provide, so this is the usual
// hand-rolled wrapper every script needing a custom command-line flag ends
// up writing.
function CmdLineParamExists(const Value: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Value) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function InitializeUninstall(): Boolean;
var
  DataDir, GpuDir: string;
begin
  Result := True;
  DataDir := ExpandConstant('{userappdata}\dictate');
  GpuDir := ExpandConstant('{app}\_internal\nvidia');
  if not (DirExists(DataDir) or DirExists(GpuDir)) then
  begin
    KeepDownloads := True;  // nothing there to remove either way
    Log('InitializeUninstall: nothing to remove, KeepDownloads=True');
    Exit;
  end;
  // /REMOVEDATA is this script's own switch (not a stock Inno Setup
  // flag) for an unattended uninstall that specifically wants everything
  // gone -- checked before UninstallSilent() so a silent run can still
  // choose either outcome deliberately, not just fall through to the
  // silent default below.
  if CmdLineParamExists('/REMOVEDATA') then
  begin
    KeepDownloads := False;
    Log('InitializeUninstall: /REMOVEDATA present, KeepDownloads=False');
    Exit;
  end;
  if UninstallSilent() then
  begin
    // A silent/unattended uninstall (a script, /VERYSILENT with no
    // /REMOVEDATA) has nobody to answer a MsgBox -- MsgBox does not
    // respect /SUPPRESSMSGBOXES for custom Pascal Script calls the way
    // Setup's own built-in dialogs do, so showing one here would hang
    // indefinitely waiting for a click that will never come. Default to
    // the safer outcome (keep) rather than silently destroying a
    // multi-GB download nobody explicitly asked to lose.
    KeepDownloads := True;
    Log('InitializeUninstall: silent run, no /REMOVEDATA, KeepDownloads=True');
    Exit;
  end;
  KeepDownloads :=
    MsgBox(
      'Keep your downloaded speech models, GPU acceleration files, and ' +
      'settings?' + #13#10#13#10 +
      'Choosing Yes leaves them on this PC, so reinstalling Dictate ' +
      'later picks up right where you left off with nothing to ' +
      'download again. Choosing No removes everything Dictate has ' +
      'saved on this PC.',
      mbConfirmation, MB_YESNO) = IDYES;
  Log('InitializeUninstall: interactive prompt answered, KeepDownloads=' + YesNo(KeepDownloads));
end;

// A directory holding a file the just-exited app process hadn't fully
// released yet (Windows can lag briefly after even a graceful exit, worse
// after a forceful kill) makes a single DelTree attempt an unreliable
// signal -- Log the actual result and, if it reports non-success, retry a
// few times with a short wait rather than silently leaving the directory
// half (or entirely) intact. This is what the app-folder incident during
// today's own testing looked like: exit code 0 from the uninstaller, but
// %APPDATA%\dictate mostly still there afterward.
function DelTreeWithRetry(Path: string; Label_: string): Boolean;
var
  Attempt: Integer;
begin
  Result := False;
  for Attempt := 1 to 5 do
  begin
    if not DirExists(Path) then
    begin
      Result := True;
      Log(Label_ + ': already gone (attempt ' + IntToStr(Attempt) + ')');
      Exit;
    end;
    Result := DelTree(Path, True, True, True);
    Log(Label_ + ': DelTree attempt ' + IntToStr(Attempt) + ' returned ' + YesNo(Result));
    if Result then
      Exit;
    Sleep(500);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataOk, GpuOk: Boolean;
begin
  // usPostUninstall: Inno has already removed every file [Files] installed
  // (dictate.exe, all of _internal's own bundled runtime, updater\) through
  // its own automatic per-file uninstall tracking -- nothing here needs to
  // repeat that. This only ever touches the two things that were never
  // tracked that way: the GPU runtime (fetched at install time by
  // DownloadGpuRuntime, not bundled in the installer) and everything under
  // %APPDATA%\dictate (all written by the running app itself, not Setup).
  if CurUninstallStep = usPostUninstall then
  begin
    Log('CurUninstallStepChanged(usPostUninstall): KeepDownloads=' + YesNo(KeepDownloads));
    if not KeepDownloads then
    begin
      DataOk := DelTreeWithRetry(ExpandConstant('{userappdata}\dictate'), 'AppData cleanup');
      GpuOk := DelTreeWithRetry(ExpandConstant('{app}\_internal\nvidia'), 'GPU runtime cleanup');
      if not DataOk then
        Log('CurUninstallStepChanged: %APPDATA%\dictate still present after all retries');
      if not GpuOk then
        Log('CurUninstallStepChanged: _internal\nvidia still present after all retries');
      // Inno's own removal already emptied {app}\_internal of everything it
      // tracked; now that nvidia\ is gone too these should be genuinely
      // empty. RemoveDir silently no-ops on a directory that isn't (kept
      // files, a locked handle) -- never a hard failure either way.
      RemoveDir(ExpandConstant('{app}\_internal'));
      RemoveDir(ExpandConstant('{app}'));
    end;
  end;
end;
