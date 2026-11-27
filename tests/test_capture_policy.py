"""Policy: spec validation, the click deny-list, login-page detection."""

from __future__ import annotations

import pytest

from draft_studio.capture import policy
from draft_studio.capture.aim import Aim
from draft_studio.capture.spec import Grant, SpecError, Step, parse


def target(**kw: object) -> policy.Target:
    base: dict[str, object] = {"ref": 0, "tag": "button", "text": "Dalej"}
    base.update(kw)
    return policy.Target.from_js(base)


@pytest.mark.parametrize(
    "label",
    [
        "Zapłać",
        "ZAPŁAĆ teraz",
        "Płacę",
        "Zamawiam",
        "Zamów",
        "Kup",
        "Opłać",
        "Usuń",
        "Anuluj",
        "Zakończ udostępnianie",
        "ZAKOŃCZ UDOSTĘPNIANIE",
        "End share",
        "End sharing",
        "Stop sharing",
        "Wyloguj",
        "Potwierdź",
        "Pay now",
        "Orders",
        "Buy",
        "Delete",
        "Cancel",
        "Remove",
        "Confirm",
        "Submit",
        "Logout",
        "Log out",
        "Sign out",
    ],
)
def test_deny_list_refuses(label: str) -> None:
    refusal = policy.refusal(target(text=label))
    assert refusal is not None and refusal.reason == "deny-list"


@pytest.mark.parametrize("field", ["aria", "title", "value"])
def test_deny_list_reads_aria_title_and_value(field: str) -> None:
    refusal = policy.refusal(target(text="", **{field: "Usuń domenę"}))
    assert refusal is not None and refusal.detail == "usun"


@pytest.mark.parametrize(
    "label", ["Dalej", "Domeny", "Pliki", "Ustawienia DNS", "repayment-free", "Display", "Border"]
)
def test_harmless_labels_pass(label: str) -> None:
    assert policy.refusal(target(text=label)) is None


def test_submit_and_fields_are_refused() -> None:
    assert policy.refusal(target(submit=True)) == policy.Refusal("submit", "button")
    assert policy.refusal(target(tag="input", field=True)) == policy.Refusal("input", "input")


def test_normalize_strips_diacritics_and_case() -> None:
    assert policy.normalize("  ZAPŁAĆ\n  Ł ") == "zaplac l"


@pytest.mark.parametrize(
    ("url", "password", "login"),
    [
        ("https://panel.example/login", False, True),
        ("https://panel.example/logowanie?next=/", False, True),
        ("https://panel.example/auth/", False, True),
        ("https://panel.example/domains", True, True),
        ("https://panel.example/domains", False, False),
        ("https://panel.example/blog/login-tips", False, False),
    ],
)
def test_login_page(url: str, password: bool, login: bool) -> None:
    assert policy.is_login_page(url, password) is login


def test_origin() -> None:
    assert policy.origin("https://panel.example:8443/a/b?c") == "https://panel.example:8443"


def test_parse_full_spec() -> None:
    spec = parse(
        {
            "url": "https://panel.example/",
            "steps": [{"click": "Domeny"}, {"wait": "Lista domen"}],
            "highlight": "DNS",
            "name": "dns-step-1",
        }
    )
    assert spec.steps == (Step("click", "Domeny"), Step("wait", "Lista domen"))
    assert spec.highlight == Aim("DNS")
    assert parse(spec.to_json()) == spec


def test_parse_minimal_spec() -> None:
    spec = parse({"url": "http://127.0.0.1:1/x", "name": "a"})
    assert spec.steps == () and spec.highlight is None


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ([], "body"),
        ({"url": "file:///etc/passwd", "name": "a"}, "url"),
        ({"url": "javascript:alert(1)", "name": "a"}, "url"),
        ({"url": 3, "name": "a"}, "url"),
        ({"url": "https://x/", "name": "Bad Name"}, "name"),
        ({"url": "https://x/", "name": "a", "steps": {}}, "steps"),
        ({"url": "https://x/", "name": "a", "steps": [{"type": "x"}]}, "step-0"),
        ({"url": "https://x/", "name": "a", "steps": [{"click": "a", "wait": "b"}]}, "step-0"),
        ({"url": "https://x/", "name": "a", "steps": [{"click": "  "}]}, "step-0"),
        ({"url": "https://x/", "name": "a", "steps": ["click"]}, "step-0"),
        ({"url": "https://x/", "name": "a", "highlight": 4}, "highlight"),
        ({"url": "https://x/", "name": "a", "steps": [{"click": "x"}] * 41}, "steps"),
    ],
)
def test_parse_rejects(body: object, detail: str) -> None:
    with pytest.raises(SpecError) as err:
        parse(body)
    assert err.value.detail == detail


def test_grant_round_trip() -> None:
    grant = Grant(step=2, click=True, requests=(("POST", "http://x/a"),))
    assert Grant.from_json(grant.to_json()) == grant


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://panel.cyberfolks.pl/security/login", True),
        ("https://x.example/Logowanie/", True),
        ("https://x.example/auth", True),
        ("https://x.example/login.html", True),
        ("https://x.example/login?next=/a", True),
        ("https://panel.cyberfolks.pl/", False),
        ("https://x.example/dashboard", False),
        ("https://x.example/login/reset-code", False),
    ],
)
def test_is_login_url_reads_the_path_only(url: str, expected: bool) -> None:
    assert policy.is_login_url(url) is expected
