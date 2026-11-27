#!/usr/bin/env python3
"""Live check: real `claude -p`, asked like kuhy, plans his screenshot walkthrough by itself.

kuhy (2026-11-27): draft studio's own Claude, asked in the editor for screenshots
of a renewal guide he writes for the panel's owner, must produce and run the
whole walkthrough: a logged-out shot of the login page; setup on the shares page
clicking „ZALOGUJ JAKO” in the owner's row (never „ZAKOŃCZ UDOSTĘPNIANIE”); the
owner's dashboard framed on „Domeny” under „Twoje usługi”; the domains page
framed on the icon-only ⋮ of the kaliszpsycholog.pl row; the open menu framed
on „Odnów” (never clicked). The text describes the owner's path, never kuhy's
setup, and carries no note about screenshots (the earlier incident: "(bez zrzutu
ekranu ...)"). Two runs, each under the 60 s cap:

  python3 scripts/live_shots_check.py ask OUT.json    one real rewrite call; checks the walk
  python3 scripts/live_shots_check.py shoot OUT.json  runs it on headless chromium against the
                                                      loopback mimic (tests/capture_live/site/walk)

`shoot` grants the one impersonation request, like kuhy on the card, and passes
when every shot lands framed, with exactly that one write sent. Claude sees
https://panel.example.pl; `shoot` maps its two pages onto the mimic. Scratch
data lives under $DS_E2E_TMP (tests/e2e/launch.py), never the live data dir.
Exit: 0 pass, 1 a check failed; prints a JSON report.
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
from draft_studio.shotreq import Shot, allowed_url, markers
from draft_studio.store import Store
from draft_studio.walks import plan, spec_of
from tests.capture_live.conftest import serve_site
from tests.capture_live.pngread import red_pixels
from tests.capture_live.test_walk_live import framed
from tests.e2e.launch import chromium, scratch_dir

PANEL = "https://panel.example.pl"
LOGIN, SHARES = f"{PANEL}/security/login", f"{PANEL}/your-data/shares"
MIMIC = {LOGIN: "/walk/login.html", SHARES: "/walk/shares.html"}
OWNER = "GABINET PSYCHOLOGICZNY ANNA LEŚNIAK-JARECKA"
STEPS = (
    f"1. Wejdź na {LOGIN} i zaloguj się swoim loginem i hasłem.",
    "2. W „Twoje usługi” kliknij „Domeny”.",
    "3. Przy domenie kaliszpsycholog.pl kliknij trzy kropki ⋮.",
    "4. Kliknij „Odnów” i opłać odnowienie.",
)
HEAD = "Cześć Aniu,\n\ntak odnowisz domenę kaliszpsycholog.pl:\n\n"
TEXT = HEAD + "\n".join(STEPS) + "\n\nPozdrawiam"
BRIEF = "Poradnik dla właścicielki gabinetu: jak odnowić domenę w panelu cyber_Folks."
# Shaped like kuhy's own instruction: what he wants, and how HE reaches her panel.
ASK = (
    "Dodaj zrzuty ekranu z czerwoną ramką i strzałką na to, co kliknąć w każdym kroku; "
    "pierwszy to strona logowania, jaką ona widzi (wylogowana). Ja nie znam jej hasła: "
    f"wchodzę na {SHARES} i w wierszu {OWNER} klikam „ZALOGUJ JAKO” "
    "(nigdy „ZAKOŃCZ UDOSTĘPNIANIE”), wtedy widzę jej panel. Ten mój krok nie trafia do tekstu."
)
BIN = Path(os.environ.get("DRAFT_STUDIO_CLAUDE_BIN") or Path.home() / ".local/bin/claude")
SEED = [{"wait": "Sesja aktywna"}]


def context() -> Context:
    """The rewrite call: the guide's steps selected, kuhy's instruction."""
    start = len(HEAD)
    return Context(
        kind="text", lang="pl", brief=BRIEF, intake=[], text=TEXT, sel=(start, start + len("\n".join(STEPS))),
        instruction=ASK,
    )  # fmt: skip


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def walk_of(answer: dict[str, Any]) -> list[Shot]:
    """The captures that run, in order (walks.py `plan`), as the server would take them."""
    shots, _, _ = plan(answer["captures"], markers(answer["replacement"]))
    return list(shots.values())


def problems(answer: dict[str, Any]) -> list[str]:
    """What is wrong with the answer, empty when it is kuhy's walkthrough."""
    out: list[str] = []
    shots = walk_of(answer)
    if len({s.walk for s in shots}) != 1 or not shots[0].walk:
        out.append(f"want one walk, got walk ids {[s.walk for s in shots]}")
    if not shots or not (shots[0].fresh and shots[0].url == LOGIN and not shots[0].steps):
        out.append("first capture is not the logged-out login page")
    clicks = [aim for s in shots for kind, aim in s.steps if kind == "click"]
    if not any(_fold(a[0]) == "zaloguj jako" and _fold(OWNER) in _fold(a[2]) for a in clicks):
        out.append("no „ZALOGUJ JAKO” click scoped to the owner's row")
    if any("zakończ" in _fold(a[0]) or "odnów" in _fold(a[0]) for a in clicks):
        out.append("clicks „ZAKOŃCZ UDOSTĘPNIANIE” or „Odnów”")
    frames = [s.highlight for s in shots]
    if ("Domeny", "", "Twoje usługi", 0) not in frames:
        out.append("no frame on „Domeny” within „Twoje usługi”")
    if not any(h[1] == "button" and "kaliszpsycholog.pl" in h[2] for h in frames):
        out.append("no frame on the icon-only ⋮ of the kaliszpsycholog.pl row")
    if not any(_fold(h[0]) == "odnów" for h in frames):
        out.append("no frame on „Odnów”")
    if any(not allowed_url(u, f"{TEXT}\n{ASK}") for s in shots for u in s.urls()):
        out.append("a URL that is not in the guide or the instruction")
    text = _fold(answer["replacement"])
    if "zaloguj jako" in text or "your-data/shares" in text or "zrzut" in text:
        out.append("the text mentions kuhy's setup or the screenshots")
    return out


def ask(path: Path) -> int:
    """One real rewrite call; the report (with `problems`) goes to `path`."""
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


def _mimic(spec: dict[str, Any], base: str) -> dict[str, Any]:
    """The spec with Claude's panel URLs moved onto the served mimic."""
    raw = json.dumps(spec, ensure_ascii=False)
    for url, page in MIMIC.items():
        raw = raw.replace(f'"{url}"', f'"{base}{page}"')
    out: dict[str, Any] = json.loads(raw)
    return out


# Where each framed target sits on the mimic (CSS px), as tests/capture_live/test_walk_live.py checks.
FRAMES = (
    ((40, 200, 200, 230), (40, 500, 200, 530), 1500),  # „Domeny” under „Twoje usługi”, not „Twoje dane”
    ((940, 228, 980, 260), (940, 168, 980, 200), 800),  # the ⋮ of kaliszpsycholog.pl, not inna-domena.pl
    ((760, 300, 960, 332), None, 1500),  # „Odnów”
)


def frames_ok(pngs: list[bytes]) -> bool:
    """Shots 2-4 frame the right element and not its look-alike (the login shot has no frame)."""
    if len(pngs) != 1 + len(FRAMES):
        return False
    return all(
        framed(png, box) > least and (other is None or framed(png, other) < 200)
        for png, (box, other, least) in zip(pngs[1:], FRAMES, strict=True)
    )


def shoot(path: Path) -> int:
    """Run the recorded walk on headless chromium; grant the one impersonation request."""
    answer = json.loads(path.read_text(encoding="utf-8"))
    tmp = scratch_dir("ds-walk-")
    store = Store(tmp / "data")
    service = CaptureService(
        store, Chromium(tmp / "data" / "capture-profile", binary=chromium(), timeout=40.0)
    )
    draft = str(store.create("text")["id"])
    with serve_site() as site:
        # kuhy's login (the key button) on the mimic: its session cookie, as tests/capture_live does.
        service.capture(draft, {"url": f"{site.base}/session.html?start", "name": "seed", "steps": SEED})
        status, body = service.capture(draft, _mimic(spec_of(walk_of(answer)), site.base))
        asked = [body["request"]["detail"]] if body.get("error") == "needs-permission" else []
        if asked:
            status, body = service.grant(body["request"]["id"])
        writes = {k: n for k, n in site.writes.items() if n and not k.startswith("GET ")}
    got = body.get("shots", [])
    pngs = [store.asset(draft, s["asset"])[0] for s in got]
    shots = [{"name": s["name"], "red": red_pixels(png)[2]} for s, png in zip(got, pngs, strict=True)]
    want = [s.key for s in walk_of(answer)]
    ok = status == 200 and [s["name"] for s in shots] == want and frames_ok(pngs)
    ok = ok and len(asked) == 1 and list(writes) == ["POST /api/impersonate?id=08245109"]
    report = {"dir": str(tmp), "status": status, "asked": asked, "writes": writes, "shots": shots, "ok": ok}
    print(json.dumps(report | ({} if status == 200 else {"body": body}), ensure_ascii=False, indent=1))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ask", "shoot"))
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    return ask(args.out) if args.mode == "ask" else shoot(args.out)


if __name__ == "__main__":
    sys.exit(main())
