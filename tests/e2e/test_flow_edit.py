"""Flow 2: proposals and diffs, stale drop, autosave, undo and history, mail fields, Claude failure.

Run: .venv/bin/pytest -m e2e tests/e2e (needs web/dist built and chromium).
Screenshots go to $DS_SHOTS (default: a scratch dir).
"""

from __future__ import annotations

import json
import time

import pytest

from tests.e2e.env import Env

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module", autouse=True)
def mail_draft(env: Env) -> None:
    """A mail draft as the intake flow leaves it, with one plain edit of yours (C6)."""
    env.new_draft(
        "mail", "Mail to the venue owner [2rounds]", (("Anna Nowak", "Warm and short"), ("Friday",))
    )
    env.caret(len("Draft for Mail"))
    env.b.type(" (MINE)")
    env.saved(env.draft_id())


def test_c10_c11_diff_per_hunk(env: Env) -> None:
    env.b.click(".cm-content")
    env.caret(len(env.doc()))
    env.b.type("\nalpha beta gamma delta")
    env.saved(env.draft_id())
    before = env.doc()
    env.propose("alpha beta gamma")
    assert env.b.js("document.querySelectorAll('.cm-hunk').length") == 2
    assert env.b.js("document.querySelector('.cm-del').textContent") == "beta"
    assert env.doc() == before  # nothing applied yet
    env.shot("c10-diff")
    env.b.click(".cm-hunk[data-hunk='0'] .hunk-accept")
    env.b.wait("document.querySelectorAll('.cm-hunk').length === 1")
    env.b.click(".cm-hunk .hunk-reject")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc() == before.replace("alpha beta gamma", "alpha 2 gamma")
    # accept-all on one proposal
    env.propose("alpha 2 gamma delta")
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc().endswith("alpha 2 gamma delta better")
    # reject-all on another
    snapshot = env.doc()
    env.propose("gamma delta better")
    env.b.click(".reject-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc() == snapshot


def test_c12_c14_your_text_replaced_only_on_accept(env: Env) -> None:
    env.caret(len(env.doc()))
    env.b.type("\nmy zork words here")
    env.saved(env.draft_id())
    assert "zork" not in env.tinted()
    env.propose("my zork words")
    assert "my zork words here" in env.doc()  # still mine until accept
    assert "zork" not in env.tinted()
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc().endswith("my 2 words better here")
    assert "2" in env.tinted() and "better" in env.tinted()
    assert "here" not in env.tinted().split("|")[-1]  # neighbouring text stays yours
    # C14: rewrite a nearby Claude span; (MINE) stays plain and unchanged.
    env.propose("Draft for Mail", "shout")
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert "(MINE)" in env.doc()
    assert "MINE" not in env.tinted()
    env.shot("c12-accepted")


def test_c13_stale_proposal_dropped(env: Env) -> None:
    env.propose("Warm and short")
    start = env.doc().index("Warm and short")
    env.caret(start + 4)
    env.b.type("ly")
    env.b.wait("document.querySelector('.cm-hunk') === null", what="stale drop")
    assert "Warmly and short" in env.doc()
    assert "2" not in env.doc()[start : start + 20]


def test_c16_autosave_and_reopen(env: Env) -> None:
    env.caret(0)
    env.b.type("Hi! ")
    draft_id = env.draft_id()
    env.b.goto(env.base + "/")  # leave right away; autosave must have it
    time.sleep(0.6)
    env.b.goto(env.base + f"/d/?id={draft_id}")
    env.b.wait("draftStudio.view.state.doc.toString().startsWith('Hi! ')", what="reopened text")
    md = env.data / "drafts" / draft_id / "draft.md"
    assert md.read_text().startswith("Hi! ")


def test_c17_undo_walks_history_and_restore(env: Env) -> None:
    draft_id = env.draft_id()
    for word in (" one", " two"):
        env.caret(len(env.doc()))
        env.b.type(word)
        env.saved(draft_id)
        time.sleep(0.5)
    with_two = env.doc()
    env.b.click("#undo")
    env.b.wait(f"draftStudio.view.state.doc.toString() !== {json.dumps(with_two)}")
    # Walk back across the reload boundary through saved versions.
    seen = [env.doc()]
    for _ in range(6):
        env.b.click("#undo")
        time.sleep(0.4)
        env.idle()
        seen.append(env.doc())
    # Every step moved back to an older saved text, never forward.
    saved = [h["text"] for h in env.api(f"/api/drafts/{draft_id}/history")["versions"] if "text" in h]
    assert len(set(seen)) >= 4, seen
    assert seen[-1] in saved and seen[-1] != with_two
    assert not seen[-1].endswith(" one two")
    # History dialog: restore the newest version, then undo the restore.
    env.b.click("#history-open")
    env.b.wait("document.querySelector('#history').open")
    env.shot("c17-history")
    current = env.doc()
    newest_v = max(
        h["v"]
        for h in env.api(f"/api/drafts/{draft_id}/history")["versions"]
        if "text" in h and h["text"] == with_two
    )
    env.b.click(f".version[data-v='{newest_v}'] .restore")
    env.b.wait(f"draftStudio.view.state.doc.toString() === {json.dumps(with_two)}", what="restored")
    env.b.click("#undo")
    env.b.wait(f"draftStudio.view.state.doc.toString() === {json.dumps(current)}", what="undo restore")
    env.b.click("#redo")
    env.saved(draft_id)


def test_c27_mail_fields_are_yours(env: Env) -> None:
    draft_id = env.draft_id()
    env.b.click("#to")
    env.b.type("owner@example.com")
    env.b.click("#subject")
    env.b.js("document.querySelector('#subject').select()")
    env.b.type("My subject")
    state = env.saved(draft_id)
    assert state["meta"]["to"] == "owner@example.com"
    assert state["meta"]["subject"] == "My subject"
    assert state["meta"]["subjectAuthor"] == "you" and state["meta"]["toAuthor"] == "you"
    assert env.b.js("document.querySelector('#subject').dataset.author") == "you"


def test_c23_claude_failure_keeps_text(env: Env) -> None:
    before = env.doc()
    propose_fail = "Friday"
    env.select_text(propose_fail)
    env.b.click(".sel-toolbar .sel-change")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type("[fail]")
    env.b.click(".sel-toolbar .sel-submit")
    env.b.wait("document.querySelector('#status').dataset.status === 'error'", what="error icon")
    assert env.b.js("document.querySelector('#status').getAttribute('role')") == "alert"
    assert env.b.js("document.querySelector('#status').getAttribute('aria-label')").startswith(
        "Claude could not"
    )
    assert env.doc() == before
    env.shot("c23-error")
