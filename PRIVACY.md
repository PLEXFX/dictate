# Privacy

Dictate is designed for local speech-to-text.

- Audio is captured only while the configured hold-to-talk key is held.
- Audio and dictated text are processed on the device and are not sent to a
  transcription service by Dictate.
- Dictate does not keep a transcript or save voice recordings.
- To insert a result, Dictate temporarily places it on the Windows clipboard,
  sends the paste shortcut, then restores the prior text clipboard contents.
- Speech-model files may download from Hugging Face into Dictate's own
  `%APPDATA%\dictate\models` folder, and optional NVIDIA GPU runtime files may
  download from PyPI when those features are selected. Those
  providers receive the normal network information required to deliver files;
  they do not receive dictated audio or text from Dictate.
- Update checks contact only the official `PLEXFX/dictate` GitHub repository.

The app's Privacy page repeats this information near the microphone and
settings controls. If this policy changes, the app and this file must be
updated together before release.
