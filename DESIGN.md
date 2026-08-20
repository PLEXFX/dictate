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
