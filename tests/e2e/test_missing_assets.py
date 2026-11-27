"""No fake images: Claude's made-up image link is cut from its proposal (the span pulses); a typed one shows a red frame.

kuhy: "fake images are worse than no image at all".
Run: .venv/bin/pytest -m e2e tests/e2e/test_missing_assets.py (needs web/dist built and chromium).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from draft_studio.claude_fake import FAKE_IMAGE
from tests.e2e.env import Env

pytestmark = pytest.mark.e2e
PRESSED = "document.querySelector('#approve').getAttribute('aria-pressed')"
TEXT = "Alpha beta gamma delta"
SIGNAL = """(() => {
  const g = document.querySelector('.cm-signal-glyph[data-signal=stripped]')
  if (!g || getComputedStyle(g).opacity < 0.9) return null
  const r = g.getBoundingClientRect()
  return {mark: document.querySelector('.cm-signal[data-signal=stripped]')?.textContent,
          glyph: !!g.querySelector('svg'), body: document.body.dataset.signal,
          status: document.querySelector('#status').dataset.status,
          onScreen: r.width > 10 && r.bottom <= innerHeight && r.top >= 0}
})()"""
MARKER = """(() => {
  const m = document.querySelector('.cm-image[data-broken] .cm-image-broken')
  if (!m || !m.querySelector('svg')) return null
  const box = m.closest('.cm-image'), s = getComputedStyle(m)
  const probe = document.createElement('i')
  probe.style.color = 'var(--danger)'
  document.body.append(probe)
  const danger = getComputedStyle(probe).color
  probe.remove()
  const r = m.getBoundingClientRect()
  return {style: s.borderTopStyle, border: s.borderTopColor, color: s.color, danger,
          label: m.getAttribute('aria-label'), text: box.textContent, imgs: box.querySelectorAll('img').length,
          w: r.width, h: r.height, onScreen: r.bottom <= innerHeight && r.top >= 0}
})()"""


def open_draft(env: Env) -> str:
    did = str(env.post("/api/drafts", {"kind": "mail"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Mail to the owner [noq]"})
    env.post(f"/api/drafts/{did}", {"text": TEXT, "claude": [], "to": "a@b.c", "subject": "Hi"}, method="PUT")
    env.b.goto(f"{env.base}/d/?id={did}")
    env.b.wait("document.body.dataset.ready === '1' && !!window.draftStudio", what="editor ready")
    return did


def approve_status(env: Env, did: str, text: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{env.base}/api/drafts/{did}/approve",
        data=json.dumps({"text": text}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, ""
    except urllib.error.HTTPError as err:
        return err.code, str(json.loads(err.read())["error"])


def test_invented_image_dropped_and_typed_one_framed(env: Env) -> None:
    b = env.b
    did = open_draft(env)
    # 1. Claude answers with text plus a link to an image nobody uploaded: the proposal keeps the
    #    text, the link is cut, and the span pulses the image-off glyph.
    env.propose("beta gamma", "[fakeimg]")
    got = b.wait(SIGNAL, what="stripped signal")
    env.shot("stripped-signal-1920")
    assert got == {"mark": "beta gamma", "glyph": True, "body": "stripped", "status": "saved", "onScreen": True}
    assert b.js("[...document.querySelectorAll('.cm-ins')].map(e => e.textContent).join('|')") == " rewritten"
    assert "assets/" not in str(b.js("document.querySelector('.cm-content').textContent"))
    assert env.doc() == TEXT
    [logged] = [h for h in env.api(f"/api/drafts/{did}/history")["versions"] if h["type"] == "proposal"]
    assert logged["stripped"] == [f"![](assets/{FAKE_IMAGE})"]
    b.wait("document.querySelector('.cm-signal-glyph') === null", what="pulse over")
    b.click(".accept-all")
    b.wait("document.querySelector('.cm-hunk') === null")
    accepted = TEXT.replace("beta gamma", "beta gamma rewritten")
    assert env.doc() == accepted

    # 2. kuhy types the same link himself: saved as typed, drawn as a red dashed frame, no picture.
    link = f"\n![](assets/{FAKE_IMAGE})"
    b.js(f"draftStudio.view.focus(); draftStudio.view.dispatch({{selection: {{anchor: {len(accepted)}}}}})")
    b.type(link)
    got = b.wait(MARKER, what="missing-image frame")
    assert got["style"] == "dashed"
    assert got["border"] == got["danger"] and got["color"] == got["danger"]
    assert got["imgs"] == 0 and got["text"] == ""
    assert got["label"] == "Missing"
    assert got["w"] > 100 and got["h"] > 50 and got["onScreen"]
    env.shot("missing-image-frame")
    assert env.saved(did)["text"] == accepted + link

    # 3. Approve is refused while the image is missing: the button stays off, so Gmail and PDF stay off.
    b.click("#approve")
    b.wait("document.querySelector('#status').dataset.error === 'missing-asset'", what="approve refused")
    assert b.js(PRESSED) == "false"
    assert env.api(f"/api/drafts/{did}")["meta"]["phase"] == "editing"
    assert approve_status(env, did, accepted + link) == (409, "missing-asset")
