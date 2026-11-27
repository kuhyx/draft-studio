"""A deploy in the middle of a long ask: the restart happens at once and nothing in flight is lost.

Real systemd --user units on a throwaway instance (own port, own data dir under the scratch dir):
a server, a long fake ask (its Claude call runs in a unit of its own), then `scripts/deploy.sh`
pointed at that instance. Needs systemd --user; marker `deploy`, run it by hand:

    python3 -m pytest -q -m deploy tests/deploy_live
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.deploy

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path.home() / "data" / "claude-scratch" / "draft-studio"
ASK = {"text": "one two three four", "from": 0, "to": 3, "question": "[slow] why"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Instance:
    """One throwaway server unit, its data dir and the web dir deploy.sh swaps."""

    def __init__(self) -> None:
        tag = secrets.token_hex(3)
        self.unit = f"ds-test-{tag}.service"
        self.port = free_port()
        self.root = SCRATCH / f"live-{tag}"
        self.data, self.web = self.root / "data", self.root / "web"
        self.src = self.root / "dist-src"
        self.base = f"http://127.0.0.1:{self.port}"

    def make_dist(self, build: str) -> None:
        """A minimal built site: enough for /version and the pages."""
        shutil.rmtree(self.src, ignore_errors=True)
        self.src.mkdir(parents=True)
        (self.src / "index.html").write_text(f"<html>{build} __BUILD__</html>")
        (self.src / "build-id").write_text(build + "\n")

    def start(self) -> None:
        self.make_dist("b1")
        self.web.mkdir(parents=True)
        shutil.copytree(self.src, self.web / "dist.b1")
        (self.web / "dist").symlink_to("dist.b1")
        env = {"DRAFT_STUDIO_DATA": self.data, "DRAFT_STUDIO_CLAUDE": "fake", "DRAFT_STUDIO_GMAIL": "fake"}
        env |= {"DRAFT_STUDIO_FAKE_SLOW": "8", "DRAFT_STUDIO_LOCALES": REPO / "locales"}
        env["DRAFT_STUDIO_JOBS"] = os.environ.get("DS_TEST_JOBS", "")  # DS_TEST_JOBS=inline: the old way, red
        cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={self.unit}"]
        cmd += [f"--working-directory={REPO}", f"--setenv=PATH={os.environ['PATH']}"]
        cmd += [f"--setenv={k}={v}" for k, v in env.items()]
        cmd += [
            sys.executable,
            "-m",
            "draft_studio",
            "--port",
            str(self.port),
            "--dist",
            str(self.web / "dist"),
        ]
        subprocess.run(cmd, check=True, timeout=20)
        self.wait_for("b1")

    def wait_for(self, build: str, timeout: float = 20.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if self.get("/version")["build"] == build:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"{self.unit} never served build {build}")

    def get(self, path: str) -> Any:
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return json.loads(resp.read())

    def post(self, path: str, body: Any, method: str = "POST", timeout: float = 5) -> Any:
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), method=method)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def deploy(self, build: str) -> subprocess.CompletedProcess[str]:
        self.make_dist(build)
        env = {**os.environ, "DS_REPO": str(REPO), "DS_NO_PULL": "1", "DS_UNIT": self.unit}
        env |= {"DS_PORT": str(self.port), "DS_WEB": str(self.web), "DS_DIST_SRC": str(self.src)}
        env["DS_BUILD"] = build
        return subprocess.run(
            ["bash", str(REPO / "scripts" / "deploy.sh")],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=55,
        )

    def stop(self) -> None:
        subprocess.run(["systemctl", "--user", "stop", self.unit], check=False, timeout=20)
        for unit in subprocess.run(
            ["systemctl", "--user", "list-units", "--plain", "--no-legend", "ds-call-*"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.split("\n"):
            if unit.strip():
                subprocess.run(["systemctl", "--user", "stop", unit.split()[0]], check=False, timeout=20)
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture
def instance() -> Iterator[Instance]:
    inst = Instance()
    try:
        inst.start()
        yield inst
    finally:
        inst.stop()


def test_deploy_restarts_at_once_and_the_running_ask_still_lands_once(instance: Instance) -> None:
    made = instance.post("/api/drafts", {"kind": "text"})
    did = made["id"]
    instance.post(f"/api/drafts/{did}", {"text": ASK["text"]}, method="PUT")
    answers: list[Any] = []

    def ask() -> None:  # the browser's request: it dies with the old server, as a tab's would
        try:
            answers.append(instance.post(f"/api/drafts/{did}/ask", ASK, timeout=30))
        except OSError as exc:
            answers.append(exc)

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.get("/api/busy")["busy"]:
        assert time.monotonic() < deadline, "the ask never became busy"
        time.sleep(0.05)
    started = time.monotonic()
    done = instance.deploy("b2")
    took = time.monotonic() - started
    assert done.returncode == 0, done.stdout + done.stderr
    assert took < 8, f"the deploy waited for the ask ({took:.1f}s): it must restart at once"
    assert instance.get("/version")["build"] == "b2"
    thread.join(30)
    assert isinstance(answers[0], OSError), "the old server's connection should have been cut"
    end = time.monotonic() + 30
    asks: list[Any] = []
    while not asks and time.monotonic() < end:
        asks = instance.get(f"/api/drafts/{did}")["meta"]["asks"]
        time.sleep(0.2)
    assert [a["answer"] for a in asks] == ["[en] About 'one': [slow] why"]
    time.sleep(1.0)
    assert len(instance.get(f"/api/drafts/{did}")["meta"]["asks"]) == 1
    assert instance.get("/api/busy") == {"busy": False, "jobs": [], "failed": []}
