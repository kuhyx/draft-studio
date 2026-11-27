"""A deploy while a granted capture runs (kuhy, 2026-11-27: "deploy at ANY TIME, nothing in progress breaks").

The permission card is granted, the capture is in flight (the site holds the granted write), and the
server is stopped like a deploy stops it. The new server finds the grant's job file, runs the capture
again and the held answer lands as a proposal in the open tab; nothing stays "granted" and stuck.

Run: .venv/bin/pytest -q -m e2e tests/e2e/test_restart_grant.py
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.capture_live.conftest import Site, serve_site
from tests.e2e.cdp import Browser, free_port
from tests.e2e.env import REPO, Env
from tests.e2e.launch import chromium, scratch_dir
from tests.e2e.server import Server

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    with serve_site() as found:
        yield found


@pytest.fixture
def rig() -> Iterator[tuple[Env, Server]]:
    tmp = scratch_dir("ds-restart-")
    data, locales = tmp / "data", tmp / "locales"
    shutil.copytree(REPO / "locales", locales)
    port = free_port()
    environ = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(data),
        "DRAFT_STUDIO_LOCALES": str(locales),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_JOBS": "files",  # job files like the live service, Claude in-process (no systemd)
        "DRAFT_STUDIO_GMAIL": "fake",
        "DRAFT_STUDIO_CHROMIUM": chromium(),
    }
    server, tab = Server(port, environ), Browser()
    server.start()
    yield Env(server.base, data, locales, tab, tmp), server
    tab.close()
    server.stop()
    shutil.rmtree(tmp, ignore_errors=True)


def until(check: Any, what: str, timeout: float = 40.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, what
        time.sleep(0.1)


def files(data: Path, pattern: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(data.glob(pattern))]


def test_a_grant_cut_by_a_deploy_lands_after_it(rig: tuple[Env, Server], site: Site) -> None:
    env, server = rig
    text = f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”."
    did = str(env.post("/api/drafts", {"kind": "text"})["id"])
    env.post(f"/api/drafts/{did}/brief", {"brief": "Poradnik [noq]"})
    env.post(f"/api/drafts/{did}", {"text": text, "claude": [], "to": "", "subject": ""}, method="PUT")
    env.b.goto(env.base + f"/d/?id={did}")
    env.b.wait("document.body.dataset.ready === '1'", what="editor ready")
    asked = {"text": text, "from": 0, "to": len(text), "instruction": "[shot]"}
    req = urllib.request.Request(
        f"{env.base}/api/drafts/{did}/propose",
        data=json.dumps(asked).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        held = json.loads(resp.read())["held"]
    rid = held["requests"][0]["id"]

    site.gate = threading.Event()  # the granted write waits at the site: the capture is cut in flight
    sent = site.writes["POST /api/save"]
    clicked = threading.Thread(
        target=lambda: _quiet(lambda: env.post(f"/api/captures/{rid}/grant", {})), daemon=True
    )
    clicked.start()
    until(lambda: site.writes["POST /api/save"] == sent + 1, "the granted write never reached the site")
    (job,) = [j for j in files(env.data, "jobs/*/job.json") if j["op"] == "grant"]
    (request,) = files(env.data, f"captures/{rid}.json")
    (record,) = files(env.data, f"drafts/{did}/held/*.json")
    assert (job["status"], request["status"]) == ("running", "granted")
    assert [s["state"] for s in record["shots"].values()] == ["pending"]  # nothing landed yet

    server.stop(signal.SIGTERM)
    end_chromium(env.data / "capture-profile")
    site.gate.set()
    site.gate = None
    server.start()
    until(lambda: len(env.api(f"/api/drafts/{did}")["proposals"]) == 1, "the proposal never landed")
    assert env.api(f"/api/drafts/{did}/held") == {"held": [], "released": [_released(env, did)]}
    env.b.wait("document.querySelectorAll('.cm-hunk').length === 1", what="the open tab shows it")
    assert "(assets/shot-1-" in str(env.b.js("document.querySelector('.cm-ins').textContent"))
    assert (
        site.writes["POST /api/save"] == sent + 2
    )  # the cut run's write, and the re-run's under the same grant
    (job,) = [j for j in files(env.data, "jobs/*/job.json") if j["op"] == "grant"]
    assert job["status"] == "done"


def end_chromium(profile: Path) -> None:
    """What the unit's cgroup does at a stop: the capture's chromium (a group of its own) goes with it."""
    mine = [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit() and _runs_on(p, profile)]
    for pid in mine:
        os.kill(pid, signal.SIGTERM)
    until(lambda: not any(Path(f"/proc/{pid}").exists() for pid in mine), "chromium did not end", 10)


def _runs_on(proc: Path, profile: Path) -> bool:
    try:
        return f"--user-data-dir={profile}" in (proc / "cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False


def _released(env: Env, did: str) -> dict[str, Any]:
    listing = env.api(f"/api/drafts/{did}/held")
    assert len(listing["released"]) == 1
    return dict(listing["released"][0])


def _quiet(call: Any) -> None:
    with contextlib.suppress(OSError):  # the server went away under it: the point of the test
        call()
