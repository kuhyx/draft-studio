"""A grant reused by the fix runs of its own capture (kuhy, 2026-11-27), on a fake clock.

The same request (method + URL) at the same click (its step text), within 10 minutes of the grant,
in the same draft, on a run that follows that very request id. Anything else shows a card.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from draft_studio.capture.reuse import REUSE_S
from draft_studio.capture.service import CaptureService
from tests.conftest import App
from tests.test_capture_service import FakeLauncher, ShotPage, draft

SHARES = "https://panel.example/your-data/shares"
IMPERSONATE = ("POST", "https://panel.example/api/impersonate?id=1")
LOGIN_AS = {"click": {"text": "ZALOGUJ JAKO", "within": "GABINET"}}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def page(sends: tuple[str, str] = IMPERSONATE, texts: tuple[str, ...] = ("Domeny",)) -> ShotPage:
    out = ShotPage(list(texts), {"ZALOGUJ JAKO": [sends], "Domeny": []})
    out.scopes = {"GABINET": ["ZALOGUJ JAKO"]}
    return out


def tracked(track: tuple[str, str]) -> ShotPage:
    out = page()
    out.effects["Domeny"] = [track]
    return out


def spec(*before: dict[str, Any], highlight: str = "Domeny") -> dict[str, Any]:
    steps = [*before, LOGIN_AS, {"shot": {"name": "dashboard", "highlight": highlight}}]
    return {"url": SHARES, "name": "renew", "steps": steps}


class Run:
    """One service, its pages in order (the grant's re-run finds no "Brak": the fix runs follow)."""

    def __init__(self, app: App, pages: list[ShotPage]) -> None:
        self.clock = Clock()
        it: Iterator[ShotPage] = iter(pages)
        self.launcher = FakeLauncher(lambda: next(it))
        self.svc = CaptureService(app.store, self.launcher, clock=self.clock)
        self.did = draft(app)

    def granted(self) -> str:
        """First run asks, kuhy grants, the re-run errors (a 422 a fix run corrects): the rid."""
        status, body = self.svc.capture(self.did, spec(highlight="Brak"))
        assert status == 409 and body["request"]["detail"] == " ".join(IMPERSONATE), body
        rid = str(body["request"]["id"])
        status, body = self.svc.grant(rid)
        assert status == 422 and body["error"] == "no-target", body
        return rid


def run(app: App, *fixes: ShotPage) -> Run:
    return Run(app, [page(), page(), *fixes])


def test_a_fix_run_reuses_the_grant_without_a_card(app: App) -> None:
    r = run(app, page(), page())
    rid = r.granted()
    status, body = r.svc.capture(r.did, spec(), follows=rid)
    assert status == 200 and [s["name"] for s in body["shots"]] == ["dashboard"], body
    r.clock.now += REUSE_S - 1  # every fix run within the window, not just one
    assert r.svc.capture(r.did, spec(), follows=rid)[0] == 200
    assert r.svc.list_pending()[1]["requests"] == []


def test_a_fix_that_moves_the_click_still_reuses_it(app: App) -> None:
    r = run(app, page())
    rid = r.granted()
    assert r.svc.capture(r.did, spec({"wait": "Domeny"}), follows=rid)[0] == 200


@pytest.mark.parametrize(
    "sends",
    [
        ("POST", "https://panel.example/api/impersonate?id=2"),  # another URL
        ("PUT", "https://panel.example/api/impersonate?id=1"),  # another method
    ],
)
def test_another_request_is_carded(app: App, sends: tuple[str, str]) -> None:
    r = run(app, page(sends))
    rid = r.granted()
    status, body = r.svc.capture(r.did, spec(), follows=rid)
    assert status == 409 and body["request"]["detail"] == " ".join(sends), body


def test_after_ten_minutes_it_is_carded_again(app: App) -> None:
    r = run(app, page())
    rid = r.granted()
    r.clock.now += REUSE_S
    assert r.svc.capture(r.did, spec(), follows=rid)[0] == 409


def test_another_walk_or_draft_is_carded(app: App) -> None:
    r = run(app, page(), page(), page(), page())
    rid = r.granted()
    other = r.svc.capture(r.did, spec(highlight="Brak"))[1]["request"]["id"]  # same names, other walk
    assert r.svc.capture(r.did, spec(), follows=other)[0] == 409
    assert r.svc.capture(r.did, spec())[0] == 409  # a run that follows no grant
    assert r.svc.capture(draft(app), spec(), follows=rid)[0] == 409  # another draft, same rid


def test_another_click_is_carded(app: App) -> None:
    r = run(app, page())
    rid = r.granted()
    fixed = spec()
    fixed["steps"][0] = {"click": "ZALOGUJ JAKO"}  # same request, a click kuhy did not allow it for
    r.launcher.make = lambda: page(texts=("Domeny", "ZALOGUJ JAKO"))
    assert r.svc.capture(r.did, fixed, follows=rid)[0] == 409


def test_a_deny_is_never_reused(app: App) -> None:
    r = run(app, page(), page())
    rid = r.svc.capture(r.did, spec(highlight="Brak"))[1]["request"]["id"]
    assert r.svc.deny(rid)[0] == 422
    assert r.svc.capture(r.did, spec(), follows=rid)[0] == 409


def test_a_card_on_a_fix_run_holds_only_kuhys_own_grants(app: App) -> None:
    """A reused grant never lands in a new record: granting that card cannot outlive the window."""
    track = ("POST", "https://panel.example/api/track")
    r = run(app, page())
    rid = r.granted()
    r.launcher.make = lambda: tracked(track)
    status, body = r.svc.capture(r.did, spec({"click": "Domeny"}), follows=rid)  # card before the reuse
    assert status == 409 and body["request"]["detail"] == " ".join(track), body
    assert body["request"]["grants"] == [] and body["request"]["allow"]["requests"] == [list(track)]
