"""Prepare the exact version-specific notes Dictate shows after an update.

CHANGELOG.md is the single source.  Before creating a GitHub release, move
the finished Unreleased entries under the matching version heading, then run
this file (or double-click prepare-release-notes.bat) and paste the copied
text into the release body.  The updater carries that body through to the
matching installed version automatically.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from version import VERSION

ROOT = Path(__file__).resolve().parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
INSTALLER = ROOT / "installer" / "dictate.iss"
VERSION_HEADING = re.compile(r"^## \[(?P<version>[^]]+)\](?:\s+-.*)?$", re.MULTILINE)
INSTALLER_VERSION = re.compile(r'#define MyAppVersion "(?P<version>[^"]+)"')


def notes_for_version(version: str, changelog: Path = CHANGELOG) -> str:
    """Return one released changelog section, without its version heading."""
    text = changelog.read_text(encoding="utf-8")
    headings = list(VERSION_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group("version") != version:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        notes = text[heading.end() : end].strip()
        if notes:
            return notes
        raise ValueError(f"{version} has no release notes in {changelog.name}.")
    raise ValueError(
        f"No released {version} section found in {changelog.name}. "
        "Move the finished Unreleased notes beneath its version heading first."
    )


def version_mismatches() -> list[str]:
    """Catch the three version strings that packaging still requires in sync."""
    pyproject_version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    installer_match = INSTALLER_VERSION.search(INSTALLER.read_text(encoding="utf-8"))
    installer_version = installer_match.group("version") if installer_match else "missing"
    sources = {"version.py": VERSION, "pyproject.toml": pyproject_version, "installer/dictate.iss": installer_version}
    return [f"{name} is {value!r}, expected {VERSION!r}" for name, value in sources.items() if value != VERSION]


def copy_to_clipboard(text: str) -> None:
    """Use Windows' built-in clipboard command; no dependency or account needed."""
    subprocess.run(["clip.exe"], input=text, text=True, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Dictate's GitHub release notes from CHANGELOG.md.")
    parser.add_argument("--copy", action="store_true", help="also copy the notes to the Windows clipboard")
    args = parser.parse_args()

    mismatches = version_mismatches()
    if mismatches:
        print("Version strings are not aligned:", file=sys.stderr)
        print("\n".join(f"- {line}" for line in mismatches), file=sys.stderr)
        return 1
    try:
        notes = notes_for_version(VERSION)
    except (OSError, ValueError) as exc:
        print(f"Release notes not ready: {exc}", file=sys.stderr)
        return 1
    if args.copy:
        try:
            copy_to_clipboard(notes)
        except OSError as exc:
            print(f"Could not copy release notes: {exc}", file=sys.stderr)
            return 1
    print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
