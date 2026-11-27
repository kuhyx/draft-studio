"""Gmail through the claude.ai Gmail connector in a headless `claude -p`.

The allowlist, not the prompt, enforces "never send": send/reply/forward are
also explicitly disallowed. delete_draft is allowed only to replace a stored
draft that has files (update_draft would drop them). The transcript is checked
afterwards and every mismatch raises: exactly one draft write, carrying exactly
the approved fields, on the stored id or as a new draft whose id the connector
returned; a replace deletes only the stored id, once, after the new draft exists.

The subprocess env carries DRAFT_STUDIO_DRAFT_ID so kuhy's outbound hook
(~/.claude/hooks/outbound_draft_gate.py) can check the approval too when it loads.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from draft_studio.claude import ClaudeError, parse_result
from draft_studio.mail import GmailError, Json, Mail
from draft_studio.store import default_data_dir
from draft_studio.transcript import Plan, calls, check

# Built-ins --restricted keeps that no step needs, so none may run (a file reference
# miss once sent the model off to Glob). Read stays: seen live, the connector fills a
# file reference only when the session may Read the file (cwd or --add-dir).
DENIED_BUILTINS = (
    "Bash",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "NotebookEdit",
    "Task",
    "Skill",
    "SendMessage",
    "CronDelete",
    "ScheduleWakeup",
    "PushNotification",
    "EnterWorktree",
    "ExitWorktree",
    "WebSearch",
    "WebFetch",
)
# Linux caps one argv string at 128 KiB (MAX_ARG_STRLEN) and the prompt is one.
MAX_PROMPT_BYTES = 120_000

UPSERT_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "created": {"type": "boolean"}},
    "required": ["id", "created"],
    "additionalProperties": False,
}


def session_cwd() -> Path:
    """Empty working dir of the `claude -p` run, under the data dir (never /tmp: tmpfs, wiped on reboot)."""
    cwd = default_data_dir() / "gmail-cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def subprocess_env(studio_id: str) -> dict[str, str]:
    """The env of the `claude -p` run: ours plus the draft studio id the outbound hook checks."""
    return {**os.environ, "DRAFT_STUDIO_DRAFT_ID": studio_id}


_SAME = (
    "with exactly the fields of the JSON below, copied unchanged "
    "(an attachment content is a file reference the app fills in)"
)


def _prompt(plan: Plan) -> str:
    gid = plan.gmail_id
    if plan.replace:
        step = (
            "This mail has files and update_draft would drop them, so never call update_draft. "
            f'Call create_draft once {_SAME}. Then call delete_draft once with draftId "{gid}" to '
            "remove the old version (if it is already gone, that is fine). "
            "Return the new draft id with created=true."
        )
    elif gid:
        step = (
            f'First call get_draft with draftId "{gid}". If it exists, call update_draft once with '
            f'draftId "{gid}" and {_SAME.removeprefix("with ")}, and return that id with created=false. '
            f"If it does not exist, call create_draft once {_SAME} "
            "and return the new draft id with created=true."
        )
    else:
        step = f"Call create_draft once {_SAME} and return the new draft id with created=true."
    fields = json.dumps(plan.mail.payload(), ensure_ascii=False)
    return (
        "Write a Gmail draft. Never send anything: only draft tools are allowed. "
        f"{step}\n\nFields (JSON):\n{fields}"
    )


@dataclass
class CliGmail:
    """Gmail through the claude.ai Gmail connector in a headless `claude -p`."""

    binary: Path
    model: str = "sonnet"
    timeout: int = 300

    def upsert(self, mail: Mail, gmail_id: str, studio_id: str) -> Json:
        """Write the draft; return its id and whether it is a new draft."""
        plan = Plan(mail, gmail_id)
        prompt = _prompt(plan)
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise GmailError("mail too long for one claude -p prompt")
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
            ",".join(plan.allowed),
            "--disallowedTools",
            ",".join((*plan.forbidden, *DENIED_BUILTINS)),
            # --restricted confines file access to the cwd; the connector fills a file
            # reference only from a folder the session may read (seen live).
            *(arg for d in plan.file_dirs for arg in ("--add-dir", d)),
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(UPSERT_SCHEMA),
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                cwd=session_cwd(),
                env=subprocess_env(studio_id),
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise GmailError(f"claude did not run: {exc}") from exc
        if proc.returncode != 0:
            raise GmailError(f"claude exited {proc.returncode}: {proc.stderr[-500:]}")
        try:
            out = parse_result(proc.stdout)
            used = calls(proc.stdout)
        except (ClaudeError, ValueError, KeyError, AttributeError) as exc:
            raise GmailError(f"unreadable transcript: {exc}") from exc
        check(used, out, plan)
        return {"id": out["id"], "created": out["created"]}
