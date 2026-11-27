"""Per-request grant on the permission card (kuhy approved the two-buttons-per-row mockup, 2026-11-27):
a card blocking three writes shows a row each; send stays disabled until every row is decided, and
sends a partial grant holding exactly the allowed pair; the proposal then lands with its shot.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_grant_rows.py
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.e2e.cardenv import BigShot, CardEnv, card_env, held_draft, shoot, visible_text_ok
from tests.test_held import SAVE, URL

pytestmark = pytest.mark.e2e

CARD = "#held-desk .held"
PAIRS = [("POST", f"{URL}api/impersonate"), ("POST", f"{URL}chat/messages"), ("POST", f"{URL}collect")]
THREE = {"Zapisz": PAIRS}
SEND = f"document.querySelector('{CARD} [data-act=send]')"
# Every /grant and /deny body the page posts, in order (the page's own fetch, wrapped).
SPY = """(() => {
  window.__grants = []
  const real = window.fetch
  window.fetch = (url, init) => {
    if (/\\/api\\/captures\\/[^/]+\\/(grant|deny)$/.test(String(url))) window.__grants.push([String(url), init?.body ?? null])
    return real(url, init)
  }
})()"""


@pytest.fixture(scope="module")
def card() -> Iterator[CardEnv]:
    with card_env() as found:
        yield found


def _pick(card: CardEnv, i: int, side: str) -> None:
    card.env.b.click(f"{CARD} .pair[data-i='{i}'] [data-act=pick-{side}]")


def test_allow_one_deny_two_sends_exactly_the_allowed_pair(card: CardEnv) -> None:
    card.launcher.make = lambda: BigShot(["Zapisz"], THREE)
    env = card.env
    did = held_draft(env, SAVE)
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    (req,) = hold["requests"]
    assert [tuple(p) for p in req["blocked"]] == PAIRS
    env.b.wait(f"document.querySelectorAll('{CARD} .pair').length === 3", what="three rows")
    env.b.wait(f"document.querySelector('{CARD} .req img')?.naturalWidth > 0", what="shot loaded")
    assert env.b.js(f"{SEND}.disabled") is True
    env.b.js(SPY)

    _pick(card, 1, "allow")
    _pick(card, 0, "deny")
    assert env.b.js(f"{SEND}.disabled") is True  # one row still undecided: no silent default
    env.b.click(f"{CARD} [data-act=send]")
    assert env.b.js("window.__grants.length") == 0
    env.b.click(f"{CARD} [data-act=deny-rest]")
    dots = env.b.js(f"[...document.querySelectorAll('{CARD} .dots i')].map(i => i.className)")
    assert dots == ["d", "a", "d"]
    assert env.b.js(f"{SEND}.disabled") is False
    visible_text_ok(env)
    shoot(env, "grant-rows")

    env.b.click(f"{CARD} [data-act=send]")
    env.b.wait("window.__grants.length === 1", what="grant posted")
    ((url, body),) = env.b.js("window.__grants")
    assert url == f"/api/captures/{req['id']}/grant"
    assert json.loads(body) == {"allow": [list(PAIRS[1])]}
    env.b.wait("document.querySelector('.cm-hunk') !== null", timeout=15, what="released proposal shown")
    env.b.wait("!document.body.dataset.held && document.querySelector('#held-desk').hidden", what="card gone")
    hunks = str(env.b.js("[...document.querySelectorAll('.cm-ins')].map(e => e.textContent).join('|')"))
    assert "(assets/shot-1-" in hunks, hunks
