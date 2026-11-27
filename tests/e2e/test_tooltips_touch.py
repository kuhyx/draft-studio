"""Tooltips on touch: long-press shows the bubble without acting, a quick tap acts at once.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_tooltips_touch.py
Screenshot (390 px long-press) goes to $DS_SHOTS.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.e2e.env import Env
from tests.e2e.tips import EN, bubble, doc, inside, no_bubble, propose, rect, select, viewport

pytestmark = pytest.mark.e2e


def test_touch_long_press_and_tap(env: Env, tip_draft: str) -> None:
    base, did, shots, b = env.base, tip_draft, env.shots, env.b
    viewport(b, 390, 844, mobile=True)
    b.goto(f"{base}/d/?id={did}")
    before = doc(b)
    # C7/C8: long-press rewrite -> bubble above, stays while held; lift hides it, no action.
    select(b, "alpha beta")
    btn = rect(b, ".sel-toolbar .sel-rewrite")

    def held() -> dict[str, Any]:
        bubble(b, EN["tips"]["rewrite"])
        time.sleep(1.8)  # let the demo reach its end frame
        b.screenshot(shots / "tip-longpress-rewrite-390.png")
        return bubble(b, EN["tips"]["rewrite"])

    v = b.touch(".sel-toolbar .sel-rewrite", 0.8, held)
    assert v["b"] <= btn["t"] and inside(v) and v["demo"], (v, btn)
    no_bubble(b)
    time.sleep(0.5)
    assert not b.js("document.body.dataset.busy") and doc(b) == before
    assert b.js("document.querySelector('.cm-hunk')") is None

    # C9: a quick tap acts at once and leaves no bubble.
    b.touch(".sel-toolbar .sel-ask", 0.05)
    b.wait("!document.querySelector('.sel-form').hidden", 3, what="ask form open")
    no_bubble(b)

    # Header long-press at the top edge: the bubble never sits under the finger.
    hb = rect(b, "#undo")
    v = b.touch("#undo", 0.8, lambda: bubble(b, EN["tips"]["undo"]))
    assert inside(v) and (v["b"] <= hb["t"] or v["l"] >= hb["r"] or v["r"] <= hb["l"]), (v, hb)

    # C8 on the mousedown path: long-press accept does not accept.
    b.js("draftStudio.view.dispatch({selection: {anchor: 0}})")
    propose(b, tap=True)
    n = b.js("document.querySelectorAll('.cm-hunk').length")
    b.touch(".cm-hunk .hunk-accept", 0.8, lambda: bubble(b, EN["tips"]["acceptHunk"]))
    no_bubble(b)
    time.sleep(0.4)
    assert b.js("document.querySelectorAll('.cm-hunk').length") == n
    b.touch(".cm-hunk .hunk-accept", 0.05)
    b.wait(f"document.querySelectorAll('.cm-hunk').length === {n - 1}", what="tap accepts")
    assert b.errors() == []
