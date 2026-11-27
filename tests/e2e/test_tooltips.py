"""Tooltips on icon-only buttons: desktop hover, keyboard focus, demos, locale, bounds.

Touch long-press lives in test_tooltips_touch.py; helpers in tips.py.
Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_tooltips.py
Screenshots (desktop hover) go to $DS_SHOTS.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.env import Env
from tests.e2e.tips import EN, PL, bubble, inside, no_bubble, propose, select, viewport

pytestmark = pytest.mark.e2e


def test_desktop_hover_focus_demo_locale(env: Env, tip_draft: str) -> None:
    base, did, shots, b = env.base, tip_draft, env.shots, env.b
    viewport(b, 1920, 1080)
    # C1/C2 list page: every icon-only button shows its tip; away and Escape hide it.
    b.goto(base + "/")
    b.wait("document.querySelector('.draft-row .delete') !== null")
    for sel, key in (
        ("#new-mail", "newMail"),
        ("#new-text", "newText"),
        ("#ui-locale", "uiLocale"),
        (".draft-row .delete", "delete"),
    ):
        b.hover(sel)
        v = bubble(b, EN["tips"][key])
        assert inside(v) and not v["demo"] and v["hidden"] == "true", (sel, v)
    b.mouse_to(900, 600)
    no_bubble(b)
    b.hover("#new-mail")
    bubble(b, EN["tips"]["newMail"])
    b.key("Escape", "Escape", 27)
    no_bubble(b)
    # A closed bubble adds no visible text.
    assert EN["tips"]["newMail"] not in b.js("document.body.innerText")
    # Every icon-only button on the page has a tip.
    assert b.js("[...document.querySelectorAll('.icon-btn')].every(e => e.dataset.label)")

    # C3: keyboard focus shows the bubble; Tab moves it on.
    b.mouse_to(900, 600)
    b.js("document.activeElement?.blur(); document.body.focus()")
    b.key("Tab", "Tab", 9)
    b.wait("document.activeElement?.id === 'new-mail'", what="focus new-mail")
    bubble(b, EN["tips"]["newMail"])
    b.key("Tab", "Tab", 9)
    bubble(b, EN["tips"]["newText"])
    b.js("document.activeElement.blur()")
    no_bubble(b)

    # Editor: text-only tips (C6), every icon button labelled.
    b.goto(f"{base}/d/?id={did}")
    for sel, key in (
        ("#undo", "undo"),
        ("#history-open", "history"),
        ("#ui-locale", "uiLocale"),
        ("header .icon-btn[data-label='back']", "back"),
        ("#gmail", "gmail"),
    ):
        b.hover(sel)
        v = bubble(b, EN["tips"][key])
        assert not v["demo"] and inside(v), (sel, v)

    # C4: selection toolbar demos.
    for cls, key in (("sel-ask", "ask"), ("sel-rewrite", "rewrite"), ("sel-change", "change")):
        select(b, "alpha beta")
        b.hover(f".sel-toolbar .{cls}")
        v = bubble(b, EN["tips"][key])
        assert v["demo"] and v["moving"] > 0, (key, v)
        if key == "ask":
            time.sleep(2.8)  # ~75 % into the 1.5 s loop: the answer is in
            b.screenshot(shots / "tip-hover-ask-1920.png")
    # The demo is the built-in sample, never the draft text.
    assert "alpha" not in b.js("document.querySelector('ds-tip').textContent")

    # C5: accept/reject demos, clicking still acts at once.
    b.mouse_to(900, 900)
    propose(b)
    b.hover(".cm-hunk .hunk-accept")
    v = bubble(b, EN["tips"]["acceptHunk"])
    assert v["demo"] and v["moving"] > 0
    b.hover(".cm-hunk .hunk-reject")
    v = bubble(b, EN["tips"]["rejectHunk"])
    assert v["demo"] and v["moving"] > 0
    n = b.js("document.querySelectorAll('.cm-hunk').length")
    b.click(".cm-hunk .hunk-accept")
    b.wait(f"document.querySelectorAll('.cm-hunk').length === {n - 1}", what="accepted")
    b.hover(".cm-hunk-all .reject-all")
    bubble(b, EN["tips"]["rejectAll"])
    b.click(".cm-hunk-all .reject-all")
    b.wait("document.querySelector('.cm-hunk') === null")

    # C1 history panel (top layer): restore and close show above the dialog.
    b.click("#history-open")
    b.wait("document.querySelector('#history').open")
    b.hover("#history .restore")
    assert inside(bubble(b, EN["tips"]["restore"]))
    b.hover("#history-close")
    bubble(b, EN["tips"]["close"])
    b.click("#history-close")

    # C10: reduced motion -> text plus one still end frame.
    b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    b.mouse_to(900, 900)
    select(b, "alpha beta")
    b.hover(".sel-toolbar .sel-ask")
    v = bubble(b, EN["tips"]["ask"])
    assert v["demo"] and v["moving"] == 0, v
    b.screenshot(shots / "tip-still-ask-1920.png")
    b.mouse_to(900, 900)
    select(b, "alpha beta")
    b.hover(".sel-toolbar .sel-change")
    assert bubble(b, EN["tips"]["change"])["moving"] == 0
    b.screenshot(shots / "tip-still-change-1920.png")
    b.call("Emulation.setEmulatedMedia", features=[])

    # C11: Polish tips; aria-label stays the full screen-reader label.
    b.mouse_to(900, 900)
    b.click("#ui-locale")
    b.wait("document.documentElement.lang === 'pl'")
    bubble(b, PL["tips"]["uiLocale"])  # the open globe bubble follows the switch
    b.hover("#undo")
    bubble(b, PL["tips"]["undo"])
    assert b.js("document.querySelector('#undo').getAttribute('aria-label')") == PL["labels"]["undo"]
    b.click("#ui-locale")
    b.wait("document.documentElement.lang === 'en'")
    assert b.errors() == []


@pytest.mark.parametrize("width,height", [(390, 844), (1920, 1080)])
@pytest.mark.parametrize("rtl", [False, True])
def test_c12_bubble_inside_viewport(env: Env, tip_draft: str, width: int, height: int, rtl: bool) -> None:
    base, did, b = env.base, tip_draft, env.b
    viewport(b, width, height)
    for url in ("/", f"/d/?id={did}"):
        b.goto(base + url)
        if rtl:
            b.js("document.documentElement.dir = 'rtl'")
        ids = b.js(
            "[...document.querySelectorAll('header .icon-btn')].filter(e => e.offsetParent)"
            ".map((e, i) => (e.dataset.tipTest = String(i)))"
        )
        for i in ids:
            b.hover(f"[data-tip-test='{i}']")
            v = bubble(b)
            assert inside(v), (url, i, v)
    assert b.errors() == []
