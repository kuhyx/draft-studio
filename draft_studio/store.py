"""On-disk drafts: markdown autosave, append-only history, trash.

Layout under the data dir (never inside the repo):
    drafts/<id>/draft.md       current text (plain markdown)
    drafts/<id>/meta.json      kind, language, mail fields, intake, asks, authorship
    drafts/<id>/history.jsonl  one JSON line per version or event, never pruned
    trash/<id>-<stamp>/        deleted drafts, moved here instead of removed
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

KINDS = ("mail", "text")
AUTHORS = ("you", "claude")
_ID_RE = re.compile(r"^[a-z0-9]{6,32}$")

Json = dict[str, Any]


class DraftNotFound(KeyError):
    """The id does not name a live draft."""


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


class Store:
    """Thread-safe draft store rooted at one data dir."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        (root / "drafts").mkdir(parents=True, exist_ok=True)
        (root / "trash").mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)

    # -- paths -----------------------------------------------------------
    def _dir(self, draft_id: str) -> Path:
        path = self.root / "drafts" / draft_id
        if not _ID_RE.match(draft_id) or not (path / "meta.json").is_file():
            raise DraftNotFound(draft_id)
        return path

    def _meta(self, draft_id: str) -> Json:
        meta: Json = json.loads((self._dir(draft_id) / "meta.json").read_text(encoding="utf-8"))
        return meta

    def _put_meta(self, draft_id: str, meta: Json) -> None:
        meta["updated"] = _now()
        _write_atomic(self._dir(draft_id) / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))

    def _append(self, path: Path, entry: Json) -> None:
        with (path / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- drafts ----------------------------------------------------------
    def create(self, kind: str) -> Json:
        """Make an empty draft of kind mail or text."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        with self._lock:
            draft_id = secrets.token_hex(6)
            path = self.root / "drafts" / draft_id
            path.mkdir()
            meta: Json = {
                "id": draft_id,
                "kind": kind,
                "lang": "",
                "to": "",
                "subject": "",
                "toAuthor": "you",
                "subjectAuthor": "claude",
                "gmailDraftId": "",
                "brief": "",
                "intake": [],
                "phase": "brief",
                "asks": [],
                "claude": [],
                "version": 0,
                "created": _now(),
            }
            (path / "draft.md").write_text("", encoding="utf-8")
            (path / "meta.json").write_text("{}", encoding="utf-8")
            self._put_meta(draft_id, meta)
            self._append(path, {"v": 0, "ts": _now(), "type": "create", "text": "", "claude": []})
            return meta

    def list_drafts(self) -> list[Json]:
        """Live drafts, newest first; unreadable ones are skipped."""
        out: list[Json] = []
        for path in (self.root / "drafts").iterdir():
            try:
                meta = self._meta(path.name)
                text = (path / "draft.md").read_text(encoding="utf-8")
            except (DraftNotFound, ValueError):
                continue
            out.append({**meta, "preview": text[:120]})
        out.sort(key=lambda m: m["updated"], reverse=True)
        return out

    def get(self, draft_id: str) -> Json:
        """Meta, current text and Claude-authored ranges."""
        with self._lock:
            path = self._dir(draft_id)
            meta = self._meta(draft_id)
            text = (path / "draft.md").read_text(encoding="utf-8")
            return {"meta": meta, "text": text, "claude": meta["claude"]}

    def save(self, draft_id: str, patch: Json, kind: str = "edit") -> Json:
        """Autosave: apply text/authorship/fields; append a version when anything changed."""
        with self._lock:
            path = self._dir(draft_id)
            meta = self._meta(draft_id)
            text = (path / "draft.md").read_text(encoding="utf-8")
            changed = False
            if "text" in patch:
                if not isinstance(patch["text"], str):
                    raise ValueError("text must be a string")
                changed |= patch["text"] != text
                text = patch["text"]
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
            for key in ("intake",):
                if key in patch:
                    changed |= patch[key] != meta[key]
                    meta[key] = patch[key]
            if not changed:
                return {"meta": meta, "text": text, "claude": meta["claude"]}
            meta["version"] += 1
            _write_atomic(path / "draft.md", text)
            self._put_meta(draft_id, meta)
            self._append(
                path,
                {
                    "v": meta["version"],
                    "ts": _now(),
                    "type": kind,
                    "text": text,
                    "claude": meta["claude"],
                    "to": meta["to"],
                    "subject": meta["subject"],
                    "toAuthor": meta["toAuthor"],
                    "subjectAuthor": meta["subjectAuthor"],
                },
            )
            return {"meta": meta, "text": text, "claude": meta["claude"]}

    def update_meta(self, draft_id: str, **fields: Any) -> Json:
        """Set server-owned meta (Gmail draft id) without a text version."""
        with self._lock:
            meta = self._meta(draft_id)
            meta.update(fields)
            self._put_meta(draft_id, meta)
            return meta

    def history(self, draft_id: str) -> list[Json]:
        """Every version and event, oldest first."""
        path = self._dir(draft_id)
        lines = (path / "history.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def restore(self, draft_id: str, version: int) -> Json:
        """Bring back version N as a new version (so the restore itself can be undone)."""
        with self._lock:
            match = [h for h in self.history(draft_id) if h.get("v") == version and "text" in h]
            if not match:
                raise KeyError(version)
            old = match[-1]
            patch = {"text": old["text"], "claude": old["claude"]}
            for key in ("to", "subject", "toAuthor", "subjectAuthor"):
                if key in old:
                    patch[key] = old[key]
            state = self.save(draft_id, patch, kind="restore")
            entries = self.history(draft_id)
            # Mark the source version on the restore entry (rewrite last line).
            last = entries[-1]
            if last.get("type") == "restore":
                last["from"] = version
                lines = [json.dumps(e, ensure_ascii=False) for e in entries]
                _write_atomic(self._dir(draft_id) / "history.jsonl", "\n".join(lines) + "\n")
            return state

    def add_ask(self, draft_id: str, ask: Json) -> Json:
        """Record an "ask" answer: in meta (side panel) and history. Text unchanged."""
        with self._lock:
            meta = self._meta(draft_id)
            entry = {**ask, "ts": _now()}
            meta["asks"].append(entry)
            self._put_meta(draft_id, meta)
            self._append(self._dir(draft_id), {"type": "ask", **entry})
            return entry

    def log(self, draft_id: str, entry: Json) -> None:
        """Append a non-version event (intake round, Claude call, Gmail upsert)."""
        with self._lock:
            self._append(self._dir(draft_id), {"ts": _now(), **entry})

    def delete(self, draft_id: str) -> None:
        """Move the draft folder to trash; nothing is hard-deleted."""
        with self._lock:
            path = self._dir(draft_id)
            stamp = time.strftime("%Y%m%dT%H%M%S")
            shutil.move(str(path), str(self.root / "trash" / f"{draft_id}-{stamp}"))
