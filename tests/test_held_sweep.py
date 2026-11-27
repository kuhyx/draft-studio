"""A held answer is never stuck (kuhy, 2026-11-27): after a restart each record resumes, is retried,
or ends as a failed hold; the boot sweep (heldjobs.py) finds what no job owns."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import heldsweep
from draft_studio.api import Api
from draft_studio.capture.pending import GRANTED
from draft_studio.heldapi import HeldApi
from tests.held_rig import CutLauncher, Rig
from tests.imgdata import png
from tests.test_capture_service import FakeLauncher
from tests.test_held import held, hold
from tests.test_held_retry import login_page


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    found = Rig(tmp_path)
    yield found
    found.close()


def record_path(rig: Rig, did: str, hid: str) -> Path:
    return rig.data / "drafts" / did / "held" / f"{hid}.json"


def edit(rig: Rig, did: str, hid: str, **slot: Any) -> None:
    path = record_path(rig, did, hid)
    rec = json.loads(path.read_text())
    rec["shots"]["shot-1"].update(slot)
    path.write_text(json.dumps(rec))


def sweep(app: Any) -> None:
    for thread in app.httpd.resume_held():
        thread.join(10)


def test_a_granted_request_no_job_owns_is_asked_again_not_resent(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    app.httpd.captures.pending.claim(rid)  # claimed by a run that was cut, before jobs existed
    assert app.httpd.captures.pending.record(rid)["status"] == GRANTED

    launcher = CutLauncher()
    fresh = rig.restart(app, launcher)
    sweep(fresh)
    (again,) = held(fresh, did)["held"]
    assert [r["id"] for r in again["requests"]] != [rid] and len(again["requests"]) == 1
    assert held(fresh, did)["released"] == []  # nothing was sent: kuhy decides on the new card
    assert [j["body"]["hold"] for j in rig.jobs("retry")] == [h["id"]]


def test_a_busy_slot_is_retried_at_boot(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    edit(rig, did, h["id"], state="busy", rid=None)  # a resume found the profile busy
    fresh = rig.restart(app, CutLauncher())
    assert held(fresh, did)["held"][0]["busy"] is True
    sweep(fresh)
    (again,) = held(fresh, did)["held"]
    assert again["busy"] is False and len(again["requests"]) == 1


def test_a_hold_whose_captures_all_resolved_is_released_at_boot(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    name = app.store.add_asset(did, "shot-1.png", png())
    edit(rig, did, h["id"], state="done", rid=None, md=f"![Zapisz](assets/{name})")
    fresh = rig.restart(app, CutLauncher())
    sweep(fresh)  # before anyone polls: the listing would release it too
    assert json.loads(record_path(rig, did, h["id"]).read_text())["status"] == "released"
    listing = held(fresh, did)
    assert listing["held"] == [] and f"](assets/{name})" in listing["released"][0]["replacement"]


def test_a_hold_waiting_on_kuhy_is_left_alone(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    launcher = CutLauncher()
    fresh = rig.restart(app, launcher)
    sweep(fresh)
    assert launcher.runs == 0 and [x["id"] for x in held(fresh, did)["held"]] == [h["id"]]
    assert rig.jobs("retry") == []


class Gate(CutLauncher):
    """A browser whose run waits until the test lets it go (a replay in flight)."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.go = threading.Event(), threading.Event()

    def open(self) -> Any:
        self.entered.set()
        assert self.go.wait(10)
        return super().open()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_hold_a_replayed_job_is_running_is_not_swept_twice(rig: Rig) -> None:
    app = rig.boot(CutLauncher(cut_at=2))
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    with pytest.raises(Exception, match=r"."):
        app.call("POST", f"/api/captures/{rid}/grant", {})
    app.httpd.shutdown()
    app.httpd.server_close()
    gate = Gate()
    fresh = rig.boot(gate)
    replays = fresh.httpd.resume_jobs()
    assert gate.entered.wait(10)
    sweep(fresh)  # the record is still waiting on the granted request, and a job is on it
    assert gate.runs == 0 and rig.jobs("retry") == []
    gate.go.set()
    for thread in replays:
        thread.join(10)
    assert gate.runs == 1 and len(held(fresh, did)["released"]) == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_resume_that_fails_ends_the_hold_as_failed_and_visible(rig: Rig) -> None:
    app = rig.boot(CutLauncher(cut_at=2))
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    with pytest.raises(Exception, match=r"."):
        app.call("POST", f"/api/captures/{rid}/grant", {})

    class Broken(CutLauncher):
        def open(self) -> Any:
            raise RuntimeError("chromium exploded")

    fresh = rig.restart(app, Broken())
    (view,) = held(fresh, did)["held"]
    assert view["failed"] == [{"key": "shot-1", "reason": "failed"}] and view["requests"] == []
    assert held(fresh, did)["released"] == []
    status, busy = fresh.call("GET", "/api/busy")
    assert status == 200 and [j["op"] for j in busy["failed"]] == ["grant"]
    with contextlib.suppress(Exception):
        fresh.call("POST", f"/api/drafts/{did}/held", {"collect": [h["id"]]})
    assert held(fresh, did)["held"] == []


def test_abandoning_what_is_gone_or_done_changes_nothing(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    held_api = HeldApi(Api(app.httpd.deps, app.httpd.captures).shots())
    held_api.abandon(rid="f" * 16)  # no hold waits on it
    held_api.abandon(draft_id=did, hid=h["id"])  # a hold waiting on kuhy's card: nothing it retries
    assert len(held(app, did)["held"][0]["requests"]) == 1
    edit(rig, did, h["id"], state="login", origin="https://panel.example", rid=None)
    held_api.abandon(draft_id=did, hid=h["id"])
    assert held(app, did)["held"][0]["failed"] == [{"key": "shot-1", "reason": "failed"}]
    held_api.abandon(draft_id=did, hid=h["id"])  # failed already: still one hold
    path = record_path(rig, did, h["id"])
    path.write_text(json.dumps({**json.loads(path.read_text()), "status": "released"}))
    held_api.abandon(draft_id=did, hid=h["id"])  # released: left as it is
    assert json.loads(path.read_text())["status"] == "released"


def test_a_request_pruned_while_a_hold_waits_is_not_stuck(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    (rig.data / "captures" / f"{h['requests'][0]['id']}.json").unlink()
    fresh = rig.restart(app, CutLauncher())
    sweep(fresh)
    assert held(fresh, did)["held"][0]["failed"] == [{"key": "shot-1", "reason": "expired"}]


def test_a_sweep_that_crashes_on_one_record_goes_on(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    app = rig.boot(CutLauncher())
    hold(app)
    fresh = rig.restart(app, CutLauncher())
    monkeypatch.setattr(heldsweep, "_unstick", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    sweep(fresh)  # logged, not raised


def test_a_retry_job_that_fails_on_replay_ends_the_hold_as_failed(rig: Rig) -> None:
    class Broken(CutLauncher):
        def open(self) -> Any:
            raise RuntimeError("chromium exploded")

    launcher = FakeLauncher(login_page)
    app = rig.boot(launcher)
    did, h = hold(app)
    jobs = app.httpd.deps.jobs
    assert jobs is not None
    with pytest.raises(SystemExit):
        jobs.run("retry", did, {"hold": h["id"], "window": ""}, lambda: (_ for _ in ()).throw(SystemExit))
    fresh = rig.restart(app, Broken())
    assert held(fresh, did)["held"][0]["failed"] == [{"key": "shot-1", "reason": "failed"}]
    assert rig.jobs("retry")[0]["status"] == "failed"
