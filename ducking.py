"""Turn other apps down while the talk key is held.

Anyone listening on speakers rather than headphones has the microphone
hearing whatever is playing -- a video, music, a game -- and Whisper happily
transcribes it. Lowering every other app's own volume for the length of a
recording keeps that bleed down without touching the Windows master volume,
so the change is invisible in the volume flyout and Dictate's own cues stay
at full level.

Every Windows app that plays sound owns an "audio session" per output
device, each with its own volume slider (the ones in Volume Mixer). This
module lowers those sliders to ``DUCK_LEVEL`` of where they were and puts
them back afterwards. Two rules keep it from ever fighting the user:

* A session is only restored if its volume is still what Dictate set. If
  someone moved that app's slider mid-recording, their choice wins.
* What was lowered is journaled to disk before the change, so a crash or a
  killed process mid-recording is repaired on the next launch instead of
  leaving every app quietly stuck at half volume (Windows remembers per-app
  volumes across restarts).

All COM work runs on one private worker thread, in order, so the UI thread
never waits on an audio driver and a duck can never overtake its restore.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
from ctypes import POINTER, c_float, c_int, c_uint, c_void_p
from ctypes.wintypes import DWORD, LPCWSTR
from pathlib import Path
from typing import Callable, Iterable, Protocol

import config

# Other apps play at this fraction of their own volume while recording.
# Half is enough to take most speaker bleed below the silence threshold
# while still leaving the audio clearly audible to the person talking.
DUCK_LEVEL = 0.5
# A short ramp instead of a jump, so the dip reads as deliberate rather than
# as a glitch in whatever is playing.
RAMP_STEPS = 6
RAMP_SECONDS = 0.12
# Volumes come back from Windows as floats; anything this close to the value
# Dictate set is treated as untouched.
_TOLERANCE = 0.01

JOURNAL_PATH = config.CONFIG_DIR / "ducked-audio.json"


class Session(Protocol):
    """One app's volume slider on one output device."""

    key: str
    pid: int

    def get_volume(self) -> float: ...

    def set_volume(self, value: float) -> None: ...


# --- Windows Core Audio (mmdeviceapi.h / audiopolicy.h) -------------------
#
# Declared by hand with comtypes, the same way update_splash.py reaches
# ITaskbarList3, so no pywin32 or extra audio package is needed. COM vtables
# are positional: every method up to the last one used must be listed in its
# real order even where this code never calls it.

_backend_ready = False


def _load_backend():
    global _backend_ready, _enumerate_sessions
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAudioSessionControl2(IUnknown):
        _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")

    class IAudioSessionEnumerator(IUnknown):
        _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")

    class IAudioSessionManager2(IUnknown):
        _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")

    class ISimpleAudioVolume(IUnknown):
        _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")

    class IMMDeviceCollection(IUnknown):
        _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")

    IAudioSessionControl2._methods_ = [
        # IAudioSessionControl
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "state")),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(c_void_p), "name")),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], LPCWSTR), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(c_void_p), "path")),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], LPCWSTR), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID)), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p)),
        # IAudioSessionControl2
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(c_void_p), "id")),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(c_void_p), "id")),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD), "pid")),
    ]
    IAudioSessionEnumerator._methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "count")),
        COMMETHOD(
            [], HRESULT, "GetSession",
            (["in"], c_int, "index"), (["out"], POINTER(POINTER(IAudioSessionControl2)), "session"),
        ),
    ]
    IAudioSessionManager2._methods_ = [
        # IAudioSessionManager
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], POINTER(GUID)), (["in"], DWORD), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], POINTER(GUID)), (["in"], DWORD), (["out"], POINTER(c_void_p))),
        # IAudioSessionManager2
        COMMETHOD(
            [], HRESULT, "GetSessionEnumerator",
            (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "sessions"),
        ),
    ]
    ISimpleAudioVolume._methods_ = [
        COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], c_float, "level"), (["in"], POINTER(GUID), "context")),
        COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], POINTER(c_float), "level")),
    ]
    IMMDevice._methods_ = [
        COMMETHOD(
            [], HRESULT, "Activate",
            (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "clsctx"),
            (["in"], c_void_p, "params"), (["out"], POINTER(POINTER(IUnknown)), "iface"),
        ),
    ]
    IMMDeviceCollection._methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "count")),
        COMMETHOD(
            [], HRESULT, "Item",
            (["in"], c_uint, "index"), (["out"], POINTER(POINTER(IMMDevice)), "device"),
        ),
    ]
    IMMDeviceEnumerator._methods_ = [
        COMMETHOD(
            [], HRESULT, "EnumAudioEndpoints",
            (["in"], c_int, "flow"), (["in"], DWORD, "mask"),
            (["out"], POINTER(POINTER(IMMDeviceCollection)), "devices"),
        ),
    ]

    clsid_enumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    e_render, device_state_active = 0, 0x1
    free = ctypes.WinDLL("ole32").CoTaskMemFree
    free.argtypes = [c_void_p]

    def take_string(ptr: int | None) -> str:
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            free(ptr)

    class _ComSession:
        def __init__(self, key: str, pid: int, volume) -> None:
            self.key = key
            self.pid = pid
            self._volume = volume

        def get_volume(self) -> float:
            return float(self._volume.GetMasterVolume())

        def set_volume(self, value: float) -> None:
            self._volume.SetMasterVolume(max(0.0, min(1.0, value)), None)

    def enumerate_sessions() -> list[_ComSession]:
        enumerator = comtypes.CoCreateInstance(
            clsid_enumerator, interface=IMMDeviceEnumerator, clsctx=comtypes.CLSCTX_ALL
        )
        devices = enumerator.EnumAudioEndpoints(e_render, device_state_active)
        found: list[_ComSession] = []
        for index in range(devices.GetCount()):
            try:
                manager = devices.Item(index).Activate(
                    IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None
                ).QueryInterface(IAudioSessionManager2)
                sessions = manager.GetSessionEnumerator()
            except Exception:
                continue  # an endpoint that vanished or refuses activation
            for n in range(sessions.GetCount()):
                try:
                    control = sessions.GetSession(n)
                    key = take_string(control.GetSessionInstanceIdentifier())
                    pid = int(control.GetProcessId())
                    volume = control.QueryInterface(ISimpleAudioVolume)
                except Exception:
                    continue
                if key:
                    found.append(_ComSession(key, pid, volume))
        return found

    _enumerate_sessions = enumerate_sessions
    _backend_ready = True
    return comtypes


def _enumerate_sessions() -> list[Session]:  # replaced by _load_backend
    return []


def _read_journal(path: Path) -> dict[str, list[float]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[float]] = {}
    for key, pair in raw.items():
        if (
            isinstance(key, str)
            and isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(v, (int, float)) for v in pair)
        ):
            out[key] = [float(pair[0]), float(pair[1])]
    return out


class Ducker:
    """Lowers other apps while recording and puts them back afterwards.

    ``duck``/``restore`` return immediately; the work happens in order on a
    private worker thread. Tests pass ``sessions`` and ``threaded=False`` to
    drive the same logic synchronously against fakes.
    """

    def __init__(
        self,
        enabled: bool,
        *,
        sessions: Callable[[], Iterable[Session]] | None = None,
        journal: Path = JOURNAL_PATH,
        own_pid: int | None = None,
        threaded: bool = True,
        ramp_steps: int = RAMP_STEPS,
    ) -> None:
        self.enabled = enabled
        self._sessions = sessions
        self._journal = journal
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._ramp_steps = max(1, ramp_steps)
        # key -> (session, original volume, volume Dictate set)
        self._ducked: dict[str, tuple[Session, float, float]] = {}
        self._queue: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        if threaded:
            self._queue = queue.Queue()
            self._thread = threading.Thread(target=self._run, name="dictate-ducking", daemon=True)
            self._thread.start()
        # Always, even with ducking off: a crash while it was on must still
        # be repaired after the user turns it off and relaunches.
        self._submit(self._recover)

    # --- public, called from the UI thread ---

    def update_settings(self, settings) -> None:
        self.enabled = bool(settings.duck_other_audio)
        if not self.enabled:
            self.restore()

    def duck(self) -> None:
        if self.enabled:
            self._submit(self._duck)

    def restore(self) -> None:
        self._submit(self._restore)

    def shutdown(self, timeout: float = 1.5) -> None:
        """Restore anything lowered and stop the worker before the app exits."""
        self._submit(self._restore)
        if self._queue is not None and self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout)

    # --- worker ---

    def _submit(self, job: Callable[[], None]) -> None:
        if self._queue is None:
            self._safely(job)
        else:
            self._queue.put(job)

    def _run(self) -> None:
        try:
            import comtypes

            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            # Importing comtypes on this thread first already initialized
            # COM here; either apartment works for these in-process objects.
            pass
        while True:
            job = self._queue.get()
            if job is None:
                return
            self._safely(job)

    @staticmethod
    def _safely(job: Callable[[], None]) -> None:
        # Purely a comfort feature: a driver quirk or a Windows change must
        # never take dictation down with it.
        try:
            job()
        except Exception as exc:
            print(f"[dictate] audio ducking error: {exc}")

    def _list(self) -> list[Session]:
        if self._sessions is not None:
            return list(self._sessions())
        if not _backend_ready:
            _load_backend()
        return list(_enumerate_sessions())

    def _ramp(self, moves: list[tuple[Session, float, float]]) -> None:
        """Move each session from its start to its end volume together."""
        steps = self._ramp_steps
        for step in range(1, steps + 1):
            fraction = step / steps
            for session, start, end in moves:
                try:
                    session.set_volume(start + (end - start) * fraction)
                except Exception:
                    pass  # that app closed mid-ramp
            if step < steps:
                time.sleep(RAMP_SECONDS / steps)

    def _duck(self) -> None:
        if self._ducked:
            return
        moves = []
        for session in self._list():
            if session.pid == self._own_pid:
                continue  # Dictate's own cues stay at full level
            try:
                original = session.get_volume()
            except Exception:
                continue
            target = original * DUCK_LEVEL
            self._ducked[session.key] = (session, original, target)
            moves.append((session, original, target))
        if not moves:
            return
        self._write_journal()
        self._ramp(moves)

    def _restore(self) -> None:
        if not self._ducked:
            return
        moves = []
        for session, original, target in self._ducked.values():
            try:
                current = session.get_volume()
            except Exception:
                continue
            if abs(current - target) <= _TOLERANCE:
                moves.append((session, current, original))
        self._ducked.clear()
        self._ramp(moves)
        self._clear_journal()

    def _recover(self) -> None:
        saved = _read_journal(self._journal)
        if not saved:
            return
        for session in self._list():
            pair = saved.get(session.key)
            if pair is None:
                continue
            original, target = pair
            try:
                if abs(session.get_volume() - target) <= _TOLERANCE:
                    session.set_volume(original)
            except Exception:
                continue
        self._clear_journal()

    def _write_journal(self) -> None:
        data = {key: [orig, target] for key, (_s, orig, target) in self._ducked.items()}
        try:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
            self._journal.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            print(f"[dictate] could not journal ducked audio: {exc}")

    def _clear_journal(self) -> None:
        try:
            self._journal.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"[dictate] could not clear ducked-audio journal: {exc}")
