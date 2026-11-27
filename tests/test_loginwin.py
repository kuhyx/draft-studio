"""The login window across a restart: the record, the unit's state, adoption and its one retry."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from draft_studio import loginwin
from draft_studio.held import HeldNotFound
from draft_studio.loginwin import LoginWindows, Record, UnitWindow
from draft_studio.storebase import DraftNotFound
from tests.conftest import App
from tests.test_held import held
from tests.test_login_route import armed

ORIGIN = "https://panel.example"


class Unit:
    """A systemd unit that stays active until the test ends it."""

    def __init__(self) -> None:
        self.ended = threading.Event()

    def poll(self) -> int | None:
        return 0 if self.ended.is_set() else None

    def wait(self) -> int:
        assert self.ended.wait(5)
        return 0


class Retries:
    """The retry the server runs: what it was asked, and what it does."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, draft_id: str, hold: str) -> None:
        self.calls.append((draft_id, hold))
        if self.fail:
            raise self.fail


def until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def windows(tmp_path: Path, retry: Retries, unit: Unit | None = None, **kw: Any) -> LoginWindows:
    found = unit or Unit()
    out = LoginWindows(tmp_path, retry, lambda *_: found, 0.0, lambda name: found, **kw)
    out.pause = 0.001
    return out


def record(tmp_path: Path) -> Record:
    rec = Record("d1", "h1", ORIGIN, "ds-login-h1-aa", 1.0)
    loginwin.write(tmp_path, rec)
    return rec


def test_the_record_round_trips_and_a_foreign_file_is_none(tmp_path: Path) -> None:
    assert loginwin.read(tmp_path) is None
    rec = record(tmp_path)
    assert loginwin.read(tmp_path) == rec
    (tmp_path / loginwin.FILE).write_text('{"hold": "h1"}')
    assert loginwin.read(tmp_path) is None
    (tmp_path / loginwin.FILE).write_text("[1]")
    assert loginwin.read(tmp_path) is None
    loginwin.clear(tmp_path)
    loginwin.clear(tmp_path)  # nothing left: not an error
    assert not (tmp_path / loginwin.FILE).exists()


def test_a_unit_window_is_open_while_systemctl_says_active() -> None:
    codes = iter([0, 0, 3])
    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, next(codes), "", "")

    naps: list[float] = []
    window = UnitWindow("ds-login-x", run, naps.append, 0.25)
    assert window.poll() is None
    assert window.wait() == 0  # the second look still active, the third gone
    assert naps == [0.25]
    assert seen[0] == ["systemctl", "--user", "is-active", "--quiet", "ds-login-x"]


def test_start_writes_the_record_before_the_unit_starts_and_keeps_it_until_the_retry(tmp_path: Path) -> None:
    unit, retry = Unit(), Retries()
    at_spawn: list[Record | None] = []

    def spawn(origin: str, data_dir: Path, name: str) -> Unit:
        at_spawn.append(loginwin.read(tmp_path))
        return unit

    win = LoginWindows(tmp_path, retry, spawn, 0.0, lambda name: unit)
    assert win.start("d1", "h1", ORIGIN)
    rec = at_spawn[0]
    assert rec is not None and (rec.draft_id, rec.hold, rec.origin) == ("d1", "h1", ORIGIN)
    assert rec.unit.startswith("ds-login-h1-")
    assert loginwin.read(tmp_path) == rec and retry.calls == []  # open: nothing retried, nothing forgotten
    unit.ended.set()
    until(lambda: retry.calls == [("d1", "h1")])
    until(lambda: loginwin.read(tmp_path) is None)  # the file goes after the retry returned


def test_a_unit_that_does_not_start_leaves_no_record(tmp_path: Path) -> None:
    def refused(origin: str, data_dir: Path, name: str) -> Unit:
        raise OSError("no systemd-run")

    win = LoginWindows(tmp_path, Retries(), refused)
    with pytest.raises(OSError, match="no systemd-run"):
        win.start("d1", "h1", ORIGIN)
    assert loginwin.read(tmp_path) is None
    assert win._window is None


def test_adopt_without_a_record_does_nothing(tmp_path: Path) -> None:
    retry = Retries()
    assert windows(tmp_path, retry).adopt() is None
    assert retry.calls == []


def test_a_live_window_is_adopted_waited_for_and_retried_once(tmp_path: Path) -> None:
    rec, unit, retry = record(tmp_path), Unit(), Retries()
    win = windows(tmp_path, retry, unit)
    thread = win.adopt()
    assert thread is not None
    time.sleep(0.05)
    assert retry.calls == [] and loginwin.read(tmp_path) == rec  # still open: it is waited for
    assert win.start("d2", "h2", ORIGIN) is False  # and a second window is refused meanwhile
    unit.ended.set()
    thread.join(5)
    assert retry.calls == [("d1", "h1")] and loginwin.read(tmp_path) is None
    assert win.adopt() is None and retry.calls == [("d1", "h1")]  # the record is gone: never a second retry


def test_a_window_that_died_while_no_server_ran_gets_its_retry_at_once(tmp_path: Path) -> None:
    record(tmp_path)
    unit, retry = Unit(), Retries()
    unit.ended.set()
    thread = windows(tmp_path, retry, unit).adopt()
    assert thread is not None
    thread.join(5)
    assert retry.calls == [("d1", "h1")] and loginwin.read(tmp_path) is None


@pytest.mark.parametrize("gone", [HeldNotFound("h1"), DraftNotFound("d1")])
def test_a_hold_that_is_gone_ends_the_record(tmp_path: Path, gone: Exception) -> None:
    record(tmp_path)
    unit, retry = Unit(), Retries(gone)
    unit.ended.set()
    thread = windows(tmp_path, retry, unit).adopt()
    assert thread is not None
    thread.join(5)
    assert loginwin.read(tmp_path) is None


def test_a_failing_retry_keeps_the_record_for_the_next_server(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rec = record(tmp_path)
    unit, retry = Unit(), Retries(RuntimeError("chromium exploded"))
    unit.ended.set()
    with caplog.at_level(logging.ERROR, logger="draft_studio"):
        thread = windows(tmp_path, retry, unit).adopt()
        assert thread is not None
        thread.join(5)
    assert "login retry failed" in caplog.text
    assert loginwin.read(tmp_path) == rec


def test_a_new_server_adopts_the_window_and_the_hold_is_retried_after_it(app: App) -> None:
    """The restart seam end to end (server.py): the record names a hold; the unit ends; its retry runs once."""
    _, did, h = armed(app)
    loginwin.write(app.store.root, Record(did, h["id"], ORIGIN, "ds-login-h1-aa", 1.0))
    unit = Unit()
    app.httpd.logins.unit_window = lambda name: unit
    thread = app.httpd.resume_login()
    assert thread is not None
    assert held(app, did)["held"][0]["tries"] == 0
    unit.ended.set()
    thread.join(5)
    assert held(app, did)["held"][0]["tries"] == 1
    assert loginwin.read(app.store.root) is None
