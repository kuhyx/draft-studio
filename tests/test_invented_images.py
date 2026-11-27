"""Claude never gets a made-up image link to kuhy; approval refuses a draft whose image file is missing."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from draft_studio.claude import Context
from draft_studio.claude_fake import FAKE_IMAGE, FakeClaude
from draft_studio.store import file_sha256
from tests.conftest import App
from tests.imgdata import png


def text_draft(app: App, text: str) -> str:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = str(d["id"])
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "claude": [], "phase": "editing"})
    return did


def propose(app: App, did: str, text: str, instruction: str = "") -> tuple[int, Any]:
    body = {"text": text, "from": 0, "to": len(text), "instruction": instruction}
    return app.call("POST", f"/api/drafts/{did}/propose", body)


def test_proposal_with_missing_asset_never_reaches_the_client(
    app: App, caplog: pytest.LogCaptureFixture
) -> None:
    did = text_draft(app, "one two three")
    with caplog.at_level(logging.WARNING, logger="draft_studio"):
        assert propose(app, did, "one two three", "[fakeimg]") == (502, {"error": "claude"})
    assert FAKE_IMAGE in caplog.text
    assert not [e for e in app.store.history(did) if e["type"] == "proposal"]
    assert app.store.get(did)["text"] == "one two three"


def test_proposal_keeping_a_stored_image_passes(app: App) -> None:
    did = text_draft(app, "")
    name = app.store.add_asset(did, "panel.png", png())
    text = f"Look ![](assets/{name}) here"
    status, prop = propose(app, did, text, "lang")
    assert status == 200
    assert prop["replacement"].endswith(text)
    assert [e for e in app.store.history(did) if e["type"] == "proposal"]


def test_first_draft_with_invented_image_is_refused(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    assert app.call("POST", f"/api/drafts/{did}/brief", {"brief": "note [noq] [fakeimg]"})[0] == 502
    assert app.store.get(did)["text"] == ""


def test_claude_sees_the_stored_asset_names(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Context] = []

    def record(self: FakeClaude, ctx: Context) -> str:
        seen.append(ctx)
        return "x"

    monkeypatch.setattr(FakeClaude, "rewrite", record)
    did = text_draft(app, "")
    b = app.store.add_asset(did, "b.png", png(5, 5))
    a = app.store.add_asset(did, "a.png", png(6, 6))
    assert propose(app, did, "abc")[0] == 200
    assert seen[0].assets == tuple(sorted([a, b]))


def test_save_keeps_a_missing_link_but_approve_refuses_it(app: App) -> None:
    text = "Hi\n![](assets/TODO-cyberfolks-01-panel.png)\n"
    did = text_draft(app, text)
    assert app.store.get(did)["text"] == text  # kuhy's edit always wins
    assert app.call("POST", f"/api/drafts/{did}/approve", {"text": text}) == (409, {"error": "missing-asset"})
    assert app.store.get(did)["meta"]["phase"] == "editing"


def test_mail_approved_before_the_gate_cannot_leave(app: App) -> None:
    """A draft approved by an older build (no check then) still never reaches Gmail or the PDF."""
    text = "Hi\n![](assets/gone.png)\n"
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    did = str(d["id"])
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "to": "a@b.c", "subject": "S", "phase": "editing"})
    meta = app.store._meta(did)
    meta.update(phase="approved", approvedSha256=file_sha256(app.store._dir(did) / "draft.md"))
    app.store._put_meta(did, meta)
    assert app.call("POST", f"/api/drafts/{did}/gmail", {}) == (409, {"error": "missing-asset"})
    assert app.call("GET", f"/api/drafts/{did}/export.pdf")[0] == 409
    assert app.gmail.drafts() == {}
