"""Keep capture storage private and small.

The capture profile runs with `--password-store=basic`, so the cookies of the
sites kuhy logged into are protected by file permissions alone: its directory
(and the pending-requests directory, whose PNGs show those sites) must be 0700
at every start and every launch. Spent permission requests are pruned so
screenshots of logged-in pages do not pile up.
"""

from __future__ import annotations

import json
import logging
import stat
import time
from collections.abc import Collection
from pathlib import Path

LOG = logging.getLogger("draft_studio")
PRIVATE = 0o700
MAX_AGE_S = 7 * 24 * 3600


def ensure_private(path: Path) -> None:
    """Create `path` as 0700 if missing; chmod it to 0700 (and log) if it is looser."""
    path.mkdir(mode=PRIVATE, parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != PRIVATE:
        path.chmod(PRIVATE)
        LOG.warning("capture: %s was mode %s, set to 0o700", path, oct(mode))


def _expired(record: Path, now: float) -> bool:
    """Denied, granted (spent), older than MAX_AGE_S, or unreadable."""
    try:
        rec = json.loads(record.read_text(encoding="utf-8"))
        return bool(rec["status"] != "pending" or now - rec["created"] > MAX_AGE_S)
    except (OSError, ValueError, LookupError, TypeError):
        return True


def prune(directory: Path, now: float | None = None, keep: Collection[str] = ()) -> int:
    """Delete expired request records with their shots, then stray shots and temp files.

    `keep`: request ids a held answer still waits on: a granted one whose re-run a restart cut is
    resumed from its record (heldjobs.py). Returns the number of records removed. Only the store's
    own file shapes (<16 hex>.json/.png, .<16 hex>.tmp) are touched.
    """
    if not directory.is_dir():
        return 0
    stamp = time.time() if now is None else now
    removed = 0
    for record in directory.glob("????????????????.json"):
        if record.stem not in keep and _expired(record, stamp):
            record.unlink()
            record.with_suffix(".png").unlink(missing_ok=True)
            removed += 1
    for shot in directory.glob("????????????????.png"):
        if not shot.with_suffix(".json").exists():
            shot.unlink()
    for tmp in directory.glob(".*.tmp"):
        tmp.unlink()
    if removed:
        LOG.info("capture: pruned %d spent permission requests from %s", removed, directory)
    return removed
