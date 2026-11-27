"""Where the E2E browser lives and how it starts: scratch dirs, chromium, free ports, page timer tracking."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
from pathlib import Path

# Never /tmp (tmpfs, wiped on reboot): profiles and data dirs live under the scratch root.
SCRATCH = Path(os.environ.get("DS_E2E_TMP") or Path.home() / "data/claude-scratch/draft-studio-e2e")

FALLBACKS = (
    "/usr/bin/chromium",
    *sorted(
        (str(p) for p in Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome")),
        key=lambda p: int(p.split("chromium-")[1].split("/")[0]),
        reverse=True,
    ),
)


# Installed before every page script. CodeMirror moves a tooltip only after a 50 ms debounced
# setTimeout (its ResizeObserver -> measureSoon), then a requestMeasure on the next frame; no
# number of animation frames bounds that. So the page counts its pending short timers and a
# position probe only reads once none is left (`__shortTimers`).
TRACK_TIMERS = """
(() => {
  const set = window.setTimeout.bind(window), clear = window.clearTimeout.bind(window);
  const pending = new Set();
  Object.defineProperty(window, '__shortTimers', { get: () => pending.size });
  window.__setTimeout = set;
  window.setTimeout = (fn, delay = 0, ...args) => {
    if (typeof fn !== 'function' || delay > 100) return set(fn, delay, ...args);
    const id = set(() => { pending.delete(id); fn(...args); }, delay);
    pending.add(id);
    return id;
  };
  window.clearTimeout = (id) => { pending.delete(id); clear(id); };
})();
"""


def chromium() -> str:
    """$DS_CHROMIUM, else the first browser that actually starts (system chromium can lose a .so)."""
    explicit = os.environ.get("DS_CHROMIUM")
    if explicit:
        return explicit
    for path in FALLBACKS:
        probe = subprocess.run([path, "--version"], capture_output=True, check=False)
        if probe.returncode == 0:
            return path
    raise RuntimeError("no working chromium")


def scratch_dir(prefix: str) -> Path:
    """A fresh directory under SCRATCH."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=SCRATCH))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port
