"""Headless chromium on the capture profile, reached over DevTools on a port chromium picks.

The profile (<data dir>/capture-profile) keeps the cookies kuhy's one-time
login left there (login.py). Chromium allows one process per profile and a
second one would hand its URL to the first, maybe the headed login window on
kuhy's screen, so a live SingletonLock owner means ProfileBusy before launch.
`--remote-debugging-port=0` makes chromium pick a free port and write it to
<profile>/DevToolsActivePort: no race, never 9222, never kuhy's display.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.page import open_page
from draft_studio.capture.wsock import WsClosed, connect

# /usr/local/bin/chromium is a hosts-file wrapper on kuhy's box: use the real binary.
CHROMIUM = "/usr/bin/chromium"
TIMEOUT_S = 45.0
# basic: the same fixed cookie key headed (login) and headless (capture) runs use;
# the OS keyring is not reachable the same way from both.
PROFILE_FLAGS = (
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
)
HEADLESS_FLAGS = (
    "--headless=new",
    "--remote-debugging-port=0",
    "--disable-gpu",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-background-networking",
    "--window-size=1280,800",
)


def profile_dir(root: Path) -> Path:
    """The capture profile inside the data dir."""
    return root / "capture-profile"


def chromium_path() -> str:
    """$DRAFT_STUDIO_CHROMIUM (shared with PDF export), else /usr/bin/chromium."""
    return os.environ.get("DRAFT_STUDIO_CHROMIUM") or CHROMIUM


def holder(profile: Path) -> int | None:
    """PID of the live process holding the profile lock, else None."""
    try:
        target = os.readlink(profile / "SingletonLock")
    except OSError:
        return None
    pid = target.rpartition("-")[2]
    if not pid.isdigit():
        return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return int(pid)
    return int(pid)


class Chromium:
    """Launcher: `with Chromium(profile).open() as page:` (runner.Page)."""

    def __init__(
        self,
        profile: Path,
        *,
        binary: str | None = None,
        timeout: float | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        connect: Callable[..., Any] = connect,
        opener: Callable[..., Any] = open_page,
        killpg: Callable[[int, int], None] = os.killpg,
    ) -> None:
        self.profile = profile
        self.binary = binary or chromium_path()
        self.timeout = timeout or float(os.environ.get("DRAFT_STUDIO_CAPTURE_TIMEOUT") or TIMEOUT_S)
        self.popen, self.clock, self.sleep = popen, clock, sleep
        self.connect, self.opener, self.killpg = connect, opener, killpg

    def busy(self) -> bool:
        """The login window (or another chromium) holds the profile."""
        return holder(self.profile) is not None

    def _endpoint(self, proc: Any, deadline: float) -> str:
        port_file = self.profile / "DevToolsActivePort"
        while True:
            lines = port_file.read_text().split("\n") if port_file.is_file() else []
            if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/"):
                return f"ws://127.0.0.1:{lines[0]}{lines[1]}"
            if proc.poll() is not None:
                if self.busy():
                    raise errors.ProfileBusy()
                raise errors.ChromiumFailed(f"exited {proc.returncode}")
            if self.clock() >= deadline:
                raise errors.CaptureTimeout()
            self.sleep(0.05)

    def _stop(self, proc: Any, cdp: Cdp | None) -> None:
        """Close gracefully (cookies reach disk), then kill whatever is left of the group."""
        if cdp is not None:
            with contextlib.suppress(errors.CaptureError):
                cdp.send("Browser.close")
            cdp.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            self.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)

    @contextlib.contextmanager
    def open(self) -> Iterator[Any]:
        """A ready page; chromium is gone when the block ends, however it ends."""
        deadline = self.clock() + self.timeout
        if not os.access(self.binary, os.X_OK):
            raise errors.NoChromium()
        if self.busy():
            raise errors.ProfileBusy()
        self.profile.mkdir(parents=True, exist_ok=True)
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        argv = [
            self.binary,
            *HEADLESS_FLAGS,
            *PROFILE_FLAGS,
            f"--user-data-dir={self.profile}",
            "about:blank",
        ]
        proc = self.popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cdp: Cdp | None = None
        try:
            url = self._endpoint(proc, deadline)
            try:
                ws = self.connect(url, max(deadline - self.clock(), 0.1))
            except (OSError, WsClosed) as exc:
                raise errors.ChromiumFailed(f"connect: {exc}") from exc
            cdp = Cdp(ws, deadline, self.clock)
            yield self.opener(cdp, self.clock)
        finally:
            self._stop(proc, cdp)
