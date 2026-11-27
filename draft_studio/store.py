"""On-disk drafts: markdown autosave, append-only history, trash.

Layout under the data dir (never inside the repo):
    drafts/<id>/draft.md       current text (plain markdown)
    drafts/<id>/meta.json      kind, language, mail fields, intake, asks, authorship
    drafts/<id>/history.jsonl  one JSON line per version or event, never pruned
    drafts/<id>/assets/<name>  images the draft references as ![alt](assets/<name>)
    trash/<id>-<stamp>/        deleted drafts (assets included), moved here instead of removed
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from typing import Any

from draft_studio import proposals
from draft_studio.assets import (
    ASSET_NAME_RE,
    IMAGE_TYPES,
    MAX_ASSET_BYTES,
    AssetError,
    AssetNotFound,
    AssetStore,
    sniff_image,
)
from draft_studio.storebase import (
    APPROVED,
    DraftNotFound,
    Json,
    StaleBase,
    _check_ranges,
    _now,
    _state,
    _write_atomic,
    default_data_dir,
    file_sha256,
)

# Re-exported: callers import the whole store API from here.
__all__ = [
    "APPROVED",
    "ASSET_NAME_RE",
    "IMAGE_TYPES",
    "MAX_ASSET_BYTES",
    "AssetError",
    "AssetNotFound",
    "DraftNotFound",
    "Json",
    "StaleBase",
    "Store",
    "_now",
    "default_data_dir",
    "file_sha256",
    "sniff_image",
]

KINDS = ("mail", "text")
# Phases: brief -> intake -> editing <-> approved. Only draft_studio.approval sets
# "approved"; a content change (CONTENT_KEYS) moves it back to "editing".
EDITING = "editing"
CONTENT_KEYS = ("text", "to", "subject")
AUTHORS = ("you", "claude")


class Store(AssetStore):
    """Thread-safe draft store rooted at one data dir (paths: storebase.py, images: assets.py)."""

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
            return _state(path, meta, text)

    def save(
        self, draft_id: str, patch: Json, kind: str = "edit", base: int | None = None, remap: bool = True
    ) -> Json:
        """Autosave: apply text/authorship/fields; append a version when anything changed.

        `base`: the version the editor's text grew from. An older one raises StaleBase
        (the tab rebases and resaves); None keeps last-write-wins for scripts.
        `remap`: carry the pending proposals through the text change (False: the caller already did).
        """
        with self._lock:
            path = self._dir(draft_id)
            meta = self._meta(draft_id)
            text = (path / "draft.md").read_text(encoding="utf-8")
            if base is not None and base != meta["version"]:
                raise StaleBase(_state(path, meta, text))
            if patch.get("phase") == APPROVED:
                raise ValueError("approve through draft_studio.approval, never a save")
            before = {"text": text, "to": meta["to"], "subject": meta["subject"]}
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
            if any(patch.get(key, before[key]) != before[key] for key in CONTENT_KEYS):
                meta["phase"] = EDITING if meta["phase"] == APPROVED else meta["phase"]
            if meta["phase"] != APPROVED:
                meta.pop("approvedSha256", None)
            if not changed:
                return _state(path, meta, text)
            meta["version"] += 1
            if remap and text != before["text"]:
                proposals.write(path, proposals.remap(proposals.read(path), before["text"], text))
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
            return _state(path, meta, text)

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
            known: list[Json] = [a for a in meta["asks"] if ask.get("job") and a.get("job") == ask["job"]]
            if known:  # a replayed job (jobs.py) lands its answer once
                return known[0]
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
        """Move the draft folder (assets included) to trash; nothing is hard-deleted."""
        with self._lock:
            path = self._dir(draft_id)
            stamp = time.strftime("%Y%m%dT%H%M%S")
            shutil.move(str(path), str(self.root / "trash" / f"{draft_id}-{stamp}"))
            self.feed.bump(draft_id)
