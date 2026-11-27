"""Flow 3: polish brief and language switch, UI locale drop-in and RTL, delete to trash.

Run: .venv/bin/pytest -m e2e tests/e2e (needs web/dist built and chromium).
Screenshots go to $DS_SHOTS (default: a scratch dir).
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.env import Env

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module", autouse=True)
def two_drafts(env: Env) -> None:
    """The list already holds a mail and a text draft; C20 adds the third."""
    env.post("/api/drafts", {"kind": "mail"})
    env.post("/api/drafts", {"kind": "text"})


def test_c20_polish_brief_and_language_switch(env: Env) -> None:
    env.b.goto(env.base + "/")
    env.b.click("#new-text")
    env.b.wait("!document.querySelector('#brief-form').hidden")
    env.b.click("#brief")
    env.b.type("Napisz krótki tekst o wiośnie [noq]")
    env.b.click("#brief-form button[type=submit]")
    env.b.wait("!document.querySelector('#work').hidden")
    assert env.b.js("document.querySelector('#lang').value") == "pl"
    assert env.b.js("document.querySelector('.cm-content').lang") == "pl"
    env.select_text("Napisz")
    env.b.click(".sel-toolbar .sel-ask")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type("Dlaczego?")
    env.b.click(".sel-toolbar .sel-submit")
    env.b.wait("document.querySelector('.ask-card .ask-a')?.textContent.startsWith('[pl]')")
    env.propose("Napisz", "lang")
    assert env.b.js("document.querySelector('.cm-ins').textContent").startswith("[pl]")
    env.b.click(".reject-all")
    env.b.js(
        "const l = document.querySelector('#lang'); l.value = 'en'; l.dispatchEvent(new Event('change'))"
    )
    env.saved(env.draft_id())
    env.propose("Napisz", "lang")
    assert env.b.js("document.querySelector('.cm-ins').textContent").startswith("[en]")
    env.b.click(".reject-all")


def test_c21_c22_locale_switch_dropin_and_rtl(env: Env) -> None:
    env.b.click("#ui-locale")  # en -> pl
    env.b.wait("document.querySelector('#undo').getAttribute('aria-label') === 'Cofnij'", what="pl labels")
    assert env.b.js("document.documentElement.lang") == "pl"
    assert env.b.js("document.querySelector('#undo').innerText.trim()") == ""
    # Drop in a new language file: it loads with no rebuild, and its dir=rtl mirrors the layout.
    xx = json.loads((env.locales / "en.json").read_text())
    xx["dir"] = "rtl"
    xx["labels"]["undo"] = "XX-undo"
    (env.locales / "xx.json").write_text(json.dumps(xx))
    env.b.click("#ui-locale")  # pl -> xx
    env.b.wait("document.documentElement.dir === 'rtl'", what="rtl")
    assert env.b.js("document.querySelector('#undo').getAttribute('aria-label')") == "XX-undo"
    rects = env.b.js(
        "(() => { const r = s => document.querySelector(s).getBoundingClientRect();"
        " return {back: r('a[data-label=back]').x, undo: r('#undo').x, w: innerWidth,"
        " sw: document.documentElement.scrollWidth} })()"
    )
    assert rects["back"] > rects["undo"], rects  # back icon now on the right
    assert rects["sw"] <= rects["w"]
    env.shot("c22-rtl")
    env.b.click("#ui-locale")  # back to en (en < pl < xx)
    env.b.wait("document.documentElement.dir === 'ltr'")


def test_c18_delete_moves_to_trash(env: Env) -> None:
    env.b.goto(env.base + "/")
    env.b.wait("document.querySelectorAll('.draft-row').length === 3")
    victim = env.b.js("document.querySelector('.draft-row').dataset.id")
    env.b.click(f".draft-row[data-id='{victim}'] .delete")
    env.b.wait("document.querySelectorAll('.draft-row').length === 2")
    trashed = [p.name for p in (env.data / "trash").iterdir()]
    assert any(name.startswith(victim) for name in trashed)
    assert (env.data / "trash" / next(n for n in trashed if n.startswith(victim)) / "draft.md").exists()
    env.shot("c18-list")
