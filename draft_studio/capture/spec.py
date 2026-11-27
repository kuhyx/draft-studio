"""The capture spec (what to open, click, wait for, highlight and shoot) and the grants that widen it.

A spec is `url` plus ordered `steps`. Before (and still): click/wait steps and one
`highlight`, shot at the end. A walkthrough (kuhy, 2026-11-27) is one browser run
for several shots: its steps also hold `shot` steps, each taken from the state
the previous steps left, and `goto` steps (another URL of kuhy's). Steps before
the first shot are setup, never shot. A `fresh` shot opens its own `url` in a new
incognito browser context (logged out: a login page) and leaves the session alone.
Click targets and highlights are aims (aim.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlsplit

from draft_studio.capture.aim import MAX_TEXT, Aim, SpecError, clean_text, parse_aim

__all__ = ["HOME", "MAX_TEXT", "NAME_RE", "Denial", "Grant", "Spec", "SpecError", "Step", "parse"]
Json = dict[str, Any]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
MAX_STEPS = 40
KINDS = ("click", "wait", "goto", "shot")
HOME = "home"  # a click's `lands`: the origin root of the page it is on (the start page of the new state)
SHOT_KEYS = {"name", "highlight", "fresh", "url"}


@dataclass(frozen=True)
class Step:
    """click `aim` | wait for `text` | goto `text` (a URL) | shot named `text` (highlight `aim`).

    `text` is always the readable form (a permission card's stepText); a plain click keeps
    `aim` None and aims at `text`. A shot is `fresh` when it runs logged out at its own `url`.
    A click may name where it `lands` (HOME or a URL): a skipped click (already.py) opens it.
    """

    kind: str
    text: str
    aim: Aim | None = None
    fresh: bool = False
    url: str | None = None
    lands: str | None = None

    @property
    def target(self) -> Aim:
        """What a click step clicks."""
        return self.aim or Aim(self.text)

    def to_json(self) -> Json:
        """The wire shape `_step` reads back."""
        if self.kind == "shot":
            shot: Json = {"name": self.text, "highlight": self.aim.to_json() if self.aim else None}
            return {"shot": shot | ({"fresh": True, "url": self.url} if self.fresh else {})}
        if self.kind == "click":
            return {"click": self.target.to_json()} | ({"lands": self.lands} if self.lands else {})
        return {self.kind: self.text}


@dataclass(frozen=True)
class Spec:
    """Open `url`, run `steps`; shoot each shot step, or (none) outline `highlight` at the end.

    Each shot is saved as `<shot name>-<sha12>.png`; a spec without shot steps saves `<name>-...`.
    """

    url: str
    name: str
    steps: tuple[Step, ...] = ()
    highlight: Aim | None = None

    @property
    def walk(self) -> bool:
        """A walkthrough: the steps name their own shots."""
        return any(s.kind == "shot" for s in self.steps)

    def plan(self) -> tuple[Step, ...]:
        """The steps the runner runs: a spec without shot steps ends with its one implicit shot."""
        return self.steps if self.walk else (*self.steps, Step("shot", self.name, aim=self.highlight))

    def to_json(self) -> Json:
        """The wire shape `parse` reads back."""
        return {
            "url": self.url,
            "name": self.name,
            "steps": [s.to_json() for s in self.steps],
            "highlight": self.highlight.to_json() if self.highlight else None,
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


def _web_url(value: object) -> bool:
    parts = urlsplit(value) if isinstance(value, str) else None
    return parts is not None and parts.scheme in ("http", "https") and bool(parts.netloc)


def _shot(raw: object, detail: str) -> Step:
    if not isinstance(raw, dict) or set(raw) - SHOT_KEYS or not NAME_RE.match(str(raw.get("name"))):
        raise SpecError(detail)
    fresh, url = raw.get("fresh", False), raw.get("url")
    if not isinstance(fresh, bool) or fresh != (url is not None) or (fresh and not _web_url(url)):
        raise SpecError(detail)
    highlight = raw.get("highlight")
    aim = None if highlight is None else parse_aim(highlight, detail)
    return Step("shot", str(raw["name"]), aim=aim, fresh=fresh, url=url if fresh else None)


def _click(value: object, lands: object, detail: str) -> Step:
    if lands is not None and lands != HOME and not _web_url(lands):
        raise SpecError(detail)
    aim = parse_aim(value, detail)
    landing = None if lands is None else str(lands)
    return Step("click", aim.label(), aim=None if aim.plain else aim, lands=landing)


def _step(index: int, raw: object) -> Step:
    detail = f"step-{index}"
    if isinstance(raw, dict) and set(raw) == {"click", "lands"}:
        return _click(raw["click"], raw["lands"], detail)
    if not isinstance(raw, dict) or len(raw) != 1:
        raise SpecError(detail)
    ((kind, value),) = raw.items()
    if kind == "click":
        return _click(value, None, detail)
    if kind == "wait":
        return Step(kind, clean_text(value, detail))
    if kind == "goto" and _web_url(value):
        return Step(kind, str(value))
    if kind == "shot":
        return _shot(value, detail)
    raise SpecError(detail)


def _steps(raw: object) -> tuple[Step, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_STEPS:
        raise SpecError("steps")
    steps = tuple(_step(i, s) for i, s in enumerate(raw))
    names: set[str] = set()
    for index, step in enumerate(steps):
        if step.kind == "shot":
            if step.text in names:
                raise SpecError(f"step-{index}")
            names.add(step.text)
    return steps


def parse(body: object) -> Spec:
    """A validated Spec, or SpecError naming the first bad field."""
    if not isinstance(body, dict):
        raise SpecError("body")
    if not _web_url(body.get("url")):
        raise SpecError("url")
    name = body.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SpecError("name")
    steps = _steps(body.get("steps", []))
    highlight = body.get("highlight")
    if highlight is not None and any(s.kind == "shot" for s in steps):
        raise SpecError("highlight")  # a walkthrough highlights per shot
    aim = None if highlight is None else parse_aim(highlight, "highlight")
    return Spec(str(body["url"]), name, steps, aim)
