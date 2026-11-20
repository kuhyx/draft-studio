"""Gmail draft upsert. Never sends: sending stays in Gmail, with kuhy.

The live path runs a headless `claude -p` that may call only the Gmail
connector's draft tools (create, get, update, list). The allowlist, not the
prompt, enforces "never send": any send/reply/forward tool is also explicitly
disallowed, and the transcript is checked afterwards so a send would raise.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from draft_studio.claude import ClaudeError, parse_result

_P = "mcp__claude_ai_Gmail__"
ALLOWED_TOOLS = (f"{_P}create_draft", f"{_P}get_draft", f"{_P}update_draft", f"{_P}list_drafts")
FORBIDDEN_TOOLS = (f"{_P}send_message", f"{_P}reply", f"{_P}forward", f"{_P}delete_draft")
SAFE_BUILTINS = ("ToolSearch",)

UPSERT_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "created": {"type": "boolean"}},
    "required": ["id", "created"],
    "additionalProperties": False,
}


class GmailError(RuntimeError):
    """The Gmail draft could not be written."""


class Gmail(Protocol):
    """Create or update one Gmail draft per mail."""

    def upsert(self, to: str, subject: str, body: str, draft_id: str) -> dict[str, Any]:
        """Return {"id", "created"}: update draft_id if it still exists, else create."""
        ...


class FakeGmail:
    """A JSON-file mailbox for tests and the E2E run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sent = 0
        self._lock = threading.Lock()

    def drafts(self) -> dict[str, dict[str, str]]:
        """All drafts in the fake mailbox."""
        if not self.path.exists():
            return {}
        data: dict[str, dict[str, str]] = json.loads(self.path.read_text(encoding="utf-8"))
        return data

    def _put(self, data: dict[str, dict[str, str]]) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def upsert(self, to: str, subject: str, body: str, draft_id: str) -> dict[str, Any]:
        """Update in place when the id exists, else make a new draft."""
        with self._lock:
            data = self.drafts()
            created = draft_id not in data
            if created:
                draft_id = "r-" + secrets.token_hex(4)
            data[draft_id] = {"to": to, "subject": subject, "body": body}
            self._put(data)
            return {"id": draft_id, "created": created}

    def delete(self, draft_id: str) -> None:
        """Simulate kuhy deleting the draft in Gmail."""
        with self._lock:
            data = self.drafts()
            data.pop(draft_id, None)
            self._put(data)


def _tools_used(stdout: str) -> list[str]:
    names: list[str] = []
    for msg in json.loads(stdout):
        if msg.get("type") != "assistant":
            continue
        for block in msg.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                names.append(block["name"])
    return names


@dataclass
class CliGmail:
    """Gmail through the claude.ai Gmail connector in a headless `claude -p`."""

    binary: Path
    model: str = "sonnet"
    timeout: int = 300

    def upsert(self, to: str, subject: str, body: str, draft_id: str) -> dict[str, Any]:
        """Write the draft; return its id and whether it was newly created."""
        mail = json.dumps({"to": to, "subject": subject, "body": body}, ensure_ascii=False)
        step = (
            f'First call get_draft for draft id "{draft_id}". If it exists, call update_draft on that id '
            "with the mail below and return that id with created=false. If it does not exist, "
            "call create_draft and return the new draft id with created=true."
            if draft_id
            else "Call create_draft with the mail below and return the new draft id with created=true."
        )
        prompt = (
            "Write a Gmail draft. Never send anything: only draft tools are allowed. "
            f"{step} Use the body verbatim as plain text.\n\nMail (JSON):\n{mail}"
        )
        argv = [
            str(self.binary),
            "-p",
            prompt,
            "--model",
            self.model,
            "--restricted",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--system-prompt",
            "You write Gmail drafts with the draft tools only. You never send mail.",
            "--allowedTools",
            ",".join(ALLOWED_TOOLS),
            "--disallowedTools",
            ",".join((*FORBIDDEN_TOOLS, "WebSearch", "WebFetch")),
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(UPSERT_SCHEMA),
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout, check=False, cwd="/tmp"
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise GmailError(f"claude did not run: {exc}") from exc
        if proc.returncode != 0:
            raise GmailError(f"claude exited {proc.returncode}: {proc.stderr[-500:]}")
        try:
            out = parse_result(proc.stdout)
            used = _tools_used(proc.stdout)
        except ClaudeError as exc:
            raise GmailError(str(exc)) from exc
        bad = [t for t in used if t not in ALLOWED_TOOLS and t not in SAFE_BUILTINS]
        if bad:
            raise GmailError(f"unexpected tools used: {bad}")
        if not any(t in (f"{_P}create_draft", f"{_P}update_draft") for t in used):
            raise GmailError("no draft was written")
        if not isinstance(out.get("id"), str) or not isinstance(out.get("created"), bool):
            raise GmailError(f"bad upsert result {out!r}")
        # The transcript must match the claim, so a "create instead of update" never
        # slips through as a silent duplicate draft.
        if out["created"] and f"{_P}create_draft" not in used:
            raise GmailError("claimed a new draft without create_draft")
        if not out["created"] and (f"{_P}update_draft" not in used or out["id"] != draft_id):
            raise GmailError("claimed an update without update_draft on the stored id")
        if draft_id and f"{_P}create_draft" in used and f"{_P}get_draft" not in used:
            raise GmailError("created a new draft without checking the stored one")
        return {"id": out["id"], "created": out["created"]}


def make_gmail(data_dir: Path) -> Gmail:
    """Pick the Gmail path from DRAFT_STUDIO_GMAIL (cli by default, fake for tests)."""
    mode = os.environ.get("DRAFT_STUDIO_GMAIL", "cli")
    if mode == "fake":
        return FakeGmail(data_dir / "gmail-fake.json")
    if mode == "cli":
        binary = os.environ.get("DRAFT_STUDIO_CLAUDE_BIN") or str(
            Path(os.environ["HOME"]) / ".local/bin/claude"
        )
        return CliGmail(binary=Path(binary))
    raise ValueError(f"DRAFT_STUDIO_GMAIL must be cli or fake, not {mode!r}")
