"""The CDP session: inline event dispatch, one deadline, named failures."""

from __future__ import annotations

import json
from typing import Any

import pytest

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.wsock import WsClosed


class FakeSock:
    def settimeout(self, value: float) -> None:
        self.timeout = value


class FakeWs:
    """Answers each sent call from `script(msg) -> list of messages to deliver`."""

    def __init__(self, script: Any = None) -> None:
        self.inbox: list[Any] = []
        self.sent: list[dict[str, Any]] = []
        self.script = script or (lambda msg: [{"id": msg["id"], "result": {"ok": msg["method"]}}])
        self.sock = FakeSock()
        self.closed = False

    def send(self, text: str) -> None:
        msg = json.loads(text)
        self.sent.append(msg)
        self.inbox.extend(self.script(msg))

    def readable(self, timeout: float) -> bool:
        return bool(self.inbox)

    def recv(self) -> str:
        item = self.inbox.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


def test_call_dispatches_events_while_waiting_and_skips_other_answers() -> None:
    seen: list[str] = []

    def script(msg: dict[str, Any]) -> list[Any]:
        return [
            {"method": "Fetch.requestPaused", "params": {"n": 1}},
            {"method": "Network.dataReceived", "params": {}},
            {"id": 999, "error": {"message": "stale"}},
            {"id": msg["id"], "result": {"frameId": "f"}},
        ]

    ws = FakeWs(script)
    cdp = Cdp(ws, deadline=10, clock=Clock())
    cdp.on("Fetch.requestPaused", lambda m: seen.append(m["method"]))
    assert cdp.call("Page.navigate", "s1", url="http://x/") == {"frameId": "f"}
    assert ws.sent == [
        {"id": 1, "method": "Page.navigate", "params": {"url": "http://x/"}, "sessionId": "s1"}
    ]
    assert seen == ["Fetch.requestPaused"]
    cdp.close()
    assert ws.closed


def test_error_answers_are_chromium_failed() -> None:
    ws = FakeWs(lambda msg: [{"id": msg["id"], "error": {"message": "No target"}}])
    with pytest.raises(errors.ChromiumFailed, match="No target"):
        Cdp(ws, deadline=10, clock=Clock()).call("Target.attachToTarget")


def test_pump_runs_until_its_time_is_up() -> None:
    ws = FakeWs()
    ws.inbox.append({"method": "Page.loadEventFired", "params": {}})
    got: list[str] = []
    cdp = Cdp(ws, deadline=10, clock=Clock())
    cdp.on("Page.loadEventFired", lambda m: got.append("load"))
    cdp.pump(0.1)
    assert got == ["load"]


def test_deadline_and_transport_failures() -> None:
    clock = Clock()
    cdp = Cdp(FakeWs(lambda msg: []), deadline=0.05, clock=clock)
    with pytest.raises(errors.CaptureTimeout):
        cdp.call("Runtime.evaluate")
    for failure, exc in [
        (TimeoutError(), errors.CaptureTimeout),
        (WsClosed("eof"), errors.ChromiumFailed),
        ("{not json", errors.ChromiumFailed),
    ]:
        cdp = Cdp(FakeWs(lambda msg, f=failure: [f]), deadline=10, clock=Clock())
        with pytest.raises(exc):
            cdp.call("Runtime.evaluate")


def test_send_failure_is_named() -> None:
    ws = FakeWs()

    def broken(text: str) -> None:
        raise BrokenPipeError

    ws.send = broken
    with pytest.raises(errors.ChromiumFailed, match=r"send Browser\.close"):
        Cdp(ws, deadline=10, clock=Clock()).send("Browser.close")
