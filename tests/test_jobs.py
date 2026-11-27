"""draft_studio.jobs: one file per long request, replayed after a restart until it is done."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from draft_studio.jobs import Job, Jobs


def jobs_of(tmp_path: Path) -> Jobs:
    return Jobs(tmp_path)


def record(jobs: Jobs, job_id: str) -> dict[str, Any]:
    return json.loads((jobs.dir / job_id / "job.json").read_text())


def test_run_writes_the_job_before_the_work_and_marks_it_done(tmp_path: Path) -> None:
    jobs = jobs_of(tmp_path)
    seen: dict[str, Any] = {}

    def work() -> str:
        job = jobs.current()
        assert job is not None
        seen.update(record(jobs, job.id), landing=job.landing)
        return "answer"

    assert jobs.run("ask", "d1", {"q": 1}, work) == "answer"
    assert seen["status"] == "running" and seen["op"] == "ask" and seen["body"] == {"q": 1}
    assert jobs.current() is None
    (job_dir,) = jobs.dir.iterdir()
    assert record(jobs, job_dir.name)["status"] == "done"
    assert (job_dir.stat().st_mode & 0o777) == 0o700


def test_a_failing_request_is_done_with_its_error_and_still_raises(tmp_path: Path) -> None:
    jobs = jobs_of(tmp_path)

    def work() -> None:
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        jobs.run("ask", "d1", {}, work)
    (job_dir,) = jobs.dir.iterdir()
    assert record(jobs, job_dir.name)["status"] == "done"
    assert record(jobs, job_dir.name)["error"] == "ValueError: bad"


def test_busy_lists_running_jobs_from_memory(tmp_path: Path) -> None:
    jobs = jobs_of(tmp_path)
    inside: dict[str, Any] = {}
    jobs.run("propose", "d1", {}, lambda: inside.update(jobs.busy()))
    assert inside["busy"] is True
    assert [(j["op"], j["draftId"]) for j in inside["jobs"]] == [("propose", "d1")]
    assert jobs.busy() == {"busy": False, "jobs": [], "failed": []}


def test_resume_replays_a_running_job_once_and_marks_it_done(tmp_path: Path) -> None:
    first = jobs_of(tmp_path)
    started = threading.Event()
    with pytest.raises(SystemExit):  # the server dies in the middle of the request
        first.run("ask", "d1", {"q": 2}, lambda: (started.set(), (_ for _ in ()).throw(SystemExit))[1])
    (job_dir,) = first.dir.iterdir()
    assert record(first, job_dir.name)["status"] == "running"
    replayed: list[Job] = []
    second = jobs_of(tmp_path)
    for thread in second.resume(replayed.append):
        thread.join(5)
    assert [(j.op, j.draft_id, j.body) for j in replayed] == [("ask", "d1", {"q": 2})]
    assert record(second, job_dir.name)["status"] == "done"
    for thread in jobs_of(tmp_path).resume(replayed.append):
        thread.join(5)
    assert len(replayed) == 1


def test_a_replay_that_fails_is_listed_and_can_be_retried(tmp_path: Path) -> None:
    first = jobs_of(tmp_path)
    with pytest.raises(SystemExit):
        first.run("brief", "d1", {}, lambda: (_ for _ in ()).throw(SystemExit))
    second = jobs_of(tmp_path)

    def boom(job: Job) -> None:
        raise RuntimeError("claude run died")

    for thread in second.resume(boom):
        thread.join(5)
    (item,) = second.busy()["failed"]
    assert item["error"] == "RuntimeError: claude run died" and item["op"] == "brief"
    assert second.busy()["busy"] is False
    ok: list[str] = []
    thread = second.retry(item["id"], lambda job: ok.append(job.id))
    assert thread is not None
    thread.join(5)
    assert ok == [item["id"]] and second.busy()["failed"] == []
    assert second.retry("nope", lambda job: None) is None
    assert second.retry(item["id"], lambda job: None) is None  # no longer failed


def test_old_done_jobs_are_pruned_and_a_torn_file_is_skipped(tmp_path: Path) -> None:
    clock = [1000.0]
    jobs = Jobs(tmp_path, clock=lambda: clock[0])
    jobs.run("ask", "d1", {}, lambda: None)
    broken = jobs.dir / "torn"
    broken.mkdir()
    (broken / "job.json").write_text("{")
    clock[0] += 3 * 86400
    Jobs(tmp_path, clock=lambda: clock[0]).resume(lambda job: None)
    assert [p.name for p in jobs.dir.iterdir()] == ["torn"]


def test_drain_waits_until_no_job_is_landing(tmp_path: Path) -> None:
    jobs = jobs_of(tmp_path)
    ticks = iter(range(100))
    result: list[bool] = []

    def work() -> None:
        job = jobs.current()
        assert job is not None
        job.landing = True
        naps: list[float] = []

        def nap(s: float) -> None:
            naps.append(s)
            job.landing = len(naps) < 3

        result.append(jobs.drain(5.0, sleep=nap, clock=lambda: float(next(ticks))))
        assert len(naps) == 3

    jobs.run("ask", "d1", {}, work)
    assert result == [True]
    stuck = jobs_of(tmp_path)
    clock = iter(range(100))

    def hold() -> None:
        job = stuck.current()
        assert job is not None
        job.landing = True
        result.append(stuck.drain(2.0, sleep=lambda s: None, clock=lambda: float(next(clock))))

    stuck.run("ask", "d1", {}, hold)
    assert result[-1] is False


def test_a_failed_job_stays_listed_across_restarts_and_retry_asks_failed_calls_again(tmp_path: Path) -> None:
    first = jobs_of(tmp_path)
    with pytest.raises(SystemExit):
        first.run("ask", "d1", {}, lambda: (_ for _ in ()).throw(SystemExit))
    second = jobs_of(tmp_path)

    def boom(job: Job) -> None:
        raise RuntimeError("x")

    for thread in second.resume(boom):
        thread.join(5)
    third = jobs_of(tmp_path)
    assert third.resume(boom) == []
    (item,) = third.busy()["failed"]
    job_dir = third.dir / item["id"]
    for name, ok in (("call-0", False), ("call-1", True)):
        (job_dir / name).mkdir()
        (job_dir / name / "result.json").write_text(json.dumps({"ok": ok}))
    (job_dir / "call-2").mkdir()  # no result at all: it died
    thread = third.retry(item["id"], lambda job: None)
    assert thread is not None
    thread.join(5)
    assert sorted(p.name for p in job_dir.glob("call-*")) == ["call-1"]
