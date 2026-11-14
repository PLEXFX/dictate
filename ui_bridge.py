"""A local link between the engine and the WinUI Settings window.

The engine stays in Python. The Settings window is a separate WinUI 3 program
(``ui\\DictateUI.exe``) that talks to it over a per-user named pipe, one JSON
object per line in each direction. Nothing here touches the network.

Engine to window:
  hello       once on connect: version, release notes, model folder, settings, status
  status      whenever the engine, GPU, model or updater state changes
  settings    when the engine's settings differ from what the window last saw
  devices     the answer to get_devices
  show_settings, quit, and the bar and tray messages listed in ui_shell.py

Window to engine:
  save_settings        the whole settings object; clamped here, applied like the old window did
  check_updates, start_update, restart_to_install, start_gpu_download
  tray_action {id}     a tray menu entry was chosen (settings, load, unload, copy_last, undo, check_update, quit)
  bar_clicked          the bar was clicked while it was armed
  capture              {"active": bool} stand the global hotkeys down while a key is recorded
  preview              {"width": px} or {"margin": px} move the real activity bar while a slider moves
  get_devices          list the microphones

``UiBridge`` is only message logic, so it is tested without Qt. ``PipeServer`` is
the thin QLocalServer wrapper that carries the lines, and ``launch_ui`` starts the
window. The pipe name carries a random suffix that only the launched window is told.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import config

POLL_MS = 250
MAX_LINE_BYTES = 1_000_000  # a settings object is about 1 KB; anything near this is not ours

_UI_DEV_PATH = Path("ui") / "bin" / "x64" / "Release" / "net10.0-windows10.0.19041.0" / "win-x64" / "DictateUI.exe"


@dataclass
class UiHost:
    """Everything the window may ask of the app, as plain callables, so tests can fake it."""

    version: str
    notes: str
    model_dir: Callable[[], str]
    get_settings: Callable[[], config.Settings]
    save_settings: Callable[[config.Settings], None]
    engine: Any
    updater: Any  # may be None
    cuda_available: Callable[[], bool]
    gpu_needs_download: Callable[[], bool]
    hardware: Callable[[], Any]
    input_devices: Callable[[], list]
    set_capture_active: Callable[[bool], None]
    preview_width: Callable[[int], None]
    preview_margin: Callable[[int], None]
    # When the window also owns the tray icon and the activity bar (ui_shell.py):
    shell_snapshot: Callable[[], dict] = lambda: {}   # tray and bar state for a newly connected window
    tray_action: Callable[[str], None] = lambda action_id: None
    bar_clicked: Callable[[], None] = lambda: None


def settings_from_dict(raw: object, current: config.Settings) -> Optional[config.Settings]:
    """Build settings from the window's object: known fields only, then clamped.

    Anything missing keeps the engine's value. ``onboarding_complete`` is the
    engine's own record of the first-run dialog, so the window cannot change it.
    """
    if not isinstance(raw, dict):
        return None
    known = config.Settings.__dataclass_fields__
    merged = asdict(current)
    for key, value in raw.items():
        if key in known and key != "onboarding_complete":
            merged[key] = value
    try:
        return config.Settings(**merged).clamped()
    except (TypeError, ValueError):
        return None


class UiBridge:
    def __init__(self, host: UiHost) -> None:
        self._host = host
        self._has_cuda: Optional[bool] = None
        self._hardware: Optional[dict] = None
        self._sent_status = ""
        self._sent_settings: Optional[dict] = None

    # --- what goes to the window ---

    def reset(self) -> None:
        """A new window connected: it has seen nothing yet."""
        self._sent_status = ""
        self._sent_settings = None

    def hello(self) -> dict:
        settings = asdict(self._host.get_settings())
        self._sent_settings = settings
        status = self.status()
        self._sent_status = _key(status)
        return {
            "type": "hello",
            "version": self._host.version,
            "notes": self._host.notes,
            "model_dir": self._host.model_dir(),
            "settings": settings,
            "status": status,
            "shell": self._host.shell_snapshot(),
        }

    def status(self) -> dict:
        engine = self._host.engine
        state, detail, progress = engine.last_status
        gpu_downloading, gpu_progress = getattr(engine, "gpu_status", (False, None))
        has_cuda = self._cuda()
        updater = self._host.updater
        if updater is None:
            u_state, u_detail, u_progress, checked = "idle", "", None, None
        else:
            u_state, u_detail, u_progress = updater.last_status
            checked = updater.last_checked_at
        return {
            "type": "status",
            "update": {"state": u_state, "detail": u_detail, "progress": u_progress, "checked_at": checked},
            "model": {"state": state, "detail": detail, "progress": progress},
            "gpu": {
                "has_cuda": has_cuda,
                "ready": has_cuda and not self._host.gpu_needs_download(),
                "downloading": bool(gpu_downloading),
                "progress": gpu_progress,
                "error": str(getattr(engine, "gpu_error", "") or ""),
            },
            "hardware": self._hardware_info(),
        }

    def poll(self) -> list[dict]:
        """Messages for what changed since the last time, called on a short timer."""
        out: list[dict] = []
        status = self.status()
        key = _key(status)
        if key != self._sent_status:
            self._sent_status = key
            out.append(status)
        settings = asdict(self._host.get_settings())
        if settings != self._sent_settings:
            self._sent_settings = settings
            out.append({"type": "settings", "settings": settings})
        return out

    def _cuda(self) -> bool:
        if self._has_cuda is None:
            try:
                self._has_cuda = bool(self._host.cuda_available())
            except Exception:
                self._has_cuda = False
        return self._has_cuda

    def _hardware_info(self) -> dict:
        if self._hardware is None:
            try:
                profile = self._host.hardware()
                self._hardware = {
                    "cpu": str(profile.cpu_name or ""),
                    "gpu": str(profile.gpu_name or ""),
                    "vram_mb": int(profile.vram_mb or 0),
                }
            except Exception:
                self._hardware = {"cpu": "", "gpu": "", "vram_mb": 0}
        return self._hardware

    # --- what comes from the window ---

    def handle_line(self, line: bytes | str) -> list[dict]:
        """One line from the window. Never raises: a bad line is ignored."""
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(msg, dict):
            return []
        try:
            return self._handle(msg)
        except Exception as exc:  # the engine must keep running whatever the window sends
            print(f"[dictate] settings window message failed: {exc}")
            return []

    def _handle(self, msg: dict) -> list[dict]:
        kind = msg.get("type")
        host = self._host
        if kind == "save_settings":
            new = settings_from_dict(msg.get("settings"), host.get_settings())
            if new is None:
                return []
            host.save_settings(new)
            # The engine may have refused part of it (Windows startup could not change).
            # Tell the window the truth; an accepted save is not echoed back.
            now = asdict(host.get_settings())
            self._sent_settings = now
            return [] if now == asdict(new) else [{"type": "settings", "settings": now}]
        if kind == "check_updates":
            if host.updater is not None:
                host.updater.check_now(silent=False)
        elif kind == "start_update":
            if host.updater is not None:
                host.updater.start_update()
        elif kind == "restart_to_install":
            if host.updater is not None:
                host.updater.restart_to_install()
        elif kind == "start_gpu_download":
            start = getattr(host.engine, "start_gpu_download", None)
            if start is not None:
                start()
        elif kind == "capture":
            host.set_capture_active(bool(msg.get("active")))
        elif kind == "preview":
            if isinstance(msg.get("width"), int):
                host.preview_width(min(max(msg["width"], 180), 280))
            if isinstance(msg.get("margin"), int):
                host.preview_margin(min(max(msg["margin"], 0), 400))
        elif kind == "tray_action":
            if isinstance(msg.get("id"), str):
                host.tray_action(msg["id"])
        elif kind == "bar_clicked":
            host.bar_clicked()
        elif kind == "get_devices":
            devices = [{"key": d.key, "label": d.label} for d in host.input_devices()]
            return [{"type": "devices", "devices": devices}]
        return []


def _key(status: dict) -> str:
    return json.dumps(status, sort_keys=True, separators=(",", ":"))


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


class LineBuffer:
    """Splits a byte stream into lines; refuses a line that never ends."""

    def __init__(self, limit: int = MAX_LINE_BYTES) -> None:
        self._buf = bytearray()
        self._limit = limit

    def feed(self, data: bytes) -> list[bytes]:
        """Return the complete lines in ``data``. Raises ValueError when the limit is passed."""
        self._buf.extend(data)
        lines: list[bytes] = []
        while True:
            end = self._buf.find(b"\n")
            if end < 0:
                break
            lines.append(bytes(self._buf[:end]))
            del self._buf[: end + 1]
        if len(self._buf) > self._limit:
            self._buf.clear()
            raise ValueError("line too long")
        return lines

    def clear(self) -> None:
        self._buf.clear()


# --- the pipe, on Qt's event loop ---

def make_pipe_server(bridge: UiBridge, parent=None):
    """Create the QLocalServer wrapper. Imported lazily so the logic above has no Qt dependency."""
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtNetwork import QLocalServer

    class PipeServer(QObject):
        connected = Signal()
        disconnected = Signal()

        def __init__(self) -> None:
            super().__init__(parent)
            self.name = "dictate-ui-" + secrets.token_hex(8)
            self._server = QLocalServer(self)
            self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self._server.newConnection.connect(self._on_connection)
            self._socket = None
            self._lines = LineBuffer()
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_MS)
            self._timer.timeout.connect(self._on_tick)

        def start(self) -> bool:
            QLocalServer.removeServer(self.name)
            return self._server.listen(self.name)

        @property
        def has_client(self) -> bool:
            return self._socket is not None

        def send(self, msg: dict) -> None:
            sock = self._socket
            if sock is not None:
                sock.write(encode(msg))

        def flush(self) -> None:
            """Push what was just sent out before the pipe closes (the quit message)."""
            sock = self._socket
            if sock is not None:
                sock.flush()
                sock.waitForBytesWritten(300)

        def close(self) -> None:
            self.flush()
            self._drop_client()
            self._server.close()

        def _on_connection(self) -> None:
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            self._drop_client()  # one window at a time: the newest wins
            self._socket = sock
            self._lines.clear()
            sock.readyRead.connect(self._on_ready)
            sock.disconnected.connect(self._on_disconnected)
            self._bridge_reset_and_hello()
            self._timer.start()
            self.connected.emit()

        def _bridge_reset_and_hello(self) -> None:
            bridge.reset()
            self.send(bridge.hello())

        def _on_ready(self) -> None:
            sock = self._socket
            if sock is None:
                return
            try:
                lines = self._lines.feed(bytes(sock.readAll()))
            except ValueError:
                self._drop_client()
                return
            for line in lines:
                for reply in bridge.handle_line(line):
                    self.send(reply)

        def _on_tick(self) -> None:
            for msg in bridge.poll():
                self.send(msg)

        def _on_disconnected(self) -> None:
            self._drop_client()

        def _drop_client(self) -> None:
            sock, self._socket = self._socket, None
            self._timer.stop()
            if sock is None:
                return
            try:
                sock.readyRead.disconnect(self._on_ready)
                sock.disconnected.disconnect(self._on_disconnected)
            except (RuntimeError, TypeError):
                pass
            sock.abort()
            sock.deleteLater()
            self.disconnected.emit()

    return PipeServer()


# --- starting the window ---

def find_ui_exe() -> Optional[Path]:
    """The WinUI window: DICTATE_UI_EXE, the installed ui folder, or this checkout's build."""
    candidates: list[Path] = []
    override = os.environ.get("DICTATE_UI_EXE")
    if override:
        candidates.append(Path(override))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "ui" / "DictateUI.exe")
    else:
        candidates.append(Path(__file__).resolve().parent / _UI_DEV_PATH)
    for path in candidates:
        if path.is_file():
            return path
    return None


def launch_ui(pipe_name: str) -> Optional[subprocess.Popen]:
    exe = find_ui_exe()
    if exe is None:
        return None
    try:
        return subprocess.Popen([str(exe), "--pipe", pipe_name], cwd=str(exe.parent))
    except OSError as exc:
        print(f"[dictate] could not start the settings window: {exc}")
        return None


# The built-in interface stays available as a saved preference and as a fallback.
WINUI_BY_DEFAULT = True


def winui_requested(argv: Optional[list[str]] = None, interface: str = "new") -> bool:
    """Whether to use the WinUI program for the activity bar, the tray icon and Settings.

    ``--winui`` on the command line (the Start Menu's "new interface" shortcut) or DICTATE_WINUI=1 asks for it;
    ``--builtin-ui`` or DICTATE_WINUI=0 refuses it; with neither, the saved choice decides.
    """
    args = sys.argv[1:] if argv is None else argv
    env = os.environ.get("DICTATE_WINUI", "").strip()
    if "--builtin-ui" in args or env == "0":
        return False
    if "--winui" in args or env == "1":
        return True
    return interface == "new"
