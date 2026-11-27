"""A session walk never starts ON a login page, and a login route that moves on by itself is no login stop.

Incident (kuhy, 2026-11-27): a held walk of `https://panel.cyberfolks.pl/security/login` kept failing
login-required although kuhy was logged in: the run began with `goto(login URL)`, and the panel (an SPA
with its auth in localStorage) shows its login route for a moment before it redirects client-side.
"""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.runner import run_all
from draft_studio.capture.spec import Spec, parse
from tests.capture_fakes import FakePage

ORIGIN = "https://panel.cyberfolks.pl"
LOGIN = f"{ORIGIN}/security/login"
SHARES = f"{ORIGIN}/your-data/shares"
DASH = f"{ORIGIN}/dashboard"
ZALOGUJ = {"text": "ZALOGUJ JAKO", "nth": 1}


def held_walk() -> Spec:
    """The plan held before the fix: session captures all repeat the login URL as their start."""
    return parse(
        {
            "url": LOGIN,
            "name": "cf-odnow-logowanie",
            "steps": [
                {"shot": {"name": "cf-odnow-logowanie", "highlight": None, "fresh": True, "url": LOGIN}},
                {"goto": SHARES},
                {"click": ZALOGUJ},
                {"wait": "Domeny"},
                {"shot": {"name": "cf-odnow-domeny", "highlight": None}},
                {"click": "Domeny"},
                {"shot": {"name": "cf-odnow-menu", "highlight": None}},
            ],
        }
    )


def gotos(page: FakePage) -> list[str]:
    return [str(c[1]) for c in page.calls if c[0] == "goto"]


def test_the_held_plan_starts_at_its_first_goto_not_the_login_page() -> None:
    page = FakePage(["ZALOGUJ JAKO", "Domeny"])
    shots = run_all(page, held_walk(), [])
    assert [n for n, _ in shots] == ["cf-odnow-logowanie", "cf-odnow-domeny", "cf-odnow-menu"]
    assert gotos(page) == [LOGIN, SHARES]  # the login URL once: the logged-out shot, in its own context
    assert page.calls[:6] == [
        ("fresh",),
        ("goto", LOGIN),
        ("shot",),
        ("clear",),
        ("unfresh",),
        ("goto", SHARES),
    ]


def test_a_session_walk_whose_first_step_is_a_click_starts_at_the_origin_root() -> None:
    walk = parse(
        {
            "url": LOGIN,
            "name": "w",
            "steps": [
                {"shot": {"name": "login", "highlight": None, "fresh": True, "url": LOGIN}},
                {"click": "Domeny"},
                {"shot": {"name": "next", "highlight": None}},
            ],
        }
    )
    page = FakePage(["Domeny"])
    assert [n for n, _ in run_all(page, walk, [])] == ["login", "next"]
    assert gotos(page) == [LOGIN, f"{ORIGIN}/"]


def test_a_session_run_that_still_lands_on_the_login_page_needs_the_login() -> None:
    page = FakePage(["Domeny"], redirects={SHARES: LOGIN})  # logged out: the site sends it to the login
    with pytest.raises(errors.LoginRequired) as err:
        run_all(page, held_walk(), [])
    assert err.value.extra == {"origin": ORIGIN, "url": LOGIN}


def test_a_login_route_that_redirects_client_side_counts_as_logged_in() -> None:
    walk = parse({"url": SHARES, "name": "s", "steps": [{"wait": "Domeny"}], "highlight": None})
    page = FakePage(["Domeny"], redirects={SHARES: LOGIN}, late={LOGIN: DASH})
    assert [n for n, _ in run_all(page, walk, [])] == ["s"]
    assert ("settle", 5.0) in page.calls


def test_a_login_page_that_stays_is_login_required_after_the_settle_window() -> None:
    walk = parse({"url": SHARES, "name": "s", "steps": [{"wait": "Domeny"}], "highlight": None})
    page = FakePage(["Domeny"], redirects={SHARES: LOGIN})
    with pytest.raises(errors.LoginRequired):
        run_all(page, walk, [])
    assert ("settle", 5.0) in page.calls


def test_a_password_field_that_goes_away_counts_as_logged_in() -> None:
    walk = parse({"url": SHARES, "name": "s", "steps": [], "highlight": None})
    page = FakePage([], late={SHARES: DASH})
    page.password = True
    assert [n for n, _ in run_all(page, walk, [])] == ["s"]
