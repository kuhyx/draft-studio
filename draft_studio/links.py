"""Image links in draft markdown, checked against the files the draft really holds.

kuhy (2026-11-27): "fake images are worse than no image at all". Draft studio's
own Claude once linked seven `assets/TODO-...png` screenshots nobody had
uploaded. Two gates use this module: api.py strips such links from a Claude
answer before the client sees it (the rest of the answer stays), and
approval.py refuses to approve (so Gmail and PDF refuse to export) while the
text links a missing asset. kuhy's own links are never stripped: Claude may
only ADD links to stored assets, and kuhy's edits always win.
"""

from __future__ import annotations

import re
from collections.abc import Collection

# Any markdown link or image target under assets/: ](assets/x), ](./assets/x), ](<assets/x>).
_ASSET_TARGET = re.compile(r"\]\(\s*<?(?:\./)?assets/([^)\s>]*)")
# Any markdown image source.
_IMAGE_SRC = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)\s>]*)")
_ASSET_PREFIX = re.compile(r"^(?:\./)?assets/")
# One whole link or image, title included: what the stripper cuts.
_TOKEN = re.compile(r"""(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]*)>?(?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)""")
# What is left of a line that held only links: blanks, a list bullet or a quote mark.
_BARE = re.compile(r"^\s*(?:[-*+>]|\d+[.)])?\s*$")
_NO_SPACE_BEFORE = ".,;:!?)]"


class LinkError(ValueError):
    """Claude's text links an image the stripper could not cut cleanly: drop the whole answer."""


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def missing_assets(text: str, stored: Collection[str]) -> list[str]:
    """Names linked as assets/<name> that are not stored, once each, in text order.

    A name the store could never hold (bad charset, `..`) is never stored, so it counts as missing.
    """
    return _unique([name for name in _ASSET_TARGET.findall(text) if name not in stored])


def invented_images(text: str, stored: Collection[str]) -> list[str]:
    """Everything Claude may not write: missing assets plus any image whose source is not an asset."""
    foreign = [src for src in _IMAGE_SRC.findall(text) if not _ASSET_PREFIX.match(src)]
    return _unique(missing_assets(text, stored) + foreign)


def _key(src: str) -> str:
    """One spelling per target: `./assets/x` and `assets/x` are the same file."""
    return src.removeprefix("./") if _ASSET_PREFIX.match(src) else src


def _targets(text: str) -> set[str]:
    """Every link and image target in kuhy's own text."""
    return {f"assets/{n}" for n in _ASSET_TARGET.findall(text)} | {_key(s) for s in _IMAGE_SRC.findall(text)}


def _unallowed(text: str, stored: Collection[str], ok: set[str]) -> list[str]:
    """Targets Claude may not link: missing assets and foreign images, minus kuhy's own."""
    missing = [f"assets/{n}" for n in missing_assets(text, stored)]
    foreign = [src for src in _IMAGE_SRC.findall(text) if not _ASSET_PREFIX.match(src)]
    return [k for k in _unique(missing + foreign) if k not in ok]


def _cut(left: str, right: str) -> tuple[str, int]:
    """Join the two sides of a removed link with one space at most; returns (line, resume point)."""
    head = left
    if left.strip():  # at the start of the line the indentation stays
        head = left.rstrip(" \t")
        tail = right.lstrip(" \t")
        if tail and tail[0] not in _NO_SPACE_BEFORE and head[-1] not in "([":
            head += " "
    return head + right.lstrip(" \t"), len(head)


def _strip_line(line: str, ok: set[str], stored: Collection[str], out: list[str]) -> str:
    """The line without its invented links; each removed link goes to `out`."""
    pos = 0
    while m := _TOKEN.search(line, pos):
        bang, words, src = m.groups()
        key, asset = _key(src), bool(_ASSET_PREFIX.match(src))
        kept = key in ok or (asset and key.removeprefix("assets/") in stored)
        if kept or not (bang or asset):
            pos = m.end()
        elif bang or not words:
            out.append(m.group(0))
            line, pos = _cut(line[: m.start()], line[m.end() :])
        else:  # a plain link to a missing asset keeps its words
            out.append(m.group(0))
            line, pos = line[: m.start()] + words + line[m.end() :], m.start() + len(words)
    return line


def _run_end(lines: list[str | None], i: int) -> int:
    """End of the run of blank and dropped lines starting at i."""
    while i < len(lines) and not (lines[i] or "").strip():
        i += 1
    return i


def drop_lines(lines: list[str | None]) -> list[str]:
    """Remove the dropped lines (None) with the blank lines that only separated them."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = _run_end(lines, i)
        if j == i:
            out.append(lines[i] or "")
            i += 1
            continue
        run = lines[i:j]
        dropped = [k for k, item in enumerate(run) if item is None]
        if not dropped:
            out.extend(item or "" for item in run)
        else:
            before, after = dropped[0], len(run) - dropped[-1] - 1
            # At the end only the text's own trailing blanks stay; at the start its leading ones.
            keep = after if j == len(lines) else before if i == 0 else max(before, after)
            out.extend([""] * keep)
        i = j
    return out


def strip_invented(text: str, stored: Collection[str], kuhy: str = "") -> tuple[str, list[str]]:
    """(text without the links Claude made up, the removed links in order).

    Invented: an image whose source is not a stored asset, or any link to a
    missing asset, unless the same target appears in `kuhy` (his span, his
    brief, the pending replacement being revised). A line holding nothing but
    such links goes, with its blank separator; inline the spaces close up.
    Fails closed with LinkError when an invented link survives the cut.
    """
    ok = _targets(kuhy)
    stripped: list[str] = []
    lines: list[str | None] = []
    for line in text.split("\n"):
        before = len(stripped)
        clean = _strip_line(line, ok, stored, stripped)
        lines.append(None if len(stripped) > before and _BARE.match(clean) else clean)
    out = "\n".join(drop_lines(lines)) if stripped else text
    if left := _unallowed(out, stored, ok):
        raise LinkError(f"links images the draft does not hold: {left}")
    return out, stripped
