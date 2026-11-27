"""Shared harness of the flow tests: the running app, one browser, and editor helpers."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from tests.e2e.cdp import Browser

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "web" / "dist"


class Env:
    def __init__(self, base: str, data: Path, locales: Path, b: Browser, shots: Path) -> None:
        self.base, self.data, self.locales, self.b, self.shots = base, data, locales, b, shots

    def shot(self, name: str) -> None:
        self.b.screenshot(self.shots / f"{name}.png")

    def api(self, path: str) -> Any:
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def post(self, path: str, body: dict[str, Any], method: str = "POST") -> Any:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    # editor helpers
    def doc(self) -> str:
        return str(self.b.js("draftStudio.view.state.doc.toString()"))

    def select(self, start: int, end: int) -> None:
        self.b.js(
            "draftStudio.view.focus();"
            f" draftStudio.view.dispatch({{selection: {{anchor: {start}, head: {end}}}}})"
        )

    def select_text(self, needle: str) -> tuple[int, int]:
        i = self.doc().index(needle)
        self.select(i, i + len(needle))
        return i, i + len(needle)

    def caret(self, pos: int) -> None:
        self.select(pos, pos)

    def tinted(self) -> str:
        return str(
            self.b.js("[...document.querySelectorAll('.cm-claude')].map(e => e.textContent).join('|')")
        )

    def saved(self, draft_id: str) -> dict[str, Any]:
        self.b.wait("document.querySelector('#status').dataset.status === 'saved'", what="saved")
        state: dict[str, Any] = self.api(f"/api/drafts/{draft_id}")
        return state

    def draft_id(self) -> str:
        return str(self.b.js("new URLSearchParams(location.search).get('id')"))

    def idle(self) -> None:
        self.b.wait("!document.body.dataset.busy", timeout=15, what="claude idle")

    def propose(self, needle: str, instruction: str = "") -> None:
        """Select `needle`, ask Claude to rewrite (or change, with `instruction`) it, wait for the diff."""
        self.select_text(needle)
        if instruction:
            self.b.click(".sel-toolbar .sel-change")
            self.b.wait("document.activeElement?.tagName === 'TEXTAREA'")
            self.b.type(instruction)
            self.b.click(".sel-toolbar .sel-submit")
        else:
            self.b.click(".sel-toolbar .sel-rewrite")
        self.idle()
        self.b.wait("document.querySelector('.cm-hunk') !== null", what="diff shown")

    def new_draft(self, kind: str, brief: str, rounds: tuple[tuple[str, ...], ...] = ()) -> None:
        """Start a `kind` draft from the list and answer each intake round until the text shows."""
        self.b.goto(self.base + "/")
        self.b.click(f"#new-{kind}")
        self.b.wait("location.pathname === '/d/' && document.body.dataset.ready === '1'")
        self.b.wait("!document.querySelector('#brief-form').hidden")
        self.b.click("#brief")
        self.b.type(brief)
        self.b.click("#brief-form button[type=submit]")
        for answers in rounds:
            self.b.wait(f"document.querySelectorAll('#questions textarea').length === {len(answers)}")
            for n, answer in enumerate(answers, 1):
                self.b.click(f"#questions .question:nth-child({n}) textarea")
                self.b.type(answer)
            self.b.click("#intake-form button[type=submit]")
        self.b.wait("!document.querySelector('#work').hidden", what="draft shown")
