"""Shared fixtures: every test gets its own data dir, never the real one."""

from __future__ import annotations

from pathlib import Path

import pytest

from draft_studio.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")
