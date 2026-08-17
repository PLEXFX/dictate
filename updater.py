"""Background updater: checks GitHub Releases for a newer Dictate build,
downloads the installer, and hands off to it once verified.

The repo is public, so the check is a single unauthenticated GET -- unlike
server-dashboard's private-repo OAuth flow, there is no token to manage.

Only the installer .exe itself can actually apply an update: a running,
compiled app can't safely overwrite its own open files. So this module's
job ends at "a verified installer is sitting in a temp folder, ready to
run" -- main.py decides when to actually launch it, and only ever does so
after the user clicks the ready notification, never silently in the
background.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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

# This is an identity pin, not a secret.  Set it to Dictate's production
# certificate thumbprint before the first signed release.  Empty deliberately
# fails closed so an unsigned beta can never be accepted as an auto-update.
TRUSTED_SIGNER_THUMBPRINT = ""

# Status states, reported through last_status for a UI that polls reactively
# (settings_window.py's refresh_status(), mirroring engine.py's same pattern).
IDLE = "idle"
CHECKING = "checking"
UP_TO_DATE = "up_to_date"
DOWNLOADING = "downloading"
READY = "ready"
INSTALLING = "installing"
ERROR = "error"
UNAVAILABLE = "unavailable"

_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
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


def _fetch_latest_release() -> Optional[dict]:
    """The latest GitHub release, or None on any failure -- offline, rate
    limited, malformed response, whatever. An update check must never be
    the thing that breaks a launch.
    """
    req = urllib.request.Request(
        _API_LATEST,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
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
    """Owns the background check/download cycle.

    UI-agnostic on purpose: main.py wires on_ready to the visible restart
    notification, never to anything that runs the installer without a click.
    """

    def __init__(
        self,
        on_ready: Optional[Callable[[str, Path], None]] = None,
        on_up_to_date: Optional[Callable[[], None]] = None,
        on_status_change: Optional[Callable[[], None]] = None,
        current_version: str = VERSION,
        check_interval: float = CHECK_INTERVAL_SECONDS,
        trusted_signer_thumbprint: str = TRUSTED_SIGNER_THUMBPRINT,
        enabled: bool = True,
    ):
        self._on_ready = on_ready or (lambda version, path: None)
        self._on_up_to_date = on_up_to_date or (lambda: None)
        self._on_status_change = on_status_change or (lambda: None)
        self._current_version = current_version
        self._check_interval = check_interval
        self._trusted_signer_thumbprint = trusted_signer_thumbprint
        self._enabled = enabled
        self._status_state = IDLE
        self._status_detail = ""
        self._revert_timer: Optional[threading.Timer] = None
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._staged: Optional[tuple[str, Path, str]] = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def last_status(self) -> tuple[str, str]:
        """The most recent (state, detail) pair, for a UI that polls
        reactively rather than wiring its own signal to every status change."""
        return (self._status_state, self._status_detail)

    @property
    def has_staged_update(self) -> bool:
        return self._staged is not None

    def _set_status(
        self, state: str, detail: str = "", *, revert_after: Optional[float] = None
    ) -> None:
        """Update status and notify. ``revert_after`` schedules a return to
        IDLE that many seconds later -- used for one-shot confirmations
        (UP_TO_DATE, READY) so a UI polling reactively (settings_window.py's
        refresh_status()) gets a real window to observe and display the
        state before it clears itself, without either side needing to track
        "have I already shown this" -- reverting the source state is simpler
        and race-free against Qt's async signal delivery than trying to
        debounce on the display side.
        """
        if self._revert_timer is not None:
            self._revert_timer.cancel()
            self._revert_timer = None
        self._status_state = state
        self._status_detail = detail
        self._on_status_change()
        if revert_after is not None:
            self._revert_timer = threading.Timer(revert_after, self._revert_to_idle)
            self._revert_timer.daemon = True
            self._revert_timer.start()

    def _revert_to_idle(self) -> None:
        self._status_state = IDLE
        self._status_detail = ""
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
        """Kick off a check in the background.

        A no-op while checks are turned off in Settings, while a check or
        download is already running, or once an update is already staged
        and waiting on the user to click it. ``silent`` suppresses the
        "you're already up to date" notification for the automatic
        background cadence -- only a manually triggered check should ever
        say that out loud.
        """
        if not self._enabled:
            return
        if self._staged is not None or not self._busy.acquire(blocking=False):
            return
        threading.Thread(
            target=self._check_and_download, args=(silent,), daemon=True
        ).start()

    def _check_and_download(self, silent: bool) -> None:
        # CHECKING/UP_TO_DATE stay behind `not silent` -- the automatic 24h
        # cadence finding nothing new shouldn't flash "Checking..." at a user
        # who never asked to see it. A download that's actually happening
        # (below) always reports, silent or not: it's real work in progress,
        # not routine background chatter.
        try:
            if not self._trusted_signer_thumbprint:
                print("[dictate] update check unavailable: this is not a signed release build")
                if not silent:
                    self._set_status(
                        UNAVAILABLE,
                        "Updates will be available in the signed Dictate release",
                        revert_after=8.0,
                    )
                return
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
            self._download_and_verify(info, silent)
        finally:
            self._busy.release()

    def _report_error(self, message: str, silent: bool) -> None:
        print(f"[dictate] update rejected: {message}")
        if not silent:
            self._set_status(ERROR, message, revert_after=8.0)

    def _download_and_verify(self, info: dict, silent: bool) -> None:
        expected_hash = _fetch_expected_sha256(
            info["checksum_url"], info["installer_name"]
        )
        if expected_hash is None:
            self._report_error("Update integrity information is missing", silent)
            return

        tmp_dir = Path(tempfile.mkdtemp(prefix="dictate-update-"))
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
            self._set_status(DOWNLOADING, f"Downloading update {info['version']} — {pct}%")

        try:
            _download(info["installer_url"], dest, on_progress)
            if dest.stat().st_size != info["installer_size"]:
                raise ValueError("download size did not match the release")
            if _sha256(dest) != expected_hash:
                raise ValueError("download hash did not match the release")
            if not _verify_authenticode(dest, self._trusted_signer_thumbprint):
                raise ValueError("installer signer did not match Dictate")
        except Exception as exc:
            dest.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass
            self._report_error("The update could not be verified", silent)
            print(f"[dictate] update verification detail: {exc}")
            return

        self._staged = (info["version"], dest, info["release_notes"])
        print(f"[dictate] update {info['version']} ready")
        # READY does not auto-revert: this remains actionable until the
        # person chooses Restart now or exits the app normally.
        self._set_status(
            READY,
            f"Update {info['version']} is ready to install",
        )
        self._on_ready(info["version"], dest)

    def apply_staged(self) -> bool:
        """Launch the staged installer silently and report whether it did.

        A True result means the caller should exit immediately after -- a
        running exe can't be overwritten by its own installer while it's
        still open. False if nothing is staged (e.g. a stale click after
        the app was already restarted some other way).
        """
        if self._staged is None:
            return False
        version, installer_path, notes = self._staged
        _write_update_notice(version, notes)
        try:
            subprocess.Popen(
                [str(installer_path), "/SP-", "/VERYSILENT", "/NORESTART"]
            )
        except OSError as exc:
            print(f"[dictate] could not start verified installer: {exc}")
            return False
        self._set_status(INSTALLING, "Restarting Dictate to install the update…")
        return True

    def shutdown(self) -> None:
        self._stop.set()
        if self._revert_timer is not None:
            self._revert_timer.cancel()
