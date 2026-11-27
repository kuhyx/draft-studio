"""A click whose target is gone because its state is already in effect (cyber_Folks impersonation).

kuhy's walk: goto shares -> click "ZALOGUJ JAKO" -> wait "Domeny" -> shots. The panel keeps the
impersonation for an hour, so a later run starts in the owner's view, where the button does not exist.
"""

from __future__ import annotations

import pytest

from draft_studio.capture import errors
from draft_studio.capture.already import goal
from draft_studio.capture.runner import run_all
from draft_studio.capture.spec import Spec, Step, parse
from tests.capture_fakes import FakePage

SHARES = "https://panel.example/your-data/shares"
ROOT = "https://panel.example/"
ROW = "GABINET"
DOMENY = {"text": "Domeny", "within": "Twoje usługi"}
# what the live no-target listed on the shares page in the owner's view
OWNER_VIEW = ["menu", "00:59:59", "pomoc i kontakt z nami", "Domeny"]
DASHBOARD = (["Domeny", "Pulpit"], {"Twoje usługi": ["Pulpit", "Domeny"]})


def plan(*after: object, click: object | None = None) -> Spec:
    steps = [{"click": click or {"text": "ZALOGUJ JAKO", "within": ROW}}, *after]
    return parse({"url": SHARES, "name": "renew", "steps": steps})


def walk() -> Spec:
    return plan({"wait": "Domeny"}, {"shot": {"name": "dashboard", "highlight": DOMENY}})


def owner_page(pages: dict[str, tuple[list[str], dict[str, list[str]]]] | None = None) -> FakePage:
    """The shares page in the owner view: the share row and its button are gone, the countdown shows."""
    return FakePage(OWNER_VIEW, pages=pages)


def test_goal_is_the_wait_after_the_click_and_the_next_session_shot() -> None:
    spec = parse(
        {
            "url": SHARES,
            "name": "w",
            "steps": [
                {"click": "A"},
                {"wait": "B"},
                {"shot": {"name": "x", "fresh": True, "url": SHARES}},
                {"shot": {"name": "y", "highlight": "C"}},
                {"click": "D"},
                {"shot": {"name": "z", "highlight": "E"}},
            ],
        }
    )
    steps = spec.plan()
    assert goal(steps, 0) == ("B", steps[3].aim) and steps[3].aim is not None
    assert goal(steps, 4) == (None, steps[5].aim)
    assert goal((Step("click", "A"), Step("click", "B")), 0) == (None, None)
    assert goal((Step("click", "A"), Step("wait", "W"), Step("wait", "V")), 0) == ("W", None)
    assert goal((Step("click", "A"), Step("wait", "W")), 0) == ("W", None)  # the plan ends first


def test_the_goal_is_met_on_this_page_so_the_click_is_skipped() -> None:
    page = FakePage(["Domeny", "menu"], scopes={"Twoje usługi": ["Domeny"]})  # no ZALOGUJ JAKO, no row
    shots = run_all(page, walk(), [])
    assert [n for n, _ in shots] == ["dashboard"]
    assert not any(c[0] in ("click", "goto") for c in page.calls[1:])  # nothing clicked, no second goto


def test_the_goal_is_met_only_on_the_origin_root_after_a_goto_there() -> None:
    # "Domeny" is in the menu here, but the dashboard's "Twoje usługi" group is not: the shot needs the root.
    page = owner_page(pages={ROOT: DASHBOARD})
    shots = run_all(page, walk(), [])
    assert [n for n, _ in shots] == ["dashboard"]
    assert [c for c in page.calls if c[0] == "goto"] == [("goto", SHARES), ("goto", ROOT)]
    assert not any(c[0] == "click" for c in page.calls) and page.where == ROOT


def test_a_wait_text_only_on_the_root_is_enough_when_no_shot_follows() -> None:
    spec = plan({"wait": "Pulpit"}, {"shot": {"name": "s", "highlight": None}})
    page = owner_page(pages={ROOT: (["Pulpit"], {})})
    assert [n for n, _ in run_all(page, spec, [])] == ["s"]
    assert page.where == ROOT


def test_a_shot_aim_alone_decides_when_no_wait_follows() -> None:
    spec = plan({"shot": {"name": "s", "highlight": DOMENY}})
    assert [n for n, _ in run_all(owner_page(pages={ROOT: DASHBOARD}), spec, [])] == ["s"]
    page = owner_page(pages={ROOT: (["Domeny"], {})})  # the group heading is missing there too
    with pytest.raises(errors.NoTarget) as err:
        run_all(page, spec, [])
    assert err.value.extra["step"] == 0 and err.value.extra["text"] == ROW


def test_an_ambiguous_highlight_counts_as_present_and_the_shot_reports_it() -> None:
    spec = plan({"wait": "Domeny"}, {"shot": {"name": "s", "highlight": "Domeny"}})
    page = FakePage(["Domeny", "Domeny"])
    with pytest.raises(errors.Ambiguous) as err:
        run_all(page, spec, [])
    assert err.value.extra["step"] == 2 and not any(c[0] == "click" for c in page.calls)


def test_the_goal_not_met_anywhere_is_still_no_target_with_the_first_page_seen() -> None:
    page = owner_page(pages={ROOT: (["Pulpit"], {})})  # the root shows no "Domeny" either
    with pytest.raises(errors.NoTarget) as err:
        run_all(page, walk(), [])
    assert err.value.extra == {"step": 0, "text": ROW, "seen": ["menu", "00:59:59"]}
    assert ("goto", ROOT) in page.calls and not any(c[0] == "click" for c in page.calls)


def test_the_goal_not_met_on_a_page_that_is_already_the_root_is_no_target_without_a_reload() -> None:
    page = FakePage(["menu"], redirects={SHARES: ROOT})
    with pytest.raises(errors.NoTarget):
        run_all(page, walk(), [])
    assert [c for c in page.calls if c[0] == "goto"] == [("goto", SHARES)]


def test_a_click_with_nothing_after_it_to_tell_the_state_apart_is_no_target() -> None:
    for after in ([], [{"click": "Domeny"}], [{"shot": {"name": "s"}}]):
        page = owner_page(pages={ROOT: DASHBOARD})
        spec = plan(*after)
        with pytest.raises(errors.NoTarget):
            run_all(page, spec, [])
        assert ("goto", ROOT) not in page.calls


def test_an_ambiguous_click_target_is_never_skipped() -> None:
    page = FakePage(["ZALOGUJ", "ZALOGUJ", "Domeny"], scopes={"Twoje usługi": ["Domeny"]})
    spec = plan({"wait": "Domeny"}, click="ZALOGUJ")
    with pytest.raises(errors.Ambiguous):
        run_all(page, spec, [])


def test_a_present_button_is_still_clicked() -> None:
    page = FakePage(["ZALOGUJ JAKO", "Domeny"], scopes={ROW: ["ZALOGUJ JAKO"], "Twoje usługi": ["Domeny"]})
    assert [n for n, _ in run_all(page, walk(), [])] == ["dashboard"]
    assert ("click", "ZALOGUJ JAKO", False, []) in page.calls
