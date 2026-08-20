"""Global hotkeys: hold-to-talk, plus a combo to open settings.

Both live on one keyboard and one mouse listener rather than pynput's
``GlobalHotKeys`` helper. That helper handles combos but swallows the raw
press/release pair that hold-to-talk needs. Keeping one shared pressed-input set
also lets a hold-to-talk binding include mouse buttons, such as
``ctrl+mouse4``.

The same binding carries two gestures. Holding it records for as long as it is
down, which is the original behaviour. Tapping it -- releasing before
``MIN_HOLD_SECONDS`` -- locks recording on instead, so a long passage does not
have to be dictated with a finger held down; the next press ends it. That tap
used to be discarded as a stray press, so the gesture costs nothing that was
previously useful, and the two are told apart by how long the key was down
rather than by a second binding the user has to learn.

Threading: pynput calls into this on its own listener thread. Nothing here
touches Qt directly -- callbacks are handed straight to the caller, which is
responsible for marshalling onto the UI thread.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from pynput import keyboard, mouse

MIN_HOLD_SECONDS = 0.25  # released faster than this is a tap, not an utterance

# Key that cancels a locked recording and throws the audio away. Not
# configurable: Esc means "get me out of this" everywhere else in Windows, and
# a locked recording is exactly the state where a user wants that reflex to
# work without having looked up a binding first.
CANCEL_KEY = "esc"

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
        on_talk_lock: Callable[[], None] | None = None,
        on_talk_cancel: Callable[[], None] | None = None,
    ):
        self._settings = settings
        self._on_talk_start = on_talk_start
        self._on_talk_end = on_talk_end
        self._on_settings = on_settings
        self._on_talk_lock = on_talk_lock or (lambda: None)
        self._on_talk_cancel = on_talk_cancel or (lambda: None)

        self._pressed: set[str] = set()
        self._talking = False
        self._press_time = 0.0
        self._combo_latched = False
        self._locked = False    # recording continues with the key released
        self._lockable = True   # this press may still turn into a lock
        self._ending_lock = False  # the press that ended a lock is still down
        self._suppressed = False  # ignore our own synthetic paste keystrokes
        self._capture_active = False  # Settings is recording a new binding
        self._watched = None            # window whose input we are counting
        self._watched_foreground = None  # callable giving the focused window
        self._watched_hits = 0
        # Reentrant: _press decides a whole gesture under this lock and can
        # call cancel_lock() from inside it, which needs the lock as well.
        self._lock = threading.RLock()
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
            # Rebinding the talk key while a locked recording runs would leave
            # a capture that the new binding cannot stop. Drop it.
            self.cancel_lock()

    # --- watching one window for user input ---

    def watch_window(self, handle, foreground) -> None:
        """Start counting input that lands in ``handle``.

        ``foreground`` is a callable returning the focused window, injected
        rather than imported so this module keeps knowing nothing about
        Windows APIs and stays testable without one.

        Only input delivered *while that window is focused* counts. That is
        the whole point: keystrokes typed into some other app cannot have
        disturbed this one's undo history, and neither can the click that
        opens Dictate's own tray menu.
        """
        self._watched = handle
        self._watched_foreground = foreground
        self._watched_hits = 0

    def stop_watching(self) -> None:
        self._watched = None
        self._watched_foreground = None
        self._watched_hits = 0

    def watched_hits(self) -> int:
        """How much user input the watched window has taken since watching began."""
        return self._watched_hits

    def _note_activity(self) -> None:
        if self._watched is None or self._suppressed:
            return
        try:
            if self._watched_foreground() == self._watched:
                self._watched_hits += 1
        except Exception:
            # Never let a failed window query silently under-report activity;
            # counting it means undo refuses, which is the safe direction.
            self._watched_hits += 1

    # --- locked recording ---

    def lock_enabled(self) -> bool:
        return bool(getattr(self._settings, "tap_to_lock", True))

    def is_locked(self) -> bool:
        return self._locked

    def cancel_lock(self) -> None:
        """Abandon a locked recording, and stop the current press forming one.

        Called both by the Esc handler and by the app when it could not
        actually open the microphone -- otherwise a tap would lock a recording
        that was never running, and the bar would sit there capturing nothing.
        """
        with self._lock:
            was_locked = self._locked
            self._locked = False
            self._lockable = False
            if was_locked:
                self._talking = False
                self._on_talk_cancel()

    def release_lock(self) -> None:
        """End a locked recording as though the user had pressed the key.

        The app calls this when a locked recording hits its time limit, so the
        audio so far is transcribed rather than discarded.
        """
        with self._lock:
            if not self._locked:
                return
            self._locked = False
            self._talking = False
            self._on_talk_end(time.monotonic() - self._press_time)

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
            # A click inside the target window moves the caret, which is
            # enough to make a later undo land somewhere unintended.
            self._press(normalize_mouse(button))
        else:
            self._release(normalize_mouse(button))

    def _press(self, name: str) -> None:
        # Counted before the early return, so a press that no binding uses
        # still marks the window as touched -- typing an ordinary letter is
        # exactly the thing that moves an undo stack on.
        self._note_activity()
        if self._suppressed or self._capture_active or not name:
            return
        # The whole decision runs under the lock, not just the pressed-key set.
        # Two listener threads deliver into here -- keyboard and mouse -- so a
        # binding spanning both (ctrl+mouse4) had both of them reading and
        # writing _talking/_locked/_press_time concurrently, which could start
        # or end a recording twice.
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

            # Esc abandons a locked recording -- unless Esc is itself part of
            # the talk binding, where ending the recording normally is what
            # was meant.
            if self._locked and name == CANCEL_KEY and CANCEL_KEY not in ptt:
                self.cancel_lock()
                return

            if not (ptt and ptt <= pressed and first_press):
                return

            if self._locked:
                # Any press of the talk key ends a locked recording. The
                # duration runs from the original press, so the whole capture
                # counts.
                self._locked = False
                self._talking = False
                self._ending_lock = True
                self._on_talk_end(time.monotonic() - self._press_time)
                return

            if not self._talking:
                self._talking = True
                self._lockable = True
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
            still_held = not ptt or ptt <= pressed
            if still_held:
                return

            if self._ending_lock:
                # Letting go of the press that finished a locked recording.
                # The capture already ended on the press; nothing to end here.
                self._ending_lock = False
                return

            if not self._talking:
                return

            held = time.monotonic() - self._press_time
            if held < MIN_HOLD_SECONDS and self._lockable and self.lock_enabled():
                self._locked = True
                self._on_talk_lock()
                return

            self._talking = False
            self._on_talk_end(held)
