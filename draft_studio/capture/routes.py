"""URL matching for the capture API (see draft_studio/capture/__init__.py for the shapes)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from draft_studio.capture.service import NOT_FOUND, CaptureService, Json

# Matched undecoded, like server.py: %2f never becomes a path separator.
_CAPTURE = re.compile(r"^/api/drafts/([^/]+)/capture$")
_REQUEST = re.compile(r"^/api/captures/([^/]+)/(grant|deny|shot)$")
PENDING = "/api/captures/pending"


def matches(path: str) -> bool:
    """True for every path this module answers (even with the wrong method)."""
    return path == PENDING or bool(_CAPTURE.match(path) or _REQUEST.match(path))


def decision(method: str, path: str) -> str | None:
    """The request id when the call grants or denies a permission request (both re-run the capture)."""
    request = _REQUEST.match(path)
    if method == "POST" and request and request.group(2) in ("grant", "deny"):
        return request.group(1)
    return None


def handle(
    service: CaptureService, method: str, path: str, body: Callable[[], Any]
) -> tuple[int, Json | bytes]:
    """(status, JSON body or PNG bytes); `body()` reads the request JSON only when needed."""
    if path == PENDING and method == "GET":
        return service.list_pending()
    capture = _CAPTURE.match(path)
    if capture and method == "POST":
        return service.capture(capture.group(1), body())
    request = _REQUEST.match(path)
    if request:
        rid, action = request.groups()
        if action == "shot" and method == "GET":
            return service.shot(rid)
        if action == "grant" and method == "POST":
            return service.grant(rid, body())
        if action == "deny" and method == "POST":
            return service.deny(rid)
    return NOT_FOUND
