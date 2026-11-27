"""Which tab a capture drives, and keeping it the only tab open.

Chromium starts with an about:blank tab, and a click may open more
(window.open, target=_blank). In a how-to the next step usually happens on
the page that just opened, so a click that was not refused follows the
newest tab it opened (page.py decides when). After every settle all other
tabs are closed and the driven one is brought to the front: a background tab
is hidden, requestAnimationFrame stops, and the highlight (which waits two
frames) would hang until the capture timeout. Every tab was armed when it
attached (arm.py: guarded fetch, page script), so a followed tab obeys the
same rules as the first one.
"""

from __future__ import annotations

from draft_studio.capture.cdp import Cdp

# 1280x800 CSS px at device scale 2: a 2560x1600 PNG. The PDF export scales a
# screenshot down to the A4 text width; on a phone kuhy's reader zooms in, and
# at scale 1 the panel's 12-14 px labels blur into unreadable smears.
WIDTH, HEIGHT, SCALE = 1280, 800, 2


class Tabs:
    """Every attached page tab (targetId -> flattened session) and the one being driven."""

    def __init__(self, cdp: Cdp) -> None:
        self.cdp = cdp
        self.pages: dict[str, str] = {}  # attach order: the last one is the newest
        self.target = self.session = ""

    def attached(self, target: str, session: str) -> None:
        """A page tab attached (paused, already armed by the caller)."""
        self.pages[target] = session

    def detached(self, session: str) -> None:
        """A tab went away (closed itself, or we closed it): never follow or close it again."""
        for target in [t for t, s in self.pages.items() if s == session]:
            del self.pages[target]

    def opened_since(self, before: set[str]) -> str | None:
        """The newest tab that attached after `before` was taken, if any."""
        new = [t for t in self.pages if t not in before]
        return new[-1] if new else None

    def follow(self, target: str) -> None:
        """Drive `target` from now on, at the capture viewport."""
        self.target, self.session = target, self.pages[target]
        self.cdp.call("Page.enable", self.session)
        self.cdp.call(
            "Emulation.setDeviceMetricsOverride",
            self.session,
            width=WIDTH,
            height=HEIGHT,
            deviceScaleFactor=SCALE,
            mobile=False,
        )

    def close_others(self) -> None:
        """Close every tab but the driven one and bring it to the front.

        The closed tabs were guarded while they lived; only the driven tab is shot.
        """
        others = [t for t in self.pages if t != self.target]
        for target in others:
            del self.pages[target]
            self.cdp.send("Target.closeTarget", targetId=target)  # fire and forget: it may have closed itself
        if others:
            self.cdp.call("Page.bringToFront", self.session)
