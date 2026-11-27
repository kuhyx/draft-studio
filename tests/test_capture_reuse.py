"""A grant reused by every re-run of its own walk for 10 minutes (kuhy, 2026-11-27), on a fake clock.

The same request (method + URL), sent by a click, in the same draft and the same walk (the chain
of permission requests one capture started, plus the fix runs that follow it). Anything else,
a deny included, shows a card.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture.reuse import REUSE_S, Ledger
from draft_studio.capture.service import CaptureService
from draft_studio.capture.spec import Denial, Grant, parse
from tests.conftest import App
from tests.test_capture_service import FakeLauncher, ShotPage, draft

SHARES = "https://panel.example/your-data/shares"
IMPERSONATE = ("POST", "https://panel.example/api/impersonate?id=1")
TRACK = ("POST", "https://panel.example/api/track")
LOGIN_AS = {"click": {"text": "ZALOGUJ JAKO", "within": "GABINET"}}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def page(sends: tuple[str, str] = IMPERSONATE, texts: tuple[str, ...] = ("Domeny",)) -> ShotPage:
    out = ShotPage(list(texts), {"ZALOGUJ JAKO": [sends], "Domeny": [TRACK]})
    out.scopes = {"GABINET": ["ZALOGUJ JAKO"]}
    return out


def spec(*after: dict[str, Any], highlight: str = "Domeny", click: Any = None) -> dict[str, Any]:
    steps = [click or LOGIN_AS, *after, {"shot": {"name": "dashboard", "highlight": highlight}}]
    return {"url": SHARES, "name": "renew", "steps": steps}


class Run:
    """One service, its pages built on demand (the grant's re-run finds no "Brak": a fix follows)."""

    def __init__(self, app: App) -> None:
        self.clock = Clock()
        self.launcher = FakeLauncher(page)
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


def test_every_fix_run_reuses_the_grant_without_a_card(app: App) -> None:
    r = Run(app)
    rid = r.granted()
    status, body = r.svc.capture(r.did, spec(), follows=rid)
    assert status == 200 and [s["name"] for s in body["shots"]] == ["dashboard"], body
    r.clock.now += REUSE_S - 1
    assert r.svc.capture(r.did, spec({"wait": "Domeny"}), follows=rid)[0] == 200  # moved, still reused
    assert r.svc.list_pending()[1]["requests"] == []


def test_a_fix_that_rewrites_the_aim_still_reuses_it(app: App) -> None:
    r = Run(app)
    rid = r.granted()
    r.launcher.make = lambda: page(texts=("Domeny", "ZALOGUJ JAKO"))
    assert (
        r.svc.capture(r.did, spec(click={"click": {"text": "ZALOGUJ JAKO", "nth": 1}}), follows=rid)[0] == 200
    )


@pytest.mark.parametrize("decide", ["grant", "deny"])
def test_a_later_card_re_runs_with_the_grant(app: App, decide: str) -> None:
    """The live cyber_Folks case: impersonation granted, then a tracker POST granted or denied."""
    r = Run(app)
    _, body = r.svc.capture(r.did, spec({"click": "Domeny"}))
    status, body = r.svc.grant(body["request"]["id"])
    assert status == 409 and body["request"]["detail"] == " ".join(TRACK), body
    status, body = getattr(r.svc, decide)(body["request"]["id"])
    assert status == 200 and r.svc.list_pending()[1]["requests"] == [], body


@pytest.mark.parametrize(
    "sends",
    [
        ("POST", "https://panel.example/api/impersonate?id=2"),  # another URL
        ("PUT", "https://panel.example/api/impersonate?id=1"),  # another method
    ],
)
def test_another_request_is_carded(app: App, sends: tuple[str, str]) -> None:
    r = Run(app)
    rid = r.granted()
    r.launcher.make = lambda: page(sends)
    status, body = r.svc.capture(r.did, spec(), follows=rid)
    assert status == 409 and body["request"]["detail"] == " ".join(sends), body


def test_after_ten_minutes_it_is_carded_again(app: App) -> None:
    r = Run(app)
    _, body = r.svc.capture(r.did, spec({"click": "Domeny"}))
    _, body = r.svc.grant(body["request"]["id"])  # the tracker card, same walk
    r.clock.now += REUSE_S
    _, body = r.svc.grant(body["request"]["id"])
    assert body["error"] == "needs-permission" and body["request"]["step"] == 0, body


def test_another_walk_or_draft_is_carded(app: App) -> None:
    r = Run(app)
    rid = r.granted()
    other = r.svc.capture(r.did, spec(highlight="Brak"))[1]["request"]["id"]  # same names, other walk
    assert r.svc.capture(r.did, spec(), follows=other)[0] == 409
    assert r.svc.capture(r.did, spec())[0] == 409  # a run that follows no grant
    assert r.svc.capture(r.did, spec(), follows="0" * 16)[0] == 409  # pruned or never made
    assert r.svc.capture(draft(app), spec(), follows=rid)[0] == 409  # another draft, same rid


def test_a_deny_is_never_reused(app: App) -> None:
    r = Run(app)
    rid = r.svc.capture(r.did, spec(highlight="Brak"))[1]["request"]["id"]
    assert r.svc.deny(rid)[0] == 422
    assert r.svc.capture(r.did, spec(), follows=rid)[0] == 409


def test_a_card_on_a_fix_run_holds_only_kuhys_own_grants(app: App) -> None:
    """A reused grant never lands in a new record: granting that card cannot outlive the window."""
    r = Run(app)
    rid = r.granted()
    status, body = r.svc.capture(r.did, spec({"click": "Domeny"}), follows=rid)
    assert status == 409 and body["request"]["detail"] == " ".join(TRACK), body
    assert body["request"]["grants"] == [] and body["request"]["allow"]["requests"] == [list(TRACK)]


def test_a_denial_wins_over_a_kept_grant() -> None:
    ledger, walk = Ledger(Clock()), parse(spec())
    ledger.remember("w1", "d1", Grant(0, click=False, requests=(IMPERSONATE, ("SUBMIT", "/x"))))
    assert ledger.grants("w1", "d1", walk, []) == [Grant(0, click=False, requests=(IMPERSONATE,))]
    assert ledger.grants("w1", "d1", walk, [Denial(3, click=False, requests=(IMPERSONATE,))]) == []
