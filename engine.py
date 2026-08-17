"""The AI part: a faster-whisper engine with an explicit load/unload lifecycle.

Two things make this different from a plain "load a model and call it":

1. Device is switchable at runtime (CPU or GPU) without restarting the app.
2. The model can sleep -- unload itself after a period of no use -- so an
   always-running dictation tool doesn't permanently hold VRAM or ~600 MB of
   RAM just in case you might talk.

faster-whisper runs on CTranslate2, not PyTorch, so this project deliberately
does not depend on torch (a ~2.5 GB install for what would amount to one
`cuda.is_available()` call). CUDA is detected through CTranslate2 itself, and
the CUDA support libraries come from the small nvidia-* pip packages -- see
_register_cuda_dlls below for why that needs a manual nudge on Windows.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import gpu_runtime

# Model downloads go through huggingface_hub (pulled in by faster_whisper).
# Two sources of noise there, neither useful for a local offline tool:
#   - The Hub server sends an "unauthenticated requests" nudge as an
#     X-HF-Warning response header on every anonymous API call; the client
#     just relays it through its own logger. HF_HUB_VERBOSITY caps that
#     logger's level and is read at call time, so setting it here (env, not
#     an in-process logging call) is enough regardless of import order.
#   - The symlink-cache UserWarning goes through Python's warnings module
#     instead, hence the separate env var the warning itself names.
# App-level [dictate] logging (see Engine._log_state) is untouched -- this
# only quiets third-party library chatter, not this app's own diagnostics.
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Engine states, reported to the UI through the state callback.
UNLOADED = "unloaded"
LOADING = "loading"
READY = "ready"
TRANSCRIBING = "transcribing"
ERROR = "error"

# Files a whisper repo actually needs, mirroring faster_whisper.utils.download_model's
# own allow_patterns -- kept in sync manually since that list isn't exported.
_MODEL_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

_cuda_dlls_registered = False
# os.add_dll_directory returns a handle that removes the directory again when
# it is garbage collected. Dropping it looks like it works -- the model loads
# fine -- and then fails much later when CTranslate2 lazily opens cuBLAS on the
# first actual compute. These have to stay alive for the life of the process.
_dll_cookies: list = []


def _register_cuda_dlls() -> None:
    """Make the pip-installed cuBLAS and cuDNN DLLs findable.

    The nvidia-* wheels drop their DLLs inside site-packages, nowhere Windows
    looks by default, so the GPU path otherwise dies with "cublas64_12.dll is
    not found" even though the package is installed.

    All three steps below are needed. CTranslate2 opens these lazily, by bare
    name, at the first actual compute -- not at import and not at model load --
    so a half-fix looks like it works right up until the first real
    transcription:

    1. add_dll_directory covers loaders that opt into the safe search order.
    2. PATH covers the plain LoadLibrary("name.dll") that CTranslate2 uses,
       which ignores step 1 entirely.
    3. Preloading by full path is the belt and braces: once a module is in the
       process, a later load by bare name resolves to it regardless of search
       order.

    Safe to call when the packages are absent -- the CPU path needs none of it.
    """
    global _cuda_dlls_registered
    if _cuda_dlls_registered or sys.platform != "win32":
        return
    _cuda_dlls_registered = True

    import ctypes
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return

    bin_dirs: list[Path] = []
    for root in spec.submodule_search_locations:
        for pkg in ("cublas", "cudnn", "cuda_nvrtc"):
            lib = Path(root) / pkg / "bin"
            if lib.is_dir():
                bin_dirs.append(lib)

    for lib in bin_dirs:
        try:
            _dll_cookies.append(os.add_dll_directory(str(lib)))
        except OSError:
            pass

    if bin_dirs:
        os.environ["PATH"] = (
            os.pathsep.join(str(d) for d in bin_dirs)
            + os.pathsep
            + os.environ.get("PATH", "")
        )

    # Dependency order matters: cublas pulls in cublasLt, and the cuDNN
    # sub-libraries have to be present before the main cudnn module opens.
    preload_order = ("cublasLt64_12", "cublas64_12", "cudnn64_9")
    LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
    for lib in bin_dirs:
        for name in preload_order:
            dll = lib / f"{name}.dll"
            if dll.exists():
                try:
                    # winmode lets the DLL resolve its own siblings from the
                    # folder it lives in.
                    _dll_cookies.append(
                        ctypes.WinDLL(str(dll), winmode=LOAD_WITH_ALTERED_SEARCH_PATH)
                    )
                except OSError:
                    pass


def cuda_available() -> bool:
    """True when CTranslate2 can actually see a usable CUDA device."""
    try:
        _register_cuda_dlls()
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(preference: str) -> str:
    """Turn a settings value into a device CTranslate2 will accept.

    'auto' means "GPU if there is one". An explicit 'cuda' still falls back to
    CPU rather than crashing, because a driver update or a second app holding
    the GPU shouldn't take dictation offline entirely.
    """
    if preference == "cpu":
        return "cpu"
    if preference in ("cuda", "auto"):
        return "cuda" if cuda_available() else "cpu"
    return "cpu"


def _resolve_repo_id(size_or_id: str) -> str | None:
    """Mirror faster_whisper.utils.download_model's own repo_id resolution.

    Returns None when it can't be determined, so the caller falls back to
    faster-whisper's normal (progress-free) download path rather than guess.
    """
    if "/" in size_or_id:
        return size_or_id
    try:
        from faster_whisper.utils import _MODELS

        return _MODELS.get(size_or_id)
    except Exception:
        return None


def _make_progress_reporter(on_bytes: Callable[[int, Optional[int]], None]):
    """Build a tqdm-duck-typed class for huggingface_hub's ``tqdm_class`` hook.

    Deliberately does not subclass real tqdm: tqdm writes to the console by
    default, and this app may have no console attached at all (the hidden
    startup launch). huggingface_hub.snapshot_download instantiates this
    class several times per call -- an outer bar counting files, plus a pair
    of internal bars counting bytes transferred/reconstructed -- and only
    the byte bars (``unit="B"``) are forwarded to ``on_bytes``. The file bar
    is ignored: for a whisper model it stalls near-complete for most of the
    download (one huge model.bin among a few tiny sidecar files) and would
    read as more broken than showing no percentage at all.

    ``__getattr__`` absorbs any tqdm method this doesn't implement as a
    no-op -- huggingface_hub's internal progress plumbing calls a few
    bookkeeping methods beyond update/close, and a future release adding one
    more should never be able to turn a progress bar into a broken load.
    """

    class _HubProgress:
        def __init__(self, *_args, total=None, initial=0, unit="", **_kwargs):
            self.total = total
            self.n = initial
            self._unit = unit

        def update(self, n=1):
            self.n += n or 0
            if self._unit == "B":
                on_bytes(self.n, self.total)

        def refresh(self):
            pass

        def close(self):
            pass

        @property
        def format_dict(self):
            return {}

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            return False

        def __getattr__(self, _name):
            return lambda *_a, **_k: None

    return _HubProgress


def _predownload_with_progress(
    size: str, on_bytes: Callable[[int, Optional[int]], None]
) -> None:
    """Pull a model into the local Hub cache while reporting real byte progress.

    Silent no-op on anything unexpected -- callers always fall through to
    faster-whisper's own download afterward, so a failure here costs only
    the percentage, never the model load itself.
    """
    repo_id = _resolve_repo_id(size)
    if not repo_id:
        return
    try:
        import huggingface_hub
    except Exception:
        return

    try:
        huggingface_hub.snapshot_download(
            repo_id, allow_patterns=_MODEL_ALLOW_PATTERNS, local_files_only=True
        )
        return  # already cached -- nothing to download, nothing to report
    except Exception:
        pass

    try:
        huggingface_hub.snapshot_download(
            repo_id,
            allow_patterns=_MODEL_ALLOW_PATTERNS,
            tqdm_class=_make_progress_reporter(on_bytes),
        )
    except Exception:
        pass


class Engine:
    """Owns the whisper model and everything about when it exists in memory.

    All public methods are safe to call from any thread. Transcription and
    loading both happen under one lock, so an idle-unload can never pull the
    model out from under an in-flight transcription.
    """

    def __init__(
        self,
        settings,
        on_state: Optional[Callable[[str, str, Optional[float]], None]] = None,
    ):
        self._settings = settings
        self._on_state = on_state or (lambda state, detail, progress=None: None)

        self._model = None
        self._loaded_key: tuple[str, str] | None = None  # (model_size, device)
        self._lock = threading.RLock()
        self._last_used = time.monotonic()
        self._state = UNLOADED
        self._detail = ""
        self._progress: Optional[float] = None

        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._idle_loop, daemon=True)
        self._reaper.start()

    # --- state reporting ---

    def _set_state(
        self, state: str, detail: str = "", progress: Optional[float] = None
    ) -> None:
        self._state = state
        self._detail = detail
        self._progress = progress
        self._log_state(state, detail)
        self._on_state(state, detail, progress)

    @staticmethod
    def _log_state(state: str, detail: str) -> None:
        """Print every state change so the console (run-dictate.bat's window)
        works as a live debugging log -- in particular, so it's possible to
        actually see which device a reload landed on rather than guessing.

        TRANSCRIBING is left out: it fires on every utterance and main.py
        already logs the recognized text for that, which is the useful half.
        """
        if state == LOADING:
            print(f"[dictate] loading model — {detail}" if detail else "[dictate] loading model")
        elif state == READY:
            print(f"[dictate] model ready — {detail}" if detail else "[dictate] model ready")
        elif state == UNLOADED:
            print("[dictate] model unloaded")
        elif state == ERROR:
            print(f"[dictate] model error — {detail}")

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_status(self) -> tuple[str, str, Optional[float]]:
        """The most recent (state, detail, progress) triple, for a UI that
        polls reactively (settings_window.py's refresh_status()) rather than
        wiring its own signal to every _set_state call."""
        return (self._state, self._detail, self._progress)

    @property
    def active_device(self) -> str:
        """The device actually in use, which may differ from the preference."""
        return self._loaded_key[1] if self._loaded_key else ""

    def update_settings(self, settings) -> None:
        """Apply new settings, unloading the model if the choice invalidates it.

        Changing the sleep timer is free. Changing model size or device means
        the resident model is now the wrong one, so it goes and the next
        utterance pays the reload.
        """
        with self._lock:
            old = (self._settings.model_size, resolve_device(self._settings.device))
            self._settings = settings
            new = (settings.model_size, resolve_device(settings.device))
            if old != new and self._model is not None:
                self._unload_locked()

    # --- model lifecycle ---

    def preload(self) -> None:
        """Load the model ahead of first use, off the UI thread."""
        threading.Thread(target=self._safe_ensure_loaded, daemon=True).start()

    def _safe_ensure_loaded(self) -> None:
        try:
            self.ensure_loaded()
        except Exception as exc:
            self._set_state(ERROR, str(exc))

    def ensure_loaded(self):
        with self._lock:
            wanted = (self._settings.model_size, resolve_device(self._settings.device))
            if self._model is not None and self._loaded_key == wanted:
                return self._model
            if self._model is not None:
                self._unload_locked()

            size, device = wanted
            compute_type = "float16" if device == "cuda" else "int8"
            self._set_state(LOADING, f"{size} on {device.upper()}")
            # device == "cuda" only happens once resolve_device() has already
            # confirmed real GPU hardware via the system driver -- detection
            # doesn't need our compute DLLs, only actual inference does. The
            # only open question here is whether those DLLs are on disk.
            if device == "cuda" and gpu_runtime.needs_download(gpu_available=True):
                self._download_gpu_runtime()
            _register_cuda_dlls()
            self._download_with_progress(size, device)
            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(size, device=device, compute_type=compute_type)
            except Exception:
                # A GPU load can fail after detection succeeded (VRAM already
                # spoken for, driver mismatch). Falling back beats failing.
                if device != "cpu":
                    self._set_state(LOADING, f"{size} on CPU (GPU load failed)")
                    self._model = WhisperModel(size, device="cpu", compute_type="int8")
                    wanted = (size, "cpu")
                else:
                    raise

            self._loaded_key = wanted
            self._last_used = time.monotonic()
            self._set_state(READY, f"{wanted[0]} on {wanted[1].upper()}")
            return self._model

    def _download_gpu_runtime(self) -> None:
        """Fetch the CUDA compute DLLs a Core-only install doesn't ship with.

        Only reached once resolve_device() has already confirmed real GPU
        hardware -- see the call site's comment. A failed download just
        leaves needs_download() true again for next time; the GPU-load
        try/except a few lines below this in ensure_loaded() still catches
        the resulting CTranslate2 failure and falls back to CPU either way,
        so nothing here can turn into a hard failure.
        """
        last_frac = 0.0
        last_emit = time.monotonic()

        def on_bytes(n: int, total: int) -> None:
            nonlocal last_frac, last_emit
            if not total:
                return
            frac = min(1.0, n / total)
            now = time.monotonic()
            if frac < 1.0 and frac - last_frac < 0.01 and now - last_emit < 0.1:
                return
            last_frac, last_emit = frac, now
            pct = int(frac * 100)
            self._set_state(LOADING, f"GPU acceleration — downloading {pct}%", frac)

        gpu_runtime.download_and_install(on_bytes)

    def _download_with_progress(self, size: str, device: str) -> None:
        """Report real percentage while a first-time model download runs.

        A no-op for an already-cached model: _predownload_with_progress's own
        local_files_only check finds it and returns immediately, so this
        never adds a percentage flicker to the common fast-load path.
        """
        last_frac = 0.0
        last_emit = time.monotonic()

        def on_bytes(n: int, total: Optional[int]) -> None:
            nonlocal last_frac, last_emit
            if not total:
                return
            frac = min(1.0, n / total)
            now = time.monotonic()
            # Throttled so a fast connection's flood of callbacks doesn't
            # flood the UI thread with state changes.
            if frac < 1.0 and frac - last_frac < 0.01 and now - last_emit < 0.1:
                return
            last_frac, last_emit = frac, now
            pct = int(frac * 100)
            self._set_state(
                LOADING, f"{size} on {device.upper()} — downloading {pct}%", frac
            )

        _predownload_with_progress(size, on_bytes)

    def _unload_locked(self) -> None:
        self._model = None
        self._loaded_key = None
        # CTranslate2 frees its VRAM when the model object is collected, and
        # CPython won't necessarily collect it promptly on its own.
        import gc

        gc.collect()
        self._set_state(UNLOADED, "")

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                self._unload_locked()

    def reload(self) -> None:
        """Force a fresh load even if a matching model is already resident.

        ensure_loaded() dedupes on (model_size, device) and would otherwise
        serve the cached model as-is -- this is for when the settings key
        hasn't changed but the model itself should be reloaded anyway.
        """
        with self._lock:
            self._unload_locked()
        self.preload()

    def _idle_loop(self) -> None:
        """Unload the model once it has gone unused for the configured time."""
        while not self._stop.wait(5.0):
            s = self._settings
            if not s.sleep_enabled or self._model is None:
                continue
            if time.monotonic() - self._last_used < s.sleep_after_minutes * 60:
                continue
            # Skip the unload if a transcription grabbed the lock in the
            # meantime; the next pass five seconds later will catch it.
            if self._lock.acquire(blocking=False):
                try:
                    if self._model is not None and self._state == READY:
                        self._unload_locked()
                finally:
                    self._lock.release()

    def shutdown(self) -> None:
        self._stop.set()
        self.unload()

    # --- the actual work ---

    def transcribe(self, audio: np.ndarray) -> str:
        """Turn float32 16 kHz mono samples into text. Blocking; call off-UI."""
        if audio.size == 0:
            return ""
        with self._lock:
            model = self.ensure_loaded()
            self._set_state(TRANSCRIBING, "")
            try:
                options = {
                    "language": "en",
                    "beam_size": 1,      # dictation is short and clean; greedy is
                                         # noticeably faster and rarely worse here
                    "vad_filter": True,  # drop leading/trailing silence from the
                                         # key press and release
                    "condition_on_previous_text": False,
                }
                if self._settings.vocabulary:
                    # Whisper treats this as a recognition hint, not a literal
                    # replacement list. It helps uncommon names and product terms
                    # without changing ordinary speech into a command language.
                    options["initial_prompt"] = (
                        "Names and terms that may appear: "
                        + ", ".join(self._settings.vocabulary)
                        + "."
                    )
                segments, _info = model.transcribe(audio, **options)
                text = "".join(seg.text for seg in segments).strip()
            finally:
                self._last_used = time.monotonic()
                if self._model is not None:
                    self._set_state(READY, f"{self._loaded_key[0]} on {self._loaded_key[1].upper()}")
            return text
