# Dictate Design System

Dictate is a minimal Windows 11 utility. The interface should feel at home beside Windows Settings: calm, clear, opaque, and deliberate. Its job is to remove friction from dictation, never add personality at the cost of recognition or speed.

## Operating principles

- Use Windows 11's visual vocabulary: Segoe UI Variable where available, system accent for primary actions, rounded 4–8 px controls, hairline separators, and restrained light/dark surfaces.
- Organize settings by the decision a person is making. Keep a persistent left navigation rail and make Dictation, Activity bar, Appearance, Updates, and Privacy distinct content pages; place implementation detail behind one clearly named Advanced section.
- Use grouped rows with separators for related settings. Do not turn every setting into an isolated card or add decorative dashboards, metrics, or icon grids.
- Keep copy short, concrete, and honest. Controls say what they do; descriptions explain a consequence only when one matters.
- Changes save automatically. Always show a quiet, local confirmation rather than adding an Apply workflow.
- The activity bar is waveform-first feedback. Do not show partial transcript text until a future version can make it reliable, smooth, and representative of what is actually being said.
- Keep activity-bar geometry adjustable without changing its visual language. Width changes preserve the waveform, tighten spacing instead of cropping bars, and animate from the current size rather than snapping.
- Keep download and update state in one adaptive control at the bottom of the Settings rail, directly above GitHub. Its quiet state is one compact line; active work may expand to two lines and one thin progress rail. The control changes its action with the real state: open details, start a download, or restart to finish a verified update.
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
