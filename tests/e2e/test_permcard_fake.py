"""The permission card's deny, login and busy rows on the real page, against an in-process server whose
capture browser and login window are fakes (cardenv.py): no real login window ever opens.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_permcard_fake.py
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from draft_studio.capture import errors
from tests.e2e.cardenv import BigShot, CardEnv, card_env, held_draft, login_tab, shoot, visible_text_ok
from tests.test_held import EFFECTS, SAVE

pytestmark = pytest.mark.e2e

CARD = "#held-desk .held"
RELEASED = "!document.body.dataset.held && document.querySelector('#held-desk').hidden"


@pytest.fixture(scope="module")
def card() -> Iterator[CardEnv]:
    with card_env() as found:
        yield found


def test_deny_drops_the_image_line(card: CardEnv) -> None:
    card.launcher.make = lambda: BigShot(["Zapisz"], EFFECTS)
    env = card.env
    held_draft(env, SAVE)
    env.b.wait(f"document.querySelector('{CARD} .req img')?.naturalWidth > 0", what="shot loaded")
    env.b.click(f"{CARD} .deny")
    env.b.wait(RELEASED, timeout=15, what="answer released")
    assert "(assets/" not in env.doc()
    assert env.b.js("document.querySelector('.held-span')") is None


def test_login_button_opens_the_fake_window_and_its_close_releases(card: CardEnv) -> None:
    card.launcher.make = login_tab
    env = card.env
    did = held_draft(env, SAVE)
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    (origin,) = hold["login"]
    assert env.b.js(f"document.querySelector('{CARD} .login .origin').textContent") == "panel.example"
    assert env.b.js(f"document.querySelector('{CARD} .login button').dataset.label") == "login"
    visible_text_ok(env)
    shoot(env, "login")
    env.b.click(f"{CARD} .login button")
    env.b.wait(f"document.querySelector('{CARD} .login button').disabled", what="login spinning")
    assert card.spawn.calls[-1][0] == origin
    shoot(env, "login-waiting")
    card.launcher.make = lambda: BigShot(["Zapisz"])  # kuhy logged in
    card.spawn.procs[-1].closed.set()
    env.b.wait("document.querySelector('.cm-hunk') !== null", timeout=15, what="released proposal shown")
    env.b.wait(RELEASED, what="card gone")
    assert "(assets/shot-1-" in env.doc() or "(assets/shot-1-" in str(
        env.b.js("[...document.querySelectorAll('.cm-ins')].map(e => e.textContent).join('|')")
    )


def test_busy_row_retries(card: CardEnv) -> None:
    card.launcher.make = errors.ProfileBusy  # a login window holds the capture profile
    env = card.env
    held_draft(env, SAVE)
    assert env.b.js(f"document.querySelector('{CARD} .busy .reason').dataset.label") == "heldBusy"
    visible_text_ok(env)
    shoot(env, "busy")
    card.launcher.make = lambda: BigShot(["Zapisz"])
    env.b.click(f"{CARD} .busy button")
    env.b.wait("document.querySelector('.cm-hunk') !== null", timeout=15, what="released proposal shown")
    env.b.wait(RELEASED, what="card gone")
