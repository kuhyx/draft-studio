"""The permission card on a real capture: a held request shows its card (shot with the refused button
outlined, reason glyph, METHOD url), hovering the card lights the held span, the shot opens large,
and a click on allow releases the proposal with the real screenshot.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_permcard.py
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.capture_live.conftest import Site, serve_site
from tests.e2e.cardenv import held_draft, shoot, visible_text_ok
from tests.e2e.env import Env

pytestmark = pytest.mark.e2e

CARD = "#held-desk .held"


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    with serve_site() as found:
        yield found


def test_allow_on_the_card_releases_the_proposal_with_its_screenshot(env: Env, site: Site) -> None:
    sent = site.writes["POST /api/save"]
    did = held_draft(env, f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”.")
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    (req,) = hold["requests"]
    assert env.b.js(f"document.querySelector('{CARD}').dataset.held") == hold["id"]
    env.b.wait(f"document.querySelector('{CARD} .req img')?.naturalWidth > 0", what="shot loaded")
    assert env.b.js(f"document.querySelector('{CARD} .reason').dataset.label") == "reasonRequest"
    assert env.b.js(f"document.querySelector('{CARD} .detail').textContent") == f"POST {site.base}/api/save"
    assert env.b.js("document.querySelector('.held-span')?.dataset.held") == hold["id"]
    assert env.b.js("document.querySelector('.held-glyph svg') !== null")
    env.b.hover(CARD)
    env.b.wait("document.querySelector('.held-span.link') !== null", what="span lit")
    visible_text_ok(env)
    shoot(env, "request")

    env.b.click(f"{CARD} .shot")
    env.b.wait("document.querySelector('dialog.shot-view')?.open", what="large view")
    assert env.b.js("document.querySelector('dialog.shot-view img').getAttribute('src')") == req["shot"]
    shoot(env, "zoom")
    env.b.click("dialog.shot-view [data-act=close]")
    env.b.wait("!document.querySelector('dialog.shot-view').open", what="large view closed")

    env.b.click(f"{CARD} .grant")
    env.b.wait("document.querySelector('.cm-hunk') !== null", timeout=40, what="released proposal shown")
    assert site.writes["POST /api/save"] == sent + 1
    hunks = str(env.b.js("[...document.querySelectorAll('.cm-ins')].map(e => e.textContent).join('|')"))
    assert "(assets/shot-1-" in hunks, hunks
    env.b.wait("!document.body.dataset.held && document.querySelector('#held-desk').hidden", what="card gone")
    assert env.b.js("document.querySelector('.held-span')") is None
