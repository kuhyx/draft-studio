"""A grant or deny is a job: a restart in the middle of its capture re-runs it, once."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture.pending import GRANTED, PENDING, NotPending, PendingStore
from draft_studio.capture.spec import parse
from draft_studio.held import HeldStore
from tests.held_rig import CutLauncher, Rig
from tests.test_held import held, hold

SPEC = {"url": "https://panel.example/", "name": "dns-1", "steps": [{"click": "Zapłać"}], "highlight": "Done"}


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    found = Rig(tmp_path)
    yield found
    found.close()


def status_of(rig: Rig, rid: str) -> str:
    return str(json.loads((rig.data / "captures" / f"{rid}.json").read_text())["status"])


@pytest.mark.parametrize("op", ["grant", "deny"])
def test_a_decision_is_a_journaled_job(rig: Rig, op: str) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    assert app.call("POST", f"/api/captures/{rid}/{op}", {})[0] == 200
    (job,) = rig.jobs(op)
    assert (job["status"], job["draftId"], job["body"]["rid"]) == ("done", did, rid)
    assert len(held(app, did)["released"]) == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the cut kills a thread
@pytest.mark.parametrize("op", ["grant", "deny"])
def test_a_capture_cut_by_a_restart_is_run_again_by_the_next_server(rig: Rig, op: str) -> None:
    app = rig.boot(CutLauncher(cut_at=2))  # run 1 holds the answer, run 2 is the decision's: cut
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    with pytest.raises(Exception, match=r"."):
        app.call("POST", f"/api/captures/{rid}/{op}", {})
    (job,) = rig.jobs(op)
    assert job["status"] == "running" and status_of(rig, rid) != PENDING  # claimed, nothing landed
    assert held(app, did)["released"] == []

    fresh = rig.restart(app, CutLauncher())
    (out,) = held(fresh, did)["released"]
    assert "](assets/" in out["replacement"] and held(fresh, did)["held"] == []
    assert rig.jobs(op)[0]["status"] == "done"
    assert len(fresh.store.list_assets(did)) == 1


def test_a_new_server_keeps_the_request_a_hold_waits_on(rig: Rig) -> None:
    """Booting prunes spent requests, but a granted one with a waiting slot is the resume's record."""
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    assert (rig.data / "captures" / f"{rid}.json").is_file()
    assert HeldStore(app.store).waiting_requests() == {rid}
    assert len(held(app, did)["held"]) == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_job_cut_before_its_claim_claims_on_replay(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    jobs = app.httpd.deps.jobs
    assert jobs is not None
    with pytest.raises(SystemExit):
        jobs.run("grant", did, {"rid": rid, "body": {}}, lambda: (_ for _ in ()).throw(SystemExit))
    assert status_of(rig, rid) == PENDING
    fresh = rig.restart(app, CutLauncher())
    assert len(held(fresh, did)["released"]) == 1


def test_a_replayed_decision_whose_answer_landed_runs_nothing(rig: Rig) -> None:
    app = rig.boot(CutLauncher())
    did, h = hold(app)
    rid = h["requests"][0]["id"]
    assert app.call("POST", f"/api/captures/{rid}/grant", {})[0] == 200
    path = next((rig.data / "jobs").glob("*/job.json"))
    for p in (rig.data / "jobs").glob("*/job.json"):
        if json.loads(p.read_text())["op"] == "grant":
            path = p
    path.write_text(json.dumps({**json.loads(path.read_text()), "status": "running"}))  # killed before "done"
    launcher = CutLauncher()
    fresh = rig.restart(app, launcher)
    assert launcher.runs == 0, "the granted write must not be sent twice"
    assert len(held(fresh, did)["released"]) == 1 and rig.jobs("grant")[0]["status"] == "done"


def test_resume_claims_what_the_cut_run_already_claimed(tmp_path: Path) -> None:
    store = PendingStore(tmp_path)
    store.dir.mkdir()
    spec = parse(SPEC)
    rec: Any = store.create(
        "d1", spec, step=0, reason="deny-list", detail="", blocked=[], grants=[], shot=b"x"
    )
    first = store.claim(rec["id"])
    with pytest.raises(NotPending):
        store.claim(rec["id"])
    again = store.claim(rec["id"], resume=True)
    assert again == first and store.record(rec["id"])["status"] == GRANTED
    with pytest.raises(NotPending):
        store.deny(rec["id"], resume=True)  # a grant is not resumed as a deny
