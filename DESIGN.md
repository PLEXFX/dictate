# Dictate Design System

Dictate is a minimal Windows 11 utility. The interface should feel at home beside Windows Settings: calm, clear, opaque, and deliberate. Its job is to remove friction from dictation, never add personality at the cost of recognition or speed.

## Operating principles

- Use Windows 11's visual vocabulary: Segoe UI Variable where available, system accent for primary actions, rounded 4–8 px controls, hairline separators, and restrained light/dark surfaces.
- Organize settings by the decision a person is making. Keep a persistent left navigation rail and make Dictation, Activity bar, Appearance, Updates, and Privacy distinct content pages; place implementation detail behind one clearly named Advanced section.
- Start each Settings destination with a compact page title and one sentence, not a second app-name hero. Use bundled Fluent icons beside the rail's text labels so recognition never depends on an icon alone.
- Use one responsive grouped-row pattern with consistent 16 px horizontal and 12 px vertical padding. Keep controls right-aligned while space allows, then move them below the copy before text can clip. Do not turn every setting into an isolated card or add decorative dashboards, metrics, or icon grids.
- Keep deeper tools such as Words I use inside the Settings window with a clear Back action and automatic saving. Reserve modal windows for short blocking decisions or version-specific release notes.
- Keep copy short, concrete, and honest. Controls say what they do; descriptions explain a consequence only when one matters.
- Changes save automatically without adding an Apply workflow or a transient message beneath the version. Keep progress and recoverable errors beside the control that owns them so the rail footer never changes height for incidental text.
- The activity bar is waveform-first feedback. Do not show partial transcript text until a future version can make it reliable, smooth, and representative of what is actually being said.
- Keep activity-bar geometry adjustable without changing its visual language. Width changes preserve the waveform, tighten spacing instead of cropping bars, and animate from the current size rather than snapping.
- Keep download and update state in one adaptive control at the bottom of the Settings rail, directly above GitHub. Hide it when there is nothing to act on; the version footer already covers passive status. Show it for active work, available setup, errors, downloads, or restart, with a thin progress rail only while work is moving. A verified restart action turns orange in both the rail and Updates page so it cannot be mistaken for another passive status.
- Lead the Updates page with one status symbol, one message, version and last-check information, and one primary action. Show progress only while work is active; keep release notes, cadence, and technical details in a quieter group below.
- Never imply an update is ready to restart until its installer has been downloaded, checksum-verified, and persisted. A restart action must revalidate that staged file before launching it.

## Motion

- Motion explains state or navigation only. Routine transitions should run about 150–250 ms; exits are faster than entrances.
- Page changes use a small directional slide with a paired opacity change while one accent indicator glides between rail destinations. Do not use page-load choreography, bounce, or looping decoration.
- A continuously adjusted value must preserve motion through interruption. Activity-bar width follows one critically damped target instead of restarting a tween for every slider step.
- Adaptive rail status changes use a short opacity settle and a restrained height transition. Only work in progress may pulse; available, error, and restart-ready states stay still so urgency comes from meaning rather than decoration.
- Preserve a useful static result if motion is interrupted; UI content must never depend on an animation completing.

## Update notes

- The post-update page is version-specific and presents the GitHub release body for the version just installed.
- A version change opens What's New once over Settings. The same theme-matched modal remains available from a quiet footer link directly above GitHub and ends with one explicit Close action.
- `CHANGELOG.md` is the release-note source. Prepare the GitHub release body with `prepare-release-notes.bat`; do not hand-maintain a second list inside the app.

## Approved September 5 refinement

- Dictation groups are Recording, Transcription, and Stay ready. Memory controls live in Advanced. Keep all existing settings and update actions functional.
- The activity surface is a 36 px opaque capsule. Listening uses the selected width, a locked recording adds a lock, and success contracts to a 145 px Inserted receipt. Errors expand to a 260 px actionable status with details above.
- State feedback is immediate: no minimum dwell before the next state. The waveform morphs from its current shape over 190 ms including the small stagger. Content crossfades and width springs preserve interruptions.
- Idle is a quiet short line with no decorative loop. Windows Animation effects off gives a static working signal, direct width changes, and no reveal motion. Hidden bars reset their receipt and stop rendering.

## September 6 icon and update refinement

- Render the Fluent navigation family at native Windows display scales. Update symbols use the native Windows 11 Segoe Fluent Icons Sync glyph (U+E895), with the bundled Microsoft SVG as a font-unavailable fallback. Thomas rejected the custom-drawn returning arrows. Downloads, checks, and warnings retain matching rounded outlines.
- The 208 px Settings rail gives the update notice room for a readable title and action. Use 10 pt semibold for its title, 9 pt for secondary text, a 3 px gap, and actual wrapped-label heights to prevent clipping. Use a quiet amber surface for availability, a solid amber restart action, and measured wrapped copy for longer status messages.
- The tray icon is always the plain logo, at the same inset size as every other app's tray icon -- Thomas asked for the amber update dot removed after testing it, since it read as a sizing inconsistency against the plain icon rather than a helpful signal. A known update (available, downloading, or ready to restart) still shows in the tray tooltip and durably in Settings' Updates page and adaptive rail; availability does not raise a popup or activity-bar notice.
