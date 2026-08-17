"""Microphone capture, plus the live signal the waveform is drawn from.

The capture side is deliberately dumb: it collects float32 samples at 16 kHz
mono for as long as the key is held. The only extra job it does is keep the
most recent slice of audio in a slot the UI can read, so the bar can draw a
waveform without a second microphone stream.

Threading: sounddevice invokes _callback on its own high-priority audio thread.
Nothing in that callback may block or allocate much, so it does one copy and
returns. The UI thread reads the slot through a lock and does the FFT itself,
where a slow frame costs a dropped frame rather than dropped audio.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
VIS_WINDOW = 1024  # samples handed to the visualizer; ~64 ms at 16 kHz

# Below this RMS, a held recording is treated as silence rather than sent to
# the model -- a muted or disconnected mic streams near-zero samples for the
# whole hold, which otherwise transcribes to nothing and looks identical to
# the user simply not having spoken. Room noise on a live mic sits well below
# this; conversational speech sits well above it.
SILENCE_RMS_THRESHOLD = 0.004


def rms_level(clip: np.ndarray) -> float:
    """Root-mean-square amplitude of a captured clip, used for silence checks."""
    if clip.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(clip))))


@dataclass(frozen=True)
class InputDevice:
    """A selectable microphone with a stable persisted key."""

    key: str
    label: str
    index: int


def input_devices() -> list[InputDevice]:
    """Return the currently available microphones without opening any of them.

    PortAudio's numeric indexes can move after a reboot or when a USB device is
    unplugged. Persist the host API and device name instead, then resolve the
    current numeric index only when recording starts.
    """
    try:
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()
    except Exception:
        return []

    candidates: list[tuple[str, str, int]] = []
    occurrences: dict[tuple[str, str], int] = {}
    name_counts: dict[str, int] = {}
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        name = str(device.get("name", f"Microphone {index}"))
        api_index = int(device.get("hostapi", 0))
        try:
            api_name = str(host_apis[api_index].get("name", "Windows audio"))
        except (IndexError, KeyError, TypeError):
            api_name = "Windows audio"
        duplicate_number = occurrences.get((api_name, name), 0)
        occurrences[(api_name, name)] = duplicate_number + 1
        key = f"{api_name}|{name}|{duplicate_number}"
        candidates.append((key, name, index))
        name_counts[name] = name_counts.get(name, 0) + 1

    return [
        InputDevice(
            key=key,
            label=f"{name} ({key.split('|', 1)[0]})" if name_counts[name] > 1 else name,
            index=index,
        )
        for key, name, index in candidates
    ]


class MicCapture:
    def __init__(self, device_key: str = ""):
        self._device_key = device_key
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._latest = np.zeros(VIS_WINDOW, dtype=np.float32)
        self._lock = threading.Lock()

    def set_device(self, device_key: str) -> None:
        """Select the microphone used by the next recording."""
        self._device_key = device_key

    def _device_index(self) -> int | None:
        if not self._device_key:
            return None
        for device in input_devices():
            if device.key == self._device_key:
                return device.index
        raise RuntimeError(
            "The selected microphone is not available. Open Settings and choose another."
        )

    def _callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        self._frames.append(chunk)
        with self._lock:
            if chunk.size >= VIS_WINDOW:
                self._latest = chunk[-VIS_WINDOW:]
            else:
                # Slide the older samples left and append the new chunk, so the
                # visualizer always sees a full window even with small buffers.
                self._latest = np.concatenate([self._latest[chunk.size:], chunk])

    def start(self) -> None:
        self._frames = []
        with self._lock:
            self._latest = np.zeros(VIS_WINDOW, dtype=np.float32)
        self._stream = sd.InputStream(
            device=self._device_index(),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=0,  # let the driver pick; lowest latency it can manage
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            self._latest = np.zeros(VIS_WINDOW, dtype=np.float32)
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames)

    def latest_window(self) -> np.ndarray:
        with self._lock:
            return self._latest.copy()


class SpectrumMeter:
    """Turns raw audio windows into the bar heights the waveform draws.

    Buckets an FFT into log-spaced bands, because voice energy is bunched into
    the low end and linear buckets would leave most of the bars dead. Heights
    fall off gradually rather than snapping to each frame's value, which is
    what makes the motion read as a voice rather than as noise.

    Produces half as many values as the waveform has bars: the bar widget
    mirrors them around its centre, so band 0 (the loudest, lowest frequencies)
    sits in the middle and the shape pulses outward. A non-mirrored spectrum
    looks broken on speech, because the high bands are near-silent and the
    right-hand side of the bar just sits flat.

    The fall-off is measured against the clock rather than counted per call.
    Whatever starves this timer -- a transcription saturating the CPU, a model
    loading off disk -- would otherwise slow the decay in exact proportion,
    and the wave would visibly sag at the one moment the machine is busy.
    """

    def __init__(self, bands: int = 20, release_tau: float = 0.106):
        self.bands = bands
        self.release_tau = release_tau
        self._last = time.perf_counter()
        self._levels = np.zeros(bands, dtype=np.float32)
        # Log-spaced edges across the range that carries speech. Above ~4 kHz
        # there is little left to show and the bars just flicker.
        self._edges = np.logspace(
            np.log10(80), np.log10(4000), bands + 1
        )
        # Speech rolls off steeply with frequency. Without this lift, the
        # outer bars would barely move no matter how loudly you talk.
        self._tilt = np.linspace(1.0, 3.4, bands).astype(np.float32)

    def reset(self) -> None:
        self._levels[:] = 0.0
        self._last = time.perf_counter()

    def _fall(self) -> float:
        """How far levels should have fallen since the previous call.

        Clamped, so returning from a long stall settles the bars rather than
        snapping them flat.
        """
        now = time.perf_counter()
        dt = min(max(now - self._last, 0.0), 0.25)
        self._last = now
        return math.exp(-dt / self.release_tau)

    def update(self, window: np.ndarray) -> np.ndarray:
        fall = self._fall()
        if window.size < 32:
            self._levels *= fall
            return self._levels.copy()

        windowed = window * np.hanning(window.size).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(window.size, 1.0 / SAMPLE_RATE)

        raw = np.zeros(self.bands, dtype=np.float32)
        for i in range(self.bands):
            lo, hi = self._edges[i], self._edges[i + 1]
            mask = (freqs >= lo) & (freqs < hi)
            if mask.any():
                raw[i] = spectrum[mask].mean()

        # Compress to something eye-shaped. Speech spans a huge dynamic range,
        # so a linear mapping leaves the bars either flat or pinned.
        raw = np.log10(1.0 + raw * self._tilt * 120.0)
        peak = raw.max()
        if peak > 0.15:
            raw = raw / max(peak, 1.0)
        else:
            raw[:] = 0.0  # near-silence: let the bars settle rather than
                          # amplifying room noise into a light show
        raw = np.clip(raw, 0.0, 1.0)

        self._levels = np.maximum(raw, self._levels * fall)
        return self._levels.copy()

    def decayed(self) -> np.ndarray:
        self._levels *= self._fall()
        return self._levels.copy()
