"""Small, local hardware facts used to make Dictate's Processing choice honest."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import winreg
from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareProfile:
    cpu_name: str
    logical_cores: int
    gpu_name: str = ""
    vram_mb: int = 0

    @property
    def has_gpu_details(self) -> bool:
        return bool(self.gpu_name and self.vram_mb > 0)

    def summary(self) -> str:
        if self.has_gpu_details:
            # Round rather than floor: a 12 GB card reports 12,282 MB, and
            # flooring showed it to its owner as "11 GB VRAM".
            return f"{self.gpu_name} · {round(self.vram_mb / 1024)} GB VRAM"
        return f"{self.cpu_name} · {self.logical_cores} logical cores"


def _cpu_name() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if isinstance(name, str) and name.strip():
                return " ".join(name.split())
    except OSError:
        pass
    return platform.processor().strip() or os.environ.get("PROCESSOR_IDENTIFIER", "CPU")


def _nvidia_gpu() -> tuple[str, int]:
    """Read the primary NVIDIA GPU without installing a second dependency."""
    command = shutil.which("nvidia-smi")
    if not command:
        return "", 0
    try:
        result = subprocess.run(
            [command, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
            # nvidia-smi is a console app; without this a windowed build of
            # Dictate still flashes a real console window open and closed
            # every time Settings opens and probes the GPU.
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        line = result.stdout.splitlines()[0].strip()
        name, memory = (part.strip() for part in line.rsplit(",", 1))
        return name, max(0, int(memory))
    except (IndexError, OSError, ValueError, subprocess.SubprocessError):
        return "", 0


def probe() -> HardwareProfile:
    """Collect the few local facts that affect sensible speech-model choices."""
    gpu_name, vram_mb = _nvidia_gpu()
    return HardwareProfile(
        cpu_name=_cpu_name(),
        logical_cores=os.cpu_count() or 1,
        gpu_name=gpu_name,
        vram_mb=vram_mb,
    )
