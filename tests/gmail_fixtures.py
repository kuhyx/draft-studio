"""Shared pieces for the Gmail tests: sample mails and fake `claude -p` transcripts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from draft_studio import gmail_cli
from draft_studio.gmail import CliGmail
from draft_studio.mail import Mail

P = "mcp__claude_ai_Gmail__"
CREATE, GET, UPDATE, DELETE = f"{P}create_draft", f"{P}get_draft", f"{P}update_draft", f"{P}delete_draft"
ATT = {
    "content": "sha256:" + "a" * 64 + ":/d/assets/p.png",
    "filename": "p.png",
    "mimeType": "image/png",
    "inline": True,
}
PLAIN = Mail(["a@b.c"], "S", "B", "<p>B</p>")
PICS = Mail(["a@b.c"], "S", "B", '<p><img src="cid:att0" alt=""></p>', [ATT])


Call = tuple[str, dict[str, Any], str | None, bool]


def call(name: str, inp: dict[str, Any] | None = None, result: str | None = "", error: bool = False) -> Call:
    return (name, inp or {}, result, error)


def create(mail: Mail = PLAIN, new_id: str = "r1", **extra: Any) -> Call:
    return call(CREATE, {**mail.payload(), **extra}, json.dumps({"id": new_id, "threadId": "t"}))


def transcript(*calls: Call, result: dict[str, Any] | None = None) -> str:
    msgs: list[dict[str, Any]] = []
    for n, (name, inp, res, err) in enumerate(calls):
        use = {"type": "tool_use", "id": f"tu{n}", "name": name, "input": inp}
        msgs.append({"type": "assistant", "message": {"content": [use]}})
        if res is not None:
            block = {
                "type": "tool_result",
                "tool_use_id": f"tu{n}",
                "content": [{"type": "text", "text": res}],
                "is_error": err,
            }
            msgs.append({"type": "user", "message": {"content": [block]}})
    msgs.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}})
    msgs.append(
        {"type": "result", "is_error": False, "structured_output": result or {"id": "r1", "created": True}}
    )
    return json.dumps(msgs)


class Rec:
    def __init__(self, out: str, code: int = 0) -> None:
        self.out, self.code, self.argv, self.kw = out, code, [], {}

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.argv, self.kw = argv, kw
        return subprocess.CompletedProcess(argv, self.code, self.out, "err")


def run(
    monkeypatch: pytest.MonkeyPatch, out: str, mail: Mail = PLAIN, gid: str = "", code: int = 0
) -> tuple[dict[str, Any], Rec]:
    rec = Rec(out, code)
    monkeypatch.setattr(gmail_cli.subprocess, "run", rec)
    return CliGmail(binary=Path("/bin/claude")).upsert(mail, gid, "studio1"), rec
