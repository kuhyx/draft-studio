"""A walkthrough over a fake page: one run, ordered shots, fresh (logged-out) shots, scoped aims."""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.runner import run, run_all
from draft_studio.capture.spec import Denial, Grant, parse
from tests.capture_fakes import FakePage

MAIN = "https://panel.example/your-data/shares"
LOGIN = "https://panel.example/security/login"
ROW = "GABINET"
IMPERSONATE = ("POST", "https://panel.example/api/impersonate")
ZALOGUJ = {"text": "ZALOGUJ JAKO", "within": ROW}
DOMENY = {"text": "Domeny", "within": "Twoje usługi"}
MORE = {"role": "button", "within": "kaliszpsycholog.pl", "nth": -1}


def walk() -> object:
    return parse(
        {
            "url": MAIN,
            "name": "renew",
            "steps": [
                {"shot": {"name": "login", "fresh": True, "url": LOGIN}},
                {"click": ZALOGUJ},
                {"shot": {"name": "dashboard", "highlight": DOMENY}},
                {"click": DOMENY},
                {"shot": {"name": "domains", "highlight": MORE}},
                {"click": MORE},
                {"shot": {"name": "menu", "highlight": "Odnów"}},
            ],
        }
    )


def panel() -> FakePage:
    return FakePage(
        ["Odnów", "Domeny", "Domeny"],
        effects={"ZALOGUJ JAKO": [IMPERSONATE]},
        scopes={
            ROW: ["ZALOGUJ JAKO", "ZAKOŃCZ UDOSTĘPNIANIE"],
            "Twoje usługi": ["Pulpit", "Domeny"],
            "kaliszpsycholog.pl": ["Szczegóły", "btn:more"],
        },
    )


def test_the_impersonation_post_is_asked_once_then_every_shot_comes_from_one_run() -> None:
    page = panel()
    with pytest.raises(errors.NeedsPermission) as err:
        run_all(page, walk(), [])
    assert (err.value.step, err.value.reason, err.value.blocked) == (1, "request", [IMPERSONATE])
    page = panel()
    shots = run_all(page, walk(), [Grant(1, click=True, requests=(IMPERSONATE,))])
    assert [name for name, _ in shots] == ["login", "dashboard", "domains", "menu"]
    assert page.calls == [
        ("goto", MAIN),
        ("fresh",),
        ("goto", LOGIN),
        ("shot",),
        ("clear",),
        ("unfresh",),
        ("click", "ZALOGUJ JAKO", True, [IMPERSONATE]),
        ("highlight", "Domeny"),
        ("shot",),
        ("clear",),
        ("click", "Domeny", False, []),
        ("highlight", "btn:more"),
        ("shot",),
        ("clear",),
        ("click", "btn:more", False, []),
        ("highlight", "Odnów"),
        ("shot",),
        ("clear",),
    ]


def test_end_share_in_the_row_is_never_clicked() -> None:
    spec = parse(
        {"url": MAIN, "name": "w", "steps": [{"click": {"text": "ZAKOŃCZ UDOSTĘPNIANIE", "within": ROW}}]}
    )
    page = panel()
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec, [])
    assert (err.value.reason, err.value.detail) == ("deny-list", "zakoncz udostepnianie")
    assert not any(c[0] == "click" for c in page.calls)


def test_a_fresh_shot_of_a_login_page_is_not_a_login_stop_but_the_session_is_checked() -> None:
    spec = parse({"url": LOGIN, "name": "w", "steps": [{"shot": {"name": "a", "fresh": True, "url": LOGIN}}]})
    assert [n for n, _ in run_all(FakePage([]), spec)] == [
        "a"
    ]  # aimed at: kuhy's step "screenshot of the login page"
    spec = parse({"url": MAIN, "name": "w", "steps": [{"goto": LOGIN}, {"shot": {"name": "a"}}]})
    with pytest.raises(errors.LoginRequired):
        run_all(FakePage([]), spec)


def test_a_denied_click_mid_walk_keeps_earlier_shots_and_outlines_the_target_as_the_next_shot() -> None:
    page = panel()
    denial = Denial(1, click=False, requests=(IMPERSONATE,))
    shots = run_all(page, walk(), [], [denial])  # the POST stays blocked; the walk goes on
    assert [n for n, _ in shots] == ["login", "dashboard", "domains", "menu"]
    spec = parse(
        {
            "url": MAIN,
            "name": "w",
            "steps": [
                {"shot": {"name": "a"}},
                {"click": "Usuń"},
                {"shot": {"name": "b", "fresh": True, "url": LOGIN}},
                {"shot": {"name": "c"}},
                {"shot": {"name": "d"}},
            ],
        }
    )
    page = FakePage(["Usuń"])
    shots = run_all(page, spec, [], [Denial(1, click=True)])
    assert [n for n, _ in shots] == ["a", "c"] and ("highlight", "Usuń") in page.calls
    assert not any(c[0] == "click" for c in page.calls)
    tail = parse({"url": MAIN, "name": "w", "steps": [{"shot": {"name": "a"}}, {"click": "Usuń"}]})
    assert [n for n, _ in run_all(FakePage(["Usuń"]), tail, [], [Denial(1, click=True)])] == ["a"]


def test_scope_anchor_missing_or_repeated_and_ambiguous_matches_carry_what_was_seen() -> None:
    def fails(aim: object, page: FakePage) -> errors.CaptureError:
        spec = parse({"url": MAIN, "name": "w", "steps": [{"shot": {"name": "a", "highlight": aim}}]})
        with pytest.raises(errors.CaptureError) as err:
            run_all(page, spec)
        return err.value

    err = fails({"text": "Domeny", "within": "Nie ma"}, panel())
    assert err.code == "no-target" and err.extra == {"step": 0, "text": "Nie ma", "seen": ["Odnów", "Domeny"]}
    page = panel()
    page.anchor_count[ROW] = 2
    err = fails(ZALOGUJ, page)
    assert err.code == "ambiguous-target" and err.extra == {"step": 0, "text": ROW, "count": 2, "seen": []}
    err = fails("Domeny", panel())
    assert err.extra == {"step": 0, "text": "Domeny", "count": 2, "seen": ["near Domeny", "near Domeny"]}
    nth = parse(
        {
            "url": MAIN,
            "name": "w",
            "steps": [{"shot": {"name": "a", "highlight": {"text": "Domeny", "nth": 2}}}],
        }
    )
    assert [n for n, _ in run_all(panel(), nth)] == ["a"]  # an ordinal among exact matches is unique


def test_legacy_highlight_errors_keep_step_none() -> None:
    spec = parse({"url": MAIN, "name": "w", "highlight": "Domeny"})
    with pytest.raises(errors.Ambiguous) as err:
        run(FakePage(["Domeny", "Domeny"]), spec, [])
    assert err.value.extra["step"] is None


# A guide's first step is a screenshot OF the login page (kuhy, 2026-11-27: "cf-odnow-logowanie"):
# aiming at a login page is not a need to log in.
DASH = "https://panel.example/dashboard"
ROOT = "https://panel.example/"  # where a session walk of the login URL starts


def login_walk(*steps: object) -> object:
    return parse(
        {"url": LOGIN, "name": "guide", "steps": [{"shot": {"name": "login", "highlight": None}}, *steps]}
    )


def test_a_walk_that_starts_at_the_login_page_shoots_it_logged_out() -> None:
    page = FakePage([], redirects={LOGIN: DASH})  # the site sends a logged-in session to the dashboard
    shots = run_all(page, login_walk(), [])
    assert [n for n, _ in shots] == ["login"]
    assert page.calls == [("fresh",), ("goto", LOGIN), ("shot",), ("clear",), ("unfresh",)]


def test_the_session_follows_after_the_login_shot() -> None:
    page = FakePage(["Domeny"], redirects={ROOT: DASH})
    shots = run_all(page, login_walk({"click": "Domeny"}, {"shot": {"name": "next", "highlight": None}}), [])
    assert [n for n, _ in shots] == ["login", "next"]
    assert page.calls[:6] == [
        ("fresh",),
        ("goto", LOGIN),
        ("shot",),
        ("clear",),
        ("unfresh",),
        ("goto", ROOT),
    ]
    assert page.where == DASH


def test_a_logged_out_session_still_asks_for_the_login_after_its_shot() -> None:
    page = FakePage(["Domeny"], redirects={ROOT: LOGIN})  # the site shows the login page again
    with pytest.raises(errors.LoginRequired) as err:
        run_all(page, login_walk({"click": "Domeny"}), [])
    assert err.value.extra == {"origin": "https://panel.example", "url": LOGIN}
    assert ("shot",) in page.calls  # the login shot had been taken first


def test_a_click_on_the_login_url_before_any_shot_still_needs_the_login() -> None:
    page = FakePage(["Domeny"], redirects={ROOT: LOGIN})
    spec = parse({"url": LOGIN, "name": "g", "steps": [{"click": "Domeny"}, {"shot": {"name": "s"}}]})
    with pytest.raises(errors.LoginRequired):
        run_all(page, spec, [])
    assert ("fresh",) not in page.calls


def test_a_plain_page_that_lands_on_a_login_page_still_needs_the_login() -> None:
    page = FakePage([], redirects={MAIN: LOGIN})
    with pytest.raises(errors.LoginRequired) as err:
        run_all(page, parse({"url": MAIN, "name": "g", "steps": [{"shot": {"name": "s"}}]}), [])
    assert err.value.extra["origin"] == "https://panel.example"


def test_a_lone_screenshot_of_a_login_url_is_taken_logged_out() -> None:
    page = FakePage([], redirects={LOGIN: DASH})
    assert run(page, parse({"url": LOGIN, "name": "login"}), []) == b"\x89PNG"
    assert page.calls[0] == ("fresh",)
