"""Atomic files: a restart in the middle of a write leaves the old file or the new one, never half."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_text(path: Path, text: str) -> None:
    """Write `text` next to `path` and rename it over: the rename is the commit."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, obj: Any) -> None:
    """`obj` as JSON, atomically."""
    write_text(path, json.dumps(obj, ensure_ascii=False))


def read_json(path: Path) -> Any | None:
    """The file's JSON, or None when it is missing (a torn file cannot exist: writes are atomic)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
