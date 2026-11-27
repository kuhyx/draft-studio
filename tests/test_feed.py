"""Multi-tab consistency: version-checked saves (409 on a stale base) and the change feed."""

from __future__ import annotations

import http.client
import json
import threading
from typing import Any

import pytest

from draft_studio.feed import Feed, stream
from draft_studio.store import StaleBase, Store
from tests.conftest import App


def test_save_with_current_base_applies(store: Store) -> None:
    did = store.create("text")["id"]
    state = store.save(did, {"text": "a"}, base=0)
    assert state["meta"]["version"] == 1
    assert store.save(did, {"text": "ab"}, base=1)["text"] == "ab"


def test_save_with_stale_base_refuses_and_keeps_text(store: Store) -> None:
    did = store.create("text")["id"]
    store.save(did, {"text": "tab A"}, base=0)
    with pytest.raises(StaleBase) as err:
        store.save(did, {"text": "tab B"}, base=0)
    assert err.value.state["text"] == "tab A"
    assert err.value.state["meta"]["version"] == 1
    assert store.get(did)["text"] == "tab A"


def test_save_without_base_stays_last_write_wins(store: Store) -> None:
    """post_draft.py (draft-review skill) PUTs without a base: it must keep working."""
    did = store.create("text")["id"]
    store.save(did, {"text": "one"})
    assert store.save(did, {"text": "two"})["text"] == "two"


def test_put_stale_base_answers_409_with_current_state(app: App) -> None:
    did = app.call("POST", "/api/drafts", {"kind": "text"})[1]["id"]
    assert app.call("PUT", f"/api/drafts/{did}", {"text": "A", "base": 0})[0] == 200
    status, body = app.call("PUT", f"/api/drafts/{did}", {"text": "B", "base": 0})
    assert status == 409
    assert body["error"] == "stale"
    assert body["state"]["text"] == "A"
    assert body["state"]["meta"]["version"] == 1


@pytest.mark.parametrize("base", ["1", 1.5, True, -1])
def test_put_bad_base_is_400(app: App, base: Any) -> None:
    did = app.call("POST", "/api/drafts", {"kind": "text"})[1]["id"]
    assert app.call("PUT", f"/api/drafts/{did}", {"text": "A", "base": base})[0] == 400


def test_feed_wait_returns_ids_bumped_after_seq() -> None:
    feed = Feed()
    start = feed.seq
    feed.bump("aaaaaa")
    feed.bump("bbbbbb")
    feed.bump("aaaaaa")
    seq, ids = feed.wait(start, timeout=0.01)
    assert seq == start + 3
    assert ids == ["aaaaaa", "bbbbbb"]


def test_feed_wait_times_out_empty() -> None:
    feed = Feed()
    assert feed.wait(feed.seq, timeout=0.01) == (feed.seq, [])


def test_feed_wait_wakes_on_bump_from_another_thread() -> None:
    feed = Feed()
    timer = threading.Timer(0.05, feed.bump, args=("cccccc",))
    timer.start()
    assert feed.wait(feed.seq, timeout=5)[1] == ["cccccc"]


def test_feed_too_far_behind_says_everything() -> None:
    feed = Feed(keep=2)
    for name in ("aaaaaa", "bbbbbb", "cccccc"):
        feed.bump(name)
    assert feed.wait(0, timeout=0.01)[1] == ["*"]


def test_store_writes_bump_the_feed(store: Store) -> None:
    start = store.feed.seq
    did = store.create("text")["id"]
    store.save(did, {"text": "x"})
    store.update_meta(did, gmailDraftId="g1")
    store.add_ask(did, {"q": "?"})
    store.delete(did)
    _seq, ids = store.feed.wait(start, timeout=0.01)
    assert ids == [did]
    assert store.feed.seq - start == 5


def _open_events(app: App) -> http.client.HTTPResponse:
    port = int(app.base.rsplit(":", 1)[1])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{port}"})
    return conn.getresponse()


def test_events_stream_pushes_draft_ids(app: App) -> None:
    resp = _open_events(app)
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    assert resp.readline() == b"retry: 1000\n"
    assert resp.readline() == b": open\n"
    assert resp.readline() == b"\n"
    did = app.call("POST", "/api/drafts", {"kind": "text"})[1]["id"]
    assert json.loads(resp.readline().removeprefix(b"data: ")) == {"id": did}
    resp.close()


def test_events_stream_heartbeats(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("draft_studio.feed.HEARTBEAT", 0.01)
    resp = _open_events(app)
    for _ in range(3):
        resp.readline()
    assert resp.readline() == b": hb\n"
    resp.close()


def test_events_refuses_foreign_origin(app: App) -> None:
    port = int(app.base.rsplit(":", 1)[1])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/events", headers={"Origin": "http://evil.test"})
    assert conn.getresponse().status == 403


class _GoneTab:
    """A handler whose client left: the first event write raises BrokenPipe."""

    close_connection = False

    def send_response(self, code: int) -> None:
        self.code = code

    def send_header(self, keyword: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass

    def _write(self, data: bytes) -> None:
        if not data.startswith(b"retry"):
            raise BrokenPipeError


def test_stream_ends_when_the_tab_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("draft_studio.feed.HEARTBEAT", 0.01)
    tab = _GoneTab()
    stream(tab, Feed())
    assert tab.close_connection and tab.code == 200
