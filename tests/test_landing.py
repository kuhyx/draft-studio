"""A replayed job lands exactly once: brief, intake and propose, also after a crash right after the save."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import jobapi, proposalapi
from draft_studio import server as srv
from draft_studio.held import HeldStore
from draft_studio.jobs import Jobs
from draft_studio.store import Store
from tests.conftest import App
from tests.test_held import SAVE, hold, saving
from tests.test_server_jobs import ASK, Spawner, boot, restart

Booted = tuple[App, Jobs, Path, Spawner]


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Booted]:
    spawn = Spawner()
    app, jobs = boot(tmp_path, spawn)
    yield app, jobs, tmp_path, spawn
    app.httpd.shutdown()
    app.httpd.server_close()


def new_draft(app: App, text: str = "one two three four") -> str:
    did = str(app.store.create("text")["id"])
    app.store.save(did, {"text": text})
    return did


def kinds(app: App, did: str, kind: str) -> list[dict[str, Any]]:
    return [h for h in app.store.history(did) if h["type"] == kind]


def run_and_die(served: Booted, mp: pytest.MonkeyPatch, op: str, did: str, body: dict[str, Any]) -> App:
    """Run the job until its answer is written, die right there, boot a new server and replay."""
    app, jobs, tmp_path, spawn = served
    api = srv.Api(app.httpd.deps, app.httpd.captures)
    owner, name = (proposalapi, "land") if op == "propose" else (Store, "save")
    real = getattr(owner, name)

    def write_then_die(*a: Any, **kw: Any) -> Any:
        out = real(*a, **kw)
        if kw.get("job") or (op == "propose" and len(a) > 4 and a[4]):
            raise SystemExit
        return out

    with mp.context() as inner:
        inner.setattr(owner, name, write_then_die)
        with pytest.raises(SystemExit):
            jobs.run(op, did, body, lambda: getattr(api, op)(did, body))
    app2, _ = restart(tmp_path, spawn, app)
    served[0].httpd, served[0].base = app2.httpd, app2.base  # the fixture closes the new server
    for thread in app2.httpd.resume_jobs():
        thread.join(10)
    return app2


def test_the_store_lands_a_job_once(store: Store) -> None:
    did = str(store.create("text")["id"])
    assert not store.landed(did, "j1")
    store.save(did, {"text": "a"}, job="j1")
    assert store.landed(did, "j1") and not store.landed(did, "j2")
    version = store.get(did)["meta"]["version"]
    again = store.save(did, {"text": "b"}, job="j1")
    assert again["text"] == "a" and again["meta"]["version"] == version
    store.save(did, {"text": "a"}, job="j2")  # no change in the text: the landing is still written
    assert store.landed(did, "j2")


def test_the_store_keeps_the_newest_landings(store: Store) -> None:
    did = str(store.create("text")["id"])
    for n in range(60):
        store.save(did, {"text": str(n)}, job=f"j{n}")
    assert not store.landed(did, "j0") and store.landed(did, "j59")


def test_a_held_answer_is_found_by_its_job(app: App) -> None:
    saving(app)
    did, first = hold(app)
    store = HeldStore(app.store)
    assert store.by_job(did, "nojob") is None
    store.put({**store.get(did, first["id"]), "job": "j9"})
    found = store.by_job(did, "j9")
    assert found is not None and found["id"] == first["id"]


def test_a_brief_that_asked_its_questions_does_not_ask_again(
    served: Booted, monkeypatch: pytest.MonkeyPatch
) -> None:
    did = new_draft(served[0])
    app = run_and_die(served, monkeypatch, "brief", did, {"brief": "Mail to the owner"})
    assert len(app.store.get(did)["meta"]["intake"]) == 2
    assert len(kinds(app, did, "intake")) == 1 and len(kinds(app, did, "brief")) == 1


def test_a_brief_that_wrote_the_first_draft_does_not_write_it_again(
    served: Booted, monkeypatch: pytest.MonkeyPatch
) -> None:
    did = new_draft(served[0])
    app = run_and_die(served, monkeypatch, "brief", did, {"brief": "Mail to the owner [noq]"})
    assert len(kinds(app, did, "draft")) == 1 and app.store.get(did)["meta"]["phase"] == "editing"


def test_a_replayed_intake_leaves_the_next_rounds_questions_open(
    served: Booted, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, *_ = served
    did = new_draft(app)
    assert app.call("POST", f"/api/drafts/{did}/brief", {"brief": "Mail [2rounds]"})[0] == 200
    app2 = run_and_die(served, monkeypatch, "intake", did, {"answers": ["alpha", "beta"]})
    items = app2.store.get(did)["meta"]["intake"]
    assert [(i["a"], i["round"]) for i in items] == [("alpha", 0), ("beta", 0), (None, 1)]
    assert len(kinds(app2, did, "intake")) == 3  # the brief's round, the answers, the next round


def test_a_replayed_propose_lands_one_proposal(served: Booted, monkeypatch: pytest.MonkeyPatch) -> None:
    did = new_draft(served[0])
    body = {**ASK, "instruction": "shout"}
    del body["question"]
    app = run_and_die(served, monkeypatch, "propose", did, body)
    (pending,) = app.call("GET", f"/api/drafts/{did}")[1]["proposals"]
    # The history line is written after the landing: a death in between loses it, never doubles it.
    assert pending["replacement"] == "ONE" and len(kinds(app, did, "proposal")) <= 1


def test_a_proposal_kuhy_rejected_does_not_come_back(served: Booted, monkeypatch: pytest.MonkeyPatch) -> None:
    app, jobs, tmp_path, spawn = served
    did = new_draft(app)
    body = {"text": "one two three four", "from": 0, "to": 3, "instruction": "shout"}
    api = srv.Api(app.httpd.deps, app.httpd.captures)
    with pytest.raises(SystemExit):
        jobs.run("propose", did, body, lambda: (api.propose(did, body), (_ for _ in ()).throw(SystemExit)))
    pid = app.call("GET", f"/api/drafts/{did}")[1]["proposals"][0]["id"]
    assert app.call("POST", f"/api/drafts/{did}/proposals/{pid}/reject", {})[0] == 200
    app2, _ = restart(tmp_path, spawn, app)
    served[0].httpd, served[0].base = app2.httpd, app2.base
    for thread in app2.httpd.resume_jobs():
        thread.join(10)
    assert app2.call("GET", f"/api/drafts/{did}")[1]["proposals"] == []


def test_a_replayed_propose_finds_its_held_answer(app: App) -> None:
    saving(app)
    jobs = Jobs(app.store.root, clock=time.time)
    app.httpd.deps.jobs = jobs
    api = srv.Api(app.httpd.deps, app.httpd.captures)
    did = new_draft(app, SAVE)
    body = {"text": SAVE, "from": 0, "to": len(SAVE), "instruction": "[shot]"}
    with pytest.raises(SystemExit):
        jobs.run("propose", did, body, lambda: (api.propose(did, body), (_ for _ in ()).throw(SystemExit)))
    again = Jobs(app.store.root, clock=time.time)
    app.httpd.deps.jobs = again
    for thread in again.resume(lambda job: jobapi.replay(api, job)):
        thread.join(10)
    assert len(HeldStore(app.store).list(did)) == 1
