"""A skipped „ZALOGUJ JAKO” must leave the walk on the dashboard, not on the shares page (draft 462fb46c512a).

Like the real panel, the mimic's menu-shares page shows the side menu („Twoje usługi” > „Domeny”) in
the owner's view too, so the goal of the click holds right there. The click `lands` "home": the
second run opens the origin root (the dashboard) and frames „Domeny” there.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.test_walk_live import DOMENY, capture, framed

pytestmark = pytest.mark.chromium
LANDS = {"lands": "home"}  # without it (the legacy plan) the frame lands on ON_SHARES: the bug
ON_DASHBOARD = (40, 200, 200, 230)  # „Domeny” under „Twoje usługi” on walk/dashboard.html
ON_SHARES = (600, 460, 760, 490)  # the side menu's „Domeny” on the shares page


def walk(base: str) -> dict[str, Any]:
    return {
        "url": f"{base}/already/menu-shares.html",
        "name": "renew",
        "steps": [
            {"click": "ZALOGUJ JAKO", **LANDS},
            {"wait": "Twoje usługi"},
            {"shot": {"name": "dashboard", "highlight": DOMENY}},
        ],
    }


def test_a_skipped_click_lands_where_the_click_would_have(site: Site, rig: Rig) -> None:
    seed = {"url": f"{site.base}/session.html?start", "name": "seed", "steps": [{"wait": "Sesja aktywna"}]}
    assert capture(rig, seed)[0] == 200
    status, body = capture(rig, walk(site.base))
    assert status == 409, body
    status, body = rig.service.grant(body["request"]["id"])
    assert status == 200 and site.switched, body

    status, body = capture(rig, walk(site.base))  # owner's view: no button, the side menu still there
    assert status == 200, body
    png = rig.store.asset(rig.draft, body["shots"][0]["asset"])[0]
    assert framed(png, ON_SHARES) < 200, "the shot was taken on the shares page"
    assert framed(png, ON_DASHBOARD) > 1500
    assert sum(n for k, n in site.writes.items() if not k.startswith("GET ")) == 1
