"""The `env` fixture: real server on a free port (fake Claude, fake Gmail) plus one headless browser."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.cdp import Browser, free_port, scratch_dir
from tests.e2e.env import DIST, REPO, Env
from tests.e2e.launch import chromium
from tests.e2e.tips import TEXT


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    assert (DIST / "index.html").is_file(), "build web/dist first"
    tmp = scratch_dir("ds-e2e-")
    data, locales = tmp / "data", tmp / "locales"
    shutil.copytree(REPO / "locales", locales)
    shots = Path(os.environ.get("DS_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    environ = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(data),
        "DRAFT_STUDIO_LOCALES": str(locales),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_GMAIL": "fake",
        # Screenshot capture (test_held.py) and PDF export use the browser the E2E picked.
        "DRAFT_STUDIO_CHROMIUM": chromium(),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "draft_studio", "--port", str(port), "--build", "e2e"], cwd=REPO, env=environ
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/version", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    b = Browser()
    try:
        yield Env(base, data, locales, b, shots)
        assert b.errors() == [], b.errors()
    finally:
        b.close()
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def tip_draft(env: Env) -> str:
    """A mail draft in the editing phase holding the tooltip tests' sample text."""
    did = str(env.post("/api/drafts", {"kind": "mail"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Mail to the owner [noq]"})
    env.post(f"/api/drafts/{did}", {"text": TEXT, "claude": [], "to": "", "subject": "Hi"}, method="PUT")
    return did
