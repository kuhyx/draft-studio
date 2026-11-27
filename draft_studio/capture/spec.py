"""The capture spec (what to open, click, wait for and highlight) and the grants that widen it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlsplit

Json = dict[str, Any]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
MAX_STEPS = 40
MAX_TEXT = 200
KINDS = ("click", "wait")


class SpecError(ValueError):
    """The spec is malformed; `detail` names the field."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Step:
    """Click the element showing `text`, or wait until `text` is visible."""

    kind: str
    text: str


@dataclass(frozen=True)
class Spec:
    """One screenshot: open `url`, run `steps`, outline `highlight`, save as `<name>-<sha12>.png`."""

    url: str
    name: str
    steps: tuple[Step, ...] = ()
    highlight: str | None = None

    def to_json(self) -> Json:
        """The wire shape `parse` reads back."""
        return {
            "url": self.url,
            "name": self.name,
            "steps": [{s.kind: s.text} for s in self.steps],
            "highlight": self.highlight,
        }


@dataclass(frozen=True)
class Grant:
    """kuhy's yes for one step: the refused click itself, and/or these exact requests, once each."""

    step: int
    click: bool
    requests: tuple[tuple[str, str], ...] = field(default=())

    def to_json(self) -> Json:
        """JSON form stored in a pending record."""
        return {"step": self.step, "click": self.click, "requests": [list(r) for r in self.requests]}

    @classmethod
    def from_json(cls, raw: Json) -> Self:
        """Inverse of to_json."""
        return cls(int(raw["step"]), bool(raw["click"]), tuple((m, u) for m, u in raw["requests"]))


@dataclass(frozen=True)
class Denial(Grant):
    """kuhy's no for one refusal, kept for the whole run: it only blocks, so repeating it is safe.

    `requests` stay failed (never sent) on every step and no longer stop the capture; `click` ends
    the capture at `step`, which is shot before the refused click, target outlined.
    """


def _text(value: object, detail: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise SpecError(detail)
    return value.strip()


def _step(index: int, raw: object) -> Step:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise SpecError(f"step-{index}")
    ((kind, text),) = raw.items()
    if kind not in KINDS:
        raise SpecError(f"step-{index}")
    return Step(kind, _text(text, f"step-{index}"))


def parse(body: object) -> Spec:
    """A validated Spec, or SpecError naming the first bad field."""
    if not isinstance(body, dict):
        raise SpecError("body")
    url = body.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise SpecError("url")
    name = body.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SpecError("name")
    steps = body.get("steps", [])
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise SpecError("steps")
    highlight = body.get("highlight")
    if highlight is not None:
        highlight = _text(highlight, "highlight")
    return Spec(url, name, tuple(_step(i, s) for i, s in enumerate(steps)), highlight)
