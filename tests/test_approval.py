"""Approval: kuhy marks the exact draft.md bytes as approved; any content edit drops it.

The consumer is kuhy's outbound-message hook (~/.claude/hooks/outbound_draft_gate.py,
outside this public repo). `hook_allows` mirrors its `approval_problem()` check so
these tests pin the on-disk contract it reads.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from draft_studio import approval
from draft_studio.store import Store
from tests.conftest import App


def hook_allows(store: Store, draft_id: str) -> bool:
    """The hook's check: meta phase is "approved" and approvedSha256 hashes draft.md on disk."""
    folder = store.root / "drafts" / draft_id
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((folder / "draft.md").read_bytes()).hexdigest()
    return meta.get("phase") == "approved" and str(meta.get("approvedSha256") or "").lower() == digest


def editing(store: Store, text: str = "Dzień dobry, Anno.\n") -> str:
    draft_id = str(store.create("mail")["id"])
    store.save(draft_id, {"text": text, "phase": "editing", "to": "a@b.pl", "subject": "S"})
    return draft_id


def test_approve_hashes_the_bytes_on_disk(store: Store) -> None:
    did = editing(store)
    version = store.get(did)["meta"]["version"]
    state = approval.approve(store, did, "Dzień dobry, Anno.\n")
    raw = (store.root / "drafts" / did / "draft.md").read_bytes()
    assert state["meta"]["phase"] == "approved"
    assert state["meta"]["approvedSha256"] == hashlib.sha256(raw).hexdigest()
    assert state["approved"] is True
    assert store.get(did)["approved"] is True
    assert hook_allows(store, did)
    assert state["meta"]["version"] == version  # not a text version: undo never walks onto it
    assert store.history(did)[-1]["type"] == "approve"


def test_changed_text_drops_approval(store: Store) -> None:
    did = editing(store)
    approval.approve(store, did, store.get(did)["text"])
    state = store.save(did, {"text": "Dzień dobry, Anno!\n"})
    assert state["meta"]["phase"] == "editing"
    assert "approvedSha256" not in state["meta"]
    assert state["approved"] is False
    assert not hook_allows(store, did)


def test_identical_put_keeps_approval(store: Store) -> None:
    """Autosave always resends text, to and subject; unchanged values keep the approval."""
    did = editing(store)
    text = store.get(did)["text"]
    approval.approve(store, did, text)
    state = store.save(did, {"text": text, "to": "a@b.pl", "subject": "S", "lang": "pl", "claude": [[0, 3]]})
    assert state["approved"] is True
    assert hook_allows(store, did)


@pytest.mark.parametrize("field", ["to", "subject"])
def test_changed_mail_field_drops_approval(store: Store, field: str) -> None:
    did = editing(store)
    approval.approve(store, did, store.get(did)["text"])
    state = store.save(did, {field: "other"})
    assert state["approved"] is False
    assert state["meta"]["phase"] == "editing"


def test_restore_of_other_text_drops_approval(store: Store) -> None:
    did = editing(store)
    store.save(did, {"text": "second"})
    approval.approve(store, did, "second")
    old = next(h["v"] for h in store.history(did) if h.get("text") == "Dzień dobry, Anno.\n")
    assert store.restore(did, old)["approved"] is False
    assert not hook_allows(store, did)


def test_put_cannot_set_approved(store: Store) -> None:
    did = editing(store)
    with pytest.raises(ValueError, match="approve"):
        store.save(did, {"phase": "approved"})
    assert store.get(did)["meta"]["phase"] == "editing"


def test_put_of_other_phase_clears_the_hash(store: Store) -> None:
    did = editing(store)
    approval.approve(store, did, store.get(did)["text"])
    state = store.save(did, {"phase": "editing"})
    assert state["approved"] is False
    assert "approvedSha256" not in state["meta"]


def test_tampered_file_is_not_approved(store: Store) -> None:
    did = editing(store)
    approval.approve(store, did, store.get(did)["text"])
    (store.root / "drafts" / did / "draft.md").write_bytes(b"swapped")
    assert store.get(did)["approved"] is False


def test_approve_refuses_stale_text_and_early_phase(store: Store) -> None:
    did = editing(store)
    with pytest.raises(approval.ApprovalError) as stale:
        approval.approve(store, did, "what the editor showed before the save")
    assert stale.value.code == "stale"
    fresh = str(store.create("text")["id"])
    with pytest.raises(approval.ApprovalError) as early:
        approval.approve(store, fresh, "")
    assert early.value.code == "bad-phase"
    assert not hook_allows(store, did)


def test_unapprove(store: Store) -> None:
    did = editing(store)
    approval.approve(store, did, store.get(did)["text"])
    approval.approve(store, did, store.get(did)["text"])  # again: same hash, still approved
    assert store.get(did)["approved"] is True
    state = approval.unapprove(store, did)
    assert state["approved"] is False
    assert state["meta"]["phase"] == "editing"
    assert "approvedSha256" not in state["meta"]
    entries = len(store.history(did))
    assert approval.unapprove(store, did)["approved"] is False  # no-op: nothing logged
    assert len(store.history(did)) == entries
    assert [h["type"] for h in store.history(did)][-3:] == ["approve", "approve", "unapprove"]


# -- HTTP routes ---------------------------------------------------------------
def test_approve_routes(app: App) -> None:
    did = editing(app.store)
    text = app.store.get(did)["text"]
    status, state = app.call("POST", f"/api/drafts/{did}/approve", {"text": text})
    assert status == 200 and state["approved"] is True and state["meta"]["phase"] == "approved"
    assert app.call("GET", f"/api/drafts/{did}")[1]["approved"] is True
    assert hook_allows(app.store, did)
    status, state = app.call("PUT", f"/api/drafts/{did}", {"text": text + "x"})
    assert status == 200 and state["approved"] is False
    assert app.call("GET", f"/api/drafts/{did}")[1]["meta"]["phase"] == "editing"
    app.call("POST", f"/api/drafts/{did}/approve", {"text": text + "x"})
    status, state = app.call("POST", f"/api/drafts/{did}/unapprove", {})
    assert status == 200 and state["approved"] is False


def test_approve_route_errors(app: App) -> None:
    did = editing(app.store)
    assert app.call("POST", f"/api/drafts/{did}/approve", {"text": "old"}) == (409, {"error": "stale"})
    assert app.call("POST", f"/api/drafts/{did}/approve", {}) == (400, {"error": "bad-text"})
    fresh = app.call("POST", "/api/drafts", {"kind": "text"})[1]["id"]
    assert app.call("POST", f"/api/drafts/{fresh}/approve", {"text": ""}) == (409, {"error": "bad-phase"})
    assert app.call("PUT", f"/api/drafts/{did}", {"phase": "approved"}) == (400, {"error": "bad-save"})
    assert app.call("POST", "/api/drafts/nope000000/approve", {"text": ""})[0] == 404
    assert app.call("GET", f"/api/drafts/{did}/approve")[0] == 404


def test_hook_contract_file(tmp_path: Path) -> None:
    """approvedSha256 is lowercase hex of the file bytes, even for CRLF and non-ASCII text."""
    store = Store(tmp_path / "data")
    did = editing(store, "Grüße\r\nzażółć\n")
    assert store.get(did)["text"] == "Grüße\nzażółć\n"  # the store reads with universal newlines
    approval.approve(store, did, store.get(did)["text"])
    meta = json.loads((store.root / "drafts" / did / "meta.json").read_text(encoding="utf-8"))
    raw = (store.root / "drafts" / did / "draft.md").read_bytes()
    assert raw == "Grüße\r\nzażółć\n".encode()
    assert meta["approvedSha256"] == hashlib.sha256(raw).hexdigest()
