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
import sys
import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import audio as audio_mod
import bar as bar_mod
import config
import engine as engine_mod
import hotkeys as hotkeys_mod
import inject
import sounds as sounds_mod
import startup
import updater as updater_mod
from theme import ThemeWatcher
from bar import Bar
from settings_window import FirstRunDialog, SettingsWindow

APP_NAME = "Dictate"
MUTEX_NAME = "Global\\DictateSingleInstance"
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


class Bridge(QObject):
    """Signal-only object used to hop onto the UI thread from other threads."""

    talk_started = Signal()
    talk_ended = Signal(float)
    open_settings = Signal()
    engine_state = Signal(str, str, object)  # state, detail, progress (0..1 or None)
    finished = Signal(str, str)  # state, detail
    command = Signal(str)  # a line typed into the console, already normalized
    update_ready = Signal(str, object)  # version, installer Path
    update_current = Signal()  # a manual check found nothing newer


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
        self.bar = Bar(self.settings)
        self.theme_watcher.changed.connect(self._on_theme_changed)
        self.cues = sounds_mod.Cues(self.settings.sound_cues)
        self.settings_window: SettingsWindow | None = None
        self._busy = False
        self._dictation_active = False  # mic open or its captured audio is still processing
        self._ptt_preload_pending = False  # background warm-up started by a PTT press
        self._reload_pending = False  # set only by a Settings-triggered reload

        self.hotkeys = hotkeys_mod.Hotkeys(
            self.settings,
            on_talk_start=self.bridge.talk_started.emit,
            on_talk_end=self.bridge.talk_ended.emit,
            on_settings=self.bridge.open_settings.emit,
        )

        self.bridge.talk_started.connect(self._start_listening)
        self.bridge.talk_ended.connect(self._stop_listening)
        self.bridge.open_settings.connect(self._show_settings)
        self.bridge.engine_state.connect(self._on_engine_state)
        self.bridge.finished.connect(self._on_finished)
        self.bridge.command.connect(self._on_command)
        self.bridge.update_ready.connect(self._on_update_ready)
        self.bridge.update_current.connect(self._on_update_current)

        # Drives the waveform. Only runs while the mic is open.
        self.meter_timer = QTimer()
        self.meter_timer.setInterval(16)
        self.meter_timer.timeout.connect(self._pump_meter)

        self._build_tray()
        self.hotkeys.start()
        self._start_command_listener()
        self.updater = updater_mod.Updater(
            on_ready=self.bridge.update_ready.emit,
            on_up_to_date=self.bridge.update_current.emit,
        )

        if not self.settings.sleep_enabled:
            self.engine.preload()
        if self.settings.always_visible:
            self.bar.set_state("idle")
            self.bar.show_bar()
        if not self.settings.onboarding_complete:
            QTimer.singleShot(0, self._show_first_run)

    # --- tray ---

    def _on_theme_changed(self, dark: bool) -> None:
        self.bar.set_theme(dark)
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.set_theme(dark)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(
            f"{APP_NAME} — hold {hotkeys_mod.format_combo(self.settings.ptt_key)} to talk"
        )

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
        # The only balloon this app ever shows is an update notice, so any
        # click on it can safely mean "apply the update" with no ambiguity
        # about which notification was clicked.
        self.tray.messageClicked.connect(self._apply_pending_update)
        self.tray.show()

    # --- console commands ---

    def _start_command_listener(self) -> None:
        """Let commands be typed into the console this app was launched from.

        Only useful when a real console is attached (run-dictate.bat /
        -debug.bat); the hidden startup launch has no one to type into it, so
        the read just blocks forever on a daemon thread and does no harm.
        """
        try:
            if sys.stdin is None or not sys.stdin.readable():
                return
        except Exception:
            return
        threading.Thread(target=self._command_loop, daemon=True).start()

    def _command_loop(self) -> None:
        try:
            for line in sys.stdin:
                text = line.strip().lower()
                if text:
                    self.bridge.command.emit(text)
        except Exception:
            pass

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
            print(
                f"[dictate] state={self.engine.state} "
                f"device={self.engine.active_device or '(none)'}"
            )
        elif text in ("help", "?"):
            print(
                "[dictate] commands: reload model | load model | unload model | status | help"
            )
        else:
            print(f"[dictate] unknown command: {text!r} (try 'help')")

    # --- dictation flow ---

    def _start_listening(self) -> None:
        if self._busy:
            return  # still pasting the last one; ignore the new press
        self.meter.reset()
        try:
            self.mic.start()
        except Exception as exc:
            self.bar.set_state("error", "No microphone")
            print(f"[dictate] mic error: {exc}")
            return
        # After the stream is open, so a machine with a slow audio device
        # cannot delay capture, and never before it, so a failed mic is
        # silent rather than chiming and then showing an error.
        self.cues.play("start")
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

    def _stop_listening(self, duration: float) -> None:
        self.meter_timer.stop()
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
            self.settings_window = SettingsWindow(self.settings, self.engine)
            self.settings_window.changed.connect(self._apply_settings)
            self.settings_window.capture_active.connect(self.hotkeys.set_capture_active)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _apply_settings(self, settings: config.Settings) -> None:
        old = self.settings
        if (settings.model_size, settings.device) != (old.model_size, old.device):
            self._reload_pending = True
        self.settings = settings
        self.mic.set_device(settings.input_device)
        self.engine.update_settings(settings)
        self.hotkeys.update_settings(settings)
        self.bar.update_settings(settings)
        self.cues.update_settings(settings)
        self.tray.setToolTip(
            f"{APP_NAME} — hold {hotkeys_mod.format_combo(settings.ptt_key)} to talk"
        )

    # --- updates ---

    def _on_update_ready(self, version: str, installer_path) -> None:
        self.tray.showMessage(
            "Dictate update ready",
            f"Version {version} is ready to install. Click to restart and update.",
            QSystemTrayIcon.Information,
            10000,
        )

    def _on_update_current(self) -> None:
        self.tray.showMessage(
            "Dictate", "You're on the latest version.", QSystemTrayIcon.Information, 5000
        )

    def _apply_pending_update(self) -> None:
        if self.updater.apply_staged():
            self._quit()

    def _quit(self) -> None:
        self.hotkeys.stop()
        self.engine.shutdown()
        self.updater.shutdown()
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
        print("[dictate] already running")
        return 0
    return App().run()


if __name__ == "__main__":
    sys.exit(main())
