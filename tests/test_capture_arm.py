"""open_page and arm over a scripted fake CDP: every target guarded before it runs, or the capture fails."""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.capture import errors
from draft_studio.capture.arm import open_page
from draft_studio.capture.page import CdpPage
from tests.capture_fakes import FakeCdp

ARMED = [
    "Network.enable",
    "Network.setBypassServiceWorker",
    "Fetch.enable",
    "Runtime.runIfWaitingForDebugger",
]
NOT_FOUND = errors.ChromiumFailed("Fetch.enable: 'Fetch.enable' wasn't found")


def attach(cdp: FakeCdp, session: str, target: str, kind: str, parent: str | None = None) -> None:
    """A Target.attachedToTarget event; `parent` is the session it attached under (None: the browser)."""
    msg: dict[str, Any] = {"params": {"sessionId": session, "targetInfo": {"targetId": target, "type": kind}}}
    if parent:
        msg["sessionId"] = parent
    cdp.handlers["Target.attachedToTarget"](msg)


def opened(cdp: FakeCdp) -> CdpPage:
    """open_page whose tab t1 attaches as session "main"."""
    cdp.answers.setdefault("Target.createTarget", {"targetId": "t1"})
    cdp.on_pump.append(lambda: attach(cdp, "main", "t1", "page"))
    return open_page(cdp, cdp.clock)


def on(cdp: FakeCdp, session: str) -> list[str]:
    return [m for m, s, _ in cdp.sent if s == session]


def test_open_page_sets_up_interception_everywhere() -> None:
    cdp = FakeCdp()

    def create(params: dict[str, Any]) -> dict[str, Any]:
        attach(cdp, "other", "t0", "service_worker")
        return {"targetId": "t1"}

    cdp.answers["Target.createTarget"] = create
    page = opened(cdp)
    assert page.session == "main" and (page.tabs.target, page.tabs.pages) == ("t1", {"t1": "main"})
    assert on(cdp, "other") == ARMED
    main = on(cdp, "main")
    assert main[:3] == ARMED[:3] and "Page.addScriptToEvaluateOnNewDocument" in main
    assert main[-1] == "Emulation.setDeviceMetricsOverride"
    metrics = next(p for m, s, p in cdp.sent if m == "Emulation.setDeviceMetricsOverride")
    assert (metrics["width"], metrics["height"], metrics["deviceScaleFactor"]) == (1280, 800, 2)
    assert "Fetch.requestPaused" in cdp.handlers
    cdp.handlers["Target.detachedFromTarget"]({"params": {"sessionId": "main"}})
    assert page.tabs.pages == {}  # a tab that closed itself is never followed or closed again


def test_dedicated_worker_under_an_armed_frame_runs_on_the_frame_guard() -> None:
    cdp = FakeCdp()
    page = opened(cdp)
    cdp.answers["Fetch.enable"] = NOT_FOUND  # what chromium answers on a "worker" session
    attach(cdp, "w1", "tw1", "worker", parent="main")
    cdp.answers.pop("Fetch.enable")
    attach(cdp, "f2", "tf2", "iframe", parent="main")  # an iframe is armed like a page ...
    cdp.answers["Fetch.enable"] = NOT_FOUND
    attach(cdp, "w2", "tw2", "worker", parent="f2")  # ... and its workers ride on its guard
    assert on(cdp, "w1") == on(cdp, "w2") == ["Network.enable", "Runtime.runIfWaitingForDebugger"]
    assert page.tabs.pages == {"t1": "main"}


def test_worker_outside_an_armed_frame_must_enable_fetch_itself() -> None:
    cdp = FakeCdp()
    opened(cdp)
    attach(cdp, "w1", "tw1", "worker")  # under the browser connection: no frame covers it
    assert on(cdp, "w1") == ARMED
    cdp.handlers["Target.detachedFromTarget"]({"params": {"sessionId": "main"}})
    cdp.answers["Fetch.enable"] = NOT_FOUND
    with pytest.raises(errors.Unguarded) as caught:
        attach(cdp, "w2", "tw2", "worker", parent="main")  # its frame went away
    assert (caught.value.code, caught.value.extra["kind"]) == ("unguarded-target", "worker")
    assert "Runtime.runIfWaitingForDebugger" not in on(cdp, "w2")  # it stays paused


@pytest.mark.parametrize("kind", ["page", "iframe", "shared_worker", "service_worker", "auction_worklet"])
def test_any_other_target_without_fetch_fails_closed_and_is_never_driven(kind: str) -> None:
    cdp = FakeCdp()
    page = opened(cdp)
    cdp.answers["Fetch.enable"] = NOT_FOUND
    with pytest.raises(errors.Unguarded) as caught:
        attach(cdp, "x", "tx", kind, parent="main")
    assert caught.value.status == 502
    assert caught.value.extra == {"kind": kind, "detail": "Fetch.enable: 'Fetch.enable' wasn't found"}
    assert page.tabs.pages == {"t1": "main"}  # an unguarded popup is never followed or shot
    assert on(cdp, "x") == ARMED[:3]
