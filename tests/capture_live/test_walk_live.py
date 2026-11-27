"""kuhy's cyber_Folks walkthrough on a loopback mimic: one run, one permission, four framed shots.

Login page (logged out), shares page (ZALOGUJ JAKO posts, then the owner's view;
ZAKOŃCZ UDOSTĘPNIANIE must never be clicked), dashboard with two "Domeny"
entries, domains table with an icon-only "⋮" opening a menu with "Odnów".
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.capture_live.conftest import Rig, Site
from tests.capture_live.pngread import decode

pytestmark = pytest.mark.chromium
SCALE = 2  # tabs.py: CSS px -> PNG px
ROW = "GABINET PSYCHOLOGICZNY ANNA LEŚNIAK-JARECKA"
DOMENY = {"text": "Domeny", "within": "Twoje usługi"}
MORE = {"role": "button", "within": "kaliszpsycholog.pl", "nth": -1}


def walk(base: str) -> dict[str, Any]:
    return {
        "url": f"{base}/walk/shares.html",
        "name": "renew",
        "steps": [
            {"shot": {"name": "login", "fresh": True, "url": f"{base}/walk/login.html"}},
            {"click": {"text": "ZALOGUJ JAKO", "within": ROW}},
            {"shot": {"name": "dashboard", "highlight": DOMENY}},
            {"click": DOMENY},
            {"shot": {"name": "domains", "highlight": MORE}},
            {"click": MORE},
            {"shot": {"name": "menu", "highlight": "Odnów"}},
        ],
    }


def share(png: bytes, box: tuple[int, int, int, int], hit: Any) -> int:
    """Pixels satisfying `hit` inside `box` (CSS px: left, top, right, bottom)."""
    width, _, bpp, px = decode(png)
    left, top, right, bottom = (v * SCALE for v in box)
    count = 0
    for row in range(top, bottom):
        line = px[row * width * bpp : (row + 1) * width * bpp]
        count += sum(1 for x in range(left, right) if hit(*line[x * bpp : x * bpp + 3]))
    return count


def red(r: int, g: int, b: int) -> bool:
    return r > 200 and g < 60 and b < 60


def framed(png: bytes, box: tuple[int, int, int, int]) -> int:
    """Red pixels in the band where the outline of an element at `box` is drawn (6 px pad, 4 px stroke)."""
    left, top, right, bottom = box
    return share(png, (left - 10, top - 10, right + 10, bottom + 10), red) - share(
        png, (left - 2, top - 2, right + 2, bottom + 2), red
    )


def capture(rig: Rig, spec: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return rig.service.capture(rig.draft, spec)


def test_the_walkthrough_needs_one_grant_and_shows_the_owner_view(site: Site, rig: Rig) -> None:
    seed = {"url": f"{site.base}/session.html?start", "name": "seed", "steps": [{"wait": "Sesja aktywna"}]}
    assert capture(rig, seed)[0] == 200
    status, body = capture(rig, walk(site.base))
    assert status == 409 and body["request"]["reason"] == "request", body
    assert body["request"]["detail"] == f"POST {site.base}/api/impersonate?id=08245109"
    assert [r["id"] for r in rig.service.list_pending()[1]["requests"]] == [body["request"]["id"]]
    status, body = rig.service.grant(body["request"]["id"])
    assert status == 200 and [s["name"] for s in body["shots"]] == [
        "login",
        "dashboard",
        "domains",
        "menu",
    ], body
    shots = {s["name"]: rig.store.asset(rig.draft, s["asset"])[0] for s in body["shots"]}

    assert share(shots["login"], (300, 600, 1200, 780), lambda r, g, b: (r, g, b) == (30, 60, 200)) > 300000
    assert share(shots["dashboard"], (600, 10, 1200, 50), lambda r, g, b: (r, g, b) == (250, 220, 0)) > 50000
    assert framed(shots["dashboard"], (40, 200, 200, 230)) > 1500  # "Domeny" under "Twoje usługi"
    assert framed(shots["dashboard"], (40, 500, 200, 530)) == 0  # not the one under "Twoje dane"
    assert framed(shots["domains"], (940, 228, 980, 260)) > 800  # the icon-only "⋮" of kaliszpsycholog.pl
    assert framed(shots["domains"], (940, 168, 980, 200)) < 200  # not inna-domena.pl's (only the arrow)
    assert framed(shots["menu"], (760, 300, 960, 332)) > 1500  # "Odnów"

    assert (
        site.writes["POST /api/impersonate?id=08245109"] == 1
        and site.writes["POST /api/impersonate?id=11111111"] == 0
    )
    assert site.writes["GET /beacon/end-share"] == 0 and site.writes["POST /api/end-share"] == 0
    assert site.writes["POST /api/login-beacon"] == 0 and site.writes["POST /api/renew"] == 0
    still = {"url": f"{site.base}/session.html", "name": "still", "steps": [{"wait": "Sesja aktywna"}]}
    assert capture(rig, still)[0] == 200  # the logged-out shot left the session alone


def test_end_share_is_refused_and_never_clicked(site: Site, rig: Rig) -> None:
    seed = {"url": f"{site.base}/session.html?start", "name": "seed", "steps": [{"wait": "Sesja aktywna"}]}
    assert capture(rig, seed)[0] == 200
    spec = {
        "url": f"{site.base}/walk/shares.html",
        "name": "end",
        "steps": [{"click": {"text": "ZAKOŃCZ UDOSTĘPNIANIE", "within": ROW}}],
    }
    status, body = capture(rig, spec)
    assert status == 409 and body["request"]["reason"] == "deny-list", body
    assert body["request"]["detail"] == "zakoncz udostepnianie"
    assert site.writes["GET /beacon/end-share"] == 0 and site.writes["POST /api/end-share"] == 0
