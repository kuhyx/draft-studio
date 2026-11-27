"""The login window as a unit of its own, written down: a server restart neither closes nor forgets it.

`python -m draft_studio.capture login <origin>` runs in its own `systemd-run --user` unit (detach.py):
a deploy that restarts the server unit leaves the window open. `<data dir>/login-window.json`
({"draftId", "hold", "origin", "unit", "started"}) is written before the unit starts and removed
only after the hold's retry returned. A server that boots finds the file and adopts the window: it
waits for the unit to end like the one that opened it, then runs the retry. A window that died while
no server was there gets its retry at once. A retry that ran twice is harmless (a hold that is no
longer held ignores it), a lost one leaves the permission card spinning, so the file goes last.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from draft_studio.atomic import read_json, write_json
from draft_studio.capture.browser import holder, profile_dir
from draft_studio.detach import systemd_spawn
from draft_studio.held import HeldNotFound
from draft_studio.storebase import DraftNotFound

LOG = logging.getLogger("draft_studio")
FREE_WAIT_S = 5.0  # how long a closed window may keep the profile lock before the retry runs anyway
UNIT_NAP_S = 0.5
FILE = "login-window.json"


class Window(Protocol):
    """A login window: the unit, or a fake in tests."""

    def poll(self) -> int | None:
        """None while the window is open."""

    def wait(self) -> int:
        """Block until the window is closed."""


@dataclass(frozen=True)
class Record:
    """The one window this data dir has open: whose hold it serves, and the unit that runs it."""

    draft_id: str
    hold: str
    origin: str
    unit: str
    started: float


def read(data_dir: Path) -> Record | None:
    """The persisted window, None when there is none (or the file is not ours)."""
    raw: Any = read_json(data_dir / FILE)
    try:
        return Record(raw["draftId"], raw["hold"], raw["origin"], raw["unit"], raw["started"])
    except (KeyError, TypeError):
        return None


def write(data_dir: Path, rec: Record) -> None:
    """Persist `rec` (atomically)."""
    write_json(
        data_dir / FILE,
        {"draftId": rec.draft_id, "hold": rec.hold, "origin": rec.origin, "unit": rec.unit}
        | {"started": rec.started},
    )


def clear(data_dir: Path) -> None:
    """The retry ran: forget the window."""
    (data_dir / FILE).unlink(missing_ok=True)


class UnitWindow:
    """A window that is a systemd user unit: open while `systemctl --user is-active` says so."""

    def __init__(
        self,
        unit: str,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        nap: float = UNIT_NAP_S,
    ) -> None:
        self.unit, self.run, self.sleep, self.nap = unit, run, sleep, nap

    def poll(self) -> int | None:
        """None while the unit is active; 0 once it is gone (a collected unit is unknown, not active)."""
        cmd = ["systemctl", "--user", "is-active", "--quiet", self.unit]
        done = self.run(cmd, check=False, capture_output=True, text=True, timeout=10)
        return None if done.returncode == 0 else 0

    def wait(self) -> int:
        """Block until the unit is gone."""
        while self.poll() is None:
            self.sleep(self.nap)
        return 0


Spawn = Callable[[str, Path, str], Window]  # (origin, data dir, unit name)
Retry = Callable[[str, str], None]  # (draft id, hold id): re-run the hold's login/busy captures
Adopt = Callable[[str], Window]


def spawn_login(
    origin: str,
    data_dir: Path,
    unit: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Window:
    """The login command in a unit of its own: neither a request nor a restart of the server stops it."""
    argv = [sys.executable, "-m", "draft_studio.capture", "login", origin]
    env = {**os.environ, "DRAFT_STUDIO_DATA": str(data_dir)}  # the profile this server captures with
    systemd_spawn(unit, argv, data_dir / "login-window.log", environ=env, run=run)
    return UnitWindow(unit)


class LoginWindows:
    """The one login window this data dir may have open, and the thread that waits for it to close."""

    def __init__(
        self,
        data_dir: Path,
        retry: Retry,
        spawn: Spawn = spawn_login,
        free_wait: float = FREE_WAIT_S,
        unit_window: Adopt = UnitWindow,
    ) -> None:
        self.data_dir, self.retry, self.spawn, self.free_wait = data_dir, retry, spawn, free_wait
        self.unit_window = unit_window
        self.profile = profile_dir(data_dir)
        self.pause = 0.1
        self._window: Window | None = None
        self._lock = threading.Lock()

    def _busy(self) -> bool:
        return (self._window is not None and self._window.poll() is None) or holder(self.profile) is not None

    def start(self, draft_id: str, hold: str, origin: str) -> bool:
        """Open the window; False when the profile is busy. The hold's retry runs once it closed."""
        with self._lock:
            if self._busy():
                return False
            rec = Record(draft_id, hold, origin, f"ds-login-{hold}-{secrets.token_hex(3)}", time.time())
            write(self.data_dir, rec)
            try:
                self._window = self.spawn(origin, self.data_dir, rec.unit)
            except Exception:
                clear(self.data_dir)
                raise
            window = self._window
        self._watch_in_thread(window, rec)
        return True

    def adopt(self) -> threading.Thread | None:
        """At boot: take over the window the last server opened (None: there was none).

        Alive, it is waited for; dead, `wait` returns at once and the retry it still owes runs now.
        """
        rec = read(self.data_dir)
        if rec is None:
            return None
        with self._lock:
            self._window = window = self.unit_window(rec.unit)
        return self._watch_in_thread(window, rec)

    def _watch_in_thread(self, window: Window, rec: Record) -> threading.Thread:
        thread = threading.Thread(target=self._watch, args=(window, rec), daemon=True)
        thread.start()
        return thread

    def _watch(self, window: Window, rec: Record) -> None:
        try:
            window.wait()
            deadline = time.monotonic() + self.free_wait
            while holder(self.profile) is not None and time.monotonic() < deadline:
                time.sleep(self.pause)
            self.retry(rec.draft_id, rec.hold)
        except (HeldNotFound, DraftNotFound):
            LOG.info("login hold %s is gone: nothing to retry", rec.hold)
        except Exception:
            LOG.exception("login retry failed")
            return  # the file stays: the next server runs the retry again
        clear(self.data_dir)
