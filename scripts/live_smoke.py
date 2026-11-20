#!/usr/bin/env python3
"""Live smoke run: one throwaway brief through a real headless `claude -p`.

Checks intake questions, a first draft, an "ask" answer and a rewrite come
back, then probes (read-only: list_drafts) whether a headless `claude -p`
can reach the claude.ai Gmail connector. Creates no Gmail draft.

Usage: python3 scripts/live_smoke.py [--skip-gmail]
Exit: 0 all passed, 1 a step failed. Prints one JSON report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draft_studio.claude import ClaudeError, CliClaude, Context
from draft_studio.gmail import _tools_used

BRIEF = "A two-sentence note to a neighbour asking them to water my plants while I am away for a week."
BIN = Path(os.environ.get("DRAFT_STUDIO_CLAUDE_BIN") or Path.home() / ".local/bin/claude")


def gmail_probe() -> dict[str, object]:
    """Can headless claude -p call the Gmail connector? Read-only list_drafts."""
    argv = [
        str(BIN),
        "-p",
        "Call the Gmail list_drafts tool once with maxResults 1. Reply with only the number of drafts.",
        "--model",
        "sonnet",
        "--restricted",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--allowedTools",
        "mcp__claude_ai_Gmail__list_drafts",
        "--disallowedTools",
        "mcp__claude_ai_Gmail__send_message,mcp__claude_ai_Gmail__create_draft,WebSearch,WebFetch",
        "--output-format",
        "json",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False, cwd="/tmp")
    try:
        used = _tools_used(proc.stdout)
    except json.JSONDecodeError:
        used = []
    ok = proc.returncode == 0 and "mcp__claude_ai_Gmail__list_drafts" in used
    return {
        "connector_reachable": ok,
        "tools_used": used,
        "path": "b: claude -p + Gmail connector" if ok else "a: OAuth token",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-gmail", action="store_true")
    args = parser.parse_args()
    claude = CliClaude(binary=BIN)
    report: dict[str, object] = {"binary": str(BIN)}
    ctx = Context(kind="text", lang="", brief=BRIEF, intake=[], text="")
    try:
        t0 = time.monotonic()
        intake = claude.intake(ctx)
        report["intake"] = intake
        answers = [
            {"q": q, "a": "Keep it friendly; I leave on Monday; the key is under the mat."}
            for q in intake["questions"]
        ]
        ctx = Context(kind="text", lang=intake["lang"], brief=BRIEF, intake=answers, text="")
        draft = claude.draft(ctx)
        report["draft"] = draft["text"]
        text = draft["text"]
        end = text.find(".") + 1 or len(text)
        span_ctx = Context(
            kind="text",
            lang=intake["lang"],
            brief=BRIEF,
            intake=answers,
            text=text,
            sel=(0, end),
            instruction="Is this sentence polite enough?",
        )
        report["ask"] = claude.ask(span_ctx)
        rewrite_ctx = Context(
            kind="text",
            lang=intake["lang"],
            brief=BRIEF,
            intake=answers,
            text=text,
            sel=(0, end),
            instruction="Make it shorter.",
        )
        report["rewrite"] = claude.rewrite(rewrite_ctx)
        report["claude_seconds"] = round(time.monotonic() - t0, 1)
    except ClaudeError as exc:
        report["error"] = str(exc)
    if not args.skip_gmail:
        report["gmail"] = gmail_probe()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    ok = (
        "error" not in report
        and bool(report.get("draft"))
        and bool(report.get("ask"))
        and bool(report.get("rewrite"))
    )
    gmail = report.get("gmail")
    if isinstance(gmail, dict):
        ok = ok and bool(gmail["connector_reachable"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
