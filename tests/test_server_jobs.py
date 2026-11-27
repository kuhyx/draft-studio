"""The server with durable jobs: /api/busy, replay after a restart, retry of a failed job."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import callrun
from draft_studio import server as srv
from draft_studio.claude_fake import FakeClaude
from draft_studio.durable import DurableClaude
from draft_studio.gmail import FakeGmail
from draft_studio.jobs import Job, Jobs
from draft_studio.store import Store
from tests.conftest import App


class Spawner:
    """systemd-run stand-in: the unit's work runs when `release` is set (at once by default)."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.release.set()
        self.calls = 0

    def __call__(self, unit: str, argv: list[str], log: Path | None) -> None:
        self.calls += 1
        threading.Thread(target=self._work, args=(argv[-1],), daemon=True).start()

    def _work(self, cdir: str) -> None:
        self.release.wait(10)
        callrun.main([cdir], factory=FakeClaude)


def boot(tmp_path: Path, spawn: Spawner) -> tuple[App, Jobs]:
    """A server on tmp_path/data as `python -m draft_studio` builds it (durable Claude, jobs)."""
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("x")
    store = Store(tmp_path / "data")
    jobs = Jobs(tmp_path / "data", clock=time.time)
    claude = DurableClaude(FakeClaude(), jobs, spawn, poll=0.01)
    deps = srv.Deps(store, claude, FakeGmail(tmp_path / "g.json"), dist, tmp_path, "b1", jobs=jobs)
    httpd = srv.make_server(deps, port=0)
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.02), daemon=True).start()
    return App(
        f"http://127.0.0.1:{httpd.server_address[1]}", store, FakeGmail(tmp_path / "g.json"), httpd
    ), jobs


@pytest.fixture
def spawn() -> Spawner:
    return Spawner()


@pytest.fixture
def app_jobs(tmp_path: Path, spawn: Spawner) -> Iterator[tuple[App, Jobs]]:
    app, jobs = boot(tmp_path, spawn)
    yield app, jobs
    app.httpd.shutdown()
    app.httpd.server_close()


def draft_with_text(app: App) -> str:
    _, made = app.call("POST", "/api/drafts", {"kind": "text"})
    app.call("PUT", f"/api/drafts/{made['id']}", {"text": "one two three four"})
    return str(made["id"])


ASK = {"text": "one two three four", "from": 0, "to": 3, "question": "why"}


def test_an_ask_runs_in_a_unit_and_lands_with_the_job_id(app_jobs: tuple[App, Jobs], spawn: Spawner) -> None:
    app, _ = app_jobs
    did = draft_with_text(app)
    status, entry = app.call("POST", f"/api/drafts/{did}/ask", ASK)
    assert status == 200 and entry["answer"] == "[en] About 'one': why"
    assert spawn.calls == 1 and entry["job"]
    assert app.call("GET", "/api/busy") == (200, {"busy": False, "jobs": [], "failed": []})


def test_busy_names_the_running_work(app_jobs: tuple[App, Jobs], spawn: Spawner) -> None:
    app, _ = app_jobs
    did = draft_with_text(app)
    spawn.release.clear()
    done: list[Any] = []
    threading.Thread(target=lambda: done.append(app.call("POST", f"/api/drafts/{did}/ask", ASK))).start()
    for _ in range(200):
        _, busy = app.call("GET", "/api/busy")
        if busy["busy"]:
            break
        time.sleep(0.01)
    assert [(j["op"], j["draftId"]) for j in busy["jobs"]] == [("ask", did)]
    spawn.release.set()
    for _ in range(200):
        if done:
            break
        time.sleep(0.01)
    assert done[0][0] == 200


def restart(tmp_path: Path, spawn: Spawner, old: App) -> tuple[App, Jobs]:
    old.httpd.shutdown()
    old.httpd.server_close()
    return boot(tmp_path, spawn)


def test_a_job_the_old_server_left_running_lands_exactly_once(tmp_path: Path, spawn: Spawner) -> None:
    app, jobs = boot(tmp_path, spawn)
    did = draft_with_text(app)

    def dies_before_landing() -> None:
        jobs.run("ask", did, ASK, lambda: (_ for _ in ()).throw(SystemExit))

    with pytest.raises(SystemExit):
        dies_before_landing()
    app2, jobs2 = restart(tmp_path, spawn, app)
    for thread in app2.httpd.resume_jobs():
        thread.join(10)
    asks = app2.call("GET", f"/api/drafts/{did}")[1]["meta"]["asks"]
    assert [a["answer"] for a in asks] == ["[en] About 'one': why"] and len(jobs2.busy()["jobs"]) == 0
    app2.httpd.shutdown()
    app2.httpd.server_close()


def test_a_job_that_landed_but_never_finished_does_not_land_twice(tmp_path: Path, spawn: Spawner) -> None:
    app, jobs = boot(tmp_path, spawn)
    did = draft_with_text(app)
    api = srv.Api(app.httpd.deps, app.httpd.captures)

    def lands_then_dies() -> None:
        api.ask(did, ASK)
        raise SystemExit

    with pytest.raises(SystemExit):
        jobs.run("ask", did, ASK, lands_then_dies)
    app2, _ = restart(tmp_path, spawn, app)
    for thread in app2.httpd.resume_jobs():
        thread.join(10)
    assert len(app2.call("GET", f"/api/drafts/{did}")[1]["meta"]["asks"]) == 1
    app2.httpd.shutdown()
    app2.httpd.server_close()


def test_replay_runs_the_brief_and_propose_ops_too(tmp_path: Path, spawn: Spawner) -> None:
    app, jobs = boot(tmp_path, spawn)
    briefed, proposed = draft_with_text(app), draft_with_text(app)
    body = {"brief": "Mail to the owner [noq]"}
    with pytest.raises(SystemExit):
        jobs.run("brief", briefed, body, lambda: (_ for _ in ()).throw(SystemExit))
    prop = {"text": "one two three four", "from": 0, "to": 3, "instruction": "shout"}
    with pytest.raises(SystemExit):
        jobs.run("propose", proposed, prop, lambda: (_ for _ in ()).throw(SystemExit))
    app2, _ = restart(tmp_path, spawn, app)
    for thread in app2.httpd.resume_jobs():
        thread.join(10)
    assert app2.call("GET", f"/api/drafts/{briefed}")[1]["meta"]["phase"] == "editing"
    (pending,) = app2.call("GET", f"/api/drafts/{proposed}")[1]["proposals"]
    assert pending["replacement"] == "ONE"
    app2.httpd.shutdown()
    app2.httpd.server_close()


def test_a_failed_replay_shows_in_busy_and_retry_runs_it_again(tmp_path: Path, spawn: Spawner) -> None:
    app, jobs = boot(tmp_path, spawn)
    with pytest.raises(SystemExit):
        jobs.run("ask", "nodraft", ASK, lambda: (_ for _ in ()).throw(SystemExit))
    app2, _ = restart(tmp_path, spawn, app)
    for thread in app2.httpd.resume_jobs():
        thread.join(10)
    _, busy = app2.call("GET", "/api/busy")
    (failed,) = busy["failed"]
    assert failed["op"] == "ask" and "DraftNotFound" in failed["error"]
    status, out = app2.call("POST", f"/api/jobs/{failed['id']}/retry", {})
    assert (status, out) == (202, {"retry": True})
    assert app2.call("POST", "/api/jobs/nothing/retry", {}) == (404, {"error": "not-found"})
    app2.httpd.shutdown()
    app2.httpd.server_close()


def test_a_job_with_an_unknown_op_fails_loudly(tmp_path: Path, spawn: Spawner) -> None:
    app, _ = boot(tmp_path, spawn)
    replay: Callable[[Job], None] = app.httpd.replay
    with pytest.raises(ValueError, match="op"):
        replay(Job("x", "gmail", "d", {}, tmp_path))
    app.httpd.shutdown()
    app.httpd.server_close()
