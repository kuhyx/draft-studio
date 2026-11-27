"""A denial keeps one refusal blocked and lets the capture go on: runner behaviour over a fake page."""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.runner import run
from draft_studio.capture.spec import Denial, Grant, parse
from tests.capture_fakes import FakePage

POST = ("POST", "https://site.example/api/track")


def spec(*steps: dict[str, str], highlight: str | None = "Done") -> object:
    return parse({"url": "https://site.example/", "name": "s", "steps": list(steps), "highlight": highlight})


def test_denied_request_stays_blocked_and_the_capture_goes_on() -> None:
    page = FakePage(["Domeny", "Lista", "Done"], {"Domeny": [POST]})
    png = run(
        page,
        spec({"click": "Domeny"}, {"wait": "Lista"}),
        [],
        [Denial(step=0, click=False, requests=(POST,))],
    )
    assert png == b"\x89PNG"
    assert ("click", "Domeny", False, []) in page.calls  # never allowed: the page fails it again
    assert page.calls[-3:] == [("highlight", "Done"), ("shot",), ("clear",)]


def test_a_denial_holds_on_every_later_step_but_only_for_its_requests() -> None:
    other = ("POST", "https://site.example/api/other")
    page = FakePage(["A", "B", "Done"], {"A": [POST], "B": [POST, other]})
    denial = Denial(step=0, click=False, requests=(POST,))
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec({"click": "A"}, {"click": "B"}), [], [denial])
    assert (err.value.step, err.value.blocked) == (1, [other])  # POST (denied) is not asked again


def test_a_denied_form_submit_goes_on() -> None:
    submit = ("SUBMIT", "https://site.example/f")
    page = FakePage(["Go", "Done"], {"Go": [submit]})
    assert (
        run(page, spec({"click": "Go"}), [], [Denial(step=0, click=False, requests=(submit,))]) == b"\x89PNG"
    )


def test_denied_click_shoots_the_page_before_it_with_the_target_outlined() -> None:
    page = FakePage(["Zapłać", "Done"])
    png = run(page, spec({"click": "Zapłać"}, {"wait": "Done"}), [], [Denial(step=0, click=True)])
    assert png == b"\x89PNG"
    assert not any(c[0] == "click" for c in page.calls)
    assert page.calls[-3:] == [("highlight", "Zapłać"), ("shot",), ("clear",)]  # not the spec's highlight


def test_denied_click_skips_the_later_steps() -> None:
    page = FakePage(["Zapłać"])  # "Nowe" is never found: the later step is not run
    assert run(page, spec({"click": "Zapłać"}, {"click": "Nowe"}), [], [Denial(step=0, click=True)])


def test_denied_click_only_counts_on_its_own_step_and_a_grant_wins_nothing() -> None:
    page = FakePage(["Zapłać", "Done"])
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec({"click": "Zapłać"}), [], [Denial(step=3, click=True)])
    assert err.value.step == 0


def test_a_click_that_is_allowed_is_clicked_whatever_was_denied() -> None:
    page = FakePage(["Zapłać", "Done"])
    run(page, spec({"click": "Zapłać"}), [Grant(step=0, click=True)], [Denial(step=0, click=True)])
    assert ("click", "Zapłać", True, []) in page.calls
