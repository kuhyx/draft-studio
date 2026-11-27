"""At boot: every held answer a restart left stuck is resumed, retried or ended as a failed hold.

Run once by the new server, after the job replays started (`Resumable.resume_held`). A held record is
left alone when a replayed grant, deny or retry job owns it, or when it waits on kuhy (a pending
permission card, a login). What no job owns is stuck, and gets one of:

- its permission request was decided by a run nobody finished (a cut grant from before jobs existed:
  the choice kuhy made, a partial grant, is not on disk): the slot is asked again as a new capture, so a
  fresh card shows and nothing is sent without a new answer;
- a slot waiting on a busy profile, or asked again: the hold's retry job (heldjobs.py);
- every capture resolved but the answer not released: it is released now.

A job that fails for good drops what it waited on (heldapi.py `abandon`): the hold then shows as failed.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from draft_studio import heldjobs
from draft_studio.api import Api
from draft_studio.capture.pending import DENIED, GRANTED, RequestNotFound
from draft_studio.held import HELD, HeldNotFound
from draft_studio.jobs import Jobs
from draft_studio.storebase import DraftNotFound

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]


def sweep(api: Api) -> None:
    """Look at every held record once."""
    for rec in list(api.shots().held.all_held()):
        try:
            with contextlib.suppress(HeldNotFound, DraftNotFound):
                _unstick(api, rec["draftId"], rec["id"])
        except Exception:
            LOG.exception("held answer %s: sweep failed", rec["id"])


def _owned(jobs: Jobs | None, rec: Json) -> bool:
    """A grant, deny or retry job is running on this record (a replay the new server started)."""
    rids = {s["rid"] for s in rec["shots"].values() if s["rid"]}
    running = jobs.running() if jobs else []
    return any(
        j.op in heldjobs.OPS and (j.body.get("rid") in rids or j.body.get("hold") == rec["id"])
        for j in running
    )


def _decided(api: Api, rid: str) -> bool:
    """The request was granted or denied: its re-run was started by somebody."""
    try:
        return api.captures.pending.record(rid)["status"] in (GRANTED, DENIED)
    except RequestNotFound:
        return False


def _unstick(api: Api, draft_id: str, hid: str) -> None:
    shots = api.shots()
    with shots.held.lock:
        rec = shots.held.get(draft_id, hid)
        if rec["status"] != HELD or _owned(api.d.jobs, rec):
            return
        cut = [s for s in rec["shots"].values() if s["state"] == "pending" and _decided(api, s["rid"])]
        for slot in cut:
            slot.update(state="new", rid=None)
        if cut:
            shots.held.put(rec)
        retry = any(s["state"] in ("busy", "new") for s in rec["shots"].values())
        if not retry and not shots.holds(rec):
            shots.release(rec)
    if retry:
        heldjobs.retry(api, draft_id, hid)
