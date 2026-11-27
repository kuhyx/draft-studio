"""Landing, accepting and rejecting proposals on the server (shapes in proposals.py).

POST /api/drafts/<id>/proposals/<pid>/accept   body {"rev"?: n, "text"?: <what the span becomes>}
    applies the change to the draft text (a new version, Claude's tint on it) and answers the draft state;
    `text` accepts only some hunks (the proposal stays for the rest), without it the whole replacement.
POST /api/drafts/<id>/proposals/<pid>/reject   body {"rev"?: n, "replacement"?: <what stays proposed>}
    drops the proposal, or with `replacement` only the hunks left out of it; answers the draft state.
404 no-proposal; 409 stale (rev moved: the body carries the current `state`); 400 bad-resolve.
Every change bumps the change feed, so the other tabs follow within a second.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from draft_studio import proposals
from draft_studio.apiutil import ApiError
from draft_studio.store import Store

Json = dict[str, Any]
ROUTE = re.compile(r"^/api/drafts/([^/]+)/proposals/([0-9]+)/(accept|reject)$")


def land(
    store: Store, draft_id: str, ask: Json, replacement: str, *, job: str = "", missing: Sequence[str] = ()
) -> Json:
    """Claude's answer for the span of `ask` (asked on `ask["text"]`) as a pending proposal.

    `outcome`: ok | stale (an edit touched the span since it was asked) | gone | overlap (proposals.add).
    `job`: the job that asked (jobs.py); it is noted in the same write as the proposal (`Store.landed`).
    `missing`: keys of the screenshots the answer asked for and did not get (kept on the proposal).
    """
    with store._lock:
        path = store._dir(draft_id)
        text = store.get(draft_id)["text"]
        data = proposals.read(path)
        moved = proposals.shift(proposals.changes(ask["text"], text), ask["from"], ask["to"])
        outcome, pid = "stale", None
        if moved is not None:
            span = proposals.cut(text, *moved)
            data, outcome, pid = proposals.add(
                data, *moved, replacement, revises=ask.get("revises", []), span=span, missing=list(missing)
            )
        if outcome == "ok" or job:
            proposals.write(path, proposals.mark(data, job) if job else data)
            store.feed.bump(draft_id)
        return {"outcome": outcome, "proposal": pid}


def _resolve(store: Store, draft_id: str, pid: int, body: Json, field: str) -> tuple[Json, Json, str | None]:
    """(draft state, the proposal, the body's optional text field) of a resolve call, checked."""
    state = store.get(draft_id)
    value = body.get(field)
    if value is not None and not isinstance(value, str):
        raise ApiError(400, "bad-resolve")
    try:
        found = proposals.find(proposals.read(store._dir(draft_id)), pid, body.get("rev"))
    except proposals.NoProposal as exc:
        raise ApiError(404, "no-proposal") from exc
    except proposals.StaleProposal as exc:
        raise ApiError(409, "stale", extra={"state": state}) from exc
    return state, found, value


def resolve(store: Store, draft_id: str, pid: int, action: str, body: Any) -> Json:
    """The route body: accept or reject one pending proposal; the draft state."""
    if not isinstance(body, dict):
        raise ApiError(400, "bad-json")
    return (accept if action == "accept" else reject)(store, draft_id, pid, body)


def accept(store: Store, draft_id: str, pid: int, body: Json) -> Json:
    """Apply the proposal (or some of its hunks) to the draft text; the new state."""
    with store._lock:
        state, found, text = _resolve(store, draft_id, pid, body, "text")
        new_span = found["replacement"] if text is None else text
        start, end = found["from"], found["to"]
        if new_span == proposals.cut(state["text"], start, end):
            return state
        path = store._dir(draft_id)
        data = proposals.accepted(proposals.read(path), found, new_span)
        claude = proposals.claude_after(state["claude"], start, end, proposals.width(new_span))
        proposals.write(path, data)
        patch = {"text": proposals.replace_span(state["text"], start, end, new_span), "claude": claude}
        return store.save(draft_id, patch, kind="accept", remap=False)


def reject(store: Store, draft_id: str, pid: int, body: Json) -> Json:
    """Drop the proposal, or the hunks left out of `replacement`; the new state."""
    with store._lock:
        state, found, replacement = _resolve(store, draft_id, pid, body, "replacement")
        span = proposals.cut(state["text"], found["from"], found["to"])
        path = store._dir(draft_id)
        proposals.write(path, proposals.rejected(proposals.read(path), found, replacement, span))
        store.feed.bump(draft_id)
        return store.get(draft_id)
