"""Comments while proposals wait: revise a pending proposal in place, and keep several pending.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_comments.py
Screenshots (1920x1080 and 390x844) go to $DS_SHOTS.
"""

from __future__ import annotations

import pytest

from tests.e2e.env import Env
from tests.e2e.tips import viewport

pytestmark = pytest.mark.e2e
TEXT = "Hi there.\n\nalpha beta gamma end.\n\nfour five six seven."
HUNKS = "document.querySelectorAll('.cm-hunk').length"
TIP_BOTTOM = "document.querySelector('.cm-tooltip').getBoundingClientRect().bottom"
LINE_TOP = "draftStudio.view.coordsAtPos(draftStudio.view.state.selection.main.from).top + 1"
IDS = "[...new Set([...document.querySelectorAll('.cm-hunk')].map(h => h.dataset.proposal))]"


@pytest.fixture
def draft(env: Env) -> str:
    """A text draft in the editing phase holding TEXT, opened in the editor."""
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "A note [noq]"})
    env.post(f"/api/drafts/{did}", {"text": TEXT, "claude": []}, method="PUT")
    env.b.goto(f"{env.base}/d/?id={did}")
    env.b.wait("document.body.dataset.ready === '1' && !document.querySelector('#work').hidden")
    return did


def comment(env: Env, needle: str, instruction: str) -> None:
    """Select `needle` and send `instruction` through the selection toolbar's change form."""
    env.select_text(needle)
    env.b.click(".sel-toolbar .sel-change")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type(instruction)
    env.b.click(".sel-toolbar .sel-submit")


def toolbar_shots(env: Env, needle: str, name: str) -> None:
    """The selection toolbar open on `needle`, at desktop and phone size."""
    for w, h, mobile in ((1920, 1080, False), (390, 844, True)):
        viewport(env.b, w, h, mobile)
        env.select_text(needle)
        env.b.wait("document.querySelector('.sel-toolbar .sel-change') !== null", what="toolbar")
        env.b.click(".sel-toolbar .sel-change")
        env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
        # The open form grows the tooltip; CodeMirror moves it back above the line on its next measure.
        env.b.wait(f"{TIP_BOTTOM} <= {LINE_TOP}", what="toolbar above the proposal")
        env.shot(f"{name}-{w}")
    viewport(env.b, 1920, 1080)


def test_comment_on_pending_proposal_revises_it(env: Env, draft: str) -> None:
    env.propose("alpha beta gamma")
    assert env.b.js(HUNKS) == 2  # "alpha 2 gamma better": beta -> 2, + " better"
    [old_id] = env.b.js(IDS)
    toolbar_shots(env, "beta", "toolbar-in-proposal")
    comment(env, "beta", "shorter")
    env.idle()
    # Claude revised its own replacement: "alpha 2 gamma", in place, same proposal.
    env.b.wait(f"{HUNKS} === 1", what="revised proposal")
    assert env.b.js(IDS) == [old_id]
    assert env.b.js("document.querySelector('.cm-ins').textContent") == "2"
    assert env.doc() == TEXT  # nothing applied yet
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc() == TEXT.replace("alpha beta gamma", "alpha 2 gamma")
    env.saved(draft)


def test_comment_elsewhere_keeps_both_pending(env: Env, draft: str) -> None:
    env.propose("four five")
    comment(env, "six seven", "longer")
    env.idle()
    env.b.wait(f"{IDS}.length === 2", what="two proposals")
    first, second = env.b.js(IDS)
    assert env.b.js("document.querySelectorAll('.accept-all').length") == 1
    for w, h, mobile in ((1920, 1080, False), (390, 844, True)):
        viewport(env.b, w, h, mobile)
        env.caret(0)
        env.shot(f"two-proposals-{w}")
    viewport(env.b, 1920, 1080)
    env.b.click(f".cm-hunk[data-proposal='{first}'] .hunk-accept")
    env.b.wait(f"JSON.stringify({IDS}) === '[\"{second}\"]'", what="first accepted")
    env.b.click(f".cm-hunk[data-proposal='{second}'] .hunk-reject")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc() == TEXT.replace("four five", "four five rewritten")
    assert "rewritten" in env.tinted()
    env.saved(draft)
