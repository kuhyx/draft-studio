"""A click whose target is gone because the state it leads to is already in effect.

kuhy's cyber_Folks walk (2026-11-27): "ZALOGUJ JAKO" impersonates the account owner, and the panel
keeps that server-side for an hour. Every later run starts in the owner's view, where the button does
not exist, so the click was a no-target and the whole walk dropped. A missing click target is not an
error when the walk's goal after that click is met: the next `wait` text is visible (and the next
shot's highlight resolves), on the page the run is on, or on the origin root, where the click would
have landed. Then the click is skipped (logged) and the walk goes on; otherwise the NoTarget stands.

A skipped click leaves the walk where the click would have (draft 462fb46c512a: the panel's side
menu shows „Domeny” on every page, so the goal held on the shares page and the dashboard shot was
taken there). A click that names where it `lands` (spec.py: HOME, the origin root, or a URL) opens
that page and checks the goal there only. A click without `lands` (a banner or consent that stays on
its page, older plans) is checked here, then on the origin root.
Nothing is clicked or sent: the only navigation is a GET of the landing page or the origin root.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from draft_studio.capture import errors, policy
from draft_studio.capture.aim import Aim
from draft_studio.capture.lookup import one
from draft_studio.capture.spec import HOME, Step

if TYPE_CHECKING:
    from draft_studio.capture.runner import Page

LOG = logging.getLogger("draft_studio")


def goal(plan: Sequence[Step], index: int) -> tuple[str | None, Aim | None]:
    """(text of the wait right after the click, highlight of the next session shot) -- None when absent.

    The look-ahead ends at the next click, goto or second wait; a fresh shot does not depend on the
    session's state and is passed over.
    """
    wait: str | None = None
    for step in plan[index + 1 :]:
        if step.kind == "wait" and wait is None:
            wait = step.text
        elif step.kind == "shot" and step.fresh:
            continue
        elif step.kind == "shot":
            return wait, step.aim
        else:
            break
    return wait, None


def _met(page: Page, wait: str | None, aim: Aim | None, *, settle: bool) -> bool:
    """The wait text shows (polled when `settle`, a page that just loaded) and the highlight resolves."""
    if wait is not None and not (page.wait_for(wait) if settle else page.seen(wait)):
        return False
    if aim is None:
        return True
    try:
        one(page, None, aim)
    except errors.NoTarget:
        return False
    except errors.Ambiguous:
        pass  # present: the shot itself reports its ambiguity, as it would after the click
    return True


def _landed(page: Page, lands: str, wait: str | None, aim: Aim | None, index: int) -> bool:
    """Open where the click lands (unless the run is there) and check the goal on that page."""
    url = f"{policy.origin(page.url())}/" if lands == HOME else lands
    if page.url() != url:
        page.goto(url)
    if _met(page, wait, aim, settle=True):
        LOG.info("click step %d skipped: its goal %r is met where it lands, %s", index, wait or aim, url)
        return True
    return False


def reached(page: Page, plan: Sequence[Step], index: int) -> bool:
    """True when the goal after click step `index` already holds where it lands, else here or on the root."""
    wait, aim = goal(plan, index)
    if wait is None and aim is None:
        return False  # nothing to tell "reached" from "never was": the no-target stands
    if lands := plan[index].lands:
        return _landed(page, lands, wait, aim, index)
    if _met(page, wait, aim, settle=False):
        LOG.info("click step %d skipped: its goal %r is already met on this page", index, wait or aim)
        return True
    root = f"{policy.origin(page.url())}/"
    if page.url() == root:
        return False
    page.goto(root)
    if _met(page, wait, aim, settle=True):
        LOG.info("click step %d skipped: its goal %r is already met on %s", index, wait or aim, root)
        return True
    return False
