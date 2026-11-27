"""Permission requests: one pending record per refusal, granted or denied once."""

from __future__ import annotations

from pathlib import Path

import pytest

from draft_studio.capture.pending import NotPending, PendingStore, RequestNotFound
from draft_studio.capture.spec import Grant, parse

SPEC = parse({"url": "https://x/", "name": "a", "steps": [{"click": "Zapłać"}]})


def make(root: Path) -> tuple[PendingStore, str]:
    pending = PendingStore(root)
    rec = pending.create(
        "draft1",
        SPEC,
        step=0,
        reason="deny-list",
        detail="zaplac",
        blocked=[],
        grants=[Grant(step=5, click=True, requests=())],
        shot=b"\x89PNG-shot",
    )
    return pending, rec["id"]


def test_create_lists_and_serves_the_shot(tmp_path: Path) -> None:
    pending, rid = make(tmp_path)
    [rec] = pending.list_pending()
    assert rec["id"] == rid and rec["draftId"] == "draft1" and rec["stepText"] == "Zapłać"
    assert rec["status"] == "pending" and rec["shot"] == f"/api/captures/{rid}/shot"
    assert rec["allow"] == {"step": 0, "click": True, "requests": []}
    assert pending.shot(rid) == b"\x89PNG-shot"
    assert (tmp_path / "captures" / f"{rid}.json").is_file()


def test_grant_is_used_once_and_carries_earlier_grants(tmp_path: Path) -> None:
    pending, rid = make(tmp_path)
    draft_id, spec, grants = pending.claim(rid)
    assert draft_id == "draft1" and spec == SPEC
    assert grants == [Grant(step=5, click=True, requests=()), Grant(step=0, click=True, requests=())]
    assert pending.list_pending() == []
    with pytest.raises(NotPending):
        pending.claim(rid)
    with pytest.raises(NotPending):
        pending.deny(rid)


def test_request_refusal_allows_only_its_requests(tmp_path: Path) -> None:
    pending = PendingStore(tmp_path)
    rec = pending.create(
        "d",
        SPEC,
        step=0,
        reason="request",
        detail="POST http://x/api",
        blocked=[("POST", "http://x/api")],
        grants=[],
        shot=b"",
    )
    assert pending.claim(rec["id"])[2] == [Grant(step=0, click=False, requests=(("POST", "http://x/api"),))]


def test_deny_cancels(tmp_path: Path) -> None:
    pending, rid = make(tmp_path)
    assert pending.deny(rid)["status"] == "denied"
    assert pending.list_pending() == []


@pytest.mark.parametrize("rid", ["nope", "../../etc", "0123456789abcdef"])
def test_unknown_ids(tmp_path: Path, rid: str) -> None:
    pending, _ = make(tmp_path)
    for call in (pending.claim, pending.deny, pending.shot):
        with pytest.raises(RequestNotFound):
            call(rid)


def test_empty_store(tmp_path: Path) -> None:
    assert PendingStore(tmp_path).list_pending() == []


def test_spent_grants_drop_same_step_merges_later_steps_carry(tmp_path: Path) -> None:
    pending = PendingStore(tmp_path)
    two = parse({"url": "https://x/", "name": "a", "steps": [{"click": "A"}, {"click": "B"}, {"click": "C"}]})
    before = Grant(step=0, click=False, requests=(("POST", "http://x/a"),))
    same = Grant(step=1, click=True)
    later = Grant(step=2, click=True)
    rec = pending.create(
        "d",
        two,
        step=1,
        reason="request",
        detail="POST http://x/b",
        blocked=[("POST", "http://x/b")],
        grants=[before, same, later],
        shot=b"",
    )
    assert rec["allow"] == {"step": 1, "click": True, "requests": [["POST", "http://x/b"]]}
    assert rec["grants"] == [later.to_json()]
    assert pending.claim(rec["id"])[2] == [
        later,
        Grant(step=1, click=True, requests=(("POST", "http://x/b"),)),
    ]
