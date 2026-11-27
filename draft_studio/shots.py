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
`strip_invented` stays the last gate on the released text (shotout.py). A proposal
whose every screenshot was dropped is HELD as failed (shotout.py `failed`), never
released as a text-only replacement; some dropped: it lands marked partial.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from draft_studio.capture.service import CaptureService
from draft_studio.claude import Claude, ClaudeError, Context
from draft_studio.held import HELD, WAITING, HeldStore
from draft_studio.shotaim import ShotError
from draft_studio.shotout import failed, held_view, release, strip
from draft_studio.shotreq import allowed_url, image_md, markers, parse_shot
from draft_studio.store import Store
from draft_studio.walks import align, locate, plan, spec_of, unit

Json = dict[str, Any]
LOG = logging.getLogger("draft_studio")
MAX_FIXES = 2
# A capture of Claude's queues behind a running one rather than fail as profile-busy.
QUEUE_S = 120.0
FIXABLE = ("no-target", "ambiguous-target")


class Held(dict[str, Any]):
    """An answer waiting for its screenshots: server.py answers it with 202."""


NEW_SLOT: Json = {"state": "new", "rid": None, "origin": None, "md": None, "reason": None, "fixes": 0}


def drop_slot(slot: Json, reason: str) -> None:
    """The slot gets no image; a shot already taken (a failed walk's earlier shots) stays taken."""
    if slot["state"] != "done":
        slot.update(state="dropped", reason=reason, rid=None, origin=None)


def _wait_or_drop(slot: Json, code: str, body: Json) -> None:
    """A run that did not finish: the slot waits on kuhy or the profile, or goes."""
    if code == "needs-permission":
        slot.update(state="pending", rid=body["request"]["id"])
    elif code == "login-required":
        slot.update(state="login", origin=body["origin"], rid=None)
    elif code == "profile-busy":
        slot.update(state="busy", rid=None)
    else:
        drop_slot(slot, code or "capture")


class Shots:
    """Runs one answer's captures; shared code for the fresh path and the held one.

    The unit of a run is walks.py `unit`: a standalone capture, or every capture of its walk.
    """

    def __init__(
        self, store: Store, claude: Claude, captures: CaptureService, context: Callable[[Json], Context]
    ) -> None:
        self.store, self.claude, self.captures, self.context = store, claude, captures, context
        self.held = HeldStore(store)

    # -- one capture run -------------------------------------------------------
    def settle(self, rec: Json, key: str, status: int, body: Json) -> bool:
        """Record one run's result in every slot of `key`'s unit; True when a corrected run should go.

        A failed walk's reply holds the shots taken before its failing step (`shots`): they are kept
        (kuhy, 2026-11-27: the login-page shot was failed with the click after it), and so is a shot an
        earlier run of the walk took; only the slots still without an image move on the error.
        """
        keys = unit(rec, key)
        code = body.get("error", "")
        single = status == 200 and "shots" not in body
        assets = {key: body["asset"]} if single else {s["name"]: s["asset"] for s in body.get("shots", [])}
        for k in keys:
            if k in assets:
                slot = rec["shots"][k]
                # rid stays on a failed walk's kept shot: the fix run `follows` it (capture/reuse.py)
                rid = None if status == 200 else slot["rid"]
                slot.update(state="done", md=image_md(slot["shot"]["alt"], assets[k]), rid=rid, origin=None)
        slots = [rec["shots"][k] for k in keys if rec["shots"][k]["state"] != "done"]
        if status == 200 or not slots:
            for slot in slots:  # a walk that ended early (a denied click): no image, never a stand-in
                drop_slot(slot, "not-shot")
        elif code in FIXABLE and rec["shots"][keys[0]]["fixes"] < MAX_FIXES:
            for k in keys:
                rec["shots"][k]["fixes"] += 1
            return self._fix(rec, keys, body)
        else:
            for slot in slots:
                _wait_or_drop(slot, code, body)
        return False

    def _fix(self, rec: Json, keys: list[str], error: Json) -> bool:
        """Ask Claude to correct the one capture the error belongs to (walks.py `locate`)."""
        shots = [parse_shot(rec["shots"][k]["shot"], k) for k in keys]
        key, local = locate(shots, error)
        slot = rec["shots"][key]
        try:
            raw = self.claude.fix_capture(self.context(rec), slot["shot"], local)
            slot["shot"] = replace(parse_shot(raw, key), walk=slot["shot"].get("walk") or "").to_json()
            # The fixed capture wins over its neighbours in the walk (walks.py `align`).
            shots = [parse_shot(rec["shots"][k]["shot"], k) for k in keys]
            for before, after in zip(shots, align(shots, prefer=key), strict=True):
                if after != before:
                    rec["shots"][after.key]["shot"] = after.to_json()
        except (ClaudeError, ShotError) as exc:
            LOG.warning("capture %s: no fix from claude: %s", key, exc)
            for k in keys:
                drop_slot(rec["shots"][k], str(error.get("error")))
            return False
        return True

    def run(self, rec: Json, key: str) -> None:
        """Capture `key`'s unit in one run, with Claude's corrections, until it settles."""
        while True:
            keys = unit(rec, key)
            try:
                shots = [parse_shot(rec["shots"][k]["shot"], k) for k in keys]
                spec = spec_of(shots)
            except ShotError as exc:
                return self._drop(rec, keys, exc.code)
            if bad := [u for s in shots for u in s.urls() if not allowed_url(u, rec["sources"])]:
                LOG.warning("capture %s: url not in kuhy's text: %s", key, bad)
                return self._drop(rec, keys, "url")
            # A fix run names a request of its walk: it reuses the walk's grants (capture/reuse.py).
            follows = next((r for r in (rec["shots"][k]["rid"] for k in keys) if r), None)
            reply = self.captures.capture(rec["draftId"], spec, wait=QUEUE_S, follows=follows)
            if not self.settle(rec, key, *reply):
                return None

    @staticmethod
    def _drop(rec: Json, keys: list[str], reason: str) -> None:
        for k in keys:
            drop_slot(rec["shots"][k], reason)

    # -- one answer ------------------------------------------------------------
    def answer(self, rec: Json, captures: list[Any]) -> Json:
        """Run the answer's captures; the released result, or a Held view (saved for later).

        A replayed job (`rec["job"]`, jobs.py) whose answer is already held gets that one, not another.
        """
        known = self.held.by_job(rec["draftId"], rec["job"]) if rec.get("job") else None
        if known and known["status"] == HELD:
            return Held({"held": self.held_view(known)})
        text, stripped = strip(self.store, rec, rec["answer"]["text"])
        rec["answer"]["text"], rec["stripped"] = text, stripped
        shots, rec["walks"], rec["dropped"] = plan(captures, markers(text))
        rec["shots"] = {k: {**NEW_SLOT, "shot": shot.to_json()} for k, shot in shots.items()}
        for key, slot in rec["shots"].items():
            if slot["state"] == "new":  # a walk's first capture already ran the rest
                self.run(rec, key)
        if self.holds(rec):
            return Held({"held": self.held_view(self.held.create(rec))})
        return self.release(rec)

    @staticmethod
    def waiting(rec: Json) -> bool:
        """Some capture still waits on kuhy (permission, login) or on the profile."""
        return any(slot["state"] in WAITING for slot in rec["shots"].values())

    def holds(self, rec: Json) -> bool:
        """The answer is kept back: it waits on a capture, or none of its screenshots could be taken."""
        return self.waiting(rec) or failed(rec)

    def held_view(self, rec: Json) -> Json:
        """What the client sees of a held answer (shotout.py)."""
        return held_view(self.captures.pending, rec, self.store.get(rec["draftId"])["text"])

    def release(self, rec: Json) -> Json:
        """The released answer (shotout.py)."""
        return release(self.store, self.held, rec)
