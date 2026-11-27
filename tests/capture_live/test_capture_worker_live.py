"""Dedicated workers on real headless chromium: the capture runs, and their writes are still refused.

Chromium attaches a dedicated worker as its own "worker" target, which has no Fetch domain:
Fetch.enable there answered "'Fetch.enable' wasn't found" and failed every capture of a page
that starts one (Google Maps, 2026-11-27). The owning frame's interception covers its requests.
"""

from __future__ import annotations

import pytest

from tests.capture_live.conftest import Rig, Site

pytestmark = pytest.mark.chromium


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
