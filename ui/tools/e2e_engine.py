"""Runs the real Dictate engine from source, isolated from the installed copy that is running on this PC.

Own settings folder (APPDATA), own single-instance names, a harmless talk key, and the four things that would touch
the real machine switched off: the Windows autostart entry, the model download, the microphone warm-up, and the
temp-folder cleanup. Everything else is the real code, including main.py's WinUI path (the new program owning the
activity bar, the tray icon and Settings).

Extra console commands for the test harness, typed on stdin (the engine's own commands still work):
  bar listening | transcribing | done | error | loading | progress | idle | lock | hide
  notify          an update-style notice whose click prints "NOTICE CLICKED"
  balloon         a Windows notification through the tray
  ping            prints PONG (the engine's event loop is alive)
"""
import json
import math
import os
import sys
from pathlib import Path

PROJECT = r"C:\Users\trach\OneDrive\Documents\Coding Projects\01_Active\dictate"
base = Path(sys.argv[1])
os.environ["APPDATA"] = str(base)  # before any project import: config.CONFIG_DIR reads it
os.environ["DICTATE_WINUI"] = "1"
# The program the engine starts inherits these: its own data folder, its test mutex, and a debug.log that names its tray window.
os.environ["DICTATEUI_HOME"] = str(base / "ui")
os.environ["DICTATEUI_DEBUG"] = "1"
if len(sys.argv) > 2 and sys.argv[2]:
    os.environ["DICTATE_UI_EXE"] = sys.argv[2]
sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

cfg = base / "dictate"
cfg.mkdir(parents=True, exist_ok=True)
if not (cfg / "settings.json").exists():
    (cfg / "settings.json").write_text(json.dumps({
        "device": "cpu", "model_size": "small.en", "ptt_key": "ctrl+alt+f12",
        "settings_hotkey": "ctrl+alt+shift+f11", "onboarding_complete": True,
        "start_with_windows": False, "auto_update_enabled": True, "sound_cues": True,
        "system_notifications_enabled": True,
    }), encoding="utf-8")

import startup
startup.set_enabled = lambda enabled: None
startup.is_enabled = lambda: False
import updater as updater_mod
updater_mod.cleanup_stale_downloads = lambda: None
# A fresh settings folder has never seen this version's "What's new", and the engine would open that (still built-in, modal)
# dialog at start-up and sit inside it. Mark it seen in the test folder, as a real install would have after the first run.
from version import VERSION
updater_mod.mark_whats_new_seen(VERSION)
import engine as engine_mod
engine_mod.Engine.preload = lambda self: False
import audio as audio_mod
audio_mod.MicCapture.warm_up = lambda self: None

import main
main.MUTEX_NAME = "Local\\DictateE2ETest"
main.RUNNING_NOTICE_EVENT = "Local\\DictateE2ETestNotice"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QSystemTrayIcon

original_on_command = main.App._on_command
voice = {"timer": None, "t": 0.0}


def stop_voice(app):
    if voice["timer"] is not None:
        voice["timer"].stop()
        voice["timer"] = None


def start_voice(app):
    stop_voice(app)
    timer = QTimer()
    timer.setInterval(16)

    def tick():
        voice["t"] += 0.016
        level = max(0.0, math.sin(voice["t"] * 1.7)) * 0.6
        app.bar.set_levels([level] * 20)  # the real call main.py makes, 60 times a second

    timer.timeout.connect(tick)
    timer.start()
    voice["timer"] = timer


def on_command(self, text):
    if text == "ping":
        print("PONG", flush=True)
    elif text.startswith("bar "):
        what = text[4:]
        if what == "listening":
            self.bar.set_state("listening")
            start_voice(self)
            return
        if what == "lock":
            self.bar.set_state("listening")
            self.bar.set_clickable(True)
            start_voice(self)
            return
        stop_voice(self)
        self.bar.set_clickable(False)
        if what in ("transcribing", "done", "idle"):
            self.bar.set_state(what)
        elif what == "error":
            self.bar.set_state("error", "No microphone detected.")
        elif what == "loading":
            self.bar.set_state("loading", "Preparing Small")
        elif what == "progress":
            self.bar.set_state("loading", "Downloading Small", 0.4)
        elif what == "hide":
            self.bar.hide()
        print(f"BAR {what}", flush=True)
    elif text == "notify":
        self._notify("Update 1.4.4 available. Click to open.", on_click=lambda: print("NOTICE CLICKED", flush=True), duration_ms=30000)
    elif text == "balloon":
        self.tray.showMessage("Dictate", "A Windows notification from the engine", QSystemTrayIcon.MessageIcon.Information, 4000)
    else:
        original_on_command(self, text)


main.App._on_command = on_command

# If something hangs, the last dump in stacks.txt shows where every thread is.
import faulthandler
_stacks = open(base / "stacks.txt", "w")
faulthandler.dump_traceback_later(15, repeat=True, file=_stacks)

app = main.App()
app.bar.clicked.connect(lambda: print("BAR CLICKED", flush=True))
QTimer.singleShot(1500, app._show_settings)

# Commands arrive as lines appended to e2e-cmd.txt (the test script cannot hold this process's stdin open reliably).
command_file = base / "e2e-cmd.txt"
command_file.write_text("", encoding="utf-8")
position = {"at": 0}


def poll_commands():
    try:
        with open(command_file, "r", encoding="utf-8") as f:
            f.seek(position["at"])
            data = f.read()
            position["at"] = f.tell()
    except OSError:
        return
    for line in data.splitlines():
        text = line.strip().lower()
        if text:
            app.bridge.command.emit(text)


command_timer = QTimer()
command_timer.setInterval(100)
command_timer.timeout.connect(poll_commands)
command_timer.start()
print("E2E READY", flush=True)
sys.exit(app.run())
