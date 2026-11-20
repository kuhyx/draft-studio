"""Gmail draft upsert: update the stored draft, create only when gone, never send."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio import gmail
from draft_studio.gmail import ALLOWED_TOOLS, CliGmail, FakeGmail, GmailError, make_gmail


def test_fake_create_update_recreate(tmp_path: Path) -> None:
    g = FakeGmail(tmp_path / "gmail-fake.json")
    first = g.upsert("a@b.c", "Hi", "Body", "")
    assert first["created"] is True
    again = g.upsert("a@b.c", "Hi 2", "Body 2", first["id"])
    assert again == {"id": first["id"], "created": False}
    assert g.drafts()[first["id"]] == {"to": "a@b.c", "subject": "Hi 2", "body": "Body 2"}
    assert len(g.drafts()) == 1
    g.delete(first["id"])
    third = g.upsert("a@b.c", "Hi 3", "Body 3", first["id"])
    assert third["created"] is True
    assert third["id"] != first["id"]
    assert g.sent == 0


def tool_msgs(*names: str, result: dict[str, Any] | None = None) -> str:
    msgs: list[dict[str, Any]] = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": n}]}} for n in names
    ]
    msgs.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}})
    msgs.append(
        {"type": "result", "is_error": False, "structured_output": result or {"id": "r1", "created": True}}
    )
    return json.dumps(msgs)


class Rec:
    def __init__(self, out: str, code: int = 0) -> None:
        self.out, self.code, self.argv = out, code, []

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.argv = argv
        return subprocess.CompletedProcess(argv, self.code, self.out, "err")


def test_cli_argv_only_draft_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Rec(tool_msgs("ToolSearch", "mcp__claude_ai_Gmail__create_draft"))
    monkeypatch.setattr(gmail.subprocess, "run", rec)
    out = CliGmail(binary=Path("/bin/claude")).upsert("a@b.c", "S", "B", "")
    assert out == {"id": "r1", "created": True}
    argv = rec.argv
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert set(allowed) == set(ALLOWED_TOOLS)
    assert not any("send" in t or "forward" in t or "reply" in t for t in allowed)
    denied = argv[argv.index("--disallowedTools") + 1]
    assert "mcp__claude_ai_Gmail__send_message" in denied
    assert "--strict-mcp-config" not in argv  # the connector must load
    assert "--bare" not in argv
    prompt = argv[argv.index("-p") + 1]
    assert "a@b.c" in prompt and "never send" in prompt.lower()


def test_cli_existing_id_in_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Rec(
        tool_msgs(
            "mcp__claude_ai_Gmail__get_draft",
            "mcp__claude_ai_Gmail__update_draft",
            result={"id": "r9", "created": False},
        )
    )
    monkeypatch.setattr(gmail.subprocess, "run", rec)
    out = CliGmail(binary=Path("/bin/claude")).upsert("a@b.c", "S", "B", "r9")
    assert out == {"id": "r9", "created": False}
    assert "r9" in rec.argv[rec.argv.index("-p") + 1]


@pytest.mark.parametrize(
    "out,code",
    [
        (tool_msgs("mcp__claude_ai_Gmail__send_message"), 0),
        (tool_msgs("mcp__claude_ai_Gmail__create_draft", result={"id": 1, "created": True}), 0),
        (tool_msgs("mcp__claude_ai_Gmail__create_draft", result={"id": "x", "created": "yes"}), 0),
        (tool_msgs(), 0),
        (tool_msgs("mcp__claude_ai_Gmail__create_draft"), 2),
        ("nope", 0),
    ],
)
def test_cli_rejects(monkeypatch: pytest.MonkeyPatch, out: str, code: int) -> None:
    monkeypatch.setattr(gmail.subprocess, "run", Rec(out, code))
    with pytest.raises(GmailError):
        CliGmail(binary=Path("/bin/claude")).upsert("a@b.c", "S", "B", "")


def test_cli_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(argv: list[str], **kw: Any) -> None:
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(gmail.subprocess, "run", slow)
    with pytest.raises(GmailError):
        CliGmail(binary=Path("/bin/claude")).upsert("a@b.c", "S", "B", "")


def test_make_gmail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "fake")
    assert isinstance(make_gmail(tmp_path), FakeGmail)
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "cli")
    monkeypatch.setenv("DRAFT_STUDIO_CLAUDE_BIN", "/x/claude")
    g = make_gmail(tmp_path)
    assert isinstance(g, CliGmail)
    assert g.binary == Path("/x/claude")
    monkeypatch.setenv("DRAFT_STUDIO_GMAIL", "zzz")
    with pytest.raises(ValueError):
        make_gmail(tmp_path)
