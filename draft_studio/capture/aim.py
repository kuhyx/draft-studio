"""What a click or a highlight aims at, exactly: never a fuzzy or "closest" match.

An aim is a visible text (`"Domeny"`, as before), or an object:

    {"text": "<exact label>" | "role": "button",   one of the two
     "within": "<exact label>",                    optional scope
     "nth": n}                                     optional: 1 = first, -1 = last

- `text`: elements whose text, aria-label, title, alt or button value equals it
  (case and whitespace folded), mapped to their clickable element (script_find.py).
- `role: "button"`: every visible button (button, [role=button], button inputs),
  for icon-only buttons with no text, e.g. the "⋮" menu of a table row.
- `within`: exactly one element must show this label (the anchor: a group
  heading, a table cell). The scope is the smallest element around the anchor
  that holds at least one candidate; only candidates inside it count. In a
  grouped sidebar that is the group; in a flat one, or a row with several
  buttons, it may hold several, and `nth` picks one by document order.
- More than one candidate left is `ambiguous-target` (with `seen`: each match's
  surroundings), none is `no-target`: Claude corrects the aim, nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]
MAX_TEXT = 200
MAX_NTH = 50
ROLES = ("button",)
KEYS = {"text", "role", "within", "nth"}


class SpecError(ValueError):
    """The spec is malformed; `detail` names the field (spec.py re-exports it)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Aim:
    """One exact target: `text` or `role`, optionally inside `within`, optionally the `nth` match."""

    text: str | None = None
    role: str | None = None
    within: str | None = None
    nth: int | None = None

    @property
    def plain(self) -> bool:
        """Only a text: written as a bare string, as specs always did."""
        return self.role is None and self.within is None and self.nth is None

    def label(self) -> str:
        """Human-readable form (permission cards, errors Claude reads)."""
        out = self.text or str(self.role)
        if self.within is not None:
            out += f" within „{self.within}”"
        return out if self.nth is None else f"{out} #{self.nth}"

    def to_json(self) -> str | Json:
        """The wire shape `parse_aim` reads back."""
        if self.plain:
            return str(self.text)
        fields = {"text": self.text, "role": self.role, "within": self.within, "nth": self.nth}
        return {k: v for k, v in fields.items() if v is not None}

    def js(self) -> Json:
        """The object the page script's `__ds.aim` takes."""
        return {"text": self.text, "role": self.role, "within": self.within, "nth": self.nth}


def clean_text(value: object, detail: str) -> str:
    """A non-empty label up to MAX_TEXT characters, stripped; SpecError(detail) otherwise."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise SpecError(detail)
    return value.strip()


def _nth(value: object, detail: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not value or abs(value) > MAX_NTH:
        raise SpecError(detail)
    return value


def parse_aim(raw: object, detail: str) -> Aim:
    """A validated Aim, or SpecError(detail)."""
    if not isinstance(raw, dict):
        return Aim(clean_text(raw, detail))
    if set(raw) - KEYS or ("text" in raw) == ("role" in raw) or raw.get("role", "button") not in ROLES:
        raise SpecError(detail)
    text = clean_text(raw["text"], detail) if "text" in raw else None
    within = clean_text(raw["within"], detail) if "within" in raw else None
    return Aim(text, raw.get("role"), within, _nth(raw.get("nth"), detail))
