"""The JSON form of a Claude call: what the server writes and the detached runner reads."""

from __future__ import annotations

import dataclasses
from typing import Any

from draft_studio.claude import Context

Json = dict[str, Any]


def ctx_to_json(ctx: Context) -> Json:
    """The context as plain JSON (tuples become lists)."""
    return dataclasses.asdict(ctx)


def ctx_from_json(raw: Json) -> Context:
    """The context back, with the tuple fields restored."""
    sel = raw.get("sel")
    return Context(**{**raw, "sel": tuple(sel) if sel else None, "assets": tuple(raw.get("assets", ()))})
