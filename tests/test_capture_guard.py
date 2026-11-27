"""The network guard over a scripted fake CDP connection: verdicts, preflights, writes still on their way."""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture.guard import Guard
from tests.capture_fakes import FakeCdp, paused, will_send


def test_guard_continues_gets_and_fails_other_methods_before_they_are_sent() -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.handle(paused("GET", "http://x/a"))
    guard.handle(paused("POST", "http://x/beacon", "r2"))
    assert cdp.sent == [
        ("Fetch.continueRequest", "s1", {"requestId": "r1"}),
        ("Fetch.failRequest", "s1", {"requestId": "r2", "errorReason": "BlockedByClient"}),
    ]
    assert guard.background == [("POST", "http://x/beacon")]
    guard.begin([("POST", "http://x/api")])
    guard.handle(paused("POST", "http://x/api", "r3"))
    guard.handle(paused("POST", "http://x/api", "r4"))
    guard.handle(paused("delete", "http://x/item", "r5"))
    assert cdp.sent[2] == ("Fetch.continueRequest", "s1", {"requestId": "r3"})
    assert guard.end() == [("POST", "http://x/api"), ("DELETE", "http://x/item")]
    assert guard.end() == []


def preflight(announced: str | None, name: str = "Access-Control-Request-Method") -> dict[str, Any]:
    """An OPTIONS request paused with the CORS preflight header (none when `announced` is None)."""
    headers = {"Origin": "https://panel.x"} | ({name: announced} if announced is not None else {})
    return paused("OPTIONS", "https://api.x/v1/me", headers=headers)


@pytest.mark.parametrize(
    ("announced", "name"),
    [
        ("GET", "Access-Control-Request-Method"),
        ("HEAD", "Access-Control-Request-Method"),
        ("get", "Access-Control-Request-Method"),
        (" Head ", "Access-Control-Request-Method"),
        ("GET", "access-control-request-method"),
        ("GET", "ACCESS-CONTROL-REQUEST-METHOD"),
    ],
)
def test_guard_lets_a_cors_preflight_for_a_read_through(announced: str, name: str) -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.begin([])
    guard.handle(preflight(announced, name))
    assert cdp.sent == [("Fetch.continueRequest", "s1", {"requestId": "r1"})]
    assert guard.end() == [] and guard.background == []


@pytest.mark.parametrize("announced", ["POST", "put", "PATCH", "DELETE", "OPTIONS", "GET, POST", "", None])
def test_guard_still_refuses_every_other_options_request(announced: str | None) -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.begin([])
    guard.handle(preflight(announced))
    assert cdp.sent == [("Fetch.failRequest", "s1", {"requestId": "r1", "errorReason": "BlockedByClient"})]
    assert guard.end() == [("OPTIONS", "https://api.x/v1/me")]


def test_guard_refuses_options_without_any_headers_and_non_options_with_the_header() -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.handle(paused("OPTIONS", "https://api.x/a"))
    guard.handle(paused("POST", "https://api.x/b", "r2", headers={"Access-Control-Request-Method": "GET"}))
    assert [m for m, _, _ in cdp.sent] == ["Fetch.failRequest", "Fetch.failRequest"]
    assert guard.background == [("OPTIONS", "https://api.x/a"), ("POST", "https://api.x/b")]


def test_a_request_paused_twice_keeps_its_first_verdict() -> None:
    # The browser-wide interception pauses again what a tab's own interception already let through.
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.begin([("POST", "http://x/api")])
    guard.handle(paused("POST", "http://x/api", "f1", network="n1"))
    guard.handle(paused("POST", "http://x/api", "f2", session="", network="n1"))
    assert cdp.sent == [
        ("Fetch.continueRequest", "s1", {"requestId": "f1"}),
        ("Fetch.continueRequest", "", {"requestId": "f2"}),
    ]
    guard.handle(paused("POST", "http://x/elsewhere", "f3", network="n1"))  # redirected: judged anew
    guard.handle(paused("POST", "http://x/elsewhere", "f4", session="", network="n1"))
    assert [m for m, _, _ in cdp.sent[2:]] == ["Fetch.failRequest", "Fetch.failRequest"]
    assert guard.end() == [("POST", "http://x/elsewhere")]


def test_a_write_is_pending_from_its_announcement_until_paused_failed_finished_or_its_tab_closed() -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    will = cdp.handlers["Network.requestWillBeSent"]
    will(will_send("GET", "http://x/a", "g1"))  # reads always go through: never waited on
    assert not guard.pending and guard.last == 0.0
    cdp.now = 2.0
    for method, url, network, session in [
        ("POST", "http://x/a", "n1", "s1"),
        ("PUT", "http://x/b", "n2", "s1"),
        ("DELETE", "http://x/c", "n3", "s1"),
        ("PATCH", "http://x/d", "n4", "s2"),
    ]:
        will(will_send(method, url, network, session))
    assert len(guard.pending) == 4 and guard.last == 2.0  # an announcement is activity too
    cdp.handlers["Fetch.requestPaused"](paused("POST", "http://x/a", network="n1"))
    cdp.handlers["Network.loadingFailed"]({"params": {"requestId": "n2"}})
    cdp.handlers["Network.loadingFinished"]({"params": {"requestId": "n3"}})
    guard.forget("s2")  # its tab closed: no event about it will ever come
    assert not guard.pending
    # Paused before its announcement arrived (two processes, no order between them): not pending.
    cdp.handlers["Fetch.requestPaused"](paused("POST", "http://x/e", "r5", network="n5"))
    will(will_send("POST", "http://x/e", "n5"))
    assert not guard.pending


def test_a_write_still_on_its_way_when_the_click_ends_is_refused() -> None:
    cdp = FakeCdp()
    guard = Guard(cdp, cdp.clock)
    guard.begin([("POST", "http://x/api")])
    cdp.handlers["Network.requestWillBeSent"](will_send("POST", "http://x/api", "n1"))
    assert guard.end() == [("POST", "http://x/api")]  # fail closed: never a success it cannot vouch for
    assert not guard.pending
    guard.handle(paused("POST", "http://x/api", network="n1"))  # arriving late: refused, not granted
    assert cdp.sent == [("Fetch.failRequest", "s1", {"requestId": "r1", "errorReason": "BlockedByClient"})]
