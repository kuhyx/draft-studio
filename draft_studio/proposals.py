"""Pending proposals of one draft, kept next to it so every open tab (and a restarted server) sees the same.

<data dir>/drafts/<id>/proposals.json:

    {"seq": <next fresh id>,
     "list": [{"id", "from", "to", "replacement", "rev"}],   sorted by `from`, spans never overlap
     "gone": {"<id>": "stale" | "accepted" | "rejected"}}    the last KEEP resolutions

`from`/`to` count UTF-16 code units of the draft text as the server holds it, like the editor does; every
write of the draft text maps them through the change (`remap`), and an edit touching a span drops it.
`rev` moves whenever the proposal changes (partial accept or reject, a revision): a resolve that carries an
older `rev` is refused (409), so a click never applies what the tab did not see. Pure functions here;
proposalapi.py runs them under the store lock.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

Json = dict[str, Any]
FILE = "proposals.json"
KEEP = 50
# Above this many character pairs the text diff is not worth it: the middle counts as one change.
DIFF_LIMIT = 2_000_000
# (from, to) of a change in the old text, and how many units it adds (negative: removes).
Change = tuple[int, int, int]


class NoProposal(KeyError):
    """No pending proposal with that id."""


class StaleProposal(ValueError):
    """The caller's `rev` is not the proposal's: it changed since the caller saw it."""


def empty() -> Json:
    return {"seq": 1, "list": [], "gone": {}}


def read(path: Path) -> Json:
    """The draft folder's proposals; a draft that never had one has none."""
    file = path / FILE
    if not file.is_file():
        return empty()
    data: Json = json.loads(file.read_text(encoding="utf-8"))
    return data


def write(path: Path, data: Json) -> None:
    tmp = path / f".{FILE}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path / FILE)


def view(data: Json) -> Json:
    """The part of the draft state the editor reads."""
    return {"proposals": data["list"], "gone": data["gone"]}


# -- UTF-16 positions over a Python string ---------------------------------------------------------
def width(text: str) -> int:
    """Length in UTF-16 code units (what the editor counts)."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def index(text: str, units: int) -> int:
    """Python index of UTF-16 position `units` (a position inside a pair rounds up)."""
    seen = 0
    for i, ch in enumerate(text):
        if seen >= units:
            return i
        seen += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def cut(text: str, start: int, end: int) -> str:
    """text[start:end] by UTF-16 positions."""
    return text[index(text, start) : index(text, end)]


def replace_span(text: str, start: int, end: int, insert: str) -> str:
    return text[: index(text, start)] + insert + text[index(text, end) :]


# -- carrying spans through a text change ----------------------------------------------------------
def changes(old: str, new: str) -> list[Change]:
    """The changed regions of `old` (UTF-16), left to right; empty when the texts are equal."""
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    mid_old, mid_new = old[head : len(old) - tail], new[head : len(new) - tail]
    if not mid_old and not mid_new:
        return []
    if len(mid_old) * len(mid_new) > DIFF_LIMIT:
        ops = [(0, len(mid_old), 0, len(mid_new))]
    else:
        match = SequenceMatcher(None, mid_old, mid_new, autojunk=False)
        ops = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in match.get_opcodes() if tag != "equal"]
    out: list[Change] = []
    for i1, i2, j1, j2 in ops:
        a, b = width(old[: head + i1]), width(old[: head + i2])
        out.append((a, b, width(new[head + j1 : head + j2]) - (b - a)))
    return out


def shift(regions: list[Change], start: int, end: int) -> tuple[int, int] | None:
    """[start, end] carried through the changes, or None when one touches it (ends included)."""
    if any(a <= end and b >= start for a, b, _ in regions):
        return None
    moved = sum(delta for _, b, delta in regions if b < start)
    return start + moved, end + moved


def remap(data: Json, old: str, new: str) -> Json:
    """The proposals after the draft text went from `old` to `new`: touched ones are dropped as stale."""
    regions = changes(old, new)
    if not regions:
        return data
    kept: list[Json] = []
    gone = dict(data["gone"])
    for p in data["list"]:
        moved = shift(regions, p["from"], p["to"])
        if moved is None:
            gone[str(p["id"])] = "stale"
        else:
            kept.append({**p, "from": moved[0], "to": moved[1]})
    return {**data, "list": kept, "gone": _recent(gone)}


def _recent(gone: Json) -> Json:
    return dict(list(gone.items())[-KEEP:])


# -- landing, accepting and rejecting --------------------------------------------------------------
def mark(data: Json, job: str) -> Json:
    """`data` with job `job` noted as landed (written in the same file write as its proposal)."""
    return {**data, "landed": [*data.get("landed", []), job][-KEEP:]}


def add(
    data: Json,
    start: int,
    end: int,
    replacement: str,
    *,
    revises: list[int],
    span: str,
    missing: Sequence[str] = (),
) -> tuple[Json, str, int | None]:
    """Land Claude's answer for [start, end): (data, outcome, id). `revises`: proposals it replaces.

    `missing`: screenshots it asked for and did not get; the proposal keeps them as `missing` (only when any).

    Outcomes: `ok`; `gone` (a proposal it revises is no longer pending); `overlap` (another one holds
    part of the span). An answer equal to the text it replaces only retires what it revised.
    """
    pending = {p["id"] for p in data["list"]}
    if not all(r in pending for r in revises):
        return data, "gone", None
    others = [p for p in data["list"] if p["id"] not in revises]
    if any(start < p["to"] and p["from"] < end for p in others):
        return data, "overlap", None
    rev = max((p["rev"] for p in data["list"] if p["id"] in revises), default=0) + 1
    pid = revises[0] if revises else data["seq"]
    new = {"id": pid, "from": start, "to": end, "replacement": replacement, "rev": rev}
    added = [] if replacement == span else [{**new, "missing": list(missing)} if missing else new]
    seq = data["seq"] if revises else data["seq"] + 1
    listed = sorted([*others, *added], key=lambda p: p["from"])
    return {**data, "seq": seq, "list": listed}, "ok", pid if added else None


def find(data: Json, pid: int, rev: object) -> Json:
    """The pending proposal `pid`; NoProposal when none, StaleProposal when `rev` is not its own."""
    for p in data["list"]:
        if p["id"] == pid:
            if rev is not None and rev != p["rev"]:
                raise StaleProposal(pid)
            found: Json = p
            return found
    raise NoProposal(pid)


def accepted(data: Json, p: Json, new_span: str) -> Json:
    """The proposals once `p`'s span holds `new_span`: the rest move along, `p` stays while it differs."""
    start, end = p["from"], p["to"]
    delta = width(new_span) - (end - start)
    out: list[Json] = []
    for q in data["list"]:
        if q["id"] == p["id"]:
            if new_span != p["replacement"]:
                out.append({**p, "to": start + width(new_span), "rev": p["rev"] + 1})
        elif q["from"] >= end:
            out.append({**q, "from": q["from"] + delta, "to": q["to"] + delta})
        else:
            out.append(q)
    gone = data["gone"] if new_span != p["replacement"] else {**data["gone"], str(p["id"]): "accepted"}
    return {**data, "list": out, "gone": _recent(gone)}


def rejected(data: Json, p: Json, replacement: str | None, span: str) -> Json:
    """The proposals once `p` is rejected: all of it, or down to `replacement` (the hunks kept)."""
    if replacement is not None and replacement != span:
        out = [
            {**q, "replacement": replacement, "rev": q["rev"] + 1} if q["id"] == p["id"] else q
            for q in data["list"]
        ]
        return {**data, "list": out}
    out = [q for q in data["list"] if q["id"] != p["id"]]
    return {**data, "list": out, "gone": _recent({**data["gone"], str(p["id"]): "rejected"})}


def claude_after(ranges: list[list[int]], start: int, end: int, size: int) -> list[list[int]]:
    """Claude's ranges once [start, end) became `size` units of Claude text (an accepted proposal)."""
    delta = size - (end - start)
    out: list[list[int]] = []
    for a, b in ranges:
        if b <= start:
            out.append([a, b])
        elif a >= end:
            out.append([a + delta, b + delta])
        else:
            if a < start:
                out.append([a, start])
            if b > end:
                out.append([start + size, b + delta])
    if size:
        out.append([start, start + size])
    merged: list[list[int]] = []
    for a, b in sorted(out):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged
