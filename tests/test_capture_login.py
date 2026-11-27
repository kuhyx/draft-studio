"""The one-time login command: a headed window on the capture profile that closes by itself."""

from __future__ import annotations

import os
import runpy
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture import login
from draft_studio.capture.wsock import WsClosed


@pytest.fixture
def chromium(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "chromium"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(binary))
    return binary


def profile() -> Path:
    return Path(os.environ["DRAFT_STUDIO_DATA"]) / "capture-profile"


class Proc:
    """A chromium process: open until `wait` is told to return or `poll` is set."""

    def __init__(self, hang: bool = False) -> None:
        self.code: int | None = None
        self.hang = hang
        self.killed = False

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("chromium", timeout or 0)
        self.code = 0
        return 0


def test_opens_a_headed_window_on_the_capture_profile_that_chromium_gives_a_devtools_port(
    chromium: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def popen(argv: list[str]) -> Proc:
        calls.append(argv)
        return Proc()

    assert login.main(["login", "https://panel.example/"], popen=popen, watch=lambda *a: "closed") == 0
    [argv] = calls
    assert argv[0] == str(chromium) and argv[-1] == "https://panel.example/"
    assert f"--user-data-dir={profile()}" in argv and "--password-store=basic" in argv
    assert "--mute-audio" in argv and "--remote-debugging-port=0" in argv
    assert not any(a.startswith("--headless") for a in argv)
    assert "closes by itself" in capsys.readouterr().out
    assert stat.S_IMODE(profile().stat().st_mode) == 0o700


def test_the_watch_gets_the_process_the_profile_and_the_url(chromium: Path) -> None:
    got: list[Any] = []
    proc = Proc()

    def watch(p: Any, prof: Path, url: str) -> str:
        got.extend([p, prof, url])
        return "logged-in"

    login.main(["login", "https://panel.example:8443/a/b"], popen=lambda argv: proc, watch=watch)
    assert got == [proc, profile(), "https://panel.example:8443/a/b"]
    assert proc.code == 0  # the process was waited for


def test_a_window_that_will_not_close_is_killed(chromium: Path) -> None:
    proc = Proc(hang=True)
    proc.kill = lambda: setattr(proc, "killed", True)
    assert (
        login.open_window(
            "https://x/", profile(), str(chromium), popen=lambda argv: proc, watch=lambda *a: "timeout"
        )
        == "timeout"
    )
    assert proc.killed


def test_without_a_devtools_connection_it_waits_for_kuhy_to_close_the_window(
    chromium: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proc = Proc()
    outcome = login.open_window(
        "https://x/", profile(), str(chromium), popen=lambda argv: proc, watch=lambda *a: "unwatched"
    )
    assert outcome == "unwatched" and proc.code == 0
    assert "close the window yourself" in capsys.readouterr().err


def test_a_stale_devtools_port_file_is_cleared_before_launch(chromium: Path) -> None:
    profile().mkdir(parents=True)
    (profile() / "DevToolsActivePort").write_text("1\n/stale")
    seen: list[bool] = []
    login.open_window(
        "https://x/", profile(), str(chromium),
        popen=lambda argv: seen.append((profile() / "DevToolsActivePort").exists()) or Proc(),
        watch=lambda *a: "closed",
    )  # fmt: skip
    assert seen == [False]


def test_refusals(chromium: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert login.main(["login", "file:///etc"]) == 2
    profile().mkdir(parents=True)
    os.symlink(f"host-{os.getpid()}", profile() / "SingletonLock")
    assert login.main(["login", "https://x/"]) == 2
    assert "in use" in capsys.readouterr().err
    (profile() / "SingletonLock").unlink()
    chromium.unlink()
    assert login.main(["login", "https://x/"]) == 3


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_watch_waits_for_the_devtools_port_then_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, proc = Clock(), Proc()
    urls: list[str] = []

    ran: list[Any] = []

    class Ran:
        def __init__(self, cdp: Any, site: str, jar: Any, **kw: Any) -> None:
            self.site, self.tuning = site, kw
            ran.append(self)

        def run(self) -> str:
            return "logged-in"

    class Ws:
        closed = False

        def close(self) -> None:
            self.closed = True

    ws = Ws()
    monkeypatch.setattr(login, "LoginWatch", Ran)
    monkeypatch.setattr(login, "connect", lambda url, t: urls.append(url) or ws)

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now > 0.2:
            (tmp_path / "DevToolsActivePort").write_text("9333\n/devtools/browser/x")

    out = login.watch_window(proc, tmp_path, "https://x/login", clock=clock, sleep=sleep, settle=0.1)
    assert (ran[0].site, ran[0].tuning["seen_login"], ran[0].tuning["settle"]) == ("https://x", True, 0.1)
    assert out == "logged-in" and urls == ["ws://127.0.0.1:9333/devtools/browser/x"] and ws.closed


def test_the_watch_gives_up_without_a_port_or_a_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    proc = Proc()
    assert login.watch_window(proc, tmp_path, "https://x", clock=clock, sleep=clock.sleep) == "unwatched"
    assert clock.now >= login.START_S
    proc.code = 0  # the window was closed before it ever published a port
    assert login.watch_window(proc, tmp_path, "https://x", clock=clock, sleep=clock.sleep) == "closed"
    proc.code = None
    (tmp_path / "DevToolsActivePort").write_text("9333\n/devtools/browser/x")

    def refuse(url: str, timeout: float) -> None:
        raise WsClosed("handshake")

    monkeypatch.setattr(login, "connect", refuse)
    assert login.watch_window(proc, tmp_path, "https://x", clock=clock, sleep=clock.sleep) == "unwatched"


def test_module_entry_point(chromium: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["draft_studio.capture", "login", "https://x/"])
    with pytest.raises(SystemExit) as done:
        runpy.run_module("draft_studio.capture", run_name="__main__")
    assert done.value.code == 0
