"""Settings: defaults, load, and save.

Stored as JSON in %APPDATA%\\dictate\\settings.json rather than next to the
code, so rebuilding or moving the project folder never loses your preferences.

User-facing preferences are surfaced in the settings window (Ctrl+Alt+D).
Internal flags record first-run completion and whether Windows should launch
Dictate automatically.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "dictate"
CONFIG_PATH = CONFIG_DIR / "settings.json"


def model_dir() -> Path:
    """Return Dictate's private speech-model cache.

    Keeping downloads beneath the app's own data folder makes them easy to
    find and keeps them separate from another app's Hugging Face cache.
    """
    return CONFIG_DIR / "models"

# Model choices, smallest first. The second value is the rough resident cost
# once loaded -- shown in the settings window so the tradeoff is visible at the
# point of choosing rather than buried in a readme.
MODELS = [
    ("tiny.en", "~150 MB, fastest, least accurate"),
    ("base.en", "~250 MB, fast"),
    ("small.en", "~600 MB, best balance for dictation"),
    ("medium.en", "~1.8 GB, slower, better on hard audio"),
    ("large-v3-turbo", "~1.9 GB, most accurate, GPU recommended"),
]

# Technical model identifiers are for faster-whisper; every person-facing
# surface should use the compact names below instead of exposing identifiers
# like ``small.en`` or ``large-v3-turbo``.
MODEL_DISPLAY_NAMES = {
    "tiny.en": "Tiny English",
    "base.en": "Base English",
    "small.en": "Small English",
    "medium.en": "Medium English",
    "large-v3-turbo": "Large v3 Turbo",
}


def model_display_name(model_size: str) -> str:
    """Return a concise human-facing name for a faster-whisper model."""
    return MODEL_DISPLAY_NAMES.get(model_size, model_size)

DEVICES = [
    ("auto", "Use the GPU when it is available"),
    ("cuda", "GPU (NVIDIA) -- fastest, holds VRAM while loaded"),
    ("cpu", "CPU -- leaves VRAM free, still fast on your Ryzen"),
]

# Outcome-based choices shown on the main Settings page. The raw model and
# processing device remain available under Advanced settings.
TRANSCRIPTION_MODES = [
    ("balanced", "Everyday (recommended)", "small.en", "cpu"),
    ("faster", "Fast response", "small.en", "cuda"),
    ("accurate", "Best accuracy", "medium.en", "auto"),
    ("max", "Max accuracy", "large-v3-turbo", "cuda"),
]


def transcription_mode_settings(mode: str) -> tuple[str, str] | None:
    for value, _label, model_size, device in TRANSCRIPTION_MODES:
        if value == mode:
            return model_size, device
    return None


def transcription_mode_for(model_size: str, device: str) -> str:
    for value, _label, mode_model, mode_device in TRANSCRIPTION_MODES:
        if (model_size, device) == (mode_model, mode_device):
            return value
    return "custom"


# The talk key lives here and nowhere else, so "the default is F9" is one
# fact rather than a literal repeated across the dataclass, the settings
# window's fallback and the clamp below.
DEFAULT_PTT_KEY = "f9"
DEFAULT_SETTINGS_HOTKEY = "ctrl+alt+d"
MAX_VOCABULARY_WORDS = 100
MAX_VOCABULARY_WORD_LENGTH = 80


def clean_vocabulary(words: object) -> list[str]:
    """Keep a small, predictable local list of names and terms for Whisper.

    The list is user-provided recognition context, not a text-replacement
    system.  It is deliberately capped so a pasted document cannot become a
    giant initial prompt or make the saved settings file unwieldy.
    """
    if not isinstance(words, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for word in words:
        if not isinstance(word, str):
            continue
        text = " ".join(word.split())[:MAX_VOCABULARY_WORD_LENGTH]
        key = text.casefold()
        if text and key not in seen:
            cleaned.append(text)
            seen.add(key)
        if len(cleaned) == MAX_VOCABULARY_WORDS:
            break
    return cleaned


def valid_combo(text: object) -> bool:
    """True when a binding has at least one usable part.

    An empty or whitespace-only string parses to no keys at all, which would
    leave the app with nothing bound -- and for the settings hotkey that means
    being locked out of the one window that could fix it.
    """
    return isinstance(text, str) and any(part.strip() for part in text.split("+"))


def _as_float(value: object, default: float) -> float:
    """Coerce a stored value to a float, falling back to the default.

    A hand-edited settings file can hold anything at all -- a string, null, a
    list. Whatever it is, it must clamp to something usable rather than raise:
    load() runs before Qt exists, so an exception here means the app dies with
    no window and no message.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    # NaN survives min/max untouched and would poison every later comparison.
    return default if number != number else number


def _as_int(value: object, default: int) -> int:
    """Coerce a stored value to an int, falling back to the default."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_choice(value: object, allowed: set[str], default: str) -> str:
    """Pick a stored value only when it is one of the known options.

    The isinstance check matters as much as the membership test: an unhashable
    value (a list, say) raises TypeError from ``in`` rather than simply
    failing it.
    """
    return value if isinstance(value, str) and value in allowed else default


@dataclass
class Settings:
    # --- model ---
    device: str = "cpu"          # auto | cuda | cpu
    model_size: str = "small.en"

    # --- lifecycle ---
    sleep_enabled: bool = True   # unload the model after a period of no use
    sleep_after_minutes: float = 10.0
    start_with_windows: bool = False

    # --- input ---
    input_device: str = ""                  # empty means the Windows default
    ptt_key: str = DEFAULT_PTT_KEY          # hold to talk
    tap_to_lock: bool = True                # a tap of that key locks recording on
    settings_hotkey: str = DEFAULT_SETTINGS_HOTKEY   # open this window
    vocabulary: list[str] = field(default_factory=list)

    # --- feedback ---
    sound_cues: bool = True       # short tones when the mic opens and closes

    # --- updates ---
    auto_update_enabled: bool = True  # periodically check GitHub for a new release

    # --- notifications ---
    # Dictate's own bar toast is always on and covers every notification this
    # app raises. This additionally mirrors the same moments to a Windows
    # system tray balloon for anyone who wants them to persist in Action
    # Center too. Off by default -- the bar toast is the one notification
    # system, not a second one bolted on unless asked for.
    system_notifications_enabled: bool = False

    # --- appearance ---
    theme_mode: str = "system"  # system | light | dark
    always_visible: bool = False  # keep the bar on screen even when idle
    bar_width: int = 200          # visible activity-bar width in logical pixels
    bar_margin: int = 8           # gap in px between the bar and the taskbar
    bar_linger_ms: int = 750      # how long the bar stays after finishing before it fades

    # --- internal ---
    onboarding_complete: bool = False

    def clamped(self) -> "Settings":
        """Return a copy with out-of-range values pulled back into bounds.

        Guards against a hand-edited settings file putting the app into a state
        the UI can't represent (a 0-minute sleep timer would unload the model
        between every single utterance).

        Every field is coerced, not just range-checked. A settings file holding
        the wrong *type* -- ``"bar_width": "wide"``, a null sleep timer, a list
        where a name belongs -- has to end up at its default rather than raise.
        load() runs as the very first statement of App.__init__, before there is
        a QApplication to show anything, so an exception raised here is an app
        that silently never appears.
        """
        out = Settings(**asdict(self))
        fallback = Settings()
        out.device = _as_choice(out.device, {d[0] for d in DEVICES}, fallback.device)
        out.model_size = _as_choice(
            out.model_size, {m[0] for m in MODELS}, fallback.model_size
        )
        if not isinstance(out.input_device, str):
            out.input_device = ""
        if not valid_combo(out.ptt_key):
            out.ptt_key = DEFAULT_PTT_KEY
        if not valid_combo(out.settings_hotkey):
            out.settings_hotkey = DEFAULT_SETTINGS_HOTKEY
        out.vocabulary = clean_vocabulary(out.vocabulary)
        out.start_with_windows = bool(out.start_with_windows)
        out.tap_to_lock = bool(out.tap_to_lock)
        out.sound_cues = bool(out.sound_cues)
        out.auto_update_enabled = bool(out.auto_update_enabled)
        out.system_notifications_enabled = bool(out.system_notifications_enabled)
        out.theme_mode = _as_choice(
            out.theme_mode, {"system", "light", "dark"}, fallback.theme_mode
        )
        out.onboarding_complete = bool(out.onboarding_complete)
        out.sleep_after_minutes = min(
            max(_as_float(out.sleep_after_minutes, fallback.sleep_after_minutes), 0.5),
            240.0,
        )
        out.bar_width = min(max(_as_int(out.bar_width, fallback.bar_width), 180), 280)
        out.bar_margin = min(max(_as_int(out.bar_margin, fallback.bar_margin), 0), 400)
        out.bar_linger_ms = min(
            max(_as_int(out.bar_linger_ms, fallback.bar_linger_ms), 400), 4000
        )
        return out


def load() -> Settings:
    """Read settings from disk, falling back to defaults for anything missing.

    A corrupt or partially-written file must never stop the app from starting,
    so any read failure quietly yields defaults instead of raising.
    """
    if not CONFIG_PATH.exists():
        return Settings()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    known = {f for f in Settings.__dataclass_fields__}
    return Settings(**{k: v for k, v in raw.items() if k in known}).clamped()


def save(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(settings), indent=2), encoding="utf-8"
    )
