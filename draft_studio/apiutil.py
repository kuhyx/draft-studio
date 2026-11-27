"""Shared bits of the route bodies: the error type and request field checks."""

from __future__ import annotations

from typing import Any

from draft_studio.claude import Context

Json = dict[str, Any]
CLOSE = {"Connection": "close"}


class ApiError(Exception):
    """An error with an HTTP status and a short machine code (shown as an icon)."""

    def __init__(self, status: int, code: str, close: bool = False, extra: Json | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.body: Json = {"error": code, **(extra or {})}
        # close: the request body was left unread, so the keep-alive connection must end.
        self.headers = CLOSE if close else {}


def _span(body: Json) -> tuple[str, int, int]:
    text, start, end = body.get("text"), body.get("from"), body.get("to")
    if not (isinstance(text, str) and isinstance(start, int) and isinstance(end, int)):
        raise ApiError(400, "bad-span")
    if not 0 <= start <= end <= len(text):
        raise ApiError(400, "bad-span")
    return text, start, end


def _context(state: Json, text: str | None = None, **kw: Any) -> Context:
    meta = state["meta"]
    return Context(
        kind=meta["kind"],
        lang=meta["lang"],
        brief=meta["brief"],
        intake=[item for item in meta["intake"] if item.get("a") is not None],
        text=state["text"] if text is None else text,
        subject=meta["subject"],
        **kw,
    )
