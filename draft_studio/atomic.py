"""Atomic files: a restart in the middle of a write leaves the old file or the new one, never half."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_text(path: Path, text: str, *, sync: bool = False) -> None:
    """Write `text` next to `path` and rename it over: the rename is the commit.

    `sync`: fsync the file before the rename and the directory after it, so the write outlives a
    power cut too (capture/spent.py: a journaled send must never be forgotten).
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        if sync:
            fh.flush()
            os.fsync(fh.fileno())
    tmp.replace(path)
    if sync:
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def write_json(path: Path, obj: Any) -> None:
    """`obj` as JSON, atomically."""
    write_text(path, json.dumps(obj, ensure_ascii=False))


def read_json(path: Path) -> Any | None:
    """The file's JSON, or None when it is missing (a torn file cannot exist: writes are atomic)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
