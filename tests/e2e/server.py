"""The app under test as a process the tests can stop and start again (same port, same data)."""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from typing import Any

from tests.e2e.env import REPO


class Server:
    """`python -m draft_studio` on `port`; `stop` kills it for real, `start` brings it back."""

    def __init__(self, port: int, environ: dict[str, str]) -> None:
        self.port, self.environ = port, environ
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen[Any] | None = None

    def start(self, build: str = "e2e") -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "draft_studio", "--port", str(self.port), "--build", build],
            cwd=REPO,
            env=self.environ,
        )
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/version", timeout=1).close()
                return
            except OSError:
                time.sleep(0.1)
        raise AssertionError("server did not come up")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait(timeout=10)
            self.proc = None
