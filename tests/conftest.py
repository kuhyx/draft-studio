"""Shared fixtures: every test gets its own data dir, never the real one."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from draft_studio import server as srv
from draft_studio.claude_fake import FakeClaude
from draft_studio.gmail import FakeGmail
from draft_studio.store import Store
from tests.e2e.order import source_order


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    source_order(items)  # after pytest-randomly or any other reorder


@pytest.fixture(autouse=True)
def _no_real_chromium(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF export never starts the real browser in unit tests; a test that wants one sets a fake."""
    monkeypatch.setenv("DRAFT_STUDIO_CHROMIUM", str(tmp_path / "no-chromium"))


class _GuardedPopen(subprocess.Popen[bytes]):
    """Refuses a headed chromium or the `capture login` command: no test may open a window."""

    def __init__(self, args: Any, *rest: Any, **kw: Any) -> None:
        argv = [str(a) for a in args] if isinstance(args, (list, tuple)) else [str(args)]
        login = "draft_studio.capture" in argv and "login" in argv
        system = (
            argv[0].startswith(("/usr/", "/opt/", "/bin/")) or "/" not in argv[0]
        )  # not a test's fake script
        browser = any(name in Path(argv[0]).name for name in ("chromium", "chrome"))
        no_window = any(a.startswith("--headless") or a == "--version" for a in argv)
        headed = system and browser and not no_window
        if login or headed:
            pytest.fail(f"a test tried to open a real browser window: {argv}")
        super().__init__(args, *rest, **kw)


@pytest.fixture(autouse=True)
def _no_real_login_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real Popen of the login command or a headed chromium fails the test (inject a fake spawner)."""
    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)


@pytest.fixture(autouse=True)
def _own_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Code that resolves the data dir itself (the claude -p cwd) never touches kuhy's real one."""
    monkeypatch.setenv("DRAFT_STUDIO_DATA", str(tmp_path / "ds-data"))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


class App:
    def __init__(self, base: str, store: Store, gmail: FakeGmail, httpd: Any) -> None:
        self.base, self.store, self.gmail, self.httpd = base, store, gmail, httpd
        self.timeout = 5.0  # seconds per call; a live capture fixture raises it to the server's own budget

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
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
    # A short poll: shutdown() waits up to one interval, 0.5 s by default, in every test teardown.
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield App(f"http://127.0.0.1:{port}", store, gmail, httpd)
    httpd.shutdown()
    httpd.server_close()
