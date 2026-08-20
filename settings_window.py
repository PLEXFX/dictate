"""Settings window, laid out like the Windows 11 Settings app.

Each option is a rounded card row: title and one line of explanation on the
left, the control on the right. That pattern is most of what makes a window
read as native, more than any individual control does.

The stable left rail opens focused Dictation, Activity bar, Appearance,
Updates, and Privacy pages. Implementation details remain available in a
collapsed Advanced section. Changes save automatically; model-affecting
changes visibly reload the engine.
"""

from __future__ import annotations

import ctypes
import os
import re
from dataclasses import asdict, replace

from PySide6.QtCore import (
    QAbstractAnimation,
    QPoint,
    Property,
    Qt,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QSize,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
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
from theme import resolve_dark, system_is_dark
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
QFrame#navRail {
    background: #1C1C1C;
    border: none;
    border-right: 1px solid #2D2D2D;
}
QLabel#navTitle { color: #FFFFFF; font-size: 14pt; font-weight: 600; }
QLabel#navCaption { color: #8F8F8F; font-size: 8pt; }
QPushButton#navItem {
    background: transparent;
    color: #DADADA;
    border: none;
    border-radius: 4px;
    padding: 9px 12px 9px 15px;
    text-align: left;
}
QPushButton#navItem:hover { background: #292929; }
QPushButton#navItem:pressed { background: #242424; }
QPushButton#navItem:checked {
    background: #303030;
    color: #FFFFFF;
    font-weight: 600;
}
QFrame#navIndicator { background: #4CC2FF; border: none; border-radius: 1px; }
QFrame#navDivider { background: #303030; border: none; max-height: 1px; }
QPushButton#navFooterLink {
    background: transparent;
    color: #BEBEBE;
    border: none;
    border-radius: 4px;
    padding: 7px 8px;
    text-align: left;
}
QPushButton#navFooterLink:hover { background: #292929; color: #FFFFFF; }
QPushButton#navFooterLink:pressed { background: #242424; }
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
QPushButton#downloadOverview {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0px;
    text-align: left;
}
QPushButton#downloadOverview[tone="accent"], QPushButton#downloadOverview[tone="available"], QPushButton#downloadOverview[tone="error"] {
    background: #292929;
    border-color: #363636;
}
QPushButton#downloadOverview:hover { background: #303030; border-color: #414141; }
QPushButton#downloadOverview:pressed { background: #252525; }
QFrame#downloadOverviewStatus {
    background: #777777;
    border: none;
    border-radius: 4px;
}
QFrame#downloadOverviewStatus[tone="success"] { background: #6CCB5F; }
QFrame#downloadOverviewStatus[tone="accent"] { background: #4CC2FF; }
QFrame#downloadOverviewStatus[tone="available"] { background: #FFB900; }
QFrame#downloadOverviewStatus[tone="error"] { background: #F1707B; }
QLabel#downloadOverviewTitle { color: #F4F4F4; font-size: 8pt; font-weight: 600; background: transparent; }
QLabel#downloadOverviewDetail { color: #AFAFAF; font-size: 8pt; background: transparent; }
QLabel#downloadOverviewChevron { color: #9A9A9A; font-size: 14pt; font-weight: 400; background: transparent; }
QProgressBar#downloadOverviewProgress { background: #484848; border: none; border-radius: 1px; min-height: 3px; max-height: 3px; }
QProgressBar#downloadOverviewProgress::chunk { background: #4CC2FF; border-radius: 1px; }
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
QFrame#navRail { background: #EDEDED; border: none; border-right: 1px solid #DDDDDD; }
QLabel#navTitle { color: #1A1A1A; font-size: 14pt; font-weight: 600; }
QLabel#navCaption { color: #666666; font-size: 8pt; }
QPushButton#navItem { background: transparent; color: #303030; border: none; border-radius: 4px; padding: 9px 12px 9px 15px; text-align: left; }
QPushButton#navItem:hover { background: #E3E3E3; }
QPushButton#navItem:pressed { background: #DADADA; }
QPushButton#navItem:checked { background: #DADADA; color: #111111; font-weight: 600; }
QFrame#navIndicator { background: #0078D4; border: none; border-radius: 1px; }
QFrame#navDivider { background: #D8D8D8; border: none; max-height: 1px; }
QPushButton#navFooterLink { background: transparent; color: #505050; border: none; border-radius: 4px; padding: 7px 8px; text-align: left; }
QPushButton#navFooterLink:hover { background: #E3E3E3; color: #111111; }
QPushButton#navFooterLink:pressed { background: #DADADA; }
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
QPushButton#downloadOverview { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0px; text-align: left; }
QPushButton#downloadOverview[tone="accent"], QPushButton#downloadOverview[tone="available"], QPushButton#downloadOverview[tone="error"] { background: #F5F5F5; border-color: #DEDEDE; }
QPushButton#downloadOverview:hover { background: #E5E5E5; border-color: #D2D2D2; }
QPushButton#downloadOverview:pressed { background: #EEEEEE; }
QFrame#downloadOverviewStatus { background: #8A8A8A; border: none; border-radius: 4px; }
QFrame#downloadOverviewStatus[tone="success"] { background: #0F7B0F; }
QFrame#downloadOverviewStatus[tone="accent"] { background: #0078D4; }
QFrame#downloadOverviewStatus[tone="available"] { background: #D97706; }
QFrame#downloadOverviewStatus[tone="error"] { background: #C42B1C; }
QLabel#downloadOverviewTitle { color: #1A1A1A; font-size: 8pt; font-weight: 600; background: transparent; }
QLabel#downloadOverviewDetail { color: #616161; font-size: 8pt; background: transparent; }
QLabel#downloadOverviewChevron { color: #6D6D6D; font-size: 14pt; font-weight: 400; background: transparent; }
QProgressBar#downloadOverviewProgress { background: #D6D6D6; border: none; border-radius: 1px; min-height: 3px; max-height: 3px; }
QProgressBar#downloadOverviewProgress::chunk { background: #0078D4; border-radius: 1px; }
QPushButton#link { background: transparent; color: #0067B8; border: none; padding: 5px 2px; }
QPushButton#link:hover { color: #004C87; text-decoration: underline; }
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


def stylesheet(dark: bool | None = None) -> str:
    dark = system_is_dark() if dark is None else dark
    return DARK_STYLE if dark else LIGHT_STYLE

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def apply_native_chrome(hwnd: int, dark: bool | None = None) -> None:
    """Apply a dark title bar and rounded corners to a solid Qt window."""
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
    """A compact Fluent slider that keeps its default value visible."""

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

        self.value_label = QLabel()
        self.value_label.setProperty("role", "status")
        col.addWidget(self.value_label)

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
        else:
            self.value_label.setText(
                f"{self._format(current)} · Default: {self._format(self._default)}"
            )


class SettlingScrollArea(QScrollArea):
    """A Windows-style brake at the end of a person-driven scroll gesture.

    It deliberately ignores programmatic scrollbar changes, such as jumping
    to a download detail, and only reacts after the wheel or scrollbar thumb
    comes to rest. The two-pixel follow-through is enough to acknowledge the
    end of a gesture without turning Settings into a bouncy touch interface.
    """

    _SETTLE_PX = 2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._settle_direction = 0
        self._drag_start_value: int | None = None
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(100)
        self._settle_timer.timeout.connect(self._play_scroll_settle)
        self._settle_motion = QSequentialAnimationGroup(self)
        self.verticalScrollBar().sliderPressed.connect(self._on_scrollbar_pressed)
        self.verticalScrollBar().sliderReleased.connect(self._on_scrollbar_released)

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        before = bar.value()
        self._cancel_scroll_settle()
        super().wheelEvent(event)
        self._note_scroll_delta(bar.value() - before)

    def _on_scrollbar_pressed(self) -> None:
        self._cancel_scroll_settle()
        self._drag_start_value = self.verticalScrollBar().value()

    def _on_scrollbar_released(self) -> None:
        if self._drag_start_value is not None:
            self._note_scroll_delta(self.verticalScrollBar().value() - self._drag_start_value)
        self._drag_start_value = None

    def _note_scroll_delta(self, delta: int) -> None:
        if not delta:
            return
        self._settle_direction = 1 if delta > 0 else -1
        self._settle_timer.start()

    def _cancel_scroll_settle(self) -> None:
        self._settle_timer.stop()
        if self._settle_motion.state() == QAbstractAnimation.Running:
            self._settle_motion.stop()

    def _play_scroll_settle(self) -> None:
        bar = self.verticalScrollBar()
        start = bar.value()
        end = max(bar.minimum(), min(bar.maximum(), start + self._settle_direction * self._SETTLE_PX))
        if end == start:
            return
        self._settle_motion.clear()
        for start_value, end_value, duration, curve in (
            (start, end, 65, FLUENT_DECELERATE),
            (end, start, 95, FLUENT_ACCELERATE),
        ):
            animation = QPropertyAnimation(bar, b"value", self._settle_motion)
            animation.setStartValue(start_value)
            animation.setEndValue(end_value)
            animation.setDuration(duration)
            animation.setEasingCurve(curve)
            self._settle_motion.addAnimation(animation)
        self._settle_motion.start()


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

        scroll = SettlingScrollArea()
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

        scroll = SettlingScrollArea()
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


def _release_note_row(text: str) -> QFrame:
    """One readable release-note item inside a Windows Settings group."""
    frame = QFrame()
    frame.setObjectName("settingsRow")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 9, 14, 9)
    note = QLabel(text)
    note.setProperty("role", "desc")
    note.setWordWrap(True)
    layout.addWidget(note)
    return frame


def _release_note_groups(notes: str) -> list[tuple[str, list[str]]]:
    """Turn the version-specific GitHub release body into quiet UI groups.

    GitHub sends the body belonging to the exact release the updater selected.
    Supporting its normal Markdown headings and bullets here keeps the app
    update view version-aware without maintaining a second set of notes.
    """
    groups: list[tuple[str, list[str]]] = []
    heading = "Changes in this version"
    entries: list[str] = []
    for raw_line in notes.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if entries:
                groups.append((heading, entries))
            heading = line.lstrip("#").strip() or heading
            entries = []
            continue
        is_bullet = line.startswith(("- ", "* "))
        if is_bullet:
            line = line[2:].strip()
        if line:
            # QLabel is intentionally plain text. Remove the small Markdown
            # subset GitHub release bodies commonly use instead of showing
            # raw asterisks, backticks, or link targets in the native dialog.
            line = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", line)
            line = line.replace("**", "").replace("__", "").replace("`", "")
            if is_bullet or not entries:
                entries.append(line)
            else:
                # CHANGELOG wraps long bullets for source readability. Keep
                # those continuation lines in one native row instead of
                # turning every source-code line into a separate change.
                entries[-1] = f"{entries[-1]} {line}"
    if entries:
        groups.append((heading, entries))
    return groups or [("Changes in this version", ["Dictate has the latest improvements and fixes."])]


class UpdateCompleteDialog(QDialog):
    """Reusable Windows 11-style What's New window for the current version."""

    # Emitted after the dialog has entered the event loop and had a chance to
    # paint. main.py uses this to release the standalone update splash only
    # once a person can actually see the newly updated app.
    presented = Signal()

    def __init__(
        self,
        version: str,
        notes: str,
        parent: QWidget | None = None,
        *,
        dark: bool | None = None,
    ):
        super().__init__(parent)
        self._presented = False
        self._dark = system_is_dark() if dark is None else dark
        self.setObjectName("root")
        self.setWindowTitle("What's new in Dictate")
        self.setModal(True)
        self.setStyleSheet(stylesheet(self._dark))
        self.setMinimumSize(540, 420)
        self.resize(600, 560)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 20)
        col.setSpacing(12)
        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(3)
        title = QLabel("What's new in Dictate")
        title.setProperty("role", "header")
        hero_layout.addWidget(title)
        version_label = QLabel(f"Version {version}")
        version_label.setProperty("role", "desc")
        hero_layout.addWidget(version_label)
        col.addWidget(hero)
        scroll = SettlingScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("root")
        content = QVBoxLayout(page)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(8)
        for group_heading, items in _release_note_groups(notes.strip()):
            group_label = QLabel(group_heading)
            group_label.setProperty("role", "section")
            content.addWidget(group_label)
            content.addWidget(_settings_group(*[_release_note_row(item) for item in items]))
        content.addStretch(1)
        scroll.setWidget(page)
        col.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        done = QPushButton("Close")
        done.setObjectName("apply")
        done.setDefault(True)
        done.setAccessibleName("Close What's New")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        col.addLayout(row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_native_chrome(int(self.winId()), self._dark)
        QTimer.singleShot(50, self._emit_presented)

    def _emit_presented(self) -> None:
        # showEvent proves the dialog was made visible. Still release the
        # splash if someone closes it unusually quickly during this short
        # post-paint delay; otherwise the updater could remain up until its
        # safety timeout despite the new app having already rendered.
        if self._presented:
            return
        self._presented = True
        self.presented.emit()


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
    width_preview = Signal(int)  # live activity-bar width while the slider is being dragged

    def __init__(
        self,
        settings: config.Settings,
        engine: engine_mod.Engine,
        updater=None,
        whats_new_version: str = VERSION,
        whats_new_notes: str = "",
    ):
        super().__init__(None)
        self._settings = settings
        self._vocabulary = list(settings.vocabulary)
        self._engine = engine
        self._updater = updater
        self._whats_new_version = whats_new_version
        self._whats_new_notes = whats_new_notes
        self._loading = True
        self._pending_reload = False
        self._has_cuda = engine_mod.cuda_available()
        self._system_dark = system_is_dark()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_now)
        self._current_section = "dictation"
        self.setObjectName("root")
        self.setWindowTitle("Dictate settings")
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(760, 540)
        self.resize(820, 620)
        base = QFont("Segoe UI Variable Text", 9)
        self.setFont(base if base.exactMatch() else QFont("Segoe UI", 9))

        self._build()
        self._apply_appearance()
        # winId() forces the native handle into existence, which is what
        # apply_native_chrome() needs for the initial dark title bar and
        # rounded corners rather than waiting for a later Windows theme change.
        self._loading = False

    def _selected_dark(self) -> bool:
        theme_mode = (
            self.theme_box.currentData() if hasattr(self, "theme_box") else self._settings.theme_mode
        )
        return resolve_dark(theme_mode, self._system_dark)

    def _apply_appearance(self) -> None:
        """Render the current settings choice immediately, before it saves."""
        dark = self._selected_dark()
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(stylesheet(dark))
        apply_native_chrome(int(self.winId()), dark)
        self._paint_chevron(dark)
        for toggle in self.findChildren(ToggleSwitch):
            toggle.set_dark(dark)

    def set_theme(self, system_dark: bool) -> None:
        """Apply a Windows change, respecting any local appearance override."""
        self._system_dark = system_dark
        self._apply_appearance()

    def _paint_chevron(self, dark: bool) -> None:
        """The chevron is painted, not styled, so it needs the theme by hand."""
        self.advanced_btn.chevron.set_color(
            QColor("#D8D8D8") if dark else QColor("#1A1A1A")
        )

    # --- compact download overview -----------------------------------

    def _refresh_download_overview(self) -> None:
        """Resolve all download work into one compact, truthful rail control."""
        active: list[tuple[str, str, str, float | None]] = []
        state, _detail, progress = self._engine.last_status
        if state == engine_mod.LOADING:
            detail = (
                f"{config.model_display_name(self._settings.model_size)} · {int(progress * 100)}%"
                if progress is not None
                else f"Preparing {config.model_display_name(self._settings.model_size)}"
            )
            active.append(("model", "Downloading model", detail, progress))

        downloading_gpu, gpu_progress = getattr(self._engine, "gpu_status", (False, None))
        if downloading_gpu:
            detail = (
                f"GPU acceleration · {int(gpu_progress * 100)}%"
                if gpu_progress is not None
                else "GPU acceleration · Working"
            )
            active.append(("gpu", "Installing GPU support", detail, gpu_progress))

        ready_action: tuple[str, str, str, str, str] | None = None
        if self._updater is not None:
            update_state, update_detail, update_progress = self._updater.last_status
            if update_state in (
                updater_mod.CHECKING,
                updater_mod.DOWNLOADING,
                updater_mod.VERIFYING,
                updater_mod.INSTALLING,
            ):
                if update_state == updater_mod.DOWNLOADING and update_progress is not None:
                    title = "Downloading update"
                    text = f"Dictate · {int(update_progress * 100)}%"
                elif update_state == updater_mod.DOWNLOADING:
                    title, text = "Downloading update", "Preparing download"
                elif update_state == updater_mod.CHECKING:
                    title, text = "Checking for updates", "Connecting to GitHub"
                elif update_state == updater_mod.VERIFYING:
                    title, text = "Finishing update", "Verifying before restart"
                else:
                    title, text = "Restarting Dictate", "Installing verified update"
                active.append(("update", title, text, update_progress))
            elif update_state == updater_mod.READY_TO_RESTART:
                ready_action = (
                    "update",
                    "restart_update",
                    "ready",
                    "Restart to finish",
                    "Verified and ready",
                )
            elif update_state == updater_mod.AVAILABLE:
                ready_action = (
                    "update",
                    "start_update",
                    "available",
                    "Update available",
                    "Click to download",
                )
            elif update_state == updater_mod.ERROR:
                ready_action = (
                    "update",
                    "open_update",
                    "error",
                    "Needs attention",
                    update_detail or "Open Updates for details",
                )

        if active:
            # Active transfer work owns the header. A ready update is still
            # available below, but it never hides a download that is moving.
            active.sort(key=lambda entry: {"update": 0, "model": 1, "gpu": 2}[entry[0]])
            self._download_focus, title, text, selected_progress = active[0]
            self._download_action = f"open_{self._download_focus}"
            extra = f" · +{len(active) - 1}" if len(active) > 1 else ""
            self._set_download_overview_presentation(
                "active", title, text + extra, selected_progress, tone="accent"
            )
            return

        if ready_action is not None:
            self._download_focus, self._download_action, mode, title, detail = ready_action
            tone = {
                "available": "available",
                "error": "error",
            }.get(mode, "accent")
            self._set_download_overview_presentation(mode, title, detail, tone=tone)
            return

        if self._has_cuda and gpu_runtime.needs_download(gpu_available=True):
            self._download_focus = "gpu"
            self._download_action = "open_gpu"
            self._set_download_overview_presentation(
                "ready", "GPU available", "Set up acceleration", tone="accent"
            )
        else:
            self._download_focus = "update"
            self._download_action = "open_update"
            if self._updater is not None and not self.auto_update_check.isChecked():
                self._set_download_overview_presentation(
                    "idle", "Updates paused", tone="muted"
                )
            else:
                self._set_download_overview_presentation(
                    "idle", "Up to date", tone="success"
                )

    def _set_download_overview_active(self, active: bool, tone: str) -> None:
        """Set the status color and a gentle pulse for active work only."""
        if (
            self.download_overview_status.property("active") != active
            or self.download_overview_status.property("tone") != tone
        ):
            self.download_overview_status.setProperty("active", active)
            self.download_overview_status.setProperty("tone", tone)
            self.download_overview.setProperty("tone", tone)
            style = self.download_overview_status.style()
            style.unpolish(self.download_overview_status)
            style.polish(self.download_overview_status)
            overview_style = self.download_overview.style()
            overview_style.unpolish(self.download_overview)
            overview_style.polish(self.download_overview)
        if active:
            self._download_overview_status_effect.setOpacity(1.0)
            if self._download_overview_pulse.state() != QAbstractAnimation.State.Running:
                self._download_overview_pulse.start()
        else:
            self._download_overview_pulse.stop()
            self._download_overview_status_effect.setOpacity(1.0)

    def _set_download_overview_presentation(
        self,
        mode: str,
        title: str,
        detail: str = "",
        progress: float | None = None,
        *,
        tone: str = "muted",
    ) -> None:
        """Switch the rail control between quiet, actionable, and active states."""
        previous_mode = self._download_overview_mode
        self._download_overview_mode = mode
        active = mode == "active"
        self.download_overview_title.setText(title)
        self._set_download_overview_active(active, tone)

        presentation_key = (mode, title, self._download_action)
        if presentation_key != getattr(self, "_download_overview_presentation_key", None):
            self._download_overview_presentation_key = presentation_key
            self._download_overview_copy_fade.stop()
            self._download_overview_copy_effect.setOpacity(0.55)
            self._download_overview_copy_fade.setStartValue(0.55)
            self._download_overview_copy_fade.setEndValue(1.0)
            self._download_overview_copy_fade.setDuration(150)
            self._download_overview_copy_fade.setEasingCurve(FLUENT_DECELERATE)
            self._download_overview_copy_fade.start()

        show_details = bool(detail)
        if show_details:
            self.download_overview_detail.setText(detail)
            self.download_overview_progress.setVisible(active)
            if active:
                if progress is None:
                    self.download_overview_progress.setRange(0, 0)
                else:
                    self.download_overview_progress.setRange(0, 100)
                    self.download_overview_progress.setValue(int(progress * 100))
            if self.download_overview_details.isHidden():
                self._download_overview_hide_details_after_fade = False
                self.download_overview_details.setVisible(True)
                self._download_overview_details_effect.setOpacity(0.0)
                self._download_overview_details_fade.stop()
                self._download_overview_details_fade.setStartValue(0.0)
                self._download_overview_details_fade.setEndValue(1.0)
                self._download_overview_details_fade.setDuration(150)
                self._download_overview_details_fade.setEasingCurve(FLUENT_DECELERATE)
                self._download_overview_details_fade.start()
        elif not self.download_overview_details.isHidden():
            # Fade the extra information away before the card contracts; that
            # feels like a Windows status control settling, not disappearing.
            self._download_overview_hide_details_after_fade = True
            self._download_overview_details_fade.stop()
            self._download_overview_details_fade.setStartValue(
                self._download_overview_details_effect.opacity()
            )
            self._download_overview_details_fade.setEndValue(0.0)
            self._download_overview_details_fade.setDuration(100)
            self._download_overview_details_fade.setEasingCurve(FLUENT_ACCELERATE)
            self._download_overview_details_fade.start()
        else:
            self.download_overview_details.setVisible(False)

        sizes = {
            "idle": QSize(156, 40),
            "ready": QSize(156, 58),
            "available": QSize(156, 58),
            "error": QSize(156, 58),
            "active": QSize(156, 68),
        }
        target = sizes[mode]
        if self._download_overview_size_target != target:
            if previous_mode == "active" and not active:
                QTimer.singleShot(105, lambda: self._animate_download_overview_size(mode, target))
            else:
                self._animate_download_overview_size(mode, target)

    def _finish_download_overview_details_fade(self) -> None:
        if self._download_overview_hide_details_after_fade:
            self.download_overview_details.setVisible(False)
            self.download_overview_progress.setVisible(False)

    def _animate_download_overview_size(self, mode: str, target: QSize) -> None:
        """Animate the rail control between its idle and active footprints."""
        if self._download_overview_mode != mode:
            return
        current = self.download_overview.size()
        if current == target:
            self.download_overview.setFixedSize(target)
            return
        self._download_overview_size_target = target
        self._download_overview_size_anim.stop()
        self._download_overview_size_anim.clear()
        self.download_overview.setMinimumSize(current)
        self.download_overview.setMaximumSize(current)
        for property_name in (b"minimumSize", b"maximumSize"):
            animation = QPropertyAnimation(self.download_overview, property_name, self)
            animation.setStartValue(current)
            animation.setEndValue(target)
            animation.setDuration(180)
            animation.setEasingCurve(FLUENT_DECELERATE)
            self._download_overview_size_anim.addAnimation(animation)
        self._download_overview_size_anim.start()

    def _finish_download_overview_size_animation(self) -> None:
        self.download_overview.setFixedSize(self._download_overview_size_target)

    def _scroll_to_widget(self, widget: QWidget) -> None:
        """Glide the Settings viewport to a detailed download control."""
        # A queued pass lets an expanding Advanced panel settle its layout
        # before its child's y coordinate and the scrollbar maximum are read.
        def start() -> None:
            bar = self._scroll.verticalScrollBar()
            top = widget.mapTo(self._scroll.widget(), widget.rect().topLeft()).y() - 12
            end = max(bar.minimum(), min(bar.maximum(), top))
            self._download_scroll_anim.stop()
            self._download_scroll_anim.setStartValue(bar.value())
            self._download_scroll_anim.setEndValue(end)
            self._download_scroll_anim.setDuration(320)
            self._download_scroll_anim.setEasingCurve(FLUENT_DECELERATE)
            self._download_scroll_anim.start()

        QTimer.singleShot(0, start)

    def _open_download_overview(self) -> None:
        """Run the rail control's current action or open its detailed page."""
        action = self._download_action
        if action == "start_update" and self._updater is not None:
            if self._updater.start_update():
                self._set_download_overview_presentation(
                    "active", "Starting download", "Preparing update", tone="accent"
                )
            return
        if action == "restart_update" and self._updater is not None:
            if self._updater.restart_to_install():
                self._set_download_overview_presentation(
                    "active", "Finishing update", "Verifying before restart", tone="accent"
                )
            return
        focus = self._download_focus
        if focus == "model":
            self._navigate_to_section("dictation")
            self._scroll_to_widget(self.model_download_row)
            return
        if focus == "gpu":
            self._navigate_to_section("dictation")
            if not self.advanced_btn.isChecked():
                self.advanced_btn.setChecked(True)
                QTimer.singleShot(ENTER_MS + 30, lambda: self._scroll_to_widget(self.gpu_download_row))
            else:
                self._scroll_to_widget(self.gpu_download_row)
            return
        self._navigate_to_section("updates")

    # --- construction ---

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("navRail")
        nav.setFixedWidth(184)
        nav_col = QVBoxLayout(nav)
        nav_col.setContentsMargins(14, 18, 14, 16)
        nav_col.setSpacing(4)
        nav_title = QLabel("Settings")
        nav_title.setObjectName("navTitle")
        nav_col.addWidget(nav_title)
        nav_caption = QLabel("Dictate")
        nav_caption.setObjectName("navCaption")
        nav_col.addWidget(nav_caption)
        nav_col.addSpacing(14)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("dictation", "Dictation"),
            ("activity", "Activity bar"),
            ("appearance", "Appearance"),
            ("updates", "Updates"),
            ("privacy", "Privacy"),
        ):
            button = QPushButton(label)
            button.setObjectName("navItem")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            self._nav_group.addButton(button)
            self._nav_buttons[key] = button
            nav_col.addWidget(button)
        self._nav_buttons["dictation"].setChecked(True)
        self.privacy_btn = self._nav_buttons["privacy"]
        self._nav_indicator = QFrame(nav)
        self._nav_indicator.setObjectName("navIndicator")
        self._nav_indicator.setFixedSize(3, 18)
        self._nav_indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._nav_indicator.hide()
        self._nav_indicator_anim = QPropertyAnimation(
            self._nav_indicator, b"pos", self
        )
        self._nav_indicator_anim.setDuration(180)
        self._nav_indicator_anim.setEasingCurve(FLUENT_DECELERATE)
        nav_col.addStretch(1)
        self._download_overview_slot = QVBoxLayout()
        self._download_overview_slot.setContentsMargins(0, 0, 0, 0)
        self._download_overview_slot.setSpacing(0)
        nav_col.addLayout(self._download_overview_slot)
        nav_col.addSpacing(7)
        nav_divider = QFrame()
        nav_divider.setObjectName("navDivider")
        nav_divider.setFixedHeight(1)
        nav_col.addWidget(nav_divider)
        nav_col.addSpacing(5)
        self.whats_new_btn = QPushButton("What's new")
        self.whats_new_btn.setObjectName("navFooterLink")
        self.whats_new_btn.setCursor(Qt.PointingHandCursor)
        self.whats_new_btn.setToolTip(f"See what changed in Dictate {self._whats_new_version}")
        self.whats_new_btn.clicked.connect(self._show_whats_new)
        nav_col.addWidget(self.whats_new_btn)
        self.github_btn = QPushButton("GitHub")
        self.github_btn.setObjectName("navFooterLink")
        self.github_btn.setCursor(Qt.PointingHandCursor)
        self.github_btn.clicked.connect(self._open_github)
        nav_col.addWidget(self.github_btn)
        nav_col.addSpacing(5)
        self.version_label = QLabel(f"Version {VERSION}")
        self.version_label.setProperty("role", "status")
        # Footer buttons draw their text eight pixels inside the rail edge.
        # Match that optical start line so the version no longer sits left
        # of What's new and GitHub.
        self.version_label.setContentsMargins(8, 0, 0, 0)
        nav_col.addWidget(self.version_label)
        self.save_status = QLabel()
        self.save_status.setProperty("role", "status")
        self.save_status.setWordWrap(True)
        self.save_status.setVisible(False)
        nav_col.addWidget(self.save_status)
        outer.addWidget(nav)

        # Each rail destination is a real page. The rail stays still while the
        # content plane changes, matching Windows Settings' spatial model.
        self._pages = QStackedWidget()
        outer.addWidget(self._pages, 1)

        scroll = SettlingScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pages.addWidget(scroll)
        self._scroll = scroll
        self._download_scroll_anim = QPropertyAnimation(
            scroll.verticalScrollBar(), b"value", self
        )

        self._settings_page = scroll
        self._privacy_page = PrivacyPage()
        self._privacy_page.back.connect(self._hide_privacy)

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

        # One adaptive rail control keeps app updates, model downloads, and
        # GPU setup visible without turning the page header into a toolbar.
        self._download_focus = "update"
        self._download_action = "open_update"
        self._download_overview_mode = "idle"
        self.download_overview = QPushButton()
        self.download_overview.setObjectName("downloadOverview")
        self.download_overview.setCursor(Qt.PointingHandCursor)
        self.download_overview.setFixedSize(156, 40)
        self.download_overview.setAccessibleName("Downloads and updates")
        self.download_overview.setToolTip(
            "See current download and update activity, or open its detailed control."
        )
        self._download_overview_size_target = QSize(156, 40)
        self._download_overview_size_anim = QParallelAnimationGroup(self)
        self._download_overview_size_anim.finished.connect(
            self._finish_download_overview_size_animation
        )

        download_copy = QHBoxLayout(self.download_overview)
        download_copy.setContentsMargins(10, 7, 8, 7)
        download_copy.setSpacing(8)
        self.download_overview_status = QFrame()
        self.download_overview_status.setObjectName("downloadOverviewStatus")
        self.download_overview_status.setProperty("active", False)
        self.download_overview_status.setProperty("tone", "muted")
        self.download_overview_status.setFixedSize(8, 8)
        self.download_overview_status.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._download_overview_status_effect = QGraphicsOpacityEffect(
            self.download_overview_status
        )
        self._download_overview_status_effect.setOpacity(1.0)
        self.download_overview_status.setGraphicsEffect(self._download_overview_status_effect)
        self._download_overview_pulse = QSequentialAnimationGroup(self)
        for start, end, easing in (
            (0.48, 1.0, FLUENT_DECELERATE),
            (1.0, 0.48, FLUENT_ACCELERATE),
        ):
            pulse = QPropertyAnimation(self._download_overview_status_effect, b"opacity", self)
            pulse.setStartValue(start)
            pulse.setEndValue(end)
            pulse.setDuration(760)
            pulse.setEasingCurve(easing)
            self._download_overview_pulse.addAnimation(pulse)
        self._download_overview_pulse.setLoopCount(-1)
        download_copy.addWidget(self.download_overview_status, 0, Qt.AlignVCenter)
        self.download_overview_copy = QWidget()
        self.download_overview_copy.setAttribute(Qt.WA_TransparentForMouseEvents)
        overview_copy = QVBoxLayout(self.download_overview_copy)
        overview_copy.setContentsMargins(0, 0, 0, 0)
        overview_copy.setSpacing(1)
        self._download_overview_copy_effect = QGraphicsOpacityEffect(
            self.download_overview_copy
        )
        self._download_overview_copy_effect.setOpacity(1.0)
        self.download_overview_copy.setGraphicsEffect(self._download_overview_copy_effect)
        self._download_overview_copy_fade = QPropertyAnimation(
            self._download_overview_copy_effect, b"opacity", self
        )
        self.download_overview_title = QLabel("Up to date")
        self.download_overview_title.setObjectName("downloadOverviewTitle")
        self.download_overview_title.setAttribute(Qt.WA_TransparentForMouseEvents)
        overview_copy.addWidget(self.download_overview_title)
        self.download_overview_details = QWidget()
        details_copy = QVBoxLayout(self.download_overview_details)
        details_copy.setContentsMargins(0, 0, 0, 0)
        details_copy.setSpacing(4)
        self.download_overview_detail = QLabel("Checking download status…")
        self.download_overview_detail.setObjectName("downloadOverviewDetail")
        self.download_overview_detail.setAttribute(Qt.WA_TransparentForMouseEvents)
        details_copy.addWidget(self.download_overview_detail)
        self.download_overview_progress = _native_progress_bar()
        self.download_overview_progress.setObjectName("downloadOverviewProgress")
        self.download_overview_progress.setFixedHeight(3)
        details_copy.addWidget(self.download_overview_progress)
        self.download_overview_details.setVisible(False)
        self._download_overview_details_effect = QGraphicsOpacityEffect(
            self.download_overview_details
        )
        self._download_overview_details_effect.setOpacity(1.0)
        self.download_overview_details.setGraphicsEffect(self._download_overview_details_effect)
        self._download_overview_hide_details_after_fade = False
        self._download_overview_details_fade = QPropertyAnimation(
            self._download_overview_details_effect, b"opacity", self
        )
        self._download_overview_details_fade.finished.connect(
            self._finish_download_overview_details_fade
        )
        overview_copy.addWidget(self.download_overview_details)
        download_copy.addWidget(self.download_overview_copy, 1)
        overview_chevron = QLabel("›")
        overview_chevron.setObjectName("downloadOverviewChevron")
        overview_chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        download_copy.addWidget(overview_chevron, 0, Qt.AlignVCenter)
        self.download_overview.clicked.connect(self._open_download_overview)
        self._download_overview_slot.addWidget(self.download_overview)
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

        self.mode_box = QComboBox()
        for value, label, _model, _device in config.TRANSCRIPTION_MODES:
            self.mode_box.addItem(label, value)
        self.mode_box.addItem("Custom (Advanced)", "custom")
        custom_item = self.mode_box.model().item(self.mode_box.findData("custom"))
        if custom_item is not None:
            custom_item.setEnabled(False)
        self.mode_box.setFixedWidth(200)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        mode_row = _card(
            "Transcription mode",
            "Everyday is the best fit for normal dictation.",
            self.mode_box,
        )
        self.mode_desc_label = mode_row.findChild(QLabel, "desc")

        # This row appears for the first local download as well as an
        # intentional model change. It sits in Essentials so a new user sees
        # the real percentage immediately, without hunting in Advanced.
        self.model_download_progress = _native_progress_bar()
        self.model_download_row = _card(
            "Speech model download",
            "Preparing Dictate's local speech model…",
            self.model_download_progress,
        )
        self.model_download_desc_label = self.model_download_row.findChild(QLabel, "desc")
        self.model_download_row.setVisible(False)

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
            "Moving this shows the normal default value.",
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
        self.essentials_group = _settings_group(
            mic_row,
            ptt_row,
            self.tap_lock_row,
            mode_row,
            self.model_download_row,
            vocabulary_row,
            sleep_row,
            sleep_after_row,
            sound_row,
            startup_row,
        )
        col.addWidget(self.essentials_group)

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
        self._update_gpu_choices()
        device_desc = "Automatic, GPU, or CPU. GPU is fastest but uses VRAM."
        if gpu_runtime.needs_download(gpu_available=self._has_cuda):
            device_desc = (
                "Automatic stays on CPU until GPU acceleration is installed. "
                "Download now downloads the files (about 1.3 GB)."
            )
        device_row = _card("Processing", device_desc, self.device_box)
        self.device_desc_label = device_row.findChild(QLabel, "desc")

        # A dedicated call-to-action for GPU acceleration, separate from the
        # Processing dropdown above: if the GPU files were skipped during
        # installation, GPU-only choices stay greyed out until this explicit
        # download finishes. That prevents a first dictation from unexpectedly
        # starting a 1.3 GB download or pretending it is already on GPU.
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

        behavior_header = QLabel("SHORTCUTS")
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

        advanced_col.addWidget(_settings_group(hotkey_row))
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
        # Keep the panel's geometry, contents, and affordance on one clock.
        # Starting them separately can expose a one-frame mismatch while the
        # layout is catching up, which reads as a hitch rather than a reveal.
        self._advanced_motion = QParallelAnimationGroup(self)
        self._advanced_motion.addAnimation(self._advanced_height_anim)
        self._advanced_motion.addAnimation(self._advanced_fade_anim)
        self._advanced_motion.addAnimation(self._advanced_chevron_anim)
        self._advanced_motion.finished.connect(self._on_advanced_anim_finished)
        col.addWidget(self.advanced_panel)

        activity_header = QLabel("ACTIVITY BAR")
        activity_header.setProperty("role", "section")
        col.addWidget(activity_header)
        self.activity_header = activity_header

        self.width_slider = ValueSlider(
            [180, 190, 200, 210, 220, 240, 260, 280],
            int(self._settings.bar_width),
            200,
            " px",
        )
        self.width_slider.valueChanged.connect(self._queue_save)
        self.width_slider.valueChanged.connect(self.width_preview.emit)
        width_row = _card(
            "Bar width",
            "Adjust the activity bar without changing the waveform behavior.",
            self.width_slider,
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
            "Distance from taskbar",
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
        self.activity_group = _settings_group(
            width_row, visible_row, margin_row, linger_row
        )
        col.addWidget(self.activity_group)

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
        self.notifications_group = _settings_group(system_notifications_row)
        col.addWidget(self.notifications_group)

        appearance_header = QLabel("APPEARANCE")
        appearance_header.setProperty("role", "section")
        col.addWidget(appearance_header)
        self.appearance_header = appearance_header
        self.theme_box = QComboBox()
        self.theme_box.addItem("Use Windows setting", "system")
        self.theme_box.addItem("Light", "light")
        self.theme_box.addItem("Dark", "dark")
        self.theme_box.setFixedWidth(190)
        self.theme_box.currentIndexChanged.connect(self._on_appearance_changed)
        theme_row = _card(
            "Color mode",
            "Follow Windows, or keep Dictate light or dark.",
            self.theme_box,
        )

        self.appearance_group = _settings_group(theme_row)
        col.addWidget(self.appearance_group)

        # Updates gets a dedicated rail destination, so the primary action is
        # prominent without being buried in a long general-settings document.
        updates_header = QLabel("DICTATE UPDATE")
        updates_header.setProperty("role", "section")
        col.addWidget(updates_header)
        self.updates_header = updates_header
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
        self.updates_group = _settings_group(
            auto_update_row, update_row, _progress_row(self.update_progress)
        )
        col.addWidget(self.updates_group)

        col.addStretch(1)

        # Same shared progress bar style, for the GPU-runtime/model download
        # that can follow a Transcription mode or Processing change -- lives
        # next to the "Saved" status text below rather than inside the
        # Update card, since it isn't update-related.
        self.reload_progress = _native_progress_bar()
        col.addWidget(self.reload_progress)

        def finish_page(
            page_scroll: QScrollArea,
            page_layout: QVBoxLayout,
            title: QLabel,
            heading: str,
            description: str,
            *widgets: QWidget,
        ) -> None:
            title.setText(heading)
            title.setProperty("role", "appTitle")
            title.style().unpolish(title)
            title.style().polish(title)
            page_layout.addWidget(title)
            copy = QLabel(description)
            copy.setProperty("role", "desc")
            copy.setWordWrap(True)
            page_layout.addWidget(copy)
            page_layout.addSpacing(12)
            for widget in widgets:
                page_layout.addWidget(widget)
            page_layout.addStretch(1)
            self._pages.addWidget(page_scroll)

        def new_page() -> tuple[QScrollArea, QVBoxLayout]:
            page_scroll = SettlingScrollArea()
            page_scroll.setWidgetResizable(True)
            page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            page_root = QWidget()
            page_root.setObjectName("root")
            page_layout = QVBoxLayout(page_root)
            page_layout.setContentsMargins(26, 24, 26, 26)
            page_layout.setSpacing(8)
            page_scroll.setWidget(page_root)
            return page_scroll, page_layout

        self._activity_page, activity_col = new_page()
        finish_page(
            self._activity_page,
            activity_col,
            self.activity_header,
            "Activity bar",
            "Choose how the compact waveform appears and leaves the screen.",
            self.activity_group,
        )

        self._appearance_page, appearance_col = new_page()
        finish_page(
            self._appearance_page,
            appearance_col,
            self.appearance_header,
            "Appearance",
            "Keep Dictate aligned with Windows and choose where notifications appear.",
            self.appearance_group,
            notifications_header,
            self.notifications_group,
        )

        self._updates_page, updates_col = new_page()
        finish_page(
            self._updates_page,
            updates_col,
            self.updates_header,
            "Updates",
            "Control when Dictate checks GitHub and install only when you choose.",
            self.updates_group,
        )
        self._pages.addWidget(self._privacy_page)

        self._page_map = {
            "dictation": self._settings_page,
            "activity": self._activity_page,
            "appearance": self._appearance_page,
            "updates": self._updates_page,
            "privacy": self._privacy_page,
        }
        self._page_order = list(self._page_map)
        self._page_effects: dict[str, QGraphicsOpacityEffect] = {}
        for key, content_page in self._page_map.items():
            effect = QGraphicsOpacityEffect(content_page)
            effect.setOpacity(1.0)
            content_page.setGraphicsEffect(effect)
            self._page_effects[key] = effect
        self._page_motion = QParallelAnimationGroup(self)
        self._page_slide = QPropertyAnimation(self._settings_page, b"pos", self)
        self._page_fade = QPropertyAnimation(
            self._page_effects["dictation"], b"opacity", self
        )
        self._page_motion.addAnimation(self._page_slide)
        self._page_motion.addAnimation(self._page_fade)
        for key, button in self._nav_buttons.items():
            button.clicked.connect(
                lambda _checked=False, section=key: self._navigate_to_section(section)
            )
        self._load_widgets(self._settings)
        self._update_gpu_download_visibility()
        self._update_gpu_download_visibility()

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

    def _update_gpu_choices(self) -> None:
        """Keep GPU-only choices unavailable until their runtime is present."""
        ready = self._has_cuda and not gpu_runtime.needs_download(gpu_available=True)
        device_item = self.device_box.model().item(self.device_box.findData("cuda"))
        if device_item is not None:
            device_item.setEnabled(ready)
            device_item.setToolTip(
                "" if ready else "Install GPU acceleration first to use this option."
            )
        for value in ("faster", "max"):
            item = self.mode_box.model().item(self.mode_box.findData(value))
            if item is not None:
                item.setEnabled(ready)
                item.setToolTip(
                    "" if ready else "Install GPU acceleration first to use this mode."
                )

    def _start_gpu_download_clicked(self) -> None:
        """Start the GPU-runtime download, and put the button back if it can't.

        ``start_gpu_download`` returns False as a no-op (no real GPU, or the
        files are already on disk). Discarding that answer left the button
        disabled and reading "Downloading…" with nothing running, and
        ``_refresh_gpu_download_status`` returns early without a GPU, so
        nothing ever restored it.
        """
        start = getattr(self._engine, "start_gpu_download", None)
        if start is None:
            return
        self.gpu_download_btn.setEnabled(False)
        self.gpu_download_btn.setText("Downloading…")
        if not start():
            self._refresh_gpu_download_status()
            if not self.gpu_download_btn.isEnabled():
                self.gpu_download_btn.setEnabled(True)
                self.gpu_download_btn.setText("Download now")

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
        self._update_gpu_choices()
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
        self._advanced_motion.stop()

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
            # maximumHeight is the value the layout is currently honoring.
            # QWidget.height() can still report the previous layout pass when
            # someone reverses the expander, which makes the panel snap.
            (
                self._advanced_height_anim,
                self.advanced_panel.maximumHeight(),
                end_height,
            ),
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
        self._advanced_motion.start()

    def _on_advanced_anim_finished(self) -> None:
        # Only actually hide once collapsed -- an expand's "finished" fires
        # too, and hiding then would undo the animation it just played.
        if not self.advanced_btn.isChecked():
            self.advanced_panel.setVisible(False)

    def _show_privacy(self) -> None:
        """Open Privacy as a real rail destination."""
        self._nav_buttons["privacy"].setChecked(True)
        self._move_nav_indicator("privacy")
        self._animate_to_page("privacy")

    def _hide_privacy(self) -> None:
        """Return to the last working page with reverse spatial motion."""
        self._nav_buttons[self._current_section].setChecked(True)
        self._move_nav_indicator(self._current_section)
        self._animate_to_page(self._current_section)

    def _move_nav_indicator(self, section: str, *, animate: bool = True) -> None:
        """Move one restrained accent mark instead of flashing button borders."""
        button = self._nav_buttons[section]
        target = QPoint(
            button.x(),
            button.y() + max(0, (button.height() - self._nav_indicator.height()) // 2),
        )
        if not animate or not self._nav_indicator.isVisible():
            self._nav_indicator_anim.stop()
            self._nav_indicator.move(target)
            self._nav_indicator.show()
            self._nav_indicator.raise_()
            return
        self._nav_indicator_anim.stop()
        self._nav_indicator_anim.setStartValue(self._nav_indicator.pos())
        self._nav_indicator_anim.setEndValue(target)
        self._nav_indicator_anim.start()
        self._nav_indicator.raise_()

    def _animate_to_page(self, section: str, *, force: bool = False) -> None:
        """Show one content page with interruption-safe spatial continuity."""
        target = self._page_map[section]
        visible = getattr(self, "_visible_section", "dictation")
        if not force and visible == section and self._pages.currentWidget() is target:
            return
        old_index = self._page_order.index(visible)
        new_index = self._page_order.index(section)
        direction = 1 if new_index >= old_index else -1

        self._page_motion.stop()
        for key, content_page in self._page_map.items():
            content_page.move(0, 0)
            self._page_effects[key].setOpacity(1.0)
        self._pages.setCurrentWidget(target)
        effect = self._page_effects[section]
        self._page_slide.setTargetObject(target)
        self._page_fade.setTargetObject(effect)
        self._page_slide.setDuration(210)
        self._page_slide.setEasingCurve(FLUENT_DECELERATE)
        self._page_slide.setStartValue(QPoint(16 * direction, 0))
        self._page_slide.setEndValue(QPoint(0, 0))
        self._page_fade.setDuration(160)
        self._page_fade.setEasingCurve(FLUENT_DECELERATE)
        self._page_fade.setStartValue(0.58)
        self._page_fade.setEndValue(1.0)
        self._visible_section = section
        self._page_motion.start()

    def _navigate_to_section(self, section: str) -> None:
        """Navigate like Windows Settings: stable rail, distinct content pages."""
        if section == "privacy":
            self._show_privacy()
            return
        self._current_section = section
        self._nav_buttons[section].setChecked(True)
        self._move_nav_indicator(section)
        self._animate_to_page(section)

    def _open_github(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/PLEXFX/dictate"))

    def _show_whats_new(self) -> None:
        """Open the current release notes without leaving Settings."""
        dialog = UpdateCompleteDialog(
            self._whats_new_version,
            self._whats_new_notes,
            self,
            dark=self._selected_dark(),
        )
        dialog.exec()

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
        # Each branch puts the button back itself when the updater declines.
        # Every one of these calls can legitimately no-op (the background
        # cadence holding the same busy lock is the common case), and a
        # declined call produces no status change to react to, so a button
        # disabled on click would otherwise sit there reading "Checking…"
        # until something unrelated refreshed it.
        u_state, _u_detail, _u_progress = self._updater.last_status
        if u_state == updater_mod.AVAILABLE:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Downloading…")
            if not self._updater.start_update():
                self.refresh_status()
        elif u_state == updater_mod.READY_TO_RESTART:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Verifying…")
            if not self._updater.restart_to_install():
                self.refresh_status()
        elif u_state in (
            updater_mod.CHECKING,
            updater_mod.DOWNLOADING,
            updater_mod.VERIFYING,
            updater_mod.INSTALLING,
        ):
            return  # already busy -- the button should already be disabled
        else:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Checking…")
            if not self._updater.check_now(silent=False):
                self.refresh_status()

    def refresh_status(self) -> None:
        """Finish model-change feedback without a permanent status badge.

        Also the live surface for GPU-runtime and update download progress:
        both flow through the same LOADING-with-progress/status-detail shape
        as an ordinary model download, so this one status line covers all
        three rather than needing a separate widget per download kind.
        Updater.last_status reverts itself back to IDLE a few seconds after
        a one-shot confirmation (UP_TO_DATE/ERROR) -- see updater.py's
        _set_status -- so this can just poll it fresh each call with no
        "have I already shown this" bookkeeping of its own. AVAILABLE and
        READY_TO_RESTART remain visible until the person acts.
        """
        self._refresh_download_overview()
        if self._updater is not None:
            auto_update_on = self.auto_update_check.isChecked()
            u_state, u_detail, u_progress = self._updater.last_status
            update_busy = u_state in (
                updater_mod.CHECKING,
                updater_mod.DOWNLOADING,
                updater_mod.VERIFYING,
                updater_mod.INSTALLING,
            )
            self.update_btn.setEnabled(
                u_state == updater_mod.READY_TO_RESTART
                or (auto_update_on and not update_busy)
            )
            if u_state == updater_mod.AVAILABLE:
                self.update_btn.setText("Download update")
            elif u_state == updater_mod.READY_TO_RESTART:
                self.update_btn.setText("Restart now")
            elif u_state == updater_mod.VERIFYING:
                self.update_btn.setText("Verifying…")
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
            self.update_progress.setVisible(update_busy)
            if update_busy:
                if u_state == updater_mod.DOWNLOADING and u_progress is not None:
                    self.update_progress.setRange(0, 100)
                    self.update_progress.setValue(int(u_progress * 100))
                else:
                    self.update_progress.setRange(0, 0)

            if u_state != updater_mod.IDLE:
                self._set_status(u_detail)
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
        # This also covers Dictate's first-run background download. It is not
        # a Settings-triggered reload yet, but the person should see exactly
        # the same real percentage if they open Settings while it is running.
        busy = state == engine_mod.LOADING and progress is not None
        self.model_download_row.setVisible(busy)
        self.reload_progress.setVisible(busy)
        if busy:
            self.reload_progress.setRange(0, 100)
            self.reload_progress.setValue(int(progress * 100))
            self.model_download_progress.setRange(0, 100)
            self.model_download_progress.setValue(int(progress * 100))
            if self.model_download_desc_label is not None:
                self.model_download_desc_label.setText(detail)
            self._set_status(detail)
            return

        self.model_download_row.setVisible(False)

        if self._pending_reload:
            if state == engine_mod.READY:
                self._pending_reload = False
                self.reload_progress.setVisible(False)
                self._clear_status()
                return
            if state == engine_mod.ERROR:
                self._pending_reload = False
                self.reload_progress.setVisible(False)
                self._set_status("The speech model could not load")
                return
        else:
            self.reload_progress.setVisible(False)
            self._clear_status()

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
            self._set_status("Could not open the model folder")

    def _refresh_tap_lock_desc(self, *_args) -> None:
        if self.tap_lock_desc_label is not None:
            self.tap_lock_desc_label.setText(self._tap_lock_desc())

    def _on_appearance_changed(self, *_args) -> None:
        if self._loading:
            return
        self._apply_appearance()
        self._queue_save()

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
            settings_hotkey=(
                self.hotkey_edit.binding() or config.DEFAULT_SETTINGS_HOTKEY
            ),
            vocabulary=self._vocabulary,
            always_visible=self.visible_check.isChecked(),
            bar_width=self.width_slider.value(),
            bar_margin=self.margin_slider.value(),
            bar_linger_ms=self.linger_slider.value(),
            start_with_windows=self.startup_check.isChecked(),
            auto_update_enabled=self.auto_update_check.isChecked(),
            system_notifications_enabled=self.system_notifications_check.isChecked(),
            theme_mode=self.theme_box.currentData(),
        ).clamped()

    def _queue_save(self, *_args) -> None:
        """Save shortly after a control changes, combining rapid adjustments."""
        if self._loading:
            return
        self.sleep_slider.setEnabled(self.sleep_check.isChecked())
        if self._updater is not None:
            self.refresh_status()
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
                self._set_status("Could not update Windows startup")
                if asdict(new) == asdict(old):
                    return

        needs_reload = (new.model_size, new.device) != (old.model_size, old.device)

        self._settings = new
        config.save(new)
        self.changed.emit(new)  # main.py wires this to engine/hotkeys/bar/tray
        if needs_reload:
            self._pending_reload = True
            self._set_status("Updating speech model…")
            self._engine.preload()
        else:
            self._pending_reload = False
            self._clear_status()

    def _show_auto_save_message(self) -> None:
        self._clear_status()

    def _set_status(self, text: str) -> None:
        """Show the rail status only for live work or an actionable failure."""
        self.save_status.setText(text)
        self.save_status.setVisible(True)

    def _clear_status(self) -> None:
        self.save_status.clear()
        self.save_status.setVisible(False)

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
        self._refresh_tap_lock_desc()
        self.hotkey_edit.setBinding(s.settings_hotkey)
        self._vocabulary = list(s.vocabulary)
        self._update_vocabulary_button()
        self.visible_check.setChecked(s.always_visible)
        self.width_slider.setValue(s.bar_width)
        self.margin_slider.setValue(s.bar_margin)
        self.linger_slider.setValue(s.bar_linger_ms)
        self.startup_check.setChecked(s.start_with_windows)
        self.auto_update_check.setChecked(s.auto_update_enabled)
        self.system_notifications_check.setChecked(s.system_notifications_enabled)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(s.theme_mode)))
        self._sync_mode_from_advanced()
        self._loading = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_appearance()
        _fill_microphone_box(self.mic_box, self._settings.input_device)
        self.refresh_status()
        visible = getattr(self, "_visible_section", "dictation")
        QTimer.singleShot(0, lambda: self._move_nav_indicator(visible, animate=False))
        self._animate_to_page(visible, force=True)

    def closeEvent(self, event) -> None:
        self.ptt_edit.cancel()
        self.hotkey_edit.cancel()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_now()
        super().closeEvent(event)
