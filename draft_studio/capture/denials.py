"""Denials kept per draft until kuhy clears them (kuhy, 2026-11-27).

Every walk showed a card for the chat widget's `OPTIONS https://robofolks.cyberfolks.pl/request`,
denied by hand each time. So a (method, URL) pair kuhy denies (a deny, or the rest of a partial
grant) is stored in <data dir>/drafts/<id>/denials.json and failed again, with no card and the
shot still taken, in every later run of that draft, its OPTIONS twin (partial.py) with it:

    {"denials": [{"request": ["POST", "https://..."], "denied": <epoch s>}, ...]}  oldest first

- it only ever blocks more: the runner fails a denied pair like any refused one and never allows
  it, so policy.py and guard.py are unchanged; a denied click is not kept (a fix run may move its
  step index), and the one-run `sent` denials of a replay (spent.py) are not either;
- a kept pair is never written into a new permission record: only kuhy's own decisions ride there,
  so a clear takes effect at the next run;
- the twin is stored as its own row, unless the same decision granted it (a partial grant that
  picked only the preflight keeps its write denied, not the preflight kuhy allowed);
- a grant of a pair lifts it and its twin; only reachable once it cards again, after a clear;
- clearing a write clears its preflight with it.

Every change bumps the change feed (feed.py), like the draft's other meta writes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from draft_studio.atomic import read_json, write_json
from draft_studio.capture.partial import with_twins
from draft_studio.capture.runner import Request
from draft_studio.capture.spec import Denial
from draft_studio.store import Store

Json = dict[str, Any]
FILE = "denials.json"
_LOCK = threading.Lock()  # never the capture lock: the listing must answer while a capture runs


class BadClear(ValueError):
    """`clear` is neither "all" nor a non-empty list of [method, url] string pairs."""


def parse_clear(body: object) -> list[Request] | None:
    """The pairs to clear (methods upper-cased), or None for "all"; BadClear otherwise."""
    raw = body.get("clear") if isinstance(body, dict) else None
    if raw == "all":
        return None
    if not isinstance(raw, list) or not raw:
        raise BadClear("clear")
    if not all(isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in raw):
        raise BadClear("clear")
    return [(m.upper(), u) for m, u in raw]


class Denials:
    """The kept denials of each draft of one data dir."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def _path(self, draft_id: str) -> Path:
        return self.store._dir(draft_id) / FILE  # DraftNotFound for a bad or missing id

    def _read(self, draft_id: str) -> dict[Request, float]:
        data = read_json(self._path(draft_id)) or {}
        return {
            (str(d["request"][0]), str(d["request"][1])): float(d["denied"]) for d in data.get("denials", [])
        }

    def _change(self, draft_id: str, old: dict[Request, float], new: dict[Request, float]) -> None:
        if new != old:
            write_json(self._path(draft_id), _listing(new))
            self.store.feed.bump(draft_id)

    def add(self, draft_id: str, requests: Iterable[Request], granted: Iterable[Request] = ()) -> None:
        """kuhy denied these: keep them and their twins failed, except what the same decision `granted`.

        A pair already kept keeps its first time.
        """
        allowed = set(granted)
        with _LOCK:
            old = self._read(draft_id)
            now, new = round(time.time(), 3), dict(old)
            for request in with_twins(list(requests)):
                if request not in allowed:
                    new.setdefault(request, now)
            self._change(draft_id, old, new)

    def lift(self, draft_id: str, requests: Iterable[Request]) -> None:
        """kuhy granted these: forget their denials and their twins'."""
        self._drop(draft_id, set(with_twins(list(requests))))

    def clear(self, draft_id: str, body: object) -> Json:
        """POST /api/drafts/<id>/denials {"clear": "all" | [[method, url], ...]}: the listing after it."""
        picked = parse_clear(body)
        self._drop(draft_id, None if picked is None else set(with_twins(picked)))
        return self.listing(draft_id)

    def _drop(self, draft_id: str, gone: set[Request] | None) -> None:
        with _LOCK:
            old = self._read(draft_id)
            self._change(
                draft_id, old, {} if gone is None else {r: t for r, t in old.items() if r not in gone}
            )

    def listing(self, draft_id: str) -> Json:
        """GET /api/drafts/<id>/denials."""
        with _LOCK:
            return _listing(self._read(draft_id))

    def denial(self, draft_id: str) -> list[Denial]:
        """The draft's kept denials as one Denial for every step (none: [])."""
        with _LOCK:
            kept = tuple(self._read(draft_id))
        return [Denial(0, click=False, requests=kept)] if kept else []


def _listing(kept: dict[Request, float]) -> Json:
    rows = sorted(kept.items(), key=lambda kv: kv[1])  # stable: same time keeps insertion order
    return {"denials": [{"request": list(r), "denied": t} for r, t in rows]}
