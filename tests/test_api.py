"""POST /api/drafts/<id>/gmail: only an approved, unedited mail reaches Gmail, images inline."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from draft_studio.gmail import FakeGmail, GmailError
from tests.conftest import App
from tests.imgdata import png


def mail_draft(app: App, text: str = "Body", to: str = "a@b.c", approve: bool = True) -> str:
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    did = str(d["id"])
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "to": to, "subject": "S", "phase": "editing"})
    if approve:
        assert app.call("POST", f"/api/drafts/{did}/approve", {"text": text})[0] == 200
    return did


def press(app: App, did: str) -> tuple[int, Any]:
    return app.call("POST", f"/api/drafts/{did}/gmail", {})


def approve(app: App, did: str) -> None:
    text = app.call("GET", f"/api/drafts/{did}")[1]["text"]
    assert app.call("POST", f"/api/drafts/{did}/approve", {"text": text})[0] == 200


def test_unapproved_mail_is_refused(app: App) -> None:
    did = mail_draft(app, approve=False)
    assert press(app, did) == (409, {"error": "not-approved"})
    assert app.gmail.drafts() == {}
    assert app.store.get(did)["meta"]["gmailDraftId"] == ""


def test_edit_after_approval_is_refused(app: App) -> None:
    did = mail_draft(app)
    app.call("PUT", f"/api/drafts/{did}", {"text": "Body, edited"})
    assert press(app, did) == (409, {"error": "not-approved"})
    assert app.gmail.drafts() == {}


def test_file_changed_behind_the_server_is_refused(app: App) -> None:
    did = mail_draft(app)
    (app.store.root / "drafts" / did / "draft.md").write_text("Tampered", encoding="utf-8")
    assert press(app, did) == (409, {"error": "stale"})
    assert app.gmail.drafts() == {}


def test_approved_upsert_flow(app: App) -> None:
    did = mail_draft(app, text="Hi **Ann**,\nsee you.", to="a@b.c, d@e.f")
    status, g1 = press(app, did)
    assert status == 200 and g1["created"] is True
    box = app.gmail.drafts()[g1["id"]]
    assert box["to"] == ["a@b.c", "d@e.f"] and box["subject"] == "S"
    assert box["body"] == "Hi Ann,\nsee you."
    assert box["htmlBody"] == "<p>Hi <strong>Ann</strong>,<br>\nsee you.</p>"
    assert box["attachments"] == [] and box["studioId"] == did
    app.call("PUT", f"/api/drafts/{did}", {"text": "Body 2"})
    approve(app, did)
    _, g2 = press(app, did)
    assert g2 == {"id": g1["id"], "created": False}
    assert len(app.gmail.drafts()) == 1 and app.gmail.drafts()[g1["id"]]["body"] == "Body 2"
    app.gmail.delete(g1["id"])
    _, g3 = press(app, did)
    assert g3["created"] is True
    assert app.store.get(did)["meta"]["gmailDraftId"] == g3["id"]


def test_images_go_inline_and_replace_the_old_draft(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    did = str(d["id"])
    data = png()
    name = app.store.add_asset(did, "chart.png", data)
    text = f"Look:\n![chart](assets/{name})"
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "to": "a@b.c", "subject": "S", "phase": "editing"})
    approve(app, did)
    _, g1 = press(app, did)
    box = app.gmail.drafts()[g1["id"]]
    path = (app.store.root / "drafts" / did / "assets" / name).resolve()
    assert box["attachments"] == [
        {
            "content": f"sha256:{hashlib.sha256(data).hexdigest()}:{path}",
            "filename": name,
            "mimeType": "image/png",
            "inline": True,
        }
    ]
    assert '<img src="cid:att0" alt="chart">' in box["htmlBody"]
    # A mail with files is never updated in place (that drops the files): new draft, old one deleted.
    _, g2 = press(app, did)
    assert g2["created"] is True and g2["id"] != g1["id"]
    assert list(app.gmail.drafts()) == [g2["id"]]
    assert app.store.get(did)["meta"]["gmailDraftId"] == g2["id"]
    log = (app.store.root / "drafts" / did / "history.jsonl").read_text().splitlines()
    assert [json.loads(x)["id"] for x in log if json.loads(x)["type"] == "gmail"] == [g1["id"], g2["id"]]


def test_missing_image_and_size_cap(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    did = mail_draft(app, text="![](assets/gone.png)", approve=False)
    assert app.call("POST", f"/api/drafts/{did}/approve", {"text": "![](assets/gone.png)"}) == (
        409,
        {"error": "missing-asset"},
    )
    assert press(app, did) == (409, {"error": "not-approved"})
    _, d = app.call("POST", "/api/drafts", {"kind": "mail"})
    big = str(d["id"])
    name = app.store.add_asset(big, "big.png", png(30, 30))
    text = f"![](assets/{name})"
    app.call("PUT", f"/api/drafts/{big}", {"text": text, "phase": "editing"})
    approve(app, big)
    assert press(app, big) == (400, {"error": "no-recipient"})
    app.call("PUT", f"/api/drafts/{big}", {"to": "a@b.c"})
    approve(app, big)
    monkeypatch.setattr("draft_studio.mailhtml.MAX_ATTACH_BYTES", 10)
    assert press(app, big) == (413, {"error": "too-large"})
    assert app.gmail.drafts() == {}


def test_text_draft_refused_and_gmail_errors(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    _, t = app.call("POST", "/api/drafts", {"kind": "text"})
    assert press(app, t["id"]) == (400, {"error": "not-mail"})
    did = mail_draft(app)

    def boom(*a: Any) -> None:
        raise GmailError("x")

    monkeypatch.setattr(FakeGmail, "upsert", boom)
    assert press(app, did) == (502, {"error": "gmail"})
