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


@pytest.fixture
def plain_instance() -> Iterator[Instance]:
    """web/dist a real directory (the live clone before its first symlink deploy); a long fake ask."""
    inst = Instance(slow="15")
    try:
        inst.start(plain_dist=True)
        yield inst
    finally:
        inst.stop()
