"""A fake browser tab for capture tests: elements are visible texts."""

from __future__ import annotations

from draft_studio.capture.policy import Target


class FakePage:
    """Elements are visible texts; `effects` maps a clicked text to what the click would send."""

    def __init__(self, texts: list[str], effects: dict[str, list[tuple[str, str]]] | None = None) -> None:
        self.texts = texts
        self.effects = effects or {}
        self.where = ""
        self.password = False
        self.calls: list[tuple[object, ...]] = []
        self.highlight_ok = True

    def goto(self, url: str) -> None:
        self.where = url
        self.calls.append(("goto", url))

    def url(self) -> str:
        return self.where

    def has_password(self) -> bool:
        return self.password

    def find(self, text: str) -> list[Target]:
        return [Target(ref=i, tag="button", text=t) for i, t in enumerate(self.texts) if t == text]

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
