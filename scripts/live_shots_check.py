#!/usr/bin/env python3
"""Live check: a step-by-step guide gets one real screenshot per step.

Incident (2026-11-27, tests/test_prompts.py): asked for a screenshot of each
step of a panel guide, Claude shot only the start page and wrote "(bez zrzutu
ekranu ...)" notes into the other steps. This replays that request with the
real `claude -p` on a 5-step guide (open, log in, 3 clicks) taken on the loopback test site
(tests/capture_live/site), in two runs that each stay under the 60 s cap:

  python3 scripts/live_shots_check.py ask OUT.json    one real rewrite call; checks the captures
  python3 scripts/live_shots_check.py shoot OUT.json  takes them on headless chromium

Pass: one framed capture per click step, all on the guide's start URL, `steps` = the
clicks of the steps before, `highlight` = this step's click, and the
replacement is the span unchanged plus marker lines (so no note about
screenshots, in any language). `shoot` passes when each one is stored with
its red frame. Scratch data lives under $DS_E2E_TMP (tests/e2e/launch.py),
never the live data dir. Exit: 0 pass, 1 a check failed; prints a JSON report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draft_studio.capture.browser import Chromium
from draft_studio.capture.service import CaptureService
from draft_studio.claude import ClaudeError, CliClaude, Context
from draft_studio.shotreq import markers, parse_shot
from draft_studio.store import Store
from tests.capture_live.conftest import serve_site
from tests.capture_live.pngread import red_pixels
from tests.e2e.launch import chromium, scratch_dir

# The incident's shape: a real-looking panel address, a login step, then clicks.
# Claude only sees this text; `shoot` points START at the served test site.
START = "https://panel.example.pl"
CLICKS = ("Domeny", "DNS", "Dodaj rekord")
STEPS = (
    f"1. Otworzyć {START} (panel firmy, u której jest adres).",
    "2. Zalogować się swoim loginem i hasłem.",
    "3. Kliknąć „Domeny”.",
    "4. Kliknąć „DNS”.",
    "5. Kliknąć przycisk „Dodaj rekord”.",
)
HEAD = "Cześć,\n\ntak dodasz rekord DNS w panelu klienta:\n\n"
TEXT = HEAD + "\n".join(STEPS) + "\n\nPozdrawiam"
BRIEF = "Poradnik dla znajomej: jak dodać rekord DNS w panelu klienta."
# kuhy's own words from the incident.
ASK = "I would like screenshots with red arrow pointing to what to click for each step"
BIN = Path(os.environ.get("DRAFT_STUDIO_CLAUDE_BIN") or Path.home() / ".local/bin/claude")
MIN_RED = 3000


def context() -> Context:
    """The rewrite call: the steps selected inside a short Polish guide."""
    start = len(HEAD)
    return Context(
        kind="text", lang="pl", brief=BRIEF, intake=[], text=TEXT, sel=(start, start + len("\n".join(STEPS))),
        instruction=ASK,
    )  # fmt: skip


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


def problems(answer: dict[str, Any]) -> list[str]:
    """What is wrong with the answer, empty when it has the per-step shape."""
    out: list[str] = []
    replacement, caps = answer["replacement"], answer["captures"]
    by_key = {c.get("key"): c for c in caps if isinstance(c, dict)}
    placed = [by_key[k] for k in markers(replacement) if k in by_key]
    if len(placed) != len(caps):
        out.append(f"{len(caps)} captures, {len(placed)} with a marker line")
    # A shot of the start page (no clicks, no frame) may sit by the steps without one.
    framed = [c for c in placed if c.get("highlight") or c.get("steps")]
    if len(framed) != len(CLICKS):
        out.append(f"want {len(CLICKS)} framed captures, one per click step, got {len(framed)}")
    for i, cap in enumerate(framed):
        clicks = [s.get("text") for s in cap.get("steps", []) if s.get("kind") == "click"]
        if i >= len(CLICKS) or clicks != list(CLICKS[:i]) or cap.get("highlight") != CLICKS[i]:
            out.append(f"capture {i + 1}: steps {clicks} highlight {cap.get('highlight')!r}")
    out.extend(f"url {c.get('url')!r}" for c in caps if c.get("url") != START)
    kept = [line for line in _lines(replacement) if not markers(line)]
    if kept != _lines("\n".join(STEPS)):
        out.append(f"step text changed: {kept}")
    return out


def ask(path: Path) -> int:
    """One real `claude -p` rewrite; the answer and its problems go to `path` and stdout."""
    t0 = time.monotonic()
    try:
        answer = CliClaude(binary=BIN).rewrite(context())
    except ClaudeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    report = {"seconds": round(time.monotonic() - t0, 1), **answer, "problems": problems(answer)}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not report["problems"] else 1


def shoot(path: Path) -> int:
    """Take the recorded captures on headless chromium against the served test site."""
    answer = json.loads(path.read_text(encoding="utf-8"))
    tmp = scratch_dir("ds-shots-")
    store = Store(tmp / "data")
    service = CaptureService(
        store, Chromium(tmp / "data" / "capture-profile", binary=chromium(), timeout=40.0)
    )
    draft = str(store.create("text")["id"])
    shots: list[dict[str, Any]] = []
    with serve_site() as site:
        for raw in answer["captures"]:
            spec = parse_shot(raw).spec()
            spec["url"] = f"{site.base}/index.html" if spec["url"] == START else spec["url"]
            status, body = service.capture(draft, spec)
            shot: dict[str, Any] = {"key": spec["name"], "status": status, **body}
            if status == 200:
                shot["red"] = red_pixels(store.asset(draft, body["asset"])[0])[2]
            shots.append(shot)
    ok = bool(shots) and all(s["status"] == 200 and s["red"] > MIN_RED for s in shots)
    print(json.dumps({"dir": str(tmp), "shots": shots, "ok": ok}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ask", "shoot"))
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    return ask(args.out) if args.mode == "ask" else shoot(args.out)


if __name__ == "__main__":
    sys.exit(main())
