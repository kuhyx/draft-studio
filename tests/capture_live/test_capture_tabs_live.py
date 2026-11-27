"""Capture against real headless chromium: following the tab a click opens."""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.keep import asset
from tests.capture_live.pngread import green_share, red_pixels

pytestmark = pytest.mark.chromium


@pytest.mark.parametrize("opener", ["Otwórz kartę", "Karta w linku"], ids=["window-open", "target-blank"])
def test_capture_follows_the_tab_a_click_opens(site: Site, rig: Rig, opener: str) -> None:
    spec = {
        "url": f"{site.base}/tabs.html",
        "name": "tab",
        "steps": [{"click": opener}, {"click": "Pokaż kod"}, {"wait": "Kod widoczny"}],
        "highlight": "Pokaż kod",
    }
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 200, body
    png = asset(rig, body["asset"])
    width, height, red = red_pixels(png)
    assert (width, height) == (2560, 1600)  # the followed tab gets the same viewport
    assert red > 3000, red
    assert green_share(png) > 0.5  # the new tab is in the shot, not the opener


def test_followed_tab_requests_are_blocked_then_granted(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/tabs.html",
        "name": "tab-save",
        "steps": [{"click": "Otwórz kartę"}, {"click": "Zapisz w karcie"}],
    }
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 409 and body["error"] == "needs-permission", body
    req = body["request"]
    assert (req["step"], req["reason"], req["detail"]) == (1, "request", f"POST {site.base}/api/tab")
    shot = rig.service.pending.shot(req["id"])
    assert red_pixels(shot)[2] > 3000 and green_share(shot) > 0.5
    assert site.writes["POST /api/tab"] == 0
    assert rig.service.grant(req["id"])[0] == 200
    assert site.writes["POST /api/tab"] == 1


def test_followed_tab_keeps_deny_list_submit_block_and_login(site: Site, rig: Rig, quick_login: None) -> None:
    def capture(*steps: str) -> tuple[int, dict[str, Any]]:
        spec = {"url": f"{site.base}/tabs.html", "name": "tab-rules", "steps": [{"click": s} for s in steps]}
        return rig.service.capture(rig.draft, spec)

    status, body = capture("Otwórz kartę", "Zapłać w karcie")
    assert status == 409 and (body["request"]["reason"], body["request"]["detail"]) == ("deny-list", "zaplac")
    status, body = capture("Otwórz kartę", "Wyślij skryptem")
    assert status == 409, body
    assert (body["request"]["reason"], body["request"]["detail"]) == (
        "form-submit",
        f"SUBMIT {site.base}/tab-form",
    )
    status, body = capture("Zaloguj w karcie")
    assert (status, body["error"], body["origin"]) == (409, "login-required", site.base)
    assert site.writes["POST /api/tab-pay"] == 0 and site.writes["POST /tab-form"] == 0
