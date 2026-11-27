"""File responses: the built pages, locale catalogs and draft images (upload and serve)."""

from __future__ import annotations

import json
import mimetypes
import re
from email.message import Message
from io import BufferedIOBase
from urllib.parse import parse_qs, urlsplit

from draft_studio.api import ApiError, Deps
from draft_studio.apiutil import Json
from draft_studio.store import MAX_ASSET_BYTES, AssetError, Store

_LOCALE = re.compile(r"^/locales/([A-Za-z-]{2,16})\.json$")
# Stored names carry a content hash, so a name never changes meaning: cache it for good.
ASSET_HEADERS = {
    "Cache-Control": "private, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def static_file(deps: Deps, path: str) -> tuple[bytes, str] | None:
    """Bytes and Content-Type of a built page or locale file; None when there is none."""
    if path == "/version":
        return json.dumps({"build": deps.build}).encode(), "application/json; charset=utf-8"
    locale = _LOCALE.match(path)
    if locale:
        file = deps.locales / f"{locale.group(1)}.json"
    else:
        rel = path.lstrip("/")
        file = deps.dist / rel
        if path.endswith("/"):
            file = file / "index.html"
    root = deps.locales if locale else deps.dist
    try:
        file.resolve().relative_to(root.resolve())
    except ValueError:
        file = root / "__missing__"
    if not file.is_file():
        return None
    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    data = file.read_bytes()
    if file.suffix == ".html" or file.name == "sw.js":
        # The service worker carries the build id too: a new build is a new worker (web/public/sw.js).
        data = data.replace(b"__BUILD__", deps.build.encode())
    if file.suffix == ".html":
        ctype = "text/html; charset=utf-8"
    return data, ctype


def upload(store: Store, draft_id: str, headers: Message, rfile: BufferedIOBase, raw_path: str) -> Json:
    """Check type and declared size before reading the body; the store sniffs the bytes."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        raise ApiError(415, "bad-image", close=True)
    declared = headers.get("Content-Length") or ""
    if not declared.isdigit():
        raise ApiError(411, "no-length", close=True)
    if int(declared) > MAX_ASSET_BYTES:
        raise ApiError(413, "too-large", close=True)
    data = rfile.read(int(declared))
    raw_name = parse_qs(urlsplit(raw_path).query).get("name", [""])[0]
    try:
        stored = store.add_asset(draft_id, raw_name, data)
    except AssetError as exc:
        raise ApiError(400, exc.code) from exc
    return {"name": stored, "md": f"![](assets/{stored})"}
