"""What a headless `claude -p` Gmail run did, read from its JSON transcript, and the checks on it.

Every mismatch raises GmailError: exactly one draft write, carrying exactly
the approved fields, whose own result holds the claimed id (the connector
refuses in a plain result, without is_error); an update only on the stored id;
a replace (stored draft with files) creates first, then deletes only the
stored id, once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from draft_studio.mail import GmailError, Json, Mail

_P = "mcp__claude_ai_Gmail__"
CREATE, GET, UPDATE, LIST, DELETE = (
    f"{_P}{n}" for n in ("create_draft", "get_draft", "update_draft", "list_drafts", "delete_draft")
)
ALLOWED_TOOLS = (CREATE, GET, UPDATE, LIST)
REPLACE_TOOLS = (CREATE, GET, LIST, DELETE)
SEND_TOOLS = (f"{_P}send_message", f"{_P}reply", f"{_P}forward")
FORBIDDEN_TOOLS = (*SEND_TOOLS, DELETE)
# StructuredOutput is how --json-schema returns the answer; it touches no mail.
SAFE_BUILTINS = ("ToolSearch", "StructuredOutput")


@dataclass(frozen=True)
class Call:
    name: str
    input: Json
    result: str | None  # None: no tool_result came back
    error: bool


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return (
        "".join(b.get("text", "") for b in content if isinstance(b, dict))
        if isinstance(content, list)
        else ""
    )


def calls(stdout: str) -> list[Call]:
    """Tool calls in order, each with its result."""
    uses: list[dict[str, Any]] = []
    results: dict[str, tuple[str, bool]] = {}
    for msg in json.loads(stdout):
        content = msg.get("message", {}).get("content") if isinstance(msg, dict) else None
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                uses.append(block)
            elif block.get("type") == "tool_result":
                results[block.get("tool_use_id", "")] = (
                    _text(block.get("content")),
                    bool(block.get("is_error")),
                )
    out: list[Call] = []
    for use in uses:
        res = results.get(use.get("id", ""))
        out.append(Call(use["name"], use.get("input") or {}, res[0] if res else None, bool(res and res[1])))
    return out


def _same_fields(call: Call, payload: Json) -> bool:
    skip = {"draftId"} if call.name == UPDATE else set()
    got = {k: v for k, v in call.input.items() if k not in skip and v not in ("", [], None, False)}
    return got == payload


@dataclass(frozen=True)
class Plan:
    """One upsert: the mail, the stored Gmail id, and the tools that flow may use."""

    mail: Mail
    gmail_id: str

    @property
    def replace(self) -> bool:
        """A stored draft with files is replaced (create, then delete), never updated."""
        return bool(self.mail.attachments and self.gmail_id)

    @property
    def file_dirs(self) -> list[str]:
        """Folders of the files the attachments reference ("sha256:<hex>:<path>")."""
        return sorted({str(Path(a["content"].split(":", 2)[2]).parent) for a in self.mail.attachments})

    @property
    def allowed(self) -> tuple[str, ...]:
        return REPLACE_TOOLS if self.replace else ALLOWED_TOOLS

    @property
    def forbidden(self) -> tuple[str, ...]:
        return SEND_TOOLS if self.replace else FORBIDDEN_TOOLS


def check(calls: list[Call], out: Json, plan: Plan) -> None:
    """Raise unless the transcript did exactly what the result claims."""
    gmail_id, payload = plan.gmail_id, plan.mail.payload()
    bad = [c.name for c in calls if c.name not in plan.allowed and c.name not in SAFE_BUILTINS]
    if bad:
        raise GmailError(f"unexpected tools used: {bad}")
    writes = [i for i, c in enumerate(calls) if c.name in (CREATE, UPDATE)]
    if len(writes) != 1:
        raise GmailError("no draft was written" if not writes else "more than one draft write")
    at = writes[0]
    write = calls[at]
    if not _same_fields(write, payload):
        raise GmailError("draft fields differ from the approved mail")
    if write.result is None or write.error:
        raise GmailError("the draft write failed")
    if not isinstance(out.get("id"), str) or not out["id"] or not isinstance(out.get("created"), bool):
        raise GmailError(f"bad upsert result {out!r}")
    # The connector refuses in a plain result (no is_error), so the id must come back in it.
    if out["id"] not in write.result:
        raise GmailError("the draft write did not return the claimed id")
    if out["created"] and write.name != CREATE:
        raise GmailError("claimed a new draft without create_draft")
    if not out["created"] and (
        write.name != UPDATE or write.input.get("draftId") != gmail_id or out["id"] != gmail_id
    ):
        raise GmailError("claimed an update without update_draft on the stored id")
    if any(c.name == GET and c.input.get("draftId") != gmail_id for c in calls):
        raise GmailError("read a draft other than the stored one")
    deletes = [i for i, c in enumerate(calls) if c.name == DELETE]
    if plan.replace:
        if (
            out["id"] == gmail_id
            or len(deletes) != 1
            or deletes[0] < at
            or calls[deletes[0]].input.get("draftId") != gmail_id
        ):
            raise GmailError(
                "the stored draft was not replaced: new draft first, then delete the stored id once"
            )
    elif gmail_id and write.name == CREATE and not any(c.name == GET for c in calls[:at]):
        raise GmailError("created a new draft without checking the stored one")
