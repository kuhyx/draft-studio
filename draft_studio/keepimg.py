"""A proposal never loses an image the span held (kuhy, 2026-11-27: the answer cut the old shots).

Claude rewrites a guide step and plans a new screenshot for it, so the old image line is missing from
its replacement. When that shot is then dropped, the step ends up with no picture at all. `restore`
puts back every old image line the answer lacks, unless a new shot took its place or the instruction
asked to remove images.

Matching works on whole lines against each baseline text (the span, a pending replacement being
revised): a line diff gives the changed hunks, and inside a hunk the n-th missing old image pairs with
the n-th marker line (`[[[IMG:key]]]`). A paired marker that resolved (`done`) replaces the old image;
an unresolved one becomes the old image's markdown; an old image with no marker left in its hunk is
put back after the hunk. An image counts as kept when its target (`assets/<name>`) is anywhere in the
answer, whatever its alt text says. Only images alone on a line are tracked.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from difflib import SequenceMatcher

_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)")
_MARKER_LINE = re.compile(r"^\s*\[\[\[\s*IMG\s*:([^\]\n]*)\]\]\]\s*$")
# A removal verb and an image noun, both: "remove the filler words" does not license cutting a picture.
_REMOVE = re.compile(r"\b(remove|delete|drop|usu[nń]\w*|skasuj\w*|wyrzu[cć]\w*)\b", re.IGNORECASE)
_PICTURE = re.compile(
    r"(image|screenshot|picture|photo|figure|zdj[eę]|obraz|zrzut|grafik|screen)", re.IGNORECASE
)
# Not a valid capture name (capture/spec.py NAME_RE), so it never collides with Claude's keys.
_KEY = "~kept-{n}"


def _target(line: str) -> str | None:
    """The link target of a line that is only an image, else None."""
    m = _IMAGE.fullmatch(line.strip())
    return m.group(1) if m else None


def asks_removal(instruction: str) -> bool:
    """Whether kuhy's instruction explicitly asks to remove images."""
    return bool(_REMOVE.search(instruction) and _PICTURE.search(instruction))


def _place(lines: list[str], at: dict[int, list[str]]) -> list[str]:
    """`lines` with each marker key inserted at its position, set off by blank lines."""
    out: list[str] = []
    for pos in range(len(lines) + 1):
        if pos in at:
            before = out[-1] if out else ""
            after = lines[pos] if pos < len(lines) else ""
            parts: list[str] = []
            for n, key in enumerate(at[pos]):
                if n or before.strip():
                    parts.append("")
                parts.append(f"[[[IMG:{key}]]]")
            if after.strip():
                parts.append("")
            out.extend(parts)
        if pos < len(lines):
            out.append(lines[pos])
    return out


def restore(
    answer: str, baselines: Collection[str], instruction: str, done: Mapping[str, str]
) -> tuple[str, dict[str, str]]:
    """(answer with markers for the images to put back, {marker key: image markdown} to substitute).

    `done`: the keys whose new shot exists. Pass the result's mapping to `substitute` next to `done`.
    """
    if asks_removal(instruction):
        return answer, {}
    new = answer.split("\n")
    kept = {m.group(1) for m in _IMAGE.finditer(answer)}
    extra: dict[str, str] = {}
    taken: set[str] = set()
    at: dict[int, list[str]] = {}
    for base in dict.fromkeys(baselines):
        old = base.split("\n")
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            lost = [(t, ln.strip()) for ln in old[i1:i2] if (t := _target(ln)) and t not in kept]
            free = [
                k
                for j in range(j1, j2)
                if (m := _MARKER_LINE.match(new[j])) and (k := m.group(1).strip()) not in taken
            ]
            for n, (target, md) in enumerate(lost):
                kept.add(target)
                if n < len(free):
                    taken.add(free[n])
                    if free[n] not in done:
                        extra[free[n]] = md
                    continue
                key = _KEY.format(n=len(extra))
                extra[key] = md
                at.setdefault(j2, []).append(key)
    return "\n".join(_place(new, at)), extra
