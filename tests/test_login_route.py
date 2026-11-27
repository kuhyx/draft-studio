"""POST /api/drafts/<id>/held/<hid>/login: a detached login window, then the server retries by itself."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from draft_studio import loginapi
from draft_studio.loginapi import LoginWindows
from tests.conftest import App
from tests.test_capture_service import ShotPage, use
from tests.test_held import held, hold

ORIGIN = "https://panel.example"
PATH = "/api/drafts/{}/held/{}/login"


class FakeProc:
    """A login window that stays open until the test closes it."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def poll(self) -> int | None:
        return 0 if self.closed.is_set() else None

    def wait(self) -> int:
        assert self.closed.wait(5)
        return 0


class FakeSpawn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.procs: list[FakeProc] = []

    def __call__(self, origin: str, data_dir: Path) -> FakeProc:
        self.calls.append((origin, data_dir))
        self.procs.append(FakeProc())
        return self.procs[-1]


def login_page() -> ShotPage:
    page = ShotPage(["Zapisz"])
    page.password = True
    return page


def armed(app: App) -> tuple[FakeSpawn, str, dict[str, Any]]:
    """A login hold, and a server whose login windows are the fake spawner (never a real chromium)."""
    use(app, login_page)
    did, h = hold(app)
    spawn = FakeSpawn()
    app.httpd.logins = LoginWindows(app.store.root, spawn, 0.0)
    return spawn, did, h


def post(app: App, did: str, hid: str, body: Any = None) -> tuple[int, Any]:
    return app.call("POST", PATH.format(did, hid), {"origin": ORIGIN} if body is None else body)


def until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def lock_profile(app: App) -> None:
    profile = app.store.root / "capture-profile"
    profile.mkdir(parents=True, exist_ok=True)
    os.symlink(f"host-{os.getpid()}", profile / "SingletonLock")


def test_login_starts_a_window_and_the_close_releases_the_answer(app: App) -> None:
    spawn, did, h = armed(app)
    assert post(app, did, h["id"]) == (202, {"started": True})
    assert spawn.calls == [(ORIGIN, app.store.root)]
    assert held(app, did)["released"] == []
    app.httpd.captures.launcher.make = lambda: ShotPage(["Zapisz"])  # kuhy logged in
    spawn.procs[0].closed.set()
    until(lambda: bool(held(app, did)["released"]))
    listing = held(app, did)
    assert listing["held"] == [] and "](assets/" in listing["released"][0]["replacement"]


def test_a_running_window_or_a_held_profile_is_busy(app: App) -> None:
    spawn, did, h = armed(app)
    assert post(app, did, h["id"])[0] == 202
    assert post(app, did, h["id"]) == (409, {"error": "profile-busy"})
    assert len(spawn.calls) == 1
    spawn.procs[0].closed.set()
    until(lambda: spawn.procs[0].poll() == 0)
    lock_profile(app)
    assert post(app, did, h["id"]) == (409, {"error": "profile-busy"})
    assert len(spawn.calls) == 1


def test_only_an_origin_the_hold_lists_is_opened(app: App) -> None:
    spawn, did, h = armed(app)
    for body in ({"origin": "https://evil.example"}, {"origin": ORIGIN + "/"}, {"origin": 1}, {}, [ORIGIN]):
        assert post(app, did, h["id"], body) == (400, {"error": "bad-origin"})
    assert spawn.calls == []


def test_unknown_draft_or_hold_is_not_found(app: App) -> None:
    spawn, did, _ = armed(app)
    assert post(app, did, "0" * 16) == (404, {"error": "not-found"})
    assert post(app, "nodraft00000", "0" * 16) == (404, {"error": "not-found"})
    assert spawn.calls == []


def test_a_released_hold_lists_no_origin(app: App) -> None:
    spawn, did, h = armed(app)
    app.httpd.captures.launcher.make = lambda: ShotPage(["Zapisz"])
    app.call("POST", f"/api/drafts/{did}/held", {"retry": h["id"]})
    assert post(app, did, h["id"]) == (400, {"error": "bad-origin"})
    assert spawn.calls == []


def test_a_spawn_failure_is_a_502(app: App) -> None:
    _, did, h = armed(app)

    def broken(origin: str, data_dir: Path) -> FakeProc:
        raise OSError("no python3")

    app.httpd.logins.spawn = broken
    assert post(app, did, h["id"]) == (502, {"error": "login-failed"})
    app.httpd.logins.spawn = FakeSpawn()
    assert post(app, did, h["id"])[0] == 202  # nothing stayed marked as running


def test_cross_origin_and_foreign_host_are_refused(app: App) -> None:
    spawn, did, h = armed(app)
    body = {"origin": ORIGIN}
    assert app.call("POST", PATH.format(did, h["id"]), body, {"Origin": "http://evil.com"})[0] == 403
    assert app.call("POST", PATH.format(did, h["id"]), body, {"Host": "evil.com"})[0] == 403
    assert spawn.calls == []


def test_a_failing_retry_is_logged_and_the_server_keeps_serving(
    app: App, caplog: pytest.LogCaptureFixture
) -> None:
    spawn, did, h = armed(app)
    assert post(app, did, h["id"])[0] == 202
    app.httpd.captures.launcher.make = lambda: RuntimeError("chromium exploded")
    with caplog.at_level(logging.ERROR, logger="draft_studio"):
        spawn.procs[0].closed.set()
        until(lambda: "login retry" in caplog.text)
    assert held(app, did)["held"] != []


def closed_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, holders: Callable[[Path], int | None], free_wait: float
) -> LoginWindows:
    monkeypatch.setattr(loginapi, "holder", holders)
    proc = FakeProc()
    proc.closed.set()
    windows = LoginWindows(tmp_path, lambda origin, data_dir: proc, free_wait)
    windows.pause = 0.001
    return windows


def test_the_retry_waits_for_the_profile_lock_to_go(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pids = iter([None, 123, 123, None])  # the first look is the start check
    windows = closed_windows(monkeypatch, tmp_path, lambda profile: next(pids), 5.0)
    done: list[str] = []
    assert windows.start(ORIGIN, lambda: done.append("retried"))
    until(lambda: done == ["retried"])
    assert next(pids, "drained") == "drained"


def test_the_retry_gives_up_waiting_for_a_stuck_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    looks = iter([None])  # free for the start check, held ever after
    windows = closed_windows(monkeypatch, tmp_path, lambda profile: next(looks, 123), 0.02)
    done: list[str] = []
    assert windows.start(ORIGIN, lambda: done.append("retried"))
    until(lambda: done == ["retried"])


def test_the_real_spawner_detaches_the_login_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class Recorder:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            seen.update(argv=argv, **kw)

    monkeypatch.setattr(subprocess, "Popen", Recorder)
    loginapi.spawn_login(ORIGIN, tmp_path)
    assert seen["argv"] == [sys.executable, "-m", "draft_studio.capture", "login", ORIGIN]
    assert seen["start_new_session"] is True
    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL
    assert seen["env"]["DRAFT_STUDIO_DATA"] == str(tmp_path)


def test_the_test_guard_refuses_a_real_login_window() -> None:
    with pytest.raises(pytest.fail.Exception):
        subprocess.Popen([sys.executable, "-m", "draft_studio.capture", "login", ORIGIN])
    with pytest.raises(pytest.fail.Exception):
        subprocess.Popen(["/usr/bin/chromium", "--new-window", ORIGIN])
    subprocess.Popen([sys.executable, "-c", "pass"]).wait()  # anything else still runs
    with pytest.raises(FileNotFoundError):  # past the guard: the headless one is only missing here
        subprocess.Popen(["/nonexistent/chromium", "--headless=new"])
