"""More pages for one headless browser: a `Tab` is a second target in the same profile."""

from __future__ import annotations

import json
import urllib.request

import websocket

from tests.e2e.cdp import Browser
from tests.e2e.launch import TRACK_TIMERS


class Tab(Browser):
    """Another tab of `parent`'s browser (shares its cookies, locks and BroadcastChannel)."""

    def __init__(self, parent: Browser) -> None:
        self.port, self.proc, self.profile = parent.port, parent.proc, parent.profile
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=5) as r:
            target = json.loads(r.read())
        self.target = str(target["id"])
        self.ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], suppress_origin=True, timeout=30
        )
        self._id = 0
        self.events = []
        self.call("Runtime.enable")
        self.call("Page.enable")
        self.call("Page.addScriptToEvaluateOnNewDocument", source=TRACK_TIMERS)

    def close(self) -> None:
        """Close this tab only; the browser stays."""
        self.ws.close()
        urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{self.target}", timeout=5).close()
