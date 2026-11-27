"""Dedicated workers on real headless chromium: the capture runs, and their writes are still refused.

Chromium attaches a dedicated worker as its own "worker" target, which has no Fetch domain:
Fetch.enable there answered "'Fetch.enable' wasn't found" and failed every capture of a page
that starts one (Google Maps, 2026-11-27). The owning frame's interception covers its requests.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.pngread import red_pixels

pytestmark = pytest.mark.chromium
SAVE = "POST /api/worker-save"


def test_page_with_dedicated_workers_is_captured_and_their_writes_refused(site: Site, rig: Rig) -> None:
    spec = {
        "url": f"{site.base}/worker.html",
        "name": "workers",
        "steps": [{"wait": "Wątki zablokowane"}, {"click": "Uruchom wątek"}, {"wait": "Wątek odczytał"}],
        "highlight": "Uruchom wątek",
    }
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 200, body
    assert site.writes["POST /api/worker"] == site.writes["POST /api/nested"] == 0


def start_worker_write(site: Site, rig: Rig) -> tuple[dict[str, Any], dict[str, Any]]:
    """A click starts a worker that POSTs: the spec and the permission request the capture answers with."""
    spec = {
        "url": f"{site.base}/worker.html?save",
        "name": "worker-save",
        "steps": [{"click": "Uruchom wątek"}],
        "highlight": "Uruchom wątek",
    }
    for _ in range(3):  # a starved renderer can run the click handler after the capture went quiet
        status, body = rig.service.capture(rig.draft, spec)
        if status == 409:
            break
    assert status == 409 and body["error"] == "needs-permission", body
    return spec, body["request"]


def test_worker_write_asks_then_is_sent_exactly_once_when_granted(site: Site, rig: Rig) -> None:
    before = site.writes[SAVE]  # the site outlives the test
    _, req = start_worker_write(site, rig)
    assert (req["reason"], req["detail"]) == ("request", f"POST {site.base}/api/worker-save")
    assert site.writes[SAVE] == before
    status, body = rig.service.grant(req["id"])
    assert status == 200, body
    assert site.writes[SAVE] == before + 1
    assert rig.service.grant(req["id"]) == (409, {"error": "not-pending"})


def test_worker_write_denied_is_never_sent_and_the_shot_is_taken(site: Site, rig: Rig) -> None:
    before = site.writes[SAVE]
    _, req = start_worker_write(site, rig)
    status, body = rig.service.deny(req["id"])
    assert status == 200, body
    assert red_pixels(rig.store.asset(rig.draft, body["asset"])[0])[2] > 3000  # the highlight is drawn
    assert site.writes[SAVE] == before
