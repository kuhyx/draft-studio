"""Change feed: which drafts changed, pushed to open editor tabs as server-sent events.

One bump per draft write (storebase.StoreBase._put_meta). A tab only learns the id
and fetches the draft itself, so an event never carries text. Each browser holds
one stream for all its tabs (web/src/lib/feed.ts): six streams would take every
HTTP/1.1 connection Chrome allows per host and starve autosave.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Protocol

# Seconds between keep-alive comments: a closed tab raises BrokenPipe on the next one.
HEARTBEAT = 10.0
_KEEP = 1000


class Feed:
    """A counter plus the last `keep` bumped ids; waiters sleep on a Condition."""

    def __init__(self, keep: int = _KEEP) -> None:
        self._cond = threading.Condition()
        self._log: deque[tuple[int, str]] = deque(maxlen=keep)
        self.seq = 0

    def bump(self, draft_id: str) -> None:
        """Record that a draft changed and wake every stream."""
        with self._cond:
            self.seq += 1
            self._log.append((self.seq, draft_id))
            self._cond.notify_all()

    def wait(self, since: int, timeout: float) -> tuple[int, list[str]]:
        """Ids bumped after `since` (each once, in order), waiting up to `timeout` for one.

        ["*"] when the log no longer reaches back to `since`: the tab refetches its draft.
        """
        with self._cond:
            self._cond.wait_for(lambda: self.seq > since, timeout)
            if self.seq == since:
                return since, []
            if self._log[0][0] > since + 1:
                return self.seq, ["*"]
            ids = [draft_id for seq, draft_id in self._log if seq > since]
            return self.seq, list(dict.fromkeys(ids))


class _Handler(Protocol):
    close_connection: bool

    def send_response(self, code: int) -> None: ...
    def send_header(self, keyword: str, value: str) -> None: ...
    def end_headers(self) -> None: ...
    def _write(self, data: bytes) -> None: ...


def stream(handler: _Handler, feed: Feed) -> None:
    """GET /api/events: one `data: {"id": ...}` line per changed draft until the client leaves.

    No Content-Length, so the connection closes when the stream ends.
    """
    handler.close_connection = True
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        # EventSource reconnects after `retry` ms; on each (re)open the tab refetches.
        handler._write(b"retry: 1000\n: open\n\n")
        seq = feed.seq
        while True:  # daemon thread: ends when the tab goes (BrokenPipe on the next write)
            seq, ids = feed.wait(seq, HEARTBEAT)
            chunk = "".join(f"data: {json.dumps({'id': i})}\n\n" for i in ids) or ": hb\n\n"
            handler._write(chunk.encode())
    except (BrokenPipeError, ConnectionResetError):
        return
