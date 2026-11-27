"""POST /api/drafts/<id>/held/<hid>/login: open the login window, then retry the hold by itself.

Shapes (also in draft_studio/capture/__init__.py, "Claude's own screenshots"):

POST /api/drafts/<id>/held/<hid>/login   body {"origin": "https://host"}
    202 {"started": true}              `python3 -m draft_studio.capture login <origin>` runs in a unit
                                       of its own (loginwin.py: a restart adopts it);
                                       when its window closes (by itself after the login, at
                                       a 10 min timeout, or by hand) the server re-runs the hold's
                                       login/busy captures itself (heldapi.py retry): the client
                                       only polls GET .../held
    409 {"error": "profile-busy"}      a login window is open, or another process holds the profile
    400 {"error": "bad-origin"}        origin is not in that hold's current `login` list (exact match)
    404 {"error": "not-found"}         no such draft or held id
    502 {"error": "login-failed"}      the login process could not be started

The origin that gets opened always comes from the held record, never from the client alone.
"""

from __future__ import annotations

import logging
from typing import Any

from draft_studio.detach import DetachError
from draft_studio.held import HELD, HeldNotFound
from draft_studio.heldapi import HeldApi
from draft_studio.loginwin import LoginWindows
from draft_studio.shots import Shots

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
Reply = tuple[int, Json]
BUSY: Reply = (409, {"error": "profile-busy"})


def handle(windows: LoginWindows, shots: Shots, draft_id: str, hid: str, body: Any) -> Reply:
    """POST .../held/<hid>/login (DraftNotFound for a bad draft id is the server's 404)."""
    held = HeldApi(shots)
    with held.held.lock:
        try:
            rec = held.held.get(draft_id, hid)
        except HeldNotFound:
            return 404, {"error": "not-found"}
        listed = shots.held_view(rec)["login"] if rec["status"] == HELD else []
    origin = body.get("origin") if isinstance(body, dict) else None
    if not isinstance(origin, str) or origin not in listed:
        return 400, {"error": "bad-origin"}
    try:
        started = windows.start(draft_id, hid, origin)
    except (OSError, DetachError):
        LOG.exception("login window did not start")
        return 502, {"error": "login-failed"}
    return (202, {"started": True}) if started else BUSY
