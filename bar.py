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
# The transcript is part of this same frameless window.  Reserving its full
# height keeps the waveform anchored above the taskbar while the card grows
# upward instead of making the whole control jump when the first words arrive.
CARD_FULL_H = 48        # two compact rows with Windows-like 4px top breathing room
CARD_ONE_H = 29         # one row stays compact until history actually exists
CARD_STUB_H = 8        # the small connected lip visible before speech resolves
CARD_OVERLAP = 3       # tucks under the pill so the two surfaces read as one
CARD_INSET = 4
CARD_TEXT_PAD = 12
CARD_RADIUS = 8
PILL_TOP = SHADOW_PAD + CARD_FULL_H
WIDTH = PILL_W + 2 * SHADOW_PAD
HEIGHT = PILL_TOP + PILL_H + SHADOW_PAD
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

# Transcript motion.  The card itself uses a near-critically-damped spring;
# words use a shorter Fluent crossfade so they feel responsive without
# flickering each time Whisper revises the unfinished phrase.
CARD_OMEGA = 31.0
CARD_ZETA = 0.78
TEXT_TRANSITION_SECONDS = 0.24
TEXT_RISE_PX = 7.0

# A word settling from tentative (52% alpha) to confirmed does not just pop
# to full opacity: its trailing, newly-confirmed span rises the last couple
# of pixels of a quick, critically-damped settle while briefly reading a
# touch brighter than the rest of the line. Critically damped on purpose --
# a *confirmation* overshooting would read as a wobble, not as certainty.
CONFIRM_PULSE_SECONDS = 0.16
CONFIRM_PULSE_RISE_PX = 1.5
CONFIRM_PULSE_BRIGHTEN = 0.30

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


def _surface_path(
    card_height: float = 0.0,
    *,
    y_offset: float = 0.0,
    pixel_inset: float = 0.0,
) -> QPainterPath:
    """One outer silhouette for the pill and its connected transcript card.

    Building the union before either the shadow or outline is painted removes
    the internal border that made the two surfaces look like stacked boxes.
    """
    pill = QPainterPath()
    pill.addRoundedRect(
        QRectF(
            SHADOW_PAD + pixel_inset,
            PILL_TOP + y_offset + pixel_inset,
            PILL_W - 2 * pixel_inset,
            PILL_H - 2 * pixel_inset,
        ),
        RADIUS,
        RADIUS,
    )
    if card_height <= 0.0:
        return pill
    card = QPainterPath()
    card.addRoundedRect(
        QRectF(
            SHADOW_PAD + CARD_INSET + pixel_inset,
            PILL_TOP + CARD_OVERLAP + y_offset - card_height + pixel_inset,
            PILL_W - 2 * CARD_INSET - 2 * pixel_inset,
            card_height - 2 * pixel_inset,
        ),
        CARD_RADIUS,
        CARD_RADIUS,
    )
    return pill.united(card)


def _shadow(
    dpr: float, dark: bool = True, card_height: float = 0.0
) -> QPixmap:
    """Ambient elevation under the complete control, cached per card height.

    Rendered at the display's device pixel ratio and tagged with it, so the
    shadow is as sharp on a 4K 150% display as on a 1080p one. A single 1x
    bitmap scaled up is exactly the kind of soft, slightly wrong edge that
    makes a hand-painted window look unlike the rest of the shell.
    """
    # Two-pixel buckets keep the animated edge visually continuous without
    # doing three blur passes on every frame of the height spring.
    card_height = max(0.0, min(CARD_FULL_H, card_height))
    shadow_card_height = float(int(round(card_height / 2.0)) * 2)
    key = ("shadow", round(dpr, 3), dark, shadow_card_height)
    if key in _CACHE:
        return _CACHE[key]
    width, height = int(round(WIDTH * dpr)), int(round(HEIGHT * dpr))
    layer = QImage(width, height, QImage.Format_ARGB32)
    layer.fill(Qt.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.scale(dpr, dpr)
    path = _surface_path(shadow_card_height, y_offset=2.0)
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


class Bar(QWidget):
    clicked = Signal()   # only fires while set_clickable(True) -- see mouseReleaseEvent

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

        self._clickable = False          # only true while a locked recording is running
        self._press_scale = 1.0
        self._press_vel = 0.0
        self._press_target = 1.0

        # Rolling two-line transcript.  ``_card_expand == 0`` still paints a
        # small lip while listening; the first line opens a compact card and
        # 1 reveals enough room for both lines.
        self._card_active = False
        self._card_expand = 0.0
        self._card_vel = 0.0
        self._card_target = 0.0
        self._text_top = ""
        self._text_bottom = ""
        self._text_from = ("", "")
        self._text_to = ("", "")
        self._text_confirmed_from = (0, 0)
        self._text_confirmed_to = (0, 0)
        self._preview_raw = ""
        self._text_elapsed: float | None = None
        self._text_advancing = False
        # Bottom-row-only: the confirmed-char count the settle pulse is
        # rising out of, and how far through that settle it is.
        self._confirm_pulse_from = 0
        self._confirm_pulse_elapsed: float | None = None
        self._loud_glow = 0.0
        self._loud_glow_vel = 0.0
        self._text_font = QFont("Segoe UI Variable Text", 9)
        if not self._text_font.exactMatch():
            self._text_font = QFont("Segoe UI", 9)

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
        # The transcript reserves its full upward animation area even while
        # collapsed.  Keep those transparent pixels click-through unless a
        # locked recording has explicitly armed the bar's finish-on-click
        # action, or the invisible rectangle would block the app underneath.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
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
        return QRect(geo.x() + SHADOW_PAD, geo.y() + PILL_TOP, PILL_W, PILL_H)

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
        y = area.bottom() + 1 - max(0, margin) - PILL_H - PILL_TOP
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
        if settings.live_preview_enabled:
            if self._state == "listening":
                self._card_active = True
        else:
            self._card_active = False
            self.clear_live_text()
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

        if state == "listening":
            self._history[:] = 0.0
            self._mic_level = 0.0
            self._slice_accum = 0.0
            self._card_active = self._settings.live_preview_enabled
            self.clear_live_text()
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
            self._card_active = False
            self._card_target = 0.0
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not clickable)
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if self._clickable:
            self._press_target = PRESS_DIP
            if not self._timer.isActive():
                self._last_tick = time.perf_counter()
                self._timer.start()

    def mouseReleaseEvent(self, event) -> None:
        if not self._clickable:
            return
        self._press_target = 1.0
        if self.rect().contains(event.position().toPoint()):
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
        """Animate a rolling Whisper preview into the two-row card.

        Only the last two fitted rows are kept.  When a new row begins, the
        lower row rises toward the history slot while the older row dissolves;
        ordinary corrections within the current row use a quieter crossfade.
        """
        if self._state != "listening" or not self._settings.live_preview_enabled:
            return
        normalized = " ".join(text.split())
        rows = self._fit_live_rows(normalized)
        top, bottom = self._fit_live_lines(normalized)
        if not bottom:
            return
        incoming = (top, bottom)
        current = self._text_to if self._text_elapsed is not None else (
            self._text_top,
            self._text_bottom,
        )
        if incoming == current:
            return

        # Preview updates are deliberately slower than this transition, but
        # if a loaded GPU returns twice in quick succession, use the newest
        # target as the visual starting point instead of jumping backwards.
        if self._text_elapsed is not None:
            self._text_top, self._text_bottom = self._text_to
        self._text_from = (self._text_top, self._text_bottom)
        self._text_confirmed_from = self._text_confirmed_to
        self._text_to = incoming

        words = normalized.split()
        previous = self._preview_raw.split()
        stable_words = 0
        for old_word, new_word in zip(previous, words):
            if old_word != new_word:
                break
            stable_words += 1
        # Whisper's unfinished tail is the part most likely to change.  Keep
        # the newest two words visibly tentative even when the rest of a new
        # result has not had a second pass yet.
        stable_words = max(stable_words, max(0, len(words) - 2))
        confirmed: list[int] = []
        for _line, start, end in rows:
            count = max(0, min(end, stable_words) - start)
            confirmed.append(len(" ".join(words[start:start + count])))
        if len(confirmed) == 1:
            confirmed = [0, confirmed[0]]
        prev_bottom_confirmed = self._text_confirmed_to[1]
        self._text_confirmed_to = tuple(confirmed[-2:])
        self._preview_raw = normalized
        self._text_advancing = bool(
            self._text_from[1]
            and incoming[0]
            and incoming[0] != self._text_from[0]
        )
        # A settle pulse only makes sense when the bottom row is still the
        # same row growing more confirmed tail -- not when it just became a
        # new row, which is the row-rise motion's job instead.
        if (
            not self._text_advancing
            and self._text_confirmed_to[1] > prev_bottom_confirmed
        ):
            self._confirm_pulse_from = prev_bottom_confirmed
            self._confirm_pulse_elapsed = 0.0
        else:
            self._confirm_pulse_elapsed = None
        self._text_elapsed = 0.0
        self._card_target = (
            1.0
            if top
            else (CARD_ONE_H - CARD_STUB_H) / (CARD_FULL_H - CARD_STUB_H)
        )
        self._card_active = True
        if not self._timer.isActive():
            self._last_tick = time.perf_counter()
            self._timer.start()
        self.update()

    def clear_live_text(self) -> None:
        """Return the listening card to its empty connected lip."""
        self._text_top = ""
        self._text_bottom = ""
        self._text_from = ("", "")
        self._text_to = ("", "")
        self._text_confirmed_from = (0, 0)
        self._text_confirmed_to = (0, 0)
        self._preview_raw = ""
        self._text_elapsed = None
        self._text_advancing = False
        self._confirm_pulse_from = 0
        self._confirm_pulse_elapsed = None
        self._card_target = 0.0
        self.update()

    def _fit_live_lines(self, text: str) -> tuple[str, str]:
        """Word-wrap a rolling preview and retain only its newest two rows."""
        rows = self._fit_live_rows(text)
        if not rows:
            return "", ""
        lines = [row[0] for row in rows]
        if len(lines) == 1:
            return "", lines[0]
        return lines[0], lines[1]

    def _fit_live_rows(self, text: str) -> list[tuple[str, int, int]]:
        """Return the newest fitted rows plus their word-index ranges."""
        words = " ".join(text.split()).split(" ")
        if not words or words == [""]:
            return []
        metrics = QFontMetrics(self._text_font)
        max_width = PILL_W - 2 * (CARD_INSET + CARD_TEXT_PAD)
        rows: list[tuple[str, int, int]] = []
        current = ""
        start = 0
        for index, word in enumerate(words):
            candidate = word if not current else f"{current} {word}"
            if metrics.horizontalAdvance(candidate) <= max_width:
                current = candidate
                continue
            if current:
                rows.append((current, start, index))
            current = word
            start = index
        if current:
            rows.append((current, start, len(words)))
        return rows[-2:]

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
        self._card_active = False
        self._card_expand = 0.0
        self._card_vel = 0.0
        self.clear_live_text()

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

        self._press_scale, self._press_vel = _spring_scalar(
            self._press_scale, self._press_vel, self._press_target,
            PRESS_OMEGA, PRESS_ZETA, dt,
        )
        self._card_expand, self._card_vel = _spring_scalar(
            self._card_expand,
            self._card_vel,
            self._card_target,
            CARD_OMEGA,
            CARD_ZETA,
            dt,
        )
        if self._text_elapsed is not None:
            self._text_elapsed += dt
            if self._text_elapsed >= TEXT_TRANSITION_SECONDS:
                self._text_top, self._text_bottom = self._text_to
                self._text_elapsed = None
        if self._confirm_pulse_elapsed is not None:
            self._confirm_pulse_elapsed += dt
            if self._confirm_pulse_elapsed >= CONFIRM_PULSE_SECONDS:
                self._confirm_pulse_elapsed = None

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

        # A permanently visible idle bar should cost nothing once it has come
        # to rest, so the clock stops when there is provably nothing moving.
        if (
            self._state == "idle"
            and self._morph_elapsed is None
            and self._reveal_anim.state() != QPropertyAnimation.Running
            and self._sweep < 0.01
            and float(np.abs(self._drawn - self._target).max()) < 0.002
            and float(np.abs(self._vel).max()) < 0.01
            and abs(self._press_scale - self._press_target) < 0.002
            and abs(self._press_vel) < 0.01
            and abs(self._card_expand - self._card_target) < 0.002
            and abs(self._card_vel) < 0.01
            and self._text_elapsed is None
            and self._confirm_pulse_elapsed is None
            and abs(self._loud_glow) < 0.002
            and abs(self._loud_glow_vel) < 0.01
        ):
            self._drawn[:] = self._target
            self._vel[:] = 0.0
            self._press_scale = self._press_target
            self._press_vel = 0.0
            self._card_expand = self._card_target
            self._card_vel = 0.0
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
            if not self._settings.always_visible:
                return np.zeros(N_BARS, dtype=np.float32)
            # A slow, centre-weighted breath rather than a flat line, so an
            # always-visible idle bar reads as listening rather than stuck.
            # Kept under LIFT_FADE_PX at its peak -- felt, not seen.
            breath = 0.5 - 0.5 * math.cos(2.0 * math.pi * self._clock * IDLE_BREATH_HZ)
            shape = np.clip(1.0 - _CENTRE_D * IDLE_BREATH_SPAN, 0.0, 1.0) ** 2
            return (IDLE_BREATH_AMPLITUDE * breath * shape).astype(np.float32)
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
        cx, cy = SHADOW_PAD + PILL_W / 2.0, PILL_TOP + PILL_H / 2.0
        p.translate(0.0, RISE_PX * (1.0 - self._reveal))
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        # Both cached bitmaps are built for this display's scale factor, so
        # the bar is as crisp at 150% on a 4K panel as at 100% on a 1080p one.
        dpr = self.devicePixelRatioF()

        p.setOpacity(self._reveal)
        expand = _smoothstep(min(1.0, max(0.0, self._card_expand)))
        visible_card_h = (
            CARD_STUB_H + (CARD_FULL_H - CARD_STUB_H) * expand
            if self._card_active
            else 0.0
        )
        p.drawPixmap(0, 0, _shadow(dpr, self._dark, visible_card_h))

        if self._card_active:
            self._paint_transcript_card(p, dpr)

        pill = QRectF(SHADOW_PAD + 0.5, PILL_TOP + 0.5, PILL_W - 1, PILL_H - 1)
        path = QPainterPath()
        path.addRoundedRect(pill, RADIUS, RADIUS)
        p.fillPath(path, self._palette["surface"])
        p.fillPath(path, QBrush(_noise_tile(dpr, self._dark)))   # the grain in Acrylic

        # One external hairline around the union.  Drawing the two rounded
        # rectangles separately left a horizontal border through their join.
        p.setOpacity(self._reveal)
        p.setPen(QPen(self._palette["stroke"], 1))
        p.drawPath(
            _surface_path(
                visible_card_h if self._card_active else 0.0,
                pixel_inset=0.5,
            )
        )
        p.setPen(Qt.NoPen)

        # The hairline is the one thing that never moves or fades, so the bars
        # and the sweep are only ever *on* it. Drawing it once, outside the
        # crossfade, is what stops the collapse into loading from reading as
        # one widget being swapped for another.
        cy = PILL_TOP + PILL_H / 2.0
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

    def _paint_transcript_card(self, p: QPainter, dpr: float) -> None:
        """Paint the connected listening lip and its two live text rows."""
        expand = _smoothstep(min(1.0, max(0.0, self._card_expand)))
        visible_h = CARD_STUB_H + (CARD_FULL_H - CARD_STUB_H) * expand
        bottom = PILL_TOP + CARD_OVERLAP
        card = QRectF(
            SHADOW_PAD + CARD_INSET + 0.5,
            bottom - visible_h,
            PILL_W - 2 * CARD_INSET - 1,
            visible_h,
        )
        path = QPainterPath()
        path.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)

        p.save()
        base_opacity = self._reveal
        p.setOpacity(base_opacity)
        # The card is intentionally only a tonal step away from the bar: dark
        # mode uses a slightly blacker surface, light mode a slightly whiter
        # one, matching Windows Settings groups without creating a new colour.
        surface = QColor(35, 35, 35, 248) if self._dark else QColor(255, 255, 255, 250)
        p.fillPath(path, surface)
        p.fillPath(path, QBrush(_noise_tile(dpr, self._dark)))
        # The shared outline is painted after the pill, around the union of
        # both shapes.  There is intentionally no card-only border here.

        text_opacity = _smoothstep(
            min(
                1.0,
                max(
                    0.0,
                    (visible_h - CARD_STUB_H) / (CARD_ONE_H - CARD_STUB_H),
                ),
            )
        )
        if text_opacity <= 0.001:
            p.restore()
            return

        p.setClipPath(path)
        p.setFont(self._text_font)
        metrics = QFontMetrics(self._text_font)
        text_left = card.left() + CARD_TEXT_PAD
        text_width = card.width() - 2 * CARD_TEXT_PAD
        line_h = max(16, metrics.height() + 2)
        # Anchor the newest row to the bar.  The first line therefore does not
        # jump when the card grows upward to make room for history above it.
        bottom_y = card.bottom() - 4 - line_h
        top_y = bottom_y - line_h

        pulse_t = 1.0
        if self._confirm_pulse_elapsed is not None:
            pulse_t = _smoothstep(self._confirm_pulse_elapsed / CONFIRM_PULSE_SECONDS)

        def draw_line(
            text: str,
            y: float,
            alpha: float,
            offset: float = 0.0,
            confirmed_chars: int = 0,
            pulse_from: int | None = None,
        ) -> None:
            if not text or alpha <= 0.001:
                return
            p.setPen(self._palette["text"])
            fitted = metrics.elidedText(text, Qt.ElideRight, int(text_width))
            confirmed_chars = min(max(0, confirmed_chars), len(fitted))
            solid = fitted[:confirmed_chars]
            tentative = fitted[confirmed_chars:]
            settled, fresh = solid, ""
            if pulse_from is not None and 0 <= pulse_from < len(solid):
                settled, fresh = solid[:pulse_from], solid[pulse_from:]
            if settled:
                p.setOpacity(base_opacity * text_opacity * alpha)
                p.drawText(
                    QRectF(text_left, y + offset, text_width, line_h),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    settled,
                )
            if fresh:
                # The word that just settled rises the last CONFIRM_PULSE_RISE_PX
                # and cools from a brief accent tint back to the ordinary text
                # colour, instead of popping straight to full opacity.
                fresh_x = text_left + metrics.horizontalAdvance(settled)
                p.setPen(_lerp_color(self._palette["text"], self._tint, CONFIRM_PULSE_BRIGHTEN * (1.0 - pulse_t)))
                p.setOpacity(base_opacity * text_opacity * alpha)
                p.drawText(
                    QRectF(
                        fresh_x,
                        y + offset - CONFIRM_PULSE_RISE_PX * (1.0 - pulse_t),
                        max(0.0, text_width - (fresh_x - text_left)),
                        line_h,
                    ),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    fresh,
                )
                p.setPen(self._palette["text"])
            if tentative:
                tentative_x = text_left + metrics.horizontalAdvance(solid)
                p.setOpacity(base_opacity * text_opacity * alpha * 0.52)
                p.drawText(
                    QRectF(
                        tentative_x,
                        y + offset,
                        max(0.0, text_width - (tentative_x - text_left)),
                        line_h,
                    ),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    tentative,
                )

        if self._text_elapsed is None:
            draw_line(self._text_top, top_y, 0.56, confirmed_chars=self._text_confirmed_to[0])
            draw_line(self._text_bottom, bottom_y, 0.98, confirmed_chars=self._text_confirmed_to[1])
        else:
            t = _smoothstep(self._text_elapsed / TEXT_TRANSITION_SECONDS)
            old_top, old_bottom = self._text_from
            new_top, new_bottom = self._text_to
            old_confirmed = self._text_confirmed_from
            new_confirmed = self._text_confirmed_to
            if self._text_advancing:
                row_rise = bottom_y - top_y
                draw_line(old_top, top_y, 0.56 * (1.0 - t), -TEXT_RISE_PX * t, old_confirmed[0])
                draw_line(old_bottom, bottom_y, 0.98 * (1.0 - t), -row_rise * t, old_confirmed[1])
                draw_line(new_top, top_y, 0.56 * t, confirmed_chars=new_confirmed[0])
                draw_line(new_bottom, bottom_y, 0.98 * t, TEXT_RISE_PX * (1.0 - t), new_confirmed[1])
            else:
                draw_line(old_top, top_y, 0.56 * (1.0 - t), confirmed_chars=old_confirmed[0])
                draw_line(new_top, top_y, 0.56 * t, confirmed_chars=new_confirmed[0])
                draw_line(old_bottom, bottom_y, 0.98 * (1.0 - t), -2.0 * t, old_confirmed[1])
                draw_line(
                    new_bottom, bottom_y, 0.98 * t, 3.0 * (1.0 - t), new_confirmed[1],
                    pulse_from=self._confirm_pulse_from,
                )
        p.restore()

    def _paint_bars(self, p: QPainter) -> None:
        """Slim capsules growing out of the hairline.

        A capsule fades in over its first few pixels of lift rather than
        appearing at full accent the moment it leaves the line. Without that,
        silence is a row of accent dots sitting on a grey rule -- which reads
        as a dashed line rather than as the clean unbroken one this is meant
        to be -- and the faintness of a quiet voice becomes part of the
        signal instead of an on/off edge.
        """
        cy = PILL_TOP + PILL_H / 2.0
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
        cy = PILL_TOP + PILL_H / 2.0
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
        cy = PILL_TOP + PILL_H / 2.0
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


def _spring_scalar(
    pos: float, vel: float, target: float, omega: float, zeta: float, dt: float
) -> tuple[float, float]:
    """Same closed-form solution as ``_spring``, for the one scalar value
    (the click press-scale) that does not need a 21-wide array."""
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
    # A realistic update message ("Update 0.2.10-beta.12 available — click
    # to download & install") measures ~750px at this font -- far past any
    # single-line width worth showing at the bar's own compact scale. Rather
    # than growing MAX_W to match (an oversized pill, and still not proof
    # against the next longer message), text past this width wraps to a
    # second line instead of eliding, so nothing is ever cut off.
    MAX_W = 400
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
        chrome = self.PAD * 2 + 22  # dot + its left offset + the pill's own side padding
        if metrics.horizontalAdvance(text) + chrome <= self.MAX_W:
            w = max(160, metrics.horizontalAdvance(text) + chrome)
            h = self.HEIGHT
        else:
            # Wrap rather than elide once one line at MAX_W isn't enough --
            # see the MAX_W comment above.
            w = self.MAX_W
            wrapped = metrics.boundingRect(
                QRect(0, 0, w - chrome, 10_000), Qt.TextWordWrap, text
            )
            h = max(self.HEIGHT, wrapped.height() + self.PAD * 2)
        self.setFixedSize(w, h)

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
        # The bar itself is always screen-centred (Bar.reposition), so this
        # only ever bites on an unusually narrow or heavily-zoomed display --
        # but a wider MAX_W makes it worth guarding rather than assuming.
        screen = QApplication.screenAt(anchor_rect.center()) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - self.width()))
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
        if metrics.horizontalAdvance(self._text) <= rect.width():
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, self._text)
        else:
            # show_message() already sized the box to fit this wrapped -- see
            # its MAX_W comment -- so this is a real reflow, not a fallback.
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap, self._text)
