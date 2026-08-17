"""The floating bar: a small Windows 11 flyout centred above the taskbar.

Drawn by hand rather than assembled from widgets. A DWM system backdrop
(Mica/Acrylic) fights Qt's own translucency on a frameless tool window and the
result is inconsistent, so this paints the Fluent material directly -- tint
plus the fine noise that makes Acrylic read as a surface rather than a flat
rectangle, an 8px flyout radius, a 1px light stroke, and an ambient shadow so
the bar sits *above* the taskbar instead of on the wallpaper.

Fluent gets its polish from material and geometry, not from lighting. There is
no glow anywhere in this file on purpose: bloom is Apple's vocabulary, and it
is what made an earlier version of this bar look like a media player.

Everything is sized in logical pixels and the two cached bitmaps -- the
shadow and the acrylic grain -- are rebuilt per device pixel ratio, so the bar
is as crisp at 150% on a 4K panel as at 100% on a 1080p one.

**One instrument, three signals.** A single hairline runs the width of the
bar and never moves. Twenty-one slim capsules grow out of it, so silence is an
unbroken rule rather than a row of dots, and every state is something
happening *to* that one line:

- *Listening* scrolls a history of how loud you have been, newest at the right.
  A spectrum was wrong here: speech energy is bunched into the low bands, so
  drawing it left-to-right leaves the right-hand side permanently dead, and
  the previous fix -- mirroring it -- made the wave perfectly symmetrical,
  which reads as decoration rather than as a response to a voice.
- *Loading* sinks the capsules back into the hairline and runs two accent
  segments along it, each brightest at its leading edge and travelling at its
  own speed so the pair drifts rather than moving as one rigid object. That is
  Windows' own indeterminate-progress idiom, and because the line was already
  there it is a connected animation rather than one widget being swapped for
  another.
- *Transcribing* undulates the capsules with two sine components crossing in
  opposite directions at different speeds: where they reinforce a crest rises,
  where they cancel the line dips, so it never resolves into a predictable
  loop. It is usually over in well under a second, so it keeps the capsules
  rather than paying for the collapse; the sweep is reserved for the wait that
  is actually long.

Three rules keep those reading as one instrument rather than three animations
sharing a widget:

- **One clock, measured in seconds.** Every signal is a function of elapsed
  time, not of how many frames have been drawn. A CPU transcription saturates
  the machine for the better part of a second and the UI thread loses frames
  while it does; per-frame arithmetic would visibly jump exactly when you are
  watching.
- **States morph, and they ripple.** Changing state snapshots what is on
  screen and cross-fades its shape and colour into the new signal, so a state
  that lasts 200 ms looks deliberate instead of truncated. Each capsule starts
  that morph a beat after its neighbour, so the change travels across the bar
  rather than every capsule moving as one block. The clock is never reset, so
  a hump crossing the bar keeps crossing it through the change.
- **Everything is sprung, slightly loosely.** Drawn heights chase the signal
  through an under-damped spring, which removes the per-frame jitter from the
  live microphone and gives an interrupted animation somewhere to carry its
  momentum. The damping is deliberately below critical so a capsule overshoots
  its value by about 5% and settles back -- critical damping is the tidy
  answer and looks inert.

The window must never take focus. Dictation pastes into whatever the user was
already typing in, and a bar that steals focus would paste into itself.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget
from theme import colors, system_is_dark

# Fluent dark-theme surface values, matching a Windows 11 flyout.
SURFACE = QColor(44, 44, 44, 246)
STROKE = QColor(255, 255, 255, 18)
TRACK = QColor(255, 255, 255, 38)     # recessed track behind the live bars
SUCCESS = QColor(108, 203, 95)
ERROR = QColor(255, 92, 92)           # Fluent critical red on a dark surface
IDLE_DOT = QColor(255, 255, 255, 78)
FALLBACK_ACCENT = QColor(76, 194, 255)  # #4CC2FF, the Windows 11 dark accent

HALF_BANDS = 20        # bands the meter produces (main.py builds it from this)
N_BARS = 21            # slim capsules, asymmetric, no mirroring
BAR_W = 4.0
BAR_GAP = 5.0
BAR_R = BAR_W / 2      # a true capsule reads sleeker than a rounded rectangle
WAVE_W = N_BARS * BAR_W + (N_BARS - 1) * BAR_GAP

PAD = 18
PILL_W = int(2 * PAD + WAVE_W)
PILL_H = 40
RADIUS = 8             # Windows 11 flyout radius; 16 read as iOS
SHADOW_PAD = 12        # room around the pill for the ambient shadow
WIDTH = PILL_W + 2 * SHADOW_PAD
HEIGHT = PILL_H + 2 * SHADOW_PAD
MAX_BAR_H = PILL_H - 12

# One hairline runs the width of the bar and never moves. At rest the bars sit
# flush in it; speaking grows them out of it; loading sinks them back into it
# while the sweep segments travel along it. Giving the resting state, the
# waveform's baseline and the progress track a single shared line is what
# stops the loading state from looking like a different widget.
TRACK_H = 2.0
MIN_BAR_H = TRACK_H
SWEEP_H = 3.0          # segments ride slightly proud of the track they run on
LIFT_FADE_PX = 4.0     # how far a capsule rises before it reaches full accent

# Motion, all in seconds or cycles-per-second so it survives a dropped frame.
# A critically damped step response is 1-(1+wt)e^-wt, so it reaches 63% at
# wt~2.15, not at wt=1 as a plain exponential would -- hence ~50 for a 40 ms
# follow rather than the 1/0.04 you would expect.
SPRING_OMEGA = 58.0
# Slightly *under* damped, so a bar arriving at its target passes it by about
# 5% and settles back. Critical damping is technically the clean answer and
# looks inert; this small overshoot is the whole difference between motion
# that is correct and motion that is satisfying. Much below 0.65 it starts
# to wobble and reads as cheap again.
SPRING_ZETA = 0.70
# Fluent point-to-point motion is 150-250 ms. An earlier build stacked a
# 350 ms dwell floor, a 200 ms morph and a 144 ms ripple, so a state change
# could take 694 ms end to end and read as lag. These are sized so the worst
# case is 470 ms and the common case is 230 ms.
MORPH_SECONDS = 0.15
# Bars begin their morph a beat apart rather than all at once, so a state
# change ripples across the bar instead of snapping as one block. 21 bars at
# 4 ms spread it over 80 ms -- enough to read as a ripple, short enough that
# the whole morph still lands inside the dwell floor below.
STAGGER_SECONDS = 0.004
MIN_DWELL_SECONDS = 0.24   # no state is on screen for less time than this
BURST_TAU = 0.24
SWEEP_TAU = 0.07           # how fast the bars collapse into the progress track
SLICE_SECONDS = 0.07       # one new amplitude column; 21 of them ~= 1.5 s
# Transcribing is two travelling sine components rather than one hump: a
# single lump sliding along reads as "a thing moving" and has no character,
# and it has to be wrap-corrected. Two components crossing at different speeds
# and in opposite directions interfere, so the line undulates and never
# settles into an obvious loop -- and being sines, there is no seam at all.
TRANSCRIBE_HZ = 1.1        # speed of the primary component
TRANSCRIBE_HARMONIC = 2.0  # crests across the bar in the counter-moving one
# A low floor lets the troughs sink most of the way back into the hairline,
# so the crests stand out instead of the whole bar reading as a dense block.
TRANSCRIBE_FLOOR = 0.10
TRANSCRIBE_SWING = 0.66
# Loading segments. Two speeds rather than one, so the pair drifts apart
# instead of travelling locked together like a single rigid object.
SWEEP_HZ = 0.55
SWEEP_SEGMENTS = ((0.00, 0.32, 1.00), (0.55, 0.16, 1.18))   # phase, length, speed
ERROR_HZ = 1.15
RISE_PX = 8.0              # entrance rises from the taskbar edge
# Clamp after a stall so nothing teleports. This is a *decorative* animation:
# when the UI thread has been starved, continuing smoothly from where it was
# looks better than skipping ahead to where the clock says it should be. Sized
# against the recovery frame -- at 0.05 the faster, narrower transcribing hump
# lurched 0.35 of full scale in a single frame coming out of a freeze.
MAX_DT = 0.025

_U = np.linspace(0.0, 1.0, N_BARS, dtype=np.float32)
_CENTRE_D = np.abs(np.arange(N_BARS) - (N_BARS - 1) / 2) / ((N_BARS - 1) / 2)
_DELAYS = (np.arange(N_BARS, dtype=np.float32) * STAGGER_SECONDS)
MORPH_TOTAL = MORPH_SECONDS + float(_DELAYS[-1])

_CACHE: dict = {}


# --- Windows accent ---------------------------------------------------------

def system_accent(dark: bool = True) -> QColor:
    """The user's Windows accent colour, adjusted for the current surface.

    Windows stores the accent as a little-endian 0xAABBGGRR DWORD. The raw
    value is often too dark to read on this pill (the default #0078D4 is a
    mid-blue), so lightness is floored rather than used as-is -- the same
    reason the shell keeps a separate light variant for dark theme.
    """
    cache_key = ("accent", dark)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    accent = FALLBACK_ACCENT
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM"
        ) as key:
            raw, _ = winreg.QueryValueEx(key, "AccentColor")
        red, green, blue = raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF
        if red or green or blue:
            base = QColor(red, green, blue)
            hue, sat, light, _ = base.getHslF()
            if hue < 0.0:          # a grey accent has no hue to keep
                hue, sat = 0.55, 0.75
            lightness = max(light, 0.66) if dark else min(light, 0.48)
            accent = QColor.fromHslF(hue, min(1.0, max(sat, 0.55)), lightness)
    except Exception:
        pass                        # any failure just keeps the Fluent default
    _CACHE[cache_key] = accent
    return accent


# --- painted Acrylic --------------------------------------------------------

def _box_blur(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    k = 2 * radius + 1
    pad = [(0, 0)] * a.ndim
    pad[axis] = (radius, radius)
    c = np.cumsum(np.pad(a, pad, mode="constant"), axis=axis)
    c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
    n = a.shape[axis]
    return (np.take(c, range(k, k + n), axis=axis)
            - np.take(c, range(0, n), axis=axis)) / k


def _noise_tile(dpr: float, dark: bool = True) -> QPixmap:
    """The fine grain in Fluent's Acrylic. Static and tiled, so it costs one
    texture fill per frame rather than any per-pixel work.

    Built at the display's device pixel ratio: the grain wants to be about one
    *device* pixel, so on a 150% display a 1x tile would be stretched and turn
    into visible mush rather than texture.
    """
    key = ("noise", round(dpr, 3), dark)
    if key in _CACHE:
        return _CACHE[key]
    size = max(16, int(round(64 * dpr)))
    rng = np.random.default_rng(7)          # fixed, so the grain never crawls
    alpha = rng.integers(0, 11, (size, size)).astype(np.uint8)
    buf = np.zeros((size, size, 4), dtype=np.uint8)
    # Premultiplied light/dark grain keeps Acrylic neutral in either system mode.
    for channel in range(3):
        buf[..., channel] = alpha if dark else 0
    buf[..., 3] = alpha
    img = QImage(buf.tobytes(), size, size, QImage.Format_ARGB32_Premultiplied)
    pixmap = QPixmap.fromImage(img.copy())
    pixmap.setDevicePixelRatio(dpr)
    _CACHE[key] = pixmap
    return pixmap


def _shadow(dpr: float, dark: bool = True) -> QPixmap:
    """Ambient elevation under the pill, blurred once and cached per scale.

    Rendered at the display's device pixel ratio and tagged with it, so the
    shadow is as sharp on a 4K 150% display as on a 1080p one. A single 1x
    bitmap scaled up is exactly the kind of soft, slightly wrong edge that
    makes a hand-painted window look unlike the rest of the shell.
    """
    key = ("shadow", round(dpr, 3), dark)
    if key in _CACHE:
        return _CACHE[key]
    width, height = int(round(WIDTH * dpr)), int(round(HEIGHT * dpr))
    layer = QImage(width, height, QImage.Format_ARGB32)
    layer.fill(Qt.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.scale(dpr, dpr)
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(SHADOW_PAD, SHADOW_PAD + 2, PILL_W, PILL_H), RADIUS, RADIUS
    )
    p.fillPath(path, colors(dark)["shadow"])
    p.end()

    arr = np.frombuffer(layer.constBits(), np.uint8).reshape(height, width, 4)
    arr = arr.astype(np.float32)
    radius = max(1, int(round(5 * dpr)))
    for _ in range(3):
        arr = _box_blur(_box_blur(arr, radius, 1), radius, 0)
    out = QImage(
        np.clip(arr, 0, 255).astype(np.uint8).tobytes(),
        width, height, QImage.Format_ARGB32,
    )
    pixmap = QPixmap.fromImage(out.copy())
    pixmap.setDevicePixelRatio(dpr)
    _CACHE[key] = pixmap
    return pixmap


def _fluent_curve(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


# Windows point-to-point motion: entrances decelerate, exits accelerate, and
# an exit is always quicker than the entrance that preceded it.
FLUENT_DECELERATE = _fluent_curve(0.1, 0.9, 0.2, 1.0)
FLUENT_ACCELERATE = _fluent_curve(0.7, 0.0, 1.0, 0.5)
ENTER_MS = 200
EXIT_MS = 150


class Bar(QWidget):
    def __init__(self, settings):
        super().__init__(None)
        self._settings = settings
        self._dark = system_is_dark()
        self._palette = colors(self._dark)
        self._state = "idle"
        self._detail = ""
        self._progress: float | None = None  # 0..1 once a download reports real bytes
        self._notice_tone = "info"       # "info" or "success" -- picks the toast's dot colour
        self._notice_duration_ms: int | None = None
        self._toast_on_click = None      # optional callback for an actionable notice
        self._linger_ms = settings.bar_linger_ms
        self._preview_margin: int | None = None  # live override while dragging the Settings slider

        self._drawn = np.zeros(N_BARS, dtype=np.float32)
        self._vel = np.zeros(N_BARS, dtype=np.float32)
        self._target = np.zeros(N_BARS, dtype=np.float32)
        self._burst = np.zeros(N_BARS, dtype=np.float32)
        # One column longer than there are bars: the extra one is the
        # incoming sample the wave is currently sliding toward.
        self._history = np.zeros(N_BARS + 1, dtype=np.float32)
        self._mic_level = 0.0
        self._slice_accum = 0.0

        self._accent = system_accent(self._dark)
        self._tint = QColor(self._palette["idle"])
        self._morph_from = np.zeros(N_BARS, dtype=np.float32)
        self._morph_from_tint = QColor(self._palette["idle"])
        self._morph_elapsed: float | None = None   # None = settled
        self._sweep = 0.0        # 0 = bars, 1 = collapsed into the progress track

        self._clock = 0.0
        self._last_tick = time.perf_counter()
        self._state_since = time.perf_counter() - MIN_DWELL_SECONDS
        self._pending: tuple[str, str, float | None] | None = None
        self._reveal = 0.0

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                      # keeps it out of Alt+Tab and the taskbar
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(WIDTH, HEIGHT)

        self._toast = Toast()
        self._toast.clicked.connect(self._on_toast_clicked)

        # Everything about the entrance -- fade, scale and rise -- is driven
        # from this one value, so the three can never disagree.
        self._reveal_anim = QPropertyAnimation(self, b"reveal", self)

        self._timer = QTimer(self)
        self._timer.setInterval(16)     # replaced by the real refresh period
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)

        self._dwell_timer = QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._flush_pending)

    def set_theme(self, dark: bool) -> None:
        """Apply the current Windows theme without interrupting dictation."""
        if dark == self._dark:
            return
        self._dark = dark
        self._palette = colors(dark)
        self._accent = system_accent(dark)
        if self._state == "idle":
            self._tint = QColor(self._palette["idle"])
        self._toast.set_theme(dark)
        self.update()

    # --- placement ---

    def pill_geometry(self) -> QRect:
        """Where the visible pill is on screen, ignoring the shadow margin."""
        geo = self.geometry()
        return QRect(geo.x() + SHADOW_PAD, geo.y() + SHADOW_PAD, PILL_W, PILL_H)

    def reposition(self) -> None:
        """Centre horizontally, sit just above the taskbar.

        availableGeometry already excludes the taskbar and is DPI-aware, so
        this lands correctly on scaled displays and follows a taskbar moved to
        another edge without any special-casing. The shadow margin is backed
        out so the configured gap applies to the pill, not to the window.
        """
        screen = QApplication.screenAt(QPoint(0, 0)) or QApplication.primaryScreen()
        if screen is None:
            return
        self._sync_frame_rate(screen)
        area = screen.availableGeometry()
        x = area.center().x() - WIDTH // 2
        margin = self._preview_margin if self._preview_margin is not None else self._settings.bar_margin
        y = area.bottom() + 1 - max(0, margin) - PILL_H - SHADOW_PAD
        self.move(x, y)
        self._toast.follow(self.pill_geometry())

    def _sync_frame_rate(self, screen) -> None:
        """Draw at the display's refresh rate rather than a hardcoded 60 Hz."""
        try:
            rate = float(screen.refreshRate())
        except (TypeError, ValueError):
            rate = 0.0
        if not 24.0 <= rate <= 400.0:
            rate = 60.0
        self._timer.setInterval(max(5, int(round(1000.0 / rate))))

    # --- state ---

    def update_settings(self, settings) -> None:
        self._settings = settings
        _CACHE.pop("accent", None)          # the user may have changed it
        self._accent = system_accent()
        self._linger_ms = settings.bar_linger_ms
        self._preview_margin = None    # the real value just landed; stop overriding it
        if settings.always_visible and self._state == "idle":
            self.show_bar()
        self.reposition()

    def preview_margin(self, margin: int) -> None:
        """Show the bar at a candidate "Bar position" value while the user
        drags the Settings slider, so moving it is visible immediately
        instead of only after the debounced autosave round-trips back
        through update_settings().

        Reuses the ordinary hide timer/auto-hide path rather than a separate
        one, so the fade after 3 seconds of no further dragging is the exact
        same animation -- same duration, same easing -- as the bar fading
        away after "stay after finishing" elapses.
        """
        self._preview_margin = margin
        self.reposition()
        if self._state == "idle":
            self.show_bar()
        self._hide_timer.start(3000)

    def notify(
        self,
        text: str,
        tone: str = "info",
        on_click=None,
        duration_ms: int | None = None,
    ) -> None:
        """Show an informational toast above the bar, in the same style and
        position as the error toast, instead of a Windows system tray
        balloon -- so a manual "check for updates" reads as this app's own
        notification system rather than a different one bolted on for just
        that button.
        """
        self._notice_tone = tone
        self._notice_duration_ms = duration_ms
        self._toast_on_click = on_click
        self._toast.setCursor(Qt.PointingHandCursor if on_click else Qt.ArrowCursor)
        self.set_state("notice", text)

    def _on_toast_clicked(self) -> None:
        if self._toast_on_click is not None:
            callback = self._toast_on_click
            self._toast_on_click = None
            callback()

    def set_state(
        self, state: str, detail: str = "", progress: float | None = None
    ) -> None:
        """Request a state, honouring the dwell floor.

        A model that loads in 200 ms would otherwise put the loading sweep on
        screen and rip it away before it had visibly done anything. Holding
        the next state back until the current one has had its turn makes that
        a short complete animation instead of a flicker.

        ``progress`` is only meaningful for "loading": a 0..1 fraction once a
        first-time model download reports real bytes, switching the track
        from the indeterminate sweep to a fill. Left ``None`` for an ordinary
        cached load, which stays on the sweep.
        """
        if state == self._state:
            self._pending = None            # latest request wins; it says "stay"
            self._dwell_timer.stop()
            self._detail = detail
            self._progress = progress
            return

        remaining = MIN_DWELL_SECONDS - (time.perf_counter() - self._state_since)
        if remaining > 0.0:
            self._pending = (state, detail, progress)
            self._dwell_timer.start(int(remaining * 1000.0) + 1)
            return
        self._apply_state(state, detail, progress)

    def _flush_pending(self) -> None:
        if self._pending is None:
            return
        state, detail, progress = self._pending
        self._pending = None
        self._apply_state(state, detail, progress)

    def _apply_state(
        self, state: str, detail: str, progress: float | None = None
    ) -> None:
        if state != self._state:
            # Morph out of what is actually on screen, not out of a canned
            # shape for the old state, so an animation interrupted halfway
            # carries its current form through the change.
            self._morph_from = self._target.copy()
            self._morph_from_tint = QColor(self._tint)
            self._morph_elapsed = 0.0
        self._state = state
        self._detail = detail
        self._progress = progress
        self._state_since = time.perf_counter()

        if state == "listening":
            self._history[:] = 0.0
            self._mic_level = 0.0
            self._slice_accum = 0.0
            self._hide_timer.stop()
            self.show_bar()
        elif state in ("transcribing", "loading"):
            self._hide_timer.stop()
            self.show_bar()
        elif state in ("done", "empty", "error", "loaded", "notice"):
            self._burst = (1.0 - _CENTRE_D ** 1.5).astype(np.float32)
            self.show_bar()
            if state == "error":
                hide_after = 2600
            elif state == "notice":
                hide_after = self._notice_duration_ms or 3600
            else:
                hide_after = self._linger_ms
            self._hide_timer.start(hide_after)
            if state == "error":
                self._toast_on_click = None
                self._toast.show_message(
                    detail or "Something isn't working", self.pill_geometry(), dot_color=ERROR
                )
            elif state == "notice":
                dot = SUCCESS if self._notice_tone == "success" else self._accent
                self._toast.show_message(detail, self.pill_geometry(), dot_color=dot)
        elif state == "idle":
            if self._settings.always_visible:
                self.show_bar()             # keeps the clock alive for the morph
            else:
                self._hide_timer.start(600)
        self.update()

    def set_levels(self, levels: np.ndarray) -> None:
        """Take the newest meter reading. Deliberately does not repaint.

        Reduced to a single loudness value because the bars show a *history*
        of how loud you have been rather than a spectrum, and RMS across the
        bands tracks speech far more honestly than any one band does.
        """
        if levels.size:
            self._mic_level = float(np.sqrt(np.mean(np.square(levels))))

    # --- reveal ---

    def _get_reveal(self) -> float:
        return self._reveal

    def _set_reveal(self, value: float) -> None:
        self._reveal = value
        self.update()

    reveal = Property(float, _get_reveal, _set_reveal)

    def show_bar(self) -> None:
        if not self.isVisible():
            self.reposition()
            self.show()
        running = self._reveal_anim.state() == QPropertyAnimation.Running
        if not running or self._reveal_anim.endValue() != 1.0:
            self._reveal_anim.stop()
            self._reveal_anim.setDuration(ENTER_MS)
            self._reveal_anim.setEasingCurve(FLUENT_DECELERATE)
            self._reveal_anim.setStartValue(self._reveal)
            self._reveal_anim.setEndValue(1.0)
            self._reveal_anim.start()
        if not self._timer.isActive():
            self._last_tick = time.perf_counter()   # ignore the stopped span
            self._timer.start()

    def _auto_hide(self) -> None:
        # Dismissed here, at the exact moment the bar itself starts leaving,
        # rather than on its own independent timer -- both then run the same
        # EXIT_MS/FLUENT_ACCELERATE fade, so a toast never lingers after the
        # bar it is anchored to has already gone.
        self._toast.dismiss()
        if self._settings.always_visible:
            self._apply_state("idle", "")
            return
        self._reveal_anim.stop()
        self._reveal_anim.setDuration(EXIT_MS)
        self._reveal_anim.setEasingCurve(FLUENT_ACCELERATE)
        self._reveal_anim.setStartValue(self._reveal)
        self._reveal_anim.setEndValue(0.0)
        self._reveal_anim.start()
        QTimer.singleShot(EXIT_MS + 20, self._finish_hide)

    def _finish_hide(self) -> None:
        if self._reveal > 0.05:
            return
        self._timer.stop()
        self.hide()
        # Reset while nothing is on screen, so the next appearance starts from
        # a clean idle rather than morphing out of a shape from minutes ago.
        self._state = "idle"
        self._state_since = time.perf_counter() - MIN_DWELL_SECONDS
        for arr in (self._drawn, self._vel, self._target,
                    self._burst, self._history, self._morph_from):
            arr[:] = 0.0
        self._morph_from_tint = QColor(self._palette["idle"])
        self._morph_elapsed = None
        self._sweep = 0.0
        self._tint = QColor(self._palette["idle"])

    # --- animation ---

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = now - self._last_tick
        self._last_tick = now
        if dt <= 0.0:
            return
        dt = min(dt, MAX_DT)

        self._clock = (self._clock + dt) % 86400.0
        # Held at full strength until the morph has finished delivering it.
        # Decaying from the moment the state applied meant the confirmation
        # was already 62% gone by the time it was fully on screen, so "done"
        # landed as a weak flicker rather than a pulse.
        if self._morph_elapsed is None:
            self._burst *= math.exp(-dt / BURST_TAU)

        if self._state == "listening":
            self._slice_accum += dt
            while self._slice_accum >= SLICE_SECONDS:
                self._slice_accum -= SLICE_SECONDS
                self._history = np.roll(self._history, -1)
            # The newest column keeps taking the live level for the whole slice
            # rather than being frozen when the slice opened, so the right-hand
            # end of the wave answers your voice immediately.
            self._history[-1] = self._mic_level

        # Bars collapse into the progress track only for the long wait.
        want_sweep = 1.0 if self._state == "loading" else 0.0
        self._sweep += (want_sweep - self._sweep) * (1.0 - math.exp(-dt / SWEEP_TAU))

        live = self._signal(self._state)
        if self._morph_elapsed is not None:
            self._morph_elapsed += dt
            # Every bar runs the same 200 ms morph, just started a beat later
            # than the one to its left, so a state change crosses the bar as a
            # ripple instead of every bar moving as one block.
            ramp = np.clip((self._morph_elapsed - _DELAYS) / MORPH_SECONDS, 0.0, 1.0)
            blend = ramp * ramp * (3.0 - 2.0 * ramp)
            self._target = self._morph_from * (1.0 - blend) + live * blend
            self._tint = _lerp_color(
                self._morph_from_tint,
                self._tint_for(self._state),
                float(blend.mean()),
            )
            if self._morph_elapsed >= MORPH_TOTAL:
                self._morph_elapsed = None
        else:
            self._target = live
            self._tint = self._tint_for(self._state)

        self._drawn, self._vel = _spring(
            self._drawn, self._vel, self._target, SPRING_OMEGA, SPRING_ZETA, dt
        )

        # A permanently visible idle bar should cost nothing once it has come
        # to rest, so the clock stops when there is provably nothing moving.
        if (
            self._state == "idle"
            and self._morph_elapsed is None
            and self._reveal_anim.state() != QPropertyAnimation.Running
            and self._sweep < 0.01
            and float(np.abs(self._drawn - self._target).max()) < 0.002
            and float(np.abs(self._vel).max()) < 0.01
        ):
            self._drawn[:] = self._target
            self._vel[:] = 0.0
            self._timer.stop()

        self.update()

    def _signal(self, state: str) -> np.ndarray:
        """The bar heights this state wants, before smoothing. Every branch
        returns the same shape, which is what lets any state morph into any
        other without a special case."""
        if state == "listening":
            # Slide between the two neighbouring columns instead of stepping
            # from one to the next. Without this the wave only changes when a
            # slice opens -- about fourteen times a second -- and no amount of
            # spring smoothing hides that the *content* is moving in jumps.
            frac = self._slice_accum / SLICE_SECONDS
            slid = self._history[:-1] * (1.0 - frac) + self._history[1:] * frac
            return np.clip(slid, 0.0, 1.0).astype(np.float32)
        if state == "loading":
            return np.zeros(N_BARS, dtype=np.float32)    # collapsed to the track
        if state == "transcribing":
            # Two travelling components moving in opposite directions at
            # different speeds. Where they reinforce, a crest rises; where they
            # cancel, the line dips -- so the bar undulates continuously and
            # never resolves into a pattern you can predict. Sines are
            # inherently periodic, so unlike a hump on a wrapping phase there
            # is no seam to correct anywhere.
            phase = self._clock * TRANSCRIBE_HZ
            forward = 0.5 + 0.5 * np.sin(2.0 * np.pi * (_U - phase))
            back = 0.5 + 0.5 * np.sin(
                2.0 * np.pi * (_U * TRANSCRIBE_HARMONIC + phase * 0.55 + 0.37)
            )
            mixed = 0.62 * forward + 0.38 * back
            # Ease the ends down a little so the wave reads as belonging to the
            # bar rather than being cut off by it.
            # Clipped before the fractional power: sin(pi*u) lands a hair below
            # zero at the endpoints in float32, and a negative raised to 0.7 is
            # NaN, which silently blanks the whole bar.
            envelope = 0.58 + 0.42 * np.clip(np.sin(np.pi * _U), 0.0, 1.0) ** 0.7
            return (
                TRANSCRIBE_FLOOR + TRANSCRIBE_SWING * mixed * envelope
            ).astype(np.float32)
        if state == "error":
            pulse = 0.5 - 0.5 * math.cos(2.0 * math.pi * self._clock * ERROR_HZ)
            return np.full(N_BARS, 0.22 + 0.40 * pulse, dtype=np.float32)
        if state in ("done", "empty", "loaded", "notice"):
            return self._burst
        return np.zeros(N_BARS, dtype=np.float32)

    def _tint_for(self, state: str) -> QColor:
        """One accent for every working state. Colour is reserved for things
        that genuinely differ: a success worth noticing and a real failure."""
        if state == "error":
            return ERROR
        if state in ("done", "loaded"):
            return SUCCESS
        if state == "notice":
            return SUCCESS if self._notice_tone == "success" else self._accent
        if state == "idle":
            return self._palette["idle"]
        return self._accent

    # --- painting ---

    def paintEvent(self, event) -> None:
        if self._reveal <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Fade, rise and scale all come off the same value, so they cannot drift.
        scale = 0.92 + 0.08 * self._reveal
        cx, cy = SHADOW_PAD + PILL_W / 2.0, SHADOW_PAD + PILL_H / 2.0
        p.translate(0.0, RISE_PX * (1.0 - self._reveal))
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        # Both cached bitmaps are built for this display's scale factor, so
        # the bar is as crisp at 150% on a 4K panel as at 100% on a 1080p one.
        dpr = self.devicePixelRatioF()

        p.setOpacity(self._reveal)
        p.drawPixmap(0, 0, _shadow(dpr, self._dark))

        pill = QRectF(SHADOW_PAD + 0.5, SHADOW_PAD + 0.5, PILL_W - 1, PILL_H - 1)
        path = QPainterPath()
        path.addRoundedRect(pill, RADIUS, RADIUS)
        p.fillPath(path, self._palette["surface"])
        p.fillPath(path, QBrush(_noise_tile(dpr, self._dark)))   # the grain in Acrylic
        p.setPen(QPen(self._palette["stroke"], 1))
        p.drawPath(path)
        p.setPen(Qt.NoPen)

        # The hairline is the one thing that never moves or fades, so the bars
        # and the sweep are only ever *on* it. Drawing it once, outside the
        # crossfade, is what stops the collapse into loading from reading as
        # one widget being swapped for another.
        cy = SHADOW_PAD + PILL_H / 2.0
        x0 = SHADOW_PAD + PAD
        hairline = QPainterPath()
        hairline.addRoundedRect(
            QRectF(x0, cy - TRACK_H / 2, WAVE_W, TRACK_H), TRACK_H / 2, TRACK_H / 2
        )
        p.fillPath(hairline, self._palette["track"])

        if self._sweep < 0.995:
            p.setOpacity(self._reveal * (1.0 - self._sweep))
            self._paint_bars(p)
        if self._sweep > 0.005:
            p.setOpacity(self._reveal * self._sweep)
            if self._state == "loading" and self._progress is not None:
                self._paint_determinate(p, self._progress)
            else:
                self._paint_sweep(p)

    def _paint_bars(self, p: QPainter) -> None:
        """Slim capsules growing out of the hairline.

        A capsule fades in over its first few pixels of lift rather than
        appearing at full accent the moment it leaves the line. Without that,
        silence is a row of accent dots sitting on a grey rule -- which reads
        as a dashed line rather than as the clean unbroken one this is meant
        to be -- and the faintness of a quiet voice becomes part of the
        signal instead of an on/off edge.
        """
        cy = SHADOW_PAD + PILL_H / 2.0
        x0 = SHADOW_PAD + PAD
        base_alpha = self._tint.alphaF()

        for i in range(N_BARS):
            h = max(MIN_BAR_H, float(self._drawn[i]) * MAX_BAR_H)
            lift = min(1.0, (h - MIN_BAR_H) / LIFT_FADE_PX)
            if lift <= 0.004:
                continue                       # flush in the line; nothing to add
            colour = QColor(self._tint)
            colour.setAlphaF(base_alpha * lift)
            capsule = QPainterPath()
            capsule.addRoundedRect(
                QRectF(x0 + i * (BAR_W + BAR_GAP), cy - h / 2, BAR_W, h),
                BAR_R, BAR_R,
            )
            p.fillPath(capsule, colour)

    def _paint_sweep(self, p: QPainter) -> None:
        """Windows' indeterminate ProgressBar, running along the same hairline
        the bars just sank into: two accent segments, the longer one leading."""
        cy = SHADOW_PAD + PILL_H / 2.0
        x0 = SHADOW_PAD + PAD
        width = WAVE_W

        for offset, frac, speed in SWEEP_SEGMENTS:
            phase = ((self._clock * SWEEP_HZ * speed) + offset) % 1.0
            eased = _smoothstep(phase)          # accelerates through the middle
            seg = width * frac
            x = x0 + eased * (width + seg) - seg
            left, right = max(x0, x), min(x0 + width, x + seg)
            if right <= left:
                continue
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(left, cy - SWEEP_H / 2, right - left, SWEEP_H),
                SWEEP_H / 2, SWEEP_H / 2,
            )
            # Bright at the leading edge, thinning toward the tail, so each
            # segment reads as having a direction rather than being a bar of
            # colour that happens to be sliding.
            head, tail = self._tint, QColor(self._tint)
            tail.setAlphaF(self._tint.alphaF() * 0.25)
            grad = QLinearGradient(x, cy, x + seg, cy)
            grad.setColorAt(0.0, tail)
            grad.setColorAt(1.0, head)
            p.fillPath(path, QBrush(grad))

    def _paint_determinate(self, p: QPainter, fraction: float) -> None:
        """A filling hairline once a download reports real byte progress.

        Replaces the indeterminate sweep on the same track rather than
        introducing a second widget, for the same reason the sweep collapses
        onto the hairline instead of appearing beside it.
        """
        cy = SHADOW_PAD + PILL_H / 2.0
        x0 = SHADOW_PAD + PAD
        filled = WAVE_W * float(np.clip(fraction, 0.0, 1.0))
        if filled <= 0.5:
            return
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(x0, cy - SWEEP_H / 2, filled, SWEEP_H), SWEEP_H / 2, SWEEP_H / 2
        )
        tail = QColor(self._tint)
        tail.setAlphaF(self._tint.alphaF() * 0.5)
        grad = QLinearGradient(x0, cy, x0 + filled, cy)
        grad.setColorAt(0.0, tail)
        grad.setColorAt(1.0, self._tint)
        p.fillPath(path, QBrush(grad))


def _spring(
    pos: np.ndarray,
    vel: np.ndarray,
    target: np.ndarray,
    omega: float,
    zeta: float,
    dt: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Advance an under-damped spring by solving it exactly.

    The closed form of a damped harmonic oscillator rather than any stepped
    integrator, for two reasons. A CPU transcription can starve the UI thread
    for tens of milliseconds, and an explicit integrator handed a step that
    long goes unstable and rings -- this is exact at any step size, so a
    dropped frame costs a frame and nothing else. And it takes the damping
    ratio as a parameter, which the implicit critically damped form could not:
    the ~5% overshoot at zeta 0.7 is what makes a bar landing on its value
    feel like it arrived rather than merely stopped.
    """
    offset = pos - target
    damped = omega * math.sqrt(1.0 - zeta * zeta)
    decay = math.exp(-zeta * omega * dt)
    cos_t = math.cos(damped * dt)
    sin_t = math.sin(damped * dt)
    a = offset
    b = (vel + zeta * omega * offset) / damped
    swing = a * cos_t + b * sin_t
    new_pos = target + decay * swing
    new_vel = decay * (-zeta * omega * swing + damped * (b * cos_t - a * sin_t))
    return new_pos.astype(np.float32), new_vel.astype(np.float32)


def _smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
        int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
    )


class Toast(QWidget):
    """A small Fluent alert glued above the bar -- a real failure (no
    microphone, a model that failed to load), or an informational notice
    (an update check result) that would otherwise have been a separate
    Windows system tray balloon. The bar changing colour says *something*
    happened; this says *what*.

    Deliberately has no auto-dismiss timer of its own: whoever shows it also
    owns a Bar hide timer, and calls dismiss() from that same callback, so
    the toast's exit and the bar's exit are the same animation started at
    the same instant rather than two clocks that can drift apart.
    """

    PAD = 12
    GAP = 8       # px between the toast's bottom edge and the pill's top edge
    HEIGHT = 38
    MAX_W = 320
    RADIUS = 8

    clicked = Signal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(0.0)
        self.setFixedSize(self.MAX_W, self.HEIGHT)

        self._text = ""
        self._dot_color = ERROR
        self._dark = system_is_dark()
        self._font = QFont("Segoe UI Variable Text", 9)
        if not self._font.exactMatch():
            self._font = QFont("Segoe UI", 9)

        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._slide_anim = QPropertyAnimation(self, b"pos", self)

    def set_theme(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def show_message(self, text: str, anchor_rect: QRect, dot_color: QColor = ERROR) -> None:
        self._text = text
        self._dot_color = dot_color

        metrics = QFontMetrics(self._font)
        w = max(160, min(self.MAX_W, metrics.horizontalAdvance(text) + self.PAD * 2 + 22))
        self.setFixedSize(w, self.HEIGHT)

        end_pos = self._anchor_pos(anchor_rect)
        start_pos = QPoint(end_pos.x(), end_pos.y() + 10)
        self.move(start_pos)
        self.show()
        self.raise_()

        for anim, start, end in (
            (self._opacity_anim, self.windowOpacity(), 1.0),
            (self._slide_anim, start_pos, end_pos),
        ):
            anim.stop()
            anim.setDuration(ENTER_MS)
            anim.setEasingCurve(FLUENT_DECELERATE)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.start()

        self.update()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()

    def follow(self, anchor_rect: QRect) -> None:
        """Keep glued to the bar if it moves while the toast is visible."""
        if self.isVisible():
            self.move(self._anchor_pos(anchor_rect))

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        for anim, start, end in (
            (self._opacity_anim, self.windowOpacity(), 0.0),
            (self._slide_anim, self.pos(), QPoint(self.x(), self.y() + 10)),
        ):
            anim.stop()
            anim.setDuration(EXIT_MS)
            anim.setEasingCurve(FLUENT_ACCELERATE)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.start()
        QTimer.singleShot(EXIT_MS + 20, self._finish_hide)

    def _finish_hide(self) -> None:
        if self.windowOpacity() < 0.05:
            self.hide()

    def _anchor_pos(self, anchor_rect: QRect) -> QPoint:
        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.top() - self.height() - self.GAP
        return QPoint(x, y)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)
        palette = colors(self._dark)
        p.fillPath(path, palette["surface"])
        p.fillPath(path, QBrush(_noise_tile(self.devicePixelRatioF(), self._dark)))
        p.setPen(QPen(palette["stroke"], 1))
        p.drawPath(path)

        cy = self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(self._dot_color)
        p.drawEllipse(QRectF(self.PAD, cy - 4, 8, 8))

        p.setFont(self._font)
        p.setPen(palette["text"])
        rect = QRectF(self.PAD + 16, 0, self.width() - self.PAD * 2 - 16, self.height())
        metrics = QFontMetrics(self._font)
        p.drawText(
            rect,
            Qt.AlignVCenter | Qt.AlignLeft,
            metrics.elidedText(self._text, Qt.ElideRight, int(rect.width())),
        )
