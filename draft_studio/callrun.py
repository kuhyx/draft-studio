"""`python -m draft_studio.callrun <call dir>`: one Claude call, in a process the server does not own.

The server writes `call.json` ({"method", "ctx", "capture"?, "error"?}) and starts this in its own
systemd unit (detach.py), so a server restart does not touch it. The answer goes to `result.json`
({"ok": true, "value"} or {"ok": false, "error"}), written atomically; `pid` tells the server the
call is alive (durable.py). A call that fails still ends in a result: only a killed process has none.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from draft_studio.atomic import read_json, write_json, write_text
from draft_studio.callio import ctx_from_json
from draft_studio.claude import Claude, ClaudeError
from draft_studio.claude_fake import make_claude
from draft_studio.durable import proc_start

LOG = logging.getLogger("draft_studio")
METHODS = ("intake", "draft", "ask", "rewrite", "fix_capture")


def _invoke(claude: Claude, call: dict[str, Any]) -> Any:
    if call["method"] not in METHODS:
        raise ClaudeError(f"not a call: {call['method']}")
    ctx = ctx_from_json(call["ctx"])
    extra = [call[key] for key in ("capture", "error") if key in call]
    return getattr(claude, call["method"])(ctx, *extra)


def main(argv: list[str] | None = None, factory: Callable[[], Claude] = make_claude) -> int:
    """Run the call in `argv[0]`; always 0 once a result is written."""
    cdir = Path((argv if argv is not None else sys.argv[1:])[0])
    write_text(cdir / "pid", f"{os.getpid()} {proc_start(os.getpid())}")
    call: dict[str, Any] = read_json(cdir / "call.json") or {}
    try:
        result: dict[str, Any] = {"ok": True, "value": _invoke(factory(), call)}
    except ClaudeError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:
        LOG.exception("call %s failed", cdir)
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    write_json(cdir / "result.json", result)
    return 0


if __name__ == "__main__":  # pragma: no cover - the unit's entry point
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
