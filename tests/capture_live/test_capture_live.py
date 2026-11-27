"""Capture against real headless chromium: clicks, highlight pixels, blocked POST, grants, login."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.pngread import green_share, red_pixels
from tests.e2e.launch import chromium

pytestmark = pytest.mark.chromium
KEEP = Path(os.environ.get("DS_CAPTURE_SHOTS") or Path.home() / "data/claude-scratch/ds-capture/shots")


def asset(rig: Rig, name: str) -> bytes:
    data, _ = rig.store.asset(rig.draft, name)
    KEEP.mkdir(parents=True, exist_ok=True)
    (KEEP / name).write_bytes(data)
    return data


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


def test_deny_list_and_form_submit_never_click(site: Site, rig: Rig) -> None:
    base = {"url": f"{site.base}/index.html", "name": "pay", "highlight": None}
    status, body = rig.service.capture(rig.draft, {**base, "steps": [{"click": "Zapłać"}]})
    assert status == 409 and (body["request"]["reason"], body["request"]["detail"]) == ("deny-list", "zaplac")
    asset_shot = rig.service.pending.shot(body["request"]["id"])
    KEEP.mkdir(parents=True, exist_ok=True)
    (KEEP / "refused-zaplac.png").write_bytes(asset_shot)
    status, body = rig.service.capture(rig.draft, {**base, "steps": [{"click": "Wyślij"}]})
    assert status == 409 and body["request"]["reason"] == "submit"
    status, body = rig.service.capture(
        rig.draft, {**base, "steps": [{"click": "Pokaż szczegóły"}, {"wait": "Szczegóły widoczne"}]}
    )
    assert status == 200, body
    assert site.writes["POST /api/pay"] == 0 and site.writes["POST /form"] == 0


def test_cors_preflight_for_a_read_goes_through(site: Site, rig: Rig) -> None:
    spec = {"url": f"{site.base}/cors.html?api={site.api}", "name": "cors", "steps": [{"wait": "Liczba 7"}]}
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 200, body
    assert site.writes["API OPTIONS /data"] >= 1 and site.writes["API GET /data"] >= 1
    assert site.writes["API POST /save"] == 0


def test_cors_preflight_for_a_write_stays_refused(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/cors.html?api={site.api}",
        "name": "cors-save",
        "steps": [{"click": "Przekaz dane"}],
        "highlight": "Przekaz dane",
    }
    for _ in range(3):  # a starved renderer can run the click handler after the capture went quiet
        status, body = rig.service.capture(rig.draft, spec)
        if status == 409:
            break
    assert status == 409 and body["error"] == "needs-permission", body
    assert (body["request"]["reason"], body["request"]["detail"]) == ("request", f"OPTIONS {site.api}/save")
    assert site.writes["API OPTIONS /save"] == 0 and site.writes["API POST /save"] == 0


def test_login_page_is_named(site: Site, rig: Rig) -> None:
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


def test_popups_and_cross_site_frames_cannot_post(site: Site, rig: Rig) -> None:
    spec = {"url": f"{site.base}/escape.html", "name": "escape", "steps": [{"click": "Otwórz okno"}]}
    status, body = rig.service.capture(rig.draft, spec)
    assert (site.writes["POST /api/frame"], site.writes["POST /api/popup"]) == (0, 0), (status, body)
    # The popup takes the foreground; our tab must still answer (highlight, screenshot), not hang to 504.
    assert status == 409 and body["error"] == "needs-permission", body
    req = body["request"]
    assert (req["reason"], req["detail"]) == ("request", f"POST {site.base}/api/popup")
    assert red_pixels(rig.service.pending.shot(req["id"]))[2] > 3000
    assert rig.service.grant(req["id"])[0] == 200
    assert (site.writes["POST /api/frame"], site.writes["POST /api/popup"]) == (0, 1)


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


def test_followed_tab_keeps_deny_list_submit_block_and_login(site: Site, rig: Rig) -> None:
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
