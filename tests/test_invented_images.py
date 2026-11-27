"""Bug class: Claude's made-up image links never reach kuhy (stripped, the rest of the answer kept).

Approval refuses a draft whose image file is missing.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from draft_studio.claude import Context
from draft_studio.claude_fake import FAKE_IMAGE, FakeClaude
from draft_studio.store import file_sha256
from tests.conftest import App
from tests.imgdata import png

FAKE_LINK = f"![](assets/{FAKE_IMAGE})"


def text_draft(app: App, text: str) -> str:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = str(d["id"])
    app.call("PUT", f"/api/drafts/{did}", {"text": text, "claude": [], "phase": "editing"})
    return did


def propose(app: App, did: str, text: str, instruction: str = "") -> tuple[int, Any]:
    body = {"text": text, "from": 0, "to": len(text), "instruction": instruction}
    return app.call("POST", f"/api/drafts/{did}/propose", body)


def proposals(app: App, did: str) -> list[dict[str, Any]]:
    return [e for e in app.store.history(did) if e["type"] == "proposal"]


def test_invented_image_is_stripped_and_the_text_kept(app: App, caplog: pytest.LogCaptureFixture) -> None:
    did = text_draft(app, "one two three")
    with caplog.at_level(logging.WARNING, logger="draft_studio"):
        status, prop = propose(app, did, "one two three", "[fakeimg]")
    assert status == 200
    assert prop == {
        "replacement": "one two three rewritten",
        "from": 0,
        "to": 13,
        "stripped": [FAKE_LINK],
        "dropped": [],
        "partial": False,
        "outcome": "ok",
        "proposal": 1,
    }
    assert FAKE_IMAGE in caplog.text
    [logged] = proposals(app, did)
    assert logged["replacement"] == "one two three rewritten" and logged["stripped"] == [FAKE_LINK]
    assert app.store.get(did)["text"] == "one two three"


def test_proposal_keeping_a_stored_image_passes(app: App) -> None:
    did = text_draft(app, "")
    name = app.store.add_asset(did, "panel.png", png())
    text = f"Look ![](assets/{name}) here"
    status, prop = propose(app, did, text, "lang")
    assert status == 200
    assert prop["replacement"].endswith(text) and prop["stripped"] == []
    assert proposals(app, did)[0]["stripped"] == []


def test_kuhys_own_images_in_the_span_survive(app: App) -> None:
    """kuhy's remote and missing-asset links stay: Claude may only ADD links to stored assets."""
    text = "Me ![](https://kuhy.pl/me.png) and ![](assets/typed.png)"
    did = text_draft(app, text)
    status, prop = propose(app, did, text, "lang")
    assert status == 200 and prop["replacement"].endswith(f"] {text}") and prop["stripped"] == []


def test_first_draft_with_invented_image_keeps_the_text(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    status, step = app.call("POST", f"/api/drafts/{did}/brief", {"brief": "note [noq] [fakeimg]"})
    assert status == 200 and step["stripped"] == [FAKE_LINK]
    text = app.store.get(did)["text"]
    assert text == "Draft for note [noq] [fakeimg].\n\n\n\nBest regards"  # no intake answers between
    assert step["state"]["claude"] == [[0, len(text)]]
    assert [e["stripped"] for e in app.store.history(did) if e["type"] == "stripped"] == [[FAKE_LINK]]


def test_first_draft_keeps_an_image_kuhy_put_in_the_brief(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    did = d["id"]
    status, step = app.call(
        "POST", f"/api/drafts/{did}/brief", {"brief": f"note [noq] [fakeimg] {FAKE_LINK}"}
    )
    assert status == 200 and step["stripped"] == []
    assert app.store.get(did)["text"].endswith(FAKE_LINK)
    assert not [e for e in app.store.history(did) if e["type"] == "stripped"]


def test_intake_questions_carry_an_empty_stripped_list(app: App) -> None:
    _, d = app.call("POST", "/api/drafts", {"kind": "text"})
    status, step = app.call("POST", f"/api/drafts/{d['id']}/brief", {"brief": "note"})
    assert status == 200 and step["questions"] and step["stripped"] == []


def test_unparsable_invented_link_still_drops_the_answer(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: a link the stripper cannot cut cleanly drops the whole answer, as before."""
    monkeypatch.setattr(
        FakeClaude, "rewrite", lambda self, ctx: {"replacement": "A ![a](x.png unclosed", "captures": []}
    )
    did = text_draft(app, "abc")
    assert propose(app, did, "abc") == (502, {"error": "claude"})
    assert proposals(app, did) == []


def test_claude_sees_the_stored_asset_names(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Context] = []

    def record(self: FakeClaude, ctx: Context) -> dict[str, Any]:
        seen.append(ctx)
        return {"replacement": "x", "captures": []}

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
