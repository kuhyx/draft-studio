"""What a capture may click and where it must stop.

A capture reads pages, it never acts on them. A click is refused (and becomes a
permission request) when its target is a form field, a form's submit button,
or its label matches the pl+en deny-list below. Labels are compared after
`normalize`, by word start, so "ZAPŁAĆ", "zapłać teraz" and "Orders" all hit.
Over-blocking is fine: it only asks kuhy.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

DENY = (
    "zaplac", "place", "zamawiam", "zamow", "kup", "oplac", "usun", "anuluj", "zakoncz udostepnianie",
    "wyloguj", "potwierdz", "pay", "order", "buy", "delete", "cancel", "remove", "confirm", "submit",
    "logout", "log out", "sign out", "end shar", "stop shar",
)  # fmt: skip
_DENY_RE = [(stem, re.compile(r"(?<![a-z0-9])" + stem.replace(" ", r"\s+"))) for stem in DENY]
_LOGIN_PATH = re.compile(r"/(login|log-in|signin|sign-in|logowanie|zaloguj|auth)(/|\.[a-z]+)?$")
_FOLD = str.maketrans({"ł": "l", "ß": "ss", "ø": "o", "đ": "d"})


@dataclass(frozen=True)
class Target:
    """The element a click would hit, as the page script describes it (see script.py)."""

    ref: int
    tag: str
    text: str = ""
    aria: str = ""
    title: str = ""
    value: str = ""
    submit: bool = False
    field: bool = False

    @classmethod
    def from_js(cls, raw: dict[str, Any]) -> Target:
        """From the page script's plain object; unknown keys are ignored."""
        return cls(
            ref=int(raw["ref"]),
            tag=str(raw.get("tag") or ""),
            text=str(raw.get("text") or ""),
            aria=str(raw.get("aria") or ""),
            title=str(raw.get("title") or ""),
            value=str(raw.get("value") or ""),
            submit=bool(raw.get("submit")),
            field=bool(raw.get("field")),
        )


@dataclass(frozen=True)
class Refusal:
    """Why a click needs kuhy's permission: reason input | submit | deny-list, detail for the UI."""

    reason: str
    detail: str


def normalize(text: str) -> str:
    """Casefolded, diacritics stripped (ł -> l), whitespace collapsed."""
    folded = unicodedata.normalize("NFKD", text.casefold().translate(_FOLD))
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(plain.split())


def refusal(target: Target) -> Refusal | None:
    """None when the click is safe, else the reason it needs permission."""
    if target.field:
        return Refusal("input", target.tag)
    if target.submit:
        return Refusal("submit", target.tag)
    for label in (target.text, target.aria, target.title, target.value):
        plain = normalize(label)
        for stem, pattern in _DENY_RE:
            if pattern.search(plain):
                return Refusal("deny-list", stem)
    return None


def is_login_url(url: str) -> bool:
    """The path looks like a login page's (the URL kuhy aims at, or the one a redirect landed on)."""
    return bool(_LOGIN_PATH.search(urlsplit(url).path.lower()))


def is_login_page(url: str, has_password: bool) -> bool:
    """A visible password field, or a login-looking path, means kuhy must log in first."""
    return has_password or is_login_url(url)


def origin(url: str) -> str:
    """scheme://host[:port] of a URL."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
