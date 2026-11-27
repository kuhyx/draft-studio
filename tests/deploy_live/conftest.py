"""The throwaway instance every deploy test restarts."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.deploy_live.instance import Instance


@pytest.fixture
def instance() -> Iterator[Instance]:
    inst = Instance()
    try:
        inst.start()
        yield inst
    finally:
        inst.stop()
