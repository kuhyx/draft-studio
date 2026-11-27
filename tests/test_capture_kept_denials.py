"""Denials kept per draft until cleared (kuhy, 2026-11-27: the robofolks chat preflight carded every walk).

A denied (method, URL) pair is stored in drafts/<id>/denials.json, failed again with no card (the
shot still taken) in every later run of that draft, its OPTIONS twin with it; a grant lifts it,
`POST /api/drafts/<id>/denials {"clear": ...}` removes it. A denial change bumps the change feed.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture.denials import Denials
from draft_studio.capture.service import CaptureService
from draft_studio.capture.spec import Denial
from tests.conftest import App
from tests.test_capture_partial import CHAT, PRE_CHAT
from tests.test_capture_service import ShotPage, draft, use

SAVE = ("POST", "https://panel.example/api/save")
SPEC: dict[str, Any] = {
    "url": "https://panel.example/shares",
    "name": "chat",
    "steps": [{"click": "Domeny"}],
    "highlight": "Lista",
}


class Rig:
    """Every run's click sends what `sends` holds (the chat widget's preflight by default)."""

    def __init__(self, app: App, sends: list[tuple[str, str]] | None = None) -> None:
        self.app, self.sends, self.pages = app, sends or [PRE_CHAT], list[ShotPage]()

        def make() -> ShotPage:
            self.pages.append(ShotPage(["Domeny", "Lista"], {"Domeny": list(self.sends)}))
            return self.pages[-1]

        self.launcher = use(app, make)
        self.svc: CaptureService = app.httpd.captures
        self.did = draft(app)

    def denied(self) -> str:
        """The first run cards the chat request, kuhy denies it (the shot lands): the card's id."""
        status, body = self.svc.capture(self.did, SPEC)
        assert status == 409, body
        rid = str(body["request"]["id"])
        assert self.svc.deny(rid)[0] == 200
        return rid

    def allowed(self) -> list[tuple[str, str]]:
        clicks = [c for c in self.pages[-1].calls if c[0] == "click"]
        return list(clicks[-1][3])


def pairs(listing: dict[str, Any]) -> list[tuple[str, str]]:
    return [(str(d["request"][0]), str(d["request"][1])) for d in listing["denials"]]


def test_a_new_walk_of_the_draft_fails_it_with_no_card(app: App) -> None:
    r = Rig(app)
    r.denied()
    status, body = r.svc.capture(r.did, SPEC)  # follows nothing: a new walk
    assert status == 200 and "asset" in body, body
    assert r.svc.list_pending()[1]["requests"] == [] and PRE_CHAT not in r.allowed()


def test_it_outlives_a_restart_and_another_draft_still_cards_it(app: App) -> None:
    r = Rig(app)
    r.denied()
    fresh = CaptureService(app.store, r.launcher, clock=lambda: 1e12)  # a new server, far later
    assert fresh.capture(r.did, SPEC)[0] == 200
    status, body = fresh.capture(draft(app), SPEC)
    assert status == 409 and [tuple(b) for b in body["request"]["blocked"]] == [PRE_CHAT], body


def test_a_denied_write_takes_its_preflight_along(app: App) -> None:
    r = Rig(app, [CHAT])
    r.denied()
    r.sends = [PRE_CHAT]  # the widget now preflights the same URL
    assert r.svc.capture(r.did, SPEC)[0] == 200
    assert pairs(r.svc.list_denials(r.did)[1]) == [CHAT, PRE_CHAT]  # the twin is its own row


def test_the_listing_says_when_and_a_clear_brings_the_card_back(app: App) -> None:
    r = Rig(app)
    r.denied()
    status, out = app.call("GET", f"/api/drafts/{r.did}/denials")
    assert status == 200 and pairs(out) == [PRE_CHAT] and isinstance(out["denials"][0]["denied"], float)
    status, out = app.call("POST", f"/api/drafts/{r.did}/denials", {"clear": [["options", PRE_CHAT[1]]]})
    assert (status, out) == (200, {"denials": []})
    assert r.svc.capture(r.did, SPEC)[0] == 409


def test_clear_all_and_a_write_clears_its_twin(app: App) -> None:
    r = Rig(app, [CHAT])
    r.denied()
    r.sends = [PRE_CHAT]
    assert app.call("POST", f"/api/drafts/{r.did}/denials", {"clear": [list(CHAT)]}) == (200, {"denials": []})
    assert r.svc.capture(r.did, SPEC)[0] == 409  # the preflight cards again: the twin went with it
    Denials(app.store).add(r.did, [CHAT, PRE_CHAT, ("SUBMIT", "https://panel.example/f")])
    assert app.call("POST", f"/api/drafts/{r.did}/denials", {"clear": "all"}) == (200, {"denials": []})


@pytest.mark.parametrize(
    "body", [{}, {"clear": "some"}, {"clear": []}, {"clear": [["POST"]]}, {"clear": [["POST", 1]]}, []]
)
def test_a_bad_clear_is_refused_and_changes_nothing(app: App, body: Any) -> None:
    r = Rig(app)
    r.denied()
    assert app.call("POST", f"/api/drafts/{r.did}/denials", body) == (400, {"error": "bad-clear"})
    assert pairs(r.svc.list_denials(r.did)[1]) == [PRE_CHAT]


def test_an_unknown_draft_is_not_found(app: App) -> None:
    Rig(app)
    assert app.call("GET", "/api/drafts/nosuchdraft/denials") == (404, {"error": "not-found"})
    assert app.call("POST", "/api/drafts/nosuchdraft/denials", {"clear": "all"}) == (
        404,
        {"error": "not-found"},
    )
    assert app.call("PUT", f"/api/drafts/{draft(app)}/denials", {})[0] == 404


def test_a_grant_lifts_it_with_its_twin_and_only_changes_bump_the_feed(app: App) -> None:
    denials, did = Denials(app.store), draft(app)
    seq = app.store.feed.seq
    denials.add(did, [CHAT])
    assert app.store.feed.seq == seq + 1
    denials.add(did, [PRE_CHAT, CHAT])  # already denied (the twin too): nothing changes
    assert app.store.feed.seq == seq + 1 and pairs(denials.listing(did)) == [CHAT, PRE_CHAT]
    assert denials.denial(did) == [Denial(0, click=False, requests=(CHAT, PRE_CHAT))]
    denials.lift(did, [("POST", "https://other.example/x")])
    assert app.store.feed.seq == seq + 1
    denials.lift(did, [CHAT])
    assert denials.listing(did) == {"denials": []} and denials.denial(did) == []
    assert app.store.feed.seq == seq + 2


def test_a_partial_grant_lifts_the_picked_pair_only(app: App) -> None:
    r = Rig(app, [CHAT, SAVE])
    Denials(app.store).add(r.did, [SAVE])  # kuhy denied it once
    status, body = r.svc.capture(r.did, SPEC)
    assert status == 409 and [tuple(b) for b in body["request"]["blocked"]] == [CHAT], body
    Denials(app.store).add(r.did, [CHAT])  # a clear raced it back in: the grant kuhy makes now wins
    assert r.svc.grant(str(body["request"]["id"]), {"allow": [list(CHAT)]})[0] == 200
    assert pairs(r.svc.list_denials(r.did)[1]) == [SAVE, ("OPTIONS", SAVE[1])] and CHAT in r.allowed()


def test_picking_only_the_preflight_keeps_the_write_denied_not_the_preflight(app: App) -> None:
    r = Rig(app, [PRE_CHAT, CHAT])
    status, body = r.svc.capture(r.did, SPEC)
    assert status == 409, body
    assert r.svc.grant(str(body["request"]["id"]), {"allow": [list(PRE_CHAT)]})[0] == 200
    assert pairs(r.svc.list_denials(r.did)[1]) == [CHAT] and PRE_CHAT in r.allowed()
