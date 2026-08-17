"""Windows light/dark theme detection and the shared Dictate palette."""

from __future__ import annotations

import winreg

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor


def system_is_dark() -> bool:
    """Return the Windows app theme; fall back to dark if it cannot be read."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            apps_use_light_theme, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return not bool(apps_use_light_theme)
    except OSError:
        return True


class ThemeWatcher(QObject):
    """Poll the Windows setting lightly so an open app follows changes too."""

    changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dark = system_is_dark()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check)
        self._timer.start()

    @property
    def dark(self) -> bool:
        return self._dark

    def _check(self) -> None:
        dark = system_is_dark()
        if dark != self._dark:
            self._dark = dark
            self.changed.emit(dark)


def colors(dark: bool) -> dict[str, QColor]:
    """Paint palette for hand-drawn controls and the compact activity surfaces."""
    if dark:
        return {
            "surface": QColor(44, 44, 44, 246), "stroke": QColor(255, 255, 255, 18),
            "track": QColor(255, 255, 255, 38), "idle": QColor(255, 255, 255, 78),
            "text": QColor(255, 255, 255, 235), "shadow": QColor(0, 0, 0, 150),
            "off_border": QColor(158, 158, 158), "off_border_hover": QColor(200, 200, 200),
            "off_fill_hover": QColor(255, 255, 255, 16), "thumb_on": QColor(0, 0, 0, 230),
            "thumb_off": QColor(206, 206, 206), "disabled": QColor(84, 84, 84),
        }
    return {
        "surface": QColor(249, 249, 249, 248), "stroke": QColor(0, 0, 0, 20),
        "track": QColor(0, 0, 0, 38), "idle": QColor(0, 0, 0, 92),
        "text": QColor(0, 0, 0, 225), "shadow": QColor(0, 0, 0, 55),
        "off_border": QColor(105, 105, 105), "off_border_hover": QColor(65, 65, 65),
        "off_fill_hover": QColor(0, 0, 0, 12), "thumb_on": QColor(255, 255, 255, 245),
        "thumb_off": QColor(95, 95, 95), "disabled": QColor(185, 185, 185),
    }
