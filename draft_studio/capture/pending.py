"""Permission requests: a refused step waits here until kuhy grants or denies it.

One JSON record plus one PNG (the screen with the refused target outlined) per
request, under <data dir>/captures/, never in the repo. A record is granted or
denied once, and every allowance it holds is spent by exactly one re-run (one
exception: the fix runs of the same capture, for 10 minutes, reuse.py):

- grants for steps before the refused one were used on the run that stopped:
  they are dropped, so a re-run reaching that step again asks again;
- grants for the refused step itself merge into the new `allow` (the
  "Zapłać" case: first the click, then click plus its POST, one approval);
- grants for later steps never ran: they carry over in `grants`.

A denial is the opposite: it only blocks, so it is kept for the whole run of
the capture (`denials`, carried by every later record, grant or deny alike).
Denying a request refusal keeps its (method, url) pairs failed; denying a
refused click stops the capture at that step. Either way the capture goes on to
its screenshot, nothing is sent.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from draft_studio.capture.private import ensure_private
from draft_studio.capture.spec import Denial, Grant, Spec, parse

Json = dict[str, Any]
_ID_RE = re.compile(r"^[a-f0-9]{16}$")
PENDING, GRANTED, DENIED = "pending", "granted", "denied"


# Refusals whose click ran: kuhy denies what the click sent, not the click.
CLICK_RAN = ("request", "form-submit")


class Claim(NamedTuple):
    """What a re-run needs: the draft, the spec, every grant and every denial so far.

    `allow` is what kuhy just granted (None for a deny): reuse.py keeps it for the fix runs.
    """

    draft_id: str
    spec: Spec
    grants: list[Grant]
    denials: list[Denial]
    allow: Grant | None = None


class RequestNotFound(KeyError):
    """No permission request with that id."""


class NotPending(ValueError):
    """The request was already granted or denied."""


def _denials(rec: Json) -> list[Denial]:
    return [Denial.from_json(d) for d in rec.get("denials", [])]  # records from before denials had none


class PendingStore:
    """Records in <root>/captures/<id>.json, screenshots in <id>.png."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "captures"
        self._lock = threading.Lock()

    def _path(self, rid: str, ext: str = "json") -> Path:
        path = self.dir / f"{rid}.{ext}"
        if not _ID_RE.match(rid) or not path.is_file():
            raise RequestNotFound(rid)
        return path

    def _load(self, rid: str) -> Json:
        data: Json = json.loads(self._path(rid).read_text(encoding="utf-8"))
        return data

    def _put(self, rec: Json) -> None:
        tmp = self.dir / f".{rec['id']}.tmp"
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / f"{rec['id']}.json")

    def create(
        self,
        draft_id: str,
        spec: Spec,
        *,
        step: int,
        reason: str,
        detail: str,
        blocked: list[tuple[str, str]],
        grants: list[Grant],
        shot: bytes,
        denials: Sequence[Denial] = (),
    ) -> Json:
        """Store a new pending request and return its public record."""
        rid = secrets.token_hex(8)
        same = [g for g in grants if g.step == step]
        allow = Grant(
            step=step,
            click=reason != "request" or any(g.click for g in same),
            requests=tuple(r for g in same for r in g.requests) + tuple(blocked),
        )
        rec = {
            "id": rid,
            "draftId": draft_id,
            "spec": spec.to_json(),
            "step": step,
            "stepText": spec.steps[step].text,
            "reason": reason,
            "detail": detail,
            "allow": allow.to_json(),
            "grants": [g.to_json() for g in grants if g.step > step],
            "denials": [d.to_json() for d in denials],
            "blocked": [list(b) for b in blocked],
            "clicked": any(g.click for g in same),
            "status": PENDING,
            "created": round(time.time(), 3),
            "shot": f"/api/captures/{rid}/shot",
        }
        with self._lock:
            ensure_private(self.dir)
            (self.dir / f"{rid}.png").write_bytes(shot)
            self._put(rec)
        return rec

    def record(self, rid: str) -> Json:
        """One request in any status, or RequestNotFound (pruned or never made)."""
        return self._load(rid)

    def list_pending(self) -> list[Json]:
        """Open requests, oldest first."""
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in self.dir.glob("*.json")]
        return sorted((r for r in recs if r["status"] == PENDING), key=lambda r: r["created"])

    def _decide(self, rid: str, status: str) -> Json:
        with self._lock:
            rec = self._load(rid)
            if rec["status"] != PENDING:
                raise NotPending(rid)
            rec.update(status=status, decided=round(time.time(), 3))
            self._put(rec)
            return rec

    def claim(self, rid: str) -> Claim:
        """Mark granted (once) and return what the re-run needs."""
        rec = self._decide(rid, GRANTED)
        allow = Grant.from_json(rec["allow"])
        grants = [Grant.from_json(g) for g in rec["grants"]] + [allow]
        return Claim(rec["draftId"], parse(rec["spec"]), grants, _denials(rec), allow)

    def deny(self, rid: str) -> Claim:
        """Mark denied (once) and return the re-run that keeps this refusal blocked.

        Its grants are only the later steps' and a click kuhy had allowed before (the earlier
        allowances were spent), never this refusal's own `allow`. A click never denied stays refused.
        """
        rec = self._decide(rid, DENIED)
        grants = [Grant.from_json(g) for g in rec["grants"]]
        if rec.get("clicked"):
            grants.append(Grant(rec["step"], click=True))
        ran = rec["reason"] in CLICK_RAN
        blocked = tuple((m, u) for m, u in rec.get("blocked", rec["allow"]["requests"]))
        denied = Denial(rec["step"], click=not ran, requests=blocked)
        return Claim(rec["draftId"], parse(rec["spec"]), grants, [*_denials(rec), denied])

    def shot(self, rid: str) -> bytes:
        """The screenshot kuhy decides on."""
        return self._path(rid, "png").read_bytes()
