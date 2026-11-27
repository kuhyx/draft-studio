"""Guard for the E2E ordering bug class: a module's tests are a script, never split or shuffled.

Dealt out singly (xdist `load`) or shuffled (pytest-randomly), the flow tests lose the state the
previous test left in the shared browser and fail as page-load timeouts.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from tests.e2e.order import source_order

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeItem:
    nodeid: str
    line: int
    e2e: bool = True

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self.e2e and name == "e2e" else None

    def reportinfo(self) -> tuple[str, int, str]:
        return (self.nodeid, self.line, self.nodeid)


def test_source_order_restores_a_shuffle_and_keeps_other_tests_in_place() -> None:
    unit = FakeItem("tests/test_api.py::test_x", 3, e2e=False)
    a2, a1 = FakeItem("tests/e2e/test_a.py::test_two", 20), FakeItem("tests/e2e/test_a.py::test_one", 10)
    b1, b2 = FakeItem("tests/e2e/test_b.py::test_one", 5), FakeItem("tests/e2e/test_b.py::test_two", 8)
    items = [a2, unit, b2, a1, b1]
    source_order(items)
    assert items == [a1, unit, a2, b1, b2]
    assert items[1] is unit


def test_e2e_modules_run_whole_on_one_xdist_worker() -> None:
    opts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["addopts"]
    assert "--dist loadfile" in opts, "e2e tests of a module must not be split across -n workers"


def test_conftest_restores_source_order_after_any_shuffle() -> None:
    text = (ROOT / "tests" / "conftest.py").read_text()
    assert "source_order(items)" in text
    assert "trylast=True" in text


def test_loadgroup_is_not_used() -> None:
    """pytest 9.2 ignores xdist 3.8's `_nodeid` rewrite, so `loadgroup` silently splits groups."""
    assert "loadgroup" not in (ROOT / "pyproject.toml").read_text()
