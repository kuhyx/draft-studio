"""The capture run over a fake page: steps, refusals, grants, login detection."""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.runner import run
from draft_studio.capture.spec import Grant, parse
from tests.capture_fakes import FakePage


def spec(*steps: dict[str, str], highlight: str | None = "Done") -> object:
    return parse({"url": "https://site.example/", "name": "s", "steps": list(steps), "highlight": highlight})


def test_steps_run_then_highlight_and_shot() -> None:
    page = FakePage(["Domeny", "Lista", "Done"])
    assert run(page, spec({"click": "Domeny"}, {"wait": "Lista"}), []) == b"\x89PNG"
    assert page.calls == [
        ("goto", "https://site.example/"),
        ("click", "Domeny", False, []),
        ("highlight", "Done"),
        ("shot",),
        ("clear",),
    ]


def test_no_highlight() -> None:
    page = FakePage([])
    run(page, spec(highlight=None), [])
    assert ("shot",) in page.calls and not any(c[0] == "highlight" for c in page.calls)


def test_deny_listed_click_needs_permission_with_a_highlighted_shot() -> None:
    page = FakePage(["Zapłać", "Done"])
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec({"click": "Zapłać"}), [])
    assert (err.value.step, err.value.reason, err.value.detail) == (0, "deny-list", "zaplac")
    assert err.value.shot == b"\x89PNG" and not any(c[0] == "click" for c in page.calls)
    assert page.calls[-3:] == [("highlight", "Zapłać"), ("shot",), ("clear",)]


def test_click_grant_lets_the_click_through_but_not_its_post() -> None:
    page = FakePage(["Zapłać", "Done"], {"Zapłać": [("POST", "https://site.example/pay")]})
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec({"click": "Zapłać"}), [Grant(step=0, click=True)])
    assert err.value.reason == "request" and err.value.blocked == [("POST", "https://site.example/pay")]
    assert err.value.detail == "POST https://site.example/pay"


def test_request_grant_allows_that_request_on_that_step_only() -> None:
    post = ("POST", "https://site.example/api")
    page = FakePage(["Send", "Again", "Done"], {"Send": [post], "Again": [post]})
    grant = Grant(step=0, click=False, requests=(post,))
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec({"click": "Send"}, {"click": "Again"}), [grant])
    assert err.value.step == 1
    assert ("click", "Send", False, [post]) in page.calls


def test_blocked_form_submit_is_its_own_reason() -> None:
    page = FakePage(["Go", "Done"], {"Go": [("SUBMIT", "https://site.example/f"), ("POST", "https://x/")]})
    with pytest.raises(errors.NeedsPermission) as err:
        run(page, spec({"click": "Go"}), [])
    assert err.value.reason == "form-submit" and err.value.detail == "SUBMIT https://site.example/f"
    page = FakePage(["Go", "Done"], {"Go": [("SUBMIT", "https://site.example/f")]})
    assert run(page, spec({"click": "Go"}), [Grant(step=0, click=True)]) == b"\x89PNG"


def test_refusal_shot_survives_a_vanished_target() -> None:
    page = FakePage(["Kup"])
    page.highlight_ok = False
    with pytest.raises(errors.NeedsPermission):
        run(page, spec({"click": "Kup"}), [])
    assert page.calls[-2:] == [("shot",), ("clear",)]


@pytest.mark.parametrize(
    ("texts", "exc", "fields"),
    [
        ([], errors.NoTarget, {"step": 0, "text": "Domeny", "seen": []}),
        (
            ["Domeny", "Domeny"],
            errors.Ambiguous,
            {"step": 0, "text": "Domeny", "count": 2, "seen": ["near Domeny", "near Domeny"]},
        ),
    ],
)
def test_click_target_must_be_unique(texts: list[str], exc: type[errors.CaptureError], fields: dict) -> None:
    with pytest.raises(exc) as err:
        run(FakePage(texts), spec({"click": "Domeny"}), [])
    assert err.value.status == 422 and err.value.extra == fields


def test_wait_and_highlight_misses_are_named() -> None:
    with pytest.raises(errors.NoTarget) as err:
        run(FakePage([]), spec({"wait": "Lista"}), [])
    assert err.value.extra == {"step": 0, "text": "Lista", "seen": []}
    with pytest.raises(errors.NoTarget) as err:
        run(FakePage([]), spec(), [])
    assert err.value.extra == {"step": None, "text": "Done", "seen": []}


def test_login_page_after_navigation_or_click() -> None:
    page = FakePage([])
    page.password = True
    with pytest.raises(errors.LoginRequired) as err:
        run(page, spec(), [])
    assert err.value.extra == {"origin": "https://site.example", "url": "https://site.example/"}
    with pytest.raises(errors.LoginRequired):
        run(FakePage(["Login", "Done"]), spec({"click": "Login"}), [])


def test_error_codes() -> None:
    assert (errors.ProfileBusy().status, errors.ProfileBusy().code) == (409, "profile-busy")
    assert (errors.NoChromium().status, errors.CaptureTimeout().status) == (503, 504)
    assert errors.ChromiumFailed("boom").extra == {"detail": "boom"}
    assert (errors.Covered("x").status, errors.Covered("x").code) == (422, "covered-target")
