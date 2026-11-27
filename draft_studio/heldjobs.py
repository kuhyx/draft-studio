"""Grant, deny and retry of a held answer as jobs (jobs.py): a restart in the middle re-runs them.

A grant or deny re-runs the capture of a permission request and then moves the held answer's slot
(heldapi.py `decided`). Both steps are one job `{"rid", "body"}` (the request id and the grant's
`allow` body, so a replay claims exactly what kuhy clicked), written before the request is claimed.

Replay (the next server, `Resumable.replay`):

- the held slot no longer waits on `rid`: the run landed, nothing to do (a granted write is never sent
  twice because a job was only marked "done" late);
- else the request is pending (cut before its claim) or already granted/denied (cut mid-run): the same
  decision runs again from the stored record (`resume`, capture/pending.py), queued behind a running
  capture instead of refused as busy. A granted write leaves at most once per grant: the run journals it
  before it leaves (capture/spent.py), and the re-run fails a journaled write again instead of sending it.

A retry job `{"hold", "window"}` (the card's retry button, or the end of a login window) runs
heldapi.py `retry`, which writes the job id into the record with its result: a replay finds it landed.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from functools import partial
from typing import Any

from draft_studio.api import Api
from draft_studio.capture import routes as capture_routes
from draft_studio.capture.pending import RequestNotFound
from draft_studio.held import HeldNotFound
from draft_studio.heldapi import HeldApi
from draft_studio.jobs import Job
from draft_studio.storebase import DraftNotFound

Json = dict[str, Any]
Reply = tuple[int, Json]
OPS = ("grant", "deny", "retry")


def decide(api: Api, op: str, rid: str, body: Any, *, resume: bool = False) -> Reply:
    """The capture re-run of a decision and the held answer's move (one unit: a job covers both)."""
    captures, held = api.captures, HeldApi(api.shots())
    try:
        status, out = captures.grant(rid, body, resume) if op == "grant" else captures.deny(rid, resume)
        held.decided(rid, status, out)
    except Exception:
        held.abandon(rid=rid)  # never leave a granted request with a hold waiting on it
        raise
    return status, out


def capture(api: Api, method: str, path: str, body: Callable[[], Any]) -> tuple[int, Json | bytes]:
    """The capture routes (server.py); a grant or deny runs as a job."""
    found = capture_routes.decision(method, path)
    draft_id = ""
    if found is not None:
        with contextlib.suppress(RequestNotFound):  # pruned: the route answers 404, the hold expires
            draft_id = str(api.captures.pending.record(found[1])["draftId"])
    if found is None or not draft_id:
        status, out = capture_routes.handle(api.captures, method, path, body)
        if found is not None and isinstance(out, dict):
            HeldApi(api.shots()).decided(found[1], status, out)
        return status, out
    op, rid = found
    data = body() if op == "grant" else {}
    work = partial(decide, api, op, rid, data)
    jobs = api.d.jobs
    return jobs.run(op, draft_id, {"rid": rid, "body": data}, work) if jobs else work()


def retry(api: Api, draft_id: str, hold: str, window: str = "") -> Json:
    """Retry a hold's login/busy captures as a job; `window`: the login window whose end asked for it."""
    work = partial(api.held, draft_id, {"retry": hold})
    jobs = api.d.jobs
    return jobs.run("retry", draft_id, {"hold": hold, "window": window}, work) if jobs else work()


def replay(api: Api, job: Job) -> Reply | None:
    """Run a cut decision or retry again; None when it had landed."""
    if job.op == "retry":
        held = HeldApi(api.shots())
        with contextlib.suppress(HeldNotFound, DraftNotFound):  # the hold was collected meanwhile
            try:
                held.retry(job.draft_id, str(job.body["hold"]), job.id)
            except Exception:
                held.abandon(draft_id=job.draft_id, hid=str(job.body["hold"]))
                raise
        return None
    rid = str(job.body["rid"])
    if HeldApi(api.shots()).held.by_request(rid) is None:
        return None
    return decide(api, job.op, rid, job.body.get("body") or {}, resume=True)
