"""Harness of the permission-card E2E: the real page in headless chromium against an in-process server.

The server runs in this process so its seams take fakes: the capture browser is a FakeLauncher
(tests/test_capture_service.py) and the login window a FakeSpawn (tests/test_login_route.py). No
test here ever starts `capture login` or a headed browser.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from draft_studio import server as srv
from draft_studio.capture.service import CaptureService
from draft_studio.claude_fake import FakeClaude
from draft_studio.gmail import FakeGmail
from draft_studio.loginapi import LoginWindows
from draft_studio.store import Store
from tests.capture_fakes import FakePage
from tests.e2e.cdp import Browser
from tests.e2e.env import DIST, REPO, Env
from tests.e2e.launch import scratch_dir
from tests.e2e.tips import viewport
from tests.imgdata import png
from tests.test_capture_service import FakeLauncher
from tests.test_login_route import FakeSpawn

SIZES = ((1920, 1080, False), (390, 844, True))


class BigShot(FakePage):
    """A fake tab whose screenshot is screen-sized, so the card's thumbnail looks like one."""

    def screenshot(self) -> bytes:
        return png(1280, 800, (226, 232, 240))


def login_tab() -> BigShot:
    tab = BigShot(["Zapisz"])
    tab.password = True
    return tab


@dataclass
class CardEnv:
    env: Env
    httpd: Any
    launcher: FakeLauncher
    spawn: FakeSpawn


@contextmanager
def card_env() -> Iterator[CardEnv]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    tmp = scratch_dir("ds-card-")
    store = Store(tmp / "data")
    deps = srv.Deps(
        store=store,
        claude=FakeClaude(),
        gmail=FakeGmail(tmp / "data" / "gmail-fake.json"),
        dist=DIST,
        locales=REPO / "locales",
        build="e2e",
    )
    httpd = srv.make_server(deps, port=0)
    launcher = FakeLauncher(lambda: BigShot(["Zapisz"]))
    httpd.captures = CaptureService(store, launcher)
    spawn = FakeSpawn()
    httpd.logins = LoginWindows(store.root, spawn, 0.0)
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.02), daemon=True).start()
    shots = Path(os.environ.get("DS_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    b = Browser()
    try:
        yield CardEnv(
            Env(f"http://127.0.0.1:{httpd.server_address[1]}", tmp, REPO / "locales", b, shots),
            httpd,
            launcher,
            spawn,
        )
        assert b.errors() == [], b.errors()
    finally:
        b.close()
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def windows_opened(spawn: FakeSpawn, count: int, timeout: float = 10.0) -> None:
    """Wait until the server opened `count` login windows: the button spins at the click, before the POST lands."""
    deadline = time.monotonic() + timeout
    while len(spawn.procs) < count:  # procs is filled last, so a window listed there is fully recorded
        assert time.monotonic() < deadline, f"login window {count} never opened ({spawn.calls})"
        time.sleep(0.01)


def held_draft(env: Env, text: str) -> str:
    """A text draft in editing holding `text`, opened in the page, with a `[shot]` change asked on it."""
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Poradnik [noq]"})
    env.post(
        f"/api/drafts/{did}",
        {"text": f"{text}\n\nKoniec.", "claude": [], "to": "", "subject": ""},
        method="PUT",
    )
    env.b.goto(f"{env.base}/d/?id={did}")
    env.select(0, len(text))
    env.b.click(".sel-toolbar .sel-change")
    env.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
    env.b.type("[shot]")
    env.b.click(".sel-toolbar .sel-submit")
    env.b.wait("document.body.dataset.held === '1'", timeout=40, what="answer held")
    env.b.wait("!document.querySelector('#held-desk').hidden", what="card shown")
    env.caret(0)  # the selection toolbar would cover the held span
    return did


def shoot(env: Env, name: str) -> None:
    """`name`-1920x1080.png and `name`-390x844.png (phone: touch emulation), then back to the desktop size."""
    for width, height, mobile in SIZES:
        viewport(env.b, width, height, mobile)
        time.sleep(0.3)  # layout + image decode before the capture, not a sync point
        env.shot(f"{name}-{width}x{height}")
    viewport(env.b, 1920, 1080)


def visible_text_ok(env: Env) -> None:
    """No placeholder junk anywhere on the page."""
    text = str(env.b.js("document.body.innerText"))
    for junk in ("undefined", "NaN", "[object Object]"):
        assert junk not in text, (junk, text)
