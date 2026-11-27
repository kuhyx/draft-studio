"""Walkthroughs: Claude's captures sharing a `walk` id run as ONE capture (one browser run).

kuhy (2026-11-27): the tool reproduces his screenshot walkthrough by itself.
One run means a permission (the "Zaloguj jako" POST on the cyber_Folks shares
page) is asked and granted once for every shot after it. Captures of a walk,
in the answer's order, become one capture spec (capture/__init__.py):

    each capture's own steps (the clicks since the previous shot), then
    {"shot": {"name": <key>, "highlight": <aim>}}         a `fresh` capture:
    {"shot": {"name": <key>, "highlight": ..., "fresh": true, "url": <its url>}}

The spec opens the one `url` every other capture of the walk repeats; a fresh
capture takes no steps. A walk breaking either rule, or holding one malformed
capture, drops whole: later shots depend on the clicks of earlier ones, so a
partial walk would show the wrong pages. Every URL (start, goto, fresh) must
be written in kuhy's text (shots.py). A held record without `walks` (made
before walkthroughs) holds only standalone captures.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from draft_studio.shotaim import ShotError, wire_aim
from draft_studio.shotreq import Shot, parse_shots

Json = dict[str, Any]


def unit(rec: Json, key: str) -> list[str]:
    """The keys that run together with `key`: its walk, in order, or itself."""
    walk = rec["shots"][key]["shot"].get("walk") or ""
    return list(rec.get("walks", {}).get(walk, [key])) if walk else [key]


def walk_spec(shots: Sequence[Shot]) -> tuple[Json, list[int]]:
    """(the capture spec of one walk, the index of each capture's shot step in it)."""
    starts = {s.url for s in shots if not s.fresh}
    if len(starts) != 1 or any(s.fresh and s.steps for s in shots):
        raise ShotError("bad-spec")
    steps: list[Json] = []
    at: list[int] = []
    for shot in shots:
        steps += shot.wire_steps()
        at.append(len(steps))
        body: Json = {"name": shot.key, "highlight": wire_aim(shot.highlight)}
        steps.append({"shot": body | ({"fresh": True, "url": shot.url} if shot.fresh else {})})
    return {"url": starts.pop(), "name": shots[0].key, "steps": steps, "highlight": None}, at


def spec_of(shots: Sequence[Shot]) -> Json:
    """The capture spec of one unit: a standalone capture as before, or its walk."""
    if len(shots) == 1 and not shots[0].walk and not shots[0].fresh:
        return shots[0].spec()
    return walk_spec(shots)[0]


def locate(shots: Sequence[Shot], error: Json) -> tuple[str, Json]:
    """(key of the capture a 422 belongs to, the error with `step` relative to that capture).

    A walk's step n belongs to the first shot at or after it; the shot step itself is its
    highlight (step null, as a standalone capture's highlight already is).
    """
    if len(shots) == 1 and not shots[0].walk:
        return shots[0].key, error
    _, at = walk_spec(shots)
    n = error.get("step") or 0
    i = next((i for i, a in enumerate(at) if a >= n), len(at) - 1)
    first = at[i] - len(shots[i].steps)
    return shots[i].key, {**error, "step": None if n == at[i] else n - first}


def plan(raws: list[Any], keys: Collection[str]) -> tuple[dict[str, Shot], dict[str, list[str]], list[Json]]:
    """(captures to run, {walk id: keys in order}, drops) for one answer with marker `keys`.

    A standalone capture runs only when the text has its marker; every capture of a usable
    walk runs (its clicks lead to the next shot), but only marked ones are placed.
    """
    shots, bad = parse_shots(raws)
    tainted = {str(r.get("walk") or "") for r in raws if isinstance(r, dict) and str(r.get("key")) in bad}
    walks: dict[str, list[str]] = {}
    for key, shot in shots.items():
        if shot.walk:
            walks.setdefault(shot.walk, []).append(key)
    for walk, members in list(walks.items()):
        try:
            if walk in tainted:
                raise ShotError("bad-spec")
            walk_spec([shots[k] for k in members])
        except ShotError:
            bad += walks.pop(walk)
    run = {k: s for k, s in shots.items() if k not in bad and (s.walk in walks or (not s.walk and k in keys))}
    dropped = [{"key": k, "reason": "bad-spec" if k in bad else "no-request"} for k in keys if k not in run]
    order = [k for k in keys if k in run] + [k for k in run if k not in keys]
    return {k: run[k] for k in order}, walks, dropped
