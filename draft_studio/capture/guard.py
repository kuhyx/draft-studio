"""The network guard every target shares: what may leave chromium, and what is still on its way.

Fetch.enable pauses every request at the Request stage, before anything is
sent: once per attached target (pages, popups, frames, workers; a dedicated
worker rides on its frame's, arm.py) and once more
browser-wide (arm.open_page), which still holds a request after its tab's
session is gone (a goodbye beacon of a tab being closed). The guard continues
GET/HEAD and fails everything else with BlockedByClient, except the CORS
preflight of a read (preflight.py). While a click is being watched, a refused
request is reported back (and becomes a permission request); outside a click
(page load, waits, shutdown) it is failed and only logged. A granted step lists
exact (method, url) pairs, each let through once; the same request paused again
by the second interception layer keeps its first verdict. A granted write is
journaled right before it is continued (spent.py), so a decision re-run after a
restart never sends it a second time.

Chromium announces a request (Network.requestWillBeSent) as soon as the page
starts it, but a starved CPU can pause it much later. A write announced and not
yet paused, failed or finished is `pending`: page.py does not call a click
settled while one is, and a click that ends with one still pending reports it
as refused (fail closed), never as a success.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from draft_studio.capture import errors, preflight, spent
from draft_studio.capture.cdp import Cdp
from draft_studio.capture.runner import Request

LOG = logging.getLogger("draft_studio")
Json = dict[str, Any]
SAFE_METHODS = ("GET", "HEAD")


def _request(params: Json) -> Request:
    return str(params["request"]["method"]).upper(), str(params["request"]["url"])


class Guard:
    """Answers Fetch.requestPaused for every session and tracks writes still on their way."""

    def __init__(self, cdp: Cdp, clock: Callable[[], float]) -> None:
        self.cdp, self.clock = cdp, clock
        self.watching = False
        self.allowed: list[Request] = []
        self.blocked: list[Request] = []
        self.announces: dict[Request, str] = {}  # a blocked preflight -> the write it announces (the card)
        self.background: list[Request] = []
        self.last = 0.0
        self.pending: dict[str, tuple[str, Request]] = {}  # networkId -> (session, request)
        self.verdicts: dict[tuple[str, str, str], bool] = {}  # (networkId, method, url) -> continued
        cdp.on("Fetch.requestPaused", self.handle)
        cdp.on("Network.requestWillBeSent", self.announced)
        cdp.on("Network.loadingFailed", self.settled)
        cdp.on("Network.loadingFinished", self.settled)

    def handle(self, msg: Json) -> None:
        """Continue a safe request or an allowed one (once); fail the rest before they leave."""
        params, session = msg["params"], msg.get("sessionId")
        method, url = key = _request(params)
        self.last = self.clock()
        rid, network = params["requestId"], str(params.get("networkId") or "")
        self.pending.pop(network, None)
        seen = (network, method, url)
        if network and seen in self.verdicts:
            ok = self.verdicts[seen]
        else:
            headers = params["request"].get("headers") or {}
            safe = method in SAFE_METHODS or preflight.is_read_preflight(method, headers)
            ok = safe or (self.watching and key in self.allowed)
            if ok and not safe:
                self.allowed.remove(key)
                self._journal(session, rid, key)  # before it leaves: a resumed re-run never sends it again
                LOG.info("capture: granted %s %s", method, url)
            elif not ok:
                (self.blocked if self.watching else self.background).append(key)
                self._announces(key, headers)
                LOG.info("capture: blocked %s %s", method, url)
            if network and method not in SAFE_METHODS:
                self.verdicts[seen] = ok
        if ok:
            self.cdp.send("Fetch.continueRequest", session, requestId=rid)
        else:
            self.cdp.send("Fetch.failRequest", session, requestId=rid, errorReason="BlockedByClient")

    def _announces(self, key: Request, headers: Json) -> None:
        """A refused preflight: keep the write it announces, so the card can say what it was for."""
        write = preflight.announced(headers) if key[0] == "OPTIONS" else None
        if write and self.watching:
            self.announces.setdefault(key, write)  # a repeat announcing another write: the first wins

    def _journal(self, session: str | None, rid: str, key: Request) -> None:
        """spent.py: record the granted write durably, or fail it (nothing leaves unjournaled)."""
        try:
            spent.note(key)
        except OSError as exc:
            self.cdp.send("Fetch.failRequest", session, requestId=rid, errorReason="BlockedByClient")
            raise errors.Unjournaled(str(exc)) from exc

    def announced(self, msg: Json) -> None:
        """The page started a request: a write stays pending until it pauses, fails or finishes."""
        params = msg["params"]
        method, url = request = _request(params)
        network = str(params["requestId"])
        if method in SAFE_METHODS or (network, method, url) in self.verdicts:  # paused already
            return
        self.last = self.clock()
        self.pending[network] = (str(msg.get("sessionId") or ""), request)
        # A preflight that never pauses is reported as refused too: keep what it announces.
        self._announces(request, params["request"].get("headers") or {})

    def settled(self, msg: Json) -> None:
        """Network.loadingFailed / loadingFinished: that request is no longer on its way."""
        self.pending.pop(str(msg["params"]["requestId"]), None)

    def forget(self, session: str) -> None:
        """A tab went away: nothing more will be heard about its requests."""
        self.pending = {k: v for k, v in self.pending.items() if v[0] != session}

    def begin(self, allowed: list[Request]) -> None:
        """Start watching a click."""
        self.watching, self.allowed, self.blocked, self.announces = True, list(allowed), [], {}

    def end(self) -> list[Request]:
        """Stop watching; what the click tried to send and was refused, or is still sending."""
        blocked = self.blocked + [request for _, request in self.pending.values()]
        self.blocked, self.watching, self.allowed, self.pending = [], False, [], {}
        return blocked
