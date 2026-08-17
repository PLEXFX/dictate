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

import json
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from version import VERSION

REPO = "PLEXFX/dictate"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# Status states, reported through last_status for a UI that polls reactively
# (settings_window.py's refresh_status(), mirroring engine.py's same pattern).
IDLE = "idle"
CHECKING = "checking"
UP_TO_DATE = "up_to_date"
DOWNLOADING = "downloading"
READY = "ready"

_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_USER_AGENT = "dictate-updater"
_REQUEST_TIMEOUT = 10
_DOWNLOAD_CHUNK = 1 << 16  # 64 KiB


def parse_version(v: str) -> tuple[int, int, int, int, int]:
    """(major, minor, patch, is_final, prerelease_number).

    A final release always outranks any beta of the same major.minor.patch,
    and higher beta numbers outrank lower ones -- plain string/tuple compare
    of "beta.10" vs "beta.2" would sort the second one higher, which is wrong.
    Anything unparseable sorts lowest, so a malformed tag can never look
    newer than what's actually running.
    """
    v = v.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z]+\.(\d+))?", v)
    if not m:
        return (0, 0, 0, 0, 0)
    major, minor, patch, pre = m.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, 0)
    return (int(major), int(minor), int(patch), 0, int(pre))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


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
    assets = data.get("assets") or []
    installer = next(
        (a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None
    )
    if not version or installer is None:
        return None
    return {
        "version": version,
        "installer_url": installer.get("browser_download_url"),
        "installer_name": installer.get("name") or "Dictate-Setup.exe",
        "installer_size": installer.get("size"),
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


class Updater:
    """Owns the background check/download cycle.

    UI-agnostic on purpose: main.py wires on_ready to a tray notification,
    never to anything that runs the installer without the user clicking it.
    """

    def __init__(
        self,
        on_ready: Optional[Callable[[str, Path], None]] = None,
        on_up_to_date: Optional[Callable[[], None]] = None,
        on_status_change: Optional[Callable[[], None]] = None,
        current_version: str = VERSION,
        check_interval: float = CHECK_INTERVAL_SECONDS,
    ):
        self._on_ready = on_ready or (lambda version, path: None)
        self._on_up_to_date = on_up_to_date or (lambda: None)
        self._on_status_change = on_status_change or (lambda: None)
        self._current_version = current_version
        self._check_interval = check_interval
        self._status_state = IDLE
        self._status_detail = ""
        self._revert_timer: Optional[threading.Timer] = None
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._staged: Optional[tuple[str, Path]] = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def last_status(self) -> tuple[str, str]:
        """The most recent (state, detail) pair, for a UI that polls
        reactively rather than wiring its own signal to every status change."""
        return (self._status_state, self._status_detail)

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

    def check_now(self, *, silent: bool = True) -> None:
        """Kick off a check in the background.

        A no-op while a check or download is already running, or once an
        update is already staged and waiting on the user to click it.
        ``silent`` suppresses the "you're already up to date" notification
        for the automatic background cadence -- only a manually triggered
        check should ever say that out loud.
        """
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
            self._download_and_verify(info)
        finally:
            self._busy.release()

    def _download_and_verify(self, info: dict) -> None:
        tmp_dir = Path(tempfile.gettempdir()) / "dictate-update"
        tmp_dir.mkdir(exist_ok=True)
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
        except Exception as exc:
            print(f"[dictate] update download failed: {exc}")
            dest.unlink(missing_ok=True)
            self._set_status(IDLE, "")
            return

        expected = info.get("installer_size")
        if expected and dest.stat().st_size != expected:
            print("[dictate] update download incomplete, discarding")
            dest.unlink(missing_ok=True)
            self._set_status(IDLE, "")
            return

        self._staged = (info["version"], dest)
        print(f"[dictate] update {info['version']} ready")
        # Longer-lived than UP_TO_DATE: this is actionable, not just a
        # confirmation, and the tray notification (main.py's showMessage)
        # is the durable reminder if the user doesn't have Settings open --
        # this just gives anyone who does a real chance to see it too.
        self._set_status(
            READY,
            f"Update {info['version']} ready — click to restart and install",
            revert_after=15.0,
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
        _version, installer_path = self._staged
        subprocess.Popen([str(installer_path), "/VERYSILENT"])
        return True

    def shutdown(self) -> None:
        self._stop.set()
        if self._revert_timer is not None:
            self._revert_timer.cancel()
