"""Capture orchestration: one capture at a time, refusals become permission requests.

Each method returns (HTTP status, JSON body) so routes.py stays plumbing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from draft_studio.capture import errors, partial, runner, spent
from draft_studio.capture.browser import Chromium, profile_dir
from draft_studio.capture.denials import BadClear, Denials
from draft_studio.capture.pending import Claim, NotPending, PendingStore, RequestNotFound
from draft_studio.capture.private import ensure_private, prune
from draft_studio.capture.reuse import Ledger
from draft_studio.capture.spec import Denial, Grant, Spec, SpecError, parse
from draft_studio.held import HeldStore
from draft_studio.store import AssetError, DraftNotFound, Store

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
Reply = tuple[int, Json]
NOT_FOUND: Reply = (404, {"error": "not-found"})
NOT_PENDING: Reply = (409, {"error": "not-pending"})
BUSY: Reply = (409, {"error": "profile-busy"})
RESUME_WAIT_S = 120.0  # a resumed decision queues behind a running capture instead of losing its run


class Launcher(Protocol):
    """browser.Chromium, or a fake in tests."""

    def busy(self) -> bool:
        """The profile is held by another process."""

    def open(self) -> AbstractContextManager[runner.Page]:
        """A ready page; the browser is gone after the block."""


class CaptureService:
    """Shared by all request threads of one server."""

    def __init__(
        self, store: Store, launcher: Launcher | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.store = store
        self.pending = PendingStore(store.root)
        self.reuse = Ledger(clock)
        self.denied = Denials(store)  # kept per draft until cleared (denials.py)
        self.launcher: Launcher = launcher or Chromium(profile_dir(store.root))
        ensure_private(profile_dir(store.root))
        ensure_private(self.pending.dir)
        prune(self.pending.dir, keep=HeldStore(store).waiting_requests())
        self._lock = threading.Lock()

    def _locked(self, work: Callable[[], Reply], wait: float = 0.0) -> Reply:
        # wait > 0: queue behind a running capture (draft studio's own Claude); kuhy's calls never wait.
        if not (self._lock.acquire(timeout=wait) if wait > 0 else self._lock.acquire(blocking=False)):
            return BUSY
        try:
            return work()
        finally:
            self._lock.release()

    def _run(
        self,
        draft_id: str,
        spec: Spec,
        grants: list[Grant],
        denials: Sequence[Denial] = (),
        *,
        scope: str | None = None,
        sent: Sequence[runner.Request] = (),
    ) -> Reply:
        """One run of walk `scope` (reuse.py; None: a new walk, scoped by its first card).

        The walk's kept grants (reuse.py) and the draft's kept denials (denials.py, every walk) reach
        the runner only: a new permission record keeps kuhy's own, so a kept decision cannot outlive
        its window or a clear. `sent`: writes a cut run of this decision already sent (spent.py):
        dropped from every grant and failed in this run only.
        """
        grants = [spent.without(g, sent) for g in grants]
        once = [Denial(0, click=False, requests=tuple(sent))] if sent else []
        kept = [*self.denied.denial(draft_id), *once]
        reused = self.reuse.grants(scope, draft_id, spec, [*denials, *kept]) if scope else []
        try:
            with self.launcher.open() as page:
                pngs = runner.run_all(page, spec, [*grants, *reused], [*denials, *kept])
        except errors.NeedsPermission as stop:
            rec = self.pending.create(
                draft_id,
                spec,
                step=stop.step,
                reason=stop.reason,
                detail=stop.detail,
                blocked=stop.blocked,
                announces=stop.announces,
                grants=grants,
                shot=stop.shot,
                denials=denials,
                scope=scope,
            )
            LOG.info("capture %s: step %s needs permission (%s)", spec.name, stop.step, stop.reason)
            return 409, {"error": stop.code, "request": rec}
        except errors.CaptureError as err:
            LOG.warning("capture %s failed: %s", spec.name, err)
            return err.status, {"error": err.code, **err.extra, **self._taken(draft_id, spec, err)}
        try:
            shots = [self._store(draft_id, shot, png) for shot, png in pngs]
        except AssetError as exc:
            return 413 if exc.code == "too-large" else 502, {"error": exc.code}
        if spec.walk:  # a walkthrough: every shot it reached, in order (a denied click ends it early)
            return 200, {"shots": shots}
        return 200, {"asset": shots[0]["asset"], "md": shots[0]["md"]}

    def _taken(self, draft_id: str, spec: Spec, err: errors.CaptureError) -> Json:
        """{"shots": [...]}: the shots a failed walk took before its failing step, stored ({} if none)."""
        if not spec.walk or not err.taken:
            return {}
        try:
            return {"shots": [self._store(draft_id, shot, png) for shot, png in err.taken]}
        except AssetError as exc:  # the failure itself is still answered; those shots are lost
            LOG.warning("capture %s: shots before the failure not kept: %s", spec.name, exc.code)
            return {}

    def _store(self, draft_id: str, shot: str, png: bytes) -> Json:
        name = self.store.add_asset(draft_id, f"{shot}.png", png)
        return {"name": shot, "asset": name, "md": f"![](assets/{name})"}

    def capture(self, draft_id: str, body: Any, wait: float = 0.0, follows: str | None = None) -> Reply:
        """POST /api/drafts/<id>/capture (`wait`: seconds to queue behind a running capture).

        `follows`: a request id of the walk this run fixes (shots.py): it runs in that walk, reuse.py.
        """
        self.store.get(draft_id)  # DraftNotFound: 404 before any browser starts
        try:
            spec = parse(body)
        except SpecError as exc:
            return 400, {"error": "bad-spec", "detail": exc.detail}
        return self._locked(lambda: self._run(draft_id, spec, [], scope=self._scope(follows)), wait)

    def _scope(self, follows: str | None) -> str | None:
        """The walk of request `follows` (None: no such request, or none named)."""
        try:
            return str(self.pending.record(follows).get("scope") or follows) if follows else None
        except RequestNotFound:
            return None

    def _redo(self, rid: str, claim: Callable[[str], Claim], wait: float = 0.0) -> Reply:
        """Re-run the capture of request `rid` as `claim` decides it (grant or deny)."""

        def work() -> Reply:
            if self.launcher.busy():  # keep the request for later rather than burn the decision
                return BUSY
            try:
                c = claim(rid)
            except RequestNotFound:
                return NOT_FOUND
            except NotPending:
                return NOT_PENDING
            except partial.NotBlocked:
                return 400, {"error": "not-blocked"}  # nothing was decided: the request stays pending
            if c.allow is not None:  # a grant: every re-run of its walk may send these again
                self.reuse.remember(c.scope, c.draft_id, spent.without(c.allow, c.sent))
                self.denied.lift(c.draft_id, c.allow.requests)  # before the run: a denial would win
            for denial in self._new_denials(rid, c.denials):  # a denial: every later run keeps it failed
                self.reuse.deny(c.scope, c.draft_id, denial)
                if not denial.click:  # a denied click is not kept (a fix run may move its step)
                    self.denied.add(c.draft_id, denial.requests, c.allow.requests if c.allow else ())
            with spent.journaling(lambda request: self.pending.spend(rid, request)):  # one grant, one send
                return self._run(c.draft_id, c.spec, c.grants, c.denials, scope=c.scope, sent=c.sent)

        return self._locked(work, wait)

    def _new_denials(self, rid: str, denials: Sequence[Denial]) -> list[Denial]:
        """The denials this decision added: the card's own were kept when they were made."""
        old = {Denial.from_json(d) for d in self.pending.record(rid).get("denials", [])}
        return [d for d in denials if d not in old]

    def grant(self, rid: str, body: Any = None, resume: bool = False) -> Reply:
        """POST /api/captures/<id>/grant: re-run with that step allowed (its walk's re-runs too, reuse.py).

        `body` {"allow": [[method, url], ...]} allows only those blocked requests (partial.py).
        `resume`: the request may be granted already by a run a restart cut: run it again (heldjobs.py).
        """
        try:
            picked = partial.parse(body)
        except partial.BadAllow:
            return 400, {"error": "bad-allow"}
        wait = RESUME_WAIT_S if resume else 0.0
        return self._redo(rid, lambda r: self.pending.claim(r, picked, resume), wait)

    def deny(self, rid: str, resume: bool = False) -> Reply:
        """POST /api/captures/<id>/deny: re-run with that refusal kept blocked, shot anyway."""
        return self._redo(rid, lambda r: self.pending.deny(r, resume), RESUME_WAIT_S if resume else 0.0)

    def list_denials(self, draft_id: str) -> Reply:
        """GET /api/drafts/<id>/denials (denials.py); never waits on a running capture."""
        try:
            return 200, self.denied.listing(draft_id)
        except DraftNotFound:
            return NOT_FOUND

    def clear_denials(self, draft_id: str, body: Any) -> Reply:
        """POST /api/drafts/<id>/denials {"clear": "all" | [[method, url], ...]}: the listing after it."""
        try:
            return 200, self.denied.clear(draft_id, body)
        except DraftNotFound:
            return NOT_FOUND
        except BadClear:
            return 400, {"error": "bad-clear"}

    def list_pending(self) -> Reply:
        """GET /api/captures/pending."""
        return 200, {"requests": self.pending.list_pending()}

    def shot(self, rid: str) -> tuple[int, Json | bytes]:
        """GET /api/captures/<id>/shot: the PNG kuhy decides on."""
        try:
            return 200, self.pending.shot(rid)
        except RequestNotFound:
            return NOT_FOUND
