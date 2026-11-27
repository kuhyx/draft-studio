"""One draft open in several places (kuhy, 2026-11-27): every tab shows the same text.

Tabs of one browser share one event stream (web/src/lib/feed.ts); a second browser
(another profile) is a second place with its own stream.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from tests.e2e.cdp import Browser
from tests.e2e.env import Env
from tests.e2e.tabs import Tab

pytestmark = pytest.mark.e2e

DOC = "draftStudio.view.state.doc.toString()"


def _draft(env: Env, text: str) -> str:
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}", {"text": text, "phase": "editing"}, method="PUT")
    return did


def _open(page: Browser, env: Env, did: str) -> None:
    page.goto(env.base + f"/d/?id={did}")


def _type_at(page: Browser, pos: int, text: str) -> None:
    page.call("Page.bringToFront")  # input goes to the front tab of a headless browser
    page.js(f"draftStudio.view.focus(); draftStudio.view.dispatch({{selection: {{anchor: {pos}}}}})")
    page.type(text)


def _shows(page: Browser, *needles: str, timeout: float = 5.0) -> str:
    cond = " && ".join(f"{DOC}.includes({n!r})" for n in needles)
    page.wait(cond, timeout=timeout, what=f"tab shows {needles}")
    return str(page.js(DOC))


@pytest.fixture
def tab(env: Env) -> Iterator[Tab]:
    t = Tab(env.b)
    yield t
    assert t.errors() == [], t.errors()
    t.close()


def test_typing_in_one_tab_shows_in_the_other(env: Env, tab: Tab) -> None:
    did = _draft(env, "Dear Ann,\n\nsee you soon.")
    _open(env.b, env, did)
    _open(tab, env, did)
    _type_at(env.b, len("Dear Ann,"), " hello")
    start = time.monotonic()
    assert _shows(tab, "Dear Ann, hello") == "Dear Ann, hello\n\nsee you soon."
    # The editor waits 400 ms before it saves; the other tab follows within about a second.
    assert time.monotonic() - start < 2.5


def test_edits_made_at_once_in_two_tabs_both_survive(env: Env, tab: Tab) -> None:
    did = _draft(env, "one two three")
    _open(env.b, env, did)
    _open(tab, env, did)
    # Both type before either autosave (400 ms) fires: one save is stale and rebases.
    _type_at(env.b, 0, "AAA ")
    _type_at(tab, len("one two three"), " BBB")
    want = "AAA one two three BBB"
    assert _shows(env.b, "AAA", "BBB") == want
    assert _shows(tab, "AAA", "BBB") == want
    env.b.wait("document.querySelector('#status').dataset.status === 'saved'", what="A saved")
    tab.wait("document.querySelector('#status').dataset.status === 'saved'", what="B saved")
    assert env.api(f"/api/drafts/{did}")["text"] == want


def test_seven_tabs_of_one_browser_all_follow(env: Env) -> None:
    """Seven event streams would take all six HTTP/1.1 connections Chrome allows: one tab holds it."""
    did = _draft(env, "start")
    tabs = [Tab(env.b) for _ in range(6)]
    try:
        _open(env.b, env, did)
        for t in tabs:
            _open(t, env, did)
        _type_at(tabs[-1], len("start"), " from seven")
        for page in [env.b, *tabs]:
            assert _shows(page, "start from seven") == "start from seven"
        assert env.api(f"/api/drafts/{did}")["text"] == "start from seven"
    finally:
        for t in tabs:
            t.close()


def test_offline_tab_keeps_its_edit_and_merges_when_back(env: Env) -> None:
    """Another browser loses the server, types, and syncs through the same rebase when it is back."""
    did = _draft(env, "middle")
    other = Browser()
    try:
        _open(env.b, env, did)
        _open(other, env, did)
        other.call("Network.enable")
        other.call(
            "Network.emulateNetworkConditions",
            offline=True,
            latency=0,
            downloadThroughput=-1,
            uploadThroughput=-1,
        )
        _type_at(other, 0, "OFF ")
        other.wait("document.querySelector('#status').dataset.status === 'error'", what="save failed offline")
        _type_at(env.b, len("middle"), " ON")
        env.b.wait("document.querySelector('#status').dataset.status === 'saved'", what="online tab saved")
        other.call(
            "Network.emulateNetworkConditions",
            offline=False,
            latency=0,
            downloadThroughput=-1,
            uploadThroughput=-1,
        )
        assert _shows(other, "OFF", "ON", timeout=10) == "OFF middle ON"
        assert _shows(env.b, "OFF", "ON", timeout=10) == "OFF middle ON"
        assert env.api(f"/api/drafts/{did}")["text"] == "OFF middle ON"
    finally:
        other.close()


def test_a_tab_keeps_unsent_answers_and_focus_when_saves_echo(env: Env) -> None:
    did = str(env.post("/api/drafts", {"kind": "mail"})["id"])
    env.post(
        f"/api/drafts/{did}", {"intake": [{"q": "Who?", "a": None, "round": 0}], "phase": "intake"}, "PUT"
    )
    _open(env.b, env, did)
    env.b.click("#questions textarea")
    env.b.type("my answer")
    env.b.click("#to")
    env.b.type("ann@example.com")
    env.b.wait(
        f"fetch('/api/drafts/{did}').then(r => r.json()).then(s => s.meta.to === 'ann@example.com')",
        what="to saved",
    )
    time.sleep(1.5)  # the save's own echo and the stream's open have come back by now
    assert env.b.js("document.querySelector('#questions textarea').value") == "my answer"
    assert env.b.js("document.activeElement.id") == "to"


def test_an_edit_in_another_tab_drops_a_pending_proposal_with_the_cross(env: Env, tab: Tab) -> None:
    did = _draft(env, "Warm and short greetings.")
    _open(env.b, env, did)
    _open(tab, env, did)
    env.b.call("Page.bringToFront")  # the selection toolbar waits on animation frames: front tab only
    env.propose("Warm and short")
    _type_at(tab, 2, "X")
    env.b.call("Page.bringToFront")
    env.b.wait("document.querySelector('[data-signal=gone]') !== null", what="cross pulse")
    env.b.wait("document.querySelector('.cm-hunk') === null", what="proposal dropped")
    assert _shows(env.b, "WaXrm") == "WaXrm and short greetings."


def test_the_stream_passes_to_another_tab_when_its_holder_leaves(env: Env, tab: Tab) -> None:
    did = _draft(env, "lead")
    _open(env.b, env, did)  # first opener: holds the lock and the stream
    _open(tab, env, did)
    env.b.goto(env.base + "/")
    env.post(f"/api/drafts/{did}", {"text": "lead changed"}, method="PUT")
    assert _shows(tab, "lead changed") == "lead changed"
