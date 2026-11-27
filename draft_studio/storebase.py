"""The data dir plumbing every store part shares: paths, meta file, history log, change feed."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from draft_studio import proposals
from draft_studio.feed import Feed

APPROVED = "approved"
_ID_RE = re.compile(r"^[a-z0-9]{6,32}$")

Json = dict[str, Any]
AUTHORS = ("you", "claude")
# Landings remembered per draft: a job replays only while it runs, so a short tail is plenty.
KEEP_LANDED = 50


class DraftNotFound(KeyError):
    """The id does not name a live draft."""


class StaleBase(Exception):
    """A save based on an older version than the stored one; `state` is the stored draft."""

    def __init__(self, state: Json) -> None:
        super().__init__("stale")
        self.state = state


def default_data_dir() -> Path:
    """Data dir: $DRAFT_STUDIO_DATA, else $XDG_DATA_HOME/draft-studio, else ~/.local/share."""
    explicit = os.environ.get("DRAFT_STUDIO_DATA")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path(os.environ["HOME"]) / ".local/share"
    return base / "draft-studio"


def _now() -> float:
    return round(time.time(), 3)


def _write_atomic(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def file_sha256(path: Path) -> str:
    """Hex sha256 of a file's bytes as they are on disk (what the outbound hook hashes)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(path: Path, meta: Json, text: str) -> Json:
    """The draft JSON every read and save returns; `approved` is re-checked against the file.

    `proposals`/`gone`: Claude's pending proposals and how the last ones ended (proposals.py).
    """
    approved = meta["phase"] == APPROVED and meta.get("approvedSha256") == file_sha256(path / "draft.md")
    pending = proposals.view(proposals.read(path))
    return {"meta": meta, "text": text, "claude": meta["claude"], "approved": approved, **pending}


def _check_ranges(value: object) -> list[list[int]]:
    """Authorship ranges are sorted [from, to] int pairs with from < to."""
    if not isinstance(value, list):
        raise ValueError("claude ranges must be a list")
    out: list[list[int]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(n, int) for n in item)
            or not 0 <= item[0] < item[1]
        ):
            raise ValueError(f"bad range {item!r}")
        out.append([item[0], item[1]])
    return out


def apply_fields(meta: Json, patch: Json) -> bool:
    """Copy the meta fields of a save patch into `meta` (validated); True when any value changed."""
    changed = False
    if "claude" in patch:
        ranges = _check_ranges(patch["claude"])
        changed |= ranges != meta["claude"]
        meta["claude"] = ranges
    for key in ("to", "subject", "lang", "brief", "phase"):
        if key in patch:
            changed |= patch[key] != meta[key]
            meta[key] = str(patch[key])
    for key in ("toAuthor", "subjectAuthor"):
        if key in patch:
            if patch[key] not in AUTHORS:
                raise ValueError(f"{key} must be one of {AUTHORS}")
            changed |= patch[key] != meta[key]
            meta[key] = patch[key]
    if "intake" in patch:
        changed |= patch["intake"] != meta["intake"]
        meta["intake"] = patch["intake"]
    return changed


def with_landing(landed: list[str], job: str) -> list[str]:
    """The landed-job list with `job` added (the newest KEEP_LANDED kept)."""
    return [*landed, job][-KEEP_LANDED:]


class StoreBase:
    """Thread-safe access to one data dir; every meta write tells the change feed."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        # Open editor tabs follow this (GET /api/events): one bump per write of a draft.
        self.feed = Feed()
        (root / "drafts").mkdir(parents=True, exist_ok=True)
        (root / "trash").mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)

    def _dir(self, draft_id: str) -> Path:
        path = self.root / "drafts" / draft_id
        if not _ID_RE.match(draft_id) or not (path / "meta.json").is_file():
            raise DraftNotFound(draft_id)
        return path

    def _meta(self, draft_id: str) -> Json:
        meta: Json = json.loads((self._dir(draft_id) / "meta.json").read_text(encoding="utf-8"))
        return meta

    def _put_meta(self, draft_id: str, meta: Json) -> None:
        """The one choke point of draft writes (saves, approval, asks, Gmail id): it bumps the feed."""
        meta["updated"] = _now()
        _write_atomic(self._dir(draft_id) / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
        self.feed.bump(draft_id)

    def landed(self, draft_id: str, job_id: str) -> bool:
        """True when the answer of job `job_id` already landed on this draft (jobs.py replays land once).

        The mark is written in the same atomic write as the landing: `save(job=...)` in meta.json,
        a proposal in proposals.json (proposalapi.land).
        """
        with self._lock:
            meta = self._meta(draft_id)
            marks = proposals.read(self._dir(draft_id)).get("landed", [])
            return job_id in meta.get("landed", []) or job_id in marks

    def _append(self, path: Path, entry: Json) -> None:
        with (path / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
