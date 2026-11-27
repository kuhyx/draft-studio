"""Approval: kuhy marks the draft he reviewed as fit to send.

Contract with kuhy's outbound-message hook (~/.claude/hooks/outbound_draft_gate.py,
`approval_problem()`): a draft is approved while meta.json has
``"phase": "approved"`` and ``approvedSha256`` equals the sha256 of draft.md's
bytes on disk. The hash is taken from the file, never from a string in memory,
so any later write to draft.md breaks the match even if this code is bypassed.
`Store.save` drops the approval when text, To or Subject change (CONTENT_KEYS).

Approval is an event, not a text version: the version counter stays put, so the
editor's undo walk through saved versions never lands on it.
"""

from __future__ import annotations

import hashlib

from draft_studio.links import missing_assets
from draft_studio.store import APPROVED, EDITING, Json, Store, _now, file_sha256


class ApprovalError(ValueError):
    """A refused approval; `code` is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse_missing_assets(store: Store, draft_id: str, text: str) -> None:
    """A linked image that is not stored would leave as a broken picture: refuse ("missing-asset")."""
    stored = {item["name"] for item in store.list_assets(draft_id)}
    if missing_assets(text, stored):
        raise ApprovalError("missing-asset")


def approve(store: Store, draft_id: str, seen: str) -> Json:
    """Approve the draft as it is on disk, if that is the text kuhy saw (`seen`).

    Refuses ("stale") when the saved text differs from what the editor showed,
    ("bad-phase") before the first draft exists (brief or intake), and
    ("missing-asset") while the text links an image the draft does not hold.
    """
    with store._lock:
        path = store._dir(draft_id)
        meta = store._meta(draft_id)
        if meta["phase"] not in (EDITING, APPROVED):
            raise ApprovalError("bad-phase")
        if (path / "draft.md").read_text(encoding="utf-8") != seen:
            raise ApprovalError("stale")
        _refuse_missing_assets(store, draft_id, seen)
        meta["phase"] = APPROVED
        meta["approvedSha256"] = file_sha256(path / "draft.md")
        store._put_meta(draft_id, meta)
        store._append(path, {"ts": _now(), "type": "approve", "sha256": meta["approvedSha256"]})
        return store.get(draft_id)


def unapprove(store: Store, draft_id: str) -> Json:
    """Take the approval back; a draft that is not approved is left as it is."""
    with store._lock:
        path = store._dir(draft_id)
        meta = store._meta(draft_id)
        if meta["phase"] == APPROVED:
            meta["phase"] = EDITING
            meta.pop("approvedSha256", None)
            store._put_meta(draft_id, meta)
            store._append(path, {"ts": _now(), "type": "unapprove"})
        return store.get(draft_id)


def approved_mail(store: Store, draft_id: str) -> tuple[Json, str]:
    """(meta, text) of a draft kuhy approved: the one check before anything leaves.

    Reads draft.md once under the store lock and returns the very bytes it
    hashed, so text saved between check and send can never ride along.
    Refuses "not-approved" unless the phase is approved, and "stale" when
    draft.md no longer matches approvedSha256 (a write that bypassed the store).
    Refuses "missing-asset" too, for drafts approved before approve() checked images.
    """
    with store._lock:
        path = store._dir(draft_id)
        meta = store._meta(draft_id)
        data = (path / "draft.md").read_bytes()
        if meta["phase"] != APPROVED:
            raise ApprovalError("not-approved")
        if meta.get("approvedSha256") != hashlib.sha256(data).hexdigest():
            raise ApprovalError("stale")
        text = data.decode("utf-8")
        _refuse_missing_assets(store, draft_id, text)
        return meta, text
