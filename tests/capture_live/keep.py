"""Where a live capture test keeps the PNGs it saw, for a human to look at."""

from __future__ import annotations

import os
from pathlib import Path

from tests.capture_live.conftest import Rig

KEEP = Path(os.environ.get("DS_CAPTURE_SHOTS") or Path.home() / "data/claude-scratch/ds-capture/shots")


def asset(rig: Rig, name: str) -> bytes:
    data, _ = rig.store.asset(rig.draft, name)
    KEEP.mkdir(parents=True, exist_ok=True)
    (KEEP / name).write_bytes(data)
    return data
