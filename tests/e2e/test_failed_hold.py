"""A proposal whose screenshots all failed is held with a card (never a text-only diff); partial shows a glyph.

kuhy (2026-11-27): the captures failed (no-target) and the answer came as a text-only proposal that also
lost the old images. Fake Claude asks for shots the fake page cannot find; the old images stay.
Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_failed_hold.py
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from draft_studio.claude_fake import FakeClaude
from tests.e2e.cardenv import BigShot, CardEnv, card_env, held_draft, shoot, visible_text_ok
from tests.e2e.env import Env
from tests.test_held import SAVE
from tests.test_shots_failed import reply

pytestmark = pytest.mark.e2e

CARD = "#held-desk .held"
OLD1, OLD2 = "![Jeden](assets/old-1.png)", "![Dwa](assets/old-2.png)"
GUIDE = f"{SAVE}\n\n{OLD1}\n\nKrok dwa.\n\n{OLD2}"


def page() -> BigShot:
    """A page that has „Zapisz” only."""
    return BigShot(["Zapisz"])


@pytest.fixture(scope="module")
def card() -> Iterator[CardEnv]:
    with card_env() as found:
        yield found


def tries(env: Env, did: str) -> int:
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    return int(hold["tries"])


def test_every_shot_failed_holds_a_card_and_the_old_images_stay(
    card: CardEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    card.launcher.make = page
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Nic"), ("b", "Nigdzie")))
    env = card.env
    did = held_draft(env, GUIDE, "popraw")
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    assert [f["reason"] for f in hold["failed"]] == ["no-target", "no-target"] and hold["requests"] == []
    rows = "[...document.querySelectorAll('#held-desk .failed .origin')].map(e => e.textContent)"
    assert env.b.js(rows) == ["a no-target", "b no-target"]
    acts = f"[...document.querySelectorAll('{CARD} .failed-bar button')].map(b => b.dataset.act)"
    assert env.b.js(acts) == ["dismiss", "retry"]
    assert env.b.js("document.querySelector('.cm-hunk')") is None  # no text-only diff
    assert env.b.js("document.querySelector('#status').dataset.status") != "busy"  # nothing runs: no spinner
    assert env.b.js("document.querySelector('.held-glyph svg') !== null")
    visible_text_ok(env)
    shoot(env, "failed")
    assert env.api(f"/api/drafts/{did}")["text"].startswith(
        f"{GUIDE}\n\nKoniec"
    )  # text and both images untouched

    env.b.click(f"{CARD} [data-act='retry']")  # the same page fails again: still held, the try counted
    deadline = time.monotonic() + 10
    while tries(env, did) < 1 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert tries(env, did) == 1 and env.b.js("document.querySelector('.cm-hunk')") is None

    env.b.click(f"{CARD} [data-act='dismiss']")
    env.b.wait("!document.body.dataset.held && document.querySelector('#held-desk').hidden", what="card gone")
    assert (
        env.api(f"/api/drafts/{did}/held")["held"] == []
        and env.b.js("document.querySelector('.held-span')") is None
    )


def test_one_shot_failed_lands_marked_and_the_old_image_of_that_step_stays(
    card: CardEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    card.launcher.make = page
    monkeypatch.setattr(FakeClaude, "rewrite", reply(("a", "Zapisz"), ("b", "Nic")))
    env = card.env
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Poradnik [noq]"})
    env.post(f"/api/drafts/{did}", {"text": GUIDE, "claude": [], "to": "", "subject": ""}, method="PUT")
    env.b.goto(f"{env.base}/d/?id={did}")
    env.select(0, len(GUIDE))
    env.b.click(".sel-toolbar .sel-change")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type("popraw")
    env.b.click(".sel-toolbar .sel-submit")
    env.b.wait("document.querySelector('.cm-missing') !== null", timeout=40, what="partial proposal shown")
    assert env.b.js("document.querySelector('.cm-missing').dataset.missing") == "b"
    assert env.b.js("document.querySelector('.cm-missing svg') !== null")
    (prop,) = env.api(f"/api/drafts/{did}")["proposals"]
    assert "![A](assets/a-" in prop["replacement"] and OLD1 not in prop["replacement"]
    assert OLD2 in prop["replacement"] and prop["missing"] == ["b"]
    assert env.b.js("document.querySelector('#held-desk').hidden")
    visible_text_ok(env)
    shoot(env, "partial")
