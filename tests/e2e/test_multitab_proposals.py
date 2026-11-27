"""One draft open in two tabs (kuhy, 2026-11-27: "a consistent view in all of them"), part 2: proposals,
held answers and permission cards live on the server, so both tabs show the same ones, a release lands once,
and a real server restart changes nothing for either tab.

Run: bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e/test_multitab_proposals.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.capture_live.conftest import Site, serve_site
from tests.e2e.cdp import Browser, free_port
from tests.e2e.env import REPO, Env
from tests.e2e.launch import chromium, scratch_dir
from tests.e2e.tabs import Tab

pytestmark = pytest.mark.e2e

CARD = "#held-desk .held"
LOOK = """JSON.stringify({
  doc: draftStudio.view.state.doc.toString(),
  hunks: [...document.querySelectorAll('.cm-hunk')].map(e => e.dataset.proposal + ':' + e.dataset.hunk),
  cards: [...document.querySelectorAll('#held-desk .held')].map(e => e.dataset.held),
})"""


@dataclass
class Duo:
    """A real server process (restartable on the same data dir and port) and two tabs on one draft."""

    env: Env  # tab A's browser
    b: Tab
    proc: subprocess.Popen[bytes]
    start: Callable[[], subprocess.Popen[bytes]]

    def restart(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.proc = self.start()

    def look(self, page: Browser) -> dict[str, Any]:
        return dict(json.loads(str(page.js(LOOK))))


def _boot(port: int, data: Path, locales: Path) -> subprocess.Popen[bytes]:
    environ = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(data),
        "DRAFT_STUDIO_LOCALES": str(locales),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_JOBS": "inline",
        "DRAFT_STUDIO_GMAIL": "fake",
        "DRAFT_STUDIO_CHROMIUM": chromium(),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "draft_studio", "--port", str(port), "--build", "e2e"], cwd=REPO, env=environ
    )
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/version", timeout=1).close()
            return proc
        except OSError:
            time.sleep(0.1)
    raise AssertionError("server did not start")


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    with serve_site() as found:
        yield found


@pytest.fixture(scope="module")
def duo() -> Iterator[Duo]:
    tmp = scratch_dir("ds-duo-")
    data, locales = tmp / "data", tmp / "locales"
    shutil.copytree(REPO / "locales", locales)
    port = free_port()
    a = Browser()
    found = Duo(
        Env(f"http://127.0.0.1:{port}", data, locales, a, tmp),
        Tab(a),
        _boot(port, data, locales),
        lambda: _boot(port, data, locales),
    )
    try:
        yield found
        assert a.errors() == [], a.errors()
        assert found.b.errors() == [], found.b.errors()
    finally:
        found.b.close()
        a.close()
        found.proc.terminate()
        found.proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


def _draft(env: Env, text: str) -> str:
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Poradnik [noq]"})
    env.post(f"/api/drafts/{did}", {"text": text, "claude": [], "to": "", "subject": ""}, method="PUT")
    return did


def _open_both(duo: Duo, did: str) -> None:
    for page in (duo.env.b, duo.b):
        page.goto(duo.env.base + f"/d/?id={did}")
        page.wait("document.body.dataset.ready === '1'", what="editor ready")


def _same_view(duo: Duo, *, hunks: bool, cards: int, timeout: float = 15.0) -> dict[str, Any]:
    """Both tabs show one and the same text, proposals and permission cards."""
    deadline = time.monotonic() + timeout
    while True:
        a, b = duo.look(duo.env.b), duo.look(duo.b)
        if a == b and bool(a["hunks"]) == hunks and len(a["cards"]) == cards:
            return a
        assert time.monotonic() < deadline, (a, b)
        time.sleep(0.1)


def _ask(duo: Duo, needle: str, instruction: str = "") -> None:
    """Tab A selects `needle` and asks Claude for a change; returns once A is idle again."""
    duo.env.b.call("Page.bringToFront")
    duo.env.select_text(needle)
    if instruction:
        duo.env.b.click(".sel-toolbar .sel-change")
        duo.env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
        duo.env.b.type(instruction)
        duo.env.b.click(".sel-toolbar .sel-submit")
    else:
        duo.env.b.click(".sel-toolbar .sel-rewrite")
    duo.env.idle()


def test_a_proposal_asked_in_a_shows_in_b_and_accepting_in_b_updates_a(duo: Duo) -> None:
    did = _draft(duo.env, "Warm and short greetings.\n\nSecond line stays.")
    _open_both(duo, did)
    _ask(duo, "Warm and short")
    seen = _same_view(duo, hunks=True, cards=0)
    assert seen["doc"].startswith("Warm and short greetings.")  # nothing applied by asking
    duo.b.call("Page.bringToFront")
    duo.b.click(".accept-all")
    after = _same_view(duo, hunks=False, cards=0)
    assert after["doc"] != seen["doc"]
    saved = duo.env.api(f"/api/drafts/{did}")
    assert saved["text"] == after["doc"] and saved["proposals"] == []
    assert saved["claude"], "the accepted text carries Claude's tint"


def test_a_permission_card_shows_in_both_tabs_and_one_answer_clears_both(duo: Duo, site: Site) -> None:
    text = f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”."
    did = _draft(duo.env, f"{text}\n\nKoniec.")
    _open_both(duo, did)
    sent = site.writes["POST /api/save"]
    _ask(duo, text, "[shot]")
    duo.env.b.wait("document.body.dataset.held === '1'", timeout=40, what="A holds the answer")
    _same_view(duo, hunks=False, cards=1)
    duo.b.wait("document.body.dataset.held === '1'", what="B busy like A")
    assert site.writes["POST /api/save"] == sent
    duo.b.call("Page.bringToFront")
    duo.b.wait(f"document.querySelector('{CARD} .req img')?.naturalWidth > 0", what="shot loaded in B")
    duo.b.click(f"{CARD} .grant")
    _same_view(duo, hunks=True, cards=0, timeout=40)
    assert site.writes["POST /api/save"] == sent + 1
    # Released once: exactly one proposal on the server and one set of hunks in each tab.
    assert len(duo.env.api(f"/api/drafts/{did}")["proposals"]) == 1
    for page in (duo.env.b, duo.b):
        assert page.js("document.querySelectorAll('.cm-hunk').length") == 1
        assert "(assets/shot-1-" in str(page.js("document.querySelector('.cm-ins').textContent"))


def test_a_server_restart_changes_nothing_for_either_tab(duo: Duo, site: Site) -> None:
    text = f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”."
    did = _draft(duo.env, f"Warm greetings today.\n\n{text}\n\nKoniec.")
    _open_both(duo, did)
    _ask(duo, "Warm greetings")
    _ask(duo, text, "[shot]")
    duo.env.b.wait("document.body.dataset.held === '1'", timeout=40, what="A holds the answer")
    before = _same_view(duo, hunks=True, cards=1)
    server_before = duo.env.api(f"/api/drafts/{did}")
    duo.restart()
    after = _same_view(duo, hunks=True, cards=1, timeout=30)
    assert after == before
    assert duo.env.api(f"/api/drafts/{did}")["proposals"] == server_before["proposals"]
    # The reloaded server still honours the card: granting it in B lands the answer in both tabs.
    duo.b.call("Page.bringToFront")
    duo.b.wait(f"document.querySelector('{CARD} .req img')?.naturalWidth > 0", what="shot after restart")
    duo.b.click(f"{CARD} .grant")
    done = _same_view(duo, hunks=True, cards=0, timeout=40)
    assert len(done["hunks"]) > len(before["hunks"])
