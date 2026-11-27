"""A walk's shot that frames an element and the click right after it on the same element agree.

Live, draft 462fb46c512a (job 1a21e49a2bf-0b37c3): the panel shows „Domeny” twice. One fix
call moved the dashboard's highlight to nth 1, a second, separate one moved the next click
to nth 2: the walk framed one „Domeny”, clicked the other and failed `no-target`. Each fix
call sees only its own capture, so walks.py makes the pair agree: on build the click takes
the highlight's aim, on a merged fix the fixed side wins (a deliberate retarget sticks).
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture.aim import Aim
from draft_studio.capture.policy import Target
from draft_studio.claude import Context
from draft_studio.claude_fake import FakeClaude
from draft_studio.shotreq import Shot, parse_shot
from draft_studio.walks import align, plan, walk_spec
from tests.capture_fakes import FakePage
from tests.conftest import App
from tests.test_held import grant, held
from tests.test_shots_walk import KEYS, MORE, Owner, aim, cap, click, images, start, walk

Json = dict[str, Any]


def shots(raws: list[Json]) -> list[Shot]:
    return [parse_shot(r) for r in raws]


def clicks(raws: list[Json]) -> list[Any]:
    return [s["click"] for s in walk_spec(shots(raws))[0]["steps"] if "click" in s]


def pair(highlight: Json, steps: list[Json], first: Json | None = None) -> list[Json]:
    return [cap("a", [], highlight, **(first or {})), cap("b", steps, aim("Odnów"))]


def test_the_click_after_a_shot_takes_its_highlights_aim() -> None:
    assert clicks(pair(aim("Domeny", nth=1), [click("Domeny", nth=2)])) == [{"text": "Domeny", "nth": 1}]


def test_text_is_folded_and_leading_waits_and_lands_are_kept() -> None:
    steps = [{"kind": "wait", **aim("Panel")}, {**click("  domeny "), "lands": "home"}]
    raws = pair(aim("Domeny", within="Twoje usługi"), steps)
    assert clicks(raws) == [{"text": "Domeny", "within": "Twoje usługi"}]
    assert walk_spec(shots(raws))[0]["steps"][2]["lands"] == "home"


def test_role_only_aims_pair_only_inside_the_same_scope() -> None:
    assert clicks(pair(aim(**MORE), [click(role="button", within="kaliszpsycholog.pl")])) == [MORE]
    other = {"role": "button", "within": "inna-domena.pl"}
    assert clicks(pair(aim(**MORE), [click(**other)])) == [other]


@pytest.mark.parametrize(
    ("highlight", "steps", "first"),
    [
        (aim("Domeny", nth=1), [click("Usługi"), click("Domeny", nth=2)], {}),  # the page moved first
        (
            aim("Domeny", nth=1),
            [{"kind": "goto", **aim("https://panel.example/x")}, click("Domeny", nth=2)],
            {},
        ),
        (aim("Domeny", nth=1), [click("Domeny", nth=2)], {"fresh": True}),  # logged-out page
        (aim(), [click("Domeny", nth=2)], {}),  # nothing framed
        (aim("Hosting"), [click("Domeny", nth=2)], {}),
    ],
)
def test_a_click_on_another_page_or_element_is_left_alone(
    highlight: Json, steps: list[Json], first: Json
) -> None:
    assert clicks(pair(highlight, steps, first))[-1] == {"text": "Domeny", "nth": 2}


def test_the_fixed_shot_wins_and_the_highlight_follows_its_click() -> None:
    a, b = align(shots(pair(aim("Domeny", nth=1), [click("Domeny", nth=2)])), prefer="b")
    assert a.highlight == ("Domeny", "", "", 2) and b.steps[0][1] == ("Domeny", "", "", 2)


def test_plan_stores_the_aligned_walk() -> None:
    raws = walk()
    raws[2]["steps"] = [click("Domeny", nth=2)]
    run, _, dropped = plan(raws, KEYS)
    assert dropped == [] and run["domains"].steps[0][1] == ("Domeny", "", "Twoje usługi", 0)


class TwoMenus(Owner):
    """Two „Domeny” (refs 0, 1); only ref `opens` leads to the domain list. Logs each frame and click."""

    def __init__(self, opens: int, log: list[tuple[str, int]]) -> None:
        super().__init__()
        self.opens, self.log = opens, log

    def find(self, aim: Aim) -> list[Target]:
        if aim.text != "Domeny" or aim.within:
            return super().find(aim)
        refs = [i for i, t in enumerate(self.texts) if t == "Domeny"]
        if aim.nth:
            refs = refs[aim.nth - 1 : aim.nth] if aim.nth > 0 else refs[aim.nth :][:1]
        return [Target(ref=i, tag="button", text="Domeny") for i in refs]

    def click(
        self, target: Target, allow_submit: bool, allowed: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        if target.text != "Domeny":
            return super().click(target, allow_submit, allowed)
        self.log.append(("click", int(target.ref)))
        if target.ref == self.opens:
            return super().click(target, allow_submit, allowed)
        return FakePage.click(self, target, allow_submit, allowed)

    def highlight(self, target: Target) -> bool:
        if target.text == "Domeny":
            self.log.append(("frame", int(target.ref)))
        return super().highlight(target)


def run_walk(
    app: App, monkeypatch: pytest.MonkeyPatch, captures: list[Json], opens: int, fix: Any
) -> tuple[list[tuple[str, Any]], list[tuple[str, int]], str]:
    asked: list[tuple[str, Any]] = []
    log: list[tuple[str, int]] = []

    def fixer(self: FakeClaude, ctx: Context, capture: Json, error: Json) -> Json:
        asked.append((capture["key"], error["step"]))
        return fix(capture, error)

    monkeypatch.setattr(FakeClaude, "fix_capture", fixer)
    _, did, (_, body) = start(app, monkeypatch, captures, lambda: TwoMenus(opens, log))
    log.clear()
    grant(app, body["held"]["requests"][0]["id"])
    return asked, log, did


def test_a_fixed_highlight_moves_the_click_with_it(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """The live run: highlight fixed to nth 1, click left ambiguous, then fixed to nth 2 on its own."""
    captures = walk()
    captures[1]["highlight"], captures[2]["steps"] = aim("Domeny"), [click("Domeny")]

    def fix(capture: Json, error: Json) -> Json:
        if error["step"] is None:
            return {**capture, "highlight": aim("Domeny", nth=1)}
        return {**capture, "steps": [click("Domeny", nth=2)]}

    asked, log, did = run_walk(app, monkeypatch, captures, 0, fix)
    assert asked == [("dashboard", None)] and log[-2:] == [("frame", 0), ("click", 0)], (asked, log)
    (out,) = held(app, did)["released"]
    assert out["dropped"] == [] and images(app, did, out["replacement"]) == KEYS


def test_a_fix_that_retargets_the_click_is_kept_and_the_frame_follows(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    captures = walk()
    captures[1]["highlight"], captures[2]["steps"] = aim("Domeny", nth=1), [click("Domeny", nth=1)]

    def fix(capture: Json, error: Json) -> Json:
        return {**capture, "steps": [click("Domeny", nth=2)]}

    asked, log, did = run_walk(app, monkeypatch, captures, 1, fix)
    assert asked == [("domains", None)] and log[-2:] == [("frame", 1), ("click", 1)], (asked, log)
    (out,) = held(app, did)["released"]
    assert out["dropped"] == [] and images(app, did, out["replacement"]) == KEYS
