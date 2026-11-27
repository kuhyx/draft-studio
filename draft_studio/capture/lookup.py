"""Exactly one visible element for an aim (aim.py), or the named failure Claude corrects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from draft_studio.capture import errors
from draft_studio.capture.aim import Aim
from draft_studio.capture.policy import Target

if TYPE_CHECKING:
    from draft_studio.capture.runner import Page


def one(page: Page, step: int | None, aim: Aim) -> Target:
    """The one match of `aim`: NoTarget (anchor or element missing) or Ambiguous otherwise."""
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
