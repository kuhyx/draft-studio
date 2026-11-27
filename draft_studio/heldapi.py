"""Held answers after the fact: grants, denials, retries, expiry, and the client's collection.

Shapes (also in draft_studio/capture/__init__.py, "Claude's own screenshots"):

GET  /api/drafts/<id>/held      200 {"held": [<held>], "released": [<released>]}
POST /api/drafts/<id>/held      body {"retry": "<held id>"}: re-run its login/busy captures
                                200 {"held": [...], "released": [...]} (the new listing)
                                body {"collect": ["<held id>", ...]}: forget released ones
                                200 {"collected": n}
POST /api/drafts/<id>/held/<hid>/login   body {"origin": "https://host"}: see loginapi.py;
                                202 {"started": true} (login window open, retry runs when it
                                closes), 409 profile-busy, 400 bad-origin, 404 not-found
"""

from __future__ import annotations

import time
from typing import Any

from draft_studio.capture.pending import RequestNotFound
from draft_studio.capture.private import MAX_AGE_S
from draft_studio.held import HELD, RELEASED, WAITING, HeldNotFound
from draft_studio.proposals import cut
from draft_studio.shots import Shots, drop_slot
from draft_studio.walks import unit

Json = dict[str, Any]


class BadHeld(ValueError):
    """The POST body names no known action."""


def _released_view(rec: Json) -> Json:
    view: Json = {"id": rec["id"], "kind": rec["kind"], **rec["result"]}
    if rec["kind"] == "proposal":
        ask = rec["ask"]
        view.update(span=cut(ask["text"], ask["from"], ask["to"]), previous=ask["previous"])
    return view


class HeldApi:
    """Every change to a held answer runs under the held store's lock."""

    def __init__(self, shots: Shots) -> None:
        self.shots = shots
        self.held = shots.held

    def _after(self, rec: Json) -> None:
        if self.shots.waiting(rec):
            self.held.put(rec)
        else:
            self.shots.release(rec)

    def decided(self, rid: str, status: int, body: Json) -> None:
        """Hook after POST /api/captures/<rid>/grant|deny answered (status, body).

        A deny re-runs the capture like a grant does (the refusal stays blocked, the shot is taken),
        so both move the slot the same way; neither drops the image line.
        """
        with self.held.lock:
            found = self.held.by_request(rid)
            if found is None:
                return
            rec, key = found
            slot = rec["shots"][key]
            code = body.get("error")
            if code in ("not-pending", "not-blocked", "bad-allow") or (
                code == "profile-busy" and self._status(rid) == "pending"
            ):
                return  # the grant was not spent: the slot keeps waiting on the same request
            if status == 404:
                drop_slot(slot, "expired")
            elif self.shots.settle(rec, key, status, body):
                self.shots.run(rec, key)
            self._after(rec)

    def _status(self, rid: str) -> str:
        """The capture request's status; "expired" once it was pruned."""
        try:
            return str(self.shots.captures.pending.record(rid)["status"])
        except RequestNotFound:
            return "expired"

    def _expire(self, rec: Json, now: float) -> None:
        """Drop the slots whose request was pruned, and everything too old."""
        old = now - rec["created"] > MAX_AGE_S
        for slot in rec["shots"].values():
            if slot["state"] not in WAITING:
                continue
            if old or (slot["state"] == "pending" and self._status(slot["rid"]) == "expired"):
                drop_slot(slot, "expired")
        self._after(rec)

    def listing(self, draft_id: str) -> Json:
        """GET /api/drafts/<id>/held."""
        with self.held.lock:
            now = time.time()
            for rec in self.held.list(draft_id):
                if rec["status"] == HELD:
                    self._expire(rec, now)
            recs = self.held.list(draft_id)
        return {
            "held": [self.shots.held_view(r) for r in recs if r["status"] == HELD],
            "released": [_released_view(r) for r in recs if r["status"] == RELEASED],
        }

    def post(self, draft_id: str, body: Json) -> Json:
        """POST /api/drafts/<id>/held: {"retry": id} or {"collect": [ids]}."""
        if isinstance(body.get("retry"), str):
            self.retry(draft_id, body["retry"])
            return self.listing(draft_id)
        ids = body.get("collect")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise BadHeld("body")
        return {"collected": self.collect(draft_id, ids)}

    def retry(self, draft_id: str, hid: str) -> None:
        """Re-run the captures that waited on a login or a busy profile (after kuhy logged in)."""
        with self.held.lock:
            rec = self.held.get(draft_id, hid)
            if rec["status"] != HELD:
                return
            # Counted even when the retry fails the same way (login window closed while logged
            # out): the permission card's login spinner waits for the hold to change.
            rec["tries"] = rec.get("tries", 0) + 1
            ran: set[str] = set()
            for key, slot in rec["shots"].items():
                if slot["state"] in ("login", "busy") and key not in ran:
                    ran.update(unit(rec, key))  # a walk runs once for all its slots
                    self.shots.run(rec, key)
            self._after(rec)

    def collect(self, draft_id: str, ids: list[str]) -> int:
        """Forget released answers the client has shown; held ones stay."""
        n = 0
        with self.held.lock:
            for hid in dict.fromkeys(ids):
                try:
                    rec = self.held.get(draft_id, hid)
                except HeldNotFound:
                    continue
                if rec["status"] == RELEASED:
                    self.held.remove(draft_id, hid)
                    n += 1
        return n
