"""Watch the headed login window and close it once kuhy is logged in, so nobody has to.

The window is never guarded or paused (no Fetch, no debugger pause): kuhy's own tabs and form
posts must run untouched. Over the browser-level DevTools connection this only reads each page's
URL, `readyState` and whether a password field shows (the capture's own test, policy.is_login_page).

Logged in = a login page was seen (on any origin: SSO sites count), and now every page is off
login pages while one page of the hold's origin has loaded and stays there for SETTLE_S. A site
that never showed a login page is never called logged in, however its first render looks.
The window then closes through `Browser.close`, so the cookies reach disk, after the session
cookies were saved to the jar (cookiejar.py). TIMEOUT_S closes it the same way: the hold's
retry then finds the login still needed and the card's login button comes back.
A window kuhy closes by hand ends the watch ("closed"); its cookies were last saved by a poll.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from draft_studio.capture import errors, policy
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.cookiejar import CookieJar

POLL_S = 0.5
SETTLE_S = 2.0
TIMEOUT_S = 600.0
SAVE_EVERY_S = 3.0
CALL_S = 5.0
STATE_JS = """(() => {
  const shown = (e) => {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return !!r.width && !!r.height && cs.visibility !== 'hidden' && cs.display !== 'none'
      && Number(cs.opacity) > 0;
  };
  return JSON.stringify({
    url: location.href,
    ready: document.readyState === 'complete',
    password: [...document.querySelectorAll('input[type=password]')].some(shown),
  });
})()"""


@dataclass(frozen=True)
class View:
    """What one tab's main frame shows."""

    url: str
    ready: bool
    password: bool

    @property
    def login(self) -> bool:
        """The capture would call this page a login page."""
        return policy.is_login_page(self.url, self.password)


class LoginWatch:
    """`run()` blocks until the window is closed (by us or by kuhy): "logged-in", "timeout" or "closed"."""

    def __init__(
        self,
        cdp: Cdp,
        origin: str,
        jar: CookieJar,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        alive: Callable[[], bool],
        settle: float = SETTLE_S,
        timeout: float = TIMEOUT_S,
        seen_login: bool = False,
    ) -> None:
        self.cdp, self.origin, self.jar = cdp, origin, jar
        self.clock, self.sleep, self.alive = clock, sleep, alive
        self.settle, self.timeout = settle, timeout
        self._sessions: dict[str, str] = {}
        # Opened on a login URL: a submit that beats the first poll still counts as having seen it.
        self.seen_login = seen_login

    def _view(self, target: str) -> View | None:
        """One tab's state; None while its page cannot be asked (navigating, gone)."""
        try:
            if target not in self._sessions:
                reply = self.cdp.call("Target.attachToTarget", targetId=target, flatten=True)
                self._sessions[target] = str(reply["sessionId"])
            out = self.cdp.call(
                "Runtime.evaluate", self._sessions[target], expression=STATE_JS, returnByValue=True
            )
            raw: dict[str, Any] = json.loads(out["result"]["value"])
        except (errors.ChromiumFailed, LookupError, ValueError, TypeError):
            return None  # the session stays: a tab between two documents answers again next poll
        return View(str(raw["url"]), bool(raw["ready"]), bool(raw["password"]))

    def views(self) -> list[View]:
        """Every open tab that answered."""
        infos = self.cdp.call("Target.getTargets")["targetInfos"]
        pages = [i["targetId"] for i in infos if i["type"] == "page"]
        for gone in set(self._sessions) - set(pages):
            del self._sessions[gone]
        return [v for v in map(self._view, pages) if v is not None]

    def _save(self) -> None:
        with contextlib.suppress(errors.CaptureError):
            self.cdp.deadline = self.clock() + CALL_S
            self.jar.save(self.cdp)

    def _close(self) -> None:
        """Cookies saved first; then a clean browser close, so the profile is flushed."""
        self._save()
        with contextlib.suppress(errors.CaptureError):
            self.cdp.send("Browser.close")

    def _logged_in(self, views: list[View], seen: bool) -> bool:
        """Off every login page, with a loaded page of the hold's origin (and a login page seen before)."""
        return (
            seen
            and not any(v.login for v in views)
            and any(v.ready and policy.origin(v.url) == self.origin for v in views)
        )

    def run(self) -> str:
        """Poll until the window is closed or the login is done."""
        start = self.clock()
        seen, since, saved = self.seen_login, None, start
        while self.alive():
            now = self.clock()
            if now - start >= self.timeout:
                self._close()
                return "timeout"
            self.cdp.deadline = now + CALL_S
            try:
                views = self.views()
            except errors.CaptureError:  # the connection is gone; the process follows
                views = []
            seen = seen or any(v.login for v in views)
            if self._logged_in(views, seen):
                since = now if since is None else since
                if now - since >= self.settle:
                    self._close()
                    return "logged-in"
            else:
                since = None
            if now - saved >= SAVE_EVERY_S:
                saved = now
                self._save()
            self.sleep(POLL_S)
        return "closed"
