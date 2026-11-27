"""Screenshots draft studio's own Claude asks for, taken before kuhy sees the answer.

kuhy (2026-11-27): "the draft logic itself should take those screenshots";
"fake images are worse than no image at all". A first draft or a proposal
arrives with `captures` (prompts.CAPTURE) and one marker line per key. Each
capture runs through the capture service first:

- 200: the marker becomes `![alt](assets/<asset>)`, a real stored file;
- 422 no-target / ambiguous-target: Claude is asked again with the error and
  its `seen` labels, at most MAX_FIXES times, then the line is dropped;
- 409 needs-permission / login-required / profile-busy: the whole answer is
  HELD (held.py) until each capture resolves (heldapi.py: grant, deny, retry);
- anything else, or a URL not written in kuhy's text: the line is dropped.

Every drop is recorded (`dropped`, and the draft's history); links.py
`strip_invented` stays the last gate on the released text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from draft_studio.capture.service import CaptureService
from draft_studio.claude import Claude, ClaudeError, Context
from draft_studio.held import WAITING, HeldStore
from draft_studio.links import LinkError, strip_invented
from draft_studio.shotreq import (
    ShotError,
    allowed_url,
    image_md,
    markers,
    parse_shot,
    parse_shots,
    substitute,
)
from draft_studio.store import Store

Json = dict[str, Any]
LOG = logging.getLogger("draft_studio")
MAX_FIXES = 2
# A capture of Claude's queues behind a running one rather than fail as profile-busy.
QUEUE_S = 120.0
FIXABLE = ("no-target", "ambiguous-target")


class Held(dict[str, Any]):
    """An answer waiting for its screenshots: server.py answers it with 202."""


def stored(store: Store, draft_id: str) -> tuple[str, ...]:
    """Names of the draft's stored images: the only ones Claude may link."""
    return tuple(item["name"] for item in store.list_assets(draft_id))


def drop_slot(slot: Json, reason: str) -> None:
    slot.update(state="dropped", reason=reason, rid=None, origin=None)


class Shots:
    """Runs one answer's captures; shared code for the fresh path and the held one."""

    def __init__(
        self, store: Store, claude: Claude, captures: CaptureService, context: Callable[[Json], Context]
    ) -> None:
        self.store, self.claude, self.captures, self.context = store, claude, captures, context
        self.held = HeldStore(store)

    # -- one capture ---------------------------------------------------------
    def settle(self, rec: Json, key: str, status: int, body: Json) -> bool:
        """Record one capture result in its slot; True when a corrected capture should run."""
        slot = rec["shots"][key]
        code = body.get("error", "")
        if status == 200:
            slot.update(state="done", md=image_md(slot["shot"]["alt"], body["asset"]), rid=None, origin=None)
        elif code == "needs-permission":
            slot.update(state="pending", rid=body["request"]["id"])
        elif code == "login-required":
            slot.update(state="login", origin=body["origin"], rid=None)
        elif code == "profile-busy":
            slot.update(state="busy", rid=None)
        elif code in FIXABLE and slot["fixes"] < MAX_FIXES:
            slot["fixes"] += 1
            return self._fix(rec, key, body)
        else:
            drop_slot(slot, code or "capture")
        return False

    def _fix(self, rec: Json, key: str, error: Json) -> bool:
        slot = rec["shots"][key]
        try:
            raw = self.claude.fix_capture(self.context(rec), slot["shot"], error)
            slot["shot"] = parse_shot(raw, key).to_json()
        except (ClaudeError, ShotError) as exc:
            LOG.warning("capture %s: no fix from claude: %s", key, exc)
            drop_slot(slot, str(error.get("error")))
            return False
        return True

    def run(self, rec: Json, key: str) -> None:
        """Capture one slot, with Claude's corrections, until it settles."""
        slot = rec["shots"][key]
        while True:
            shot = parse_shot(slot["shot"], key)
            if not allowed_url(shot.url, rec["sources"]):
                LOG.warning("capture %s: url not in kuhy's text: %s", key, shot.url)
                drop_slot(slot, "url")
                return
            if not self.settle(rec, key, *self.captures.capture(rec["draftId"], shot.spec(), wait=QUEUE_S)):
                return

    # -- one answer ------------------------------------------------------------
    def answer(self, rec: Json, captures: list[Any]) -> Json:
        """Run the answer's captures; the released result, or a Held view (saved for later)."""
        text, stripped = self._strip(rec, rec["answer"]["text"])
        rec["answer"]["text"], rec["stripped"] = text, stripped
        shots, bad = parse_shots(captures)
        keys = markers(text)
        rec["shots"] = {
            k: {
                "shot": shots[k].to_json(),
                "state": "new",
                "rid": None,
                "origin": None,
                "md": None,
                "reason": None,
                "fixes": 0,
            }
            for k in keys
            if k in shots
        }
        rec["dropped"] = [
            {"key": k, "reason": "bad-spec" if k in bad else "no-request"} for k in keys if k not in shots
        ]
        for key in rec["shots"]:
            self.run(rec, key)
        if self.waiting(rec):
            return Held({"held": self.held_view(self.held.create(rec))})
        return self.release(rec)

    def _strip(self, rec: Json, text: str) -> tuple[str, list[str]]:
        try:
            out, cut = strip_invented(text, stored(self.store, rec["draftId"]), rec["kuhy"])
        except LinkError as exc:
            raise ClaudeError(f"dropped: {exc}") from exc
        if cut:
            LOG.warning("stripped invented image links: %s", cut)
        return out, cut

    @staticmethod
    def waiting(rec: Json) -> bool:
        """Some capture still waits on kuhy (permission, login) or on the profile."""
        return any(slot["state"] in WAITING for slot in rec["shots"].values())

    def held_view(self, rec: Json) -> Json:
        """What the client sees of a held answer (documented in capture/__init__.py)."""
        slots = rec["shots"].values()
        rids = {s["rid"] for s in slots if s["state"] == "pending"}
        ask = rec["ask"]
        return {
            "id": rec["id"],
            "kind": rec["kind"],
            "from": ask.get("from"),
            "to": ask.get("to"),
            "requests": [r for r in self.captures.pending.list_pending() if r["id"] in rids],
            "login": sorted({s["origin"] for s in slots if s["state"] == "login"}),
            "busy": any(s["state"] == "busy" for s in slots),
            "tries": rec.get("tries", 0),
        }

    def release(self, rec: Json) -> Json:
        """The final text: images in, failed lines out, last gate run; logged in the draft's history."""
        done = {k: s["md"] for k, s in rec["shots"].items() if s["state"] == "done"}
        text, cut = substitute(rec["answer"]["text"], done)
        reasons = {k: s["reason"] for k, s in rec["shots"].items()}
        listed = {d["key"] for d in rec["dropped"]}
        rec["dropped"] += [{"key": k, "reason": reasons.get(k) or "capture"} for k in cut if k not in listed]
        try:
            text, _ = strip_invented(text, stored(self.store, rec["draftId"]), rec["kuhy"])
        except LinkError:  # never expected: the swapped-in links are clean; fall back to no images
            text, _ = substitute(rec["answer"]["text"], {})
        if rec["dropped"]:
            LOG.warning("dropped screenshots: %s", rec["dropped"])
        result = self._deliver(rec, text)
        if "id" in rec:
            rec.update(status="released", result=result)
            self.held.put(rec)
        return result

    def _deliver(self, rec: Json, text: str) -> Json:
        did, ask, extra = rec["draftId"], rec["ask"], {"stripped": rec["stripped"], "dropped": rec["dropped"]}
        if rec["kind"] == "proposal":
            out = {"replacement": text, "from": ask["from"], "to": ask["to"], **extra}
            entry = {
                "type": "proposal",
                "instruction": ask["instruction"],
                "previous": ask["previous"],
                **out,
            }
            self.store.log(did, entry)
            return out
        if rec["stripped"]:
            self.store.log(did, {"type": "stripped", "stripped": rec["stripped"]})
        if rec["dropped"]:
            self.store.log(did, {"type": "dropped", "dropped": rec["dropped"]})
        meta = self.store.get(did)["meta"]
        patch: Json = {"text": text, "phase": "editing", "claude": [[0, len(text)]] if text else []}
        subject = rec["answer"]["subject"]
        if meta["kind"] == "mail" and meta["subjectAuthor"] != "you" and subject:
            patch.update(subject=subject, subjectAuthor="claude")
        return {"questions": [], "state": self.store.save(did, patch, kind="draft"), **extra}
