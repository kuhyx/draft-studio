"""Launching chromium on the capture profile: lock detection, endpoint discovery, shutdown."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture import browser, errors
from draft_studio.capture.wsock import WsClosed
from tests.test_capture_cdp import FakeWs as ScriptedWs


class FakeProc:
    def __init__(self, exits: int | None = None, hangs: bool = False) -> None:
        self.pid = 424242
        self.returncode = exits
        self.hangs = hangs
        self.waits = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        if self.hangs and self.waits == 1:
            raise subprocess.TimeoutExpired("chromium", timeout or 0)
        return 0


class FakeWs:
    """Chromium hangs up as soon as it is asked to close; `log` orders hang-up, kill and our close."""

    def __init__(self, log: list[str]) -> None:
        self.sent: list[str] = []
        self.closed = False
        self.log = log
        self.sock = self

    def settimeout(self, value: float) -> None:
        """Socket stand-in."""

    def send(self, text: str) -> None:
        self.sent.append(text)

    def readable(self, timeout: float) -> bool:
        return True

    def recv(self) -> str:
        self.log.append("hung up")
        raise WsClosed("eof")

    def close(self) -> None:
        self.log.append("closed")
        self.closed = True


class Rig:
    """A Chromium whose process, socket, page and clock are fakes."""

    def __init__(
        self, tmp: Path, proc: FakeProc, port_text: str | None = "9555\n/devtools/browser/abc"
    ) -> None:
        self.profile = tmp / "capture-profile"
        self.binary = tmp / "chromium"
        self.binary.write_text("#!/bin/sh\n")
        self.binary.chmod(0o755)
        self.proc, self.port_text = proc, port_text
        self.argv: list[str] = []
        self.kills: list[tuple[int, int]] = []
        self.log: list[str] = []
        self.ws = FakeWs(self.log)
        self.urls: list[str] = []
        self.now = 0.0
        self.chromium = browser.Chromium(
            self.profile,
            binary=str(self.binary),
            timeout=5.0,
            popen=self.popen,
            clock=lambda: self.now,
            sleep=self.sleep,
            connect=self.connect,
            opener=lambda cdp, clock: ("page", cdp),
            killpg=self.killpg,
        )

    def killpg(self, pid: int, sig: int) -> None:
        self.log.append("killed")
        self.kills.append((pid, sig))

    def popen(self, argv: list[str], **kw: Any) -> FakeProc:
        self.argv = argv
        assert kw["start_new_session"] is True
        return self.proc

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.port_text is not None and self.now > 0.2:
            (self.profile / "DevToolsActivePort").write_text(self.port_text)

    def connect(self, url: str, timeout: float) -> FakeWs:
        self.urls.append(url)
        return self.ws


def test_open_launches_headless_on_the_profile_and_closes_gracefully(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    rig.profile.mkdir()
    (rig.profile / "DevToolsActivePort").write_text("1\n/stale")
    with rig.chromium.open() as (page, _cdp):
        assert page == "page"
    assert rig.urls == ["ws://127.0.0.1:9555/devtools/browser/abc"]
    assert "--headless=new" in rig.argv and "--remote-debugging-port=0" in rig.argv
    assert f"--user-data-dir={rig.profile}" in rig.argv and "--disable-extensions" in rig.argv
    assert not any("9222" in a for a in rig.argv)
    assert '"Browser.close"' in rig.ws.sent[0] and rig.ws.closed
    assert rig.kills == [(424242, signal.SIGKILL)]
    # The guard kept answering until chromium hung up; our end closed only after the kill.
    assert rig.log == ["hung up", "killed", "closed"]


def test_hung_chromium_is_killed(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc(hangs=True))
    with rig.chromium.open():
        pass
    assert rig.proc.waits == 2 and rig.kills


def test_no_chromium(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    rig.binary.unlink()
    with pytest.raises(errors.NoChromium), rig.chromium.open():
        pass
    assert rig.argv == []


def lock(profile: Path, pid: int) -> None:
    profile.mkdir(exist_ok=True)
    os.symlink(f"{socket.gethostname()}-{pid}", profile / "SingletonLock")


def test_profile_held_by_a_live_process_is_busy(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    lock(rig.profile, os.getpid())
    assert browser.holder(rig.profile) == os.getpid() and rig.chromium.busy()
    with pytest.raises(errors.ProfileBusy), rig.chromium.open():
        pass
    assert rig.argv == []


@pytest.mark.parametrize("target", ["host-999999999", "garbage"])
def test_stale_or_odd_lock_is_ignored(tmp_path: Path, target: str) -> None:
    rig = Rig(tmp_path, FakeProc())
    rig.profile.mkdir()
    os.symlink(target, rig.profile / "SingletonLock")
    assert browser.holder(rig.profile) is None and not rig.chromium.busy()


def test_lock_owned_by_another_user_counts_as_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock(tmp_path, 1)

    def deny(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(browser.os, "kill", deny)
    assert browser.holder(tmp_path) == 1


def test_chromium_that_exits_early(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc(exits=21), port_text=None)
    with pytest.raises(errors.ChromiumFailed, match="exited 21"), rig.chromium.open():
        pass
    rig = Rig(tmp_path, FakeProc(exits=0), port_text=None)
    rig.chromium.popen = lambda argv, **kw: (lock(rig.profile, os.getpid()), rig.proc)[1]
    with pytest.raises(errors.ProfileBusy), rig.chromium.open():
        pass


def test_endpoint_timeout_and_connect_failure(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc(), port_text=None)
    with pytest.raises(errors.CaptureTimeout), rig.chromium.open():
        pass
    assert rig.kills

    (tmp_path / "b").mkdir()
    rig = Rig(tmp_path / "b", FakeProc())

    def refuse(url: str, timeout: float) -> FakeWs:
        raise ConnectionRefusedError("no")

    rig.chromium.connect = refuse
    with pytest.raises(errors.ChromiumFailed, match="connect"), rig.chromium.open():
        pass


def test_failed_close_still_kills(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())

    def broken(text: str) -> None:
        raise BrokenPipeError

    rig.ws.send = broken
    with rig.chromium.open():
        pass
    assert rig.kills


def test_defaults_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", "/x/chromium")
    monkeypatch.setenv("DRAFT_STUDIO_CAPTURE_TIMEOUT", "7")
    chromium = browser.Chromium(tmp_path)
    assert (chromium.binary, chromium.timeout) == ("/x/chromium", 7.0)
    monkeypatch.delenv("DRAFT_STUDIO_CHROMIUM")
    monkeypatch.delenv("DRAFT_STUDIO_CAPTURE_TIMEOUT")
    assert browser.Chromium(tmp_path).binary == browser.CHROMIUM
    assert browser.profile_dir(tmp_path) == tmp_path / "capture-profile"


def test_session_cookies_are_restored_before_the_page_and_refreshed_before_the_close(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    rig.profile.mkdir()
    old = {"name": "sid", "value": "old", "domain": "panel.example", "path": "/"}
    new = {**old, "value": "new", "session": True}
    (rig.profile / "session-cookies.json").write_text(
        json.dumps({"cookies": [{"seen": time.time(), "cookie": old}]})
    )

    def script(msg: dict[str, Any]) -> list[Any]:
        if msg["method"] == "Browser.close":
            return [WsClosed("eof")]
        return [{"id": msg["id"], "result": {"cookies": [new]}}]

    ws = ScriptedWs(script)
    rig.ws = ws
    seen_by_opener: list[list[str]] = []
    rig.chromium.opener = lambda cdp, clock: seen_by_opener.append([m["method"] for m in ws.sent])
    with rig.chromium.open():
        pass
    assert seen_by_opener == [["Storage.setCookies"]]  # restored before any page exists
    assert [m["method"] for m in ws.sent] == ["Storage.setCookies", "Storage.getCookies", "Browser.close"]
    assert (
        json.loads((rig.profile / "session-cookies.json").read_text())["cookies"][0]["cookie"]["value"]
        == "new"
    )


def test_without_a_jar_the_run_sends_no_cookie_calls(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeProc())
    with rig.chromium.open():
        pass
    assert not any("Cookies" in text for text in rig.ws.sent)
