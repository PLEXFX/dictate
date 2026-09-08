"""The floating bar: a small Windows 11 flyout centred above the taskbar.

Drawn by hand rather than assembled from widgets. A solid Fluent surface keeps
the compact flyout legible in both light and dark mode, with a compact capsule shape, a
1px light stroke, and an ambient shadow so the bar sits *above* the taskbar.

Fluent gets its polish from material and geometry, not from lighting. There is
no glow anywhere in this file on purpose: bloom is Apple's vocabulary, and it
is what made an earlier version of this bar look like a media player.

Everything is sized in logical pixels and the cached shadow is rebuilt per
device pixel ratio, so the bar is as crisp at 150% on a 4K panel as at 100% on
a 1080p one.

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

import ctypes
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
from theme import (
    colors,
    resolve_dark,
    system_is_dark,
)

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
BAR_GAP = 4.0
BAR_R = BAR_W / 2      # a true capsule reads sleeker than a rounded rectangle
WAVE_W = N_BARS * BAR_W + (N_BARS - 1) * BAR_GAP

PAD = 18
PILL_W = int(2 * PAD + WAVE_W)
PILL_H = 36
RADIUS = PILL_H / 2    # Compact capsule approved in the Settings preview
SHADOW_PAD = 12        # room around the pill for the ambient shadow
PILL_TOP = SHADOW_PAD
WIDTH = PILL_W + 2 * SHADOW_PAD
HEIGHT = PILL_TOP + PILL_H + SHADOW_PAD
MAX_BAR_H = PILL_H - 12

# The pill's own width range. The floor matches the "done" receipt; the
# ceiling is sized for a full error/notice sentence on one line now that
# message text lives inline instead of in a separate floating toast (see
# _label_width) -- well past the 145-280 range Settings' own width slider
# uses, which config.py clamps independently, so raising this never lets
# that slider go wider than it already promises.
PILL_W_MIN = 145.0
PILL_W_MAX = 420.0
DEFAULT_ERROR_TEXT = "Something isn't working"

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
# The click bounce (locked recording only, see set_clickable) reuses this same
# closed-form spring rather than a separate canned animation -- "everything is
# sprung" applies to the one interactive gesture the bar has, not just the
# capsules. Tuned tighter and snappier than SPRING_OMEGA/ZETA on purpose: the
# capsules are meant to read as breath, loose and organic; a button press in
# Windows is solid and barely overshoots. Reusing the capsules' looser spring
# here would make a click feel like it wobbles instead of clicks.
PRESS_OMEGA = 90.0
PRESS_ZETA = 0.85
PRESS_DIP = 0.97   # how far the pill shrinks on press; subtle, not a bounce toy
# Width follows a critically damped spring instead of restarting a canned
# animation on every slider step. Preserving velocity is what makes a drag
# feel like one continuous gesture rather than a stack of interrupted tweens.
WIDTH_OMEGA = 14.0
WIDTH_ZETA = 1.0
# The taskbar gap moves the whole native window, where discrete slider values
# are especially obvious. A critically damped spring keeps the bar attached to
# the person's drag without the stepped movement of calling move() for every
# individual slider value.
MARGIN_OMEGA = 18.0
MARGIN_ZETA = 1.0
# Fluent point-to-point motion is 150-250 ms. An earlier build stacked a
# 350 ms dwell floor, a 200 ms morph and a 144 ms ripple, so a state change
# could take 694 ms end to end and read as lag. These are sized so the worst
# case is 470 ms and the common case is 230 ms.
MORPH_SECONDS = 0.15
# The idle squish (bar.py's _idle_mix, below) has to stay behind the bar
# heights collapsing to the hairline -- otherwise the wave visibly narrows
# while still-tall capsules are inside it, reading as a squashed accordion
# rather than bars settling into a quiet line. This is slower than the
# 0.045 s crossfade used for content/lock so the squish only becomes
# noticeable once the morph above has mostly finished.
IDLE_SQUISH_SECONDS = 0.16
# Bars begin their morph a beat apart rather than all at once, so a state
# change ripples across the bar instead of snapping as one block. 21 bars at
# 4 ms spread it over 80 ms -- enough to read as a ripple, short enough that
# the whole morph still lands inside the dwell floor below.
STAGGER_SECONDS = 0.002
MIN_DWELL_SECONDS = 0.0   # state feedback never waits behind a previous animation
BURST_TAU = 0.24
SWEEP_TAU = 0.07           # how fast the bars collapse into the progress track
SLICE_SECONDS = 0.07       # one new amplitude column; 21 of them ~= 1.5 s
# Transcribing is two travelling sine components rather than one hump: a
# single lump sliding along reads as "a thing moving" and has no character,
# and it has to be wrap-corrected. Two components crossing at different speeds
# and in opposite directions interfere, so the line undulates and never
# settles into an obvious loop -- and being sines, there is no seam at all.
TRANSCRIBE_HZ = 0.75        # speed of the primary component
TRANSCRIBE_HARMONIC = 2.0  # crests across the bar in the counter-moving one
# A low floor lets the troughs sink most of the way back into the hairline,
# so the crests stand out instead of the whole bar reading as a dense block.
TRANSCRIBE_FLOOR = 0.10
TRANSCRIBE_SWING = 0.32
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

# Loudness nudges the listening accent brighter/more saturated rather than
# changing colour outright -- colour is still reserved for states that
# genuinely differ. Smoothed on its own, slower spring so it tracks the
# *envelope* of your voice, not every frame of mic jitter the way the
# capsules themselves do.
LOUD_GLOW_OMEGA = 14.0
# _spring_scalar's closed form divides by sqrt(1 - zeta^2), so 1.0 (exactly
# critical) is a division by zero. 0.98 is indistinguishable from critical --
# no visible overshoot -- while staying just inside the solver's domain.
LOUD_GLOW_ZETA = 0.98
LOUD_GLOW_SAT = 0.18
LOUD_GLOW_LIGHT = 0.10

# A permanently visible idle bar (Settings: "always visible") breathes very
# slowly at its centre rather than sitting dead flat, so it reads as alive
# and listening rather than merely undismissed. Amplitude stays under
# LIFT_FADE_PX at its peak so it is felt more than seen.
IDLE_BREATH_HZ = 0.12          # ~8.3 s per cycle
IDLE_BREATH_AMPLITUDE = 0.16
IDLE_BREATH_SPAN = 1.6         # how far from centre the breathing reaches

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


# --- cached shadow ----------------------------------------------------------

def _box_blur(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    k = 2 * radius + 1
    pad = [(0, 0)] * a.ndim
    pad[axis] = (radius, radius)
    c = np.cumsum(np.pad(a, pad, mode="constant"), axis=axis)
    c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
    n = a.shape[axis]
    return (np.take(c, range(k, k + n), axis=axis)
            - np.take(c, range(0, n), axis=axis)) / k


def _surface_path(
    *,
    pill_width: float = PILL_W,
    y_offset: float = 0.0,
    pixel_inset: float = 0.0,
) -> QPainterPath:
    """The bar's outer silhouette, shared by the shadow and the outline.

    Building one path for both removes the internal border that made the
    surface look like stacked boxes.
    """
    pill = QPainterPath()
    pill.addRoundedRect(
        QRectF(
            SHADOW_PAD + pixel_inset,
            PILL_TOP + y_offset + pixel_inset,
            pill_width - 2 * pixel_inset,
            PILL_H - 2 * pixel_inset,
        ),
        RADIUS,
        RADIUS,
    )
    return pill


def _shadow(
    dpr: float,
    dark: bool = True,
    pill_width: float = PILL_W,
) -> QPixmap:
    """Ambient elevation under the bar, cached per width and scale factor.

    Rendered at the display's device pixel ratio and tagged with it, so the
    shadow is as sharp on a 4K 150% display as on a 1080p one. A single 1x
    bitmap scaled up is exactly the kind of soft, slightly wrong edge that
    makes a hand-painted window look unlike the rest of the shell.
    """
    pill_width = float(round(pill_width))
    key = ("shadow", round(dpr, 3), dark, pill_width)
    if key in _CACHE:
        return _CACHE[key]
    logical_width = pill_width + 2 * SHADOW_PAD
    width, height = int(round(logical_width * dpr)), int(round(HEIGHT * dpr))
    layer = QImage(width, height, QImage.Format_ARGB32)
    layer.fill(Qt.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.scale(dpr, dpr)
    path = _surface_path(pill_width=pill_width, y_offset=2.0)
    shadow_color = QColor(colors(dark)["shadow"])
    shadow_color.setAlpha(round(shadow_color.alpha() * 0.76))
    p.fillPath(path, shadow_color)
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


def _animations_enabled() -> bool:
    """Follow Windows Accessibility > Visual effects > Animation effects."""
    enabled = ctypes.c_int(1)
    try:
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


class Bar(QWidget):
    clicked = Signal()   # only fires while set_clickable(True) -- see mouseReleaseEvent

    def __init__(self, settings):
        super().__init__(None)
        self._settings = settings
        self._dark = resolve_dark(settings.theme_mode)
        self._palette = colors(self._dark)
        self._reduced_motion = not _animations_enabled()
        self._state = "idle"
        self._detail = ""
        self._progress: float | None = None  # 0..1 once a download reports real bytes
        self._notice_tone = "info"       # "info" or "success" -- picks the notice's dot colour
        self._notice_duration_ms: int | None = None
        self._notice_on_click = None     # optional callback for an actionable notice
        self._linger_ms = settings.bar_linger_ms
        self._margin_value = float(settings.bar_margin)
        self._margin_target = self._margin_value
        self._margin_velocity = 0.0
        self._margin_last_tick = time.perf_counter()
        self._pill_width = float(settings.bar_width)
        self._width_target = self._pill_width
        self._width_velocity = 0.0
        self._width_last_tick = time.perf_counter()
        self._width_anchor_x: float | None = None

        self._content = {"wave": 1.0, "done": 0.0, "loaded": 0.0, "error": 0.0, "notice": 0.0}
        self._lock_mix = 0.0
        self._idle_mix = 1.0
        self._clickable = False          # only true while a locked recording is running
        self._press_scale = 1.0
        self._press_vel = 0.0
        self._press_target = 1.0

        self._loud_glow = 0.0
        self._loud_glow_vel = 0.0

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
        # Keep transparent pixels click-through unless a locked recording has
        # explicitly armed the bar's finish-on-click action.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self._window_width(), HEIGHT)

        # Everything about the entrance -- fade, scale and rise -- is driven
        # from this one value, so the three can never disagree.
        self._reveal_anim = QPropertyAnimation(self, b"reveal", self)

        self._width_timer = QTimer(self)
        self._width_timer.setInterval(16)
        self._width_timer.setTimerType(Qt.PreciseTimer)
        self._width_timer.timeout.connect(self._tick_width)

        self._margin_timer = QTimer(self)
        self._margin_timer.setInterval(16)
        self._margin_timer.setTimerType(Qt.PreciseTimer)
        self._margin_timer.timeout.connect(self._tick_margin)

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
        """Apply the resolved light/dark appearance without interrupting dictation."""
        if dark == self._dark:
            return
        self._dark = dark
        self._palette = colors(dark)
        self._accent = system_accent(dark)
        if self._state == "idle":
            self._tint = QColor(self._palette["idle"])
        self.update()

    # --- placement ---

    def _state_width(self) -> int:
        if self._state == "done":
            return 145
        if self._state == "error":
            return self._label_width(self._detail or DEFAULT_ERROR_TEXT)
        if self._state == "notice":
            return self._label_width(self._detail)
        if self._state == "loaded":
            return self._label_width(self._detail or "Ready")
        if self._state == "loading" and self._progress is not None and self._detail:
            return self._label_width(self._detail, has_icon=False)
        return self._settings.bar_width

    def _label_width(self, text: str, has_icon: bool = True) -> int:
        """Pill width that fits ``text`` (plus its icon, if any) on one line
        with a little breathing room -- so a real error/notice message never
        has to be clipped down to a canned short label the way it did when
        the full text lived in a separate floating toast instead of here.
        """
        font = QFont("Segoe UI Variable Text", 10 if has_icon else 9)
        text_w = QFontMetrics(font).horizontalAdvance(text)
        icon_allowance = 25.0 if has_icon else 0.0
        width = text_w + icon_allowance + 2 * PAD + 16
        return int(max(PILL_W_MIN, min(PILL_W_MAX, width)))

    def _window_width(self) -> int:
        return int(round(self._pill_width)) + 2 * SHADOW_PAD

    def _wave_width(self) -> float:
        return max(1.0, self._pill_width - 2 * PAD)

    def _bar_gap(self) -> float:
        return max(0.0, (self._wave_width() - N_BARS * BAR_W) / (N_BARS - 1))

    def _set_pill_width(self, width: float) -> None:
        width = min(PILL_W_MAX, max(PILL_W_MIN, float(width)))
        if abs(width - self._pill_width) < 0.01:
            return
        anchor_x = self._width_anchor_x
        if anchor_x is None:
            anchor_x = self.x() + self.width() / 2.0
        self._pill_width = width
        self.setFixedSize(self._window_width(), HEIGHT)
        if self.isVisible():
            self.move(round(anchor_x - self.width() / 2.0), self.y())
        self.update()

    def _animate_width(self, width: int) -> None:
        target = float(min(PILL_W_MAX, max(PILL_W_MIN, width)))
        self._width_target = target
        if self._reduced_motion:
            self._width_timer.stop()
            self._width_velocity = 0.0
            self._set_pill_width(target)
            return
        if abs(target - self._pill_width) < 0.05 and abs(self._width_velocity) < 0.05:
            self._set_pill_width(target)
            return
        if not self._width_timer.isActive():
            self._width_anchor_x = self.x() + self.width() / 2.0
            self._width_last_tick = time.perf_counter()
            self._width_timer.start()

    def _tick_width(self) -> None:
        """Advance one interruption-safe width spring frame."""
        now = time.perf_counter()
        dt = min(max(0.0, now - self._width_last_tick), 0.05)
        self._width_last_tick = now
        if dt <= 0.0:
            return
        width, velocity = _spring_scalar(
            self._pill_width,
            self._width_velocity,
            self._width_target,
            WIDTH_OMEGA,
            WIDTH_ZETA,
            dt,
        )
        self._width_velocity = velocity
        self._set_pill_width(width)
        if abs(width - self._width_target) < 0.05 and abs(velocity) < 0.5:
            self._set_pill_width(self._width_target)
            self._width_velocity = 0.0
            self._width_timer.stop()
            self._width_anchor_x = None

    def _animate_margin(self, margin: int) -> None:
        """Retarget one continuous taskbar-gap spring without snapping."""
        target = float(max(0, margin))
        self._margin_target = target
        if abs(target - self._margin_value) < 0.05 and abs(self._margin_velocity) < 0.05:
            self._margin_value = target
            self._move_for_margin(target)
            return
        if not self._margin_timer.isActive():
            self._margin_last_tick = time.perf_counter()
            self._margin_timer.start()

    def _tick_margin(self) -> None:
        """Advance the taskbar-gap spring from its current, not starting, value."""
        now = time.perf_counter()
        dt = min(max(0.0, now - self._margin_last_tick), 0.05)
        self._margin_last_tick = now
        if dt <= 0.0:
            return
        margin, velocity = _spring_scalar(
            self._margin_value,
            self._margin_velocity,
            self._margin_target,
            MARGIN_OMEGA,
            MARGIN_ZETA,
            dt,
        )
        self._margin_value = margin
        self._margin_velocity = velocity
        self._move_for_margin(margin)
        if abs(margin - self._margin_target) < 0.05 and abs(velocity) < 0.5:
            self._margin_value = self._margin_target
            self._margin_velocity = 0.0
            self._move_for_margin(self._margin_target)
            self._margin_timer.stop()

    def pill_geometry(self) -> QRect:
        """Where the visible pill is on screen, ignoring the shadow margin."""
        geo = self.geometry()
        return QRect(
            geo.x() + SHADOW_PAD,
            geo.y() + PILL_TOP,
            int(round(self._pill_width)),
            PILL_H,
        )

    def _move_for_margin(self, margin: float) -> None:
        """Place the current window for one already-resolved taskbar gap."""
        screen = QApplication.screenAt(QPoint(0, 0)) or QApplication.primaryScreen()
        if screen is None:
            return
        self._sync_frame_rate(screen)
        area = screen.availableGeometry()
        x = area.center().x() - self._window_width() // 2
        y = area.bottom() + 1 - max(0.0, margin) - PILL_H - PILL_TOP
        self.move(x, round(y))

    def reposition(self) -> None:
        """Centre horizontally, sit just above the taskbar.

        availableGeometry already excludes the taskbar and is DPI-aware, so
        this lands correctly on scaled displays and follows a taskbar moved to
        another edge without any special-casing. The shadow margin is backed
        out so the configured gap applies to the pill, not to the window.
        """
        self._move_for_margin(self._margin_value)
        if self._width_timer.isActive():
            # This move just recomputed the true on-screen centre from real
            # screen geometry. If the width spring is mid-flight, its next
            # tick calls _set_pill_width(), which re-derives x from
            # _width_anchor_x rather than from the screen -- a stale anchor
            # (e.g. captured while the bar was hidden at a leftover position)
            # would silently overwrite the correct position just set above,
            # snapping the bar away from centre on the very next frame.
            self._width_anchor_x = self.x() + self.width() / 2.0
        else:
            self._width_anchor_x = None

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
        # Both variants go, because the user may have changed the Windows
        # accent while this window was open. The key is the (name, dark) pair
        # system_accent() caches under -- popping a bare "accent" string
        # matched nothing and quietly left the old colour in place forever.
        _CACHE.pop(("accent", True), None)
        _CACHE.pop(("accent", False), None)
        # Must pass the resolved theme: the no-argument call defaults to
        # dark=True, which repainted the bar in the dark-mode accent after any
        # settings save while the app was in light mode.
        self._accent = system_accent(self._dark)
        self._linger_ms = settings.bar_linger_ms
        if abs(self._width_target - self._state_width()) >= 0.5:
            self._animate_width(self._state_width())
        if abs(self._margin_target - settings.bar_margin) >= 0.5:
            self._animate_margin(settings.bar_margin)
        if self._state == "idle":
            if settings.always_visible:
                # A just-disabled preview/linger timer must not win after the
                # person explicitly asks for the idle bar to stay on screen.
                self._hide_timer.stop()
                self.show_bar()
            elif self.isVisible():
                # Settings changes do not pass through _apply_state(), so
                # turning this preference off used to leave an already-idle
                # bar visible until the next dictation happened to return to
                # idle. Start the ordinary exit immediately instead.
                self._hide_timer.stop()
                self._auto_hide()
        if not self._margin_timer.isActive():
            self.reposition()

    def preview_width(self, width: int) -> None:
        """Preview a candidate width with the same Fluent resize used on save."""
        self._animate_width(width)
        if self._state == "idle":
            self.show_bar()
        self._hide_timer.start(3000)

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
        self._animate_margin(margin)
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
        """Show an informational notice inline in the bar itself, expanding
        the pill to fit the message on one line -- the same surface every
        other state already speaks from, rather than a separate floating
        window bolted on above it.
        """
        self._notice_tone = tone
        self._notice_duration_ms = duration_ms
        self._notice_on_click = on_click
        self._refresh_mouse_transparency()
        self.set_state("notice", text)

    def set_state(
        self, state: str, detail: str = "", progress: float | None = None
    ) -> None:
        """Request a state, honouring the dwell floor.

        A model that loads in 200 ms would otherwise put the loading sweep on
        screen and rip it away before it had visibly done anything. Holding
        the next state back until the current one has had its turn makes that
        a short complete animation instead of a flicker.

        ``progress`` is only meaningful for "loading": a 0..1 fraction once a
        first-time model download, a GPU-runtime download, or an update
        download reports real bytes, switching the track from the
        indeterminate sweep to a fill. Left ``None`` for an ordinary cached
        load, which stays on the sweep. Whenever it carries a live
        percentage, ``detail`` is painted as a small caption above the track
        (see paintEvent) and the pill widens to fit it -- kept current on
        every call, not just the first, so the caption tracks the fill
        instead of freezing at whatever it said when downloading started.
        """
        if state == self._state:
            self._pending = None            # latest request wins; it says "stay"
            self._dwell_timer.stop()
            self._detail = detail
            self._progress = progress
            self._animate_width(self._state_width())
            self.update()
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
        if state != "notice" and self._notice_on_click is not None:
            # A new state means whatever the last notice offered to do on
            # click is no longer on screen to click -- an idle bar must not
            # keep the pointing-hand cursor from a message that already left.
            self._notice_on_click = None
            self._refresh_mouse_transparency()
        if state != self._state:
            # Morph out of what is actually on screen (_drawn), not out of
            # wherever the spring happened to be heading (_target) -- the
            # spring lags its target and overshoots by ~5%, so at the instant
            # a state changes those two can differ slightly. Starting from
            # _drawn means an animation interrupted mid-flight always carries
            # its true current shape into the change, with no last-instant pop.
            self._morph_from = self._drawn.copy()
            self._morph_from_tint = QColor(self._tint)
            self._morph_elapsed = 0.0
        self._state = state
        self._detail = detail
        self._progress = progress
        self._state_since = time.perf_counter()
        self._animate_width(self._state_width())

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
        elif state == "idle":
            if self._settings.always_visible:
                self.show_bar()             # keeps the clock alive for the morph
            else:
                self._hide_timer.start(600)
        self.update()

    def set_clickable(self, clickable: bool) -> None:
        """Arm or disarm the bar's own click gesture.

        Deliberately not tied to ``_state`` -- a locked recording keeps the
        ordinary "listening" state throughout (main.py's choice, so the tap
        -to-lock feature never had to touch this file's state machine).
        main.py calls this exactly when clicking the bar would mean
        something: on while a locked recording is running, off the moment it
        ends. A click while this is off does not bounce or fire ``clicked`` --
        an idle bar that visibly reacts to a click but does nothing reads as
        broken, not premium.
        """
        self._clickable = clickable
        self._refresh_mouse_transparency()

    def _refresh_mouse_transparency(self) -> None:
        """WA_TransparentForMouseEvents and the cursor both follow whichever
        click gesture is currently armed -- the tap-to-lock finish click
        (set_clickable) or an actionable notice's on_click -- so a bar with
        neither active stays fully click-through, and either one alone still
        looks and behaves exactly as it did before the other existed.
        """
        active = self._clickable or self._notice_on_click is not None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not active)
        self.setCursor(Qt.PointingHandCursor if active else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if not (self._clickable or self._notice_on_click is not None):
            return
        self._press_target = PRESS_DIP
        if not self._timer.isActive():
            self._last_tick = time.perf_counter()
            self._timer.start()

    def mouseReleaseEvent(self, event) -> None:
        if not (self._clickable or self._notice_on_click is not None):
            return
        self._press_target = 1.0
        if not self.rect().contains(event.position().toPoint()):
            return
        if self._notice_on_click is not None:
            callback = self._notice_on_click
            self._notice_on_click = None
            self._refresh_mouse_transparency()
            callback()
        elif self._clickable:
            self.clicked.emit()

    def set_levels(self, levels: np.ndarray) -> None:
        """Take the newest meter reading. Deliberately does not repaint.

        Reduced to a single loudness value because the bars show a *history*
        of how loud you have been rather than a spectrum, and RMS across the
        bands tracks speech far more honestly than any one band does.
        """
        if levels.size:
            self._mic_level = float(np.sqrt(np.mean(np.square(levels))))

    def set_live_text(self, text: str) -> None:
        """Intentionally ignore provisional transcript text.

        The final, pasted transcription is the only text Dictate presents; see
        DESIGN.md. Kept as a no-op so an accidental call from a future
        streaming experiment is harmless rather than a crash. The previous
        implementation lived on below an unconditional ``return`` -- git
        history has it if the preview is ever rebuilt.
        """
        return

    # --- reveal ---

    def _get_reveal(self) -> float:
        return self._reveal

    def _set_reveal(self, value: float) -> None:
        self._reveal = value
        self.update()

    reveal = Property(float, _get_reveal, _set_reveal)

    def show_bar(self) -> None:
        self._reduced_motion = not _animations_enabled()
        if not self.isVisible():
            self.reposition()
            self.show()
        running = self._reveal_anim.state() == QPropertyAnimation.Running
        if not running or self._reveal_anim.endValue() != 1.0:
            self._reveal_anim.stop()
            self._reveal_anim.setDuration(0 if self._reduced_motion else ENTER_MS)
            self._reveal_anim.setEasingCurve(FLUENT_DECELERATE)
            self._reveal_anim.setStartValue(self._reveal)
            self._reveal_anim.setEndValue(1.0)
            self._reveal_anim.start()
        if not self._timer.isActive():
            self._last_tick = time.perf_counter()   # ignore the stopped span
            self._timer.start()

    def _auto_hide(self) -> None:
        if self._settings.always_visible:
            self._apply_state("idle", "")
            return
        self._reveal_anim.stop()
        self._reveal_anim.setDuration(0 if self._reduced_motion else EXIT_MS)
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
        self._content = {"wave": 1.0, "done": 0.0, "loaded": 0.0, "error": 0.0, "notice": 0.0}
        self._lock_mix = 0.0
        self._idle_mix = 1.0
        self._width_timer.stop()
        self._width_velocity = 0.0
        self._width_target = float(self._settings.bar_width)
        self._set_pill_width(self._width_target)
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

        if not self._reduced_motion:
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

        self._press_scale, self._press_vel = _spring_scalar(
            self._press_scale, self._press_vel, self._press_target,
            PRESS_OMEGA, PRESS_ZETA, dt,
        )
        # Tracks the mic envelope while listening and relaxes to 0 the moment
        # it stops, so the tint glow never lingers into another state.
        target_glow = self._mic_level if self._state == "listening" else 0.0
        self._loud_glow, self._loud_glow_vel = _spring_scalar(
            self._loud_glow, self._loud_glow_vel, target_glow,
            LOUD_GLOW_OMEGA, LOUD_GLOW_ZETA, dt,
        )

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

        # Crossfade from the currently rendered composition on every interrupt.
        content_target = (
            self._state if self._state in ("done", "loaded", "error", "notice") else "wave"
        )
        blend = 1.0 if self._reduced_motion else 1.0 - math.exp(-dt / 0.045)
        for name in self._content:
            self._content[name] += (float(name == content_target) - self._content[name]) * blend
        self._lock_mix += (float(self._clickable and self._state == "listening") - self._lock_mix) * blend
        # Squishing the wave narrower is only allowed to start once the bar
        # heights have actually finished collapsing to the hairline (morph
        # complete) -- otherwise the width closes in around still-tall
        # capsules and reads as a squashed accordion instead of bars settling
        # quietly before the track narrows. Leaving idle un-squishes right
        # away, since there is nothing tall inside yet for it to clip.
        idle_target = 1.0 if (self._state == "idle" and self._morph_elapsed is None) else 0.0
        idle_blend = 1.0 if self._reduced_motion else 1.0 - math.exp(-dt / IDLE_SQUISH_SECONDS)
        self._idle_mix += (idle_target - self._idle_mix) * idle_blend

        if self._reduced_motion:
            self._drawn[:] = live
            self._vel[:] = 0.0
            self._press_scale = 1.0
            self._morph_elapsed = None

        # A permanently visible idle bar should cost nothing once it has come
        # to rest, so the clock stops when there is provably nothing moving.
        if (
            self._state == "idle"
            and self._morph_elapsed is None
            and self._idle_mix > 0.999
            and self._content["wave"] > 0.999
            and self._reveal_anim.state() != QPropertyAnimation.Running
            and self._sweep < 0.01
            and float(np.abs(self._drawn - self._target).max()) < 0.002
            and float(np.abs(self._vel).max()) < 0.01
            and abs(self._press_scale - self._press_target) < 0.002
            and abs(self._press_vel) < 0.01
            and abs(self._loud_glow) < 0.002
            and abs(self._loud_glow_vel) < 0.01
        ):
            self._drawn[:] = self._target
            self._vel[:] = 0.0
            self._press_scale = self._press_target
            self._press_vel = 0.0
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
        if state == "idle":
            return np.zeros(N_BARS, dtype=np.float32)
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
        if state == "listening" and self._loud_glow > 0.01:
            return _loud_tint(self._accent, self._loud_glow)
        return self._accent

    # --- painting ---

    def paintEvent(self, event) -> None:
        if self._reveal <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Fade, rise and scale all come off the same value, so they cannot drift.
        # The click press-dip multiplies in on top -- same pivot, so a click
        # mid-entrance still shrinks from wherever the reveal scale already is.
        scale = (0.92 + 0.08 * self._reveal) * self._press_scale
        cx, cy = SHADOW_PAD + self._pill_width / 2.0, PILL_TOP + PILL_H / 2.0
        p.translate(0.0, RISE_PX * (1.0 - self._reveal))
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        # The cached shadow is built for this display's scale factor, so the
        # bar is as crisp at 150% on a 4K panel as at 100% on a 1080p one.
        dpr = self.devicePixelRatioF()

        p.setOpacity(self._reveal)
        p.drawPixmap(0, 0, _shadow(dpr, self._dark, self._pill_width))

        pill = QRectF(
            SHADOW_PAD + 0.5,
            PILL_TOP + 0.5,
            self._pill_width - 1,
            PILL_H - 1,
        )
        path = QPainterPath()
        path.addRoundedRect(pill, RADIUS, RADIUS)
        p.fillPath(path, self._palette["surface"])

        # One external hairline around the whole surface.
        p.setOpacity(self._reveal)
        p.setPen(QPen(self._palette["stroke"], 1))
        p.drawPath(_surface_path(pill_width=self._pill_width, pixel_inset=0.5))
        p.setPen(Qt.NoPen)

        # Wave, lock, and receipts share the same surface and transition clock.
        p.save()
        wave_alpha = self._reveal * self._content["wave"]
        p.setOpacity(wave_alpha)
        # A live download caption sits above the track (see below), so the
        # track itself nudges down to leave it room -- the pill stays one
        # 36 px surface rather than growing a second row for it.
        show_loading_caption = (
            self._state == "loading" and self._progress is not None and bool(self._detail)
        )
        track_offset = 7.0 if show_loading_caption else 0.0
        track_cy = PILL_TOP + PILL_H / 2.0 + track_offset
        cx = SHADOW_PAD + self._pill_width / 2.0
        p.translate(cx + 9 * self._lock_mix, track_cy)
        p.scale((1.0 - 0.64 * self._idle_mix) * (1.0 - 0.16 * self._lock_mix), 1.0)
        p.translate(-cx, -track_cy)
        x0 = SHADOW_PAD + PAD
        hairline = QPainterPath()
        hairline.addRoundedRect(QRectF(x0, track_cy - TRACK_H / 2, self._wave_width(), TRACK_H), 1, 1)
        p.fillPath(hairline, self._palette["track"])
        if self._sweep < 0.995:
            p.setOpacity(wave_alpha * (1.0 - self._sweep))
            self._paint_bars(p, track_cy)
        if self._sweep > 0.005:
            p.setOpacity(wave_alpha * self._sweep)
            if self._state == "loading" and self._progress is not None:
                self._paint_determinate(p, self._progress, track_cy)
            else:
                self._paint_sweep(p, track_cy)
        if show_loading_caption:
            p.setOpacity(wave_alpha)
            cap_font = QFont("Segoe UI Variable Text", 8)
            p.setFont(cap_font)
            p.setPen(self._palette["text"])
            # Always this one short word, never the raw detail text (which
            # could be "Downloading Small · CPU · 42%" or similar) --
            # the fill underneath already shows the real percentage, so
            # repeating it here just made the pill busier to read at a
            # glance. Settings' own progress rows still show the full detail.
            p.drawText(
                QRectF(x0, PILL_TOP + 4, self._wave_width(), 14),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "Downloading…",
            )
        p.restore()
        if self._lock_mix > 0.001:
            p.setOpacity(self._reveal * self._lock_mix)
            p.setPen(QPen(self._accent, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            x = SHADOW_PAD + 18
            p.drawRoundedRect(QRectF(x, cy - 2, 9, 7), 2, 2)
            p.drawArc(QRectF(x + 2, cy - 8, 5, 9), 0, 180 * 16)
        # Real message text, not a canned short label -- now that it lives on
        # the bar's own surface instead of a separate floating toast, nothing
        # is lost by keeping it short, so the actual detail is what's shown.
        labels = {
            "done": "Inserted",
            "loaded": self._detail or "Ready",
            "error": self._detail or DEFAULT_ERROR_TEXT,
            "notice": self._detail,
        }
        notice_dot = SUCCESS if self._notice_tone == "success" else self._accent
        icon_tone = {
            "done": SUCCESS if self._dark else QColor("#25752C"),
            "loaded": SUCCESS if self._dark else QColor("#25752C"),
            "error": ERROR if self._dark else QColor("#C42B1C"),
            "notice": notice_dot,
        }
        for name in ("done", "loaded", "error", "notice"):
            weight = self._content[name]
            if weight <= 0.001:
                continue
            p.setOpacity(self._reveal * weight)
            font = QFont("Segoe UI Variable Text", 10)
            p.setFont(font)
            metrics = QFontMetrics(font)
            # Measured against the pill's *target* width, not its current,
            # still-animating one: the width spring takes noticeably longer
            # to settle than the opacity crossfade above, so eliding against
            # the live width used to show the message truncated -- shorter,
            # with an ellipsis -- for the first several frames, un-truncating
            # itself as the pill kept growing underneath. _label_width already
            # sized the target width for this exact text, so the target is
            # the width it is entitled to; elided here only as a defensive
            # floor so a future caller passing something unexpectedly long
            # still can't overflow the pill.
            target_wave_width = max(1.0, self._width_target - 2 * PAD)
            max_text_w = max(40.0, target_wave_width - 25.0)
            label = metrics.elidedText(labels[name], Qt.ElideRight, int(max_text_w))
            text_width = metrics.horizontalAdvance(label)
            x = cx - (text_width + 25) / 2
            y = cy + 3 * (1.0 - weight)
            tone = icon_tone[name]
            if name == "notice":
                p.setPen(Qt.NoPen)
                p.setBrush(tone)
                p.drawEllipse(QRectF(x + 2, y - 4, 8, 8))
            else:
                p.setPen(QPen(tone, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                if name in ("done", "loaded"):
                    check = QPainterPath(QPointF(x, y))
                    check.lineTo(x + 4, y + 4)
                    check.lineTo(x + 12, y - 5)
                    p.drawPath(check)
                else:
                    p.drawEllipse(QRectF(x, y - 6, 12, 12))
                    p.drawLine(QPointF(x + 6, y - 3), QPointF(x + 6, y))
                    p.drawPoint(QPointF(x + 6, y + 3))
            p.setPen(self._palette["text"])
            p.drawText(QRectF(x + 25, y - 12, text_width + 2, 24), Qt.AlignVCenter, label)

    def _paint_bars(self, p: QPainter, cy: float) -> None:
        """Slim capsules growing out of the hairline.

        A capsule fades in over its first few pixels of lift rather than
        appearing at full accent the moment it leaves the line. Without that,
        silence is a row of accent dots sitting on a grey rule -- which reads
        as a dashed line rather than as the clean unbroken one this is meant
        to be -- and the faintness of a quiet voice becomes part of the
        signal instead of an on/off edge.
        """
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
                QRectF(
                    x0 + i * (BAR_W + self._bar_gap()),
                    cy - h / 2,
                    BAR_W,
                    h,
                ),
                BAR_R, BAR_R,
            )
            p.fillPath(capsule, colour)

    def _paint_sweep(self, p: QPainter, cy: float) -> None:
        """Windows' indeterminate ProgressBar, running along the same hairline
        the bars just sank into: two accent segments, the longer one leading."""
        x0 = SHADOW_PAD + PAD
        width = self._wave_width()

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

    def _paint_determinate(self, p: QPainter, fraction: float, cy: float) -> None:
        """A filling hairline once a download reports real byte progress.

        Replaces the indeterminate sweep on the same track rather than
        introducing a second widget, for the same reason the sweep collapses
        onto the hairline instead of appearing beside it.
        """
        x0 = SHADOW_PAD + PAD
        filled = self._wave_width() * float(np.clip(fraction, 0.0, 1.0))
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


def _spring_scalar(
    pos: float, vel: float, target: float, omega: float, zeta: float, dt: float
) -> tuple[float, float]:
    """Same closed-form solution as ``_spring``, for the one scalar value
    (the click press-scale) that does not need a 21-wide array."""
    offset = pos - target
    if zeta >= 0.999:
        decay = math.exp(-omega * dt)
        b = vel + omega * offset
        new_offset = (offset + b * dt) * decay
        new_vel = (vel - omega * b * dt) * decay
        return target + new_offset, new_vel
    damped = omega * math.sqrt(1.0 - zeta * zeta)
    decay = math.exp(-zeta * omega * dt)
    cos_t = math.cos(damped * dt)
    sin_t = math.sin(damped * dt)
    a = offset
    b = (vel + zeta * omega * offset) / damped
    swing = a * cos_t + b * sin_t
    new_pos = target + decay * swing
    new_vel = decay * (-zeta * omega * swing + damped * (b * cos_t - a * sin_t))
    return new_pos, new_vel


def _smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _loud_tint(base: QColor, level: float) -> QColor:
    """Nudge the listening accent brighter and a touch more saturated as the
    mic reads louder, so a quiet voice keeps the calm accent and a loud one
    glows -- without ever becoming a different colour."""
    hue, sat, light, alpha = base.getHslF()
    amount = min(1.0, max(0.0, level))
    if hue < 0.0:            # a grey accent has no hue to keep, per system_accent
        hue = 0.0
    sat = min(1.0, sat + LOUD_GLOW_SAT * amount)
    light = min(0.85, light + LOUD_GLOW_LIGHT * amount)
    return QColor.fromHslF(hue, sat, light, alpha)


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
        int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
    )
