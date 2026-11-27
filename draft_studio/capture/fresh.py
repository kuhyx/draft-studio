"""A logged-out tab for a fresh shot: a new incognito browser context in the same chromium.

kuhy's guides start at a login page, which the capture profile cannot show
without logging him out. Target.createBrowserContext gives an empty context
(no cookies, no storage) that is dropped afterwards; the profile's session is
never touched. The new tab attaches like any other (arm.open_page: paused,
Fetch.enable, page script) and is registered only once armed, so it obeys the
same rules: reads only, every write failed. While it is driven the session tab
is kept open (`Tabs.keep`); afterwards the fresh tab is closed, its context
disposed and the session tab driven again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from draft_studio.capture import errors
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.tabs import Tabs


@contextmanager
def fresh_tab(cdp: Cdp, tabs: Tabs) -> Iterator[None]:
    """Drive a new tab in a new browser context for the block, then go back to the session tab."""
    main = tabs.target
    context = cdp.call("Target.createBrowserContext", disposeOnDetach=True)["browserContextId"]
    try:
        target = cdp.call("Target.createTarget", url="about:blank", browserContextId=context)["targetId"]
        while target not in tabs.pages:  # armed first: the capture deadline bounds this wait
            cdp.pump(0.05)
        tabs.keep.add(main)
        tabs.follow(target)
        yield
    finally:
        tabs.keep.discard(main)
        back = main in tabs.pages
        if back:
            tabs.follow(main)
            tabs.close_others()
        with suppress(errors.CaptureError):
            cdp.call("Target.disposeBrowserContext", browserContextId=context)
    if not back:  # never go on in the logged-out context as if it were the session
        raise errors.ChromiumFailed("the session tab closed during a fresh shot")
