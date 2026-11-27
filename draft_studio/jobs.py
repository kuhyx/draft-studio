"""Long requests as files: a request that outlives the server process is picked up again by the next one.

Every ask, proposal, brief and intake round writes `<data>/jobs/<id>/job.json` before it does any
work (the op, the draft, the request body) and marks it done when the answer landed. The slow part,
the Claude call, runs in its own systemd unit and journals its answer in the job's `call-<k>/`
folder (durable.py). A restart in between leaves the job `running`; the next server replays it from
the start: the journal hands every finished call back at once, an unfinished one is waited for, so
the answer lands once. A replay that cannot finish is marked `failed`, listed by `GET /api/busy`
and run again by `POST /api/jobs/<id>/retry`.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from draft_studio.atomic import read_json, write_json

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
RUNNING, DONE, FAILED = "running", "done", "failed"
KEEP_DONE_S = 86400.0
DRAIN_NAP_S = 0.05


@dataclass
class Job:
    """One request's file. `landing`: its answer arrived and is being written (a restart waits for that)."""

    id: str
    op: str
    draft_id: str
    body: Json
    dir: Path
    status: str = RUNNING
    error: str = ""
    created: float = 0.0
    landing: bool = False
    calls: int = 0

    def next_call(self) -> int:
        """The index of the next Claude call of this run: replays count the same way."""
        self.calls += 1
        return self.calls - 1


class Jobs:
    """The jobs of one data dir; shared by all request threads."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time) -> None:
        self.dir, self.clock = root / "jobs", clock
        self._local = threading.local()
        self._lock = threading.Lock()
        self._active: dict[str, Job] = {}
        self._failed: dict[str, Job] = {}

    def current(self) -> Job | None:
        """The job the calling thread is working on."""
        job: Job | None = getattr(self._local, "job", None)
        return job

    def tag(self) -> dict[str, str]:
        """{"job": id} while this thread works on a job: what lands carries it, so a replay lands once."""
        job = self.current()
        return {"job": job.id} if job else {}

    # -- files -----------------------------------------------------------
    def _save(self, job: Job) -> None:
        raw = {"id": job.id, "op": job.op, "draftId": job.draft_id, "body": job.body}
        write_json(
            job.dir / "job.json", {**raw, "status": job.status, "error": job.error, "created": job.created}
        )

    def _load(self, folder: Path) -> Job | None:
        try:
            raw: Json = read_json(folder / "job.json") or {}
            return Job(
                raw["id"],
                raw["op"],
                raw["draftId"],
                raw["body"],
                folder,
                raw["status"],
                raw["error"],
                raw["created"],
            )
        except (ValueError, KeyError, TypeError):
            LOG.warning("job file %s is unreadable: skipped", folder)
            return None

    def find(self, op: str, **body: str) -> Job | None:
        """A job of `op` whose body has these fields, in any status (its file is its record)."""
        for folder in sorted(self.dir.iterdir()) if self.dir.is_dir() else []:
            job = self._load(folder)
            if job and job.op == op and all(job.body.get(k) == v for k, v in body.items()):
                return job
        return None

    # -- running ---------------------------------------------------------
    def run[T](self, op: str, draft_id: str, body: Json, fn: Callable[[], T]) -> T:
        """Do `fn` as a job (the live path of one request)."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = self.dir / f"{int(self.clock() * 1000):x}-{secrets.token_hex(3)}"
        folder.mkdir(mode=0o700)
        job = Job(folder.name, op, draft_id, body, folder, created=self.clock())
        self._save(job)
        return self._execute(job, fn, live=True)

    def _execute[T](self, job: Job, fn: Callable[[], T], *, live: bool) -> T:
        with self._lock:
            self._active[job.id] = job
        self._local.job = job
        try:
            out = fn()
        except Exception as exc:
            # A live request answered its client with the error; a replay has nobody to tell.
            self._end(job, DONE if live else FAILED, f"{type(exc).__name__}: {exc}")
            raise
        else:
            self._end(job, DONE, "")
            return out
        finally:
            self._local.job = None
            with self._lock:
                self._active.pop(job.id, None)

    def _end(self, job: Job, status: str, error: str) -> None:
        job.status, job.error, job.landing = status, error, False
        self._save(job)
        if status == FAILED:
            with self._lock:
                self._failed[job.id] = job

    # -- after a restart --------------------------------------------------
    def resume(self, replay: Callable[[Job], object]) -> list[threading.Thread]:
        """Replay every job the last server left running; list the failed ones; prune old done ones."""
        threads: list[threading.Thread] = []
        for folder in sorted(self.dir.iterdir()) if self.dir.is_dir() else []:
            job = self._load(folder)
            if job is None:
                continue
            if job.status == RUNNING:
                threads.append(self._start(job, replay))
            elif job.status == FAILED:
                self._failed[job.id] = job
            elif self.clock() - job.created > KEEP_DONE_S:
                shutil.rmtree(folder, ignore_errors=True)
        return threads

    def _start(self, job: Job, replay: Callable[[Job], object]) -> threading.Thread:
        thread = threading.Thread(target=self._replay, args=(job, replay), daemon=True)
        with self._lock:
            self._active[job.id] = job
        thread.start()
        return thread

    def _replay(self, job: Job, replay: Callable[[Job], object]) -> None:
        try:
            self._execute(job, lambda: replay(job), live=False)
        except Exception:
            LOG.exception("job %s (%s) failed on replay", job.id, job.op)

    def retry(self, job_id: str, replay: Callable[[Job], object]) -> threading.Thread | None:
        """Run a failed job again (its failed calls are asked again); None when no such failed job."""
        with self._lock:
            job = self._failed.pop(job_id, None)
        if job is None:
            return None
        for call in job.dir.glob("call-*"):
            if (read_json(call / "result.json") or {}).get("ok") is not True:
                shutil.rmtree(call, ignore_errors=True)
        job.status, job.error = RUNNING, ""
        self._save(job)
        return self._start(job, replay)

    # -- what is going on -------------------------------------------------
    def running(self) -> list[Job]:
        """The jobs in flight right now (live requests and replays)."""
        with self._lock:
            return list(self._active.values())

    def busy(self) -> Json:
        """GET /api/busy: the jobs in flight and the failed ones, from memory (no disk, no fork)."""
        with self._lock:
            running = [
                {"id": j.id, "op": j.op, "draftId": j.draft_id, "since": j.created}
                for j in self._active.values()
            ]
            failed = [
                {"id": j.id, "op": j.op, "draftId": j.draft_id, "error": j.error}
                for j in self._failed.values()
            ]
        return {"busy": bool(running), "jobs": running, "failed": failed}

    def drain(
        self,
        timeout: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> bool:
        """Wait until no job is writing its answer (a stop must not cut that in two); False on timeout."""
        deadline = clock() + timeout
        while any(j.landing for j in list(self._active.values())):
            if clock() >= deadline:
                return False
            sleep(DRAIN_NAP_S)
        return True
