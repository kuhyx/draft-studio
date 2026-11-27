"""A partial grant (kuhy, 2026-11-27): POST /api/captures/<id>/grant {"allow": [[method, url], ...]}.

Only those blocked pairs are sent; the rest of the card stays blocked like a deny. Fake page + service.
"""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture.service import CaptureService
from tests.conftest import App
from tests.test_capture_service import FakeLauncher, ShotPage, draft
from tests.test_held import grant as grant_http
from tests.test_held import held, hold, saving

IMPERSONATE = ("POST", "https://api.panel.example/impersonates/1")
CHAT = ("POST", "https://robofolks.panel.example/request")
PRE_IMP = ("OPTIONS", IMPERSONATE[1])
PRE_CHAT = ("OPTIONS", CHAT[1])
LATER = ("POST", "https://panel.example/api/later")
SPEC: dict[str, Any] = {
    "url": "https://panel.example/shares",
    "name": "login-as",
    "steps": [{"click": "ZALOGUJ JAKO"}],
    "highlight": "Domeny",
}
TWO_STEPS = {**SPEC, "steps": [{"click": "ZALOGUJ JAKO"}, {"click": "Dalej"}]}


class Rig:
    def __init__(self, app: App, effects: dict[str, list[tuple[str, str]]] | None = None) -> None:
        self.pages: list[ShotPage] = []
        self.effects = effects or {"ZALOGUJ JAKO": [IMPERSONATE, CHAT]}

        def make() -> ShotPage:
            self.pages.append(ShotPage(["ZALOGUJ JAKO", "Dalej", "Domeny"], self.effects))
            return self.pages[-1]

        self.launcher = FakeLauncher(make)
        self.svc = CaptureService(app.store, self.launcher)
        self.did = draft(app)

    def card(self, spec: dict[str, Any] = SPEC) -> dict[str, Any]:
        status, body = self.svc.capture(self.did, spec)
        assert status == 409 and body["error"] == "needs-permission", body
        return dict(body["request"])

    def allowed(self, run: int = -1) -> list[tuple[str, str]]:
        """The allowance the click of the given run (last by default) was given."""
        clicks = [c for c in self.pages[run].calls if c[0] == "click"]
        return list(clicks[-1][3])


def test_the_card_blocks_both_posts(app: App) -> None:
    card = Rig(app).card()
    assert [tuple(b) for b in card["blocked"]] == [IMPERSONATE, CHAT]


def test_a_subset_grant_sends_only_those_and_keeps_the_rest_blocked(app: App) -> None:
    r = Rig(app)
    rid = r.card()["id"]
    status, body = r.svc.grant(rid, {"allow": [list(IMPERSONATE)]})
    assert status == 200 and body["asset"], body  # the chat POST did not card again
    assert IMPERSONATE in r.allowed() and CHAT not in r.allowed()
    assert PRE_IMP in r.allowed()  # its preflight, listed or not, rides along
    assert r.svc.pending.record(rid)["status"] == "granted"
    assert r.svc.list_pending()[1]["requests"] == []


def test_no_body_or_no_allow_key_grants_everything_as_before(app: App) -> None:
    for body in (None, {}, {"allow": None}):
        r = Rig(app)
        assert r.svc.grant(r.card()["id"], body)[0] == 200
        assert {IMPERSONATE, CHAT} <= set(r.allowed())


def test_a_pair_not_in_blocked_is_400_and_the_card_stays_pending(app: App) -> None:
    r = Rig(app)
    rid = r.card()["id"]
    for allow in ([["POST", "https://elsewhere.example/x"]], [list(IMPERSONATE), ["GET", IMPERSONATE[1]]]):
        assert r.svc.grant(rid, {"allow": allow}) == (400, {"error": "not-blocked"})
    assert r.launcher.runs == 1 and r.svc.pending.record(rid)["status"] == "pending"
    assert r.svc.grant(rid, {"allow": [["post", IMPERSONATE[1]]]})[0] == 200  # a method in any case


@pytest.mark.parametrize(
    "body",
    [
        [],
        "x",
        {"allow": []},
        {"allow": "POST"},
        {"allow": [["POST"]]},
        {"allow": [["POST", 1]]},
        {"allow": [7]},
    ],
)
def test_a_malformed_allow_is_400_bad_allow(app: App, body: Any) -> None:
    r = Rig(app)
    rid = r.card()["id"]
    assert r.svc.grant(rid, body) == (400, {"error": "bad-allow"})
    assert r.svc.pending.record(rid)["status"] == "pending"


def test_a_denied_pair_is_not_carded_again_on_a_later_step(app: App) -> None:
    r = Rig(app, {"ZALOGUJ JAKO": [IMPERSONATE, CHAT], "Dalej": [CHAT, LATER]})
    rid = r.card(TWO_STEPS)["id"]
    status, body = r.svc.grant(rid, {"allow": [list(IMPERSONATE)]})
    assert status == 409 and body["request"]["step"] == 1, body
    assert [tuple(b) for b in body["request"]["blocked"]] == [LATER]  # CHAT stays denied, never asked
    assert [tuple(d) for d in body["request"]["denials"][0]["requests"]] == [CHAT, PRE_CHAT]
    assert r.svc.grant(body["request"]["id"])[0] == 200  # the next card's plain grant keeps the denial
    assert CHAT not in r.allowed() and LATER in r.allowed()


def test_preflight_of_a_granted_request_is_allowed_and_of_a_denied_one_is_not(app: App) -> None:
    effects = {"ZALOGUJ JAKO": [PRE_IMP, PRE_CHAT, IMPERSONATE, CHAT]}
    r = Rig(app, effects)
    card = r.card()
    assert len(card["blocked"]) == 4
    assert r.svc.grant(card["id"], {"allow": [list(IMPERSONATE)]})[0] == 200
    assert PRE_IMP in r.allowed() and PRE_CHAT not in r.allowed() and CHAT not in r.allowed()


def test_a_denied_post_denies_its_preflight_twin_too(app: App) -> None:
    r = Rig(app, {"ZALOGUJ JAKO": [IMPERSONATE, CHAT, PRE_CHAT]})
    rid = r.card()["id"]
    assert r.svc.grant(rid, {"allow": [list(IMPERSONATE)]})[0] == 200  # PRE_CHAT: denied with CHAT


def test_allowing_a_preflight_alone_keeps_its_post_denied(app: App) -> None:
    r = Rig(app, {"ZALOGUJ JAKO": [PRE_IMP, IMPERSONATE]})
    rid = r.card()["id"]
    assert r.svc.grant(rid, {"allow": [list(PRE_IMP)]})[0] == 200
    assert PRE_IMP in r.allowed() and IMPERSONATE not in r.allowed()


def test_a_submit_has_no_preflight_twin(app: App) -> None:
    submit = ("SUBMIT", "https://panel.example/f")
    r = Rig(app, {"ZALOGUJ JAKO": [submit, CHAT]})
    rid = r.card()["id"]
    assert r.svc.grant(rid, {"allow": [list(submit)]})[0] == 200
    assert ("OPTIONS", submit[1]) not in r.allowed()


def test_reuse_covers_only_the_granted_pairs(app: App) -> None:
    r = Rig(app)
    rid = r.card()["id"]
    assert r.svc.grant(rid, {"allow": [list(IMPERSONATE)]})[0] == 200
    (entry,) = r.svc.reuse._entries.values()
    assert set(entry.until) == {IMPERSONATE, PRE_IMP}
    status, _ = r.svc.capture(r.did, SPEC, follows=rid)  # a fix run: the grant and the denial are reused
    assert status == 200 and IMPERSONATE in r.allowed() and CHAT not in r.allowed()


def test_a_click_card_has_no_requests_to_pick(app: App) -> None:
    r = Rig(app, {"Zapłać": [CHAT]})
    r.pages.clear()
    spec = {**SPEC, "steps": [{"click": "Zapłać"}]}
    r.launcher.make = lambda: ShotPage(["Zapłać", "Domeny"], r.effects)
    rid = r.card(spec)["id"]
    assert r.svc.grant(rid, {"allow": [list(CHAT)]}) == (400, {"error": "not-blocked"})
    assert r.svc.grant(rid)[0] == 409  # click granted, its POST is the next card


def test_over_http_the_body_picks_the_pairs(app: App) -> None:
    r = Rig(app)
    app.httpd.captures = r.svc
    rid = r.card()["id"]
    assert app.call("POST", f"/api/captures/{rid}/grant", {"allow": [["POST", "https://x.example/"]]}) == (
        400,
        {"error": "not-blocked"},
    )
    status, _ = app.call("POST", f"/api/captures/{rid}/grant", {"allow": [list(IMPERSONATE)]})
    assert status == 200 and CHAT not in r.allowed()


def test_a_400_keeps_the_held_answer_waiting_on_the_same_card(app: App) -> None:
    saving(app)
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    bad = {"allow": [["POST", "https://elsewhere.example/"]]}
    assert app.call("POST", f"/api/captures/{rid}/grant", bad) == (400, {"error": "not-blocked"})
    assert [x["id"] for x in held(app, did)["held"]] == [h["id"]]
    assert [x["id"] for x in held(app, did)["held"][0]["requests"]] == [rid]
    assert grant_http(app, rid)[0] == 200
