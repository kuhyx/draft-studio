"""Gmail draft upsert: exact fields, update or replace the stored draft, never send."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from draft_studio.gmail import CliGmail, FakeGmail, GmailError, make_gmail
from draft_studio.mail import Mail
from tests.gmail_fixtures import (
    PLAIN,
)
from tests.imgdata import png


def ref(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "content": f"sha256:{digest}:{path}",
        "filename": path.name,
        "mimeType": "image/png",
        "inline": True,
    }


def test_fake_create_update_recreate(tmp_path: Path) -> None:
    g = FakeGmail(tmp_path / "gmail-fake.json")
    first = g.upsert(PLAIN, "", "s1")
    assert first["created"] is True
    again = g.upsert(Mail(["a@b.c"], "Hi 2", "Body 2", "<p>Body 2</p>"), first["id"], "s1")
    assert again == {"id": first["id"], "created": False}
    assert g.drafts()[first["id"]] == {
        "to": ["a@b.c"],
        "subject": "Hi 2",
        "body": "Body 2",
        "htmlBody": "<p>Body 2</p>",
        "attachments": [],
        "studioId": "s1",
    }
    g.delete(first["id"])
    third = g.upsert(PLAIN, first["id"], "s1")
    assert third["created"] is True and third["id"] != first["id"]
    assert len(g.drafts()) == 1 and g.sent == 0


def test_fake_replaces_a_draft_with_files(tmp_path: Path) -> None:
    pic = tmp_path / "p.png"
    pic.write_bytes(png())
    g = FakeGmail(tmp_path / "gmail-fake.json")
    mail = Mail(["a@b.c"], "S", "B", '<img src="cid:att0">', [ref(pic)])
    first = g.upsert(mail, "", "s1")
    second = g.upsert(mail, first["id"], "s1")
    assert second["created"] is True and second["id"] != first["id"]
    assert list(g.drafts()) == [second["id"]]
    assert g.drafts()[second["id"]]["attachments"] == [ref(pic)]


def test_fake_checks_file_references(tmp_path: Path) -> None:
    pic = tmp_path / "p.png"
    pic.write_bytes(png())
    good = ref(pic)
    g = FakeGmail(tmp_path / "gmail-fake.json")
    for content in (
        f"sha256:{'0' * 64}:{pic}",
        f"sha256:{good['content'][7:71]}:p.png",
        "aGVsbG8=",
        f"sha256:{'0' * 64}:{tmp_path}/gone.png",
    ):
        with pytest.raises(GmailError):
            g.upsert(Mail([], "", "t", "", [{**good, "content": content}]), "", "s1")
    assert g.drafts() == {}


def test_make_gmail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "fake")
    assert isinstance(make_gmail(tmp_path), FakeGmail)
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "cli")
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE_BIN", "/x/claude")
    g = make_gmail(tmp_path)
    assert isinstance(g, CliGmail)
    assert g.binary == Path("/x/claude")
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "zzz")
    with pytest.raises(ValueError):
        make_gmail(tmp_path)
