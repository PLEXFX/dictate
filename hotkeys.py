"""Global hotkeys: hold-to-talk, plus a combo to open settings.

Both live on one keyboard and one mouse listener rather than pynput's
``GlobalHotKeys`` helper. That helper handles combos but swallows the raw
press/release pair that hold-to-talk needs. Keeping one shared pressed-input set
also lets a hold-to-talk binding include mouse buttons, such as
``ctrl+mouse4``.

Threading: pynput calls into this on its own listener thread. Nothing here
touches Qt directly -- callbacks are handed straight to the caller, which is
responsible for marshalling onto the UI thread.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from pynput import keyboard, mouse

MIN_HOLD_SECONDS = 0.25  # shorter than this is a stray tap, not an utterance

_MODIFIERS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "win", keyboard.Key.cmd_l: "win", keyboard.Key.cmd_r: "win",
}

_MOUSE_BUTTONS = {
    mouse.Button.left: "mouse1",
    mouse.Button.right: "mouse2",
    mouse.Button.middle: "mouse3",
}
for _button, _name in (("x1", "mouse4"), ("x2", "mouse5")):
    _value = getattr(mouse.Button, _button, None)
    if _value is not None:
        _MOUSE_BUTTONS[_value] = _name

_COMBO_ORDER = {
    "ctrl": 0,
    "alt": 1,
    "shift": 2,
    "win": 3,
}

_DISPLAY_NAMES = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Windows",
    "esc": "Esc",
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "mouse1": "Mouse left",
    "mouse2": "Mouse right",
    "mouse3": "Mouse middle",
    "mouse4": "Mouse 4",
    "mouse5": "Mouse 5",
}


def normalize(key) -> str:
    """Reduce a pynput key to a stable lowercase name.

    Left and right modifiers collapse to one name, so a combo bound to 'ctrl'
    fires from either side of the keyboard.
    """
    if key in _MODIFIERS:
        return _MODIFIERS[key]
    if isinstance(key, keyboard.Key):
        return key.name.lower()
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char.lower()
        # Held modifiers rewrite key.char to a control code; the virtual key
        # code still identifies the physical letter.
        if key.vk is not None and 65 <= key.vk <= 90:
            return chr(key.vk).lower()
    return ""


def normalize_mouse(button) -> str:
    """Reduce a pynput mouse button to the name stored in settings."""
    return _MOUSE_BUTTONS.get(button, "")


def parse_combo(text: str) -> set[str]:
    return {part.strip().lower() for part in text.split("+") if part.strip()}


def canonical_combo(parts: set[str]) -> str:
    """Store a combination in a stable, readable order."""
    return "+".join(sorted(parts, key=lambda part: (_COMBO_ORDER.get(part, 10), part)))


def format_combo(text: str) -> str:
    """Turn a stored binding into a human-readable label for the UI."""
    parts = canonical_combo(parse_combo(text)).split("+")
    labels: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in _DISPLAY_NAMES:
            labels.append(_DISPLAY_NAMES[part])
        elif part.startswith("f") and part[1:].isdigit():
            labels.append(part.upper())
        else:
            labels.append(part.replace("_", " ").title())
    return " + ".join(labels)


class Hotkeys:
    def __init__(
        self,
        settings,
        on_talk_start: Callable[[], None],
        on_talk_end: Callable[[float], None],
        on_settings: Callable[[], None],
    ):
        self._settings = settings
        self._on_talk_start = on_talk_start
        self._on_talk_end = on_talk_end
        self._on_settings = on_settings

        self._pressed: set[str] = set()
        self._talking = False
        self._press_time = 0.0
        self._combo_latched = False
        self._suppressed = False  # ignore our own synthetic paste keystrokes
        self._capture_active = False  # Settings is recording a new binding
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None

    def update_settings(self, settings) -> None:
        self._settings = settings

    def suppress(self, on: bool) -> None:
        self._suppressed = on
        if on:
            # Drop tracked state; the synthetic Ctrl+V's release events would
            # otherwise leave 'ctrl' stuck in the pressed set forever.
            with self._lock:
                self._pressed.clear()

    def set_capture_active(self, active: bool) -> None:
        """Temporarily stop bindings firing while Settings records an input.

        The recorder receives the UI's raw press/release events. Clearing our
        own state at both edges means those ignored events can never leave a
        modifier or mouse button stuck after recording finishes.
        """
        self._capture_active = active
        with self._lock:
            self._pressed.clear()
        if active:
            self._combo_latched = False

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.start()
        self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
        self._mouse_listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None

    def _on_press(self, key) -> None:
        self._press(normalize(key))

    def _on_release(self, key) -> None:
        self._release(normalize(key))

    def _on_mouse_click(self, _x, _y, button, pressed: bool) -> None:
        if pressed:
            self._press(normalize_mouse(button))
        else:
            self._release(normalize_mouse(button))

    def _press(self, name: str) -> None:
        if self._suppressed or self._capture_active or not name:
            return
        with self._lock:
            first_press = name not in self._pressed
            self._pressed.add(name)
            pressed = set(self._pressed)

        combo = parse_combo(self._settings.settings_hotkey)
        if combo and combo <= pressed:
            if not self._combo_latched:
                self._combo_latched = True
                self._on_settings()
            return

        ptt = parse_combo(self._settings.ptt_key)
        if ptt and ptt <= pressed and first_press and not self._talking:
            self._talking = True
            self._press_time = time.monotonic()
            self._on_talk_start()

    def _release(self, name: str) -> None:
        if self._suppressed or self._capture_active or not name:
            return
        with self._lock:
            self._pressed.discard(name)
            pressed = set(self._pressed)

        combo = parse_combo(self._settings.settings_hotkey)
        if self._combo_latched and not (combo <= pressed):
            self._combo_latched = False

        ptt = parse_combo(self._settings.ptt_key)
        if self._talking and not (ptt <= pressed):
            self._talking = False
            self._on_talk_end(time.monotonic() - self._press_time)
