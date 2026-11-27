"""kuhy's walkthrough from the editor: the change asked on his guide holds on ONE card (the
impersonation POST on the shares page); one allow lands the proposal with every screenshot.

Fake Claude `[walk]` (claude_fake.walk_request), real headless chromium against the loopback
mimic of kuhy's cyber_Folks flow (tests/capture_live/site/walk).
Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_walk_card.py
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.capture_live.conftest import Site, serve_site
from tests.e2e.cardenv import held_draft
from tests.e2e.env import Env

pytestmark = pytest.mark.e2e

CARD = "#held-desk .held"
ROW = "GABINET PSYCHOLOGICZNY ANNA LEŚNIAK-JARECKA"


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    with serve_site() as found:
        yield found


def test_one_allow_on_the_card_lands_every_shot_of_the_walk(env: Env, site: Site) -> None:
    seed = str(env.post("/api/drafts", {"kind": "text"})["id"])  # kuhy's login on the mimic
    spec = {"url": f"{site.base}/session.html?start", "name": "seed", "steps": [{"wait": "Sesja aktywna"}]}
    assert "asset" in env.post(f"/api/drafts/{seed}/capture", spec)
    guide = (
        f"1. Otworzyć {site.base}/walk/login.html.\n2. Kliknąć „Domeny” w „Twoje usługi”.\n"
        "3. Kliknąć ⋮ w „kaliszpsycholog.pl”.\n4. Kliknąć „Odnów”."
    )
    did = held_draft(env, guide, f"[walk] {site.base}/walk/shares.html „ZALOGUJ JAKO” w „{ROW}”")
    (hold,) = env.api(f"/api/drafts/{did}/held")["held"]
    (req,) = hold["requests"]
    assert req["detail"] == f"POST {site.base}/api/impersonate?id=08245109"
    assert env.b.js(f"document.querySelectorAll('{CARD}').length") == 1

    env.b.click(f"{CARD} .grant")
    env.b.wait("document.querySelector('.cm-hunk') !== null", timeout=50, what="released proposal shown")
    hunks = str(env.b.js("[...document.querySelectorAll('.cm-ins')].map(e => e.textContent).join('|')"))
    assert [f"(assets/shot-{n}-" in hunks for n in range(1, 5)] == [True] * 4, hunks
    assert "IMG" not in hunks and "ZALOGUJ" not in hunks
    env.b.wait("!document.body.dataset.held && document.querySelector('#held-desk').hidden", what="card gone")
    assert site.writes["POST /api/impersonate?id=08245109"] == 1
    assert site.writes["POST /api/end-share"] == 0 and site.writes["POST /api/renew"] == 0
