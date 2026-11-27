"""The one Claude interface and the headless `claude -p` adapter (the fake: claude_fake.py).

Every call sends the whole context: brief, intake answers, the whole current
draft with the selected span marked, and kuhy's instruction. The CLI adapter
runs kuhy's subscription (`claude -p`, OAuth). It never falls back to the API
and never passes `--bare` (that mode only accepts an API key). User hooks,
CLAUDE.md, tools and MCP servers are all switched off so a call is a pure
text-in, JSON-out function.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from draft_studio.prompts import (
    ASK_SCHEMA,
    DRAFT_SCHEMA,
    FIX_SCHEMA,
    INTAKE_SCHEMA,
    REWRITE_SCHEMA,
    SYSTEM_PROMPT,
    fix_task,
)

SEL_OPEN = "[[[SEL]]]"
SEL_CLOSE = "[[[/SEL]]]"
MAX_INTAKE_ROUNDS = 4


class ClaudeError(RuntimeError):
    """Claude could not answer (CLI missing, failed, timed out, bad output)."""


def mark_span(text: str, start: int, end: int) -> str:
    """Wrap text[start:end] in the selection markers."""
    if not 0 <= start <= end <= len(text):
        raise ValueError(f"bad span {start}..{end} for length {len(text)}")
    return text[:start] + SEL_OPEN + text[start:end] + SEL_CLOSE + text[end:]


@dataclass(frozen=True)
class Context:
    """Everything one Claude call sees."""

    kind: str
    lang: str
    brief: str
    intake: list[dict[str, str]]
    text: str
    subject: str = ""
    sel: tuple[int, int] | None = None
    instruction: str = ""
    # The pending replacement for the marked span that the instruction comments on.
    previous: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    # Names in drafts/<id>/assets/: the only images Claude may link (api.py drops any other).
    assets: tuple[str, ...] = ()

    def render(self) -> str:
        """The prompt body: labelled sections, the draft last."""
        qa = "\n".join(f"Q: {item['q']}\nA: {item['a']}" for item in self.intake) or "(none)"
        draft = mark_span(self.text, *self.sel) if self.sel else self.text
        parts = [
            f"Draft kind: {self.kind}",
            f"Draft language tag: {self.lang or '(detect from brief)'}",
            f"Brief:\n{self.brief}",
            f"Intake answers:\n{qa}",
        ]
        if self.kind == "mail":
            parts.append(f"Mail subject: {self.subject}")
        stored = "\n".join(f"![](assets/{name})" for name in self.assets)
        parts.append(f"Stored images:\n{stored or '(none: write no image links)'}")
        parts.append(f"Current draft:\n{draft or '(none yet)'}")
        if self.previous:
            parts.append(f"Your pending replacement for the marked span:\n{self.previous}")
        if self.instruction:
            parts.append(f"Instruction:\n{self.instruction}")
        return "\n\n".join(parts)


class Claude(Protocol):
    """The five things the editor asks of Claude."""

    def intake(self, ctx: Context) -> dict[str, Any]:
        """Return {"lang", "questions"}; empty questions means ready to draft."""
        ...

    def draft(self, ctx: Context) -> dict[str, Any]:
        """Return {"text", "subject", "captures"} for the first draft (captures: prompts.CAPTURE)."""
        ...

    def ask(self, ctx: Context) -> str:
        """Answer a question about the marked span. Never changes the draft."""
        ...

    def rewrite(self, ctx: Context) -> dict[str, Any]:
        """{"replacement", "captures"} for the marked span (plain rewrite or by instruction)."""
        ...

    def fix_capture(self, ctx: Context, capture: dict[str, Any], error: dict[str, Any]) -> dict[str, Any]:
        """A corrected capture after the browser found no unique element (error carries `seen`)."""
        ...


def _strings(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ClaudeError(f"{key} must be a list of strings")
    return list(value)


def _captures(obj: dict[str, Any]) -> list[Any]:
    """The requested screenshots; each entry is checked by shots.py, which drops a bad one."""
    value = obj.get("captures", [])
    if not isinstance(value, list):
        raise ClaudeError("captures must be a list")
    return value


def _string(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ClaudeError(f"missing string {key}")
    return value


@dataclass
class CliClaude:
    """Headless `claude -p` on kuhy's subscription."""

    binary: Path
    model: str = "sonnet"
    timeout: int = 300

    def _run(self, task: str, ctx: Context, schema: dict[str, Any]) -> dict[str, Any]:
        argv = [
            str(self.binary),
            "-p",
            f"Task: {task}\n\n{ctx.render()}",
            "--model",
            self.model,
            "--restricted",
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--system-prompt",
            SYSTEM_PROMPT,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout, check=False, cwd="/tmp"
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ClaudeError(f"claude did not run: {exc}") from exc
        if proc.returncode != 0:
            raise ClaudeError(f"claude exited {proc.returncode}: {proc.stderr[-500:]}")
        return parse_result(proc.stdout)

    def intake(self, ctx: Context) -> dict[str, Any]:
        """Ask the questions needed before drafting (none when the brief is enough)."""
        out = self._run(
            "Intake. Decide which questions you still need answered before writing a good first draft. "
            "Ask only what matters; return an empty list when the brief and answers are enough. "
            "Write the questions in the brief's language and detect that language.",
            ctx,
            INTAKE_SCHEMA,
        )
        return {"lang": _string(out, "lang"), "questions": _strings(out.get("questions"), "questions")}

    def draft(self, ctx: Context) -> dict[str, Any]:
        """Write the first draft (and a subject for a mail), with the screenshots it asks for."""
        out = self._run(
            "Write the first full draft as markdown. For a mail, also propose a subject line; "
            "for a text draft, return an empty subject.",
            ctx,
            DRAFT_SCHEMA,
        )
        return {"text": _string(out, "text"), "subject": _string(out, "subject"), "captures": _captures(out)}

    def ask(self, ctx: Context) -> str:
        """Answer the instruction (a question) about the marked span."""
        out = self._run(
            "Answer the author's question about the marked span. Do not rewrite the draft.", ctx, ASK_SCHEMA
        )
        return _string(out, "answer")

    def rewrite(self, ctx: Context) -> dict[str, Any]:
        """Replacement text for the marked span, with the screenshots it asks for."""
        if ctx.previous:
            how = "following the author's comment (the instruction)" if ctx.instruction else "to read better"
            task = f"Revise your pending replacement for the marked span {how}. Return only the new one."
        elif ctx.instruction:
            task = "Change the marked span following the instruction. Return only its replacement."
        else:
            task = "Rewrite the marked span so it reads better. Return only its replacement."
        out = self._run(task, ctx, REWRITE_SCHEMA)
        return {"replacement": _string(out, "replacement"), "captures": _captures(out)}

    def fix_capture(self, ctx: Context, capture: dict[str, Any], error: dict[str, Any]) -> dict[str, Any]:
        """The capture with step texts corrected from the error's `seen` labels."""
        out = self._run(fix_task(capture, error), ctx, FIX_SCHEMA).get("capture")
        if not isinstance(out, dict):
            raise ClaudeError("missing capture")
        return out


def parse_result(stdout: str) -> dict[str, Any]:
    """Pull structured_output out of `claude -p --output-format json`."""
    try:
        messages = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError("claude output is not JSON") from exc
    if not isinstance(messages, list):
        raise ClaudeError("claude output is not a message list")
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    if not results:
        raise ClaudeError("claude returned no result")
    last = results[-1]
    if last.get("is_error"):
        raise ClaudeError(f"claude error: {last.get('result')}")
    out = last.get("structured_output")
    if not isinstance(out, dict):
        raise ClaudeError("claude returned no structured output")
    return out
