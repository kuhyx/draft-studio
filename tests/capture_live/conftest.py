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


@dataclass
class Site:
    base: str
    writes: Counter[str] = field(default_factory=Counter)
    api: str = ""  # a second loopback origin: pages reach it cross-origin (CORS)


class _Handler(SimpleHTTPRequestHandler):
    site: Site

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet."""

    def _write(self) -> None:
        self.site.writes[f"{self.command} {self.path}"] += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_POST = do_PUT = do_DELETE = _write

    def do_GET(self) -> None:
        """Static files; a GET to /beacon/<x> is counted too (proof a click ran, e.g. a forbidden one)."""
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
