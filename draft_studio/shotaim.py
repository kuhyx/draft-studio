"""The aims and steps in Claude's screenshot requests, normalised.

Structured output makes every schema property required (prompts.AIM), so an
unset field arrives as a sentinel: "" for text, role and within, 0 for nth.
They are normalised away here into the capture API's aim (capture/aim.py):
None (no target), a bare text, or {"text" | "role", "within"?, "nth"?}.
Older held records wrote a highlight as a bare string and a step as
{"kind", "text"} (no `lands`); both still parse.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Json = dict[str, Any]
# text, role, within, nth; "" and 0 mean unset.
Aim4 = tuple[str, str, str, int]
# kind, aim, lands: where a click leads ("" unset, "home", a URL), see capture/already.py.
Step3 = tuple[str, Aim4, str]
KINDS = ("click", "wait", "goto")


class ShotError(ValueError):
    """One request is unusable; `code` is what the drop is recorded as."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def text_of(raw: Mapping[str, Any], key: str, default: str | None = None) -> str:
    """A stripped string field; ShotError("bad-spec") when it is missing or not a string."""
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ShotError("bad-spec")
    return value.strip()


def wire_aim(aim: Aim4) -> str | Json | None:
    """The capture API's aim: None for no target, a bare text when that is all."""
    text, role, within, nth = aim
    if not text and not role:
        return None
    if text and not within and not nth:
        return text
    out: Json = {"text": text} if text else {"role": role}
    return out | ({"within": within} if within else {}) | ({"nth": nth} if nth else {})


def aim_json(aim: Aim4) -> Json:
    """Claude's own shape of an aim, every key present."""
    return dict(zip(("text", "role", "within", "nth"), aim, strict=True))


def parse_aim(raw: object) -> Aim4:
    """A bare string (older records) or Claude's object with sentinels; a text wins over a role."""
    if isinstance(raw, str):
        return (raw.strip(), "", "", 0)
    if not isinstance(raw, dict):
        raise ShotError("bad-spec")
    text, role, within = (text_of(raw, k, "") for k in ("text", "role", "within"))
    nth = raw.get("nth", 0)
    if role not in ("", "button") or isinstance(nth, bool) or not isinstance(nth, int):
        raise ShotError("bad-spec")
    return (text, "" if text else role, within, nth)


def parse_step(raw: object) -> Step3:
    """(kind, aim, lands) of one step; wait and goto keep only their text (goto: the URL), never a lands."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise ShotError("bad-spec")
    kind, aim, lands = str(raw["kind"]), parse_aim(raw), text_of(raw, "lands", "")
    if kind != "click":
        aim, lands = (aim[0], "", "", 0), ""
    if (not aim[0] and not aim[1]) or not (
        lands in ("", "home") or lands.startswith(("http://", "https://"))
    ):
        raise ShotError("bad-spec")
    return kind, aim, lands


def wire_step(kind: str, aim: Aim4, lands: str = "") -> Json:
    """One step in the capture API's form."""
    if kind != "click":
        return {kind: aim[0]}
    return {"click": wire_aim(aim)} | ({"lands": lands} if lands else {})
