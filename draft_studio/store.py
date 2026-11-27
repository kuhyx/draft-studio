"""On-disk drafts: markdown autosave, append-only history, trash.

Layout under the data dir (never inside the repo):
    drafts/<id>/draft.md       current text (plain markdown)
    drafts/<id>/meta.json      kind, language, mail fields, intake, asks, authorship
    drafts/<id>/history.jsonl  one JSON line per version or event, never pruned
    drafts/<id>/assets/<name>  images the draft references as ![alt](assets/<name>)
    trash/<id>-<stamp>/        deleted drafts (assets included), moved here instead of removed
"""

from __future__ import annotations

import hashlib
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
# Phases: brief -> intake -> editing <-> approved. Only draft_studio.approval sets
# "approved"; a content change (CONTENT_KEYS) moves it back to "editing".
EDITING = "editing"
APPROVED = "approved"
CONTENT_KEYS = ("text", "to", "subject")
AUTHORS = ("you", "claude")
_ID_RE = re.compile(r"^[a-z0-9]{6,32}$")

MAX_ASSET_BYTES = 10 * 1024 * 1024
# Sniffed type -> served Content-Type. SVG is left out on purpose: it can carry script.
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
# Extensions a client may keep for each sniffed type (the first is the default).
_EXTS = {"png": ("png",), "jpg": ("jpg", "jpeg"), "gif": ("gif",), "webp": ("webp",)}
# Stored names: safe charset, no leading dot, an image extension. Every lookup checks it.
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.(png|jpg|jpeg|gif|webp)$")
_HASH_LEN = 12
_STEM_MAX = 60

Json = dict[str, Any]


class DraftNotFound(KeyError):
    """The id does not name a live draft."""


class AssetNotFound(KeyError):
    """The name does not name a stored image of this draft."""


class AssetError(ValueError):
    """An upload the store refuses; `code` is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sniff_image(data: bytes) -> str | None:
    """Image type from the magic bytes (png, jpg, gif, webp), else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _asset_name(raw: str, kind: str, digest: str) -> str:
    """`<safe stem>-<hash>.<ext>`: basename only, [A-Za-z0-9_-]; ext kept when it matches the bytes."""
    base = re.split(r"[/\\]", raw)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    ext = ext.lower() if ext.lower() in _EXTS[kind] else _EXTS[kind][0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")[:_STEM_MAX].strip("-_") or "image"
    return f"{stem}-{digest[:_HASH_LEN]}.{ext}"


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
    """The draft JSON every read and save returns; `approved` is re-checked against the file."""
    approved = meta["phase"] == APPROVED and meta.get("approvedSha256") == file_sha256(path / "draft.md")
    return {"meta": meta, "text": text, "claude": meta["claude"], "approved": approved}


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
            return _state(path, meta, text)

    def save(self, draft_id: str, patch: Json, kind: str = "edit") -> Json:
        """Autosave: apply text/authorship/fields; append a version when anything changed."""
        with self._lock:
            path = self._dir(draft_id)
            meta = self._meta(draft_id)
            text = (path / "draft.md").read_text(encoding="utf-8")
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
            entry = {**ask, "ts": _now()}
            meta["asks"].append(entry)
            self._put_meta(draft_id, meta)
            self._append(self._dir(draft_id), {"type": "ask", **entry})
            return entry

    def log(self, draft_id: str, entry: Json) -> None:
        """Append a non-version event (intake round, Claude call, Gmail upsert)."""
        with self._lock:
            self._append(self._dir(draft_id), {"ts": _now(), **entry})

    # -- image assets -----------------------------------------------------
    def add_asset(self, draft_id: str, name: str, data: bytes) -> str:
        """Store image bytes under the draft; same bytes return the already stored name.

        Not a text version: the markdown reference is plain text, so history covers it.
        """
        if len(data) > MAX_ASSET_BYTES:
            raise AssetError("too-large")
        kind = sniff_image(data)
        if kind is None:
            raise AssetError("bad-image")
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            folder = self._dir(draft_id) / "assets"
            folder.mkdir(exist_ok=True)
            for old in folder.glob(f"*-{digest[:_HASH_LEN]}.*"):
                if ASSET_NAME_RE.match(old.name) and old.read_bytes() == data:
                    return old.name
            stored = _asset_name(name, kind, digest)
            if (folder / stored).exists():  # short-hash clash with other bytes: never overwrite
                stored = f"{digest}.{_EXTS[kind][0]}"
            tmp = folder / f".{stored}.tmp"
            tmp.write_bytes(data)
            tmp.replace(folder / stored)
            self._append(folder.parent, {"ts": _now(), "type": "asset", "name": stored})
            return stored

    def asset(self, draft_id: str, name: str) -> tuple[bytes, str]:
        """Bytes and Content-Type (sniffed again, never trusted from the name) of one image."""
        folder = self._dir(draft_id) / "assets"
        if not ASSET_NAME_RE.match(name):
            raise AssetNotFound(name)
        path = folder / name
        try:
            path.resolve().relative_to(folder.resolve())
            data = path.read_bytes()
        except (ValueError, OSError) as exc:
            raise AssetNotFound(name) from exc
        kind = sniff_image(data)
        if kind is None:
            raise AssetNotFound(name)
        return data, IMAGE_TYPES[kind]

    def list_assets(self, draft_id: str) -> list[Json]:
        """Stored images of a draft (name, size, type), by name."""
        folder = self._dir(draft_id) / "assets"
        out: list[Json] = []
        for path in sorted(folder.glob("*")) if folder.is_dir() else []:
            if not ASSET_NAME_RE.match(path.name) or not path.is_file():
                continue
            kind = sniff_image(path.read_bytes()[:16])
            if kind:
                out.append({"name": path.name, "size": path.stat().st_size, "type": IMAGE_TYPES[kind]})
        return out

    def delete(self, draft_id: str) -> None:
        """Move the draft folder (assets included) to trash; nothing is hard-deleted."""
        with self._lock:
            path = self._dir(draft_id)
            stamp = time.strftime("%Y%m%dT%H%M%S")
            shutil.move(str(path), str(self.root / "trash" / f"{draft_id}-{stamp}"))
