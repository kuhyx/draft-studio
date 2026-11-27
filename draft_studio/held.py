"""Held answers: a proposal or first draft waiting for its screenshots, stored next to its draft.

<data dir>/drafts/<draft id>/held/<16 hex>.json, one record per answer:

    {"id", "draftId", "kind": "proposal" | "draft", "status": "held" | "released",
     "created", "ask": {...what the answer was for...}, "answer": {"text", "subject"},
     "kuhy": <kuhy's own text: the strip_invented allowlist>, "sources": <URL allowlist>,
     "shots": {<key>: <slot>}, "stripped": [...], "dropped": [...], "result": {...} | null}
    <slot> = {"shot": <prompts.CAPTURE>, "state": "pending" | "login" | "busy" | "done" | "dropped",
              "rid": <capture request id> | null, "origin": <login origin> | null,
              "md": <image markdown> | null, "reason": <drop code> | null, "fixes": n}

Records are released in place (status "released", `result` filled) and
removed once the client collects them.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from draft_studio.store import Store

Json = dict[str, Any]
_ID_RE = re.compile(r"^[a-f0-9]{16}$")
HELD, RELEASED = "held", "released"
WAITING = ("pending", "login", "busy")


class HeldNotFound(KeyError):
    """No held answer with that id for that draft."""


class HeldStore:
    """Thread-safe records under each draft's held/ folder."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.lock = threading.RLock()

    def _dir(self, draft_id: str) -> Path:
        self.store.get(draft_id)  # DraftNotFound for a bad or missing id
        return self.store.root / "drafts" / draft_id / "held"

    def _path(self, draft_id: str, hid: str) -> Path:
        path = self._dir(draft_id) / f"{hid}.json"
        if not _ID_RE.match(hid) or not path.is_file():
            raise HeldNotFound(hid)
        return path

    def create(self, rec: Json) -> Json:
        """Store a new held record (id, created and status filled in)."""
        rec = {**rec, "id": secrets.token_hex(8), "status": HELD, "created": round(time.time(), 3)}
        rec.setdefault("result", None)
        self.put(rec)
        return rec

    def put(self, rec: Json) -> None:
        """Write a record atomically."""
        folder = self._dir(rec["draftId"])
        folder.mkdir(mode=0o700, exist_ok=True)
        tmp = folder / f".{rec['id']}.tmp"
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(folder / f"{rec['id']}.json")

    def get(self, draft_id: str, hid: str) -> Json:
        """One record, or HeldNotFound."""
        data: Json = json.loads(self._path(draft_id, hid).read_text(encoding="utf-8"))
        return data

    def list(self, draft_id: str) -> list[Json]:
        """The draft's records, oldest first."""
        folder = self._dir(draft_id)
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
        return sorted(recs, key=lambda r: r["created"])

    def remove(self, draft_id: str, hid: str) -> None:
        """Delete a collected record."""
        self._path(draft_id, hid).unlink()

    def all_held(self) -> Iterator[Json]:
        """Every record still held, in any draft (to find the one a capture request belongs to)."""
        for path in (self.store.root / "drafts").glob("*/held/*.json"):
            rec: Json = json.loads(path.read_text(encoding="utf-8"))
            if rec["status"] == HELD:
                yield rec

    def by_request(self, rid: str) -> tuple[Json, str] | None:
        """(record, shot key) of the held slot waiting on capture request `rid`."""
        for rec in self.all_held():
            for key, slot in rec["shots"].items():
                if slot["state"] == "pending" and slot["rid"] == rid:
                    return rec, key
        return None
