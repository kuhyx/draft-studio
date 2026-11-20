"""Headless end-to-end flow against the real server, fake Claude and fake Gmail.

Run: .venv/bin/pytest -m e2e tests/e2e (needs web/dist built and /usr/bin/chromium).
Screenshots go to $DS_SHOTS (default: a temp dir).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.cdp import Browser, free_port

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "web" / "dist"


class Env:
    def __init__(self, base: str, data: Path, locales: Path, b: Browser, shots: Path) -> None:
        self.base, self.data, self.locales, self.b, self.shots = base, data, locales, b, shots

    def shot(self, name: str) -> None:
        self.b.screenshot(self.shots / f"{name}.png")

    def api(self, path: str) -> Any:
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def gmail(self) -> dict[str, Any]:
        f = self.data / "gmail-fake.json"
        return json.loads(f.read_text()) if f.exists() else {}

    # editor helpers
    def doc(self) -> str:
        return str(self.b.js("draftStudio.view.state.doc.toString()"))

    def select(self, start: int, end: int) -> None:
        self.b.js(
            "draftStudio.view.focus();"
            f" draftStudio.view.dispatch({{selection: {{anchor: {start}, head: {end}}}}})"
        )

    def select_text(self, needle: str) -> tuple[int, int]:
        i = self.doc().index(needle)
        self.select(i, i + len(needle))
        return i, i + len(needle)

    def caret(self, pos: int) -> None:
        self.select(pos, pos)

    def tinted(self) -> str:
        return str(
            self.b.js("[...document.querySelectorAll('.cm-claude')].map(e => e.textContent).join('|')")
        )

    def saved(self, draft_id: str) -> dict[str, Any]:
        self.b.wait("document.querySelector('#status').dataset.status === 'saved'", what="saved")
        state: dict[str, Any] = self.api(f"/api/drafts/{draft_id}")
        return state

    def draft_id(self) -> str:
        return str(self.b.js("new URLSearchParams(location.search).get('id')"))

    def idle(self) -> None:
        self.b.wait("!document.body.dataset.busy", timeout=15, what="claude idle")


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    tmp = Path(tempfile.mkdtemp(prefix="ds-e2e-"))
    data, locales = tmp / "data", tmp / "locales"
    shutil.copytree(REPO / "locales", locales)
    shots = Path(os.environ.get("DS_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    environ = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(data),
        "DRAFT_STUDIO_LOCALES": str(locales),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_GMAIL": "fake",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "draft_studio", "--port", str(port), "--build", "e2e"], cwd=REPO, env=environ
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/version", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    b = Browser()
    try:
        yield Env(base, data, locales, b, shots)
        assert b.errors() == [], b.errors()
    finally:
        b.close()
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


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


def propose(env: Env, needle: str, instruction: str = "") -> None:
    env.select_text(needle)
    if instruction:
        env.b.click(".sel-toolbar .sel-change")
        env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
        env.b.type(instruction)
        env.b.click(".sel-toolbar .sel-submit")
    else:
        env.b.click(".sel-toolbar .sel-rewrite")
    env.idle()
    env.b.wait("document.querySelector('.cm-hunk') !== null", what="diff shown")


def test_c10_c11_diff_per_hunk(env: Env) -> None:
    env.b.click(".cm-content")
    env.caret(len(env.doc()))
    env.b.type("\nalpha beta gamma delta")
    env.saved(env.draft_id())
    before = env.doc()
    propose(env, "alpha beta gamma")
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
    propose(env, "alpha 2 gamma delta")
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc().endswith("alpha 2 gamma delta better")
    # reject-all on another
    snapshot = env.doc()
    propose(env, "gamma delta better")
    env.b.click(".reject-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc() == snapshot


def test_c12_c14_your_text_replaced_only_on_accept(env: Env) -> None:
    env.caret(len(env.doc()))
    env.b.type("\nmy zork words here")
    env.saved(env.draft_id())
    assert "zork" not in env.tinted()
    propose(env, "my zork words")
    assert "my zork words here" in env.doc()  # still mine until accept
    assert "zork" not in env.tinted()
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert env.doc().endswith("my 2 words better here")
    assert "2" in env.tinted() and "better" in env.tinted()
    assert "here" not in env.tinted().split("|")[-1]  # neighbouring text stays yours
    # C14: rewrite a nearby Claude span; (MINE) stays plain and unchanged.
    propose(env, "Draft for Mail", "shout")
    env.b.click(".accept-all")
    env.b.wait("document.querySelector('.cm-hunk') === null")
    assert "(MINE)" in env.doc()
    assert "MINE" not in env.tinted()
    env.shot("c12-accepted")


def test_c13_stale_proposal_dropped(env: Env) -> None:
    propose(env, "Warm and short")
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


def test_c24_c25_gmail_upsert(env: Env) -> None:
    draft_id = env.draft_id()
    env.b.click("#gmail")
    env.b.wait("document.querySelector('#status').dataset.status === 'done'", what="gmail done")
    box = env.gmail()
    assert len(box) == 1
    gid, mail = next(iter(box.items()))
    assert mail["to"] == "owner@example.com" and mail["subject"] == "My subject"
    assert mail["body"] == env.doc()
    env.caret(len(env.doc()))
    env.b.type(" more")
    env.b.click("#gmail")
    env.b.wait("document.querySelector('#status').dataset.status === 'done'")
    box = env.gmail()
    assert list(box) == [gid] and box[gid]["body"].endswith(" more")
    # Deleted in Gmail: the next press creates a new one.
    box.pop(gid)
    (env.data / "gmail-fake.json").write_text(json.dumps(box))
    env.b.click("#gmail")
    env.b.wait(f"document.body.dataset.gmail && document.body.dataset.gmail !== {json.dumps(gid)}")
    assert len(env.gmail()) == 1
    assert env.api(f"/api/drafts/{draft_id}")["meta"]["gmailDraftId"] != gid


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
    propose(env, "Napisz", "lang")
    assert env.b.js("document.querySelector('.cm-ins').textContent").startswith("[pl]")
    env.b.click(".reject-all")
    env.b.js(
        "const l = document.querySelector('#lang'); l.value = 'en'; l.dispatchEvent(new Event('change'))"
    )
    env.saved(env.draft_id())
    propose(env, "Napisz", "lang")
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
