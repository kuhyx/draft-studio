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
partial walk would show the wrong pages. A stepless capture of a login URL shoots logged
out even when Claude did not flag it `fresh` (and is no second start). Every URL (start,
goto, fresh) must be written in kuhy's text (shots.py). A held record without `walks` (made
before walkthroughs) holds only standalone captures.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Any

from draft_studio.capture import policy
from draft_studio.shotaim import Aim4, ShotError, wire_aim
from draft_studio.shotreq import Shot, parse_shots

Json = dict[str, Any]


def unit(rec: Json, key: str) -> list[str]:
    """The keys that run together with `key`: its walk, in order, or itself."""
    walk = rec["shots"][key]["shot"].get("walk") or ""
    return list(rec.get("walks", {}).get(walk, [key])) if walk else [key]


def _login_page_shot(shot: Shot) -> bool:
    """A stepless session capture of a login URL: the picture of that page, whatever Claude flagged."""
    return not shot.fresh and not shot.steps and policy.is_login_url(shot.url)


def _fold(text: str) -> str:
    """Case and whitespace folded, as capture matches a text (capture/script_find.py)."""
    return " ".join(text.split()).casefold()


def _same(framed: Aim4, clicked: Aim4) -> bool:
    """Both aim at one element: the same text, or (icon-only) the same role inside the same scope."""
    if framed[0] or clicked[0]:
        return _fold(framed[0]) == _fold(clicked[0])
    scope = (framed[1], _fold(framed[2]))
    return bool(framed[1] and framed[2]) and scope == (clicked[1], _fold(clicked[2]))


def _first_click(shot: Shot) -> int | None:
    """Index of the click that runs on the page the previous shot showed: waits only before it."""
    for i, (kind, _, _) in enumerate(shot.steps):
        if kind != "wait":
            return i if kind == "click" else None
    return None


def align(shots: Sequence[Shot], prefer: str = "") -> list[Shot]:
    """The walk with every shot's highlight and the click right after it on one element agreeing.

    Live, draft 462fb46c512a: two „Domeny”; one fix call moved the highlight to nth 1, another
    the next click to nth 2, so the walk framed one and clicked the other (each fix call sees
    only its own capture). The click takes the highlight's aim (`within`, `nth`; its `lands`
    stays), except next to `prefer`, the capture a fix just changed: its side wins, so a
    deliberate retarget of the click moves the frame with it and is never undone. A shot taken
    logged out shows another page than the one the next click runs on: never paired.
    """
    out = list(shots)
    for k in range(len(out) - 1):
        shot, nxt = out[k], out[k + 1]
        i = _first_click(nxt)
        if shot.fresh or _login_page_shot(shot) or i is None or not _same(shot.highlight, nxt.steps[i][1]):
            continue
        kind, clicked, lands = nxt.steps[i]
        if nxt.key == prefer:
            out[k] = replace(shot, highlight=clicked)
        else:
            out[k + 1] = replace(
                nxt, steps=(*nxt.steps[:i], (kind, shot.highlight, lands), *nxt.steps[i + 1 :])
            )
    return out


def walk_spec(shots: Sequence[Shot]) -> tuple[Json, list[int]]:
    """(the capture spec of one walk, the index of each capture's shot step in it); aligned first."""
    shots = align(shots)
    others = {s.url for s in shots if not s.fresh and not _login_page_shot(s)}
    starts = others or {s.url for s in shots if not s.fresh}
    if len(starts) != 1 or any(s.fresh and s.steps for s in shots):
        raise ShotError("bad-spec")
    steps: list[Json] = []
    at: list[int] = []
    for shot in shots:
        steps += shot.wire_steps()
        at.append(len(steps))
        body: Json = {"name": shot.key, "highlight": wire_aim(shot.highlight)}
        logged_out = shot.fresh or (bool(others) and _login_page_shot(shot))
        steps.append({"shot": body | ({"fresh": True, "url": shot.url} if logged_out else {})})
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
            shots.update((s.key, s) for s in align([shots[k] for k in members]))
        except ShotError:
            bad += walks.pop(walk)
    run = {k: s for k, s in shots.items() if k not in bad and (s.walk in walks or (not s.walk and k in keys))}
    dropped = [{"key": k, "reason": "bad-spec" if k in bad else "no-request"} for k in keys if k not in run]
    order = [k for k in keys if k in run] + [k for k in run if k not in keys]
    return {k: run[k] for k in order}, walks, dropped
