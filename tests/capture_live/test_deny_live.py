"""A denied request stays blocked (never sent) and the capture still returns its screenshot."""

from __future__ import annotations

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.pngread import red_pixels

pytestmark = pytest.mark.chromium


def test_denied_background_posts_are_never_sent_and_the_shot_is_taken(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/tracker.html?api={site.api}",
        "name": "tracker",
        "steps": [{"click": "Domeny"}, {"wait": "Lista domen"}],
        "highlight": "Lista domen",
    }
    for _ in range(3):  # a starved renderer can run the click handler after the capture went quiet
        status, body = rig.service.capture(rig.draft, spec)
        if status == 409:
            break
    assert status == 409 and body["error"] == "needs-permission", body
    req = body["request"]
    assert req["reason"] == "request"
    status, body = rig.service.deny(req["id"])
    assert status == 200, body
    assert red_pixels(rig.store.asset(rig.draft, body["asset"])[0])[2] > 3000  # the highlight is drawn
    assert site.writes["POST /api/track"] == 0 and site.writes["API POST /track"] == 0
    assert site.writes["API OPTIONS /track"] == 0
    assert rig.service.deny(req["id"]) == (409, {"error": "not-pending"})


def test_denied_click_shoots_the_page_before_it(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/index.html",
        "name": "pay",
        "steps": [{"click": "Zapłać"}, {"click": "Domeny"}],
        "highlight": "Domeny",
    }
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 409 and body["request"]["reason"] == "deny-list", body
    status, body = rig.service.deny(body["request"]["id"])
    assert status == 200, body
    shot = rig.store.asset(rig.draft, body["asset"])[0]
    assert red_pixels(shot)[2] > 3000  # the refused button is outlined
    assert site.writes["POST /api/pay"] == 0
