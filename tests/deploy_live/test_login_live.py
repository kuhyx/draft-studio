"""A deploy while the login window is open: the window stays, the new server adopts it, the retry runs once.

The window is the real `python -m draft_studio.capture login` in its own unit, on a fake chromium
(instance.FAKE_CHROMIUM) that stays open 12 s; no real browser and no window on kuhy's screen.
Run by hand, one test per call: `python3 -m pytest -q -m deploy tests/deploy_live/test_login_live.py`
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from draft_studio.claude_fake import shot_request
from draft_studio.shotreq import parse_shot
from tests.deploy_live.instance import Instance

pytestmark = pytest.mark.deploy

URL = "http://127.0.0.1:9/login"
ORIGIN = "http://127.0.0.1:9"


def seed_hold(instance: Instance, draft_id: str) -> str:
    """A first draft waiting on a login, written straight into the instance's data dir."""
    hold = "a" * 16
    shot = parse_shot(shot_request(URL, "")[0], "shot-1").to_json()
    slot = {"shot": shot, "state": "login", "rid": None, "origin": ORIGIN, "md": None, "reason": None}
    rec: dict[str, Any] = {"id": hold, "draftId": draft_id, "kind": "draft", "status": "held"}
    rec |= {"created": time.time(), "ask": {}, "answer": {"text": "Hi\n\n[[[IMG:shot-1]]]\n", "subject": ""}}
    rec |= {"kuhy": URL, "sources": URL, "shots": {"shot-1": slot | {"fixes": 0}}, "walks": {}}
    rec |= {"stripped": [], "dropped": [], "result": None}
    folder = instance.data / "drafts" / draft_id / "held"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{hold}.json").write_text(json.dumps(rec))
    return hold


def test_the_login_window_survives_a_deploy_and_its_retry_runs_once(instance: Instance) -> None:
    did = instance.post("/api/drafts", {"kind": "text"})["id"]
    hold = seed_hold(instance, did)
    assert instance.get(f"/api/drafts/{did}/held")["held"][0]["login"] == [ORIGIN]
    login = f"/api/drafts/{did}/held/{hold}/login"
    assert instance.post(login, {"origin": ORIGIN}) == {"started": True}
    record = json.loads((instance.data / "login-window.json").read_text())
    assert record["hold"] == hold and instance.unit_active(record["unit"])

    done = instance.deploy("b2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert instance.unit_active(record["unit"]), "the window died with the server it was opened by"
    with pytest.raises(OSError, match="409"):  # the new server knows the window is still open
        instance.post(login, {"origin": ORIGIN})

    end = time.monotonic() + 40
    listing: dict[str, Any] = {}
    while time.monotonic() < end:
        listing = instance.get(f"/api/drafts/{did}/held")
        if listing["held"] == [] and listing["released"]:
            break
        time.sleep(0.3)
    assert listing["held"] == [] and len(listing["released"]) == 1, listing
    time.sleep(1.5)
    assert len(instance.get(f"/api/drafts/{did}/held")["released"]) == 1
    saved = json.loads((instance.data / "drafts" / did / "held" / f"{hold}.json").read_text())
    assert saved["tries"] == 1, "the retry must run exactly once"
    assert not (instance.data / "login-window.json").exists()
    assert not instance.unit_active(record["unit"])
