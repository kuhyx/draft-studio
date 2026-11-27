"""draft_studio.durable: Claude calls that run in their own unit and are journaled in the job."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from draft_studio import callrun
from draft_studio.claude import ClaudeError, Context
from draft_studio.claude_fake import FakeClaude
from draft_studio.detach import DetachError
from draft_studio.durable import DurableClaude, proc_start
from draft_studio.jobs import Jobs

CTX = Context(kind="text", lang="en", brief="b", intake=[], text="one two three", sel=(0, 3), instruction="q")


class Spawner:
    """Stands in for systemd-run: `run` executes the call at once, like the unit would."""

    def __init__(self, run: bool = True) -> None:
        self.run, self.calls = run, []
        self.after: list[Any] = []

    def __call__(self, unit: str, argv: list[str], log: Path | None) -> None:
        self.calls.append(unit)
        if self.run:
            callrun.main([argv[-1]], factory=FakeClaude)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def rig(tmp_path: Path, spawn: Spawner | None = None) -> tuple[Jobs, DurableClaude, Spawner, Clock]:
    spawn = spawn or Spawner()
    clock = Clock()
    jobs = Jobs(tmp_path, clock=clock)
    claude = DurableClaude(FakeClaude(), jobs, spawn, sleep=clock.sleep, clock=clock, poll=1.0)
    return jobs, claude, spawn, clock


def test_outside_a_job_the_call_runs_in_process(tmp_path: Path) -> None:
    _, claude, spawn, _ = rig(tmp_path)
    assert claude.ask(CTX) == "[en] About 'one': q"
    assert spawn.calls == []


def test_inside_a_job_the_call_runs_in_a_unit_of_its_own(tmp_path: Path) -> None:
    jobs, claude, spawn, _ = rig(tmp_path)
    out: list[Any] = []
    jobs.run("ask", "d1", {}, lambda: out.append(claude.ask(CTX)))
    assert out == ["[en] About 'one': q"]
    (unit,) = spawn.calls
    assert unit.startswith("ds-call-") and unit.endswith("-0-0")
    (call,) = (p for p in jobs.dir.glob("*/call-0/call.json"))
    assert json.loads(call.read_text())["method"] == "ask"


def test_every_method_goes_through_the_journal(tmp_path: Path) -> None:
    jobs, claude, spawn, _ = rig(tmp_path)
    cap = {"steps": [], "highlight": {"text": "x"}}

    def work() -> list[Any]:
        return [
            claude.intake(CTX),
            claude.draft(CTX),
            claude.rewrite(CTX),
            claude.fix_capture(CTX, cap, {"text": "x", "seen": []}),
        ]

    got = jobs.run("brief", "d1", {}, work)
    assert got[0]["questions"] and got[1]["text"] and got[2]["replacement"] and got[3]["steps"] == []
    assert len(spawn.calls) == 4


def test_a_replayed_job_takes_the_stored_result_and_spawns_nothing(tmp_path: Path) -> None:
    jobs, claude, spawn, _ = rig(tmp_path)
    with pytest.raises(SystemExit):  # the server dies after the unit answered, before landing
        jobs.run("ask", "d1", {}, lambda: (claude.ask(CTX), (_ for _ in ()).throw(SystemExit))[1])
    again: list[Any] = []
    jobs2, claude2, spawn2, _ = rig(tmp_path, spawn=Spawner(run=False))
    for thread in jobs2.resume(lambda job: again.append(claude2.ask(CTX))):
        thread.join(5)
    assert again == ["[en] About 'one': q"] and spawn2.calls == [] and len(spawn.calls) == 1


def test_a_failure_the_unit_reported_is_a_claude_error(tmp_path: Path) -> None:
    jobs, claude, _, _ = rig(tmp_path)
    bad = Context(kind="text", lang="en", brief="[fail]", intake=[], text="x")
    with pytest.raises(ClaudeError, match="fake failure"):
        jobs.run("brief", "d1", {}, lambda: claude.intake(bad))


def test_a_unit_that_never_starts_is_retried_once_then_a_loud_error(tmp_path: Path) -> None:
    jobs, claude, spawn, _ = rig(tmp_path, Spawner(run=False))
    with pytest.raises(ClaudeError, match="died"):
        jobs.run("ask", "d1", {}, lambda: claude.ask(CTX))
    assert [u[-3:] for u in spawn.calls] == ["0-0", "0-1"]


def test_a_unit_that_answers_late_is_waited_for(tmp_path: Path) -> None:
    spawn = Spawner(run=False)
    jobs, claude, _, clock = rig(tmp_path, spawn)
    real_sleep = clock.sleep
    naps = []

    def sleep(seconds: float) -> None:
        naps.append(seconds)
        real_sleep(seconds)
        if len(naps) == 3:
            (cdir,) = jobs.dir.glob("*/call-0")
            callrun.main([str(cdir)], factory=FakeClaude)

    claude.sleep = sleep
    out: list[Any] = []
    jobs.run("ask", "d1", {}, lambda: out.append(claude.ask(CTX)))
    assert out and len(naps) == 3 and len(spawn.calls) == 1


def test_a_running_unit_is_alive_a_gone_one_is_dead(tmp_path: Path) -> None:
    spawn = Spawner(run=False)
    jobs, claude, _, clock = rig(tmp_path, spawn)
    mine = f"{os.getpid()} {proc_start(os.getpid())}"
    naps: list[float] = []

    def sleep(seconds: float) -> None:
        naps.append(seconds)
        clock.now += 100  # far beyond the start-up grace: only the pid file keeps the call alive
        (cdir,) = jobs.dir.glob("*/call-0")
        if len(naps) == 1:
            (cdir / "pid").write_text(mine)
        elif len(naps) == 3:
            callrun.main([str(cdir)], factory=FakeClaude)
        elif len(naps) == 2:
            (cdir / "pid").write_text(f"{os.getpid()} 1")  # same pid number, other process

    claude.sleep = sleep
    out: list[Any] = []
    jobs.run("ask", "d1", {}, lambda: out.append(claude.ask(CTX)))
    assert out and len(spawn.calls) == 2  # the reused pid counted as dead: one respawn answered


def test_proc_start_of_a_missing_process_is_none() -> None:
    assert proc_start(2**22 + 12345) is None


def test_a_spawn_failure_is_a_claude_error(tmp_path: Path) -> None:
    def broken(unit: str, argv: list[str], log: Path | None) -> None:
        raise DetachError("no systemd")

    jobs, claude, _, _ = rig(tmp_path)
    claude.spawn = broken
    with pytest.raises(ClaudeError, match="no systemd"):
        jobs.run("ask", "d1", {}, lambda: claude.ask(CTX))


def test_landing_is_marked_after_the_answer_and_cleared_by_the_next_call(tmp_path: Path) -> None:
    jobs, claude, _, _ = rig(tmp_path)
    flags: list[bool] = []

    def work() -> None:
        job = jobs.current()
        assert job is not None
        claude.ask(CTX)
        flags.append(job.landing)
        claude.ask(CTX)
        flags.append(job.landing)

    jobs.run("ask", "d1", {}, work)
    assert flags == [True, True]
    assert jobs.busy()["busy"] is False


def test_an_answer_that_lands_while_the_unit_is_checked_is_taken(tmp_path: Path) -> None:
    class Racy(DurableClaude):
        def _alive(self, cdir: Path, started: float) -> bool:
            callrun.main([str(cdir)], factory=FakeClaude)
            return False

    spawn = Spawner(run=False)
    jobs = Jobs(tmp_path)
    claude = Racy(FakeClaude(), jobs, spawn, poll=0.0)
    out: list[Any] = []
    jobs.run("ask", "d1", {}, lambda: out.append(claude.ask(CTX)))
    assert out and len(spawn.calls) == 1
