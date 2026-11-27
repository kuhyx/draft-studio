"""Session-only cookies, kept across browser restarts for at most 12 hours.

kuhy's login lives in a cookie with no expiry (the panel's auth cookie): chromium drops those
when it exits, so the headless capture that follows the login window would find itself logged
out. The login window saves its session cookies here while it is open (loginwatch.py) and every
headless run restores them before its first navigation and refreshes them on the way out
(browser.py). Cookies with an expiry are not stored: the profile's own Cookies DB keeps them.

The file sits in the 0700 capture profile, mode 0600, like the Cookies DB (password-store=basic).
Values never reach a log. Restored cookies stay session cookies (no expiry is set), so chromium
never writes them to its own DB past the cap: 12 h after the cookie was first seen, it is dropped.
Saves merge by (name, domain, path): a poll made before the login finished never wipes a jar.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.private import ensure_private

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
FILE = "session-cookies.json"
MAX_AGE_S = 12 * 3600
# Storage.setCookies' CookieParam fields; getCookies also reports size, session, expires, sameParty.
FIELDS = (
    *("name", "value", "domain", "path", "secure", "httpOnly"),
    *("sameSite", "priority", "sourceScheme", "sourcePort"),
)


def _key(cookie: Json) -> tuple[str, str, str]:
    return (str(cookie.get("name")), str(cookie.get("domain")), str(cookie.get("path")))


class CookieJar:
    """`load` into a browser, `save` out of it; both only ever log a failure, never raise."""

    def __init__(self, profile: Path, wall: Callable[[], float] = time.time) -> None:
        self.profile, self.wall = profile, wall
        self.path = profile / FILE

    def exists(self) -> bool:
        """A jar was written before (a headless run only refreshes an existing one)."""
        return self.path.is_file()

    def _entries(self) -> list[Json]:
        """The stored entries that are still young; junk of any shape counts as an empty jar."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))["cookies"]
        except (OSError, ValueError, LookupError, TypeError):
            return []
        if not isinstance(raw, list):
            return []
        now = self.wall()
        return [
            e
            for e in raw
            if isinstance(e, dict)
            and isinstance(e.get("cookie"), dict)
            and isinstance(e.get("seen"), int | float)
            and now - e["seen"] < MAX_AGE_S
        ]

    def load(self, cdp: Cdp) -> int:
        """Restore the stored cookies into the default browser context; how many were sent."""
        cookies = [e["cookie"] for e in self._entries()]
        if not cookies:
            return 0
        try:
            cdp.call("Storage.setCookies", cookies=cookies)
        except errors.CaptureError as exc:
            LOG.warning("capture: cookie jar not restored: %s", exc.code)
            return 0
        return len(cookies)

    def save(self, cdp: Cdp) -> None:
        """Merge the browser's session cookies into the file."""
        try:
            live = cdp.call("Storage.getCookies").get("cookies", [])
        except errors.CaptureError as exc:
            LOG.warning("capture: cookie jar not saved: %s", exc.code)
            return
        merged = {_key(e["cookie"]): e for e in self._entries()}
        now = self.wall()
        for c in live:
            if c.get("session"):
                param = {k: c[k] for k in FIELDS if k in c}
                merged[_key(c)] = {"seen": merged.get(_key(c), {}).get("seen", now), "cookie": param}
        self._write(list(merged.values()))

    def _write(self, entries: list[Json]) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            ensure_private(self.profile)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump({"cookies": entries}, out)
            os.replace(tmp, self.path)
        except OSError as exc:
            LOG.warning("capture: cookie jar not written: %s", type(exc).__name__)
            tmp.unlink(missing_ok=True)
