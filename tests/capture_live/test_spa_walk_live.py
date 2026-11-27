"""A walk that names the login URL of a single-page panel, on real headless chromium.

The panel keeps its login in localStorage and renders its login route for a moment before it
redirects a logged-in session client-side (tests/capture_live/site/spa/index.html). A session walk
must not start on that route, and must not take the moment for a login stop.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.test_login_live import SPA_FORM, Window

pytestmark = pytest.mark.chromium


def walk(site: Site, steps: list[Any]) -> dict[str, object]:
    """What a held plan holds: the session start is the login URL, the first shot a logged-out one."""
    login = f"{site.base}/spa/security/login"
    shot = {"shot": {"name": "login", "highlight": None, "fresh": True, "url": login}}
    return {"url": login, "name": "guide", "steps": [shot, *steps]}


def logged_in(site: Site, rig: Rig) -> None:
    window = Window(site, rig, "/spa/", settle=0.5, unseen=3)
    window.kuhy(SPA_FORM)
    assert window.finished() == "logged-in"


DASH = {"shot": {"name": "dash", "highlight": None}}


def test_a_logged_in_walk_from_the_login_url_gets_its_shots(site: Site, rig: Rig) -> None:
    logged_in(site, rig)
    steps = [{"goto": f"{site.base}/spa/dashboard"}, {"wait": "Domeny"}, DASH]
    status, body = rig.service.capture(rig.draft, walk(site, steps))
    assert status == 200, body


def test_a_login_route_that_redirects_itself_is_no_login_stop(site: Site, rig: Rig) -> None:
    logged_in(site, rig)
    page = {"url": f"{site.base}/spa/security/login", "name": "p", "steps": [{"wait": "Domeny"}]}
    assert rig.service.capture(rig.draft, page)[0] == 200  # the redirect after the 1.5 s route render


def test_a_logged_out_walk_from_the_login_url_still_needs_the_login(site: Site, rig: Rig) -> None:
    steps = [{"goto": f"{site.base}/spa/dashboard"}, {"wait": "Domeny"}, DASH]
    status, body = rig.service.capture(rig.draft, walk(site, steps))
    assert (status, body["error"], body["origin"]) == (409, "login-required", site.base)
