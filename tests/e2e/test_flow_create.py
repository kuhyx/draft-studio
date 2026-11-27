"""Flow 1: create drafts, intake rounds, authorship tint, selection toolbar, ask panel.

Run: .venv/bin/pytest -m e2e tests/e2e (needs web/dist built and chromium).
Screenshots go to $DS_SHOTS (default: a scratch dir).
"""

from __future__ import annotations

import pytest

from tests.e2e.env import Env

pytestmark = pytest.mark.e2e


def test_c1_list_page(env: Env) -> None:
    env.b.goto(env.base + "/")
    env.b.wait("document.querySelector('#new-mail')?.getAttribute('aria-label') === 'New mail draft'")
    text = env.b.js("document.body.innerText.trim()")
    assert text == "", f"visible chrome text: {text!r}"
    env.shot("c1-list-empty")


def test_c3_create_mail_and_text(env: Env) -> None:
    env.b.click("#new-text")
    env.b.wait("location.pathname === '/d/' && document.body.dataset.ready === '1'")
    env.b.wait("!document.querySelector('#brief-form').hidden")
    assert env.b.js("document.querySelector('#mail-fields').hidden") is True
    assert env.b.js("document.querySelector('#gmail').hidden") is True  # C26
    env.b.goto(env.base + "/")
    env.b.click("#new-mail")
    env.b.wait("location.pathname === '/d/' && document.body.dataset.ready === '1'")
    env.b.wait("!document.querySelector('#brief-form').hidden")
    assert env.b.js("document.querySelector('#mail-fields').hidden") is False
    assert env.b.js("document.querySelector('#gmail').hidden") is False
    drafts = env.api("/api/drafts")["drafts"]
    assert sorted(d["kind"] for d in drafts) == ["mail", "text"]


def test_c4_c5_intake_rounds_then_draft(env: Env) -> None:
    env.b.click("#brief")
    env.b.type("Mail to the venue owner about renting the room [2rounds]")
    env.b.click("#brief-form button[type=submit]")
    env.b.wait("document.querySelectorAll('#questions textarea').length === 2", what="round 1")
    env.shot("c4-intake-round1")
    env.b.click("#questions .question:nth-child(1) textarea")
    env.b.type("Anna Nowak")
    env.b.click("#questions .question:nth-child(2) textarea")
    env.b.type("Warm and short")
    env.b.click("#intake-form button[type=submit]")
    env.b.wait("document.querySelectorAll('#questions textarea').length === 1", what="round 2")
    env.b.click("#questions textarea")
    env.b.type("Friday")
    env.b.click("#intake-form button[type=submit]")
    env.b.wait("!document.querySelector('#work').hidden", what="draft shown")
    doc = env.doc()
    assert "Anna Nowak" in doc and "Friday" in doc
    subject = env.b.js("document.querySelector('#subject').value")
    assert subject.endswith("Subject from Claude")
    assert env.b.js("document.querySelector('#subject').dataset.author") == "claude"
    # C6: the whole first draft is Claude text, tinted.
    assert env.tinted().replace("|", "").replace("\n", "") == doc.replace("\n", "")
    env.shot("c4-first-draft")


def test_c6_c15_your_typing_is_plain(env: Env) -> None:
    env.caret(len("Draft for Mail"))
    env.b.type(" (MINE)")
    env.b.wait("!document.querySelector('.cm-claude')?.textContent.includes('MINE')")
    assert "(MINE)" in env.doc()
    assert "MINE" not in env.tinted()
    # Delete and paste freely anywhere.
    end = len(env.doc())
    env.caret(end)
    env.b.key("Backspace", "Backspace", 8)
    env.b.type("s!")
    env.b.call("Input.insertText", text="\nPasted line.")
    state = env.saved(env.draft_id())
    assert state["text"] == env.doc()
    assert state["text"].endswith("regards!\nPasted line.")
    env.shot("c6-tint")


def test_c7_toolbar_on_any_span(env: Env) -> None:
    doc = env.doc()
    spans = [(0, 1), (doc.index("Anna"), doc.index("Anna") + 4), (0, doc.index(".") + 1), (0, len(doc))]
    for start, end in spans:
        env.select(start, end)
        env.b.wait("document.querySelector('.sel-toolbar .sel-ask') !== null", what=f"toolbar {start}-{end}")
        assert env.b.js("document.querySelectorAll('.sel-toolbar .icon-btn').length") >= 3
    env.shot("c7-toolbar")


def test_c8_c9_ask_goes_to_panel_and_survives_reload(env: Env) -> None:
    before = env.doc()
    env.select_text("Anna Nowak")
    env.b.click(".sel-toolbar .sel-ask")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type("Is the name right?")
    env.b.click(".sel-toolbar .sel-submit")
    env.idle()
    env.b.wait("document.querySelectorAll('.ask-card').length === 1")
    answer = env.b.js("document.querySelector('.ask-card .ask-a').textContent")
    assert answer.startswith("[en] About 'Anna Nowak'")
    assert env.doc() == before
    env.shot("c8-ask-panel")
    env.b.goto(env.b.js("location.href"))
    env.b.wait("document.querySelectorAll('.ask-card').length === 1", what="ask after reload")
    hist = env.api(f"/api/drafts/{env.draft_id()}/history")["versions"]
    assert any(h["type"] == "ask" and h["question"] == "Is the name right?" for h in hist)
