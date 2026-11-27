"""Capture against real headless chromium: clicks, highlight pixels, blocked POST, login, profile."""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.keep import asset
from tests.capture_live.pngread import red_pixels
from tests.e2e.launch import chromium

pytestmark = pytest.mark.chromium


def test_clicks_wait_and_highlight(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/index.html",
        "name": "domains",
        "steps": [{"click": "Domeny"}, {"wait": "Lista domen"}],
        "highlight": "DNS",
    }
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 200, body
    width, height, red = red_pixels(asset(rig, body["asset"]))
    assert (width, height) == (2560, 1600)
    assert red > 3000, red  # outline + arrow at device scale 2
    status, body = rig.service.capture(rig.draft, {**spec, "name": "plain", "highlight": None})
    assert status == 200 and red_pixels(asset(rig, body["asset"]))[2] == 0  # overlay removed, page has no red


def test_post_is_blocked_then_granted_exactly_once(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/index.html",
        "name": "save",
        "steps": [{"click": "Zapisz"}],
        "highlight": "Zapisz",
    }
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 409 and body["error"] == "needs-permission", body
    req = body["request"]
    assert (req["reason"], req["detail"]) == ("request", f"POST {site.base}/api/save")
    assert site.writes["POST /api/save"] == 0
    assert red_pixels(rig.service.pending.shot(req["id"]))[2] > 3000

    status, body = rig.service.grant(req["id"])
    assert status == 200, body
    assert site.writes["POST /api/save"] == 1
    assert rig.service.grant(req["id"]) == (409, {"error": "not-pending"})

    status, body = rig.service.capture(rig.draft, spec)
    assert status == 409 and body["request"]["reason"] == "request"
    assert site.writes["POST /api/save"] == 1


def test_login_page_is_named(site: Site, rig: Rig, quick_login: None) -> None:
    status, body = rig.service.capture(
        rig.draft, {"url": f"{site.base}/index.html", "name": "acct", "steps": [{"click": "Konto"}]}
    )
    assert (status, body["error"], body["origin"]) == (409, "login-required", site.base)


def test_cookies_persist_in_the_profile_between_captures(site: Site, rig: Rig) -> None:
    def capture(query: str, expect: str) -> int:
        spec = {"url": f"{site.base}/session.html{query}", "name": "s", "steps": [{"wait": expect}]}
        return rig.service.capture(rig.draft, spec)[0]

    assert capture("", "Brak sesji") == 200
    assert capture("?start", "Sesja aktywna") == 200
    assert capture("", "Sesja aktywna") == 200  # a new chromium process, same profile
    assert (rig.profile / "Default" / "Cookies").is_file()


def test_profile_held_by_another_chromium_is_busy(site: Site, rig: Rig) -> None:
    # Stands in for the headed login window (headless here: never kuhy's display).
    rig.profile.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [chromium(), "--headless=new", f"--user-data-dir={rig.profile}", "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if (rig.profile / "SingletonLock").is_symlink():
                break
            time.sleep(0.05)
        spec = {"url": f"{site.base}/index.html", "name": "busy"}
        assert rig.service.capture(rig.draft, spec) == (409, {"error": "profile-busy"})
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    assert rig.service.capture(rig.draft, spec)[0] == 200
