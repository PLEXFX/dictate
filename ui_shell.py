"""Stand-ins for the activity bar and the tray icon when the WinUI program owns them.

main.py drives the bar and the tray through a handful of calls (``bar.set_state``,
``tray.setToolTip``, ``act_undo.setEnabled`` ...). In WinUI mode those objects are
these classes: they offer the same calls but only forward what happened to the
window over the pipe (see ui_bridge.py), and turn the window's clicks back into
the same Qt signals the real widgets raised. Nothing is drawn here.

Messages to the window (all one JSON object per line):
  bar_state     {state, detail, progress}   what the bar should be doing
  bar_level     {v}                         the microphone loudness, 0..1, while listening
  bar_notice    {text, tone, clickable, duration_ms}
  bar_clickable {value}                     a locked recording can be finished by clicking the bar
  bar_theme     {dark}
  bar_show, bar_hide
  tray          {tooltip, settings_label, copy_last, undo, check_update}
  balloon       {text, tone, duration_ms}   a Windows notification, only when the person turned them on
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Signal


class ShellChannel:
    """The way out to the window. Messages sent while no window is connected are dropped:
    the window is told the current tray and bar state again when it connects (``hello``)."""

    def __init__(self) -> None:
        self.server: Any = None  # a ui_bridge pipe server, set once it exists

    @property
    def connected(self) -> bool:
        return self.server is not None and self.server.has_client

    def send(self, message: dict) -> None:
        if self.connected:
            self.server.send(message)


class RemoteBar(QObject):
    """The activity bar's side of main.py's calls, drawn by the window instead."""

    clicked = Signal()  # only raised while a locked recording is armed, like the real bar

    def __init__(self, settings, channel: ShellChannel) -> None:
        super().__init__()
        self._channel = channel
        self._settings = settings
        self._dark = True
        self._clickable = False
        self._notice_on_click: Optional[Callable[[], None]] = None

    # --- the calls main.py makes ---

    def set_theme(self, dark: bool) -> None:
        self._dark = bool(dark)
        self._channel.send({"type": "bar_theme", "dark": self._dark})

    def update_settings(self, settings) -> None:
        # The window gets settings from its own Settings page and from the engine's
        # `settings` message, and applies width, gap, linger and always-visible itself.
        self._settings = settings

    def preview_width(self, width: int) -> None:
        pass  # the window moves its own bar while a slider moves

    def preview_margin(self, margin: int) -> None:
        pass

    def notify(
        self,
        text: str,
        tone: str = "info",
        on_click: Optional[Callable[[], None]] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        self._notice_on_click = on_click
        self._channel.send({
            "type": "bar_notice",
            "text": text,
            "tone": tone,
            "clickable": on_click is not None,
            "duration_ms": duration_ms,
        })

    def set_state(self, state: str, detail: str = "", progress: Optional[float] = None) -> None:
        if state != "notice":
            self._notice_on_click = None  # whatever the last notice offered is no longer on screen
        self._channel.send({"type": "bar_state", "state": state, "detail": detail, "progress": progress})

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = bool(clickable)
        self._channel.send({"type": "bar_clickable", "value": self._clickable})

    def set_levels(self, levels) -> None:
        """The newest meter reading, reduced to one loudness (the bar shows a history of it)."""
        try:
            size = len(levels)
        except TypeError:
            return
        if not size:
            return
        level = math.sqrt(sum(float(v) * float(v) for v in levels) / size)
        self._channel.send({"type": "bar_level", "v": round(min(max(level, 0.0), 1.0), 3)})

    def set_live_text(self, text: str) -> None:
        return  # the bar is waveform-only; see DESIGN.md

    def show_bar(self) -> None:
        self._channel.send({"type": "bar_show"})

    def hide(self) -> None:
        self._channel.send({"type": "bar_hide"})

    # --- the window's click ---

    def handle_click(self) -> None:
        """A click on the bar: an actionable notice wins, else finish a locked recording."""
        if self._notice_on_click is not None:
            callback, self._notice_on_click = self._notice_on_click, None
            callback()
        elif self._clickable:
            self.clicked.emit()

    def snapshot(self) -> dict:
        return {"dark": self._dark}


class RemoteAction(QObject):
    """One tray menu entry: the parts of QAction that main.py uses."""

    triggered = Signal()

    def __init__(self, tray: "RemoteTray", action_id: str, text: str, enabled: bool = True) -> None:
        super().__init__()
        self._tray = tray
        self.id = action_id
        self._text = text
        self._enabled = enabled

    def setText(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._tray.changed()

    def text(self) -> str:
        return self._text

    def setEnabled(self, enabled: bool) -> None:
        if bool(enabled) != self._enabled:
            self._enabled = bool(enabled)
            self._tray.changed()

    def isEnabled(self) -> bool:
        return self._enabled


class RemoteTray(QObject):
    """The tray icon's side of main.py's calls. The window owns the icon and the menu."""

    def __init__(self, channel: ShellChannel, tooltip: str = "") -> None:
        super().__init__()
        self._channel = channel
        self._tooltip = tooltip
        self._actions: dict[str, RemoteAction] = {}

    def add_action(self, action_id: str, text: str, enabled: bool = True) -> RemoteAction:
        action = RemoteAction(self, action_id, text, enabled)
        self._actions[action_id] = action
        return action

    def setToolTip(self, text: str) -> None:
        if text != self._tooltip:
            self._tooltip = text
            self.changed()

    def showMessage(self, title: str, text: str, icon: Any = None, duration_ms: int = 5000) -> None:
        name = getattr(icon, "name", "") or str(icon)
        tone = "error" if "warning" in name.lower() or "critical" in name.lower() else "info"
        self._channel.send({"type": "balloon", "text": text, "tone": tone, "duration_ms": int(duration_ms)})

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def trigger(self, action_id: str) -> None:
        action = self._actions.get(action_id)
        if action is not None and (action.isEnabled() or action_id == "settings"):
            action.triggered.emit()

    def snapshot(self) -> dict:
        def label(action_id: str) -> str:
            action = self._actions.get(action_id)
            return action.text() if action is not None else ""

        def enabled(action_id: str) -> bool:
            action = self._actions.get(action_id)
            return bool(action and action.isEnabled())

        return {
            "tooltip": self._tooltip,
            "settings_label": label("settings"),
            "copy_last": enabled("copy_last"),
            "undo": enabled("undo"),
            "check_update": enabled("check_update"),
        }

    def changed(self) -> None:
        self._channel.send({"type": "tray", **self.snapshot()})
