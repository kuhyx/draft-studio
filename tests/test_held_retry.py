"""The held answer's retry is one job (jobs.py): the card's button, the login window's end, a restart."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import loginwin
from draft_studio.loginwin import LoginWindows
from tests.held_rig import Rig
from tests.test_capture_service import FakeLauncher, ShotPage
from tests.test_held import held, hold
from tests.test_login_route import ORIGIN, PATH, FakeSpawn, until


def login_page() -> ShotPage:
    page = ShotPage(["Zapisz"])
    page.password = True
    return page


def logged_in() -> ShotPage:
    return ShotPage(["Zapisz"])


class Dead:
    """The login window's unit after a restart: already gone."""

    def poll(self) -> int:
        return 0

    def wait(self) -> int:
        return 0


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    found = Rig(tmp_path)
    yield found
    found.close()


def open_window(rig: Rig, launcher: FakeLauncher) -> tuple[Any, str, dict[str, Any], FakeSpawn]:
    app = rig.boot(launcher)
    did, h = hold(app)
    spawn = FakeSpawn()
    app.httpd.logins = LoginWindows(app.store.root, app.httpd.retry_held, spawn, 0.0)
    assert app.call("POST", PATH.format(did, h["id"]), {"origin": ORIGIN}) == (202, {"started": True})
    return app, did, h, spawn


def tries(rig: Rig, did: str, hid: str) -> int:
    return int(json.loads((rig.data / "drafts" / did / "held" / f"{hid}.json").read_text()).get("tries", 0))


def test_a_retry_with_no_login_window_runs_as_a_job(rig: Rig) -> None:
    launcher = FakeLauncher(login_page)
    app = rig.boot(launcher)
    did, h = hold(app)
    launcher.make = logged_in
    status, listing = app.call("POST", f"/api/drafts/{did}/held", {"retry": h["id"]})
    assert status == 200 and listing["held"] == [] and len(listing["released"]) == 1
    (job,) = rig.jobs("retry")
    assert (job["status"], job["draftId"], job["body"]["hold"]) == ("done", did, h["id"])


def test_the_login_window_closing_runs_the_retry_job_once(rig: Rig) -> None:
    launcher = FakeLauncher(login_page)
    app, did, h, spawn = open_window(rig, launcher)
    window = loginwin.read(rig.data)
    assert window is not None
    unit = window.unit
    launcher.make = logged_in
    spawn.procs[0].closed.set()
    until(lambda: bool(held(app, did)["released"]))
    until(lambda: loginwin.read(rig.data) is None)
    (job,) = rig.jobs("retry")
    assert job["body"] == {"hold": h["id"], "window": unit} and job["status"] == "done"
    assert tries(rig, did, h["id"]) == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_retry_cut_by_a_restart_runs_once_not_twice(rig: Rig) -> None:
    launcher = FakeLauncher(login_page)
    app, did, h, spawn = open_window(rig, launcher)
    launcher.make = lambda: (_ for _ in ()).throw(SystemExit)  # the server dies inside the retry
    spawn.procs[0].closed.set()
    until(lambda: bool(rig.jobs("retry")))
    assert rig.jobs("retry")[0]["status"] == "running" and loginwin.read(rig.data) is not None

    again = FakeLauncher(logged_in)
    fresh = rig.restart(app, again)
    fresh.httpd.logins = LoginWindows(rig.data, fresh.httpd.retry_held, unit_window=lambda name: Dead())
    thread = fresh.httpd.resume_login()
    assert thread is not None
    thread.join(10)
    assert again.runs == 1, "the replayed job and the adopted window both retried"
    assert len(held(fresh, did)["released"]) == 1 and loginwin.read(rig.data) is None
    assert tries(rig, did, h["id"]) == 1 and len(rig.jobs("retry")) == 1


def test_a_finished_retry_whose_window_file_stayed_is_not_run_again(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = FakeLauncher(login_page)
    app, did, _, spawn = open_window(rig, launcher)
    monkeypatch.setattr(loginwin, "clear", lambda data_dir: None)  # killed between the job and the clear
    launcher.make = logged_in
    spawn.procs[0].closed.set()
    until(lambda: bool(held(app, did)["released"]))
    until(lambda: rig.jobs("retry")[0]["status"] == "done")
    monkeypatch.undo()
    assert loginwin.read(rig.data) is not None

    again = FakeLauncher(logged_in)
    fresh = rig.restart(app, again)
    fresh.httpd.logins = LoginWindows(rig.data, fresh.httpd.retry_held, unit_window=lambda name: Dead())
    thread = fresh.httpd.resume_login()
    assert thread is not None
    thread.join(10)
    assert again.runs == 0 and loginwin.read(rig.data) is None and len(rig.jobs("retry")) == 1
