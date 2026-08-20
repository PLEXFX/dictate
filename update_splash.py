"""Native progress splash shown while Dictate's own process is gone.

The installer replaces Dictate's files silently (/VERYSILENT -- no Inno
Setup UI at all), so for roughly a minute nothing in the main app can stay
alive to show anything. This is a second, tiny program launched by
main.py's _on_update_installing() as its own detached process, right after
Dictate hides its own windows and right before Dictate quits, so the two
never appear on screen at the same time -- one closes before this opens.

Deliberately self-contained: no engine.py/bar.py/audio imports, so this
build's own PyInstaller Analysis (dictate.spec) stays small next to the
~350MB main app instead of duplicating faster-whisper/ctranslate2.

Usage: dictate-updater.exe --installer-pid <pid>
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import HRESULT, c_int, c_uint64
from ctypes.wintypes import BOOL, HWND
from pathlib import Path
from typing import Optional

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from theme import system_is_dark

APP_NAME = "Dictate"

# Same frozen-vs-dev resolution as main.py's ICON_PATH -- see that file's
# comment for why sys._MEIPASS is the real answer for a frozen build.
if getattr(sys, "frozen", False):
    ICON_PATH = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "icon.ico"
else:
    ICON_PATH = Path(__file__).resolve().parent / "icon.ico"


def _fluent_curve(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


# Same Windows point-to-point motion as bar.py: entrances decelerate, exits
# accelerate. Duplicated rather than imported to keep this build's own
# dependency graph free of bar.py's audio-meter imports.
FLUENT_DECELERATE = _fluent_curve(0.1, 0.9, 0.2, 1.0)
FLUENT_ACCELERATE = _fluent_curve(0.7, 0.0, 1.0, 0.5)
FADE_IN_MS = 200
FADE_OUT_MS = 150

POLL_INTERVAL_MS = 300
FAILURE_HOLD_SECONDS = 2.0
SAFETY_TIMEOUT_SECONDS = 90.0
# Must stay byte-for-byte identical to main.py's UPDATED_WINDOW_READY_EVENT --
# the two processes find each other by this name and nothing else.
UPDATE_READY_EVENT = "Local\\DictateUpdatedWindowReady"
_WAIT_OBJECT_0 = 0

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


# --- pure decision logic -----------------------------------------------
# Deliberately free of ctypes/Qt so it can be unit tested the same way the
# rest of this codebase's logic is (see tests.test_features).

WAITING = "waiting"
SUCCESS_GRACE = "success_grace"
RELAUNCH_AND_CLOSE = "relaunch_and_close"
CLOSE = "close"


def decide_next_action(
    *,
    exit_code: Optional[int],
    elapsed_seconds: float,
    updated_window_ready: bool,
) -> str:
    """The splash's whole state machine, as one pure function.

    ``exit_code`` is None while the installer is still running, or if its
    process handle could never be opened in the first place. A nonzero exit
    code is Setup's own failure/cancel contract and always wins immediately.
    A successful installer is deliberately *not* enough to close: Dictate
    signals ``updated_window_ready`` only after its update window is visible.
    That removes the blank gap that arose when merely seeing dictate.exe start
    was treated as proof that the replacement UI was on screen. The safety
    ceiling still wins over everything, so this can never wait forever.
    """
    if elapsed_seconds >= SAFETY_TIMEOUT_SECONDS:
        return CLOSE
    if exit_code is not None and exit_code != 0:
        return RELAUNCH_AND_CLOSE
    if updated_window_ready:
        return CLOSE
    return SUCCESS_GRACE if exit_code == 0 else WAITING


def relaunch_target_path(splash_exe_path: Path) -> Path:
    """Where the previous dictate.exe should be, given this splash's own
    installed location: {app}\\updater\\dictate-updater.exe -> {app}\\dictate.exe
    (see installer/dictate.iss's Files section for that layout).
    """
    return splash_exe_path.resolve().parent.parent / "dictate.exe"


# --- thin OS wrappers ----------------------------------------------------

_kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None


def _open_process(pid: int):
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return handle or None


def poll_exit_code(handle) -> Optional[int]:
    """None while still running; the process's real exit code once it isn't."""
    code = ctypes.c_ulong()
    if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return None
    if code.value == _STILL_ACTIVE:
        return None
    return code.value


def _create_update_ready_event():
    """Create/reset the handoff event before the installer can relaunch us."""
    if _kernel32 is None:
        return None
    try:
        handle = _kernel32.CreateEventW(None, True, False, UPDATE_READY_EVENT)
        if handle:
            _kernel32.ResetEvent(handle)
        return handle or None
    except Exception:
        return None


def _update_window_ready(handle) -> bool:
    """Non-blocking read of the freshly updated app's rendered-window signal."""
    if handle is None or _kernel32 is None:
        return False
    try:
        return _kernel32.WaitForSingleObject(handle, 0) == _WAIT_OBJECT_0
    except Exception:
        return False


# ITaskbarList3 (shobjidl.h) -- the interface behind the moving progress
# bar Explorer paints on a taskbar button during a real file copy. Neither
# pywin32 nor a typelib-generation step is used here: comtypes lets the
# vtable be declared by hand from the well-known CLSID/IID, which is the
# standard way to reach this specific interface from Python. Only the
# methods actually needed (through SetProgressState) are declared, but COM
# vtables are positional, so every earlier inherited method still has to
# be listed in its real order even though this code never calls them.
_CLSID_TASKBAR_LIST = GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}")
_IID_ITASKBAR_LIST3 = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")

TBPF_NOPROGRESS = 0x0
TBPF_INDETERMINATE = 0x1
TBPF_ERROR = 0x4


class _ITaskbarList3(IUnknown):
    _iid_ = _IID_ITASKBAR_LIST3
    _methods_ = [
        COMMETHOD([], HRESULT, "HrInit"),
        COMMETHOD([], HRESULT, "AddTab", (["in"], HWND, "hwnd")),
        COMMETHOD([], HRESULT, "DeleteTab", (["in"], HWND, "hwnd")),
        COMMETHOD([], HRESULT, "ActivateTab", (["in"], HWND, "hwnd")),
        COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], HWND, "hwnd")),
        COMMETHOD(
            [], HRESULT, "MarkFullscreenWindow",
            (["in"], HWND, "hwnd"), (["in"], BOOL, "fFullscreen"),
        ),
        COMMETHOD(
            [], HRESULT, "SetProgressValue",
            (["in"], HWND, "hwnd"), (["in"], c_uint64, "ullCompleted"), (["in"], c_uint64, "ullTotal"),
        ),
        COMMETHOD(
            [], HRESULT, "SetProgressState",
            (["in"], HWND, "hwnd"), (["in"], c_int, "tbpFlags"),
        ),
    ]


class _TaskbarProgress:
    """Best-effort ITaskbarList3 wrapper -- purely cosmetic, so any failure
    here (COM unavailable, a future Windows change, whatever) is swallowed
    rather than affecting the splash's actual job of watching the install.
    """

    def __init__(self, hwnd: int) -> None:
        self._hwnd = hwnd
        self._taskbar = None
        try:
            comtypes.CoInitialize()
            self._taskbar = comtypes.CoCreateInstance(
                _CLSID_TASKBAR_LIST,
                interface=_ITaskbarList3,
                clsctx=comtypes.CLSCTX_INPROC_SERVER,
            )
            self._taskbar.HrInit()
        except Exception as exc:
            print(f"[dictate-updater] taskbar progress unavailable: {exc}")
            self._taskbar = None

    def indeterminate(self) -> None:
        self._set_state(TBPF_INDETERMINATE)

    def error(self) -> None:
        self._set_state(TBPF_ERROR)

    def clear(self) -> None:
        self._set_state(TBPF_NOPROGRESS)

    def _set_state(self, flag: int) -> None:
        if self._taskbar is None:
            return
        try:
            self._taskbar.SetProgressState(self._hwnd, flag)
        except Exception:
            pass


# --- styling (duplicated from settings_window.py's nativeProgress/card
# rules rather than imported, for the same self-contained-build reason) ---


def _card_style(dark: bool) -> str:
    if dark:
        return "QFrame#card { background: #2B2B2B; border: 1px solid #3D3D3D; border-radius: 10px; }"
    return "QFrame#card { background: #FFFFFF; border: 1px solid #E0E0E0; border-radius: 10px; }"


def _text_style(dark: bool) -> str:
    color = "#FFFFFF" if dark else "#1A1A1A"
    return f"color: {color}; font-size: 10pt; font-weight: 600; background: transparent;"


def _progress_style(dark: bool) -> str:
    track = "rgba(255, 255, 255, 139)" if dark else "#DADADA"
    accent = "#4CC2FF" if dark else "#0078D4"
    return (
        f"QProgressBar#nativeProgress {{ background: {track}; border: none; "
        f"border-radius: 2px; max-height: 4px; min-height: 4px; }} "
        f"QProgressBar#nativeProgress::chunk {{ background: {accent}; border-radius: 2px; }}"
    )


class SplashWindow(QWidget):
    def __init__(self, installer_pid: int) -> None:
        super().__init__()
        self._installer_pid = installer_pid
        self._handle = _open_process(installer_pid)
        self._exit_code: Optional[int] = None
        self._start = time.monotonic()
        self._ready_event = _create_update_ready_event()
        self._done = False
        self._taskbar: Optional[_TaskbarProgress] = None

        dark = system_is_dark()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(APP_NAME)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setFixedSize(320, 118)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(_card_style(dark))
        outer.addWidget(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(18, 16, 18, 16)
        row.setSpacing(14)

        icon_label = QLabel()
        icon_label.setFixedSize(32, 32)
        if ICON_PATH.exists():
            icon_label.setPixmap(QIcon(str(ICON_PATH)).pixmap(32, 32))
        row.addWidget(icon_label, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(8)
        self._title = QLabel("Updating Dictate…")
        self._title.setStyleSheet(_text_style(dark))
        self._title.setWordWrap(True)
        col.addWidget(self._title)
        self._progress = QProgressBar()
        self._progress.setObjectName("nativeProgress")
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 0)
        self._progress.setStyleSheet(_progress_style(dark))
        col.addWidget(self._progress)
        row.addLayout(col, 1)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_finished_connected = False

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        print(f"[dictate-updater] watching installer pid {installer_pid}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._taskbar is None:
            self._taskbar = _TaskbarProgress(int(self.winId()))
            self._taskbar.indeterminate()
        self._fade_to(1.0, FADE_IN_MS, FLUENT_DECELERATE)
        self._timer.start()

    def _fade_to(self, value: float, duration: int, curve, on_finished=None) -> None:
        self._fade.stop()
        if self._fade_finished_connected:
            self._fade.finished.disconnect()
            self._fade_finished_connected = False
        self._fade.setDuration(duration)
        self._fade.setEasingCurve(curve)
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(value)
        if on_finished is not None:
            self._fade.finished.connect(on_finished)
            self._fade_finished_connected = True
        self._fade.start()

    def _tick(self) -> None:
        if self._done:
            return
        if self._handle is not None and self._exit_code is None:
            code = poll_exit_code(self._handle)
            if code is not None:
                self._exit_code = code
                print(f"[dictate-updater] installer exited with code {code}")
                if code == 0:
                    self._title.setText("Starting the updated Dictate…")

        elapsed = time.monotonic() - self._start

        action = decide_next_action(
            exit_code=self._exit_code,
            elapsed_seconds=elapsed,
            updated_window_ready=_update_window_ready(self._ready_event),
        )
        if action in (WAITING, SUCCESS_GRACE):
            return

        self._done = True
        self._timer.stop()
        if action == RELAUNCH_AND_CLOSE:
            self._handle_failure()
        else:
            if elapsed >= SAFETY_TIMEOUT_SECONDS:
                print("[dictate-updater] safety timeout reached -- closing")
            else:
                print("[dictate-updater] updated Dictate window is visible -- closing")
            self._start_fade_out()

    def _handle_failure(self) -> None:
        print("[dictate-updater] installer reported failure -- relaunching Dictate")
        if self._taskbar is not None:
            self._taskbar.error()
        self._title.setText("Update failed — reopening Dictate")
        target = relaunch_target_path(Path(sys.argv[0]))
        try:
            if target.exists():
                subprocess.Popen([str(target)])
            else:
                print(f"[dictate-updater] relaunch target missing: {target}")
        except OSError as exc:
            print(f"[dictate-updater] could not relaunch Dictate: {exc}")
        QTimer.singleShot(int(FAILURE_HOLD_SECONDS * 1000), self._start_fade_out)

    def _start_fade_out(self) -> None:
        if self._taskbar is not None:
            self._taskbar.clear()
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._ready_event is not None:
            _kernel32.CloseHandle(self._ready_event)
            self._ready_event = None
        self._fade_to(0.0, FADE_OUT_MS, FLUENT_ACCELERATE, on_finished=self.close)


def _parse_installer_pid(argv: list[str]) -> Optional[int]:
    if "--installer-pid" not in argv:
        return None
    idx = argv.index("--installer-pid")
    if idx + 1 >= len(argv):
        return None
    try:
        return int(argv[idx + 1])
    except ValueError:
        return None


def main() -> int:
    installer_pid = _parse_installer_pid(sys.argv[1:])
    if installer_pid is None:
        print("[dictate-updater] no --installer-pid given, exiting")
        return 1

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = SplashWindow(installer_pid)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
