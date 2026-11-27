"""CdpPage and the network guard over a scripted fake CDP connection."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from draft_studio.capture import errors
from draft_studio.capture.page import CdpPage, Guard, open_page
from draft_studio.capture.policy import Target


class FakeCdp:
    """Records sends; `answers[method]` is a value or a callable(params) for call() results."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, dict[str, Any]]] = []
        self.answers: dict[str, Any] = {}
        self.handlers: dict[str, Any] = {}
        self.now = 0.0
        self.on_pump: list[Any] = []

    def on(self, method: str, handler: Any) -> None:
        self.handlers[method] = handler

    def send(self, method: str, session: str | None = None, **params: Any) -> int:
        self.sent.append((method, session, params))
        return len(self.sent)

    def call(self, method: str, session: str | None = None, **params: Any) -> dict[str, Any]:
        self.send(method, session, **params)
        answer = self.answers.get(method, {})
        out = answer(params) if callable(answer) else answer
        if isinstance(out, Exception):
            raise out
        return out

    def pump(self, seconds: float) -> None:
        self.now += seconds
        if self.on_pump:
            self.on_pump.pop(0)()

    def clock(self) -> float:
        return self.now

    def methods(self) -> list[str]:
        return [m for m, _, _ in self.sent]


def paused(method: str, url: str, rid: str = "r1", session: str = "s1") -> dict[str, Any]:
    return {"sessionId": session, "params": {"requestId": rid, "request": {"method": method, "url": url}}}


def expr_key(params: dict[str, Any]) -> str:
    """The call after the idempotent install prefix."""
    return str(params["expression"]).rsplit(";\n", 1)[-1]


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


def ready_page(cdp: FakeCdp, **kw: Any) -> CdpPage:
    cdp.answers["Runtime.evaluate"] = lambda p: {"result": {"value": "complete"}}
    return CdpPage(cdp, "s1", Guard(cdp, cdp.clock), cdp.clock, **kw)


def test_open_page_sets_up_interception_everywhere() -> None:
    cdp = FakeCdp()

    def create(params: dict[str, Any]) -> dict[str, Any]:
        cdp.handlers["Target.attachedToTarget"](
            {"params": {"sessionId": "other", "targetInfo": {"targetId": "t0", "type": "service_worker"}}}
        )
        return {"targetId": "t1"}

    cdp.answers["Target.createTarget"] = create
    cdp.on_pump.append(
        lambda: cdp.handlers["Target.attachedToTarget"](
            {"params": {"sessionId": "main", "targetInfo": {"targetId": "t1", "type": "page"}}}
        )
    )
    page = open_page(cdp, cdp.clock)
    assert page.session == "main"
    worker = [m for m, s, _ in cdp.sent if s == "other"]
    assert worker == [
        "Network.enable",
        "Network.setBypassServiceWorker",
        "Fetch.enable",
        "Runtime.runIfWaitingForDebugger",
    ]
    main = [m for m, s, _ in cdp.sent if s == "main"]
    assert (
        "Page.addScriptToEvaluateOnNewDocument" in main and main[-1] == "Emulation.setDeviceMetricsOverride"
    )
    metrics = next(p for m, s, p in cdp.sent if m == "Emulation.setDeviceMetricsOverride")
    assert (metrics["width"], metrics["height"], metrics["deviceScaleFactor"]) == (1280, 800, 2)
    assert "Fetch.requestPaused" in cdp.handlers


def test_goto_waits_for_load_and_names_navigation_errors() -> None:
    cdp = FakeCdp()
    states = iter([errors.ChromiumFailed("context destroyed"), {"result": {"value": "loading"}}])
    page = ready_page(cdp)
    cdp.answers["Runtime.evaluate"] = lambda p: next(states, {"result": {"value": "complete"}})
    page.goto("http://x/")
    assert ("Page.navigate", "s1", {"url": "http://x/"}) in cdp.sent
    cdp.answers["Page.navigate"] = {"errorText": "net::ERR_CONNECTION_REFUSED"}
    with pytest.raises(errors.ChromiumFailed, match="ERR_CONNECTION_REFUSED"):
        page.goto("http://x/")


def test_settle_gives_up_at_the_step_timeout() -> None:
    cdp = FakeCdp()
    page = ready_page(cdp, step_timeout=1.0)
    cdp.answers["Runtime.evaluate"] = {"result": {"value": "loading"}}
    page.goto("http://x/")  # a page that never finishes loading still gets its screenshot
    assert cdp.now >= 1.0


def test_js_exceptions_are_named() -> None:
    cdp = FakeCdp()
    page = ready_page(cdp)
    cdp.answers["Runtime.evaluate"] = {"exceptionDetails": {"text": "boom"}}
    with pytest.raises(errors.ChromiumFailed, match="boom"):
        page.url()


def test_reads_find_wait_highlight_clear_and_shot() -> None:
    cdp = FakeCdp()
    page = ready_page(cdp)
    values: dict[str, Any] = {
        "location.href": "http://x/a",
        "__ds.hasPassword()": True,
        '__ds.find("Domeny")': [{"ref": 3, "tag": "a", "text": "Domeny"}],
        '__ds.seen("Lista")': True,
        "__ds.highlight(3)": True,
        "__ds.labels()": ["Domeny"],
    }
    cdp.answers["Runtime.evaluate"] = lambda p: {"result": {"value": values.get(expr_key(p))}}
    cdp.answers["Page.captureScreenshot"] = {"data": base64.b64encode(b"\x89PNG").decode()}
    assert page.url() == "http://x/a" and page.has_password() is True
    assert page.find("Domeny") == [Target(ref=3, tag="a", text="Domeny")]
    assert page.wait_for("Lista") is True and page.labels() == ["Domeny"]
    assert page.highlight(Target(ref=3, tag="a")) is True
    page.clear()
    assert page.screenshot() == b"\x89PNG"
    values['__ds.seen("Lista")'] = False
    assert page.wait_for("Lista") is False


def test_click_reports_blocked_requests_and_submits() -> None:
    cdp = FakeCdp()
    page = ready_page(cdp)
    script: dict[str, Any] = {
        "__ds.point(1)": {"x": 10, "y": 20, "hit": True},
        "__ds.takeSubmits()": ["http://x/f"],
    }

    def press(p: dict[str, Any]) -> Any:
        if p["type"] == "mousePressed":
            page.guard.handle(paused("POST", "http://x/api", "r9"))
        return {}

    cdp.answers["Runtime.evaluate"] = lambda p: {"result": {"value": script.get(expr_key(p), "complete")}}
    cdp.answers["Input.dispatchMouseEvent"] = press
    blocked = page.click(Target(ref=1, tag="button"), allow_submit=True, allowed=[])
    assert blocked == [("POST", "http://x/api"), ("SUBMIT", "http://x/f")]
    mouse = [p["type"] for m, _, p in cdp.sent if m == "Input.dispatchMouseEvent"]
    assert mouse == ["mouseMoved", "mousePressed", "mouseReleased"]
    exprs = [expr_key(p) for m, _, p in cdp.sent if m == "Runtime.evaluate"]
    assert "__ds.allow = true" in exprs and exprs[-1] == "__ds.allow = false"


@pytest.mark.parametrize("point", [None, {"x": 1, "y": 1, "hit": False}])
def test_click_refuses_a_covered_or_vanished_target(point: Any) -> None:
    cdp = FakeCdp()
    page = ready_page(cdp)
    cdp.answers["Runtime.evaluate"] = {"result": {"value": point}}
    with pytest.raises(errors.Covered) as err:
        page.click(Target(ref=1, tag="a", text="Domeny"), allow_submit=False, allowed=[])
    assert err.value.extra == {"text": "Domeny"}
    assert "Input.dispatchMouseEvent" not in cdp.methods()
