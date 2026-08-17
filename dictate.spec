# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Dictate.

Onedir, not onefile: faster-whisper/ctranslate2 + PySide6 are a heavy,
DLL-heavy footprint on their own. Onefile's self-extraction adds real
startup latency to a tray app meant to launch fast, and is a stronger
antivirus/SmartScreen trigger on top of this build already being unsigned.
Onedir also maps directly onto the Inno Setup installer's Program Files
layout with no extra unpacking step.

CUDA DLLs (nvidia-cublas-cu12, nvidia-cudnn-cu12, cuda_nvrtc) are
deliberately NOT collected here. Bundling them made every build/installer
~1GB regardless of whether the machine had an NVIDIA card. gpu_runtime.py
already fetches those exact wheels from PyPI and lays them out under
``_internal/nvidia/<pkg>/bin`` on demand -- the same path engine.py's
_register_cuda_dlls() already searches -- so a Core-only build plus that
on-demand download is now the only path to GPU acceleration, at install
time or later from Settings.

A second, independent target is built here too: update_splash.py ->
dictate-updater.exe, the native progress window shown while the installer
silently replaces dictate.exe's own files (see that file's own docstring).
It gets its own Analysis/EXE/COLLECT into dist/dictate-updater/ rather than
sharing dictate.exe's _internal tree -- PyInstaller's MERGE() can do real
sharing between two targets, but ties their output folders together with
relative-path assumptions for uncertain benefit here; Inno Setup's
SolidCompression=yes already collapses most of the duplicated Qt/Python-
runtime bytes in the final installer regardless of whether the two onedir
trees are physically merged.

Build with: uv run pyinstaller dictate.spec --noconfirm
Output: dist/dictate/dictate.exe (plus its _internal/ support tree), and
        dist/dictate-updater/dictate-updater.exe (its own small tree).
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

ctranslate2_datas, ctranslate2_binaries, ctranslate2_hidden = collect_all("ctranslate2")

datas = []
datas += ctranslate2_datas
datas += collect_data_files("faster_whisper")  # the bundled Silero VAD model
datas += [("icon.ico", ".")]

binaries = []
binaries += ctranslate2_binaries

hiddenimports = []
hiddenimports += ctranslate2_hidden
hiddenimports += ["huggingface_hub", "faster_whisper"]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dictate",
    icon="icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed: the tray icon and Settings window are the UI
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="dictate",
)

# --- dictate-updater.exe: the update-progress splash, built separately ---

splash_a = Analysis(
    ["update_splash.py"],
    pathex=[],
    binaries=[],
    datas=[("icon.ico", ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
splash_pyz = PYZ(splash_a.pure, cipher=block_cipher)

splash_exe = EXE(
    splash_pyz,
    splash_a.scripts,
    [],
    exclude_binaries=True,
    name="dictate-updater",
    icon="icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

splash_coll = COLLECT(
    splash_exe,
    splash_a.binaries,
    splash_a.zipfiles,
    splash_a.datas,
    strip=False,
    upx=False,
    name="dictate-updater",
)
