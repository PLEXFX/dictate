"""Settings window, laid out like the Windows 11 Settings app.

Each option is a rounded card row: title and one line of explanation on the
left, the control on the right. That pattern is most of what makes a window
read as native, more than any individual control does.

The main page exposes only the decisions needed for everyday dictation.
Implementation details remain available in a collapsed Advanced section.
Changes save automatically; model-affecting changes visibly reload the engine.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import asdict, replace

from PySide6.QtCore import Property, Qt, QPropertyAnimation, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import audio as audio_mod
import config
import engine as engine_mod
import gpu_runtime
import hotkeys as hotkeys_mod
import startup as startup_mod
import updater as updater_mod
from bar import ENTER_MS, EXIT_MS, FLUENT_ACCELERATE, FLUENT_DECELERATE
from theme import system_is_dark, transparency_enabled
from toggle import ToggleSwitch
from version import VERSION

DARK_STYLE = """
QWidget#root { background: #202020; }
QLabel { color: #FFFFFF; }
QLabel[role="title"] { font-size: 10pt; }
QLabel[role="desc"] { color: #9D9D9D; font-size: 8pt; }
QLabel[role="header"] { color: #FFFFFF; font-size: 11pt; font-weight: 600; }
QLabel[role="status"] { color: #9D9D9D; font-size: 8pt; }
QLabel[role="appTitle"] { color: #FFFFFF; font-size: 19pt; font-weight: 600; }
QLabel[role="section"] {
    color: #D8D8D8;
    font-size: 8pt;
    font-weight: 600;
    padding: 3px 2px 1px 2px;
}
QFrame#card {
    background: #2B2B2B;
    border: 1px solid #303030;
    border-radius: 6px;
}
QFrame#hero {
    background: #252525;
    border: 1px solid #303030;
    border-radius: 8px;
}
QFrame#settingsGroup {
    background: #2B2B2B;
    border: 1px solid #353535;
    border-radius: 8px;
}
QFrame#settingsRow {
    background: transparent;
    border: none;
    border-bottom: 1px solid #383838;
    border-radius: 0px;
}
QFrame#settingsRow[nested="true"] { background: #272727; }
QFrame#settingsRow[last="true"] { border-bottom: none; }

QComboBox, QLineEdit, QPlainTextEdit, QPushButton#keyCapture {
    background: #333333;
    border: 1px solid #3D3D3D;
    border-bottom: 1px solid #545454;
    border-radius: 4px;
    padding: 5px 10px;
    color: #FFFFFF;
    min-height: 18px;
    selection-background-color: #4CC2FF;
    selection-color: #000000;
}
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover, QPushButton#keyCapture:hover { background: #383838; }
QComboBox:disabled, QLineEdit:disabled, QPlainTextEdit:disabled, QPushButton#keyCapture:disabled {
    background: #2A2A2A; color: #6D6D6D; border-color: #333333;
}
QPushButton#keyCapture:focus { border-color: #4CC2FF; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #2C2C2C;
    border: 1px solid #3D3D3D;
    border-radius: 6px;
    color: #FFFFFF;
    selection-background-color: #3D3D3D;
    outline: none;
    padding: 4px;
}
QPushButton#apply {
    background: #4CC2FF;
    color: #000000;
    border: none;
    border-radius: 4px;
    padding: 6px 22px;
    font-weight: 600;
}
QPushButton#apply:hover { background: #62CBFF; }
QPushButton#apply:pressed { background: #3DAEEA; }
QPushButton#apply:disabled { background: #3A3A3A; color: #6D6D6D; }
QPushButton#secondary {
    background: #333333;
    color: #FFFFFF;
    border: 1px solid #454545;
    border-radius: 4px;
    padding: 6px 18px;
}
QPushButton#secondary:hover { background: #3B3B3B; }
QPushButton#link {
    background: transparent;
    color: #4CC2FF;
    border: none;
    padding: 5px 2px;
}
QPushButton#link:hover { color: #78D3FF; text-decoration: underline; }
/* Windows 11 expander: the same card the settings groups use, so an open
   Advanced section reads as a continuation of the page rather than a link
   someone left at the bottom of it. */
QPushButton#expander {
    background: #2B2B2B;
    border: 1px solid #353535;
    border-radius: 8px;
    text-align: left;
}
QPushButton#expander:hover { background: #323232; }
QPushButton#expander:pressed { background: #262626; }
QPushButton#expander:checked { border-color: #3D3D3D; }
QLabel#expanderLabel { color: #E8E8E8; font-weight: 600; background: transparent; }
QPushButton#sliderReset {
    background: transparent;
    color: #4CC2FF;
    border: none;
    padding: 1px 0px;
    font-size: 8pt;
}
QPushButton#sliderReset:hover { color: #78D3FF; text-decoration: underline; }

/* Windows 11 slider. The thumb is an accent-filled centre inside a neutral
   ring -- not a light centre with an accent ring, which is the inverted
   version and the reason these looked wrong. The centre is 12px at rest,
   grows to 14px on hover and shrinks to 10px while dragging, exactly as the
   shell's own sliders do; a radial gradient is how that is expressed in a
   Qt stylesheet, since the thumb is a single element here. */
QSlider { min-height: 24px; }
QSlider::groove:horizontal {
    height: 4px;
    background: rgba(255, 255, 255, 139);
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #4CC2FF;
    border-radius: 2px;
}
QSlider::add-page:horizontal {
    background: rgba(255, 255, 255, 139);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #4CC2FF, stop:0.60 #4CC2FF, stop:0.62 #454545, stop:1 #454545);
    border: 1px solid #4A4A4A;
    width: 18px;
    height: 18px;
    margin: -8px 0px;
    border-radius: 10px;
}
QSlider::handle:horizontal:hover {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #4CC2FF, stop:0.70 #4CC2FF, stop:0.72 #454545, stop:1 #454545);
}
QSlider::handle:horizontal:pressed {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #3DAEEA, stop:0.50 #3DAEEA, stop:0.52 #454545, stop:1 #454545);
}
QSlider::groove:horizontal:disabled,
QSlider::add-page:horizontal:disabled { background: rgba(255, 255, 255, 60); }
QSlider::sub-page:horizontal:disabled { background: #5A5A5A; }
QSlider::handle:horizontal:disabled {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #7A7A7A, stop:0.60 #7A7A7A, stop:0.62 #454545, stop:1 #454545);
    border-color: #3C3C3C;
}

QScrollArea { border: none; background: #202020; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #4D4D4D; border-radius: 3px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #5E5E5E; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* Windows 11 progress bar: a thin flat track with a rounded accent chunk --
   used for update/GPU-runtime/model downloads, determinate or (range 0,0)
   the indeterminate "still working" case, both in the same four-pixel rail
   the rest of the shell uses instead of a taller stock QProgressBar. */
QProgressBar#nativeProgress {
    background: rgba(255, 255, 255, 139);
    border: none;
    border-radius: 2px;
    max-height: 4px;
    min-height: 4px;
}
QProgressBar#nativeProgress::chunk {
    background: #4CC2FF;
    border-radius: 2px;
}

/* Back-navigation header for an in-place sub-page (Privacy). */
QPushButton#backNav {
    background: transparent;
    border: none;
    color: #4CC2FF;
    font-weight: 600;
    padding: 4px 2px;
    text-align: left;
}
QPushButton#backNav:hover { color: #78D3FF; }
"""

LIGHT_STYLE = """
QWidget#root { background: #F3F3F3; }
QLabel { color: #1A1A1A; }
QLabel[role="title"] { font-size: 10pt; }
QLabel[role="desc"], QLabel[role="status"] { color: #5F5F5F; font-size: 8pt; }
QLabel[role="header"] { color: #1A1A1A; font-size: 11pt; font-weight: 600; }
QLabel[role="appTitle"] { color: #1A1A1A; font-size: 19pt; font-weight: 600; }
QLabel[role="section"] { color: #4A4A4A; font-size: 8pt; font-weight: 600; padding: 3px 2px 1px 2px; }
QFrame#card { background: #FFFFFF; border: 1px solid #E3E3E3; border-radius: 6px; }
QFrame#hero { background: #FAFAFA; border: 1px solid #E0E0E0; border-radius: 8px; }
QFrame#settingsGroup { background: #FFFFFF; border: 1px solid #E0E0E0; border-radius: 8px; }
QFrame#settingsRow { background: transparent; border: none; border-bottom: 1px solid #E6E6E6; border-radius: 0px; }
QFrame#settingsRow[nested="true"] { background: #F8F8F8; }
QFrame#settingsRow[last="true"] { border-bottom: none; }
QComboBox, QLineEdit, QPlainTextEdit, QPushButton#keyCapture { background: #FFFFFF; border: 1px solid #C9C9C9; border-bottom: 1px solid #AFAFAF; border-radius: 4px; padding: 5px 10px; color: #1A1A1A; min-height: 18px; selection-background-color: #0078D4; selection-color: #FFFFFF; }
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover, QPushButton#keyCapture:hover { background: #F8F8F8; }
QComboBox:disabled, QLineEdit:disabled, QPlainTextEdit:disabled, QPushButton#keyCapture:disabled { background: #EEEEEE; color: #888888; border-color: #DDDDDD; }
QPushButton#keyCapture:focus { border-color: #0078D4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #FFFFFF; border: 1px solid #C9C9C9; border-radius: 6px; color: #1A1A1A; selection-background-color: #E5F1FB; outline: none; padding: 4px; }
QPushButton#apply { background: #0078D4; color: #FFFFFF; border: none; border-radius: 4px; padding: 6px 22px; font-weight: 600; }
QPushButton#apply:hover { background: #006CBE; } QPushButton#apply:pressed { background: #005A9E; } QPushButton#apply:disabled { background: #DADADA; color: #888888; }
QPushButton#secondary { background: #FFFFFF; color: #1A1A1A; border: 1px solid #C9C9C9; border-radius: 4px; padding: 6px 18px; }
QPushButton#secondary:hover { background: #F3F3F3; }
QPushButton#link, QPushButton#sliderReset { background: transparent; color: #0067B8; border: none; padding: 5px 2px; }
QPushButton#link:hover, QPushButton#sliderReset:hover { color: #004C87; text-decoration: underline; }
QPushButton#expander { background: #FFFFFF; border: 1px solid #E0E0E0; border-radius: 8px; text-align: left; }
QPushButton#expander:hover { background: #F8F8F8; }
QPushButton#expander:pressed { background: #F0F0F0; }
QPushButton#expander:checked { border-color: #D6D6D6; }
QLabel#expanderLabel { color: #1A1A1A; font-weight: 600; background: transparent; }
QSlider { min-height: 24px; } QSlider::groove:horizontal, QSlider::add-page:horizontal { height: 4px; background: #8A8A8A; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #0078D4; border-radius: 2px; }
QSlider::handle:horizontal { background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #0078D4, stop:0.60 #0078D4, stop:0.62 #FFFFFF, stop:1 #FFFFFF); border: 1px solid #B8B8B8; width: 18px; height: 18px; margin: -8px 0px; border-radius: 10px; }
QSlider::handle:horizontal:hover { background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #0078D4, stop:0.70 #0078D4, stop:0.72 #FFFFFF, stop:1 #FFFFFF); }
QScrollArea { border: none; background: #F3F3F3; } QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; } QScrollBar::handle:vertical { background: #A8A8A8; border-radius: 3px; min-height: 24px; } QScrollBar::handle:vertical:hover { background: #7E7E7E; } QScrollBar::add-line, QScrollBar::sub-line { height: 0; } QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar#nativeProgress { background: #DADADA; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }
QProgressBar#nativeProgress::chunk { background: #0078D4; border-radius: 2px; }
QPushButton#backNav { background: transparent; border: none; color: #0067B8; font-weight: 600; padding: 4px 2px; text-align: left; }
QPushButton#backNav:hover { color: #004C87; }
"""


# Cleaner display text for the Advanced tab's model picker. config.MODELS
# keeps the raw faster-whisper size names (what engine.py actually loads);
# these are only how the combo box shows them.
MODEL_LABELS = {
    "tiny.en": "Tiny",
    "base.en": "Base",
    "small.en": "Small",
    "medium.en": "Medium",
    "large-v3-turbo": "Large v3 Turbo",
}


# A translucent #root doesn't touch any card, header, or control -- those
# keep their own near-opaque backgrounds from DARK_STYLE/LIGHT_STYLE above,
# same as the real Windows 11 Settings app: Mica/Acrylic blurs the gaps
# between cards, not the cards themselves. Appended after the base
# stylesheet so it overrides just the one #root rule.
_TRANSPARENT_ROOT_OVERRIDE = "\nQWidget#root { background: transparent; }\n"


def stylesheet(dark: bool | None = None, transparent: bool | None = None) -> str:
    base = DARK_STYLE if (system_is_dark() if dark is None else dark) else LIGHT_STYLE
    if transparency_enabled() if transparent is None else transparent:
        return base + _TRANSPARENT_ROOT_OVERRIDE
    return base

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWCP_ROUND = 2
DWMSBT_NONE = 1
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic -- the public enum has no plain "glass"


def apply_native_chrome(
    hwnd: int, dark: bool | None = None, transparent: bool | None = None
) -> None:
    """Dark title bar, rounded corners, and (when Windows' own "Transparency
    effects" setting is on) an Acrylic system backdrop on a normal window.

    These are the same DWM attributes the shell uses. Silently ignored on
    builds that predate them (DWMWA_SYSTEMBACKDROP_TYPE needs Windows 11), so
    no version check is needed -- DwmSetWindowAttribute just fails and the
    window keeps its solid QSS-painted background from stylesheet() instead,
    exactly the "fall back to standard light or dark mode" Windows itself
    does when a caller asks for a backdrop it can't provide.
    """
    try:
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(int(system_is_dark() if dark is None else dark))
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), ctypes.sizeof(dark)
        )
        corner = ctypes.c_int(DWMWCP_ROUND)
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner)
        )
        want_transparent = transparency_enabled() if transparent is None else transparent
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW if want_transparent else DWMSBT_NONE)
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )
    except Exception:
        pass


def _card(title: str, desc: str, control: QWidget) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(12)

    text = QVBoxLayout()
    text.setSpacing(1)
    label = QLabel(title)
    label.setProperty("role", "title")
    text.addWidget(label)
    if desc:
        sub = QLabel(desc)
        sub.setObjectName("desc")  # so a row can update its own explanation
        sub.setProperty("role", "desc")
        sub.setWordWrap(True)
        text.addWidget(sub)
    row.addLayout(text, 1)
    row.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
    return frame


def _header(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "header")
    return label


def _info_card(title: str, desc: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 11, 14, 11)
    layout.setSpacing(3)
    heading = QLabel(title)
    heading.setProperty("role", "title")
    layout.addWidget(heading)
    body = QLabel(desc)
    body.setProperty("role", "desc")
    body.setWordWrap(True)
    layout.addWidget(body)
    return frame


def _text_row(title: str, desc: str) -> QFrame:
    """A read-only disclosure row -- title plus explanation, no control.

    Meant to sit inside _settings_group() so a set of related facts reads as
    one grouped list with hairline dividers, the same way the Windows
    Settings app groups related items under one card instead of stacking a
    separate floating box per fact.
    """
    frame = QFrame()
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(2)
    heading = QLabel(title)
    heading.setProperty("role", "title")
    layout.addWidget(heading)
    body = QLabel(desc)
    body.setProperty("role", "desc")
    body.setWordWrap(True)
    layout.addWidget(body)
    return frame


def _native_progress_bar() -> QProgressBar:
    """One shared, Fluent-styled progress bar for every download in the app.

    Update checks, GPU-runtime downloads, and model downloads already flow
    through the same LOADING-with-optional-progress status shape (see
    SettingsWindow.refresh_status()); this is the one visual it drives,
    rather than a separate bar per download kind.
    """
    bar = QProgressBar()
    bar.setObjectName("nativeProgress")
    bar.setTextVisible(False)
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setVisible(False)
    return bar


def _progress_row(bar: QProgressBar) -> QFrame:
    """A slim, title-less row that just holds a full-width progress bar."""
    frame = QFrame()
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 8, 14, 8)
    layout.addWidget(bar)
    return frame


class Chevron(QWidget):
    """The expander's arrow, drawn rather than typed.

    Windows 11 rotates this glyph through 180 degrees as a section opens; it
    is the single motion that most makes an expander read as native. A text
    character cannot do that -- swapping "›" for "⌄" is a jump cut in the
    middle of an otherwise continuous animation -- so it is painted from two
    strokes and given a real ``rotation`` property for QPropertyAnimation to
    drive alongside the height and fade.
    """

    SIZE = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rotation = 0.0
        self._color = QColor("#D8D8D8")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def rotation(self) -> float:
        return self._rotation

    def setRotation(self, angle: float) -> None:
        self._rotation = float(angle)
        self.update()

    # Named for Qt's property system, which is what QPropertyAnimation drives.
    rotation = Property(float, rotation, setRotation)

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.rotate(self._rotation)

        pen = QPen(self._color)
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        # A chevron pointing down, drawn about its own centre so rotation has
        # no visual drift.
        reach = self.SIZE * 0.26
        path = QPainterPath()
        path.moveTo(-reach, -reach * 0.55)
        path.lineTo(0.0, reach * 0.55)
        path.lineTo(reach, -reach * 0.55)
        painter.drawPath(path)
        painter.end()


class ExpanderHeader(QPushButton):
    """A Windows 11 expander header: label on the left, chevron on the right.

    A plain QPushButton cannot place its text and its glyph at opposite ends
    of the row, which is what makes the native control read as a card rather
    than as a link. The button keeps its checkable click behaviour and simply
    lays its two pieces out itself.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("expander")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)

        self.label = QLabel(text)
        self.label.setObjectName("expanderLabel")
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.label)
        row.addStretch(1)

        self.chevron = Chevron(self)
        row.addWidget(self.chevron, 0, Qt.AlignVCenter)
        self.setMinimumHeight(44)  # the native expander's row height


def _settings_group(*rows: QFrame) -> QFrame:
    group = QFrame()
    group.setObjectName("settingsGroup")
    layout = QVBoxLayout(group)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for index, row in enumerate(rows):
        row.setObjectName("settingsRow")
        row.setProperty("last", index == len(rows) - 1)
        layout.addWidget(row)
    return group


_QT_SPECIAL_KEYS = {
    Qt.Key_Control: "ctrl",
    Qt.Key_Alt: "alt",
    Qt.Key_AltGr: "alt",
    Qt.Key_Shift: "shift",
    Qt.Key_Meta: "win",
    Qt.Key_Escape: "esc",
    Qt.Key_Space: "space",
    Qt.Key_Return: "enter",
    Qt.Key_Enter: "enter",
    Qt.Key_Tab: "tab",
    Qt.Key_Backspace: "backspace",
    Qt.Key_Delete: "delete",
    Qt.Key_Insert: "insert",
    Qt.Key_Home: "home",
    Qt.Key_End: "end",
    Qt.Key_PageUp: "page_up",
    Qt.Key_PageDown: "page_down",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
}

_QT_MOUSE_BUTTONS = {
    Qt.LeftButton: "mouse1",
    Qt.RightButton: "mouse2",
    Qt.MiddleButton: "mouse3",
    Qt.BackButton: "mouse4",
    Qt.ForwardButton: "mouse5",
}


def _qt_key_name(event) -> str:
    """Translate a Qt key event to the same names as the global listener."""
    key = event.key()
    if key in _QT_SPECIAL_KEYS:
        return _QT_SPECIAL_KEYS[key]
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key).lower()
    if Qt.Key_F1 <= key <= Qt.Key_F35:
        return f"f{key - Qt.Key_F1 + 1}"
    return ""


class BindingCapture(QPushButton):
    """A click-to-record shortcut button for keyboard and mouse combinations."""

    bindingChanged = Signal(str)
    captureActive = Signal(bool)

    def __init__(self, binding: str) -> None:
        super().__init__()
        self._binding = ""
        self._active = False
        self._held: set[str] = set()
        self._recorded: set[str] = set()
        self.setObjectName("keyCapture")
        self.setFixedWidth(180)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._start_capture)
        self.setBinding(binding)

    def binding(self) -> str:
        return self._binding

    def setBinding(self, binding: str) -> None:
        self._binding = hotkeys_mod.canonical_combo(hotkeys_mod.parse_combo(binding))
        self.setText(hotkeys_mod.format_combo(self._binding))
        self.setToolTip("Click, then press a key or mouse button. Hold inputs together for a combination.")

    def cancel(self) -> None:
        if not self._active:
            return
        self._active = False
        self._held.clear()
        self._recorded.clear()
        self.setText(hotkeys_mod.format_combo(self._binding))
        self.captureActive.emit(False)

    def _start_capture(self) -> None:
        if self._active:
            return
        self._active = True
        self._held.clear()
        self._recorded.clear()
        self.setText("Press a key or mouse button…")
        self.setFocus()
        self.captureActive.emit(True)

    def _press(self, name: str) -> None:
        if not name or not self._active:
            return
        self._held.add(name)
        self._recorded.update(self._held)
        self.setText(hotkeys_mod.format_combo(hotkeys_mod.canonical_combo(self._recorded)))

    def _release(self, name: str) -> None:
        if not name or not self._active:
            return
        self._held.discard(name)
        if self._held or not self._recorded:
            return
        self._binding = hotkeys_mod.canonical_combo(self._recorded)
        self._active = False
        self.setText(hotkeys_mod.format_combo(self._binding))
        self.bindingChanged.emit(self._binding)
        self.captureActive.emit(False)

    def keyPressEvent(self, event) -> None:
        if self._active:
            if not event.isAutoRepeat():
                self._press(_qt_key_name(event))
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if self._active:
            if not event.isAutoRepeat():
                self._release(_qt_key_name(event))
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._active:
            self._press(_QT_MOUSE_BUTTONS.get(event.button(), ""))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._active:
            self._release(_QT_MOUSE_BUTTONS.get(event.button(), ""))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event) -> None:
        self.cancel()
        super().focusOutEvent(event)


class ValueSlider(QWidget):
    """A compact Fluent slider that keeps its default visible after a change."""

    valueChanged = Signal(int)

    def __init__(
        self,
        values: list[int],
        value: int,
        default: int,
        suffix: str,
    ):
        super().__init__()
        self._values = values
        self._default = default
        self._suffix = suffix
        self.setFixedWidth(230)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.value_label = QLabel()
        self.value_label.setProperty("role", "status")
        top.addWidget(self.value_label)
        top.addStretch(1)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setObjectName("sliderReset")
        self.reset_btn.clicked.connect(lambda: self.setValue(self._default))
        top.addWidget(self.reset_btn)
        col.addLayout(top)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, len(values) - 1)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.valueChanged.connect(self._on_index_changed)
        col.addWidget(self.slider)
        self.setValue(value)

    def value(self) -> int:
        return self._values[self.slider.value()]

    def setValue(self, value: float | int) -> None:
        nearest = min(range(len(self._values)), key=lambda i: abs(self._values[i] - value))
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(nearest)
        self.slider.blockSignals(blocked)
        self._update_label()

    def _format(self, value: int) -> str:
        return f"{value}{self._suffix}"

    def _on_index_changed(self, _index: int) -> None:
        self._update_label()
        self.valueChanged.emit(self.value())

    def _update_label(self) -> None:
        current = self.value()
        if current == self._default:
            self.value_label.setText(f"{self._format(current)} · Default")
            self.reset_btn.setVisible(False)
        else:
            self.value_label.setText(
                f"{self._format(current)} · Default: {self._format(self._default)}"
            )
            self.reset_btn.setVisible(True)


def _fill_microphone_box(box: QComboBox, selected_key: str) -> None:
    blocked = box.blockSignals(True)
    box.clear()
    box.addItem("Windows default", "")
    for device in audio_mod.input_devices():
        box.addItem(device.label, device.key)
    if selected_key and box.findData(selected_key) < 0:
        box.addItem("Unavailable — previous selection", selected_key)
    box.setCurrentIndex(max(0, box.findData(selected_key)))
    box.blockSignals(blocked)


def _populate_privacy_content(col: QVBoxLayout) -> None:
    """Build the actual privacy disclosure into ``col``.

    Shared by PrivacyDialog (still used by the first-run welcome dialog,
    which is its own standalone top-level window) and PrivacyPage (the
    in-place Settings sub-page navigated to from SettingsWindow), so the
    real content -- the part that matters -- exists in exactly one place.
    """
    title = QLabel("Your voice stays on this PC")
    title.setProperty("role", "header")
    col.addWidget(title)
    intro = QLabel(
        "Dictate is a local tool with no account and no cloud transcription. "
        "This page explains exactly what it uses and the few things that "
        "leave this PC."
    )
    intro.setProperty("role", "desc")
    intro.setWordWrap(True)
    col.addWidget(intro)
    col.addSpacing(4)

    local_header = QLabel("STAYS ON THIS PC")
    local_header.setProperty("role", "section")
    col.addWidget(local_header)
    col.addWidget(
        _settings_group(
            _text_row(
                "Microphone",
                "The selected microphone opens only while you hold the talk "
                "key. Audio is kept in memory long enough to transcribe it; "
                "Dictate does not save voice recordings.",
            ),
            _text_row(
                "Local transcription",
                "faster-whisper processes your audio on this computer. "
                "Dictate does not upload recordings or transcribed text to "
                "a transcription service.",
            ),
            _text_row(
                "Last dictation",
                "The most recent successful result is kept in memory only, "
                "never written to disk, so \"Copy last dictation\" in the "
                "tray menu can recover it. It's replaced the moment you "
                "dictate again, and cleared when Dictate closes.",
            ),
            _text_row(
                "Clipboard",
                "Dictate temporarily uses the Windows clipboard to insert "
                "the result, then restores the previous contents — "
                "including an empty clipboard. \"Copy last dictation\" "
                "works the same way: your prior clipboard returns after "
                "5 seconds unless you copy something new first.",
            ),
            _text_row(
                "Words I use",
                "Names, brands, or terms you add are saved to your local "
                "settings file and used only as local recognition hints "
                "for Whisper. They never leave this PC.",
            ),
            _text_row(
                "Your settings",
                "Preferences — device, hotkeys, recognition words, window "
                "layout — are saved as plain JSON at "
                "%APPDATA%\\dictate\\settings.json. Nothing here is "
                "tied to an account or synced anywhere.",
            ),
            _text_row(
                "Diagnostics",
                "The debug console reports status and errors, but it does "
                "not print the words you dictated.",
            ),
        )
    )

    network_header = QLabel("LEAVES THIS PC")
    network_header.setProperty("role", "section")
    col.addWidget(network_header)
    col.addWidget(
        _settings_group(
            _text_row(
                "Speech model downloads",
                "The first time you use a speech model, its files may "
                "download from Hugging Face. Your audio and words are "
                "never part of that request.",
            ),
            _text_row(
                "GPU acceleration files",
                "Switching Processing to GPU downloads NVIDIA's CUDA "
                "runtime files from PyPI the first time, if your PC has a "
                "supported GPU but is missing them. Again, no audio or "
                "text is part of that request.",
            ),
            _text_row(
                "Update checks",
                "Dictate asks GitHub's public release page once a day, or "
                "whenever you click \"Check for updates,\" whether a newer "
                "version exists. No account, hardware ID, or usage data is "
                "sent — the same plain request any visitor's browser "
                "would make. Turn off \"Check for updates automatically\" "
                "in Settings and Dictate never makes this request.",
            ),
        )
    )


class PrivacyDialog(QDialog):
    """Plain-language disclosure of Dictate's real local data flow.

    Only reached from FirstRunDialog now -- SettingsWindow navigates to
    PrivacyPage in place instead of opening this as a second window.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("Dictate privacy")
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(520, 460)
        self.resize(560, 660)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        page = QWidget()
        page.setObjectName("root")
        col = QVBoxLayout(page)
        col.setContentsMargins(22, 20, 22, 20)
        col.setSpacing(9)
        scroll.setWidget(page)

        _populate_privacy_content(col)

        col.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_btn)
        col.addLayout(button_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_native_chrome(int(self.winId()))


class PrivacyPage(QWidget):
    """Privacy as an in-place Settings sub-page, not a popup window.

    Same real content as PrivacyDialog (via _populate_privacy_content), but
    laid out the way Windows 11's own Settings app handles a sub-page: a
    back arrow at the top instead of a Close button, swapped into the same
    window SettingsWindow already owns rather than opened as a new one.
    """

    back = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        page = QWidget()
        page.setObjectName("root")
        col = QVBoxLayout(page)
        col.setContentsMargins(22, 20, 22, 20)
        col.setSpacing(9)
        scroll.setWidget(page)

        back_btn = QPushButton("← Privacy")
        back_btn.setObjectName("backNav")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self.back.emit)
        col.addWidget(back_btn)
        col.addSpacing(2)

        _populate_privacy_content(col)
        col.addStretch(1)


class UpdateCompleteDialog(QDialog):
    """A small, native-looking receipt after Dictate restarts updated."""

    def __init__(self, version: str, notes: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("What's new in Dictate")
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(500, 340)
        self.resize(540, 390)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 22)
        col.setSpacing(10)
        title = QLabel("Dictate was updated")
        title.setProperty("role", "header")
        col.addWidget(title)
        version_label = QLabel(f"Version {version} is ready to use.")
        version_label.setProperty("role", "desc")
        col.addWidget(version_label)
        heading = QLabel("WHAT'S NEW")
        heading.setProperty("role", "section")
        col.addWidget(heading)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(notes.strip() or "Dictate has the latest improvements and fixes.")
        body.setMinimumHeight(150)
        col.addWidget(body, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        done = QPushButton("Done")
        done.setObjectName("apply")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        col.addLayout(row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_native_chrome(int(self.winId()))


class VocabularyDialog(QDialog):
    """A deliberately small editor for Whisper recognition hints."""

    def __init__(self, words: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("Words I use")
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(500, 360)
        self.resize(540, 390)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 22)
        col.setSpacing(10)
        title = QLabel("Words I use")
        title.setProperty("role", "header")
        col.addWidget(title)
        intro = QLabel(
            "Add names, brands, places, or technical terms one per line. "
            "Dictate uses them only as local recognition hints."
        )
        intro.setProperty("role", "desc")
        intro.setWordWrap(True)
        col.addWidget(intro)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Northwind Studio\nCTranslate2\nSpringfield")
        self.editor.setPlainText("\n".join(words))
        self.editor.setMaximumBlockCount(config.MAX_VOCABULARY_WORDS)
        col.addWidget(self.editor, 1)

        note = QLabel(f"Up to {config.MAX_VOCABULARY_WORDS} words or short phrases.")
        note.setProperty("role", "status")
        col.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Save")
        save.setObjectName("apply")
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        col.addLayout(buttons)

    @property
    def vocabulary(self) -> list[str]:
        return config.clean_vocabulary(self.editor.toPlainText().splitlines())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_native_chrome(int(self.winId()))


class FirstRunDialog(QDialog):
    """One-time setup that teaches the core interaction and chooses a mic."""

    def __init__(self, settings: config.Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("Welcome to Dictate")
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(540, 470)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 22)
        col.setSpacing(10)
        title = QLabel("Set up Dictate")
        title.setProperty("role", "header")
        col.addWidget(title)
        intro = QLabel(
            f"Hold {settings.ptt_key.upper()} anywhere in Windows, speak, then release. "
            "Dictate transcribes locally and puts the words where you were typing. "
            "For anything longer, tap that key instead of holding it and recording "
            "stays on until you tap again."
        )
        intro.setProperty("role", "desc")
        intro.setWordWrap(True)
        col.addWidget(intro)
        col.addSpacing(6)

        self.mic_box = QComboBox()
        self.mic_box.setFixedWidth(255)
        _fill_microphone_box(self.mic_box, settings.input_device)
        col.addWidget(
            _card(
                "Microphone",
                "Choose one now, or let Windows use its current default.",
                self.mic_box,
            )
        )
        col.addWidget(
            _info_card(
                "Private by design",
                "Audio is captured only while the talk key is held and transcription "
                "runs on this PC. Dictate does not save recordings or upload your words.",
            )
        )
        col.addWidget(
            _info_card(
                "Lives in the tray",
                f"Use {settings.settings_hotkey.upper()} for Settings. Right-click "
                "the tray icon to load or "
                "unload the speech model, or to quit Dictate.",
            )
        )
        col.addStretch(1)

        row = QHBoxLayout()
        privacy_btn = QPushButton("Privacy")
        privacy_btn.setObjectName("link")
        privacy_btn.clicked.connect(self._show_privacy)
        row.addWidget(privacy_btn)
        row.addStretch(1)
        start_btn = QPushButton("Start using Dictate")
        start_btn.setObjectName("apply")
        start_btn.clicked.connect(self.accept)
        row.addWidget(start_btn)
        col.addLayout(row)

    @property
    def input_device(self) -> str:
        return self.mic_box.currentData() or ""

    def _show_privacy(self) -> None:
        PrivacyDialog(self).exec()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_native_chrome(int(self.winId()))


class SettingsWindow(QWidget):
    changed = Signal(object)  # emits the new Settings
    capture_active = Signal(bool)  # tells the global listener to stand down
    margin_preview = Signal(int)  # live "Bar position" value while the slider is being dragged

    def __init__(
        self,
        settings: config.Settings,
        engine: engine_mod.Engine,
        updater=None,
    ):
        super().__init__(None)
        self._settings = settings
        self._vocabulary = list(settings.vocabulary)
        self._engine = engine
        self._updater = updater
        self._loading = True
        self._pending_reload = False
        self._has_cuda = engine_mod.cuda_available()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_now)
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.setInterval(1500)
        self._saved_timer.timeout.connect(self._show_auto_save_message)

        self.setObjectName("root")
        self.setWindowTitle("Dictate settings")
        self.setAttribute(Qt.WA_TranslucentBackground, transparency_enabled())
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(620, 520)
        self.resize(660, 560)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        self._build()
        self._paint_chevron(system_is_dark())
        # winId() forces the native handle into existence, which is what
        # apply_native_chrome() needs -- previously this window's first open
        # had no dark title bar or rounded corners at all, only gaining them
        # after set_theme() ran once from a live Windows theme change. Not
        # something this pass set out to fix, but the same gap would have
        # silently swallowed the new Acrylic backdrop below too.
        apply_native_chrome(int(self.winId()))
        self._loading = False

    def set_theme(self, dark: bool) -> None:
        """Refresh an already-open Settings window after Windows changes
        theme OR its separate "Transparency effects" setting -- both are
        cheap to recompute together, so one refresh call covers either."""
        transparent = transparency_enabled()
        self.setAttribute(Qt.WA_TranslucentBackground, transparent)
        self.setStyleSheet(stylesheet(dark, transparent))
        apply_native_chrome(int(self.winId()), dark, transparent)
        self._paint_chevron(dark)

    def _paint_chevron(self, dark: bool) -> None:
        """The chevron is painted, not styled, so it needs the theme by hand."""
        self.advanced_btn.chevron.set_color(
            QColor("#D8D8D8") if dark else QColor("#1A1A1A")
        )

    # --- construction ---

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Privacy is a second page in the same window (Windows 11 Settings
        # style: navigate in, back arrow to return) rather than a popup --
        # see PrivacyPage. self._pages holds exactly these two.
        self._pages = QStackedWidget()
        outer.addWidget(self._pages)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pages.addWidget(scroll)

        self._privacy_page = PrivacyPage()
        self._privacy_page.back.connect(lambda: self._pages.setCurrentWidget(scroll))
        self._pages.addWidget(self._privacy_page)

        page = QWidget()
        page.setObjectName("root")
        col = QVBoxLayout(page)
        col.setContentsMargins(22, 20, 22, 22)
        col.setSpacing(8)
        scroll.setWidget(page)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_row = QHBoxLayout(hero)
        hero_row.setContentsMargins(16, 14, 16, 14)
        hero_row.setSpacing(14)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(2)
        app_title = QLabel("Dictate")
        app_title.setProperty("role", "appTitle")
        hero_copy.addWidget(app_title)
        app_desc = QLabel("Your voice stays on this PC.")
        app_desc.setProperty("role", "desc")
        hero_copy.addWidget(app_desc)
        hero_row.addLayout(hero_copy, 1)
        col.addWidget(hero)

        col.addSpacing(3)
        essential_header = QLabel("ESSENTIALS")
        essential_header.setProperty("role", "section")
        col.addWidget(essential_header)

        self.mic_box = QComboBox()
        self.mic_box.setFixedWidth(200)
        _fill_microphone_box(self.mic_box, self._settings.input_device)
        self.mic_box.currentIndexChanged.connect(self._queue_save)
        mic_row = _card("Microphone", "The microphone Dictate listens to.", self.mic_box)

        self.ptt_edit = BindingCapture(self._settings.ptt_key)
        self.ptt_edit.bindingChanged.connect(self._queue_save)
        self.ptt_edit.bindingChanged.connect(self._refresh_tap_lock_desc)
        self.ptt_edit.captureActive.connect(self.capture_active.emit)
        ptt_row = _card(
            "Hold to talk",
            "Click, then press a key or mouse button. Hold inputs together for a combination.",
            self.ptt_edit,
        )

        self.tap_lock_check = ToggleSwitch(self._settings.tap_to_lock)
        self.tap_lock_check.toggled.connect(self._queue_save)
        self.tap_lock_row = _card(
            "Tap to keep recording",
            self._tap_lock_desc(),
            self.tap_lock_check,
        )
        self.tap_lock_desc_label = self.tap_lock_row.findChild(QLabel, "desc")

        self.live_preview_check = ToggleSwitch(self._settings.live_preview_enabled)
        self.live_preview_check.toggled.connect(self._queue_save)
        self.live_preview_check.toggled.connect(self._sync_preview_controls)
        live_preview_row = _card(
            "Live transcript preview",
            "Shows smooth rolling text above the activity bar while you speak.",
            self.live_preview_check,
        )

        self.enhanced_preview_check = ToggleSwitch(
            self._settings.enhanced_preview_enabled
        )
        self.enhanced_preview_check.toggled.connect(self._queue_save)
        enhanced_preview_row = _card(
            "Enhanced preview (Alpha)",
            "Uses a separate Base model on the CPU for more responsive live updates. "
            "Needs a stronger PC and about 250 MB for the model download.",
            self.enhanced_preview_check,
        )
        enhanced_preview_row.setProperty("nested", True)
        enhanced_preview_row.layout().setContentsMargins(30, 9, 14, 9)

        self.mode_box = QComboBox()
        for value, label, _model, _device in config.TRANSCRIPTION_MODES:
            self.mode_box.addItem(label, value)
        self.mode_box.addItem("Custom (Advanced)", "custom")
        custom_item = self.mode_box.model().item(self.mode_box.findData("custom"))
        if custom_item is not None:
            custom_item.setEnabled(False)
        if not self._has_cuda:
            for value, tooltip in (
                ("faster", "Fast response needs an NVIDIA CUDA GPU."),
                ("max", "Max accuracy needs an NVIDIA CUDA GPU."),
            ):
                item = self.mode_box.model().item(self.mode_box.findData(value))
                if item is not None:
                    item.setEnabled(False)
                    item.setToolTip(tooltip)
        self.mode_box.setFixedWidth(200)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        mode_row = _card(
            "Transcription mode",
            "Everyday is the best fit for normal dictation.",
            self.mode_box,
        )
        self.mode_desc_label = mode_row.findChild(QLabel, "desc")

        self.vocabulary_btn = QPushButton()
        self.vocabulary_btn.setObjectName("secondary")
        self.vocabulary_btn.setFixedWidth(120)
        self.vocabulary_btn.clicked.connect(self._edit_vocabulary)
        self._update_vocabulary_button()
        vocabulary_row = _card(
            "Words I use",
            "Names, brands, and terms Dictate should recognize more reliably.",
            self.vocabulary_btn,
        )

        self.sleep_check = ToggleSwitch(self._settings.sleep_enabled)
        self.sleep_check.toggled.connect(self._queue_save)
        sleep_row = _card(
            "Sleep when idle",
            "Releases model memory after you stop dictating.",
            self.sleep_check,
        )

        self.sleep_slider = ValueSlider(
            [1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240],
            int(self._settings.sleep_after_minutes),
            10,
            " min",
        )
        self.sleep_slider.valueChanged.connect(self._queue_save)
        sleep_after_row = _card(
            "Sleep after",
            "Moving this shows the normal default and offers a one-click reset.",
            self.sleep_slider,
        )

        self.sound_check = ToggleSwitch(self._settings.sound_cues)
        self.sound_check.toggled.connect(self._queue_save)
        sound_row = _card(
            "Sounds",
            "A short tone when Dictate starts listening, and another when it "
            "starts turning your speech into text.",
            self.sound_check,
        )

        self.startup_check = ToggleSwitch(self._settings.start_with_windows)
        self.startup_check.toggled.connect(self._queue_save)
        startup_row = _card(
            "Start with Windows",
            "Keep Dictate ready without opening it manually.",
            self.startup_check,
        )
        col.addWidget(
            _settings_group(
                mic_row,
                ptt_row,
                self.tap_lock_row,
                live_preview_row,
                enhanced_preview_row,
                mode_row,
                vocabulary_row,
                sleep_row,
                sleep_after_row,
                sound_row,
                startup_row,
            )
        )

        self.advanced_btn = ExpanderHeader("Advanced settings")
        self.advanced_btn.toggled.connect(self._toggle_advanced)
        col.addWidget(self.advanced_btn)

        self.advanced_panel = QWidget()
        advanced_col = QVBoxLayout(self.advanced_panel)
        advanced_col.setContentsMargins(0, 0, 0, 0)
        advanced_col.setSpacing(8)

        performance_header = QLabel("PERFORMANCE")
        performance_header.setProperty("role", "section")
        advanced_col.addWidget(performance_header)

        self.device_box = QComboBox()
        for value, _desc in config.DEVICES:
            self.device_box.addItem(
                {"auto": "Automatic", "cuda": "GPU", "cpu": "CPU"}[value], value
            )
        self.device_box.setFixedWidth(150)
        self.device_box.currentIndexChanged.connect(self._on_advanced_model_changed)
        if not self._has_cuda:
            idx = self.device_box.findData("cuda")
            item = self.device_box.model().item(idx)
            if item is not None:
                item.setEnabled(False)
        device_desc = "Automatic, GPU, or CPU. GPU is fastest but uses VRAM."
        if gpu_runtime.needs_download(gpu_available=self._has_cuda):
            device_desc = (
                "Automatic, GPU, or CPU. Switching to GPU downloads the "
                "acceleration files the first time (about 1.3 GB)."
            )
        device_row = _card("Processing", device_desc, self.device_box)
        self.device_desc_label = device_row.findChild(QLabel, "desc")

        # A dedicated call-to-action for GPU acceleration, separate from the
        # Processing dropdown above: for someone who skipped the installer's
        # GPU task and never touches Processing at all, this is the only way
        # to fetch the files ahead of time instead of discovering the
        # download mid-dictation. Selecting GPU in the dropdown above starts
        # the same download automatically (see _maybe_start_gpu_download) --
        # this button exists for people who want the files without switching
        # yet. Hidden once the files are already on disk or there's no real
        # GPU to accelerate with.
        self.gpu_download_btn = QPushButton("Download now")
        self.gpu_download_btn.clicked.connect(self._start_gpu_download_clicked)
        self.gpu_download_row = _card(
            "GPU acceleration",
            "Not downloaded yet. Dictate keeps using CPU until this finishes "
            "— about 1.3 GB, downloaded once in the background.",
            self.gpu_download_btn,
        )
        self.gpu_download_desc_label = self.gpu_download_row.findChild(QLabel, "desc")
        self.gpu_download_progress = _native_progress_bar()
        self.gpu_download_progress_row = _progress_row(self.gpu_download_progress)
        self.gpu_download_row.setVisible(False)
        self.gpu_download_progress_row.setVisible(False)

        self.model_box = QComboBox()
        for value, _desc in config.MODELS:
            self.model_box.addItem(MODEL_LABELS.get(value, value), value)
        self.model_box.setFixedWidth(150)
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
        self.model_card = _card("Speech model", "Model details", self.model_box)
        self.model_desc_label = self.model_card.findChild(QLabel, "desc")

        self.open_models_btn = QPushButton("Open model folder")
        self.open_models_btn.clicked.connect(self._open_model_folder)
        model_storage_row = _card(
            "Speech-model files",
            "Stored only for Dictate, separate from other apps and downloads.",
            self.open_models_btn,
        )

        advanced_col.addWidget(
            _settings_group(
                device_row,
                self.gpu_download_row,
                self.gpu_download_progress_row,
                self.model_card,
                model_storage_row,
            )
        )

        behavior_header = QLabel("APP BEHAVIOR")
        behavior_header.setProperty("role", "section")
        advanced_col.addWidget(behavior_header)

        self.hotkey_edit = BindingCapture(self._settings.settings_hotkey)
        self.hotkey_edit.bindingChanged.connect(self._queue_save)
        self.hotkey_edit.captureActive.connect(self.capture_active.emit)
        hotkey_row = _card(
            "Open settings",
            "Click, then press a key or mouse button. Hold inputs together for a combination.",
            self.hotkey_edit,
        )

        self.visible_check = ToggleSwitch(self._settings.always_visible)
        self.visible_check.toggled.connect(self._queue_save)
        visible_row = _card(
            "Always show activity bar",
            "Off means it appears only while you are dictating.",
            self.visible_check,
        )

        self.margin_slider = ValueSlider(
            [0, 2, 4, 6, 8, 10, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96],
            self._settings.bar_margin,
            8,
            " px",
        )
        self.margin_slider.valueChanged.connect(self._queue_save)
        self.margin_slider.valueChanged.connect(self.margin_preview.emit)
        margin_row = _card(
            "Bar position",
            "Gap between the activity bar and the taskbar; 8 px is the default.",
            self.margin_slider,
        )

        self.linger_slider = ValueSlider(
            [500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000],
            int(self._settings.bar_linger_ms),
            750,
            " ms",
        )
        self.linger_slider.valueChanged.connect(self._queue_save)
        linger_row = _card(
            "Stay after finishing",
            "How long the bar stays on screen after dictation finishes, "
            "before it fades away.",
            self.linger_slider,
        )
        advanced_col.addWidget(
            _settings_group(hotkey_row, visible_row, margin_row, linger_row)
        )
        self.advanced_panel.setVisible(False)
        self.advanced_panel.setMaximumHeight(0)
        self._advanced_opacity = QGraphicsOpacityEffect(self.advanced_panel)
        self._advanced_opacity.setOpacity(0.0)
        self.advanced_panel.setGraphicsEffect(self._advanced_opacity)
        self._advanced_height_anim = QPropertyAnimation(
            self.advanced_panel, b"maximumHeight", self
        )
        self._advanced_fade_anim = QPropertyAnimation(
            self._advanced_opacity, b"opacity", self
        )
        self._advanced_chevron_anim = QPropertyAnimation(
            self.advanced_btn.chevron, b"rotation", self
        )
        self._advanced_height_anim.finished.connect(self._on_advanced_anim_finished)
        col.addWidget(self.advanced_panel)

        # Notifications section: covers every bar toast this app raises, not
        # just updates -- Dictate opening, a second launch attempt finding it
        # already open, and update results all route through the same
        # App._notify() helper in main.py. Placed before Dictate Update
        # rather than folded into that card, since it isn't update-specific.
        notifications_header = QLabel("NOTIFICATIONS")
        notifications_header.setProperty("role", "section")
        col.addWidget(notifications_header)
        self.system_notifications_check = ToggleSwitch(
            self._settings.system_notifications_enabled
        )
        self.system_notifications_check.toggled.connect(self._queue_save)
        system_notifications_row = _card(
            "Also show Windows notifications",
            "Dictate's floating bar already shows every notification it "
            "raises -- opening, an update, a second launch attempt. Turn "
            "this on to also mirror them as a Windows notification.",
            self.system_notifications_check,
        )
        col.addWidget(_settings_group(system_notifications_row))

        # Windows puts Windows Update at the end of Settings navigation. This
        # single-page app has no navigation rail, so its equivalent is the
        # final primary section -- prominent, never hidden in a footer.
        updates_header = QLabel("DICTATE UPDATE")
        updates_header.setProperty("role", "section")
        col.addWidget(updates_header)
        self.auto_update_check = ToggleSwitch(self._settings.auto_update_enabled)
        self.auto_update_check.toggled.connect(self._queue_save)
        auto_update_row = _card(
            "Check for updates automatically",
            "Dictate periodically checks its official GitHub release for a "
            "new version. Turn this off and Dictate never contacts GitHub "
            "about updates.",
            self.auto_update_check,
        )
        self.update_btn = QPushButton("Check for updates")
        self.update_btn.setObjectName("apply")
        self.update_btn.setVisible(self._updater is not None)
        self.update_btn.clicked.connect(self._check_for_updates)
        update_row = _card(
            "You're up to date",
            "Dictate checks only its official GitHub release, and never "
            "downloads a new version without you clicking to install it.",
            self.update_btn,
        )
        self.update_desc_label = update_row.findChild(QLabel, "desc")
        self.update_progress = _native_progress_bar()
        col.addWidget(
            _settings_group(auto_update_row, update_row, _progress_row(self.update_progress))
        )

        col.addStretch(1)

        # Same shared progress bar style, for the GPU-runtime/model download
        # that can follow a Transcription mode or Processing change -- lives
        # next to the "Saved" status text below rather than inside the
        # Update card, since it isn't update-related.
        self.reload_progress = _native_progress_bar()
        col.addWidget(self.reload_progress)

        bottom_row = QHBoxLayout()
        self.privacy_btn = QPushButton("Privacy")
        self.privacy_btn.setObjectName("link")
        self.privacy_btn.clicked.connect(self._show_privacy)
        bottom_row.addWidget(self.privacy_btn, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self.github_btn = QPushButton("GitHub")
        self.github_btn.setObjectName("link")
        self.github_btn.clicked.connect(self._open_github)
        bottom_row.addWidget(self.github_btn, 0, Qt.AlignLeft | Qt.AlignVCenter)
        about = QLabel(f"Version {VERSION}")
        about.setProperty("role", "status")
        bottom_row.addWidget(about)
        bottom_row.addStretch(1)
        self.save_status = QLabel("Changes save automatically")
        self.save_status.setProperty("role", "status")
        bottom_row.addWidget(self.save_status, 0, Qt.AlignRight | Qt.AlignVCenter)

        col.addLayout(bottom_row)
        self._load_widgets(self._settings)
        self._update_gpu_download_visibility()
        self._maybe_start_gpu_download()

    # --- behaviour ---

    def _model_desc(self) -> str:
        chosen = self.model_box.currentData()
        for value, desc in config.MODELS:
            if value == chosen:
                # Not .capitalize() -- that would lowercase the rest and turn
                # "~600 MB" into "~600 mb".
                return f"Bigger is more accurate and slower. {desc[0].upper()}{desc[1:]}."
        return "Bigger is more accurate and slower to run."

    def _on_model_changed(self, *_args) -> None:
        if self.model_desc_label is not None:
            self.model_desc_label.setText(self._model_desc())
        self._on_advanced_model_changed()

    def _on_advanced_model_changed(self, *_args) -> None:
        if self._loading:
            return
        self._sync_mode_from_advanced()
        self._queue_save()
        self._maybe_start_gpu_download()

    def _on_mode_changed(self, *_args) -> None:
        if self._loading:
            return
        chosen = config.transcription_mode_settings(self.mode_box.currentData())
        if chosen is None:
            return
        model_size, device = chosen
        blocked_device = self.device_box.blockSignals(True)
        blocked_model = self.model_box.blockSignals(True)
        self.device_box.setCurrentIndex(max(0, self.device_box.findData(device)))
        self.model_box.setCurrentIndex(max(0, self.model_box.findData(model_size)))
        self.device_box.blockSignals(blocked_device)
        self.model_box.blockSignals(blocked_model)
        if self.model_desc_label is not None:
            self.model_desc_label.setText(self._model_desc())
        self._update_mode_desc()
        self._queue_save()
        self._maybe_start_gpu_download()

    def _sync_mode_from_advanced(self) -> None:
        mode = config.transcription_mode_for(
            self.model_box.currentData(), self.device_box.currentData()
        )
        blocked = self.mode_box.blockSignals(True)
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(mode)))
        self.mode_box.blockSignals(blocked)
        self._update_mode_desc()

    def _update_mode_desc(self) -> None:
        descriptions = {
            "balanced": "Best for everyday dictation without using the GPU.",
            "faster": "Uses the GPU for the shortest wait after you release the key.",
            "accurate": "Uses a larger model for names, accents, and difficult audio.",
            "max": "Uses Dictate's largest model for the hardest audio. NVIDIA GPU required.",
            "custom": "Advanced model and processing choices are active.",
        }
        if self.mode_desc_label is not None:
            self.mode_desc_label.setText(
                descriptions.get(self.mode_box.currentData(), descriptions["balanced"])
            )

    def _maybe_start_gpu_download(self) -> None:
        """Kick off the GPU-runtime download the moment a choice needs it,
        rather than waiting for a dictation to discover that lazily.

        Safe to call anytime, including at window construction (an install-
        time "gpuaccel" task seeds device=cuda before Settings has ever been
        opened) -- Engine.start_gpu_download() is itself a no-op unless
        there's a real GPU and the files are actually missing. Reached via
        getattr rather than a direct call so a test double or older Engine
        stub without the method just no-ops instead of crashing the slot.
        """
        start = getattr(self._engine, "start_gpu_download", None)
        if start is not None and self._has_cuda and self.device_box.currentData() in (
            "cuda",
            "auto",
        ):
            start()
        self._update_gpu_download_visibility()

    def _start_gpu_download_clicked(self) -> None:
        self.gpu_download_btn.setEnabled(False)
        self.gpu_download_btn.setText("Downloading…")
        start = getattr(self._engine, "start_gpu_download", None)
        if start is not None:
            start()

    def _update_gpu_download_visibility(self) -> None:
        """Show the dedicated GPU row only while there's something to act
        on: a real GPU with files not yet on disk. Once they land, this row
        has nothing left to offer -- Processing above already covers it."""
        visible = self._has_cuda and gpu_runtime.needs_download(gpu_available=True)
        self.gpu_download_row.setVisible(visible)
        self.gpu_download_progress_row.setVisible(visible)

    def _refresh_gpu_download_status(self) -> None:
        """Live progress for a GPU-runtime download in flight, polled the
        same reactive way as last_status/updater's own last_status -- see
        refresh_status()'s own docstring for why polling beats a dedicated
        cross-thread signal for this app's status surfaces."""
        if not self._has_cuda:
            return
        downloading, progress = getattr(self._engine, "gpu_status", (False, None))
        needs = gpu_runtime.needs_download(gpu_available=True)
        visible = needs or downloading
        self.gpu_download_row.setVisible(visible)
        self.gpu_download_progress_row.setVisible(visible)
        self.gpu_download_progress.setVisible(downloading)
        if downloading:
            self.gpu_download_btn.setEnabled(False)
            self.gpu_download_btn.setText("Downloading…")
            if progress is not None:
                self.gpu_download_progress.setRange(0, 100)
                self.gpu_download_progress.setValue(int(progress * 100))
            else:
                self.gpu_download_progress.setRange(0, 0)
            if self.gpu_download_desc_label is not None:
                pct = f" {int(progress * 100)}%" if progress is not None else ""
                self.gpu_download_desc_label.setText(
                    f"Downloading{pct} — Dictate keeps using CPU until this finishes."
                )
        elif needs:
            self.gpu_download_btn.setEnabled(True)
            self.gpu_download_btn.setText("Download now")
            if self.gpu_download_desc_label is not None:
                self.gpu_download_desc_label.setText(
                    "Not downloaded yet. Dictate keeps using CPU until this "
                    "finishes — about 1.3 GB, downloaded once in the background."
                )

    def _update_vocabulary_button(self) -> None:
        count = len(self._vocabulary)
        self.vocabulary_btn.setText("Add words" if count == 0 else f"Edit ({count})")

    def _edit_vocabulary(self) -> None:
        dialog = VocabularyDialog(self._vocabulary, self)
        if dialog.exec():
            words = dialog.vocabulary
            if words != self._vocabulary:
                self._vocabulary = words
                self._update_vocabulary_button()
                self._queue_save()

    def _toggle_advanced(self, open_: bool) -> None:
        """Grow/shrink the Advanced panel with a fade and a rotating chevron.

        The same Fluent point-to-point motion (entrances decelerate, exits
        accelerate) the floating bar already uses for its own reveals --
        reusing bar.py's ENTER_MS/EXIT_MS/FLUENT_DECELERATE/FLUENT_ACCELERATE
        rather than inventing separate timing for this window.

        All three run on one curve and one duration, because they are one
        gesture. The chevron turning at a different rate than the panel opens
        is the thing that makes a hand-built expander feel unlike the real
        control, even when every individual piece is right.
        """
        self._advanced_height_anim.stop()
        self._advanced_fade_anim.stop()
        self._advanced_chevron_anim.stop()

        duration = ENTER_MS if open_ else EXIT_MS
        curve = FLUENT_DECELERATE if open_ else FLUENT_ACCELERATE

        if open_:
            self.advanced_panel.setVisible(True)
            end_height = self.advanced_panel.sizeHint().height()
            end_opacity = 1.0
        else:
            end_height = 0
            end_opacity = 0.0

        for anim, start, end in (
            (self._advanced_height_anim, self.advanced_panel.height(), end_height),
            (self._advanced_fade_anim, self._advanced_opacity.opacity(), end_opacity),
            (
                self._advanced_chevron_anim,
                self.advanced_btn.chevron.rotation,
                180.0 if open_ else 0.0,
            ),
        ):
            anim.setDuration(duration)
            anim.setEasingCurve(curve)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.start()

    def _on_advanced_anim_finished(self) -> None:
        # Only actually hide once collapsed -- an expand's "finished" fires
        # too, and hiding then would undo the animation it just played.
        if not self.advanced_btn.isChecked():
            self.advanced_panel.setVisible(False)

    def _show_privacy(self) -> None:
        self._pages.setCurrentWidget(self._privacy_page)

    def _open_github(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/PLEXFX/dictate"))

    def _check_for_updates(self) -> None:
        if self._updater is None or not self.auto_update_check.isChecked():
            return
        # Disabled synchronously, in this same click -- not left to
        # refresh_status() reacting later to the updater's own async,
        # cross-thread status-change signal. That round trip left a real
        # window where a second click landed on a button that hadn't
        # visibly disabled yet and got silently swallowed by the updater's
        # own busy guard, which read as the button "randomly" not doing
        # anything. Qt never delivers a second click to a widget this
        # handler already disabled -- it processes one click at a time.
        u_state, _u_detail, _u_progress = self._updater.last_status
        if u_state == updater_mod.AVAILABLE:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Downloading…")
            self._updater.start_update()
        elif u_state in (updater_mod.CHECKING, updater_mod.DOWNLOADING, updater_mod.INSTALLING):
            return  # already busy -- the button should already be disabled
        else:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Checking…")
            self._updater.check_now(silent=False)

    def refresh_status(self) -> None:
        """Finish model-change feedback without a permanent status badge.

        Also the live surface for GPU-runtime and update download progress:
        both flow through the same LOADING-with-progress/status-detail shape
        as an ordinary model download, so this one status line covers all
        three rather than needing a separate widget per download kind.
        Updater.last_status reverts itself back to IDLE a few seconds after
        a one-shot confirmation (UP_TO_DATE/ERROR) -- see updater.py's
        _set_status -- so this can just poll it fresh each call with no
        "have I already shown this" bookkeeping of its own. AVAILABLE
        remains visible until the person clicks "Download & install".
        """
        if self._updater is not None:
            auto_update_on = self.auto_update_check.isChecked()
            u_state, u_detail, u_progress = self._updater.last_status
            self.update_btn.setEnabled(
                auto_update_on
                and u_state not in (updater_mod.CHECKING, updater_mod.DOWNLOADING, updater_mod.INSTALLING)
            )
            if u_state == updater_mod.AVAILABLE:
                self.update_btn.setText("Download & install")
            elif u_state == updater_mod.INSTALLING:
                self.update_btn.setText("Restarting…")
            elif u_state in (updater_mod.CHECKING, updater_mod.DOWNLOADING):
                self.update_btn.setText("Checking…" if u_state == updater_mod.CHECKING else "Downloading…")
            else:
                self.update_btn.setText("Check for updates")

            # Real native progress bar, not just a percentage inside the
            # description text: determinate while a size is known
            # (DOWNLOADING), indeterminate (range 0,0) for CHECKING/
            # INSTALLING, where there's real work happening but no fraction.
            busy = u_state in (updater_mod.CHECKING, updater_mod.DOWNLOADING, updater_mod.INSTALLING)
            self.update_progress.setVisible(busy)
            if busy:
                if u_state == updater_mod.DOWNLOADING and u_progress is not None:
                    self.update_progress.setRange(0, 100)
                    self.update_progress.setValue(int(u_progress * 100))
                else:
                    self.update_progress.setRange(0, 0)

            if u_state != updater_mod.IDLE:
                self.save_status.setText(u_detail)
                if self.update_desc_label is not None:
                    self.update_desc_label.setText(u_detail)
                return

            if self.update_desc_label is not None:
                if auto_update_on:
                    self.update_desc_label.setText(
                        "Dictate checks only its official GitHub release, and "
                        "verifies the download's checksum before installing it."
                    )
                else:
                    self.update_desc_label.setText(
                        "Turned off — Dictate won't check GitHub for new releases."
                    )

        self._refresh_gpu_download_status()

        state, detail, progress = self._engine.last_status
        if self._pending_reload:
            busy = state == engine_mod.LOADING and progress is not None
            self.reload_progress.setVisible(busy)
            if busy:
                self.reload_progress.setRange(0, 100)
                self.reload_progress.setValue(int(progress * 100))
                self.save_status.setText(f"Saved · {detail}")
                return
            if state == engine_mod.READY:
                self._pending_reload = False
                self.reload_progress.setVisible(False)
                self.save_status.setText("Saved")
                self._saved_timer.start()
                return
            if state == engine_mod.ERROR:
                self._pending_reload = False
                self.reload_progress.setVisible(False)
                self.save_status.setText("The speech model could not load")
                return
        else:
            self.reload_progress.setVisible(False)

    def _tap_lock_desc(self) -> str:
        """Explain the gesture using the key that is actually bound to it."""
        key = hotkeys_mod.format_combo(
            self.ptt_edit.binding() or self._settings.ptt_key or config.DEFAULT_PTT_KEY
        )
        return (
            f"Tap {key} instead of holding it and Dictate keeps recording. "
            f"Tap again to finish, or press Esc to discard it."
        )

    def _open_model_folder(self) -> None:
        """Open Dictate's private model cache, creating it if needed."""
        folder = config.model_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)
        except OSError:
            self.save_status.setText("Could not open the model folder")

    def _refresh_tap_lock_desc(self, *_args) -> None:
        if self.tap_lock_desc_label is not None:
            self.tap_lock_desc_label.setText(self._tap_lock_desc())

    def _sync_preview_controls(self, *_args) -> None:
        """Keep Enhanced preview visibly nested under its parent switch."""
        self.enhanced_preview_check.setEnabled(
            self.live_preview_check.isChecked()
        )

    def _collect_settings(self) -> config.Settings:
        return replace(
            self._settings,
            device=self.device_box.currentData(),
            model_size=self.model_box.currentData(),
            input_device=self.mic_box.currentData() or "",
            sleep_enabled=self.sleep_check.isChecked(),
            sound_cues=self.sound_check.isChecked(),
            sleep_after_minutes=self.sleep_slider.value(),
            ptt_key=self.ptt_edit.binding() or config.DEFAULT_PTT_KEY,
            tap_to_lock=self.tap_lock_check.isChecked(),
            live_preview_enabled=self.live_preview_check.isChecked(),
            enhanced_preview_enabled=self.enhanced_preview_check.isChecked(),
            settings_hotkey=self.hotkey_edit.binding() or "ctrl+alt+d",
            vocabulary=self._vocabulary,
            always_visible=self.visible_check.isChecked(),
            bar_margin=self.margin_slider.value(),
            bar_linger_ms=self.linger_slider.value(),
            start_with_windows=self.startup_check.isChecked(),
            auto_update_enabled=self.auto_update_check.isChecked(),
            system_notifications_enabled=self.system_notifications_check.isChecked(),
        ).clamped()

    def _queue_save(self, *_args) -> None:
        """Save shortly after a control changes, combining rapid adjustments."""
        if self._loading:
            return
        self.sleep_slider.setEnabled(self.sleep_check.isChecked())
        if self._updater is not None:
            self.refresh_status()
        self.save_status.setText("Saving…")
        self._save_timer.start()

    def _save_now(self) -> None:
        new = self._collect_settings()
        if asdict(new) == asdict(self._settings):
            self._show_auto_save_message()
            return
        old = self._settings

        if new.start_with_windows != old.start_with_windows:
            try:
                startup_mod.set_enabled(new.start_with_windows)
            except OSError:
                blocked = self.startup_check.blockSignals(True)
                self.startup_check.setChecked(old.start_with_windows)
                self.startup_check.blockSignals(blocked)
                new = replace(new, start_with_windows=old.start_with_windows)
                self.save_status.setText("Could not update Windows startup")
                if asdict(new) == asdict(old):
                    return

        needs_reload = (new.model_size, new.device) != (old.model_size, old.device)

        self._settings = new
        config.save(new)
        self.changed.emit(new)  # main.py wires this to engine/hotkeys/bar/tray
        self.save_status.setText("Saved")
        self._saved_timer.start()

        if needs_reload:
            self._pending_reload = True
            self.save_status.setText("Saved · Updating speech model…")
            self._engine.preload()
        else:
            self._pending_reload = False

    def _show_auto_save_message(self) -> None:
        self.save_status.setText("Changes save automatically")

    def _load_widgets(self, s: config.Settings) -> None:
        """Push settings values into the controls without staging a change."""
        self._loading = True
        self.device_box.setCurrentIndex(max(0, self.device_box.findData(s.device)))
        self.model_box.setCurrentIndex(max(0, self.model_box.findData(s.model_size)))
        if self.model_desc_label is not None:
            self.model_desc_label.setText(self._model_desc())
        self.sleep_check.setChecked(s.sleep_enabled)
        self.sleep_slider.setValue(s.sleep_after_minutes)
        self.sleep_slider.setEnabled(s.sleep_enabled)
        _fill_microphone_box(self.mic_box, s.input_device)
        self.ptt_edit.setBinding(s.ptt_key)
        self.tap_lock_check.setChecked(s.tap_to_lock)
        self.live_preview_check.setChecked(s.live_preview_enabled)
        self.enhanced_preview_check.setChecked(s.enhanced_preview_enabled)
        self._sync_preview_controls()
        self._refresh_tap_lock_desc()
        self.hotkey_edit.setBinding(s.settings_hotkey)
        self._vocabulary = list(s.vocabulary)
        self._update_vocabulary_button()
        self.visible_check.setChecked(s.always_visible)
        self.margin_slider.setValue(s.bar_margin)
        self.linger_slider.setValue(s.bar_linger_ms)
        self.startup_check.setChecked(s.start_with_windows)
        self.auto_update_check.setChecked(s.auto_update_enabled)
        self.system_notifications_check.setChecked(s.system_notifications_enabled)
        self._sync_mode_from_advanced()
        self._loading = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_native_chrome(int(self.winId()))
        _fill_microphone_box(self.mic_box, self._settings.input_device)
        self.refresh_status()

    def closeEvent(self, event) -> None:
        self.ptt_edit.cancel()
        self.hotkey_edit.cancel()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_now()
        super().closeEvent(event)
