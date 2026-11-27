"""A card lists each (method, URL) pair once (kuhy, 2026-11-27: the robofolks page sent its preflight twice).

The card's `blocked` and `announces` hold each pair once, first-seen order, the first non-null announce.
The grant still carries every repeat the click sent (`allow.requests`): the guard lets each granted
copy through once (one grant, one write, spent.py), so a grant of the pair lets all of its repeats
through, a partial grant too; a denial of the pair fails every repeat.
"""

from __future__ import annotations

from typing import Any

from draft_studio.capture.guard import Guard
from draft_studio.capture.policy import Target
from draft_studio.capture.service import CaptureService
from tests.capture_fakes import FakeCdp, paused
from tests.conftest import App
from tests.test_capture_service import FakeLauncher, ShotPage, draft

PRE = ("OPTIONS", "https://robofolks.panel.example/request")
POST = ("POST", "https://panel.example/api/track")
SPEC: dict[str, Any] = {
    "url": "https://panel.example/shares",
    "name": "chat",
    "steps": [{"click": "Domeny"}],
    "highlight": "Lista",
}


class SpendingPage(ShotPage):
    """Like the guard: each allowed copy lets one request through, a repeat needs a copy of its own."""

    def click(
        self, target: Target, allow_submit: bool, allowed: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        self.calls.append(("click", target.text, allow_submit, list(allowed)))
        left, blocked = list(allowed), []
        for r in self.effects.get(target.text, []):
            if r in left:
                left.remove(r)
            elif not (r[0] == "SUBMIT" and allow_submit):
                blocked.append(r)
        return blocked


def _svc(app: App, sent: list[tuple[str, str]]) -> tuple[CaptureService, str, dict[str, Any]]:
    def make() -> SpendingPage:
        page = SpendingPage(["Domeny", "Lista"], {"Domeny": sent})
        page.announce = {PRE: "POST"}
        return page

    svc, did = CaptureService(app.store, FakeLauncher(make)), draft(app)
    status, body = svc.capture(did, SPEC)
    assert status == 409, body
    return svc, did, body["request"]


def test_the_guard_keeps_the_first_write_a_repeated_preflight_announced() -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.begin([])
    guard.handle(paused(*PRE, "r1", headers={"Access-Control-Request-Method": "POST"}, network="n1"))
    guard.handle(paused(*PRE, "r2", headers={"Access-Control-Request-Method": "PUT"}, network="n2"))
    assert guard.end() == [PRE, PRE]  # both really left the page: the card dedupes, not the guard
    assert guard.announces == {PRE: "POST"}


def test_a_card_lists_a_repeated_pair_once_in_first_seen_order(app: App) -> None:
    svc, _, rec = _svc(app, [PRE, POST, PRE, POST, PRE])
    assert [tuple(b) for b in rec["blocked"]] == [PRE, POST]
    assert rec["announces"] == ["POST", None]
    listed = svc.list_pending()[1]["requests"][0]
    assert [tuple(b) for b in listed["blocked"]] == [PRE, POST] and listed["announces"] == ["POST", None]
    assert sorted(tuple(r) for r in rec["allow"]["requests"]) == sorted([PRE, POST, PRE, POST, PRE])


def test_granting_the_card_lets_every_repeat_through(app: App) -> None:
    svc, _, rec = _svc(app, [PRE, PRE, PRE])  # 3: the walk's reuse ledger adds one copy of its own
    status, body = svc.grant(rec["id"])
    assert status == 200, body


def test_a_partial_grant_of_a_repeated_pair_lets_every_repeat_through(app: App) -> None:
    svc, _, rec = _svc(app, [PRE, POST, PRE, PRE])
    status, body = svc.grant(rec["id"], {"allow": [list(PRE)]})
    assert status == 200, body  # POST (and its twin) denied, both copies of PRE sent: no second card


def test_denying_a_repeated_pair_fails_every_repeat_without_a_new_card(app: App) -> None:
    svc, _, rec = _svc(app, [PRE, PRE])
    status, body = svc.deny(rec["id"])
    assert status == 200, body
    assert svc.list_pending()[1]["requests"] == []
