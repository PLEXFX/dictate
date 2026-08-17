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
    live_preview_enabled: bool = True  # rolling two-line card while speaking
    enhanced_preview_enabled: bool = False  # dedicated Base model on CPU

    # --- updates ---
    auto_update_enabled: bool = True  # periodically check GitHub for a new release

    # --- appearance ---
    always_visible: bool = False  # keep the bar on screen even when idle
    bar_margin: int = 8           # gap in px between the bar and the taskbar
    bar_linger_ms: int = 750      # how long the bar stays after finishing before it fades

    # --- internal ---
    onboarding_complete: bool = False

    def clamped(self) -> "Settings":
        """Return a copy with out-of-range values pulled back into bounds.

        Guards against a hand-edited settings file putting the app into a state
        the UI can't represent (a 0-minute sleep timer would unload the model
        between every single utterance).
        """
        out = Settings(**asdict(self))
        if out.device not in {d[0] for d in DEVICES}:
            out.device = "cpu"
        if out.model_size not in {m[0] for m in MODELS}:
            out.model_size = "small.en"
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
        out.live_preview_enabled = bool(out.live_preview_enabled)
        out.enhanced_preview_enabled = bool(out.enhanced_preview_enabled)
        out.auto_update_enabled = bool(out.auto_update_enabled)
        out.onboarding_complete = bool(out.onboarding_complete)
        out.sleep_after_minutes = min(max(float(out.sleep_after_minutes), 0.5), 240.0)
        out.bar_margin = min(max(int(out.bar_margin), 0), 400)
        out.bar_linger_ms = min(max(int(out.bar_linger_ms), 400), 4000)
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
