"""Claude calls that survive a restart of the server: each runs in its own unit and is journaled in its job.

Inside a job (jobs.py) a call writes `<job>/call-<k>/call.json`, starts `draft_studio.callrun` in a
transient systemd unit (detach.py) and waits for `result.json`. `k` counts the calls of one run, so
a replayed job asks for the same folders: a finished call hands its answer back at once, a running
one is waited for, a dead one (no result, unit gone) is started again once, then fails loudly.
Outside a job (scripts, tests) the call runs in this process.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from draft_studio.atomic import read_json, write_json
from draft_studio.callio import ctx_to_json
from draft_studio.claude import Claude, ClaudeError, Context
from draft_studio.detach import DetachError, Spawn
from draft_studio.jobs import Job, Jobs

POLL_S = 0.1
ATTEMPTS = 2  # the first run, and one more when its unit died without an answer
STARTUP_S = 30.0  # how long a started unit may take to write its pid file
Json = dict[str, Any]


def proc_start(pid: int) -> int | None:
    """The start time (clock ticks since boot) of process `pid`; None when there is none."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return int(stat.rsplit(")", 1)[1].split()[19])


class DurableClaude:
    """The Claude protocol (claude.py) over journaled, detached calls."""

    def __init__(
        self,
        inner: Claude,
        jobs: Jobs,
        spawn: Spawn,
        *,
        poll: float = POLL_S,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.inner, self.jobs, self.spawn = inner, jobs, spawn
        self.poll, self.sleep, self.clock = poll, sleep, clock

    def intake(self, ctx: Context) -> Json:
        """Intake round (claude.py)."""
        out: Json = self._call("intake", ctx)
        return out

    def draft(self, ctx: Context) -> Json:
        """First draft (claude.py)."""
        out: Json = self._call("draft", ctx)
        return out

    def ask(self, ctx: Context) -> str:
        """Answer to a question (claude.py)."""
        return str(self._call("ask", ctx))

    def rewrite(self, ctx: Context) -> Json:
        """Replacement for the marked span (claude.py)."""
        out: Json = self._call("rewrite", ctx)
        return out

    def fix_capture(self, ctx: Context, capture: Json, error: Json) -> Json:
        """A corrected capture (claude.py)."""
        out: Json = self._call("fix_capture", ctx, capture=capture, error=error)
        return out

    def _call(self, method: str, ctx: Context, **extra: Json) -> Any:
        job = self.jobs.current()
        if job is None:
            return getattr(self.inner, method)(ctx, *extra.values())
        cdir = job.dir / f"call-{job.next_call()}"
        job.landing = False  # waiting for the unit: a stop may cut in here
        out = self._journal(job, cdir, {"method": method, "ctx": ctx_to_json(ctx), **extra})
        job.landing = True  # the answer is in: until the job ends, a stop waits
        if not out["ok"]:
            raise ClaudeError(out["error"])
        return out["value"]

    def _journal(self, job: Job, cdir: Path, call: Json) -> Json:
        cdir.mkdir(exist_ok=True)
        started: Json | None = read_json(cdir / "call.json")
        if started is None:
            started = self._start(job, cdir, call, 0)
        while True:
            if (out := self._result(cdir)) is not None:
                return out
            if not self._alive(cdir, started["started"]):
                if (out := self._result(cdir)) is not None:  # it finished while we looked
                    return out
                if started["attempt"] + 1 >= ATTEMPTS:
                    return {"ok": False, "error": "claude run died"}
                started = self._start(job, cdir, started, started["attempt"] + 1)
            self.sleep(self.poll)

    @staticmethod
    def _result(cdir: Path) -> Json | None:
        out: Json | None = read_json(cdir / "result.json")
        return out

    def _start(self, job: Job, cdir: Path, call: Json, attempt: int) -> Json:
        (cdir / "pid").unlink(missing_ok=True)
        call = {**call, "attempt": attempt, "started": self.clock()}
        write_json(cdir / "call.json", call)
        unit = f"ds-call-{job.id}-{cdir.name.removeprefix('call-')}-{attempt}"
        try:
            self.spawn(unit, [sys.executable, "-m", "draft_studio.callrun", str(cdir)], cdir / "out.log")
        except DetachError as exc:
            raise ClaudeError(str(exc)) from exc
        return call

    def _alive(self, cdir: Path, started: float) -> bool:
        """The unit still runs: its pid file names a live process of the same start time."""
        try:
            pid, _, begun = (cdir / "pid").read_text(encoding="utf-8").partition(" ")
        except FileNotFoundError:
            return self.clock() - started < STARTUP_S
        return pid.isdigit() and str(proc_start(int(pid))) == begun
