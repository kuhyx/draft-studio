"""The deterministic fake Claude (tests, headless E2E) and the adapter picker.

The picker lives here, not in claude.py, so claude.py never imports its fake.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from draft_studio.claude import Claude, ClaudeError, CliClaude, Context

_PL_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]|\b(napisz|jest|dzień)\b", re.IGNORECASE)
# What `[fakeimg]` links: the incident's shape, a screenshot nobody uploaded.
FAKE_IMAGE = "TODO-cyberfolks-01-panel.png"


class FakeClaude:
    """Deterministic stand-in for tests and the headless E2E.

    Markers in the brief/instruction steer it: `[noq]` skips intake,
    `[2rounds]` asks a second round, `[fail]` raises ClaudeError,
    `[fakeimg]` links an image the draft does not hold.
    """

    def _maybe_fail(self, ctx: Context) -> None:
        if "[fail]" in ctx.brief or "[fail]" in ctx.instruction:
            raise ClaudeError("fake failure")

    def intake(self, ctx: Context) -> dict[str, Any]:
        """Two questions, then an optional second round, then none."""
        self._maybe_fail(ctx)
        lang = ctx.lang or ("pl" if _PL_RE.search(ctx.brief) else "en")
        if "[noq]" in ctx.brief:
            return {"lang": lang, "questions": []}
        prefix = "Pytanie" if lang == "pl" else "Question"
        if not ctx.intake:
            return {"lang": lang, "questions": [f"{prefix} 1: who reads it?", f"{prefix} 2: what tone?"]}
        if "[2rounds]" in ctx.brief and len(ctx.intake) == 2:
            return {"lang": lang, "questions": [f"{prefix} 3: any deadline?"]}
        return {"lang": lang, "questions": []}

    def draft(self, ctx: Context) -> dict[str, str]:
        """Brief plus the answers, one paragraph each."""
        self._maybe_fail(ctx)
        answers = "\n\n".join(item["a"] for item in ctx.intake)
        text = f"Draft for {ctx.brief.strip()}.\n\n{answers}\n\nBest regards".strip()
        if "[fakeimg]" in ctx.brief:
            text += f"\n\n![](assets/{FAKE_IMAGE})"
        return {
            "text": text,
            "subject": f"[{ctx.lang or 'en'}] Subject from Claude" if ctx.kind == "mail" else "",
        }

    def ask(self, ctx: Context) -> str:
        """Echo the span and question, tagged with the language."""
        self._maybe_fail(ctx)
        start, end = ctx.sel or (0, 0)
        return f"[{ctx.lang or 'en'}] About '{ctx.text[start:end]}': {ctx.instruction}"

    def rewrite(self, ctx: Context) -> str:
        """Deterministic edits so diffs have both kept and changed words."""
        self._maybe_fail(ctx)
        start, end = ctx.sel or (0, len(ctx.text))
        span = ctx.text[start:end]
        if ctx.instruction == "shout":
            return span.upper()
        if ctx.instruction == "lang":
            return f"[{ctx.lang}] {span}"
        if ctx.instruction == "[fakeimg]":
            return f"{span}\n\n![](assets/{FAKE_IMAGE})"
        words = span.split(" ")
        if len(words) >= 3:
            words[1] = "2"
            return " ".join(words) + " better"
        return f"{span} rewritten"


def make_claude() -> Claude:
    """Pick the adapter from DRAFT_STUDIO_CLAUDE (cli by default, fake for tests)."""
    mode = os.environ.get("DRAFT_STUDIO_CLAUDE", "cli")
    if mode == "fake":
        return FakeClaude()
    if mode == "cli":
        binary = os.environ.get("DRAFT_STUDIO_CLAUDE_BIN") or str(
            Path(os.environ["HOME"]) / ".local/bin/claude"
        )
        return CliClaude(binary=Path(binary))
    raise ValueError(f"DRAFT_STUDIO_CLAUDE must be cli or fake, not {mode!r}")
