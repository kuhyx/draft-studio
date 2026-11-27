"""Shared fixtures: every test gets its own data dir, never the real one."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import server as srv
from draft_studio.claude import FakeClaude
from draft_studio.gmail import FakeGmail
from draft_studio.store import Store


@pytest.fixture(autouse=True)
def _no_real_chromium(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF export never starts the real browser in unit tests; a test that wants one sets a fake."""
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(tmp_path / "no-chromium"))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


class App:
    def __init__(self, base: str, store: Store, gmail: FakeGmail, httpd: Any) -> None:
        self.base, self.store, self.gmail, self.httpd = base, store, gmail, httpd

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, json.loads(raw) if "json" in ctype else raw.decode()
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw)
            except json.JSONDecodeError:
                return err.code, raw.decode()


@pytest.fixture
def app(tmp_path: Path) -> Iterator[App]:
    dist = tmp_path / "dist"
    (dist / "d").mkdir(parents=True)
    (dist / "index.html").write_text("<html>list __BUILD__</html>")
    (dist / "d" / "index.html").write_text("<html>editor</html>")
    (dist / "app.js").write_text("console.log(1)")
    locales = tmp_path / "locales"
    locales.mkdir()
    (locales / "en.json").write_text('{"dir": "ltr"}')
    (locales / "pl.json").write_text('{"dir": "ltr"}')
    (locales / "README").write_text("x")
    store = Store(tmp_path / "data")
    gmail = FakeGmail(tmp_path / "data" / "gmail-fake.json")
    httpd = srv.make_server(
        srv.Deps(store=store, claude=FakeClaude(), gmail=gmail, dist=dist, locales=locales, build="abc123"),
        port=0,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield App(f"http://127.0.0.1:{port}", store, gmail, httpd)
    httpd.shutdown()
    httpd.server_close()
