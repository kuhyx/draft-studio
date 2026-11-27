"""The login window on real chromium (headless here: never kuhy's display) against a site whose
login cookie has no expiry. The window closes itself, and the headless capture that follows is
still logged in, although chromium drops session cookies when it exits.

Run: .venv/bin/pytest -q -m chromium tests/capture_live/test_login_live.py
"""

from __future__ import annotations

import stat
import subprocess
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture.browser import read_endpoint
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.login import open_window, watch_window
from draft_studio.capture.wsock import connect
from tests.capture_live.conftest import Rig, Site
from tests.e2e.launch import chromium

pytestmark = pytest.mark.chromium


class Window:
    """The login window in a thread, as the server's detached command runs it."""

    def __init__(self, site: Site, rig: Rig, path: str = "/auth/login.html", **tuning: float) -> None:
        self.outcome = ""
        self.profile = rig.profile
        self.launched = threading.Event()  # the stale port file of an earlier run is gone by then

        def popen(argv: list[str]) -> subprocess.Popen[bytes]:
            self.launched.set()
            return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        watch = partial(watch_window, **tuning)

        def run() -> None:
            self.outcome = open_window(
                f"{site.base}{path}",
                rig.profile,
                chromium(),
                extra=("--headless=new",),
                popen=popen,
                watch=watch,
            )

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def kuhy(self, expression: str) -> Any:
        """Run `expression` in the window's tab, once its login form has loaded."""
        assert self.launched.wait(10), "the window never started"
        deadline = time.monotonic() + 20
        while (url := read_endpoint(self.profile)) is None:
            assert time.monotonic() < deadline, "no DevTools port"
            time.sleep(0.05)
        cdp = Cdp(connect(url, 5), time.monotonic() + 20)
        try:
            while True:
                pages = [t for t in cdp.call("Target.getTargets")["targetInfos"] if t["type"] == "page"]
                if pages:
                    session = cdp.call("Target.attachToTarget", targetId=pages[0]["targetId"], flatten=True)
                    ready = cdp.call(
                        "Runtime.evaluate", session["sessionId"], expression=READY, returnByValue=True
                    )
                    if ready["result"]["value"]:
                        break
                assert time.monotonic() < deadline, "login form never loaded"
                time.sleep(0.1)
            out = cdp.call(
                "Runtime.evaluate", session["sessionId"], expression=expression, returnByValue=True
            )
            return out["result"].get("value")
        finally:
            cdp.close()

    def close_by_hand(self) -> None:
        """What kuhy does with the window's close button."""
        url = read_endpoint(self.profile)
        assert url is not None
        cdp = Cdp(connect(url, 5), time.monotonic() + 20)
        cdp.send("Browser.close")
        cdp.close()

    def finished(self, seconds: float = 30) -> str:
        self.thread.join(seconds)
        assert not self.thread.is_alive(), "the login window is still open"
        return self.outcome


READY = "document.readyState === 'complete' && !!document.querySelector('input[type=password]')"
SUBMIT = "document.querySelector('form').submit()"


def spec(site: Site) -> dict[str, Any]:
    return {"url": f"{site.base}/auth/home", "name": "acct"}


def test_the_window_closes_itself_and_the_session_survives_the_restart(site: Site, rig: Rig) -> None:
    status, body = rig.service.capture(rig.draft, spec(site))
    assert (status, body["error"]) == (409, "login-required")
    window = Window(site, rig, settle=0.5)
    window.kuhy(SUBMIT)
    assert window.finished() == "logged-in"  # nobody closed it
    assert site.writes["POST /auth/login"] >= 1
    jar = rig.profile / "session-cookies.json"
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600
    assert rig.service.capture(rig.draft, spec(site))[0] == 200  # a new headless browser, still logged in
    jar.unlink()  # control: the cookie has no expiry, so without the jar chromium forgot it
    assert rig.service.capture(rig.draft, spec(site))[1]["error"] == "login-required"


def test_closing_the_window_by_hand_keeps_the_login(site: Site, rig: Rig) -> None:
    window = Window(site, rig, settle=600)  # never decides by itself
    window.kuhy(SUBMIT)
    jar: Path = rig.profile / "session-cookies.json"
    deadline = time.monotonic() + 20
    while not (jar.is_file() and "sid" in jar.read_text()):  # a poll saved it
        assert time.monotonic() < deadline, "the cookie was never saved"
        time.sleep(0.2)
    window.close_by_hand()
    assert window.finished() == "closed"
    assert rig.service.capture(rig.draft, spec(site))[0] == 200


def test_a_login_that_never_happens_ends_at_the_timeout(site: Site, rig: Rig) -> None:
    window = Window(site, rig, timeout=3)
    assert window.finished() == "timeout"
    assert rig.service.capture(rig.draft, spec(site))[1]["error"] == "login-required"
    assert rig.service.capture(rig.draft, spec(site))[1]["error"] == "login-required"  # profile free again


SPA_FORM = "document.querySelector('form').requestSubmit()"


def test_a_single_page_panel_closes_the_window_without_a_page_load(site: Site, rig: Rig) -> None:
    window = Window(site, rig, "/spa/", settle=0.5, unseen=3)  # opened on the origin, which redirects itself
    window.kuhy(SPA_FORM)  # a fast login: the form may be gone before a poll sees it
    assert window.finished() == "logged-in"
    assert site.writes["POST /spa/api/login"] >= 1
    spa = {"url": f"{site.base}/spa/dashboard", "name": "spa"}
    assert rig.service.capture(rig.draft, spec(site) | spa)[0] == 200  # localStorage outlives the window


def test_a_window_on_a_site_that_is_still_logged_in_closes_by_itself(site: Site, rig: Rig) -> None:
    first = Window(site, rig, "/spa/", settle=0.5, unseen=3)
    first.kuhy(SPA_FORM)
    assert first.finished() == "logged-in"
    again = Window(site, rig, "/spa/", unseen=3)  # a retry's window: the dashboard opens, no login page
    assert again.finished() == "logged-in"
