"""One capture run over a Page: open the URL, run the steps under the policy, outline, screenshot.

The Page protocol is the browser seam: page.py drives real chromium over CDP,
the tests use a fake. Every refusal stops the run with NeedsPermission carrying
a screenshot of the refused target outlined, so kuhy sees what he is asked about.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from draft_studio.capture import errors, policy
from draft_studio.capture.policy import Target
from draft_studio.capture.spec import Denial, Grant, Spec

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

    def find(self, text: str) -> list[Target]:
        """Visible elements showing `text` (deduplicated to their clickable element)."""

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


def _one(page: Page, step: int | None, text: str) -> Target:
    found = page.find(text)
    if not found:
        raise errors.NoTarget(step, text, page.labels())
    if len(found) > 1:
        raise errors.Ambiguous(step, text, len(found))
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


def _click(page: Page, step: int, text: str, grants: list[Grant], denials: Sequence[Denial]) -> Target | None:
    """Click it; the refused target when kuhy denied this very click (nothing was clicked)."""
    target = _one(page, step, text)
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


def run(page: Page, spec: Spec, grants: list[Grant], denials: Sequence[Denial] = ()) -> bytes:
    """The finished PNG, or a named CaptureError.

    A denied click ends the run there: the shot is the page before that click, its target outlined.
    """
    page.goto(spec.url)
    _check_login(page, spec)
    for index, step in enumerate(spec.steps):
        if step.kind == "wait":
            if not page.wait_for(step.text):
                raise errors.NoTarget(index, step.text, page.labels())
        elif denied := _click(page, index, step.text, [g for g in grants if g.step == index], denials):
            return _outlined(page, denied)
        _check_login(page, spec)
    if spec.highlight is None:
        return _outlined(page, None)
    return _outlined(page, _one(page, None, spec.highlight))
