"""The login window finishes by itself: a fake DevTools browser, a fake clock, no real chromium."""

from __future__ import annotations

import json
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.loginwatch import LoginWatch

ORIGIN = "https://panel.example"
LOGIN = ORIGIN + "/login"
HOME = ORIGIN + "/home"


class FakeBrowser:
    """A scripted browser-level Cdp: `pages` maps target id -> what its main frame shows."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.dead = False
        self.closed = False
        self.deadline = 0.0

    def show(self, url: str, ready: bool = True, password: bool = False, tid: str = "t1") -> None:
        self.pages[tid] = {"url": url, "ready": ready, "password": password}

    def send(self, method: str, session: str | None = None, **params: Any) -> int:
        self.calls.append((method, session))
        if self.dead:
            raise errors.ChromiumFailed("send: gone")
        self.closed = self.closed or method == "Browser.close"
        return 1

    def call(self, method: str, session: str | None = None, **params: Any) -> dict[str, Any]:
        self.calls.append((method, session))
        if self.dead:
            raise errors.ChromiumFailed("devtools: eof")
        if method == "Target.getTargets":
            infos = [{"targetId": t, "type": "page", "url": p["url"]} for t, p in self.pages.items()]
            return {"targetInfos": [{"targetId": "w", "type": "service_worker", "url": ""}, *infos]}
        if method == "Target.attachToTarget":
            return {"sessionId": "s-" + params["targetId"]}
        page = self.pages[str(session)[2:]]
        if page.get("broken"):
            raise errors.ChromiumFailed("Runtime.evaluate: no context")
        state = {"url": page["url"], "ready": page["ready"], "password": page["password"]}
        return {"result": {"value": json.dumps(state)}}


class Jar:
    def __init__(self) -> None:
        self.saves = 0

    def save(self, cdp: Any) -> None:
        self.saves += 1


class Rig:
    """Time moves only when the watcher sleeps; `script[n]` runs before the n-th poll."""

    def __init__(self, timeout: float = 600.0) -> None:
        self.now = 0.0
        self.browser = FakeBrowser()
        self.jar = Jar()
        self.script: dict[int, Any] = {}
        self.polls = 0
        self.watch = LoginWatch(
            self.browser,
            ORIGIN,
            self.jar,
            clock=lambda: self.now,
            sleep=self.sleep,
            alive=lambda: not self.browser.closed,
            timeout=timeout,
        )

    def sleep(self, seconds: float) -> None:
        self.polls += 1
        self.now += seconds
        if self.polls in self.script:
            self.script[self.polls]()


def test_leaving_the_login_page_closes_the_window_by_itself() -> None:
    rig = Rig()
    rig.browser.show(LOGIN, password=True)
    rig.script[4] = lambda: rig.browser.show(HOME)  # kuhy logged in
    assert rig.watch.run() == "logged-in"
    assert rig.browser.closed and rig.jar.saves >= 1
    assert rig.now < 10  # the 2 s settle, not the 10 min timeout


def test_a_page_that_has_not_settled_does_not_close_the_window() -> None:
    rig = Rig()
    rig.browser.show(LOGIN, password=True)
    rig.script[2] = lambda: rig.browser.show(HOME)
    rig.script[4] = lambda: rig.browser.show(LOGIN, password=True)  # 1 s on the dashboard, then login again
    rig.script[8] = lambda: setattr(rig.browser, "closed", True)  # kuhy closes it
    assert rig.watch.run() == "closed"
    assert "Browser.close" not in [m for m, _ in rig.browser.calls]


def test_a_window_on_an_already_logged_in_site_closes_after_the_grace_period() -> None:
    """The profile still holds the login: the site opens on its dashboard, no login page ever shows."""
    rig = Rig()
    rig.browser.show(HOME)
    assert rig.watch.run() == "logged-in"
    assert rig.browser.closed and rig.jar.saves >= 1
    assert 12 <= rig.now < 20  # the unseen grace (12 s), not the 10 min timeout


def test_a_slow_first_render_gets_the_grace_period_before_its_login_page_shows() -> None:
    """A SPA's first render (or a bot check) has no password field yet: it is not a finished login."""
    rig = Rig()
    rig.browser.show(ORIGIN + "/")
    rig.script[16] = lambda: rig.browser.show(LOGIN, password=True)  # 8 s in: the form appears
    rig.script[40] = lambda: rig.browser.show(HOME)  # kuhy logged in
    assert rig.watch.run() == "logged-in"
    assert rig.now > 20  # only after the login page was seen and left, never at the 12 s mark


def test_the_grace_period_restarts_when_the_page_changes_state() -> None:
    rig = Rig()
    rig.browser.show(ORIGIN + "/")
    rig.script[18] = lambda: rig.browser.show(ORIGIN + "/", ready=False)  # 9 s in: a reload starts
    rig.script[20] = lambda: rig.browser.show(ORIGIN + "/")
    assert rig.watch.run() == "logged-in"
    assert rig.now > 20


def test_a_password_field_on_the_same_url_is_a_login_page_too() -> None:
    rig = Rig()
    rig.browser.show(ORIGIN + "/", password=True)
    rig.script[3] = lambda: rig.browser.show(ORIGIN + "/")  # the form went away, the URL did not change
    assert rig.watch.run() == "logged-in"


def test_a_login_on_another_origin_counts_but_only_the_hold_origin_ends_it() -> None:
    rig = Rig()
    rig.browser.show("https://sso.example/login", password=True)
    rig.script[3] = lambda: rig.browser.show("https://sso.example/consent")  # still the SSO site
    rig.script[40] = lambda: rig.browser.show(HOME)  # back on the panel
    assert rig.watch.run() == "logged-in"
    assert rig.now > 15


def test_a_page_still_loading_does_not_count() -> None:
    rig = Rig(timeout=20)
    rig.browser.show(LOGIN, password=True)
    rig.script[2] = lambda: rig.browser.show(HOME, ready=False)
    assert rig.watch.run() == "timeout"


def test_a_closed_window_ends_the_watch_quietly() -> None:
    rig = Rig()
    rig.browser.show(LOGIN, password=True)
    rig.script[2] = lambda: setattr(rig.browser, "closed", True)
    assert rig.watch.run() == "closed"
    assert "Browser.close" not in [m for m, _ in rig.browser.calls]


def test_a_dead_connection_waits_for_the_window_to_go() -> None:
    rig = Rig()
    rig.browser.dead = True
    rig.script[3] = lambda: setattr(rig.browser, "closed", True)
    assert rig.watch.run() == "closed"


def test_a_window_that_cannot_be_told_to_close_is_still_reported() -> None:
    rig = Rig(timeout=5)
    rig.browser.show(ORIGIN + "/")
    rig.script[3] = lambda: setattr(rig.browser, "dead", True)
    assert rig.watch.run() == "timeout"


def test_targets_are_attached_once_and_broken_or_foreign_ones_skipped() -> None:
    rig = Rig()
    rig.browser.show(LOGIN, password=True)
    rig.browser.show(LOGIN, tid="t2")
    rig.browser.pages["t2"]["broken"] = True
    rig.script[3] = lambda: (rig.browser.show(HOME), rig.browser.pages.pop("t2"))
    assert rig.watch.run() == "logged-in"
    attaches = [m for m, _ in rig.browser.calls if m == "Target.attachToTarget"]
    assert len(attaches) == 2  # one session per tab for good, a broken tab included


def test_cookies_are_saved_while_the_window_is_open() -> None:
    rig = Rig(timeout=20)
    rig.browser.show(ORIGIN + "/")
    rig.watch.run()
    assert rig.jar.saves >= 4  # every few seconds, so a hand-closed window loses at most a few


def test_calls_get_their_own_short_deadline() -> None:
    rig = Rig(timeout=3)
    rig.browser.show(ORIGIN + "/")
    rig.watch.run()
    assert rig.browser.deadline > 0


def test_a_window_opened_on_a_login_url_needs_no_first_sighting() -> None:
    """A password manager can submit before the first poll: the start URL already was the login page."""
    rig = Rig()
    rig.watch.seen_login = True
    rig.browser.show(HOME)
    assert rig.watch.run() == "logged-in"
