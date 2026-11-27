"""POST /api/drafts/<id>/held/<hid>/login: open the login window, then retry the hold by itself.

Shapes (also in draft_studio/capture/__init__.py, "Claude's own screenshots"):

POST /api/drafts/<id>/held/<hid>/login   body {"origin": "https://host"}
    202 {"started": true}              `python3 -m draft_studio.capture login <origin>` runs detached;
                                       when kuhy closes its window the server re-runs the hold's
                                       login/busy captures itself (heldapi.py retry): the client
                                       only polls GET .../held
    409 {"error": "profile-busy"}      a login window is open, or another process holds the profile
    400 {"error": "bad-origin"}        origin is not in that hold's current `login` list (exact match)
    404 {"error": "not-found"}         no such draft or held id
    502 {"error": "login-failed"}      the login process could not be started

The origin that gets opened always comes from the held record, never from the client alone.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from draft_studio.capture.browser import holder, profile_dir
from draft_studio.held import HELD, HeldNotFound
from draft_studio.heldapi import HeldApi
from draft_studio.shots import Shots

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
Reply = tuple[int, Json]
BUSY: Reply = (409, {"error": "profile-busy"})
FREE_WAIT_S = 5.0  # how long a closed window may keep the profile lock before the retry runs anyway


class Window(Protocol):
    """subprocess.Popen, or a fake in tests."""

    def poll(self) -> int | None:
        """None while the window is open."""

    def wait(self) -> int:
        """Block until the window is closed."""


Spawn = Callable[[str, Path], Window]


def spawn_login(origin: str, data_dir: Path) -> Window:
    """The login command in its own session, so it outlives neither a request nor a server restart."""
    return subprocess.Popen(
        [sys.executable, "-m", "draft_studio.capture", "login", origin],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "DRAFT_STUDIO_DATA": str(data_dir)},  # the profile this server captures with
        start_new_session=True,
    )


class LoginWindows:
    """The one login window this server may have open, and the thread that waits for it to close."""

    def __init__(self, data_dir: Path, spawn: Spawn = spawn_login, free_wait: float = FREE_WAIT_S) -> None:
        self.data_dir, self.spawn, self.free_wait = data_dir, spawn, free_wait
        self.profile = profile_dir(data_dir)
        self.pause = 0.1
        self._window: Window | None = None
        self._lock = threading.Lock()

    def _busy(self) -> bool:
        return (self._window is not None and self._window.poll() is None) or holder(self.profile) is not None

    def start(self, origin: str, then: Callable[[], None]) -> bool:
        """Open the window; False when the profile is busy. `then` runs once the window closed."""
        with self._lock:
            if self._busy():
                return False
            self._window = window = self.spawn(origin, self.data_dir)
        threading.Thread(target=self._watch, args=(window, then), daemon=True).start()
        return True

    def _watch(self, window: Window, then: Callable[[], None]) -> None:
        try:
            window.wait()
            deadline = time.monotonic() + self.free_wait
            while holder(self.profile) is not None and time.monotonic() < deadline:
                time.sleep(self.pause)
            then()
        except Exception:
            LOG.exception("login retry failed")


def handle(windows: LoginWindows, shots: Shots, draft_id: str, hid: str, body: Any) -> Reply:
    """POST .../held/<hid>/login (DraftNotFound for a bad draft id is the server's 404)."""
    held = HeldApi(shots)
    with held.held.lock:
        try:
            rec = held.held.get(draft_id, hid)
        except HeldNotFound:
            return 404, {"error": "not-found"}
        listed = shots.held_view(rec)["login"] if rec["status"] == HELD else []
    origin = body.get("origin") if isinstance(body, dict) else None
    if not isinstance(origin, str) or origin not in listed:
        return 400, {"error": "bad-origin"}
    try:
        started = windows.start(origin, lambda: held.retry(draft_id, hid))
    except OSError:
        LOG.exception("login window did not start")
        return 502, {"error": "login-failed"}
    return (202, {"started": True}) if started else BUSY
