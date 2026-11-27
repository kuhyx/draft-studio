"""Session-only cookies survive a browser restart: the jar in the private profile (cookiejar.py)."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture import errors
from draft_studio.capture.cookiejar import MAX_AGE_S, CookieJar


def cookie(
    name: str, value: str = "v", session: bool = True, domain: str = "panel.example"
) -> dict[str, Any]:
    return {
        "name": name, "value": value, "domain": domain, "path": "/", "secure": True, "httpOnly": True,
        "sameSite": "Lax", "session": session, "expires": -1 if session else 2e9, "size": 9,
        "priority": "Medium", "sameParty": False, "sourceScheme": "Secure", "sourcePort": 443,
    }  # fmt: skip


class FakeCdp:
    def __init__(self, cookies: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.cookies = cookies or []
        self.fail = fail
        self.set_with: list[dict[str, Any]] = []
        self.deadline = 0.0

    def call(self, method: str, session: str | None = None, **params: Any) -> dict[str, Any]:
        if self.fail:
            raise errors.ChromiumFailed(f"{method}: boom")
        if method == "Storage.setCookies":
            self.set_with = params["cookies"]
            return {}
        return {"cookies": self.cookies}


class Wall:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    return tmp_path / "capture-profile"


def stored(profile: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((profile / "session-cookies.json").read_text())
    return data


def test_only_session_cookies_are_kept_and_the_file_is_private(profile: Path) -> None:
    jar = CookieJar(profile, Wall())
    jar.save(FakeCdp([cookie("sid"), cookie("device", session=False, domain=".example")]))
    entries = stored(profile)["cookies"]
    assert [e["cookie"]["name"] for e in entries] == ["sid"]
    assert stat.S_IMODE(os.stat(profile / "session-cookies.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(profile).st_mode) == 0o700


def test_load_hands_the_cookies_back_without_an_expiry(profile: Path) -> None:
    CookieJar(profile, Wall()).save(FakeCdp([cookie("sid", "secret")]))
    cdp = FakeCdp()
    assert CookieJar(profile, Wall()).load(cdp) == 1
    (sent,) = cdp.set_with
    assert sent == {
        "name": "sid", "value": "secret", "domain": "panel.example", "path": "/", "secure": True,
        "httpOnly": True, "sameSite": "Lax", "priority": "Medium", "sourceScheme": "Secure",
        "sourcePort": 443,
    }  # fmt: skip


def test_a_poll_before_the_login_never_wipes_the_jar(profile: Path) -> None:
    wall = Wall()
    jar = CookieJar(profile, wall)
    jar.save(FakeCdp([cookie("sid", "old")]))
    jar.save(FakeCdp([cookie("other")]))  # the window shows a logged-out page right now
    names = {e["cookie"]["name"]: e["cookie"]["value"] for e in stored(profile)["cookies"]}
    assert names == {"sid": "old", "other": "v"}
    jar.save(FakeCdp([cookie("sid", "rotated")]))
    assert {e["cookie"]["name"]: e["cookie"]["value"] for e in stored(profile)["cookies"]}["sid"] == "rotated"


def test_a_cookie_ends_twelve_hours_after_its_login(profile: Path) -> None:
    wall = Wall()
    jar = CookieJar(profile, wall)
    jar.save(FakeCdp([cookie("sid")]))
    wall.now += MAX_AGE_S - 60
    jar.save(FakeCdp([cookie("sid", "rotated")]))  # a refresh does not restart the clock
    wall.now += 120
    cdp = FakeCdp()
    assert jar.load(cdp) == 0 and cdp.set_with == []
    jar.save(FakeCdp([]))
    assert stored(profile)["cookies"] == []  # the next save drops it from the file


def test_a_broken_or_missing_file_is_an_empty_jar(profile: Path) -> None:
    jar = CookieJar(profile, Wall())
    cdp = FakeCdp()
    assert not jar.exists() and jar.load(cdp) == 0
    profile.mkdir()
    for junk in ("not json", "[]", '{"cookies": "x"}', '{"cookies": [1, {"cookie": 1}]}'):
        (profile / "session-cookies.json").write_text(junk)
        assert jar.load(cdp) == 0
    assert jar.exists()


def test_browser_errors_are_logged_without_values(profile: Path, caplog: pytest.LogCaptureFixture) -> None:
    jar = CookieJar(profile, Wall())
    with caplog.at_level(logging.WARNING, logger="draft_studio"):
        jar.save(FakeCdp([cookie("sid", "hunter2")], fail=True))
        jar.save(FakeCdp([cookie("sid", "hunter2")]))
        assert jar.load(FakeCdp(fail=True)) == 0
    assert "hunter2" not in caplog.text and caplog.text.count("cookie jar") == 2


def test_a_file_that_cannot_be_written_is_logged(
    profile: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with caplog.at_level(logging.WARNING, logger="draft_studio"):
        CookieJar(profile, Wall()).save(FakeCdp([cookie("sid", "hunter2")]))
    assert "cookie jar" in caplog.text and "hunter2" not in caplog.text
    assert list(profile.glob("*.tmp")) == []
