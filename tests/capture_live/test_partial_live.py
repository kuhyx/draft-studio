"""A partial grant on real chromium: a click fires two POSTs, kuhy allows one, the other is never sent."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.pngread import red_pixels

pytestmark = pytest.mark.chromium


def _tracker_card(site: Site, rig: Rig) -> dict[str, Any]:
    """The card of a click that fires two POSTs: same-origin /api/track and cross-origin <api>/track."""
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
    return dict(body["request"])


def test_a_partial_grant_sends_only_the_picked_post(site: Site, rig: Rig) -> None:
    before = Counter(site.writes)  # the site is shared by this module's tests
    card = _tracker_card(site, rig)
    same = ["POST", f"{site.base}/api/track"]
    assert tuple(same) in [tuple(b) for b in card["blocked"]], card["blocked"]
    status, body = rig.service.grant(card["id"], {"allow": [same]})
    assert status == 200, body  # the cross-origin POST stays blocked and does not card again
    assert red_pixels(rig.store.asset(rig.draft, body["asset"])[0])[2] > 3000  # the shot is taken
    sent = Counter(site.writes) - before
    assert sent["POST /api/track"] == 1
    assert sent["API POST /track"] == 0 and sent["API OPTIONS /track"] == 0
    assert rig.service.grant(card["id"]) == (409, {"error": "not-pending"})
    assert rig.service.grant("0" * 16, {"allow": [same]}) == (404, {"error": "not-found"})


def test_a_pair_that_was_not_blocked_is_refused_and_nothing_is_sent(site: Site, rig: Rig) -> None:
    before = {k: v for k, v in site.writes.items() if "GET" not in k}
    card = _tracker_card(site, rig)
    assert rig.service.grant(card["id"], {"allow": [["POST", f"{site.base}/api/other"]]}) == (
        400,
        {"error": "not-blocked"},
    )
    assert {k: v for k, v in site.writes.items() if "GET" not in k} == before  # no write reached the site
    assert [r["id"] for r in rig.service.list_pending()[1]["requests"]] == [card["id"]]


def test_the_preflight_picked_for_the_cross_origin_post_goes_out_and_the_other_post_stays_denied(
    site: Site, rig: Rig
) -> None:
    before = Counter(site.writes)
    card = _tracker_card(site, rig)
    preflight = ["OPTIONS", f"{site.api}/track"]
    status, body = rig.service.grant(card["id"], {"allow": [preflight]})
    assert status == 409 and [tuple(b) for b in body["request"]["blocked"]] == [
        ("POST", f"{site.api}/track")
    ], body  # the same-origin POST is not asked again
    status, body = rig.service.grant(body["request"]["id"])
    assert status == 200, body
    sent = Counter(site.writes) - before
    assert sent["API OPTIONS /track"] >= 1  # the preflight the first run was given (and the re-run's)
    assert (sent["API POST /track"], sent["POST /api/track"]) == (1, 0)
