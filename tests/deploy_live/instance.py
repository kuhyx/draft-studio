"""One throwaway server unit on its own port, data dir and web dir: what the deploy tests restart."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path.home() / "data" / "claude-scratch" / "draft-studio"
# A stand-in for chromium: a headed one (the login window) stays open, a headless one (a capture) fails at once.
FAKE_CHROMIUM = "#!/bin/bash\nfor a in \"$@\"; do [[ \"$a\" == --headless* ]] && exit 0; done\nsleep 12\n"


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
        fake = self.root / "chromium"
        fake.write_text(FAKE_CHROMIUM)
        fake.chmod(0o755)
        env = {"DRAFT_STUDIO_DATA": self.data, "DRAFT_STUDIO_CLAUDE": "fake", "DRAFT_STUDIO_GMAIL": "fake"}
        env |= {"DRAFT_STUDIO_CHROMIUM": fake, "DISPLAY": ""}  # never a window on kuhy's screen
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

    def own_units(self) -> list[str]:
        """The units this instance started: its jobs' claude calls and its login window, nobody else's."""
        names = [f"ds-call-{p.name}-*" for p in (self.data / "jobs").glob("*")]
        rec = self.data / "login-window.json"
        if rec.is_file():
            names.append(json.loads(rec.read_text())["unit"])
        return names

    def unit_active(self, name: str) -> bool:
        done = subprocess.run(["systemctl", "--user", "is-active", "--quiet", name], check=False, timeout=20)
        return done.returncode == 0

    def stop(self) -> None:
        units = self.own_units()
        subprocess.run(["systemctl", "--user", "stop", self.unit], check=False, timeout=20)
        for name in units:
            subprocess.run(["systemctl", "--user", "stop", name], check=False, timeout=20)
        shutil.rmtree(self.root, ignore_errors=True)
