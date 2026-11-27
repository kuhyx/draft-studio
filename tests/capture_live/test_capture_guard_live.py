"""Capture against real headless chromium: deny list, CORS, popups and frames, writes at shutdown."""

from __future__ import annotations

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.keep import KEEP
from tests.capture_live.pngread import red_pixels

pytestmark = pytest.mark.chromium


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
    status, body = rig.service.capture(rig.draft, spec)
    assert status == 409 and body["error"] == "needs-permission", body
    assert (body["request"]["reason"], body["request"]["detail"]) == ("request", f"OPTIONS {site.api}/save")
    assert site.writes["API OPTIONS /save"] == 0 and site.writes["API POST /save"] == 0


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
    assert site.writes["POST /api/bye"] == 0  # the closed popup's and frame's goodbye beacons


def test_writes_still_on_their_way_at_shutdown_are_refused_not_sent(site: Site, rig: Rig) -> None:
    status, body = rig.service.capture(
        rig.draft, {"url": f"{site.base}/leak.html", "name": "leak", "highlight": "Zapisz"}
    )
    assert status == 200, body
    assert site.writes["POST /api/leak"] == 0 and site.writes["POST /api/leak-beacon"] == 0
