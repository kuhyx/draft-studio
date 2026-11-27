"""One capture run over a Page: open the URL, run the steps under the policy, outline, screenshot.

The Page protocol is the browser seam: page.py drives real chromium over CDP,
the tests use a fake. Every refusal stops the run with NeedsPermission carrying
a screenshot of the refused target outlined, so kuhy sees what he is asked about.
"""

from __future__ import annotations

from typing import Protocol

from draft_studio.capture import errors, policy
from draft_studio.capture.policy import Target
from draft_studio.capture.spec import Grant, Spec

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


def _click(page: Page, step: int, text: str, grants: list[Grant]) -> None:
    target = _one(page, step, text)
    click_ok = any(g.click for g in grants)
    allowed = [r for g in grants for r in g.requests if r[0] != SUBMIT]
    refused = policy.refusal(target)
    if refused and not click_ok:
        _refuse(page, step, target, refused.reason, refused.detail, blocked=[])
    blocked = page.click(target, allow_submit=click_ok, allowed=allowed)
    if blocked:
        submits = [b for b in blocked if b[0] == SUBMIT]
        first = (submits or blocked)[0]
        reason = "form-submit" if submits else "request"
        _refuse(page, step, target, reason, f"{first[0]} {first[1]}", blocked=blocked)


def run(page: Page, spec: Spec, grants: list[Grant]) -> bytes:
    """The finished PNG, or a named CaptureError."""
    page.goto(spec.url)
    _check_login(page, spec)
    for index, step in enumerate(spec.steps):
        if step.kind == "wait":
            if not page.wait_for(step.text):
                raise errors.NoTarget(index, step.text, page.labels())
        else:
            _click(page, index, step.text, [g for g in grants if g.step == index])
        _check_login(page, spec)
    if spec.highlight is not None:
        page.highlight(_one(page, None, spec.highlight))
    png = page.screenshot()
    page.clear()
    return png
