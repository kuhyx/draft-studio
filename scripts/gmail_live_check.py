#!/usr/bin/env python3
"""Live Gmail check through the real server route, one step per call (each under a minute).

Start a scratch server first: scratch data dir, fake Claude, real Gmail connector:

    systemd-run --user --collect --unit ds-live-gmail --working-directory "$PWD" \\
      --setenv DRAFT_STUDIO_DATA=$HOME/data/claude-scratch/ds-live/data \\
      --setenv DRAFT_STUDIO_CLAUDE=fake --setenv DRAFT_STUDIO_GMAIL=cli \\
      .venv/bin/python -m draft_studio --port 8796

Then:
    python3 scripts/gmail_live_check.py setup --to <your own address>  # approved mail, one inline PNG
    python3 scripts/gmail_live_check.py press <id>    # Gmail button; prints {"id", "created"}
    python3 scripts/gmail_live_check.py refused <id>  # edits the text; the press must be refused (409)

The mail goes only to the address you name (yours: the connector attaches
files only to a mail with a recipient) and is never sent. Check the Gmail draft (attachment
present: get_draft FULL_CONTENT), then delete it. Exit 0 pass, 1 fail.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import urllib.error
import urllib.request
import zlib
from typing import Any

SUBJECT = "draft-studio test draft (safe to delete)"


def png() -> bytes:
    """A 16x16 solid PNG, built in code."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes((30, 140, 200)) * 16) * 16
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def call(base: str, method: str, path: str, body: object = None, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    ctype = "image/png" if raw is not None else "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=55) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def setup(base: str, to: str) -> int:
    _, draft = call(base, "POST", "/api/drafts", {"kind": "mail"})
    did = str(draft["id"])
    _, asset = call(base, "POST", f"/api/drafts/{did}/assets?name=dot.png", raw=png())
    text = f"Inline image check:\n\n![dot](assets/{asset['name']})\n\nEnd."
    call(base, "PUT", f"/api/drafts/{did}", {"text": text, "to": to, "subject": SUBJECT, "phase": "editing"})
    status, _ = call(base, "POST", f"/api/drafts/{did}/approve", {"text": text})
    print(json.dumps({"draft": did, "approve": status}))
    return 0 if status == 200 else 1


def press(base: str, did: str) -> int:
    status, out = call(base, "POST", f"/api/drafts/{did}/gmail", {})
    print(json.dumps({"status": status, "out": out}))
    return 0 if status == 200 else 1


def refused(base: str, did: str) -> int:
    _, state = call(base, "GET", f"/api/drafts/{did}")
    call(base, "PUT", f"/api/drafts/{did}", {"text": state["text"] + " Edited."})
    status, out = call(base, "POST", f"/api/drafts/{did}/gmail", {})
    print(json.dumps({"status": status, "out": out}))
    return 0 if status == 409 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("setup", "press", "refused"))
    parser.add_argument("draft", nargs="?", default="")
    parser.add_argument("--base", default="http://127.0.0.1:8796")
    parser.add_argument("--to", default="")
    args = parser.parse_args()
    if args.step == "setup":
        if not args.to:
            parser.error("setup needs --to <your own address>")
        return setup(args.base, args.to)
    return press(args.base, args.draft) if args.step == "press" else refused(args.base, args.draft)


if __name__ == "__main__":
    sys.exit(main())
