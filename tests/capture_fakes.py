"""Fakes for capture tests: a browser tab whose elements are visible texts, and a scripted CDP link."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from draft_studio.capture.aim import Aim
from draft_studio.capture.policy import Target


class FakePage:
    """Elements are visible texts; `effects` maps a clicked text to what the click would send.

    `scopes[anchor]` lists the elements in the scope of that `within` anchor (aim.py); a text
    starting with "btn:" is an icon-only button (found by role, no text of its own).
    """

    def __init__(
        self,
        texts: list[str],
        effects: dict[str, list[tuple[str, str]]] | None = None,
        scopes: dict[str, list[str]] | None = None,
        redirects: dict[str, str] | None = None,
    ) -> None:
        self.texts = texts
        self.redirects = redirects or {}  # a site that sends a logged-in session elsewhere
        self.effects = effects or {}
        self.scopes = scopes or {}
        self.anchor_count: dict[str, int] = {}
        self.where = ""
        self.password = False
        self.calls: list[tuple[object, ...]] = []
        self.highlight_ok = True

    def goto(self, url: str) -> None:
        self.where = self.redirects.get(url, url)
        self.calls.append(("goto", url))

    def url(self) -> str:
        return self.where

    def has_password(self) -> bool:
        return self.password

    def find(self, aim: Aim) -> list[Target]:
        pool = self.scopes.get(aim.within, []) if aim.within else self.texts
        hits = [t for t in pool if (t.startswith("btn:") if aim.role else t == aim.text)]
        if aim.nth:
            index = aim.nth - 1 if aim.nth > 0 else len(hits) + aim.nth
            hits = hits[index : index + 1] if 0 <= index < len(hits) else []
        return [Target(ref=i, tag="button", text=t) for i, t in enumerate(hits)]

    def anchors(self, text: str) -> int:
        return self.anchor_count.get(text, 1 if text in self.scopes else 0)

    def around(self, targets: list[Target]) -> list[str]:
        return [f"near {t.text}" for t in targets]

    @contextmanager
    def fresh(self) -> Iterator[None]:
        self.calls.append(("fresh",))
        saved = self.where
        try:
            yield
        finally:
            self.where = saved
            self.calls.append(("unfresh",))

    def labels(self) -> list[str]:
        return self.texts[:2]

    def wait_for(self, text: str) -> bool:
        return text in self.texts

    def click(
        self, target: Target, allow_submit: bool, allowed: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        self.calls.append(("click", target.text, allow_submit, list(allowed)))
        sent = self.effects.get(target.text, [])
        blocked = [r for r in sent if r not in allowed and not (r[0] == "SUBMIT" and allow_submit)]
        if target.text == "Login":
            self.password = True
        return blocked

    def highlight(self, target: Target) -> bool:
        self.calls.append(("highlight", target.text))
        return self.highlight_ok

    def clear(self) -> None:
        self.calls.append(("clear",))

    def screenshot(self) -> bytes:
        self.calls.append(("shot",))
        return b"\x89PNG"


class FakeCdp:
    """Records sends; `answers[method]` is a value or a callable(params) for call() results."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, dict[str, Any]]] = []
        self.answers: dict[str, Any] = {}
        self.handlers: dict[str, Any] = {}
        self.now = 0.0
        self.on_pump: list[Any] = []

    def on(self, method: str, handler: Any) -> None:
        self.handlers[method] = handler

    def send(self, method: str, session: str | None = None, **params: Any) -> int:
        self.sent.append((method, session, params))
        return len(self.sent)

    def call(self, method: str, session: str | None = None, **params: Any) -> dict[str, Any]:
        self.send(method, session, **params)
        answer = self.answers.get(method, {})
        out = answer(params) if callable(answer) else answer
        if isinstance(out, Exception):
            raise out
        return out

    def pump(self, seconds: float) -> None:
        self.now += seconds
        if self.on_pump:
            self.on_pump.pop(0)()

    def clock(self) -> float:
        return self.now

    def methods(self) -> list[str]:
        return [m for m, _, _ in self.sent]


def paused(
    method: str,
    url: str,
    rid: str = "r1",
    session: str = "s1",
    headers: dict[str, str] | None = None,
    network: str | None = None,
) -> dict[str, Any]:
    """A Fetch.requestPaused event (`request.headers` / `networkId` only when given, as for a bare request)."""
    request: dict[str, Any] = {"method": method, "url": url}
    if headers is not None:
        request["headers"] = headers
    params: dict[str, Any] = {"requestId": rid, "request": request}
    if network is not None:
        params["networkId"] = network
    return {"sessionId": session, "params": params}


def will_send(method: str, url: str, network: str, session: str = "s1") -> dict[str, Any]:
    """A Network.requestWillBeSent event: the renderer started a request (it pauses later)."""
    return {"sessionId": session, "params": {"requestId": network, "request": {"method": method, "url": url}}}


def expr_key(params: dict[str, Any]) -> str:
    """The call after the idempotent install prefix."""
    return str(params["expression"]).rsplit(";\n", 1)[-1]
