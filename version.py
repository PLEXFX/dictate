"""Single source of truth for Dictate's own version string at runtime.

Kept in sync by hand with pyproject.toml's [project] version and
installer/dictate.iss's MyAppVersion at each release -- there's no build
step here that reads one of those and drives the others automatically, so
all three need updating together when cutting a new version.
"""

VERSION = "1.0.2-beta.1"
