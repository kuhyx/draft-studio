"""A held screenshot in the browser: a capture that would POST holds the proposal (busy, nothing
sent); a grant through the API releases it, and the page lands it with the real screenshot.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_held.py
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from tests.capture_live.conftest import Site, serve_site
from tests.e2e.env import Env
from tests.e2e.test_images import IMG_UNDER_LINE

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    with serve_site() as found:
        yield found


def grant(env: Env, rid: str) -> Any:
    """POST .../grant: it re-runs the real capture, so it may take longer than Env.post allows."""
    req = urllib.request.Request(
        f"{env.base}/api/captures/{rid}/grant", data=b"{}", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=50) as r:
        return json.loads(r.read())


def test_held_proposal_is_busy_until_granted_then_lands_with_its_screenshot(env: Env, site: Site) -> None:
    text = f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”."
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Poradnik [noq]"})
    doc = f"{text}\n\nKoniec."
    env.post(f"/api/drafts/{did}", {"text": doc, "claude": [], "to": "", "subject": ""}, method="PUT")
    env.b.goto(f"{env.base}/d/?id={did}")
    sent = site.writes["POST /api/save"]

    env.select(0, len(text))
    env.b.click(".sel-toolbar .sel-change")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type("[shot]")
    env.b.click(".sel-toolbar .sel-submit")
    env.b.wait("document.body.dataset.held === '1'", timeout=40, what="proposal held")
    env.idle()
    assert env.b.js("document.querySelector('#status').dataset.status") == "busy"
    assert env.b.js("document.querySelector('.cm-hunk')") is None
    assert site.writes["POST /api/save"] == sent  # held: the capture sent nothing
    # Typing outside the span while held: autosave runs, the spinner stays.
    env.caret(len(doc))
    env.b.type("!")
    saved = f"fetch('/api/drafts/{did}').then(r => r.json()).then(s => s.text.endsWith('Koniec.!'))"
    env.b.wait(saved, what="typed text saved")
    assert env.b.js("document.querySelector('#status').dataset.status") == "busy"
    env.shot("held-busy")

    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    (req,) = hold["requests"]
    assert (req["reason"], req["detail"]) == ("request", f"POST {site.base}/api/save")
    assert "md" in grant(env, req["id"])
    assert site.writes["POST /api/save"] == sent + 1
    # Coming back to the page polls at once instead of waiting out the backoff.
    env.b.js("window.dispatchEvent(new Event('focus'))")
    env.b.wait("document.querySelector('.cm-hunk') !== null", timeout=10, what="released proposal shown")
    env.b.wait("!document.body.dataset.held", what="hold cleared")
    env.b.wait("document.querySelector('#status').dataset.status !== 'busy'", what="spinner gone")
    hunks = str(env.b.js("[...document.querySelectorAll('.cm-ins')].map(e => e.textContent).join('|')"))
    assert "(assets/shot-1-" in hunks, hunks
    env.b.wait(
        f"fetch('/api/drafts/{did}/held').then(r => r.json()).then(l => l.released.length === 0)",
        what="release collected",
    )
    env.shot("held-released")
    env.b.click(".accept-all")
    name = env.doc().split("(assets/", 1)[1].split(")", 1)[0]
    got = env.b.wait(IMG_UNDER_LINE % json.dumps(name), what="screenshot rendered")
    assert got["natural"] == 2560, got
    env.saved(did)
    env.shot("held-accepted")


HIDE = "Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true})"
SHOW = "delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'))"


def test_held_first_draft_survives_a_field_edit_saved_after_its_release(env: Env, site: Site) -> None:
    env.b.goto(env.base + "/")
    env.b.click("#new-mail")
    env.b.wait("location.pathname === '/d/' && document.body.dataset.ready === '1'")
    env.b.wait("!document.querySelector('#brief-form').hidden")
    did = env.draft_id()
    env.b.click("#brief")
    env.b.type(f"[noq] [shot] Otworzyć {site.base}/index.html i kliknąć „Zapisz”.")
    env.b.click("#brief-form button[type=submit]")
    env.b.wait("document.body.dataset.held === '1'", timeout=40, what="first draft held")
    assert env.b.js("document.querySelector('#work').hidden") is True

    # Hidden page: no poll, so the release waits while kuhy edits the subject after it.
    env.b.js(HIDE)
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    assert "md" in grant(env, hold["requests"][0]["id"])
    released = env.api(f"/api/drafts/{did}")
    assert released["meta"]["phase"] == "editing" and "(assets/shot-1-" in released["text"]
    env.b.click("#subject")
    env.b.type("Moj temat")
    env.b.wait(
        f"fetch('/api/drafts/{did}').then(r => r.json()).then(s => s.meta.subject === 'Moj temat')",
        what="subject saved",
    )
    env.b.js(SHOW)
    env.b.wait("!document.querySelector('#work').hidden", what="first draft shown")
    env.b.wait("!document.body.dataset.held", what="hold cleared")
    assert "(assets/shot-1-" in env.doc()
    env.b.click("#subject")
    env.b.type("!")
    state = env.saved(did)
    assert state["text"] == released["text"] and state["meta"]["subject"] == "Moj temat!", state
