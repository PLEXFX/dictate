"""On-demand GPU (CUDA) compute runtime for an installed build.

A Core-only install has no nvidia-cublas-cu12 / nvidia-cudnn-cu12 DLLs on
disk at all -- they were an optional several-GB installer component. GPU
*detection* still works without them (the system's own NVIDIA driver
answers "is there a CUDA device", not these files), so a Core-only install
on a real GPU machine can select "GPU" in Settings today and just silently
fall back to CPU when CTranslate2 can't find the compute libraries. This
module lets that moment become a real download instead of a silent no-op.

Fetches straight from PyPI rather than re-hosting a copy on every GitHub
release -- it's the same place `uv pip install` already gets these from,
and there's nothing to keep in sync across releases.

Only meaningful for a frozen (PyInstaller) build. The dev/uv workflow
already has these three packages as ordinary pyproject.toml dependencies.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from updater import _REQUEST_TIMEOUT, _USER_AGENT, _download

# Maps each PyPI package to the subdirectory name engine.py's own
# _register_cuda_dlls() already searches for under nvidia/<name>/bin.
_PACKAGE_SUBDIRS = {
    "nvidia-cublas-cu12": "cublas",
    "nvidia-cudnn-cu12": "cudnn",
    "nvidia-cuda-nvrtc-cu12": "cuda_nvrtc",
}
# Mirrors pyproject.toml's own nvidia-cudnn-cu12>=9,<10 ceiling.
_CUDNN_MAX_MAJOR = 10


def runtime_dir() -> Path:
    """Where the CUDA DLLs live (or should end up).

    A frozen onedir build's sys._MEIPASS is the _internal folder PyInstaller
    already isolates these into at build time -- see dictate.spec. In dev
    mode this is informational only (uv pip install already manages it);
    is_installed()/needs_download() gate on sys.frozen before this matters.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "nvidia"
    try:
        spec = importlib.util.find_spec("nvidia")
        if spec and spec.submodule_search_locations:
            return Path(list(spec.submodule_search_locations)[0])
    except Exception:
        pass
    return Path()


def is_installed() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    base = runtime_dir()
    return all((base / subdir / "bin").is_dir() for subdir in _PACKAGE_SUBDIRS.values())


def needs_download(*, gpu_available: bool) -> bool:
    """True exactly when there's a real CUDA device but our compute DLLs
    aren't on disk -- the one case a download can actually fix. Takes
    ``gpu_available`` rather than calling engine.cuda_available() itself to
    avoid a circular import (engine.py is the caller here).
    """
    return bool(getattr(sys, "frozen", False) and gpu_available and not is_installed())


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for chunk in v.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _latest_win_amd64_wheel(
    package: str, *, max_major: Optional[int] = None
) -> Optional[tuple[str, int]]:
    """The newest win_amd64 wheel URL/size for a package, or None on failure.

    Uses the `releases` map (every version PyPI has ever published) rather
    than just `info.version`/`urls` (the latest release only), since
    nvidia-cudnn-cu12 needs to stay under a major-version ceiling that a
    brand new release could cross.
    """
    req = urllib.request.Request(
        f"https://pypi.org/pypi/{package}/json", headers={"User-Agent": _USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    candidates: list[tuple[tuple[int, ...], str, int]] = []
    for version, files in (data.get("releases") or {}).items():
        vt = _version_tuple(version)
        if not vt or (max_major is not None and vt[0] >= max_major):
            continue
        for f in files or []:
            name = str(f.get("filename", ""))
            if name.lower().endswith("-win_amd64.whl") and not f.get("yanked"):
                url = f.get("url")
                if url:
                    candidates.append((vt, url, int(f.get("size") or 0)))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    _vt, url, size = candidates[-1]
    return url, size


def download_and_install(
    on_progress: Optional[Callable[[int, int], None]] = None
) -> bool:
    """Fetch and install the CUDA compute DLLs. Never raises -- a failure
    just means needs_download() stays true and the caller's existing
    CPU-fallback path (engine.py's GPU-load try/except) handles the rest.

    Builds the full nvidia/ tree in a temp directory first and only then
    swaps it into place with one move, so an interrupted download or a
    process killed partway through never leaves is_installed() seeing a
    half-populated folder as complete.
    """
    if not getattr(sys, "frozen", False):
        return False

    wheels: list[tuple[str, str, int]] = []
    for package in _PACKAGE_SUBDIRS:
        max_major = _CUDNN_MAX_MAJOR if package == "nvidia-cudnn-cu12" else None
        found = _latest_win_amd64_wheel(package, max_major=max_major)
        if found is None:
            return False
        url, size = found
        wheels.append((package, url, size))

    total = sum(size for _p, _u, size in wheels)
    completed = 0

    try:
        with tempfile.TemporaryDirectory(prefix="dictate-gpu-") as tmp:
            tmp_path = Path(tmp)
            extract_root = tmp_path / "nvidia"
            extract_root.mkdir()

            for package, url, size in wheels:
                wheel_path = tmp_path / f"{package}.whl"
                base = completed

                def on_chunk(n: int, _total: Optional[int]) -> None:
                    if on_progress is not None and total:
                        on_progress(base + n, total)

                _download(url, wheel_path, on_chunk)
                completed += size

                subdir = _PACKAGE_SUBDIRS[package]
                prefix = f"nvidia/{subdir}/bin/"
                with zipfile.ZipFile(wheel_path) as zf:
                    members = [
                        m for m in zf.namelist() if m.startswith(prefix) and not m.endswith("/")
                    ]
                    if not members:
                        return False
                    zf.extractall(tmp_path, members)
                wheel_path.unlink(missing_ok=True)

            target = runtime_dir()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(extract_root), str(target))
    except Exception as exc:
        print(f"[dictate] GPU runtime download failed: {exc}")
        return False

    return is_installed()
