# Changelog

All notable changes to Dictate are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
