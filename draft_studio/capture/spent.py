"""One grant, one write (kuhy, 2026-11-27): a granted write leaves chromium at most once per grant.

A grant or deny re-runs its capture as a job (heldjobs.py). A restart can cut that run after a
granted write already left (the POST reached the site, the shot was never taken); the next server
replays the decision. Without a record of the send it would send that write a second time.

So while a decision's run is under way (`journaling`, set by service.py around the run of request
`rid`), the guard calls `note` for each granted write right before Fetch.continueRequest, and the
hook appends the (method, URL) pair to the request's record, fsync'd (PendingStore.spend). A record
that cannot be written fails the request instead (guard.py, errors.Unjournaled): nothing leaves
unjournaled.

A resumed claim hands the journaled pairs back (`Claim.sent`). The replay treats them as consumed:
they are dropped from every grant (`without`) and from what reuse.py keeps for the walk, and the
runner gets them as a requests-only denial for this run only, so the page's new try is failed with
no card and the shots are still taken. A walk that cannot go on without that write (its next aim is
gone) fails as usual: the hold shows as failed with retry and dismiss, and kuhy is asked again.

Pairs, not single requests, are journaled: a pair granted for two clicks and sent by one is not sent
by the replay at all. That is fail closed: fewer sends than granted, never more.

The hook is a context variable: CDP events are dispatched on the capture's own thread (cdp.py), so
the guard, built per browser in arm.py, needs no knowledge of grants or jobs.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from draft_studio.capture.spec import Grant

Request = tuple[str, str]
Journal = Callable[[Request], None]
_JOURNAL: ContextVar[Journal | None] = ContextVar("draft_studio_spent", default=None)


@contextmanager
def journaling(journal: Journal) -> Iterator[None]:
    """Within the block, every granted write the guard lets through is passed to `journal` first."""
    token = _JOURNAL.set(journal)
    try:
        yield
    finally:
        _JOURNAL.reset(token)


def note(request: Request) -> None:
    """The guard is about to let `request` leave: journal it (outside a decision's run, nothing to do)."""
    journal = _JOURNAL.get()
    if journal is not None:
        journal(request)


def without[G: Grant](grant: G, sent: Collection[Request]) -> G:
    """`grant` minus the pairs already sent under it."""
    if not any(r in sent for r in grant.requests):
        return grant
    return replace(grant, requests=tuple(r for r in grant.requests if r not in sent))
