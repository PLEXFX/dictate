# Dictate

Hold a key anywhere in Windows, speak, release. The text appears in whatever
you were typing in. Transcription runs on this machine — no account and no
cloud transcription. Speech-model files may download the first time they are used.

## What's new in v1.0.2-beta.1

GPU acceleration now downloads automatically in the background — at launch
if chosen during install, or the moment you pick it in Settings — with a
live progress row and a "Download now" option for anyone who skipped it.
Dictation stays fully usable on CPU while it downloads. The Settings window
also now supports Windows' own Acrylic transparency effect.

See [CHANGELOG.md](CHANGELOG.md) for the complete version history.
Read [PRIVACY.md](PRIVACY.md) for exactly what Dictate processes and what its
optional downloads contact.

A small Windows 11 flyout appears centred just above the taskbar while you
talk. One hairline runs its width and never moves: in silence that is all you
see, speaking grows slim capsules out of it that scroll a history of how loud
you have been, a model loading sinks them back into the line while two accent
segments sweep along it, and transcribing undulates them with two waves
crossing in opposite directions so the line never repeats a pattern.
States morph into one another rather than swapping, each capsule starting a
beat after its neighbour so the change ripples across, and it takes its colour
from your Windows accent, glowing a little brighter the louder you speak. It
draws at your display's refresh rate, is built for whatever scale factor
that display uses, and stops entirely once nothing is moving. A live
transcript card grows out of the same surface while you talk, showing the
last line or two of what Dictate has heard so far.

## Install

Download the latest `Dictate-Setup-*.exe` from
[Releases](https://github.com/PLEXFX/dictate/releases/latest) and run it — no
Python or setup needed. It's unsigned, so Windows SmartScreen will show an
"unrecognized publisher" warning the first time; click **More info → Run
anyway**. If Setup detects a supported NVIDIA graphics card, it offers a
checkbox to enable GPU acceleration — the actual files download on demand
the first time Dictate needs them rather than being bundled into the
installer, so machines without a card never download them at all. There's
also a checkbox for whether Dictate should check GitHub for new versions in
the background afterward — on by default, and also changeable later from
Settings. Uninstalling removes your settings along with the program, so a
later reinstall starts completely fresh rather than picking up an old
configuration.

## Running it

Double-click `run-dictate.bat`. It lives in the tray; there is no main window
and no console window either. The first launch explains the core controls and
lets you choose a microphone.

- **Hold F9** — talk. Release and the text is pasted where your cursor is.
- **Tap F9** — for anything longer than a sentence. Recording stays on with your
  hands free; tap again to finish, or press Esc to throw it away. A short level
  tone marks the moment it locks, and the bar staying on screen after you let go
  is the other half of that signal. A locked recording stops itself after five
  minutes so a forgotten one cannot run all day.
- **Ctrl+Alt+D** — settings.
- Tray icon — left click for settings, right click for a menu (load model now,
  copy the last dictation, undo the last dictation, unload model, quit).

Use `run-dictate-debug.bat` instead when something is wrong; it keeps a
console open so `[dictate]` status lines and errors are visible, and you can
type commands into it — `help` lists them (model loading, status, GPU and
version info, a settings/update shortcut, and a couple that fake an update
notification for testing without touching the network).

## Settings

The main page keeps only the everyday choices visible. Changes save
automatically, and preferences are stored in the normal Windows per-user
app-data folder rather than beside the source code.

| Setting | What it does |
| --- | --- |
| Microphone | Windows default or a specific available microphone |
| Hold to talk | Click and press the key or mouse button to hold, default `f9`. Hold inputs together to record a combination such as `ctrl+mouse4`. An empty or unusable binding falls back to `f9`. |
| Tap to keep recording | On by default. Tapping the talk key instead of holding it locks recording on until you tap again. Turn it off to make the key hold-only. |
| Transcription mode | Everyday, Fast response, Best accuracy, or Max accuracy without model jargon |
| Words I use | Local names, brands, and terms that help Dictate recognize uncommon words |
| Sleep when idle / Sleep after | Release model memory after a clear slider-controlled delay |
| Sounds | A short rising tone when the mic opens, a falling one when it starts transcribing |
| Start with Windows | Launch Dictate silently when the current user signs in |

**Advanced settings** contains the raw controls:

| Setting | What it does |
| --- | --- |
| Processing | CPU, GPU, or automatic. If installed without GPU support, switching to GPU downloads the acceleration files first (about 1.3 GB from PyPI). |
| Speech model | `tiny.en` through `large-v3-turbo`; `small.en` is the default. **Open model folder** shows Dictate's private model cache. |
| Open settings | Click and press a keyboard or mouse combination, default `ctrl+alt+d` |
| Always show activity bar / Bar position | Control when and where the bar appears |

Dictate always pastes the result and adds one separating space. Those are
consistent app behaviors rather than settings. Slider values show the default
and offer a Reset control whenever they move away from it.

**Undo last dictation** in the tray menu takes back the text Dictate just
pasted, and refuses whenever it cannot be confident that text is still the last
thing to have changed. It will not act if you have typed or clicked in that
window since, if the window has closed, if more than 30 seconds have passed, or
if the window will not come back to the front. It is offered once per dictation.
Dictate cannot read another application's undo history, so refusing on any doubt
is the only safe rule: a refused undo costs you one re-selection, while a wrong
one would destroy work Dictate never wrote.

The tray menu can copy the most recent dictation again. It keeps that one
result in memory only; choosing it temporarily replaces the clipboard, then
restores what was there after 5 seconds. If you copy anything in that window,
Dictate leaves your newer clipboard item alone.

A Notifications section in Settings has one toggle, "Also show Windows
notifications" — off by default. Dictate's own floating-bar notification
always shows for everything it raises (opening, an update, a second launch
attempt); turning this on also mirrors each one as a normal Windows
notification.

The Dictate Update section at the bottom of Settings has its own toggle,
"Check for updates automatically" — on by default, and also set at install
time. Turning it off stops the daily background check and disables the
manual "Check for updates" button; Dictate makes no GitHub request at all
while it's off. Finding a newer version never downloads it automatically —
Settings shows "Download & install," and the bar shows the same
notification; either one starts the download, verifies it, and installs it
only once you click it.

The Privacy page at the bottom of Settings explains microphone use, local
transcription, model downloads, clipboard behavior, and diagnostics in plain
language. Debug output reports activity and errors without printing dictated text.

## Sounds

Three cues, on by default and switchable off under Sounds in Settings: a rising
pair of notes when the microphone opens, the same two notes falling when it
closes and transcription begins, and the same pair at one pitch — neither rising
nor falling — when a tap locks recording on. They are the same idea heard three
ways, so they read as "open", "close" and "staying open" rather than as
unrelated beeps.

No cue can be louder than the others. Each is normalised to a shared peak and
then held to a shared RMS, because peak alone does not settle how loud something
sounds: the lock cue's two same-pitch notes overlap constructively where a rising
pair does not, and matching only their peaks left it audibly the loudest of the
three.

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
and cache in `%APPDATA%\dictate\models`, separate from other apps.

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
