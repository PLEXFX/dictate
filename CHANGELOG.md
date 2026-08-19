# Changelog

All notable changes to Dictate are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0-beta.1] - 2026-08-19

### Added

- Every version change now opens its bundled release notes once after launch,
  including upgrades installed manually instead of through Dictate's updater.
- Settings now has a permanent **What's new** footer button above GitHub. It
  opens the current version's notes in a theme-matched modal with a clear
  **Close** action.

### Changed

- Aligned the version label with the What's new and GitHub footer text in
  Settings.

### Fixed

- The in-app updater now runs its progress window from a temporary copy, so
  Windows no longer blocks Setup from replacing `dictate-updater.exe` with an
  access-denied prompt during installation.

## [1.1.0-beta.1] - 2026-08-19

### Changed

- Removed the live transcript preview from the activity bar and Settings. It
  will return only when it can be smooth, accurate, and representative of the
  words being spoken.
- Refined Settings and the post-update page around a minimal Windows 11
  layout. Privacy now opens and returns with a short in-place transition.
- Reorganized Settings into focused Dictation, Activity bar, Appearance,
  Updates, and Privacy pages behind a persistent Windows-style navigation
  rail. GitHub and version details now live in its minimal footer.
- Moved download and update status into one adaptive rail control above
  GitHub. Update downloads now stop at a persisted, verified “Restart to
  finish” state, and the installer is revalidated before Dictate launches it.

### Removed

- Removed Settings reset-to-default actions. Changes now remain in place until
  you choose a different value yourself.

### Added

- `prepare-release-notes.bat` now copies the exact version-specific release
  body from this changelog for GitHub. The in-app post-update page receives
  that matching release body automatically after an update.
- Added a live activity-bar width control from 180–280 px. The new 200 px
  default is slightly shorter, and its interruption-safe spring follows rapid
  slider changes without restarting, drifting, or cropping the waveform.

## [1.0.3-beta.1] - 2026-08-18

### Added

- A fresh install now opens Settings immediately and starts the default
  **Small English** CPU model download in the background. Settings shows the
  real progress, and holding the talk key while it downloads clearly says
  that recording continues while the speech model finishes downloading.
- Setup can download the optional NVIDIA GPU acceleration files during a
  fresh install, with a real percentage progress bar in the installer. If
  you skip it, Settings provides the explicit **Download now** path later.
- Settings has a compact **Downloads & installs** control at the top. It
  stays out of the way when idle, then smoothly expands to show model, GPU,
  or update progress and takes you straight to the relevant setting.

### Changed

- Settings and the dictation bar now use an opaque Windows 11-style surface
  with a System, Light, or Dark color choice. The old transparency option has
  been removed.
- GPU acceleration is only available when Dictate's optional runtime files
  are actually installed. Until then, Dictate safely stays on CPU and
  GPU-only choices are unavailable instead of implying that acceleration is
  ready.
- GPU runtime downloads inside Dictate now fetch their three packages in
  parallel while keeping one smooth combined progress indicator.
- Model names throughout the app are human-readable: for example, **Small
  English** instead of `small.en`.
- Uninstalling now asks whether to keep downloaded models, GPU files, and
  Settings data, so a later reinstall does not needlessly download them again.

### Fixed

- The updater splash now remains visible until the updated Dictate window has
  rendered, removing the blank waiting period during an in-app update.
- Repeated first-run/model warm-up requests share one worker instead of
  starting duplicate downloads.
- The installer's GPU-progress handoff and uninstall cleanup recover from
  brief Windows file locks instead of silently failing or leaving data behind.

## [1.0.2-beta.1] - 2026-08-18

### Added

- GPU acceleration files now start downloading automatically as soon as
  Dictate launches (if GPU was chosen at install) or the moment you pick GPU
  in Settings — no need to start a dictation first. A new "GPU acceleration"
  row in Settings → Advanced shows live progress and a "Download now" button
  for anyone who skipped it earlier. Dictation stays fully usable on CPU the
  whole time it downloads.
- The Settings window now uses Windows' own Acrylic transparency effect when
  "Transparency effects" is turned on in Windows, and falls back to a solid
  light/dark background when it's off.

### Fixed

- The tray's "Check for updates" stayed clickable and silently did nothing
  when "Check for updates automatically" was turned off in Settings. It now
  matches Settings' own button and disables itself instead.

## [1.0.1-beta.1] - 2026-08-18

### Fixed

- Changing a setting (like the speech model or GPU/CPU) while a model or GPU
  download was already in progress could freeze the app until that download
  finished. Settings now apply instantly even mid-download.

## [1.0.0-beta.1] - 2026-08-17

### Added

- A small progress window now bridges the gap after clicking "Restart now" —
  Dictate used to close and leave the screen empty for up to a minute while
  the installer ran silently. It shows install progress, relaunches Dictate
  automatically if the install fails, and closes on its own once done.
- The floating bar now shows a compact **Downloading model…** label with a
  determinate progress fill when a selected speech model needs to download.
  Swapping to an already-cached model keeps the quieter loading sweep.

### Changed

- Update verification now checks the SHA-256 GitHub itself computes for the
  installer asset instead of a separate `.exe.sha256` file, so releases only
  have one file to download. Installs on 0.2.4-beta.1 and earlier won't see
  this release as an available update — their built-in updater still expects
  the old sidecar file, which no longer gets published; a manual reinstall
  is needed to move onto this update path.
- Installer size grew from 93MB to about 130MB to include the new update
  progress window's own bundled runtime.

## [0.2.4-beta.1] - 2026-08-17

### Added

- Speech models now download into Dictate's own app-data folder, separate
  from other apps' caches. Settings includes an **Open model folder** button
  so you can inspect or clear only Dictate's downloaded models.
- The existing Enhanced Preview and update-failure notices now also appear on
  Dictate's floating bar. When **Also show Windows notifications** is on, the
  same short message is mirrored to Windows.

### Changed

- Rewrote Dictate's existing notifications as short, readable messages that
  fit comfortably in the floating toast: startup, already-open, updates,
  copied dictation, undo refusals, and Enhanced Preview feedback.

## [0.2.3-beta.1] - 2026-08-17

### Changed

- Updates no longer download in the background the moment a check finds one.
  Dictate now shows "Update available," and downloads, verifies, and
  installs only after you click it — one click does the whole thing, with no
  separate "now click again to install" step afterward.
- The installer is about 90% smaller (93 MB vs ~1.07 GB): GPU acceleration
  files are no longer bundled into every install regardless of whether the
  machine has a card. Setup detects an NVIDIA card and offers to enable GPU
  acceleration; the files still download on demand the first time Dictate
  actually needs them, exactly as they already did when switching to GPU
  from Settings.
- Privacy is now a page you navigate to inside Settings instead of a
  separate window.

### Added

- Real download progress bars in Settings for updates, GPU acceleration,
  and speech-model downloads, instead of only a text percentage.
- "Also show Windows notifications" in Settings. Dictate's own floating-bar
  notification is always on; turn this on to also mirror it as a normal
  Windows notification.
- Dictate now tells you when it's open and ready, and again if you try to
  open a second copy while it's already running.
- A GitHub link next to Privacy in Settings.
- Uninstalling now also removes your settings, so a later reinstall starts
  completely fresh — including the first-run welcome screen.

### Fixed

- "Check for updates" could sometimes silently do nothing when clicked — a
  timing gap meant the click occasionally landed in the moment before the
  button had a chance to disable itself. It now disables the instant you
  click it.
- Longer notification text no longer gets cut off mid-sentence — it wraps
  onto a second line instead.

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
