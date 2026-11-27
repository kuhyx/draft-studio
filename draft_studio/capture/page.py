"""The real Page (runner.Page) over CDP: navigate, click under the guard, outline, screenshot.

What may leave chromium, and which writes are still on their way, is guard.py.
A wait ends only once the tab loaded, no write is pending and no request paused
for `settle` s. Which tab is driven, and following the tab a click opens, is tabs.py.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.guard import Guard
from draft_studio.capture.policy import Target
from draft_studio.capture.runner import Request
from draft_studio.capture.script import SCRIPT
from draft_studio.capture.tabs import Tabs

Json = dict[str, Any]


class CdpPage:
    """The driven tab (tabs.session), which a clean click moves to the tab it opened."""

    def __init__(
        self,
        cdp: Cdp,
        tabs: Tabs,
        guard: Guard,
        clock: Callable[[], float],
        *,
        settle: float = 0.5,
        step_timeout: float = 10.0,
    ) -> None:
        self.cdp, self.tabs, self.guard, self.clock = cdp, tabs, guard, clock
        self.settle, self.step_timeout = settle, step_timeout

    @property
    def session(self) -> str:
        """Flattened session id of the driven tab."""
        return self.tabs.session

    def js(self, expr: str, session: str | None = None) -> Any:
        """Evaluate on `session` (default: the driven tab) after (re)installing the helper.

        Page exceptions are ChromiumFailed.
        """
        out = self.cdp.call(
            "Runtime.evaluate",
            session or self.session,
            expression=f"{SCRIPT};\n{expr}",
            returnByValue=True,
            awaitPromise=True,
        )
        if "exceptionDetails" in out:
            raise errors.ChromiumFailed(f"page script: {json.dumps(out['exceptionDetails'])[:300]}")
        return out.get("result", {}).get("value")

    def _wait_quiet(self, session: str | None = None) -> None:
        """Until the tab loaded, no write is pending and no request paused for `settle` s (or the
        tab closed, or the step timeout)."""
        watched, start = session or self.session, self.clock()
        while watched in self.tabs.pages.values() and self.clock() - start < self.step_timeout:
            try:
                ready = self.js("document.readyState", session) == "complete"
            except errors.ChromiumFailed:  # mid-navigation: the old context is gone
                ready = False
            if ready and not self.guard.pending and self.clock() - self.guard.last >= self.settle:
                break
            self.cdp.pump(0.1)

    def goto(self, url: str) -> None:
        """Navigate, then wait for the page to go quiet."""
        res = self.cdp.call("Page.navigate", self.session, url=url)
        if res.get("errorText"):
            raise errors.ChromiumFailed(f"navigate {url}: {res['errorText']}")
        self._wait_quiet()
        self.tabs.close_others()

    def url(self) -> str:
        """location.href."""
        return str(self.js("location.href"))

    def has_password(self) -> bool:
        """A visible password field."""
        return bool(self.js("__ds.hasPassword()"))

    def find(self, text: str) -> list[Target]:
        """Visible elements showing `text`."""
        return [Target.from_js(raw) for raw in self.js(f"__ds.find({json.dumps(text)})")]

    def labels(self) -> list[str]:
        """Labels of visible clickable elements (for a no-target error)."""
        return [str(label) for label in self.js("__ds.labels()")]

    def wait_for(self, text: str) -> bool:
        """Poll until `text` shows, up to the step timeout."""
        start = self.clock()
        while not self.js(f"__ds.seen({json.dumps(text)})"):
            if self.clock() - start >= self.step_timeout:
                return False
            self.cdp.pump(0.1)
        return True

    def click(self, target: Target, allow_submit: bool, allowed: list[Request]) -> list[Request]:
        """A real mouse click at the hit-tested centre, watched by the guard until quiet.

        A tab the click opened is watched too (its load-time requests and form
        submits count as this click's). Nothing refused: the capture follows
        it. Something refused: it is closed and the clicked tab stays driven,
        so the refusal's shot outlines the target kuhy is asked about.
        """
        point = self.js(f"__ds.point({target.ref})")
        if not point or not point["hit"]:
            raise errors.Covered(target.text)
        self.js(f"__ds.allow = {json.dumps(allow_submit)}")
        before, clicked = set(self.tabs.pages), self.session
        self.guard.begin(allowed)
        x, y = point["x"], point["y"]
        self.cdp.call("Input.dispatchMouseEvent", self.session, type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            self.cdp.call(
                "Input.dispatchMouseEvent", self.session, type=kind, x=x, y=y, button="left", clickCount=1
            )
        self.guard.last = self.clock()
        self._wait_quiet()
        if opened := self.tabs.opened_since(before):
            self._wait_quiet(self.tabs.pages[opened])
        opened = self.tabs.opened_since(before)  # it may have closed itself meanwhile
        live = set(self.tabs.pages.values())
        submits: list[Any] = []
        for session in [clicked, self.tabs.pages[opened] if opened else ""]:
            if session in live:
                submits += self.js("__ds.takeSubmits()", session) or []
        if clicked in live:
            self.js("__ds.allow = false", clicked)
        blocked = self.guard.end() + [("SUBMIT", str(s)) for s in submits]
        if opened and not blocked:
            self.tabs.follow(opened)
        self.tabs.close_others()
        return blocked

    def highlight(self, target: Target) -> bool:
        """Outline plus arrow; False when the element is gone."""
        return bool(self.js(f"__ds.highlight({target.ref})"))

    def clear(self) -> None:
        """Remove the overlay."""
        self.js("__ds.clear()")

    def screenshot(self) -> bytes:
        """PNG of the viewport."""
        out = self.cdp.call("Page.captureScreenshot", self.session, format="png")
        return base64.b64decode(out["data"])


def _arm(cdp: Cdp, session: str, kind: str) -> None:
    """Interception on a newly attached target, before it runs anything.

    Fail closed: a target whose requests cannot be intercepted fails the
    capture (ChromiumFailed) instead of running unwatched. Pages and frames
    also auto-attach their own children (cross-site frames run in their own
    process and are not covered by the parent's Fetch.enable).
    """
    cdp.send("Network.enable", session)
    cdp.send("Network.setBypassServiceWorker", session, bypass=True)
    cdp.call("Fetch.enable", session, patterns=[{"urlPattern": "*", "requestStage": "Request"}])
    if kind in ("page", "iframe"):
        cdp.send("Page.addScriptToEvaluateOnNewDocument", session, source=SCRIPT)
        cdp.call("Target.setAutoAttach", session, autoAttach=True, waitForDebuggerOnStart=True, flatten=True)
    cdp.send("Runtime.runIfWaitingForDebugger", session)


def open_page(cdp: Cdp, clock: Callable[[], float]) -> CdpPage:
    """Auto-attach (paused) to every target, open our tab, set its viewport."""
    guard, tabs = Guard(cdp, clock), Tabs(cdp)
    sessions: dict[str, str] = {}

    def attached(msg: Json) -> None:
        info, session = msg["params"]["targetInfo"], msg["params"]["sessionId"]
        sessions[info["targetId"]] = session
        if info["type"] == "page":
            tabs.attached(info["targetId"], session)
        _arm(cdp, session, info["type"])

    def detached(msg: Json) -> None:
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
