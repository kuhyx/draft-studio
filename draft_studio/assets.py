"""Image assets of a draft: drafts/<id>/assets/<name>, referenced as ![alt](assets/<name>)."""

from __future__ import annotations

import hashlib
import re

from draft_studio.storebase import Json, StoreBase, _now

MAX_ASSET_BYTES = 10 * 1024 * 1024
# Sniffed type -> served Content-Type. SVG is left out on purpose: it can carry script.
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
# Extensions a client may keep for each sniffed type (the first is the default).
_EXTS = {"png": ("png",), "jpg": ("jpg", "jpeg"), "gif": ("gif",), "webp": ("webp",)}
# Stored names: safe charset, no leading dot, an image extension. Every lookup checks it.
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.(png|jpg|jpeg|gif|webp)$")
_HASH_LEN = 12
_STEM_MAX = 60


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


class AssetStore(StoreBase):
    """The image half of the store (store.Store adds the drafts)."""

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
