# Contributing to Dictate

Thanks for helping improve Dictate while it is in public beta.

## Before opening an issue

- Search [open issues](https://github.com/PLEXFX/dictate/issues) first.
- Use the latest beta installer when reporting a user-facing problem.
- Do not include dictated text, recordings, access tokens, or other private material.
- For a security concern, follow [SECURITY.md](SECURITY.md) instead of creating a public issue.

## Good bug reports

A useful report says what you expected, what happened instead, and the shortest repeatable steps. Include the Dictate version, Windows version, whether you use CPU or GPU processing, and any relevant error shown by `run-dictate-debug.bat`—with private text removed.

## Proposing an improvement

Describe the problem before prescribing a solution. Dictate aims to be a quiet, native-feeling Windows 11 utility, so proposals should make dictation clearer, faster, safer, or less distracting. See [DESIGN.md](DESIGN.md) for the product direction.

## Development setup

Dictate targets Windows 11 and Python 3.12. Install [uv](https://docs.astral.sh/uv/), then run:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r pyproject.toml
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Use `run-dictate-debug.bat` for visible diagnostics while developing. Keep changes focused, include tests when behavior changes, and do not commit build output, virtual environments, or local handoff files.

## Pull requests

Open an issue first for significant changes so the direction is clear. In a pull request, explain the user-facing result, mention test coverage, and include a screenshot or short recording for visible UI changes. Do not change release versions or release assets as part of a normal contribution.
