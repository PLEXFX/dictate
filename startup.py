"""Register Dictate for the current user's Windows sign-in.

In the dev checkout, the Run entry points at a tiny hidden VBS launcher so
Windows can start the resilient batch launcher (which activates the venv)
without flashing a console window. An installed build needs none of that
indirection -- it's a single windowed exe already, so the Run entry points
at it directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Dictate"
LAUNCHER_PATH = Path(__file__).resolve().parent / "run-dictate-hidden.vbs"


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    wscript = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "wscript.exe"
    return f'"{wscript}" "{LAUNCHER_PATH}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    return str(value).casefold() == startup_command().casefold()


def set_enabled(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
            return
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
