"""Gmail draft upsert. Never sends: sending stays in Gmail, with kuhy.

The live path is CliGmail (gmail_cli.py): a headless `claude -p` limited to the
connector's draft tools. FakeGmail is a JSON-file mailbox for tests and the
E2E run; it mirrors the connector: a draft with files is replaced, never
updated, and a file reference must name a file whose bytes match its hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from draft_studio.gmail_cli import CliGmail
from draft_studio.mail import Gmail, GmailError, Json, Mail
from draft_studio.transcript import ALLOWED_TOOLS, FORBIDDEN_TOOLS

__all__ = ["ALLOWED_TOOLS", "FORBIDDEN_TOOLS", "CliGmail", "FakeGmail", "Gmail", "GmailError", "make_gmail"]

_REF = re.compile(r"^sha256:([0-9a-f]{64}):(/.+)$")


def _check_ref(content: str) -> None:
    """What the connector does with a file reference: the file must exist and match the hash."""
    m = _REF.match(content)
    try:
        ok = m is not None and hashlib.sha256(Path(m.group(2)).read_bytes()).hexdigest() == m.group(1)
    except OSError:
        ok = False
    if not ok:
        raise GmailError(f"bad file reference {content[:80]!r}")


class FakeGmail:
    """A JSON-file mailbox for tests and the E2E run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sent = 0
        self._lock = threading.Lock()

    def drafts(self) -> dict[str, dict[str, Any]]:
        """All drafts in the fake mailbox."""
        if not self.path.exists():
            return {}
        data: dict[str, dict[str, Any]] = json.loads(self.path.read_text(encoding="utf-8"))
        return data

    def _put(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def upsert(self, mail: Mail, gmail_id: str, studio_id: str) -> Json:
        """Update in place when the id exists and the mail has no files, else make a new draft."""
        for att in mail.attachments:
            _check_ref(att["content"])
        with self._lock:
            data = self.drafts()
            keep = gmail_id in data and not mail.attachments
            data.pop(gmail_id, None)
            new_id = gmail_id if keep else "r-" + secrets.token_hex(4)
            data[new_id] = {
                "to": mail.to,
                "subject": mail.subject,
                "body": mail.text,
                "htmlBody": mail.html,
                "attachments": mail.attachments,
                "studioId": studio_id,
            }
            self._put(data)
            return {"id": new_id, "created": not keep}

    def delete(self, draft_id: str) -> None:
        """Simulate kuhy deleting the draft in Gmail."""
        with self._lock:
            data = self.drafts()
            data.pop(draft_id, None)
            self._put(data)


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
