"""DevTools protocol session over one WebSocket: calls, fire-and-forget sends, inline events.

Events are dispatched while a call waits for its answer, not queued: a paused
request (Fetch.requestPaused) must be answered before chromium can finish the
navigation or click the call is waiting on. A handler may make its own call
(arming a new target); answers are kept by id, so the outer call still gets
its own. Everything shares one deadline.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from draft_studio.capture import errors
from draft_studio.capture.wsock import WebSocket, WsClosed

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
Handler = Callable[[Json], None]


class Cdp:
    """Browser-level connection; page commands pass `session` (flattened sessions)."""

    def __init__(self, ws: WebSocket, deadline: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ws = ws
        self.deadline = deadline
        self.clock = clock
        self.handlers: dict[str, Handler] = {}
        self._id = 0
        self._waiting: set[int] = set()
        self._answers: dict[int, Json] = {}

    def on(self, method: str, handler: Handler) -> None:
        """Call `handler(message)` for every event named `method`."""
        self.handlers[method] = handler

    def left(self) -> float:
        """Seconds until the deadline; raises CaptureTimeout once it passed."""
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise errors.CaptureTimeout()
        return remaining

    def send(self, method: str, session: str | None = None, **params: Any) -> int:
        """Send without waiting for the answer; returns the message id."""
        self._id += 1
        msg: Json = {"id": self._id, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        try:
            self.ws.send(json.dumps(msg))
        except OSError as exc:
            raise errors.ChromiumFailed(f"send {method}: {exc}") from exc
        return self._id

    def _next(self, timeout: float) -> Json | None:
        """Read and dispatch one message if one arrives within `timeout`."""
        try:
            if not self.ws.readable(min(timeout, self.left())):
                return None
            self.ws.sock.settimeout(self.left())
            msg: Json = json.loads(self.ws.recv())
        except TimeoutError as exc:
            raise errors.CaptureTimeout() from exc
        except (OSError, WsClosed, ValueError) as exc:
            raise errors.ChromiumFailed(f"devtools: {exc}") from exc
        if "id" in msg:
            if msg["id"] in self._waiting:
                self._answers[msg["id"]] = msg
            elif "error" in msg:
                LOG.debug("capture: devtools error for #%s: %s", msg["id"], msg["error"])
        elif msg.get("method") in self.handlers:
            self.handlers[msg["method"]](msg)
        return msg

    def call(self, method: str, session: str | None = None, **params: Any) -> Json:
        """Send and wait for the result, dispatching events meanwhile (handlers may call too)."""
        my = self.send(method, session, **params)
        self._waiting.add(my)
        try:
            while my not in self._answers:
                self._next(self.left())
            msg = self._answers.pop(my)
        finally:
            self._waiting.discard(my)
        if "error" in msg:
            raise errors.ChromiumFailed(f"{method}: {msg['error'].get('message', msg['error'])}")
        result: Json = msg.get("result", {})
        return result

    def pump(self, seconds: float) -> None:
        """Dispatch events for `seconds` (capped by the deadline)."""
        until = self.clock() + seconds
        while (wait := until - self.clock()) > 0:
            self._next(wait)

    def close(self) -> None:
        """Drop the socket."""
        self.ws.close()
