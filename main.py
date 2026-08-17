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
import sys
import threading
import time
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
import sounds as sounds_mod
import startup
import updater as updater_mod
from theme import ThemeWatcher
from bar import Bar
from settings_window import FirstRunDialog, SettingsWindow, UpdateCompleteDialog
from version import VERSION

APP_NAME = "Dictate"
MUTEX_NAME = "Global\\DictateSingleInstance"
# A second launch attempt sets this; the running instance polls it rather
# than running a message-pump listener for a second, unrelated purpose.
RUNNING_NOTICE_EVENT = "Global\\DictateShowRunningNotice"

# Longest a locked (tap-started) recording runs before Dictate stops it and
# transcribes what it has. A hold is bounded by the user's finger; a lock is
# not, so a forgotten one would otherwise hold the microphone open all day.
MAX_LOCKED_SECONDS = 5 * 60

# Whisper is an offline model, so "live" text is a throttled rolling preview:
# one bounded audio window at a time, never a queue of increasingly stale
# inference jobs.  The final release transcription still uses the full clip.
LIVE_PREVIEW_POLL_MS = 80
LIVE_PREVIEW_INTERVAL_MS = 450
LIVE_PREVIEW_PAUSE_SETTLE_MS = 160
LIVE_PREVIEW_PAUSE_MIN_GAP_MS = 180
LIVE_PREVIEW_WINDOW_SECONDS = 6.0
LIVE_PREVIEW_MIN_SECONDS = 0.45
ENHANCED_PREVIEW_SLOW_SECONDS = 0.9

# How long "Undo last dictation" stays on offer. Dictate cannot read another
# app's undo history, so it can never *prove* its paste is still the top of
# that stack -- it can only refuse whenever it has evidence otherwise. A short
# window is the last of those guards: the longer the offer sits there, the more
# chance the app's own state has moved on in some way no listener can see.
UNDO_WINDOW_SECONDS = 30

# (name, description) pairs for the debug console's `help` output. A plain
# list rather than a dict so the printed order matches the order below --
# roughly "look something up" first, then actions, then the fake-notification
# pair used only to eyeball the update UI without touching the network.
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
    ("update test", "simulate an update-available notification (no network, no download)"),
    ("update test current", "simulate an up-to-date notification"),
    ("quit", "exit Dictate"),
    ("help", "show this list (alias: ?)"),
]
# A frozen build's __file__ points inside the bundle, not a real sibling
# directory containing icon.ico. sys._MEIPASS is PyInstaller's own answer to
# "where did you put my data files" -- the onedir _internal folder here, a
# temp extraction dir for onefile -- and is the documented way to find them
# rather than guessing the layout ourselves.
if getattr(sys, "frozen", False):
    ICON_PATH = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "icon.ico"
else:
    ICON_PATH = Path(__file__).resolve().parent / "icon.ico"


def already_running() -> bool:
    """True when another copy holds the named mutex.

    Two instances would both grab the hotkey and both paste, so the second one
    exits rather than fighting the first.
    """
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return handle != 0 and ctypes.windll.kernel32.GetLastError() == 183
    except Exception:
        return False


def _signal_running_notice() -> None:
    """Wake the already-running instance's "still open" bar notification.

    CreateEventW is idempotent -- this opens the primary instance's own
    named event rather than needing its window handle -- so a second launch
    attempt can raise the flag with no IPC channel of its own beyond the
    name both processes already agree on.
    """
    try:
        handle = ctypes.windll.kernel32.CreateEventW(None, True, False, RUNNING_NOTICE_EVENT)
        if handle:
            ctypes.windll.kernel32.SetEvent(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


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


def preview_hardware() -> tuple[int, float, bool]:
    """Return logical CPU threads, installed RAM in GiB, and a cautious limit flag."""
    threads = os.cpu_count() or 1
    ram_gib = 0.0
    try:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram_gib = status.total_physical / (1024 ** 3)
    except (AttributeError, OSError):
        pass
    limited = threads < 8 or (ram_gib > 0 and ram_gib < 12.0)
    return threads, ram_gib, limited


class Bridge(QObject):
    """Signal-only object used to hop onto the UI thread from other threads."""

    talk_started = Signal()
    talk_ended = Signal(float)
    talk_locked = Signal()  # a tap turned the hold into a locked recording
    talk_cancelled = Signal()  # a locked recording was abandoned, audio discarded
    open_settings = Signal()
    engine_state = Signal(str, str, object)  # state, detail, progress (0..1 or None)
    finished = Signal(str, str)  # state, detail
    command = Signal(str)  # a line typed into the console, already normalized
    update_available = Signal(str, str)  # version, release notes -- nothing downloaded yet
    update_installing = Signal(str)  # version -- installer launched, app should quit now
    update_error = Signal(str)  # a start_update() download/verify/launch failure
    update_current = Signal()  # a manual check found nothing newer
    update_status_changed = Signal()  # live status/progress text changed
    dictated = Signal(str)  # successfully inserted text, kept only in memory
    pasted_into = Signal(int)  # window handle the text landed in, for undo
    # generation, rolling text, measured inference seconds (-1 on failure),
    # and whether the dedicated Enhanced preview model handled the request.
    live_preview = Signal(int, str, float, bool)


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
            self.settings, on_state=self.bridge.engine_state.emit
        )
        self.preview_engine = engine_mod.PreviewEngine(self.settings)
        self.bar = Bar(self.settings)
        self.theme_watcher.changed.connect(self._on_theme_changed)
        self.cues = sounds_mod.Cues(self.settings.sound_cues)
        self.settings_window: SettingsWindow | None = None
        self._busy = False
        self._dictation_active = False  # mic open or its captured audio is still processing
        self._ptt_preload_pending = False  # background warm-up started by a PTT press
        self._start_cue_at = 0.0  # when the "start" cue began, so "lock" can wait it out
        self._reload_pending = False  # set only by a Settings-triggered reload
        self._last_dictation = ""
        self._undo_target = None  # window the last paste landed in
        self._undo_at = 0.0
        self._preview_generation = 0
        self._preview_running = False
        self._preview_last_request_at = 0.0
        self._preview_last_voice_at = 0.0
        self._preview_was_speaking = False
        self._enhanced_benchmark_pending = False
        self._clipboard_restore = None
        self._clipboard_restore_timer = QTimer()
        self._clipboard_restore_timer.setSingleShot(True)
        self._clipboard_restore_timer.timeout.connect(self._restore_recovery_clipboard)
        self._update_notice = (
            updater_mod.consume_update_notice(VERSION) if "--updated" in sys.argv else None
        )
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
            self._running_notice_handle = ctypes.windll.kernel32.CreateEventW(
                None, True, False, RUNNING_NOTICE_EVENT
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
        self.bridge.finished.connect(self._on_finished)
        self.bridge.command.connect(self._on_command)
        self.bridge.update_available.connect(self._on_update_available)
        self.bridge.update_installing.connect(self._on_update_installing)
        self.bridge.update_error.connect(self._on_update_error)
        self.bridge.update_current.connect(self._on_update_current)
        self.bridge.update_status_changed.connect(self._on_update_status_changed)
        self.bridge.dictated.connect(self._remember_last_dictation)
        self.bridge.pasted_into.connect(self._offer_undo)
        self.bridge.live_preview.connect(self._on_live_preview)
        self.bar.clicked.connect(self._on_bar_clicked)

        # Drives the waveform. Only runs while the mic is open.
        self.meter_timer = QTimer()
        self.meter_timer.setInterval(16)
        self.meter_timer.timeout.connect(self._pump_meter)

        self.live_preview_timer = QTimer()
        self.live_preview_timer.setInterval(LIVE_PREVIEW_POLL_MS)
        self.live_preview_timer.timeout.connect(self._request_live_preview)

        self._build_tray()
        self.hotkeys.start()
        self._start_command_listener()
        self.updater = updater_mod.Updater(
            on_available=self.bridge.update_available.emit,
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
        elif self._update_notice is not None:
            QTimer.singleShot(300, self._show_update_complete)
        else:
            # Skipped on first run (the welcome dialog already greets them)
            # and on a just-updated restart (the What's New dialog already
            # does) -- this is only the plain "an ordinary launch finished"
            # case, which otherwise has no visible confirmation at all since
            # Dictate opens straight to the tray with no window.
            QTimer.singleShot(500, self._show_ready_notice)

    # --- tray ---

    def _on_theme_changed(self, dark: bool) -> None:
        self.bar.set_theme(dark)
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.set_theme(dark)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(_tray_tip(self.settings))

        menu = QMenu()
        act_settings = QAction(f"Settings\t{self.settings.settings_hotkey}", menu)
        act_settings.triggered.connect(self._show_settings)
        menu.addAction(act_settings)
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

        act_check_update = QAction("Check for updates", menu)
        act_check_update.triggered.connect(lambda: self.updater.check_now(silent=False))
        menu.addAction(act_check_update)
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
            ctypes.windll.kernel32.SetConsoleTitleW(f"{APP_NAME} — debug console")
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
            else:
                print("[dictate] checking GitHub for a new release…")
                self.updater.check_now(silent=False)
        elif text in ("open data", "open settings folder", "open folder"):
            try:
                os.startfile(config.CONFIG_DIR)
            except OSError as exc:
                print(f"[dictate] could not open {config.CONFIG_DIR}: {exc}")
        elif text == "update test":
            # Fakes a "found a newer version" result without touching the
            # network or waiting on the real 24h check cadence, so the
            # available-toast (text, colour, click-to-download) can be
            # eyeballed on demand. Nothing is actually recorded as available
            # in the real Updater, so clicking the toast finds start_update()
            # has nothing to do and no-ops instead of trying to download a
            # nonexistent release.
            print("[dictate] simulating an update-available notification")
            self.bridge.update_available.emit("9.9.9-test", "Simulated release notes.")
        elif text == "update test current":
            print("[dictate] simulating an up-to-date notification")
            self.bridge.update_current.emit()
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
        self.meter.reset()
        try:
            self.mic.start()
        except Exception as exc:
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
        self._dictation_active = True
        if self.engine.state != engine_mod.READY:
            self._ptt_preload_pending = True
            self.engine.preload()
        self.bar.set_state("listening")
        self.meter_timer.start()
        self._preview_generation += 1
        now = time.perf_counter()
        self._preview_last_request_at = now
        self._preview_last_voice_at = now
        self._preview_was_speaking = False
        if self.settings.live_preview_enabled:
            self.live_preview_timer.start()

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
        self.live_preview_timer.stop()
        self._preview_generation += 1
        self.mic.stop()
        self._dictation_active = False
        self.bar.set_clickable(False)
        self.bar.set_state("idle")
        print("[dictate] recording cancelled")

    def _stop_listening(self, duration: float) -> None:
        self.lock_limit_timer.stop()
        self.meter_timer.stop()
        self.live_preview_timer.stop()
        self._preview_generation += 1
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
        try:
            # Stop our own listener from seeing the synthetic Ctrl+V, which
            # would otherwise leave 'ctrl' stuck in the pressed-key set.
            self.hotkeys.suppress(True)
            inject.send(payload, "paste")
        except Exception as exc:
            print(f"[dictate] inject error: {exc}")
            self.bridge.finished.emit("error", str(exc)[:40])
            return
        finally:
            self.hotkeys.suppress(False)

        preview = text if len(text) <= 18 else text[:17] + "…"
        self.bridge.dictated.emit(text)
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

    def _request_live_preview(self) -> None:
        """Start one coalesced preview, favoring natural pauses in speech."""
        if (
            not self.settings.live_preview_enabled
            or self._preview_running
            or not self._dictation_active
        ):
            return

        now = time.perf_counter()
        latest = self.mic.latest_window()
        voice_now = (
            latest.size >= 32
            and audio_mod.rms_level(latest)
            >= audio_mod.SILENCE_RMS_THRESHOLD * 1.15
        )
        if voice_now:
            self._preview_last_voice_at = now
            self._preview_was_speaking = True
        pause_edge = (
            self._preview_was_speaking
            and (now - self._preview_last_voice_at) * 1000
            >= LIVE_PREVIEW_PAUSE_SETTLE_MS
        )
        since_last_ms = (now - self._preview_last_request_at) * 1000
        if pause_edge:
            self._preview_was_speaking = False
        if since_last_ms < LIVE_PREVIEW_INTERVAL_MS and not (
            pause_edge and since_last_ms >= LIVE_PREVIEW_PAUSE_MIN_GAP_MS
        ):
            return

        clip = self.mic.snapshot(LIVE_PREVIEW_WINDOW_SECONDS)
        if clip.size < int(audio_mod.SAMPLE_RATE * LIVE_PREVIEW_MIN_SECONDS):
            return
        if audio_mod.rms_level(clip) < audio_mod.SILENCE_RMS_THRESHOLD:
            return
        generation = self._preview_generation
        self._preview_running = True
        self._preview_last_request_at = now
        enhanced = self.settings.enhanced_preview_enabled

        def work() -> None:
            measured = 0.0
            try:
                if enhanced:
                    text, measured = self.preview_engine.transcribe(clip)
                else:
                    text = self.engine.transcribe_preview(clip)
            except Exception as exc:
                # Preview is decorative and must never break the dependable
                # release-to-paste path.  The final pass will report a real
                # transcription failure through its existing error state.
                print(f"[dictate] live preview skipped: {exc}")
                text = ""
                measured = -1.0
            self.bridge.live_preview.emit(generation, text, measured, enhanced)

        threading.Thread(target=work, daemon=True).start()

    def _on_live_preview(
        self, generation: int, text: str, measured: float = 0.0, enhanced: bool = False
    ) -> None:
        self._preview_running = False
        if enhanced and self._enhanced_benchmark_pending:
            self._enhanced_benchmark_pending = False
            if measured < 0:
                self._notify("Enhanced preview couldn't start.")
            elif measured > ENHANCED_PREVIEW_SLOW_SECONDS:
                self._notify("Enhanced preview may be slow.")
            else:
                self._notify("Enhanced preview is ready.", tone="success")
        if generation != self._preview_generation or not self._dictation_active:
            return
        if text:
            self.bar.set_live_text(text)

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
        self._apply_settings(completed)

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
            if not (self._dictation_active or self._ptt_preload_pending):
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

    def _show_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.settings, self.engine, self.updater)
            self.settings_window.changed.connect(self._apply_settings)
            self.settings_window.capture_active.connect(self.hotkeys.set_capture_active)
            self.settings_window.margin_preview.connect(self.bar.preview_margin)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _show_update_complete(self) -> None:
        UpdateCompleteDialog(VERSION, self._update_notice or "").exec()

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
        if handle is None:
            return
        if ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != 0:
            return
        ctypes.windll.kernel32.ResetEvent(handle)
        self._notify("Dictate is already open.", tone="info")

    def _apply_settings(self, settings: config.Settings) -> None:
        old = self.settings
        if (settings.model_size, settings.device) != (old.model_size, old.device):
            self._reload_pending = True
        self.settings = settings
        if settings.auto_update_enabled != old.auto_update_enabled:
            self.updater.set_enabled(settings.auto_update_enabled)
        if settings.live_preview_enabled != old.live_preview_enabled:
            self._preview_generation += 1
            if settings.live_preview_enabled and self._dictation_active:
                self.live_preview_timer.start()
            else:
                self.live_preview_timer.stop()
        enhanced_turned_on = (
            settings.enhanced_preview_enabled
            and not old.enhanced_preview_enabled
        )
        if enhanced_turned_on:
            self._enhanced_benchmark_pending = True
            _threads, _ram_gib, limited = preview_hardware()
            self._notify(
                "Enhanced preview may be slow." if limited else "Enhanced preview is on."
            )
        elif old.enhanced_preview_enabled and not settings.enhanced_preview_enabled:
            self._enhanced_benchmark_pending = False
            preview_engine = getattr(self, "preview_engine", None)
            if preview_engine is not None:
                threading.Thread(target=preview_engine.unload, daemon=True).start()
        self.mic.set_device(settings.input_device)
        self.engine.update_settings(settings)
        preview_engine = getattr(self, "preview_engine", None)
        if preview_engine is not None:
            preview_engine.update_settings(settings)
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
            self.bar.set_state("error", "Couldn't safely protect your clipboard")
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
        # download, and that same click also carries through to installing
        # once the download verifies -- see _start_update() and
        # updater.Updater.start_update()'s own docstring.
        self._notify(
            f"Update {version} ready. Click to install.",
            tone="info",
            on_click=self._start_update,
        )

    def _start_update(self) -> None:
        if self.updater.start_update():
            self._notify("Downloading update…", tone="info")

    def _on_update_installing(self, version: str) -> None:
        # The installer is already launched and waiting for this process to
        # exit before it can overwrite these files -- nothing left to ask.
        self._quit()

    def _on_update_error(self, message: str) -> None:
        self.bar.set_state("error", message[:60])
        self._notify("Update download failed.", tone="error")

    def _on_update_current(self) -> None:
        self._notify("You're up to date.", tone="success")

    def _on_update_status_changed(self) -> None:
        if self.settings_window:
            self.settings_window.refresh_status()

    def _quit(self) -> None:
        self.hotkeys.stop()
        self.engine.shutdown()
        preview_engine = getattr(self, "preview_engine", None)
        if preview_engine is not None:
            preview_engine.shutdown()
        self.updater.shutdown()
        running_notice_handle = getattr(self, "_running_notice_handle", None)
        if running_notice_handle:
            ctypes.windll.kernel32.CloseHandle(running_notice_handle)
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
