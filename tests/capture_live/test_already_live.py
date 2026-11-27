"""A re-run that starts already switched (cyber_Folks keeps an impersonation for an hour).

The mimic's "log in as" button sets a server-side flag; from then on its shares page is the owner's
view (no button) and its root the dashboard. The second run must skip the click and still shoot all.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.test_walk_live import DOMENY, MORE, ROW, capture, framed

pytestmark = pytest.mark.chromium
NAMES = ["login", "dashboard", "domains"]


def walk(base: str) -> dict[str, Any]:
    return {
        "url": f"{base}/already/shares.html",
        "name": "renew",
        "steps": [
            {"shot": {"name": "login", "fresh": True, "url": f"{base}/walk/login.html"}},
            {"click": {"text": "ZALOGUJ JAKO", "within": ROW}},
            {"wait": "Domeny"},
            {"shot": {"name": "dashboard", "highlight": DOMENY}},
            {"click": DOMENY},
            {"shot": {"name": "domains", "highlight": MORE}},
        ],
    }


def test_a_second_run_that_starts_switched_skips_the_click_and_shoots_everything(
    site: Site, rig: Rig
) -> None:
    seed = {"url": f"{site.base}/session.html?start", "name": "seed", "steps": [{"wait": "Sesja aktywna"}]}
    assert capture(rig, seed)[0] == 200
    status, body = capture(rig, walk(site.base))
    assert status == 409 and body["request"]["detail"] == f"POST {site.base}/api/switch?id=08245109", body
    status, body = rig.service.grant(body["request"]["id"])
    assert status == 200 and [s["name"] for s in body["shots"]] == NAMES, body
    assert site.switched and site.writes["POST /api/switch?id=08245109"] == 1

    status, body = capture(rig, walk(site.base))  # the site is in the owner's view now: no button
    assert status == 200, body
    assert [s["name"] for s in body["shots"]] == NAMES
    png = rig.store.asset(rig.draft, body["shots"][1]["asset"])[0]
    assert framed(png, (40, 200, 200, 230)) > 1500  # "Domeny" under "Twoje usługi", on the dashboard
    assert site.writes["POST /api/switch?id=08245109"] == 1  # nothing was clicked, nothing sent again
    assert sum(n for k, n in site.writes.items() if not k.startswith("GET ")) == 1
