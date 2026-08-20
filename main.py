"""Dictate: hold a key, speak, and the text lands in whatever you were typing in.

Wiring and thread policy for the whole app.

Three threads matter here:

- The Qt UI thread owns the bar, the settings window, and the tray icon. It
  must never block, or the waveform stutters.
- pynput's listener thread delivers the hotkeys.
- A short-lived worker per utterance runs transcription and the paste, both of
  which block for hundreds of milliseconds.

Every hop between them goes through Bridge's signals. Qt queues a signal
automatically when it crosses into the thread its receiver lives on, which
makes it the one safe way in from the listener and worker threads.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import audio as audio_mod
import bar as bar_mod
import config
import engine as engine_mod
import gpu_runtime
import hotkeys as hotkeys_mod
import inject
import release_notes as release_notes_mod
import sounds as sounds_mod
import startup
import updater as updater_mod
from theme import ThemeWatcher, resolve_dark
from bar import Bar
from settings_window import FirstRunDialog, SettingsWindow, UpdateCompleteDialog
from version import VERSION

APP_NAME = "Dictate"
# All three named objects live in the per-session "Local\\" namespace, not
# "Global\\". Dictate installs per user, keeps its settings in %APPDATA%, and
# every process that needs to find these is in the same signed-in session. A
# Global name is shared across every session on the machine, so a second
# signed-in Windows user could not start Dictate at all while the first had it
# open -- the single-instance mutex would already be held by someone else's
# session, and the running-notice event would be raised in it.
MUTEX_NAME = "Local\\DictateSingleInstance"
# A second launch attempt sets this; the running instance polls it rather
# than running a message-pump listener for a second, unrelated purpose.
RUNNING_NOTICE_EVENT = "Local\\DictateShowRunningNotice"
# The updater splash creates this manual-reset event before the silent
# installer starts. The freshly updated app signals it only after its visible
# "What's new" window has had a chance to render, which keeps the splash on
# screen through the otherwise blank handoff between two executables.
UPDATED_WINDOW_READY_EVENT = "Local\\DictateUpdatedWindowReady"

# Longest a locked (tap-started) recording runs before Dictate stops it and
# transcribes what it has. A hold is bounded by the user's finger; a lock is
# not, so a forgotten one would otherwise hold the microphone open all day.
MAX_LOCKED_SECONDS = 5 * 60

# How long "Undo last dictation" stays on offer. Dictate cannot read another
# app's undo history, so it can never *prove* its paste is still the top of
# that stack -- it can only refuse whenever it has evidence otherwise. A short
# window is the last of those guards: the longer the offer sits there, the more
# chance the app's own state has moved on in some way no listener can see.
UNDO_WINDOW_SECONDS = 30

# (name, description) pairs for the debug console's `help` output. A plain
# list rather than a dict so the printed order matches the order below --
# roughly "look something up" first, then actions.
CONSOLE_COMMANDS: list[tuple[str, str]] = [
    ("status", "current engine state, active device, and auto-update setting"),
    ("gpu", "GPU detection and whether acceleration files are installed"),
    ("version", "the running Dictate version"),
    ("vocab", "the saved 'Words I use' list and the settings file location"),
    ("load model", "load the speech model now (alias: load)"),
    ("unload model", "release the speech model from memory (alias: unload)"),
    ("reload model", "reload after a settings change (alias: reload)"),
    ("test sound", "play the mic-open and mic-close cues"),
    ("settings", "open the Settings window"),
    ("check update", "check GitHub for a new release right now"),
    ("open data", "open the settings folder in File Explorer"),
    ("quit", "exit Dictate"),
    ("help", "show this list (alias: ?)"),
]
# A frozen build's __file__ points inside the bundle, not a real sibling
# directory containing icon.ico. sys._MEIPASS is PyInstaller's own answer to
# "where did you put my data files" -- the onedir _internal folder here, a
# temp extraction dir for onefile -- and is the documented way to find them
# rather than guessing the layout ourselves.
if getattr(sys, "frozen", False):
    BUNDLE_DATA_PATH = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
    )
    ICON_PATH = BUNDLE_DATA_PATH / "icon.ico"
else:
    BUNDLE_DATA_PATH = Path(__file__).resolve().parent
    ICON_PATH = BUNDLE_DATA_PATH / "icon.ico"
CHANGELOG_PATH = BUNDLE_DATA_PATH / "CHANGELOG.md"


def current_release_notes() -> str:
    """Read this build's one bundled changelog section.

    The in-app updater still carries GitHub's matching release body through
    the restart. This bundled source covers manual installer upgrades and the
    permanent Settings button without maintaining a second list.
    """
    try:
        return release_notes_mod.notes_for_version(VERSION, CHANGELOG_PATH)
    except (OSError, ValueError):
        return "Dictate has the latest improvements and fixes."


ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0


def _load_kernel32():
    """kernel32 with real signatures, or None where it cannot be loaded.

    Declaring these matters: without a restype, ctypes assumes a C ``int`` and
    truncates every returned HANDLE to 32 bits, and without argtypes it guesses
    at what to pass one back as. ``use_last_error`` matters for the same kind
    of reason -- it gives ctypes its own copy of the thread's error value, so
    ``get_last_error()`` reports what CreateMutexW actually set rather than
    whatever the next Windows call happened to leave behind.
    """
    try:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return None
    lib.CreateMutexW.restype = wintypes.HANDLE
    lib.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    lib.CreateEventW.restype = wintypes.HANDLE
    lib.CreateEventW.argtypes = (
        wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    )
    lib.SetEvent.argtypes = (wintypes.HANDLE,)
    lib.ResetEvent.argtypes = (wintypes.HANDLE,)
    lib.CloseHandle.argtypes = (wintypes.HANDLE,)
    lib.WaitForSingleObject.restype = wintypes.DWORD
    lib.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    lib.SetConsoleTitleW.argtypes = (wintypes.LPCWSTR,)
    return lib


_kernel32 = _load_kernel32()


def already_running() -> bool:
    """True when another copy holds the named mutex.

    Two instances would both grab the hotkey and both paste, so the second one
    exits rather than fighting the first.

    The handle is deliberately never closed: holding it open for the life of
    the process is what keeps the name claimed.
    """
    if _kernel32 is None:
        return False
    try:
        handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return bool(handle) and ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    except Exception:
        return False


def _signal_named_event(name: str) -> None:
    """Raise a named manual-reset event, creating it if it does not exist.

    CreateEventW is idempotent -- it opens the existing event when one is
    already there -- so this reaches the other process with no IPC channel
    beyond the name both sides already agree on.
    """
    if _kernel32 is None:
        return
    try:
        handle = _kernel32.CreateEventW(None, True, False, name)
        if handle:
            _kernel32.SetEvent(handle)
            _kernel32.CloseHandle(handle)
    except Exception:
        pass


def _signal_running_notice() -> None:
    """Wake the already-running instance's "still open" bar notification."""
    _signal_named_event(RUNNING_NOTICE_EVENT)


def _signal_updated_window_ready() -> None:
    """Tell a running update splash that the new Dictate window is visible."""
    _signal_named_event(UPDATED_WINDOW_READY_EVENT)


def load_icon() -> QIcon:
    """Use the shipped .ico if present, else fall back to the drawn glyph.

    Keeps the app working even if icon.ico is ever missing (a fresh clone, a
    stripped-down copy) instead of failing to start over cosmetics.
    """
    if ICON_PATH.exists():
        icon = QIcon(str(ICON_PATH))
        if not icon.isNull():
            return icon
    return make_icon()


def make_icon(color: QColor = QColor(76, 194, 255)) -> QIcon:
    """Draw the tray icon rather than shipping a .ico next to the code."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    path = QPainterPath()
    path.addRoundedRect(25, 10, 14, 26, 7, 7)          # capsule
    p.drawPath(path)
    p.drawRoundedRect(30, 38, 4, 10, 2, 2)             # stem
    p.drawRoundedRect(22, 46, 20, 4, 2, 2)             # base
    p.setBrush(Qt.NoBrush)
    p.setPen(QColor(color.red(), color.green(), color.blue(), 200))
    p.drawArc(18, 22, 28, 24, 200 * 16, 140 * 16)      # pickup arc
    p.end()
    return QIcon(pixmap)


def _tray_tip(settings) -> str:
    """One sentence for the tray icon, naming whichever gestures are live."""
    key = hotkeys_mod.format_combo(settings.ptt_key)
    if settings.tap_to_lock:
        return f"{APP_NAME} — hold {key} to talk, or tap it to keep recording"
    return f"{APP_NAME} — hold {key} to talk"


class Bridge(QObject):
    """Signal-only object used to hop onto the UI thread from other threads."""

    talk_started = Signal()
    talk_ended = Signal(float)
    talk_locked = Signal()  # a tap turned the hold into a locked recording
    talk_cancelled = Signal()  # a locked recording was abandoned, audio discarded
    open_settings = Signal()
    engine_state = Signal(str, str, object)  # state, detail, progress (0..1 or None)
    gpu_status = Signal(bool, object)  # downloading, progress (0..1 or None)
    finished = Signal(str, str)  # state, detail
    command = Signal(str)  # a line typed into the console, already normalized
    update_available = Signal(str, str)  # version, release notes -- nothing downloaded yet
    update_installing = Signal(str, int)  # version, installer pid -- app should quit now
    update_error = Signal(str)  # a start_update() download/verify/launch failure
    update_current = Signal()  # a manual check found nothing newer
    update_status_changed = Signal()  # live status/progress text changed
    dictated = Signal(str)  # successfully inserted text, kept only in memory
    pasted_into = Signal(int)  # window handle the text landed in, for undo
    undo_finished = Signal(bool)  # an undo attempt finished; True when it landed


class App:
    def __init__(self) -> None:
        self.settings = config.load()
        try:
            if startup.is_enabled() != self.settings.start_with_windows:
                startup.set_enabled(self.settings.start_with_windows)
        except OSError as exc:
            print(f"[dictate] startup setting error: {exc}")

        self.qt = QApplication(sys.argv)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setQuitOnLastWindowClosed(False)
        base = QFont("Segoe UI Variable Text", 9)
        self.qt.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))
        self.icon = load_icon()
        self.qt.setWindowIcon(self.icon)
        self.theme_watcher = ThemeWatcher(self.qt)

        self.bridge = Bridge()
        self.mic = audio_mod.MicCapture(self.settings.input_device)
        self.meter = audio_mod.SpectrumMeter(bands=bar_mod.HALF_BANDS)
        self.engine = engine_mod.Engine(
            self.settings,
            on_state=self.bridge.engine_state.emit,
            on_gpu_status=self.bridge.gpu_status.emit,
        )
        self.bar = Bar(self.settings)
        self.theme_watcher.changed.connect(self._on_theme_changed)
        self.cues = sounds_mod.Cues(self.settings.sound_cues)
        self.settings_window: SettingsWindow | None = None
        self._apply_appearance()
        self._busy = False
        self._dictation_active = False  # mic open or its captured audio is still processing
        self._ptt_preload_pending = False  # background warm-up started by a PTT press
        self._start_cue_at = 0.0  # when the "start" cue began, so "lock" can wait it out
        self._reload_pending = False  # set only by a Settings-triggered reload
        self._gpu_download_showing = False  # bar is currently reflecting a GPU download
        self._update_showing = False  # bar is currently reflecting update check/download/install
        self._update_ready_notified = False
        self._last_dictation = ""
        self._undo_target = None  # window the last paste landed in
        self._undo_at = 0.0
        self._clipboard_restore = None
        self._clipboard_restore_timer = QTimer()
        self._clipboard_restore_timer.setSingleShot(True)
        self._clipboard_restore_timer.timeout.connect(self._restore_recovery_clipboard)
        self._updated_restart = "--updated" in sys.argv
        self._update_notice = (
            updater_mod.consume_update_notice(VERSION) if self._updated_restart else None
        )
        self._whats_new_notes = (
            self._update_notice
            if self._update_notice is not None
            else current_release_notes()
        )
        self._show_whats_new_after_start = self.settings.onboarding_complete and (
            self._updated_restart or updater_mod.whats_new_is_unseen(VERSION)
        )
        self._prepared_update_splash: Path | None = None
        # A successful update never comes back to delete its own ~1 GB
        # temp download once it hands off to the installer -- do it here
        # instead, once per launch, so old downloads don't pile up in %TEMP%.
        updater_mod.cleanup_stale_downloads()

        self.hotkeys = hotkeys_mod.Hotkeys(
            self.settings,
            on_talk_start=self.bridge.talk_started.emit,
            on_talk_end=self.bridge.talk_ended.emit,
            on_settings=self.bridge.open_settings.emit,
            on_talk_lock=self.bridge.talk_locked.emit,
            on_talk_cancel=self.bridge.talk_cancelled.emit,
        )

        # Stops a locked recording that was started and then forgotten. Without
        # it a lock left running holds the microphone open and grows the capture
        # buffer for as long as the app runs.
        self.lock_limit_timer = QTimer()
        self.lock_limit_timer.setSingleShot(True)
        self.lock_limit_timer.setInterval(int(MAX_LOCKED_SECONDS * 1000))
        self.lock_limit_timer.timeout.connect(self._on_lock_limit)

        # Retires the undo offer on its own, so a menu left unopened for an
        # hour never presents a stale one.
        self.undo_expiry_timer = QTimer()
        self.undo_expiry_timer.setSingleShot(True)
        self.undo_expiry_timer.setInterval(UNDO_WINDOW_SECONDS * 1000)
        self.undo_expiry_timer.timeout.connect(self._withdraw_undo)

        # Polling, not a message-pump listener, because that named event is
        # the only thing this app ever needs to hear from a second launch
        # attempt -- see _signal_running_notice() and already_running().
        try:
            self._running_notice_handle = (
                _kernel32.CreateEventW(None, True, False, RUNNING_NOTICE_EVENT)
                if _kernel32 is not None
                else None
            )
        except Exception:
            self._running_notice_handle = None
        self.running_notice_timer = QTimer()
        self.running_notice_timer.setInterval(500)
        self.running_notice_timer.timeout.connect(self._check_running_notice)
        if self._running_notice_handle:
            self.running_notice_timer.start()

        self.bridge.talk_started.connect(self._start_listening)
        self.bridge.talk_ended.connect(self._stop_listening)
        self.bridge.talk_locked.connect(self._on_talk_locked)
        self.bridge.talk_cancelled.connect(self._cancel_listening)
        self.bridge.open_settings.connect(self._show_settings)
        self.bridge.engine_state.connect(self._on_engine_state)
        self.bridge.gpu_status.connect(self._on_gpu_status)
        self.bridge.finished.connect(self._on_finished)
        self.bridge.command.connect(self._on_command)
        self.bridge.update_available.connect(self._on_update_available)
        self.bridge.update_installing.connect(self._on_update_installing)
        self.bridge.update_error.connect(self._on_update_error)
        self.bridge.update_current.connect(self._on_update_current)
        self.bridge.update_status_changed.connect(self._on_update_status_changed)
        self.bridge.dictated.connect(self._remember_last_dictation)
        self.bridge.pasted_into.connect(self._offer_undo)
        self.bridge.undo_finished.connect(self._on_undo_finished)
        self.bar.clicked.connect(self._on_bar_clicked)

        # Drives the waveform. Only runs while the mic is open.
        self.meter_timer = QTimer()
        self.meter_timer.setInterval(16)
        self.meter_timer.timeout.connect(self._pump_meter)

        self._build_tray()
        self.hotkeys.start()
        self._start_command_listener()
        self.updater = updater_mod.Updater(
            on_available=self.bridge.update_available.emit,
            on_prepare_installing=self._prepare_update_splash,
            on_installing=self.bridge.update_installing.emit,
            on_error=self.bridge.update_error.emit,
            on_up_to_date=self.bridge.update_current.emit,
            on_status_change=self.bridge.update_status_changed.emit,
            enabled=self.settings.auto_update_enabled,
        )

        if not self.settings.sleep_enabled:
            self.engine.preload()
        if self.settings.always_visible:
            self.bar.set_state("idle")
            self.bar.show_bar()
        if not self.settings.onboarding_complete:
            QTimer.singleShot(0, self._show_first_run)
        elif self._show_whats_new_after_start:
            QTimer.singleShot(300, self._show_update_complete)
        else:
            # Skipped on first run (the welcome dialog already greets them)
            # and on a just-updated restart (the What's New dialog already
            # does) -- this is only the plain "an ordinary launch finished"
            # case, which otherwise has no visible confirmation at all since
            # Dictate opens straight to the tray with no window.
            QTimer.singleShot(500, self._show_ready_notice)

    # --- tray ---

    def _apply_appearance(self) -> None:
        """Apply Dictate's System/Light/Dark choice."""
        watcher = getattr(self, "theme_watcher", None)
        system_dark = watcher.dark if watcher is not None else None
        dark = resolve_dark(self.settings.theme_mode, system_dark)
        self.bar.set_theme(dark)
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.set_theme(system_dark or False)

    def _on_theme_changed(self, _dark: bool) -> None:
        if self.settings.theme_mode == "system":
            self._apply_appearance()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(_tray_tip(self.settings))

        menu = QMenu()
        # format_combo, like every other person-facing surface: the raw stored
        # binding is "ctrl+alt+d", which is a storage format, not a label.
        # Kept live in _apply_settings so rebinding updates this menu too.
        self.act_settings = QAction(
            f"Settings\t{hotkeys_mod.format_combo(self.settings.settings_hotkey)}", menu
        )
        self.act_settings.triggered.connect(self._show_settings)
        menu.addAction(self.act_settings)
        menu.addSeparator()

        act_load = QAction("Load model now", menu)
        act_load.triggered.connect(self.engine.preload)
        menu.addAction(act_load)

        act_unload = QAction("Unload model", menu)
        act_unload.triggered.connect(self.engine.unload)
        menu.addAction(act_unload)

        self.act_copy_last = QAction("Copy last dictation", menu)
        self.act_copy_last.setEnabled(False)
        self.act_copy_last.triggered.connect(self._copy_last_dictation)
        menu.addAction(self.act_copy_last)

        self.act_undo = QAction("Undo last dictation", menu)
        self.act_undo.setEnabled(False)
        self.act_undo.triggered.connect(self._undo_last_paste)
        menu.addAction(self.act_undo)
        menu.addSeparator()

        # Enabled state mirrors auto_update_enabled (kept live in
        # _apply_settings) rather than always being clickable -- Settings'
        # own "Check for updates" button already disables itself for the
        # same reason: the toggle's own description promises Dictate "never
        # contacts GitHub about updates" when off, and check_now() already
        # no-ops silently then. A clickable action that silently does
        # nothing reads as broken; a disabled one reads as exactly what it
        # is.
        self.act_check_update = QAction("Check for updates", menu)
        self.act_check_update.setEnabled(self.settings.auto_update_enabled)
        self.act_check_update.triggered.connect(self._check_for_updates)
        menu.addAction(self.act_check_update)
        menu.addSeparator()

        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_settings()
            if reason == QSystemTrayIcon.Trigger
            else None
        )
        self.tray.show()

    # --- console commands ---

    def _start_command_listener(self) -> None:
        """Let commands be typed into the console this app was launched from.

        Only useful when a real console is attached (run-dictate-debug.bat);
        run-dictate.bat and the hidden startup launch have no console and no
        one to type into it, so the read just blocks forever on a daemon
        thread and does no harm.
        """
        try:
            if sys.stdin is None or not sys.stdin.readable():
                return
        except Exception:
            return
        try:
            if _kernel32 is not None:
                _kernel32.SetConsoleTitleW(f"{APP_NAME} — debug console")
        except Exception:
            pass
        print(f"[dictate] {APP_NAME} {VERSION} — type 'help' for commands")
        threading.Thread(target=self._command_loop, daemon=True).start()

    def _command_loop(self) -> None:
        try:
            for line in sys.stdin:
                text = line.strip().lower()
                if text:
                    self.bridge.command.emit(text)
        except Exception:
            pass

    def _print_help(self) -> None:
        width = max(len(name) for name, _desc in CONSOLE_COMMANDS)
        print(f"[dictate] {APP_NAME} {VERSION} commands:")
        for name, desc in CONSOLE_COMMANDS:
            print(f"    {name.ljust(width)}   {desc}")

    def _on_command(self, text: str) -> None:
        if text in ("reload", "reload model", "reload models"):
            print("[dictate] reload requested")
            self._reload_pending = True
            self.engine.reload()
        elif text in ("load", "load model"):
            print("[dictate] load requested")
            self._reload_pending = True
            self.engine.preload()
        elif text in ("unload", "unload model"):
            self.engine.unload()
        elif text == "status":
            print(f"[dictate]   state         {self.engine.state}")
            print(f"[dictate]   device        {self.engine.active_device or '(none)'}")
            print(
                f"[dictate]   auto-update   "
                f"{'on' if self.settings.auto_update_enabled else 'off'}"
            )
        elif text == "gpu":
            detected = engine_mod.cuda_available()
            print(f"[dictate]   GPU detected           {'yes' if detected else 'no'}")
            print(
                f"[dictate]   acceleration installed  "
                f"{'yes' if gpu_runtime.is_installed() else 'no'}"
            )
        elif text == "version":
            print(f"[dictate] {APP_NAME} {VERSION}")
        elif text in ("vocab", "vocabulary"):
            words = self.settings.vocabulary
            if words:
                print(f"[dictate] vocabulary ({len(words)}): {', '.join(words)}")
            else:
                print("[dictate] vocabulary: (empty)")
            print(f"[dictate] settings file: {config.CONFIG_PATH}")
        elif text in ("test sound", "sound test"):
            print("[dictate] playing cues")
            self.cues.play("start")
            QTimer.singleShot(400, lambda: self.cues.play("stop"))
        elif text == "settings":
            self._show_settings()
        elif text in ("check update", "check for updates"):
            if not self.settings.auto_update_enabled:
                print(
                    "[dictate] update checks are turned off "
                    "(Settings > Check for updates automatically)"
                )
            # Printing "checking…" unconditionally was a lie whenever
            # check_now declined -- report what actually happened instead.
            elif self.updater.check_now(silent=False):
                print("[dictate] checking GitHub for a new release…")
            else:
                state, detail, _progress = self.updater.last_status
                print(f"[dictate] {detail or 'no check started'} ({state})")
        elif text in ("open data", "open settings folder", "open folder"):
            try:
                os.startfile(config.CONFIG_DIR)
            except OSError as exc:
                print(f"[dictate] could not open {config.CONFIG_DIR}: {exc}")
        elif text == "quit":
            self._quit()
        elif text in ("help", "?"):
            self._print_help()
        else:
            print(f"[dictate] unknown command: {text!r} (try 'help')")

    # --- dictation flow ---

    def _start_listening(self) -> None:
        if self._busy:
            # Still pasting the last one; ignore the new press. Cancelling the
            # lock too stops a tap here from locking a recording that never
            # started.
            self.hotkeys.cancel_lock()
            return
        # Shown before anything else so the press always has instant visual
        # feedback, even when opening the audio device is slow (worst on a
        # cold first press, since PortAudio has not touched the driver yet).
        # ``_dictation_active`` also has to flip here, not after mic.start()
        # succeeds: engine LOADING (e.g. a first-run model download) can fire
        # in between and would otherwise steal the bar back to "loading"
        # before the listening state ever became visible.
        self._dictation_active = True
        self.bar.set_state("listening")
        self.meter.reset()
        try:
            self.mic.start()
        except Exception as exc:
            self._dictation_active = False
            self.hotkeys.cancel_lock()
            self.bar.set_state("error", "No microphone")
            print(f"[dictate] mic error: {exc}")
            return
        # After the stream is open, so a machine with a slow audio device
        # cannot delay capture, and never before it, so a failed mic is
        # silent rather than chiming and then showing an error.
        self.cues.play("start")
        self._start_cue_at = time.monotonic()
        # Capture is already live. Warming an unloaded model now overlaps its
        # cold-start cost with the user's speech instead of making them wait
        # after release. ``preload`` is asynchronous and ``ensure_loaded``
        # deduplicates a model that is already resident.
        if self.engine.state != engine_mod.READY:
            self._ptt_preload_pending = True
            self.engine.preload()
        self.meter_timer.start()

    def _on_talk_locked(self) -> None:
        """A tap locked the recording on. The bar staying up is the signal."""
        if not self._dictation_active:
            # The microphone never opened, so there is nothing to keep running.
            self.hotkeys.cancel_lock()
            return
        self.lock_limit_timer.start()
        # A tap is by definition faster than MIN_HOLD_SECONDS, so without this
        # the lock cue's A5 starts while the start cue's own closing A5 (same
        # pitch, on purpose -- see sounds.py) is still decaying, and the two
        # audibly phase against each other instead of reading as one settled
        # tone. Wait out whatever's left of the start cue first.
        remaining = sounds_mod.cue_seconds("start") - (time.monotonic() - self._start_cue_at)
        QTimer.singleShot(max(0, int(remaining * 1000)), self._play_lock_cue)
        self.bar.set_clickable(True)
        print("[dictate] recording locked on — press the talk key again to finish")

    def _play_lock_cue(self) -> None:
        # The delay above means the lock could already be over by the time
        # this fires (a very fast finish-tap right after locking); playing
        # the cue for a recording that has already ended would be confusing.
        if self.hotkeys.is_locked():
            self.cues.play("lock")

    def _on_bar_clicked(self) -> None:
        """Clicking the bar while it is armed finishes a locked recording,
        the mouse equivalent of pressing the talk key again."""
        if self.hotkeys.is_locked():
            self.hotkeys.release_lock()

    def _on_lock_limit(self) -> None:
        print(f"[dictate] locked recording hit {MAX_LOCKED_SECONDS}s — finishing it")
        self.hotkeys.release_lock()

    def _cancel_listening(self) -> None:
        """Throw away a locked recording without transcribing it."""
        self.lock_limit_timer.stop()
        self.meter_timer.stop()
        self.mic.stop()
        self._dictation_active = False
        self.bar.set_clickable(False)
        self.bar.set_state("idle")
        print("[dictate] recording cancelled")

    def _stop_listening(self, duration: float) -> None:
        if self._busy:
            # _start_listening declined this press because the previous
            # dictation is still being transcribed and pasted, so there is no
            # capture to end here. Returning early leaves that work's own
            # "transcribing" bar state alone -- falling through reset the bar
            # to idle underneath a dictation that was still running.
            return
        self.lock_limit_timer.stop()
        self.meter_timer.stop()
        self.bar.set_clickable(False)
        clip = self.mic.stop()
        if duration < hotkeys_mod.MIN_HOLD_SECONDS or clip.size == 0:
            self._dictation_active = False
            self.bar.set_state("idle")
            return
        if audio_mod.rms_level(clip) < audio_mod.SILENCE_RMS_THRESHOLD:
            self._dictation_active = False
            self.bar.set_state("error", "No audio detected — check your microphone")
            return
        self._busy = True
        # Only once there is really something to transcribe: a tap too short
        # to count should not answer with the "working" cue.
        self.cues.play("stop")
        self.bar.set_state("transcribing")
        threading.Thread(target=self._work, args=(clip,), daemon=True).start()

    def _work(self, clip) -> None:
        """Transcribe and paste. Runs off the UI thread; both steps block."""
        try:
            text = self.engine.transcribe(clip)
        except Exception as exc:
            print(f"[dictate] transcribe error: {exc}")
            self.bridge.finished.emit("error", str(exc)[:40])
            return
        if not text:
            print("[dictate] heard: (nothing)")
            self.bridge.finished.emit("empty", "")
            return
        # Debug mode reports useful activity without placing the user's
        # dictated words in a console log.
        print(f"[dictate] heard text ({len(text)} characters)")

        # Dictate always pastes and always leaves one separating space. These
        # are app behavior, not decisions a user should have to configure.
        payload = text + " "
        # Read the focused window before pasting, not after: the paste itself
        # cannot move focus, but anything the user does next can, and this is
        # the identity the undo offer is anchored to.
        target = inject.foreground_window()
        # Kept for the tray's "Copy last dictation" before any insertion is
        # attempted. Whatever happens to the paste, the words the user just
        # spoke must not be the thing that gets lost.
        self.bridge.dictated.emit(text)
        try:
            # Stop our own listener from seeing the synthetic Ctrl+V, which
            # would otherwise leave 'ctrl' stuck in the pressed-key set.
            self.hotkeys.suppress(True)
            try:
                inject.send(payload, "paste")
            except inject.ClipboardUnavailable as exc:
                # The clipboard holds something Dictate cannot put back, or
                # another program is holding it open. Typing touches no
                # clipboard at all, so it always works here -- slower, but the
                # dictation still lands where the user was working.
                print(f"[dictate] pasting unavailable ({exc}); typing instead")
                inject.send(payload, "type")
        except Exception as exc:
            print(f"[dictate] inject error: {exc}")
            self.bridge.finished.emit("error", str(exc)[:40])
            return
        finally:
            self.hotkeys.suppress(False)

        preview = text if len(text) <= 18 else text[:17] + "…"
        if target is not None:
            self.bridge.pasted_into.emit(target)
        self.bridge.finished.emit("done", preview)

    def _on_finished(self, state: str, detail: str) -> None:
        self._busy = False
        self._dictation_active = False
        self.bar.set_state(state, detail)
        if self.settings_window:
            self.settings_window.refresh_status()

    def _pump_meter(self) -> None:
        self.bar.set_levels(self.meter.update(self.mic.latest_window()))

    # --- settings ---

    def _show_first_run(self) -> None:
        dialog = FirstRunDialog(self.settings)
        if not dialog.exec():
            return
        completed = replace(
            self.settings,
            input_device=dialog.input_device,
            onboarding_complete=True,
        ).clamped()
        config.save(completed)
        updater_mod.mark_whats_new_seen(VERSION)
        self._apply_settings(completed)
        # A fresh install should begin the default model download right after
        # setup, not make the first actual dictation discover it. The engine
        # works on its own thread and reports real byte progress to both the
        # Settings page and the activity-bar notification.
        self._reload_pending = True
        self._show_settings()
        self.engine.preload()

    def _on_engine_state(
        self, state: str, detail: str, progress: float | None = None
    ) -> None:
        # Only model loading and failures belong on the bar; the dictation flow
        # owns the listening/transcribing/done states itself. READY only goes
        # to the bar when it's the tail end of a Settings-triggered reload --
        # every ordinary transcription also passes through READY, and that
        # traffic already has its own bar state (done/empty/error) a moment
        # later via _on_finished.
        if state == engine_mod.LOADING:
            # A PTT-triggered warm-up happens while the mic is already
            # recording. The listening waveform is the honest status then;
            # replacing it with a loading indicator would imply capture paused.
            # A real first-time model download is more important than the
            # listening waveform: it explains why transcription cannot finish
            # yet, and its live percentage is the feedback a fresh user needs.
            if progress is not None:
                if self._dictation_active:
                    percent = f" {int(progress * 100)}%" if progress is not None else ""
                    detail = f"Recording continues — speech model downloading{percent}"
                self.bar.set_state("loading", detail, progress)
            elif not (
                self._dictation_active or self._ptt_preload_pending
            ):
                self.bar.set_state("loading", detail, progress)
        elif state == engine_mod.READY:
            if self._ptt_preload_pending:
                self._ptt_preload_pending = False
            elif self._reload_pending:
                self._reload_pending = False
                self.bar.set_state("loaded", detail)
        elif state == engine_mod.ERROR:
            self._ptt_preload_pending = False
            if not self._dictation_active:
                self._reload_pending = False
                self.bar.set_state("error", detail[:40])
        if self.settings_window:
            self.settings_window.refresh_status()

    def _on_gpu_status(self, downloading: bool, progress: float | None) -> None:
        """A background GPU-runtime download reported progress.

        Shown on the bar itself (Thomas's own ask) via the same loading
        sweep/fill the model-loading flow already uses, labeled so it never
        reads as a stuck dictation -- a real dictation always wins, same
        guard `_on_engine_state` already uses for its own LOADING branch.
        Also mirrored to Settings' own dedicated GPU row when that window
        is open.
        """
        if self.settings_window:
            self.settings_window.refresh_status()
        if self._dictation_active or self._ptt_preload_pending:
            return
        if downloading:
            self._gpu_download_showing = True
            pct = f" {int(progress * 100)}%" if progress is not None else ""
            self.bar.set_state("loading", f"Downloading GPU acceleration…{pct}", progress)
        elif self._gpu_download_showing:
            self._gpu_download_showing = False
            self.bar.set_state("loaded", "GPU acceleration ready")

    def _show_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(
                self.settings,
                self.engine,
                self.updater,
                VERSION,
                self._whats_new_notes,
            )
            self.settings_window.changed.connect(self._apply_settings)
            self.settings_window.capture_active.connect(self.hotkeys.set_capture_active)
            self.settings_window.margin_preview.connect(self.bar.preview_margin)
            self.settings_window.width_preview.connect(self.bar.preview_width)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _show_update_complete(self) -> None:
        # Keep Settings as the stable parent surface. The release notes are a
        # focused modal on top, and the same window remains available later
        # from the rail's permanent What's new button.
        self._show_settings()
        dark = resolve_dark(self.settings.theme_mode, self.theme_watcher.dark)
        dialog = UpdateCompleteDialog(
            VERSION,
            self._whats_new_notes,
            self.settings_window,
            dark=dark,
        )
        dialog.presented.connect(_signal_updated_window_ready)
        dialog.presented.connect(self._mark_current_version_seen)
        dialog.exec()

    def _mark_current_version_seen(self) -> None:
        updater_mod.mark_whats_new_seen(VERSION)

    # --- open/already-running notices ---

    def _notify(
        self, text: str, tone: str = "info", on_click=None, duration_ms: int = 5000
    ) -> None:
        """Show Dictate's own bar toast, and mirror it to a system tray
        balloon too when the user has opted into that in Settings.

        The tray balloon is never itself clickable -- ``on_click`` only
        wires the bar toast's own click gesture -- so an actionable
        notification (an update available) still has exactly one place to
        click it regardless of whether system notifications are also on.
        """
        self.bar.notify(text, tone=tone, on_click=on_click, duration_ms=duration_ms)
        if self.settings.system_notifications_enabled:
            icon = QSystemTrayIcon.Warning if tone == "error" else QSystemTrayIcon.Information
            self.tray.showMessage(APP_NAME, text, icon, duration_ms)

    def _show_ready_notice(self) -> None:
        key = hotkeys_mod.format_combo(self.settings.ptt_key)
        self._notify(f"Ready. Hold {key} to talk.", tone="success")

    def _check_running_notice(self) -> None:
        """Poll the named event a second launch attempt sets.

        WaitForSingleObject with a 0ms timeout is a non-blocking check, not
        a wait -- this timer fires every 500ms regardless of whether the
        event is signalled, so a missed check is never more than one tick
        late.
        """
        handle = getattr(self, "_running_notice_handle", None)
        if handle is None or _kernel32 is None:
            return
        if _kernel32.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0:
            return
        _kernel32.ResetEvent(handle)
        self._notify("Dictate is already open.", tone="info")

    def _apply_settings(self, settings: config.Settings) -> None:
        old = self.settings
        if (settings.model_size, settings.device) != (old.model_size, old.device):
            self._reload_pending = True
        self.settings = settings
        self._apply_appearance()
        if settings.auto_update_enabled != old.auto_update_enabled:
            self.updater.set_enabled(settings.auto_update_enabled)
            self.act_check_update.setEnabled(settings.auto_update_enabled)
        if settings.settings_hotkey != old.settings_hotkey:
            self.act_settings.setText(
                f"Settings\t{hotkeys_mod.format_combo(settings.settings_hotkey)}"
            )
        self.mic.set_device(settings.input_device)
        self.engine.update_settings(settings)
        self.hotkeys.update_settings(settings)
        if not settings.tap_to_lock:
            # Turning the gesture off must also release a lock already running,
            # or it would keep recording with no supported way to end it.
            self.hotkeys.cancel_lock()
        self.bar.update_settings(settings)
        self.cues.update_settings(settings)
        self.tray.setToolTip(_tray_tip(settings))

    # --- undoing a paste ---

    def _offer_undo(self, handle: int) -> None:
        """Arm "Undo last dictation" for the window the text just landed in."""
        self._undo_target = handle
        self._undo_at = time.monotonic()
        self.hotkeys.watch_window(handle, inject.foreground_window)
        self.act_undo.setEnabled(True)
        self.undo_expiry_timer.start()

    def _withdraw_undo(self) -> None:
        self._undo_target = None
        self.hotkeys.stop_watching()
        self.undo_expiry_timer.stop()
        self.act_undo.setEnabled(False)

    def _undo_refusal(self) -> str:
        """Why undo must not fire, or an empty string when it is safe.

        Every branch here answers the same question: is Dictate's paste still
        the newest thing in that window's undo history? It cannot ask the app
        directly, so it refuses on any evidence to the contrary rather than
        guessing. A refused undo costs one re-selection; a wrong one destroys
        work Dictate did not create.
        """
        if self._undo_target is None:
            return "Nothing to undo"
        if time.monotonic() - self._undo_at > UNDO_WINDOW_SECONDS:
            return "Undo window expired"
        if self.hotkeys.watched_hits():
            return "You changed that app"
        if not inject.window_is_alive(self._undo_target):
            return "That app is closed"
        return ""

    def _undo_last_paste(self) -> None:
        refusal = self._undo_refusal()
        if refusal:
            print(f"[dictate] not undoing — {refusal}")
            self._notify(f"Can't undo: {refusal}.", tone="info")
            self._withdraw_undo()
            return

        target = self._undo_target
        # One use only. Withdrawing first means a failure part-way through
        # cannot leave a stale offer pointing at a window that has moved on.
        self._withdraw_undo()
        # Off the UI thread: undo_in() refocuses the target window and then
        # sleeps out Windows' asynchronous focus handover, which is exactly
        # the blocking inject.py's module docstring says must never happen on
        # this thread. Done here it froze the bar mid-animation for the whole
        # settle, on the one action whose feedback is a bar notification.
        threading.Thread(target=self._run_undo, args=(target,), daemon=True).start()

    def _run_undo(self, target: int) -> None:
        """Refocus the target window and send its undo. Worker thread only."""
        self.hotkeys.suppress(True)
        try:
            sent = inject.undo_in(target)
        except Exception as exc:
            print(f"[dictate] undo error: {exc}")
            sent = False
        finally:
            self.hotkeys.suppress(False)

        if sent:
            print("[dictate] undid the last dictation")
        else:
            print("[dictate] not undoing — could not focus that window again")
        self.bridge.undo_finished.emit(sent)

    def _on_undo_finished(self, sent: bool) -> None:
        if not sent:
            self._notify("Can't undo: the app didn't respond.")

    def _remember_last_dictation(self, text: str) -> None:
        """Keep one result in RAM for recovery without creating a transcript log."""
        self._last_dictation = text
        self.act_copy_last.setEnabled(True)

    def _copy_last_dictation(self) -> None:
        """Offer one short-lived clipboard recovery copy from the tray menu."""
        # Restarting the recovery window must preserve the *original*
        # clipboard, not treat Dictate's first temporary copy as the new
        # baseline. Restore it first when it is still ours; if the user copied
        # something else, the callback correctly leaves that newer item alone.
        if self._clipboard_restore_timer.isActive():
            self._clipboard_restore_timer.stop()
            self._restore_recovery_clipboard()
        restore = inject.copy_temporarily(self._last_dictation)
        if restore is None:
            # _notify, like every other outcome this menu can produce -- a raw
            # bar state skips the toast that actually carries the message.
            self._notify("Couldn't safely protect your clipboard.", tone="error")
            return
        self._clipboard_restore = restore
        self._clipboard_restore_timer.start(inject.TEMPORARY_COPY_SECONDS * 1000)
        self._notify(
            "Last dictation copied.",
            tone="info",
            duration_ms=inject.TEMPORARY_COPY_SECONDS * 1000,
        )

    def _restore_recovery_clipboard(self) -> None:
        if self._clipboard_restore is not None:
            self._clipboard_restore()
            self._clipboard_restore = None

    # --- updates ---

    def _on_update_available(self, version: str, notes: str) -> None:
        # No auto-download: this notification is the only thing that ever
        # happens on its own. Clicking it is the one action that starts the
        # download. Installation remains a separate, explicit restart action
        # after the downloaded installer has been verified.
        self._notify(
            f"Update {version} available. Click to download.",
            tone="info",
            on_click=self._start_update,
        )

    def _check_for_updates(self) -> None:
        """Tray and console entry point for a manual check.

        Always answers. ``check_now`` legitimately declines when an update is
        already found or already downloaded -- but a menu item that does
        nothing at all reads as broken, so the state that made it decline is
        what gets shown instead.
        """
        if not self.settings.auto_update_enabled:
            self._notify("Update checks are turned off in Settings.", tone="info")
            return
        state, _detail, _progress = self.updater.last_status
        if state == updater_mod.READY_TO_RESTART:
            self._notify(
                "Update verified. Click to restart and finish.",
                tone="success",
                on_click=self._restart_update,
                duration_ms=8000,
            )
            return
        if self.updater.check_now(silent=False):
            self._notify("Checking for updates…", tone="info")

    def _start_update(self) -> None:
        if self.updater.start_update():
            self._notify("Downloading update…", tone="info")

    def _restart_update(self) -> None:
        if self.updater.restart_to_install():
            self._notify("Verifying update before restart…", tone="info")

    def _prepare_update_splash(self) -> None:
        """Stage the progress helper before Setup can touch its install dir."""
        self._prepared_update_splash = None
        if not getattr(sys, "frozen", False):
            return
        installed_updater = Path(sys.executable).resolve().parent / "updater"
        self._prepared_update_splash = updater_mod.stage_update_splash(installed_updater)

    def _on_update_installing(self, version: str, installer_pid: int) -> None:
        # The installer is already launched and waiting for this process to
        # exit before it can overwrite these files -- nothing left to ask.
        # Hide every Dictate-owned window *before* the splash goes up, so
        # the two are never visible at the same time -- one closes before
        # the other opens, not both at once.
        self.bar.hide()
        if self.settings_window is not None:
            self.settings_window.hide()
        self._launch_update_splash(installer_pid)
        self._quit()

    def _launch_update_splash(self, installer_pid: int) -> None:
        """Best-effort: a native progress window bridging the silent-install
        gap (see update_splash.py). The splash waits for the named
        UPDATED_WINDOW_READY_EVENT, which the new app signals only after its
        update window has rendered. Only meaningful for a frozen build --
        dev mode has no dictate-updater.exe to launch -- and a failure here
        must never block the actual update, which is already verified and
        already launched by this point.
        """
        splash_exe = self._prepared_update_splash
        self._prepared_update_splash = None
        if splash_exe is None or not splash_exe.exists():
            return
        args = [str(splash_exe), "--installer-pid", str(installer_pid)]
        # The splash always runs from a temporary staged copy (see
        # stage_update_splash), so it can never work out {app} -- where the
        # real dictate.exe it may need to relaunch after a failed install
        # lives -- from its own exe path. Pass it explicitly.
        if getattr(sys, "frozen", False):
            args += ["--app-dir", str(Path(sys.executable).resolve().parent)]
        try:
            subprocess.Popen(args)
        except OSError as exc:
            print(f"[dictate] could not launch update splash: {exc}")

    def _on_update_error(self, message: str) -> None:
        self.bar.set_state("error", message[:60])
        self._notify("Update download failed.", tone="error")

    def _on_update_current(self) -> None:
        self._notify("You're up to date.", tone="success")

    def _on_update_status_changed(self) -> None:
        """Live update-check/download/install status, mirrored onto the bar
        (Thomas's own ask) as the same loading sweep/fill everything else
        uses. Only ever shows the *busy* states here -- CHECKING/
        DOWNLOADING/INSTALLING. Every terminal state already has its own
        dedicated callback that shows the right toast (_on_update_current,
        _on_update_error, _on_update_available), each wired to its own
        Updater callback and firing independently of this one; duplicating
        that here would just race the same bar state twice for one event.
        This only needs to start the busy indicator and know when to stop,
        not narrate how it ended. CHECKING never fires here for the silent
        24h background cadence -- updater.py itself only sets that status
        for a manual check (button, tray, console), so this never surfaces
        a background check nobody asked to see.
        """
        if self.settings_window:
            self.settings_window.refresh_status()
        if self._dictation_active or self._ptt_preload_pending:
            return
        state, detail, progress = self.updater.last_status
        busy = (
            updater_mod.CHECKING,
            updater_mod.DOWNLOADING,
            updater_mod.VERIFYING,
            updater_mod.INSTALLING,
        )
        if state in busy:
            self._update_ready_notified = False
            self._update_showing = True
            self.bar.set_state(
                "loading", detail, progress if state == updater_mod.DOWNLOADING else None
            )
        elif state == updater_mod.READY_TO_RESTART:
            self._update_showing = False
            if not getattr(self, "_update_ready_notified", False):
                self._update_ready_notified = True
                self._notify(
                    "Update verified. Click to restart and finish.",
                    tone="success",
                    on_click=self._restart_update,
                    duration_ms=8000,
                )
        elif self._update_showing:
            self._update_showing = False

    def _quit(self) -> None:
        self.hotkeys.stop()
        self.engine.shutdown()
        self.updater.shutdown()
        running_notice_handle = getattr(self, "_running_notice_handle", None)
        if running_notice_handle and _kernel32 is not None:
            _kernel32.CloseHandle(running_notice_handle)
            self._running_notice_handle = None
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        print(
            f"[dictate] ready — hold {hotkeys_mod.format_combo(self.settings.ptt_key)}, "
            f"{hotkeys_mod.format_combo(self.settings.settings_hotkey)} for settings"
        )
        print(
            "[dictate] type a command + Enter: reload model | load model | "
            "unload model | status | help"
        )
        return self.qt.exec()


def main() -> int:
    if already_running():
        _signal_running_notice()
        print("[dictate] already running")
        return 0
    return App().run()


if __name__ == "__main__":
    sys.exit(main())
