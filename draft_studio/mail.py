"""The mail a Gmail draft is written from, and the Gmail interface both paths share."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

Json = dict[str, Any]


class GmailError(RuntimeError):
    """The Gmail draft could not be written."""


class MailError(ValueError):
    """The draft cannot become a mail (e.g. images over Gmail's size cap); `code` is the API code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Mail:
    """One mail as the connector's create_draft takes it.

    `attachments` are inline images whose `content` is a file reference
    ("sha256:<hex>:<absolute path>"); `<img src="cid:attN">` in `html` points at
    attachments[N].
    """

    to: list[str]
    subject: str
    text: str
    html: str
    attachments: list[Json] = field(default_factory=list)

    def payload(self) -> Json:
        """create_draft fields, empty ones left out (the connector treats absent and empty alike)."""
        full: Json = {
            "to": self.to,
            "subject": self.subject,
            "body": self.text,
            "htmlBody": self.html,
            "attachments": self.attachments,
        }
        return {key: value for key, value in full.items() if value}


class Gmail(Protocol):
    """Create or update one Gmail draft per mail."""

    def upsert(self, mail: Mail, gmail_id: str, studio_id: str) -> Json:
        """Write `mail`; return {"id", "created"}.

        gmail_id is the stored Gmail draft id ("" for none); studio_id is the
        draft studio draft the mail comes from (the outbound hook checks it).
        """
        ...
