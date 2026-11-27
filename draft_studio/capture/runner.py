"""One capture run over a Page: open the URL, run the steps under the policy, outline, screenshot.

The Page protocol is the browser seam: page.py drives real chromium over CDP,
the tests use a fake. Every refusal stops the run with NeedsPermission carrying
a screenshot of the refused target outlined, so kuhy sees what he is asked about.
A walkthrough (spec.py) shoots at each shot step, all in this one run; a fresh
shot runs in a logged-out context (`Page.fresh`, fresh.py) and skips the login check.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Protocol

from draft_studio.capture import already, errors, policy
from draft_studio.capture.aim import Aim
from draft_studio.capture.lookup import one
from draft_studio.capture.policy import Target
from draft_studio.capture.spec import Denial, Grant, Spec, Step

Request = tuple[str, str]
SUBMIT = "SUBMIT"
LOGIN_SETTLE = 5.0  # s a single-page app gets to leave its login route before the login is asked for


class Page(Protocol):
    """What the runner needs from a browser tab."""

    def goto(self, url: str) -> None:
        """Navigate and wait for the load."""

    def url(self) -> str:
        """The current URL."""

    def has_password(self) -> bool:
        """A visible password input is on screen."""

    def leaves_login(self, seconds: float) -> bool:
        """Wait up to `seconds` for a login-looking page to turn into another (a client-side redirect)."""

    def find(self, aim: Aim) -> list[Target]:
        """Visible elements `aim` matches (deduplicated to their clickable element), see aim.py."""

    def anchors(self, text: str) -> int:
        """How many visible elements show `text` (an aim's `within` needs exactly one)."""

    def around(self, targets: list[Target]) -> list[str]:
        """What surrounds each target, so an ambiguous-target error says where the matches are."""

    def fresh(self) -> AbstractContextManager[None]:
        """Drive a new tab in a new incognito context for the block; the session tab is untouched."""

    def labels(self) -> list[str]:
        """Labels of the visible clickable elements, so a no-target error can say what is there."""

    def seen(self, text: str) -> bool:
        """`text` is visible now (no waiting)."""

    def wait_for(self, text: str) -> bool:
        """True once `text` is visible, False at the step timeout."""

    def click(self, target: Target, allow_submit: bool, allowed: list[Request]) -> list[Request]:
        """Click it and settle; return what was blocked: (method, url) or (SUBMIT, action)."""

    def highlight(self, target: Target) -> bool:
        """Draw the red outline and arrow; False when the element is gone."""

    def clear(self) -> None:
        """Remove the overlay."""

    def screenshot(self) -> bytes:
        """PNG of the viewport."""


def _check_login(page: Page, spec: Spec) -> None:
    url = page.url()
    if policy.is_login_page(url, page.has_password()) and not page.leaves_login(LOGIN_SETTLE):
        raise errors.LoginRequired(policy.origin(url), spec.url)


def _refuse(
    page: Page, step: int, target: Target, reason: str, detail: str, *, blocked: list[Request]
) -> None:
    page.highlight(target)
    shot = page.screenshot()
    page.clear()
    raise errors.NeedsPermission(step, reason, detail, blocked, shot)


def _click(page: Page, step: int, aim: Aim, grants: list[Grant], denials: Sequence[Denial]) -> Target | None:
    """Click it; the refused target when kuhy denied this very click (nothing was clicked)."""
    target = one(page, step, aim)
    click_ok = any(g.click for g in grants)
    allowed = [r for g in grants for r in g.requests if r[0] != SUBMIT]
    refused = policy.refusal(target)
    if refused and not click_ok:
        if any(d.step == step and d.click for d in denials):
            return target
        _refuse(page, step, target, refused.reason, refused.detail, blocked=[])
    # A denied request is never allowed, so the page fails it again; it just no longer stops the run.
    denied = {r for d in denials for r in d.requests}
    blocked = [b for b in page.click(target, allow_submit=click_ok, allowed=allowed) if b not in denied]
    if blocked:
        submits = [b for b in blocked if b[0] == SUBMIT]
        first = (submits or blocked)[0]
        reason = "form-submit" if submits else "request"
        _refuse(page, step, target, reason, f"{first[0]} {first[1]}", blocked=blocked)
    return None


def _clicked(
    page: Page, plan: Sequence[Step], index: int, grants: Sequence[Grant], denials: Sequence[Denial]
) -> Target | None:
    """`_click`, except that a missing target whose goal is already reached (already.py) is no click."""
    try:
        return _click(page, index, plan[index].target, [g for g in grants if g.step == index], denials)
    except errors.NoTarget:
        if already.reached(page, plan, index):
            return None
        raise


def _outlined(page: Page, target: Target | None) -> bytes:
    if target is not None:
        page.highlight(target)
    png = page.screenshot()
    page.clear()
    return png


def _shoot(page: Page, where: int | None, shot: Step) -> bytes:
    if shot.fresh and shot.url:
        with page.fresh():
            page.goto(shot.url)
            return _outlined(page, one(page, where, shot.aim) if shot.aim else None)
    return _outlined(page, one(page, where, shot.aim) if shot.aim else None)


def _login_shots(page: Page, spec: Spec, plan: Sequence[Step], shots: list[tuple[str, bytes]]) -> None:
    """Shots of the login page itself (kuhy, 2026-11-27: step 1 of his guide is a screenshot of it).

    A spec that opens on a login URL and starts with shot steps aims at that page: landing on it
    is the target, not a need to log in. Such shots run logged out, in a fresh context (the
    session would be sent on to the dashboard), before the session tab opens anything. Each is
    appended to `shots` as it is taken, so a later one that fails keeps the earlier (run_all).
    """
    if not policy.is_login_url(spec.url):
        return
    lead = next((i for i, s in enumerate(plan) if s.kind != "shot"), len(plan))
    for i, s in enumerate(plan[:lead]):
        logged_out = replace(s, fresh=True, url=s.url or spec.url)
        shots.append((s.text, _shoot(page, i if spec.walk else None, logged_out)))


def _opening(spec: Spec, rest: Sequence[Step]) -> tuple[str, bool]:
    """(the URL the session tab opens, whether it is the first step's own goto).

    A login URL is where a walk's logged-out shot aims, never where the session starts: a panel that
    keeps its login in the browser shows that route for a moment before it redirects a logged-in
    session. The session opens on the first step's goto, else on the origin root.
    """
    if not policy.is_login_url(spec.url):
        return spec.url, False
    if rest and rest[0].kind == "goto":
        return rest[0].text, True
    return f"{policy.origin(spec.url)}/", False


def run_all(
    page: Page, spec: Spec, grants: Sequence[Grant] = (), denials: Sequence[Denial] = ()
) -> list[tuple[str, bytes]]:
    """(shot name, PNG) for every shot step in order, or a named CaptureError.

    A denied click ends the run there: the next session shot is the page before that click, its
    target outlined, and later steps are skipped. A spec without shot steps has one implicit shot,
    whose highlight errors keep step None as before. Leading shots of a login URL run first, logged
    out (`_login_shots`); a login page the session lands on for any other step, and does not leave on
    its own within a few seconds (`leaves_login`), is login-required.
    """
    shots: list[tuple[str, bytes]] = []
    try:
        return _walk(page, spec, grants, denials, shots)
    except errors.CaptureError as err:  # a failed walk keeps what it took before the failing step
        err.taken = list(shots)
        raise


def _walk(
    page: Page, spec: Spec, grants: Sequence[Grant], denials: Sequence[Denial], shots: list[tuple[str, bytes]]
) -> list[tuple[str, bytes]]:
    """run_all's steps; every shot is appended to `shots` as it is taken."""
    plan = spec.plan()
    _login_shots(page, spec, plan, shots)
    if len(shots) == len(plan):
        return shots
    opening, consumed = _opening(spec, plan[len(shots) :])
    skip = len(shots) + int(consumed)  # steps already done: the logged-out shots, the opening goto
    page.goto(opening)
    _check_login(page, spec)
    for index, step in enumerate(plan):
        if index < skip:
            continue
        if step.kind == "shot":
            shots.append((step.text, _shoot(page, index if spec.walk else None, step)))
            continue
        if step.kind == "wait":
            if not page.wait_for(step.text):
                raise errors.NoTarget(index, step.text, page.labels())
        elif step.kind == "goto":
            page.goto(step.text)
        elif denied := _clicked(page, plan, index, grants, denials):
            later = [s for s in plan[index + 1 :] if s.kind == "shot" and not s.fresh]
            if later:
                shots.append((later[0].text, _outlined(page, denied)))
            return shots
        _check_login(page, spec)
    return shots


def run(page: Page, spec: Spec, grants: list[Grant], denials: Sequence[Denial] = ()) -> bytes:
    """The one PNG of a spec without shot steps (see run_all)."""
    return run_all(page, spec, grants, denials)[0][1]
