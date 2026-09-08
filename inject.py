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
from ctypes import wintypes

import pyperclip
from pynput import keyboard

_controller = keyboard.Controller()

# Windows needs a beat between putting data on the clipboard and the target app
# reading it back. Too short and the paste lands empty.
CLIPBOARD_SETTLE = 0.06
PASTE_SETTLE = 0.18
TEMPORARY_COPY_SECONDS = 5
FOCUS_SETTLE = 0.12  # Windows switches the foreground window asynchronously

CF_UNICODETEXT = 13
_TEXT_FORMATS = {1, 7, CF_UNICODETEXT, 16}  # text, OEM text, Unicode, locale

_user32_lib = None
_user32_ready = False


def _user32():
    """user32 with real signatures, or None where it cannot be loaded.

    Window handles are pointer-sized. ctypes assumes a C ``int`` return without
    a declared restype, which truncates and sign-extends an HWND -- so the
    handle undo is anchored to could come back as a different number from the
    one Windows handed out.
    """
    global _user32_lib, _user32_ready
    if _user32_ready:
        return _user32_lib
    _user32_ready = True
    try:
        lib = ctypes.WinDLL("user32", use_last_error=True)
    except (AttributeError, OSError):
        return None
    lib.GetForegroundWindow.restype = wintypes.HWND
    lib.GetForegroundWindow.argtypes = ()
    lib.SetForegroundWindow.restype = wintypes.BOOL
    lib.SetForegroundWindow.argtypes = (wintypes.HWND,)
    lib.IsWindow.restype = wintypes.BOOL
    lib.IsWindow.argtypes = (wintypes.HWND,)
    lib.GetClipboardSequenceNumber.restype = wintypes.DWORD
    lib.GetClipboardSequenceNumber.argtypes = ()
    lib.CountClipboardFormats.restype = ctypes.c_int
    lib.CountClipboardFormats.argtypes = ()
    lib.IsClipboardFormatAvailable.restype = wintypes.BOOL
    lib.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
    lib.OpenClipboard.restype = wintypes.BOOL
    lib.OpenClipboard.argtypes = (wintypes.HWND,)
    lib.CloseClipboard.restype = wintypes.BOOL
    lib.CloseClipboard.argtypes = ()
    lib.EnumClipboardFormats.restype = wintypes.UINT
    lib.EnumClipboardFormats.argtypes = (wintypes.UINT,)
    _user32_lib = lib
    return lib


def _read_clipboard() -> tuple[bool, str]:
    try:
        return True, pyperclip.paste()
    except Exception:
        return False, ""


def clipboard_is_borrowable() -> bool:
    """Borrow only an empty or plain-text clipboard we can fully restore.

    Rich text, HTML, images and application-private formats may coexist with
    Unicode text. A text-only backup would silently discard those formats.
    """
    try:
        user32 = _user32()
        if user32 is None or not user32.OpenClipboard(None):
            return False
        try:
            formats = set()
            current = 0
            while True:
                ctypes.set_last_error(0)
                current = int(user32.EnumClipboardFormats(current))
                if not current:
                    if ctypes.get_last_error():
                        return False
                    break
                formats.add(current)
                if current not in _TEXT_FORMATS:
                    return False
            return not formats or CF_UNICODETEXT in formats
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def _clipboard_sequence() -> int | None:
    """Return Windows' clipboard change counter, when it is available.

    It lets a delayed restore tell the difference between Dictate's own copy
    and the user copying something else in the meantime. A content comparison
    is only a fallback: the user may deliberately copy the same words again.
    """
    try:
        user32 = _user32()
        if user32 is None:
            return None
        return int(user32.GetClipboardSequenceNumber()) or None
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
    # Same reason as send(): a read alone cannot tell an empty clipboard from
    # one holding an image or copied files, and both read back as "".
    if not clipboard_is_borrowable():
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
        user32 = _user32()
        if user32 is None:
            return None
        return int(user32.GetForegroundWindow() or 0) or None
    except Exception:
        return None


def window_is_alive(handle: int) -> bool:
    try:
        user32 = _user32()
        if user32 is None:
            return False
        return bool(user32.IsWindow(handle))
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
        user32 = _user32()
        if user32 is None or not user32.SetForegroundWindow(handle):
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


class ClipboardUnavailable(RuntimeError):
    """Raised when pasting would destroy clipboard content Dictate can't restore.

    Distinct from a generic failure so the caller can respond by typing the
    text instead, which touches no clipboard at all. Losing a dictation is a
    worse outcome than a slower insertion.
    """


def send(text: str, mode: str = "paste") -> None:
    if not text:
        return
    if mode == "type":
        _controller.type(text)
        return

    restore = copy_temporarily(text)
    if restore is None:
        raise ClipboardUnavailable("Couldn't safely preserve the clipboard")
    try:
        time.sleep(CLIPBOARD_SETTLE)
        with _controller.pressed(keyboard.Key.ctrl):
            _controller.press("v")
            _controller.release("v")
        time.sleep(PASTE_SETTLE)
    finally:
        # Uses the clipboard sequence counter: a newer copy belongs to the
        # user, including a deliberate copy of the exact same words.
        restore()
