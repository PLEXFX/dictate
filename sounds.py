"""Two short cues: one when the microphone opens, one when it closes.

Synthesised rather than shipped as files. sounddevice is already a dependency
for capture, numpy is already used for the waveform, and a generated tone is a
few lines against two binary assets that have to be produced, licensed and
kept in sync with any future change of pitch or length.

The pair is deliberately one idea heard twice. Both cues are the same two
notes a perfect fifth apart -- rising when the microphone opens, falling when
it closes -- so they read as an "open" and a matching "close" rather than as
two unrelated beeps. That mirrors the bar itself, which rises into view when
you start and settles back into its line when you stop.

What keeps them from being annoying, given they fire on every single use:
about 150 ms with an exponential decay, a raised-cosine attack and a faded
tail (a waveform that starts or ends away from zero is a click, and a click is
the most fatiguing thing a repeated UI sound can have), and a sine with one
gentle octave partial rather than anything in the harsh 2-4 kHz region.

**Playback is QSoundEffect, not sounddevice.** The first version called
`sd.play()`, which opens a brand new output stream on every cue -- and the
default output device here reports 90 ms of low-latency buffering before a
freshly opened stream produces any sound at all. On an HDMI or DisplayPort
endpoint, which power down aggressively, waking the device on top of that
routinely swallowed most of a 150 ms clip, so cues went missing at random.
QSoundEffect exists for exactly this job: it decodes the sample once, keeps a
warm low-latency pipeline, and plays with no per-cue device setup.

sounddevice remains as a fallback for a build where QtMultimedia is missing.
Either way playback never blocks the caller and never raises: a machine with
no output device must not be able to stop dictation from working.
"""

from __future__ import annotations

import hashlib
import threading
import wave

import numpy as np
import sounddevice as sd

import config

SAMPLE_RATE = 44100

# D5 and A5 -- a perfect fifth. Consonant, and low enough to stay warm rather
# than sounding like an alert.
LOW_HZ = 587.33
HIGH_HZ = 880.00

NOTE_SECONDS = 0.11
GAP_SECONDS = 0.04   # second note starts before the first has died away
DECAY_TAU = 0.038
ATTACK_SECONDS = 0.004
OCTAVE_MIX = 0.18    # a little body, well short of sounding like a square wave

# The stored waveform sits near full scale so the 16-bit file keeps its
# dynamic range; how loud the cue actually is comes from CUE_VOLUME, which is
# the single knob worth turning.
FILE_PEAK = 0.90
CUE_VOLUME = 0.125

_CUES: dict[str, np.ndarray] = {}


def _note(freq: float) -> np.ndarray:
    count = int(SAMPLE_RATE * NOTE_SECONDS)
    t = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    wave_form = np.sin(2 * np.pi * freq * t) + OCTAVE_MIX * np.sin(
        2 * np.pi * freq * 2 * t
    )
    envelope = np.exp(-t / DECAY_TAU)
    attack = int(SAMPLE_RATE * ATTACK_SECONDS)
    if attack > 1:
        # Raised cosine in, so the waveform starts from silence instead of
        # stepping straight to full amplitude, which would be a click.
        envelope[:attack] *= 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, attack))
    return wave_form * envelope


def _cue(first: float, second: float) -> np.ndarray:
    lead, follow = _note(first), _note(second)
    offset = int(SAMPLE_RATE * GAP_SECONDS)
    out = np.zeros(offset + follow.size, dtype=np.float64)
    out[: lead.size] += lead
    out[offset : offset + follow.size] += follow

    loudest = float(np.abs(out).max())
    if loudest > 0.0:
        out *= FILE_PEAK / loudest
    tail = int(SAMPLE_RATE * 0.006)
    if tail > 1:
        out[-tail:] *= np.linspace(1.0, 0.0, tail)
    return out.astype(np.float32)


def _cues() -> dict[str, np.ndarray]:
    if not _CUES:
        _CUES["start"] = _cue(LOW_HZ, HIGH_HZ)    # rising: the mic is open
        _CUES["stop"] = _cue(HIGH_HZ, LOW_HZ)     # falling: it is closed, working
    return _CUES


def _fingerprint() -> str:
    """Short hash of everything that shapes the sound.

    It goes in the filename, so retuning any constant here writes new files
    rather than leaving a stale cue cached in the user's app-data folder.
    """
    recipe = (
        LOW_HZ, HIGH_HZ, NOTE_SECONDS, GAP_SECONDS, DECAY_TAU,
        ATTACK_SECONDS, OCTAVE_MIX, FILE_PEAK, SAMPLE_RATE,
    )
    return hashlib.sha1(repr(recipe).encode()).hexdigest()[:8]


def cue_files() -> dict[str, "object"]:
    """Write the cues to app data if they are not already there, and return
    their paths. Cheap after the first run: the files are content-addressed,
    so an unchanged recipe is a pair of existence checks."""
    folder = config.CONFIG_DIR
    stamp = _fingerprint()
    paths = {}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, clip in _cues().items():
            path = folder / f"cue-{name}-{stamp}.wav"
            if not path.exists():
                _write_wav(path, clip)
            paths[name] = path
        for stale in folder.glob("cue-*.wav"):
            if stamp not in stale.name:
                stale.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[dictate] could not prepare sound cues: {exc}")
        return {}
    return paths


def _write_wav(path, clip: np.ndarray) -> None:
    pcm = (np.clip(clip, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


class Cues:
    """Plays the two cues, or nothing at all when the user has them off.

    Must be constructed on the Qt UI thread, which is also where play() is
    called from -- both hotkey handlers arrive there through Bridge's queued
    signals, so QSoundEffect is never touched off-thread.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self._effects: dict = {}
        self._load()

    def _load(self) -> None:
        """Decode both cues up front so the first keypress is not the thing
        that wakes the audio device.

        Retried on first use if it could not run at construction, so ordering
        never silently costs the cues.
        """
        if self._effects:
            return
        try:
            from PySide6.QtCore import QCoreApplication, QUrl
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            return                       # falls back to sounddevice below
        if QCoreApplication.instance() is None:
            # QSoundEffect is a QObject and needs a live application; building
            # one without it hangs rather than failing. The app always has one
            # by this point, but tests import this module long before any Qt
            # object exists.
            return
        for name, path in cue_files().items():
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(CUE_VOLUME)
            effect.setLoopCount(1)
            self._effects[name] = effect

    def update_settings(self, settings) -> None:
        self.enabled = bool(getattr(settings, "sound_cues", True))

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        self._fire(name)

    def _fire(self, name: str) -> None:
        self._load()
        effect = self._effects.get(name)
        if effect is not None:
            try:
                effect.play()
                return
            except Exception as exc:
                print(f"[dictate] sound cue failed: {exc}")
        clip = _cues().get(name)
        if clip is not None:
            threading.Thread(target=_emit, args=(clip,), daemon=True).start()


def _emit(clip: np.ndarray) -> None:
    """Fallback path only. Opens its own short-lived output stream, which is
    why it is the fallback: the setup cost is what made cues unreliable."""
    try:
        sd.play(clip * CUE_VOLUME, SAMPLE_RATE, blocking=True)
    except Exception as exc:                      # no output device, or it went away
        print(f"[dictate] sound cue unavailable: {exc}")
