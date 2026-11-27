"""Opening the capture tab and arming every target chromium attaches, before it runs anything.

Fetch.enable pauses each request of an armed target for the guard (guard.py).
Fail closed: a target whose requests cannot be intercepted fails the capture
(Unguarded, `unguarded-target`) instead of running unwatched, and a page tab
is registered (so it can be followed and shot) only once it is armed.
One shape has no Fetch domain of its own: a dedicated worker ("worker"
target: Fetch.enable answers "'Fetch.enable' wasn't found"). Its requests,
nested workers' and blob: workers' included, go through the frame that
started it and are paused by that frame's Fetch
(tests/capture_live/test_capture_worker_live.py). So a worker skips
Fetch.enable only when it attached under a frame armed here; any other
worker, and every other type, must enable it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.guard import Guard
from draft_studio.capture.page import CdpPage
from draft_studio.capture.script import SCRIPT
from draft_studio.capture.tabs import Tabs

Json = dict[str, Any]
FRAMES = ("page", "iframe")


def arm(cdp: Cdp, session: str, kind: str, *, under_frame: bool) -> None:
    """Interception on a newly attached (paused) target, then let it run.

    `under_frame`: it attached under a page or iframe session that was armed.
    Pages and frames also auto-attach their own children (cross-site frames run
    in their own process and are not covered by the parent's Fetch.enable).
    """
    cdp.send("Network.enable", session)
    if not (kind == "worker" and under_frame):
        cdp.send("Network.setBypassServiceWorker", session, bypass=True)
        try:
            cdp.call("Fetch.enable", session, patterns=[{"urlPattern": "*", "requestStage": "Request"}])
        except errors.ChromiumFailed as exc:
            raise errors.Unguarded(kind, str(exc.extra.get("detail"))) from exc
    if kind in FRAMES:
        cdp.send("Page.addScriptToEvaluateOnNewDocument", session, source=SCRIPT)
        cdp.call("Target.setAutoAttach", session, autoAttach=True, waitForDebuggerOnStart=True, flatten=True)
    cdp.send("Runtime.runIfWaitingForDebugger", session)


def open_page(cdp: Cdp, clock: Callable[[], float]) -> CdpPage:
    """Auto-attach (paused) to every target, open our tab, set its viewport."""
    guard, tabs = Guard(cdp, clock), Tabs(cdp)
    sessions: dict[str, str] = {}
    frames: set[str] = set()  # sessions of armed pages and iframes

    def attached(msg: Json) -> None:
        info, session = msg["params"]["targetInfo"], msg["params"]["sessionId"]
        kind = info["type"]
        arm(cdp, session, kind, under_frame=msg.get("sessionId") in frames)
        sessions[info["targetId"]] = session
        if kind in FRAMES:
            frames.add(session)
        if kind == "page":
            tabs.attached(info["targetId"], session)

    def detached(msg: Json) -> None:
        frames.discard(msg["params"]["sessionId"])
        tabs.detached(msg["params"]["sessionId"])
        guard.forget(msg["params"]["sessionId"])

    cdp.on("Target.attachedToTarget", attached)
    cdp.on("Target.detachedFromTarget", detached)
    # Browser-wide as well: it still holds what a tab sends after its own session is gone.
    cdp.call("Fetch.enable", patterns=[{"urlPattern": "*", "requestStage": "Request"}])
    cdp.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=True, flatten=True)
    target = cdp.call("Target.createTarget", url="about:blank")["targetId"]
    while target not in sessions:
        cdp.pump(0.05)
    tabs.follow(target)
    return CdpPage(cdp, tabs, guard, clock)
