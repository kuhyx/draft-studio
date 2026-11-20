#!/usr/bin/env python3
"""One live Gmail check through the real server route (needs kuhy's approval).

Starts a scratch server (scratch data dir, fake Claude, real Gmail connector),
creates one mail draft, presses the Gmail action twice and checks the second
press updated the same Gmail draft. Never sends. Leaves exactly one Gmail
draft (Subject below) for kuhy to look at and delete.

Usage: python3 scripts/gmail_live_check.py   Exit 0 pass, 1 fail.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUBJECT = "draft-studio test draft (safe to delete)"


def call(base: str, method: str, path: str, body: object | None = None) -> dict[str, object]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=400) as r:
        out: dict[str, object] = json.loads(r.read())
        return out


def main() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = Path(tempfile.mkdtemp(prefix="ds-gmail-"))
    env = {
        **os.environ,
        "DRAFT_STUDIO_DATA": str(data),
        "DRAFT_STUDIO_CLAUDE": "fake",
        "DRAFT_STUDIO_GMAIL": "cli",
    }
    server = subprocess.Popen([sys.executable, "-m", "draft_studio", "--port", str(port)], cwd=REPO, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/version", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        draft = call(base, "POST", "/api/drafts", {"kind": "mail"})
        did = str(draft["id"])
        body = {
            "to": "test@example.com",
            "subject": SUBJECT,
            "text": "First version of the body.",
            "toAuthor": "you",
            "subjectAuthor": "you",
        }
        call(base, "PUT", f"/api/drafts/{did}", body)
        first = call(base, "POST", f"/api/drafts/{did}/gmail", {})
        call(base, "PUT", f"/api/drafts/{did}", {"text": "Second version of the body (updated)."})
        second = call(base, "POST", f"/api/drafts/{did}/gmail", {})
        ok = first["created"] is True and second == {"id": first["id"], "created": False}
        print(json.dumps({"first": first, "second": second, "pass": ok}))
        return 0 if ok else 1
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
