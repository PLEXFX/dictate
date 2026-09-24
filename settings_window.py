"""Settings window, laid out like the Windows 11 Settings app.

Each option uses one responsive grouped row: title and one line of explanation
on the left, the control on the right, then stacked below the copy when space
gets tight. That pattern is most of what makes a window read as native, more
than any individual control does.

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
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QPoint,
    QPointF,
    QRectF,
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
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QBoxLayout,
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
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import audio as audio_mod
import config
import engine as engine_mod
import gpu_runtime
import hardware
import hotkeys as hotkeys_mod
import startup as startup_mod
import updater as updater_mod
from bar import ENTER_MS, EXIT_MS, FLUENT_ACCELERATE, FLUENT_DECELERATE, system_accent
from theme import resolve_dark, system_is_dark
from toggle import ToggleSwitch
from version import VERSION

DARK_STYLE = """
QWidget#root { background: #202020; }
QLabel { color: #FFFFFF; }
QLabel[role="title"] { font-size: 10pt; }
QLabel[role="desc"] { color: #B5B5B5; font-size: 9pt; }
QLabel[role="header"] { color: #FFFFFF; font-size: 11pt; font-weight: 600; }
QLabel[role="status"] { color: #B5B5B5; font-size: 9pt; }
QLabel[role="appTitle"] { color: #FFFFFF; font-size: 19pt; font-weight: 600; }
QLabel[role="section"] {
    color: #9A9A9A;
    font-size: 8pt;
    font-weight: 600;
    padding: 6px 2px 2px 2px;
    letter-spacing: 0.5px;
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
QFrame#settingsGroup[updateStatus="true"] { border-color: #424242; }
QLabel#updateMeta { color: #8F8F8F; font-size: 8pt; }

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
QComboBox::down-arrow { image: url(__CHEVRON_DOWN_DARK__); width: 10px; height: 10px; }
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
QPushButton#apply[updateState="restart"] { background: #F59E0B; color: #1A1A1A; }
QPushButton#apply[updateState="restart"]:hover { background: #FDBA3B; }
QPushButton#apply[updateState="restart"]:pressed { background: #D97706; }
QPushButton#apply[attention="true"], QPushButton#apply[attention="true"]:hover, QPushButton#secondary[attention="true"], QPushButton#secondary[attention="true"]:hover { background: #4CC2FF; color: #101010; border: 2px solid #FFFFFF; }
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
QPushButton#downloadOverview[tone="available"] { background: #302B21; border-color: #51432B; }
QPushButton#downloadOverview:focus { border-color: #4CC2FF; }
QPushButton#downloadOverview:hover { background: #303030; border-color: #414141; }
QPushButton#downloadOverview:pressed { background: #252525; }
QPushButton#downloadOverview[tone="restart"] { background: #F59E0B; border-color: #F59E0B; }
QPushButton#downloadOverview[tone="restart"]:hover { background: #FDBA3B; border-color: #FDBA3B; }
QPushButton#downloadOverview[tone="restart"]:pressed { background: #D97706; border-color: #D97706; }
QLabel#downloadOverviewTitle { color: #F4F4F4; font-size: 10pt; font-weight: 600; background: transparent; }
QLabel#downloadOverviewDetail { color: #BDBDBD; font-size: 9pt; background: transparent; }
QLabel#downloadOverviewChevron { color: #9A9A9A; font-size: 14pt; font-weight: 400; background: transparent; }
QLabel#downloadOverviewTitle[tone="restart"],
QLabel#downloadOverviewDetail[tone="restart"],
QLabel#downloadOverviewChevron[tone="restart"] { color: #1A1A1A; }
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
QLabel[role="desc"], QLabel[role="status"] { color: #626262; font-size: 9pt; }
QLabel[role="header"] { color: #1A1A1A; font-size: 11pt; font-weight: 600; }
QLabel[role="appTitle"] { color: #1A1A1A; font-size: 19pt; font-weight: 600; }
QLabel[role="section"] { color: #707070; font-size: 8pt; font-weight: 600; padding: 6px 2px 2px 2px; letter-spacing: 0.5px; }
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
QFrame#settingsGroup[updateStatus="true"] { border-color: #D2D2D2; }
QLabel#updateMeta { color: #666666; font-size: 8pt; }
QComboBox, QLineEdit, QPlainTextEdit, QPushButton#keyCapture { background: #FFFFFF; border: 1px solid #C9C9C9; border-bottom: 1px solid #AFAFAF; border-radius: 4px; padding: 5px 10px; color: #1A1A1A; min-height: 18px; selection-background-color: #0078D4; selection-color: #FFFFFF; }
QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover, QPushButton#keyCapture:hover { background: #F8F8F8; }
QComboBox:disabled, QLineEdit:disabled, QPlainTextEdit:disabled, QPushButton#keyCapture:disabled { background: #EEEEEE; color: #888888; border-color: #DDDDDD; }
QPushButton#keyCapture:focus { border-color: #0078D4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(__CHEVRON_DOWN_LIGHT__); width: 10px; height: 10px; }
QComboBox QAbstractItemView { background: #FFFFFF; border: 1px solid #C9C9C9; border-radius: 6px; color: #1A1A1A; selection-background-color: #E5F1FB; outline: none; padding: 4px; }
QPushButton#apply { background: #0078D4; color: #FFFFFF; border: none; border-radius: 4px; padding: 6px 22px; font-weight: 600; }
QPushButton#apply:hover { background: #006CBE; } QPushButton#apply:pressed { background: #005A9E; } QPushButton#apply:disabled { background: #DADADA; color: #888888; }
QPushButton#apply[updateState="restart"] { background: #B54708; color: #FFFFFF; }
QPushButton#apply[updateState="restart"]:hover { background: #963800; }
QPushButton#apply[updateState="restart"]:pressed { background: #7A2E00; }
QPushButton#apply[attention="true"], QPushButton#apply[attention="true"]:hover, QPushButton#secondary[attention="true"], QPushButton#secondary[attention="true"]:hover { background: #0078D4; color: #FFFFFF; border: 2px solid #183B58; }
QPushButton#secondary { background: #FFFFFF; color: #1A1A1A; border: 1px solid #C9C9C9; border-radius: 4px; padding: 6px 18px; }
QPushButton#secondary:hover { background: #F3F3F3; }
QPushButton#downloadOverview { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0px; text-align: left; }
QPushButton#downloadOverview[tone="accent"], QPushButton#downloadOverview[tone="available"], QPushButton#downloadOverview[tone="error"] { background: #F5F5F5; border-color: #DEDEDE; }
QPushButton#downloadOverview[tone="available"] { background: #FFF8EB; border-color: #E5D4B5; }
QPushButton#downloadOverview:focus { border-color: #0078D4; }
QPushButton#downloadOverview:hover { background: #E5E5E5; border-color: #D2D2D2; }
QPushButton#downloadOverview:pressed { background: #EEEEEE; }
QPushButton#downloadOverview[tone="restart"] { background: #B54708; border-color: #B54708; }
QPushButton#downloadOverview[tone="restart"]:hover { background: #963800; border-color: #963800; }
QPushButton#downloadOverview[tone="restart"]:pressed { background: #7A2E00; border-color: #7A2E00; }
QLabel#downloadOverviewTitle { color: #1A1A1A; font-size: 10pt; font-weight: 600; background: transparent; }
QLabel#downloadOverviewDetail { color: #616161; font-size: 9pt; background: transparent; }
QLabel#downloadOverviewChevron { color: #6D6D6D; font-size: 14pt; font-weight: 400; background: transparent; }
QLabel#downloadOverviewTitle[tone="restart"],
QLabel#downloadOverviewDetail[tone="restart"],
QLabel#downloadOverviewChevron[tone="restart"] { color: #FFFFFF; }
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


_FLUENT_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "fluent"


def _render_icon(draw, size: int, gap: int = 0) -> QIcon:
    """Keep native-size pixels at the common Windows display scales.

    ``gap`` adds transparent pixels to the right of the drawing grid. Qt gives
    a QPushButton a fixed four-pixel icon-to-text gap with no stylesheet
    property to widen it, so a wider-than-tall icon whose artwork sits on the
    left is the only way to reach Windows 11's roomier rail spacing.
    """
    icon = QIcon()
    for scale in (1.0, 1.25, 1.5, 2.0, 3.0):
        pixels = round(size * scale)
        pixmap = QPixmap(pixels + round(gap * scale), pixels)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(pixels / 20.0, pixels / 20.0)
        draw(painter)
        painter.end()
        pixmap.setDevicePixelRatio(pixels / size)
        icon.addPixmap(pixmap)
    return icon


# Windows 11's own Segoe Fluent Icons codepoints, one per rail destination, so
# every icon comes from a single family drawn on a single optical grid instead
# of a mix of stroke weights. Appearance is deliberately the Color palette
# glyph, never a gear -- a gear reads as "Settings", which is the whole window.
# https://learn.microsoft.com/windows/apps/design/iconography/segoe-fluent-icons-font
_SEGOE_GLYPHS: dict[str, str] = {
    "dictation": "\ue720",    # Microphone
    "activity": "\ue9d9",     # Diagnostic (a live trace on a surface)
    "appearance": "\ue790",   # Color (paint palette)
    "updates": "\ue895",      # Sync
    "privacy": "\ue72e",      # Lock
}

# Windows 11 draws rail glyphs at 16 px inside a 20 px slot. Filling the slot
# edge to edge, as this did before, reads noticeably heavier than the OS.
_GLYPH_PX = 16

# Transparent pixels baked onto the right of a rail icon. Qt's own icon-to-text
# gap is a hard-coded 4 px, which left the labels almost touching their glyphs;
# 8 more brings the pair to Windows 11's own rail spacing.
NAV_ICON_GAP = 8

# The rail's update notice, laid out left to right. Its wrapped copy has to be
# measured before Qt lays it out, so the width it gets is derived from these
# rather than restated as a number that silently drifts out of agreement.
_OVERVIEW_WIDTH = 180
_OVERVIEW_MARGIN_LEFT = 11
_OVERVIEW_MARGIN_RIGHT = 9
_OVERVIEW_STATUS_PX = 20
_OVERVIEW_STATUS_GAP = NAV_ICON_GAP + 4
_OVERVIEW_CHEVRON_PX = 12
_OVERVIEW_CHEVRON_GAP = 6
_OVERVIEW_COPY_WIDTH = (
    _OVERVIEW_WIDTH
    - _OVERVIEW_MARGIN_LEFT
    - _OVERVIEW_MARGIN_RIGHT
    - _OVERVIEW_STATUS_PX
    - _OVERVIEW_STATUS_GAP
    - _OVERVIEW_CHEVRON_PX
    - _OVERVIEW_CHEVRON_GAP
)


@lru_cache(maxsize=128)
def _fluent_icon(
    name: str, dark: bool, size: int = 20, color: str | None = None, gap: int = 0
) -> QIcon:
    """Render Windows 11 navigation icons without display-scale blur."""
    fill = color or ("#DADADA" if dark else "#303030")
    glyph = _SEGOE_GLYPHS.get(name)
    if glyph is not None and "Segoe Fluent Icons" in QFontDatabase.families():
        font = QFont("Segoe Fluent Icons")
        font.setPixelSize(_GLYPH_PX)
        def draw_glyph(painter):
            painter.setFont(font)
            painter.setPen(QColor(fill))
            painter.drawText(QRectF(0, 0, 20, 20), Qt.AlignCenter, glyph)
        return _render_icon(draw_glyph, size, gap)
    # Keep the bundled Microsoft SVGs as a fallback when the font is absent.
    try:
        svg = (_FLUENT_ASSET_DIR / f"{name}.svg").read_text(encoding="utf-8")
    except OSError:
        return QIcon()
    svg = svg.replace('fill="#212121"', f'fill="{fill}"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    inset = (20 - _GLYPH_PX) / 2
    box = QRectF(inset, inset, _GLYPH_PX, _GLYPH_PX)
    return _render_icon(lambda p: renderer.render(p, box), size, gap)


def _wrapped_height(label: QLabel, width: int) -> int:
    """How tall this label's text really is once wrapped to ``width``.

    Not ``heightForWidth``: these labels are given a fixed height each time the
    notice changes state, and a fixed height is what ``heightForWidth`` reports
    back, so measuring that way returned the *previous* state's height and left
    the notice sized for whatever it used to say.
    """
    return label.fontMetrics().boundingRect(
        0, 0, width, 10_000, Qt.TextWordWrap | Qt.AlignLeft, label.text()
    ).height()


@lru_cache(maxsize=128)
def _status_glyph_icon(kind: str, color: str, size: int = 20) -> QIcon:
    """Matching rounded outline symbols, in a shared 20 px drawing grid."""
    if kind == "spin":
        return _fluent_icon("updates", True, size, color)
    def draw(p):
        p.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        if kind == "check":
            p.drawEllipse(QRectF(2.75, 2.75, 14.5, 14.5))
            path.moveTo(6.25, 10)
            path.lineTo(8.75, 12.5)
            path.lineTo(13.75, 7.5)
        elif kind == "down":
            path.moveTo(10, 3)
            path.lineTo(10, 12)
            path.moveTo(6.5, 8.5)
            path.lineTo(10, 12)
            path.lineTo(13.5, 8.5)
            path.moveTo(4, 13)
            path.lineTo(4, 16.5)
            path.lineTo(16, 16.5)
            path.lineTo(16, 13)
        elif kind == "warn":
            path.moveTo(9.1, 3.5)
            path.quadTo(10, 2, 10.9, 3.5)
            path.lineTo(17.2, 14.5)
            path.quadTo(18, 16, 16.3, 16)
            path.lineTo(3.7, 16)
            path.quadTo(2, 16, 2.8, 14.5)
            path.closeSubpath()
            path.moveTo(10, 7)
            path.lineTo(10, 10.5)
            p.drawPoint(QPointF(10, 13.25))
        elif kind == "chevron":
            path.moveTo(8, 5.5)
            path.lineTo(12.5, 10)
            path.lineTo(8, 14.5)
        p.drawPath(path)
    return _render_icon(draw, size)


# Update status -> (outline glyph, dark-theme colour, light-theme colour).
_UPDATE_ICON_TONES: dict[str, tuple[str, str, str]] = {
    updater_mod.CHECKING: ("spin", "#4CC2FF", "#0078D4"),
    updater_mod.DOWNLOADING: ("down", "#4CC2FF", "#0078D4"),
    updater_mod.VERIFYING: ("spin", "#4CC2FF", "#0078D4"),
    updater_mod.INSTALLING: ("spin", "#4CC2FF", "#0078D4"),
    updater_mod.AVAILABLE: ("down", "#FFB900", "#D97706"),
    updater_mod.READY_TO_RESTART: ("spin", "#F59E0B", "#B54708"),
    updater_mod.ERROR: ("warn", "#F1707B", "#C42B1C"),
    updater_mod.UP_TO_DATE: ("check", "#6CCB5F", "#0F7B0F"),
}

# Modes share colours with the Updates page. Restart uses contrasting ink
# because its entire button is amber; GPU setup retains a download symbol.
_DOWNLOAD_OVERVIEW_ICONS: dict[str, tuple[str, str, str]] = {
    "active": ("spin", "#4CC2FF", "#0078D4"),
    "ready": ("down", "#4CC2FF", "#0078D4"),
    "available": ("down", "#FFB900", "#D97706"),
    "restart": ("spin", "#1A1A1A", "#FFFFFF"),
    "error": ("warn", "#F1707B", "#C42B1C"),
}


def _chevron_down_url(dark: bool) -> str:
    """Absolute, forward-slash file path for the QSS ``url()`` below.

    QSS's ``url()`` does not accept Windows' native backslash separators, so
    the path is normalized with ``as_posix()`` even though this is a plain
    filesystem path, not a real URL.
    """
    name = "chevron-down-dark.svg" if dark else "chevron-down-light.svg"
    return (_FLUENT_ASSET_DIR / name).as_posix()


def stylesheet(dark: bool | None = None) -> str:
    """Styling a QComboBox's drop-down subcontrol at all -- even just
    ``border: none`` -- stops Qt drawing its native arrow entirely, so both
    themes need an explicit ``down-arrow`` image or the control silently
    loses its only visual hint that it opens a list. See chevron-down-*.svg.
    """
    dark = system_is_dark() if dark is None else dark
    if dark:
        return DARK_STYLE.replace("__CHEVRON_DOWN_DARK__", _chevron_down_url(True))
    return LIGHT_STYLE.replace("__CHEVRON_DOWN_LIGHT__", _chevron_down_url(False))

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


class SettingsRow(QFrame):
    """The single row pattern used throughout Settings.

    Wide rows keep copy and control on one line. Narrow rows move the control
    below the copy, which prevents long descriptions from being squeezed or
    clipped when the window is resized.
    """

    STACK_BELOW_WIDTH = 500

    def __init__(
        self,
        title: str,
        desc: str,
        control: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(56)
        self._control = control
        self._stacked = False

        self._row = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._row.setContentsMargins(16, 10, 16, 10)
        self._row.setSpacing(16)

        self._copy = QWidget()
        self._copy.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._copy.setMinimumWidth(0)
        self.text_layout = QVBoxLayout(self._copy)
        self.text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setMinimumWidth(0)
        self.title_label.setProperty("role", "title")
        self.title_layout = QHBoxLayout()
        self.title_layout.setContentsMargins(0, 0, 0, 0)
        self.title_layout.setSpacing(8)
        self.title_layout.addWidget(self.title_label)
        self.title_layout.addStretch(1)
        self.text_layout.addLayout(self.title_layout)
        self.desc_label: QLabel | None = None
        if desc:
            self.desc_label = QLabel(desc)
            self.desc_label.setObjectName("desc")
            self.desc_label.setProperty("role", "desc")
            self.desc_label.setWordWrap(True)
            self.desc_label.setMinimumWidth(0)
            self.desc_label.setMaximumWidth(480)
            self.text_layout.addWidget(self.desc_label)
        self._row.addWidget(self._copy, 1)
        if control is not None:
            self._row.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)

    def add_detail(self, widget: QWidget) -> None:
        widget.setMinimumWidth(0)
        self.text_layout.addWidget(widget)

    def add_title_icon(self) -> QLabel:
        icon_label = QLabel()
        icon_label.setFixedSize(20, 20)
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title_layout.insertWidget(0, icon_label, 0, Qt.AlignVCenter)
        return icon_label

    @property
    def is_stacked(self) -> bool:
        return self._stacked

    def resizeEvent(self, event) -> None:
        stacked = event.size().width() < self.STACK_BELOW_WIDTH
        if stacked != self._stacked:
            self._stacked = stacked
            self._row.setDirection(
                QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight
            )
            self._row.setStretch(0, 0 if stacked else 1)
            if self._control is not None:
                alignment = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if stacked
                    else Qt.AlignRight | Qt.AlignVCenter
                )
                self._row.setAlignment(self._control, alignment)
        super().resizeEvent(event)


def _card(title: str, desc: str, control: QWidget) -> SettingsRow:
    return SettingsRow(title, desc, control)


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


def _text_row(title: str, desc: str) -> SettingsRow:
    """A read-only disclosure row -- title plus explanation, no control.

    Meant to sit inside _settings_group() so a set of related facts reads as
    one grouped list with hairline dividers, the same way the Windows
    Settings app groups related items under one card instead of stacking a
    separate floating box per fact.
    """
    return SettingsRow(title, desc)


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
    layout.setContentsMargins(16, 12, 16, 12)
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
        self.setFixedWidth(200)
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
        "Dictate runs locally with no account and no cloud transcription. "
        "Below is exactly what it accesses and the few things that leave this PC."
    )
    intro.setProperty("role", "desc")
    intro.setWordWrap(True)
    col.addWidget(intro)

    local_header = QLabel("Stays on this PC")
    local_header.setProperty("role", "section")
    col.addWidget(local_header)
    col.addWidget(
        _settings_group(
            _text_row(
                "Microphone",
                "Opens only while you hold the talk key. Audio is "
                "transcribed in memory and never saved to disk.",
            ),
            _text_row(
                "Transcription",
                "Processed locally by faster-whisper. No audio or text is "
                "uploaded to any service.",
            ),
            _text_row(
                "Last dictation",
                "The most recent result is held in memory only — never "
                "written to disk. It clears when Dictate closes.",
            ),
            _text_row(
                "Clipboard",
                "Used briefly to insert text, then your prior contents are "
                "restored. \"Copy last dictation\" does the same — your "
                "clipboard returns after 5 seconds.",
            ),
            _text_row(
                "Words I use",
                "Saved to your local settings file as recognition hints for "
                "Whisper. Never sent anywhere.",
            ),
            _text_row(
                "Your settings",
                "Saved as plain JSON at %APPDATA%\\dictate\\settings.json. "
                "No account, no sync.",
            ),
            _text_row(
                "Diagnostics",
                "Reports status and errors only — never the words you dictated.",
            ),
        )
    )

    network_header = QLabel("Leaves this PC")
    network_header.setProperty("role", "section")
    col.addWidget(network_header)
    col.addWidget(
        _settings_group(
            _text_row(
                "Speech model downloads",
                "Model files download from Hugging Face the first time you "
                "use them. No audio or text is part of the request.",
            ),
            _text_row(
                "GPU acceleration files",
                "NVIDIA CUDA runtime files download from PyPI when you "
                "switch to GPU processing. No audio or text is included.",
            ),
            _text_row(
                "Update checks",
                "Dictate asks GitHub's release page once a day whether a "
                "newer version exists. No account, hardware ID, or usage "
                "data is sent. Disable \"Check automatically\" to stop "
                "these requests entirely.",
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
        col.setContentsMargins(22, 16, 22, 20)
        col.setSpacing(8)
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
        col.setContentsMargins(22, 16, 22, 20)
        col.setSpacing(8)
        scroll.setWidget(page)

        back_btn = QPushButton("← Back")
        back_btn.setObjectName("backNav")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self.back.emit)
        col.addWidget(back_btn)

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


class VocabularyPage(QWidget):
    """Edit local recognition hints without opening a second window."""

    back = Signal()
    vocabularyChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self._loading = False

        col = QVBoxLayout(self)
        col.setContentsMargins(26, 20, 26, 26)
        col.setSpacing(8)

        back_btn = QPushButton("Back to Dictation")
        back_btn.setObjectName("backNav")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setAccessibleName("Back to Dictation settings")
        back_btn.clicked.connect(self.back.emit)
        col.addWidget(back_btn)
        col.addSpacing(2)

        title = QLabel("Words I use")
        title.setProperty("role", "appTitle")
        col.addWidget(title)
        intro = QLabel(
            "Add names, brands, places, or technical terms one per line. "
            "Dictate uses them only as local recognition hints."
        )
        intro.setProperty("role", "desc")
        intro.setWordWrap(True)
        col.addWidget(intro)
        col.addSpacing(12)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Northwind Studio\nCTranslate2\nSpringfield")
        self.editor.setMaximumBlockCount(config.MAX_VOCABULARY_WORDS)
        self.editor.setAccessibleName("Words Dictate should recognize")
        self.editor.textChanged.connect(self._on_text_changed)
        col.addWidget(self.editor, 1)

        self.note = QLabel()
        self.note.setProperty("role", "status")
        col.addWidget(self.note)
        self._refresh_note()

    @property
    def vocabulary(self) -> list[str]:
        return config.clean_vocabulary(self.editor.toPlainText().splitlines())

    def set_vocabulary(self, words: list[str]) -> None:
        self._loading = True
        self.editor.setPlainText("\n".join(words))
        self._loading = False
        self._refresh_note()

    def focus_editor(self) -> None:
        self.editor.setFocus(Qt.OtherFocusReason)

    def _refresh_note(self) -> None:
        count = len(self.vocabulary)
        noun = "entry" if count == 1 else "entries"
        self.note.setText(
            f"{count} {noun} · Up to {config.MAX_VOCABULARY_WORDS} · Changes save automatically"
        )

    def _on_text_changed(self) -> None:
        self._refresh_note()
        if not self._loading:
            self.vocabularyChanged.emit(self.vocabulary)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.back.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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
        self._hardware = hardware.probe()
        self._system_dark = system_is_dark()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_now)
        self._attention_timer = QTimer(self)
        self._attention_timer.setSingleShot(True)
        self._attention_timer.timeout.connect(self._clear_button_attention)
        self._attention_button = None
        self._current_section = "dictation"
        self.setObjectName("root")
        self.setWindowTitle("Dictate settings")
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(stylesheet())
        self.setMinimumSize(760, 540)
        self.resize(860, 740)
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
        self._apply_fluent_icons(dark)
        apply_native_chrome(int(self.winId()), dark)
        self._paint_chevron(dark)
        for toggle in self.findChildren(ToggleSwitch):
            toggle.set_dark(dark)

    def _apply_fluent_icons(self, dark: bool) -> None:
        self._refresh_nav_icons(dark)
        self._refresh_update_status_icon()
        if hasattr(self, "download_overview_status"):
            self._update_download_overview_icon(self.download_overview_status.property("tone"))

    def _refresh_nav_icons(self, dark: bool | None = None) -> None:
        """Tint the current page's own rail icon with the system accent,
        the rest a plain neutral -- the checked row already gets a bolder
        background and text weight from the stylesheet, but a QIcon is a
        baked bitmap that stylesheet ``color:`` never touches, so without
        this every icon stayed the same flat grey regardless of selection.
        Windows Settings' own rail highlights the current icon the same way.
        """
        if dark is None:
            dark = self._selected_dark()
        accent = system_accent(dark).name()
        current = getattr(self, "_visible_section", "dictation")
        for section, asset_name in getattr(self, "_nav_icon_names", {}).items():
            color = accent if section == current else None
            self._nav_buttons[section].setIcon(
                _fluent_icon(asset_name, dark, color=color, gap=NAV_ICON_GAP)
            )

    def _refresh_update_status_icon(self) -> None:
        """Recolour and reshape the Updates card's own icon for whatever
        the updater is actually reporting, instead of the same static grey
        arrow-circle for every state -- see _UPDATE_ICON_TONES.
        """
        if not hasattr(self, "update_status_icon"):
            return
        dark = self._selected_dark()
        state = self._updater.last_status[0] if self._updater is not None else None
        tones = _UPDATE_ICON_TONES.get(state)
        if tones is None:
            # IDLE (or no updater at all): mirrors the adaptive rail's own
            # idle branch -- a plain success check when checks are on,
            # otherwise the neutral rotate icon while they're paused.
            if self._updater is not None and self.auto_update_check.isChecked():
                tones = ("check", "#6CCB5F", "#0F7B0F")
            else:
                self.update_status_icon.setPixmap(_fluent_icon("updates", dark).pixmap(QSize(20, 20), self.devicePixelRatioF()))
                return
        kind, dark_color, light_color = tones
        color = dark_color if dark else light_color
        icon = (
            _fluent_icon("updates", dark, color=color)
            if kind == "spin"
            else _status_glyph_icon(kind, color)
        )
        self.update_status_icon.setPixmap(icon.pixmap(QSize(20, 20), self.devicePixelRatioF()))

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
                    "restart",
                    "Restart Dictate",
                    "Update verified and ready",
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
                "restart": "restart",
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

    def _update_download_overview_icon(self, tone: str) -> None:
        """Give the rail chip's status glyph the right shape and colour for
        its current mode -- see _DOWNLOAD_OVERVIEW_ICONS. Keyed by mode, not
        the ``tone`` argument, because "active" (busy work) and "ready" (GPU
        set-up available) share the accent tone but need different shapes.
        """
        icon_spec = _DOWNLOAD_OVERVIEW_ICONS.get(self._download_overview_mode)
        if icon_spec is None:
            self.download_overview_status.clear()
            return
        dark = self._selected_dark()
        kind, dark_color, light_color = icon_spec
        color = dark_color if dark else light_color
        icon = (
            _fluent_icon("updates", dark, color=color)
            if kind == "spin"
            else _status_glyph_icon(kind, color, size=20)
        )
        self.download_overview_status.setPixmap(icon.pixmap(QSize(20, 20), self.devicePixelRatioF()))
        chevron_color = color if self._download_overview_mode == "restart" else ("#BDBDBD" if dark else "#616161")
        self.download_overview_chevron.setPixmap(
            _status_glyph_icon("chevron", chevron_color, 12).pixmap(QSize(12, 12), self.devicePixelRatioF())
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
            overview_style = self.download_overview.style()
            overview_style.unpolish(self.download_overview)
            overview_style.polish(self.download_overview)
            copy_tone = "restart" if tone == "restart" else "default"
            for copy_widget in (
                self.download_overview_title,
                self.download_overview_detail,
                self.download_overview_chevron,
            ):
                copy_widget.setProperty("tone", copy_tone)
                copy_style = copy_widget.style()
                copy_style.unpolish(copy_widget)
                copy_style.polish(copy_widget)
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
        self._download_overview_mode = mode
        if mode == "idle":
            self._download_overview_size_anim.stop()
            self._download_overview_pulse.stop()
            self.download_overview.setVisible(False)
            self.download_overview.setAccessibleName("")
            self.download_overview.setToolTip("")
            return

        self.download_overview.setVisible(True)
        active = mode == "active"
        self.download_overview_title.setText(title)
        self._set_download_overview_active(active, tone)
        self._update_download_overview_icon(tone)
        summary = f"{title}. {detail}" if detail else title
        self.download_overview.setAccessibleName(summary)
        self.download_overview.setToolTip(summary)

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
            self.download_overview_details.setVisible(True)
        else:
            self.download_overview_details.setVisible(False)
            self.download_overview_progress.setVisible(False)

        # Measure the actual QLabel wrapping, then constrain the nested layouts.
        # A minimum-height button alone lets Qt shrink wrapped labels to fit.
        copy_width = _OVERVIEW_COPY_WIDTH
        title_height = _wrapped_height(self.download_overview_title, copy_width)
        detail_height = (
            _wrapped_height(self.download_overview_detail, copy_width) if show_details else 0
        )
        self.download_overview_title.setFixedHeight(title_height)
        self.download_overview_detail.setFixedHeight(detail_height)
        details_height = detail_height + (7 if active else 0)
        self.download_overview_details.setFixedHeight(details_height)
        copy_height = title_height + (3 + details_height if show_details else 0)
        self.download_overview_copy.setFixedHeight(copy_height)
        target = QSize(_OVERVIEW_WIDTH, max(64, 20 + copy_height))
        if self._download_overview_size_target != target:
            self._animate_download_overview_size(mode, target)

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
            animation.setDuration(200)
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

    def _clear_button_attention(self) -> None:
        self._attention_timer.stop()
        button = self._attention_button
        self._attention_button = None
        if button is not None:
            button.setProperty("attention", "false")
            button.style().unpolish(button)
            button.style().polish(button)

    def _highlight_button(self, button: QPushButton) -> None:
        """Make the destination action easy to spot for ten seconds."""
        self._clear_button_attention()
        if not button.isVisible() or not button.isEnabled():
            return
        self._attention_button = button
        button.setProperty("attention", "true")
        button.style().unpolish(button)
        button.style().polish(button)
        self._attention_timer.start(10_000)

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
                QTimer.singleShot(ENTER_MS + 30, lambda: (
                    self._scroll_to_widget(self.gpu_download_row),
                    self._highlight_button(self.gpu_download_btn),
                ))
            else:
                self._scroll_to_widget(self.gpu_download_row)
                self._highlight_button(self.gpu_download_btn)
            return
        self._navigate_to_section("updates")
        QTimer.singleShot(ENTER_MS + 50, lambda: self._highlight_button(self.update_btn))

    # --- construction ---

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("navRail")
        nav.setFixedWidth(208)
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
        self._nav_icon_names = {
            "dictation": "dictation",
            "activity": "activity",
            "appearance": "appearance",
            "updates": "updates",
            "privacy": "privacy",
        }
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
            button.setIconSize(QSize(20 + NAV_ICON_GAP, 20))
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
        self._vocabulary_page = VocabularyPage()
        self._vocabulary_page.back.connect(self._hide_vocabulary)
        self._vocabulary_page.vocabularyChanged.connect(self._set_vocabulary)

        page = QWidget()
        page.setObjectName("root")
        col = QVBoxLayout(page)
        col.setContentsMargins(26, 24, 26, 26)
        col.setSpacing(8)
        scroll.setWidget(page)

        app_title = QLabel("Dictation")
        app_title.setProperty("role", "appTitle")
        app_desc = QLabel("Your microphone, talk key, and speech preferences.")
        app_desc.setProperty("role", "desc")
        app_desc.setWordWrap(True)

        # One adaptive rail control keeps app updates, model downloads, and
        # GPU setup visible without turning the page header into a toolbar.
        self._download_focus = "update"
        self._download_action = "open_update"
        self._download_overview_mode = "idle"
        self.download_overview = QPushButton()
        self.download_overview.setObjectName("downloadOverview")
        self.download_overview.setCursor(Qt.PointingHandCursor)
        self.download_overview.setFixedSize(_OVERVIEW_WIDTH, 40)
        self.download_overview.setVisible(False)
        self.download_overview.setAccessibleName("Downloads and updates")
        self.download_overview.setToolTip(
            "See current download and update activity, or open its detailed control."
        )
        self._download_overview_size_target = QSize(_OVERVIEW_WIDTH, 40)
        self._download_overview_size_anim = QParallelAnimationGroup(self)
        self._download_overview_size_anim.finished.connect(
            self._finish_download_overview_size_animation
        )

        download_copy = QHBoxLayout(self.download_overview)
        download_copy.setContentsMargins(
            _OVERVIEW_MARGIN_LEFT, 10, _OVERVIEW_MARGIN_RIGHT, 10
        )
        # Spacing is set per gap, not once for the row: the glyph needs the
        # rail's roomier icon-to-label distance, while the trailing chevron
        # only needs enough clearance not to crowd the copy it follows.
        download_copy.setSpacing(0)
        # Use the same 20 px outline symbols as navigation and Updates.
        self.download_overview_status = QLabel()
        self.download_overview_status.setProperty("active", False)
        self.download_overview_status.setProperty("tone", "muted")
        self.download_overview_status.setFixedSize(_OVERVIEW_STATUS_PX, _OVERVIEW_STATUS_PX)
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
        # Windows 11 lines a leading glyph up with the first line of copy,
        # not the middle of a block that may wrap to three lines.
        download_copy.addWidget(self.download_overview_status, 0, Qt.AlignTop)
        download_copy.addSpacing(_OVERVIEW_STATUS_GAP)
        self.download_overview_copy = QWidget()
        self.download_overview_copy.setAttribute(Qt.WA_TransparentForMouseEvents)
        overview_copy = QVBoxLayout(self.download_overview_copy)
        overview_copy.setContentsMargins(0, 0, 0, 0)
        overview_copy.setSpacing(3)
        self.download_overview_title = QLabel("Up to date")
        self.download_overview_title.setObjectName("downloadOverviewTitle")
        self.download_overview_title.setWordWrap(True)
        self.download_overview_title.setTextFormat(Qt.PlainText)
        self.download_overview_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.download_overview_title.setAttribute(Qt.WA_TransparentForMouseEvents)
        overview_copy.addWidget(self.download_overview_title)
        self.download_overview_details = QWidget()
        details_copy = QVBoxLayout(self.download_overview_details)
        details_copy.setContentsMargins(0, 0, 0, 0)
        details_copy.setSpacing(4)
        self.download_overview_detail = QLabel("Checking download status…")
        self.download_overview_detail.setObjectName("downloadOverviewDetail")
        self.download_overview_detail.setWordWrap(True)
        self.download_overview_detail.setTextFormat(Qt.PlainText)
        self.download_overview_detail.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.download_overview_detail.setAttribute(Qt.WA_TransparentForMouseEvents)
        details_copy.addWidget(self.download_overview_detail)
        self.download_overview_progress = _native_progress_bar()
        self.download_overview_progress.setObjectName("downloadOverviewProgress")
        self.download_overview_progress.setFixedHeight(3)
        details_copy.addWidget(self.download_overview_progress)
        self.download_overview_details.setVisible(False)
        overview_copy.addWidget(self.download_overview_details)
        download_copy.addWidget(self.download_overview_copy, 1)
        download_copy.addSpacing(_OVERVIEW_CHEVRON_GAP)
        self.download_overview_chevron = QLabel()
        self.download_overview_chevron.setFixedSize(_OVERVIEW_CHEVRON_PX, _OVERVIEW_CHEVRON_PX)
        self.download_overview_chevron.setObjectName("downloadOverviewChevron")
        self.download_overview_chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        download_copy.addWidget(self.download_overview_chevron, 0, Qt.AlignVCenter)
        self.download_overview.clicked.connect(self._open_download_overview)
        self._download_overview_slot.addWidget(self.download_overview)
        col.addWidget(app_title)
        col.addWidget(app_desc)
        col.addSpacing(12)

        self.mic_box = QComboBox()
        self.mic_box.setFixedWidth(200)
        _fill_microphone_box(self.mic_box, self._settings.input_device)
        self.mic_box.currentIndexChanged.connect(self._queue_save)
        mic_row = _card("Microphone", "Choose where Dictate listens.", self.mic_box)

        self.ptt_edit = BindingCapture(self._settings.ptt_key)
        self.ptt_edit.setToolTip("Click to change. Press a key or mouse button, or hold inputs together for a combination.")
        self.ptt_edit.bindingChanged.connect(self._queue_save)
        self.ptt_edit.bindingChanged.connect(self._refresh_tap_lock_desc)
        self.ptt_edit.captureActive.connect(self.capture_active.emit)
        ptt_row = _card(
            "Hold to talk",
            "Hold the key, speak, then release.",
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

        # Where dictation runs and which speech model it uses are the main
        # sentence for how dictation actually behaves, so both live in
        # Essentials, not behind Advanced -- as two plain, direct choices
        # rather than one combined "Speed & accuracy" abstraction. Matches
        # the Microphone combo above them.
        self.device_box = QComboBox()
        self.device_box.setFixedWidth(200)
        self._populate_device_choices()
        self.device_box.currentIndexChanged.connect(self._on_device_changed)
        device_row = _card(
            "Processing",
            "Where Dictate runs the speech model.",
            self.device_box,
        )
        self.device_desc_label = device_row.findChild(QLabel, "desc")

        self.model_box = QComboBox()
        self.model_box.setFixedWidth(200)
        self._populate_model_choices()
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
        model_row = _card(
            "Model",
            "Which speech model to use.",
            self.model_box,
        )
        self.model_desc_label = model_row.findChild(QLabel, "desc")

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
            "Help Dictate recognize names and uncommon terms.",
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
            "How long to wait before releasing model memory.",
            self.sleep_slider,
        )

        self.sound_check = ToggleSwitch(self._settings.sound_cues)
        self.sound_check.toggled.connect(self._queue_save)
        sound_row = _card(
            "Recording sounds",
            "A short cue when recording starts and finishes.",
            self.sound_check,
        )

        self.duck_check = ToggleSwitch(self._settings.duck_other_audio)
        self.duck_check.toggled.connect(self._queue_save)
        duck_row = _card(
            "Lower other audio",
            "Turns other apps down while you talk, so speakers don't bleed into the mic.",
            self.duck_check,
        )

        self.startup_check = ToggleSwitch(self._settings.start_with_windows)
        self.startup_check.toggled.connect(self._queue_save)
        startup_row = _card(
            "Start with Windows",
            "Keep Dictate ready after you sign in.",
            self.startup_check,
        )
        self.startup_desc_label = startup_row.findChild(QLabel, "desc")

        # Ten rows in one undivided list is past the point where anyone scans
        # rather than reads. Split them along the promise the page title just
        # made -- "listens, transcribes, and stays ready" -- so the headings
        # answer a question the reader already has, and no group exceeds four
        # rows. Headings sit outside the frames, matching ACTIVITY BAR below.
        self.essentials_group = QWidget()
        essentials = QVBoxLayout(self.essentials_group)
        essentials.setContentsMargins(0, 0, 0, 0)
        essentials.setSpacing(8)
        for heading, rows in (
            ("Recording", (mic_row, ptt_row, self.tap_lock_row, duck_row)),
            (
                "Transcription",
                (device_row, model_row, self.model_download_row, vocabulary_row),
            ),
            ("Stay ready", (sound_row, startup_row)),
        ):
            label = QLabel(heading)
            label.setProperty("role", "section")
            essentials.addWidget(label)
            essentials.addWidget(_settings_group(*rows))
        col.addWidget(self.essentials_group)

        self.advanced_btn = ExpanderHeader("Advanced settings")
        self.advanced_btn.toggled.connect(self._toggle_advanced)
        col.addWidget(self.advanced_btn)

        self.advanced_panel = QWidget()
        advanced_col = QVBoxLayout(self.advanced_panel)
        advanced_col.setContentsMargins(0, 0, 0, 0)
        advanced_col.setSpacing(8)

        memory_header = QLabel("Memory")
        memory_header.setProperty("role", "section")
        advanced_col.addWidget(memory_header)
        advanced_col.addWidget(_settings_group(sleep_row, sleep_after_row))

        performance_header = QLabel("GPU acceleration")
        performance_header.setProperty("role", "section")
        advanced_col.addWidget(performance_header)

        # A dedicated call-to-action for GPU acceleration, separate from the
        # Processing choice in Essentials above: if the GPU files were
        # skipped during installation, the GPU option there stays greyed out
        # until this explicit download finishes. That prevents a first
        # dictation from unexpectedly starting a 1.3 GB download or
        # pretending it is already on GPU.
        self.gpu_download_btn = QPushButton("Download now")
        self.gpu_download_btn.setObjectName("secondary")
        self.gpu_download_btn.clicked.connect(self._start_gpu_download_clicked)
        self.gpu_download_btn.clicked.connect(self._clear_button_attention)
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

        self.open_models_btn = QPushButton("Open model folder")
        self.open_models_btn.clicked.connect(self._open_model_folder)
        model_storage_row = _card(
            "Speech-model files",
            "Stored only for Dictate, separate from other apps and downloads.",
            self.open_models_btn,
        )
        self.model_storage_desc_label = model_storage_row.findChild(QLabel, "desc")

        advanced_col.addWidget(
            _settings_group(
                self.gpu_download_row,
                self.gpu_download_progress_row,
                model_storage_row,
            )
        )

        behavior_header = QLabel("Shortcuts")
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

        activity_header = QLabel("Activity Bar")
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
        notifications_header = QLabel("Notifications")
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

        appearance_header = QLabel("Appearance")
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
        updates_header = QLabel("Dictate Update")
        updates_header.setProperty("role", "section")
        col.addWidget(updates_header)
        self.updates_header = updates_header
        self.auto_update_check = ToggleSwitch(self._settings.auto_update_enabled)
        self.auto_update_check.toggled.connect(self._queue_save)
        self.update_btn = QPushButton("Check for updates")
        self.update_btn.setObjectName("apply")
        self.update_btn.setProperty("updateState", "default")
        self.update_btn.setVisible(self._updater is not None)
        self.update_btn.clicked.connect(self._check_for_updates)
        self.update_btn.clicked.connect(self._clear_button_attention)
        update_row = _card(
            "You're up to date",
            "Dictate checks GitHub for a newer official release.",
            self.update_btn,
        )
        self.update_status_icon = update_row.add_title_icon()
        self.update_title_label = update_row.title_label
        self.update_desc_label = update_row.desc_label
        self.update_meta_label = QLabel()
        self.update_meta_label.setObjectName("updateMeta")
        update_row.add_detail(self.update_meta_label)
        self.update_progress = _native_progress_bar()
        update_row.add_detail(self.update_progress)
        self.updates_group = _settings_group(update_row)
        self.updates_group.setProperty("updateStatus", True)

        auto_update_row = _card(
            "Check automatically",
            "Check at startup and every 24 hours while Dictate keeps running.",
            self.auto_update_check,
        )
        self.updates_whats_new_btn = QPushButton("View release notes")
        self.updates_whats_new_btn.setObjectName("secondary")
        self.updates_whats_new_btn.clicked.connect(self._show_whats_new)
        whats_new_row = _card(
            "What's new",
            f"Read the changes included with Dictate {self._whats_new_version}.",
            self.updates_whats_new_btn,
        )
        update_source_row = _text_row(
            "Download and install",
            "Downloads only when you choose. Restart to install.",
        )
        self.update_details_group = _settings_group(
            auto_update_row, whats_new_row, update_source_row
        )

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
        update_preferences_header = QLabel("Update Settings")
        update_preferences_header.setProperty("role", "section")
        finish_page(
            self._updates_page,
            updates_col,
            self.updates_header,
            "Updates",
            "See your update status and choose when a new version is installed.",
            self.updates_group,
            update_preferences_header,
            self.update_details_group,
        )
        self._pages.addWidget(self._privacy_page)
        self._pages.addWidget(self._vocabulary_page)

        self._page_map = {
            "dictation": self._settings_page,
            "activity": self._activity_page,
            "appearance": self._appearance_page,
            "updates": self._updates_page,
            "privacy": self._privacy_page,
            "vocabulary": self._vocabulary_page,
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

    def _gpu_ready(self) -> bool:
        return self._has_cuda and not gpu_runtime.needs_download(gpu_available=True)

    def _populate_device_choices(self) -> None:
        """CPU always works; GPU is disabled until its runtime is ready."""
        self.device_box.clear()
        for value, label in config.DEVICES:
            self.device_box.addItem(label, value)
        index = self.device_box.findData("cuda")
        if index >= 0:
            item = self.device_box.model().item(index)
            gpu_selectable = self._has_cuda and self._gpu_ready()
            item.setEnabled(gpu_selectable)
            if not self._has_cuda:
                item.setToolTip("No supported NVIDIA GPU was found on this PC.")
            elif not gpu_selectable:
                item.setToolTip("Set up GPU acceleration first to use this option.")

    def _populate_model_choices(self) -> None:
        for size, summary in config.MODELS:
            self.model_box.addItem(config.model_display_name(size), size)
            self.model_box.setItemData(
                self.model_box.count() - 1, summary, Qt.ToolTipRole
            )

    def _device_description(self) -> str:
        """One short line: which real hardware this choice runs on, or why
        GPU isn't available yet."""
        if self._has_cuda and not self._gpu_ready():
            return (
                "Set up GPU acceleration in Advanced settings to use "
                "your graphics card."
            )
        if self.device_box.currentData() == "cuda" and self._hardware.has_gpu_details:
            return self._hardware.gpu_name
        return self._hardware.cpu_name or "Where Dictate runs the speech model."

    def _model_description(self) -> str:
        size = self.model_box.currentData()
        for value, summary in config.MODELS:
            if value == size:
                return summary
        return "Which speech model to use."

    def _on_device_changed(self, *_args) -> None:
        if self._loading:
            return
        self._update_device_description()
        self._queue_save()

    def _on_model_changed(self, *_args) -> None:
        if self._loading:
            return
        self._update_model_description()
        self._queue_save()

    def _update_device_description(self) -> None:
        if self.device_desc_label is not None:
            self.device_desc_label.setText(self._device_description())

    def _update_model_description(self) -> None:
        if self.model_desc_label is not None:
            self.model_desc_label.setText(self._model_description())

    def _update_processing_choices(self) -> None:
        """Refresh GPU availability after GPU acceleration is installed."""
        index = self.device_box.findData("cuda")
        if index >= 0:
            item = self.device_box.model().item(index)
            gpu_selectable = self._has_cuda and self._gpu_ready()
            item.setEnabled(gpu_selectable)
            item.setToolTip(
                "" if gpu_selectable
                else "Set up GPU acceleration first to use this option."
            )
        self._update_device_description()

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
        self._update_processing_choices()
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
            error = getattr(self._engine, "gpu_error", "")
            if isinstance(error, str) and error:
                self.gpu_download_btn.setEnabled(True)
                self.gpu_download_btn.setText("Try again")
                if self.gpu_download_desc_label is not None:
                    self.gpu_download_desc_label.setText(error)
                return
            self.gpu_download_btn.setEnabled(True)
            self.gpu_download_btn.setText("Download now")
            if self.gpu_download_desc_label is not None:
                self.gpu_download_desc_label.setText(
                    "Not downloaded yet. Dictate keeps using CPU until this "
                    "finishes — about 1.3 GB, downloaded once in the background."
                )

    def _update_vocabulary_button(self) -> None:
        count = len(self._vocabulary)
        self.vocabulary_btn.setText("Edit words" if count == 0 else f"Edit words ({count})")

    def _edit_vocabulary(self) -> None:
        self._vocabulary_origin_scroll = self._scroll.verticalScrollBar().value()
        self._vocabulary_page.set_vocabulary(self._vocabulary)
        self._nav_buttons["dictation"].setChecked(True)
        self._move_nav_indicator("dictation")
        self._animate_to_page("vocabulary")
        QTimer.singleShot(0, self._vocabulary_page.focus_editor)

    def _set_vocabulary(self, words: list[str]) -> None:
        if words == self._vocabulary:
            return
        self._vocabulary = words
        self._update_vocabulary_button()
        self._queue_save()

    def _hide_vocabulary(self) -> None:
        self._nav_buttons["dictation"].setChecked(True)
        self._move_nav_indicator("dictation")
        self._animate_to_page("dictation")
        previous = getattr(self, "_vocabulary_origin_scroll", 0)
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(previous))

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
        self._refresh_nav_icons()
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

    def _set_update_button_state(self, state: str) -> None:
        """Keep the restart action visually distinct from ordinary update work."""
        presentation = "restart" if state == updater_mod.READY_TO_RESTART else "default"
        if self.update_btn.property("updateState") == presentation:
            return
        self.update_btn.setProperty("updateState", presentation)
        style = self.update_btn.style()
        style.unpolish(self.update_btn)
        style.polish(self.update_btn)

    def _check_for_updates(self) -> None:
        if self._updater is None:
            return
        u_state, _u_detail, _u_progress = self._updater.last_status
        # Turning off future checks must not strand an update that was already
        # found or a verified installer that is already waiting to restart.
        if (
            not self.auto_update_check.isChecked()
            and u_state not in (updater_mod.AVAILABLE, updater_mod.READY_TO_RESTART)
        ):
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
        if u_state == updater_mod.AVAILABLE:
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Downloading…")
            if not self._updater.start_update():
                self.refresh_status()
        elif u_state == updater_mod.READY_TO_RESTART:
            self._set_update_button_state(updater_mod.VERIFYING)
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

    def _update_meta_text(self) -> str:
        checked_at = getattr(self._updater, "last_checked_at", None)
        if not isinstance(checked_at, (int, float)):
            checked = "Not checked yet this session"
        else:
            checked_time = datetime.fromtimestamp(checked_at)
            clock = checked_time.strftime("%I:%M %p").lstrip("0")
            if checked_time.date() == datetime.now().date():
                checked = f"Last checked today at {clock}"
            else:
                checked = f"Last checked {checked_time.strftime('%b %d')} at {clock}"
        return f"Version {VERSION} · {checked}"

    def refresh_status(self) -> None:
        """Refresh progress and errors inside the controls that own them.

        GPU-runtime, model, and update work all expose the same
        LOADING-with-progress/status-detail shape. Each state stays beside
        its action instead of creating a separate message in the rail footer.
        Updater.last_status reverts itself back to IDLE a few seconds after
        a one-shot confirmation (UP_TO_DATE/ERROR) -- see updater.py's
        _set_status -- so this can poll it fresh with no extra bookkeeping.
        AVAILABLE and READY_TO_RESTART remain visible until the person acts.
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
            update_actionable = u_state in (
                updater_mod.AVAILABLE,
                updater_mod.READY_TO_RESTART,
            )
            self.update_btn.setEnabled(
                update_actionable
                or (auto_update_on and not update_busy)
            )
            self._set_update_button_state(u_state)
            self._refresh_update_status_icon()
            self.update_meta_label.setText(self._update_meta_text())
            update_title = {
                updater_mod.CHECKING: "Checking for updates",
                updater_mod.UP_TO_DATE: "You're up to date",
                updater_mod.AVAILABLE: "Update available",
                updater_mod.DOWNLOADING: "Downloading update",
                updater_mod.READY_TO_RESTART: "Restart to finish",
                updater_mod.VERIFYING: "Verifying update",
                updater_mod.INSTALLING: "Restarting Dictate",
                updater_mod.ERROR: "Update needs attention",
            }.get(u_state, "You're up to date" if auto_update_on else "Updates paused")
            if self.update_title_label is not None:
                self.update_title_label.setText(update_title)
            if u_state == updater_mod.AVAILABLE:
                self.update_btn.setText("Download update")
            elif u_state == updater_mod.READY_TO_RESTART:
                self.update_btn.setText("Restart Dictate")
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
                if self.update_desc_label is not None:
                    update_description = {
                        updater_mod.CHECKING: "Looking for a newer official release.",
                        updater_mod.UP_TO_DATE: "Dictate is running the latest available version.",
                        updater_mod.AVAILABLE: (
                            f"{u_detail}. Nothing downloads until you choose it."
                            if u_detail
                            else "A newer version is ready to download when you choose."
                        ),
                        updater_mod.DOWNLOADING: u_detail or "Downloading the installer.",
                        updater_mod.READY_TO_RESTART: (
                            "The update is downloaded and verified. Restart when you're ready."
                        ),
                        updater_mod.VERIFYING: "Checking the installer before restart.",
                        updater_mod.INSTALLING: "Dictate is closing to install the update.",
                        updater_mod.ERROR: u_detail or "The update could not be completed.",
                    }.get(u_state, u_detail)
                    self.update_desc_label.setText(update_description)
                return

            if self.update_desc_label is not None:
                if auto_update_on:
                    self.update_desc_label.setText(
                        "Dictate is running the latest version found in this session."
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
            return

        self.model_download_row.setVisible(False)

        if self._pending_reload:
            if state == engine_mod.READY:
                self._pending_reload = False
                self.reload_progress.setVisible(False)
                self._update_model_description()
                return
            if state == engine_mod.ERROR:
                self._pending_reload = False
                self.reload_progress.setVisible(False)
                if self.model_desc_label is not None:
                    self.model_desc_label.setText(
                        "Dictate could not load this choice. Pick another model or device and try again."
                    )
                return
        else:
            self.reload_progress.setVisible(False)

    def _tap_lock_desc(self) -> str:
        """Explain the gesture using the key that is actually bound to it."""
        key = hotkeys_mod.format_combo(
            self.ptt_edit.binding() or self._settings.ptt_key or config.DEFAULT_PTT_KEY
        )
        return (
            f"Tap {key} again to finish. Esc cancels."
        )

    def _open_model_folder(self) -> None:
        """Open Dictate's private model cache, creating it if needed."""
        folder = config.model_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if self.model_storage_desc_label is not None:
            self.model_storage_desc_label.setText(
                "Stored only for Dictate, separate from other apps and downloads."
            )
        try:
            os.startfile(folder)
        except OSError:
            if self.model_storage_desc_label is not None:
                self.model_storage_desc_label.setText(
                    "Could not open this folder. Try again."
                )

    def _refresh_tap_lock_desc(self, *_args) -> None:
        if self.tap_lock_desc_label is not None:
            self.tap_lock_desc_label.setText(self._tap_lock_desc())

    def _on_appearance_changed(self, *_args) -> None:
        if self._loading:
            return
        self._apply_appearance()
        self._queue_save()

    def _collect_settings(self) -> config.Settings:
        model_size = self.model_box.currentData() or self._settings.model_size
        device = self.device_box.currentData() or self._settings.device
        return replace(
            self._settings,
            device=device,
            model_size=model_size,
            input_device=self.mic_box.currentData() or "",
            sleep_enabled=self.sleep_check.isChecked(),
            sound_cues=self.sound_check.isChecked(),
            duck_other_audio=self.duck_check.isChecked(),
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
            return
        old = self._settings

        if new.start_with_windows != old.start_with_windows:
            if self.startup_desc_label is not None:
                self.startup_desc_label.setText(
                    "Keep Dictate ready after you sign in."
                )
            try:
                startup_mod.set_enabled(new.start_with_windows)
            except OSError:
                blocked = self.startup_check.blockSignals(True)
                self.startup_check.setChecked(old.start_with_windows)
                self.startup_check.blockSignals(blocked)
                new = replace(new, start_with_windows=old.start_with_windows)
                if self.startup_desc_label is not None:
                    self.startup_desc_label.setText(
                        "Could not change Windows startup. Try again."
                    )
                if asdict(new) == asdict(old):
                    return

        needs_reload = (new.model_size, new.device) != (old.model_size, old.device)

        self._settings = new
        config.save(new)
        self.changed.emit(new)  # main.py wires this to engine/hotkeys/bar/tray
        if needs_reload:
            self._pending_reload = True
            if self.model_desc_label is not None:
                self.model_desc_label.setText("Updating processing…")
            self._engine.preload()
        else:
            self._pending_reload = False

    def _load_widgets(self, s: config.Settings) -> None:
        """Push settings values into the controls without staging a change."""
        self._loading = True
        device_index = self.device_box.findData(s.device)
        self.device_box.setCurrentIndex(max(0, device_index))
        model_index = self.model_box.findData(s.model_size)
        self.model_box.setCurrentIndex(max(0, model_index))
        self._update_device_description()
        self._update_model_description()
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
