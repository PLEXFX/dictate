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
;   "C:\Users\trach\AppData\Local\Programs\Inno Setup 6\ISCC.exe" dictate.iss
; after first producing dist\dictate\ via: uv run pyinstaller ..\dictate.spec
;
; Override the version at build time without editing this file:
;   ISCC.exe dictate.iss /DMyAppVersion=0.1.0-beta.3

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0-beta.2"
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

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
