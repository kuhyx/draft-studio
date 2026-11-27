"""E2E tests of one module are a script: one server and browser, state handed from test to test.

They must run in source order on one worker, or they fail in ways that look like page-load
timeouts (`#brief` or `draftStudio` missing because the test that created the draft ran elsewhere).
Two things broke that: pytest-xdist's default `load` mode deals single tests to any worker, and
pytest-randomly shuffles them. pyproject addopts sets `--dist loadfile` (a file goes to one
worker whole) and `tests/conftest.py` calls `source_order` last. Guard: `tests/test_e2e_order.py`.
"""

from __future__ import annotations

from typing import Any

E2E_MARK = "e2e"


def is_e2e(item: Any) -> bool:
    return item.get_closest_marker(E2E_MARK) is not None


def module_of(item: Any) -> str:
    module: str = item.nodeid.split("::")[0]
    return module


def source_order(items: list[Any]) -> None:
    """Undo any shuffle among e2e tests: modules keep their place, tests inside follow the source."""
    e2e = [i for i in items if is_e2e(i)]
    first_seen: dict[str, int] = {}
    for i in e2e:
        first_seen.setdefault(module_of(i), len(first_seen))
    e2e.sort(key=lambda i: (first_seen[module_of(i)], i.reportinfo()[1] or 0))
    slots = iter(e2e)
    items[:] = [next(slots) if is_e2e(i) else i for i in items]
