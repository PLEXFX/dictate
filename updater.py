"""Background updater: checks GitHub Releases for a newer Dictate build,
downloads the installer, and hands off to it once verified.

The repo is public, so the check is a single unauthenticated GET -- unlike
server-dashboard's private-repo OAuth flow, there is no token to manage.

Checking and downloading are deliberately two separate steps. ``check_now``
only ever tells the caller a newer release exists (AVAILABLE) -- it never
downloads anything on its own, on a timer or otherwise. Downloading only
starts from an explicit ``start_update()`` call, which main.py wires to a
person clicking the "download & install" notification, never to a
background timer. Once that download verifies, the installer is launched
immediately as part of the same click -- there is no separate "now click
again to actually install" step, because the click that started the
download already said what should happen once it finishes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from version import VERSION

REPO = "PLEXFX/dictate"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# This is an identity pin, not a secret. Set it to Dictate's production
# certificate thumbprint once a signed release exists, and every update from
# then on is also checked against this specific signer. Empty means Dictate
# has no code-signing certificate yet: updates still require the exact
# PLEXFX/dictate release URL and a matching SHA-256 checksum (both always
# enforced below, never skipped), just not a signature -- a deliberate,
# requested trade-off so updates work today rather than the stronger
# guarantee a signed release would give. Revisit once signing is set up.
TRUSTED_SIGNER_THUMBPRINT = ""

# Status states, reported through last_status for a UI that polls reactively
# (settings_window.py's refresh_status(), mirroring engine.py's same pattern).
IDLE = "idle"
CHECKING = "checking"
UP_TO_DATE = "up_to_date"
AVAILABLE = "available"    # a newer release exists; nothing downloaded yet
DOWNLOADING = "downloading"
INSTALLING = "installing"
ERROR = "error"

# Deliberately NOT GitHub's /releases/latest endpoint: that endpoint only
# ever returns the newest release that is *not* flagged prerelease, and
# every Dictate release published so far -- including this one -- is a beta
# with the prerelease flag set. Against this repo, /releases/latest 404s
# every time, which looked to _fetch_latest_release like "offline" and made
# every update check since the first beta silently report "up to date."
# /releases (the list endpoint) includes prereleases, so this fetches that
# list and picks the entry with the numerically highest version itself,
# rather than trusting the list's order.
_API_RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
_RELEASE_PREFIX = f"/{REPO}/releases/download/"
_USER_AGENT = "dictate-updater"
_REQUEST_TIMEOUT = 10
_DOWNLOAD_CHUNK = 1 << 16  # 64 KiB
_MAX_CHECKSUM_BYTES = 4096
_MAX_RELEASE_NOTES_CHARS = 5000
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z]+\.(\d+))?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def parse_version(v: str) -> tuple[int, int, int, int, int]:
    """(major, minor, patch, is_final, prerelease_number).

    A final release always outranks any beta of the same major.minor.patch,
    and higher beta numbers outrank lower ones -- plain string/tuple compare
    of "beta.10" vs "beta.2" would sort the second one higher, which is wrong.
    Anything unparseable sorts lowest, so a malformed tag can never look
    newer than what's actually running.
    """
    v = v.strip().lstrip("vV")
    m = _VERSION_RE.fullmatch(v)
    if not m:
        return (0, 0, 0, 0, 0)
    major, minor, patch, pre = m.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, 0)
    return (int(major), int(minor), int(patch), 0, int(pre))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _release_asset_url(asset: object) -> Optional[str]:
    """Accept only this repository's canonical GitHub release URL.

    GitHub can redirect this initial URL to its release-asset CDN.  The
    installer still has to pass the independent SHA-256 and signer checks.
    """
    if not isinstance(asset, dict):
        return None
    url = asset.get("browser_download_url")
    if not isinstance(url, str):
        return None
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "github.com"
        or not parsed.path.startswith(_RELEASE_PREFIX)
    ):
        return None
    return url


def _pick_latest(releases: object) -> Optional[dict]:
    """Highest-parsed-version, non-draft entry in a /releases list response.

    Picked by version rather than by list position or ``created_at`` so a
    release published out of order (a hotfix, a re-run) can never be
    shadowed by something merely newer on the clock.
    """
    if not isinstance(releases, list):
        return None
    best: Optional[dict] = None
    best_key = (0, 0, 0, 0, 0)
    for entry in releases:
        if not isinstance(entry, dict) or entry.get("draft"):
            continue
        candidate = str(entry.get("tag_name", "")).strip().lstrip("vV")
        key = parse_version(candidate)
        if key > best_key:
            best_key = key
            best = entry
    return best


def _fetch_latest_release() -> Optional[dict]:
    """The latest GitHub release, or None on any failure -- offline, rate
    limited, malformed response, whatever. An update check must never be
    the thing that breaks a launch.
    """
    req = urllib.request.Request(
        _API_RELEASES,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    data = _pick_latest(releases)
    if data is None:
        return None

    version = str(data.get("tag_name", "")).strip().lstrip("vV")
    if not _VERSION_RE.fullmatch(version):
        return None
    installer_name = f"Dictate-Setup-{version}.exe"
    checksum_name = f"{installer_name}.sha256"
    assets = data.get("assets") or []
    by_name = {
        str(asset.get("name", "")): asset for asset in assets if isinstance(asset, dict)
    }
    installer = by_name.get(installer_name)
    checksum = by_name.get(checksum_name)
    installer_url = _release_asset_url(installer)
    checksum_url = _release_asset_url(checksum)
    if installer_url is None or checksum_url is None:
        return None
    size = installer.get("size") if isinstance(installer, dict) else None
    if not isinstance(size, int) or size <= 0:
        return None
    return {
        "version": version,
        "installer_url": installer_url,
        "installer_name": installer_name,
        "installer_size": size,
        "checksum_url": checksum_url,
        "release_notes": str(data.get("body") or "").strip()[:_MAX_RELEASE_NOTES_CHARS],
    }


def _download(
    url: str, dest: Path, on_progress: Optional[Callable[[int, Optional[int]], None]]
) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        total = resp.length  # None if the server didn't send Content-Length
        downloaded = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(downloaded, total)


def _fetch_expected_sha256(url: str, installer_name: str) -> Optional[str]:
    """Read a conventional ``<hex> [*]filename`` checksum sidecar safely."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as response:
            raw = response.read(_MAX_CHECKSUM_BYTES + 1)
    except Exception:
        return None
    if len(raw) > _MAX_CHECKSUM_BYTES:
        return None
    try:
        fields = raw.decode("utf-8").strip().split()
    except UnicodeDecodeError:
        return None
    if not fields or not _SHA256_RE.fullmatch(fields[0]):
        return None
    if len(fields) > 1 and fields[1].lstrip("*") != installer_name:
        return None
    return fields[0].casefold()


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _verify_authenticode(path: Path, trusted_thumbprint: str) -> bool:
    """Use Windows trust validation *and* pin Dictate's signer identity."""
    expected = re.sub(r"\s+", "", trusted_thumbprint).upper()
    if not re.fullmatch(r"[0-9A-F]{40}", expected):
        return False
    script = (
        "$sig = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "$thumb = if ($sig.SignerCertificate) { "
        "$sig.SignerCertificate.Thumbprint.Replace(' ', '').ToUpperInvariant() } "
        "else { '' }; "
        "if ($sig.Status -eq 'Valid' -and $thumb -eq $args[1]) { 'trusted' }"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
                str(path),
                expected,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "trusted"


_DOWNLOAD_TEMP_PREFIX = "dictate-update-"


def cleanup_stale_downloads() -> None:
    """Remove temp folders a past update download left behind.

    ``_download_and_verify`` only deletes its own ``dictate-update-*`` temp
    dir when verification fails -- a *successful* update hands the ~1 GB
    installer off to a detached process and quits immediately after, and
    never comes back to clean up its own download. Left alone, every
    applied update leaves another full installer sitting in %TEMP% forever.

    Safe to sweep unconditionally at startup: whatever process created one
    of these folders has already finished with it (or crashed) by the time
    a *new* instance is starting, since a fresh app owns its own updater
    and always downloads fresh rather than resuming a prior temp file.
    """
    base = Path(tempfile.gettempdir())
    try:
        candidates = list(base.glob(f"{_DOWNLOAD_TEMP_PREFIX}*"))
    except OSError:
        return
    for folder in candidates:
        shutil.rmtree(folder, ignore_errors=True)


def _update_notice_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "dictate" / "update-notice.json"


def _write_update_notice(version: str, notes: str) -> None:
    """Persist public release notes across the updater-triggered restart."""
    path = _update_notice_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": version, "notes": notes[:_MAX_RELEASE_NOTES_CHARS]}),
            encoding="utf-8",
        )
    except OSError:
        # The notification is optional; a verified update must still work.
        pass


def consume_update_notice(version: str) -> Optional[str]:
    """Return and clear the update notice only for the version now running."""
    path = _update_notice_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != version:
        return None
    notes = data.get("notes")
    return notes if isinstance(notes, str) else ""


class Updater:
    """Owns the background check cycle, and the download+install pipeline
    once a person explicitly asks for it via start_update().

    UI-agnostic on purpose: main.py wires on_available to the bar's
    click-to-download notification and on_installing to actually quitting
    the app, never to anything that downloads or runs the installer without
    that click.
    """

    def __init__(
        self,
        on_available: Optional[Callable[[str, str], None]] = None,
        on_installing: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_up_to_date: Optional[Callable[[], None]] = None,
        on_status_change: Optional[Callable[[], None]] = None,
        current_version: str = VERSION,
        check_interval: float = CHECK_INTERVAL_SECONDS,
        trusted_signer_thumbprint: str = TRUSTED_SIGNER_THUMBPRINT,
        enabled: bool = True,
    ):
        self._on_available = on_available or (lambda version, notes: None)
        self._on_installing = on_installing or (lambda version: None)
        self._on_error = on_error or (lambda message: None)
        self._on_up_to_date = on_up_to_date or (lambda: None)
        self._on_status_change = on_status_change or (lambda: None)
        self._current_version = current_version
        self._check_interval = check_interval
        self._trusted_signer_thumbprint = trusted_signer_thumbprint
        self._enabled = enabled
        self._status_state = IDLE
        self._status_detail = ""
        self._status_progress: Optional[float] = None
        self._revert_timer: Optional[threading.Timer] = None
        self._stop = threading.Event()
        self._busy = threading.Lock()
        # Set once check_now() finds something newer, cleared the instant
        # start_update() picks it up -- both act as the "is a download
        # already spoken for" guard alongside _busy, and as the only record
        # of which release AVAILABLE is currently naming.
        self._available: Optional[dict] = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def last_status(self) -> tuple[str, str, Optional[float]]:
        """The most recent (state, detail, progress) triple, for a UI that
        polls reactively rather than wiring its own signal to every status
        change. Mirrors engine.Engine.last_status's exact shape -- progress
        is a 0..1 fraction while DOWNLOADING, None otherwise -- so
        settings_window.py can drive one progress-bar widget from either
        source without a separate case for each.
        """
        return (self._status_state, self._status_detail, self._status_progress)

    def _set_status(
        self,
        state: str,
        detail: str = "",
        *,
        progress: Optional[float] = None,
        revert_after: Optional[float] = None,
    ) -> None:
        """Update status and notify. ``revert_after`` schedules a return to
        IDLE that many seconds later -- used for one-shot confirmations
        (UP_TO_DATE, ERROR) so a UI polling reactively (settings_window.py's
        refresh_status()) gets a real window to observe and display the
        state before it clears itself, without either side needing to track
        "have I already shown this" -- reverting the source state is simpler
        and race-free against Qt's async signal delivery than trying to
        debounce on the display side. AVAILABLE/DOWNLOADING/INSTALLING never
        pass one: each is superseded by the next real state in the pipeline
        rather than reverting to IDLE on its own.
        """
        if self._revert_timer is not None:
            self._revert_timer.cancel()
            self._revert_timer = None
        self._status_state = state
        self._status_detail = detail
        self._status_progress = progress
        self._on_status_change()
        if revert_after is not None:
            self._revert_timer = threading.Timer(revert_after, self._revert_to_idle)
            self._revert_timer.daemon = True
            self._revert_timer.start()

    def _revert_to_idle(self) -> None:
        self._status_state = IDLE
        self._status_detail = ""
        self._status_progress = None
        self._on_status_change()

    def _loop(self) -> None:
        self.check_now()
        while not self._stop.wait(self._check_interval):
            self.check_now()

    def set_enabled(self, enabled: bool) -> None:
        """Turn the background cadence and manual checks on or off live.

        Settings-driven: main.py calls this from _apply_settings whenever
        auto_update_enabled changes, so a toggle flips behavior immediately
        rather than waiting for the next check_interval or app restart.
        Re-enabling kicks an immediate check, mirroring what a fresh
        Updater does on construction.
        """
        was_enabled = self._enabled
        self._enabled = enabled
        if enabled and not was_enabled:
            self.check_now()

    def check_now(self, *, silent: bool = True) -> None:
        """Kick off a check in the background. Never downloads anything.

        A no-op while checks are turned off in Settings, while a check is
        already running, while a download/install is already running (both
        hold the same _busy lock a check needs), or once a release is
        already AVAILABLE and waiting on start_update() -- re-checking then
        would just refetch the same answer and risk clobbering the pending
        state. ``silent`` suppresses the "you're already up to date"
        notification for the automatic background cadence -- only a
        manually triggered check should ever say that out loud.
        """
        if not self._enabled:
            return
        if self._available is not None or not self._busy.acquire(blocking=False):
            return
        threading.Thread(target=self._check, args=(silent,), daemon=True).start()

    def _check(self, silent: bool) -> None:
        # CHECKING/UP_TO_DATE stay behind `not silent` -- the automatic 24h
        # cadence finding nothing new shouldn't flash "Checking..." at a user
        # who never asked to see it. AVAILABLE always reports regardless:
        # it is the one thing this module is never allowed to act on by
        # itself, so the person has to be told about it to ever see it.
        try:
            if not silent:
                self._set_status(CHECKING, "Checking for updates…")
            info = _fetch_latest_release()
            if info is None or not is_newer(info["version"], self._current_version):
                if not silent:
                    self._set_status(
                        UP_TO_DATE, "You're on the latest version", revert_after=3.0
                    )
                    self._on_up_to_date()
                return
            self._available = info
            print(f"[dictate] update {info['version']} available")
            self._set_status(AVAILABLE, f"Update {info['version']} is available")
            self._on_available(info["version"], info["release_notes"])
        finally:
            self._busy.release()

    def start_update(self) -> bool:
        """Download, verify, and install the currently available release.

        Only main.py's click handler for the AVAILABLE notification (bar
        toast or the Settings button) should ever call this -- never a
        timer, never automatically after check_now() finds something.
        Returns False as a harmless no-op if there is nothing available or
        a download is already running, so a duplicate click (the bar toast
        and the Settings button both wired to the same Updater) can't start
        two overlapping downloads.
        """
        info = self._available
        if info is None or not self._busy.acquire(blocking=False):
            return False
        self._available = None
        threading.Thread(
            target=self._download_and_install, args=(info,), daemon=True
        ).start()
        return True

    def _fail(self, message: str) -> None:
        print(f"[dictate] update rejected: {message}")
        # Always visible, unlike a background check's silent failures --
        # start_update() only ever runs from an explicit click, so there is
        # always someone waiting to hear whether it worked.
        self._set_status(ERROR, message, revert_after=8.0)
        self._on_error(message)

    def _download_and_install(self, info: dict) -> None:
        try:
            expected_hash = _fetch_expected_sha256(
                info["checksum_url"], info["installer_name"]
            )
            if expected_hash is None:
                self._fail("Update integrity information is missing")
                return

            tmp_dir = Path(tempfile.mkdtemp(prefix=_DOWNLOAD_TEMP_PREFIX))
            dest = tmp_dir / info["installer_name"]

            last_frac = 0.0
            last_emit = time.monotonic()

            def on_progress(n: int, total: Optional[int]) -> None:
                nonlocal last_frac, last_emit
                if not total:
                    return
                frac = min(1.0, n / total)
                now = time.monotonic()
                if frac < 1.0 and frac - last_frac < 0.01 and now - last_emit < 0.1:
                    return
                last_frac, last_emit = frac, now
                pct = int(frac * 100)
                self._set_status(
                    DOWNLOADING,
                    f"Downloading update {info['version']} — {pct}%",
                    progress=frac,
                )

            try:
                _download(info["installer_url"], dest, on_progress)
                if dest.stat().st_size != info["installer_size"]:
                    raise ValueError("download size did not match the release")
                if _sha256(dest) != expected_hash:
                    raise ValueError("download hash did not match the release")
                # No cert configured yet -- see TRUSTED_SIGNER_THUMBPRINT's
                # comment. Once one is, every update is also checked against
                # that signer.
                if self._trusted_signer_thumbprint and not _verify_authenticode(
                    dest, self._trusted_signer_thumbprint
                ):
                    raise ValueError("installer signer did not match Dictate")
            except Exception as exc:
                dest.unlink(missing_ok=True)
                try:
                    tmp_dir.rmdir()
                except OSError:
                    pass
                self._fail("The update could not be verified")
                print(f"[dictate] update verification detail: {exc}")
                return

            _write_update_notice(info["version"], info["release_notes"])
            try:
                subprocess.Popen([str(dest), "/SP-", "/VERYSILENT", "/NORESTART"])
            except OSError as exc:
                self._fail("The update downloaded but could not be started")
                print(f"[dictate] could not start verified installer: {exc}")
                return

            print(f"[dictate] update {info['version']} installing")
            # Does not auto-revert: main.py's on_installing quits the app
            # right after this, so there is no later moment to revert from.
            self._set_status(INSTALLING, "Restarting Dictate to install the update…")
            self._on_installing(info["version"])
        finally:
            self._busy.release()

    def shutdown(self) -> None:
        self._stop.set()
        if self._revert_timer is not None:
            self._revert_timer.cancel()
