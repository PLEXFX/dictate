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

import ctypes
import time
from collections.abc import Callable

import pyperclip
from pynput import keyboard

_controller = keyboard.Controller()

# Windows needs a beat between putting data on the clipboard and the target app
# reading it back. Too short and the paste lands empty.
CLIPBOARD_SETTLE = 0.06
PASTE_SETTLE = 0.18
TEMPORARY_COPY_SECONDS = 5
FOCUS_SETTLE = 0.12  # Windows switches the foreground window asynchronously


def _read_clipboard() -> tuple[bool, str]:
    try:
        return True, pyperclip.paste()
    except Exception:
        return False, ""


def _clipboard_sequence() -> int | None:
    """Return Windows' clipboard change counter, when it is available.

    It lets a delayed restore tell the difference between Dictate's own copy
    and the user copying something else in the meantime. A content comparison
    is only a fallback: the user may deliberately copy the same words again.
    """
    try:
        value = int(ctypes.windll.user32.GetClipboardSequenceNumber())
        return value or None
    except Exception:
        return None


def copy_temporarily(text: str) -> Callable[[], bool] | None:
    """Copy ``text`` for a short recovery window without stealing clipboard data.

    The returned callback restores the previous text *only* when the user has
    not changed the clipboard since Dictate copied the recovery text. If the
    old clipboard cannot be read safely, this declines to copy rather than
    risking an image, file list, or another app's private clipboard payload.
    """
    if not text:
        return None
    previous_read, previous = _read_clipboard()
    if not previous_read:
        return None
    try:
        pyperclip.copy(text)
    except Exception:
        return None
    own_sequence = _clipboard_sequence()

    def restore() -> bool:
        try:
            if own_sequence is not None:
                # Any change, including the user copying the same words, is
                # theirs. Never restore over it.
                if _clipboard_sequence() != own_sequence:
                    return False
            else:
                readable, current = _read_clipboard()
                if not readable or current != text:
                    return False
            pyperclip.copy(previous)
            return True
        except Exception:
            return False

    return restore


def foreground_window() -> int | None:
    """Handle of the window with focus right now, or None if it cannot be read.

    Undo is built on this: text lands in whatever window is focused, so that
    handle is the only reliable identity for "the app Dictate just typed into".
    """
    try:
        return int(ctypes.windll.user32.GetForegroundWindow()) or None
    except Exception:
        return None


def window_is_alive(handle: int) -> bool:
    try:
        return bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(handle)))
    except Exception:
        return False


def undo_in(handle: int) -> bool:
    """Send one Ctrl+Z to ``handle``, refocusing it first.

    Returns False without sending anything when the window cannot be brought
    back to the front. Refusing is always correct here: a Ctrl+Z delivered to
    the wrong window undoes a stranger's work, and there is no way to take
    that back.
    """
    if not window_is_alive(handle):
        return False
    try:
        if not ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(handle)):
            return False
    except Exception:
        return False
    # Windows hands focus over asynchronously; typing into the old window is
    # exactly the failure this whole function exists to avoid.
    time.sleep(FOCUS_SETTLE)
    if foreground_window() != handle:
        return False
    with _controller.pressed(keyboard.Key.ctrl):
        _controller.press("z")
        _controller.release("z")
    return True


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
