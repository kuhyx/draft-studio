"""A granted request, reused by every re-run of its own walk for 10 minutes (kuhy, 2026-11-27).

A grant is spent by one re-run (pending.py). Yet the walk runs again, from the start, when
a later card is granted or denied (a tracker POST after "ZALOGUJ JAKO") and when Claude's
fix runs it after a 422 (draft_studio/shots.py), and each re-run would ask again for the
impersonation POST kuhy had just allowed. So a grant's requests are kept here per walk:

- a walk is the chain of permission requests one capture started: each record stores its
  `scope` (the first request's id), a re-run of a record keeps it, and a fix run that
  `follows` a request id takes that record's scope;
- only exact (method, URL) pairs kuhy saw on a card, never a click allowance or a SUBMIT.
  They are allowed at any click step (so a fix that rewrites the aim still matches); the
  guard (guard.py) lets an allowed write through only while it watches a click, never on a
  page load or in the background;
- only in the same draft, for REUSE_S seconds after that grant; a denied request is never reused
  (a denial wins).

Denials are kept the same way (kuhy, 2026-11-27: a fix run carried none, so the chat POST he had
denied carded again every time): the exact (method, URL) pairs of a deny or of the rest of a partial
grant, same walk, same draft, REUSE_S seconds. A kept denial is failed again with no card and the
shot still taken, so it only ever blocks more and never widens policy.py or guard.py. A denied click
is not kept (a fix run may move its step index), and a later grant of the pair lifts its denial.

The body is not compared: the guard, the card and every grant name a request by method and
URL, and the grant's own re-run already sends whatever body the page builds. A body hash
would add nothing kuhy saw, and a per-load token in it would silently undo the reuse.

The ledger lives in memory only: a restarted server asks again (fail closed). A reused grant
is never written into a new permission record (service.py), so it cannot outlive its window.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from draft_studio.capture.runner import SUBMIT, Request
from draft_studio.capture.spec import Denial, Grant, Spec

REUSE_S = 600.0


@dataclass
class Entry:
    """What one walk may send again: request -> until when."""

    draft_id: str
    until: dict[Request, float] = field(default_factory=dict)
    denied: dict[Request, float] = field(default_factory=dict)


class Ledger:
    """Walk scope -> Entry; read and written under CaptureService's lock."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self._entries: dict[str, Entry] = {}

    def remember(self, scope: str, draft_id: str, allow: Grant) -> None:
        """kuhy granted a card of walk `scope`: keep the requests it allowed."""
        entry = self._entries.setdefault(scope, Entry(draft_id))
        for request in allow.requests:
            entry.denied.pop(request, None)  # kuhy changed his mind
            if request[0] != SUBMIT:
                entry.until[request] = self.clock() + REUSE_S

    def deny(self, scope: str, draft_id: str, denial: Denial) -> None:
        """kuhy denied requests of walk `scope`: keep them failed for its re-runs (a denied click is not)."""
        entry = self._entries.setdefault(scope, Entry(draft_id))
        for request in () if denial.click else denial.requests:
            entry.until.pop(request, None)
            entry.denied[request] = self.clock() + REUSE_S

    def denials(self, scope: str, draft_id: str) -> list[Denial]:
        """The kept denials of walk `scope`, as one Denial for every step (requests block at any step)."""
        entry = self._entries.get(scope)
        if entry is None or entry.draft_id != draft_id:
            return []
        now = self.clock()
        entry.denied = {r: t for r, t in entry.denied.items() if t > now}
        return [Denial(0, click=False, requests=tuple(entry.denied))] if entry.denied else []

    def grants(self, scope: str, draft_id: str, spec: Spec, denials: Sequence[Denial]) -> list[Grant]:
        """The kept requests walk `scope` may send again, one grant per click step."""
        now, denied = self.clock(), {r for d in denials for r in d.requests}
        entry = self._entries.get(scope)
        if entry is None or entry.draft_id != draft_id:
            return []
        entry.until = {r: t for r, t in entry.until.items() if t > now}
        requests = tuple(r for r in entry.until if r not in denied)
        if not requests:
            return []
        return [
            Grant(i, click=False, requests=requests) for i, s in enumerate(spec.plan()) if s.kind == "click"
        ]
