# Dictate

Hold a key anywhere in Windows, speak, release. The text appears in whatever
you were typing in. Transcription runs on this machine — no account and no
cloud transcription. Speech-model files may download the first time they are used.

## What's new in v0.1.0-beta.2

Dictate can now be installed as a normal Windows app — no Python required —
with an optional GPU-acceleration add-on and a background updater that keeps
it current. Skipped GPU acceleration at install time? Switching to GPU in
Settings now downloads it on the spot instead of silently staying on CPU. A
silent or dead microphone and a slow first-time model download now both give
clear feedback instead of looking stuck.

See [CHANGELOG.md](CHANGELOG.md) for the complete version history.

A small Windows 11 flyout appears centred just above the taskbar while you
talk. One hairline runs its width and never moves: in silence that is all you
see, speaking grows slim capsules out of it that scroll a history of how loud
you have been, a model loading sinks them back into the line while two accent
segments sweep along it, and transcribing undulates them with two waves
crossing in opposite directions so the line never repeats a pattern.
States morph into one another rather than swapping, each capsule starting a
beat after its neighbour so the change ripples across, and it takes its colour
from your Windows accent. It draws at your display's refresh rate, is built
for whatever scale factor that display uses, and stops entirely once nothing
is moving.

## Install

Download the latest `Dictate-Setup-*.exe` from
[Releases](https://github.com/PLEXFX/dictate/releases/latest) and run it — no
Python or setup needed. It's unsigned, so Windows SmartScreen will show an
"unrecognized publisher" warning the first time; click **More info → Run
anyway**. The installer offers an optional GPU-acceleration component for
supported NVIDIA graphics cards; CPU transcription works either way, and
Dictate checks for new versions in the background afterward.

## Running it

Double-click `run-dictate.bat`. It lives in the tray; there is no main window.
The first launch explains the core controls and lets you choose a microphone.

- **Hold F9** — talk. Release and the text is pasted where your cursor is.
- **Ctrl+Alt+D** — settings.
- Tray icon — left click for settings, right click for a menu (load model now,
  unload model, quit).

Use `run-dictate-debug.bat` instead when something is wrong; it keeps a console
open so errors are visible.

## Settings

The main page keeps only the everyday choices visible. Changes save
automatically, and preferences are stored in the normal Windows per-user
app-data folder rather than beside the source code.

| Setting | What it does |
| --- | --- |
| Microphone | Windows default or a specific available microphone |
| Hold to talk | Click and press the key or mouse button to hold, default `f9`. Hold inputs together to record a combination such as `ctrl+mouse4`. An empty or unusable binding falls back to `f9`. |
| Transcription mode | Everyday, Fast response, Best accuracy, or Max accuracy without model jargon |
| Sleep when idle / Sleep after | Release model memory after a clear slider-controlled delay |
| Sounds | A short rising tone when the mic opens, a falling one when it starts transcribing |
| Start with Windows | Launch Dictate silently when the current user signs in |

**Advanced settings** contains the raw controls:

| Setting | What it does |
| --- | --- |
| Processing | CPU, GPU, or automatic. If installed without GPU support, switching to GPU downloads the acceleration files first (about 1.3 GB from PyPI). |
| Speech model | `tiny.en` through `large-v3-turbo`; `small.en` is the default |
| Open settings | Click and press a keyboard or mouse combination, default `ctrl+alt+d` |
| Always show activity bar / Bar position | Control when and where the bar appears |

Dictate always pastes the result and adds one separating space. Those are
consistent app behaviors rather than settings. Slider values show the default
and offer a Reset control whenever they move away from it.

The Privacy link at the bottom of Settings explains microphone use, local
transcription, model downloads, clipboard behavior, and diagnostics in plain
language. Debug output reports activity and errors without printing dictated text.

## Sounds

Two cues, on by default and switchable off under Sounds in Settings: a rising
pair of notes when the microphone opens and the same two notes falling when it
closes and transcription begins. They are the same idea heard twice, so they
read as "open" and "close" rather than as two unrelated beeps.

They are synthesised rather than shipped as files — `sounddevice` and numpy
are already here for capture and the waveform. Each is about 150 ms and begins
and ends at exactly zero amplitude, because a waveform that starts or stops
away from zero is a click, and a click is the most fatiguing thing a sound you
hear on every single use can have. Volume is one constant, `CUE_VOLUME` in
`sounds.py`; pitch, spacing and decay are constants beside it.

Playback goes through Qt's `QSoundEffect`, which decodes each cue once and
keeps a warm pipeline. An earlier version opened a fresh output stream per cue
and dropped them at random: this machine's default output reports 90 ms of
buffering before a newly opened stream makes any sound, and an HDMI or
DisplayPort endpoint waking from idle on top of that can swallow a 150 ms clip
whole. The generated WAVs are cached in the app-data folder under a name
containing a hash of the recipe, so retuning any constant writes new files
instead of leaving a stale cue behind.

The opening cue plays just after the microphone stream opens, so if you are on
speakers rather than headphones it is audible to the microphone. It is short
and quiet enough that transcription is unaffected in practice; if it ever is,
the fix is to drop the first ~150 ms of each clip.

## What it costs

Measured on this machine (RTX 4070 SUPER, Ryzen 7 9700X) with `small.en`,
against 4-second sentences.

| | CPU (int8) | GPU (float16) |
| --- | --- | --- |
| Time per 4s utterance | ~0.9 s | ~0.19 s |
| Speed vs realtime | 0.24x | 0.05x |
| Word accuracy on the test set | 97% | 100% |
| RAM with model loaded | 582 MB | 449 MB |
| VRAM with model loaded | 0 MB | 766 MB |

Idle, with the model asleep and the bar hidden, the app sits at **71 MB of RAM
and no VRAM at all**. The bar costs about 0.2 ms per frame — about 1% of one core
at 60 Hz and 2% at 144 Hz, and only while it is actually on screen: an idle
bar stops its clock and costs nothing. The shadow and the acrylic grain are
rendered once per display scale factor and cached, and a capsule sitting flush
in the hairline is skipped rather than drawn, so silence is close to free.

**CPU is the default, deliberately.** It never touches the GPU, and 0.9 s for a
sentence is below the threshold where you would sit waiting. Switch to GPU if
you dictate long passages and want them near-instant.

One honest caveat on the GPU path: sleeping releases the model's VRAM but not
the CUDA context, which holds roughly 200 MB for as long as the app runs. Only
quitting gives that back. On CPU, sleep really does return everything.

## How it fits together

| File | Job |
| --- | --- |
| `main.py` | Wiring, tray icon, and the thread policy for the whole app |
| `engine.py` | faster-whisper with load/unload/device-switch lifecycle |
| `audio.py` | Mic capture and the FFT the waveform is drawn from |
| `bar.py` | The floating bar above the taskbar |
| `settings_window.py` | Essential and Advanced settings in a Windows 11 layout |
| `startup.py` | Current-user Windows startup registration |
| `toggle.py` | Windows 11 toggle switch |
| `hotkeys.py` | Global hold-to-talk and the settings combo |
| `inject.py` | Getting text into the focused window |
| `sounds.py` | The two synthesised cues and their playback |
| `config.py` | Settings defaults, load, save, and clamping |
| `updater.py` | Background GitHub Releases check, download, and verification |
| `gpu_runtime.py` | On-demand CUDA compute DLL download from PyPI for an installed build |

Three threads matter: the Qt UI thread, pynput's listener thread, and a
short-lived worker per utterance. Every hop between them goes through the
`Bridge` signals in `main.py`.

Transcription is [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
which runs on CTranslate2 rather than PyTorch. That is why there is no `torch`
in the dependency list — it would be about 2.5 GB of install for one
`cuda.is_available()` call. CUDA support comes from the much smaller
`nvidia-cublas-cu12` and `nvidia-cudnn-cu12` wheels.

## Setup from scratch

Needs [uv](https://docs.astral.sh/uv/). Models download themselves on first use
and cache in `%USERPROFILE%\.cache\huggingface`.

```
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r pyproject.toml
```

## If something goes wrong

**Nothing happens when I hold F9.** Check the tray icon is there. Some
full-screen games and elevated windows (Task Manager, anything running as
administrator) swallow global hotkeys — Windows blocks a normal-privilege app
from seeing keys pressed over an elevated one.

**"Python Launcher is sorry to say ... No Python at ...".** This is uv's venv
launcher shim failing to start an interpreter that is present and working — it
has been seen on this machine across several uv venvs, not just this project.

Both launchers handle it automatically: they test the shim first and, if it
fails, run the base interpreter directly with the venv's packages on
`PYTHONPATH`. Same result, nothing in between to break.
`run-dictate-debug.bat` prints which route it took.

If you see the message anyway, the environment is genuinely missing. Rebuild it:

```
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r pyproject.toml
```

Downloads are cached, so a rebuild is much faster than the first install.

**Text pastes into the wrong place.** The paste goes wherever the cursor is
when you release the key. Click into the field first.

**"cublas64_12.dll is not found".** The GPU support wheels are missing or the
venv is broken; reinstall with the command above. The app falls back to CPU
rather than failing outright, so this shows up as unexpectedly slower
transcription rather than an error.

**It feels slow on the first utterance after a break.** That is the model
reloading after sleeping. Either raise the sleep timer or turn sleep off in
settings.
