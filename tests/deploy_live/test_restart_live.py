"""A deploy in the middle of a long ask: the restart happens at once and nothing in flight is lost.

Real systemd --user units on a throwaway instance (own port, own data dir under the scratch dir):
a server, a long fake ask (its Claude call runs in a unit of its own), then `scripts/deploy.sh`
pointed at that instance. Needs systemd --user; marker `deploy`, run it by hand:

    python3 -m pytest -q -m deploy tests/deploy_live
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from tests.deploy_live.instance import Instance

pytestmark = pytest.mark.deploy

ASK = {"text": "one two three four", "from": 0, "to": 3, "question": "[slow] why"}


def test_deploy_restarts_at_once_and_the_running_ask_still_lands_once(instance: Instance) -> None:
    made = instance.post("/api/drafts", {"kind": "text"})
    did = made["id"]
    instance.post(f"/api/drafts/{did}", {"text": ASK["text"]}, method="PUT")
    answers: list[Any] = []

    def ask() -> None:  # the browser's request: it dies with the old server, as a tab's would
        try:
            answers.append(instance.post(f"/api/drafts/{did}/ask", ASK, timeout=30))
        except OSError as exc:
            answers.append(exc)

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.get("/api/busy")["busy"]:
        assert time.monotonic() < deadline, "the ask never became busy"
        time.sleep(0.05)
    started = time.monotonic()
    done = instance.deploy("b2")
    took = time.monotonic() - started
    assert done.returncode == 0, done.stdout + done.stderr
    assert took < 8, f"the deploy waited for the ask ({took:.1f}s): it must restart at once"
    assert instance.get("/version")["build"] == "b2"
    thread.join(30)
    assert isinstance(answers[0], OSError), "the old server's connection should have been cut"
    end = time.monotonic() + 30
    asks: list[Any] = []
    while not asks and time.monotonic() < end:
        asks = instance.get(f"/api/drafts/{did}")["meta"]["asks"]
        time.sleep(0.2)
    assert [a["answer"] for a in asks] == ["[en] About 'one': [slow] why"]
    time.sleep(1.0)
    assert len(instance.get(f"/api/drafts/{did}")["meta"]["asks"]) == 1
    assert instance.get("/api/busy") == {"busy": False, "jobs": [], "failed": []}
