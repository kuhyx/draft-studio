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
from typing import Protocol

from draft_studio.capture import errors, policy
from draft_studio.capture.aim import Aim
from draft_studio.capture.policy import Target
from draft_studio.capture.spec import Denial, Grant, Spec, Step

Request = tuple[str, str]
SUBMIT = "SUBMIT"


class Page(Protocol):
    """What the runner needs from a browser tab."""

    def goto(self, url: str) -> None:
        """Navigate and wait for the load."""

    def url(self) -> str:
        """The current URL."""

    def has_password(self) -> bool:
        """A visible password input is on screen."""

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


def _one(page: Page, step: int | None, aim: Aim) -> Target:
    if aim.within is not None:
        count = page.anchors(aim.within)
        if not count:
            raise errors.NoTarget(step, aim.within, page.labels())
        if count > 1:
            raise errors.Ambiguous(step, aim.within, count, [])
    found = page.find(aim)
    if not found:
        raise errors.NoTarget(step, aim.label(), page.labels())
    if len(found) > 1:
        raise errors.Ambiguous(step, aim.label(), len(found), page.around(found))
    return found[0]


def _check_login(page: Page, spec: Spec) -> None:
    url = page.url()
    if policy.is_login_page(url, page.has_password()):
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
    target = _one(page, step, aim)
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
            return _outlined(page, _one(page, where, shot.aim) if shot.aim else None)
    return _outlined(page, _one(page, where, shot.aim) if shot.aim else None)


def run_all(
    page: Page, spec: Spec, grants: Sequence[Grant] = (), denials: Sequence[Denial] = ()
) -> list[tuple[str, bytes]]:
    """(shot name, PNG) for every shot step in order, or a named CaptureError.

    A denied click ends the run there: the next session shot is the page before that click, its
    target outlined, and later steps are skipped. A spec without shot steps has one implicit shot,
    whose highlight errors keep step None as before.
    """
    plan, shots = spec.plan(), []
    page.goto(spec.url)
    _check_login(page, spec)
    for index, step in enumerate(plan):
        if step.kind == "shot":
            shots.append((step.text, _shoot(page, index if spec.walk else None, step)))
            continue
        if step.kind == "wait":
            if not page.wait_for(step.text):
                raise errors.NoTarget(index, step.text, page.labels())
        elif step.kind == "goto":
            page.goto(step.text)
        elif denied := _click(page, index, step.target, [g for g in grants if g.step == index], denials):
            later = [s for s in plan[index + 1 :] if s.kind == "shot" and not s.fresh]
            return [*shots, (later[0].text, _outlined(page, denied))] if later else shots
        _check_login(page, spec)
    return shots


def run(page: Page, spec: Spec, grants: list[Grant], denials: Sequence[Denial] = ()) -> bytes:
    """The one PNG of a spec without shot steps (see run_all)."""
    return run_all(page, spec, grants, denials)[0][1]
