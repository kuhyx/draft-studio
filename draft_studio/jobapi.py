"""Which API calls are jobs (jobs.py), how a replayed one runs, and the two routes that show them.

GET  /api/busy              200 {"busy": bool, "jobs": [{id, op, draftId, since}],
                                 "failed": [{id, op, draftId, error}]}
                            from memory: what runs now, and the jobs a restart could not finish
POST /api/jobs/<id>/retry   202 {"retry": true} runs a failed job again; 404 {"error": "not-found"}
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from draft_studio.api import Api
from draft_studio.jobs import Job, Jobs

# The calls that wait on Claude: each is a job, so a restart in the middle replays it.
JOB_OPS = ("brief", "intake", "ask", "propose")
RETRY = re.compile(r"^/api/jobs/([^/]+)/retry$")


def run[T](jobs: Jobs | None, op: str, draft_id: str, body: dict[str, Any], fn: Callable[[], T]) -> T:
    """`fn` as a job when `op` is one; otherwise (or without a job store, in tests) as it is."""
    if jobs is None or op not in JOB_OPS:
        return fn()
    return jobs.run(op, draft_id, body, fn)


def busy(jobs: Jobs | None) -> dict[str, Any]:
    """GET /api/busy."""
    return jobs.busy() if jobs else {"busy": False, "jobs": [], "failed": []}


def replay(api: Api, job: Job) -> object:
    """Run a job again from its file (the new server's side of a restart)."""
    if job.op not in JOB_OPS:
        raise ValueError(f"unknown op {job.op!r} in job {job.id}")
    return getattr(api, job.op)(job.draft_id, job.body)


def retry(jobs: Jobs | None, api: Api, job_id: str) -> threading.Thread | None:
    """POST /api/jobs/<id>/retry: the thread running the failed job again, None when there is no such one."""
    return jobs.retry(job_id, lambda job: replay(api, job)) if jobs else None
