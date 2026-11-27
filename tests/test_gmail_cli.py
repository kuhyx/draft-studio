"""CliGmail: argv, env and prompt of the headless `claude -p`, update and replace flows."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio import gmail_cli
from draft_studio.gmail import ALLOWED_TOOLS, CliGmail, GmailError
from draft_studio.mail import Mail
from tests.gmail_fixtures import (
    DELETE,
    GET,
    PICS,
    PLAIN,
    UPDATE,
    P,
    Rec,
    call,
    create,
    run,
    transcript,
)


def test_cli_reads_string_tool_results(monkeypatch: pytest.MonkeyPatch) -> None:
    msgs = json.loads(transcript(create()))
    for msg in msgs:
        for block in msg.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                block["content"] = '{"id": "r1"}'
    msgs.insert(0, {"type": "user", "message": {"content": "the prompt"}})
    assert run(monkeypatch, json.dumps(msgs))[0] == {"id": "r1", "created": True}


def test_cli_argv_env_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_DATA", "/data/x")
    out, rec = run(monkeypatch, transcript(call("ToolSearch"), create(), call("StructuredOutput")))
    assert out == {"id": "r1", "created": True}
    argv = rec.argv
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert set(allowed) == set(ALLOWED_TOOLS) and DELETE not in allowed
    assert not any("send" in t or "forward" in t or "reply" in t for t in allowed)
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert f"{P}send_message" in denied and DELETE in denied
    assert {"Glob", "Bash", "Write", "Edit"} <= set(denied) and "--add-dir" not in argv
    assert "--strict-mcp-config" not in argv and "--bare" not in argv
    prompt = argv[argv.index("-p") + 1]
    assert json.dumps(PLAIN.payload(), ensure_ascii=False) in prompt and "never send" in prompt.lower()
    env = rec.kw["env"]
    assert env["DRAFT_STUDIO_DRAFT_ID"] == "studio1" and env["DRAFT_STUDIO_DATA"] == "/data/x"
    assert env["PATH"] == os.environ["PATH"]


def test_cli_update_and_recreate(monkeypatch: pytest.MonkeyPatch) -> None:
    upd = transcript(
        call(GET, {"draftId": "r9"}, "{}"),
        call(UPDATE, {"draftId": "r9", **PLAIN.payload()}, '{"id": "r9"}'),
        result={"id": "r9", "created": False},
    )
    out, rec = run(monkeypatch, upd, gid="r9")
    assert out == {"id": "r9", "created": False}
    assert "r9" in rec.argv[rec.argv.index("-p") + 1]
    gone = transcript(
        call(GET, {"draftId": "gone"}, "not found", error=True),
        create(new_id="new"),
        result={"id": "new", "created": True},
    )
    assert run(monkeypatch, gone, gid="gone")[0] == {"id": "new", "created": True}


def test_cli_replaces_a_draft_with_files(monkeypatch: pytest.MonkeyPatch) -> None:
    t = transcript(
        create(PICS, "new"), call(DELETE, {"draftId": "old"}, "{}"), result={"id": "new", "created": True}
    )
    out, rec = run(monkeypatch, t, mail=PICS, gid="old")
    assert out == {"id": "new", "created": True}
    assert DELETE in rec.argv[rec.argv.index("--allowedTools") + 1].split(",")
    assert DELETE not in rec.argv[rec.argv.index("--disallowedTools") + 1]
    # The connector reads a file reference only from a folder the session may Read.
    assert rec.argv[rec.argv.index("--add-dir") + 1] == "/d/assets"
    assert "Read" not in rec.argv[rec.argv.index("--disallowedTools") + 1].split(",")
    prompt = rec.argv[rec.argv.index("-p") + 1]
    assert "update_draft" in prompt and "delete_draft" in prompt and '"old"' in prompt
    # Already gone in Gmail: the failed delete is fine.
    gone = transcript(
        create(PICS, "new"),
        call(DELETE, {"draftId": "old"}, "404", error=True),
        result={"id": "new", "created": True},
    )
    assert run(monkeypatch, gone, mail=PICS, gid="old")[0]["id"] == "new"
    # First press of a mail with files: a plain create, no delete allowed.
    _, first = run(monkeypatch, transcript(create(PICS)), mail=PICS)
    assert DELETE not in first.argv[first.argv.index("--allowedTools") + 1]


def test_cli_process_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(GmailError):
        run(monkeypatch, transcript(create()), code=2)
    with pytest.raises(GmailError):
        run(monkeypatch, "nope")

    def slow(argv: list[str], **kw: Any) -> None:
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(gmail_cli.subprocess, "run", slow)
    with pytest.raises(GmailError):
        CliGmail(binary=Path("/bin/claude")).upsert(PLAIN, "", "s1")


def test_cli_refuses_a_prompt_over_the_argv_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = Mail(["a@b.c"], "S", "x" * 70_000, "<p>" + "x" * 70_000 + "</p>")
    rec = Rec(transcript(create(huge)))
    monkeypatch.setattr(gmail_cli.subprocess, "run", rec)
    with pytest.raises(GmailError):
        CliGmail(binary=Path("/bin/claude")).upsert(huge, "", "s1")
    assert rec.argv == []
