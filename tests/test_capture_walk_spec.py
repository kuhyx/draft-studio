"""Walkthrough specs: aims (scoped / icon-only targets), shot and goto steps, the implicit legacy shot."""

from __future__ import annotations

import pytest

from draft_studio.capture.aim import Aim, parse_aim
from draft_studio.capture.spec import SpecError, Step, parse

U = "https://panel.example/your-data/shares"
LOGIN = "https://panel.example/security/login"
ROW = "GABINET PSYCHOLOGICZNY ANNA LEŚNIAK-JARECKA"
WALK = {
    "url": U,
    "name": "renew",
    "steps": [
        {"shot": {"name": "login", "fresh": True, "url": LOGIN}},
        {"click": {"text": "ZALOGUJ JAKO", "within": ROW}},
        {"shot": {"name": "dashboard", "highlight": {"text": "Domeny", "within": "Twoje usługi"}}},
        {"click": {"text": "Domeny", "within": "Twoje usługi"}},
        {
            "shot": {
                "name": "domains",
                "highlight": {"role": "button", "within": "kaliszpsycholog.pl", "nth": -1},
            }
        },
        {"click": {"role": "button", "within": "kaliszpsycholog.pl", "nth": -1}},
        {"goto": "https://panel.example/services/domains"},
        {"shot": {"name": "menu", "highlight": "Odnów"}},
    ],
}


def test_plain_text_aim_stays_a_string() -> None:
    aim = parse_aim("  Domeny ", "x")
    assert aim == Aim("Domeny") and aim.plain and aim.to_json() == "Domeny" and aim.label() == "Domeny"


def test_scoped_and_icon_aims_round_trip() -> None:
    scoped = parse_aim({"text": "Domeny", "within": "Twoje usługi", "nth": 2}, "x")
    assert scoped.label() == "Domeny within „Twoje usługi” #2"
    icon = parse_aim({"role": "button", "within": "kaliszpsycholog.pl", "nth": -1}, "x")
    assert icon.label() == "button within „kaliszpsycholog.pl” #-1"
    for aim in (scoped, icon):
        assert not aim.plain and parse_aim(aim.to_json(), "x") == aim
    assert icon.js() == {"text": None, "role": "button", "within": "kaliszpsycholog.pl", "nth": -1}


@pytest.mark.parametrize(
    "raw",
    [
        3,
        " ",
        {},
        {"text": "a", "role": "button"},
        {"role": "link"},
        {"text": "a", "nth": 0},
        {"text": "a", "nth": True},
        {"text": "a", "nth": 99},
        {"text": "a", "within": ""},
        {"text": "a", "near": "b"},
        {"text": "x" * 201},
    ],
)
def test_bad_aims(raw: object) -> None:
    with pytest.raises(SpecError) as err:
        parse_aim(raw, "step-3")
    assert err.value.detail == "step-3"


def test_walk_parses_and_round_trips() -> None:
    spec = parse(WALK)
    assert spec.walk and spec.highlight is None
    assert [s.kind for s in spec.steps] == ["shot", "click", "shot", "click", "shot", "click", "goto", "shot"]
    login, click = spec.steps[0], spec.steps[1]
    assert (login.text, login.fresh, login.url, login.aim) == ("login", True, LOGIN, None)
    assert click.text == f"ZALOGUJ JAKO within „{ROW}”" and click.target == Aim("ZALOGUJ JAKO", within=ROW)
    assert (
        spec.steps[-1].aim == Aim("Odnów") and spec.steps[6].text == "https://panel.example/services/domains"
    )
    assert parse(spec.to_json()) == spec
    assert spec.plan() == spec.steps


def test_legacy_spec_gets_one_implicit_shot() -> None:
    spec = parse({"url": U, "name": "one", "steps": [{"click": "Domeny"}], "highlight": "DNS"})
    assert not spec.walk and spec.steps == (Step("click", "Domeny"),) and spec.highlight == Aim("DNS")
    assert spec.steps[0].target == Aim("Domeny")
    assert spec.plan()[-1] == Step("shot", "one", aim=Aim("DNS"))
    assert spec.to_json()["highlight"] == "DNS" and parse(spec.to_json()) == spec


def _walk(*steps: object, **extra: object) -> dict[str, object]:
    return {"url": U, "name": "w", "steps": list(steps), **extra}


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (_walk({"shot": {"name": "a"}}, highlight="x"), "highlight"),
        (_walk({"shot": "a"}), "step-0"),
        (_walk({"shot": {"name": "Bad"}}), "step-0"),
        (_walk({"shot": {"name": "a", "fresh": True}}), "step-0"),
        (_walk({"shot": {"name": "a", "url": "https://x/"}}), "step-0"),
        (_walk({"shot": {"name": "a", "fresh": True, "url": "ftp://x/"}}), "step-0"),
        (_walk({"shot": {"name": "a", "fresh": 1, "url": "https://x/"}}), "step-0"),
        (_walk({"shot": {"name": "a", "zoom": 2}}), "step-0"),
        (_walk({"shot": {"name": "a", "highlight": {"role": "x"}}}), "step-0"),
        (_walk({"shot": {"name": "a"}}, {"shot": {"name": "a"}}), "step-1"),
        (_walk({"shot": {"name": "a"}}, {"goto": "file:///etc/passwd"}), "step-1"),
        (_walk({"click": {"text": "a", "nth": 0}}), "step-0"),
        (_walk({"wait": {"text": "a"}}), "step-0"),
    ],
)
def test_walk_rejects(body: object, detail: str) -> None:
    with pytest.raises(SpecError) as err:
        parse(body)
    assert err.value.detail == detail
