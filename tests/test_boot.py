"""draft_studio.boot: the environment becomes a server; a stop drains jobs that are writing."""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from typing import Any

import pytest

from draft_studio import boot
from draft_studio.durable import DurableClaude
from draft_studio.jobs import Jobs
from draft_studio.server import DEFAULT_PORT


class FakeHttpd:
    def __init__(self, deps: Any, port: int) -> None:
        self.deps, self.port = deps, port
        self.resumed = self.served = self.adopted = self.swept = False
        self.stopped = threading.Event()

    def resume_jobs(self) -> list[Any]:
        self.resumed = True
        return []

    def resume_held(self) -> list[Any]:
        self.swept = True
        return []

    def resume_login(self) -> None:
        self.adopted = True

    def serve_forever(self) -> None:
        self.served = True

    def shutdown(self) -> None:
        self.stopped.set()


def run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]) -> FakeHttpd:
    made: list[FakeHttpd] = []
    monkeypatch.setattr(boot, "make_server", lambda deps, port: made.append(FakeHttpd(deps, port)) or made[0])
    monkeypatch.setenv("DRAFT_STUDIO_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE", "fake")
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "fake")
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    boot.main(argv)
    return made[0]


def test_main_parses_args_and_resumes_the_jobs_before_serving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    httpd = run_main(monkeypatch, tmp_path, ["--port", "9999", "--build", "zz"])
    assert (
        httpd.port == 9999
        and httpd.served
        and httpd.resumed
        and httpd.swept
        and httpd.adopted
        and httpd.deps.build == "zz"
    )
    assert isinstance(httpd.deps.claude, DurableClaude) and isinstance(httpd.deps.jobs, Jobs)
    assert run_main(monkeypatch, tmp_path, []).port == DEFAULT_PORT


def test_inline_mode_has_no_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_JOBS", "inline")
    httpd = run_main(monkeypatch, tmp_path, [])
    assert httpd.deps.jobs is None and not isinstance(httpd.deps.claude, DurableClaude)
    assert httpd.adopted  # a login window outlives a restart with or without jobs


def test_files_mode_keeps_the_job_files_with_claude_in_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_JOBS", "files")
    httpd = run_main(monkeypatch, tmp_path, [])
    assert isinstance(httpd.deps.jobs, Jobs) and not isinstance(httpd.deps.claude, DurableClaude)


def test_sigterm_ends_the_loop_and_drains(tmp_path: Path) -> None:
    handlers: dict[int, Any] = {}
    drained: list[float] = []

    class Spy(Jobs):
        def drain(self, timeout: float, *rest: Any) -> bool:
            drained.append(timeout)
            return True

    jobs = Spy(tmp_path)
    httpd = FakeHttpd(None, 0)
    boot.serve(httpd, jobs, install=lambda sig, fn: handlers.update({sig: fn}))
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert httpd.stopped.wait(5) and drained == [boot.DRAIN_S]


def test_serve_without_jobs_just_serves() -> None:
    httpd = FakeHttpd(None, 0)
    boot.serve(httpd, None, install=lambda sig, fn: None)
    assert httpd.served
