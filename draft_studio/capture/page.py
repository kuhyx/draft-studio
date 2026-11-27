"""The real Page (runner.Page) over CDP, and the network guard every target shares.

Fetch.enable pauses every request of every attached target (pages, popups,
workers) at the Request stage, before anything is sent. The guard continues
GET/HEAD and fails everything else with BlockedByClient. While a click is
being watched, a refused request is reported back (and becomes a permission
request); outside a click (page load, waits) it is failed and only logged.
A granted step lists exact (method, url) pairs, each let through once.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.policy import Target
from draft_studio.capture.runner import Request
from draft_studio.capture.script import SCRIPT

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
SAFE_METHODS = ("GET", "HEAD")
# 1280x800 CSS px at device scale 2: a 2560x1600 PNG. The PDF export scales a
# screenshot down to the A4 text width; on a phone kuhy's reader zooms in, and
# at scale 1 the panel's 12-14 px labels blur into unreadable smears.
WIDTH, HEIGHT, SCALE = 1280, 800, 2


class Guard:
    """Answers Fetch.requestPaused for every session."""

    def __init__(self, cdp: Cdp, clock: Callable[[], float]) -> None:
        self.cdp, self.clock = cdp, clock
        self.watching = False
        self.allowed: list[Request] = []
        self.blocked: list[Request] = []
        self.background: list[Request] = []
        self.last = 0.0

    def handle(self, msg: Json) -> None:
        """Continue a safe request or an allowed one (once); fail the rest before they leave."""
        params, session = msg["params"], msg.get("sessionId")
        method, url = str(params["request"]["method"]).upper(), str(params["request"]["url"])
        self.last = self.clock()
        rid = params["requestId"]
        key = (method, url)
        if method in SAFE_METHODS or (self.watching and key in self.allowed):
            if method not in SAFE_METHODS:
                self.allowed.remove(key)
                LOG.info("capture: granted %s %s", method, url)
            self.cdp.send("Fetch.continueRequest", session, requestId=rid)
            return
        self.cdp.send("Fetch.failRequest", session, requestId=rid, errorReason="BlockedByClient")
        (self.blocked if self.watching else self.background).append(key)
        LOG.info("capture: blocked %s %s", method, url)

    def begin(self, allowed: list[Request]) -> None:
        """Start watching a click."""
        self.watching, self.allowed, self.blocked = True, list(allowed), []

    def end(self) -> list[Request]:
        """Stop watching; what the click tried to send and was refused."""
        blocked, self.blocked, self.watching, self.allowed = self.blocked, [], False, []
        return blocked


class CdpPage:
    """One tab, reached through its flattened session id."""

    def __init__(
        self,
        cdp: Cdp,
        session: str,
        guard: Guard,
        clock: Callable[[], float],
        *,
        settle: float = 0.5,
        step_timeout: float = 10.0,
    ) -> None:
        self.cdp, self.session, self.guard, self.clock = cdp, session, guard, clock
        self.settle, self.step_timeout = settle, step_timeout

    def js(self, expr: str) -> Any:
        """Evaluate after (re)installing the helper; page exceptions are ChromiumFailed."""
        out = self.cdp.call(
            "Runtime.evaluate",
            self.session,
            expression=f"{SCRIPT};\n{expr}",
            returnByValue=True,
            awaitPromise=True,
        )
        if "exceptionDetails" in out:
            raise errors.ChromiumFailed(f"page script: {json.dumps(out['exceptionDetails'])[:300]}")
        return out.get("result", {}).get("value")

    def _wait_quiet(self) -> None:
        """Until the document loaded and no request paused for `settle` s, or the step timeout."""
        start = self.clock()
        while self.clock() - start < self.step_timeout:
            try:
                ready = self.js("document.readyState") == "complete"
            except errors.ChromiumFailed:  # mid-navigation: the old context is gone
                ready = False
            if ready and self.clock() - self.guard.last >= self.settle:
                return
            self.cdp.pump(0.1)

    def goto(self, url: str) -> None:
        """Navigate, then wait for the page to go quiet."""
        res = self.cdp.call("Page.navigate", self.session, url=url)
        if res.get("errorText"):
            raise errors.ChromiumFailed(f"navigate {url}: {res['errorText']}")
        self._wait_quiet()

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
        """A real mouse click at the hit-tested centre, watched by the guard until quiet."""
        point = self.js(f"__ds.point({target.ref})")
        if not point or not point["hit"]:
            raise errors.Covered(target.text)
        self.js(f"__ds.allow = {json.dumps(allow_submit)}")
        self.guard.begin(allowed)
        x, y = point["x"], point["y"]
        self.cdp.call("Input.dispatchMouseEvent", self.session, type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            self.cdp.call(
                "Input.dispatchMouseEvent", self.session, type=kind, x=x, y=y, button="left", clickCount=1
            )
        self.guard.last = self.clock()
        self._wait_quiet()
        submits = self.js("__ds.takeSubmits()") or []
        self.js("__ds.allow = false")
        return self.guard.end() + [("SUBMIT", str(s)) for s in submits]

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
    """Interception on a newly attached target, before it runs anything."""
    cdp.send("Network.enable", session)
    cdp.send("Network.setBypassServiceWorker", session, bypass=True)
    cdp.send("Fetch.enable", session, patterns=[{"urlPattern": "*", "requestStage": "Request"}])
    if kind == "page":
        cdp.send("Page.addScriptToEvaluateOnNewDocument", session, source=SCRIPT)
    cdp.send("Runtime.runIfWaitingForDebugger", session)


def open_page(cdp: Cdp, clock: Callable[[], float]) -> CdpPage:
    """Auto-attach (paused) to every target, open our tab, set its viewport."""
    guard = Guard(cdp, clock)
    sessions: dict[str, str] = {}

    def attached(msg: Json) -> None:
        info, session = msg["params"]["targetInfo"], msg["params"]["sessionId"]
        sessions[info["targetId"]] = session
        _arm(cdp, session, info["type"])

    cdp.on("Fetch.requestPaused", guard.handle)
    cdp.on("Target.attachedToTarget", attached)
    cdp.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=True, flatten=True)
    target = cdp.call("Target.createTarget", url="about:blank")["targetId"]
    while target not in sessions:
        cdp.pump(0.05)
    session = sessions[target]
    cdp.call("Page.enable", session)
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        session,
        width=WIDTH,
        height=HEIGHT,
        deviceScaleFactor=SCALE,
        mobile=False,
    )
    return CdpPage(cdp, session, guard, clock)
