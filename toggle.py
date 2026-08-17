"""A Windows 11 toggle switch.

Qt has no native toggle, and a stylesheet-themed QCheckBox can't draw the
checkmark without shipping an image, so on/off settings ended up as a plain
blue square. Windows 11 Settings uses a switch for exactly this kind of option
anyway, so this draws the real thing: a pill track, a thumb that slides and
grows when switched on, and the same hover states the shell uses.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget
from theme import colors, system_is_dark

W, H = 40, 20


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._slide = 1.0 if checked else 0.0
        self._hover = False

        self.setFixedSize(W, H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._anim = QPropertyAnimation(self, b"slide", self)
        self._anim.setDuration(130)

    def sizeHint(self) -> QSize:
        return QSize(W, H)

    # --- state ---

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._anim.stop()
        self._anim.setEndValue(1.0 if value else 0.0)
        self._anim.start()
        self.toggled.emit(value)

    def _toggle(self) -> None:
        self.setChecked(not self._checked)

    # --- input ---

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._toggle()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._toggle()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    # --- painting ---

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        palette = colors(system_is_dark())
        enabled = self.isEnabled()
        track = QRectF(0.5, 0.5, W - 1, H - 1)
        radius = track.height() / 2

        if self._checked:
            accent = QColor(0, 120, 212) if not system_is_dark() else QColor(76, 194, 255)
            accent_hover = QColor(0, 108, 190) if not system_is_dark() else QColor(96, 205, 255)
            fill = accent_hover if (self._hover and enabled) else accent
            if not enabled:
                fill = palette["disabled"]
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(track, radius, radius)
        else:
            border = palette["off_border_hover"] if (self._hover and enabled) else palette["off_border"]
            if not enabled:
                border = palette["disabled"]
            p.setPen(QPen(border, 1))
            p.setBrush(palette["off_fill_hover"] if (self._hover and enabled) else Qt.NoBrush)
            p.drawRoundedRect(track, radius, radius)

        # The thumb grows on the way to "on", which is the detail that makes
        # the switch feel like the system one rather than a generic slider.
        r = 5.0 + 1.0 * self._slide
        cx = 10.0 + self._slide * (W - 20)
        thumb = palette["thumb_on"] if self._checked else palette["thumb_off"]
        if not enabled:
            thumb = palette["disabled"]
        p.setPen(Qt.NoPen)
        p.setBrush(thumb)
        p.drawEllipse(QRectF(cx - r, H / 2 - r, r * 2, r * 2))

    # --- animated property ---

    def _get_slide(self) -> float:
        return self._slide

    def _set_slide(self, value: float) -> None:
        self._slide = value
        self.update()

    slide = Property(float, _get_slide, _set_slide)
