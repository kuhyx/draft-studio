"""A granted request, reused by the fix runs of its own capture for 10 minutes (kuhy, 2026-11-27).

A grant is spent by one re-run (pending.py). When that re-run fails on an aim (422) and
Claude's fix runs the walk again (draft_studio/shots.py), the fix run would ask for the
impersonation POST kuhy has just allowed. So the requests of a grant are remembered here,
under the id of the request kuhy granted, and a run that `follows` that id gets them again:

- only exact (method, URL) pairs kuhy saw on the card, never a click allowance or a SUBMIT;
- only at a click step whose text equals the granted one ("ZALOGUJ JAKO within „GABINET”"),
  wherever the fix moved it; another click, even one sending the same request, asks;
- only in the same draft, and only for REUSE_S seconds after the grant;
- a denial is never remembered (it re-runs once with the request blocked, as before).

The body is not compared: the guard (guard.py), the card and every grant name a request by
method and URL, and the grant's own re-run already sends whatever body the page builds. A
body hash would add nothing kuhy saw, and a per-load token in it would silently undo the reuse.

The ledger lives in memory only: a restarted server asks again (fail closed). The run that
follows never writes the reused grant into a new permission record (service.py), so a card
granted after the window cannot carry it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from draft_studio.capture.runner import SUBMIT, Request
from draft_studio.capture.spec import Grant, Spec

REUSE_S = 600.0


@dataclass(frozen=True)
class Entry:
    """What one grant lets its own fix runs send again, and until when."""

    draft_id: str
    until: float
    step_text: str
    requests: tuple[Request, ...]


class Ledger:
    """Granted request id -> Entry; read and written under CaptureService's lock."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self._entries: dict[str, Entry] = {}

    def remember(self, rid: str, draft_id: str, spec: Spec, allow: Grant) -> None:
        """kuhy granted request `rid`: keep the requests it allowed (none: nothing to keep)."""
        requests = tuple(r for r in allow.requests if r[0] != SUBMIT)
        if requests:
            text = spec.plan()[allow.step].text
            self._entries[rid] = Entry(draft_id, self.clock() + REUSE_S, text, requests)

    def grants(self, follows: str | None, draft_id: str, spec: Spec) -> list[Grant]:
        """The grants a run that follows request `follows` may reuse (none outside the window)."""
        now = self.clock()
        self._entries = {k: e for k, e in self._entries.items() if e.until > now}
        entry = self._entries.get(follows or "")
        if entry is None or entry.draft_id != draft_id:
            return []
        steps = spec.plan()
        hits = [i for i, s in enumerate(steps) if s.kind == "click" and s.text == entry.step_text]
        return [Grant(hits[0], click=False, requests=entry.requests)] if hits else []
