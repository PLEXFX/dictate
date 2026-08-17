# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Dictate.

Onedir, not onefile: faster-whisper/ctranslate2 + PySide6 + the optional CUDA
DLLs are a heavy, DLL-heavy footprint. Onefile's self-extraction adds real
startup latency to a tray app meant to launch fast, and is a stronger
antivirus/SmartScreen trigger on top of this build already being unsigned.
Onedir also maps directly onto the Inno Setup installer's Program Files
layout with no extra unpacking step.

CUDA DLLs (nvidia-cublas-cu12, nvidia-cudnn-cu12, and cuda_nvrtc alongside
them) are collected here like everything else, but deliberately land under
their own site-packages-relative path (``_internal/nvidia/...``) rather than
being merged into anything else -- that predictable, isolated path is what
lets the Inno Setup script offer them as an optional "GPU acceleration"
component instead of forcing every user to download them. engine.py's own
_register_cuda_dlls() already searches for exactly this ``nvidia/<pkg>/bin``
layout, so nothing there needs to change for either component choice.

Build with: uv run pyinstaller dictate.spec --noconfirm
Output: dist/dictate/dictate.exe (plus its _internal/ support tree).
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

block_cipher = None

ctranslate2_datas, ctranslate2_binaries, ctranslate2_hidden = collect_all("ctranslate2")

datas = []
datas += ctranslate2_datas
datas += collect_data_files("faster_whisper")  # the bundled Silero VAD model
datas += [("icon.ico", ".")]

binaries = []
binaries += ctranslate2_binaries
# nvidia-cublas-cu12 / nvidia-cudnn-cu12 (and the cuda_nvrtc pulled in
# alongside them) are never `import`ed as executable Python -- engine.py only
# locates them via importlib.util.find_spec -- so PyInstaller's normal import
# analysis can't discover their DLLs on its own; they have to be collected
# explicitly.
binaries += collect_dynamic_libs("nvidia")

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
