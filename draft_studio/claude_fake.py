"""The deterministic fake Claude (tests, headless E2E) and the adapter picker.

The picker lives here, not in claude.py, so claude.py never imports its fake.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from draft_studio.claude import Claude, ClaudeError, CliClaude, Context

_PL_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]|\b(napisz|jest|dzień)\b", re.IGNORECASE)
# What `[fakeimg]` links: the incident's shape, a screenshot nobody uploaded.
FAKE_IMAGE = "TODO-cyberfolks-01-panel.png"
_URL = re.compile(r"https?://[^\s<>()\[\]\"'„”]+")
_QUOTED = re.compile(r"„([^”\n]+)”")
SHOT_KEY = "shot-1"


def fold(text: str) -> str:
    """Case and diacritics folded, ł included (NFKD leaves it alone)."""
    plain = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in plain if not unicodedata.combining(c)).casefold()


def shot_request(where: str, also: str) -> list[dict[str, Any]]:
    """What `[shot]` asks for: the first URL in `where` (else `also`), a click per „quoted” text."""
    urls = _URL.findall(where) or _URL.findall(also)
    if not urls:
        return []
    clicks = _QUOTED.findall(where)
    steps = [{"kind": "click", "text": t} for t in clicks]
    alt = clicks[-1] if clicks else "page"
    url = urls[0].rstrip(".,;:!?")
    return [
        {"key": SHOT_KEY, "url": url, "steps": steps, "highlight": clicks[-1] if clicks else "", "alt": alt}
    ]


class FakeClaude:
    """Deterministic stand-in for tests and the headless E2E.

    Markers in the brief/instruction steer it: `[noq]` skips intake,
    `[2rounds]` asks a second round, `[fail]` raises ClaudeError,
    `[fakeimg]` links an image the draft does not hold, `[shot]` asks for a
    screenshot of the first URL in the span (a first draft: the brief),
    clicking each „quoted” text, like the real Claude reading a how-to.
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

    def draft(self, ctx: Context) -> dict[str, Any]:
        """Brief plus the answers, one paragraph each."""
        self._maybe_fail(ctx)
        answers = "\n\n".join(item["a"] for item in ctx.intake)
        text = f"Draft for {ctx.brief.strip()}.\n\n{answers}\n\nBest regards".strip()
        if "[fakeimg]" in ctx.brief:
            text += f"\n\n![](assets/{FAKE_IMAGE})"
        captures = shot_request(ctx.brief, "") if "[shot]" in ctx.brief else []
        if captures:
            text += f"\n\n[[[IMG:{SHOT_KEY}]]]"
        return {
            "text": text,
            "subject": f"[{ctx.lang or 'en'}] Subject from Claude" if ctx.kind == "mail" else "",
            "captures": captures,
        }

    def ask(self, ctx: Context) -> str:
        """Echo the span and question, tagged with the language."""
        self._maybe_fail(ctx)
        start, end = ctx.sel or (0, 0)
        return f"[{ctx.lang or 'en'}] About '{ctx.text[start:end]}': {ctx.instruction}"

    def rewrite(self, ctx: Context) -> dict[str, Any]:
        """{"replacement": _edit(ctx), "captures": [...]} ([shot]: the span, then a marker line)."""
        self._maybe_fail(ctx)
        if ctx.instruction == "[shot]":
            start, end = ctx.sel or (0, len(ctx.text))
            span = ctx.text[start:end]
            captures = shot_request(span, f"{ctx.text}\n{ctx.brief}")
            marker = f"\n\n[[[IMG:{SHOT_KEY}]]]" if captures else ""
            return {"replacement": span + marker, "captures": captures}
        return {"replacement": self._edit(ctx), "captures": []}

    def fix_capture(self, ctx: Context, capture: dict[str, Any], error: dict[str, Any]) -> dict[str, Any]:
        """Swap the failed text for the `seen` label it folds to; unchanged when none does."""
        self._maybe_fail(ctx)
        wrong = error.get("text")
        seen = {fold(label): label for label in error.get("seen", [])}
        fixed = seen.get(fold(str(wrong)), wrong)

        def swap(aim: dict[str, Any]) -> dict[str, Any]:
            return {**aim, **{k: fixed for k in ("text", "within") if aim.get(k) == wrong}}

        return {
            **capture,
            "steps": [swap(s) for s in capture["steps"]],
            "highlight": swap(capture["highlight"]),
        }

    def _edit(self, ctx: Context) -> str:
        """Deterministic edits so diffs have both kept and changed words."""
        if ctx.previous:
            # A revision drops the old replacement's last word, so it differs from both texts.
            head = ctx.previous.rsplit(" ", 1)
            return head[0] if len(head) > 1 else f"{ctx.previous} revised"
        start, end = ctx.sel or (0, len(ctx.text))
        span = ctx.text[start:end]
        if ctx.instruction == "shout":
            return span.upper()
        if ctx.instruction == "lang":
            return f"[{ctx.lang}] {span}"
        if ctx.instruction == "[fakeimg]":
            return f"{span} rewritten\n\n![](assets/{FAKE_IMAGE})"
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
