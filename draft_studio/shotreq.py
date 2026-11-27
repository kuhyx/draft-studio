"""Claude's screenshot requests: parse them, check their URL, and swap marker lines for images.

A request (prompts.CAPTURE) becomes a capture spec (draft_studio/capture/spec.py)
only when its URL is written in kuhy's own text: a made-up URL opened on the
logged-in capture profile gives a real screenshot of the wrong page, which is a
fake image too. Every marker line `[[[IMG:<key>]]]` either becomes the stored
image or goes, so no marker ever reaches the client.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from draft_studio.capture.spec import MAX_TEXT, NAME_RE
from draft_studio.links import drop_lines
from draft_studio.prompts import MARKER

Json = dict[str, Any]
_MARKER = re.compile(r"\[\[\[\s*IMG\s*:([^\]\n]*)\]\]\]")
# Any line Claude may have meant as a marker, even a malformed one: it never reaches kuhy.
_MARKERISH = re.compile(r"\[\[\[\s*IMG", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>()\[\]\"'„”“`]+")
_URL_TAIL = ".,;:!?"
_ALT_DROP = re.compile(r"[\[\]()<>!\n\r\t`*_]+")


class ShotError(ValueError):
    """One request is unusable; `code` is what the drop is recorded as."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Shot:
    """One screenshot Claude asked for."""

    key: str
    url: str
    steps: tuple[tuple[str, str], ...]
    highlight: str
    alt: str

    def spec(self) -> Json:
        """The capture API body (draft_studio/capture/__init__.py)."""
        return {
            "url": self.url,
            "name": self.key,
            "steps": [{kind: text} for kind, text in self.steps],
            "highlight": self.highlight or None,
        }

    def to_json(self) -> Json:
        """Claude's own shape (prompts.CAPTURE), stored in a held record and sent back on a fix."""
        return {
            "key": self.key,
            "url": self.url,
            "steps": [{"kind": kind, "text": text} for kind, text in self.steps],
            "highlight": self.highlight,
            "alt": self.alt,
        }


def clean_alt(alt: str) -> str:
    """Alt text that cannot break the image link: no brackets, no newlines, one line."""
    return " ".join(_ALT_DROP.sub(" ", alt).split())[:MAX_TEXT]


def _str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ShotError("bad-spec")
    return value.strip()


def parse_shot(raw: object, key: str | None = None) -> Shot:
    """A Shot from Claude's JSON; `key` pins it (a fix may not rename a capture)."""
    if not isinstance(raw, dict):
        raise ShotError("bad-spec")
    name = key or _str(raw, "key")
    if not NAME_RE.match(name):
        raise ShotError("bad-spec")
    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise ShotError("bad-spec")
    pairs = tuple((_str(s, "kind"), _str(s, "text")) for s in steps if isinstance(s, dict))
    if len(pairs) != len(steps) or any(kind not in ("click", "wait") for kind, _ in pairs):
        raise ShotError("bad-spec")
    return Shot(name, _str(raw, "url"), pairs, _str(raw, "highlight"), clean_alt(_str(raw, "alt")))


def parse_shots(raws: list[Any]) -> tuple[dict[str, Shot], list[str]]:
    """({key: Shot} of the usable requests, keys of the bad ones); a repeated key is bad."""
    shots: dict[str, Shot] = {}
    bad: list[str] = []
    for raw in raws:
        try:
            shot = parse_shot(raw)
        except ShotError:
            bad.append(str(raw.get("key")) if isinstance(raw, dict) else "?")
            continue
        if shot.key in shots:
            bad.append(shot.key)
        shots[shot.key] = shot
    return {k: s for k, s in shots.items() if k not in bad}, bad


def urls_in(text: str) -> set[str]:
    """Every http(s) URL written in `text`, trailing punctuation and slash removed."""
    return {m.rstrip(_URL_TAIL).rstrip("/") for m in _URL.findall(text)}


def allowed_url(url: str, kuhy: str) -> bool:
    """True only for a URL written in kuhy's text (brief, answers, draft, revised replacement)."""
    return url.rstrip("/") in urls_in(kuhy)


def markers(text: str) -> list[str]:
    """Keys of the marker lines, in text order, once each."""
    return list(dict.fromkeys(k.strip() for k in _MARKER.findall(text)))


def substitute(text: str, done: Mapping[str, str]) -> tuple[str, list[str]]:
    """(text with each marker swapped for its image markdown, keys of the markers dropped).

    An unresolved marker is cut; a line left blank by the cut goes, with the
    blank line that only separated it. A line still holding something
    marker-shaped (malformed) goes whole.
    """
    dropped: list[str] = []
    lines: list[str | None] = []
    for line in text.split("\n"):
        keys = [k.strip() for k in _MARKER.findall(line)]
        dropped.extend(k for k in keys if k not in done)
        out = _MARKER.sub(lambda m: done.get(m.group(1).strip(), ""), line)
        if _MARKERISH.search(out):
            dropped.append("?")
            lines.append(None)
        elif keys and not out.strip():
            lines.append(None)
        else:
            lines.append(out)
    if any(item is None for item in lines):
        return "\n".join(drop_lines(lines)), list(dict.fromkeys(dropped))
    return "\n".join(item or "" for item in lines), list(dict.fromkeys(dropped))


def image_md(alt: str, asset: str) -> str:
    """The markdown a resolved marker becomes."""
    return f"![{alt}](assets/{asset})"


def marker(key: str) -> str:
    """The marker line for `key` (tests, the fake Claude)."""
    return MARKER.format(key=key)


def unknown(keys: Collection[str], shots: Mapping[str, Shot]) -> list[str]:
    """Marker keys Claude wrote without a usable request."""
    return [k for k in keys if k not in shots]
