"""Real headless chromium against a loopback test site that counts every non-GET it receives."""

from __future__ import annotations

import shutil
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from draft_studio.capture.browser import Chromium
from draft_studio.capture.service import CaptureService
from draft_studio.store import Store
from tests.e2e.launch import chromium, scratch_dir

SITE = Path(__file__).parent / "site"


@dataclass
class Site:
    base: str
    writes: Counter[str] = field(default_factory=Counter)


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


@contextmanager
def serve_site() -> Iterator[Site]:
    """The test site on a free loopback port (tests/e2e/test_held.py serves it next to the app)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(SITE)))
    found = Site(f"http://127.0.0.1:{httpd.server_address[1]}")
    _Handler.site = found
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield found
    finally:
        httpd.shutdown()
        httpd.server_close()


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
    service = CaptureService(store, Chromium(profile, binary=chromium(), timeout=40.0))
    yield Rig(store, service, str(store.create("text")["id"]), profile)
    shutil.rmtree(tmp, ignore_errors=True)
