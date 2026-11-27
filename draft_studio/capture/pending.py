"""Permission requests: a refused step waits here until kuhy grants or denies it.

One JSON record plus one PNG (the screen with the refused target outlined) per
request, under <data dir>/captures/, never in the repo. A record is granted or
denied once. Its grant carries the grants the run that produced it already
had, so a capture needing two permissions asks twice and never loops; the
re-run does repeat the earlier granted steps (listed in `grants`).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from draft_studio.capture.spec import Grant, Spec, parse

Json = dict[str, Any]
_ID_RE = re.compile(r"^[a-f0-9]{16}$")
PENDING, GRANTED, DENIED = "pending", "granted", "denied"


class RequestNotFound(KeyError):
    """No permission request with that id."""


class NotPending(ValueError):
    """The request was already granted or denied."""


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
    ) -> Json:
        """Store a new pending request and return its public record."""
        rid = secrets.token_hex(8)
        allow = Grant(step=step, click=reason != "request", requests=tuple(blocked))
        rec = {
            "id": rid,
            "draftId": draft_id,
            "spec": spec.to_json(),
            "step": step,
            "stepText": spec.steps[step].text,
            "reason": reason,
            "detail": detail,
            "allow": allow.to_json(),
            "grants": [g.to_json() for g in grants],
            "status": PENDING,
            "created": round(time.time(), 3),
            "shot": f"/api/captures/{rid}/shot",
        }
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / f"{rid}.png").write_bytes(shot)
            self._put(rec)
        return rec

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

    def claim(self, rid: str) -> tuple[str, Spec, list[Grant]]:
        """Mark granted (once) and return what the re-run needs: draft id, spec, all grants."""
        rec = self._decide(rid, GRANTED)
        grants = [Grant.from_json(g) for g in rec["grants"]] + [Grant.from_json(rec["allow"])]
        return rec["draftId"], parse(rec["spec"]), grants

    def deny(self, rid: str) -> Json:
        """Cancel the capture this request belongs to."""
        return self._decide(rid, DENIED)

    def shot(self, rid: str) -> bytes:
        """The screenshot kuhy decides on."""
        return self._path(rid, "png").read_bytes()
