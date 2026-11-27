"""Unsaved edits outlive the page (offline-first): IndexedDB keeps them, a reopened draft replays them.

Most tests kill the server process for real: the service worker (web/public/sw.js) then serves the
page from its cache, the editor opens on the state its unsaved record holds, and the edits save once
the server is back. Two tests make the page's own save replies vanish instead (`dsHold`: a save reaches
the server, its reply never reaches the page, like a tab closed mid-save; `dsMute`: GETs fail, so the
tab never hears of another writer).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.e2e.cdp import Browser
from tests.e2e.env import Env
from tests.e2e.tabs import Tab

pytestmark = pytest.mark.e2e

NET = """
(() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = ((init && init.method) || 'GET').toUpperCase();
    const s = sessionStorage;
    if (s.dsMute && method === 'GET' && url.includes('/api/drafts/')) {
      return Promise.reject(new TypeError('Failed to fetch'));
    }
    if (s.dsHold && method === 'PUT') {
      void real(input, init).catch(() => undefined);
      return new Promise(() => undefined);
    }
    return real(input, init);
  };
})();
"""

RECORDS = """new Promise((ok, no) => {
  const r = indexedDB.open('draft-studio', 1);
  r.onerror = () => no(r.error);
  r.onsuccess = () => {
    const q = r.result.transaction('unsaved').objectStore('unsaved').getAll();
    q.onsuccess = () => ok(q.result);
  };
})"""

SAVED = "document.querySelector('#status').dataset.status === 'saved'"
WARM = """navigator.serviceWorker.ready.then(async () => {
  for (const k of await caches.keys()) {
    if (await (await caches.open(k)).match(location.origin + '/d/')) return navigator.serviceWorker.controller !== null;
  }
  return false;
})"""
CACHES = """caches.keys().then(async (ks) => {
  const out = [];
  for (const k of ks) out.push([k, (await (await caches.open(k)).keys()).map((r) => new URL(r.url).pathname)]);
  return out;
})"""


@pytest.fixture
def net(env: Env) -> Iterator[Browser]:
    script = env.b.call("Page.addScriptToEvaluateOnNewDocument", source=NET)["identifier"]
    yield env.b
    env.b.js("try { sessionStorage.clear() } catch {}")  # the test may end on about:blank
    env.b.call("Page.removeScriptToEvaluateOnNewDocument", identifier=script)


@pytest.fixture
def down(env: Env) -> Iterator[Env]:
    """The test may kill the server; it is back (build `e2e`) at the end whatever happens."""
    yield env
    if env.server is not None and env.server.proc is None:
        env.server.start()


def _draft(env: Env, text: str, kind: str = "text") -> str:
    did = str(env.post("/api/drafts", {"kind": kind})["id"])
    env.post(f"/api/drafts/{did}", {"text": text, "phase": "editing"}, method="PUT")
    return did


def _open(page: Browser, env: Env, did: str) -> None:
    page.goto(env.base + f"/d/?id={did}")
    page.wait(WARM, what="page cached by the service worker")


def _type_at(page: Browser, pos: int, text: str) -> None:
    page.call("Page.bringToFront")
    page.js(f"draftStudio.view.focus(); draftStudio.view.dispatch({{selection: {{anchor: {pos}}}}})")
    page.type(text)


def _records(page: Browser, cond: str) -> None:
    page.wait(f"({RECORDS}).then((r) => {cond})", what=f"records where {cond}")


def _text(env: Env, did: str) -> str:
    return str(env.api(f"/api/drafts/{did}")["text"])


def _stop(env: Env) -> None:
    assert env.server is not None
    env.server.stop()


def _start(env: Env, build: str = "e2e") -> None:
    assert env.server is not None
    env.server.start(build)


def _leave(page: Browser) -> None:
    """Close the page the way a closed tab does: no more answers, the lock goes with the document."""
    page.js("sessionStorage.clear()")
    page.call("Page.navigate", url="about:blank")
    page.wait("location.href === 'about:blank'", what="left the page")


def test_edits_typed_with_the_server_dead_survive_a_reload_and_save_when_it_is_back(down: Env) -> None:
    env = down
    did = _draft(env, "alpha beta", kind="mail")
    _open(env.b, env, did)
    _stop(env)
    _type_at(env.b, len("alpha beta"), " ONE")
    env.b.click("#to")
    env.b.type("ann@example.com")
    env.b.click("#subject")
    env.b.type("Hello")
    env.b.wait("document.querySelector('#status').dataset.status === 'error'", what="save failed")
    _records(
        env.b,
        "r.some((x) => x.text === 'alpha beta ONE' && x.to === 'ann@example.com' && x.subject === 'Hello')",
    )
    env.b.goto(env.base + f"/d/?id={did}")  # reload with nothing answering
    assert env.doc() == "alpha beta ONE"
    fields = env.b.js("document.querySelector('#to').value + '|' + document.querySelector('#subject').value")
    assert fields == "ann@example.com|Hello"
    _type_at(env.b, 0, "NEW ")  # still offline, after the reload
    _start(env)
    env.b.wait(SAVED, timeout=15, what="saved once the server is back")
    state = env.api(f"/api/drafts/{did}")
    assert (state["text"], state["meta"]["to"], state["meta"]["subject"]) == (
        "NEW alpha beta ONE",
        "ann@example.com",
        "Hello",
    )
    _records(env.b, "r.length === 0")
    _leave(env.b)


def test_every_gone_tab_is_replayed_oldest_first_and_none_stays_behind(down: Env) -> None:
    env = down
    did = _draft(env, "one two three")
    _open(env.b, env, did)
    other = Tab(env.b)
    try:
        _open(other, env, did)
        _stop(env)
        _type_at(env.b, 0, "AAA ")
        _records(env.b, "r.length === 1")
        _type_at(other, len("one two three"), " ZZZ")
        _records(env.b, "r.length === 2")
    finally:
        other.close()
    _leave(env.b)
    env.b.goto(env.base + f"/d/?id={did}")
    assert env.doc() == "AAA one two three ZZZ"
    _start(env)
    env.b.wait(SAVED, timeout=15, what="both saved")
    assert _text(env, did) == "AAA one two three ZZZ"
    _records(env.b, "r.length === 0")
    _leave(env.b)


def test_edits_made_while_a_stale_save_is_in_flight_survive_closing_the_tab(env: Env, net: Browser) -> None:
    did = _draft(env, "one two three")
    _open(net, env, did)
    net.js("sessionStorage.dsMute = '1'; sessionStorage.dsHold = '1'")
    env.post(f"/api/drafts/{did}", {"text": "BBB one two three"}, method="PUT")  # another writer first
    _type_at(net, len("one two three"), " X")
    _records(net, "r.some((x) => x.sent && x.sent.endsWith(' X'))")
    _type_at(net, len("one two three X"), "Y")  # typed while the 409 is on its way back
    _records(net, "r.some((x) => x.text.includes('one two three XY'))")
    assert _text(env, did) == "BBB one two three"  # the stale save was refused
    _leave(net)
    _open(net, env, did)
    assert env.doc() == "BBB one two three XY"
    net.wait(SAVED, timeout=15, what="saved")
    assert _text(env, did) == "BBB one two three XY"
    _records(net, "r.length === 0")
    _leave(net)


def test_a_save_that_landed_before_the_tab_closed_is_not_replayed_twice(env: Env, net: Browser) -> None:
    did = _draft(env, "one two three")
    _open(net, env, did)
    net.js("sessionStorage.dsHold = '1'")
    _type_at(net, len("one two three"), " X")
    net.wait(
        f"fetch('/api/drafts/{did}').then(r => r.json()).then(s => s.text.endsWith(' X'))", what="X landed"
    )
    _type_at(net, len("one two three X"), "Y")
    _records(net, "r.some((x) => x.text.includes('one two three XY'))")
    _leave(net)
    _open(net, env, did)
    net.wait(SAVED, timeout=15, what="saved")
    assert _text(env, did) == "one two three XY"
    assert env.doc() == "one two three XY"
    _leave(net)


def test_the_worker_never_keeps_api_answers_and_follows_a_new_build(down: Env) -> None:
    env = down
    did = _draft(env, "build test")
    _open(env.b, env, did)
    cached = dict(env.b.js(CACHES))
    assert list(cached) == ["ds-e2e"]
    assert not [u for u in cached["ds-e2e"] if u.startswith("/api/") or u == "/version"]
    assert {"/d/", "/"} <= set(cached["ds-e2e"])
    _stop(env)
    _start(env, build="e3")  # a deploy: the open page learns of it, saves, and reloads
    env.b.wait("document.documentElement.dataset.build === 'e3'", timeout=20, what="page on the new build")
    env.b.wait(
        f"({CACHES}).then((c) => c.length === 1 && c[0][0] === 'ds-e3')", timeout=20, what="old cache gone"
    )
    _leave(env.b)
    _stop(env)
    _start(env)
