# Changelog

All notable changes to Dictate are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.2-beta.1] - 2026-08-17

### Fixed

- An applied update no longer leaves its ~1 GB download sitting in your temp
  folder forever — old ones are now cleaned up automatically the next time
  Dictate starts.

## [0.2.1-beta.1] - 2026-08-17

### Fixed

- Update checking actually works now. It never has before: GitHub's
  "latest release" endpoint excludes prereleases, and every Dictate release
  so far has been a beta, so that check has 404'd silently since the first
  one and always reported "you're on the latest version" no matter what was
  actually out. It also required a checksum sidecar file that no release had
  ever included, which would have kept blocking it even once the first
  problem was fixed. Update checks now look at the full release list and a
  checksum sidecar is published with every release going forward.

## [0.2.0-beta.2] - 2026-08-17

### Added

- Tap the talk key instead of holding it and recording stays on until you tap
  it again, so a long passage no longer has to be dictated with a finger held
  down. Holding still works exactly as before — the two are told apart by how
  long the key was down, so there is no second binding to learn. Esc discards a
  locked recording, and one stops itself after five minutes rather than holding
  the microphone open indefinitely. A "Tap to keep recording" toggle in Settings
  turns the gesture off for anyone who wants the key to stay hold-only.
- A third sound cue marking the moment recording locks on: the same two notes as
  the other cues, at one pitch, because locking is the one state where nothing
  is changing. Every cue is now held to a shared loudness rather than only a
  shared peak, so the new one cannot come out louder than the two already there.
- "Undo last dictation" in the tray menu, which takes back the text Dictate just
  pasted. It refuses whenever it cannot be confident that text is still the last
  change in that window — if you have typed or clicked there since, if the window
  has closed, after 30 seconds, or if the window will not come forward — because
  Dictate cannot read another application's undo history and a wrong undo would
  destroy work it never wrote.
- A live transcript preview above the waveform while you talk: an empty
  connected lip opens into a compact one-line card, then grows to a two-line
  card as a second row appears, without moving the waveform or the newest
  line. The older line fades and rises out of view as a new one arrives, and
  the still-unsettled tail of what you just said stays dimmed until a second
  pass confirms it. An optional "Enhanced preview (Alpha)" mode runs a second,
  independent CPU model so this rolling preview never has to wait behind the
  final transcription — it downloads its own small model on first use, warns
  if a machine looks too slow for it, and can be turned off without affecting
  final transcription at all. The whole preview can be switched off from
  Settings if you'd rather the bar stay a plain waveform.

### Changed

- Advanced settings is now a real Windows 11 expander: a full-width card
  matching the settings groups above it, with the label on the left and a
  chevron on the right that rotates through 180 degrees as the section opens.
  The rotation, the panel's height and its fade all run on one duration and one
  curve, since they are one gesture.
- The bar's capsules now glow a touch brighter and more saturated the louder
  you speak, instead of holding one flat accent colour regardless of volume.
- Words in the live preview settle into place with a brief rise and a cool
  fade from the accent colour back to normal text, rather than snapping
  straight to full opacity the instant a second pass confirms them.
- An always-visible idle bar (the "always show" setting) now breathes very
  slightly at its centre instead of sitting perfectly flat, so it reads as
  alive rather than stuck.

## [0.1.2-beta.3] - 2026-08-17

### Security

- Dictate has no code-signing certificate yet, so update checks no longer
  refuse to run because of that. Updates still require the exact
  `PLEXFX/dictate` release URL and a matching SHA-256 checksum; there is
  just no Authenticode signature check layered on top until a signed
  release exists. See `SECURITY.md` for the current update trust model.

## [0.1.2-beta.2] - 2026-08-17

### Security

- Updates now accept only the exact release assets from `PLEXFX/dictate`,
  require a matching SHA-256 sidecar, and verify Dictate's pinned Windows
  code-signing certificate before offering an installer.
- Added a public privacy statement and security-reporting policy.

### Changed

- Settings now gives Dictate Update its own Windows-style section with check,
  download, restart, and post-update What's New states.
- `run-dictate.bat` no longer leaves a console window open behind the app —
  it hands off to `pythonw.exe` and closes immediately, matching the
  installed app's behavior. Errors still show a readable window with a
  pause. Use `run-dictate-debug.bat` when you want to see `[dictate]` status
  lines and errors live in a console.
- The debug console (`run-dictate-debug.bat`) sets its window title to
  "Dictate — debug console" and prints aligned columns for `help` and
  `status` instead of a single run-on line.

### Added

- A "Check for updates automatically" toggle in Settings, plus a matching
  installer checkbox on first install. Turning it off stops the 24h
  background check and the manual "Check for updates" button immediately —
  Dictate never contacts GitHub while it's off.
- A simple "Words I use" editor for local names, brands, and technical terms.
  Dictate passes the list to Whisper as recognition context without sending it
  anywhere or treating it as text replacement rules.
- A "Copy last dictation" tray action. It keeps only the most recent result in
  memory and can put it back on the clipboard for 5 seconds without losing the
  user's earlier clipboard text.
- New debug-console commands: `gpu`, `version`, `vocab`, `test sound`,
  `settings`, `check update`, `open data`, and `quit`, all listed in `help`.

### Privacy

- The temporary recovery copy restores the earlier clipboard only if the user
  has not copied something new in the meantime.
- Update checks can now be turned off entirely from Settings or the
  installer, instead of only being able to change how often they happen.

## [0.1.0-beta.2] - 2026-08-17

### Added

- A Windows installer (`Dictate-Setup-x.y.z.exe`) that installs Dictate
  without needing Python or any manual setup, with an optional GPU
  acceleration component for supported NVIDIA graphics cards.
- A background updater that checks for new releases on launch and every
  24 hours, downloads and verifies them, and asks before installing —
  Dictate never restarts or updates itself without a click.
- Switching to GPU in Settings after a Core-only install now downloads the
  acceleration files automatically instead of silently staying on CPU, with
  live progress on both the activity bar and the Settings status line.
- A "Check for updates" button in Settings, alongside the existing tray
  menu action.

### Fixed

- A microphone that captures no audio at all (muted, disconnected, wrong
  device selected) now shows a clear error instead of silently doing
  nothing.
- The first-time download of a speech model now shows real download
  progress on the activity bar instead of an indefinite spinner.

## [0.1.0-beta.1] - 2026-08-16

### Added

- Local Windows push-to-talk dictation that records from the selected
  microphone and pastes the transcription into the focused app.
- Local faster-whisper transcription with CPU, NVIDIA GPU, and model-size
  controls, plus automatic model sleep when idle.
- A Windows 11-style tray app, activity bar, Settings window, onboarding,
  privacy explanation, and optional startup at sign-in.
- Click-to-record keyboard and mouse bindings, including held combinations for
  dictation and the Settings shortcut.
- Background model warm-up while the user is speaking after the model has
  slept, so most cold-start time is hidden before they release the key.
- Optional paired audio cues for starting and stopping dictation.

### Security

- Audio is transcribed on the PC. Dictate does not upload recordings or
  dictated text to a transcription service.
