"""Real headless chromium against a loopback test site that counts every non-GET it receives."""

from __future__ import annotations

import shutil
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture.browser import Chromium
from draft_studio.capture.service import CaptureService
from draft_studio.store import Store
from tests.e2e.launch import chromium, scratch_dir

SITE = Path(__file__).parent / "site"
CAPTURE_TIMEOUT = 40.0  # per chromium run, as the service's own deadline
# What an /already/ shares page is served as once /api/switch ran: menu-owner keeps the side menu
# („Twoje usługi” > „Domeny”) every real panel page shows, so the goal holds there too.
OWNER_VIEW = {
    "/already/shares.html": "/already/owner.html",
    "/already/menu-shares.html": "/already/menu-owner.html",
}


@dataclass
class Site:
    base: str
    writes: Counter[str] = field(default_factory=Counter)
    api: str = ""  # a second loopback origin: pages reach it cross-origin (CORS)
    switched: bool = False  # server-side "logged in as the owner" (/api/switch), kept for the whole run
    gate: threading.Event | None = None  # set by a test: a write waits here (a capture cut in flight)


class _Handler(SimpleHTTPRequestHandler):
    site: Site

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet."""

    def _write(self) -> None:
        self.site.writes[f"{self.command} {self.path}"] += 1
        if self.site.gate is not None:
            self.site.gate.wait(30)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_PUT = do_DELETE = _write

    def end_headers(self) -> None:
        """/already/ pages change with the server-side switch: chromium must not reuse a cached copy."""
        if self.path.startswith("/already/") or self.path == "/":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self) -> None:
        """/auth/login logs in with a session-only cookie (no Max-Age: chromium drops it on exit)."""
        if self.path == "/spa/api/login":  # the single-page panel's fetch login
            self.site.writes["POST /spa/api/login"] += 1
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=ok; Path=/; HttpOnly")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/api/switch"):  # the "log in as" of /already/shares.html
            self.site.switched = True
        if self.path != "/auth/login":
            self._write()
            return
        self.site.writes["POST /auth/login"] += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(303)
        self.send_header("Set-Cookie", "sid=ok; Path=/; HttpOnly")
        self.send_header("Location", "/auth/home")
        self.end_headers()

    def _home(self) -> None:
        """The page behind the login: the cookie, or a redirect to the login page."""
        ok = "sid=ok" in (self.headers.get("Cookie") or "")
        body = b"<h1>Konto</h1>" if ok else b""
        self.send_response(200 if ok else 302)
        self.send_header("Location", "/auth/login.html")
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Static files; /auth/home needs the login cookie; a GET to /beacon/<x> is counted too."""
        if self.path == "/auth/home":
            self._home()
            return
        if self.path == "/spa/api/me":  # the single-page panel asks who is logged in
            self.send_response(200 if "sid=ok" in (self.headers.get("Cookie") or "") else 401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.site.switched and self.path in OWNER_VIEW:
            self.path = OWNER_VIEW[self.path]  # the owner's view has no share row
        if self.site.switched and self.path == "/":  # ...and its root is the dashboard
            self.send_response(302)
            self.send_header("Location", "/walk/dashboard.html")
            self.end_headers()
            return
        if self.path.startswith("/spa/") and not self.path.startswith("/spa/api/"):
            self.path = "/spa/index.html"  # every route of the panel is its one page
        if not self.path.startswith("/beacon/"):
            super().do_GET()
            return
        self.site.writes[f"GET {self.path}"] += 1
        self.send_response(204)
        self.end_headers()


class _Api(BaseHTTPRequestHandler):
    """The cross-origin JSON API: answers preflights, serves GET /data, counts everything it receives."""

    site: Site

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet."""

    def _handle(self) -> None:
        self.site.writes[f"API {self.command} {self.path}"] += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        preflight = self.command == "OPTIONS"
        body = b"" if preflight else b'{"count": 7}'
        self.send_response(204 if preflight else 200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, DELETE")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_OPTIONS = do_POST = _handle


@contextmanager
def serve_site() -> Iterator[Site]:
    """The test site and its cross-origin API on free loopback ports (tests/e2e serve it next to the app)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(SITE)))
    api = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    found = Site(
        f"http://127.0.0.1:{httpd.server_address[1]}", api=f"http://127.0.0.1:{api.server_address[1]}"
    )
    _Handler.site = _Api.site = found
    for server in (httpd, api):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield found
    finally:
        for server in (httpd, api):
            server.shutdown()
            server.server_close()


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    with serve_site() as found:
        yield found


@dataclass
class Rig:
    store: Store
    service: CaptureService
    draft: str
    profile: Path


@pytest.fixture
def rig() -> Iterator[Rig]:
    tmp = scratch_dir("ds-capture-")
    store = Store(tmp / "data")
    profile = tmp / "data" / "capture-profile"
    service = CaptureService(store, Chromium(profile, binary=chromium(), timeout=CAPTURE_TIMEOUT))
    yield Rig(store, service, str(store.create("text")["id"]), profile)
    shutil.rmtree(tmp, ignore_errors=True)
