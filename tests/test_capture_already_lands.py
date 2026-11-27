"""A skipped click leaves the walk where the click would have (cyber_Folks, draft 462fb46c512a).

Live (2026-11-27): the panel shows its side menu („Twoje usługi” > „Domeny”) on every page, so in the
owner's view the shares page itself met the goal of „ZALOGUJ JAKO” and the dashboard shot was taken
on the shares page. A click step names where it `lands`: "home" (the site's start page in the new
state) or a URL; a skipped click opens it and checks the goal there, never on the page it was on.
"""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.runner import run_all
from draft_studio.capture.spec import SpecError, Step, parse
from draft_studio.prompts import _STEP
from draft_studio.shotaim import ShotError, parse_step, wire_step
from draft_studio.shotreq import parse_shot
from tests.capture_fakes import FakePage

SHARES = "https://panel.example/your-data/shares"
ROOT = "https://panel.example/"
DASH = "https://panel.example/dashboard"
CLICK = {"text": "ZALOGUJ JAKO", "within": "GABINET"}
DOMENY = {"text": "Domeny", "within": "Twoje usługi"}
# the shares page in the owner's view: kuhy's own row, and the side menu every page shows
MENU = (["menu", "KONTO 00000000", "Twoje usługi", "Domeny"], {"Twoje usługi": ["Domeny"]})
DASHBOARD = (["Twoje usługi", "Domeny", "Pulpit"], {"Twoje usługi": ["Pulpit", "Domeny"]})


def walk(lands: str) -> dict[str, object]:
    steps = [
        {"click": CLICK, "lands": lands},
        {"wait": "Twoje usługi"},
        {"shot": {"name": "d", "highlight": DOMENY}},
    ]
    return {"url": SHARES, "name": "renew", "steps": steps}


def test_a_skipped_click_that_lands_home_shoots_on_the_root_not_on_the_page_it_was_on() -> None:
    page = FakePage([], pages={SHARES: MENU, ROOT: DASHBOARD})
    shots = run_all(page, parse(walk("home")), [])
    assert [n for n, _ in shots] == ["d"] and page.where == ROOT
    assert [c for c in page.calls if c[0] == "goto"] == [("goto", SHARES), ("goto", ROOT)]
    assert not any(c[0] == "click" for c in page.calls)


def test_a_skipped_click_that_lands_on_a_url_opens_that_url() -> None:
    page = FakePage([], pages={SHARES: MENU, DASH: DASHBOARD})
    assert [n for n, _ in run_all(page, parse(walk(DASH)), [])] == ["d"]
    assert page.where == DASH and ("goto", ROOT) not in page.calls


def test_a_run_already_where_the_click_lands_is_not_reloaded() -> None:
    page = FakePage([], redirects={SHARES: ROOT}, pages={ROOT: DASHBOARD})
    assert [n for n, _ in run_all(page, parse(walk("home")), [])] == ["d"]
    assert [c for c in page.calls if c[0] == "goto"] == [("goto", SHARES)]


def test_a_goal_met_here_but_not_where_the_click_lands_is_no_target() -> None:
    page = FakePage([], pages={SHARES: MENU, ROOT: (["Pulpit"], {})})
    with pytest.raises(errors.NoTarget) as err:
        run_all(page, parse(walk("home")), [])
    assert err.value.extra["step"] == 0 and err.value.extra["text"] == "GABINET"


def test_a_present_button_is_clicked_and_lands_changes_nothing() -> None:
    page = FakePage(
        ["ZALOGUJ JAKO", "Twoje usługi", "Domeny"],
        scopes={"GABINET": ["ZALOGUJ JAKO"], "Twoje usługi": ["Domeny"]},
    )
    assert [n for n, _ in run_all(page, parse(walk("home")), [])] == ["d"]
    assert ("click", "ZALOGUJ JAKO", False, []) in page.calls and ("goto", ROOT) not in page.calls


def test_lands_round_trips_through_the_stored_spec() -> None:
    for lands in ("home", DASH):
        spec = parse(walk(lands))
        assert spec.steps[0].lands == lands and parse(spec.to_json()) == spec
    plain = parse({"url": SHARES, "name": "n", "steps": [{"click": "A"}]})
    assert plain.steps[0] == Step("click", "A") and plain.to_json()["steps"] == [{"click": "A"}]


@pytest.mark.parametrize(
    "step",
    [
        {"click": "A", "lands": "nowhere"},
        {"click": "A", "lands": ""},
        {"click": "A", "lands": 3},
        {"wait": "A", "lands": "home"},
        {"click": "A", "other": "home"},
    ],
)
def test_a_bad_lands_is_a_spec_error(step: object) -> None:
    with pytest.raises(SpecError):
        parse({"url": SHARES, "name": "n", "steps": [step]})


def test_claudes_step_carries_lands_to_the_wire_and_into_the_url_allow_list() -> None:
    kind, aim, lands = parse_step({"kind": "click", "text": "ZALOGUJ JAKO", "lands": "home"})
    assert wire_step(kind, aim, lands) == {"click": "ZALOGUJ JAKO", "lands": "home"}
    assert parse_step({"kind": "click", "text": "A"})[2] == ""  # older held records: no lands
    assert parse_step({"kind": "wait", "text": "A", "lands": "home"})[2] == ""  # only a click lands
    assert wire_step(*parse_step({"kind": "click", "text": "A", "lands": ""})) == {"click": "A"}
    with pytest.raises(ShotError):
        parse_step({"kind": "click", "text": "A", "lands": "the dashboard"})
    assert "lands" in _STEP["required"]
    raw = {
        "key": "k",
        "url": SHARES,
        "steps": [{"kind": "click", "text": "A", "lands": DASH}],
        "highlight": "",
        "alt": "",
    }
    shot = parse_shot(raw)
    assert shot.urls() == (SHARES, DASH) and shot.to_json()["steps"][0]["lands"] == DASH
