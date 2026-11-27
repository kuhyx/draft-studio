"""A partial grant (kuhy, 2026-11-27): kuhy allows some of a card's blocked requests, not all.

`POST /api/captures/<id>/grant {"allow": [["POST", "https://..."], ...]}` sends only those
(method, URL) pairs, which must all be in the card's `blocked`; the rest of `blocked` is kept as a
denial (pending.py): failed again, never sent, no new card, the capture goes on to its shot.

A write on another origin is preceded by a CORS preflight, an OPTIONS to the same URL
(preflight.py lets only the preflight of a read through). So a pair kuhy allows takes its OPTIONS
twin along (granted), and a pair left blocked takes its twin along (denied): the preflight is never
a separate decision. A SUBMIT pair (a form action) has no preflight.
"""

from __future__ import annotations

from typing import Any

from draft_studio.capture.runner import SUBMIT, Request
from draft_studio.capture.spec import Denial, Grant

Json = dict[str, Any]
OPTIONS = "OPTIONS"


class BadAllow(ValueError):
    """The `allow` body is not a non-empty list of [method, url] string pairs."""


class NotBlocked(ValueError):
    """An allowed pair is not one the card blocked."""


def parse(body: object) -> list[Request] | None:
    """The pairs kuhy picked (methods upper-cased), or None for a body without `allow` (grant all)."""
    if body is None:
        return None
    if not isinstance(body, dict):
        raise BadAllow("body")
    raw = body.get("allow")
    if raw is None:
        return None
    pairs = raw if isinstance(raw, list) and raw else None
    if pairs is None or not all(isinstance(p, list) and len(p) == 2 for p in pairs):
        raise BadAllow("allow")
    if not all(isinstance(x, str) for p in pairs for x in p):
        raise BadAllow("allow")
    return [(str(m).upper(), str(u)) for m, u in pairs]


def with_twins(pairs: list[Request]) -> list[Request]:
    """The pairs plus the OPTIONS preflight of each write, in order, once each."""
    twins = [(OPTIONS, u) for m, u in pairs if m not in (OPTIONS, SUBMIT)]
    return list(dict.fromkeys([*pairs, *twins]))


def split(rec: Json, picked: list[Request]) -> tuple[Grant, Denial | None]:
    """(what the re-run allows, what stays denied) for card `rec`; NotBlocked before anything changes."""
    blocked = [(str(m), str(u)) for m, u in rec.get("blocked", rec["allow"]["requests"])]
    if any(p not in blocked for p in picked):
        raise NotBlocked(picked)
    allowed = with_twins(picked)
    denied = [r for r in with_twins([b for b in blocked if b not in allowed]) if r not in allowed]
    prior = [(m, u) for m, u in rec["allow"]["requests"] if (m, u) not in blocked]  # earlier same-step grants
    grant = Grant(rec["step"], bool(rec["allow"]["click"]), (*prior, *allowed))
    return grant, Denial(rec["step"], click=False, requests=tuple(denied)) if denied else None
