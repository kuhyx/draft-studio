"""A blocked preflight names the write it announces (kuhy, 2026-11-27: the robofolks chat card read `OPTIONS <url>`).

The card's `blocked` pairs stay (method, URL) so grant matching is unchanged; the record gains
`announces`, aligned index by index with `blocked`: an OPTIONS with Access-Control-Request-Method
carries that method upper-cased, every other entry null.
"""

from __future__ import annotations

import json
from typing import Any

from draft_studio.capture.guard import Guard
from draft_studio.capture.preflight import announced
from draft_studio.capture.service import CaptureService
from tests.capture_fakes import FakeCdp, paused
from tests.conftest import App
from tests.test_capture_service import FakeLauncher, ShotPage, draft

SAME = ("POST", "https://panel.example/api/track")
PRE_CHAT = ("OPTIONS", "https://robofolks.panel.example/request")
PRE_LATER = ("OPTIONS", "https://api.panel.example/later")
SPEC: dict[str, Any] = {
    "url": "https://panel.example/shares",
    "name": "chat",
    "steps": [{"click": "Domeny"}, {"click": "Dalej"}],
    "highlight": "Lista",
}


def test_the_header_names_the_write_in_any_case() -> None:
    assert announced({"access-control-request-method": " post "}) == "POST"
    assert announced({"Access-Control-Request-Method": "PUT"}) == "PUT"
    assert announced({"Origin": "https://panel.x"}) is None
    assert announced({"Access-Control-Request-Method": " "}) is None


def test_the_guard_keeps_what_a_blocked_preflight_announced_for_the_click() -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.begin([])
    guard.handle(paused(*PRE_CHAT, headers={"access-control-request-method": "post"}))
    guard.handle(paused("OPTIONS", "https://x.example/bare", "r2"))
    guard.handle(paused(*SAME, "r3", headers={"Access-Control-Request-Method": "PUT"}))
    assert guard.end() == [PRE_CHAT, ("OPTIONS", "https://x.example/bare"), SAME]
    assert guard.announces == {PRE_CHAT: "POST"}  # still readable after the click ended
    guard.begin([])
    assert guard.announces == {}  # a new click starts clean


def _svc(app: App, effects: dict[str, list[tuple[str, str]]]) -> tuple[CaptureService, str]:
    def make() -> ShotPage:
        page = ShotPage(["Domeny", "Dalej", "Lista", "Zakończ udostępnianie"], effects)
        page.announce = {PRE_CHAT: "POST", PRE_LATER: "PUT"}
        return page

    return CaptureService(app.store, FakeLauncher(make)), draft(app)


def test_the_card_carries_announces_aligned_with_blocked(app: App) -> None:
    svc, did = _svc(app, {"Domeny": [SAME, PRE_CHAT]})
    status, body = svc.capture(did, SPEC)
    assert status == 409, body
    rec = body["request"]
    assert [tuple(b) for b in rec["blocked"]] == [SAME, PRE_CHAT]  # the pair itself is unchanged
    assert rec["announces"] == [None, "POST"]
    assert svc.list_pending()[1]["requests"][0]["announces"] == [None, "POST"]


def test_announces_stay_aligned_when_a_denial_drops_an_entry(app: App) -> None:
    svc, did = _svc(app, {"Domeny": [PRE_CHAT], "Dalej": [PRE_CHAT, PRE_LATER, SAME]})
    first = svc.capture(did, SPEC)[1]["request"]
    status, body = svc.deny(first["id"])
    assert status == 409, body
    assert [tuple(b) for b in body["request"]["blocked"]] == [PRE_LATER, SAME]
    assert body["request"]["announces"] == ["PUT", None]


def test_a_click_refusal_announces_nothing_and_old_records_read_as_null(app: App) -> None:
    svc, did = _svc(app, {})
    status, body = svc.capture(did, {**SPEC, "steps": [{"click": "Zakończ udostępnianie"}]})
    assert status == 409 and body["request"]["reason"] == "deny-list", body
    assert body["request"]["blocked"] == [] and body["request"]["announces"] == []
    svc, did = _svc(app, {"Domeny": [SAME, PRE_CHAT]})
    rid = svc.capture(did, SPEC)[1]["request"]["id"]
    path = svc.pending.dir / f"{rid}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    del rec["announces"]  # a card written before announces existed
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert [r["announces"] for r in svc.list_pending()[1]["requests"] if r["id"] == rid] == [[None, None]]
