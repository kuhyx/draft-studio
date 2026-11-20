"""The one Claude interface, a headless `claude -p` adapter and a deterministic fake.

Every call sends the whole context: brief, intake answers, the whole current
draft with the selected span marked, and kuhy's instruction. The CLI adapter
runs kuhy's subscription (`claude -p`, OAuth). It never falls back to the API
and never passes `--bare` (that mode only accepts an API key). User hooks,
CLAUDE.md, tools and MCP servers are all switched off so a call is a pure
text-in, JSON-out function.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SEL_OPEN = "[[[SEL]]]"
SEL_CLOSE = "[[[/SEL]]]"
MAX_INTAKE_ROUNDS = 4

SYSTEM_PROMPT = """You co-write text drafts with the author in a draft editor.
Rules:
- Reply only through the JSON schema you are given.
- Write in the draft's language (BCP 47 tag in the context). If the tag is empty, use the brief's language.
- When a span is marked with [[[SEL]]] ... [[[/SEL]]], work on that span only.
  A rewrite returns only the replacement for that span, without the markers,
  fitting seamlessly into the surrounding text and keeping the draft's tone.
- Never invent facts the author did not give; ask instead during intake.
- The draft is markdown source."""

INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "lang": {"type": "string", "description": "BCP 47 tag of the brief's language, e.g. en, pl"},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lang", "questions"],
    "additionalProperties": False,
}
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "subject": {"type": "string", "description": "mail subject; empty for a text draft"},
    },
    "required": ["text", "subject"],
    "additionalProperties": False,
}
ASK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"replacement": {"type": "string"}},
    "required": ["replacement"],
    "additionalProperties": False,
}


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
    extra: dict[str, Any] = field(default_factory=dict)

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
        parts.append(f"Current draft:\n{draft or '(none yet)'}")
        if self.instruction:
            parts.append(f"Instruction:\n{self.instruction}")
        return "\n\n".join(parts)


class Claude(Protocol):
    """The four things the editor asks of Claude."""

    def intake(self, ctx: Context) -> dict[str, Any]:
        """Return {"lang", "questions"}; empty questions means ready to draft."""
        ...

    def draft(self, ctx: Context) -> dict[str, str]:
        """Return {"text", "subject"} for the first draft."""
        ...

    def ask(self, ctx: Context) -> str:
        """Answer a question about the marked span. Never changes the draft."""
        ...

    def rewrite(self, ctx: Context) -> str:
        """Replacement for the marked span (plain rewrite or by instruction)."""
        ...


def _strings(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ClaudeError(f"{key} must be a list of strings")
    return list(value)


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

    def draft(self, ctx: Context) -> dict[str, str]:
        """Write the first draft (and a subject for a mail)."""
        out = self._run(
            "Write the first full draft as markdown. For a mail, also propose a subject line; "
            "for a text draft, return an empty subject.",
            ctx,
            DRAFT_SCHEMA,
        )
        return {"text": _string(out, "text"), "subject": _string(out, "subject")}

    def ask(self, ctx: Context) -> str:
        """Answer the instruction (a question) about the marked span."""
        out = self._run(
            "Answer the author's question about the marked span. Do not rewrite the draft.", ctx, ASK_SCHEMA
        )
        return _string(out, "answer")

    def rewrite(self, ctx: Context) -> str:
        """Replacement text for the marked span."""
        task = (
            "Change the marked span following the instruction. Return only its replacement."
            if ctx.instruction
            else "Rewrite the marked span so it reads better. Return only its replacement."
        )
        return _string(self._run(task, ctx, REWRITE_SCHEMA), "replacement")


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


_PL_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]|\b(napisz|jest|dzień)\b", re.IGNORECASE)


class FakeClaude:
    """Deterministic stand-in for tests and the headless E2E.

    Markers in the brief/instruction steer it: `[noq]` skips intake,
    `[2rounds]` asks a second round, `[fail]` raises ClaudeError.
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
