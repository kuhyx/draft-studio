"""A chat widget's preflight on real chromium: its card says which write it announces."""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site

pytestmark = pytest.mark.chromium


def _spec(site: Site) -> dict[str, Any]:
    return {
        "url": f"{site.base}/tracker.html?api={site.api}",
        "name": "tracker",
        "steps": [{"click": "Domeny"}, {"wait": "Lista domen"}],
        "highlight": "Lista domen",
    }


def _card(site: Site, rig: Rig) -> dict[str, Any]:
    """The card of tracker.html's click: a same-origin POST and a cross-origin JSON POST (preflighted)."""
    for _ in range(3):  # a starved renderer can run the click handler after the capture went quiet
        status, body = rig.service.capture(rig.draft, _spec(site))
        if status == 409:
            break
    assert status == 409 and body["error"] == "needs-permission", body
    return dict(body["request"])


def test_the_preflight_entry_announces_the_post(site: Site, rig: Rig) -> None:
    card = _card(site, rig)
    pairs = [tuple(b) for b in card["blocked"]]
    pre, same = ("OPTIONS", f"{site.api}/track"), ("POST", f"{site.base}/api/track")
    assert pre in pairs and same in pairs, pairs
    assert len(card["announces"]) == len(pairs)
    assert card["announces"][pairs.index(pre)] == "POST"
    assert card["announces"][pairs.index(same)] is None
