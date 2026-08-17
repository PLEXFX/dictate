"""Getting the transcribed text into whatever window has focus.

Two routes:

- paste (default): stash the text on the clipboard and send Ctrl+V. Fast and
  exact regardless of length, but it borrows the clipboard, so the previous
  contents are saved and put back afterwards.
- type: send the characters one at a time. Slower and can trip up apps with
  their own autocomplete, but never touches the clipboard.

Both are called from a worker thread, never the UI thread -- the sleeps here
would otherwise freeze the bar mid-animation.
"""

from __future__ import annotations

import time

import pyperclip
from pynput import keyboard

_controller = keyboard.Controller()

# Windows needs a beat between putting data on the clipboard and the target app
# reading it back. Too short and the paste lands empty.
CLIPBOARD_SETTLE = 0.06
PASTE_SETTLE = 0.18


def _read_clipboard() -> tuple[bool, str]:
    try:
        return True, pyperclip.paste()
    except Exception:
        return False, ""


def send(text: str, mode: str = "paste") -> None:
    if not text:
        return
    if mode == "type":
        _controller.type(text)
        return

    previous_read, previous = _read_clipboard()
    try:
        pyperclip.copy(text)
        time.sleep(CLIPBOARD_SETTLE)
        with _controller.pressed(keyboard.Key.ctrl):
            _controller.press("v")
            _controller.release("v")
        time.sleep(PASTE_SETTLE)
    finally:
        # Restore the prior text even when it was an empty string. The old
        # truthiness check left the dictated text behind whenever the clipboard
        # started empty.
        try:
            if previous_read:
                pyperclip.copy(previous)
        except Exception:
            pass
